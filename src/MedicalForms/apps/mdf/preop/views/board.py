"""Preop <-> Inpatient Board resolvers, atomic clinical handoff builder, and handoff POST view."""
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views import generic

from ...board import add_event, clean_text
from ...models import BoardEntry, BoardPatient, PreopPatient
from ..constants import _PREOP_MUTATION_LOCK, _sqlite_acquire_write_lock
from ..meds import _effective_allergy_state
from ..registry import rule_value
from ..utils import _int, _num, merged_studies


def _valid_db_pk(val):
    return isinstance(val, int) and not isinstance(val, bool) and 0 < val < (1 << 63)


def resolve_board_patient(patient):
    """Return active BoardPatient linked to this PreopPatient (never hijacking another PreopPatient's Board card)."""
    s = merged_studies(patient)
    bpid = s.get('board_patient_id')
    if _valid_db_pk(bpid):
        bp = BoardPatient.objects.filter(pk=bpid, discharged_at__isnull=True).first()
        if bp:
            return bp
        if BoardPatient.objects.filter(pk=bpid).exists():
            return None
    if patient.patient_name:
        other_preops = (
            PreopPatient.objects.filter(patient_name__iexact=patient.patient_name)
            .exclude(pk=patient.pk)
            .values_list('studies', flat=True)
        )
        claimed_ids = {
            st.get('board_patient_id') for st in other_preops
            if isinstance(st, dict) and _valid_db_pk(st.get('board_patient_id'))
        }
        qs = BoardPatient.objects.filter(discharged_at__isnull=True, name__iexact=patient.patient_name)
        if claimed_ids:
            qs = qs.exclude(pk__in=claimed_ids)
        return qs.order_by('-admitted_at').first()
    return None


def resolve_preop_for_board(board_patient):
    """Return the PreopPatient linked to this BoardPatient (preserving explicit ID links even after Preop archive!)."""
    if not board_patient:
        return None
    same_name = list(
        PreopPatient.objects.filter(patient_name__iexact=board_patient.name)
        .order_by('-updated_at', '-id')[:20]
    )
    for pre in same_name:
        if isinstance(pre.studies, dict) and pre.studies.get('board_patient_id') == board_patient.pk:
            return pre
    recent = PreopPatient.objects.order_by('-updated_at', '-id')[:80]
    for pre in recent:
        if isinstance(pre.studies, dict) and pre.studies.get('board_patient_id') == board_patient.pk:
            return pre
    for pre in same_name:
        if not pre.archived and not (isinstance(pre.studies, dict) and pre.studies.get('board_patient_id')):
            return pre
    return None


def admit_preop_to_board(patient, user, unit='', bed=''):
    """1-click clinical handoff from PreopPatient to BoardPatient + initial STATUS & duty TASKs."""
    with _PREOP_MUTATION_LOCK, transaction.atomic():
        if patient.pk:
            _sqlite_acquire_write_lock(patient.pk)
            patient = PreopPatient.objects.select_for_update().select_related('surgeon', 'surgeon__profile').get(pk=patient.pk)
        return _admit_preop_to_board_locked(patient, user, unit=unit, bed=bed)


def _upsert_board_patient(patient, user, ev, s, unit, bed, now):
    target_unit = (
        unit if unit in (BoardPatient.ICU, BoardPatient.WARD)
        else (BoardPatient.ICU if ev['needs_icu_bed'] else BoardPatient.WARD)
    )
    bed_clean = clean_text(bed, 20).replace('\n', ' ')
    diag_raw = ev.get('effective_operation_label') or patient.planned_operation_label
    diag_str = clean_text(diag_raw, 255).replace('\n', ' ')
    attending = patient.surgeon or user

    bp = resolve_board_patient(patient)
    if bp is None:
        bp = BoardPatient.objects.create(
            name=patient.patient_name,
            bed=bed_clean,
            diagnosis=diag_str,
            unit=target_unit,
            doctor=attending,
            created_by=user,
            admitted_at=now,
        )
        add_event(bp, user, f'გადმოყვანილია წინასაოპერაციოდან — {bp.get_unit_display()}', when=now)
        return bp, True, diag_str

    old_unit = bp.unit
    changed = False
    if bed_clean and bp.bed != bed_clean:
        bp.bed = bed_clean
        changed = True
    if unit in (BoardPatient.ICU, BoardPatient.WARD) and bp.unit != unit:
        bp.unit = unit
        changed = True
    if diag_str and (not bp.diagnosis or s.get('board_patient_id') == bp.pk):
        if bp.diagnosis != diag_str:
            bp.diagnosis = diag_str
            changed = True
    if patient.surgeon and bp.doctor_id != patient.surgeon_id and (not bp.doctor_id or s.get('board_patient_id') == bp.pk):
        bp.doctor = patient.surgeon
        changed = True
    elif not bp.doctor_id and attending:
        bp.doctor = attending
        changed = True
    if changed:
        bp.save()
    if old_unit != bp.unit:
        add_event(bp, user, f'გადაყვანილია: {"რეანიმაცია" if old_unit == BoardPatient.ICU else "პალატა"} → {bp.get_unit_display()}', when=now)
    return bp, False, diag_str


def _build_handoff_status_text(ev, q, s, diag_str, allergy_chips, eff_iod_board):
    bp_arm_diff_lim = rule_value('VIT_BP_HYPOTENSION_SHOCK')['arm_diff']
    anesth_line_sfx = f" ({ev['anesth_lines_txt']})" if ev.get('anesth_lines_txt') else ''
    op_verb = (
        f"ჩატარდა ({ev['operated_date_ka']})" if (ev.get('case_outcome') == 'operated' and ev.get('operated_date_ka'))
        else ('ჩატარდა' if ev.get('case_outcome') == 'operated' else 'დაგეგმილია')
    )
    status_lines = [
        f"[წინასაოპერაციო ჰენდოფი] {op_verb}: {diag_str} · {ev['effective_asa']} · {ev['effective_anesth_label']}{anesth_line_sfx}."
    ]
    vitals_labs = []
    bp_s = _int(s.get('bp_sys'), 30, 320)
    bp_d = _int(s.get('bp_dia'), 20, 220)
    bp_sl = _int(s.get('bp_sys_l'), 30, 320)
    bp_dl = _int(s.get('bp_dia_l'), 20, 220)
    has_r_bp = bp_s is not None and bp_d is not None and bp_s > bp_d
    has_l_bp = bp_sl is not None and bp_dl is not None and bp_sl > bp_dl
    if has_r_bp and has_l_bp:
        vitals_labs.append(f'BP {bp_s}/{bp_d} (L: {bp_sl}/{bp_dl}) mmHg')
    elif has_r_bp:
        vitals_labs.append(f'BP {bp_s}/{bp_d} mmHg')
    elif has_l_bp:
        vitals_labs.append(f'BP (L) {bp_sl}/{bp_dl} mmHg')
    if ev['hb_gl'] is not None:
        vitals_labs.append(f"Hb {ev['hb_gl']} გ/ლ")
    if ev.get('plt') is not None:
        vitals_labs.append(f"PLT {ev['plt']}")
    inr_b = _num(s.get('inr'), 0.4, 20.0)
    if inr_b is not None:
        vitals_labs.append(f'INR {inr_b}')
    if ev.get('creat_unit_mismatch') or ev.get('creat_unit_ambiguous'):
        vitals_labs.append('კრეატინინის ერთეული დასაზუსტებელია')
    elif ev['egfr'] is not None:
        crcl_part = f" / CrCl {ev['crcl']}" if ev.get('crcl') is not None else ''
        vitals_labs.append(f"eGFR {ev['egfr']}{crcl_part}")
    if s.get('blood_group'):
        vitals_labs.append(f"ჯგუფი: {s['blood_group']}")
    if ev.get('min_abi') is not None:
        vitals_labs.append(f"ABI {ev['min_abi']}")
    if ev.get('min_tp') is not None:
        vitals_labs.append(f"TP {ev['min_tp']} mmHg")
    lvef_b = _int(s.get('lvef'), 5, 95)
    if lvef_b is not None:
        vitals_labs.append(f'LVEF {lvef_b}%')
    if ev.get('nt_probnp') is not None:
        vitals_labs.append(f"NT-proBNP {ev['nt_probnp']} pg/mL")
    if ev.get('hs_ctn') is not None:
        vitals_labs.append(f"hs-cTn {ev['hs_ctn']} ng/L")
    if (s.get('ecg') or 'pending') != 'pending':
        vitals_labs.append(f"ეკგ: {ev['ecg_label']}")
    if vitals_labs:
        status_lines.append('მონაცემები: ' + ', '.join(vitals_labs) + '.')

    safety_bits = []
    if 'hit_heparin' in allergy_chips:
        safety_bits.append('HIT ანამნეზში (ჰეპარინი/LMWH აკრძალულია!)')
    if 'beta_lactam' in allergy_chips:
        safety_bits.append('მძიმე β-ლაქტამური ალერგია (ცეფაზოლინი არა!)')
    elif 'penicillin_mild' in allergy_chips:
        safety_bits.append('პენიცილინი (მსუბუქი გამონაყარი — ცეფაზოლინი დასაშვებია)')
    if ev.get('has_vancomycin_allergy') or 'vancomycin' in allergy_chips:
        safety_bits.append('ვანკომიცინზე ალერგია (არ გამოიყენოთ!)')
    if 'latex_chlorhex' in allergy_chips:
        safety_bits.append('ალერგია ლატექსზე / ქლორჰექსიდინზე!')
    if eff_iod_board:
        safety_bits.append('იოდი/კონტრასტზე ალერგია!')
    elif ev.get('uses_contrast') and not q.get('allergy_iodine_asked'):
        safety_bits.append('იოდზე ალერგია დასაზუსტებელია!')
    if 'nsaid' in allergy_chips:
        safety_bits.append('აასს / NSAID ალერგია (კეტოროლაკი/დექსკეტოპროფენი არა!)')
    if 'clopidogrel_intol' in allergy_chips:
        safety_bits.append('კლოპიდოგრელის აუტანლობა / ალერგია!')
    if q.get('ven_lidocaine_allergy') or 'lidocaine' in allergy_chips:
        safety_bits.append('ლიდოკაინზე ალერგია!')
    if (q.get('allergy_text') or '').strip():
        safety_bits.append(f"ალერგიის ჩანაწერი: {q['allergy_text']}")
    if ev.get('bp_arm_diff') is not None and ev['bp_arm_diff'] >= bp_arm_diff_lim:
        higher_arm = 'მარჯვენა' if (bp_s or 0) >= (bp_sl or 0) else 'მარცხენა'
        safety_bits.append(f"წნევის მონიტორინგი {higher_arm} მკლავზე (ΔBP {ev['bp_arm_diff']} mmHg)")
    if q.get('pacemaker'):
        cied_sfx = f" — {ev['cied_plan_label']}" if ev.get('cied_plan_label') else ''
        safety_bits.append(f'კარდიოსტიმულატორი / ICD (CIED){cied_sfx}')
    if ev.get('difficult_airway_alert'):
        safety_bits.append('მოსალოდნელი რთული ინტუბაცია (ვიდეოლარინგოსკოპი!)')
    if ev.get('max_contrast_ml') is not None:
        c_str = f"მაქს. იოდის კონტრასტი ≤ {ev['max_contrast_ml']} მლ"
        if ev.get('co2_angio_advised'):
            c_str += ' (CO2 ანგიოგრაფია / IVUS)'
        safety_bits.append(c_str)
    if s.get('cell_saver_ready'):
        safety_bits.append('Cell Saver + TXA მზადყოფნა')
    if safety_bits:
        status_lines.append('უსაფრთხოება: ' + '; '.join(safety_bits) + '.')
    return clean_text('\n'.join(status_lines), 4000)


_PATHWAY_PRIMARY_PREFIXES = (
    'ანტითრომბოზული სქემა და სტატინი:',
    'CEA პოსტოპ. ნევროლოგიური',
    'პოსტოპ. ჰემოდინამიკის',
    'ვენური პოსტოპ. რეჟიმი:',
)
_VTE_PREFIXES = ('VTE პროფილაქტიკა', 'HIT ანამნეზში!', 'VTE / ანტიკოაგულაცია:')
_ABX_PREFIXES = ('მძიმე ბეტა-ლაქტამური ალერგია!', 'მძიმე β-ლაქტამსა')
_NEPH_PREFIXES = ('პროგრამული დიალიზი:', 'ნეფროპროტექცია:', 'ნეფროპროტექცია (')
_ALL_MANAGED_PREFIXES = (
    *_PATHWAY_PRIMARY_PREFIXES,
    *_VTE_PREFIXES,
    *_ABX_PREFIXES,
    *_NEPH_PREFIXES,
    'NSAID / აასს ალერგია!',
    'გასტროპროტექცია:',
    'კარდიოსტიმულატორი / ICD',
    'გაწერისას VQI / მეთვალყურეობის გრაფიკი:',
)


def _build_handoff_tasks(ev, q, s, pathway, allergy_chips, eff_renal_board):
    ren_red = rule_value('REN_CKD_NEPHRO_HYDRATION')['red']
    cap_low_max = rule_value('SCORE_CAPRINI_VTE')['low_max']
    task_candidates = []
    board_statin_tail = '' if (s.get('liver_status') == '3x' or ev.get('eff_pregnant')) else ' + მაღალი ინტენსივობის სტატინი (LDL <1.4 მმოლ/ლ)'
    if pathway in (PreopPatient.Pathway.ABF, PreopPatient.Pathway.PAD_BYPASS, PreopPatient.Pathway.PAD_PTA):
        task_candidates.append((
            _PATHWAY_PRIMARY_PREFIXES,
            f"ანტითრომბოზული სქემა და სტატინი: {ev['post_antithrombotic_label']}{board_statin_tail} + დისტალური პულსაციის/დოპლერ-მონიტორინგი",
        ))
    elif pathway == PreopPatient.Pathway.CEA:
        task_candidates.append((
            _PATHWAY_PRIMARY_PREFIXES,
            f"CEA პოსტოპ. ნევროლოგიური და წნევის მონიტორინგი (SBP <140–150 mmHg ჰიპერპერფუზიის პრევენციისთვის!) + {ev['surgical_brief']['postop_antithrombotic']}",
        ))
    elif pathway == PreopPatient.Pathway.AAA_INFRA:
        task_candidates.append((
            _PATHWAY_PRIMARY_PREFIXES,
            f"პოსტოპ. ჰემოდინამიკის, დიურეზის და ქვემო კიდურების პულსაციის მონიტორინგი + {ev['surgical_brief']['postop_antithrombotic']} (ESVS 2024)",
        ))
    else:
        if ev.get('compression_contra'):
            comp_txt = 'ელასტიური კომპრესია უკუნაჩვენებია (არტერიული იშემია!)'
        elif ev.get('tbi_reduced_comp'):
            comp_txt = 'მსუბუქი I კლასის კომპრესია (<20 mmHg — სასაზღვრო TP/TBI)'
        elif ev.get('abi_non_compressible') and not ev.get('tbi_comp_safe'):
            comp_txt = 'კომპრესიამდე სავალდებულოა TP/TBI შეფასება (არაკომპრესირებადი ABI >1.40!)'
        else:
            comp_txt = 'ელასტიური კომპრესია პროტოკოლით'
        task_candidates.append((
            _PATHWAY_PRIMARY_PREFIXES,
            f'ვენური პოსტოპ. რეჟიმი: ადრეული აქტიური სიარული · {comp_txt}',
        ))

    cea_bleed_board = (
        pathway == PreopPatient.Pathway.CEA
        and (s.get('brain_imaging') == 'large_or_hemorrhage' or bool(ev.get('active_thrombolysis_window')))
    )
    if cea_bleed_board:
        hit_cea_sfx = ' (ასევე HIT ანამნეზში — UFH/LMWH კატეგორიულად აკრძალულია!)' if 'hit_heparin' in allergy_chips else ''
        task_candidates.append((
            _VTE_PREFIXES,
            f"VTE პროფილაქტიკა (Caprini {ev['caprini_score']} ქ. — {ev['caprini_tier']}): მხოლოდ მექანიკური პროფილაქტიკა (IPC) — ფარმაკოლოგიური ანტიკოაგულაცია უკუნაჩვენებია თავის ტვინის სისხლჩაქცევის / rt-PA ≤6 დღის გამო!{hit_cea_sfx}",
        ))
    elif 'hit_heparin' in allergy_chips:
        has_mv = 'mech_valve' in (q.get('cad_chips') or [])
        liver_3x = s.get('liver_status') == '3x'
        sev_renal = bool(q.get('on_dialysis') or (eff_renal_board is not None and eff_renal_board < ren_red))
        if has_mv:
            hit_board_agents = (
                'ბივალირუდინი (არგატრობანი −75% ღვიძლის ≥3x-ზე!) → ვარფარინი (VKA)'
                if liver_3x else 'ბივალირუდინი / არგატრობანი → ვარფარინი (VKA) — ფონდაპარინუქსი/DOAC არა!'
            )
        elif liver_3x and sev_renal:
            hit_board_agents = 'ბივალირუდინი (არგატრობანი −75% ღვიძლის ≥3x-ზე; ფონდაპარინუქსი CrCl<30/დიალიზზე და ღვიძლის ≥3x-ზე აკრძალულია!)'
        elif sev_renal:
            hit_board_agents = 'ბივალირუდინი / არგატრობანი (ფონდაპარინუქსი CrCl<30/დიალიზზე აკრძალულია!)'
        elif liver_3x:
            hit_board_agents = 'ბივალირუდინი (არგატრობანი −75% ღვიძლის ≥3x-ზე; ფონდაპარინუქსი არარეკომენდებულია!)'
        else:
            hit_board_agents = 'ფონდაპარინუქსი / არგატრობანი / ბივალირუდინი'
        task_candidates.append((
            _VTE_PREFIXES,
            f'HIT ანამნეზში! არაფრაქციული ჰეპარინი და LMWH აკრძალულია — არაჰეპარინული ანტიკოაგულაცია ({hit_board_agents})',
        ))
    elif ev.get('has_contra_oac'):
        task_candidates.append((
            _VTE_PREFIXES,
            'VTE / ანტიკოაგულაცია: უკუნაჩვენები OAC არ განაახლოთ — შეარჩიეთ უსაფრთხო სქემა კარდიოლოგ/ნეფროლოგთან!',
        ))
    elif ev.get('has_full_oac'):
        task_candidates.append((
            _VTE_PREFIXES,
            'VTE / ანტიკოაგულაცია: პაციენტი მუდმივ ანტიკოაგულანტზეა (OAC) — სრული ჰემოსტაზის შემდეგ განახლდეს OAC სქემა ქირურგთან შეთანხმებით (ორმაგი ანტიკოაგულაციის გარეშე!)',
        ))
    elif ev.get('has_mech_valve'):
        task_candidates.append((
            _VTE_PREFIXES,
            'VTE / ანტიკოაგულაცია: მექანიკური სარქველი — სავალდებულოა თერაპიული ვარფარინი (VKA) / ხიდური სქემა კარდიოლოგთან (პროფილაქტიკური LMWH ან DOAC არასაკმარისია/აკრძალულია!)',
        ))
    elif ev['caprini_score'] > cap_low_max or not ev['is_venous']:
        if s.get('liver_status') == '3x':
            vte_board_drug = 'LMWH / UFH კოაგულოგრამის კონტროლით (ღვიძლის ≥3x-ზე DOAC არა!)'
        elif q.get('on_dialysis') or (eff_renal_board is not None and eff_renal_board < ren_red):
            vte_board_drug = 'კანქვეშა UFH 5000 სე ×2–3 ან თირკმელზე კორექტირებული LMWH'
        else:
            vte_board_drug = 'LMWH პროფილაქტიკური დოზით'
        task_candidates.append((
            _VTE_PREFIXES,
            f"VTE პროფილაქტიკა (Caprini {ev['caprini_score']} ქ. — {ev['caprini_tier']}): ადრეული მობილიზაცია + {vte_board_drug} ჰემოსტაზის შემდეგ",
        ))
    _append_allergy_renal_handoff(task_candidates, ev, q, s, pathway, allergy_chips, eff_renal_board)
    return task_candidates


def _append_allergy_renal_handoff(task_candidates, ev, q, s, pathway, allergy_chips, eff_renal_board):
    gent_avoid = rule_value('ALLERGY_HIT_ABX_PROPHYLAXIS')['gentamicin_avoid_renal']
    na_hi = rule_value('REN_ELECTROLYTES_K_NA')['na_hi']
    hyd_egfr = rule_value('REN_CKD_NEPHRO_HYDRATION')['hydration']
    abx_reg = (ev.get('surgical_brief') or {}).get('abx_regimen') or ''
    if 'beta_lactam' in allergy_chips and 'არ ესაჭიროება' not in abx_reg:
        groin_abx_sfx = ''
        if pathway in (PreopPatient.Pathway.AAA_INFRA, PreopPatient.Pathway.ABF, PreopPatient.Pathway.PAD_BYPASS, PreopPatient.Pathway.PAD_PTA):
            if q.get('on_dialysis') or (eff_renal_board is not None and eff_renal_board < gent_avoid) or ev.get('open_aortic'):
                groin_abx_sfx = ' (+ აზტრეონამი / ციპროფლოქსაცინი — გენტამიცინი აკრძალულია!)'
            else:
                groin_abx_sfx = ' (+ გენტამიცინი / აზტრეონამი საზარდულის მიდგომისას)'
        if ev.get('has_vancomycin_allergy') or 'vancomycin' in allergy_chips:
            abx_task_txt = (
                f'მძიმე β-ლაქტამსა და ვანკომიცინზე ალერგია! ცეფაზოლინი და ვანკომიცინი აკრძალულია — '
                f'პროფილაქტიკა კლინდამიცინით 900 მგ IV{groin_abx_sfx}'
            )
        else:
            abx_task_txt = (
                f'მძიმე ბეტა-ლაქტამური ალერგია! ცეფაზოლინი უკუნაჩვენებია — '
                f'პროფილაქტიკა ვანკომიცინით ან კლინდამიცინით (ვანკომიცინი მაქს. 2 გ){groin_abx_sfx}'
            )
        task_candidates.append((_ABX_PREFIXES, abx_task_txt))

    if 'nsaid' in allergy_chips:
        task_candidates.append((
            'NSAID / აასს ალერგია!',
            'NSAID / აასს ალერგია! პოსტოპ. ანალგეზიაში კეტოროლაკი / დექსკეტოპროფენი არ გამოიყენოთ (პარაცეტამოლი ± ოპიოიდი)',
        ))

    if q.get('on_dialysis'):
        task_candidates.append((
            _NEPH_PREFIXES,
            'პროგრამული დიალიზი: შეათანხმეთ დიალიზის სეანსი და საკონტროლო K+ ოპერაციამდე/შემდეგ (უჰეპარინო რეჟიმი)',
        ))
    elif ev.get('needs_hydration'):
        contrast_sfx = f" (მაქს. კონტრასტი ≤ {ev['max_contrast_ml']} მლ)" if ev.get('max_contrast_ml') is not None else ''
        na_b = ev.get('na_val_sat')
        if na_b is None:
            na_b = _num(s.get('na'), 90, 200)
        if na_b is not None and na_b >= na_hi:
            hf_tail = ' · HFrEF სითხის შეზღუდვით!' if ev.get('chf_hyd_flag') else ''
            hyd_b = f'ჰიპერნატრიემია — 0.9% NaCl-ის გარეშე, ნელი ჰიპოტონური კორექცია{hf_tail}'
        elif ev.get('chf_hyd_flag'):
            hyd_b = 'შეზღუდული იზოტონური ჰიდრატაცია (0.5 მლ/კგ/სთ — გულის უკმარისობა / LVEF <40%!)'
        else:
            hyd_b = 'იზოტონური ჰიდრატაცია'
        task_candidates.append((
            _NEPH_PREFIXES,
            f'ნეფროპროტექცია: {hyd_b} + საკონტროლო კრეატინინი და K+ 24–48 სთ-ში{contrast_sfx}',
        ))
    elif eff_renal_board is not None and eff_renal_board < hyd_egfr:
        task_candidates.append((
            _NEPH_PREFIXES,
            'ნეფროპროტექცია (თირკმლის მონიტორინგი): საკონტროლო კრეატინინი და K+ 24–48 სთ-ში (ნეფროტოქსიკური წამლების თავიდან აცილება)',
        ))

    ft_med_keys = set(ev.get('ft_meds_keys') or ())
    has_ppi_now = ('ppi' in (q.get('med_chips') or ())) or ('omeprazole' in ft_med_keys)
    if (
        ev.get('needs_ppi') or q.get('ulcer') or q.get('ulcer_active_symp')
        or s.get('gastro_result') == 'acute_ulcer'
        or (ev.get('cyp2c19_ppi_sensitive') and has_ppi_now)
    ):
        ppi_b = 'პანტოპრაზოლი 40 მგ (CYP2C19 უსაფრთხო)' if ev.get('cyp2c19_ppi_sensitive') else 'ომეპრაზოლი / პანტოპრაზოლი (PPI)'
        task_candidates.append((
            'გასტროპროტექცია:',
            f'გასტროპროტექცია: {ppi_b} ანტითრომბოზული თერაპიის ფონზე (ESC 2022 Class I)',
        ))
    if q.get('pacemaker'):
        task_candidates.append((
            'კარდიოსტიმულატორი / ICD',
            'კარდიოსტიმულატორი / ICD (CIED): პოსტოპერაციული რიტმისა და აპარატის რეჟიმის გადამოწმება',
        ))
    task_candidates.append((
        'გაწერისას VQI / მეთვალყურეობის გრაფიკი:',
        f"გაწერისას VQI / მეთვალყურეობის გრაფიკი: {ev['surveillance_plan']}",
    ))


def _admit_preop_to_board_locked(patient, user, unit='', bed=''):
    from .. import evaluate

    ev = evaluate(patient)
    q, s = ev['q'], ev['s']
    pathway = ev['pathway']
    now = timezone.now()

    bp, was_created, diag_str = _upsert_board_patient(patient, user, ev, s, unit, bed, now)
    allergy_chips, eff_iod_board, _ = _effective_allergy_state(q)
    eff_renal_board = min([v for v in (ev.get('crcl'), ev.get('egfr')) if v is not None], default=None)

    status_text = _build_handoff_status_text(ev, q, s, diag_str, allergy_chips, eff_iod_board)
    existing_handoff = (
        BoardEntry.objects.filter(patient=bp, kind=BoardEntry.STATUS, text__startswith='[წინასაოპერაციო ჰენდოფი]')
        .order_by('-id')
        .first()
    )
    if existing_handoff is None:
        BoardEntry.objects.create(patient=bp, kind=BoardEntry.STATUS, text=status_text, created_by=user, created_at=now)
    elif existing_handoff.text != status_text:
        existing_handoff.text = status_text
        existing_handoff.updated_by = user
        existing_handoff.updated_at = now
        existing_handoff.save(update_fields=['text', 'updated_by', 'updated_at'])

    task_candidates = _build_handoff_tasks(ev, q, s, pathway, allergy_chips, eff_renal_board)
    existing_task_objs = list(BoardEntry.objects.filter(patient=bp, kind=BoardEntry.TASK).order_by('id'))
    kept_open_ids = set()
    added_tasks = 0
    for prefix, t_raw in task_candidates:
        t_clean = clean_text(t_raw, 4000)
        if not t_clean:
            continue
        open_match = next(
            (obj for obj in existing_task_objs if obj.done_at is None and obj.pk not in kept_open_ids and (obj.text == t_clean or obj.text.startswith(prefix))),
            None,
        )
        if open_match is not None:
            if open_match.text != t_clean:
                open_match.text = t_clean
                open_match.updated_by = user
                open_match.updated_at = now
                open_match.save(update_fields=['text', 'updated_by', 'updated_at'])
            kept_open_ids.add(open_match.pk)
        elif not any(obj.done_at is not None and obj.text == t_clean for obj in existing_task_objs):
            new_obj = BoardEntry.objects.create(patient=bp, kind=BoardEntry.TASK, text=t_clean, created_by=user, created_at=now)
            existing_task_objs.append(new_obj)
            kept_open_ids.add(new_obj.pk)
            added_tasks += 1

    stale_open_ids = [
        obj.pk for obj in existing_task_objs
        if obj.done_at is None and obj.pk not in kept_open_ids and obj.text.startswith(_ALL_MANAGED_PREFIXES)
    ]
    if stale_open_ids:
        BoardEntry.objects.filter(pk__in=stale_open_ids).delete()

    bp.save(update_fields=['updated_at'])
    s_mut = merged_studies(patient)
    s_mut['board_patient_id'] = bp.pk
    patient.studies = s_mut
    patient.updated_by = user
    patient.save(update_fields=['studies', 'updated_at', 'updated_by'])
    return bp, was_created, added_tasks


class PreopBoardHandoffView(LoginRequiredMixin, generic.View):
    """1-click POST endpoint transferring a PreopPatient to the Inpatient Board ('პაციენტების დაფა')."""

    def post(self, request, pk):
        patient = get_object_or_404(
            PreopPatient.objects.select_related('surgeon', 'surgeon__profile'),
            pk=pk,
        )
        unit = request.POST.get('unit') or ''
        bed = request.POST.get('bed') or ''
        bp, was_created, added_tasks = admit_preop_to_board(patient, request.user, unit=unit, bed=bed)
        if was_created:
            messages.success(
                request,
                f'{bp.name} გადმოყვანილია პაციენტების დაფაზე ({bp.get_unit_display()}, დაემატა {added_tasks} პროტოკოლური დავალება).',
            )
        else:
            messages.success(request, f'{bp.name} — დაფის ჰენდოფი განახლდა.')
        return HttpResponseRedirect(reverse('board-patient', args=[bp.pk]))
