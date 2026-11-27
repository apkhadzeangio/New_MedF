"""Evaluation context object and initial baseline/hemodynamic setup for preop engine."""
from types import SimpleNamespace

from django.utils import timezone

from ...models import PreopPatient
from ..calculators import (
    _parse_scr_mgdl,
    _s_raw,
    _sat_age,
    _sat_bmi_from,
    _sat_crcl,
    _sat_egfr,
    _sat_gl_mmol,
    _sat_hb_gl,
    _sat_num,
    calc_bmi,
    calc_caprini,
    calc_crcl,
    calc_egfr,
    is_creat_unit_mismatch,
    normalize_albumin_gl,
    normalize_glucose_mmol,
    normalize_hb_gl,
    normalize_ldl_mmol,
)
from ..constants import CASE_OUTCOME_CHOICES
from ..meds import _detect_freetext_high_risk_meds, _effective_allergy_state
from ..registry import rule_value
from ..utils import (
    _date,
    _int,
    _num,
    _resolve_pathway,
    cta_live_months_age,
    merged_q,
    merged_studies,
    prep_days_elapsed,
    target_prep_days,
)

_IMPLAUSIBLE_SPEC = (
    ('bp_sys', 'SBP', 30, 320), ('bp_dia', 'DBP', 20, 220),
    ('bp_sys_l', 'SBP (L)', 30, 320), ('bp_dia_l', 'DBP (L)', 20, 220),
    ('hr', 'HR', 15, 260), ('spo2', 'SpO2', 30, 100),
    ('weight_kg', 'წონა', 25, 260), ('height_cm', 'სიმაღლე', 100, 230),
    ('hb', 'Hb', 1, 260), ('inr', 'INR', 0.4, 20.0),
    ('k', 'K+', 1.0, 12.0), ('na', 'Na+', 95, 190),
    ('glucose', 'გლუკოზა', 0.5, 1000), ('hba1c', 'HbA1c', 3.0, 25.0),
    ('albumin', 'ალბუმინი', 0.5, 80), ('ldl', 'LDL', 0.2, 500),
    ('ferritin', 'ფერიტინი', 1, 5000), ('tsat', 'TSAT', 1, 100),
    ('lvef', 'LVEF', 5, 95), ('nt_probnp', 'NT-proBNP', 1, 100000),
    ('hs_ctn', 'hs-cTn', 0.1, 50000), ('aaa_mm', 'AAA მმ', 10, 200),
    ('cta_months', 'CTA თვე', 0, 240), ('carotid_r', 'ICA R', 0, 100),
    ('carotid_l', 'ICA L', 0, 100), ('abi_r', 'ABI R', 0.0, 3.0),
    ('abi_l', 'ABI L', 0.0, 3.0), ('tp_r', 'TP R', 0, 400),
    ('tp_l', 'TP L', 0, 400), ('tbi_r', 'TBI R', 0.0, 3.0),
    ('tbi_l', 'TBI L', 0.0, 3.0), ('urine_wbc', 'შარდის WBC', 0, 2000),
    ('spiro_fev1', 'FEV1', 10, 160), ('ven_gsv_mm', 'GSV მმ', 1, 50),
    ('pad_gsv_mm', 'PAD GSV მმ', 0.5, 30), ('dos_bp_sys', 'DoS SBP', 40, 300),
    ('dos_bp_dia', 'DoS DBP', 20, 200), ('dos_glucose', 'DoS გლუკოზა', 0.5, 1000),
    ('dos_inr', 'DoS INR', 0.5, 15.0),
)


def _has_raw(val):
    return val is not None and (isinstance(val, bool) or str(val).strip() != '')


def _fmt_bad_val(lbl, raw):
    import math
    txt = str(raw).strip()
    if (isinstance(raw, float) and not math.isfinite(raw)) or txt.lower() in (
        'nan', '+nan', '-nan', 'inf', '+inf', '-inf', 'infinity', '+infinity', '-infinity',
    ):
        return f'{lbl} (არასწორი რიცხვი)'
    return f'{lbl}={txt[:24]}'


def _detect_implausible_fields(raw_s, s, creat_unit, creat_unit_ambiguous, today=None, q=None):
    from ..utils import is_valid_birth_date
    src = raw_s if isinstance(raw_s, dict) else {}
    inv = src.get('_invalid_inputs') if isinstance(src.get('_invalid_inputs'), dict) else {}
    q_d = q if isinstance(q, dict) else {}
    bad = []
    seen = set()
    for key, lbl, lo, hi in _IMPLAUSIBLE_SPEC:
        raw = src[key] if key in src else s.get(key)
        if _has_raw(raw) and _num(raw, lo=lo, hi=hi) is None:
            bad.append(_fmt_bad_val(lbl, raw))
            seen.add(key)
        elif key in inv and _has_raw(inv[key]):
            bad.append(_fmt_bad_val(lbl, inv[key]))
            seen.add(key)
    for key, lbl, check_fn in (
        ('creat', 'კრეატინინი', None),
        ('fibrinogen', 'ფიბრინოგენი', None),
        ('wbc', 'WBC', lambda v: _num(v, lo=0.1, hi=150000) is not None),
        ('plt', 'PLT', lambda v: (pv := _num(v, lo=1, hi=2000000)) is not None and not (2500 < pv < 3000)),
    ):
        raw = src[key] if key in src else s.get(key)
        if key == 'creat' and _has_raw(raw):
            scr_mg, ambig = _parse_scr_mgdl(raw, creat_unit)
            if scr_mg is None and (not creat_unit_ambiguous or creat_unit in ('umol', 'mgdl')):
                bad.append(_fmt_bad_val(lbl, raw))
                seen.add(key)
            elif ambig and creat_unit in ('umol', 'mgdl'):
                bad.append(f'{_fmt_bad_val(lbl, raw)} ({creat_unit})')
                seen.add(key)
        elif key == 'fibrinogen' and _has_raw(raw):
            from ..calculators import normalize_fibrinogen_gl
            if normalize_fibrinogen_gl(raw) is None:
                bad.append(_fmt_bad_val(lbl, raw))
                seen.add(key)
        elif check_fn and _has_raw(raw) and not check_fn(raw):
            bad.append(_fmt_bad_val(lbl, raw))
            seen.add(key)
        elif key not in seen and key in inv and _has_raw(inv[key]):
            bad.append(_fmt_bad_val(lbl, inv[key]))
            seen.add(key)
    for dkey, dlbl, raw_val in (
        ('cta_date', 'CTA თარიღი', src['cta_date'] if 'cta_date' in src else s.get('cta_date')),
        ('cea_event_date', 'CEA ეპიზოდის თარიღი', q_d.get('cea_event_date')),
        ('birth_date', 'დაბადების თარიღი', src['birth_date'] if 'birth_date' in src else s.get('birth_date')),
    ):
        if _has_raw(raw_val):
            dt_v = _date(raw_val)
            bad_dt = (not is_valid_birth_date(dt_v, today=today)) if dkey == 'birth_date' else (dt_v is None or (today and dt_v > today))
            if bad_dt:
                bad.append(_fmt_bad_val(dlbl, raw_val))
                seen.add(dkey)
        elif dkey in inv and _has_raw(inv[dkey]):
            bad.append(_fmt_bad_val(dlbl, inv[dkey]))
            seen.add(dkey)
    extra_inv_labels = {
        'planned_surgery_date': 'ოპერაციის თარიღი',
        'labs_date': 'ანალიზების თარიღი',
        'age': 'ასაკი',
        'abf_claud_m': 'კოჭლობის მანძილი',
    }
    for ik, iv in inv.items():
        if ik not in seen and _has_raw(iv):
            bad.append(_fmt_bad_val(extra_inv_labels.get(ik, str(ik)), iv))
            seen.add(ik)
    for sk, dk, lbl_bp in (
        ('bp_sys', 'bp_dia', 'წნევა (R)'),
        ('bp_sys_l', 'bp_dia_l', 'წნევა (L)'),
        ('dos_bp_sys', 'dos_bp_dia', 'დილის წნევა'),
    ):
        sv = _num(src[sk] if (sk in src and _has_raw(src[sk])) else (inv[sk] if (sk in inv and _has_raw(inv[sk])) else s.get(sk)))
        dv = _num(src[dk] if (dk in src and _has_raw(src[dk])) else (inv[dk] if (dk in inv and _has_raw(inv[dk])) else s.get(dk)))
        if sv is not None and dv is not None and sv <= dv:
            bad.append(f'{lbl_bp} ინვერტირებულია ({int(sv)}/{int(dv)})')
    return bad


class EvalContext(SimpleNamespace):
    """Mutable per-patient evaluation state passed sequentially through ordered rule modules."""


def _init_limb_hemodynamics(ctx):
    from ..calculators import _sat_num
    from ..registry import rule_value
    patient, pathway, s, q = ctx.patient, ctx.pathway, ctx.s, ctx.q
    art_cfg = rule_value('VEN_ARTERIAL_ISCHEMIA_COMPRESSION')
    abi_noncomp_thresh = art_cfg['abi_noncomp']
    abi_r = _num(s.get('abi_r'), lo=0.0, hi=3.0)
    abi_l = _num(s.get('abi_l'), lo=0.0, hi=3.0)
    tp_r = _int(s.get('tp_r'), lo=0, hi=400)
    tp_l = _int(s.get('tp_l'), lo=0, hi=400)
    tbi_r = _num(s.get('tbi_r'), lo=0.0, hi=3.0)
    tbi_l = _num(s.get('tbi_l'), lo=0.0, hi=3.0)
    abi_r_s = abi_r if abi_r is not None else _sat_num(_s_raw(s, 'abi_r'), 0.0, 3.0)
    abi_l_s = abi_l if abi_l is not None else _sat_num(_s_raw(s, 'abi_l'), 0.0, 3.0)
    tp_r_s = tp_r if tp_r is not None else _sat_num(_s_raw(s, 'tp_r'), 0, 400)
    tp_l_s = tp_l if tp_l is not None else _sat_num(_s_raw(s, 'tp_l'), 0, 400)
    tbi_r_s = tbi_r if tbi_r is not None else _sat_num(_s_raw(s, 'tbi_r'), 0.0, 3.0)
    tbi_l_s = tbi_l if tbi_l is not None else _sat_num(_s_raw(s, 'tbi_l'), 0.0, 3.0)

    side_s = patient.side if isinstance(patient.side, str) else ''
    unilateral_limb_pw = pathway in (PreopPatient.Pathway.PAD_BYPASS, PreopPatient.Pathway.PAD_PTA, PreopPatient.Pathway.VENOUS)
    unilateral_specified = unilateral_limb_pw and side_s in ('R', 'L')
    if unilateral_limb_pw and side_s == 'R':
        scope_legs, contra_abi, contra_tp = ('R',), abi_l, tp_l
    elif unilateral_limb_pw and side_s == 'L':
        scope_legs, contra_abi, contra_tp = ('L',), abi_r, tp_r
    else:
        scope_legs, contra_abi, contra_tp = ('R', 'L'), None, None

    leg_vals = {
        'R': {'abi': abi_r, 'tp': tp_r, 'tbi': tbi_r, 'abi_s': abi_r_s, 'tp_s': tp_r_s, 'tbi_s': tbi_r_s},
        'L': {'abi': abi_l, 'tp': tp_l, 'tbi': tbi_l, 'abi_s': abi_l_s, 'tp_s': tp_l_s, 'tbi_s': tbi_l_s},
    }
    scoped_abis = [leg_vals[lg]['abi'] for lg in scope_legs if leg_vals[lg]['abi'] is not None]
    scoped_tps = [leg_vals[lg]['tp'] for lg in scope_legs if leg_vals[lg]['tp'] is not None]
    scoped_tbis = [leg_vals[lg]['tbi'] for lg in scope_legs if leg_vals[lg]['tbi'] is not None]
    all_abis = [v for v in (abi_r, abi_l) if v is not None]
    all_tps = [v for v in (tp_r, tp_l) if v is not None]
    all_tbis = [v for v in (tbi_r, tbi_l) if v is not None]
    eff_abis = scoped_abis if unilateral_specified else all_abis
    eff_tps = scoped_tps if unilateral_specified else all_tps
    eff_tbis = scoped_tbis if unilateral_specified else all_tbis
    eff_abis_s = [leg_vals[lg]['abi_s'] for lg in (scope_legs if unilateral_specified else ('R', 'L')) if leg_vals[lg]['abi_s'] is not None]
    eff_tps_s = [leg_vals[lg]['tp_s'] for lg in (scope_legs if unilateral_specified else ('R', 'L')) if leg_vals[lg]['tp_s'] is not None]
    eff_tbis_s = [leg_vals[lg]['tbi_s'] for lg in (scope_legs if unilateral_specified else ('R', 'L')) if leg_vals[lg]['tbi_s'] is not None]

    min_abi, max_abi = min(eff_abis, default=None), max(eff_abis, default=None)
    max_abi_s = max(eff_abis_s, default=None)
    min_comp_abi = min([v for v in eff_abis if v <= abi_noncomp_thresh], default=None)
    min_comp_abi_s = min([v for v in eff_abis_s if v <= abi_noncomp_thresh], default=None)
    min_tp, min_tbi = min(eff_tps, default=None), min(eff_tbis, default=None)
    min_tp_s, min_tbi_s = min(eff_tps_s, default=None), min(eff_tbis_s, default=None)

    nc_legs = [lg for lg in scope_legs if leg_vals[lg]['abi_s'] is not None and leg_vals[lg]['abi_s'] > abi_noncomp_thresh]
    if not nc_legs and not unilateral_specified and not scoped_abis:
        nc_legs = [lg for lg in ('R', 'L') if leg_vals[lg]['abi_s'] is not None and leg_vals[lg]['abi_s'] > abi_noncomp_thresh]
    abi_non_compressible = bool(nc_legs)
    if nc_legs:
        tbi_measured = all((leg_vals[lg]['tp'] is not None or leg_vals[lg]['tbi'] is not None) for lg in nc_legs)
        tbi_comp_safe = all(
            ((leg_vals[lg]['tp'] is not None and leg_vals[lg]['tp'] >= art_cfg['tp_comp_ok'])
             or (leg_vals[lg]['tbi'] is not None and leg_vals[lg]['tbi'] >= art_cfg['tbi_comp_ok']))
            for lg in nc_legs
        )
    else:
        tbi_measured = bool(scoped_tps or scoped_tbis) if unilateral_specified else bool(all_tps or all_tbis)
        tbi_comp_safe = True

    compression_contra = bool(
        (min_comp_abi_s is not None and min_comp_abi_s < art_cfg['abi_red'])
        or (min_tp_s is not None and min_tp_s < art_cfg['tp_red'])
        or (min_tbi_s is not None and min_tbi_s < art_cfg['tbi_red'])
    )
    tbi_reduced_comp = bool(abi_non_compressible and tbi_measured and not compression_contra and not tbi_comp_safe)
    cap_renal = None if ctx.creat_unit_mismatch else ctx.eff_renal_sat
    caprini_score, caprini_tier, caprini_advice = calc_caprini(
        patient, q, ctx.bmi_sat, today=ctx.today, eff_renal=cap_renal,
        liver_status=s.get('liver_status') or 'normal',
        compression_contra=compression_contra,
    )

    ctx.abi_r, ctx.abi_l, ctx.tp_r, ctx.tp_l, ctx.tbi_r, ctx.tbi_l = abi_r, abi_l, tp_r, tp_l, tbi_r, tbi_l
    ctx.contra_abi, ctx.contra_tp = contra_abi, contra_tp
    ctx.scoped_abis, ctx.scoped_tps, ctx.scoped_tbis = scoped_abis, scoped_tps, scoped_tbis
    ctx.min_abi, ctx.max_abi, ctx.max_abi_sat, ctx.min_comp_abi = min_abi, max_abi, max_abi_s, min_comp_abi
    ctx.min_comp_abi_sat, ctx.min_tp_sat, ctx.min_tbi_sat = min_comp_abi_s, min_tp_s, min_tbi_s
    ctx.min_tp, ctx.min_tbi = min_tp, min_tbi
    ctx.nc_legs, ctx.abi_non_compressible = nc_legs, abi_non_compressible
    ctx.tbi_measured, ctx.tbi_comp_safe = tbi_measured, tbi_comp_safe
    ctx.compression_contra, ctx.tbi_reduced_comp = compression_contra, tbi_reduced_comp
    ctx.caprini_score, ctx.caprini_tier, ctx.caprini_advice = caprini_score, caprini_tier, caprini_advice


def _init_base_flags_and_pre_reflex(ctx):
    from ..calculators import _sat_num
    from ..meds_freetext import _has_unrecognized_allergy_clause
    from ..utils import is_symp_cea_patient
    q, s = ctx.q, ctx.s
    ctx.pad_gsv_mm = _num(s.get('pad_gsv_mm'), lo=0.5, hi=30)
    ctx.ldl_val = normalize_ldl_mmol(s.get('ldl'))
    ctx.weight_num = _num(s.get('weight_kg'), lo=25, hi=260)
    ctx.weight_sat = _sat_num(_s_raw(s, 'weight_kg'), 25, 260)
    ctx.is_ali_acute = bool(
        ctx.pathway in (PreopPatient.Pathway.ABF, PreopPatient.Pathway.PAD_BYPASS, PreopPatient.Pathway.PAD_PTA)
        and q.get('abf_duration') == '< 2 კვირა'
    )

    ctx.ipsi_car, ctx.contra_car, ctx.is_symp_cea, ctx.is_clti_any = None, None, False, False
    ctx.days_since_ev = None
    ctx.needs_brain_ct = ctx.needs_neuro = ctx.needs_laryngoscopy = ctx.needs_cea_antiplatelet = False
    ctx.needs_wound_care = ctx.needs_abi_check = ctx.needs_tbi_check = False
    ctx.needs_vte_prophylaxis = ctx.needs_ecg_venous = ctx.needs_alt_vein = False
    ctx.cea_high_surg_risk = ctx.key_badge_urgent = False
    ctx.lidocaine_max_mg, ctx.key_badge = None, ''

    ctx.med_chips = set(q.get('med_chips') or [])
    ctx.cad_chips = set(q.get('cad_chips') or [])
    structured_alg_chips = set(q.get('allergy_chips') or [])
    if q.get('ven_lidocaine_allergy'):
        structured_alg_chips.add('lidocaine')
    ctx.allergy_chips, ctx.eff_allergy_iodine, ctx.ft_allergy_matched = _effective_allergy_state(q)
    ctx.has_vancomycin_allergy = 'vancomycin' in ctx.allergy_chips
    ctx.unconfirmed_ft_allergies = (
        ((ctx.ft_allergy_matched - {'iodine'}) - structured_alg_chips)
        | ({'iodine'} if ('iodine' in ctx.ft_allergy_matched and not q.get('allergy_iodine')) else set())
    )
    ctx.ft_meds = _detect_freetext_high_risk_meds(q.get('other_meds') or '', q=q)
    has_unmatched_allergy_text = _has_unrecognized_allergy_clause(q.get('allergy_text') or '')
    ctx.unclarified_allergy_other = bool(
        (q.get('allergy_other') and not ctx.allergy_chips and not ctx.eff_allergy_iodine)
        or has_unmatched_allergy_text
    )
    ctx.vasc_chips = set(q.get('vasc_chips') or [])
    ctx.anesth_lines_set = set(s.get('anesth_lines_chips') or [])
    ctx.has_statin = ('statin' in ctx.med_chips) or any(
        w in (q.get('other_meds') or '').lower()
        for w in ('სტატინ', 'ატორვ', 'როზუვ', 'კრესტორ', 'ლიპრიმ', 'statin', 'atorvast', 'rosuvast', 'simvast', 'ezetimib')
    )
    ctx.has_safe_aspirin = bool(q.get('med_aspirin') and 'nsaid' not in ctx.allergy_chips)
    ctx.has_safe_clopidogrel = bool(q.get('med_clopidogrel') and 'clopidogrel_intol' not in ctx.allergy_chips)
    has_stroke_hx_ctx = bool(
        q.get('neuro') or q.get('cea_symp_event') or q.get('cea_crescendo')
        or q.get('cea_disabling_stroke') or q.get('cea_thrombolysis')
        or (ctx.pathway == PreopPatient.Pathway.CEA and is_symp_cea_patient(ctx.patient, q=q, s=s, today=ctx.today, require_filled=False))
    )
    ctx.has_safe_prasugrel = bool(q.get('med_prasugrel') and not has_stroke_hx_ctx)
    ctx.has_antiplatelet = bool(
        ctx.has_safe_aspirin or ctx.has_safe_clopidogrel or q.get('med_ticagrelor') or ctx.has_safe_prasugrel
    )
    # Cilostazol is a vasodilator/PDE3 inhibitor, NOT an ESC/ESVS guideline substitute for antiplatelet therapy
    ctx.has_any_antithrombotic = bool(
        ctx.has_antiplatelet or q.get('med_aspirin') or q.get('med_clopidogrel')
        or q.get('med_doac') or q.get('med_dabigatran')
        or q.get('med_warfarin') or ('rivaroxaban_2_5' in ctx.med_chips)
    )

    if s.get('case_wifi_unsalvageable'):
        ctx.red_flags.append(
            'კონფერენციის სტადირებით WIfI Stage 5 (კიდური გადარჩენადი არ არის) — '
            'რევასკულარიზაციის ჩვენება გადაამოწმეთ ქირურგთან!'
        )

    if ctx.abi_non_compressible and not ctx.is_ali_acute:
        ctx.needs_tbi_check = True
        nc_side_lbl = f" ({'/'.join(ctx.nc_legs)})" if ctx.nc_legs else ''
        abi_disp = ctx.max_abi if ctx.max_abi is not None else ctx.max_abi_sat
        ctx.reflex_orders.append({
            'key': 'tbi',
            'trigger': f'არაკომპრესირებადი არტერია{nc_side_lbl} (ABI {abi_disp} > 1.40 — მონკებერგის კალციფიკაცია)',
            'action': 'სამიზნე კიდურზე თითის წნევის (Toe Pressure, mmHg) ან თით-მხრის ინდექსის (TBI) განსაზღვრა რეალური პერფუზიის შესაფასებლად',
            'done': ctx.tbi_measured,
        })
    hemo_cfg = rule_value('PAD_CLTI_HEMO_THRESHOLDS')
    if not ctx.is_venous and (
        (ctx.min_tp_sat is not None and ctx.min_tp_sat < hemo_cfg['tp_severe'])
        or (ctx.min_tbi_sat is not None and ctx.min_tbi_sat < hemo_cfg['tbi_severe'])
    ):
        ctx.amber_flags.append(
            f'კრიტიკულად დაბალი Toe Pressure / TBI (TP {ctx.min_tp if ctx.min_tp is not None else "—"} mmHg / '
            f'TBI {ctx.min_tbi if ctx.min_tbi is not None else "—"}) — ადასტურებს მძიმე იშემიას (CLTI).'
        )


def init_eval_context(patient, today=None):
    today = _date(today) or timezone.localdate()
    pathway = _resolve_pathway(patient)
    q = merged_q(patient)
    s = merged_studies(patient)
    case_outcome = s.get('case_outcome_status') if s.get('case_outcome_status') in dict(CASE_OUTCOME_CHOICES) else 'scheduled'
    on_dialysis = bool(q.get('on_dialysis'))
    creat_unit = s.get('creat_unit') or ''
    raw_creat = _s_raw(s, 'creat')
    raw_weight = _s_raw(s, 'weight_kg')
    _, creat_unit_ambiguous = _parse_scr_mgdl(raw_creat, creat_unit)
    creat_unit_mismatch = (not on_dialysis) and is_creat_unit_mismatch(raw_creat, creat_unit)
    egfr = calc_egfr(raw_creat, patient.age, patient.sex or '', creat_unit, on_dialysis=on_dialysis)
    crcl = calc_crcl(
        raw_creat, patient.age, patient.sex or '', raw_weight,
        creat_unit, on_dialysis=on_dialysis, height_cm=s.get('height_cm'),
    )
    egfr_sat = egfr if egfr is not None else _sat_egfr(raw_creat, patient.age, patient.sex or '', creat_unit, on_dialysis=on_dialysis)
    crcl_sat = crcl if crcl is not None else _sat_crcl(
        raw_creat, patient.age, patient.sex or '', raw_weight,
        creat_unit, on_dialysis=on_dialysis, height_cm=s.get('height_cm'),
    )
    car_r, car_l = _int(s.get('carotid_r'), lo=0, hi=100), _int(s.get('carotid_l'), lo=0, hi=100)
    car_r_sat = _sat_num(_s_raw(s, 'carotid_r'), 0, 100)
    car_l_sat = _sat_num(_s_raw(s, 'carotid_l'), 0, 100)
    car_r_si = int(round(car_r_sat)) if car_r_sat is not None else None
    car_l_si = int(round(car_l_sat)) if car_l_sat is not None else None
    raw_s = patient.studies if isinstance(patient.studies, dict) else {}
    implausible_fields = _detect_implausible_fields(raw_s, s, creat_unit, creat_unit_ambiguous, today=today, q=q)
    bmi_val = calc_bmi(s.get('weight_kg'), s.get('height_cm'))
    bmi_sat = _sat_bmi_from(bmi_val, s)
    age_val = _int(patient.age, lo=1, hi=120)
    age_sat = age_val if age_val is not None else (_sat_age(patient.age) if _num(patient.age) is not None else None)
    aaa_mm = _num(s.get('aaa_mm'), lo=10, hi=200)
    aaa_mm_sat = aaa_mm if aaa_mm is not None else _sat_num(_s_raw(s, 'aaa_mm'), 10, 200)

    ctx = EvalContext(
        patient=patient,
        age=age_val,
        age_sat=age_sat,
        today=today,
        pathway=pathway,
        is_venous=(pathway == PreopPatient.Pathway.VENOUS),
        is_phleb=(patient.op_subtype == PreopPatient.OpSubtype.PHLEB),
        is_pta=(pathway == PreopPatient.Pathway.PAD_PTA),
        is_pad_bypass=(pathway == PreopPatient.Pathway.PAD_BYPASS),
        is_on_hold=(patient.someday_list == PreopPatient.SomedayList.HOLD),
        q=q,
        s=s,
        raw_s=raw_s,
        wbc=None,
        q_filled=bool(q.get('filled')),
        case_outcome=case_outcome,
        is_active_case=(case_outcome == 'scheduled') and not patient.archived,
        days=prep_days_elapsed(patient, today=today),
        t_days=target_prep_days(patient, today=today),
        red_flags=[],
        amber_flags=[],
        reflex_orders=[],
        priority_flags=[],
        hard_contraindications=[],
        implausible_fields=implausible_fields,
        hb_gl=normalize_hb_gl(s.get('hb')),
        hb_gl_sat=_sat_hb_gl(_s_raw(s, 'hb')),
        gl_mmol=normalize_glucose_mmol(s.get('glucose')),
        gl_mmol_sat=_sat_gl_mmol(_s_raw(s, 'glucose')),
        alb_gl=normalize_albumin_gl(s.get('albumin')),
        on_dialysis=on_dialysis,
        creat_unit=creat_unit,
        creat_unit_ambiguous=creat_unit_ambiguous,
        creat_unit_mismatch=creat_unit_mismatch,
        egfr=egfr,
        crcl=crcl,
        egfr_sat=egfr_sat,
        crcl_sat=crcl_sat,
        eff_renal=None if on_dialysis else min([v for v in (egfr, crcl) if v is not None], default=None),
        eff_renal_sat=None if on_dialysis else min([v for v in (egfr_sat, crcl_sat) if v is not None], default=None),
        bmi=bmi_val,
        bmi_sat=bmi_sat,
        liver_3x=(s.get('liver_status') == '3x'),
        aaa_mm=aaa_mm,
        aaa_mm_sat=aaa_mm_sat,
        cta_months=cta_live_months_age(s, today=today),
        car_r=car_r,
        car_l=car_l,
        car_r_sat=car_r_si,
        car_l_sat=car_l_si,
        max_car=max([v for v in (car_r, car_l) if v is not None], default=None),
        max_car_sat=max([v for v in (car_r_si, car_l_si) if v is not None], default=None),
    )
    if implausible_fields:
        ctx.red_flags.append(
            f'შეყვანილია დაუშვებელი / დიაპაზონს გარეთა მნიშვნელობა ({", ".join(implausible_fields[:4])}) — '
            'გადაამოწმეთ ლაბორატორიული ერთეული და მაჩვენებლები!'
        )
    _init_limb_hemodynamics(ctx)
    _init_base_flags_and_pre_reflex(ctx)
    return ctx
