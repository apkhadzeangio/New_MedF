"""Clinical calculators, lab unit normalizers, risk indices (RCRI, VSG-CRI, ARISCAT, Caprini, ASA), and surveillance."""
from ..models import PreopPatient
from .meds import _effective_allergy_state
from .utils import (
    _int,
    _num,
    _resolve_pathway,
    _str_list,
    cea_live_days_since_event,
    is_pregnancy_eligible,
    is_symp_cea_patient,
)


def _sat_num(val, lo, hi):
    """Parse finite number and saturate out-of-range values to [lo, hi] for monotonic risk scoring."""
    v = _num(val)
    if v is None:
        return None
    return max(lo, min(hi, v))


def _sat_age(val):
    v = _num(val)
    if v is None or v < 1:
        return 0
    return int(round(min(v, 120)))


def _parse_plt_k(val):
    """Normalize PLT to x10^9/L (converts /uL >=3000 via /1000; saturates <=0 to 0 for monotonicity)."""
    v = _num(val)
    if v is None:
        return None
    if v < 0:
        return 0
    if v >= 3000:
        return int(round(v / 1000.0))
    return int(round(min(v, 2500)))


def _parse_scr_mgdl(creat, creat_unit=''):
    """Return (scr_mgdl, is_ambiguous). Rejects explicit unit mismatches and 15..35 auto-detect ambiguity."""
    from .registry import rule_value
    ren_u = rule_value('SCORE_RENAL_CKDEPI_CG')
    c = _num(creat, lo=0.1, hi=3000)
    if c is None:
        return None, False
    if creat_unit == 'mgdl':
        if c > ren_u['ambig_hi']:
            return None, True
        return c, False
    if creat_unit == 'umol':
        if c < ren_u['ambig_lo']:
            return None, True
        return c / ren_u['umol_factor'], False
    if 0.1 <= c <= ren_u['ambig_lo']:
        return c, False
    if c >= ren_u['ambig_hi']:
        return c / ren_u['umol_factor'], False
    return None, True


def _sat_scr_mgdl(creat, creat_unit=''):
    """Return scr_mgdl for risk scoring, saturating extreme >3000 umol/L to worst-case renal failure."""
    scr, ambig = _parse_scr_mgdl(creat, creat_unit)
    if scr is not None:
        return scr
    raw = _num(creat)
    if raw is not None and raw > 3000 and not ambig:
        return 3000.0 / 88.4
    return None


def calc_egfr(creat, age, sex='M', creat_unit='', on_dialysis=False):
    """CKD-EPI 2021 race-free eGFR (mL/min/1.73m2). Never guesses missing age/sex or ambiguous units."""
    if on_dialysis:
        return None
    scr_mgdl, _ = _parse_scr_mgdl(creat, creat_unit)
    age_v = _int(age, lo=12, hi=120)
    if scr_mgdl is None or age_v is None or sex not in ('M', 'F'):
        return None
    female = (sex == 'F')
    kappa = 0.7 if female else 0.9
    alpha = -0.241 if female else -0.302
    mult = 1.012 if female else 1.0
    ratio = scr_mgdl / kappa
    egfr = 142.0 * (min(ratio, 1.0) ** alpha) * (max(ratio, 1.0) ** -1.200) * (0.9938 ** age_v) * mult
    return int(round(egfr))


def calc_crcl(creat, age, sex='M', weight_kg=None, creat_unit='', on_dialysis=False, height_cm=None):
    """Cockcroft-Gault unindexed CrCl (mL/min) using Adjusted Body Weight in BMI >= 30 obesity."""
    if on_dialysis:
        return None
    scr_mgdl, _ = _parse_scr_mgdl(creat, creat_unit)
    w = _num(weight_kg, lo=25, hi=260)
    age_v = _int(age, lo=12, hi=120)
    if scr_mgdl is None or w is None or age_v is None or sex not in ('M', 'F'):
        return None
    h = _num(height_cm, lo=100, hi=230)
    eff_w = float(w)
    if h is not None:
        bmi_v = w / ((h / 100.0) ** 2)
        ibw = (50.0 if sex == 'M' else 45.5) + 0.9 * max(0.0, h - 152.4)
        if bmi_v >= 30.0 and w > ibw:
            eff_w = ibw + 0.4 * (w - ibw)
    crcl = ((140.0 - age_v) * eff_w) / (72.0 * scr_mgdl)
    if sex == 'F':
        crcl *= 0.85
    return int(round(crcl))


def calc_bmi(weight_kg, height_cm):
    """Body Mass Index (kg/m2) or None."""
    w = _num(weight_kg, lo=25, hi=260)
    h = _num(height_cm, lo=100, hi=230)
    if w is None or h is None:
        return None
    return round(w / ((h / 100.0) ** 2), 1)


def normalize_hb_gl(hb):
    """Return hemoglobin in g/L (converts g/dL ONLY in physiologic 3.5..22.0 range; 29 stays 29 g/L)."""
    v = _num(hb, lo=1, hi=260)
    if v is None:
        return None
    return round(v * 10, 1) if 3.5 <= v <= 22.0 else v


def normalize_glucose_mmol(gl):
    """Return glucose in mmol/L (converts mg/dL > 35.0 with 2-decimal precision for unit-boundary parity)."""
    from .registry import rule_value
    v = _num(gl, lo=0.5, hi=1000)
    if v is None:
        return None
    mgdl_cutoff = rule_value('GLY_UNIT_CONVERSION_MGDL')
    return round(v / 18.0, 2) if v > mgdl_cutoff else v


def normalize_albumin_gl(alb):
    """Return serum albumin in g/L (converts g/dL ONLY in 1.0..6.0 range)."""
    v = _num(alb, lo=0.5, hi=80)
    if v is None:
        return None
    return round(v * 10, 1) if 1.0 <= v <= 6.0 else v


def normalize_fibrinogen_gl(fib):
    """Return plasma fibrinogen in g/L (converts mg/dL > 15.0 via /100, e.g. 140 mg/dL -> 1.4 g/L)."""
    v = _num(fib, lo=0.2, hi=1500)
    if v is None:
        return None
    return round(v / 100.0, 2) if v > 15.0 else v


def normalize_ldl_mmol(ldl):
    """Return LDL cholesterol in mmol/L (converts mg/dL > 20.0 via /38.67, e.g. 130 mg/dL -> 3.36 mmol/L)."""
    v = _num(ldl, lo=0.2, hi=500)
    if v is None:
        return None
    return round(v / 38.67, 2) if v > 20.0 else v


def calc_max_contrast_ml(egfr, on_dialysis=False):
    """Safe iodinated contrast volume ceiling (mL) using contrast-to-eGFR ratio (ESVS / Gurm)."""
    if on_dialysis or egfr is None:
        return None
    if egfr < 30:
        return max(15, int(round(egfr * 1.0)))
    if egfr < 45:
        return int(round(egfr * 1.5))
    if egfr < 60:
        return int(round(egfr * 2.0))
    return min(250, int(round(egfr * 3.0)))


def _valid_bilateral_sbp(s):
    """Return max valid systolic BP across R/L arms, rejecting inverted readings (SBP <= DBP)."""
    bp_sys, bp_dia = _sat_num(s.get('bp_sys'), 30, 320), _sat_num(s.get('bp_dia'), 20, 220)
    bp_sys_l, bp_dia_l = _sat_num(s.get('bp_sys_l'), 30, 320), _sat_num(s.get('bp_dia_l'), 20, 220)
    r_inv = (bp_sys is not None and bp_dia is not None and bp_sys <= bp_dia)
    l_inv = (bp_sys_l is not None and bp_dia_l is not None and bp_sys_l <= bp_dia_l)
    valid = [v for v, inv in ((bp_sys, r_inv), (bp_sys_l, l_inv)) if v is not None and not inv]
    return max(valid, default=None)


def _caprini_points(patient, q, bmi, today, s_cur, cad_set, has_hit):
    pts = 0
    age = _sat_age(patient.age)
    if age >= 75:
        pts += 3
    elif age >= 61:
        pts += 2
    elif age >= 41:
        pts += 1

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

    bmi_v = _num(bmi, lo=8, hi=90)
    if bmi_v is not None and bmi_v > 25:
        pts += 1
    if (patient.sex or '') == 'F' and q.get('ven_hormones'):
        pts += 1
    if is_pregnancy_eligible(patient) and (q.get('ven_pregnant') or q.get('pregnant_possible')):
        pts += 1
    if q.get('pulm'):
        pts += 1

    cea_d = cea_live_days_since_event(q, today=today, s=s_cur)
    has_acute_stroke = (
        pw == PreopPatient.Pathway.CEA
        and cea_d is not None and cea_d <= 30
        and ('minor_stroke' in _str_list(q.get('cea_event_chips')) or bool(q.get('cea_disabling_stroke')))
    )
    if has_acute_stroke:
        pts += 5  # acute stroke <1 month (remote stroke >1m = 0 pts per Caprini 2005)

    if 'hf' in cad_set:
        pts += 1
    if 'mi' in cad_set:
        pts += 1
    if q.get('ven_cancer_immob'):
        pts += 2
    if q.get('ven_dvt_history'):
        pts += 3
    if q.get('ven_thrombophilia'):
        pts += 3
    if has_hit:
        pts += 3  # HIT is a +3 Caprini hypercoagulable state AND contraindicates all heparins!
    return pts


def _caprini_tier_and_advice(pts, has_mech_valve, has_hit, severe_renal, severe_liver, compression_contra):
    comp_sfx = '' if compression_contra else ' + კომპრესია'
    comp_low = ' (კომპრესია უკუნაჩვენებია არტერიული უკმარისობის გამო!)' if compression_contra else ' + ელასტიური კომპრესია'
    hit_dtis = (
        'IV ბივალირუდინი (არგატრობანი ღვიძლის ≥3x-ზე ერიდეთ/დოზა -75%!)'
        if severe_liver else 'IV ბივალირუდინი/არგატრობანი'
    )
    if pts >= 5:
        tier = 'მაღალი (≥5)'
        if has_mech_valve:
            if has_hit:
                advice = f'მექანიკური სარქველი + HIT: სტაციონარული {hit_dtis} და უვადო VKA (ჰეპარინი/LMWH და DOAC აკრძალულია!){comp_sfx}'
            elif severe_renal:
                advice = f'მექანიკური სარქველი + CrCl/eGFR <30: IV UFH ხიდი aPTT კონტროლით და უვადო VKA (DOAC აკრძალულია!){comp_sfx}'
            else:
                advice = f'მექანიკური სარქველი: ვარფარინის (VKA) აღდგენა ± LMWH ხიდი კარდიოლოგის სქემით (DOAC აკრძალულია!){comp_sfx}'
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
    elif pts >= 3:
        tier = 'საშუალო (3–4)'
        if has_mech_valve and has_hit:
            advice = f'მექანიკური სარქველი + HIT: {hit_dtis} და უვადო VKA (ჰეპარინი/LMWH და DOAC აკრძალულია!){comp_sfx}'
        elif has_mech_valve:
            advice = f'ადრეული მობილიზაცია{comp_sfx} + უვადო ვარფარინის (VKA) რეჟიმი (DOAC აკრძალულია!)'
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
    eff_ren_v = _num(eff_renal, lo=0, hi=300)
    severe_renal = bool(q_d.get('on_dialysis')) or (eff_ren_v is not None and eff_ren_v < 30)
    severe_liver = (liver_status or s_cur.get('liver_status') or 'normal') == '3x'
    tier, advice = _caprini_tier_and_advice(
        pts, 'mech_valve' in cad_set, has_hit, severe_renal, severe_liver, compression_contra,
    )
    return pts, tier, advice


def _sat_hb_gl(raw):
    hb_gl = normalize_hb_gl(raw)
    if hb_gl is not None:
        return hb_gl
    v = _num(raw)
    return 0.5 if (v is not None and v < 1) else None


def _sat_fib_gl(raw):
    fib = normalize_fibrinogen_gl(raw)
    if fib is not None:
        return fib
    v = _num(raw)
    return 0.1 if (v is not None and v < 0.2) else None


def calc_auto_asa(patient, q, s, egfr=None, bmi=None, today=None):
    """Estimate ASA Physical Status ('I'..'IV') deterministically from vascular pathway, comorbidities & acute vitals/labs."""
    from .registry import rule_value
    q = q if isinstance(q, dict) else {}
    s = s if isinstance(s, dict) else {}
    egfr_v = _num(egfr, lo=0, hi=300)
    if egfr_v is None and not q.get('on_dialysis'):
        sat_cr = _sat_scr_mgdl(s.get('creat'), s.get('creat_unit') or '')
        if sat_cr is not None and sat_cr >= 20.0:
            egfr_v = 2
    bmi_v = _num(bmi, lo=8, hi=90)
    pw = _resolve_pathway(patient)
    cad_chips = set(_str_list(q.get('cad_chips')))
    allergy_chips, _, _ = _effective_allergy_state(q)
    aaa_mm = _num(s.get('aaa_mm'), lo=10, hi=250)
    lvef = _sat_num(s.get('lvef'), 5, 95)
    fev1 = _sat_num(s.get('spiro_fev1'), 10, 160)
    hb_gl = _sat_hb_gl(s.get('hb'))
    gl_mmol = normalize_glucose_mmol(s.get('glucose'))
    hba1c = _num(s.get('hba1c'), lo=3.0, hi=25.0)
    spo2 = _sat_num(s.get('spo2'), 30, 100)
    eff_sbp = _valid_bilateral_sbp(s)
    raw_sbp_low = min([v for v in (_sat_num(s.get('bp_sys'), 30, 320), _sat_num(s.get('bp_sys_l'), 30, 320)) if v is not None], default=None)
    hr_val = _sat_num(s.get('hr'), 15, 260)
    k_val = _sat_num(s.get('k'), 1.0, 12.0)
    plt_val = _parse_plt_k(s.get('plt'))
    fib_val = _sat_fib_gl(s.get('fibrinogen'))
    inr_val = _sat_num(s.get('inr'), 0.4, 20.0)
    nt_probnp = _sat_num(s.get('nt_probnp'), 1, 100000)
    hs_ctn = _sat_num(s.get('hs_ctn'), 0.1, 50000)
    cea_d = cea_live_days_since_event(q, today=today, s=s)
    is_symp_cea = is_symp_cea_patient(patient, q, today=today, s=s, require_filled=False)

    if (
        bool(q.get('aaa_pain'))
        or (aaa_mm is not None and aaa_mm >= rule_value('AAA_GIANT_MM'))
        or (s.get('aaa_morphology') == 'inflammatory_mycotic')
        or bool(q.get('cea_crescendo'))
        or (is_symp_cea and (cea_d is None or cea_d <= 90))
        or bool(q.get('recent_stent'))
        or ('mech_valve' in cad_chips and bool(q.get('filled')) and (not q.get('med_warfarin') or 'hit_heparin' in allergy_chips))
        or (lvef is not None and lvef < 40)
        or bool(s.get('echo_valve_issue'))
        or (s.get('ecg') == 'ischemia_arrhythmia')
        or bool(q.get('abf_wet_infection'))
        or (fev1 is not None and fev1 < 50)
        or (spo2 is not None and spo2 < 90)
        or (eff_sbp is not None and eff_sbp < 90)
        or (eff_sbp is not None and eff_sbp <= 100 and hr_val is not None and hr_val >= eff_sbp)
        or (eff_sbp is None and raw_sbp_low is not None and raw_sbp_low <= 100 and hr_val is not None and hr_val >= 100 and hr_val >= raw_sbp_low)
        or (hr_val is not None and (hr_val >= 135 or hr_val < 40))
        or (k_val is not None and (k_val >= 6.0 or k_val < 3.0))
        or (hb_gl is not None and hb_gl < 70)
        or (plt_val is not None and plt_val < 50)
        or (fib_val is not None and fib_val < 1.2)
        or (egfr_v is not None and egfr_v < 15 and not q.get('on_dialysis'))
        or (s.get('tsh_status') == 'hypothyroid_severe')
        or (s.get('liver_status') == '3x' and inr_val is not None and inr_val > 1.5)
        or (bmi_v is not None and bmi_v < 16.0)
        or (nt_probnp is not None and nt_probnp >= 900)
        or (hs_ctn is not None and hs_ctn >= 52)
    ):
        return 'IV'

    if (
        pw != PreopPatient.Pathway.VENOUS or bool(q.get('on_dialysis'))
        or (egfr_v is not None and egfr_v < 60) or bool(q.get('cad')) or bool(cad_chips)
        or bool(q.get('neuro')) or (s.get('ecg') == 'afib_controlled')
        or bool(q.get('ven_dvt_history')) or bool(q.get('ven_cancer_immob'))
        or (s.get('liver_status') == '3x') or (hb_gl is not None and hb_gl < 100)
        or (hba1c is not None and hba1c >= 8.5) or (gl_mmol is not None and gl_mmol >= 16.7)
        or bool(q.get('dm_insulin')) or (bmi_v is not None and bmi_v >= 40)
        or bool(q.get('pacemaker')) or ('copd' in _str_list(q.get('pulm_chips')))
        or (spo2 is not None and spo2 < 94) or (eff_sbp is not None and eff_sbp >= 180)
        or (k_val is not None and (k_val >= 5.4 or k_val < 3.3)) or (q.get('cfs_level') == '5_plus')
    ):
        return 'III'

    if (
        bool(q.get('htn')) or bool(q.get('dm')) or bool(q.get('smoker'))
        or bool(q.get('pulm')) or bool(q.get('thyroid_disease'))
        or (is_pregnancy_eligible(patient) and (bool(q.get('ven_pregnant')) or bool(q.get('pregnant_possible'))))
        or (bmi_v is not None and bmi_v >= 30)
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
    lvef = _sat_num(s.get('lvef'), 5, 95)
    ihd = bool(
        q.get('recent_stent')
        or bool(cad_chips & {'mi', 'angina', 'cabg', 'stent_old', 'stent_6_12m'})
        or ecg == 'ischemia_arrhythmia'
        or (q_filled and bool(q.get('cad')) and not cad_chips)
    )
    chf = bool(
        ('hf' in cad_chips)
        or (lvef is not None and lvef < 40)
    )
    return ihd, chf


def calc_rcri(patient, q, s, is_symp_cea=False):
    """Validated 6-factor Revised Cardiac Risk Index (Lee et al., Circulation 1999 / ESC 2022)."""
    from .registry import rule_value
    q = q if isinstance(q, dict) else {}
    s = s if isinstance(s, dict) else {}
    rcri_cfg = rule_value('SCORE_RCRI_LEE_1999')
    pathway = _resolve_pathway(patient)
    q_filled = bool(q.get('filled'))
    ihd, chf = _ihd_and_chf_flags(q, s)
    scr_mgdl = _sat_scr_mgdl(s.get('creat'), s.get('creat_unit') or '')

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
    if q_filled and bool(q.get('dm_insulin')):
        factors.append('ინსულინოთერაპია')
    if bool(q.get('on_dialysis')) or (scr_mgdl is not None and scr_mgdl > rcri_cfg['scr_mgdl']):
        factors.append('კრეატინინი >2.0 მგ/დლ (>177 მკმოლ/ლ) / დიალიზი')

    score = len(factors)
    if score == 0:
        mace_pct, tier, tier_label = '0.4%', 'low', 'დაბალი (~0.4% MACE)'
    elif score == 1:
        mace_pct, tier, tier_label = '0.9%', 'low', 'დაბალი (~0.9% MACE)'
    elif score == 2:
        mace_pct, tier, tier_label = '6.6%', 'intermediate', 'საშუალო (~6.6% MACE)'
    else:
        mace_pct, tier, tier_label = '≥11.0%', 'high', 'მაღალი (≥11.0% MACE)'
    return {
        'score': score,
        'mace_pct': mace_pct,
        'tier': tier,
        'tier_label': tier_label,
        'factors': factors,
    }


def calc_vsg_cri(patient, q, s):
    """Vascular Study Group of New England Cardiac Risk Index (Bertges et al., J Vasc Surg 2010 — exact published MACE table)."""
    from .registry import rule_value
    q = q if isinstance(q, dict) else {}
    s = s if isinstance(s, dict) else {}
    vsg_cfg = rule_value('SCORE_VSG_CRI_BERTGES_2010')
    pathway = _resolve_pathway(patient)
    if pathway == PreopPatient.Pathway.VENOUS:
        return None
    q_filled = bool(q.get('filled'))
    cad_chips = set(_str_list(q.get('cad_chips')))
    pulm_chips = set(_str_list(q.get('pulm_chips')))
    med_chips = set(_str_list(q.get('med_chips')))
    ihd, chf = _ihd_and_chf_flags(q, s)
    scr_mgdl = _sat_scr_mgdl(s.get('creat'), s.get('creat_unit') or '')
    spiro_fev1 = _sat_num(s.get('spiro_fev1'), 10, 160)

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
    if q_filled and q.get('dm_insulin'):
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
        or (spiro_fev1 is not None and spiro_fev1 < 70)
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
    if (('cabg' in cad_chips) or ('stent_old' in cad_chips)) and not (q_filled and q.get('recent_stent')):
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
    return {
        'score': pts,
        'tier': tier,
        'risk_pct': est,
        'tier_label': lbl,
        'factors': factors,
    }


def calc_ariscat_pulm(patient, q, s, hb_gl=None):
    """Validated ARISCAT postoperative pulmonary complication (PPC) score (Canet et al. 2010 / ESAIC 2024)."""
    from .registry import rule_value
    q = q if isinstance(q, dict) else {}
    s = s if isinstance(s, dict) else {}
    ari_cfg = rule_value('SCORE_ARISCAT_CANET_2010')
    pathway = _resolve_pathway(patient)
    q_filled = bool(q.get('filled'))
    inf_chips = set(_str_list(q.get('inf_chips')))
    spo2 = _sat_num(s.get('spo2'), 30, 100)
    eff_hb = hb_gl if hb_gl is not None else _sat_hb_gl(s.get('hb'))
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

    if (q_filled and 'fever_resp' in inf_chips) or (s.get('cxr') == 'infiltrate'):
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
        if open_aortic:
            dur_eff, dur_auto = 'gt_3h', True
        elif pathway in (PreopPatient.Pathway.CEA, PreopPatient.Pathway.PAD_BYPASS):
            dur_eff, dur_auto = '2_3h', True
        else:
            dur_eff, dur_auto = 'lt_2h', True
    else:
        dur_eff, dur_auto = dur_est, False

    auto_sfx = ' — ავტო' if dur_auto else ''
    if dur_eff == 'gt_3h':
        pts += 23
        factors.append(f'ოპერაციის ხანგრძლივობა >3 სთ{auto_sfx} (+23)')
    elif dur_eff == '2_3h':
        pts += 16
        factors.append(f'ოპერაციის ხანგრძლივობა 2–3 სთ{auto_sfx} (+16)')

    if s.get('asa_emergency') or (q_filled and (q.get('aaa_pain') or q.get('cea_crescendo'))):
        pts += 8
        factors.append('სასწრაფო/გადაუდებელი ჩვენება (+8)')

    if pts < ari_cfg['low_lt']:
        tier, ppc_pct, lbl = 'low', '1.6%', 'დაბალი (~1.6% PPC)'
    elif pts <= ari_cfg['int_max']:
        tier, ppc_pct, lbl = 'intermediate', '13.3%', 'საშუალო (~13.3% PPC)'
    else:
        tier, ppc_pct, lbl = 'high', '42.1%', 'მაღალი (~42.1% PPC)'
    return {
        'score': pts,
        'tier': tier,
        'ppc_pct': ppc_pct,
        'tier_label': lbl,
        'factors': factors,
    }
