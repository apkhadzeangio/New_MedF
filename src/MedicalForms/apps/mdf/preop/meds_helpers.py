"""Shared helper utilities for perioperative medication scheduling and STOP-lock protection."""
import datetime

from .utils import _date, _has_raw_scalar, _int, _num, parse_plt_k

_IMMED_STOP_PLAN = 'შეწყვიტეთ დაუყოვნებლივ (ექიმთან შეთანხმებით)!'
_GI_BLEED_PLAN = 'აქტიური სისხლდენა/წყლული — სასწრაფოდ შეათანხმეთ ექიმთან!'


def _apply_stop_lock_to_med_schedule(rows):
    """C1: Suppress dated elective discontinuation orders on STOP while preserving acute/contraindicated safety alerts."""
    for r in rows:
        if r.get('acute_hold'):
            continue
        if r.get('unstructured'):
            r['date_plan'] = 'STOP — დააზუსტეთ ექიმთან (არასტრუქტურირებულია)!'
            r['tone'] = 'red'
        elif r.get('contraindicated'):
            r['date_plan'] = 'STOP — დაუყოვნებლივ დაუკავშირდით ექიმს!'
            r['tone'] = 'red'
        elif (
            r.get('hold_or_skip')
            or 'შემოწმება' in (r.get('date_plan') or '')
            or (r.get('drug') == 'ვარფარინი' and 'EVLA' in (r.get('rule') or ''))
        ):
            r['date_plan'] = 'STOP — თვითნებურად არ შეწყვიტოთ!'
            r['tone'] = 'red'
        elif r.get('date_plan') and ('მიიღეთ' in r['date_plan'] or 'შეათანხმეთ' in r['date_plan'] or 'არ შეწყვიტოთ' in r['date_plan']):
            r['date_plan'] = 'გააგრძელეთ ექიმთან შეთანხმებით (STOP — ოპერაცია შეჩერებულია)'
    return rows


def _make_med_row_helpers(op_dt):
    rows = []
    safe_dt = op_dt if (op_dt and 1900 <= op_dt.year <= 2100) else None

    def d_str(delta_days):
        if not safe_dt:
            return ''
        try:
            return (safe_dt + datetime.timedelta(days=delta_days)).strftime('%d.%m')
        except OverflowError:
            return ''

    def hold_str(hold_days, sfx=''):
        if not safe_dt:
            return ''
        return f'ბოლო: {d_str(-hold_days - 1)} · არ მიიღოთ {d_str(-hold_days)}-დან{sfx}'

    def add_row(drug, rule, tone, date_plan='', hold_or_skip=False, force_date_plan=False,
                acute_hold=False, contraindicated=False, glp1_diet=False, unstructured=False,
                mandatory_hold_days=0, reconcile_only=False):
        if contraindicated and (
            not (date_plan or '').strip()
            or 'ბოლო:' in (date_plan or '')
            or 'მიიღეთ' in (date_plan or '')
        ):
            date_plan = _IMMED_STOP_PLAN
            force_date_plan = True
        row = {
            'drug': drug,
            'rule': rule,
            'tone': tone,
            'date_plan': date_plan if (safe_dt or force_date_plan) else '',
            'hold_or_skip': hold_or_skip,
        }
        if acute_hold:
            row['acute_hold'] = True
        if contraindicated:
            row['contraindicated'] = True
        if glp1_diet:
            row['glp1_diet'] = True
        if unstructured:
            row['unstructured'] = True
        if mandatory_hold_days:
            row['mandatory_hold_days'] = int(mandatory_hold_days)
        if reconcile_only:
            row['reconcile_only'] = True
        rows.append(row)

    return rows, d_str, hold_str, add_row


def _valid_med_sbp(s):
    from .calculators import _inverted_bp_arms, _raw_bp_pair
    bp_sys, _, r_inv = _raw_bp_pair(s, 'bp_sys', 'bp_dia')
    bp_sys_l, _, l_inv = _raw_bp_pair(s, 'bp_sys_l', 'bp_dia_l')
    valid = [v for v, inv in ((bp_sys, r_inv), (bp_sys_l, l_inv)) if v is not None and not inv]
    eff = max(valid, default=None)
    return eff, (eff is None and bool(_inverted_bp_arms(s)))


def _med_renal_and_flags(patient, q, s, egfr, crcl):
    from .calculators import _parse_scr_mgdl, _s_raw, _sat_crcl, _sat_egfr, is_creat_unit_mismatch
    from .registry import rule_value
    on_dialysis = bool(q.get('on_dialysis'))
    egfr = int(round(1.0 if v < 0 else min(v, 300))) if (v := _num(egfr)) is not None else None
    crcl = int(round(1.0 if v < 0 else min(v, 2000))) if (v := _num(crcl)) is not None else None
    if not on_dialysis and egfr is None and crcl is None:
        raw_cr, cr_u = _s_raw(s, 'creat'), s.get('creat_unit') or ''
        age_raw, cr_raw_n = _num(patient.age), _num(raw_cr)
        out_of_rng = bool((age_raw is not None and age_raw > 120) or (cr_raw_n is not None and cr_raw_n > 3000))
        if out_of_rng and not is_creat_unit_mismatch(raw_cr, cr_u) and not _parse_scr_mgdl(raw_cr, cr_u)[1]:
            eg_s = _sat_egfr(raw_cr, patient.age, patient.sex, creat_unit=cr_u)
            cr_s = _sat_crcl(raw_cr, patient.age, patient.sex, weight_kg=_s_raw(s, 'weight_kg'), creat_unit=cr_u, height_cm=_s_raw(s, 'height_cm'))
            egfr = int(round(eg_s)) if eg_s is not None else None
            crcl = int(round(cr_s)) if cr_s is not None else None
    renal_vals = [x for x in (egfr, crcl) if x is not None]
    eff_renal = None if on_dialysis else (min(renal_vals) if renal_vals else None)
    cl_label = 'დიალიზი' if on_dialysis else (f'CrCl {crcl}' if crcl is not None and (egfr is None or crcl <= egfr) else (f'eGFR {egfr}' if egfr is not None else ''))
    age_m, frail_age = _int(patient.age, lo=12, hi=120) or 0, rule_value('ANES_AIRWAY_STOPBANG_ICU')['delirium_age']
    frail_no_wt_note = (f' [≥{frail_age} წ. — შეიყვანეთ წონა ზუსტი CrCl-ისთვის!]' if age_m >= frail_age else ' [შეიყვანეთ წონა ზუსტი CrCl-ისთვის!]') if (crcl is None and egfr is not None) else ''
    return on_dialysis, eff_renal, cl_label, frail_no_wt_note


def _pick_study_raw(s, raw_s, inv_in, key):
    if key in s:
        if _has_raw_scalar(s[key]):
            return s[key]
        if key in inv_in:
            return inv_in[key]
        san_inv = s.get('_sanitized_invalid')
        if isinstance(san_inv, dict) and key in san_inv:
            return san_inv[key]
        return None
    if key in inv_in:
        return inv_in[key]
    return raw_s.get(key)


def _standalone_is_neuraxial(patient, anesth_plan, is_phleb, has_epidural_line, q, s, today, eff_alg, ft_meds, cad_chips, med_chips, on_dialysis, eff_renal, raw_s=None):
    from django.utils import timezone

    from .calculators import _inverted_bp_arms, _s_raw, _valid_bilateral_sbp, normalize_fibrinogen_gl
    from .registry import rule_value
    from .utils import has_prior_stroke_or_tia
    if anesth_plan == 'neuraxial' or has_epidural_line:
        return True
    if not (anesth_plan == 'auto' and is_phleb):
        return False
    raw_s = raw_s if isinstance(raw_s, dict) else {}
    asra_cfg = rule_value('ASRA_NEURAXIAL_COAG_GATE')
    doac_low = rule_value('MED_DOAC_HOLD_INTERVALS')['crcl_low']
    dabi_low = rule_value('MED_DABIGATRAN_HOLD_INTERVALS')['crcl_low']
    shock_lim = rule_value('VIT_BP_HYPOTENSION_SHOCK')['shock_idx']
    inv_in = s.get('_invalid_inputs') if isinstance(s.get('_invalid_inputs'), dict) else {}
    raw_dos_i = _pick_study_raw(s, raw_s, inv_in, 'dos_inr')
    dos_i_pres = _has_raw_scalar(raw_dos_i)
    dos_i = _num(raw_dos_i, lo=0.5, hi=15.0)
    base_i = _num(_pick_study_raw(s, raw_s, inv_in, 'inr'), lo=0.4, hi=20.0)
    eff_i, op_dt, td = (dos_i if dos_i_pres else base_i), _date(s.get('planned_surgery_date')), (_date(today) or timezone.localdate())
    prewash = bool(q.get('med_warfarin') and not dos_i_pres and eff_i is not None and asra_cfg['inr_max'] < eff_i <= asra_cfg['vka_prewashout_inr_max'] and (op_dt is None or op_dt > td))
    plt_k = parse_plt_k(_pick_study_raw(s, raw_s, inv_in, 'plt'))
    fib = normalize_fibrinogen_gl(_pick_study_raw(s, raw_s, inv_in, 'fibrinogen'))
    def _has_bad(k, parsed):
        rv = _pick_study_raw(s, raw_s, inv_in, k)
        return parsed is None and (_has_raw_scalar(rv) or k in inv_in)
    ft_u = ft_meds.get('unstructured_keys') or set()
    ft_m = ft_meds.get('matched_keys') or set()
    p2y_cnt = sum([bool(q.get('med_clopidogrel')), bool(q.get('med_ticagrelor')), bool(q.get('med_prasugrel'))]) + len(ft_u & {'clopidogrel', 'ticagrelor', 'prasugrel'})
    has_stroke = has_prior_stroke_or_tia(patient, q=q, s=s, today=td)
    unint_p2y = p2y_cnt >= 1 and (bool(q.get('recent_stent')) or (('stent_6_12m' in cad_chips) and ('mi' in cad_chips)) or ('nsaid' in eff_alg and (bool(q.get('cad')) or bool(cad_chips) or has_stroke)) or p2y_cnt >= 2)
    sbp, hr_raw, bp_inv = _valid_bilateral_sbp(s), _num(_s_raw(s, 'hr')), bool(_inverted_bp_arms(s))
    raw_sbp_low = any((v := _num(_pick_study_raw(s, raw_s, inv_in, k))) is not None and (v < 30 or (sbp is None and v < asra_cfg['sbp_min'])) for k in ('bp_sys', 'bp_sys_l'))
    si = round(hr_raw / sbp, 2) if (hr_raw is not None and 15 <= hr_raw <= 260 and sbp and sbp > 0) else None
    doac_renal_blk = (
        ((q.get('med_doac') or 'rivaroxaban_2_5' in med_chips) and (on_dialysis or (eff_renal is not None and eff_renal < doac_low)))
        or (q.get('med_dabigatran') and (on_dialysis or (eff_renal is not None and eff_renal < dabi_low)))
    )
    blocked = (
        _has_bad('dos_inr', dos_i) or (not dos_i_pres and _has_bad('inr', base_i)) or _has_bad('plt', plt_k) or _has_bad('fibrinogen', fib)
        or (plt_k is not None and plt_k < asra_cfg['plt_min'])
        or (eff_i is not None and eff_i > asra_cfg['inr_max'] and not prewash) or (fib is not None and fib < asra_cfg['fib_min'])
        or bool(s.get('aptt_prolonged')) or unint_p2y or bool(ft_meds.get('unstructured_antithrombotic')) or bool(ft_m & {'parenteral_oac', 'parenteral_dti'})
        or ('hit_heparin' in eff_alg and 'mech_valve' in cad_chips)
        or doac_renal_blk
        or bool(q.get('ven_lidocaine_allergy')) or ('lidocaine' in eff_alg) or bool(s.get('echo_valve_issue')) or bp_inv
        or (sbp is not None and sbp < asra_cfg['sbp_min']) or raw_sbp_low or (si is not None and si >= shock_lim) or (hr_raw is not None and (hr_raw > 260 or hr_raw < 15))
    )
    return not blocked
