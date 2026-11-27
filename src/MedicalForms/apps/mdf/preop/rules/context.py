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
    is_hb_unit_ambiguous,
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
    has_prior_stroke_or_tia,
    is_pregnancy_eligible,
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
    ('hb', 'Hb', 3.5, 260), ('inr', 'INR', 0.4, 20.0),
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
    if val is None:
        return False
    if isinstance(val, bool):
        return True
    if isinstance(val, int) and abs(val) > 10**18:
        return True
    if isinstance(val, (dict, list, tuple, set)):
        return bool(val)
    return str(val).strip() != ''


def _has_nonfinite(obj):
    import math
    if isinstance(obj, float):
        return not math.isfinite(obj)
    if isinstance(obj, str):
        return obj.strip().lower() in (
            'nan', '+nan', '-nan', 'inf', '+inf', '-inf', 'infinity', '+infinity', '-infinity',
        )
    if isinstance(obj, dict):
        return any(_has_nonfinite(k) or _has_nonfinite(v) for k, v in obj.items())
    if isinstance(obj, (list, tuple, set)):
        return any(_has_nonfinite(x) for x in obj)
    return False


def _fmt_bad_val(lbl, raw):
    from ..utils import _safe_display_str
    lbl_s = _safe_display_str(lbl, max_len=32, default='ველი')
    if _has_nonfinite(raw):
        return f'{lbl_s} (არასწორი რიცხვი)'
    if isinstance(raw, (dict, list, tuple, set)):
        return f'{lbl_s} (არასწორი ფორმატი)'
    if isinstance(raw, int) and not isinstance(raw, bool) and abs(raw) > 10**12:
        return f'{lbl_s} (დიაპაზონს გარეთ)'
    if isinstance(raw, float) and abs(raw) > 1e12:
        return f'{lbl_s} (დიაპაზონს გარეთ)'
    return f'{lbl_s}={str(raw).strip()[:24]}'


def _detect_date_and_bp_implausible(src, s, inv, q_d, today, bad, seen):
    from ..utils import is_valid_birth_date
    raw_cta_dt = src['cta_date'] if 'cta_date' in src else s.get('cta_date')
    raw_op_dt = src['planned_surgery_date'] if 'planned_surgery_date' in src else s.get('planned_surgery_date')
    for dkey, dlbl, raw_val in (
        ('cta_date', 'CTA თარიღი', raw_cta_dt),
        ('cea_event_date', 'CEA ეპიზოდის თარიღი', q_d.get('cea_event_date')),
        ('birth_date', 'დაბადების თარიღი', src['birth_date'] if 'birth_date' in src else s.get('birth_date')),
        ('planned_surgery_date', 'ოპერაციის თარიღი', raw_op_dt),
    ):
        if _has_raw(raw_val):
            dt_v = _date(raw_val)
            if dkey == 'birth_date':
                bad_dt = not is_valid_birth_date(dt_v, today=today)
            elif dkey == 'planned_surgery_date':
                bad_dt = dt_v is None
            else:
                bad_dt = dt_v is None or (today and dt_v > today)
            if bad_dt:
                bad.append(_fmt_bad_val(dlbl, raw_val))
                seen.add(dkey)
        elif dkey in inv and _has_raw(inv[dkey]):
            bad.append(_fmt_bad_val(dlbl, inv[dkey]))
            seen.add(dkey)
    cta_dt_v, op_dt_v = _date(raw_cta_dt), _date(raw_op_dt)
    if cta_dt_v and op_dt_v and cta_dt_v > op_dt_v:
        bad.append(f'CTA თარიღი ({cta_dt_v.isoformat()}) ოპერაციის თარიღზე ({op_dt_v.isoformat()}) გვიანაა')
    raw_cea_days = q_d.get('cea_days_since_event')
    if _has_raw(raw_cea_days) and _int(raw_cea_days, lo=0, hi=3650) is None:
        bad.append(_fmt_bad_val('CEA სიმპტომიდან დღეები', raw_cea_days))
        seen.add('cea_days_since_event')
    extra_inv_labels = {
        'planned_surgery_date': 'ოპერაციის თარიღი',
        'labs_date': 'ანალიზების თარიღი',
        'age': 'ასაკი',
        'abf_claud_m': 'კოჭლობის მანძილი',
    }
    for ik, iv in inv.items():
        if ik not in seen and _has_raw(iv):
            bad.append(_fmt_bad_val(extra_inv_labels.get(ik, ik), iv))
            seen.add(ik)
    for sk, dk, lbl_bp in (
        ('bp_sys', 'bp_dia', 'წნევა (R)'),
        ('bp_sys_l', 'bp_dia_l', 'წნევა (L)'),
        ('dos_bp_sys', 'dos_bp_dia', 'დილის წნევა'),
    ):
        sv = _num(src[sk] if (sk in src and _has_raw(src[sk])) else (inv[sk] if (sk in inv and _has_raw(inv[sk])) else s.get(sk)))
        dv = _num(src[dk] if (dk in src and _has_raw(src[dk])) else (inv[dk] if (dk in inv and _has_raw(inv[dk])) else s.get(dk)))
        if sv is not None and dv is not None and sv <= dv:
            bp_disp = f'{int(round(sv))}/{int(round(dv))}' if (abs(sv) <= 9999 and abs(dv) <= 9999) else 'დიაპაზონს გარეთ'
            bad.append(f'{lbl_bp} ინვერტირებულია ({bp_disp})')


def _detect_implausible_fields(raw_s, s, creat_unit, creat_unit_ambiguous, today=None, q=None, raw_age=None):
    from ..registry import rule_value
    from ..utils import parse_plt_k, parse_wbc_k
    src = raw_s if isinstance(raw_s, dict) else {}
    inv = src.get('_invalid_inputs') if isinstance(src.get('_invalid_inputs'), dict) else {}
    q_d = q if isinstance(q, dict) else {}
    unit_cfg = rule_value('LAB_UNIT_AUTO_CONVERSION')
    bad, seen = [], set()
    if _has_raw(raw_age) and _int(raw_age, lo=12, hi=120) is None:
        bad.append(_fmt_bad_val('ასაკი', raw_age))
        seen.add('age')
    for key, lbl, lo, hi in _IMPLAUSIBLE_SPEC:
        eff_lo = unit_cfg['hb_gdl_lo'] if key == 'hb' else lo
        raw = src[key] if key in src else s.get(key)
        if _has_raw(raw) and _num(raw, lo=eff_lo, hi=hi) is None:
            bad.append(_fmt_bad_val(lbl, raw))
            seen.add(key)
        elif key in inv and _has_raw(inv[key]):
            bad.append(_fmt_bad_val(lbl, inv[key]))
            seen.add(key)
    for key, lbl, check_fn in (
        ('creat', 'კრეატინინი', None),
        ('fibrinogen', 'ფიბრინოგენი', None),
        ('wbc', 'WBC', lambda v: parse_wbc_k(v) is not None),
        ('plt', 'PLT', lambda v: parse_plt_k(v) is not None),
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
    _detect_date_and_bp_implausible(src, s, inv, q_d, today, bad, seen)
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
    tp_r = _num(s.get('tp_r'), lo=0, hi=400)
    tp_l = _num(s.get('tp_l'), lo=0, hi=400)
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
        scope_legs, contra_abi, contra_tp, contra_tbi = ('R',), abi_l, tp_l, tbi_l
    elif unilateral_limb_pw and side_s == 'L':
        scope_legs, contra_abi, contra_tp, contra_tbi = ('L',), abi_r, tp_r, tbi_r
    else:
        scope_legs, contra_abi, contra_tp, contra_tbi = ('R', 'L'), None, None, None

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
        tbi_comp_safe = tbi_measured and all(
            (leg_vals[lg]['tp'] is None or leg_vals[lg]['tp'] >= art_cfg['tp_comp_ok'])
            and (leg_vals[lg]['tbi'] is None or leg_vals[lg]['tbi'] >= art_cfg['tbi_comp_ok'])
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
    any_border_tp_tbi = bool(
        (min_tp_s is not None and art_cfg['tp_red'] <= min_tp_s < art_cfg['tp_comp_ok'])
        or (min_tbi_s is not None and art_cfg['tbi_red'] <= min_tbi_s < art_cfg['tbi_comp_ok'])
    )
    tbi_reduced_comp = bool(
        (not compression_contra)
        and ((abi_non_compressible and tbi_measured and not tbi_comp_safe) or any_border_tp_tbi)
    )
    cap_renal = None if ctx.creat_unit_mismatch else ctx.eff_renal_sat
    caprini_score, caprini_tier, caprini_advice = calc_caprini(
        patient, q, ctx.bmi_sat, today=ctx.today, eff_renal=cap_renal,
        liver_status=s.get('liver_status') or 'normal',
        compression_contra=compression_contra,
    )

    ctx.abi_r, ctx.abi_l, ctx.tp_r, ctx.tp_l, ctx.tbi_r, ctx.tbi_l = abi_r, abi_l, tp_r, tp_l, tbi_r, tbi_l
    ctx.contra_abi, ctx.contra_tp, ctx.contra_tbi = contra_abi, contra_tp, contra_tbi
    ctx.scoped_abis, ctx.scoped_tps, ctx.scoped_tbis = scoped_abis, scoped_tps, scoped_tbis
    ctx.min_abi, ctx.max_abi, ctx.max_abi_sat, ctx.min_comp_abi = min_abi, max_abi, max_abi_s, min_comp_abi
    ctx.min_comp_abi_sat, ctx.min_tp_sat, ctx.min_tbi_sat = min_comp_abi_s, min_tp_s, min_tbi_s
    ctx.min_tp, ctx.min_tbi = min_tp, min_tbi
    ctx.nc_legs, ctx.abi_non_compressible = nc_legs, abi_non_compressible
    ctx.tbi_measured, ctx.tbi_comp_safe = tbi_measured, tbi_comp_safe
    ctx.compression_contra, ctx.tbi_reduced_comp = compression_contra, tbi_reduced_comp
    ctx.caprini_score, ctx.caprini_tier, ctx.caprini_advice = caprini_score, caprini_tier, caprini_advice


def _init_base_flags_and_pre_reflex(ctx):
    from ..calculators import _inverted_bp_arms, _sat_num, _valid_bilateral_sbp
    from ..meds_freetext import _has_unrecognized_allergy_clause
    q, s = ctx.q, ctx.s
    ctx.pad_gsv_mm = _num(s.get('pad_gsv_mm'), lo=0.5, hi=30)
    ctx.ldl_val = normalize_ldl_mmol(s.get('ldl'))
    ctx.weight_num = _num(s.get('weight_kg'), lo=25, hi=260)
    ctx.weight_sat = _sat_num(_s_raw(s, 'weight_kg'), 25, 260)
    lvef_dm = _sat_num(_s_raw(s, 'lvef'), 5, 95)
    lvef_red_dm = rule_value('CARDIO_BIOMARKERS_ECHO')['lvef_red']
    ren_hyd_dm = rule_value('REN_CKD_NEPHRO_HYDRATION')['hydration']
    sglt2_likely_dm = bool(
        q.get('sglt2')
        and 'hf' not in set(q.get('cad_chips') or [])
        and (lvef_dm is None or lvef_dm >= lvef_red_dm)
        and not ctx.on_dialysis
        and (ctx.eff_renal_sat is None or ctx.eff_renal_sat >= ren_hyd_dm)
    )
    ctx.has_known_dm = bool(q.get('dm') or q.get('dm_insulin') or q.get('metformin') or sglt2_likely_dm)
    ctx.sglt2_unlisted_dm = bool(sglt2_likely_dm and not (q.get('dm') or q.get('dm_insulin') or q.get('metformin')))
    ctx.active_gi_bleed = bool(s.get('gastro_result') == 'acute_ulcer' or (q.get('ulcer_active_symp') and s.get('gastro_result') != 'remission'))
    ctx.eff_pregnant = bool(is_pregnancy_eligible(ctx.patient) and (q.get('pregnant_possible') or q.get('ven_pregnant')))
    ctx.is_ali_acute = bool(
        ctx.pathway in (PreopPatient.Pathway.ABF, PreopPatient.Pathway.PAD_BYPASS, PreopPatient.Pathway.PAD_PTA)
        and q.get('abf_duration') == '< 2 კვირა'
    )
    hypo_cfg_c = rule_value('VIT_BP_HYPOTENSION_SHOCK')
    sbp_pre_c = _valid_bilateral_sbp(s)
    if sbp_pre_c is None:
        sbp_pre_c = min((min(sv, dv) for sv, dv in _inverted_bp_arms(s)), default=None)
    hr_pre_c = _sat_num(_s_raw(s, 'hr'), 15, 260)
    si_pre_c = round(hr_pre_c / sbp_pre_c, 2) if (hr_pre_c is not None and sbp_pre_c is not None and sbp_pre_c > 0) else None
    is_rup_pre = bool(
        ctx.pathway == PreopPatient.Pathway.AAA_INFRA and q.get('aaa_pain')
        and ((sbp_pre_c is not None and sbp_pre_c < hypo_cfg_c['red_sbp']) or (si_pre_c is not None and si_pre_c >= hypo_cfg_c['shock_idx']))
    )
    ctx.is_rup_pre = is_rup_pre

    ctx.ipsi_car, ctx.contra_car, ctx.is_symp_cea, ctx.is_clti_any = None, None, False, False
    ctx.days_since_ev = None
    ctx.needs_brain_ct = ctx.needs_neuro = ctx.needs_laryngoscopy = ctx.needs_cea_antiplatelet = False
    ctx.needs_wound_care = ctx.needs_abi_check = ctx.needs_tbi_check = False
    ctx.needs_vte_prophylaxis = ctx.needs_ecg_venous = ctx.needs_alt_vein = False
    ctx.gsv_explicitly_adequate = False
    ctx.cea_high_surg_risk = ctx.key_badge_urgent = False
    ctx.lidocaine_max_mg, ctx.key_badge = None, ''

    ctx.med_chips = set(q.get('med_chips') or [])
    ctx.cad_chips = set(q.get('cad_chips') or [])
    ctx.has_mech_valve = 'mech_valve' in ctx.cad_chips
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
    ctx.has_ft_full_oac = bool(ctx.ft_meds.get('has_ft_full_oac'))
    has_unmatched_allergy_text = _has_unrecognized_allergy_clause(q.get('allergy_text') or '')
    ctx.unclarified_allergy_other = bool(
        (q.get('allergy_other') and not ctx.allergy_chips and not ctx.eff_allergy_iodine)
        or has_unmatched_allergy_text
    )
    ctx.vasc_chips = set(q.get('vasc_chips') or [])
    ctx.anesth_lines_set = set(s.get('anesth_lines_chips') or [])
    from ..meds_freetext import _FT_STATIN_STEMS, _positive_clauses_text

    pos_other_meds = _positive_clauses_text(q.get('other_meds') or '')
    ctx.has_statin = ('statin' in ctx.med_chips) or bool(ctx.ft_meds.get('has_ft_statin')) or any(
        w in pos_other_meds for w in (*_FT_STATIN_STEMS, 'ატორვ', 'როზუვ', 'atorvast', 'rosuvast', 'simvast', 'ezetimib')
    )
    ctx.has_safe_aspirin = bool(q.get('med_aspirin') and 'nsaid' not in ctx.allergy_chips)
    ctx.has_safe_clopidogrel = bool(q.get('med_clopidogrel') and 'clopidogrel_intol' not in ctx.allergy_chips)
    ctx.has_safe_prasugrel = bool(q.get('med_prasugrel') and not has_prior_stroke_or_tia(ctx.patient, q=q, s=s, today=ctx.today))
    ctx.has_antiplatelet = bool(
        ctx.has_safe_aspirin or ctx.has_safe_clopidogrel or q.get('med_ticagrelor') or ctx.has_safe_prasugrel
    )
    # Cilostazol is a vasodilator/PDE3 inhibitor, NOT an ESC/ESVS guideline substitute for antiplatelet therapy
    ctx.has_any_antithrombotic = bool(
        ctx.has_antiplatelet or q.get('med_aspirin') or q.get('med_clopidogrel') or q.get('med_prasugrel')
        or q.get('med_doac') or q.get('med_dabigatran')
        or q.get('med_warfarin') or ('rivaroxaban_2_5' in ctx.med_chips) or ctx.has_ft_full_oac
    )

    _init_pre_reflex_orders(ctx, s, is_rup_pre)


def _init_pre_reflex_orders(ctx, s, is_rup_pre):
    if s.get('case_wifi_unsalvageable') and ctx.pathway in (
        PreopPatient.Pathway.ABF, PreopPatient.Pathway.PAD_BYPASS, PreopPatient.Pathway.PAD_PTA,
    ):
        ctx.red_flags.append(
            'კონფერენციის სტადირებით WIfI Stage 5 (კიდური გადარჩენადი არ არის) — '
            'რევასკულარიზაციის ჩვენება გადაამოწმეთ ქირურგთან!'
        )

    if ctx.abi_non_compressible and not ctx.is_ali_acute and not is_rup_pre:
        ctx.needs_tbi_check = True
        nc_side_lbl = f" ({'/'.join(ctx.nc_legs)})" if ctx.nc_legs else ''
        abi_disp = ctx.max_abi if ctx.max_abi is not None else ctx.max_abi_sat
        abi_nc_lim = rule_value('VEN_ARTERIAL_ISCHEMIA_COMPRESSION')['abi_noncomp']
        ctx.reflex_orders.append({
            'key': 'tbi',
            'trigger': f'არაკომპრესირებადი არტერია{nc_side_lbl} (ABI {abi_disp} > {abi_nc_lim:.2f} — მონკებერგის კალციფიკაცია)',
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


def _compute_core_metrics(patient, q, s, today):
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
    car_r, car_l = _num(s.get('carotid_r'), lo=0, hi=100), _num(s.get('carotid_l'), lo=0, hi=100)
    car_r_sat = _sat_num(_s_raw(s, 'carotid_r'), 0, 100)
    car_l_sat = _sat_num(_s_raw(s, 'carotid_l'), 0, 100)
    car_r_si = int(round(car_r_sat)) if car_r_sat is not None else None
    car_l_si = int(round(car_l_sat)) if car_l_sat is not None else None
    raw_s = patient.studies if isinstance(patient.studies, dict) else {}
    raw_q = patient.questionnaire if isinstance(patient.questionnaire, dict) else q
    implausible_fields = _detect_implausible_fields(
        raw_s, s, creat_unit, creat_unit_ambiguous, today=today, q=raw_q, raw_age=patient.age,
    )
    bmi_val = calc_bmi(s.get('weight_kg'), s.get('height_cm'))
    hb_unit_ambiguous = is_hb_unit_ambiguous(_s_raw(s, 'hb'))
    age_val = _int(patient.age, lo=12, hi=120)
    aaa_mm = _num(s.get('aaa_mm'), lo=10, hi=200)
    return {
        'raw_s': raw_s, 'raw_creat': raw_creat, 'on_dialysis': on_dialysis,
        'creat_unit': creat_unit, 'creat_unit_ambiguous': creat_unit_ambiguous, 'creat_unit_mismatch': creat_unit_mismatch,
        'egfr': egfr, 'crcl': crcl, 'egfr_sat': egfr_sat, 'crcl_sat': crcl_sat,
        'eff_renal': None if on_dialysis else min([v for v in (egfr, crcl) if v is not None], default=None),
        'eff_renal_sat': None if on_dialysis else min([v for v in (egfr_sat, crcl_sat) if v is not None], default=None),
        'car_r': car_r, 'car_l': car_l, 'car_r_sat': car_r_si, 'car_l_sat': car_l_si,
        'max_car': max([v for v in (car_r, car_l) if v is not None], default=None),
        'max_car_sat': max([v for v in (car_r_si, car_l_si) if v is not None], default=None),
        'implausible_fields': implausible_fields,
        'bmi': bmi_val, 'bmi_sat': _sat_bmi_from(bmi_val, s),
        'age': age_val,
        'age_sat': age_val if age_val is not None else (_sat_age(patient.age) if _num(patient.age) is not None else None),
        'aaa_mm': aaa_mm, 'aaa_mm_sat': aaa_mm if aaa_mm is not None else _sat_num(_s_raw(s, 'aaa_mm'), 10, 200),
        'hb_unit_ambiguous': hb_unit_ambiguous,
        'hb_gl': None if hb_unit_ambiguous else normalize_hb_gl(s.get('hb')),
        'hb_gl_sat': None if hb_unit_ambiguous else _sat_hb_gl(_s_raw(s, 'hb')),
    }


def init_eval_context(patient, today=None):
    today = _date(today) or timezone.localdate()
    pathway = _resolve_pathway(patient)
    q = merged_q(patient)
    s = merged_studies(patient)
    case_outcome = s.get('case_outcome_status') if s.get('case_outcome_status') in dict(CASE_OUTCOME_CHOICES) else 'scheduled'
    m = _compute_core_metrics(patient, q, s, today)
    ctx = EvalContext(
        patient=patient,
        today=today,
        pathway=pathway,
        is_venous=(pathway == PreopPatient.Pathway.VENOUS),
        is_phleb=(patient.op_subtype == PreopPatient.OpSubtype.PHLEB),
        is_pta=(pathway == PreopPatient.Pathway.PAD_PTA),
        is_pad_bypass=(pathway == PreopPatient.Pathway.PAD_BYPASS),
        is_on_hold=(patient.someday_list == PreopPatient.SomedayList.HOLD),
        q=q,
        s=s,
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
        gl_mmol=normalize_glucose_mmol(s.get('glucose')),
        gl_mmol_sat=_sat_gl_mmol(_s_raw(s, 'glucose')),
        alb_gl=normalize_albumin_gl(s.get('albumin')),
        liver_3x=(s.get('liver_status') == '3x'),
        cta_months=cta_live_months_age(s, today=today),
        **m,
    )
    non_bp_inv = [x for x in m['implausible_fields'] if 'ინვერტირებულია' not in x]
    if non_bp_inv:
        ctx.red_flags.append(
            f'შეყვანილია დაუშვებელი / დიაპაზონს გარეთა მნიშვნელობა ({", ".join(non_bp_inv[:4])}) — '
            'გადაამოწმეთ ლაბორატორიული ერთეული და მაჩვენებლები!'
        )
    elif any('დილის წნევა ინვერტირებულია' in x for x in m['implausible_fields']):
        inv_dos = [x for x in m['implausible_fields'] if 'დილის წნევა ინვერტირებულია' in x]
        ctx.red_flags.append(
            f'არტერიული წნევის არასწორი ჩანაწერი ({", ".join(inv_dos)}) — '
            'გადაამოწმეთ სისტოლური და დიასტოლური მაჩვენებლები!'
        )
    _init_limb_hemodynamics(ctx)
    _init_base_flags_and_pre_reflex(ctx)
    return ctx
