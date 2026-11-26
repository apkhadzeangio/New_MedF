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
            .values_list('studies', flat=True)[:30]
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
    diag_str = clean_text(patient.planned_operation_label, 255).replace('\n', ' ')
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
    if bp_s is not None and bp_d is not None and bp_s > bp_d:
        vitals_labs.append(f'BP {bp_s}/{bp_d} mmHg')
    if ev['hb_gl'] is not None:
        vitals_labs.append(f"Hb {ev['hb_gl']} გ/ლ")
    if ev.get('plt') is not None:
        vitals_labs.append(f"PLT {ev['plt']}")
    inr_b = _num(s.get('inr'), 0.4, 20.0)
    if inr_b is not None:
        vitals_labs.append(f'INR {inr_b}')
    if ev['egfr'] is not None:
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
    if 'latex_chlorhex' in allergy_chips:
        safety_bits.append('ალერგია ლატექსზე / ქლორჰექსიდინზე!')
    if eff_iod_board:
        safety_bits.append('იოდი/კონტრასტზე ალერგია!')
    if 'nsaid' in allergy_chips:
        safety_bits.append('აასს / NSAID ალერგია (კეტოროლაკი/დექსკეტოპროფენი არა!)')
    if q.get('ven_lidocaine_allergy') or 'lidocaine' in allergy_chips:
        safety_bits.append('ლიდოკაინზე ალერგია!')
    if (q.get('allergy_text') or '').strip():
        safety_bits.append(f"ალერგიის ჩანაწერი: {q['allergy_text']}")
    if ev.get('bp_arm_diff') is not None and ev['bp_arm_diff'] >= 20:
        bp_l = _int(s.get('bp_sys_l'), 30, 320)
        higher_arm = 'მარჯვენა' if (bp_s or 0) >= (bp_l or 0) else 'მარცხენა'
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


def _build_handoff_tasks(ev, q, s, pathway, allergy_chips, eff_renal_board):
    task_candidates = []
    board_statin_tail = '' if s.get('liver_status') == '3x' else ' + მაღალი ინტენსივობის სტატინი (LDL <1.4 მმოლ/ლ)'
    if pathway in (PreopPatient.Pathway.ABF, PreopPatient.Pathway.PAD_BYPASS, PreopPatient.Pathway.PAD_PTA):
        task_candidates.append((
            'ანტითრომბოზული სქემა და სტატინი:',
            f"ანტითრომბოზული სქემა და სტატინი: {ev['post_antithrombotic_label']}{board_statin_tail} + დისტალური პულსაციის/დოპლერ-მონიტორინგი",
        ))
    elif pathway == PreopPatient.Pathway.CEA:
        task_candidates.append((
            'CEA პოსტოპ. ნევროლოგიური',
            f"CEA პოსტოპ. ნევროლოგიური და წნევის მონიტორინგი (SBP <140–160 mmHg ჰიპერპერფუზიის პრევენციისთვის!) + {ev['surgical_brief']['postop_antithrombotic']}",
        ))
    elif pathway == PreopPatient.Pathway.AAA_INFRA:
        task_candidates.append((
            'პოსტოპ. ჰემოდინამიკის',
            f"პოსტოპ. ჰემოდინამიკის, დიურეზის და ქვემო კიდურების პულსაციის მონიტორინგი + {ev['surgical_brief']['postop_antithrombotic']} (ESVS 2024)",
        ))
    else:
        comp_txt = 'ელასტიური კომპრესია უკუნაჩვენებია (არტერიული იშემია!)' if ev.get('compression_contra') else 'ელასტიური კომპრესია პროტოკოლით'
        task_candidates.append((
            'ვენური პოსტოპ. რეჟიმი:',
            f'ვენური პოსტოპ. რეჟიმი: ადრეული აქტიური სიარული · {comp_txt}',
        ))

    cea_bleed_board = (
        pathway == PreopPatient.Pathway.CEA
        and (s.get('brain_imaging') == 'large_or_hemorrhage' or bool(ev.get('active_thrombolysis_window')))
    )
    if cea_bleed_board:
        hit_cea_sfx = ' (ასევე HIT ანამნეზში — UFH/LMWH კატეგორიულად აკრძალულია!)' if 'hit_heparin' in allergy_chips else ''
        task_candidates.append((
            'VTE პროფილაქტიკა',
            f"VTE პროფილაქტიკა (Caprini {ev['caprini_score']} ქ. — {ev['caprini_tier']}): მხოლოდ მექანიკური პროფილაქტიკა (IPC) — ფარმაკოლოგიური ანტიკოაგულაცია უკუნაჩვენებია თავის ტვინის სისხლჩაქცევის / rt-PA ≤6 დღის გამო!{hit_cea_sfx}",
        ))
    elif 'hit_heparin' in allergy_chips:
        has_mv = 'mech_valve' in (q.get('cad_chips') or [])
        liver_3x = s.get('liver_status') == '3x'
        sev_renal = bool(q.get('on_dialysis') or (eff_renal_board is not None and eff_renal_board < 30))
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
            'HIT ანამნეზში!',
            f'HIT ანამნეზში! არაფრაქციული ჰეპარინი და LMWH აკრძალულია — არაჰეპარინული ანტიკოაგულაცია ({hit_board_agents})',
        ))
    elif ev.get('has_full_oac'):
        oac_contra_b = bool(
            (q.get('med_dabigatran') and (q.get('on_dialysis') or (eff_renal_board is not None and eff_renal_board < 30)))
            or ((q.get('med_doac') or q.get('med_dabigatran')) and s.get('liver_status') == '3x')
        )
        if oac_contra_b:
            task_candidates.append((
                'VTE / ანტიკოაგულაცია:',
                'VTE / ანტიკოაგულაცია: უკუნაჩვენები OAC არ განაახლოთ — შეარჩიეთ უსაფრთხო სქემა კარდიოლოგ/ნეფროლოგთან!',
            ))
        else:
            task_candidates.append((
                'VTE / ანტიკოაგულაცია:',
                'VTE / ანტიკოაგულაცია: პაციენტი მუდმივ ანტიკოაგულანტზეა (OAC) — სრული ჰემოსტაზის შემდეგ განახლდეს OAC სქემა ქირურგთან შეთანხმებით (ორმაგი ანტიკოაგულაციის გარეშე!)',
            ))
    elif ev['caprini_score'] >= 3 or not ev['is_venous']:
        if s.get('liver_status') == '3x':
            vte_board_drug = 'LMWH / UFH კოაგულოგრამის კონტროლით (ღვიძლის ≥3x-ზე DOAC არა!)'
        elif q.get('on_dialysis') or (eff_renal_board is not None and eff_renal_board < 30):
            vte_board_drug = 'კანქვეშა UFH 5000 სე ×2–3 ან თირკმელზე კორექტირებული LMWH'
        else:
            vte_board_drug = 'LMWH პროფილაქტიკური დოზით'
        task_candidates.append((
            'VTE პროფილაქტიკა',
            f"VTE პროფილაქტიკა (Caprini {ev['caprini_score']} ქ. — {ev['caprini_tier']}): ადრეული მობილიზაცია + {vte_board_drug} ჰემოსტაზის შემდეგ",
        ))
    _append_allergy_renal_handoff(task_candidates, ev, q, s, pathway, allergy_chips, eff_renal_board)
    return task_candidates


def _append_allergy_renal_handoff(task_candidates, ev, q, s, pathway, allergy_chips, eff_renal_board):
    if 'beta_lactam' in allergy_chips:
        groin_abx_sfx = ''
        if pathway in (PreopPatient.Pathway.AAA_INFRA, PreopPatient.Pathway.ABF, PreopPatient.Pathway.PAD_BYPASS, PreopPatient.Pathway.PAD_PTA):
            if q.get('on_dialysis') or (eff_renal_board is not None and eff_renal_board < 45) or ev.get('open_aortic'):
                groin_abx_sfx = ' (+ აზტრეონამი / ციპროფლოქსაცინი — გენტამიცინი აკრძალულია!)'
            else:
                groin_abx_sfx = ' (+ გენტამიცინი / აზტრეონამი საზარდულის მიდგომისას)'
        task_candidates.append((
            'მძიმე ბეტა-ლაქტამური ალერგია!',
            f'მძიმე ბეტა-ლაქტამური ალერგია! ცეფაზოლინი უკუნაჩვენებია — პროფილაქტიკა ვანკომიცინით ან კლინდამიცინით (ვანკომიცინი მაქს. 2 გ){groin_abx_sfx}',
        ))

    if 'nsaid' in allergy_chips:
        task_candidates.append((
            'NSAID / აასს ალერგია!',
            'NSAID / აასს ალერგია! პოსტოპ. ანალგეზიაში კეტოროლაკი / დექსკეტოპროფენი არ გამოიყენოთ (პარაცეტამოლი ± ოპიოიდი)',
        ))

    if q.get('on_dialysis'):
        task_candidates.append((
            'პროგრამული დიალიზი:',
            'პროგრამული დიალიზი: შეათანხმეთ დიალიზის სეანსი და საკონტროლო K+ ოპერაციამდე/შემდეგ (უჰეპარინო რეჟიმი)',
        ))
    elif ev.get('needs_hydration'):
        contrast_sfx = f" (მაქს. კონტრასტი ≤ {ev['max_contrast_ml']} მლ)" if ev.get('max_contrast_ml') is not None else ''
        na_b = ev.get('na_val_sat')
        if na_b is None:
            na_b = _num(s.get('na'), 90, 200)
        if na_b is not None and na_b >= 150:
            hf_tail = ' · HFrEF სითხის შეზღუდვით!' if ev.get('chf_hyd_flag') else ''
            hyd_b = f'ჰიპერნატრიემია — 0.9% NaCl-ის გარეშე, ნელი ჰიპოტონური კორექცია{hf_tail}'
        elif ev.get('chf_hyd_flag'):
            hyd_b = 'შეზღუდული იზოტონური ჰიდრატაცია (0.5 მლ/კგ/სთ — გულის უკმარისობა / LVEF <40%!)'
        else:
            hyd_b = 'იზოტონური ჰიდრატაცია'
        task_candidates.append((
            'ნეფროპროტექცია:',
            f'ნეფროპროტექცია: {hyd_b} + საკონტროლო კრეატინინი და K+ 24–48 სთ-ში{contrast_sfx}',
        ))
    elif eff_renal_board is not None and eff_renal_board < 60:
        task_candidates.append((
            'ნეფროპროტექცია:',
            'ნეფროპროტექცია (თირკმლის მონიტორინგი): საკონტროლო კრეატინინი და K+ 24–48 სთ-ში (ნეფროტოქსიკური წამლების თავიდან აცილება)',
        ))

    if ev.get('needs_ppi') or q.get('ulcer'):
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
    added_tasks = 0
    for prefix, t_raw in task_candidates:
        t_clean = clean_text(t_raw, 4000)
        if not t_clean:
            continue
        matched = next((obj for obj in existing_task_objs if obj.text == t_clean or obj.text.startswith(prefix)), None)
        if matched is None:
            new_obj = BoardEntry.objects.create(patient=bp, kind=BoardEntry.TASK, text=t_clean, created_by=user, created_at=now)
            existing_task_objs.append(new_obj)
            added_tasks += 1
        elif matched.text != t_clean and matched.done_at is None:
            matched.text = t_clean
            matched.updated_by = user
            matched.updated_at = now
            matched.save(update_fields=['text', 'updated_by', 'updated_at'])

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
