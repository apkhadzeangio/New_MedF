"""Temporary shrunk legacy bridge for evaluate() and views during Track C3 (removed in C4/C5)."""
import datetime
import secrets

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse, Http404, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views import generic

from ..board import add_event, clean_text, doctors, person_name
from ..cases import PhotoError
from ..models import BoardEntry, BoardPatient, Case, PreopFile, PreopPatient
from .anamnesis import build_self_fill_highlights, generate_anamnesis
from .calculators import (
    _ihd_and_chf_flags,
    _parse_scr_mgdl,
    build_surveillance_plan,
    calc_ariscat_pulm,
    calc_auto_asa,
    calc_bmi,
    calc_caprini,
    calc_crcl,
    calc_egfr,
    calc_max_contrast_ml,
    calc_rcri,
    calc_vsg_cri,
    normalize_albumin_gl,
    normalize_fibrinogen_gl,
    normalize_glucose_mmol,
    normalize_hb_gl,
    normalize_ldl_mmol,
)
from .case_import import detect_case_preop, import_case_to_preop
from .constants import (
    AAA_MORPHOLOGY_CHOICES,
    AAA_NECK_CHOICES,
    ANESTH_PLAN_CHOICES,
    ASA_CHOICES,
    CASE_OUTCOME_CHOICES,
    CEAP_CHOICES,
    CFS_CHOICES,
    CHIP_CATALOGS,
    CHIP_LABELS,
    CIED_PLAN_CHOICES,
    CXR_LABELS,
    DEFAULT_CARDIOLOGISTS,
    DEFAULT_Q,
    DEFAULT_STUDIES,
    DURATION_CHIPS,
    ECG_LABELS,
    MALLAMPATI_CHOICES,
    METS_LIMIT_CHOICES,
    MONTHS_SHORT,
    OP_SUBTYPE_GROUPS,
    PATHWAY_SHORT,
    POPLITEAL_SCREEN_CHOICES,
    POST_ANTITHROMBOTIC_CHOICES,
    PRIORITY_LABELS,
    SELF_FILL_MAX_AGE,
    SELF_FILL_MED_HINTS,
    SELF_FILL_SALT,
    STATUS_ALERT,
    STATUS_LABELS,
    STATUS_NO_Q,
    STATUS_PROGRESS,
    STATUS_READY,
    STOP_BANG_CHOICES,
    SURG_DURATION_CHOICES,
    TARGET_PREP_DAYS,
    VQI_30D_CHOICES,
    WEEKDAYS_KA,
    _PREOP_MUTATION_LOCK,
    _sqlite_acquire_write_lock,
)
from .forms import apply_questionnaire_post, apply_self_fill_post, save_preop_file
from .meds import (
    _apply_stop_lock_to_med_schedule,
    _detect_freetext_high_risk_meds,
    _effective_allergy_state,
    build_med_schedule,
)
from .utils import (
    _age_from_dob,
    _alerts_signature,
    _cardiologist_choices,
    _clean_person_id,
    _date,
    _fmt_ka_date,
    _int,
    _monday_of,
    _num,
    _resolve_pathway,
    _update_hold_pause,
    _week_title,
    cea_live_days_since_event,
    chip_summary,
    consent_url_for_preop,
    cta_live_months_age,
    make_self_fill_token,
    mask_person_id,
    mask_phone,
    merged_q,
    merged_studies,
    prep_days_elapsed,
    target_prep_days,
)


def evaluate(patient, today=None):
    """Evaluate thresholds, Autonomous Nurse Reflex Orders, Stage 2 tasks, Cardiology Audit, and 2D Triage."""
    today = today or timezone.localdate()
    pathway = _resolve_pathway(patient)
    is_venous = (pathway == PreopPatient.Pathway.VENOUS)
    is_phleb = (patient.op_subtype == PreopPatient.OpSubtype.PHLEB)
    is_pta = (pathway == PreopPatient.Pathway.PAD_PTA)
    is_pad_bypass = (pathway == PreopPatient.Pathway.PAD_BYPASS)
    is_on_hold = (patient.someday_list == PreopPatient.SomedayList.HOLD)

    q = merged_q(patient)
    s = merged_studies(patient)
    q_filled = bool(q.get('filled'))
    case_outcome = s.get('case_outcome_status') if s.get('case_outcome_status') in dict(CASE_OUTCOME_CHOICES) else 'scheduled'
    is_active_case = (case_outcome == 'scheduled') and not patient.archived
    days = prep_days_elapsed(patient, today=today)
    t_days = target_prep_days(patient, today=today)

    red_flags = []
    amber_flags = []
    reflex_orders = []
    priority_flags = []  # urgency/clock alerts that move from blocking red_flags once workup is 100% ready
    hard_contraindications = []  # absolute anatomical/clinical contraindications that cannot be overridden to STATUS_READY

    hb_gl = normalize_hb_gl(s.get('hb'))
    gl_mmol = normalize_glucose_mmol(s.get('glucose'))
    alb_gl = normalize_albumin_gl(s.get('albumin'))
    on_dialysis = bool(q.get('on_dialysis'))
    creat_unit = s.get('creat_unit') or ''
    _, creat_unit_ambiguous = _parse_scr_mgdl(s.get('creat'), creat_unit)
    egfr = calc_egfr(s.get('creat'), patient.age, patient.sex or '', creat_unit, on_dialysis=on_dialysis)
    crcl = calc_crcl(
        s.get('creat'), patient.age, patient.sex or '', s.get('weight_kg'),
        creat_unit, on_dialysis=on_dialysis, height_cm=s.get('height_cm'),
    )
    eff_renal = None if on_dialysis else min([v for v in (egfr, crcl) if v is not None], default=None)
    bmi = calc_bmi(s.get('weight_kg'), s.get('height_cm'))
    liver_3x = (s.get('liver_status') == '3x')

    aaa_mm = _num(s.get('aaa_mm'), lo=10, hi=200)
    cta_months = cta_live_months_age(s, today=today)
    car_r, car_l = _int(s.get('carotid_r'), lo=0, hi=100), _int(s.get('carotid_l'), lo=0, hi=100)
    max_car = max([v for v in (car_r, car_l) if v is not None], default=None)

    abi_r = _num(s.get('abi_r'), lo=0.0, hi=3.0)
    abi_l = _num(s.get('abi_l'), lo=0.0, hi=3.0)
    tp_r = _int(s.get('tp_r'), lo=0, hi=400)
    tp_l = _int(s.get('tp_l'), lo=0, hi=400)
    tbi_r = _num(s.get('tbi_r'), lo=0.0, hi=3.0)
    tbi_l = _num(s.get('tbi_l'), lo=0.0, hi=3.0)

    # Side-aware limb hemodynamics: unilateral limbs never let contralateral TP/ABI mask ipsilateral ischemia/calcinosis
    unilateral_limb_pw = pathway in (PreopPatient.Pathway.PAD_BYPASS, PreopPatient.Pathway.PAD_PTA, PreopPatient.Pathway.VENOUS)
    if unilateral_limb_pw and patient.side == 'R':
        scope_legs = ('R',)
        contra_abi, contra_tp = abi_l, tp_l
    elif unilateral_limb_pw and patient.side == 'L':
        scope_legs = ('L',)
        contra_abi, contra_tp = abi_r, tp_r
    else:
        scope_legs = ('R', 'L')
        contra_abi, contra_tp = None, None

    leg_vals = {
        'R': {'abi': abi_r, 'tp': tp_r, 'tbi': tbi_r},
        'L': {'abi': abi_l, 'tp': tp_l, 'tbi': tbi_l},
    }
    scoped_abis = [leg_vals[lg]['abi'] for lg in scope_legs if leg_vals[lg]['abi'] is not None]
    scoped_tps = [leg_vals[lg]['tp'] for lg in scope_legs if leg_vals[lg]['tp'] is not None]
    scoped_tbis = [leg_vals[lg]['tbi'] for lg in scope_legs if leg_vals[lg]['tbi'] is not None]
    all_abis = [v for v in (abi_r, abi_l) if v is not None]
    all_tps = [v for v in (tp_r, tp_l) if v is not None]
    all_tbis = [v for v in (tbi_r, tbi_l) if v is not None]

    eff_abis = scoped_abis if scoped_abis else all_abis
    eff_tps = scoped_tps if scoped_tps else all_tps
    eff_tbis = scoped_tbis if scoped_tbis else all_tbis

    min_abi = min(eff_abis, default=None)
    max_abi = max(eff_abis, default=None)
    min_comp_abi = min([v for v in eff_abis if v <= 1.40], default=None)
    min_tp = min(eff_tps, default=None)
    min_tbi = min(eff_tbis, default=None)

    nc_legs = [lg for lg in scope_legs if leg_vals[lg]['abi'] is not None and leg_vals[lg]['abi'] > 1.40]
    if not nc_legs and not scoped_abis:
        nc_legs = [lg for lg in ('R', 'L') if leg_vals[lg]['abi'] is not None and leg_vals[lg]['abi'] > 1.40]
    abi_non_compressible = bool(nc_legs)
    if nc_legs:
        tbi_measured = all((leg_vals[lg]['tp'] is not None or leg_vals[lg]['tbi'] is not None) for lg in nc_legs)
        tbi_comp_safe = all(
            ((leg_vals[lg]['tp'] is not None and leg_vals[lg]['tp'] >= 60)
             or (leg_vals[lg]['tbi'] is not None and leg_vals[lg]['tbi'] >= 0.70))
            for lg in nc_legs
        )
    else:
        tbi_measured = bool(scoped_tps or scoped_tbis) if unilateral_limb_pw and patient.side in ('R', 'L') else bool(all_tps or all_tbis)
        tbi_comp_safe = True

    compression_contra = bool(
        (min_comp_abi is not None and min_comp_abi < 0.80)
        or (abi_non_compressible and ((min_tp is not None and min_tp < 50) or (min_tbi is not None and min_tbi < 0.50)))
    )
    tbi_reduced_comp = bool(abi_non_compressible and tbi_measured and not compression_contra and not tbi_comp_safe)
    caprini_score, caprini_tier, caprini_advice = calc_caprini(
        patient, q, bmi, today=today, eff_renal=eff_renal,
        liver_status=s.get('liver_status') or 'normal',
        compression_contra=compression_contra,
    )

    pad_gsv_mm = _num(s.get('pad_gsv_mm'), lo=0.5, hi=30)
    ldl_val = normalize_ldl_mmol(s.get('ldl'))
    weight_num = _num(s.get('weight_kg'), lo=25, hi=260)

    ipsi_car = None
    contra_car = None
    is_symp_cea = False
    is_clti_any = False
    days_since_ev = None
    needs_brain_ct = False
    needs_neuro = False
    needs_laryngoscopy = False
    needs_cea_antiplatelet = False
    needs_wound_care = False
    needs_abi_check = False
    needs_tbi_check = False
    needs_vte_prophylaxis = False
    needs_ecg_venous = False
    needs_alt_vein = False
    cea_high_surg_risk = False
    lidocaine_max_mg = None
    key_badge = ''
    key_badge_urgent = False

    med_chips = set(q.get('med_chips') or [])
    cad_chips = set(q.get('cad_chips') or [])
    allergy_chips, eff_allergy_iodine, ft_allergy_matched = _effective_allergy_state(q)
    ft_meds = _detect_freetext_high_risk_meds(q.get('other_meds') or '', q=q)
    unclarified_allergy_other = bool(q_filled and q.get('allergy_other') and not allergy_chips and not eff_allergy_iodine)
    vasc_chips = set(q.get('vasc_chips') or [])
    anesth_lines_set = set(s.get('anesth_lines_chips') or [])
    has_statin = ('statin' in med_chips) or any(
        w in (q.get('other_meds') or '').lower() for w in ('სტატინ', 'ატორვ', 'როზუვ', 'კრესტორ', 'ლიპრიმ')
    )
    has_antiplatelet = bool(
        q.get('med_aspirin') or q.get('med_clopidogrel') or q.get('med_ticagrelor') or q.get('med_prasugrel')
    )
    has_any_antithrombotic = bool(
        has_antiplatelet or q.get('med_doac') or q.get('med_dabigatran')
        or q.get('med_warfarin') or ('rivaroxaban_2_5' in med_chips) or ('cilostazol' in med_chips)
    )

    if s.get('case_wifi_unsalvageable'):
        red_flags.append(
            'კონფერენციის სტადირებით WIfI Stage 5 (კიდური გადარჩენადი არ არის) — '
            'რევასკულარიზაციის ჩვენება გადაამოწმეთ ქირურგთან!'
        )

    if abi_non_compressible:
        needs_tbi_check = True
        nc_side_lbl = f" ({'/'.join(nc_legs)})" if nc_legs else ''
        reflex_orders.append({
            'key': 'tbi',
            'trigger': f'არაკომპრესირებადი არტერია{nc_side_lbl} (ABI {max_abi} > 1.40 — მონკებერგის კალციფიკაცია)',
            'action': 'სამიზნე კიდურზე თითის წნევის (Toe Pressure, mmHg) ან თით-მხრის ინდექსის (TBI) განსაზღვრა რეალური პერფუზიის შესაფასებლად',
            'done': tbi_measured,
        })
        if not is_venous and ((min_tp is not None and min_tp < 30) or (min_tbi is not None and min_tbi < 0.30)):
            amber_flags.append(
                f'კრიტიკულად დაბალი Toe Pressure / TBI (TP {min_tp if min_tp is not None else "—"} mmHg / '
                f'TBI {min_tbi if min_tbi is not None else "—"}) — ადასტურებს მძიმე იშემიას (CLTI).'
            )

    # 1. Pathway-specific Vascular / Venous Evaluation & Preparation Clocks
    if pathway == PreopPatient.Pathway.AAA_INFRA:
        aaa_morph = s.get('aaa_morphology') or 'fusiform'
        aaa_neck = s.get('aaa_neck_status') or 'pending'
        pop_scr = s.get('popliteal_screen_status') or 'pending'

        if q_filled and q.get('aaa_pain'):
            red_flags.append(
                'საეჭვოა სიმპტომური AAA (ახალი ტკივილი მუცლის/წელის/გვერდის არეში) — '
                'გეგმური კვლევების ნაცვლად დაუყოვნებლივ აცნობეთ ქირურგს!'
            )
        if aaa_morph == 'inflammatory_mycotic':
            red_flags.append(
                'საეჭვოა მიკოზური / ინფიცირებული ანევრიზმა (ESVS 2024) — '
                'სასწრაფოდ აცნობეთ ქირურგს (სისხლის ნათესები, ანტიბიოტიკოთერაპია და დაჩქარებული ტაქტიკა)!'
            )
        elif aaa_morph == 'inflammatory':
            amber_flags.append(
                'სტერილური ანთებითი AAA / პერიაორტიტი (ESVS 2024): ღია დისექციისას მომატებულია თორმეტგოჯა ნაწლავისა და შარდსაწვეთების დაზიანების რისკი — ანატომიური შესაბამისობისას უპირატესია EVAR.'
            )
        if aaa_mm is not None:
            min_thresh = 50 if patient.sex == 'F' else 55
            mm_fmt = int(aaa_mm) if aaa_mm == int(aaa_mm) else aaa_mm
            key_badge = f'{mm_fmt} მმ'
            key_badge_urgent = bool(aaa_mm >= 70 or aaa_morph == 'inflammatory_mycotic')
            if aaa_mm >= 70:
                msg = f'გიგანტური ანევრიზმა ({aaa_mm} მმ >= 70 მმ) — მაღალი რუპტურის რისკი, დაჩქარებული მომზადება!'
                red_flags.append(msg)
                priority_flags.append(msg)
            elif aaa_mm < min_thresh:
                if aaa_morph in ('saccular', 'rapid_growth', 'inflammatory'):
                    morph_reason = {
                        'saccular': 'საკულარული მორფოლოგია',
                        'rapid_growth': 'სწრაფი ზრდის დინამიკა (≥10 მმ/წ)',
                        'inflammatory': 'ანთებითი მორფოლოგია / პერიაორტიტი',
                    }[aaa_morph]
                    amber_flags.append(
                        f'ESVS 2024: {morph_reason} ({aaa_mm} მმ) — '
                        f'ოპერაციული ჩვენება დასაბუთებულია სტანდარტულ დიამეტრამდეც ({min_thresh} მმ).'
                    )
                elif aaa_morph != 'inflammatory_mycotic' and not (q_filled and q.get('aaa_pain')):
                    red_flags.append(
                        f'ფუზიფორმული ანევრიზმის დიამეტრი ({aaa_mm} მმ) სტანდარტულ ზღვარზე (< {min_thresh} მმ) ნაკლებია '
                        '(ESVS 2024 Class III) — გეგმური ოპერაციული ჩვენება გადაამოწმეთ ქირურგთან!'
                    )
            if patient.age is not None and patient.age < 50:
                amber_flags.append(
                    f'ახალგაზრდა ასაკში გამოვლენილი აორტის ანევრიზმა ({patient.age} წ. < 50 წ.) — ESVS 2024: '
                    'გამოირიცხოს შემაერთებელი ქსოვილის გენეტიკური არტერიოპათია (Marfan / Loeys-Dietz / ვასკულიტი) და მთელი აორტის პათოლოგია.'
                )

        if aaa_neck == 'hostile_neck':
            if patient.op_subtype == 'evar':
                red_flags.append(
                    'CTA-ზე Hostile Neck (ყელი <10–15 მმ / კუთხე >60° / თრომბი-კალციფიკაცია — IFU-ს გარეთ)! '
                    'ESVS 2024 Class III: სტანდარტული EVAR არ არის რეკომენდებული — შეათანხმეთ ღია რეზექცია ან FEVAR ქირურგთან!'
                )
            elif patient.op_subtype == 'open':
                amber_flags.append(
                    'CTA-ზე რთული პროქსიმალური ყელი (Hostile / იუქსტარენალური ყელი) ღია რეზექციაზე — '
                    'დაიგეგმოს იუქსტა-/სუპრარენალური კლამპირების მზადყოფნა და თირკმლის ცივი პერფუზია / მანიტოლი!'
                )
            elif patient.op_subtype == 'tbd':
                amber_flags.append(
                    'CTA-ზე Hostile Neck (IFU-ს გარეთ) — ტაქტიკის შერჩევისას სტანდარტული EVAR არ არის რეკომენდებული; განიხილეთ ღია რეზექცია ან FEVAR.'
                )
        elif patient.op_subtype == 'evar' and aaa_mm is not None and (
            aaa_neck == 'pending' or (s.get('aaa_iliac_access') or 'pending') == 'pending'
        ):
            amber_flags.append(
                'EVAR-ის დაგეგმვისას CTA-ზე შეაფასეთ აორტის პროქსიმალური ყელის (IFU) და თეძო-ბარძაყის მიდგომის (≥7 მმ) სტატუსი.'
            )
        if pop_scr == 'aneurysm_found':
            red_flags.append(
                'გამოვლინდა თანმხლები მუხლქვეშა/ბარძაყის ანევრიზმა (ESVS 2024: 10–15% თანაარსებობა AAA-სთან) — '
                'დისტალური თრომბოემბოლიის რისკი, დაგეგმეთ ეტაპობრივი/თანმხლები მკურნალობა ქირურგთან!'
            )

        if days > t_days and not patient.approved_at and not is_on_hold and is_active_case:
            msg = f'გახანგრძლივებული წინასაოპერაციო მომზადება: გავიდა {days} დღე (სამიზნე ლიმიტი: {t_days} დღე) — რუპტურის რისკი!'
            red_flags.append(msg)
            priority_flags.append(msg)
        elif days in (5, 6) and not patient.approved_at and not is_on_hold and is_active_case:
            amber_flags.append(f'მომზადების მე-{days} დღე ({t_days} დღიდან) — დააჩქარეთ დარჩენილი კვლევები.')

        if cta_months is not None:
            if cta_months > 6 or (cta_months > 3 and aaa_mm is not None and aaa_mm >= 65):
                red_flags.append(f'კტ-ანგიოგრაფია ხანდაზმულია ({cta_months} თვის წინანდელი) — შეათანხმეთ განმეორებითი CTA ქირურგთან.')
        if q_filled and not s.get('cta_disc'):
            amber_flags.append('აორტის კტ-ანგიოგრაფიის DICOM დისკი ჯერ არ არის მოტანილი / შემოწმებული.')

        if q_filled and q.get('cfs_level') == '5_plus' and patient.op_subtype == 'open':
            amber_flags.append('დაუძლურების მაღალი ინდექსი (CFS ≥ 5) — ESVS 2024-ით ღია რეზექციასთან შედარებით განიხილეთ EVAR ან რისკის გადაფასება.')
        if s.get('aaa_iliac_access') == 'narrow_calcified':
            amber_flags.append('CTA-ზე ვიწრო (<7 მმ) ან მძიმედ კალციფიცირებული თეძო-ბარძაყის მიდგომა — EVAR-ისას საჭიროა Iliac Conduit / მიდგომის დაგეგმვა.')
        elif s.get('aaa_iliac_access') == 'iliac_aneurysm':
            amber_flags.append('თანმხლები თეძოს არტერიის ანევრიზმა CTA-ზე — დასაგეგმია შიგნითა თეძოს არტერიის (IIA) შენარჩუნება.')
        if s.get('aaa_renal_vein_anom'):
            amber_flags.append('CTA-ზე რეტროაორტული მარცხენა თირკმლის ვენა / ცხენისებრი თირკმელი — საყურადღებოა აორტის ყელის დისექციისა და კლამპირებისას!')
        if q_filled and q.get('aaa_pedal_pulse') == 'reduced':
            needs_abi_check = True
            reflex_orders.append({
                'key': 'aaa_abi',
                'trigger': 'ტერფის პულსაცია მიღებისას დაქვეითებულია',
                'action': 'საბაზისო გოჯ-მხრის ინდექსი (ABI) + მუხლქვეშა არტერიების ექო-სკრინინგი ოპერაციამდე',
                'done': min_abi is not None,
            })

    elif pathway == PreopPatient.Pathway.CEA:
        days_since_ev = cea_live_days_since_event(q, today=today, s=s)
        symp_within_6m = (days_since_ev is None) or (days_since_ev <= 180)
        is_symp_cea = (
            (patient.op_subtype == PreopPatient.OpSubtype.CEA_SYMP and symp_within_6m)
            or (q_filled and bool(q.get('cea_symp_event')) and symp_within_6m)
        )

        if days_since_ev is not None and days_since_ev > 180 and (patient.op_subtype == PreopPatient.OpSubtype.CEA_SYMP or q.get('cea_symp_event')):
            amber_flags.append(
                f'ნევროლოგიური ეპიზოდიდან გასულია {days_since_ev} დღე (>6 თვე) — ESVS 2023 გაიდლაინით განიხილება როგორც ასიმპტომური სტენოზი.'
            )
        elif patient.op_subtype == PreopPatient.OpSubtype.CEA_ASYMP and q_filled and q.get('cea_symp_event'):
            amber_flags.append(
                'კითხვარით დაფიქსირდა ნევროლოგიური სიმპტომი ბოლო 6 თვეში — გადაამოწმეთ სიმპტომური CEA-ს სტატუსი (14-დღიანი ფანჯარა)!'
            )
        if q_filled and q.get('cea_crescendo'):
            red_flags.append(
                'Crescendo TIA / მერყევი ნევროლოგიური დეფიციტი ბოლო 24–48 სთ-ში — სასწრაფოდ აცნობეთ ქირურგსა და ნევროლოგს!'
            )
        if q_filled and q.get('cea_thrombolysis'):
            if (s.get('brain_imaging') == 'normal_small') and days_since_ev is not None and days_since_ev >= 2:
                red_flags.append(
                    'ინტრავენური თრომბოლიზისი ბოლო 6 დღეში (ESVS 2023) — >24 სთ-ის შემდეგ საკონტროლო CT-ით ჰემორაგიის გამორიცხვისას '
                    'დასაშვებია ასპირინი 75–100 მგ ქირურგ/ნევროლოგთან შეთანხმებით, ხოლო ნაადრევი სისტემური ჰეპარინიზაცია/CEA ≤6 დღეში საშიშია!'
                )
            else:
                red_flags.append(
                    'ინტრავენური თრომბოლიზისი ბოლო 6 დღეში (ESVS 2023) — ნაადრევი ჰეპარინიზაციის/სისხლჩაქცევის რისკი, შეათანხმეთ ოპერაციის თარიღი ქირურგთან!'
                )
        if q_filled and q.get('cea_disabling_stroke'):
            red_flags.append(
                'მძიმე ნარჩენი ნევროლოგიური დეფიციტი (mRS >= 3) — გადაამოწმეთ CEA-ს ჩვენება და ვადები ქირურგთან და ნევროლოგთან!'
            )

        if is_symp_cea and days_since_ev is not None and not patient.approved_at and not is_on_hold and is_active_case:
            if days_since_ev > 14:
                msg = f'ინდექსური სიმპტომიდან გავიდა {days_since_ev} დღე — გადაცილებულია ESVS 2023-ის 14-დღიანი ოპტიმალური ფანჯარა! დააჩქარეთ ქირურგის შეფასება.'
                red_flags.append(msg)
                priority_flags.append(msg)
            elif days_since_ev >= 10:
                amber_flags.append(
                    f'სიმპტომიდან გასულია {days_since_ev} დღე (ESVS 14-დღიანი ფანჯრიდან დარჩა {14 - days_since_ev} დღე) — დააჩქარეთ კვლევები!'
                )

        if days > t_days and not patient.approved_at and not is_on_hold and is_active_case:
            if is_symp_cea:
                msg = f'გახანგრძლივებული მომზადება სიმპტომურ CEA-ზე: გავიდა {days} დღე (სამიზნე ლიმიტი: {t_days} დღე) — განმეორებითი ინსულტის რისკი!'
            else:
                msg = f'გახანგრძლივებული წინასაოპერაციო მომზადება: გავიდა {days} დღე (სამიზნე ლიმიტი: {t_days} დღე).'
            red_flags.append(msg)
            priority_flags.append(msg)
        elif not patient.approved_at and not is_on_hold and is_active_case and days >= max(4, t_days - 1):
            amber_flags.append(f'მომზადების მე-{days} დღე ({t_days} დღიდან) — დააჩქარეთ დარჩენილი კვლევები.')

        if patient.side == 'B':
            bilat_cea_msg = (
                'ერთდროული ორმხრივი კაროტიდული ენდარტერექტომია (CEA, მხარე B) აბსოლუტურად უკუნაჩვენებია — '
                'ორმხრივი კრანიალური ნერვების დამბლისა და ასფიქსიის ლეტალური რისკი (ESVS 2023)! დაგეგმეთ ეტაპობრივად (R ან L).'
            )
            red_flags.append(bilat_cea_msg)
            hard_contraindications.append(bilat_cea_msg)
        elif q_filled and patient.side not in ('R', 'L'):
            amber_flags.append('მიუთითეთ დაგეგმილი CEA-ს მხარე (მარჯვენა ან მარცხენა) პაციენტის ბარათში.')

        ipsi_car = car_r if patient.side == 'R' else (car_l if patient.side == 'L' else max_car)
        contra_car = (
            car_l if patient.side == 'R'
            else (car_r if patient.side == 'L' else (min(car_r, car_l) if (car_r is not None and car_l is not None) else None))
        )

        if ipsi_car is not None:
            side_prefix = f'{patient.side} ' if patient.side in ('R', 'L') else ''
            key_badge = f'{side_prefix}{ipsi_car}%'
            key_badge_urgent = bool(is_symp_cea or ipsi_car >= 90)
            if ipsi_car >= 100:
                occ_msg = (
                    'საოპერაციო მხარეს ICA-ს სრული ოკლუზია (100%) — CEA სრულ ქრონიკულ ოკლუზიაზე არ ტარდება! '
                    'სასწრაფოდ გადაამოწმეთ CTA ქირურგთან (near-occlusion vs ოკლუზია).'
                )
                red_flags.append(occ_msg)
                hard_contraindications.append(occ_msg)
            elif is_symp_cea and ipsi_car < 50:
                red_flags.append(f'საოპერაციო მხარეს სტენოზი ({ipsi_car}%) < 50% (NASCET) — გადაამოწმეთ CEA-ს ჩვენება ქირურგთან.')
            elif not is_symp_cea and ipsi_car < 60:
                red_flags.append(f'ასიმპტომურ საოპერაციო მხარეს სტენოზი ({ipsi_car}%) < 60% (NASCET) — გადაამოწმეთ CEA-ს ჩვენება ქირურგთან.')
            elif not is_symp_cea and ipsi_car < 70:
                amber_flags.append(f'ასიმპტომური სტენოზი {ipsi_car}% (60–69% NASCET) — გადაამოწმეთ მაღალი რისკის ბალთის ნიშნები CTA/დუპლექსზე.')
        elif is_symp_cea and days_since_ev is not None:
            key_badge = f'{days_since_ev}/14დღ'
            key_badge_urgent = True

        if contra_car is not None:
            if contra_car >= 80:
                red_flags.append(
                    f'კონტრალატერალური საძილე არტერიის კრიტიკული სტენოზი / ოკლუზია ({contra_car}%) — '
                    'ინტრაოპერაციული შუნტის (Shunt) მზადყოფნა და ჰიპერპერფუზიის მაღალი რისკი!'
                )
            elif contra_car >= 50:
                amber_flags.append(f'კონტრალატერალური საძილე არტერიის სტენოზი ({contra_car}%) — საყურადღებოა ცერებრული პერფუზიისთვის.')

        cea_cta = s.get('cea_cta_status') or 'pending'
        if cea_cta == 'high_calcified':
            amber_flags.append('კისრის/რკალის CTA-ზე მაღალი ბიფურკაცია (C2) / მძიმე კალციფიკაცია / კინკინგი — აცნობეთ ქირურგს.')
        if q_filled and q.get('cea_neck_limited'):
            amber_flags.append('კისრის გაშლა მკვეთრად შეზღუდულია — საყურადღებოა საოპერაციო პოზიციონირებისა და ინტუბაციისთვის.')

        needs_brain_ct = is_symp_cea or (q_filled and bool(q.get('neuro')))
        brain_im = s.get('brain_imaging') or 'pending'
        if brain_im == 'large_or_hemorrhage':
            hem_msg = (
                'თავის ტვინის CT/MRI-ზე სისხლჩაქცევა ან ფართო მწვავე ინფარქტი (>1/3 MCA) — '
                'ნაადრევი CEA საშიშია, სასწრაფოდ აცნობეთ ქირურგსა და ნევროლოგს!'
            )
            red_flags.append(hem_msg)
            hard_contraindications.append(hem_msg)
        if q_filled and not s.get('cta_disc'):
            amber_flags.append('კისრის/თავის ტვინის CTA/MRI DICOM დისკი ჯერ არ არის მოტანილი / შემოწმებული.')

        needs_neuro = is_symp_cea or (q_filled and bool(q.get('neuro')))
        needs_laryngoscopy = q_filled and (bool(q.get('cea_prior_neck')) or bool(q.get('cea_hoarseness')))
        laryng_res = s.get('laryngoscopy_res') or 'pending'
        if laryng_res == 'palsy':
            red_flags.append(
                'ლარინგოსკოპიით გამოვლინდა სახმო იოგის პარეზი — ორმხრივი ნერვის დაზიანებისა და ასფიქსიის საფრთხე! '
                'სასწრაფოდ შეათანხმეთ ქირურგთან (CAS vs CEA).'
            )

        cea_neck_set = set(q.get('cea_neck_chips') or [])
        lvef_prelim = _int(s.get('lvef'), lo=5, hi=95)
        cea_high_surg_risk = bool(
            ('neck_rad' in cea_neck_set) or ('ipsi_restenosis' in cea_neck_set)
            or laryng_res == 'palsy' or cea_cta == 'high_calcified'
            or (lvef_prelim is not None and lvef_prelim < 40)
        )
        if cea_high_surg_risk:
            amber_flags.append(
                'ESVS 2023 მაღალი ქირურგიული რისკი ღია CEA-სთვის (დასხივება / რესტენოზი / C2 მაღალი ბიფურკაცია / სახმო იოგი / LVEF <40%) — '
                'ქირურგთან განიხილეთ კაროტიდული სტენტირება (CAS / TCAR).'
            )

        # ESVS 2023 Class I: perioperative antiplatelet is mandatory BEFORE CEA (holding OAC alone leaves patient unprotected),
        # UNLESS acute intracranial hemorrhage/large infarct or recent IV thrombolysis prohibits immediate antiplatelet initiation!
        antiplatelet_prohibited_cea = (brain_im == 'large_or_hemorrhage') or (q_filled and bool(q.get('cea_thrombolysis')))
        if q_filled and not has_antiplatelet and not antiplatelet_prohibited_cea:
            needs_cea_antiplatelet = True
            has_oac_only = bool(q.get('med_doac') or q.get('med_dabigatran') or q.get('med_warfarin'))
            ap_trigger = (
                'პაციენტი იღებს მხოლოდ ანტიკოაგულანტს (რომელიც ოპერაციამდე ჩერდება) ანტიაგრეგანტის გარეშე!'
                if has_oac_only else 'CEA-ს წინ პაციენტი არ იღებს ანტიაგრეგანტს (ESVS 2023 Class I)'
            )
            reflex_orders.append({
                'key': 'cea_antiplatelet',
                'trigger': ap_trigger,
                'action': 'თავის ტვინის CT-ით სისხლჩაქცევის გამორიცხვის შემდეგ დაუყოვნებლივ დაიწყოს ასპირინი (± კლოპიდოგრელი სიმპტომურ CEA-ზე) ქირურგთან შეთანხმებით',
                'done': bool(s.get('cea_antiplatelet_started')),
            })
            if not s.get('cea_antiplatelet_started'):
                red_flags.append(
                    'პაციენტი არ იღებს ანტიაგრეგანტს (ასპირინი / კლოპიდოგრელი)! ESVS 2023 Class I-ით CEA-ს წინ '
                    'ანტიაგრეგანტული თერაპია სავალდებულოა ინსულტის პრევენციისთვის — აცნობეთ ქირურგს.'
                )
        if q_filled and not has_statin and not liver_3x:
            amber_flags.append('პაციენტს არ უფიქსირდება სტატინის მიღება (ESVS 2023 Class I: CEA-ს წინ ნაჩვენებია მაღალი ინტენსივობის სტატინი).')
        elif ldl_val is not None and ldl_val >= 1.4 and not liver_3x:
            amber_flags.append(f'LDL ქოლესტერინი {ldl_val} მმოლ/ლ (>= 1.4 მმოლ/ლ) — ESVS 2023/ESC სამიზნისთვის განიხილეთ სტატინის მაქსიმალური დოზა ± ეზეტიმიბი.')

    elif pathway == PreopPatient.Pathway.ABF:
        is_clti = (patient.op_subtype == PreopPatient.OpSubtype.ABF_CLTI) or (
            q_filled and (bool(q.get('abf_rest_pain')) or bool(q.get('abf_ulcer')))
        )
        is_clti_any = is_clti
        if patient.op_subtype == PreopPatient.OpSubtype.ABF_CLAUD and q_filled and (q.get('abf_rest_pain') or q.get('abf_ulcer')):
            amber_flags.append(
                'კითხვარით დაფიქსირდა მოსვენებითი ტკივილი ან წყლული (CLTI) — სასურველია ქვეტიპის გადართვა კრიტიკულ იშემიაზე (7-დღიანი რეჟიმი).'
            )
        elif patient.op_subtype == PreopPatient.OpSubtype.ABF_CLAUD and q_filled and not is_clti:
            if not q.get('ic_bmt_3m') or q.get('abf_duration') in ('< 2 კვირა', '1–3 თვე'):
                amber_flags.append(
                    'ხანგამოშვებითი კოჭლობა (ESVS 2024 Class I): ინვაზიურ ჩარევამდე რეკომენდებულია ≥3 თვიანი ოპტიმალური მედიკამენტური თერაპია (BMT) და სავარჯიშო თერაპია (SET).'
                )
        if q_filled and q.get('abf_wet_infection'):
            red_flags.append(
                'ტერფის სველი ნეკროზი / ჩირქოვანი გამონადენი / ფლეგმონა — სინთეზური ბიფურკაციული პროთეზის სეფსისის უმაღლესი რისკი! '
                'სასწრაფოდ აცნობეთ ქირურგს სანაციისა და ეტაპობრივი ტაქტიკის გადასაწყვეტად.'
            )
        if q_filled and q.get('abf_groin_issue'):
            red_flags.append(
                'საზარდულის არეში სოკოვანი გამოყელვა (ინტერტრიგო) / გამონაყარი / ანთებითი კვანძი — '
                'ბარძაყის ანასტომოზისა და პროთეზის ინფიცირების უმაღლესი რისკი! აცნობეთ ქირურგს.'
            )

        if days > t_days and not patient.approved_at and not is_on_hold and is_active_case:
            if is_clti:
                msg = f'გახანგრძლივებული მომზადება CLTI პაციენტთან: გავიდა {days} დღე (სამიზნე ლიმიტი: {t_days} დღე) — კიდურის დაკარგვის რისკი!'
            else:
                msg = f'გახანგრძლივებული წინასაოპერაციო მომზადება: გავიდა {days} დღე (სამიზნე ლიმიტი: {t_days} დღე).'
            red_flags.append(msg)
            priority_flags.append(msg)
        elif not patient.approved_at and not is_on_hold and is_active_case and days >= t_days - 2:
            amber_flags.append(f'მომზადების მე-{days} დღე ({t_days} დღიდან) — დააჩქარეთ დარჩენილი კვლევები.')

        clamp_zone = s.get('abf_clamp_zone') or 'pending'
        if clamp_zone == 'juxtarenal_calcified':
            red_flags.append(
                'CTA-ზე იუქსტარენალური აორტის ოკლუზია ან მძიმე კალციფიკაცია — საჭიროა სუპრარენალური კლამპირებისა და თირკმლის დაცვის დაგეგმვა ქირურგთან!'
            )

        outflow = s.get('abf_outflow') or 'pending'
        if outflow == 'poor_outflow':
            red_flags.append(
                'არადამაკმაყოფილებელი დისტალური Outflow (Profunda + SFA მძიმე დაზიანება) — შუნტის თრომბოზის რისკი! '
                'შეათანხმეთ ჰიბრიდული/დისტალური რეკონსტრუქცია ქირურგთან.'
            )
            needs_alt_vein = True
            reflex_orders.append({
                'key': 'abf_gsv',
                'trigger': 'CTA-ზე მძიმე დისტალური Outflow დაზიანება',
                'action': 'დიდი საჩინო ვენის (GSV) რეფლექს ექო-კარტირება (შესაძლო თანმხლები ბარძაყ-მუხლქვეშა შუნტირებისთვის)',
                'done': (s.get('alt_vein_mapped') or 'pending') != 'pending',
            })
        elif outflow == 'profunda_stenosis':
            amber_flags.append('CFA / Profunda Femoris სტენოზი CTA-ზე — გასათვალისწინებელია თანმხლები ენდარტერექტომია / პროფუნდოპლასტიკა.')

        if s.get('abf_mesenteric') == 'sma_stenosis':
            red_flags.append('CTA-ზე ზემო ჯორჯლის არტერიის (SMA) მნიშვნელოვანი სტენოზი/ოკლუზია — მსხვილი/წვრილი ნაწლავის იშემიის უმაღლესი რისკი აორტაზე ჩარევისას!')
        elif s.get('abf_mesenteric') == 'ima_dominant_iia_occ':
            amber_flags.append('დომინანტური IMA კოლატერალი / ორმხრივი IIA ოკლუზია CTA-ზე — დასაგეგმია IMA რეიმპლანტაცია / მენჯის პერფუზიის შენარჩუნება.')
        if s.get('abf_renal_stenosis'):
            amber_flags.append('თანმხლები თირკმლის არტერიის სტენოზი (≥60%) CTA-ზე — განიხილეთ თანმხლები რენალური ჩარევა / ნეფროპროტექცია.')

        if min_comp_abi is not None:
            key_badge = f'ABI {min_comp_abi}'
            key_badge_urgent = bool(is_clti or min_comp_abi < 0.4)
            if min_comp_abi < 0.4 and not is_clti:
                amber_flags.append(f'კრიტიკულად დაბალი გოჯ-მხრის ინდექსი (ABI {min_comp_abi} < 0.40) — შეესაბამება მძიმე იშემიას.')
        elif min_tp is not None:
            key_badge = f'TP {min_tp}'
            key_badge_urgent = bool(is_clti or min_tp < 30)
        elif min_tbi is not None:
            key_badge = f'TBI {min_tbi}'
            key_badge_urgent = bool(is_clti or min_tbi < 0.30)
        elif is_clti:
            key_badge = 'CLTI'
            key_badge_urgent = True
        elif q.get('abf_claud_m'):
            key_badge = f"{q['abf_claud_m']} მ"

        if q_filled and not s.get('cta_disc'):
            amber_flags.append('აორტო-ილიაკური / ქვემო კიდურების CTA DICOM დისკი ჯერ არ არის მოტანილი / შემოწმებული.')
        if q_filled and not has_any_antithrombotic:
            amber_flags.append('პაციენტი არ იღებს ანტიაგრეგანტს (ESVS 2024 Class I: ასპირინი ან კლოპიდოგრელი).')
        if q_filled and not has_statin and not liver_3x:
            amber_flags.append('პაციენტს არ უფიქსირდება სტატინი (ESVS 2024 Class I: მაღალი ინტენსივობის სტატინი).')
        elif ldl_val is not None and ldl_val >= 1.4 and not liver_3x:
            amber_flags.append(f'LDL ქოლესტერინი {ldl_val} მმოლ/ლ (>= 1.4 მმოლ/ლ) — განიხილეთ ლიპიდდამწევი თერაპიის გაძლიერება (ESVS 2024).')

        needs_wound_care = q_filled and (bool(q.get('abf_ulcer')) or bool(q.get('abf_wet_infection')) or bool(q.get('abf_groin_issue')))

    elif is_pad_bypass:
        is_clti = (patient.op_subtype == PreopPatient.OpSubtype.BYPASS_CLTI) or (
            q_filled and (bool(q.get('abf_rest_pain')) or bool(q.get('abf_ulcer')))
        )
        is_clti_any = is_clti
        if patient.side == 'B':
            red_flags.append(
                'ერთდროული ორმხრივი ინფრაინგვინალური შუნტირება (მხარე B) დაკავშირებულია მაღალ გართულებებთან — დააზუსტეთ ეტაპობრივი მხარე (R ან L) ქირურგთან!'
            )
        elif q_filled and patient.side not in ('R', 'L'):
            amber_flags.append('მიუთითეთ საოპერაციო კიდურის მხარე (მარჯვენა ან მარცხენა).')
        if q_filled and q.get('abf_wet_infection'):
            red_flags.append('ტერფის სველი განგრენა / ჩირქოვანი ინფექცია — სასწრაფოდ აცნობეთ ქირურგს დრენირებისა და ანტიბიოტიკოთერაპიისთვის!')
        if q_filled and q.get('abf_groin_issue'):
            red_flags.append('საზარდულის არეში ინტერტრიგო / ინფექცია — შუნტის ინფიცირების მაღალი რისკი! აცნობეთ ქირურგს.')
        if days > t_days and not patient.approved_at and not is_on_hold and is_active_case:
            msg = f'გახანგრძლივებული მომზადება პერიფერიულ შუნტირებაზე: გავიდა {days} დღე (ლიმიტი: {t_days} დღე).'
            red_flags.append(msg)
            priority_flags.append(msg)

        runoff = s.get('pad_runoff_status') or 'pending'
        if runoff == 'desert_foot':
            if not is_clti:
                df_msg = (
                    'ხანგამოშვებითი კოჭლობის (IC) დროს Desert foot კალაპოტზე ინფრაინგვინალური შუნტირება '
                    'უკუნაჩვენებია (ESVS 2024 Class III — თრომბოზისა და კიდურის დაკარგვის რისკი)! გადაამოწმეთ ჩვენება ქირურგთან!'
                )
                red_flags.append(df_msg)
                hard_contraindications.append(df_msg)
            else:
                red_flags.append('CTA/ანგიოგრაფიით კრიტიკულად დაზიანებული დისტალური კალაპოტი (Desert foot) — შუნტის ადრეული თრომბოზის უმაღლესი რისკი! აცნობეთ ქირურგს.')
        elif runoff == 'tibial_single':
            amber_flags.append('დისტალური მიმღები მხოლოდ წვივის ერთი არტერიაა — აუცილებელია ხარისხიანი აუტოვენა (GSV ≥ 3.0 მმ).')

        gsv_st = s.get('pad_gsv_status') or 'pending'
        gsv_inadequate = (
            gsv_st == 'inadequate'
            or (pad_gsv_mm is not None and pad_gsv_mm < 3.0)
            or ('vein_strip' in vasc_chips)
        )
        if gsv_inadequate:
            needs_alt_vein = True
            reflex_orders.append({
                'key': 'alt_vein',
                'trigger': 'იფსილატერალური GSV უვარგისია (< 3.0 მმ ან ამოღებულია)',
                'action': 'ალტერნატიული აუტოვენის რეფლექს-კარტირება (კონტრალატერალური GSV, SSV და ხელის ვენები — Cephalic/Basilic)',
                'done': (s.get('alt_vein_mapped') or 'pending') != 'pending',
            })
            if s.get('alt_vein_mapped') == 'contra_gsv_ok':
                if patient.side == 'B':
                    red_flags.append('ორმხრივი შუნტირებისას (მხარე B) „კონტრალატერალური GSV“ წინააღმდეგობრივია — დააზუსტეთ საოპერაციო მხარე და კონდუიტი!')
                elif (contra_abi is not None and contra_abi < 0.50) or (contra_tp is not None and contra_tp < 40):
                    red_flags.append(
                        f'კონტრალატერალურ კიდურზე მძიმე არტერიული იშემიაა (ABI {contra_abi if contra_abi is not None else "—"} / '
                        f'TP {contra_tp if contra_tp is not None else "—"} mmHg) — იშემიური კიდურიდან GSV-ის აღება საშიშია ჭრილობის ნეკროზის გამო! განიხილეთ ხელის ვენა (Arm Vein).'
                    )
                elif (contra_abi is not None and contra_abi < 0.70) or (contra_tp is not None and contra_tp < 60):
                    amber_flags.append(
                        f'დონორ (კონტრალატერალურ) კიდურზე დაქვეითებული პერფუზიაა (ABI {contra_abi if contra_abi is not None else "—"} / '
                        f'TP {contra_tp if contra_tp is not None else "—"}) — GSV-ის აღებისას საყურადღებოა ჭრილობის შეხორცება!'
                    )
                elif contra_abi is not None and contra_abi > 1.40 and not (contra_tp is not None and contra_tp >= 60):
                    amber_flags.append(
                        f'დონორ კიდურზე არაკომპრესირებადი ABI ({contra_abi} > 1.40) — კონტრალატერალური GSV-ის აღებამდე გადაამოწმეთ Toe Pressure ≥60 mmHg!'
                    )
                elif contra_abi is None and contra_tp is None:
                    amber_flags.append('კონტრალატერალური GSV-ის აღებამდე გადაამოწმეთ დონორი კიდურის ABI / პულსაცია!')
            if s.get('alt_vein_mapped') == 'no_autovein_prosthetic':
                if not is_clti:
                    pros_ic_msg = (
                        'ხანგამოშვებითი კოჭლობის (IC) დროს აუტოვენის არარსებობისას სინთეზური ინფრაინგვინალური შუნტირება '
                        'უკუნაჩვენებია (ESVS 2024 / ACC-AHA Class III — Harm)! გადაამოწმეთ ჩვენება ქირურგთან!'
                    )
                    red_flags.append(pros_ic_msg)
                    hard_contraindications.append(pros_ic_msg)
                else:
                    red_flags.append('ვარგისი აუტოვენა ვერცერთ კიდურზე ვერ მოიძებნა — შეათანხმეთ სინთეზური/კომპოზიტური პროთეზის ან ენდოვასკულური ტაქტიკა ქირურგთან!')

        if not is_clti and patient.side in ('R', 'L'):
            t_abi_byp = abi_r if patient.side == 'R' else abi_l
            t_tp_byp = tp_r if patient.side == 'R' else tp_l
            if t_abi_byp is not None and 0.90 <= t_abi_byp <= 1.40 and not (t_tp_byp is not None and t_tp_byp < 60):
                red_flags.append(
                    f'საოპერაციო კიდურზე ({patient.side}) ფიქსირდება ნორმალური ABI ({t_abi_byp} — 0.90–1.40) კოჭლობის ფონზე — '
                    'გადაამოწმეთ არჩეული მხარე (R/L) ან დატვირთვის შემდგომი პერფუზია ქირურგთან!'
                )
        if q_filled and not is_clti:
            if not q.get('ic_bmt_3m') or q.get('abf_duration') in ('< 2 კვირა', '1–3 თვე'):
                amber_flags.append(
                    'ხანგამოშვებითი კოჭლობა (ESVS 2024 Class I): ინვაზიურ ჩარევამდე რეკომენდებულია ≥3 თვიანი ოპტიმალური მედიკამენტური თერაპია (BMT) და სავარჯიშო თერაპია (SET).'
                )

        if pad_gsv_mm is not None:
            key_badge = f'GSV {pad_gsv_mm}მმ'
            key_badge_urgent = bool(pad_gsv_mm < 3.0 or is_clti)
        elif min_comp_abi is not None:
            key_badge = f'ABI {min_comp_abi}'
            key_badge_urgent = bool(is_clti or min_comp_abi < 0.4)
        elif min_tp is not None:
            key_badge = f'TP {min_tp}'
            key_badge_urgent = bool(is_clti or min_tp < 30)
        elif is_clti:
            key_badge = 'CLTI'
            key_badge_urgent = True

        if q_filled and not s.get('cta_disc'):
            amber_flags.append('ქვემო კიდურების ანგიოგრაფიის / CTA DICOM დისკი ჯერ არ არის მოტანილი.')
        if q_filled and not has_any_antithrombotic:
            amber_flags.append('პაციენტი არ იღებს ანტიაგრეგანტს (ESVS 2024 Class I).')
        if q_filled and not has_statin and not liver_3x:
            amber_flags.append('პაციენტს არ უფიქსირდება სტატინი (ESVS 2024 Class I).')
        elif ldl_val is not None and ldl_val >= 1.4 and not liver_3x:
            amber_flags.append(f'LDL ქოლესტერინი {ldl_val} მმოლ/ლ (>= 1.4 მმოლ/ლ) — განიხილეთ სტატინის ინტენსიფიკაცია ± ეზეტიმიბი.')
        needs_wound_care = q_filled and (bool(q.get('abf_ulcer')) or bool(q.get('abf_wet_infection')) or bool(q.get('abf_groin_issue')))

    elif is_pta:
        is_clti = (patient.op_subtype == PreopPatient.OpSubtype.PTA_CLTI) or (
            q_filled and (bool(q.get('abf_rest_pain')) or bool(q.get('abf_ulcer')))
        )
        is_clti_any = is_clti
        if q_filled and patient.side not in ('R', 'L', 'B'):
            amber_flags.append('მიუთითეთ ჩარევის მხარე (მარჯვენა, მარცხენა ან ორმხრივი).')
        if q_filled and q.get('abf_wet_infection'):
            red_flags.append('ტერფის სველი ინფექცია / ფლეგმონა — რევასკულარიზაციასთან ერთად საჭიროა გადაუდებელი ქირურგიული სანაცია!')
        if q_filled and q.get('abf_groin_issue'):
            red_flags.append('საზარდულის არეში ინტერტრიგო / ინფექცია — პუნქციის ზონის ინფიცირების რისკი! განიხილეთ ალტერნატიული მიდგომა და სანაცია.')
        if days > t_days and not patient.approved_at and not is_on_hold and is_active_case:
            msg = f'გახანგრძლივებული მომზადება PTA/სტენტირებაზე: გავიდა {days} დღე (ლიმიტი: {t_days} დღე).'
            red_flags.append(msg)
            priority_flags.append(msg)

        if not is_clti and (s.get('pta_lesion_site') == 'infrapopliteal'):
            btk_ic_msg = (
                'ხანგამოშვებითი კოჭლობის (IC) დროს წვივის/ტერფის არტერიებზე (BTK / Infrapopliteal) ენდოვასკულური ჩარევა '
                'უკუნაჩვენებია (ESVS 2024 / ACC-AHA 2024 Class III — Harm)! გადაამოწმეთ ჩვენება ქირურგთან.'
            )
            red_flags.append(btk_ic_msg)
            hard_contraindications.append(btk_ic_msg)
        elif not is_clti and (s.get('pta_lesion_site') == 'multilevel'):
            amber_flags.append(
                'მრავალდონიანი დაზიანება ხანგამოშვებითი კოჭლობისას — დარწმუნდით, რომ არ იგეგმება იზოლირებული წვივის (BTK) არტერიების ჩარევა (ESVS 2024 Class III).'
            )
        if not is_clti and patient.side in ('R', 'L'):
            t_abi_pta = abi_r if patient.side == 'R' else abi_l
            t_tp_pta = tp_r if patient.side == 'R' else tp_l
            if t_abi_pta is not None and 0.90 <= t_abi_pta <= 1.40 and not (t_tp_pta is not None and t_tp_pta < 60):
                red_flags.append(
                    f'საოპერაციო კიდურზე ({patient.side}) ფიქსირდება ნორმალური ABI ({t_abi_pta} — 0.90–1.40) კოჭლობის ფონზე — '
                    'გადაამოწმეთ არჩეული მხარე (R/L) ან ჩვენება ქირურგთან!'
                )
        if q_filled and not is_clti:
            if not q.get('ic_bmt_3m') or q.get('abf_duration') in ('< 2 კვირა', '1–3 თვე'):
                amber_flags.append(
                    'ხანგამოშვებითი კოჭლობა (ESVS 2024 Class I): ინვაზიურ ჩარევამდე რეკომენდებულია ≥3 თვიანი ოპტიმალური მედიკამენტური თერაპია (BMT) და სავარჯიშო თერაპია (SET).'
                )

        if s.get('pta_access_site') == 'cfa_calcified_scar':
            amber_flags.append('პუნქციის ზონაში (CFA) ნაწიბური / მძიმე კალციფიკაცია — განიხილეთ კონტრალატერალური, სხივის (Radial) ან მხრის მიდგომა ექო-კონტროლით.')
        if q_filled and q.get('pta_cant_lie_flat'):
            amber_flags.append('პაციენტს უჭირს ზურგზე გაუნძრევლად წოლა 60–90 წთ — ავტომატურად ჩაირთო ანესთეზიოლოგი (სედაციისთვის).')
        if 'clopidogrel_intol' in allergy_chips:
            has_oac_pta = bool(q_filled and (q.get('med_warfarin') or q.get('med_doac') or q.get('med_dabigatran')))
            alt_pta_txt = (
                'OAC + ასპირინი 75–100 მგ (OAC + ტიკაგრელორი ESC Class III სისხლდენის რისკის გამო არ ინიშნება!)'
                if has_oac_pta else 'ალტერნატიული P2Y12 (ტიკაგრელორი)'
            )
            red_flags.append(f'ანამნეზში კლოპიდოგრელის აუტანლობა — სტენტირებამდე შეათანხმეთ {alt_pta_txt} ქირურგთან!')
        if q_filled and not has_any_antithrombotic:
            amber_flags.append('პაციენტი არ იღებს ანტიაგრეგანტს PTA/სტენტირებამდე (ESVS 2024 Class I).')
        if q_filled and not has_statin and not liver_3x:
            amber_flags.append('პაციენტს არ უფიქსირდება სტატინი (ESVS 2024 Class I).')
        elif ldl_val is not None and ldl_val >= 1.4 and not liver_3x:
            amber_flags.append(f'LDL ქოლესტერინი {ldl_val} მმოლ/ლ (>= 1.4 მმოლ/ლ) — განიხილეთ სტატინის ინტენსიფიკაცია ± ეზეტიმიბი.')

        if min_comp_abi is not None:
            key_badge = f'ABI {min_comp_abi}'
            key_badge_urgent = bool(is_clti or min_comp_abi < 0.4)
        elif min_tp is not None:
            key_badge = f'TP {min_tp}'
            key_badge_urgent = bool(is_clti or min_tp < 30)
        elif is_clti:
            key_badge = 'CLTI'
            key_badge_urgent = True
        needs_wound_care = q_filled and (bool(q.get('abf_ulcer')) or bool(q.get('abf_wet_infection')) or bool(q.get('abf_groin_issue')))

    elif is_venous:
        ceap = q.get('ven_ceap') if q.get('ven_ceap') in ('C2', 'C3', 'C4', 'C5', 'C6') else 'C2'
        ven_dup = s.get('ven_duplex_status') or 'pending'
        gsv_mm = _num(s.get('ven_gsv_mm'), lo=1, hi=50)
        if patient.op_subtype == PreopPatient.OpSubtype.EVLA and weight_num is not None:
            ht_cm_lido = _num(s.get('height_cm'), lo=100, hi=230)
            if bmi is not None and bmi >= 30 and ht_cm_lido is not None:
                ibw_lido = (45.5 if patient.sex == 'F' else 50.0) + 0.91 * max(0.0, ht_cm_lido - 152.4)
                dosing_wt_lido = min(weight_num, ibw_lido + 0.4 * max(0.0, weight_num - ibw_lido))
            else:
                dosing_wt_lido = weight_num
            _, chf_lido = _ihd_and_chf_flags(q, s)
            reduced_lido_cl = bool(
                chf_lido
                or (s.get('liver_status') in ('2x', '3x'))
                or ('amiodarone' in med_chips)
                or ((patient.age or 0) >= 80 and (on_dialysis or (eff_renal is not None and eff_renal < 45)))
            )
            if reduced_lido_cl:
                lidocaine_max_mg = min(300, int(round(dosing_wt_lido * 7.0 * 0.65)))
            else:
                lidocaine_max_mg = min(500, int(round(dosing_wt_lido * 7.0)))

        if ven_dup == 'acute_dvt':
            dvt_msg = 'ვენების დუპლექსით მწვავე ღრმა ვენების თრომბოზი (DVT) — გეგმური EVLA / ფლებექტომია აბსოლუტურად უკუნაჩვენებია! სასწრაფოდ აცნობეთ ქირურგს.'
            red_flags.append(dvt_msg)
            hard_contraindications.append(dvt_msg)
        elif ven_dup == 'deep_obstruction':
            obs_msg = 'ღრმა ვენური სისტემის ოკლუზია (ზედაპირული ვენა მთავარი კოლატერალია) — აბლაცია/ფლებექტომია უკუნაჩვენებია! აცნობეთ ქირურგს.'
            red_flags.append(obs_msg)
            hard_contraindications.append(obs_msg)
        elif ven_dup == 'superficial_thrombus' or (q_filled and q.get('ven_acute_svt')):
            red_flags.append(
                'მწვავე აღმავალი თრომბოფლებიტი (SVT) — გადაამოწმეთ SFJ/SPJ-სთან დისტანცია დუპლექსით და შეათანხმეთ ტაქტიკა ქირურგთან!'
            )

        if q_filled and (q.get('ven_pregnant') or q.get('pregnant_possible')):
            preg_msg = 'ორსულობა / ლაქტაცია / მშობიარობის შემდგომი პერიოდი (<3 თვ) — გეგმური EVLA / ფლებექტომია უკუნაჩვენებია (ESVS 2022)!'
            red_flags.append(preg_msg)
            hard_contraindications.append(preg_msg)
        if q_filled and (q.get('ven_lidocaine_allergy') or 'lidocaine' in allergy_chips):
            red_flags.append(
                'ალერგია ლიდოკაინზე / ადგილობრივ ანესთეტიკზე — ტუმესცენტური ანესთეზია საშიშია! სავალდებულოა ქირურგისა და ანესთეზიოლოგის ჩართვა.'
            )
        if q_filled and q.get('ven_skin_infection'):
            red_flags.append('მწვავე კანის ინფექცია / წითელი ქარი / ჩირქოვანი ეგზემა პუნქციის/ჭრილობის ზონაში — საჭიროა წინასწარი სანაცია!')
        if q_filled and q.get('ven_bleeding'):
            red_flags.append('ანამნეზში სისხლდენა ვარიკოზული კვანძიდან (ESVS 2022) — აცნობეთ ქირურგს პრიორიტეტული ჩარევისთვის!')

        if gsv_mm is not None and gsv_mm >= 15:
            amber_flags.append(f'საჩინო ვენის დიდი დიამეტრი ({gsv_mm} მმ >= 15 მმ) — გაითვალისწინეთ EHIT-ის პრევენცია და ენერგიის რეჟიმი.')
        if s.get('ven_epifascial'):
            amber_flags.append('დუპლექსით ეპიფასციური / კანთან ახლო ღერო (<5 მმ) — საჭიროა ჭარბი ტუმესცენტური ბალიში კანის დამწვრობის/პიგმენტაციის ასაცილებლად.')
        if s.get('ven_tortuous'):
            amber_flags.append('დუპლექსით ძლიერ დაკლაკნილი ღერო / სინექიები — გაითვალისწინეთ დამატებითი პუნქციის ან ფლებექტომიის საჭიროება.')
        if q_filled and ceap == 'C6' and not q.get('ven_skin_infection'):
            amber_flags.append('აქტიური ვენური წყლული (CEAP C6) — ESVS 2022 Class I-ით ადრეული EVLA აჩქარებს წყლულის შეხორცებას.')
        if q_filled and patient.side not in ('R', 'L', 'B'):
            amber_flags.append('მიუთითეთ საოპერაციო კიდური (მარჯვენა, მარცხენა ან ორმხრივი).')

        needs_abi_check = q_filled and (
            bool(q.get('claudication'))
            or ceap == 'C6'
            or (ceap == 'C5' and (bool(q.get('dm')) or (patient.age or 0) >= 65))
        )
        if min_abi is not None:
            if min_abi < 0.50:
                sev_ven_isch = (
                    f'ქვემო კიდურის მძიმე არტერიული იშემია (ABI {min_abi} < 0.80 / < 0.50) — '
                    'ვენური აბლაცია/ფლებექტომია და კომპრესია აბსოლუტურად უკუნაჩვენებია (ESVS 2022)! სასწრაფოდ აცნობეთ ქირურგს.'
                )
                red_flags.append(sev_ven_isch)
                hard_contraindications.append(sev_ven_isch)
            elif min_abi < 0.8:
                red_flags.append(
                    f'ქვემო კიდურის არტერიული უკმარისობა (ABI {min_abi} < 0.80) — კომპრესიული ტრიკოტაჟი და ვენური ჩარევა საშიშია! აცნობეთ ქირურგს.'
                )
            elif min_abi < 0.9:
                amber_flags.append(f'სასაზღვრო გოჯ-მხრის ინდექსი (ABI {min_abi}) — კომპრესიის ხარისხი შეათანხმეთ ქირურგთან.')
        if min_tp is not None and min_tp < 30:
            tp_ven_isch = (
                f'კრიტიკულად დაბალი თითის წნევა (TP {min_tp} mmHg < 30 mmHg) — მძიმე არტერიული იშემია, '
                'გეგმური ვენური ჩარევა და კომპრესია უკუნაჩვენებია (ESVS 2022)!'
            )
            red_flags.append(tp_ven_isch)
            hard_contraindications.append(tp_ven_isch)
        if abi_non_compressible:
            if (min_tp is not None and min_tp < 50) or (min_tbi is not None and min_tbi < 0.50):
                red_flags.append(
                    f'არაკომპრესირებადი ABI ({max_abi}) + დაბალი Toe Pressure/TBI (TP {min_tp if min_tp is not None else "—"} / '
                    f'TBI {min_tbi if min_tbi is not None else "—"}) — არტერიული უკმარისობა, კომპრესიული ტრიკოტაჟი უკუნაჩვენებია!'
                )
            elif tbi_reduced_comp:
                amber_flags.append(
                    f'არაკომპრესირებადი არტერია (ABI {max_abi} > 1.40) + სასაზღვრო TP/TBI '
                    f'(TP {min_tp if min_tp is not None else "—"} / TBI {min_tbi if min_tbi is not None else "—"}) — '
                    '2 კლასის ნაცვლად დასაშვებია მხოლოდ მსუბუქი I კლასის კომპრესია (<20 mmHg) ქირურგთან შეთანხმებით!'
                )
            elif not tbi_comp_safe:
                amber_flags.append(
                    f'არაკომპრესირებადი არტერია (ABI {max_abi} > 1.40) — 2 კლასის კომპრესიული წინდის დანიშვნამდე '
                    'სამიზნე კიდურზე სავალდებულოა Toe Pressure (≥60 mmHg) ან TBI (≥0.70)!'
                )

        high_vte = q_filled and (
            bool(q.get('ven_dvt_history')) or bool(q.get('ven_thrombophilia'))
            or bool(q.get('ven_cancer_immob')) or bool(q.get('ven_acute_svt'))
            or ('hit_heparin' in allergy_chips)
        )
        mod_vte_count = sum([
            bool((patient.age or 0) >= 60),
            bool(bmi is not None and bmi >= 30),
            bool(q_filled and q.get('ven_hormones')),
            is_phleb,
            bool(q_filled and ceap in ('C5', 'C6')),
        ])
        needs_vte_prophylaxis = high_vte or (q_filled and (mod_vte_count >= 2 or caprini_score >= 5))
        if high_vte and not s.get('vte_prophylaxis_planned'):
            ven_severe_renal = on_dialysis or (eff_renal is not None and eff_renal < 30)
            if 'mech_valve' in cad_chips:
                vte_agents = 'ვარფარინი (VKA) კარდიოლოგის სქემით — DOAC აკრძალულია!'
            elif 'hit_heparin' in allergy_chips:
                vte_agents = (
                    'IPC / არგატრობანი-ბივალირუდინი — HIT + CrCl<30: ჰეპარინი, ფონდაპარინუქსი და DOAC აკრძალულია!'
                    if ven_severe_renal else
                    ('ფონდაპარინუქსი — HIT + ღვიძლის ≥3x: ჰეპარინი და DOAC აკრძალულია!' if liver_3x else 'ფონდაპარინუქსი / DOAC — HIT: ჰეპარინი აკრძალულია!')
                )
            elif ven_severe_renal or liver_3x:
                vte_agents = 'კანქვეშა UFH / დოზა-კორექტირებული LMWH (DOAC არა!)'
            else:
                vte_agents = 'LMWH / DOAC'
            amber_flags.append(
                f'მაღალი თრომბოემბოლიური რისკი (Caprini {caprini_score}) — შეათანხმეთ ინდივიდუალური თრომბოპროფილაქტიკა ({vte_agents}) ქირურგთან.'
            )

        needs_wound_care = q_filled and (ceap == 'C6' or bool(q.get('ven_skin_infection')))
        needs_ecg_venous = (
            is_phleb
            or ((patient.age or 0) >= 50)
            or (q_filled and (bool(q.get('htn')) or bool(q.get('cad')) or bool(q.get('dm'))))
            or bool(s.get('irregular_hr'))
        )

        if days > t_days and not patient.approved_at and not is_on_hold and is_active_case:
            amber_flags.append(f'წინასაოპერაციო მომზადებიდან გასულია {days} დღე — გადაამოწმეთ ანალიზების აქტუალობა.')

        if q_filled:
            gsv_part = f' · {int(gsv_mm)}მმ' if gsv_mm else ''
            key_badge = f'{ceap}{gsv_part}'
            key_badge_urgent = bool(q.get('ven_acute_svt') or q.get('ven_bleeding') or ceap == 'C6')
        elif gsv_mm:
            key_badge = f'{int(gsv_mm)} მმ'

    # C10.3: Diabetic / Dialysis CLTI Toe-Pressure / TBI Reflex (ESVS 2024 / WIfI)
    if (
        pathway in (PreopPatient.Pathway.ABF, PreopPatient.Pathway.PAD_BYPASS, PreopPatient.Pathway.PAD_PTA)
        and is_clti_any and q_filled and (bool(q.get('dm')) or on_dialysis)
        and not abi_non_compressible
    ):
        needs_tbi_check = True
        reflex_orders.append({
            'key': 'tbi',
            'trigger': 'შაქრიანი დიაბეტი / დიალიზი + CLTI (მონკებერგის კალციფიკაციისა და ფსევდონორმალური ABI-ის რისკი — ESVS 2024 / WIfI)',
            'action': 'სამიზნე კიდურზე თითის წნევის (Toe Pressure, mmHg) ან TBI განსაზღვრა WIfI სტადირებისა და პერფუზიის ზუსტი შეფასებისთვის',
            'done': tbi_measured,
        })

    # C10.4 & C10.5: Cross-pathway incidental AAA / acute DVT & universal pregnancy radiation/elective barriers
    if pathway != PreopPatient.Pathway.AAA_INFRA and aaa_mm is not None:
        min_t_cross = 50 if patient.sex == 'F' else 55
        if aaa_mm >= min_t_cross:
            red_flags.append(
                f'თანმხლები მუცლის აორტის ანევრიზმა ({aaa_mm} მმ >= {min_t_cross} მმ) — შეათანხმეთ პრიორიტეტულობა და ეტაპობრივი ტაქტიკა ქირურგთან!'
            )
    if not is_venous and (s.get('ven_duplex_status') == 'acute_dvt'):
        red_flags.append(
            'ვენების დუპლექსით გამოვლინდა მწვავე ღრმა ვენების თრომბოზი (DVT) — გეგმურ ჩარევამდე სასწრაფოდ შეათანხმეთ ანტიკოაგულაცია და ტაქტიკა ქირურგთან!'
        )

    show_pregnancy_check = bool((patient.sex == 'F') and (patient.age is None or 12 <= patient.age <= 55))
    eff_pregnant = bool(q_filled and (q.get('pregnant_possible') or q.get('ven_pregnant')))
    if eff_pregnant and not is_venous:
        is_elective_claud = pathway in (
            PreopPatient.Pathway.ABF, PreopPatient.Pathway.PAD_BYPASS, PreopPatient.Pathway.PAD_PTA,
        ) and not is_clti_any
        if (patient.op_subtype in ('evar', 'tbd')) or is_pta:
            preg_art_msg = (
                'ორსულობა / შესაძლო ორსულობა რენტგენო-ფლუოროსკოპიულ ჩარევაზე (EVAR / PTA) — ნაყოფის დასხივებისა და იოდის კონტრასტის რისკი! '
                'სავალდებულოა β-hCG ტესტი, მეან-გინეკოლოგის ჩართვა და გეგმური ჩარევის გადადება!'
            )
        else:
            preg_art_msg = (
                'ორსულობა / შესაძლო ორსულობა — სავალდებულოა β-hCG ვერიფიკაცია, მეან-გინეკოლოგის კონსულტაცია და გეგმური ოპერაციის გადადების განხილვა!'
            )
        red_flags.append(preg_art_msg)
        if is_elective_claud:
            hard_contraindications.append(preg_art_msg)

    # Pre-compute urgency & emergency fast-track flags (UK NVR / CPOC / ESVS)
    is_rupture_suspect = bool(pathway == PreopPatient.Pathway.AAA_INFRA and q_filled and q.get('aaa_pain'))
    is_acute_emergency = bool(q_filled and (bool(q.get('aaa_pain')) or bool(q.get('cea_crescendo'))))
    is_septic_or_mycotic_urgent = bool(
        (pathway == PreopPatient.Pathway.AAA_INFRA and s.get('aaa_morphology') == 'inflammatory_mycotic')
        or (q_filled and bool(q.get('abf_wet_infection')))
    )
    is_urgent_case = bool(
        is_acute_emergency
        or is_septic_or_mycotic_urgent
        or (aaa_mm is not None and aaa_mm >= 70)
        or is_symp_cea
        or is_clti_any
        or (min_comp_abi is not None and min_comp_abi < 0.4)
        or (min_tp is not None and min_tp < 30)
        or (min_tbi is not None and min_tbi < 0.30)
        or (is_venous and q_filled and (bool(q.get('ven_acute_svt')) or bool(q.get('ven_bleeding'))))
        or (days > t_days and not is_venous and not is_on_hold and is_active_case)
    )

    # 2. Vitals & Bilateral Arm BP (ESC 2022 / ESVS 2023)
    cardio_decision = patient.cardio_decision or ''
    cardio_cleared = (cardio_decision == 'cleared')
    anesth_cleared = bool(s.get('anesth_done'))
    anesth_plan_raw = s.get('anesthesia_plan') or 'auto'
    explicit_neuraxial = (anesth_plan_raw == 'neuraxial') or ('epidural' in anesth_lines_set)

    bp_sys, bp_dia = _int(s.get('bp_sys'), lo=30, hi=320), _int(s.get('bp_dia'), lo=20, hi=220)
    bp_sys_l, bp_dia_l = _int(s.get('bp_sys_l'), lo=30, hi=320), _int(s.get('bp_dia_l'), lo=20, hi=220)
    bp_r_inverted = (bp_sys is not None and bp_dia is not None and bp_sys <= bp_dia)
    bp_l_inverted = (bp_sys_l is not None and bp_dia_l is not None and bp_sys_l <= bp_dia_l)
    bp_inverted = bp_r_inverted or bp_l_inverted
    if bp_inverted:
        inv_s = bp_sys if bp_r_inverted else bp_sys_l
        inv_d = bp_dia if bp_r_inverted else bp_dia_l
        red_flags.append(
            f'არტერიული წნევის არასწორი ჩანაწერი (SBP ≤ DBP: {inv_s}/{inv_d} mmHg) — გადაამოწმეთ სისტოლური და დიასტოლური მაჩვენებლები!'
        )
    valid_sys_vals = [
        v for v, inv in ((bp_sys, bp_r_inverted), (bp_sys_l, bp_l_inverted))
        if v is not None and not inv
    ]
    valid_dia_vals = [
        v for v, inv in ((bp_dia, bp_r_inverted), (bp_dia_l, bp_l_inverted))
        if v is not None and not inv
    ]
    eff_sys = max(valid_sys_vals, default=None)
    eff_dia = max(valid_dia_vals, default=None)
    bp_arm_diff = (
        abs(bp_sys - bp_sys_l)
        if (bp_sys is not None and bp_sys_l is not None and not bp_r_inverted and not bp_l_inverted)
        else None
    )

    if bp_arm_diff is not None and bp_arm_diff >= 20:
        higher_arm = 'მარჯვენა' if bp_sys >= bp_sys_l else 'მარცხენა'
        amber_flags.append(
            f'მკლავებს შორის წნევის ასიმეტრია ({bp_arm_diff} mmHg >= 20 mmHg) — საეჭვოა ლავიწქვეშა არტერიის სტენოზი! '
            f'არტერიული წნევის მონიტორინგი უნდა წარმოებდეს მაღალი წნევის ({higher_arm}) მკლავზე.'
        )

    hr, spo2 = _int(s.get('hr'), lo=15, hi=260), _int(s.get('spo2'), lo=30, hi=100)
    shock_index = round(hr / eff_sys, 2) if (hr is not None and eff_sys is not None and eff_sys > 0) else None
    severe_htn = bool((eff_sys and eff_sys >= 180) or (eff_dia and eff_dia >= 110))
    if severe_htn:
        if pathway == PreopPatient.Pathway.CEA:
            red_flags.append(
                f'მაღალი არტერიული წნევა ({eff_sys or "?"}/{eff_dia or "?"} mmHg >= 180/110) — CEA-ზე ცერებრული ჰიპერპერფუზიის, '
                'სისხლჩაქცევისა და კისრის ჰემატომის უმაღლესი რისკი! სავალდებულოა სტაბილიზაცია ოპერაციამდე.'
            )
        else:
            red_flags.append(f'მაღალი არტერიული წნევა ({eff_sys or "?"}/{eff_dia or "?"} mmHg >= 180/110) — საჭიროა კორექცია ოპერაციამდე.')
    elif eff_sys and eff_sys < 90:
        if pathway == PreopPatient.Pathway.AAA_INFRA:
            red_flags.append(f'ჰიპოტენზია ({eff_sys}/{eff_dia or "?"} mmHg) — სასწრაფოდ გამოირიცხოს რუპტურა / შოკი!')
        else:
            red_flags.append(f'ჰიპოტენზია ({eff_sys}/{eff_dia or "?"} mmHg) — სასწრაფოდ შეფასდეს ჰემოდინამიკა!')
    elif eff_sys and eff_sys < 100:
        amber_flags.append(
            f'სასაზღვრო ჰიპოტენზია ({eff_sys}/{eff_dia or "?"} mmHg — SBP 90–99): შეაფასეთ ვოლემია და დილის ანტიჰიპერტენზიული წამლების გამოტოვება.'
        )
    elif (eff_sys and eff_sys >= 160) or (eff_dia and eff_dia >= 100):
        amber_flags.append(f'მომატებული არტერიული წნევა ({eff_sys or "?"}/{eff_dia or "?"} mmHg) — სასურველია ამბულატორიული კორექცია (ESC 2022: სტადია 2 ჰიპერტენზია <180/110 ოპერაციას არ აყოვნებს).')

    if shock_index is not None and shock_index >= 1.0:
        red_flags.append(
            f'შოკის ინდექსი მომატებულია (HR/SBP = {shock_index} >= 1.0) — ფარული ჰიპოვოლემიის / სისხლდენის / სეფსისის რისკი! სასწრაფოდ შეფასდეს ჰემოდინამიკა!'
        )
    if eff_sys is not None and eff_dia is not None and 0 < (eff_sys - eff_dia) < 20:
        amber_flags.append(
            f'ვიწრო პულსური წნევა ({eff_sys - eff_dia} mmHg < 20 mmHg) — გამოირიცხოს ჰიპოვოლემია, აორტის სტენოზი ან დაბალი განდევნა.'
        )

    if hr and (hr > 110 or hr < 45):
        red_flags.append(f'პულსის კრიტიკული ცვლილება (HR {hr}/წთ) — საჭიროა ეკგ და კარდიოლოგის შეფასება.')
    elif s.get('irregular_hr'):
        if cardio_cleared:
            amber_flags.append(f'არარეგულარული რიტმი (HR {hr or "?"}/წთ) — შეფასებულია და დაშვებულია კარდიოლოგის მიერ ✓')
        else:
            red_flags.append(f'პულსის/რიტმის საყურადღებო ცვლილება (HR {hr or "?"}/წთ) — საჭიროა ეკგ და კარდიოლოგის შეფასება.')
    elif hr and 45 <= hr < 55:
        if (q_filled and q.get('pacemaker')) or ((s.get('ecg') or 'pending') == 'ischemia_arrhythmia'):
            red_flags.append(f'ბრადიკარდია (HR {hr}/წთ < 55) კარდიოსტიმულატორის / ეკგ ცვლილების ფონზე — გადაამოწმეთ სტიმულატორი და AV გამტარებლობა!')
        else:
            amber_flags.append(f'ზომიერი ბრადიკარდია (HR {hr}/წთ < 55) — დილის ბეტა-ბლოკატორის დოზა შეაფასეთ პულსის მიხედვით.')

    if spo2 is not None:
        if spo2 < 90:
            red_flags.append(f'მძიმე ჰიპოქსემია (SpO2 {spo2}% < 90%) — აცნობეთ ქირურგსა და ანესთეზიოლოგს!')
        elif spo2 < 94:
            amber_flags.append(f'დაქვეითებული სატურაცია (SpO2 {spo2}%).')

    mets_limb_only = q_filled and (not q.get('mets_ok', True)) and (q.get('mets_limitation') == 'limb')
    true_low_mets = q_filled and (not q.get('mets_ok', True)) and (q.get('mets_limitation') != 'limb')
    if q_filled and not q.get('mets_ok', True):
        if mets_limb_only:
            amber_flags.append('2 სართულზე ასვლა შეზღუდულია მხოლოდ ფეხის იშემიური ტკივილით (კოჭლობით) — კარდიო-რესპირატორული ქოშინის გარეშე.')
        else:
            amber_flags.append('დაქვეითებული ფუნქციური ამტანობა (< 4 METs — ქოშინი/დაღლა 2 სართულზე ასვლისას).')

    # 3. Hematology, Coagulation, Anemia Standing Reflex, PBM & Early ASRA 5th Ed Neuraxial Resolution
    open_aortic = (
        pathway == PreopPatient.Pathway.ABF
        or (pathway == PreopPatient.Pathway.AAA_INFRA and patient.op_subtype != 'evar')
    )
    open_major = open_aortic or is_pad_bypass
    plt = _int(s.get('plt'), lo=1, hi=2500)
    inr = _num(s.get('inr'), lo=0.4, hi=20.0)
    dos_inr_early = _num(s.get('dos_inr'), lo=0.5, hi=15)
    eff_inr_nx = dos_inr_early if dos_inr_early is not None else inr
    fib = normalize_fibrinogen_gl(s.get('fibrinogen'))
    on_oral_anticoag = q_filled and (q.get('med_warfarin') or q.get('med_doac') or q.get('med_dabigatran'))

    has_acs_stent_early = ('stent_6_12m' in cad_chips) and ('mi' in cad_chips)
    p2y12_cnt_early = sum([bool(q.get('med_clopidogrel')), bool(q.get('med_ticagrelor')), bool(q.get('med_prasugrel'))]) if q_filled else 0
    uninterrupted_p2y12_early = q_filled and (
        (p2y12_cnt_early >= 1)
        and (bool(q.get('recent_stent')) or has_acs_stent_early or is_pta or (is_symp_cea and not q.get('med_prasugrel')))
    )
    hard_nx_reasons = []
    if plt is not None and plt < 100:
        hard_nx_reasons.append(f'PLT {plt} < 100')
    if eff_inr_nx is not None and eff_inr_nx > 1.4:
        hard_nx_reasons.append(f'INR {eff_inr_nx} > 1.4')
    if fib is not None and fib < 1.5:
        hard_nx_reasons.append(f'ფიბრინოგენი {fib} < 1.5 გ/ლ')
    if s.get('aptt_prolonged'):
        hard_nx_reasons.append('APTT გახანგრძლივებულია')
    if uninterrupted_p2y12_early:
        hard_nx_reasons.append('უწყვეტი P2Y12/DAPT — ნეირაქსიული ბლოკადა უკუნაჩვენებია!')
    if 'hit_heparin' in allergy_chips and not is_venous:
        hard_nx_reasons.append('HIT არტერიულ ოპერაციაზე (IV DTI) — ნეირაქსიული ბლოკადა უკუნაჩვენებია!')
    if (q_filled and (q.get('med_doac') or q.get('med_dabigatran') or 'rivaroxaban_2_5' in med_chips)) and (
        on_dialysis or (eff_renal is not None and eff_renal < 30)
    ):
        hard_nx_reasons.append('CrCl/eGFR < 30 ანტიკოაგულანტზე — ნეირაქსიული ბლოკადა მოერიდეთ!')
    if bool(s.get('echo_valve_issue')) and not cardio_cleared:
        hard_nx_reasons.append('მძიმე სარქვლოვანი მანკი / AS — სპინალური ბლოკადა საშიშია!')
    if (eff_sys is not None and eff_sys < 95) or (shock_index is not None and shock_index >= 1.0):
        hard_nx_reasons.append('ჰიპოტენზია / შოკი (SBP <95 ან SI ≥1.0) — სიმპათექტომია საშიშია!')

    solo_neuraxial_anatomic_mismatch = (anesth_plan_raw == 'neuraxial') and (
        pathway == PreopPatient.Pathway.CEA or (open_aortic and 'epidural' not in anesth_lines_set)
    )
    if solo_neuraxial_anatomic_mismatch:
        red_flags.append(
            'არჩეულია მხოლოდ სპინალური/ეპიდურული გაუტკივარება კისრის (CEA) ან ღია აორტის ოპერაციაზე — ანატომიურად შეუსაბამოა! '
            'შეარჩიეთ ზოგადი ენდოტრაქეული ანესთეზია (± ეპიდურული ანალგეზია ღია აორტაზე) ან კისრის წნულის ბლოკადა.'
        )

    auto_spinal_candidate = (anesth_plan_raw == 'auto') and (is_phleb or patient.op_subtype == 'evar' or is_pad_bypass)
    has_neuraxial_plan = explicit_neuraxial or ((anesth_plan_raw == 'auto') and is_phleb and not hard_nx_reasons)
    neuraxial_hard_blocked = bool(explicit_neuraxial and (hard_nx_reasons or solo_neuraxial_anatomic_mismatch))
    if neuraxial_hard_blocked and not solo_neuraxial_anatomic_mismatch and not uninterrupted_p2y12_early:
        red_flags.append(
            f"არჩეული ნეირაქსიული / ეპიდურული გაუტკივარება უკუნაჩვენებია ({', '.join(hard_nx_reasons)}) — შეცვალეთ ანესთეზიის გეგმა (ASRA 5th Ed)!"
        )

    ferritin_val = _num(s.get('ferritin'), lo=1, hi=5000)
    tsat_val = _num(s.get('tsat'), lo=1, hi=100)
    hb_norm = 120 if patient.sex == 'F' else 130
    needs_anemia_panel = (hb_gl is not None and hb_gl < 115)
    anemia_panel_satisfied = bool(s.get('anemia_panel_done')) or ferritin_val is not None or tsat_val is not None
    pbm_iv_iron_indicated = bool(
        hb_gl is not None and hb_gl < hb_norm and (
            (ferritin_val is not None and ferritin_val < 30)
            or (ferritin_val is not None and ferritin_val < 100 and tsat_val is not None and tsat_val < 20)
        )
    )
    if hb_gl is not None:
        hb_poly_hi = 165 if patient.sex == 'F' else 185
        if needs_anemia_panel:
            reflex_orders.append({
                'key': 'anemia',
                'trigger': f'ანემია (Hb {hb_gl} გ/ლ < 115)',
                'action': 'ფერიტინი + შრატის რკინა/TSAT (%) + რეტიკულოციტები (ESAIC PBM)',
                'done': anemia_panel_satisfied,
            })
        if pbm_iv_iron_indicated and not is_urgent_case:
            reflex_orders.append({
                'key': 'pbm_iron',
                'trigger': f'რკინადეფიციტური ანემია (Hb {hb_gl} გ/ლ, ფერიტინი {ferritin_val} ნგ/მლ{f", TSAT {tsat_val}%" if tsat_val is not None else ""})',
                'action': 'PBM პროტოკოლი (ESAIC / ESVS): ინტრავენური რკინის ინფუზია (ფერიკარბოქსიმალტოზა) ალოგენური ტრანსფუზიის შესამცირებლად',
                'done': bool(s.get('pbm_iv_iron_done')),
            })
        if hb_gl < 100:
            red_flags.append(f'მნიშვნელოვანი ანემია (Hb {hb_gl} გ/ლ < 100) — აცნობეთ ქირურგს (მიზეზის ძიება / PBM კორექცია).')
        elif hb_gl > hb_poly_hi:
            amber_flags.append(f'ერითროციტოზი / ჰემოკონცენტრაცია (Hb {hb_gl} გ/ლ > {hb_poly_hi}) — მომატებული თრომბოზული რისკი, საჭიროა ჰიდრატაცია და შეფასება.')
        elif hb_gl < hb_norm and not is_venous:
            amber_flags.append(f'მსუბუქი ანემია (Hb {hb_gl} გ/ლ) — ღია ოპერაციაზე სავალდებულოა ერითრომასის რეზერვი.')

    wbc = _num(s.get('wbc'), lo=0.1, hi=200.0)
    if wbc is not None and (wbc > 12.0 or wbc < 3.5):
        red_flags.append(f'ლეიკოციტების ცვლილება (WBC {wbc} x10^9/L) — გამოირიცხოს მწვავე ინფექცია ჩარევამდე!')

    if plt is not None:
        if plt < 100:
            spinal_note = ' (სპინალური/ეპიდურული ჰემატომისა და სისხლდენის რისკი!)' if has_neuraxial_plan else ''
            red_flags.append(f'თრომბოციტოპენია (PLT {plt} x10^9/L < 100){spinal_note} — აცნობეთ ქირურგს.')
        elif plt < 150:
            amber_flags.append(f'ზომიერი თრომბოციტოპენია (PLT {plt} x10^9/L) — საყურადღებოა ანესთეზიოლოგისთვის.')
    elif hb_gl is not None and not is_venous:
        amber_flags.append('სისხლის საერთო ანალიზში შეიტანეთ თრომბოციტების (PLT) რაოდენობა (ჰეპარინიზაციისა და ნეირაქსიული უსაფრთხოებისთვის).')

    if open_major and hb_gl is not None and not s.get('blood_group'):
        amber_flags.append('ღია სისხლძარღვოვანი ოპერაციის წინ მიუთითეთ სისხლის ჯგუფი და რეზუსი (Type & Screen).')

    if fib is not None and fib < 1.5:
        red_flags.append(f'მძიმე ჰიპოფიბრინოგენემია (ფიბრინოგენი {fib} გ/ლ < 1.5) — კოაგულოპათიური სისხლდენის მაღალი რისკი (ESAIC 2023)!')
    if is_venous and patient.op_subtype == PreopPatient.OpSubtype.EVLA and not has_neuraxial_plan and on_oral_anticoag and inr and inr <= 3.0 and not s.get('aptt_prolonged') and (fib is None or fib >= 1.8):
        if inr > 1.35:
            amber_flags.append(f'INR {inr} ანტიკოაგულანტის ფონზე — EVLA-ს წინ დააზუსტეთ ქირურგთან (ESVS 2022-ით მოხსნა აუცილებელი არ არის, თუ INR <= 3.0).')
    elif (inr and inr > 1.35) or s.get('aptt_prolonged') or (fib is not None and fib < 1.8):
        red_flags.append('კოაგულოგრამის დარღვევა (INR > 1.35 / გახანგრძლივებული APTT / დაბალი ფიბრინოგენი) — აცნობეთ ქირურგს!')

    # 4. Renal Function, Dialysis, Unit Guard, Contrast Budget & Nephrology Standing Reflex
    na_val = _num(s.get('na'), lo=95, hi=190)
    _, chf_hyd_flag = _ihd_and_chf_flags(q, s)
    uses_contrast = (patient.op_subtype in ('evar', 'tbd')) or is_pta
    max_contrast_ml = calc_max_contrast_ml(eff_renal, on_dialysis=on_dialysis) if uses_contrast else None
    co2_angio_advised = bool(uses_contrast and not on_dialysis and eff_renal is not None and eff_renal < 30)
    needs_nephro_panel = on_dialysis or (eff_renal is not None and eff_renal < 45)
    needs_hydration = (not on_dialysis) and (eff_renal is not None and eff_renal < 60) and uses_contrast
    renal_disp = f'CrCl {crcl} / eGFR {egfr}' if (crcl is not None and egfr is not None and crcl < egfr) else f'eGFR {egfr}'
    if on_dialysis:
        reflex_orders.append({
            'key': 'nephro',
            'trigger': 'პროგრამული დიალიზი / თირკმლის ტერმინალური უკმარისობა',
            'action': 'დიალიზის გრაფიკის (ოპერაციის წინა დღეს უჰეპარინოდ), K+ კონტროლისა და მოცულობის შეთანხმება ნეფროლოგთან',
            'done': bool(s.get('nephro_panel_done')),
        })
        if not s.get('nephro_panel_done'):
            red_flags.append('პაციენტი იმყოფება დიალიზზე — სავალდებულოა დიალიზის სესიისა და კალიუმის შეთანხმება ნეფროლოგთან და ქირურგთან!')
    elif creat_unit_ambiguous:
        red_flags.append(
            f'კრეატინინის ერთეული საეჭვოა ({s.get("creat")} — 15–35 შუალედი)! '
            'მიუთითეთ ზუსტი ერთეული (µmol/L თუ mg/dL) eGFR / CrCl უსაფრთხო გამოთვლისთვის.'
        )
    elif s.get('creat') is not None and egfr is None:
        amber_flags.append('კრეატინინის კლირენსის / eGFR გამოსათვლელად მიუთითეთ პაციენტის ასაკი (12–120 წ.) და სქესი.')
    elif eff_renal is not None:
        if needs_nephro_panel:
            nephro_lean = is_venous or is_pta or bool(s.get('cta_disc')) or (eff_renal >= 30)
            nephro_act = (
                'შარდოვანა, ელექტროლიტები (K+/HCO3-) და ნეფროლოგის კონსულტაცია (CTA-ით / ზომიერ CKD 3b-ზე რუტინული თირკმლის ექო და Ca/P სავალდებულო არ არის)'
                if nephro_lean
                else 'შარდოვანა, Ca/P, შარდის ცილა + თირკმელების ექოსკოპია + ნეფროლოგის კონსულტაცია'
            )
            reflex_orders.append({
                'key': 'nephro',
                'trigger': f'დაქვეითებული თირკმლის ფუნქცია ({renal_disp} მლ/წთ < 45)',
                'action': nephro_act,
                'done': bool(s.get('nephro_panel_done')),
            })
        if needs_hydration:
            c_lim = f', მაქს. კონტრასტი ≤ {max_contrast_ml} მლ' if max_contrast_ml else ''
            metf_sfx = ' + მეტფორმინის შეჩერება' if (q_filled and q.get('metformin')) else ''
            if na_val is not None and na_val >= 150:
                hyd_fluid = f'ჰიპერნატრიემია (Na+ {na_val} ≥150) — მოერიდეთ 0.9% NaCl-ით დატვირთვას! ნელი ჰიპოტონური კორექცია (ΔNa+ ≤ 8 მმოლ/ლ/24 სთ){c_lim}{metf_sfx}'
            elif chf_hyd_flag:
                hyd_fluid = f'ფრთხილი მოცულობა-კონტროლირებადი ჰიდრატაცია (0.5 მლ/კგ/სთ LVEDP/შეშუპების კონტროლით — HFrEF სითხით გადატვირთვის რისკი!){c_lim}{metf_sfx}'
            else:
                hyd_fluid = f'იზოტონური ჰიდრატაციის პროტოკოლი (0.9% NaCl){c_lim}{metf_sfx}'
            reflex_orders.append({
                'key': 'hydration',
                'trigger': f'{renal_disp} მლ/წთ (< 60) კონტრასტული ჩარევის წინ',
                'action': hyd_fluid,
                'done': bool(s.get('hydration_planned')),
            })
        if eff_renal < 30:
            co2_note = f' (მაქს. იოდის კონტრასტი ≤ {max_contrast_ml} მლ — განიხილეთ CO2 ანგიოგრაფია / IVUS!)' if co2_angio_advised else ''
            red_flags.append(f'თირკმლის მძიმე უკმარისობა ({renal_disp} მლ/წთ < 30){co2_note} — სავალდებულოა ნეფროლოგი და ტაქტიკის შეთანხმება ქირურგთან!')
        elif eff_renal < 60 and not is_venous and not needs_hydration:
            amber_flags.append(f'{renal_disp} მლ/წთ (CKD 3) — კონტრასტული კვლევისას საჭიროა იზოტონური ჰიდრატაცია.')

    k_val = _num(s.get('k'), lo=1.0, hi=12.0)
    if k_val is not None:
        if k_val < 3.3 or k_val >= 5.4:
            red_flags.append(f'კალიუმის კრიტიკული დონე (K+ {k_val} მმოლ/ლ) — არითმიის რისკი! აცნობეთ ექიმს.')
        elif k_val < 3.5 or k_val > 5.0:
            amber_flags.append(f'კალიუმის სასაზღვრო დონე (K+ {k_val} მმოლ/ლ) — სასურველია კორექცია ოპერაციამდე.')
    elif hb_gl is not None and not is_venous and (
        on_dialysis or (eff_renal is not None and eff_renal < 60) or ('acei_arb' in med_chips) or ('ace_arb' in med_chips) or ('diuretic' in med_chips)
    ):
        amber_flags.append('CKD / დიალიზი / ACEi-დიურეტიკის ფონზე შეიტანეთ შრატის კალიუმი (K+) ოპერაციამდე.')

    if na_val is not None and na_val >= 150:
        red_flags.append(f'კრიტიკული ჰიპერნატრიემია (Na+ {na_val} მმოლ/ლ >= 150) — მოერიდეთ 0.9% NaCl-ით დატვირთვას, ნელი კორექცია ΔNa+ ≤ 8 მმოლ/ლ/24 სთ! აცნობეთ ანესთეზიოლოგს.')
    elif na_val is not None and na_val < 130:
        red_flags.append(f'კრიტიკული ჰიპონატრიემია (Na+ {na_val} მმოლ/ლ < 130) — ნელი კორექცია ΔNa+ ≤ 8 მმოლ/ლ/24 სთ (ოსმოსური დემიელინიზაციის პრევენცია)! აცნობეთ ანესთეზიოლოგს.')

    # 5. Glycemia, HbA1c / Endocrinology Reflex, Thyroid & Drug-Interaction Flags
    hba1c = _num(s.get('hba1c'), lo=3.0, hi=25.0)
    needs_hba1c_reflex = (gl_mmol is not None and gl_mmol >= 7.0 and hba1c is None)
    needs_endo_reflex = (hba1c is not None and hba1c >= 8.5) or (gl_mmol is not None and gl_mmol >= 11.1)
    if needs_hba1c_reflex:
        reflex_orders.append({
            'key': 'hba1c',
            'trigger': f'უზმოდ გლუკოზა მომატებულია ({gl_mmol} მმოლ/ლ >= 7.0)',
            'action': 'გლიკირებული ჰემოგლობინის (HbA1c %) განსაზღვრა ექიმის ჩართვამდე',
            'done': hba1c is not None,
        })
    if needs_endo_reflex:
        reflex_orders.append({
            'key': 'endo',
            'trigger': f'მაღალი გლიკემია / HbA1c ({gl_mmol or "—"} მმოლ/ლ, HbA1c {hba1c or "—"}%)',
            'action': 'ენდოკრინოლოგის კონსულტაცია და გლიკემიის კორექცია ოპერაციამდე',
            'done': bool(s.get('endo_consult_done')),
        })
    if gl_mmol is not None and (gl_mmol >= 14.0 or gl_mmol < 3.9):
        red_flags.append(
            f'კრიტიკული გლიკემია (გლუკოზა {gl_mmol} მმოლ/ლ) — სასწრაფო კორექცია და ენდოკრინოლოგის ჩართვა!'
        )
    elif (q_filled and q.get('dm') and hba1c is None and not is_venous and not needs_hba1c_reflex):
        amber_flags.append('შაქრიანი დიაბეტი — გადაამოწმეთ HbA1c.')
    if hba1c is not None:
        if hba1c >= 6.5 and q_filled and not q.get('dm'):
            red_flags.append(
                f'HbA1c {hba1c}% (≥ 6.5%) დიაბეტის ანამნეზის გარეშე — გამოვლენილია არადიაგნოსტირებული შაქრიანი დიაბეტი! '
                'ჩართეთ პერიოპერაციული გლიკემიური მონიტორინგი და ენდოკრინოლოგი.'
            )
        if hba1c >= 8.5:
            amber_flags.append(
                f'ცუდად კონტროლირებადი გლიკემია (HbA1c {hba1c}% ≥ 8.5% — CPOC / ESC) — '
                'მომატებულია ჭრილობის/პროთეზის ინფექციის რისკი; საჭიროა პერიოპერაციული ინსულინოთერაპიის ოპტიმიზაცია.'
            )

    if q_filled and (q.get('med_doac') or q.get('med_dabigatran')):
        if bmi is not None and bmi > 50:
            amber_flags.append(
                f'ექსტრემალური სიმსუქნე (BMI {bmi} > 50 კგ/მ²) DOAC/დაბიგატრანის ფონზე (ISTH 2021) — '
                'პლაზმური კონცენტრაცია შესაძლოა არაპროგნოზირებადი იყოს; განიხილეთ ანტი-Xa/დონის კონტროლი ან ვარფარინი.'
            )
        if 'amiodarone' in med_chips and (on_dialysis or (eff_renal is not None and eff_renal < 60)):
            amber_flags.append(
                'ამიოდარონი + DOAC/დაბიგატრანი დაქვეითებული თირკმლის ფუნქციის (CrCl/eGFR < 60) ფონზე — '
                'P-gp ინჰიბირება ზრდის პრეპარატის აკუმულაციას (EHRA 2021: განიხილეთ +24 სთ-ით ადრე შეჩერება)!'
            )

    tsh_res = s.get('tsh_status') or 'pending'
    needs_tsh = q_filled and (not is_venous) and (
        bool(q.get('thyroid_disease'))
        or ('amiodarone' in med_chips and uses_contrast)
        or (tsh_res != 'pending')
    )
    if needs_tsh:
        reflex_orders.append({
            'key': 'tsh',
            'trigger': 'ფარისებრი ჯირკვლის პათოლოგია / ამიოდარონი + იოდის კონტრასტი',
            'action': 'TSH და FT4 კონტროლი იოდ-ინდუცირებული თირეოტოქსიკოზის / ჰიპოთირეოზის გამოსარიცხად',
            'done': tsh_res != 'pending',
        })
    if tsh_res == 'hyperthyroid':
        red_flags.append('ჰიპერთირეოზი (დაბალი TSH / მაღალი FT4) — იოდის კონტრასტი საშიშია თირეოტოქსიკური კრიზის გამო! აცნობეთ ექიმს.')
    elif tsh_res == 'hypothyroid_severe':
        red_flags.append(
            'მძიმე დეკომპენსირებული ჰიპოთირეოზი (მაღალი TSH / დაბალი FT4) — ანესთეზიისას მიქსედემური კომის, '
            'ჰიპოთერმიისა და რეფრაქტერული ჰიპოტენზიის უმაღლესი რისკი! გეგმურ ჩარევამდე აცნობეთ ენდოკრინოლოგსა და ანესთეზიოლოგს!'
        )

    if bmi is not None and bmi < 16.0:
        red_flags.append(
            f'მძიმე კახექსია / კვებითი გამოფიტვა (BMI {bmi} < 16.0 კგ/მ²) — ჭრილობის შეუხორცებლობისა და პოსტოპერაციული გართულებების '
            'უმაღლესი რისკი! განიხილეთ წინასაოპერაციო ნუტრიციული ოპტიმიზაცია ქირურგთან.'
        )
    if q_filled and q.get('cfs_level') == '5_plus' and pathway in (PreopPatient.Pathway.ABF, PreopPatient.Pathway.PAD_BYPASS):
        amber_flags.append(
            'გამოხატული კლინიკური დაუძლურება (CFS ≥ 5 — CPOC / ESVS) ღია არტერიული რეკონსტრუქციის წინ — '
            'განიხილეთ ენდოვასკულური ალტერნატივა (PTA/სტენტირება) და გერიატრიული დელირიუმის პრევენცია.'
        )

    if open_major and alb_gl is not None:
        if alb_gl < 30:
            red_flags.append(f'მძიმე ჰიპოალბუმინემია ({alb_gl} გ/ლ < 30) — ღია აორტული ოპერაციის მაღალი რისკი, აცნობეთ ქირურგს!')
        elif alb_gl < 35:
            amber_flags.append(f'დაბალი ალბუმინი ({alb_gl} გ/ლ < 35) — მომატებული ქირურგიული/ჭრილობის გართულების რისკი.')

    if s.get('liver_status') == '3x':
        red_flags.append('ღვიძლის ფერმენტების/ბილირუბინის მნიშვნელოვანი მატება (>=3x ULN) — აცნობეთ ექიმს.')
    if s.get('viral_positive'):
        amber_flags.append('დადებითი ვირუსული მარკერი (B/C/HIV/RW) — საოპერაციო ეპიდ-უსაფრთხოება და ღვიძლის სტატუსის გათვალისწინება.')

    # 6. Infection Control, IDSA 2019 ASB-Safe Urinalysis & Dental Sanitation Reflex
    has_synthetic_implant = (
        pathway in (PreopPatient.Pathway.AAA_INFRA, PreopPatient.Pathway.ABF, PreopPatient.Pathway.CEA)
        or (is_pad_bypass and s.get('alt_vein_mapped') == 'no_autovein_prosthetic')
    )
    inf_chip_set = set(q.get('inf_chips') or [])
    # Only dysuria or unspecified active_infection (without isolated non-urinary chips) triggers UTI reflex!
    has_uti_symptoms = q_filled and (
        ('dysuria' in inf_chip_set)
        or (bool(q.get('active_infection')) and not inf_chip_set)
    )
    has_non_uti_active_inf = q_filled and bool(q.get('active_infection')) and bool(inf_chip_set - {'dysuria', 'dental_acute'})
    if has_non_uti_active_inf:
        inf_desc = chip_summary(list(inf_chip_set - {'dysuria', 'dental_acute'}), 'inf_chips', q.get('infection_text'))
        red_flags.append(f'აქტიური არასაშარდე ინფექციური კერა ({inf_desc}) — გეგმურ ჩარევამდე აცნობეთ ქირურგს სანაციისთვის!')

    urine_wbc = _int(s.get('urine_wbc'), lo=0, hi=2000)
    uti_suspected = (
        has_uti_symptoms
        or bool(s.get('urine_nitrite'))
        or (has_synthetic_implant and urine_wbc is not None and urine_wbc >= 25)
    )
    if uti_suspected:
        reflex_orders.append({
            'key': 'uti',
            'trigger': 'სიმპტომური საშარდე/აქტიური ინფექცია, ნიტრიტები+ ან გამოხატული პიურია (≥25)',
            'action': 'შარდის ბაქტერიოლოგია + ანტიბიოტიკოგრამა და ინფექციური კერის სანაცია',
            'done': bool(s.get('urine_culture_done')),
        })
        if not s.get('urine_culture_done'):
            if is_venous:
                red_flags.append('აქტიური ინფექციური კერა / საშარდე ინფექცია — გეგმური ვენური ჩარევის წინ აცნობეთ ქირურგს!')
            else:
                red_flags.append(
                    'საეჭვოა სიმპტომური საშარდე ან სხვა აქტიური ინფექცია (ნიტრიტები+ / დიზურია / ლეიკოციტურია ≥25) — '
                    'სინთეზური პროთეზის/პატჩის ინფიცირების მაღალი რისკი! აცნობეთ ქირურგს.'
                )
    elif urine_wbc is not None and urine_wbc > 10 and not is_venous:
        amber_flags.append(
            f'უსიმპტომო ლეიკოციტურია (შარდში ლეიკოციტები {urine_wbc} მხ/არ, ნიტრიტებისა და დიზურიის გარეშე) — '
            'IDSA 2019 გაიდლაინით უსიმპტომო ბაქტერიურიის რუტინული მკურნალობა ოპერაციას არ აყოვნებს.'
        )

    needs_dental = q_filled and has_synthetic_implant and (
        bool(q.get('dental_infection')) or 'dental_acute' in inf_chip_set
    )
    if needs_dental:
        antiseptic_lbl = 'პოვიდონ-იოდის სავლები (ქლორჰექსიდინზე ალერგია!)' if 'latex_chlorhex' in allergy_chips else 'ქლორჰექსიდინის დეკოლონიზაცია'
        reflex_orders.append({
            'key': 'dental',
            'trigger': 'კარიესული/ჩირქოვანი კბილი სინთეზური პროთეზის/პატჩის ჩადგმამდე',
            'action': f'სტომატოლოგის სანაცია (წერილობითი ცნობა) + {antiseptic_lbl}',
            'done': bool(s.get('dental_sanitation_done')),
        })
    if has_synthetic_implant and not is_urgent_case:
        shower_antiseptic = 'პოვიდონ-იოდის შხაპი (ქლორჰექსიდინზე ალერგია!)' if 'latex_chlorhex' in allergy_chips else 'ქლორჰექსიდინის ანტისეპტიკური შხაპი'
        reflex_orders.append({
            'key': 'decol',
            'trigger': 'სინთეზური სისხლძარღვოვანი პროთეზის / პატჩის / სტენტ-გრაფტის იმპლანტაცია',
            'action': f'პროთეზის ინფექციის პრევენცია: წინასაოპერაციო {shower_antiseptic}ს ინსტრუქტაჟი',
            'done': bool(s.get('decolonization_ready')),
        })

    # Stale Labs Expiry Guard (>14 days)
    labs_dt = _date(s.get('labs_date'))
    labs_date_ka = _fmt_ka_date(labs_dt)
    labs_age_days = (today - labs_dt).days if (labs_dt and labs_dt <= today) else None
    needs_stale_labs = bool(labs_age_days is not None and labs_age_days > 14 and not patient.approved_at and is_active_case)
    if needs_stale_labs:
        reflex_orders.append({
            'key': 'stale_labs',
            'trigger': f'ანალიზები ჩაბარებულია {labs_age_days} დღის წინ (>14 დღე)',
            'action': 'საკონტროლო სისხლის საერთო, კოაგულოგრამა და კრეატინინი განახლდეს ოპერაციამდე',
            'done': bool(s.get('stale_labs_refreshed')),
        })

    # 7. Pulmonary, Indication-Driven Chest X-Ray & ESAIC 2024 Spirometry
    cxr = s.get('cxr') or 'pending'
    if cxr == 'infiltrate':
        red_flags.append('გულმკერდის რენტგენზე ინფილტრაცია / გამონაჟონი / საყურადღებო კერა — აცნობეთ ქირურგს!')

    if is_venous or is_pta:
        needs_spirometry = is_phleb and (
            (q_filled and bool(q.get('pulm')))
            or (spo2 is not None and spo2 < 94)
            or cxr == 'emphysema'
        )
    else:
        needs_spirometry = (
            (q_filled and bool(q.get('pulm')))
            or (spo2 is not None and spo2 < 94)
            or cxr == 'emphysema'
            or (open_aortic and true_low_mets)
            or (s.get('spiro_fev1') is not None)
        )
    if q_filled and q.get('smoker') and not needs_spirometry and not is_venous:
        amber_flags.append('თამბაქოს მწეველი — რეკომენდებულია თამბაქოს შეწყვეტა და სუნთქვითი ვარჯიშები (ESAIC 2024: უსიმპტომო მწეველთან რუტინული სპირომეტრია ოპერაციას არ აყოვნებს).')

    spiro_fev1 = _int(s.get('spiro_fev1'), lo=10, hi=160)
    if needs_spirometry and spiro_fev1 is not None:
        if spiro_fev1 < 50:
            if anesth_cleared:
                amber_flags.append(f'სპირომეტრიაზე მძიმე დარღვევა (FEV1 {spiro_fev1}% < 50%) — შეფასებულია ანესთეზიოლოგის მიერ ✓')
            else:
                red_flags.append(
                    f'სპირომეტრიაზე მძიმე დარღვევა (FEV1 {spiro_fev1}% < 50%) — '
                    'მაღალი რესპირატორული რისკი! აცნობეთ ქირურგსა და ანესთეზიოლოგს.'
                )
        elif spiro_fev1 < 70:
            amber_flags.append(f'სპირომეტრიაზე ზომიერი დაქვეითება (FEV1 {spiro_fev1}%) — საყურადღებოა ანესთეზიოლოგისთვის.')

    # 8. Indication-Driven Carotid Duplex (AAA, ABF, PAD Bypass) & Smart PPI vs Gastroscopy
    needs_carotid_duplex = (
        pathway == PreopPatient.Pathway.CEA
        or (
            pathway in (PreopPatient.Pathway.AAA_INFRA, PreopPatient.Pathway.ABF, PreopPatient.Pathway.PAD_BYPASS)
            and q_filled and (bool(q.get('neuro')) or bool(q.get('carotid_bruit_symp')) or bool(s.get('carotid_symp')))
        )
    )
    if pathway in (PreopPatient.Pathway.AAA_INFRA, PreopPatient.Pathway.ABF, PreopPatient.Pathway.PAD_BYPASS) and max_car is not None:
        op_short = PATHWAY_SHORT.get(pathway, 'ოპერაცია')
        if max_car >= 70 or (max_car >= 50 and s.get('carotid_symp')):
            red_flags.append(
                f'ჰემოდინამიკურად მნიშვნელოვანი კაროტიდული სტენოზი ({max_car}%) — '
                f'აცნობეთ ქირურგს ეტაპობრივი ტაქტიკის (CEA <-> {op_short}) გადასაწყვეტად!'
            )
        elif max_car >= 50:
            amber_flags.append(f'ასიმპტომური კაროტიდული სტენოზი ({max_car}%) — ინტრაოპერაციული ჰიპოტენზიის თავიდან აცილება.')

    gastro_res = s.get('gastro_result') or 'pending'
    if is_venous or is_pta:
        needs_gastro = (
            (q_filled and bool(q.get('ulcer_active_symp')))
            or ((q_filled and bool(q.get('ulcer'))) and (hb_gl is not None and hb_gl < 100) and not is_urgent_case and not on_dialysis)
            or gastro_res != 'pending'
        )
    else:
        # Urgent vascular windows (symptomatic CEA / CLTI / giant AAA) and ESRD renal anemia do NOT delay surgery for routine EGD unless active GI bleed symptoms!
        needs_gastro = (
            (q_filled and bool(q.get('ulcer_active_symp')))
            or ((hb_gl is not None and hb_gl < 100) and not is_urgent_case and not on_dialysis)
            or gastro_res != 'pending'
        )
    if needs_gastro and gastro_res == 'acute_ulcer':
        red_flags.append('გასტროსკოპიაზე მწვავე წყლული / ეროზიული გასტრიტი — აცნობეთ ქირურგს.')

    p2y12_early_cnt = sum([bool(q.get('med_clopidogrel')), bool(q.get('med_ticagrelor')), bool(q.get('med_prasugrel'))]) if q_filled else 0
    has_dapt_high_gi = q_filled and (
        (p2y12_early_cnt >= 2)
        or (bool(q.get('med_aspirin')) and p2y12_early_cnt >= 1 and (((patient.age or 0) >= 65) or ('steroid' in med_chips)))
    )
    has_oac_ap_ppi = q_filled and on_oral_anticoag and (has_antiplatelet or ('cilostazol' in med_chips))
    has_elder_steroid_ppi = q_filled and ((patient.age or 0) >= 65) and ('steroid' in med_chips) and has_any_antithrombotic
    needs_ppi = bool(
        q_filled
        and (bool(q.get('ulcer')) or has_dapt_high_gi or has_oac_ap_ppi or has_elder_steroid_ppi)
        and not needs_gastro
        and 'ppi' not in med_chips
    )
    if needs_ppi:
        ppi_pref_lbl = (
            'პანტოპრაზოლი 40 მგ (კლოპიდოგრელ/ცილოსტაზოლზე ომეპრაზოლს მოერიდეთ — CYP2C19!)'
            if (q.get('med_clopidogrel') or 'cilostazol' in med_chips)
            else 'ომეპრაზოლი / პანტოპრაზოლი 40 მგ'
        )
        ppi_trig_lbl = (
            'ანამნეზში რემისიული წყლულოვანი დაავადება (მწვავე სისხლდენის ნიშნების გარეშე)'
            if q.get('ulcer')
            else 'მაღალი გასტროინტესტინური სისხლდენის რისკი (DAPT / OAC+ანტიაგრეგანტი / ≥65 წ. სტეროიდზე — ESC 2022 Class I)'
        )
        reflex_orders.append({
            'key': 'ppi',
            'trigger': ppi_trig_lbl,
            'action': f'პერიოპერაციული გასტროპროტექცია — PPI ({ppi_pref_lbl}, ESC 2022 Class I) რუტინული ეგდს-ით დაყოვნების გარეშე',
            'done': bool(s.get('ppi_started')) or ('ppi' in med_chips),
        })

    # 9. Medications, Antibiotic Prophylaxis Switch, HIT, CIED, VOYAGER PAD & Contrast Allergy
    abx_prophylaxis_note = ''
    if 'hit_heparin' in allergy_chips:
        red_flags.append(
            'საეჭვოა ჰეპარინ-ინდუცირებული თრომბოციტოპენია (HIT) — არაფრაქციული ჰეპარინი და LMWH უკუნაჩვენებია! '
            'სასწრაფოდ შეათანხმეთ ალტერნატიული ანტიკოაგულაცია ქირურგთან!'
        )
    if 'beta_lactam' in allergy_chips:
        abx_prophylaxis_note = 'β-ლაქტამზე ალერგია: პროფილაქტიკური ცეფაზოლინის ნაცვლად გამოიყენება ვანკომიცინი ან კლინდამიცინი!'
        amber_flags.append(abx_prophylaxis_note)
    elif 'penicillin_mild' in allergy_chips:
        amber_flags.append('მსუბუქი არა-ანაფილაქსიური გამონაყარი პენიცილინზე — AAAAI 2022 გაიდლაინით ცეფაზოლინი უსაფრთხოდ დასაშვებია.')
    if 'latex_chlorhex' in allergy_chips:
        amber_flags.append('ალერგია ლატექსზე / ქლორჰექსიდინზე — გამოიყენეთ ულატექსო ხელთათმანები/კათეტერი და პოვიდონ-იოდის ანტისეპტიკი!')
    if unclarified_allergy_other:
        amber_flags.append('დაფიქსირებულია დაუზუსტებელი მედიკამენტური ალერგია — ცეფაზოლინის შეყვანამდე დააზუსტეთ ალერგენი!')
    if q_filled and ft_meds['unstructured_labels']:
        red_flags.append(
            f"თავისუფალ ტექსტში („{q.get('other_meds')}“) მითითებულია მაღალი რისკის პრეპარატი ({', '.join(ft_meds['unstructured_labels'])}), "
            'რომელიც სტრუქტურულ ჩიპებში მონიშნული არ არის — სასწრაფოდ დააზუსტეთ შეჩერების სქემისთვის!'
        )

    needs_contrast_premed = q_filled and eff_allergy_iodine and uses_contrast
    if q_filled and eff_allergy_iodine and not is_venous:
        if uses_contrast:
            reflex_orders.append({
                'key': 'contrast_premed',
                'trigger': 'იოდზე / კონტრასტზე ალერგია ანამნეზში',
                'action': 'კონტრასტის წინასწარი პრემედიკაციის სქემა (სტეროიდი + H1-ბლოკატორი) და შეთანხმება ქირურგთან',
                'done': bool(s.get('contrast_premed_done')),
            })
            if not s.get('contrast_premed_done'):
                amber_flags.append('ანამნეზში ალერგია იოდზე / კონტრასტზე — ჩართულია პრემედიკაციის რეფლექს-პროტოკოლი (სტეროიდი + ანტიჰისტამინი).')
        else:
            amber_flags.append('ანამნეზში ალერგია იოდზე / კონტრასტზე.')

    needs_cied = q_filled and bool(q.get('pacemaker'))
    cied_ok = bool(s.get('cied_checked')) or ((s.get('cied_plan') or 'pending') != 'pending')
    if needs_cied:
        reflex_orders.append({
            'key': 'cied',
            'trigger': 'იმპლანტირებული კარდიოსტიმულატორი / დეფიბრილატორი (CIED)',
            'action': 'მოწყობილობის პასპორტი/ბოლო შემოწმება, მაგნიტის რეჟიმი და ბიპოლარული კოაგულაციის დაგეგმვა კარდიოლოგ/ანესთეზიოლოგთან',
            'done': cied_ok,
        })

    med_schedule = build_med_schedule(
        patient, q, s, egfr, crcl=crcl, has_neuraxial_override=has_neuraxial_plan,
    ) if q_filled else []
    clop_needs_hold = bool(q.get('med_clopidogrel') and not is_pta and not is_symp_cea)
    metf_contra_any = bool(q.get('metformin')) and (on_dialysis or (eff_renal is not None and eff_renal < 30) or liver_3x)
    metf_hold_48h = bool(q.get('metformin')) and (
        (uses_contrast and (eff_renal is None or eff_renal < 60))
        or open_major
        or (eff_renal is not None and eff_renal < 45)
    )
    needs_med_hold = q_filled and (
        clop_needs_hold
        or bool(q.get('med_ticagrelor') and not is_pta)
        or bool(q.get('med_prasugrel'))
        or bool(q.get('med_doac'))
        or bool(q.get('med_dabigatran'))
        or bool(q.get('med_warfarin'))
        or ('cilostazol' in med_chips)
        or ('rivaroxaban_2_5' in med_chips)
        or bool(q.get('glp1'))
        or bool(q.get('sglt2'))
        or bool(q.get('dm_insulin'))
        or metf_contra_any
        or (not is_venous and metf_hold_48h)
        or bool(ft_meds['unstructured_labels'])
    )

    if q_filled and metf_contra_any:
        red_flags.append(
            'მეტფორმინი უკუნაჩვენებია თირკმლის მძიმე უკმარისობის (CrCl/eGFR <30 / დიალიზი) ან ღვიძლის ≥3x დისფუნქციის დროს '
            '(ლაქტატ-აციდოზის რისკი) — შეწყვიტეთ დაუყოვნებლივ და შეათანხმეთ ენდოკრინოლოგთან!'
        )

    has_mech_valve = 'mech_valve' in cad_chips
    has_acs_stent_12m = ('stent_6_12m' in cad_chips) and ('mi' in cad_chips)
    has_afib = ('afib' in cad_chips) or ((s.get('ecg') or 'pending') == 'afib_controlled')
    oac_count = sum([
        bool(q.get('med_warfarin')),
        bool(q.get('med_doac')),
        bool(q.get('med_dabigatran')),
        ('rivaroxaban_2_5' in med_chips),
    ]) if q_filled else 0
    p2y12_count = sum([
        bool(q.get('med_clopidogrel')),
        bool(q.get('med_ticagrelor')),
        bool(q.get('med_prasugrel')),
    ]) if q_filled else 0
    total_at_count = (
        oac_count + p2y12_count
        + (1 if (q_filled and q.get('med_aspirin')) else 0)
        + (1 if (q_filled and 'cilostazol' in med_chips) else 0)
    )

    if oac_count >= 2:
        red_flags.append(
            'პაციენტთან ერთდროულად მონიშნულია 2 ან მეტი პერორალური ანტიკოაგულანტი — '
            'ლეტალური დუბლირების/სისხლდენის რისკი, დაუყოვნებლივ დააზუსტეთ სქემა!'
        )
    if p2y12_count >= 2:
        red_flags.append('ერთდროულად მონიშნულია ორი P2Y12 ანტიაგრეგანტი — გადაამოწმეთ მედიკამენტური დუბლირება!')

    has_full_oac = on_oral_anticoag
    has_dual_ap_intake = q_filled and bool(q.get('med_aspirin')) and (p2y12_count >= 1)
    if q_filled and total_at_count >= 3:
        red_flags.append(
            'პაციენტი ერთდროულად იღებს ≥3 ანტითრომბოზულ პრეპარატს (სამმაგი ანტითრომბოზული თერაპია — სისხლდენის მაღალი რისკი)! '
            'ESC / ESVS / EMA გაიდლაინით გადაამოწმეთ დე-ესკალაცია (ასპირინის/ცილოსტაზოლის მოხსნა და OAC + მონო-ანტიაგრეგანტი).'
        )

    if q_filled and has_full_oac and (q.get('med_ticagrelor') or q.get('med_prasugrel')):
        red_flags.append(
            'პაციენტი იღებს პერორალურ ანტიკოაგულანტს (OAC) ტიკაგრელორთან ან პრასუგრელთან ერთად (ESC Class III — სისხლდენის უმაღლესი რისკი)! '
            'შეათანხმეთ დე-ესკალაცია კლოპიდოგრელზე ან ასპირინზე კარდიოლოგთან!'
        )
    if q_filled and 'clopidogrel_intol' in allergy_chips and q.get('med_clopidogrel'):
        red_flags.append(
            'პაციენტს მონიშნული აქვს კლოპიდოგრელის აუტანლობა/ალერგია, მაგრამ იღებს კლოპიდოგრელს — დაუყოვნებლივ გადაამოწმეთ დანიშნულება!'
        )

    lvef_early = _int(s.get('lvef'), lo=5, hi=95)
    cilostazol_contra = bool(
        chf_hyd_flag
        or (lvef_early is not None and lvef_early < 50 and 'hf' in cad_chips)
        or on_dialysis
        or (eff_renal is not None and eff_renal <= 25)
        or ((s.get('ecg') or 'pending') == 'ischemia_arrhythmia')
        or (hr is not None and hr > 110)
    )
    if q_filled and 'cilostazol' in med_chips and cilostazol_contra:
        red_flags.append(
            'ცილოსტაზოლი უკუნაჩვენებია გულის უკმარისობის / CrCl ≤25 / ტაქიარითმიის დროს (FDA Black Box / ESVS Class III) — შეათანხმეთ მოხსნა!'
        )

    if q_filled and liver_3x and (q.get('med_doac') or q.get('med_dabigatran') or 'rivaroxaban_2_5' in med_chips):
        red_flags.append(
            'ღვიძლის მძიმე დისფუნქცია (≥3x ULN) ანტიკოაგულანტის ფონზე: კოაგულოპათიის / Child-Pugh B–C დროს DOAC უკუნაჩვენებია (EHRA 2021) — გადაამოწმეთ სქემა!'
        )

    op_dt_early = _date(s.get('planned_surgery_date'))
    if q_filled and has_mech_valve:
        if q.get('med_doac') or q.get('med_dabigatran') or 'rivaroxaban_2_5' in med_chips:
            red_flags.append(
                'მექანიკურ სარქველზე DOAC / დაბიგატრანი / რივაროქსაბანი აბსოლუტურად უკუნაჩვენებია '
                '(ESC Class III — სარქვლის თრომბოზის რისკი)! სასწრაფოდ გადაამოწმეთ ვარფარინი (VKA) კარდიოლოგთან!'
            )
        if not q.get('med_warfarin'):
            red_flags.append(
                'ფიქსირდება მექანიკური სარქველი ვარფარინის (VKA) გარეშე — სარქვლის მწვავე თრომბოზის საფრთხე! გადაამოწმეთ ანტიკოაგულაცია კარდიოლოგთან!'
            )
        elif inr is not None and inr < 2.0:
            in_vka_washout = bool(op_dt_early and 0 <= (op_dt_early - today).days <= 5)
            if not in_vka_washout:
                red_flags.append(
                    f'მექანიკურ სარქველზე საბაზისო INR სუბთერაპიულია ({inr} < 2.0) — სარქვლის თრომბოზის რისკი, სასწრაფოდ შეაფასეთ ჰეპარინის ხიდი კარდიოლოგთან!'
                )
        if 'hit_heparin' in allergy_chips:
            red_flags.append(
                'მექანიკური სარქველი + HIT ანამნეზში — ვარფარინის მოხსნისას უხიდოდ დატოვება და ჰეპარინი/LMWH აკრძალულია! '
                'სავალდებულოა სტაციონარული IV ბივალირუდინი ან არგატრობანი (aPTT კონტროლით)!'
            )

    if q_filled and has_afib and not has_full_oac:
        amber_flags.append(
            'ფიქსირდება მოციმციმე არითმია (AFib) პერორალური ანტიკოაგულაციის გარეშე — შეაფასეთ CHA2DS2-VA და ინსულტის პროფილაქტიკა კარდიოლოგთან!'
        )

    post_at_raw = s.get('post_antithrombotic_plan') or 'auto'
    post_antithrombotic_label = ''
    voyager_safe = (
        (eff_renal is None or eff_renal >= 30)
        and (egfr is None or egfr >= 30)
        and (crcl is None or crcl >= 15)
        and not on_dialysis
        and not q.get('ulcer_active_symp')
        and not q.get('neuro')
        and not liver_3x
        and not has_full_oac
        and not has_mech_valve
        and not has_afib
        and not q.get('med_ticagrelor')
        and not q.get('med_prasugrel')
        and not q.get('recent_stent')
        and not has_acs_stent_12m
    )
    alt_p2y12_lbl = 'ტიკაგრელორი' if 'clopidogrel_intol' in allergy_chips else 'კლოპიდოგრელი 75 მგ'
    oac_partner_lbl = (
        'ასპირინი 75–100 მგ (OAC + ტიკაგრელორი ESC Class III სისხლდენის რისკის გამო არ ინიშნება — შეათანხმეთ კონსილიუმით!)'
        if 'clopidogrel_intol' in allergy_chips else 'კლოპიდოგრელი 75 მგ'
    )
    has_active_stent_dapt = bool(q_filled and (q.get('recent_stent') or has_acs_stent_12m))
    if pathway in (PreopPatient.Pathway.ABF, PreopPatient.Pathway.PAD_BYPASS, PreopPatient.Pathway.PAD_PTA):
        if post_at_raw != 'auto':
            post_antithrombotic_label = dict(POST_ANTITHROMBOTIC_CHOICES).get(post_at_raw, '')
            if has_mech_valve and post_at_raw in ('voyager', 'dapt', 'sapt'):
                red_flags.append(
                    'მექანიკური სარქვლის მქონე პაციენტთან პოსტოპ. სავალდებულოა უვადო ვარფარინი (VKA) ± მონო-ანტიაგრეგანტი — VOYAGER / მონო-SAPT დაუშვებელია!'
                )
            elif post_at_raw in ('voyager', 'dapt') and has_full_oac:
                red_flags.append(
                    'არჩეულია პოსტოპ. VOYAGER / DAPT პაციენტთან, რომელიც მუდმივ ანტიკოაგულანტზეა (OAC) — სამმაგი თერაპიის სისხლდენის მაღალი რისკი! შეცვალეთ OAC + მონო-ანტიაგრეგანტით.'
                )
            elif post_at_raw == 'voyager' and not voyager_safe:
                red_flags.append(
                    'პოსტოპ. VOYAGER PAD (რივაროქსაბანი 2.5 მგ ×2) უკუნაჩვენებია/საშიშია (დიალიზი, CrCl/eGFR <30, მექ. სარქველი, AFib, სტენტი/DAPT, ინსულტი, წყლული ან ღვიძლის 3x დაზიანება) — შეარჩიეთ DAPT ან SAPT!'
                )
            elif has_active_stent_dapt and post_at_raw == 'sapt' and not has_full_oac:
                red_flags.append('ახალი კორონარული სტენტის (<6–12 თვ) ფონზე პოსტოპ. მონოთერაპიაზე (SAPT) ჩამოქვეითება საშიშია — შეინარჩუნეთ DAPT!')
            elif post_at_raw == 'dapt' and 'clopidogrel_intol' in allergy_chips and not has_full_oac:
                amber_flags.append('კლოპიდოგრელის აუტანლობის გამო პოსტოპ. DAPT სქემაში გამოიყენეთ ალტერნატიული P2Y12 (ტიკაგრელორი).')
            elif post_at_raw == 'oac_sapt' and 'clopidogrel_intol' in allergy_chips:
                amber_flags.append('კლოპიდოგრელის აუტანლობის გამო OAC-ის ფონზე გამოიყენეთ ასპირინი 75–100 მგ (OAC + ტიკაგრელორი ESC Class III არ ინიშნება!).')
        elif has_mech_valve:
            post_antithrombotic_label = 'უვადო ვარფარინი (VKA) კარდიოლოგის სამიზნე INR-ით + ჩვენებისას ასპირინი 75–100 მგ (DOAC / VOYAGER აკრძალულია!)'
        elif has_full_oac:
            post_antithrombotic_label = f'OAC + {oac_partner_lbl} (სამმაგი თერაპიის გარეშე — ESC/ESVS)'
        elif has_active_stent_dapt:
            post_antithrombotic_label = f'უწყვეტი DAPT: ასპირინი 100 მგ + {alt_p2y12_lbl} (კორონარული სტენტის დაცვა — მონოთერაპიაზე გადაყვანა აკრძალულია!)'
        elif is_pta:
            if voyager_safe:
                post_antithrombotic_label = f'DAPT (ასპირინი + {alt_p2y12_lbl} 1–3 თვე) ან VOYAGER PAD (რივაროქსაბანი 2.5 მგ ×2 + ასპირინი)'
            else:
                post_antithrombotic_label = f'DAPT (ასპირინი + {alt_p2y12_lbl} 1–3 თვე; VOYAGER შეზღუდულია)'
        elif voyager_safe:
            post_antithrombotic_label = 'VOYAGER PAD (რივაროქსაბანი 2.5 მგ ×2 + ასპირინი 100 მგ — ACC/AHA 2024 Class I) ან SAPT'
        else:
            post_antithrombotic_label = f'მონო-ანტიაგრეგანტი ({alt_p2y12_lbl} ან ასპირინი 100 მგ)'

    uninterrupted_p2y12 = q_filled and (
        (p2y12_count >= 1)
        and (bool(q.get('recent_stent')) or has_acs_stent_12m or is_pta or (is_symp_cea and not q.get('med_prasugrel')))
    )
    if has_neuraxial_plan and uninterrupted_p2y12:
        red_flags.append(
            'ნეირაქსიული ანესთეზია / ეპიდურული კათეტერი უკუნაჩვენებია უწყვეტი P2Y12 თერაპიის '
            '(კლოპიდოგრელი/ტიკაგრელორი/პრასუგრელი — სტენტი/DAPT/სიმპტ. CEA) ფონზე (ASRA 5th Ed — სპინალური ჰემატომის უმაღლესი რისკი)! '
            'შეცვალეთ გაუტკივარება ზოგადი/ადგილობრივი მეთოდით.'
        )
    if has_active_stent_dapt:
        if p2y12_count == 0:
            red_flags.append(
                'კორონარული სტენტი (<6–12 თვ) აქტიური P2Y12 ანტიაგრეგანტის გარეშე — სტენტის მწვავე თრომბოზის ლეტალური რისკი! '
                'სასწრაფოდ გადაამოწმეთ DAPT კარდიოლოგთან!'
            )
        is_purely_elective_stent = (
            patient.op_subtype in ('abf_claud', 'bypass_claud', 'pta_claud', 'cea_asymp')
            or (is_venous and not (q.get('ven_bleeding') or q.get('ven_ceap') == 'C6'))
        )
        if is_purely_elective_stent:
            red_flags.append(
                'ESC 2022 Class I: ახალი კორონარული სტენტის (<6–12 თვ სავალდებულო DAPT ფანჯარა) ფონზე წმინდა გეგმური ჩარევა '
                '(კოჭლობა / ასიმპტომური CEA / გეგმური ვარიკოზი) რეკომენდებულია გადაიდოს DAPT ვადის დასრულებამდე!'
            )
    if q_filled and q.get('recent_stent'):
        red_flags.append(
            'კორონარული სტენტი ჩადგმულია ბოლო 6 თვეში (DAPT) — თვითნებური მოხსნა საშიშია! '
            'შეათანხმეთ ანტიაგრეგანტული რეჟიმი და ოპერაციის ტაქტიკა ქირურგთან.'
        )
    elif q_filled and has_acs_stent_12m and not cardio_cleared:
        red_flags.append(
            'ინფარქტის შემდგომი კორონარული სტენტი 6–12 თვეში (ESC: სტანდარტული DAPT 12 თვეა!) — '
            'ანტიაგრეგანტის შეჩერებამდე სავალდებულოა კარდიოლოგისა და ქირურგის შეთანხმება!'
        )
    elif needs_med_hold and not s.get('med_hold_agreed'):
        if is_venous and patient.op_subtype == PreopPatient.OpSubtype.EVLA and not has_neuraxial_plan and not q.get('sglt2') and not q.get('glp1') and not q.get('dm_insulin') and not metf_contra_any:
            amber_flags.append('პაციენტი იღებს ანტიკოაგულანტს / ანტიაგრეგანტს — დააზუსტეთ ქირურგთან (ESVS 2022-ით EVLA შესაძლებელია მოუხსნელადაც).')
        elif has_neuraxial_plan:
            proc_word = 'ეპიდურული / სპინალური ანესთეზიისა და ფლებექტომიის' if is_phleb else 'ეპიდურული / სპინალური ანესთეზიის'
            amber_flags.append(
                f'პაციენტი იღებს ანტიკოაგულანტს / კლოპიდოგრელს / SGLT2 / ინსულინს — {proc_word} წინ '
                'სავალდებულოა შეჩერების დღის შეთანხმება (იხ. ASRA მედიკამენტების ცხრილი)!'
            )
        else:
            amber_flags.append('პაციენტი იღებს ანტიკოაგულანტს / P2Y12 / SGLT2 / GLP-1 / ინსულინს / მეტფორმინს — შეათანხმეთ მიღების/შეჩერების დღე ქირურგთან.')

    # 10. Smart Dual-Track Cardiac Biomarkers (NT-proBNP / hs-cTn OR In-House TTE) & ESC 2022 / CARP Audit
    lvef = _int(s.get('lvef'), lo=5, hi=95)
    nt_probnp = _int(s.get('nt_probnp'), lo=1, hi=50000)
    hs_ctn = _num(s.get('hs_ctn'), lo=0.1, hi=50000)
    ecg = s.get('ecg') or 'pending'

    if nt_probnp is not None and nt_probnp >= 125:
        if cardio_cleared:
            amber_flags.append(f'NT-proBNP {nt_probnp} პგ/მლ (≥125) — შეფასებულია და დაშვებულია კარდიოლოგის მიერ ✓')
        elif nt_probnp >= 300:
            echo_req_s = f'ექოკარდიოგრაფია ჩატარებულია (LVEF {lvef}%) — სავალდებულოა კარდიოლოგის შეფასება.' if lvef is not None else 'სავალდებულოა ექოკარდიოგრაფია და კარდიოლოგის შეფასება.'
            red_flags.append(
                f'მაღალი NT-proBNP ({nt_probnp} პგ/მლ >= 300) — გულის უკმარისობისა და პერიოპერაციული MACE მაღალი რისკი! {echo_req_s}'
            )
        elif lvef is not None:
            amber_flags.append(f'მომატებული NT-proBNP ({nt_probnp} პგ/მლ >= 125; LVEF {lvef}% შეფასებულია) — ESC 2022-ით საჭიროა კარდიოლოგის კონსულტაცია.')
        else:
            amber_flags.append(f'მომატებული NT-proBNP ({nt_probnp} პგ/მლ >= 125) — ESC 2022 Class I-ით ნაჩვენებია ექოკარდიოგრაფია და კარდიოლოგი.')

    if hs_ctn is not None and hs_ctn >= 14:
        if cardio_cleared:
            amber_flags.append(f'საბაზისო hs-cTn {hs_ctn} ნგ/ლ — შეფასებულია კარდიოლოგის მიერ ✓')
        elif hs_ctn >= 52:
            red_flags.append(f'მნიშვნელოვნად მომატებული საბაზისო ტროპონინი (hs-cTn {hs_ctn} ნგ/ლ) — გამოირიცხოს მწვავე მიოკარდიუმის დაზიანება კარდიოლოგთან!')
        else:
            amber_flags.append(f'საბაზისო hs-cTn {hs_ctn} ნგ/ლ (>ULN) — დაფიქსირებულია პოსტოპერაციული MINS შედარებისთვის; შეაფასოს კარდიოლოგმა.')

    if (lvef is not None and lvef < 40) or s.get('echo_valve_issue') or ecg == 'ischemia_arrhythmia':
        if cardio_cleared:
            amber_flags.append('კარდიორისკი (LVEF < 40% / სარქვლოვანი პათოლოგია / ეკგ) შეფასებულია და დაშვებულია კარდიოლოგის მიერ ✓')
        else:
            red_flags.append('კარდიოლოგიური Red Flag (LVEF < 40% / მძიმე სარქვლოვანი მანკი / მწვავე ეკგ ცვლილება) — აცნობეთ ქირურგსა და კარდიოლოგს!')
    elif ecg == 'afib_controlled':
        if hr is not None and hr > 110:
            red_flags.append(f'მოციმციმე არითმია — ტაქისისტოლია (HR {hr}/წთ > 110) — სავალდებულოა რიტმის/სიხშირის კონტროლი კარდიოლოგთან!')
        elif cardio_cleared:
            amber_flags.append('ნორმოსისტოლური მოციმციმე არითმია — შეფასებულია და დაშვებულია კარდიოლოგის მიერ ✓')
        else:
            amber_flags.append('ეკგ-ზე ნორმოსისტოლური მოციმციმე არითმია — საჭიროა კარდიოლოგის შეფასება.')
    elif lvef is not None and lvef < 50:
        amber_flags.append(f'ზომიერად დაქვეითებული განდევნის ფრაქცია (LVEF {lvef}%).')

    is_pure_local_pta = (
        is_pta
        and not (q_filled and bool(q.get('pta_cant_lie_flat')))
        and (s.get('anesthesia_plan') in (None, '', 'auto', 'local'))
    )
    ckd_cardio_thresh = 45 if is_pure_local_pta else 60
    guideline_justifies_cag = (
        true_low_mets
        or (q_filled and bool(q.get('cad')))
        or (lvef is not None and lvef < 50)
        or (nt_probnp is not None and nt_probnp >= 300)
        or (hs_ctn is not None and hs_ctn >= 14)
        or bool(s.get('echo_valve_issue'))
        or ecg == 'ischemia_arrhythmia'
    )
    guideline_justifies_extra_cardio = (
        guideline_justifies_cag
        or needs_cied
        or (nt_probnp is not None and nt_probnp >= 125)
        or ecg == 'afib_controlled'
        or (hr is not None and (hr > 110 or hr < 45))
        or (pathway not in (PreopPatient.Pathway.CEA, PreopPatient.Pathway.VENOUS) and q_filled and bool(q.get('neuro')))
        or (not is_venous and (on_dialysis or (egfr is not None and egfr < ckd_cardio_thresh)))
        or (not is_venous and q_filled and bool(q.get('dm_insulin')))
    )
    cardio_non_compliant = False
    if cardio_decision in ('stress', 'cag', 'pci'):
        if cardio_decision in ('cag', 'pci'):
            cardio_non_compliant = not guideline_justifies_cag
        else:
            cardio_non_compliant = not guideline_justifies_extra_cardio
        who_cardio = patient.cardiologist or 'კარდიოლოგი'
        audit_tag = ' [ESC 2022 Class III / CARP — დაბალი/სტაბილური რისკის პაციენტთან!]' if cardio_non_compliant else ''
        red_flags.append(
            f'კარდიოლოგმა ({who_cardio}) მოითხოვა {patient.get_cardio_decision_display()}{audit_tag} — '
            'დაყოვნებამდე ან სტენტირებამდე სავალდებულოა ქირურგთან შეთანხმება!'
        )

    # Smart Dual-Track: Open aortic repairs always require cardiac consult & echo/BNP;
    # CEA (stroke window!), EVAR, PAD Bypass, PTA & Venous require cardiology ONLY on clinical risk triggers (incl. BP >=180/110)!
    needs_cardio = (
        open_aortic
        or guideline_justifies_extra_cardio
        or bool(s.get('irregular_hr'))
        or severe_htn
        or bool(cardio_decision)
    )
    needs_echo_or_bnp = open_aortic or guideline_justifies_extra_cardio
    mandatory_tte = bool(
        ('hf' in cad_chips)
        or has_mech_valve
        or s.get('echo_valve_issue')
        or (nt_probnp is not None and nt_probnp >= 300)
    )
    cardiac_screen_done = (
        lvef is not None
        or (not mandatory_tte and nt_probnp is not None and nt_probnp < 125)
    )
    needs_cxr = (
        (q_filled and (bool(q.get('pulm')) or 'hf' in cad_chips or 'fever_resp' in inf_chip_set))
        or (spo2 is not None and spo2 < 94)
    )

    auto_asa = calc_auto_asa(patient, q, s, egfr=egfr, bmi=bmi, today=today)
    asa_raw = s.get('asa_class') if s.get('asa_class') in ('auto', 'I', 'II', 'III', 'IV') else 'auto'
    asa_roman = auto_asa if asa_raw == 'auto' else asa_raw

    needs_anesth = (
        (not is_venous and not is_pta)
        or is_phleb
        or (asa_roman == 'IV')
        or has_neuraxial_plan
        or (s.get('anesthesia_plan') in ('neuraxial', 'general', 'plexus'))
        or (is_pta and q_filled and bool(q.get('pta_cant_lie_flat')))
        or needs_cied
        or (q_filled and (
            bool(q.get('anesth_issue')) or bool(q.get('glp1'))
            or bool(q.get('ven_lidocaine_allergy'))
            or 'lidocaine' in allergy_chips or true_low_mets
        ))
        or bool(s.get('anesth_done'))
    )

    # Stage 1 baseline completeness (Rational Testing — NICE NG45 / ESC 2022 / IDSA 2019 / ESVS 2023–2024)
    renal_lab_present = on_dialysis or (s.get('creat') is not None and not creat_unit_ambiguous)
    target_limb_hemo_present = bool(scoped_abis or scoped_tps or scoped_tbis)
    if is_venous:
        is_lean_evla = (not is_phleb) and (not has_neuraxial_plan) and (anesth_plan_raw != 'general')
        ven_needs_creat = is_phleb or has_neuraxial_plan or bool(q.get('med_doac') or q.get('med_dabigatran'))
        ven_needs_inr = (not is_lean_evla) or on_oral_anticoag or (s.get('liver_status') in ('2x', '3x'))
        ven_needs_gl = (not is_lean_evla) or bool(q.get('dm')) or (bmi is not None and bmi >= 30) or ((patient.age or 0) >= 45)
        ven_labs_ok = (
            hb_gl is not None and plt is not None
            and (not ven_needs_inr or inr is not None)
            and (not ven_needs_gl or gl_mmol is not None)
            and (not ven_needs_creat or renal_lab_present)
        )
        ven_ecg_ok = (not needs_ecg_venous) or (ecg != 'pending')
        s1_core_done = ven_labs_ok and ven_ecg_ok and ((s.get('ven_duplex_status') or 'pending') != 'pending')
    elif is_pta:
        s1_core_done = (
            hb_gl is not None and plt is not None and inr is not None
            and renal_lab_present and gl_mmol is not None
            and ecg != 'pending'
            and target_limb_hemo_present
            and (s.get('pta_access_site') or 'pending') != 'pending'
        )
    else:
        needs_urin_s1 = has_uti_symptoms
        common_s1 = (
            hb_gl is not None and renal_lab_present and inr is not None
            and (not needs_urin_s1 or urine_wbc is not None)
            and (not needs_carotid_duplex or (car_r is not None and car_l is not None))
            and (not needs_cxr or cxr != 'pending')
            and ecg != 'pending'
            and (not needs_echo_or_bnp or cardiac_screen_done)
        )
        if pathway == PreopPatient.Pathway.CEA:
            s1_core_done = (
                common_s1
                and car_r is not None and car_l is not None
                and (s.get('cea_cta_status') or 'pending') != 'pending'
                and (not needs_brain_ct or (s.get('brain_imaging') or 'pending') != 'pending')
            )
        elif pathway == PreopPatient.Pathway.ABF:
            s1_core_done = (
                common_s1
                and (s.get('abf_clamp_zone') or 'pending') != 'pending'
                and (s.get('abf_outflow') or 'pending') != 'pending'
                and (abi_r is not None or abi_l is not None or tbi_measured)
            )
        elif is_pad_bypass:
            s1_core_done = (
                common_s1
                and (s.get('pad_runoff_status') or 'pending') != 'pending'
                and (s.get('pad_gsv_status') or 'pending') != 'pending'
                and target_limb_hemo_present
            )
        else:
            s1_core_done = common_s1 and (aaa_mm is not None)

    # Dynamic Checklist (Stage 1 + Stage 2 + Standing Reflex Orders)
    pw_short = PATHWAY_SHORT.get(pathway, 'AAA')
    tasks = [
        {'key': 'q', 'label': f'კითხვარების შევსება (ზოგადი + {pw_short})', 'done': q_filled},
        {
            'key': 's1',
            'label': (
                'საფეხური 1: ვენების დუპლექსი და საბაზისო ანალიზები' if is_venous
                else ('საფეხური 1: ანალიზები, eGFR, ეკგ, ABI და მიდგომა' if is_pta
                      else 'საფეხური 1: საბაზისო ანალიზები და ჩვენებითი კვლევები')
            ),
            'done': s1_core_done,
        },
    ]
    bypass_outpatient_emergency = is_rupture_suspect
    bypass_outpatient_septic = is_acute_emergency or is_septic_or_mycotic_urgent
    if needs_stale_labs and not bypass_outpatient_septic:
        tasks.append({'key': 'stale_labs', 'label': 'რეფლექს-განახლება: ხანდაზმული ანალიზების (>14 დღე) გადაბარება', 'done': bool(s.get('stale_labs_refreshed'))})
    if needs_anemia_panel and not bypass_outpatient_emergency:
        tasks.append({'key': 'anemia', 'label': 'რეფლექს-პაკეტი (Hb < 115): ფერიტინი, შრატის რკინა/TSAT, რეტიკულოციტები', 'done': anemia_panel_satisfied})
    if pbm_iv_iron_indicated and not is_urgent_case:
        tasks.append({'key': 'pbm_iron', 'label': 'PBM პროტოკოლი (რკინადეფიციტი): ინტრავენური რკინის ინფუზია / შეთანხმება', 'done': bool(s.get('pbm_iv_iron_done'))})
    if needs_hba1c_reflex and not bypass_outpatient_emergency:
        tasks.append({'key': 'hba1c', 'label': 'რეფლექს-ანალიზი (გლუკოზა >= 7.0): HbA1c (%)', 'done': hba1c is not None})
    if needs_endo_reflex and not bypass_outpatient_emergency:
        tasks.append({'key': 'endo', 'label': 'რეფლექს-კონსულტაცია: ენდოკრინოლოგი (გლიკემიის კორექცია)', 'done': bool(s.get('endo_consult_done'))})
    if needs_nephro_panel and (on_dialysis or not bypass_outpatient_emergency):
        nephro_lean_t = is_venous or is_pta or bool(s.get('cta_disc')) or (eff_renal is not None and eff_renal >= 30)
        n_lbl = (
            'დიალიზის გრაფიკი (უჰეპარინო სესია) და K+ შეთანხმება ნეფროლოგთან'
            if on_dialysis else
            ('რეფლექს-პაკეტი (CKD < 45): შარდოვანა, ელექტროლიტები და ნეფროლოგი' if nephro_lean_t
             else 'რეფლექს-პაკეტი (eGFR < 30): შარდოვანა, Ca/P, თირკმლის ექო და ნეფროლოგი')
        )
        tasks.append({'key': 'nephro', 'label': n_lbl, 'done': bool(s.get('nephro_panel_done'))})
    if needs_hydration and not is_rupture_suspect:
        c_tag = f' (მაქს. კონტრასტი ≤ {max_contrast_ml} მლ)' if max_contrast_ml else ''
        tasks.append({'key': 'hydration', 'label': f'კონტრასტ-ნეფროპროტექცია (eGFR < 60): იზოტონური ჰიდრატაცია{c_tag}', 'done': bool(s.get('hydration_planned'))})
    if needs_tsh and not bypass_outpatient_emergency:
        tasks.append({'key': 'tsh', 'label': 'რეფლექს-ანალიზი იოდის კონტრასტამდე: TSH და FT4', 'done': tsh_res != 'pending'})
    if needs_dental and not bypass_outpatient_septic:
        tasks.append({'key': 'dental', 'label': 'სტომატოლოგის სანაცია პროთეზირებამდე (წერილობითი ცნობა)', 'done': bool(s.get('dental_sanitation_done'))})
    if needs_contrast_premed:
        tasks.append({'key': 'premed', 'label': 'იოდზე ალერგიის პრემედიკაციის სქემა (სტეროიდი + ანტიჰისტამინი)', 'done': bool(s.get('contrast_premed_done'))})
    if needs_cied:
        tasks.append({'key': 'cied', 'label': 'კარდიოსტიმულატორის / ICD შემოწმება (მაგნიტის რეჟიმი / ბიპოლარი)', 'done': cied_ok})
    if needs_tbi_check:
        tbi_lbl = (
            'რეფლექს-დოპლერი (ABI > 1.40): თითის წნევა (Toe Pressure) / TBI'
            if abi_non_compressible
            else 'რეფლექს-დოპლერი (დიაბეტი / დიალიზი + CLTI): თითის წნევა (Toe Pressure) / TBI'
        )
        tasks.append({'key': 'tbi', 'label': tbi_lbl, 'done': tbi_measured})
    if needs_alt_vein:
        tasks.append({'key': 'alt_vein', 'label': 'რეფლექს ექო-კარტირება: კონტრალატერალური GSV / SSV / ხელის ვენები', 'done': (s.get('alt_vein_mapped') or 'pending') != 'pending'})
    if pathway == PreopPatient.Pathway.CEA and needs_cea_antiplatelet:
        tasks.append({'key': 'cea_antiplatelet', 'label': 'CEA ანტიაგრეგანტი (ასპირინი ± კლოპიდოგრელი) დაწყებულია', 'done': bool(s.get('cea_antiplatelet_started'))})
    if pathway == PreopPatient.Pathway.CEA and needs_neuro:
        tasks.append({'key': 'neuro', 'label': 'ნევროლოგის წინასაოპერაციო კონსულტაცია (mRS / სტატუსი)', 'done': bool(s.get('neuro_consult_done'))})
    if pathway == PreopPatient.Pathway.CEA and needs_laryngoscopy:
        tasks.append({'key': 'laryng', 'label': 'ლარინგოსკოპია — სახმო იოგების შემოწმება (ENT)', 'done': (s.get('laryngoscopy_res') or 'pending') != 'pending'})
    if (is_venous or pathway == PreopPatient.Pathway.AAA_INFRA) and needs_abi_check and not is_rupture_suspect:
        tasks.append({'key': 'abi', 'label': 'გოჯ-მხრის ინდექსი (ABI)', 'done': min_abi is not None})
    if is_venous and needs_vte_prophylaxis:
        ven_severe_renal_t = on_dialysis or (eff_renal is not None and eff_renal < 30)
        if 'mech_valve' in cad_chips:
            vte_lbl = 'ვარფარინი (VKA) — DOAC აკრძალულია!'
        elif 'hit_heparin' in allergy_chips:
            vte_lbl = 'IPC / არგატრობანი-ბივალირუდინი (HIT + CrCl<30!)' if ven_severe_renal_t else ('ფონდაპარინუქსი (HIT + ღვიძლის ≥3x!)' if liver_3x else 'ფონდაპარინუქსი / DOAC (HIT!)')
        elif ven_severe_renal_t or liver_3x:
            vte_lbl = 'კანქვეშა UFH / დოზა-კორექტირებული LMWH (DOAC არა!)'
        else:
            vte_lbl = 'LMWH / DOAC'
        tasks.append({'key': 'vte', 'label': f'თრომბოპროფილაქტიკის სქემის შეთანხმება (Caprini {caprini_score} — {vte_lbl})', 'done': bool(s.get('vte_prophylaxis_planned'))})
    if needs_spirometry and not bypass_outpatient_emergency:
        tasks.append({'key': 'spiro', 'label': 'სპირომეტრია (FEV1%)', 'done': spiro_fev1 is not None})
    if needs_gastro and not bypass_outpatient_emergency:
        tasks.append({'key': 'gastro', 'label': 'გასტროსკოპია (ეგდს)', 'done': gastro_res != 'pending'})
    if needs_ppi:
        ppi_t_lbl = (
            'გასტროპროტექცია (პანტოპრაზოლი 40 მგ — CYP2C19 უსაფრთხო, ეგდს-ის გარეშე)'
            if (q.get('med_clopidogrel') or 'cilostazol' in med_chips)
            else 'გასტროპროტექცია (PPI: ომეპრაზოლი / პანტოპრაზოლი 40 მგ — ეგდს-ის გარეშე)'
        )
        tasks.append({'key': 'ppi', 'label': ppi_t_lbl, 'done': bool(s.get('ppi_started')) or ('ppi' in med_chips)})
    if uti_suspected and not bypass_outpatient_emergency:
        tasks.append({'key': 'uti', 'label': 'შარდის ბაქტერიოლოგია + ანტიბიოტიკოგრამა / სანაცია', 'done': bool(s.get('urine_culture_done'))})
    if (pathway in (PreopPatient.Pathway.ABF, PreopPatient.Pathway.PAD_BYPASS, PreopPatient.Pathway.PAD_PTA) or is_venous) and needs_wound_care:
        w_lbl = 'ვენური წყლულის / კანის მოვლის შეთანხმება ქირურგთან' if is_venous else 'ტერფის ნაცხი / საზარდულის კანის სანაცია'
        tasks.append({'key': 'wound', 'label': w_lbl, 'done': bool(s.get('wound_care_done'))})
    if is_venous and not compression_contra:
        tbi_comp_ok = (not abi_non_compressible) or tbi_comp_safe or tbi_reduced_comp
        comp_lbl = (
            'მსუბუქი I კლასის კომპრესიული წინდის (<20 mmHg) შერჩევა (სასაზღვრო TP/TBI)'
            if tbi_reduced_comp
            else '2 კლასის კომპრესიული წინდის / გეტრის ზომის შერჩევა'
        )
        tasks.append({'key': 'comp', 'label': comp_lbl, 'done': bool(s.get('compression_ready')) and tbi_comp_ok})
    if is_pta and q_filled:
        dapt_lbl = (
            f'ანტითრომბოზული სქემა OAC ფონზე: ანტიკოაგულანტი + {oac_partner_lbl} (სამმაგი თერაპიის გარეშე!)'
            if has_full_oac else
            f'ანტითრომბოზული მზაობა (DAPT: ასპირინი + {alt_p2y12_lbl} ან VOYAGER PAD)'
        )
        tasks.append({'key': 'dapt', 'label': dapt_lbl, 'done': bool(s.get('dapt_ready'))})
    if open_major:
        tasks.append({'key': 'blood', 'label': 'სისხლის კომპონენტების რეზერვის მომზადება', 'done': bool(s.get('blood_reserved'))})
    if pathway == PreopPatient.Pathway.AAA_INFRA and patient.op_subtype == 'evar':
        tasks.append({'key': 'sizing', 'label': 'EVAR სტენტ-გრაფტის ზომების დათვლა და მზაობა', 'done': bool(s.get('evar_sized'))})
    if needs_med_hold:
        tasks.append({'key': 'med_hold', 'label': 'მედიკამენტების მიღების/შეჩერების სქემის დადასტურება', 'done': bool(s.get('med_hold_agreed'))})
    if needs_cardio and not bypass_outpatient_emergency:
        tasks.append({
            'key': 'cardio',
            'label': f'კარდიოლოგის კონსულტაცია ({patient.cardiologist or "ლიკა ინჯია"})',
            'done': cardio_cleared,
        })
    if needs_anesth:
        tasks.append({
            'key': 'anesth',
            'label': 'ანესთეზიოლოგის კონსულტაცია',
            'done': anesth_cleared,
        })

    done_count = sum(1 for t in tasks if t['done'])
    all_tasks_done = (done_count == len(tasks))
    all_reflex_done = bool(reflex_orders) and all(r['done'] for r in reflex_orders)

    # 11. Stage P2 & P3: Structured Anesthesiology (ESAIC 2024 / RCoA / ASRA 5th Ed), Surgical Wait Guard, DoS & 30-Day VQI
    effective_asa = f"ASA {asa_roman}{'-E' if (s.get('asa_emergency') or is_acute_emergency) else ''}"
    if pathway == PreopPatient.Pathway.CEA and not is_symp_cea and (patient.age or 0) >= 80 and asa_roman == 'IV':
        red_flags.append(
            'ასიმპტომური CEA ≥80 წლის ASA IV პაციენტთან — ESVS 2023-ით 5-წლიანი სარგებელი საეჭვოა, '
            'გადაამოწმეთ კონსერვატიული მედიკამენტური ტაქტიკა (BMT) ქირურგთან!'
        )

    mallampati = s.get('airway_mallampati') if s.get('airway_mallampati') in ('I_II', 'III', 'IV_difficult') else 'I_II'
    mallampati_label = dict(MALLAMPATI_CHOICES).get(mallampati, 'Mallampati I–II')
    difficult_airway_alert = (mallampati == 'IV_difficult') or (q_filled and bool(q.get('cea_neck_limited')) and mallampati == 'III')
    if mallampati == 'IV_difficult':
        amber_flags.append(
            'მოსალოდნელი რთული ინტუბაცია (Mallampati IV) — საოპერაციოში ვიდეოლარინგოსკოპისა და რთული სასუნთქი გზების ნაკრების მზადყოფნა!'
        )

    pulm_chip_set = set(q.get('pulm_chips') or [])
    has_htn_sb = bool(q_filled and q.get('htn')) or bool((eff_sys is not None and eff_sys >= 140) or (eff_dia is not None and eff_dia >= 90))
    stop_bang_auto = sum([
        bool(bmi is not None and bmi > 35),
        bool((patient.age or 0) > 50),
        has_htn_sb,
        bool(patient.sex == 'M'),
    ]) + (3 if 'osa' in pulm_chip_set else 0)
    auto_sb_tier = 'high' if ('osa' in pulm_chip_set or stop_bang_auto >= 5) else ('intermediate' if stop_bang_auto >= 3 else 'low')
    sb_raw = s.get('stop_bang_risk') if s.get('stop_bang_risk') in ('auto', 'low', 'intermediate', 'high') else 'auto'
    effective_stop_bang = auto_sb_tier if sb_raw == 'auto' else sb_raw
    stop_bang_label = {
        'low': 'დაბალი (0–2)',
        'intermediate': 'საშუალო (3–4)',
        'high': 'მაღალი OSA რისკი (≥5)',
    }[effective_stop_bang]
    if effective_stop_bang == 'high' and (not is_venous or is_phleb):
        amber_flags.append(
            'მაღალი ობსტრუქციული ძილის აპნოეს რისკი (STOP-Bang ≥5 / OSA) — ოპიოიდების დაზოგვა, პოსტოპ. პულსოქსიმეტრია და CPAP მზადყოფნა.'
        )

    needs_ett_airway = (
        open_aortic
        or pathway == PreopPatient.Pathway.CEA
        or (q_filled and (bool(q.get('aaa_pain')) or bool(q.get('glp1'))))
        or (bmi is not None and bmi >= 40)
    )
    if anesth_plan_raw == 'general' and needs_ett_airway:
        effective_anesth_label = 'ზოგადი ენდოტრაქეული ანესთეზია' + (' + ეპიდურული ანალგეზია' if 'epidural' in anesth_lines_set else '')
    elif anesth_plan_raw != 'auto':
        effective_anesth_label = dict(ANESTH_PLAN_CHOICES).get(anesth_plan_raw, anesth_plan_raw)
    elif patient.op_subtype == PreopPatient.OpSubtype.EVLA:
        effective_anesth_label = 'ტუმესცენტური ადგილობრივი ანესთეზია'
    elif is_pta:
        effective_anesth_label = 'ადგილობრივი ანესთეზია (± სედაცია)'
    elif pathway == PreopPatient.Pathway.CEA:
        effective_anesth_label = 'ზოგადი ენდოტრაქეული ან რეგიონული ბლოკადა (ცერებრული მონიტორინგით)'
    elif pathway == PreopPatient.Pathway.AAA_INFRA and patient.op_subtype == 'tbd':
        effective_anesth_label = 'ზუსტდება CTA ტაქტიკით (ღია: ზოგადი ენდოტრაქეული vs EVAR: ზოგადი / ადგილობრივი)'
    elif is_phleb or patient.op_subtype == 'evar' or is_pad_bypass:
        if hard_nx_reasons:
            base_ga_lbl = 'ზოგადი ენდოტრაქეული ანესთეზია' if needs_ett_airway else 'ზოგადი ანესთეზია'
            effective_anesth_label = f'{base_ga_lbl} (ნეირაქსიული შეზღუდულია!)'
        elif is_phleb:
            effective_anesth_label = 'სპინალური / ზოგადი ანესთეზია'
        elif patient.op_subtype == 'evar':
            effective_anesth_label = 'ზოგადი ან ადგილობრივი ანესთეზია (სპინალურის არჩევისას — ASRA 72 სთ რეჟიმი)'
        else:
            effective_anesth_label = 'ზოგადი ანესთეზია (სპინალურის არჩევისას — ASRA 72 სთ რეჟიმი)'
    else:
        if 'epidural' in anesth_lines_set:
            effective_anesth_label = 'ზოგადი ენდოტრაქეული + ეპიდურული ანალგეზია'
        elif hard_nx_reasons:
            effective_anesth_label = 'ზოგადი ენდოტრაქეული ანესთეზია (ეპიდურული შეზღუდულია!)'
        else:
            effective_anesth_label = 'ზოგადი ენდოტრაქეული ანესთეზია'

    anesth_lines_txt = chip_summary(s.get('anesth_lines_chips'), 'anesth_lines_chips')
    cied_plan_raw = s.get('cied_plan') if s.get('cied_plan') in ('pending', 'bipolar_only', 'magnet_async', 'ep_reprogram') else 'pending'
    cied_plan_label = dict(CIED_PLAN_CHOICES).get(cied_plan_raw, '') if needs_cied and cied_plan_raw != 'pending' else ''
    needs_icu_bed = bool(
        open_aortic
        or asa_roman == 'IV'
        or (lvef is not None and lvef < 40)
        or (q_filled and bool(q.get('cea_crescendo')))
        or bool(anesth_lines_set & {'art_line', 'cvc', 'epidural'})
    )

    person_id = _clean_person_id(s.get('person_id'))
    birth_date_ka = _fmt_ka_date(s.get('birth_date'))
    op_dt = _date(s.get('planned_surgery_date'))
    planned_surgery_ka = _fmt_ka_date(op_dt)
    planned_surgery_short = _fmt_ka_date(op_dt, short=True)
    operated_date_ka = _fmt_ka_date(s.get('operated_date'))
    case_outcome_label = dict(CASE_OUTCOME_CHOICES).get(case_outcome, '')

    if op_dt and is_active_case:
        ref_start = patient.started_on or patient.visit_date or today
        wait_to_op = (op_dt - ref_start).days
        if pathway == PreopPatient.Pathway.CEA and is_symp_cea:
            ev_dt = _date(q.get('cea_event_date'))
            symp_to_op = (
                (op_dt - ev_dt).days if ev_dt
                else ((days_since_ev + (op_dt - today).days) if (days_since_ev is not None and op_dt >= today) else None)
            )
            if symp_to_op is not None and symp_to_op > 14:
                amber_flags.append(
                    f'დაგეგმილი CEA-ს თარიღი ({planned_surgery_ka}) სცდება სიმპტომიდან 14-დღიან ფანჯარას '
                    f'({symp_to_op} დღე — ESVS 2023 / NVR სამიზნე ≤14 დღე)! განიხილეთ უფრო ადრეული საოპერაციო სლოტი.'
                )
        elif pathway == PreopPatient.Pathway.AAA_INFRA and aaa_mm is not None:
            min_aaa_thresh = 50 if patient.sex == 'F' else 55
            if aaa_mm >= 70 and wait_to_op > 14:
                amber_flags.append(
                    f'გიგანტური AAA ({aaa_mm} მმ): დაგეგმილი ოპერაციის თარიღი ({planned_surgery_ka}) დაშორებულია {wait_to_op} დღით (>14 დღე — მაღალი რუპტურის რისკი)!'
                )
            elif aaa_mm >= min_aaa_thresh and wait_to_op > 56:
                amber_flags.append(
                    f'AAA ({aaa_mm} მმ): დაგეგმილი ოპერაციის მოლოდინი {wait_to_op} დღეა (UK NVR / ESVS სამიზნე ≤8 კვირა / 56 დღე).'
                )
        elif is_clti_any:
            clti_lim = 5 if (q_filled and q.get('abf_wet_infection')) else 14
            if wait_to_op > clti_lim:
                amber_flags.append(
                    f'CLTI პაციენტის დაგეგმილი ოპერაცია ({planned_surgery_ka}) დაშორებულია {wait_to_op} დღით (UK NVR სამიზნე ≤{clti_lim} დღე) — კიდურის დაკარგვის რისკი!'
                )

    if q_filled and q.get('glp1'):
        fasting_instruction = (
            'GLP-1 პროტოკოლი (Multi-Society 2024 / ESAIC 2025): გართულების გარეშე უმეტეს პაციენტთან პრეპარატი გრძელდება, '
            'სავალდებულოა ოპერაციამდე 24 სთ მხოლოდ სუფთა თხევადი დიეტა, მყარი საკვები შეწყდეს ≥8 სთ-ით ადრე, სუფთა წყალი — 2 სთ-ით ადრე '
            '(GI სიმპტომებისას — შეჩერების განხილვა და კუჭის ექო-სკრინინგი / RSI).'
        )
    else:
        fasting_instruction = (
            'ESAIC / ERAS უზმოდ ყოფნის წესი: მყარი საკვები შეწყდეს ოპერაციამდე 6 საათით ადრე · '
            'სუფთა გამჭვირვალე წყალი დასაშვებია ოპერაციამდე 2 საათით ადრე (დილის დაშვებული წამლები — ყლუპი წყლით).'
        )
    surveillance_plan = build_surveillance_plan(patient)

    # Compact allergy summary for Passport & WHO Sign-In safety strip
    allergy_bits = []
    if 'hit_heparin' in allergy_chips:
        allergy_bits.append('HIT — ჰეპარინი/LMWH აკრძალულია!')
    if 'beta_lactam' in allergy_chips:
        allergy_bits.append('β-ლაქტამი (ცეფაზოლინი არა!)')
    elif 'penicillin_mild' in allergy_chips:
        allergy_bits.append('პენიცილინი (მსუბუქი გამონაყარი — ცეფაზოლინი დასაშვებია)')
    if q_filled and (q.get('ven_lidocaine_allergy') or 'lidocaine' in allergy_chips):
        allergy_bits.append('ლიდოკაინი / ადგილობრივი ანესთეტიკი!')
    if 'latex_chlorhex' in allergy_chips:
        allergy_bits.append('ლატექსი / ქლორჰექსიდინი!')
    if q_filled and eff_allergy_iodine:
        allergy_bits.append('იოდი / კონტრასტი!')
    if 'nsaid' in allergy_chips:
        allergy_bits.append('აასს / NSAID ალერგია!')
    if 'clopidogrel_intol' in allergy_chips:
        allergy_bits.append('კლოპიდოგრელის აუტანლობა')
    if q_filled and q.get('allergy_text'):
        allergy_bits.append(q['allergy_text'])
    elif q_filled and (q.get('allergy_other') or unclarified_allergy_other) and not allergy_bits:
        allergy_bits.append('მედიკამენტური ალერგია (დასაზუსტებელია!)')
    allergy_summary = '; '.join(allergy_bits)

    # Day-of-Surgery (DoS / WHO Sign-In) Gate — isolated from Stage 2 tasks so STATUS_READY is preserved
    dos_checks = [
        {
            'key': 'dos_identity_consent',
            'label': 'პაციენტის იდენტიფიკაცია (პირადი N / დაბ. თარიღი) და ხელმოწერილი ინფორმირებული თანხმობა',
            'done': bool(s.get('dos_identity_consent')),
        },
    ]
    if pathway not in (PreopPatient.Pathway.AAA_INFRA, PreopPatient.Pathway.ABF):
        valid_dos_sides = ('R', 'L') if pathway == PreopPatient.Pathway.CEA else ('R', 'L', 'B')
        side_hint = (
            'CEA-ზე მხარე B უკუნაჩვენებია — მიუთითეთ R ან L!'
            if (pathway == PreopPatient.Pathway.CEA and patient.side == 'B')
            else {'R': 'მარჯვენა', 'L': 'მარცხენა', 'B': 'ორმხრივი'}.get(patient.side, 'მხარე მიუთითეთ!')
        )
        dos_checks.append({
            'key': 'dos_site_marked',
            'label': f'საოპერაციო მხარის მარკირება კანზე ({side_hint}) — WHO Sign-In',
            'done': bool(s.get('dos_site_marked')) and (patient.side in valid_dos_sides),
        })
    dos_fast_lbl = (
        'უზმოდ ყოფნის რეჟიმი დაცულია: GLP-1 პროტოკოლი (24 სთ სუფთა თხევადი დიეტა / ≥8 სთ მყარი საკვები / 2 სთ წყალი)'
        if (q_filled and q.get('glp1'))
        else 'უზმოდ ყოფნის რეჟიმი დაცულია (6 სთ მყარი საკვები / 2 სთ სუფთა წყალი)'
    )
    dos_checks.append({
        'key': 'dos_fasting_confirmed',
        'label': dos_fast_lbl,
        'done': bool(s.get('dos_fasting_confirmed')),
    })
    if needs_med_hold or has_any_antithrombotic:
        dos_checks.append({
            'key': 'dos_med_hold_verified',
            'label': 'ანტიკოაგულანტის / P2Y12 / SGLT2 / ინსულინის რეჟიმი და დილის დაშვებული წამლების მიღება გადამოწმდა',
            'done': bool(s.get('dos_med_hold_verified')),
        })
    if q_filled and q.get('sglt2'):
        dos_checks.append({
            'key': 'dos_ketones_ok',
            'label': 'SGLT2 უსაფრთხოება (ეუგლიკემიური DKA-ს გამორიცხვა): სისხლის/შარდის კეტონები ნორმაშია (β-OHB < 0.6 მმოლ/ლ) და პრეპარატი შეჩერებულია ≥72–96 სთ',
            'done': bool(s.get('dos_ketones_ok')),
        })
    if q_filled and on_dialysis:
        dos_checks.append({
            'key': 'dos_k_verified',
            'label': 'დიალიზის შემდგომი საკონტროლო კალიუმი (K+ < 5.4 მმოლ/ლ) გადამოწმებულია და AV ფისტულის მკლავი დაცულია (მანჟეტი/კათეტერი აკრძალულია!)',
            'done': bool(s.get('dos_k_verified')),
        })
    if 'latex_chlorhex' in allergy_chips:
        dos_checks.append({
            'key': 'dos_latex_free_or',
            'label': 'ულატექსო საოპერაციო გარემო (ხელთათმანები/კათეტერი) და პოვიდონ-იოდის ანტისეპტიკი (ქლორჰექსიდინის გარეშე!) მზადაა',
            'done': bool(s.get('dos_latex_free_or')),
        })
    is_uncomplicated_pta_abx = is_pta and not (q_filled and (q.get('abf_wet_infection') or q.get('abf_groin_issue')))
    if not ((is_venous and patient.op_subtype == PreopPatient.OpSubtype.EVLA) or is_uncomplicated_pta_abx):
        cefaz_dose = '3 გ (≥120 კგ!)' if (weight_num is not None and weight_num >= 120) else '2 გ'
        abx_hint = 'ვანკომიცინი / კლინდამიცინი (β-ლაქტამზე ალერგია!)' if 'beta_lactam' in allergy_chips else f'ცეფაზოლინი {cefaz_dose} (ჭრილობამდე ≤60 წთ)'
        if unclarified_allergy_other:
            abx_hint += ' [დააზუსტეთ უცნობი ალერგია შეყვანამდე!]'
        dos_checks.append({
            'key': 'dos_abx_ready',
            'label': f'ანტიბიოტიკოპროფილაქტიკა მზადაა: {abx_hint}',
            'done': bool(s.get('dos_abx_ready')),
        })
    if open_major or patient.op_subtype == 'evar' or has_synthetic_implant:
        decol_sfx = ''
        if has_synthetic_implant:
            decol_sfx = (
                ' · ანტისეპტიკა: პოვიდონ-იოდი (ქლორჰექსიდინზე ალერგია!)'
                if 'latex_chlorhex' in allergy_chips else
                (' · ქლორჰექსიდინის შხაპი ✓' if s.get('decolonization_ready') else ' · ანტისეპტიკური შხაპი')
            )
        dos_checks.append({
            'key': 'dos_blood_implant_ready',
            'label': f'ერითრომასის რეზერვი / სისხლის ჯგუფი და სისხლძარღვოვანი პროთეზი/სტენტ-გრაფტი საოპერაციოშია{decol_sfx}',
            'done': bool(s.get('dos_blood_implant_ready')),
        })
    if needs_icu_bed:
        dos_checks.append({
            'key': 'icu_bed_booked',
            'label': 'პოსტოპერაციული რეანიმაციის (ICU / HDU) საწოლი დაჯავშნილია და დადასტურებულია',
            'done': bool(s.get('icu_bed_booked')),
        })

    dos_sys = _int(s.get('dos_bp_sys'), lo=40, hi=300)
    dos_dia = _int(s.get('dos_bp_dia'), lo=20, hi=200)
    dos_gl = normalize_glucose_mmol(s.get('dos_glucose'))
    dos_inr = _num(s.get('dos_inr'), lo=0.5, hi=15)
    dos_warnings = list(hard_contraindications)
    dos_bp_inverted = (dos_sys is not None and dos_dia is not None and dos_sys <= dos_dia)
    if dos_bp_inverted:
        dos_warnings.append(
            f'ოპერაციის დილის წნევის არასწორი ჩანაწერი (SBP ≤ DBP: {dos_sys}/{dos_dia} mmHg) — გადაამოწმეთ მანჟეტი და მაჩვენებელი!'
        )
    elif (dos_sys and dos_sys >= 180) or (dos_dia and dos_dia >= 110):
        dos_warnings.append(
            f'ოპერაციის დილის კრიტიკული ჰიპერტენზია ({dos_sys or "?"}/{dos_dia or "?"} mmHg >= 180/110) — გეგმური ინდუქცია საშიშია, საჭიროა სტაბილიზაცია!'
        )
    elif dos_sys and dos_sys < 90:
        dos_warnings.append(f'ოპერაციის დილის ჰიპოტენზია ({dos_sys}/{dos_dia or "?"} mmHg < 90) — სასწრაფოდ შეფასდეს ჰემოდინამიკა!')
    if dos_gl is not None and (dos_gl >= 14.0 or dos_gl < 3.9):
        dos_warnings.append(f'ოპერაციის დილის კრიტიკული გლიკემია ({dos_gl} მმოლ/ლ) — საჭიროა კორექცია საოპერაციოში შეყვანამდე!')
    elif dos_gl is not None and dos_gl > 10.0:
        amber_flags.append(f'ოპერაციის დილის ჰიპერგლიკემია ({dos_gl} მმოლ/ლ > 10.0) — სასურველია ინსულინით კორექცია პერიოპერაციული ინფექციის შესამცირებლად.')

    is_evla_only = is_venous and patient.op_subtype == PreopPatient.OpSubtype.EVLA and not has_neuraxial_plan
    inr_lim = 3.0 if is_evla_only else (1.4 if has_neuraxial_plan else 1.5)
    any_dos_ticked = any(c['done'] for c in dos_checks)
    if dos_inr is not None:
        if dos_inr > inr_lim:
            dos_warnings.append(f'ოპერაციის დილის INR ({dos_inr}) აჭარბებს უსაფრთხო ზღვარს (≤ {inr_lim}) — სისხლდენის / სპინალური ჰემატომის რისკი!')
    elif any_dos_ticked:
        if inr is not None and inr > inr_lim:
            dos_warnings.append(
                f'საბაზისო INR ({inr}) აჭარბებს ზღვარს (≤ {inr_lim}), ხოლო ოპერაციის დილის საკონტროლო INR შეყვანილი არ არის!'
            )
        elif q_filled and q.get('med_warfarin') and not is_evla_only:
            dos_warnings.append('ვარფარინის ფონზე ოპერაციის დილის საკონტროლო INR სავალდებულოა საოპერაციოში შეყვანამდე!')

    has_hard_contraindication = bool(hard_contraindications)
    dos_hold_danger = bool(dos_warnings)
    dos_done_count = sum(1 for c in dos_checks if c['done'])
    dos_total_count = len(dos_checks)

    vqi_bundle_applicable = not is_venous
    vqi_bundle_done = (
        bool(s.get('vqi_discharge_antiplatelet') and s.get('vqi_discharge_statin'))
        if vqi_bundle_applicable else bool(s.get('vqi_surveillance_scheduled'))
    )
    vqi_30d_status = s.get('vqi_30d_status') if s.get('vqi_30d_status') in dict(VQI_30D_CHOICES) else 'pending'
    vqi_30d_label = dict(VQI_30D_CHOICES).get(vqi_30d_status, '')

    # 2D Triage State Machine: once 100% of preoperative tasks are complete, anatomical/clock urgency
    # moves from blocking red_flags to priority_tier + amber_flags so the patient reaches STATUS_READY!
    if all_tasks_done and priority_flags:
        prio_set = set(priority_flags)
        red_flags = [f for f in red_flags if f not in prio_set]
        for pf in priority_flags:
            amber_flags.insert(0, f'[სასწრაფო პრიორიტეტი] {pf}')

    amber_flags = list(dict.fromkeys(amber_flags))

    # C9.4: Concise Page-1 Passport Cautions so priority urgency & cleared severe cardio/pulm risks never vanish from Passport!
    passport_cautions = []
    for af in amber_flags:
        if af.startswith('[სასწრაფო პრიორიტეტი]'):
            passport_cautions.append(af)
    if cardio_cleared and s.get('echo_valve_issue'):
        passport_cautions.append(
            'კარდიო-სიფრთხილე (დაშვებულია ✓): მნიშვნელოვანი სარქვლოვანი პათოლოგია / AS — მოერიდეთ სწრაფ ვაზოდილატაციას/ჰიპოტენზიას (A-line, ვაზოპრესორი)!'
        )
    elif cardio_cleared and lvef is not None and lvef < 40:
        passport_cautions.append(
            f'კარდიო-სიფრთხილე (დაშვებულია ✓): დაქვეითებული განდევნის ფრაქცია (LVEF {lvef}%) — ფრთხილი ინფუზია და ჰემოდინამიკური კონტროლი.'
        )
    if anesth_cleared and spiro_fev1 is not None and spiro_fev1 < 50:
        passport_cautions.append(
            f'პულმო-სიფრთხილე (დაშვებულია ✓): მძიმე სპირომეტრიული დარღვევა (FEV1 {spiro_fev1}%) — ფილტვ-დამზოგველი ვენტილაცია და პოსტოპ. მონიტორინგი.'
        )
    for af in amber_flags:
        if len(passport_cautions) >= 4:
            break
        if af not in passport_cautions and any(
            kw in af for kw in (
                'ასიმეტრია', 'სასაზღვრო ჰიპოტენზია', 'ვიწრო პულსური', 'STOP-Bang',
                '14-დღიან ფანჯარას', 'რუპტურის რისკი', 'კიდურის დაკარგვის', 'NT-proBNP', 'Mallampati IV',
            )
        ):
            passport_cautions.append(af)

    if is_urgent_case:
        priority_tier = 'urgent'
    elif (
        (aaa_mm is not None and aaa_mm >= 65)
        or (ipsi_car is not None and ipsi_car >= 90)
        or (is_venous and q_filled and q.get('ven_ceap') == 'C6')
        or (not is_venous and not is_on_hold and is_active_case and days >= max(4, t_days - 1))
    ):
        priority_tier = 'high'
    elif is_venous:
        priority_tier = 'low'
    else:
        priority_tier = 'moderate'

    current_sig = _alerts_signature(red_flags)
    alerts_acked = bool(patient.alerts_ack_at and s.get('alerts_ack_sig') == current_sig)
    has_unacked_red = bool(red_flags) and not alerts_acked
    dos_ready = bool(
        q_filled
        and (dos_done_count == dos_total_count)
        and not dos_hold_danger
        and not has_unacked_red
        and not neuraxial_hard_blocked
    )

    # Safety: NEW unacknowledged red_flags (even pre-questionnaire or post-approval!) or hard contraindications ALWAYS force STATUS_ALERT!
    if has_unacked_red or has_hard_contraindication:
        status = STATUS_ALERT
    elif not q_filled:
        status = STATUS_NO_Q
    elif patient.approved_at or all_tasks_done:
        status = STATUS_READY
    else:
        status = STATUS_PROGRESS

    over_target = days > t_days and not patient.approved_at and not is_venous and not is_on_hold and is_active_case

    # 12. Pre-Pilot Academic Refinement: Quantitative Risk Scores, Specialist Briefings & Nurse Guided UX
    rcri = calc_rcri(patient, q, s, is_symp_cea=is_symp_cea)
    vsg_cri = calc_vsg_cri(patient, q, s)
    ariscat = calc_ariscat_pulm(patient, q, s, hb_gl=hb_gl)
    ihd_flag, chf_flag = _ihd_and_chf_flags(q, s)

    frailty_notes = []
    if q_filled and q.get('cfs_level') == '5_plus':
        frailty_notes.append('კლინიკური დაუძლურება (CFS ≥ 5)')
    elif q_filled and q.get('cfs_level') == '4':
        frailty_notes.append('მოწყვლადობა (CFS 4)')
    if alb_gl is not None and alb_gl < 35:
        frailty_notes.append(f'ჰიპოალბუმინემია ({alb_gl} გ/ლ)')
    if bmi is not None and bmi < 16.0:
        frailty_notes.append(f'მძიმე კახექსია (BMI {bmi}!)')
    elif bmi is not None and bmi < 18.5:
        frailty_notes.append(f'დეფიციტური წონა (BMI {bmi})')
    elif bmi is not None and bmi >= 35:
        frailty_notes.append(f'სიმსუქნე II–III ხ. (BMI {bmi})')
    if q_filled and q.get('cea_disabling_stroke'):
        frailty_notes.append('შეზღუდული მობილობა (mRS ≥ 3)')
    if (patient.age or 0) >= 80:
        frailty_notes.append(f'ასაკი {patient.age} წ.')
    needs_delirium_guard = bool((q_filled and q.get('cfs_level') in ('4', '5_plus')) or (patient.age or 0) >= 75)
    if needs_delirium_guard and not is_venous:
        frailty_notes.append('დელირიუმის პრევენცია (ESAIC 2024)')

    # Exact Stage 1 mandatory field keys + nurse jump-chips so nurses never hunt across 20+ inputs
    s1_required_fields = []
    missing_items = []
    if not q_filled:
        missing_items.append({'tab': 'q', 'field': 'mets_ok', 'label': 'კითხვარი'})
    elif q.get('filled_by') == 'პაციენტი (ბმულით)' and not q.get('nurse_verified'):
        missing_items.append({'tab': 'q', 'field': 'btn-verify-self', 'label': 'პაციენტის კითხვარის გადამოწმება'})

    def _req_s1(fname, missing_cond, lbl):
        s1_required_fields.append(fname)
        if missing_cond:
            missing_items.append({'tab': 's1', 'field': fname, 'label': lbl})

    if is_venous:
        is_lean_evla_ux = (not is_phleb) and (not has_neuraxial_plan) and (anesth_plan_raw != 'general')
        ven_needs_creat_ux = is_phleb or has_neuraxial_plan or bool(q.get('med_doac') or q.get('med_dabigatran'))
        ven_needs_inr_ux = (not is_lean_evla_ux) or on_oral_anticoag or (s.get('liver_status') in ('2x', '3x'))
        ven_needs_gl_ux = (not is_lean_evla_ux) or bool(q.get('dm')) or (bmi is not None and bmi >= 30) or ((patient.age or 0) >= 45)
        _req_s1('hb', hb_gl is None, 'Hb')
        _req_s1('plt', plt is None, 'PLT')
        if ven_needs_inr_ux:
            _req_s1('inr', inr is None, 'INR')
        if ven_needs_gl_ux:
            _req_s1('glucose', gl_mmol is None, 'გლუკოზა')
        if ven_needs_creat_ux and not on_dialysis:
            _req_s1('creat', not renal_lab_present, 'კრეატინინი')
        _req_s1('ven_duplex_status', (s.get('ven_duplex_status') or 'pending') == 'pending', 'ვენების დუპლექსი')
        if needs_ecg_venous:
            _req_s1('ecg', ecg == 'pending', 'ეკგ')
    elif is_pta:
        _req_s1('hb', hb_gl is None, 'Hb')
        _req_s1('plt', plt is None, 'PLT')
        _req_s1('inr', inr is None, 'INR')
        if not on_dialysis:
            _req_s1('creat', not renal_lab_present, 'კრეატინინი')
        _req_s1('glucose', gl_mmol is None, 'გლუკოზა')
        _req_s1('abi_r' if patient.side != 'L' else 'abi_l', not target_limb_hemo_present, 'ABI / TP')
        _req_s1('pta_access_site', (s.get('pta_access_site') or 'pending') == 'pending', 'პუნქციის მიდგომა')
        _req_s1('ecg', ecg == 'pending', 'ეკგ')
    else:
        _req_s1('hb', hb_gl is None, 'Hb')
        _req_s1('inr', inr is None, 'INR')
        if not on_dialysis:
            _req_s1('creat', not renal_lab_present, 'კრეატინინი')
        if has_uti_symptoms:
            _req_s1('urine_wbc', urine_wbc is None, 'შარდის ლეიკოციტები')
        _req_s1('ecg', ecg == 'pending', 'ეკგ')
        if needs_echo_or_bnp:
            s1_required_fields.extend(['lvef', 'nt_probnp'])
            if not cardiac_screen_done:
                missing_items.append({'tab': 's1', 'field': 'lvef', 'label': 'ექო (LVEF) ან NT-proBNP'})
        if needs_cxr:
            _req_s1('cxr', cxr == 'pending', 'რენტგენი')
        if pathway == PreopPatient.Pathway.CEA:
            _req_s1('carotid_r', car_r is None, 'მარჯვ. ICA %')
            _req_s1('carotid_l', car_l is None, 'მარცხ. ICA %')
            _req_s1('cea_cta_status', (s.get('cea_cta_status') or 'pending') == 'pending', 'კისრის CTA')
            if needs_brain_ct:
                _req_s1('brain_imaging', (s.get('brain_imaging') or 'pending') == 'pending', 'ტვინის CT/MRI')
        else:
            if needs_carotid_duplex:
                _req_s1('carotid_r', car_r is None, 'მარჯვ. საძილე')
                _req_s1('carotid_l', car_l is None, 'მარცხ. საძილე')
            if pathway == PreopPatient.Pathway.ABF:
                _req_s1('abi_r', (abi_r is None and abi_l is None and not tbi_measured), 'ABI')
                s1_required_fields.append('abi_l')
                _req_s1('abf_clamp_zone', (s.get('abf_clamp_zone') or 'pending') == 'pending', 'აორტის საკლამპე ზონა')
                _req_s1('abf_outflow', (s.get('abf_outflow') or 'pending') == 'pending', 'ბარძაყის Outflow')
            elif is_pad_bypass:
                _req_s1('abi_r' if patient.side != 'L' else 'abi_l', not target_limb_hemo_present, 'ABI / TP')
                _req_s1('pad_runoff_status', (s.get('pad_runoff_status') or 'pending') == 'pending', 'Runoff კალაპოტი')
                _req_s1('pad_gsv_status', (s.get('pad_gsv_status') or 'pending') == 'pending', 'GSV კარტირება')
            else:
                _req_s1('aaa_mm', aaa_mm is None, 'AAA დიამეტრი (მმ)')

    s2_field_map = {
        'stale_labs': ('stale_labs_refreshed', 'ანალიზების განახლება'),
        'anemia': ('ferritin', 'ფერიტინი / TSAT'),
        'pbm_iron': ('pbm_iv_iron_done', 'IV რკინა (PBM)'),
        'hba1c': ('hba1c', 'HbA1c'),
        'endo': ('endo_consult_done', 'ენდოკრინოლოგი'),
        'nephro': ('nephro_panel_done', 'ნეფროლოგი / დიალიზი'),
        'hydration': ('hydration_planned', 'ჰიდრატაციის გეგმა'),
        'tsh': ('tsh_status', 'TSH / FT4'),
        'dental': ('dental_sanitation_done', 'სტომატოლოგი'),
        'premed': ('contrast_premed_done', 'იოდის პრემედიკაცია'),
        'cied': ('cied_checked', 'CIED შემოწმება'),
        'tbi': ('tp_r' if patient.side != 'L' else 'tp_l', 'Toe Pressure / TBI'),
        'alt_vein': ('alt_vein_mapped', 'ალტ. ვენის კარტირება'),
        'cea_antiplatelet': ('cea_antiplatelet_started', 'CEA ანტიაგრეგანტი'),
        'neuro': ('neuro_consult_done', 'ნევროლოგი'),
        'laryng': ('laryngoscopy_res', 'ლარინგოსკოპია'),
        'abi': ('abi_r', 'საბაზისო ABI'),
        'vte': ('vte_prophylaxis_planned', 'VTE პროფილაქტიკა'),
        'spiro': ('spiro_fev1', 'სპირომეტრია'),
        'gastro': ('gastro_result', 'გასტროსკოპია'),
        'ppi': ('ppi_started', 'PPI გასტროპროტექცია'),
        'uti': ('urine_culture_done', 'შარდის ნათესი'),
        'wound': ('wound_care_done', 'ჭრილობის/კანის სანაცია'),
        'comp': ('compression_ready', 'კომპრესიული წინდა'),
        'dapt': ('dapt_ready', 'DAPT / ანტითრომბოზული'),
        'blood': ('blood_reserved', 'სისხლის რეზერვი'),
        'sizing': ('evar_sized', 'EVAR ზომების დათვლა'),
        'decol': ('decolonization_ready', 'ანტისეპტიკური შხაპი'),
        'med_hold': ('med_hold_agreed', 'წამლების სქემის შეთანხმება'),
        'cardio': ('cardio_decision', 'კარდიოლოგი'),
        'anesth': ('anesth_done', 'ანესთეზიოლოგი'),
    }
    for t_obj in tasks:
        tk = t_obj['key']
        if tk in ('q', 's1') or t_obj['done']:
            continue
        fld_name, short_lbl = s2_field_map.get(tk, ('', t_obj['label']))
        missing_items.append({'tab': 's2', 'field': fld_name, 'label': short_lbl})

    if has_hard_contraindication:
        next_step_hint = 'უკუჩვენება — შეაჩერეთ!'
    elif not q_filled:
        next_step_hint = 'აკლია: კითხვარი'
    elif missing_items:
        top_lbls = ', '.join(m['label'] for m in missing_items[:2])
        more_cnt = len(missing_items) - 2
        next_step_hint = f'აკლია: {top_lbls}' + (f' (+{more_cnt})' if more_cnt > 0 else '')
    elif has_unacked_red:
        next_step_hint = 'საჭიროებს ქირურგის გაცნობას'
    elif not patient.approved_at:
        next_step_hint = 'მზადაა ქირურგის დასტურისთვის'
    else:
        next_step_hint = '✓ დადასტურებულია'

    self_fill_highlights = build_self_fill_highlights(q) if q.get('filled_by') == 'პაციენტი (ბმულით)' else []

    side_badge = {
        'R': 'მარჯვენა (R)',
        'L': 'მარცხენა (L)',
        'B': 'ორმხრივი (B)',
    }.get(patient.side or '', 'აორტა / ცენტრალური' if pathway in (PreopPatient.Pathway.AAA_INFRA, PreopPatient.Pathway.ABF) else 'მხარე დასაზუსტებელია')

    if has_hard_contraindication or dos_hold_danger or solo_neuraxial_anatomic_mismatch:
        clearance_seal = {
            'code': 'stop',
            'badge': 'STOP — დაშვება დაბლოკილია',
            'detail': 'ფიქსირდება აბსოლუტური კლინიკური უკუჩვენება ან საოპერაციო დილის საშიში მაჩვენებელი!',
        }
        _apply_stop_lock_to_med_schedule(med_schedule)
    elif neuraxial_hard_blocked:
        clearance_seal = {
            'code': 'pending',
            'badge': 'მომზადების პროცესში — ანესთეზიის გეგმა შესაცვლელია!',
            'detail': 'არჩეული ნეირაქსიული / ეპიდურული გაუტკივარება უკუნაჩვენებია (ASRA 5th Ed) — შეცვალეთ გაუტკივარების მეთოდი.',
        }
    elif q.get('filled_by') == 'პაციენტი (ბმულით)' and not q.get('nurse_verified') and not patient.approved_at:
        clearance_seal = {
            'code': 'pending',
            'badge': 'მომზადების პროცესში — სავალდებულოა ექთნის ვერიფიკაცია!',
            'detail': 'კითხვარი შევსებულია პაციენტის ბმულით; დაშვებამდე სავალდებულოა ექთნის მიერ გადამოწმება ან ქირურგის დასტური.',
        }
    elif status == STATUS_READY and all_tasks_done:
        clearance_seal = {
            'code': 'go',
            'badge': 'GO — უსაფრთხოდ მზადაა ოპერაციისთვის',
            'detail': 'ყველა პროტოკოლური კვლევა, რეფლექს-ორდერი და კონსულტაცია შესრულებულია.',
        }
    elif patient.approved_at and not has_unacked_red:
        clearance_seal = {
            'code': 'conditional',
            'badge': 'GO (პირობითი) — დაშვებულია ქირურგის მიერ',
            'detail': f'ქირურგმა დაადასტურა; საოპერაციომდე გადაამოწმეთ დარჩენილი პუნქტები ({done_count}/{len(tasks)}).',
        }
    else:
        clearance_seal = {
            'code': 'pending',
            'badge': 'მომზადების პროცესში',
            'detail': f'შესრულებულია {done_count}/{len(tasks)} პროტოკოლური დავალება — საჭიროებს დასრულებას დაშვებამდე.',
        }

    # Specialist Surgical & Anatomical Briefing
    if pathway == PreopPatient.Pathway.CEA:
        cea_cta_lbl = {
            'concordant': 'ტიპური ბიფურკაცია, დუპლექსთან შესაბამისი',
            'high_calcified': 'მაღალი ბიფურკაცია (C2) / მძიმე კალციფიკაცია / კინკინგი (!)',
            'pending': 'CTA შესატანია',
        }.get(s.get('cea_cta_status') or 'pending', 'ზუსტდება')
        br_lbl = {
            'normal_small': 'სისხლჩაქცევის გარეშე / მცირე კერა',
            'large_or_hemorrhage': 'ფართო ინფარქტი (>1/3 MCA) ან ჰემორაგია — უკუჩვენება!',
            'pending': 'შესატანია' if needs_brain_ct else 'არ იყო ნაჩვენები',
        }.get(s.get('brain_imaging') or 'pending', 'ზუსტდება')
        shunt_note = (
            'ინტრაოპერაციული შუნტის (Shunt) სავალდებულო მზადყოფნა (კონტრალატ. სტენოზი/ოკლუზია ≥70% ან გადატანილი ინსულტი)!'
            if ((contra_car is not None and contra_car >= 70) or (q_filled and q.get('neuro')) or is_symp_cea)
            else 'სტანდარტული ცერებრული მონიტორინგი / სელექციური შუნტირება'
        )
        anat_summary = (
            f"NASCET სტენოზი: იფსილატერალური {ipsi_car if ipsi_car is not None else '—'}% / "
            f"კონტრალატერალური {contra_car if contra_car is not None else '—'}% · კისრის CTA: {cea_cta_lbl}"
        )
        tactical_note = f'ტვინის CT/MRI: {br_lbl} · ცერებრული დაცვა: {shunt_note}'
    elif pathway == PreopPatient.Pathway.ABF:
        clamp_lbl = {
            'infrarenal': 'სუფთა ინფრარენალური საკლამპე ზონა',
            'juxtarenal_calcified': 'იუქსტარენალური ოკლუზია / მძიმე კალციფიკაცია (სუპრარენალური კლამპის მზადყოფნა!)',
            'pending': 'CTA საკლამპე ზონა შესატანია',
        }.get(s.get('abf_clamp_zone') or 'pending', 'ზუსტდება')
        out_lbl = {
            'patent': 'CFA და Profunda ორმხრივ გამავალია',
            'profunda_stenosis': 'Profunda სტენოზი — დაგეგმეთ პროფუნდოპლასტიკა!',
            'poor_outflow': 'მძიმე დისტალური კალაპოტი (GSV კარტირება / კომბინირებული შუნტი!)',
            'pending': 'Outflow შესატანია',
        }.get(s.get('abf_outflow') or 'pending', 'ზუსტდება')
        mes_lbl = {
            'normal': 'SMA გამავალია',
            'ima_dominant_iia_occ': 'დომინანტური IMA / ორმხრივი IIA ოკლუზია — IMA რეიმპლანტაციის საკითხი!',
            'sma_stenosis': 'SMA სტენოზი/ოკლუზია — ნაწლავის იშემიის უმაღლესი რისკი! განიხილეთ წინასწარი/თანმხლები SMA სტენტირება ან შუნტირება (ESVS)!',
        }.get(s.get('abf_mesenteric') or 'normal', 'SMA გამავალია')
        anat_summary = f"საკლამპე ზონა: {clamp_lbl} · Outflow: {out_lbl}"
        tactical_note = f"ვისცერული/მენჯის კოლატერალები: {mes_lbl} · ABI R {abi_r if abi_r is not None else '—'} / L {abi_l if abi_l is not None else '—'}"
        if q_filled and q.get('abf_wet_infection'):
            tactical_note += ' · სასწრაფო ტერფის დრენირება/სანაცია + ემპირიული ფართო სპექტრის IV ანტიბიოტიკი!'
    elif is_pad_bypass:
        run_lbl = {
            'popliteal_patent': 'მუხლქვეშა არტერია + ≥2 წვივის არტერია გამავალია',
            'tibial_single': 'ერთადერთი გამავალი წვივის/ტერფის არტერია (აუცილებელია აუტოვენა ≥3.0 მმ!)',
            'desert_foot': 'Desert Foot — მძიმე დისტალური ოკლუზია (!)',
            'pending': 'Runoff შესატანია',
        }.get(s.get('pad_runoff_status') or 'pending', 'ზუსტდება')
        alt_vein_lbl = {
            'pending': 'შესაფასებელია',
            'contra_gsv_ok': 'კონტრალატერალური GSV ვარგისია',
            'arm_ssv_ok': 'ხელის ვენა (Cephalic/Basilic) / SSV ვარგისია',
            'no_autovein_prosthetic': 'აუტოვენა არ მოიძებნა — სინთეზური/კომპოზიტური პროთეზი',
        }.get(s.get('alt_vein_mapped') or 'pending', 'შესაფასებელია')
        gsv_lbl = {
            'adequate': f"იფსილატერალური GSV ვარგისია ({pad_gsv_mm if pad_gsv_mm is not None else '≥3.0'} მმ)",
            'inadequate': f"იფსილატერალური GSV უვარგისია (ალტ. ვენა: {alt_vein_lbl})",
            'pending': 'GSV კარტირება შესატანია',
        }.get(s.get('pad_gsv_status') or 'pending', 'ზუსტდება')
        anat_summary = f"Runoff კალაპოტი: {run_lbl}"
        tactical_note = f"კონდუიტი: {gsv_lbl} · სამიზნე ჰემოდინამიკა: ABI min {min_abi if min_abi is not None else '—'}" + (f", TP min {min_tp} mmHg" if min_tp is not None else '')
    elif is_pta:
        seg_lbl = {
            'iliac': 'თეძოს არტერიები (Iliac)',
            'femoropopliteal': 'ბარძაყ-მუხლქვეშა სეგმენტი (SFA/Popliteal)',
            'infrapopliteal': 'წვივის/ტერფის არტერიები (BTK)',
            'multilevel': 'მრავალდონიანი დაზიანება',
        }.get(s.get('pta_lesion_site') or 'femoropopliteal', 'SFA/Popliteal')
        acc_lbl = {
            'cfa_clean': 'საერთო ბარძაყის არტერია (CFA) თავისუფალია პუნქციისთვის',
            'cfa_calcified_scar': 'CFA კალციფიცირებული/ნაწიბუროვანია — განიხილეთ კონტრალატერალური ან რადიალური/ბრაქიალური მიდგომა!',
            'pending': 'პუნქციის ზონა შესაფასებელია',
        }.get(s.get('pta_access_site') or 'pending', 'ზუსტდება')
        anat_summary = f"სამიზნე სეგმენტი: {seg_lbl} · ABI min {min_abi if min_abi is not None else '—'}" + (f" · TP min {min_tp} mmHg" if min_tp is not None else '')
        tactical_note = f"არტერიული მიდგომა: {acc_lbl}"
    elif is_venous:
        dup_lbl = {
            'deep_patent_reflux': 'ღრმა ვენები გამავალია, ზედაპირული რეფლუქსი დადასტურებულია',
            'acute_dvt': 'მწვავე DVT — ოპერაცია უკუნაჩვენებია!',
            'deep_obstruction': 'ღრმა ვენების ოკლუზია — აბლაცია/ფლებექტომია უკუნაჩვენებია!',
            'superficial_thrombus': 'მწვავე აღმავალი თრომბოფლებიტი (SFJ/SPJ საყურადღებოა!)',
            'pending': 'დუპლექსი შესატანია',
        }.get(s.get('ven_duplex_status') or 'pending', 'ზუსტდება')
        vein_pool = {
            'gsv': 'GSV', 'ssv': 'SSV', 'gsv_ssv': 'GSV + SSV', 'aagsv_trib': 'AAGSV / შენაკადები / პერფორანტები',
        }.get(s.get('ven_target_vein') or 'gsv', 'GSV')
        anat_bits = []
        if s.get('ven_epifascial'):
            anat_bits.append('ეპიფასციური ღერო (<5 მმ — ჭარბი ტუმესცენცია კანის დამწვრობის პრევენციისთვის!)')
        if s.get('ven_tortuous'):
            anat_bits.append('დაკლაკნილი ღერო / სინექიები (ორმაგი პუნქციის მზადყოფნა)')
        if s.get('ven_perforator'):
            anat_bits.append('ინკომპეტენტური პერფორანტი ≥3.5 მმ')
        anat_summary = f"CEAP {q.get('ven_ceap') or 'C2'} · აუზი: {vein_pool}" + (f" ({s.get('ven_gsv_mm')} მმ)" if s.get('ven_gsv_mm') is not None else '') + f" · დუპლექსი: {dup_lbl}"
        tactical_note = '; '.join(anat_bits) if anat_bits else 'სტანდარტული ენდოვენური / ქირურგიული ანატომია'
    else:
        morph_map = dict(AAA_MORPHOLOGY_CHOICES)
        neck_map = dict(AAA_NECK_CHOICES)
        pop_map = dict(POPLITEAL_SCREEN_CHOICES)
        iliac_lbl = {
            'ok_7mm': 'თეძო-ბარძაყის მიდგომა ≥7 მმ ✓',
            'narrow_calcified': 'ვიწრო/კალციფ. მიდგომა (<7 მმ — Iliac Conduit!)',
            'iliac_aneurysm': 'თეძოს ანევრიზმა (IIA შენარჩუნება!)',
            'pending': 'მიდგომა შესაფასებელია',
        }.get(s.get('aaa_iliac_access') or 'pending', '—')
        tbd_sfx = ' · ტაქტიკა: ღია რეზექცია vs EVAR ზუსტდება CTA-ით' if patient.op_subtype == 'tbd' else ''
        anat_summary = (
            f"ინფრარენალური AAA: {aaa_mm if aaa_mm is not None else '—'} მმ · "
            f"{morph_map.get(s.get('aaa_morphology') or 'fusiform', 'ფუზიფორმული')}{tbd_sfx}"
        )
        if patient.op_subtype in ('evar', 'tbd'):
            neck_part = f"პროქს. ყელი: {neck_map.get(s.get('aaa_neck_status') or 'pending', '—')} · მიდგომა: {iliac_lbl} · "
        elif (s.get('aaa_neck_status') or 'pending') == 'hostile_neck':
            neck_part = 'პროქს. ყელი: Hostile / იუქსტარენალური (სუპრარენალური კლამპი + თირკმლის ცივი პერფუზია!) · '
        else:
            neck_part = ''
        rv_part = ' · რეტროაორტული/ცირკუმაორტული მარცხენა თირკმლის ვენა (!)' if s.get('aaa_renal_vein_anom') else ''
        myc_part = ' · მიკოზური AAA: სისხლის ნათესები ×2 + ემპირიული IV ანტიბიოტიკი!' if s.get('aaa_morphology') == 'inflammatory_mycotic' else ''
        rup_oac_part = ''
        if is_rupture_suspect and on_oral_anticoag:
            if q.get('med_warfarin'):
                rup_oac_part = ' · სასწრაფო OAC რევერსია: 4F-PCC 25–50 სე/კგ IV + ვიტამინი K 10 მგ IV!'
            elif q.get('med_dabigatran'):
                rup_oac_part = ' · სასწრაფო რევერსია: იდარუციზუმაბი (Praxbind) 5 გ IV!'
            else:
                rup_oac_part = ' · სასწრაფო Xa-DOAC რევერსია: 4F-PCC 50 სე/კგ IV (მოერიდეთ ანდექსანეტ ალფას კლამპირებამდე — იწვევს UFH რეზისტენტობას — ESVS 2024)!'
        tactical_note = f"{neck_part}მუხლქვეშა სკრინინგი: {pop_map.get(s.get('popliteal_screen_status') or 'pending', '—')}{rv_part}{myc_part}{rup_oac_part}"

    wt_num = _num(s.get('weight_kg'), lo=25, hi=260)
    is_std_evla_abx = is_venous and patient.op_subtype == PreopPatient.OpSubtype.EVLA and not (
        q_filled and (q.get('ven_ceap') == 'C6' or q.get('ven_acute_svt') or q.get('ven_skin_infection'))
    )
    if is_std_evla_abx:
        abx_regimen = 'რუტინული ანტიბიოტიკოპროფილაქტიკა სტანდარტულ EVLA-ზე არ ესაჭიროება (ESVS 2022)'
    elif 'beta_lactam' in allergy_chips:
        needs_gram_neg_groin = pathway in (
            PreopPatient.Pathway.AAA_INFRA, PreopPatient.Pathway.ABF,
            PreopPatient.Pathway.PAD_BYPASS, PreopPatient.Pathway.PAD_PTA,
        )
        if not needs_gram_neg_groin:
            abx_regimen = (
                'β-ლაქტამური ალერგია (ცეფაზოლინი აკრძალულია!) → ვანკომიცინი 15–20 მგ/კგ IV '
                '(მაქს. 2 გ, 90–120 წთ ნელი ინფუზიით) ან კლინდამიცინი 900 მგ IV'
            )
        else:
            avoid_gent = on_dialysis or (eff_renal is not None and eff_renal < 45) or open_aortic
            gram_neg_addon = (
                '+ აზტრეონამი 2 გ ან ციპროფლოქსაცინი 400 მგ IV საზარდულის/მუცლის მიდგომისას — დიალიზზე, CrCl/eGFR <45-სა და ღია აორტაზე გენტამიცინს მოერიდეთ (გენტამიცინი აკრძალულია)!'
                if avoid_gent
                else '+ გენტამიცინი 5 მგ/კგ IV საზარდულის მიდგომისას (ან აზტრეონამი 2 გ / ციპროფლოქსაცინი 400 მგ IV)'
            )
            abx_regimen = (
                'β-ლაქტამური ალერგია (ცეფაზოლინი აკრძალულია!) → ვანკომიცინი 15–20 მგ/კგ IV (მაქს. 2 გ, 90–120 წთ ნელი ინფუზიით) '
                f'ან კლინდამიცინი 900 მგ IV ({gram_neg_addon})'
            )
    else:
        redose_str = 'ყოველ 8–12 სთ-ში (CrCl/eGFR <30 / დიალიზი)' if (on_dialysis or (eff_renal is not None and eff_renal < 30)) else 'ყოველ 4 სთ-ში'
        cef_dose_str = '3 გ IV (წონა ≥ 120 კგ)' if (wt_num is not None and wt_num >= 120) else '2 გ IV'
        abx_regimen = f'ცეფაზოლინი {cef_dose_str} განაკვეთამდე 30–60 წთ-ით ადრე (რე-დოზირება {redose_str} ან >1500 მლ სისხლის დანაკარგზე)'
        if 'penicillin_mild' in allergy_chips:
            abx_regimen += ' [მსუბუქი პენიცილინის გამონაყარი — ცეფაზოლინი დასაშვებია AAAAI 2022]'
        if is_uncomplicated_pta_abx:
            abx_regimen = f'ESVS 2024: გართულების გარეშე პერკუტანულ PTA-ზე რუტინული პროფილაქტიკა აუცილებელი არ არის; სტენტ-გრაფტის / მაღალი რისკის საზარდულისას — {abx_regimen}'
    if unclarified_allergy_other and not is_std_evla_abx:
        abx_regimen += ' [დააზუსტეთ უცნობი მედიკამენტური ალერგია შეყვანამდე!]'

    hit_severe_renal = on_dialysis or (eff_renal is not None and eff_renal < 30)
    if 'hit_heparin' in allergy_chips:
        if is_venous and patient.op_subtype == PreopPatient.OpSubtype.EVLA and not needs_vte_prophylaxis:
            heparin_plan = (
                'HIT ანამნეზში! არაფრაქციული ჰეპარინი (UFH) და LMWH კატეგორიულად აკრძალულია · '
                'სტანდარტულ EVLA-ზე ინტრაოპ. ანტიკოაგულაცია არ ესაჭიროება (საჭიროებისას — ბივალირუდინი ან არგატრობანი; ადრეული აქტივაცია)'
            )
        elif hit_severe_renal:
            heparin_plan = (
                'HIT ანამნეზში! UFH, LMWH, ფონდაპარინუქსი და DOAC (CrCl/eGFR <30 / დიალიზი) კატეგორიულად აკრძალულია → '
                'ინტრაოპ./პოსტოპ. ბივალირუდინი ან არგატრობანი (მექ. სარქველზე ვარფარინზე გადასვლით)!'
            )
        elif liver_3x:
            heparin_plan = (
                'HIT ანამნეზში + ღვიძლის ≥3x დაზიანება! UFH, LMWH და DOAC აკრძალულია → '
                'უპირატესია ბივალირუდინი (არგატრობანის დოზა მცირდება 75%-ით!); პოსტოპ. ფონდაპარინუქსი (CrCl ≥30)!'
            )
        elif has_mech_valve:
            heparin_plan = (
                'HIT ანამნეზში + მექანიკური სარქველი! UFH, LMWH და DOAC აკრძალულია → '
                'IV ბივალირუდინი ან არგატრობანი პოსტოპ. ვარფარინზე (VKA) გადასვლით!'
            )
        else:
            heparin_plan = 'HIT ანამნეზში! არაფრაქციული ჰეპარინი (UFH) და LMWH კატეგორიულად აკრძალულია → ბივალირუდინი ან არგატრობანი (პოსტოპ. ფონდაპარინუქსი/DOAC CrCl ≥30-ზე)!'
    elif pathway == PreopPatient.Pathway.CEA and (
        s.get('brain_imaging') == 'large_or_hemorrhage' or (q_filled and q.get('cea_thrombolysis'))
    ):
        heparin_plan = 'ყურადღება! ტვინის ჰემორაგია / ფართო ინფარქტი ან თრომბოლიზისი ≤6 დღეში — ნაადრევი სისტემური ჰეპარინიზაცია საშიშია ჰემორაგიული ტრანსფორმაციის გამო (ESVS 2023)!'
    elif is_venous:
        heparin_plan = f'ინტრაოპერაციული UFH არ ესაჭიროება · VTE პროფილაქტიკა: Caprini {caprini_score} ქ. ({caprini_tier}) — {caprini_advice}'
    else:
        plt_s = f'{plt} ×10^9/L' if plt is not None else 'შესატანია!'
        heparin_plan = f'არაფრაქციული ჰეპარინი (UFH) 70–100 სე/კგ IV კლამპირებამდე/ჩარევამდე (სამიზნე ACT > 200–250 წმ · საბაზისო PLT: {plt_s})'

    contrast_plan = ''
    hyd_fluid_note = (
        'ჰიპერნატრიემია Na+ ≥150 — 0.9% NaCl-ით დატვირთვა აკრძალულია!'
        if (na_val is not None and na_val >= 150) else
        ('ფრთხილი 0.9% NaCl 0.5 მლ/კგ/სთ (HFrEF!)' if chf_hyd_flag else 'პერიპროცედურული 0.9% NaCl 1 მლ/კგ/სთ ჰიდრატაცია')
    )
    if max_contrast_ml is not None:
        contrast_plan = f'მაქს. იოდის კონტრასტის ბიუჯეტი (Gurm): ≤ {max_contrast_ml} მლ'
        if co2_angio_advised:
            contrast_plan += f' · CrCl/eGFR < 30: უპირატესია CO2 ანგიოგრაფია / IVUS + {hyd_fluid_note}!'
        elif needs_hydration:
            contrast_plan += f' + {hyd_fluid_note}'
        if q_filled and eff_allergy_iodine:
            contrast_plan += ' · იოდზე ალერგია: სავალდებულო პრემედიკაცია (სტეროიდი + H1-ბლოკატორი)!'
    elif q_filled and eff_allergy_iodine and not is_venous:
        contrast_plan = 'იოდზე ალერგია — სავალდებულო პრემედიკაცია (სტეროიდი + H1-ბლოკატორი) კონტრასტის გამოყენებისას!'

    statin_tail = '' if liver_3x else ' + მაღალი ინტენსივობის სტატინი (LDL <1.4 მმოლ/ლ — ESVS Class I)'
    pause_oac_win = '~24 სთ-ში' if is_pta else '48–72 სთ-ში (D+1 პროფილაქტ. LMWH)'
    if is_venous:
        default_postop_at = caprini_advice
    elif post_antithrombotic_label:
        default_postop_at = post_antithrombotic_label + (statin_tail if 'სტატინი' not in post_antithrombotic_label else '')
    elif has_mech_valve:
        default_postop_at = f'უვადო ვარფარინი (VKA) კარდიოლოგის სამიზნე INR-ით + ჩვენებისას ასპირინი 75–100 მგ (DOAC აკრძალულია!){statin_tail}'
    elif has_full_oac:
        default_postop_at = f'მუდმივი ანტიკოაგულანტის (OAC) აღდგენა {pause_oac_win} ჰემოსტაზით (PAUSE) + ჩვენებისას {oac_partner_lbl} (სამმაგი თერაპიის გარეშე){statin_tail}'
    elif has_active_stent_dapt:
        default_postop_at = f'უწყვეტი DAPT: ასპირინი 100 მგ + {alt_p2y12_lbl} (კორონარული სტენტის დაცვა!){statin_tail}'
    elif 'clopidogrel_intol' in allergy_chips:
        default_postop_at = f'ასპირინი 100 მგ უწყვეტად (კლოპიდოგრელის აუტანლობა!){statin_tail}'
    else:
        default_postop_at = f'ასპირინი (ან კლოპიდოგრელი) უწყვეტად{statin_tail}'

    surgical_brief = {
        'side_badge': side_badge,
        'anatomy_summary': anat_summary,
        'tactical_note': tactical_note,
        'abx_regimen': abx_regimen,
        'heparin_plan': heparin_plan,
        'contrast_plan': contrast_plan,
        'postop_antithrombotic': default_postop_at,
        'surveillance_plan': surveillance_plan,
    }

    # Specialist Anesthetic & Hemodynamic Plan
    neuraxial_blockers = list(hard_nx_reasons)
    if solo_neuraxial_anatomic_mismatch:
        neuraxial_blockers.insert(0, 'ანატომიურად შეუსაბამო სოლო-ნეირაქსიული გეგმა!')
    asra_ufh_note = (
        ' · ASRA 2025 UFH წესი: ნეირაქსიული პუნქციიდან IV ჰეპარინამდე ≥60 წთ (სისხლიანი პუნქციისას — გადადება/კონსილიუმი); '
        'კათეტერის ამოღება UFH-დან ≥4–6 სთ-ში (ნორმალური aPTT) და P2Y12/DOAC/VOYAGER დაწყებამდე!'
        if (has_neuraxial_plan and not is_venous) else ''
    )
    vka_inr_caution = (
        f' [ვარფარინის ფონზე ASRA 5th Ed რეკომენდაციით სასურველია INR-ის სრული ნორმალიზება ≤1.2 (მიმდინარე: {eff_inr_nx})!]'
        if (q_filled and q.get('med_warfarin') and eff_inr_nx is not None and 1.2 < eff_inr_nx <= 1.4)
        else ''
    )
    if has_neuraxial_plan:
        if plt is None:
            neuraxial_blockers.append('PLT უცნობია')
        if eff_inr_nx is None:
            neuraxial_blockers.append('INR უცნობია')
        if ft_meds['unstructured_antithrombotic']:
            neuraxial_blockers.append('other_meds-ში მითითებული ანტიკოაგულანტი/ანტიაგრეგანტი დასაზუსტებელია!')
        if not uninterrupted_p2y12 and q_filled and (
            q.get('med_doac') or q.get('med_dabigatran') or q.get('med_warfarin')
            or q.get('med_clopidogrel') or q.get('med_ticagrelor') or q.get('med_prasugrel')
            or ('rivaroxaban_2_5' in med_chips) or ('cilostazol' in med_chips)
        ):
            if not (s.get('med_hold_agreed') or s.get('dos_med_hold_verified')):
                neuraxial_blockers.append('ASRA წამლის შეჩერების ინტერვალი დასადასტურებელია')
        neuraxial_gate = (
            'საყურადღებო/შეზღუდულია (ASRA 5th Ed): ' + ', '.join(neuraxial_blockers) + vka_inr_caution + asra_ufh_note
            if neuraxial_blockers else
            f'ASRA 5th Ed კრიტერიუმები დაცულია (INR {eff_inr_nx}, PLT {plt}) ✓{vka_inr_caution}{asra_ufh_note}'
        )
    elif auto_spinal_candidate and hard_nx_reasons:
        neuraxial_gate = 'ნეირაქსიული შეზღუდულია (ავტო-გადართვა ზოგადზე — ASRA 5th Ed): ' + ', '.join(hard_nx_reasons)
    elif auto_spinal_candidate:
        neuraxial_gate = (
            f'ASRA 5th Ed: ნეირაქსიული დასაშვებია (INR {eff_inr_nx if eff_inr_nx is not None else "—"}, PLT {plt if plt is not None else "—"}) '
            '— არჩევისას მიუთითეთ „სპინალური / ეპიდურული“ (72 სთ DOAC / ≥60 წთ UFH წესისთვის)'
        )
    else:
        neuraxial_gate = 'ნეირაქსიული ბლოკადა/ეპიდურული კათეტერი არ იგეგმება'

    arm_note = ''
    if bp_arm_diff is not None and bp_arm_diff >= 20:
        hi_arm = 'მარჯვენა' if (bp_sys or 0) >= (bp_sys_l or 0) else 'მარცხენა'
        arm_note = f' [წნევის მანჟეტი / A-line სავალდებულოა {hi_arm} მკლავზე — ΔBP {bp_arm_diff} mmHg!]'

    if pathway == PreopPatient.Pathway.CEA:
        bp_corridor = (
            'კლამპირებისას: MAP 85–100 mmHg / SBP 110–160 mmHg (მოერიდეთ <110 და >170 mmHg!) · '
            'რეპერფუზიის შემდეგ: მკაცრად SBP < 140–150 mmHg (ჰიპერპერფუზიის სინდრომისა და ჰემატომის პრევენცია — ESVS 2023)!' + arm_note
        )
    elif is_rupture_suspect:
        bp_corridor = (
            'პერმისიული ჰიპოტენზია SBP 70–90 mmHg (გონების შენარჩუნებით) პროქსიმალურ კლამპირებამდე / ბალონურ ოკლუზიამდე — '
            'მოერიდეთ აგრესიულ კრისტალოიდურ ინფუზიას (ESVS 2024)!' + arm_note
        )
    elif pathway == PreopPatient.Pathway.AAA_INFRA and patient.op_subtype == 'tbd':
        bp_corridor = (
            'MAP ≥ 65–75 mmHg · ღია რეზექციაზე: კლამპირება/დეკლამპირების კონტროლი; '
            'EVAR-ზე: იმპლანტაციისას SBP 90–110 mmHg.' + arm_note
        )
    elif open_aortic:
        bp_corridor = (
            'MAP ≥ 65–75 mmHg (კორონარული/საძილე სტენოზისას ≥ 75–80 mmHg); აორტის კლამპირების წინ afterload-ის კონტროლი, '
            'დეკლამპირების წინ ინფუზიური მზადყოფნა და ვაზოპრესორი (declamping hypotension პრევენცია)!' + arm_note
        )
    elif pathway == PreopPatient.Pathway.AAA_INFRA and patient.op_subtype == 'evar':
        bp_corridor = 'MAP ≥ 65–75 mmHg; სტენტ-გრაფტის გაშლისას კონტროლირებადი SBP 90–110 mmHg ზუსტი იმპლანტაციისთვის.' + arm_note
    elif is_pad_bypass or is_pta:
        bp_corridor = 'MAP ≥ 70–80 mmHg (ინტრაოპერაციული ჰიპოტენზიისა და რეკონსტრუქციის ადრეული თრომბოზის პრევენცია).' + arm_note
    else:
        lido_part = (
            f' · ტუმესცენტური ლიდოკაინი (ადრენალინით) ≤ {lidocaine_max_mg} მგ (LAST ანტიდოტი: 20% Intralipid მზადყოფნაში!)'
            if lidocaine_max_mg is not None else ''
        )
        bp_corridor = 'სტაბილური ნორმოტენზია' + lido_part + arm_note

    high_ischemic_rbc = (
        ihd_flag or chf_flag or has_mech_valve
        or (pathway == PreopPatient.Pathway.CEA)
        or (max_car is not None and max_car >= 70)
        or is_clti_any
    )
    rbc_thresh = 'Hb ≥ 80–90 გ/ლ (გიდ / გულის უკმარისობა / ცერებრული / კრიტიკული იშემიის ზღვარი)' if high_ischemic_rbc else 'Hb ≥ 70–80 გ/ლ (რესტრიქციული PBM ზღვარი)'
    bg_lbl = s.get('blood_group') or 'დაუდგენელია'
    if is_rupture_suspect:
        transfusion_pbm = (
            f'ჯგუფი: {bg_lbl} · სასწრაფო MTP პროტოკოლი: O Rh(-) / ჯგუფ-სპეციფიკური ერითრომასა + ს.გ.პ. + თრომბოკონცენტრატი '
            '(1:1:1) + Cell Saver + TXA 1 გ IV!'
        )
    elif open_major:
        res_lbl = 'ერითრომასის რეზერვი მზადაა ✓' if s.get('blood_reserved') else 'ერითრომასის რეზერვი მოსამზადებელია!'
        cs_lbl = ' · Cell Saver + TXA მზადაა ✓' if s.get('cell_saver_ready') else (' · განიხილეთ Cell Saver + TXA' if open_aortic else '')
        transfusion_pbm = f'ჯგუფი: {bg_lbl} · სამიზნე ტრიგერი: {rbc_thresh} · {res_lbl}{cs_lbl}'
    else:
        transfusion_pbm = f'ჯგუფი: {bg_lbl} · ტრანსფუზიული ტრიგერი საჭიროებისას: {rbc_thresh}'
    coag_pbm_bits = []
    if plt is not None and plt < 100:
        if 'hit_heparin' in allergy_chips:
            coag_pbm_bits.append('PLT <100 + HIT: პროფილაქტიკური თრომბოკონცენტრატის გადასხმა უკუნაჩვენებია (მხოლოდ სიცოცხლისთვის საშიშ სისხლდენაზე)!')
        else:
            coag_pbm_bits.append(f'თრომბოკონცენტრატის მზადყოფნა (PLT {plt} <100 — სამიზნე ≥80–100)')
    if fib is not None and fib < 1.8:
        coag_pbm_bits.append(f'ფიბრინოგენის კონცენტრატი 3–4 გ IV / კრიოპრეციპიტატი (Fib {fib} <1.8 გ/ლ — ESAIC 2023)')
    if inr is not None and inr > 1.5 and not on_oral_anticoag:
        coag_pbm_bits.append(f'კოაგულოპათია OAC-ის გარეშე (INR {inr} >1.5): ვიტამინი K 10 მგ IV ± 4F-PCC (ESAIC 2023)')
    if coag_pbm_bits:
        transfusion_pbm += ' · ' + ' · '.join(coag_pbm_bits)

    airway_bits = [mallampati_label, f'STOP-Bang: {stop_bang_label}']
    if is_rupture_suspect:
        airway_bits.append('RSI ინდუქცია მხოლოდ საოპერაციო ველის დამუშავებისა და ქირურგიული მზადყოფნის შემდეგ!')
    if (k_val is not None and k_val >= 5.5) or (q_filled and (q.get('cea_disabling_stroke') or (q.get('neuro') and is_symp_cea))):
        airway_bits.append('სუქცინილქოლინი უკუნაჩვენებია (ლეტალური ჰიპერკალიემიის რისკი — როკურონიუმი + სუგამადექსი)!')
    if q_filled and q.get('glp1'):
        airway_bits.append('GLP-1 ასპირაციის დაცვა (კუჭის POCUS / RSI)')
    if q_filled and q.get('cea_neck_limited'):
        airway_bits.append('კისრის გაშლა შეზღუდულია!')
    if q_filled and q.get('loose_teeth'):
        airway_bits.append('მორყეული კბილები / პროთეზი')
    if (s.get('laryngoscopy_res') or 'pending') == 'palsy':
        airway_bits.append('სახმო იოგის პარეზი (!)')

    if open_aortic:
        default_lines_lbl = 'A-line + CVC + 2 მსხვილი პერიფერიული კათეტერი + ფოლი + აქტიური გათბობა ≥36.0°C'
    elif pathway == PreopPatient.Pathway.CEA or asa_roman == 'IV':
        default_lines_lbl = 'A-line (ინვაზიური წნევა) + ECG/SpO2 + აქტიური გათბობა'
    elif is_venous or is_pta:
        default_lines_lbl = 'სტანდარტული არაინვაზიური მონიტორინგი (ECG, SpO2, NIBP)'
    else:
        default_lines_lbl = 'A-line / ინვაზიური მონიტორინგი ქირურგიული მასშტაბით + აქტიური გათბობა'
    cied_brief_sfx = (
        f' · CIED: {cied_plan_label}' if cied_plan_label else
        (' · CIED: რეჟიმი დასაზუსტებელია (!) — გარე პედების მზადყოფნა!' if needs_cied else '')
    )
    fistula_sfx = ' · AV ფისტულის მკლავის დაცვა (წნევის მანჟეტი, პუნქცია და კათეტერი აკრძალულია!)' if on_dialysis else ''

    needs_glycemic_corridor = bool(
        (q_filled and (q.get('dm') or q.get('dm_insulin')))
        or (gl_mmol is not None and gl_mmol >= 7.0)
        or (hba1c is not None and hba1c >= 6.5)
    )
    needs_vriii = bool(
        (q_filled and q.get('dm_insulin') and open_aortic)
        or (gl_mmol is not None and gl_mmol >= 12.0)
        or (dos_gl is not None and dos_gl >= 12.0)
    )
    vriii_sfx = ' · ინტრაოპ./ICU: IV ინსულინის ინფუზია (VRIII) საათობრივი გლუკოზისა და K+ კონტროლით!' if needs_vriii else ''

    base_disp = (
        ('პოსტოპ. რეანიმაცია / HDU (დაჯავშნილია ✓)' if s.get('icu_bed_booked') else 'პოსტოპ. რეანიმაცია / HDU — საწოლი დასაჯავშნია!')
        if needs_icu_bed else
        ('დღის სტაციონარი / ამბულატორიული გაწერა ადრეული აქტივაციით' if (is_venous and patient.op_subtype == PreopPatient.OpSubtype.EVLA) else 'სისხლძარღვთა ქირურგიის სტაციონარული პალატა')
    )
    disp_extras = []
    if not is_venous and (open_aortic or rcri['score'] >= 2 or (nt_probnp is not None and nt_probnp >= 125) or (hs_ctn is not None and hs_ctn >= 14)):
        disp_extras.append('MINS მონიტორინგი: hs-cTn 24 სთ და 48 სთ (ESC 2022)')
    if needs_delirium_guard and not is_venous:
        disp_extras.append('დელირიუმის პრევენცია (ბენზოდიაზეპინების გარეშე — ESAIC 2024)')

    anesthetic_brief = {
        'modality': f'{effective_asa} · {effective_anesth_label}',
        'neuraxial_gate': neuraxial_gate,
        'airway_summary': ' · '.join(airway_bits),
        'bp_corridor': bp_corridor,
        'lines_and_cied': (anesth_lines_txt or default_lines_lbl) + cied_brief_sfx + fistula_sfx,
        'transfusion_pbm': transfusion_pbm,
        'glycemic_target': (
            f'პერიოპერაციული გლიკემიური კორიდორი 5.5–10.0 მმოლ/ლ (ჰიპოგლიკემიის <3.9 და >10.0 თავიდან აცილება — ESC/ESAIC){vriii_sfx}'
            if needs_glycemic_corridor
            else 'სტანდარტული ნორმოგლიკემია (<10.0 მმოლ/ლ)'
        ),
        'disposition': base_disp + ((' · ' + ' · '.join(disp_extras)) if disp_extras else ''),
    }

    ecg_display_label = (
        'მოციმციმე არითმია — ტაქისისტოლია (HR > 110!)'
        if (ecg == 'afib_controlled' and hr is not None and hr > 110)
        else ECG_LABELS.get(ecg, ecg)
    )

    return {
        'pathway': pathway,
        'pathway_short': pw_short,
        'side_badge': side_badge,
        'rcri': rcri,
        'vsg_cri': vsg_cri,
        'ariscat': ariscat,
        'frailty_notes': frailty_notes,
        's1_required_fields': s1_required_fields,
        'missing_items': missing_items,
        'next_step_hint': next_step_hint,
        'self_fill_highlights': self_fill_highlights,
        'clearance_seal': clearance_seal,
        'surgical_brief': surgical_brief,
        'anesthetic_brief': anesthetic_brief,
        'passport_cautions': passport_cautions,
        'neuraxial_hard_blocked': neuraxial_hard_blocked,
        'eff_allergy_latex': ('latex_chlorhex' in allergy_chips),
        'show_pregnancy_check': show_pregnancy_check,
        'shock_index': shock_index,
        'fib_gl': fib,
        'is_venous': is_venous,
        'is_phleb': is_phleb,
        'is_pta': is_pta,
        'is_pad_bypass': is_pad_bypass,
        'is_on_hold': is_on_hold,
        'is_urgent_case': is_urgent_case,
        'is_symp_cea': is_symp_cea,
        'open_aortic': open_aortic,
        'has_synthetic_implant': has_synthetic_implant,
        'has_full_oac': has_full_oac,
        'has_neuraxial_plan': has_neuraxial_plan,
        'has_hard_contraindication': has_hard_contraindication,
        'cea_high_surg_risk': cea_high_surg_risk,
        'post_antithrombotic_label': post_antithrombotic_label,
        'lidocaine_max_mg': lidocaine_max_mg,
        'max_contrast_ml': max_contrast_ml,
        'co2_angio_advised': co2_angio_advised,
        'pbm_iv_iron_indicated': pbm_iv_iron_indicated,
        'nt_probnp': nt_probnp,
        'hs_ctn': hs_ctn,
        'tsat': tsat_val,
        'ldl': ldl_val,
        'person_id': person_id,
        'birth_date_ka': birth_date_ka,
        'labs_date_ka': labs_date_ka,
        'planned_surgery_ka': planned_surgery_ka,
        'planned_surgery_short': planned_surgery_short,
        'operated_date_ka': operated_date_ka,
        'allergy_summary': allergy_summary,
        'auto_asa': auto_asa,
        'asa_roman': asa_roman,
        'effective_asa': effective_asa,
        'mallampati_label': mallampati_label,
        'difficult_airway_alert': difficult_airway_alert,
        'stop_bang_auto': stop_bang_auto,
        'effective_stop_bang': effective_stop_bang,
        'stop_bang_label': stop_bang_label,
        'effective_anesth_label': effective_anesth_label,
        'anesth_lines_txt': anesth_lines_txt,
        'cied_plan_label': cied_plan_label,
        'needs_icu_bed': needs_icu_bed,
        'fasting_instruction': fasting_instruction,
        'surveillance_plan': surveillance_plan,
        'dos_checks': dos_checks,
        'dos_warnings': dos_warnings,
        'dos_hold_danger': dos_hold_danger,
        'dos_done_count': dos_done_count,
        'dos_total_count': dos_total_count,
        'dos_ready': dos_ready,
        'dos_gl_mmol': dos_gl,
        'case_outcome': case_outcome,
        'case_outcome_label': case_outcome_label,
        'vqi_bundle_applicable': vqi_bundle_applicable,
        'vqi_bundle_done': vqi_bundle_done,
        'vqi_30d_status': vqi_30d_status,
        'vqi_30d_label': vqi_30d_label,
        'status': status,
        'status_label': STATUS_LABELS[status],
        'workup_ready': bool((patient.approved_at or all_tasks_done) and not has_hard_contraindication and not solo_neuraxial_anatomic_mismatch),
        'priority_tier': priority_tier,
        'priority_label': PRIORITY_LABELS[priority_tier],
        'days_elapsed': days,
        'target_days': t_days,
        'over_target_days': over_target,
        'over_7_days': over_target,
        'key_badge': key_badge,
        'key_badge_urgent': key_badge_urgent,
        'red_flags': red_flags,
        'amber_flags': amber_flags,
        'reflex_orders': reflex_orders,
        'all_reflex_done': all_reflex_done,
        'med_schedule': med_schedule,
        'abx_prophylaxis_note': abx_prophylaxis_note,
        'caprini_score': caprini_score,
        'caprini_tier': caprini_tier,
        'caprini_advice': caprini_advice,
        'bp_arm_diff': bp_arm_diff,
        'labs_age_days': labs_age_days,
        'alerts_sig': current_sig,
        'alerts_acked': alerts_acked,
        'has_unacked_red': has_unacked_red,
        'egfr': egfr,
        'crcl': crcl,
        'creat_unit_ambiguous': creat_unit_ambiguous,
        'bmi': bmi,
        'hb_gl': hb_gl,
        'gl_mmol': gl_mmol,
        'alb_gl': alb_gl,
        'aaa_mm': aaa_mm,
        'cta_months': cta_months,
        'days_since_ev': days_since_ev,
        'ipsi_car': ipsi_car,
        'contra_car': contra_car,
        'min_abi': min_abi,
        'max_abi': max_abi,
        'min_tp': min_tp,
        'min_tbi': min_tbi,
        'abi_non_compressible': abi_non_compressible,
        'tbi_measured': tbi_measured,
        'tbi_comp_safe': tbi_comp_safe,
        'pad_gsv_mm': pad_gsv_mm,
        'needs_anemia_panel': needs_anemia_panel,
        'needs_hba1c_reflex': needs_hba1c_reflex,
        'needs_endo_reflex': needs_endo_reflex,
        'needs_nephro_panel': needs_nephro_panel,
        'needs_hydration': needs_hydration,
        'needs_tsh': needs_tsh,
        'needs_dental': needs_dental,
        'needs_contrast_premed': needs_contrast_premed,
        'needs_cied': needs_cied,
        'needs_tbi_check': needs_tbi_check,
        'needs_cea_antiplatelet': needs_cea_antiplatelet,
        'needs_alt_vein': needs_alt_vein,
        'needs_stale_labs': needs_stale_labs,
        'needs_spirometry': needs_spirometry,
        'needs_gastro': needs_gastro,
        'needs_ppi': needs_ppi,
        'needs_carotid_duplex': needs_carotid_duplex,
        'needs_cxr': needs_cxr,
        'needs_echo_or_bnp': needs_echo_or_bnp,
        'uti_suspected': uti_suspected,
        'needs_med_hold': needs_med_hold,
        'needs_brain_ct': needs_brain_ct,
        'needs_neuro': needs_neuro,
        'needs_laryngoscopy': needs_laryngoscopy,
        'needs_wound_care': needs_wound_care,
        'needs_abi_check': needs_abi_check,
        'needs_vte_prophylaxis': needs_vte_prophylaxis,
        'needs_ecg_venous': needs_ecg_venous,
        'needs_cardio': needs_cardio,
        'needs_anesth': needs_anesth,
        'needs_blood': open_major,
        'guideline_justifies_extra_cardio': guideline_justifies_extra_cardio,
        'cardio_non_compliant': cardio_non_compliant,
        's1_core_done': s1_core_done,
        'tasks': tasks,
        'done_count': done_count,
        'total_count': len(tasks),
        'ecg_label': ecg_display_label,
        'cxr_label': CXR_LABELS.get(cxr, cxr),
        'q': q,
        's': s,
    }



class PreopWeekView(LoginRequiredMixin, generic.TemplateView):
    """Tweek.so-style weekly calendar + Someday lists for Nurse-Led Preoperative Assessment."""
    template_name = 'mdf/preop/week.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        today = timezone.localdate()
        req_day = _date(self.request.GET.get('week')) or today
        monday = _monday_of(req_day)
        sunday = monday + datetime.timedelta(days=6)

        status_filter = self.request.GET.get('status', 'all')
        if status_filter not in ('all', STATUS_NO_Q, STATUS_PROGRESS, STATUS_ALERT, STATUS_READY):
            status_filter = 'all'
        pathway_filter = self.request.GET.get('pathway', 'all')
        if pathway_filter not in ('all', *PreopPatient.Pathway.values):
            pathway_filter = 'all'
        outcome_mode = 'done' if self.request.GET.get('outcome') == 'done' else 'active'
        q = clean_text(self.request.GET.get('q'), 100)

        qs = (
            PreopPatient.objects.filter(archived=False)
            .filter(
                Q(visit_date__gte=monday, visit_date__lte=sunday)
                | ~Q(someday_list='')
            )
            .select_related('surgeon', 'surgeon__profile', 'case')
            .order_by('id')
        )
        if pathway_filter != 'all':
            qs = qs.filter(pathway=pathway_filter)
        if q:
            qs = qs.filter(patient_name__icontains=q)

        all_non_archived = list(
            PreopPatient.objects.filter(archived=False)
            .select_related('surgeon', 'surgeon__profile')
            .order_by('visit_date', 'id')[:500]
        )
        pw_counts = {
            'all': 0,
            'aaa_infra': 0, 'cea': 0, 'abf': 0,
            'pad_bypass': 0, 'pad_pta': 0, 'venous': 0,
        }
        counts = {'all': 0, STATUS_NO_Q: 0, STATUS_PROGRESS: 0, STATUS_ALERT: 0, STATUS_READY: 0}
        ev_by_id = {}
        active_scheduled_total = 0
        completed_or_cancelled_total = 0

        for p in all_non_archived:
            ev = evaluate(p, today=today)
            ev_by_id[p.pk] = ev
            is_done_case = ev['case_outcome'] != 'scheduled'
            if is_done_case:
                completed_or_cancelled_total += 1
            else:
                active_scheduled_total += 1
            in_mode = is_done_case if outcome_mode == 'done' else (not is_done_case)
            if not in_mode:
                continue
            pw_counts['all'] += 1
            pw_counts[ev['pathway']] = pw_counts.get(ev['pathway'], 0) + 1
            if pathway_filter == 'all' or ev['pathway'] == pathway_filter:
                counts['all'] += 1
                counts[ev['status']] += 1

        visible_patients = []
        for p in qs:
            p.ev = ev_by_id.get(p.pk) or evaluate(p, today=today)
            is_done_case = p.ev['case_outcome'] != 'scheduled'
            in_mode = is_done_case if outcome_mode == 'done' else (not is_done_case)
            if not in_mode:
                continue
            p.surgeon_name = person_name(p.surgeon)
            if status_filter == 'all' or p.ev['status'] == status_filter:
                visible_patients.append(p)

        days = []
        for offset in range(7):
            d = monday + datetime.timedelta(days=offset)
            items = [p for p in visible_patients if p.visit_date == d and not p.someday_list]
            days.append({
                'date': d,
                'iso': d.isoformat(),
                'weekday': WEEKDAYS_KA[offset],
                'short_date': f'{d.day} {MONTHS_SHORT[d.month]}',
                'is_today': d == today,
                'patients': items,
                'empty_lines': range(max(0, (2 if offset >= 5 else 3) - len(items))),
            })

        someday_cols = []
        for key, label in PreopPatient.SomedayList.choices[1:]:
            items = [p for p in visible_patients if p.someday_list == key]
            someday_cols.append({'key': key, 'label': label, 'patients': items})

        # Surface overdue OR off-week acute vascular emergencies (symptomatic AAA pain / crescendo TIA) so nothing hides in next week!
        overdue_patients = []
        for p in all_non_archived:
            ev = ev_by_id[p.pk]
            if ev['case_outcome'] != 'scheduled':
                continue
            if pathway_filter != 'all' and ev['pathway'] != pathway_filter:
                continue
            has_acute_red = bool(
                ev['q'].get('aaa_pain') or ev['q'].get('cea_crescendo') or ev['q'].get('abf_wet_infection')
            )
            off_week_acute = (
                has_acute_red
                and p.someday_list != PreopPatient.SomedayList.HOLD
                and (not p.visit_date or not (monday <= p.visit_date <= sunday))
            )
            if ev['over_target_days'] or off_week_acute:
                p.ev = ev
                overdue_patients.append(p)

        ctx.update({
            'today': today,
            'monday': monday,
            'prev_week': (monday - datetime.timedelta(days=7)).isoformat(),
            'next_week': (monday + datetime.timedelta(days=7)).isoformat(),
            'week_title': _week_title(monday),
            'days_mon_fri': days[:5],
            'day_sat': days[5],
            'day_sun': days[6],
            'someday_cols': someday_cols,
            'status_filter': status_filter,
            'pathway_filter': pathway_filter,
            'outcome_mode': outcome_mode,
            'active_scheduled_total': active_scheduled_total,
            'completed_or_cancelled_total': completed_or_cancelled_total,
            'q': q,
            'doctors': doctors(),
            'op_subtype_groups': OP_SUBTYPE_GROUPS,
            'overdue_patients': overdue_patients,
            'pathway_pills': [
                ('all', 'ყველა', pw_counts['all']),
                ('aaa_infra', 'AAA', pw_counts['aaa_infra']),
                ('cea', 'CEA', pw_counts['cea']),
                ('abf', 'ABF', pw_counts['abf']),
                ('pad_bypass', 'პერიფ. შუნტირება', pw_counts['pad_bypass']),
                ('pad_pta', 'PTA / სტენტი', pw_counts['pad_pta']),
                ('venous', 'ვენური (EVLA/ფლებ.)', pw_counts['venous']),
            ],
            'filter_pills': [
                ('all', 'ყველა', counts['all']),
                (STATUS_NO_Q, 'კითხვარი შესავსებია', counts[STATUS_NO_Q]),
                (STATUS_PROGRESS, 'კვლევებშია', counts[STATUS_PROGRESS]),
                (STATUS_ALERT, 'ექიმის ჩართვა', counts[STATUS_ALERT]),
                (STATUS_READY, 'მზადაა ექიმისთვის', counts[STATUS_READY]),
            ],
        })
        return ctx


class PreopCreateView(LoginRequiredMixin, generic.View):
    """Inline 1-line Tweek add (POST) or 1-click transfer/re-sync from a Conference Case (GET/POST)."""

    def get(self, request):
        case_id = request.GET.get('case', '')
        if case_id.isdigit():
            case = get_object_or_404(Case, pk=int(case_id))
            existing = PreopPatient.objects.filter(case=case, archived=False).first()
            if existing:
                if request.GET.get('sync') == '1':
                    op_sub, side = detect_case_preop(case)
                    if not existing.side and side:
                        existing.side = side
                        existing.save(update_fields=['side'])
                    import_case_to_preop(case, existing)
                    messages.success(request, 'კონფერენციის ქეისიდან სტადირება და ანატომიური მონაცემები განახლდა.')
                return HttpResponseRedirect(reverse('preop-patient', args=[existing.pk]))
            op_sub, side = detect_case_preop(case)
            p = PreopPatient.objects.create(
                case=case,
                patient_name=case.patient_name,
                age=case.age,
                sex=case.sex or 'M',
                side=side,
                op_subtype=op_sub,
                surgeon=case.doctor or request.user,
                someday_list=PreopPatient.SomedayList.CONF,
                created_by=request.user,
                updated_by=request.user,
            )
            import_case_to_preop(case, p)
            messages.success(request, f'{p.patient_name} გადმოყვანილია წინასაოპერაციო სიაში.')
            return HttpResponseRedirect(reverse('preop-patient', args=[p.pk]))
        return HttpResponseRedirect(reverse('preop'))

    def post(self, request):
        name = clean_text(request.POST.get('patient_name'), 150).replace('\n', ' ')
        slot = (request.POST.get('slot') or '').strip()
        op_subtype = request.POST.get('op_subtype') or PreopPatient.OpSubtype.OPEN
        if op_subtype not in PreopPatient.OpSubtype.values:
            op_subtype = PreopPatient.OpSubtype.OPEN
        side = request.POST.get('side') if request.POST.get('side') in ('R', 'L', 'B') else ''

        surgeon = None
        surgeon_raw = request.POST.get('surgeon') or ''
        if surgeon_raw.isdigit():
            surgeon = get_user_model().objects.filter(pk=int(surgeon_raw)).first()
        if surgeon is None and hasattr(request.user, 'profile'):
            surgeon = request.user

        if not name:
            messages.error(request, 'ჩაწერეთ პაციენტის სახელი და გვარი.')
            return HttpResponseRedirect(reverse('preop'))

        visit_date = None
        someday = ''
        started_on = None
        init_studies = {}
        today = timezone.localdate()
        default_sex = 'F' if op_subtype in ('evla', 'phlebectomy') and request.POST.get('sex') not in ('M', 'F') else 'M'
        sex = request.POST.get('sex') if request.POST.get('sex') in ('M', 'F') else default_sex
        if slot.startswith('someday:'):
            key = slot.split(':', 1)[1]
            someday = key if key in PreopPatient.SomedayList.values else PreopPatient.SomedayList.CONF
            if someday == PreopPatient.SomedayList.HOLD:
                init_studies['hold_since'] = today.isoformat()
        else:
            visit_date = _date(slot) or today
            started_on = visit_date

        p = PreopPatient.objects.create(
            patient_name=name,
            age=_int(request.POST.get('age'), lo=1, hi=120),
            sex=sex,
            side=side,
            op_subtype=op_subtype,
            surgeon=surgeon,
            visit_date=visit_date,
            someday_list=someday,
            started_on=started_on,
            studies=init_studies,
            created_by=request.user,
            updated_by=request.user,
        )
        if request.POST.get('open_card') == '1':
            return HttpResponseRedirect(reverse('preop-patient', args=[p.pk]))
        week_q = f'?week={visit_date.isoformat()}' if visit_date else ''
        return HttpResponseRedirect(reverse('preop') + week_q)


class PreopPatientView(LoginRequiredMixin, generic.View):
    """Individual Preoperative Assessment Workspace for one patient (5 fast tabs)."""
    template_name = 'mdf/preop/patient.html'

    def get(self, request, pk):
        patient = get_object_or_404(
            PreopPatient.objects.select_related('surgeon', 'surgeon__profile', 'approved_by', 'alerts_ack_by', 'case'),
            pk=pk,
        )
        return self._render(request, patient)

    def post(self, request, pk):
        patient = get_object_or_404(PreopPatient, pk=pk)
        section = request.POST.get('section') or 'header'

        with _PREOP_MUTATION_LOCK, transaction.atomic():
            _sqlite_acquire_write_lock(patient.pk)
            patient = PreopPatient.objects.select_for_update().get(pk=patient.pk)

            if section == 'header':
                self._save_header(request, patient)
                messages.success(request, 'პაციენტის მონაცემები განახლდა.')
                return HttpResponseRedirect(reverse('preop-patient', args=[patient.pk]) + f"?tab={request.POST.get('tab', 'q')}")

            if section == 'questionnaire':
                self._save_questionnaire(request, patient)
                messages.success(request, 'კითხვარები შეინახა — განახლდა ანამნეზი და მომზადების გეგმა.')
                return HttpResponseRedirect(reverse('preop-patient', args=[patient.pk]) + '?tab=s1')

            if section == 'stage1':
                self._save_stage1(request, patient)
                messages.success(request, 'საფეხური 1-ის ანალიზები და კვლევები შეინახა.')
                return HttpResponseRedirect(reverse('preop-patient', args=[patient.pk]) + '?tab=s2')

            if section == 'stage2':
                self._save_stage2(request, patient)
                messages.success(request, 'მომზადების გეგმა და კონსულტაციები შეინახა.')
                return HttpResponseRedirect(reverse('preop-patient', args=[patient.pk]) + '?tab=s2')

            if section == 'signoff':
                return self._save_signoff(request, patient)

            if section == 'dos_vqi':
                return self._save_dos_vqi(request, patient)

            if section == 'self_link':
                return self._save_self_link(request, patient)

        return HttpResponseRedirect(reverse('preop-patient', args=[patient.pk]))

    def _save_header(self, request, patient):
        s = merged_studies(patient)
        today = timezone.localdate()
        old_someday = patient.someday_list
        old_side = patient.side
        old_sub = patient.op_subtype
        old_pw = _resolve_pathway(patient)
        was_unstarted = (patient.started_on is None)

        name = clean_text(request.POST.get('patient_name'), 150).replace('\n', ' ')
        if name:
            patient.patient_name = name
        age_in = _int(request.POST.get('age'), lo=1, hi=120)
        if 'person_id' in request.POST:
            s['person_id'] = _clean_person_id(request.POST.get('person_id'))
        if 'birth_date' in request.POST:
            bd = _date(request.POST.get('birth_date'))
            s['birth_date'] = bd.isoformat() if (bd and bd <= today) else ''
            if age_in is None and bd:
                age_in = _age_from_dob(bd)
        if age_in is not None or 'age' in request.POST:
            patient.age = age_in
        if 'planned_surgery_date' in request.POST:
            op_d = _date(request.POST.get('planned_surgery_date'))
            s['planned_surgery_date'] = op_d.isoformat() if op_d else ''

        sex = request.POST.get('sex') or ''
        if sex in ('M', 'F', ''):
            patient.sex = sex
        if 'side' in request.POST and request.POST.get('side') in ('', 'R', 'L', 'B'):
            patient.side = request.POST.get('side')
        patient.phone = clean_text(request.POST.get('phone'), 50).replace('\n', ' ')
        op_sub = request.POST.get('op_subtype') or ''
        if op_sub in PreopPatient.OpSubtype.values:
            patient.op_subtype = op_sub
        surg_raw = request.POST.get('surgeon') or ''
        patient.surgeon = get_user_model().objects.filter(pk=int(surg_raw)).first() if surg_raw.isdigit() else None

        slot_kind = request.POST.get('slot_kind') or ''
        if slot_kind.startswith('someday:'):
            key = slot_kind.split(':', 1)[1]
            patient.someday_list = key if key in PreopPatient.SomedayList.values else PreopPatient.SomedayList.CONF
            patient.visit_date = None
        else:
            vd = _date(request.POST.get('visit_date'))
            if vd:
                patient.visit_date = vd
                patient.someday_list = ''
                if not patient.started_on:
                    patient.started_on = vd

        _update_hold_pause(
            s, old_someday, patient.someday_list, today=today,
            started_on=(None if was_unstarted else patient.started_on),
        )

        new_pw = PreopPatient.SUBTYPE_PATHWAY.get(patient.op_subtype, patient.pathway)
        uf = [
            'patient_name', 'age', 'sex', 'side', 'phone', 'op_subtype', 'pathway',
            'surgeon', 'visit_date', 'someday_list', 'started_on', 'studies',
            'updated_at', 'updated_by',
        ]
        if patient.side != old_side or patient.op_subtype != old_sub:
            s['dos_site_marked'] = False
            s['alerts_ack_sig'] = ''
            patient.approved_at = None
            patient.approved_by = None
            patient.alerts_ack_at = None
            patient.alerts_ack_by = None
            patient.anamnesis = ''
            uf.extend(['approved_at', 'approved_by', 'alerts_ack_at', 'alerts_ack_by', 'anamnesis'])
        if new_pw != old_pw and new_pw in (PreopPatient.Pathway.VENOUS, PreopPatient.Pathway.PAD_PTA):
            s['carotid_symp'] = False

        patient.studies = s
        patient.updated_by = request.user
        patient.save(update_fields=uf)

    def _save_questionnaire(self, request, patient):
        old_side = patient.side
        apply_questionnaire_post(request.POST, patient, person_name(request.user))
        patient.updated_by = request.user
        uf = ['questionnaire', 'studies', 'side', 'started_on', 'updated_at', 'updated_by']
        if patient.side != old_side:
            uf.extend(['approved_at', 'approved_by', 'anamnesis'])
        patient.save(update_fields=uf)

    def _save_stage1(self, request, patient):
        s = merged_studies(patient)
        today = timezone.localdate()

        if 'wbc' in request.POST:
            raw_wbc = _num(request.POST.get('wbc'), lo=0.1, hi=150000)
            if raw_wbc is not None and raw_wbc >= 500:
                raw_wbc = round(raw_wbc / 1000.0, 2)
            s['wbc'] = _num(raw_wbc, lo=0.1, hi=150) if raw_wbc is not None else None
        if 'plt' in request.POST:
            raw_plt = _num(request.POST.get('plt'), lo=1, hi=2000000)
            if raw_plt is not None and raw_plt >= 10000:
                raw_plt = int(round(raw_plt / 1000.0))
            s['plt'] = _num(raw_plt, lo=1, hi=2000) if raw_plt is not None else None

        num_fields = (
            ('aaa_mm', 10, 200), ('cta_months', 0, 120),
            ('ven_gsv_mm', 1, 50), ('pad_gsv_mm', 0.5, 30),
            ('abi_r', 0.0, 3.0), ('abi_l', 0.0, 3.0),
            ('tp_r', 0, 400), ('tp_l', 0, 400),
            ('tbi_r', 0.0, 3.0), ('tbi_l', 0.0, 3.0),
            ('bp_sys', 40, 300), ('bp_dia', 20, 200),
            ('bp_sys_l', 40, 300), ('bp_dia_l', 20, 200),
            ('hr', 20, 250), ('spo2', 50, 100),
            ('weight_kg', 25, 260), ('height_cm', 100, 230),
            ('hb', 1, 260), ('inr', 0.5, 15),
            ('creat', 0.1, 3000), ('k', 1.5, 10), ('na', 100, 180),
            ('glucose', 0.5, 1000), ('hba1c', 3, 25),
            ('ferritin', 1, 5000), ('tsat', 1, 100),
            ('nt_probnp', 1, 50000), ('hs_ctn', 0.1, 50000),
            ('albumin', 0.5, 80),
            ('urine_wbc', 0, 1000),
            ('carotid_r', 0, 100), ('carotid_l', 0, 100), ('lvef', 5, 90),
        )
        for key, lo, hi in num_fields:
            if key in request.POST:
                s[key] = _num(request.POST.get(key), lo=lo, hi=hi)
        if 'fibrinogen' in request.POST:
            s['fibrinogen'] = normalize_fibrinogen_gl(request.POST.get('fibrinogen'))
        if 'ldl' in request.POST:
            s['ldl'] = normalize_ldl_mmol(request.POST.get('ldl'))

        if 'cta_date' in request.POST:
            cd = _date(request.POST.get('cta_date'))
            s['cta_date'] = cd.isoformat() if (cd and cd <= today) else ''
            if cd and cd <= today:
                s['cta_months'] = cta_live_months_age({'cta_date': s['cta_date']}, today=today)

        if 'labs_date' in request.POST:
            ld = _date(request.POST.get('labs_date'))
            s['labs_date'] = ld.isoformat() if (ld and ld <= today) else ''
            if ld and ld <= today and (today - ld).days <= 14:
                s['stale_labs_refreshed'] = False

        if 'creat_unit' in request.POST and request.POST.get('creat_unit') in ('', 'umol', 'mgdl'):
            s['creat_unit'] = request.POST.get('creat_unit')

        for bf in (
            'cta_disc', 'irregular_hr', 'aptt_prolonged', 'viral_positive',
            'urine_nitrite', 'carotid_symp', 'echo_valve_issue',
            'aaa_renal_vein_anom', 'abf_renal_stenosis',
            'ven_epifascial', 'ven_tortuous', 'ven_perforator',
        ):
            if bf in request.POST or bf in ('irregular_hr', 'aptt_prolonged', 'viral_positive', 'carotid_symp', 'echo_valve_issue'):
                s[bf] = request.POST.get(bf) in ('1', 'on', 'true')
            elif bf == 'cta_disc' and patient.pathway not in (PreopPatient.Pathway.VENOUS, PreopPatient.Pathway.PAD_PTA):
                s[bf] = False
            elif bf == 'aaa_renal_vein_anom' and patient.pathway == PreopPatient.Pathway.AAA_INFRA:
                s[bf] = False
            elif bf == 'abf_renal_stenosis' and patient.pathway == PreopPatient.Pathway.ABF:
                s[bf] = False
            elif bf in ('ven_epifascial', 'ven_tortuous', 'ven_perforator') and patient.pathway == PreopPatient.Pathway.VENOUS:
                s[bf] = False
            elif bf == 'urine_nitrite' and patient.pathway not in (PreopPatient.Pathway.VENOUS, PreopPatient.Pathway.PAD_PTA):
                s[bf] = False

        if 'blood_group' in request.POST:
            s['blood_group'] = clean_text(request.POST.get('blood_group'), 30)
        for sel_key, allowed in (
            ('liver_status', ('normal', '2x', '3x')),
            ('cxr', ('pending', 'normal', 'emphysema', 'infiltrate')),
            ('ecg', ('pending', 'normal', 'afib_controlled', 'ischemia_arrhythmia')),
            ('aaa_morphology', ('fusiform', 'saccular', 'rapid_growth', 'inflammatory', 'inflammatory_mycotic')),
            ('aaa_neck_status', ('pending', 'friendly_ifu', 'hostile_neck')),
            ('popliteal_screen_status', ('pending', 'normal', 'aneurysm_found')),
            ('aaa_iliac_access', ('pending', 'ok_7mm', 'narrow_calcified', 'iliac_aneurysm')),
            ('cea_cta_status', ('pending', 'concordant', 'high_calcified')),
            ('brain_imaging', ('pending', 'normal_small', 'large_or_hemorrhage')),
            ('abf_clamp_zone', ('pending', 'infrarenal', 'juxtarenal_calcified')),
            ('abf_outflow', ('pending', 'patent', 'profunda_stenosis', 'poor_outflow')),
            ('abf_mesenteric', ('normal', 'ima_dominant_iia_occ', 'sma_stenosis')),
            ('pad_runoff_status', ('pending', 'popliteal_patent', 'tibial_single', 'desert_foot')),
            ('pad_gsv_status', ('pending', 'adequate', 'inadequate')),
            ('pta_lesion_site', ('iliac', 'femoropopliteal', 'infrapopliteal', 'multilevel')),
            ('pta_access_site', ('pending', 'cfa_clean', 'cfa_calcified_scar')),
            ('ven_duplex_status', ('pending', 'deep_patent_reflux', 'acute_dvt', 'deep_obstruction', 'superficial_thrombus')),
            ('ven_target_vein', ('gsv', 'ssv', 'gsv_ssv', 'aagsv_trib')),
        ):
            if request.POST.get(sel_key) in allowed:
                s[sel_key] = request.POST.get(sel_key)

        if not patient.started_on:
            patient.started_on = patient.visit_date or today
        patient.studies = s
        patient.updated_by = request.user
        patient.save(update_fields=['studies', 'started_on', 'updated_at', 'updated_by'])

    def _save_stage2(self, request, patient):
        ev_before = evaluate(patient)
        s = merged_studies(patient)
        old_nurse_chips = list(s.get('nurse_note_chips') or [])
        old_nurse_chip_txt = chip_summary(old_nurse_chips, 'nurse_note_chips')
        old_cardio_chips = list(s.get('cardio_note_chips') or [])
        old_cardio_chip_txt = chip_summary(old_cardio_chips, 'cardio_note_chips')

        if 'spiro_fev1' in request.POST:
            s['spiro_fev1'] = _int(request.POST.get('spiro_fev1'), lo=10, hi=150)
        if 'abi_r' in request.POST:
            s['abi_r'] = _num(request.POST.get('abi_r'), lo=0.0, hi=3.0)
        if 'abi_l' in request.POST:
            s['abi_l'] = _num(request.POST.get('abi_l'), lo=0.0, hi=3.0)
        if 'tp_r' in request.POST:
            s['tp_r'] = _int(request.POST.get('tp_r'), lo=0, hi=400)
        if 'tp_l' in request.POST:
            s['tp_l'] = _int(request.POST.get('tp_l'), lo=0, hi=400)
        if 'tbi_r' in request.POST:
            s['tbi_r'] = _num(request.POST.get('tbi_r'), lo=0.0, hi=3.0)
        if 'tbi_l' in request.POST:
            s['tbi_l'] = _num(request.POST.get('tbi_l'), lo=0.0, hi=3.0)
        if 'hba1c' in request.POST:
            s['hba1c'] = _num(request.POST.get('hba1c'), lo=3, hi=25)
        if 'ferritin' in request.POST:
            s['ferritin'] = _num(request.POST.get('ferritin'), lo=1, hi=5000)
        if 'tsat' in request.POST:
            s['tsat'] = _num(request.POST.get('tsat'), lo=1, hi=100)

        if 'planned_surgery_date' in request.POST:
            op_d = _date(request.POST.get('planned_surgery_date'))
            s['planned_surgery_date'] = op_d.isoformat() if op_d else ''

        for sel_key, allowed in (
            ('anesthesia_plan', ('auto', 'local', 'plexus', 'neuraxial', 'general')),
            ('asa_class', ('auto', 'I', 'II', 'III', 'IV')),
            ('airway_mallampati', ('I_II', 'III', 'IV_difficult')),
            ('stop_bang_risk', ('auto', 'low', 'intermediate', 'high')),
            ('surg_duration_est', ('auto', 'lt_2h', '2_3h', 'gt_3h')),
            ('cied_plan', ('pending', 'bipolar_only', 'magnet_async', 'ep_reprogram')),
            ('gastro_result', ('pending', 'remission', 'acute_ulcer')),
            ('laryngoscopy_res', ('pending', 'normal', 'palsy')),
            ('tsh_status', ('pending', 'euthyroid', 'hyperthyroid', 'hypothyroid_severe')),
            ('alt_vein_mapped', ('pending', 'contra_gsv_ok', 'arm_ssv_ok', 'no_autovein_prosthetic')),
            ('post_antithrombotic_plan', ('auto', 'voyager', 'dapt', 'sapt', 'oac_sapt')),
        ):
            if request.POST.get(sel_key) in allowed:
                s[sel_key] = request.POST.get(sel_key)

        s2_bool_visible = {
            'anesth_done': True,
            'stale_labs_refreshed': bool(ev_before['needs_stale_labs']),
            'anemia_panel_done': bool(ev_before['needs_anemia_panel']),
            'pbm_iv_iron_done': bool(ev_before['pbm_iv_iron_indicated'] and not ev_before['is_urgent_case']),
            'endo_consult_done': bool(ev_before['needs_endo_reflex']),
            'nephro_panel_done': bool(ev_before['needs_nephro_panel']),
            'hydration_planned': bool(ev_before['needs_hydration']),
            'dental_sanitation_done': bool(ev_before['needs_dental']),
            'contrast_premed_done': bool(ev_before['needs_contrast_premed']),
            'cied_checked': bool(ev_before['needs_cied']),
            'cea_antiplatelet_started': bool(ev_before['pathway'] == 'cea' and ev_before['needs_cea_antiplatelet']),
            'vte_prophylaxis_planned': bool(ev_before['is_venous'] and ev_before['needs_vte_prophylaxis']),
            'compression_ready': bool(ev_before['is_venous'] and (ev_before['min_abi'] is None or ev_before['min_abi'] >= 0.8)),
            'dapt_ready': bool(ev_before['is_pta'] and ev_before['q'].get('filled')),
            'neuro_consult_done': bool(ev_before['pathway'] == 'cea' and ev_before['needs_neuro']),
            'ppi_started': bool(ev_before['needs_ppi']),
            'urine_culture_done': bool(ev_before['uti_suspected']),
            'wound_care_done': bool(ev_before['needs_wound_care']),
            'blood_reserved': bool(ev_before['needs_blood']),
            'cell_saver_ready': bool(ev_before['needs_blood'] and ev_before['open_aortic']),
            'evar_sized': bool(ev_before['pathway'] == 'aaa_infra' and patient.op_subtype == 'evar'),
            'decolonization_ready': bool(ev_before['has_synthetic_implant']),
            'med_hold_agreed': bool(ev_before['needs_med_hold']),
        }
        for bf in (
            'urine_culture_done', 'blood_reserved', 'evar_sized', 'med_hold_agreed',
            'anesth_done', 'neuro_consult_done', 'wound_care_done',
            'vte_prophylaxis_planned', 'compression_ready',
            'anemia_panel_done', 'pbm_iv_iron_done', 'cell_saver_ready', 'ppi_started',
            'endo_consult_done', 'nephro_panel_done',
            'hydration_planned', 'dental_sanitation_done', 'decolonization_ready',
            'contrast_premed_done', 'dapt_ready', 'stale_labs_refreshed',
            'cea_antiplatelet_started', 'cied_checked',
        ):
            if bf in request.POST:
                s[bf] = request.POST.get(bf) in ('1', 'on', 'true')
            elif s2_bool_visible.get(bf, True):
                s[bf] = False
        if (s.get('cied_plan') or 'pending') != 'pending':
            s['cied_checked'] = True

        cab_present = 'anesth_cabinet_present' in request.POST
        if cab_present or 'asa_emergency' in request.POST:
            s['asa_emergency'] = request.POST.get('asa_emergency') in ('1', 'on', 'true')
        if 'icu_bed_field_present' in request.POST or 'icu_bed_booked' in request.POST:
            s['icu_bed_booked'] = request.POST.get('icu_bed_booked') in ('1', 'on', 'true')

        if hasattr(request.POST, 'getlist'):
            s['nurse_note_chips'] = [
                k for k in request.POST.getlist('nurse_note_chips')
                if k in CHIP_LABELS['nurse_note_chips']
            ]
            s['cardio_note_chips'] = [
                k for k in request.POST.getlist('cardio_note_chips')
                if k in CHIP_LABELS['cardio_note_chips']
            ]
            if cab_present or 'anesth_lines_chips' in request.POST:
                s['anesth_lines_chips'] = [
                    k for k in request.POST.getlist('anesth_lines_chips')
                    if k in CHIP_LABELS['anesth_lines_chips']
                ]

        new_nurse_chip_txt = chip_summary(s.get('nurse_note_chips'), 'nurse_note_chips')
        if 'nurse_note_custom' in request.POST:
            custom_nn = clean_text(request.POST.get('nurse_note_custom'), 1000)
            s['nurse_note_custom'] = custom_nn
            s['nurse_note'] = '; '.join([x for x in (new_nurse_chip_txt, custom_nn) if x])[:1000]
        else:
            raw_nurse = clean_text(request.POST.get('nurse_note'), 1000)
            if raw_nurse == old_nurse_chip_txt and s['nurse_note_chips'] != old_nurse_chips:
                raw_nurse = ''
            s['nurse_note_custom'] = '' if raw_nurse == new_nurse_chip_txt else raw_nurse
            s['nurse_note'] = raw_nurse or new_nurse_chip_txt

        uf = ['studies', 'updated_at', 'updated_by']
        if 'cardiologist' in request.POST or 'cardiologist_custom' in request.POST:
            cardio_name = clean_text(
                request.POST.get('cardiologist_custom') or request.POST.get('cardiologist'), 100
            ).replace('\n', ' ')
            patient.cardiologist = cardio_name or 'ლიკა ინჯია'
            uf.append('cardiologist')
        if 'cardio_decision' in request.POST:
            dec = request.POST.get('cardio_decision') or ''
            if dec in PreopPatient.CardioDecision.values:
                old_dec = patient.cardio_decision
                patient.cardio_decision = dec
                if dec and not patient.cardio_date:
                    patient.cardio_date = timezone.localdate()
                elif not dec:
                    patient.cardio_date = None
                uf.extend(['cardio_decision', 'cardio_date'])
                if dec != old_dec and dec in ('stress', 'cag', 'pci'):
                    patient.alerts_ack_at = None
                    s['alerts_ack_sig'] = ''
                    uf.append('alerts_ack_at')

        new_cardio_chip_txt = chip_summary(s.get('cardio_note_chips'), 'cardio_note_chips')
        if 'cardio_note_custom' in request.POST:
            custom_cn = clean_text(request.POST.get('cardio_note_custom'), 255).replace('\n', ' ')
            s['cardio_note_custom'] = custom_cn
            patient.cardio_note = '; '.join([x for x in (new_cardio_chip_txt, custom_cn) if x])[:255]
            uf.append('cardio_note')
        elif 'cardio_note' in request.POST or 'cardio_note_chips' in request.POST:
            raw_cn = clean_text(request.POST.get('cardio_note'), 255).replace('\n', ' ')
            if raw_cn == old_cardio_chip_txt and s['cardio_note_chips'] != old_cardio_chips:
                raw_cn = ''
            s['cardio_note_custom'] = '' if raw_cn == new_cardio_chip_txt else raw_cn
            patient.cardio_note = (raw_cn or new_cardio_chip_txt)[:255]
            uf.append('cardio_note')

        patient.studies = s
        patient.updated_by = request.user
        patient.save(update_fields=list(dict.fromkeys(uf)))

    def _save_dos_vqi(self, request, patient):
        s = merged_studies(patient)
        today = timezone.localdate()
        action = request.POST.get('action') or 'save_dos'
        form_present = 'dos_form_present' in request.POST

        if 'planned_surgery_date' in request.POST:
            op_d = _date(request.POST.get('planned_surgery_date'))
            s['planned_surgery_date'] = op_d.isoformat() if op_d else ''
        if 'operated_date' in request.POST:
            done_d = _date(request.POST.get('operated_date'))
            s['operated_date'] = done_d.isoformat() if (done_d and done_d <= today) else ''

        for key, lo, hi in (
            ('dos_bp_sys', 40, 300),
            ('dos_bp_dia', 20, 200),
            ('dos_glucose', 0.5, 1000),
            ('dos_inr', 0.5, 15),
        ):
            if key in request.POST:
                s[key] = _num(request.POST.get(key), lo=lo, hi=hi)

        for bf in (
            'dos_identity_consent', 'dos_site_marked', 'dos_fasting_confirmed',
            'dos_med_hold_verified', 'dos_ketones_ok', 'dos_k_verified', 'dos_latex_free_or',
            'dos_abx_ready', 'dos_blood_implant_ready',
            'icu_bed_booked',
            'vqi_discharge_antiplatelet', 'vqi_discharge_statin',
            'vqi_smoking_cessation', 'vqi_surveillance_scheduled',
        ):
            if form_present or bf in request.POST:
                s[bf] = request.POST.get(bf) in ('1', 'on', 'true')

        if request.POST.get('case_outcome_status') in dict(CASE_OUTCOME_CHOICES):
            s['case_outcome_status'] = request.POST.get('case_outcome_status')
        if request.POST.get('vqi_30d_status') in dict(VQI_30D_CHOICES):
            s['vqi_30d_status'] = request.POST.get('vqi_30d_status')

        if hasattr(request.POST, 'getlist'):
            if form_present or 'cancel_reason_chips' in request.POST:
                s['cancel_reason_chips'] = [
                    k for k in request.POST.getlist('cancel_reason_chips')
                    if k in CHIP_LABELS['cancel_reason_chips']
                ]
            if form_present or 'vqi_complication_chips' in request.POST:
                s['vqi_complication_chips'] = [
                    k for k in request.POST.getlist('vqi_complication_chips')
                    if k in CHIP_LABELS['vqi_complication_chips']
                ]
                if s['vqi_complication_chips'] and s.get('vqi_30d_status') in ('pending', 'uneventful', None, ''):
                    s['vqi_30d_status'] = 'complication'

        if action == 'complete_and_board':
            s['case_outcome_status'] = 'operated'
            if not s.get('operated_date'):
                s['operated_date'] = today.isoformat()
            s['outcome_recorded_on'] = today.isoformat()
            patient.studies = s
            patient.updated_by = request.user
            patient.save(update_fields=['studies', 'updated_at', 'updated_by'])
            bp, _, added_tasks = admit_preop_to_board(
                patient,
                request.user,
                unit=request.POST.get('unit') or '',
                bed=request.POST.get('bed') or '',
            )
            messages.success(
                request,
                f'{patient.patient_name} — ოპერაცია დაფიქსირდა და პაციენტი გადაყვანილია დაფაზე ({bp.get_unit_display()}, +{added_tasks} დავალება).',
            )
            return HttpResponseRedirect(reverse('board-patient', args=[bp.pk]))

        if s.get('case_outcome_status') == 'operated' and not s.get('operated_date'):
            plan_dt = _date(s.get('planned_surgery_date'))
            s['operated_date'] = (plan_dt.isoformat() if (plan_dt and plan_dt <= today) else today.isoformat())
        if s.get('case_outcome_status') != 'scheduled' and not s.get('outcome_recorded_on'):
            s['outcome_recorded_on'] = today.isoformat()
        elif s.get('case_outcome_status') == 'scheduled':
            s['outcome_recorded_on'] = ''

        patient.studies = s
        patient.updated_by = request.user
        patient.save(update_fields=['studies', 'updated_at', 'updated_by'])
        messages.success(request, 'ოპერაციის დილის კარიბჭე და 30-დღიანი VQI მონიტორინგი შეინახა.')
        return HttpResponseRedirect(reverse('preop-patient', args=[patient.pk]) + '?tab=dos')

    def _save_signoff(self, request, patient):
        action = request.POST.get('action') or 'save_anamnesis'
        can_approve_signoff = (
            request.user.is_superuser
            or request.user.is_staff
            or hasattr(request.user, 'profile')
            or patient.surgeon_id is None
            or patient.surgeon_id == request.user.pk
        )
        if action in ('approve', 'unapprove') and not can_approve_signoff:
            messages.error(request, 'ოპერაციაზე ქირურგის დასტურის/მოხსნის უფლება აქვს მხოლოდ მკურნალ ქირურგს ან ექიმს!')
            return HttpResponseRedirect(reverse('preop-patient', args=[patient.pk]) + '?tab=doc')

        uf = ['updated_at', 'updated_by']
        # Only persist anamnesis on explicit save_anamnesis / approve (NEVER freeze auto-anamnesis on Day-1 ack_alerts or unapprove!)
        if 'anamnesis' in request.POST:
            if action == 'regenerate':
                patient.anamnesis = ''
                uf.append('anamnesis')
            elif action in ('save_anamnesis', 'approve'):
                patient.anamnesis = clean_text(request.POST.get('anamnesis'), 4000)
                uf.append('anamnesis')

        ev = evaluate(patient)
        s = merged_studies(patient)

        if action == 'ack_alerts':
            s['alerts_ack_sig'] = ev['alerts_sig']
            patient.studies = s
            patient.alerts_ack_at = timezone.now()
            patient.alerts_ack_by = request.user
            uf.extend(['studies', 'alerts_ack_at', 'alerts_ack_by'])
            messages.success(request, 'გაფრთხილებები მონიშნულია როგორც გაცნობილი.')
        elif action == 'approve':
            if ev.get('has_hard_contraindication'):
                messages.error(request, 'აბსოლუტური კლინიკური უკუჩვენების გამო ოპერაციაზე დადასტურება დაბლოკილია!')
                return HttpResponseRedirect(reverse('preop-patient', args=[patient.pk]) + '?tab=doc')
            s['alerts_ack_sig'] = ev['alerts_sig']
            if request.POST.get('move_ready') == '1':
                _update_hold_pause(s, patient.someday_list, PreopPatient.SomedayList.READY, started_on=patient.started_on)
                patient.someday_list = PreopPatient.SomedayList.READY
                patient.visit_date = None
                uf.extend(['someday_list', 'visit_date'])
            patient.studies = s
            patient.approved_at = timezone.now()
            patient.approved_by = request.user
            uf.extend(['studies', 'approved_at', 'approved_by'])
            messages.success(request, f'{patient.patient_name} — დადასტურებულია ოპერაციისთვის.')
        elif action == 'unapprove':
            patient.approved_at = None
            patient.approved_by = None
            uf.extend(['approved_at', 'approved_by'])
            messages.info(request, 'დადასტურება მოიხსნა.')
        else:
            messages.success(request, 'ანამნეზი შეინახა.')

        patient.updated_by = request.user
        patient.save(update_fields=list(dict.fromkeys(uf)))
        return HttpResponseRedirect(reverse('preop-patient', args=[patient.pk]) + '?tab=doc')

    def _save_self_link(self, request, patient):
        act = request.POST.get('action') or 'rotate'
        q = merged_q(patient)
        s = merged_studies(patient)
        uf = ['studies', 'updated_at', 'updated_by']
        if act == 'revoke':
            s['self_fill_revoked'] = True
            messages.info(request, 'პაციენტის თვითშევსების ბმული გაუქმდა.')
        else:
            s['self_fill_nonce'] = secrets.token_hex(4)
            s['self_fill_revoked'] = False
            q['self_fill_submitted'] = False
            patient.questionnaire = q
            uf.append('questionnaire')
            messages.success(request, 'ახალი ერთჯერადი ბმული გენერირდა (წინა ბმული გაუქმდა).')
        patient.studies = s
        patient.updated_by = request.user
        patient.save(update_fields=uf)
        tab_ret = request.POST.get('tab') or 'q'
        if tab_ret not in ('q', 's1', 's2', 'doc', 'dos'):
            tab_ret = 'q'
        return HttpResponseRedirect(reverse('preop-patient', args=[patient.pk]) + f'?tab={tab_ret}')

    def _render(self, request, patient):
        ev = evaluate(patient)
        tab = request.GET.get('tab') or ('q' if not ev['q'].get('filled') else 's1')
        if tab not in ('q', 's1', 's2', 'doc', 'dos'):
            tab = 'q'
        auto_anamnesis = generate_anamnesis(patient, ev)
        files = list(patient.files.select_related('created_by', 'created_by__profile'))
        files_by_cat = []
        for key, label in PreopFile.Category.choices:
            cat_items = [f for f in files if f.category == key]
            if cat_items:
                files_by_cat.append({'key': key, 'label': label, 'files': cat_items})

        from ..blood import BLOOD_GROUPS as BG_LIST
        self_token = make_self_fill_token(patient)
        board_patient = resolve_board_patient(patient)
        default_board_unit = board_patient.unit if board_patient else (BoardPatient.ICU if ev['needs_icu_bed'] else BoardPatient.WARD)
        return render(request, self.template_name, {
            'p': patient,
            'ev': ev,
            'q': ev['q'],
            's': ev['s'],
            'tab': tab,
            'auto_anamnesis': auto_anamnesis,
            'shown_anamnesis': patient.anamnesis or auto_anamnesis,
            'self_fill_path': reverse('preop-self-fill', args=[self_token]),
            'self_fill_revoked': bool(ev['s'].get('self_fill_revoked')),
            'self_fill_used': bool(ev['q'].get('self_fill_submitted')),
            'consent_url': consent_url_for_preop(patient),
            'board_patient': board_patient,
            'default_board_unit': default_board_unit,
            'chips': CHIP_CATALOGS,
            'cfs_choices': CFS_CHOICES,
            'anesth_plan_choices': ANESTH_PLAN_CHOICES,
            'asa_choices': ASA_CHOICES,
            'mallampati_choices': MALLAMPATI_CHOICES,
            'stop_bang_choices': STOP_BANG_CHOICES,
            'surg_duration_choices': SURG_DURATION_CHOICES,
            'cied_plan_choices': CIED_PLAN_CHOICES,
            'case_outcome_choices': CASE_OUTCOME_CHOICES,
            'vqi_30d_choices': VQI_30D_CHOICES,
            'aaa_morphology_choices': AAA_MORPHOLOGY_CHOICES,
            'aaa_neck_choices': AAA_NECK_CHOICES,
            'popliteal_screen_choices': POPLITEAL_SCREEN_CHOICES,
            'post_antithrombotic_choices': POST_ANTITHROMBOTIC_CHOICES,
            'mets_limit_choices': METS_LIMIT_CHOICES,
            'duration_chips': DURATION_CHIPS,
            'ceap_choices': CEAP_CHOICES,
            'doctors': doctors(),
            'op_subtype_groups': OP_SUBTYPE_GROUPS,
            'side_choices': Case.Side.choices,
            'someday_choices': PreopPatient.SomedayList.choices[1:],
            'cardio_decisions': PreopPatient.CardioDecision.choices,
            'cardiologists': _cardiologist_choices(),
            'file_categories': PreopFile.Category.choices,
            'files_by_cat': files_by_cat,
            'total_files': len(files),
            'blood_groups': BG_LIST,
            'today': timezone.localdate(),
        })


def resolve_board_patient(patient):
    """Return active BoardPatient linked to this PreopPatient (never hijacking another PreopPatient's Board card)."""
    s = merged_studies(patient)
    bpid = s.get('board_patient_id')
    if bpid:
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
            if isinstance(st, dict) and st.get('board_patient_id')
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


def _admit_preop_to_board_locked(patient, user, unit='', bed=''):
    ev = evaluate(patient)
    q, s = ev['q'], ev['s']
    pathway = ev['pathway']
    now = timezone.now()

    target_unit = (
        unit if unit in (BoardPatient.ICU, BoardPatient.WARD)
        else (BoardPatient.ICU if ev['needs_icu_bed'] else BoardPatient.WARD)
    )
    bed_clean = clean_text(bed, 20).replace('\n', ' ')
    diag_str = clean_text(patient.planned_operation_label, 255).replace('\n', ' ')
    attending = patient.surgeon or user

    bp = resolve_board_patient(patient)
    was_created = False
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
        was_created = True
    else:
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

    # Structured initial BoardEntry.STATUS summary
    anesth_line_sfx = f" ({ev['anesth_lines_txt']})" if ev.get('anesth_lines_txt') else ''
    op_verb = (
        f"ჩატარდა ({ev['operated_date_ka']})" if (ev.get('case_outcome') == 'operated' and ev.get('operated_date_ka'))
        else ('ჩატარდა' if ev.get('case_outcome') == 'operated' else 'დაგეგმილია')
    )
    status_lines = [
        f"[წინასაოპერაციო ჰენდოფი] {op_verb}: {diag_str} · {ev['effective_asa']} · {ev['effective_anesth_label']}{anesth_line_sfx}."
    ]
    vitals_labs = []
    if s.get('bp_sys') and s.get('bp_dia'):
        vitals_labs.append(f"BP {s['bp_sys']}/{s['bp_dia']} mmHg")
    if ev['hb_gl'] is not None:
        vitals_labs.append(f"Hb {ev['hb_gl']} გ/ლ")
    if s.get('plt') is not None:
        vitals_labs.append(f"PLT {s['plt']}")
    if s.get('inr') is not None:
        vitals_labs.append(f"INR {s['inr']}")
    if ev['egfr'] is not None:
        crcl_part = f" / CrCl {ev['crcl']}" if ev.get('crcl') is not None else ''
        vitals_labs.append(f"eGFR {ev['egfr']}{crcl_part}")
    if s.get('blood_group'):
        vitals_labs.append(f"ჯგუფი: {s['blood_group']}")
    if ev.get('min_abi') is not None:
        vitals_labs.append(f"ABI {ev['min_abi']}")
    if ev.get('min_tp') is not None:
        vitals_labs.append(f"TP {ev['min_tp']} mmHg")
    if s.get('lvef') is not None:
        vitals_labs.append(f"LVEF {s['lvef']}%")
    if ev.get('nt_probnp') is not None:
        vitals_labs.append(f"NT-proBNP {ev['nt_probnp']} pg/mL")
    if ev.get('hs_ctn') is not None:
        vitals_labs.append(f"hs-cTn {ev['hs_ctn']} ng/L")
    if (s.get('ecg') or 'pending') != 'pending':
        vitals_labs.append(f"ეკგ: {ev['ecg_label']}")
    if vitals_labs:
        status_lines.append('მონაცემები: ' + ', '.join(vitals_labs) + '.')

    safety_bits = []
    allergy_chips, eff_iod_board, _ = _effective_allergy_state(q)
    eff_renal_board = min([v for v in (ev.get('crcl'), ev.get('egfr')) if v is not None], default=None)
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
    if ev.get('bp_arm_diff') is not None and ev['bp_arm_diff'] >= 20:
        higher_arm = 'მარჯვენა' if (s.get('bp_sys') or 0) >= (s.get('bp_sys_l') or 0) else 'მარცხენა'
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

    status_text = clean_text('\n'.join(status_lines), 4000)
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

    # Protocol-driven duty doctor TASKs (idempotent & prefix-updatable on re-handoff)
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
        task_candidates.append((
            'ვენური პოსტოპ. რეჟიმი:',
            'ვენური პოსტოპ. რეჟიმი: ადრეული აქტიური სიარული და ელასტიური კომპრესია პროტოკოლით',
        ))

    if 'hit_heparin' in allergy_chips:
        hit_board_agents = (
            'ბივალირუდინი / არგატრობანი (ფონდაპარინუქსი CrCl<30/დიალიზზე აკრძალულია!)'
            if (q.get('on_dialysis') or (eff_renal_board is not None and eff_renal_board < 30))
            else 'ფონდაპარინუქსი / არგატრობანი / ბივალირუდინი'
        )
        task_candidates.append((
            'HIT ანამნეზში!',
            f'HIT ანამნეზში! არაფრაქციული ჰეპარინი და LMWH აკრძალულია — არაჰეპარინული ანტიკოაგულაცია ({hit_board_agents})',
        ))
    elif ev.get('has_full_oac'):
        task_candidates.append((
            'VTE / ანტიკოაგულაცია:',
            'VTE / ანტიკოაგულაცია: პაციენტი მუდმივ ანტიკოაგულანტზეა (OAC) — სრული ჰემოსტაზის შემდეგ განახლდეს OAC სქემა ქირურგთან შეთანხმებით (ორმაგი ანტიკოაგულაციის გარეშე!)',
        ))
    elif ev['caprini_score'] >= 3 or not ev['is_venous']:
        vte_board_drug = (
            'კანქვეშა UFH 5000 სე ×2–3 ან თირკმელზე კორექტირებული LMWH'
            if (q.get('on_dialysis') or (eff_renal_board is not None and eff_renal_board < 30))
            else 'LMWH პროფილაქტიკური დოზით'
        )
        task_candidates.append((
            'VTE პროფილაქტიკა',
            f"VTE პროფილაქტიკა (Caprini {ev['caprini_score']} ქ. — {ev['caprini_tier']}): ადრეული მობილიზაცია + {vte_board_drug} ჰემოსტაზის შემდეგ",
        ))

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
    elif ev.get('max_contrast_ml') is not None or (eff_renal_board is not None and eff_renal_board < 60):
        contrast_sfx = f" (მაქს. კონტრასტი ≤ {ev['max_contrast_ml']} მლ)" if ev.get('max_contrast_ml') is not None else ''
        task_candidates.append((
            'ნეფროპროტექცია:',
            f'ნეფროპროტექცია: იზოტონური ჰიდრატაცია + საკონტროლო კრეატინინი და K+ 24–48 სთ-ში{contrast_sfx}',
        ))

    if ev.get('needs_ppi') or q.get('ulcer'):
        task_candidates.append((
            'გასტროპროტექცია:',
            'გასტროპროტექცია: ომეპრაზოლი / პანტოპრაზოლი (PPI) ანტითრომბოზული თერაპიის ფონზე (ESC 2022 Class I)',
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


class PreopPassportView(LoginRequiredMixin, generic.TemplateView):
    """1-Page A4 Printable Preoperative Passport ('წინასაოპერაციო პასპორტი') for the patient chart."""
    template_name = 'mdf/preop/passport.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        patient = get_object_or_404(
            PreopPatient.objects.select_related('surgeon', 'surgeon__profile', 'approved_by'),
            pk=kwargs['pk'],
        )
        ev = evaluate(patient)
        auto_anam = generate_anamnesis(patient, ev)
        ctx.update({
            'p': patient,
            'ev': ev,
            'q': ev['q'],
            's': ev['s'],
            'shown_anamnesis': patient.anamnesis or auto_anam,
            'surgeon_name': person_name(patient.surgeon),
            'approved_by_name': person_name(patient.approved_by) if patient.approved_by else '',
            'printed_on': timezone.localtime().strftime('%d.%m.%Y %H:%M'),
        })
        return ctx


class PreopSelfFillView(generic.View):
    """Signed, one-time, revocable Patient Self-Questionnaire link with PII masking & strict privacy headers."""
    template_name = 'mdf/preop/self_fill.html'

    def dispatch(self, request, *args, **kwargs):
        resp = super().dispatch(request, *args, **kwargs)
        resp['Referrer-Policy'] = 'no-referrer'
        resp['X-Robots-Tag'] = 'noindex, nofollow, noarchive'
        resp['Cache-Control'] = 'no-store, private'
        return resp

    def _load_patient(self, token):
        signer = TimestampSigner(salt=SELF_FILL_SALT)
        try:
            raw = signer.unsign(token, max_age=SELF_FILL_MAX_AGE)
            pk_str, _, tok_nonce = raw.partition(':')
            pk = int(pk_str)
        except (BadSignature, SignatureExpired, ValueError):
            return None
        patient = PreopPatient.objects.filter(pk=pk, archived=False).first()
        if not patient:
            return None
        s = merged_studies(patient)
        if s.get('self_fill_revoked'):
            return None
        cur_nonce = str(s.get('self_fill_nonce') or '').strip()
        if tok_nonce != cur_nonce:
            return None
        return patient

    def get(self, request, token):
        patient = self._load_patient(token)
        if not patient:
            return render(request, self.template_name, {'invalid_link': True}, status=404)
        ev = evaluate(patient)
        staff_locked = bool(patient.approved_at or ev['q'].get('nurse_verified') or ev['case_outcome'] != 'scheduled')
        submitted_once = bool(ev['q'].get('self_fill_submitted'))
        is_done_screen = (request.GET.get('done') == '1') and submitted_once and not staff_locked
        locked = staff_locked or (submitted_once and not is_done_screen)
        emergency_symptom = bool(ev['q'].get('aaa_pain') or ev['q'].get('cea_crescendo')) if is_done_screen else False
        # L7-F2: Never pre-populate prior clinical answers/meds/allergies on an unauthenticated bearer GET link!
        q_public = dict(DEFAULT_Q)
        s_public = dict(DEFAULT_STUDIES)
        return render(request, self.template_name, {
            'p': patient,
            'ev': ev,
            'q': q_public,
            's': s_public,
            'chips': CHIP_CATALOGS,
            'self_med_hints': SELF_FILL_MED_HINTS,
            'mets_limit_choices': METS_LIMIT_CHOICES,
            'duration_chips': DURATION_CHIPS,
            'ceap_choices': CEAP_CHOICES,
            'locked': locked,
            'staff_locked': staff_locked,
            'masked_person_id': mask_person_id(ev['person_id']),
            'masked_phone': mask_phone(patient.phone),
            'has_birth_date': bool(ev['s'].get('birth_date')),
            'emergency_symptom': emergency_symptom,
            'submitted': is_done_screen,
        })

    def post(self, request, token):
        patient = self._load_patient(token)
        if not patient:
            return render(request, self.template_name, {'invalid_link': True}, status=404)
        with _PREOP_MUTATION_LOCK, transaction.atomic():
            _sqlite_acquire_write_lock(patient.pk)
            patient = PreopPatient.objects.select_for_update().filter(pk=patient.pk, archived=False).first()
            if not patient:
                return render(request, self.template_name, {'invalid_link': True}, status=404)
            q_cur = merged_q(patient)
            s_cur = merged_studies(patient)
            if (
                patient.approved_at
                or q_cur.get('nurse_verified')
                or q_cur.get('self_fill_submitted')
                or s_cur.get('self_fill_revoked')
                or (s_cur.get('case_outcome_status') or 'scheduled') != 'scheduled'
            ):
                return HttpResponseRedirect(reverse('preop-self-fill', args=[token]) + '?locked=1')
            uf = ['questionnaire', 'studies', 'side', 'started_on', 'updated_at']
            age_posted = _int(request.POST.get('age'), lo=12, hi=120) if request.POST.get('age') else None
            if age_posted and not patient.age:
                patient.age = age_posted
                uf.append('age')
            if not patient.sex and request.POST.get('sex') in ('M', 'F'):
                patient.sex = request.POST.get('sex')
                uf.append('sex')
            if not patient.phone and 'phone' in request.POST:
                ph = clean_text(request.POST.get('phone'), 50).replace('\n', ' ')
                if ph:
                    patient.phone = ph
                    uf.append('phone')
            apply_self_fill_post(request.POST, patient)
            s_mut = merged_studies(patient)
            pid = _clean_person_id(request.POST.get('person_id'))
            if pid and not s_mut.get('person_id'):
                s_mut['person_id'] = pid
            bd = _date(request.POST.get('birth_date'))
            if bd and bd <= timezone.localdate() and not s_mut.get('birth_date'):
                s_mut['birth_date'] = bd.isoformat()
                if not patient.age:
                    patient.age = _age_from_dob(bd)
                    if 'age' not in uf:
                        uf.append('age')
            patient.studies = s_mut
            patient.save(update_fields=uf)
        return HttpResponseRedirect(reverse('preop-self-fill', args=[token]) + '?done=1')


class PreopMoveView(LoginRequiredMixin, generic.View):
    """Move a patient between dates / Someday lists (drag & drop AJAX or form POST), archive, or delete."""

    def post(self, request, pk):
        patient = get_object_or_404(PreopPatient, pk=pk)
        action = request.POST.get('action') or 'move'
        ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest'

        with _PREOP_MUTATION_LOCK, transaction.atomic():
            _sqlite_acquire_write_lock(patient.pk)
            patient = PreopPatient.objects.select_for_update().get(pk=patient.pk)

            if action == 'archive':
                patient.archived = not patient.archived
                patient.updated_by = request.user
                patient.save(update_fields=['archived', 'updated_at', 'updated_by'])
                if ajax:
                    return JsonResponse({'ok': True, 'archived': patient.archived})
                messages.info(request, f'{patient.patient_name} {"გადავიდა არქივში" if patient.archived else "დაბრუნდა სიაში"}.')
                return HttpResponseRedirect(reverse('preop'))

            if action == 'delete':
                s_del = merged_studies(patient)
                is_audit_protected = (
                    s_del.get('case_outcome_status') == 'operated'
                    or bool(s_del.get('board_patient_id'))
                )
                if is_audit_protected and not (request.user.is_superuser or request.user.is_staff):
                    patient.archived = True
                    patient.updated_by = request.user
                    patient.save(update_fields=['archived', 'updated_at', 'updated_by'])
                    if ajax:
                        return JsonResponse({'ok': True, 'archived': True, 'deleted': False})
                    messages.info(request, f'{patient.patient_name} ნაოპერაციები/ჰოსპიტალიზებულია — სამედიცინო აუდიტის დაცვის მიზნით გადატანილია არქივში.')
                    return HttpResponseRedirect(reverse('preop'))
                name = patient.patient_name
                patient.delete()
                if ajax:
                    return JsonResponse({'ok': True, 'deleted': True})
                messages.success(request, f'{name} წაიშალა წინასაოპერაციო სიიდან.')
                return HttpResponseRedirect(reverse('preop'))

            old_someday = patient.someday_list
            was_unstarted = (patient.started_on is None)
            s_mut = merged_studies(patient)
            slot = (request.POST.get('slot') or '').strip()
            if slot.startswith('someday:'):
                key = slot.split(':', 1)[1]
                if key not in PreopPatient.SomedayList.values or not key:
                    return JsonResponse({'error': 'bad_slot'}, status=400) if ajax else HttpResponseRedirect(reverse('preop'))
                patient.someday_list = key
                patient.visit_date = None
            else:
                vd = _date(slot)
                if not vd:
                    return JsonResponse({'error': 'bad_date'}, status=400) if ajax else HttpResponseRedirect(reverse('preop'))
                patient.visit_date = vd
                patient.someday_list = ''
                if not patient.started_on:
                    patient.started_on = vd

            _update_hold_pause(
                s_mut, old_someday, patient.someday_list,
                started_on=(None if was_unstarted else patient.started_on),
            )
            patient.studies = s_mut
            patient.updated_by = request.user
            patient.save(update_fields=['visit_date', 'someday_list', 'started_on', 'studies', 'updated_at', 'updated_by'])
            if ajax:
                ev = evaluate(patient)
                return JsonResponse({'ok': True, 'days_elapsed': ev['days_elapsed'], 'status': ev['status']})
            return HttpResponseRedirect(reverse('preop'))


class PreopFileUploadView(LoginRequiredMixin, generic.View):
    """Upload one or more photos or PDF reports to a preoperative patient card."""

    def post(self, request, pk):
        patient = get_object_or_404(PreopPatient, pk=pk)
        category = request.POST.get('category') or PreopFile.Category.LABS
        caption = request.POST.get('caption') or ''
        uploaded_on = _date(request.POST.get('uploaded_on'))
        if uploaded_on and uploaded_on > timezone.localdate():
            uploaded_on = None

        files = request.FILES.getlist('file')
        added, errors = 0, []
        for f in files[:20]:
            try:
                save_preop_file(patient, f, category, caption, request.user, uploaded_on)
                added += 1
            except PhotoError as e:
                errors.append(f'{f.name}: {e}')

        for err in errors:
            messages.error(request, err)
        if added:
            messages.success(request, f'დაემატა {added} ფაილი.')
        elif not errors:
            messages.error(request, 'ფაილი არ არის არჩეული.')
        tab = request.POST.get('tab') or 's1'
        return HttpResponseRedirect(reverse('preop-patient', args=[patient.pk]) + f'?tab={tab}#files')


class PreopFileView(LoginRequiredMixin, generic.View):
    """Serve a private preoperative photo or PDF to authenticated clinic staff only."""

    def get(self, request, pk, size='full'):
        rec = get_object_or_404(PreopFile, pk=pk)
        if size == 'thumb' and not rec.is_pdf and rec.thumb:
            field = rec.thumb
            ctype = 'image/jpeg'
            ext = 'jpg'
        else:
            field = rec.file
            ctype = 'application/pdf' if rec.is_pdf else 'image/jpeg'
            ext = 'pdf' if rec.is_pdf else 'jpg'
        try:
            fh = field.open('rb')
        except (FileNotFoundError, ValueError):
            raise Http404
        response = FileResponse(fh, content_type=ctype)
        response['Cache-Control'] = 'private, max-age=604800'
        response['Content-Disposition'] = f'inline; filename="preop-file-{rec.pk}.{ext}"'
        response['Content-Security-Policy'] = "default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; sandbox"
        response['X-Frame-Options'] = 'SAMEORIGIN'
        response['X-Content-Type-Options'] = 'nosniff'
        return response


class PreopFileDeleteView(LoginRequiredMixin, generic.View):

    def post(self, request, pk):
        rec = get_object_or_404(PreopFile, pk=pk)
        patient_id = rec.patient_id
        rec.delete()
        messages.success(request, 'ფაილი წაიშალა.')
        return HttpResponseRedirect(reverse('preop-patient', args=[patient_id]) + '?tab=s1#files')


class PreopAuditView(LoginRequiredMixin, generic.TemplateView):
    """Cardiology Consultation, Theatre Cancellation & 30-Day SVS VQI / NVR Quality Audit."""
    template_name = 'mdf/preop/audit.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        today = timezone.localdate()
        pathway_filter = self.request.GET.get('pathway', 'all')
        if pathway_filter not in ('all', *PreopPatient.Pathway.values):
            pathway_filter = 'all'

        base_qs = PreopPatient.objects.select_related('surgeon', 'surgeon__profile').order_by('-created_at', '-id')
        if pathway_filter != 'all':
            base_qs = base_qs.filter(pathway=pathway_filter)
        qs = list(base_qs[:500])

        # Active still-hospitalized BoardPatient IDs (excluded from VQI discharge medication denominator until discharged!)
        active_inpatient_bp_ids = set(
            BoardPatient.objects.filter(discharged_at__isnull=True).values_list('pk', flat=True)
        )

        groups = {}
        for name in DEFAULT_CARDIOLOGISTS:
            groups[name] = {
                'name': name, 'total': 0, 'cleared': 0,
                'extra': 0, 'non_compliant': 0, 'days_sum': 0, 'over_7': 0,
            }

        evaluated_rows = []
        over_target_total = 0
        non_compliant_total = 0
        cardio_seen_total = 0

        # Stage P2 & P3: Theatre Cancellation & 30-Day SVS VQI / UK NVR Registry KPIs
        operated_total = 0
        cancelled_total = 0
        arterial_operated_total = 0
        vqi_statin_ap_count = 0
        cea_symp_operated_total = 0
        cea_symp_within_14d_count = 0
        vqi_30d_tracked_total = 0
        vqi_uneventful_count = 0
        vqi_complication_total = 0
        cancel_counts = {k: 0 for k, _ in CHIP_CATALOGS['cancel_reason_chips']}
        vqi_rows = []

        for p in qs:
            ev = evaluate(p, today=today)
            p.ev = ev
            p.surgeon_name = person_name(p.surgeon)
            s = ev['s']
            q = ev['q']
            if ev['over_target_days']:
                over_target_total += 1
            if p.cardio_decision:
                cardio_seen_total += 1
                cname = p.cardiologist or 'ლიკა ინჯია'
                g = groups.setdefault(cname, {
                    'name': cname, 'total': 0, 'cleared': 0,
                    'extra': 0, 'non_compliant': 0, 'days_sum': 0, 'over_7': 0,
                })
                g['total'] += 1
                if p.cardio_decision == 'cleared':
                    g['cleared'] += 1
                else:
                    g['extra'] += 1
                if ev['cardio_non_compliant']:
                    g['non_compliant'] += 1
                    non_compliant_total += 1
                if ev['over_target_days']:
                    g['over_7'] += 1
                g['days_sum'] += ev['days_elapsed']
                evaluated_rows.append(p)

            outcome = ev['case_outcome']
            p.cancel_reasons_str = chip_summary(s.get('cancel_reason_chips'), 'cancel_reason_chips')
            p.vqi_complications_str = chip_summary(s.get('vqi_complication_chips'), 'vqi_complication_chips')
            if outcome == 'operated':
                operated_total += 1
                bpid = s.get('board_patient_id')
                still_inpatient = bool(bpid and bpid in active_inpatient_bp_ids and not ev['vqi_bundle_done'])
                in_hosp_mortality = (ev['vqi_30d_status'] == 'mortality_30d')
                if not ev['is_venous'] and not still_inpatient and not in_hosp_mortality:
                    arterial_operated_total += 1
                    if ev['vqi_bundle_done']:
                        vqi_statin_ap_count += 1
                if ev.get('is_symp_cea'):
                    ev_dt = _date(q.get('cea_event_date'))
                    op_done_dt = _date(s.get('operated_date')) or _date(s.get('planned_surgery_date'))
                    symp_wait = (
                        (op_done_dt - ev_dt).days if (ev_dt and op_done_dt)
                        else ev.get('days_since_ev')
                    )
                    if symp_wait is not None and 0 <= symp_wait <= 180:
                        cea_symp_operated_total += 1
                        if symp_wait <= 14:
                            cea_symp_within_14d_count += 1
                if ev['vqi_30d_status'] != 'pending':
                    vqi_30d_tracked_total += 1
                    if ev['vqi_30d_status'] == 'uneventful':
                        vqi_uneventful_count += 1
                    else:
                        vqi_complication_total += 1
                vqi_rows.append(p)
            elif outcome.startswith('cancelled_'):
                cancelled_total += 1
                for ck in (s.get('cancel_reason_chips') or []):
                    if ck in cancel_counts:
                        cancel_counts[ck] += 1
                vqi_rows.append(p)

        table_rows = []
        for g in groups.values():
            g['extra_pct'] = round(g['extra'] * 100 / g['total']) if g['total'] else 0
            g['avg_days'] = round(g['days_sum'] / g['total'], 1) if g['total'] else None
            table_rows.append(g)

        theatre_decided_total = operated_total + cancelled_total
        cancel_rate_pct = round(cancelled_total * 100 / theatre_decided_total, 1) if theatre_decided_total else 0
        vqi_statin_ap_pct = round(vqi_statin_ap_count * 100 / arterial_operated_total) if arterial_operated_total else None
        cea_symp_within_14d_pct = round(cea_symp_within_14d_count * 100 / cea_symp_operated_total) if cea_symp_operated_total else None
        vqi_uneventful_30d_pct = round(vqi_uneventful_count * 100 / vqi_30d_tracked_total) if vqi_30d_tracked_total else None
        cancel_reason_rows = [
            {'key': ck, 'label': clbl, 'count': cancel_counts[ck]}
            for ck, clbl in CHIP_CATALOGS['cancel_reason_chips']
            if cancel_counts[ck] > 0
        ]

        ctx.update({
            'pathway_filter': pathway_filter,
            'pathway_pills': [
                ('all', 'ყველა'),
                ('aaa_infra', 'AAA'),
                ('cea', 'CEA'),
                ('abf', 'ABF'),
                ('pad_bypass', 'პერიფ. შუნტირება'),
                ('pad_pta', 'PTA / სტენტი'),
                ('venous', 'ვენური'),
            ],
            'total_patients': len(qs),
            'total_aaa': len(qs),
            'cardio_seen_total': cardio_seen_total,
            'non_compliant_total': non_compliant_total,
            'over_7_total': over_target_total,
            'table_rows': table_rows,
            'patient_rows': evaluated_rows[:150],
            'target_days': TARGET_PREP_DAYS,
            'operated_total': operated_total,
            'cancelled_total': cancelled_total,
            'cancel_rate_pct': cancel_rate_pct,
            'cancel_reason_rows': cancel_reason_rows,
            'arterial_operated_total': arterial_operated_total,
            'vqi_statin_ap_count': vqi_statin_ap_count,
            'vqi_statin_ap_pct': vqi_statin_ap_pct,
            'cea_symp_operated_total': cea_symp_operated_total,
            'cea_symp_within_14d_count': cea_symp_within_14d_count,
            'cea_symp_within_14d_pct': cea_symp_within_14d_pct,
            'vqi_30d_tracked_total': vqi_30d_tracked_total,
            'vqi_uneventful_count': vqi_uneventful_count,
            'vqi_complication_total': vqi_complication_total,
            'vqi_uneventful_30d_pct': vqi_uneventful_30d_pct,
            'vqi_rows': vqi_rows[:150],
        })
        return ctx

