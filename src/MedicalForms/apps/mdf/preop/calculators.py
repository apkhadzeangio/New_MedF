"""Clinical calculators, lab unit normalizers, risk indices (RCRI, VSG-CRI, ARISCAT, Caprini, ASA), and surveillance."""
from ..models import PreopPatient
from .meds import _effective_allergy_state
from .registry import rule_value
from .utils import (
    _num,
    _resolve_pathway,
    _str_list,
    cea_live_days_since_event,
    is_pregnancy_eligible,
    is_symp_cea_patient,
)


def _s_raw(s, key):
    if not isinstance(s, dict):
        return None
    v = s.get(key)
    if v is not None and str(v).strip() != '':
        return v
    inv = s.get('_invalid_inputs')
    return inv.get(key) if isinstance(inv, dict) else None


def _sat_num(val, lo, hi):
    """Parse finite number and saturate out-of-range values to [lo, hi] for monotonic risk scoring."""
    v = _num(val)
    return None if v is None else max(lo, min(hi, v))


def _sat_age(val):
    v = _num(val)
    return 0 if (v is None or v < 1) else int(round(min(v, 120)))


def _parse_plt_k(val):
    """Normalize PLT to x10^9/L (converts /uL >=3000 via /1000; rejects ambiguous 2501..2999; saturates <=0 to 0)."""
    v = _num(val)
    if v is None:
        return None
    if v < 0:
        return 0
    if 2500 < v < 3000:
        return None
    return int(round(v / 1000.0)) if v >= 3000 else int(round(v))


def _parse_scr_mgdl(creat, creat_unit=''):
    """Return (scr_mgdl, is_ambiguous). Rejects explicit unit mismatches and 15..35 auto-detect ambiguity."""
    ren_u = rule_value('SCORE_RENAL_CKDEPI_CG')
    c = _num(creat, lo=0.1, hi=3000)
    if c is None:
        raw_c = _num(creat)
        if creat_unit == 'mgdl' and raw_c is not None and raw_c > ren_u['ambig_hi']:
            return None, True
        return None, False
    if creat_unit == 'mgdl':
        return (None, True) if c > ren_u['ambig_hi'] else (c, False)
    if creat_unit == 'umol':
        return (None, True) if c < ren_u['ambig_lo'] else (c / ren_u['umol_factor'], False)
    if 0.1 <= c <= ren_u['ambig_lo']:
        return c, False
    if c >= ren_u['ambig_hi']:
        return c / ren_u['umol_factor'], False
    return None, True


def is_creat_unit_mismatch(creat, creat_unit=''):
    """Return True when explicit creat_unit ('mgdl'/'umol') contradicts the entered creatinine magnitude."""
    ren_u = rule_value('SCORE_RENAL_CKDEPI_CG')
    raw = _num(creat)
    if raw is None or raw <= 0:
        return False
    return (creat_unit == 'mgdl' and raw > ren_u['ambig_hi']) or (creat_unit == 'umol' and raw < ren_u['ambig_lo'])


def _clean_num(v):
    if v is None:
        return None
    iv = int(round(v))
    return iv if abs(v - iv) < 1e-9 else v


def is_hb_unit_ambiguous(hb):
    """Return True when raw hemoglobin falls in the ambiguous 22.1–39.9 g/dL vs g/L zone."""
    v = _num(hb)
    u = rule_value('LAB_UNIT_AUTO_CONVERSION')
    return v is not None and u['hb_gdl_hi'] < v < u['hb_ambig_hi']


def _sat_scr_mgdl(creat, creat_unit=''):
    """Return scr_mgdl for risk scoring, saturating extreme >3000 umol/L (never unit-mismatched mg/dL)."""
    if is_creat_unit_mismatch(creat, creat_unit):
        return None
    ren_u = rule_value('SCORE_RENAL_CKDEPI_CG')
    scr, ambig = _parse_scr_mgdl(creat, creat_unit)
    if scr is not None:
        return scr
    raw = _num(creat)
    if raw is not None and raw > 3000 and not ambig and creat_unit != 'mgdl':
        return 3000.0 / ren_u['umol_factor']
    return None


def _egfr_from_scr(scr_mgdl, age_v, sex):
    if scr_mgdl is None or age_v is None or sex not in ('M', 'F'):
        return None
    kappa, alpha, mult = (0.7, -0.241, 1.012) if (sex == 'F') else (0.9, -0.302, 1.0)
    ratio = scr_mgdl / kappa
    return int(round(142.0 * (min(ratio, 1.0) ** alpha) * (max(ratio, 1.0) ** -1.200) * (0.9938 ** age_v) * mult))


def _crcl_from_scr(scr_mgdl, age_v, sex, w, height_cm=None):
    if scr_mgdl is None or w is None or age_v is None or sex not in ('M', 'F'):
        return None
    h_raw = _num(height_cm)
    h = max(100.0, min(230.0, h_raw)) if (h_raw is not None and h_raw > 0) else None
    eff_w = float(w)
    if h is not None:
        bmi_v = w / ((h / 100.0) ** 2)
        ibw = (50.0 if sex == 'M' else 45.5) + 0.9 * max(0.0, h - 152.4)
        if bmi_v >= 30.0 and w > ibw:
            eff_w = ibw + 0.4 * (w - ibw)
    crcl = ((140.0 - age_v) * eff_w) / (72.0 * scr_mgdl)
    return int(round(crcl * (0.85 if sex == 'F' else 1.0)))


def calc_egfr(creat, age, sex='M', creat_unit='', on_dialysis=False):
    """CKD-EPI 2021 race-free eGFR (mL/min/1.73m2). Returns None on missing/unit-mismatch/ambiguous inputs."""
    if on_dialysis:
        return None
    scr_mgdl, ambig = _parse_scr_mgdl(creat, creat_unit)
    if scr_mgdl is None and not ambig and creat_unit != 'mgdl':
        raw_c = _num(creat)
        if raw_c is not None and raw_c > 3000:
            scr_mgdl = 3000.0 / rule_value('SCORE_RENAL_CKDEPI_CG')['umol_factor']
    age_num = _num(age, lo=12, hi=120)
    return _egfr_from_scr(scr_mgdl, int(round(age_num)) if age_num is not None else None, sex)


def _sat_egfr(creat, age, sex='M', creat_unit='', on_dialysis=False):
    if on_dialysis:
        return None
    age_num = _num(age)
    age_v = int(round(min(age_num, 120))) if (age_num is not None and age_num >= 12) else None
    return _egfr_from_scr(_sat_scr_mgdl(creat, creat_unit), age_v, sex)


def calc_crcl(creat, age, sex='M', weight_kg=None, creat_unit='', on_dialysis=False, height_cm=None):
    """Cockcroft-Gault unindexed CrCl (mL/min) using ABW in BMI >= 30. Returns None on unit-mismatch/ambiguous inputs."""
    if on_dialysis:
        return None
    scr_mgdl, ambig = _parse_scr_mgdl(creat, creat_unit)
    if scr_mgdl is None and not ambig and creat_unit != 'mgdl':
        raw_c = _num(creat)
        if raw_c is not None and raw_c > 3000:
            scr_mgdl = 3000.0 / rule_value('SCORE_RENAL_CKDEPI_CG')['umol_factor']
    w = _num(weight_kg, lo=25, hi=260)
    age_num = _num(age, lo=12, hi=120)
    return _crcl_from_scr(scr_mgdl, int(round(age_num)) if age_num is not None else None, sex, w, height_cm)


def _sat_crcl(creat, age, sex='M', weight_kg=None, creat_unit='', on_dialysis=False, height_cm=None):
    if on_dialysis:
        return None
    w_raw = _num(weight_kg)
    w = max(25.0, min(260.0, w_raw)) if (w_raw is not None and w_raw > 0) else None
    age_num = _num(age)
    age_v = int(round(min(age_num, 120))) if (age_num is not None and age_num >= 12) else None
    return _crcl_from_scr(_sat_scr_mgdl(creat, creat_unit), age_v, sex, w, height_cm)


def calc_bmi(weight_kg, height_cm):
    """Body Mass Index (kg/m2) or None."""
    w, h = _num(weight_kg, lo=25, hi=260), _num(height_cm, lo=100, hi=230)
    return None if (w is None or h is None) else round(w / ((h / 100.0) ** 2), 1)


def normalize_hb_gl(hb):
    """Return hemoglobin in g/L using registered LAB_UNIT_AUTO_CONVERSION bounds."""
    u = rule_value('LAB_UNIT_AUTO_CONVERSION')
    v = _num(hb, lo=u['hb_gdl_lo'], hi=260)
    if v is None:
        return None
    return _clean_num(round(v * 10, 1) if u['hb_gdl_lo'] <= v <= u['hb_gdl_hi'] else v)


def normalize_glucose_mmol(gl):
    """Return glucose in mmol/L (converts mg/dL > 35.0 with 2-decimal precision for unit-boundary parity)."""
    v = _num(gl, lo=0.5, hi=1000)
    if v is None:
        return None
    return _clean_num(round(v / 18.0, 2) if v > rule_value('GLY_UNIT_CONVERSION_MGDL') else v)


def normalize_albumin_gl(alb):
    """Return serum albumin in g/L using registered LAB_UNIT_AUTO_CONVERSION bounds."""
    v = _num(alb, lo=0.5, hi=80)
    if v is None:
        return None
    u = rule_value('LAB_UNIT_AUTO_CONVERSION')
    return _clean_num(round(v * 10, 1) if u['alb_gdl_lo'] <= v <= u['alb_gdl_hi'] else v)


def normalize_fibrinogen_gl(fib):
    """Return plasma fibrinogen in g/L (idempotent across 15.01..1500 mg/dL via lo=0.15)."""
    v = _num(fib, lo=0.15, hi=1500)
    if v is None:
        return None
    return _clean_num(round(v / 100.0, 2) if v > rule_value('LAB_UNIT_AUTO_CONVERSION')['fib_mgdl_gt'] else v)


def normalize_ldl_mmol(ldl):
    """Return LDL cholesterol in mmol/L using registered LAB_UNIT_AUTO_CONVERSION bounds."""
    v = _num(ldl, lo=0.2, hi=500)
    if v is None:
        return None
    return _clean_num(round(v / 38.67, 2) if v > rule_value('LAB_UNIT_AUTO_CONVERSION')['ldl_mgdl_gt'] else v)


def calc_max_contrast_ml(egfr, on_dialysis=False):
    """Safe iodinated contrast volume ceiling (mL) using tiered contrast-to-eGFR ratios (ESVS / Gurm)."""
    if on_dialysis or egfr is None:
        return None
    eg_raw = _num(egfr)
    if eg_raw is None:
        return None
    eg_v = min(500.0, max(0.0, eg_raw))
    ren = rule_value('REN_CKD_NEPHRO_HYDRATION')
    if eg_v < ren['red']:
        return max(15, int(round(eg_v * ren.get('mult_lt30', 1.0))))
    if eg_v < ren['nephro']:
        return int(round(eg_v * ren.get('mult_30_44', 1.5)))
    if eg_v < ren['hydration']:
        return int(round(eg_v * ren.get('mult_45_59', 2.0)))
    return min(250, int(round(eg_v * ren['gurm_mult'])))


def _raw_bp_pair(s, sk, dk):
    rsv, rdv = _num(_s_raw(s, sk)), _num(_s_raw(s, dk))
    sv, dv = _sat_num(rsv, 30, 320), _sat_num(rdv, 20, 220)
    return sv, dv, (rsv is not None and rdv is not None and rsv <= rdv)


def _valid_bilateral_sbp(s):
    """Return max valid systolic BP across R/L arms, rejecting inverted readings (raw SBP <= DBP)."""
    bp_sys, _, r_inv = _raw_bp_pair(s, 'bp_sys', 'bp_dia')
    bp_sys_l, _, l_inv = _raw_bp_pair(s, 'bp_sys_l', 'bp_dia_l')
    valid = [v for v, inv in ((bp_sys, r_inv), (bp_sys_l, l_inv)) if v is not None and not inv]
    return max(valid, default=None)


def _valid_bilateral_dbp(s):
    """Return max valid diastolic BP across R/L arms, rejecting inverted readings (raw SBP <= DBP)."""
    _, bp_dia, r_inv = _raw_bp_pair(s, 'bp_sys', 'bp_dia')
    _, bp_dia_l, l_inv = _raw_bp_pair(s, 'bp_sys_l', 'bp_dia_l')
    valid = [v for v, inv in ((bp_dia, r_inv), (bp_dia_l, l_inv)) if v is not None and not inv]
    return max(valid, default=None)


def _inverted_bp_arms(s):
    """Return saturated (sys, dia) tuples for entered BP arms where raw SBP <= DBP."""
    arms = []
    for sk, dk in (('bp_sys', 'bp_dia'), ('bp_sys_l', 'bp_dia_l')):
        sv, dv, inv = _raw_bp_pair(s, sk, dk)
        if inv:
            arms.append((sv, dv))
    return arms


def _sat_bmi_from(bmi, s):
    v = _sat_num(bmi, 8, 90)
    if v is not None:
        return v
    w = _sat_num(_s_raw(s, 'weight_kg'), 25, 260)
    h = _sat_num(_s_raw(s, 'height_cm'), 100, 230)
    if w is not None and h is not None:
        return _sat_num(round(w / ((h / 100.0) ** 2), 1), 8, 90)
    return None


def _caprini_points(patient, q, bmi, today, s_cur, cad_set, has_hit):
    cap = rule_value('SCORE_CAPRINI_VTE')
    lvef_red = rule_value('CARDIO_BIOMARKERS_ECHO')['lvef_red']
    pts = 0
    age = _sat_age(patient.age)
    pts += 3 if age >= cap['age_3'] else (2 if age >= cap['age_2'] else (1 if age >= cap['age_1'] else 0))

    pw = _resolve_pathway(patient)
    if pw == PreopPatient.Pathway.VENOUS:
        pts += 1  # varicose veins
        if q.get('ven_ceap') in ('C3', 'C4', 'C5', 'C6'):
            pts += 1  # leg edema / chronic venous stasis
        pts += 2 if patient.op_subtype == PreopPatient.OpSubtype.PHLEB else 1
    elif pw == PreopPatient.Pathway.PAD_PTA:
        pts += 1
    else:
        pts += 2  # major vascular surgery >45 min

    bmi_v = _sat_bmi_from(bmi, s_cur)
    if bmi_v is not None and bmi_v > cap['bmi_1']:
        pts += 1
    if (patient.sex or '') == 'F' and q.get('ven_hormones'):
        pts += 1
    if is_pregnancy_eligible(patient) and (q.get('ven_pregnant') or q.get('pregnant_possible')):
        pts += 1
    pulm_chips = set(_str_list(q.get('pulm_chips')))
    if ('copd' in pulm_chips) or (q.get('pulm') and not pulm_chips):
        pts += 1  # Caprini 2005 awards +1 for COPD, not isolated asthma

    cea_d = cea_live_days_since_event(q, today=today, s=s_cur)
    if (
        pw == PreopPatient.Pathway.CEA
        and cea_d is not None and cea_d <= cap['stroke_days']
        and ('minor_stroke' in _str_list(q.get('cea_event_chips')) or bool(q.get('cea_disabling_stroke')))
    ):
        pts += 5  # acute stroke <1 month (remote stroke >1m = 0 pts per Caprini 2005)

    lvef_c = _sat_num(_s_raw(s_cur, 'lvef'), 5, 95)
    if ('hf' in cad_set) or (lvef_c is not None and lvef_c < lvef_red):
        pts += 1
    if 'mi' in cad_set:
        pts += 1
    if q.get('ven_cancer_immob'):
        pts += 2
    if q.get('ven_dvt_history') or bool(_str_list(q.get('ven_dvt_chips'))):
        pts += 3
    if q.get('ven_thrombophilia'):
        pts += 3
    if has_hit:
        pts += 3  # HIT is a +3 Caprini hypercoagulable state AND contraindicates all heparins!
    return pts


def _caprini_tier_and_advice(
    pts, has_mech_valve, has_hit, severe_renal, severe_liver, compression_contra,
    cea_bleed_block=False, eff_pregnant=False,
):
    cap_cfg = rule_value('SCORE_CAPRINI_VTE')
    comp_sfx = '' if compression_contra else ' + კომპრესია'
    comp_low = ' (კომპრესია უკუნაჩვენებია არტერიული უკმარისობის გამო!)' if compression_contra else ' + ელასტიური კომპრესია'
    hit_dtis = (
        'IV ბივალირუდინი (არგატრობანი ღვიძლის ≥3x-ზე ერიდეთ/დოზა -75%!)'
        if severe_liver else 'IV ბივალირუდინი/არგატრობანი'
    )
    if pts >= cap_cfg['high_min']:
        tier = f"მაღალი (≥{cap_cfg['high_min']})"
        if cea_bleed_block:
            advice = f'მხოლოდ მექანიკური პროფილაქტიკა (IPC{comp_sfx}) — ტვინის სისხლჩაქცევის / rt-PA ≤6 დღის ფონზე ფარმაკოლოგიური ანტიკოაგულაცია დაბლოკილია!'
        elif has_mech_valve:
            if has_hit:
                advice = f'მექანიკური სარქველი + HIT: სტაციონარული {hit_dtis} და უვადო VKA (ჰეპარინი/LMWH და DOAC აკრძალულია!){comp_sfx}'
            elif severe_renal:
                advice = f'მექანიკური სარქველი + CrCl/eGFR <30: IV UFH ხიდი aPTT კონტროლით და უვადო VKA (DOAC აკრძალულია!){comp_sfx}'
            else:
                advice = f'მექანიკური სარქველი: ვარფარინის (VKA) აღდგენა ± LMWH ხიდი კარდიოლოგის სქემით (DOAC აკრძალულია!){comp_sfx}'
        elif eff_pregnant:
            if has_hit:
                advice = f'მექანიკური პროფილაქტიკა (IPC) / არაჰეპარინული სქემა მეან-გინეკოლოგთან და ჰემატოლოგთან (ორსულობა + HIT — ჰეპარინი და DOAC აკრძალულია!){comp_sfx}'
            elif severe_renal:
                advice = f'ფარმაკოლოგიური პროფილაქტიკა: კანქვეშა UFH ან anti-Xa კონტროლირებადი LMWH (ორსულობა + CrCl<30 — DOAC უკუნაჩვენებია!){comp_sfx}'
            else:
                advice = f'ფარმაკოლოგიური პროფილაქტიკა (მხოლოდ LMWH / UFH 7–10 დღე — ორსულობაზე DOAC უკუნაჩვენებია!){comp_sfx}'
        elif has_hit:
            if severe_renal:
                advice = f'მექანიკური თრომბოპროფილაქტიკა (IPC) / სტაციონარული {hit_dtis} (HIT + CrCl/eGFR <30: ჰეპარინი, ფონდაპარინუქსი და DOAC აკრძალულია!){comp_sfx}'
            elif severe_liver:
                advice = f'არაჰეპარინული პროფილაქტიკა: IPC / IV ბივალირუდინი (HIT: ჰეპარინი აკრძალულია; ღვიძლის ≥3x-ზე DOAC და ფონდაპარინუქსი არარეკომენდებულია!){comp_sfx}'
            else:
                advice = f'არაჰეპარინული პროფილაქტიკა: ფონდაპარინუქსი ან DOAC 7–10 დღე (HIT: LMWH/ჰეპარინი აკრძალულია!){comp_sfx}'
        elif severe_renal:
            advice = f'ფარმაკოლოგიური პროფილაქტიკა: კანქვეშა UFH 5000 სე ×2–3 ან თირკმელზე კორექტირებული LMWH anti-Xa კონტროლით (CrCl/eGFR <30: DOAC/ფონდაპარინუქსი აკრძალულია!){comp_sfx}'
        elif severe_liver:
            advice = f'ფარმაკოლოგიური პროფილაქტიკა: LMWH / UFH კოაგულოგრამის კონტროლით (ღვიძლის ≥3x-ზე DOAC უკუნაჩვენებია!){comp_sfx}'
        else:
            advice = f'ფარმაკოლოგიური პროფილაქტიკა (LMWH / DOAC 7–10 დღე){comp_sfx}'
    elif pts > cap_cfg['low_max']:
        tier = f"საშუალო ({cap_cfg['low_max'] + 1}–{cap_cfg['mod_max']})"
        if cea_bleed_block:
            advice = f'მხოლოდ ადრეული მობილიზაცია + IPC{comp_sfx} (ტვინის სისხლჩაქცევა / rt-PA ≤6 დღე — ფარმაკოლოგიური ანტიკოაგულაცია დაბლოკილია!)'
        elif has_mech_valve and has_hit:
            advice = f'მექანიკური სარქველი + HIT: {hit_dtis} და უვადო VKA (ჰეპარინი/LMWH და DOAC აკრძალულია!){comp_sfx}'
        elif has_mech_valve:
            advice = f'ადრეული მობილიზაცია{comp_sfx} + უვადო ვარფარინის (VKA) რეჟიმი (DOAC აკრძალულია!)'
        elif eff_pregnant:
            if has_hit:
                advice = f'ადრეული მობილიზაცია{comp_sfx} + IPC (ორსულობა + HIT — ჰეპარინი და DOAC აკრძალულია!)'
            else:
                advice = f'ადრეული მობილიზაცია{comp_sfx} ± LMWH / UFH მეან-გინეკოლოგის შეფასებით (ორსულობაზე DOAC უკუნაჩვენებია!)'
        elif has_hit:
            if severe_renal:
                advice = f'ადრეული მობილიზაცია{comp_sfx} + IPC (HIT + CrCl/eGFR <30: ჰეპარინი, ფონდაპარინუქსი და DOAC აკრძალულია!)'
            elif severe_liver:
                advice = f'ადრეული მობილიზაცია{comp_sfx} + IPC / ბივალირუდინი (HIT + ღვიძლის ≥3x: ჰეპარინი, DOAC და ფონდაპარინუქსი არა!)'
            else:
                advice = f'ადრეული მობილიზაცია{comp_sfx} ± ფონდაპარინუქსი/DOAC (HIT: ჰეპარინი აკრძალულია!)'
        elif severe_renal:
            advice = f'ადრეული მობილიზაცია{comp_sfx} ± კანქვეშა UFH / დოზა-კორექტირებული LMWH (CrCl/eGFR <30: DOAC არა!)'
        elif severe_liver:
            advice = f'ადრეული მობილიზაცია{comp_sfx} ± LMWH ქირურგის შეფასებით (ღვიძლის ≥3x-ზე DOAC არა!)'
        else:
            advice = f'ადრეული მობილიზაცია{comp_sfx} ± LMWH/DOAC ქირურგის შეფასებით'
    else:
        tier = 'დაბალი (0–2)'
        advice = f'ადრეული სიარული{comp_low} (რუტინული ჰეპარინის გარეშე)'
    return tier, advice


def calc_caprini(patient, q, bmi=None, today=None, eff_renal=None, liver_status='normal', compression_contra=False):
    """Auto-calculate numeric Caprini VTE score (with HIT +3 & acute stroke <1m +5) and safe VTE advice."""
    q_d = q if isinstance(q, dict) else {}
    s_cur = patient.studies if isinstance(patient.studies, dict) else {}
    cad_set = set(_str_list(q_d.get('cad_chips')))
    eff_alg, _, _ = _effective_allergy_state(q_d)
    has_hit = 'hit_heparin' in eff_alg
    pts = _caprini_points(patient, q_d, bmi, today, s_cur, cad_set, has_hit)
    eff_ren_raw = _num(eff_renal)
    eff_ren_v = max(0.0, eff_ren_raw) if eff_ren_raw is not None else None
    ren_red = rule_value('REN_CKD_NEPHRO_HYDRATION')['red']
    rtpa_d = rule_value('CEA_ACUTE_NEURO_THROMBOLYSIS')
    severe_renal = bool(q_d.get('on_dialysis')) or (eff_ren_v is not None and eff_ren_v < ren_red)
    severe_liver = (liver_status or s_cur.get('liver_status') or 'normal') == '3x'
    cea_d = cea_live_days_since_event(q_d, today=today, s=s_cur)
    cea_bleed_block = _resolve_pathway(patient) == PreopPatient.Pathway.CEA and (
        s_cur.get('brain_imaging') == 'large_or_hemorrhage'
        or (bool(q_d.get('cea_thrombolysis')) and (cea_d is None or cea_d <= rtpa_d))
    )
    eff_preg = is_pregnancy_eligible(patient) and bool(q_d.get('pregnant_possible') or q_d.get('ven_pregnant'))
    tier, advice = _caprini_tier_and_advice(
        pts, 'mech_valve' in cad_set, has_hit, severe_renal, severe_liver, compression_contra,
        cea_bleed_block=cea_bleed_block, eff_pregnant=eff_preg,
    )
    return pts, tier, advice


def _sat_hb_gl(raw):
    if is_hb_unit_ambiguous(raw):
        return None
    hb_gl = normalize_hb_gl(raw)
    if hb_gl is not None:
        return hb_gl
    v = _num(raw)
    u = rule_value('LAB_UNIT_AUTO_CONVERSION')
    return 0.5 if (v is not None and v < u['hb_gdl_lo']) else None


def _sat_fib_gl(raw):
    fib = normalize_fibrinogen_gl(raw)
    if fib is not None:
        return fib
    v = _num(raw)
    return 0.1 if (v is not None and v < 0.2) else None


def _sat_gl_mmol(raw):
    gl = normalize_glucose_mmol(raw)
    if gl is not None:
        return gl
    v = _num(raw)
    return None if v is None else (0.5 if v < 0.5 else round(1000.0 / 18.0, 2))


def calc_auto_asa(patient, q, s, egfr=None, bmi=None, today=None):
    """Estimate ASA Physical Status ('I'..'IV') deterministically from vascular pathway, comorbidities & acute vitals/labs."""
    asa = rule_value('SCORE_ASA_PHYSICAL_STATUS')
    q = q if isinstance(q, dict) else {}
    s = s if isinstance(s, dict) else {}
    egfr_raw = _num(egfr)
    egfr_v = max(0.0, min(300.0, egfr_raw)) if egfr_raw is not None else None
    if egfr_v is None and not q.get('on_dialysis'):
        sat_cr = _sat_scr_mgdl(_s_raw(s, 'creat'), s.get('creat_unit') or '')
        if sat_cr is not None and sat_cr >= 20.0:
            egfr_v = 2
    bmi_v, pw = _sat_bmi_from(bmi, s), _resolve_pathway(patient)
    cad_chips = set(_str_list(q.get('cad_chips')))
    allergy_chips, _, _ = _effective_allergy_state(q)
    aaa_mm, lvef, fev1 = _sat_num(_s_raw(s, 'aaa_mm'), 10, 250), _sat_num(_s_raw(s, 'lvef'), 5, 95), _sat_num(_s_raw(s, 'spiro_fev1'), 10, 160)
    hb_gl, gl_mmol, hba1c = _sat_hb_gl(_s_raw(s, 'hb')), _sat_gl_mmol(_s_raw(s, 'glucose')), _sat_num(_s_raw(s, 'hba1c'), 3.0, 25.0)
    spo2, eff_sbp, eff_dbp = _sat_num(_s_raw(s, 'spo2'), 30, 100), _valid_bilateral_sbp(s), _valid_bilateral_dbp(s)
    inv_bp_all = _inverted_bp_arms(s)
    inv_bp = inv_bp_all if eff_sbp is None else []
    inv_max_all, inv_min = max((max(a, b) for a, b in inv_bp_all), default=None), min((min(a, b) for a, b in inv_bp), default=None)
    raw_sbp_low = min([v for v in (_sat_num(_s_raw(s, 'bp_sys'), 30, 320), _sat_num(_s_raw(s, 'bp_sys_l'), 30, 320)) if v is not None], default=None)
    hr_val, k_val, plt_val = _sat_num(_s_raw(s, 'hr'), 15, 260), _sat_num(_s_raw(s, 'k'), 1.0, 12.0), _parse_plt_k(_s_raw(s, 'plt'))
    fib_val, inr_val = _sat_fib_gl(_s_raw(s, 'fibrinogen')), _sat_num(_s_raw(s, 'inr'), 0.4, 20.0)
    nt_probnp, hs_ctn = _sat_num(_s_raw(s, 'nt_probnp'), 1, 100000), _sat_num(_s_raw(s, 'hs_ctn'), 0.1, 50000)
    cea_d = cea_live_days_since_event(q, today=today, s=s)
    is_symp_cea = is_symp_cea_patient(patient, q, today=today, s=s, require_filled=False)
    is_pad_pw = pw in (PreopPatient.Pathway.ABF, PreopPatient.Pathway.PAD_BYPASS, PreopPatient.Pathway.PAD_PTA)

    if (
        (pw == PreopPatient.Pathway.AAA_INFRA and (bool(q.get('aaa_pain')) or s.get('aaa_morphology') == 'inflammatory_mycotic'))
        or (aaa_mm is not None and aaa_mm >= rule_value('AAA_GIANT_MM'))
        or (pw == PreopPatient.Pathway.CEA and bool(q.get('cea_crescendo')))
        or (is_symp_cea and (cea_d is None or cea_d <= asa['cea_recent_d']))
        or bool(q.get('recent_stent'))
        or ('mech_valve' in cad_chips and ('hit_heparin' in allergy_chips or bool(q.get('med_doac')) or bool(q.get('med_dabigatran')) or (bool(q.get('filled')) and not q.get('med_warfarin'))))
        or (lvef is not None and lvef < asa['lvef_iv']) or bool(s.get('echo_valve_issue')) or (s.get('ecg') == 'ischemia_arrhythmia')
        or (is_pad_pw and bool(q.get('abf_wet_infection')))
        or (fev1 is not None and fev1 < asa['fev1_iv']) or (spo2 is not None and spo2 < asa['spo2_iv'])
        or (eff_sbp is not None and eff_sbp < asa['sbp_iv']) or (inv_min is not None and inv_min < asa['sbp_iv'])
        or (eff_sbp is not None and eff_sbp <= asa['sbp_si_iv'] and hr_val is not None and hr_val >= eff_sbp)
        or (eff_sbp is None and raw_sbp_low is not None and raw_sbp_low <= asa['sbp_si_iv'] and hr_val is not None and hr_val >= raw_sbp_low)
        or (hr_val is not None and (hr_val >= asa['hr_iv_hi'] or hr_val < asa['hr_iv_lo']))
        or (k_val is not None and (k_val >= asa['k_iv_hi'] or k_val < asa['k_iv_lo']))
        or (hb_gl is not None and hb_gl < asa['hb_iv']) or (plt_val is not None and plt_val < asa['plt_iv'])
        or (fib_val is not None and fib_val < asa['fib_iv'])
        or (egfr_v is not None and egfr_v < asa['egfr_iv'] and not q.get('on_dialysis'))
        or (s.get('tsh_status') == 'hypothyroid_severe') or (s.get('liver_status') == '3x' and inr_val is not None and inr_val > asa['inr_iv'])
        or (bmi_v is not None and bmi_v < asa['bmi_iv_lo'])
        or (nt_probnp is not None and nt_probnp >= asa['bnp_iv']) or (hs_ctn is not None and hs_ctn >= asa['ctn_iv'])
    ):
        return 'IV'

    if (
        pw != PreopPatient.Pathway.VENOUS or bool(q.get('on_dialysis'))
        or (egfr_v is not None and egfr_v < asa['egfr_iii']) or bool(q.get('cad')) or bool(cad_chips)
        or bool(q.get('neuro')) or (s.get('ecg') == 'afib_controlled')
        or bool(q.get('ven_dvt_history')) or bool(_str_list(q.get('ven_dvt_chips'))) or bool(q.get('ven_cancer_immob'))
        or (s.get('liver_status') == '3x') or (hb_gl is not None and hb_gl < asa['hb_iii'])
        or (hba1c is not None and hba1c >= asa['hba1c_iii']) or (gl_mmol is not None and gl_mmol >= asa['gl_iii'])
        or bool(q.get('dm_insulin')) or (bmi_v is not None and bmi_v >= asa['bmi_iii'])
        or bool(q.get('pacemaker')) or ('copd' in _str_list(q.get('pulm_chips')))
        or (spo2 is not None and spo2 < asa['spo2_iii']) or (eff_sbp is not None and eff_sbp >= asa['sbp_iii'])
        or (eff_dbp is not None and eff_dbp >= asa['dbp_iii']) or (inv_max_all is not None and inv_max_all >= asa['dbp_iii'])
        or (k_val is not None and (k_val >= asa['k_iii_hi'] or k_val < asa['k_iii_lo'])) or (q.get('cfs_level') == '5_plus')
    ):
        return 'III'

    if (
        bool(q.get('htn')) or bool(q.get('dm')) or bool(q.get('metformin')) or bool(q.get('smoker'))
        or bool(q.get('pulm')) or bool(_str_list(q.get('pulm_chips'))) or bool(q.get('thyroid_disease'))
        or (hba1c is not None and hba1c >= asa['hba1c_ii'])
        or (is_pregnancy_eligible(patient) and (bool(q.get('ven_pregnant')) or bool(q.get('pregnant_possible'))))
        or (bmi_v is not None and bmi_v >= asa['bmi_ii'])
    ):
        return 'II'
    return 'I'


def build_surveillance_plan(patient):
    """Return pathway-specific post-operative Duplex/CTA surveillance schedule (ESVS 2022–2024)."""
    pw = _resolve_pathway(patient)
    if pw == PreopPatient.Pathway.AAA_INFRA:
        if patient.op_subtype == 'evar':
            return 'ESVS 2024 EVAR მეთვალყურეობა (რისკ-სტრატიფიცირებული): კტ-ანგიოგრაფია (CTA) 30 დღეში; დაბალი რისკისას (ენდოლიკის გარეშე, IFU-ში) — კონტროლი 5 წელიწადში, მაღალი რისკისას — ყოველწლიური CTA/დუპლექსი.'
        return 'ESVS 2024 ღია AAA მეთვალყურეობა: კლინიკური და დუპლექს-შეფასება 6–12 თვეში, შემდეგ აორტა-თეძოს CTA/დუპლექსი ყოველ 5 წელიწადში.'
    if pw == PreopPatient.Pathway.CEA:
        return 'ESVS 2023 CEA მეთვალყურეობა: საძილე არტერიების დუპლექს-სკანირება 1–3 თვეში, 6–12 თვეში და შემდეგ წელიწადში ერთხელ + BP და LDL (<1.4 მმოლ/ლ) კონტროლი.'
    if pw == PreopPatient.Pathway.ABF:
        return 'ESVS 2024 ABF მეთვალყურეობა: კლინიკური შეფასება, ABI და ბარძაყის ანასტომოზების დუპლექსი 1, 6 და 12 თვეში, შემდეგ წელიწადში ერთხელ.'
    if pw == PreopPatient.Pathway.PAD_BYPASS:
        return 'ESVS 2024 შუნტირების მეთვალყურეობა: შუნტის დუპლექს-სკანირება (PSV / რესტენოზი) + ABI/TBI 1, 3, 6 და 12 თვეში, შემდეგ წელიწადში ერთხელ.'
    if pw == PreopPatient.Pathway.PAD_PTA:
        return 'ESVS 2024 PTA/სტენტირების მეთვალყურეობა: კლინიკური შეფასება + ABI/TBI და დუპლექსი 1 თვეში და 12 თვეში (CLTI-ისას — წყლულის შეხორცებამდე).'
    if patient.op_subtype == PreopPatient.OpSubtype.EVLA:
        return 'ESVS 2022 EVLA მეთვალყურეობა: საკონტროლო ვენური დუპლექს-სკანირება 1–4 კვირაში (EHIT-ის გამორიცხვა და ღეროს ოკლუზიის დადასტურება).'
    return 'ESVS 2022 ფლებექტომიის მეთვალყურეობა: კლინიკური და დუპლექს-კონტროლი 4–6 კვირაში.'


def _ihd_and_chf_flags(q, s):
    """Distinguish true ischemic heart disease (IHD) and clinical heart failure (CHF) for Lee RCRI & Bertges VSG-CRI."""
    q = q if isinstance(q, dict) else {}
    s = s if isinstance(s, dict) else {}
    q_filled = bool(q.get('filled'))
    cad_chips = set(_str_list(q.get('cad_chips')))
    ecg = s.get('ecg') or 'pending'
    lvef = _sat_num(_s_raw(s, 'lvef'), 5, 95)
    lvef_red = rule_value('CARDIO_BIOMARKERS_ECHO')['lvef_red']
    ihd = bool(
        q.get('recent_stent')
        or bool(cad_chips & {'mi', 'angina', 'cabg', 'stent_old', 'stent_6_12m'})
        or ecg == 'ischemia_arrhythmia'
        or (q_filled and bool(q.get('cad')) and not cad_chips)
    )
    chf = bool(('hf' in cad_chips) or (lvef is not None and lvef < lvef_red))
    return ihd, chf


def calc_rcri(patient, q, s, is_symp_cea=False):
    """Validated 6-factor Revised Cardiac Risk Index (Lee et al., Circulation 1999 / ESC 2022)."""
    q = q if isinstance(q, dict) else {}
    s = s if isinstance(s, dict) else {}
    rcri_cfg = rule_value('SCORE_RCRI_LEE_1999')
    pathway = _resolve_pathway(patient)
    q_filled = bool(q.get('filled'))
    ihd, chf = _ihd_and_chf_flags(q, s)
    scr_mgdl = _sat_scr_mgdl(_s_raw(s, 'creat'), s.get('creat_unit') or '')

    factors = []
    # Original Lee 1999 high-risk surgery factor: strictly intraperitoneal, intrathoracic,
    # or suprainguinal vascular surgery (open/TBD AAA or ABF — NOT infrainguinal bypass or CEA).
    is_suprainguinal_vasc_op = (
        pathway == PreopPatient.Pathway.ABF
        or (pathway == PreopPatient.Pathway.AAA_INFRA and patient.op_subtype != 'evar')
    )
    if is_suprainguinal_vasc_op:
        factors.append('მაღალი რისკის სუპრაინგვინალური სისხლძარღვოვანი ოპერაცია (Lee 1999)')
    if ihd:
        factors.append('გულის იშემიური დაავადება (IHD)')
    if chf:
        factors.append('გულის უკმარისობა (CHF / LVEF <40%)')
    has_prior_cvd = (
        (q_filled and (bool(q.get('neuro')) or bool(q.get('cea_symp_event'))))
        or is_symp_cea
        or (pathway == PreopPatient.Pathway.CEA and patient.op_subtype == PreopPatient.OpSubtype.CEA_SYMP)
        or (pathway != PreopPatient.Pathway.VENOUS and bool(s.get('carotid_symp')))
        or (s.get('brain_imaging') == 'large_or_hemorrhage')
    )
    if has_prior_cvd:
        factors.append('ინსულტი / TIA ანამნეზში')
    if bool(q.get('dm_insulin')):
        factors.append('ინსულინოთერაპია')
    if bool(q.get('on_dialysis')) or (scr_mgdl is not None and scr_mgdl > rcri_cfg['scr_mgdl']):
        factors.append('კრეატინინი >2.0 მგ/დლ (>177 მკმოლ/ლ) / დიალიზი')

    score = len(factors)
    band_map = dict(rcri_cfg['bands'])
    if score == 0:
        mace_pct, tier, tier_label = band_map[0], 'low', f'დაბალი (~{band_map[0]} MACE)'
    elif score == 1:
        mace_pct, tier, tier_label = band_map[1], 'low', f'დაბალი (~{band_map[1]} MACE)'
    elif score == 2:
        mace_pct, tier, tier_label = band_map[2], 'intermediate', f'საშუალო (~{band_map[2]} MACE)'
    else:
        pct_hi = band_map[3].replace('>=', '≥')
        mace_pct, tier, tier_label = pct_hi, 'high', f'მაღალი ({pct_hi} MACE)'
    return {'score': score, 'mace_pct': mace_pct, 'tier': tier, 'tier_label': tier_label, 'factors': factors}


def calc_vsg_cri(patient, q, s):
    """Vascular Study Group of New England Cardiac Risk Index (Bertges et al., J Vasc Surg 2010 — exact published MACE table)."""
    q = q if isinstance(q, dict) else {}
    s = s if isinstance(s, dict) else {}
    vsg_cfg = rule_value('SCORE_VSG_CRI_BERTGES_2010')
    fev1_amb = rule_value('PULM_CXR_SPIROMETRY')['fev1_amb']
    pathway = _resolve_pathway(patient)
    if pathway == PreopPatient.Pathway.VENOUS:
        return None
    q_filled = bool(q.get('filled'))
    cad_chips = set(_str_list(q.get('cad_chips')))
    pulm_chips = set(_str_list(q.get('pulm_chips')))
    med_chips = set(_str_list(q.get('med_chips')))
    ihd, chf = _ihd_and_chf_flags(q, s)
    scr_mgdl = _sat_scr_mgdl(_s_raw(s, 'creat'), s.get('creat_unit') or '')
    spiro_fev1 = _sat_num(_s_raw(s, 'spiro_fev1'), 10, 160)

    pts = 0
    factors = []
    age = _sat_age(patient.age)
    if age >= 80:
        pts += 4
        factors.append('ასაკი ≥80 (+4)')
    elif age >= 70:
        pts += 3
        factors.append('ასაკი 70–79 (+3)')
    elif age >= 60:
        pts += 2
        factors.append('ასაკი 60–69 (+2)')

    if q_filled and q.get('smoker'):
        pts += 1
        factors.append('თამბაქო (+1)')
    if bool(q.get('dm_insulin')):
        pts += 1
        factors.append('ინსულინი (+1)')
    if ihd:
        pts += 2
        factors.append('გიდ / CAD (+2)')
    if chf:
        pts += 2
        factors.append('გულის უკმარისობა (+2)')
    has_copd = (
        ('copd' in pulm_chips)
        or (q_filled and bool(q.get('pulm')) and not pulm_chips)
        or (s.get('cxr') == 'emphysema')
        or (spiro_fev1 is not None and spiro_fev1 < fev1_amb)
    )
    if has_copd:
        pts += 2
        factors.append('ფქოდ / COPD (+2)')
    if bool(q.get('on_dialysis')) or (scr_mgdl is not None and scr_mgdl >= vsg_cfg['scr_mgdl']):
        pts += 2
        factors.append('კრეატინინი ≥1.8 მგ/დლ / დიალიზი (+2)')
    if 'beta_blocker' in med_chips:
        pts += 1
        factors.append('მუდმივი β-ბლოკატორი (+1)')
    if (('cabg' in cad_chips) or ('stent_old' in cad_chips)) and not bool(q.get('recent_stent')):
        pts = max(0, pts - 1)
        factors.append('წარსულში რევასკულარიზაცია CABG/PCI (-1)')

    # Exact published Bertges et al. 2010 (J Vasc Surg) 6-category MACE probabilities:
    rates = vsg_cfg['rates']
    if pts <= 3:
        tier, est, lbl = 'low', rates['0_3'], f"დაბალი ({rates['0_3']})"
    elif pts == 4:
        tier, est, lbl = 'low', rates['4'], f"დაბალი/საშუალო ({rates['4']})"
    elif pts == 5:
        tier, est, lbl = 'intermediate', rates['5'], f"საშუალო ({rates['5']})"
    elif pts == 6:
        tier, est, lbl = 'intermediate', rates['6'], f"საშუალო ({rates['6']})"
    elif pts == 7:
        tier, est, lbl = 'high', rates['7'], f"მაღალი ({rates['7']})"
    else:
        tier, est, lbl = 'high', rates['ge8'], f"ძალიან მაღალი ({rates['ge8']})"
    return {'score': pts, 'tier': tier, 'risk_pct': est, 'tier_label': lbl, 'factors': factors}


def calc_ariscat_pulm(patient, q, s, hb_gl=None):
    """Validated ARISCAT postoperative pulmonary complication (PPC) score (Canet et al. 2010 / ESAIC 2024)."""
    q = q if isinstance(q, dict) else {}
    s = s if isinstance(s, dict) else {}
    ari_cfg = rule_value('SCORE_ARISCAT_CANET_2010')
    pathway = _resolve_pathway(patient)
    inf_chips = set(_str_list(q.get('inf_chips')))
    spo2 = _sat_num(_s_raw(s, 'spo2'), 30, 100)
    eff_hb = _sat_hb_gl(hb_gl) if hb_gl is not None else _sat_hb_gl(_s_raw(s, 'hb'))
    open_aortic = (
        (pathway == PreopPatient.Pathway.AAA_INFRA and patient.op_subtype != 'evar')
        or pathway == PreopPatient.Pathway.ABF
    )

    pts = 0
    factors = []
    age = _sat_age(patient.age)
    if age > 80:
        pts += 16
        factors.append('ასაკი >80 (+16)')
    elif age >= 51:
        pts += 3
        factors.append('ასაკი 51–80 (+3)')

    if spo2 is not None:
        spo2_i = int(round(spo2))
        if spo2 <= 90:
            pts += 24
            factors.append(f'SpO2 {spo2_i}% ≤90 (+24)')
        elif spo2 <= 95:
            pts += 8
            factors.append(f'SpO2 {spo2_i}% 91–95 (+8)')

    if ('fever_resp' in inf_chips) or (s.get('cxr') == 'infiltrate'):
        pts += 17
        factors.append('რესპირატორული ინფექცია / ინფილტრაცია (+17)')

    if eff_hb is not None and eff_hb <= ari_cfg['hb_max']:
        pts += 11
        factors.append('ანემია Hb ≤100 გ/ლ (+11)')

    if open_aortic:
        pts += 15
        factors.append('ზედა აბდომინალური / აორტული განაკვეთი (+15)')

    dur_est = s.get('surg_duration_est') or 'auto'
    if dur_est not in ('auto', 'lt_2h', '2_3h', 'gt_3h'):
        dur_est = 'auto'
    if dur_est == 'auto':
        dur_eff = 'gt_3h' if open_aortic else ('2_3h' if pathway in (PreopPatient.Pathway.CEA, PreopPatient.Pathway.PAD_BYPASS) else 'lt_2h')
        dur_auto = True
    else:
        dur_eff, dur_auto = dur_est, False

    auto_sfx = ' — ავტო' if dur_auto else ''
    if dur_eff == 'gt_3h':
        pts += 23
        factors.append(f'ოპერაციის ხანგრძლივობა >3 სთ{auto_sfx} (+23)')
    elif dur_eff == '2_3h':
        pts += 16
        factors.append(f'ოპერაციის ხანგრძლივობა 2–3 სთ{auto_sfx} (+16)')

    is_ali_ar = (
        pathway in (PreopPatient.Pathway.ABF, PreopPatient.Pathway.PAD_BYPASS, PreopPatient.Pathway.PAD_PTA)
        and q.get('abf_duration') == '< 2 კვირა'
    )
    if (
        s.get('asa_emergency')
        or (pathway == PreopPatient.Pathway.AAA_INFRA and q.get('aaa_pain'))
        or (pathway == PreopPatient.Pathway.CEA and q.get('cea_crescendo'))
        or is_ali_ar
    ):
        pts += 8
        factors.append('სასწრაფო/გადაუდებელი ჩვენება (+8)')

    if pts < ari_cfg['low_lt']:
        tier, ppc_pct, lbl = 'low', '1.6%', 'დაბალი (~1.6% PPC)'
    elif pts <= ari_cfg['int_max']:
        tier, ppc_pct, lbl = 'intermediate', '13.3%', 'საშუალო (~13.3% PPC)'
    else:
        tier, ppc_pct, lbl = 'high', '42.1%', 'მაღალი (~42.1% PPC)'
    return {'score': pts, 'tier': tier, 'ppc_pct': ppc_pct, 'tier_label': lbl, 'factors': factors}
