"""Free-text medication/allergy re-exports and perioperative medication hold/continue scheduler."""
import datetime

from ..models import PreopPatient
from .meds_freetext import (
    _ASA_WORD_RE,
    _ASPIRIN_FT_STEMS,
    _RIVA_LOW_RE,
    _detect_freetext_allergies,
    _detect_freetext_high_risk_meds,
    _effective_allergy_state,
)
from .utils import _date, _int, _num, _resolve_pathway, _str_list, cea_live_days_since_event, is_pregnancy_eligible

__all__ = [
    '_ASA_WORD_RE', '_ASPIRIN_FT_STEMS', '_RIVA_LOW_RE',
    '_detect_freetext_allergies', '_detect_freetext_high_risk_meds', '_effective_allergy_state',
    'build_med_schedule',
]


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
    from .registry import rule_value
    on_dialysis = bool(q.get('on_dialysis'))
    def _parse_renal(raw, hi):
        v = _num(raw)
        if v is None:
            return None
        return 1.0 if v < 0 else min(v, hi)
    egfr_v = _parse_renal(egfr, 300)
    crcl_v = _parse_renal(crcl, 2000)
    egfr = int(round(egfr_v)) if egfr_v is not None else None
    crcl = int(round(crcl_v)) if crcl_v is not None else None
    renal_vals = [v for v in (egfr, crcl) if v is not None]
    eff_renal = None if on_dialysis else (min(renal_vals) if renal_vals else None)
    if on_dialysis:
        cl_label = 'დიალიზი'
    elif crcl is not None and (egfr is None or crcl <= egfr):
        cl_label = f'CrCl {crcl}'
    elif egfr is not None:
        cl_label = f'eGFR {egfr}'
    else:
        cl_label = ''
    age_m = _int(patient.age, lo=1, hi=120) or 0
    frail_age = rule_value('ANES_AIRWAY_STOPBANG_ICU')['delirium_age']
    if crcl is None and egfr is not None:
        frail_no_wt_note = f' [≥{frail_age} წ. — შეიყვანეთ წონა ზუსტი CrCl-ისთვის!]' if age_m >= frail_age else ' [შეიყვანეთ წონა ზუსტი CrCl-ისთვის!]'
    else:
        frail_no_wt_note = ''
    return on_dialysis, eff_renal, cl_label, frail_no_wt_note


def _med_schedule_ctx(patient, q, s, egfr, crcl, has_neuraxial_override, today=None):
    from .calculators import _parse_scr_mgdl, _s_raw, _sat_num, is_creat_unit_mismatch
    from .registry import rule_value
    from .utils import has_prior_stroke_or_tia, is_symp_cea_patient
    pw = _resolve_pathway(patient)
    is_evla = (patient.op_subtype == PreopPatient.OpSubtype.EVLA)
    is_phleb = (patient.op_subtype == PreopPatient.OpSubtype.PHLEB)
    is_pta = (pw == PreopPatient.Pathway.PAD_PTA)
    cea_d = cea_live_days_since_event(q, today=today, s=s) if pw == PreopPatient.Pathway.CEA else None
    is_symp_cea = is_symp_cea_patient(patient, q, today=today, s=s, require_filled=False)
    has_stroke_hx = bool(is_symp_cea or has_prior_stroke_or_tia(patient, q=q, s=s))
    is_open_major = pw in (
        PreopPatient.Pathway.AAA_INFRA, PreopPatient.Pathway.ABF, PreopPatient.Pathway.PAD_BYPASS
    ) and patient.op_subtype != 'evar'
    is_ia_first_pass = patient.op_subtype in ('evar', 'tbd')
    uses_contrast = is_ia_first_pass or is_pta
    anesth_plan = s.get('anesthesia_plan') or 'auto'
    has_epidural_line = 'epidural' in _str_list(s.get('anesth_lines_chips'))
    is_neuraxial = (
        bool(has_neuraxial_override) if has_neuraxial_override is not None
        else ((anesth_plan == 'neuraxial') or has_epidural_line or (anesth_plan == 'auto' and is_phleb))
    )
    on_dialysis, eff_renal, cl_label, frail_no_wt_note = _med_renal_and_flags(patient, q, s, egfr, crcl)
    raw_cr_m, cr_u_m = _s_raw(s, 'creat'), s.get('creat_unit') or ''
    creat_mismatch = (not on_dialysis) and is_creat_unit_mismatch(raw_cr_m, cr_u_m)
    creat_ambig = (not on_dialysis) and (not creat_mismatch) and bool(_parse_scr_mgdl(raw_cr_m, cr_u_m)[1])
    creat_extreme_hi = (not on_dialysis) and (egfr is None) and ((_num(raw_cr_m) or 0) > 3000)
    op_dt = _date(s.get('planned_surgery_date'))
    liver_3x = (s.get('liver_status') == '3x')
    rows, d_str, hold_str, add_row = _make_med_row_helpers(op_dt)

    med_chips, cad_chips = set(_str_list(q.get('med_chips'))), set(_str_list(q.get('cad_chips')))
    ven_dvt_chips, vasc_chips = set(_str_list(q.get('ven_dvt_chips'))), set(_str_list(q.get('vasc_chips')))
    eff_allergy_chips, _, _ = _effective_allergy_state(q)
    ft_meds = _detect_freetext_high_risk_meds(q.get('other_meds') or '', q=q)

    has_mech_valve, has_acs_stent_12m = 'mech_valve' in cad_chips, ('stent_6_12m' in cad_chips) and ('mi' in cad_chips)
    bp_sys_cur, bp_inverted_only = _valid_med_sbp(s)
    hr_cur, lvef_cur = _sat_num(_s_raw(s, 'hr'), 15, 260), _sat_num(_s_raw(s, 'lvef'), 5, 95)
    nt_probnp_cur, k_cur = _sat_num(_s_raw(s, 'nt_probnp'), 1, 100000), _sat_num(_s_raw(s, 'k'), 1.0, 12.0)
    cilo_cfg, lvef_red_lim = rule_value('MED_CILOSTAZOL_BLACKBOX'), rule_value('CARDIO_BIOMARKERS_ECHO')['lvef_red']
    ren_red_lim = rule_value('REN_CKD_NEPHRO_HYDRATION')['red']
    lido_alg = bool(q.get('ven_lidocaine_allergy')) or ('lidocaine' in eff_allergy_chips)
    abi_vals = [v for v in (_num(s.get('abi_r'), 0, 3), _num(s.get('abi_l'), 0, 3)) if v is not None]
    min_abi = min(abi_vals) if abi_vals else None
    chf_any = ('hf' in cad_chips) or (lvef_cur is not None and lvef_cur < lvef_red_lim) or (nt_probnp_cur is not None and nt_probnp_cur >= cilo_cfg['bnp_max'])
    has_true_hfref = (lvef_cur is not None and lvef_cur < lvef_red_lim) or (('hf' in cad_chips) and lvef_cur is None)
    has_full_oac = bool(q.get('med_warfarin') or q.get('med_doac') or q.get('med_dabigatran'))
    p2y12_cnt = sum([bool(q.get('med_clopidogrel')), bool(q.get('med_ticagrelor')), bool(q.get('med_prasugrel'))])
    oac_count = sum([bool(q.get('med_warfarin')), bool(q.get('med_doac')), bool(q.get('med_dabigatran')), ('rivaroxaban_2_5' in med_chips)])
    total_at_cnt = oac_count + p2y12_cnt + (1 if q.get('med_aspirin') else 0) + (1 if 'cilostazol' in med_chips else 0)
    has_hit, severe_renal = 'hit_heparin' in eff_allergy_chips, on_dialysis or (eff_renal is not None and eff_renal < ren_red_lim)
    dup_oac_warn = ' [ყურადღება: ერთდროულად ≥2 ანტიკოაგულანტია მონიშნული — გადაამოწმეთ დუბლირება!]' if oac_count >= 2 else ''
    mech_doac_warn = ' [მექანიკურ სარქველზე აბსოლუტურად უკუნაჩვენებია (ESC Class III) — გადაამოწმეთ ვარფარინი!]' if has_mech_valve else ''
    liver_doac_warn = ' [ღვიძლის ≥3x დისფუნქცია: კოაგულოპათიის/Child-Pugh B–C დროს DOAC უკუნაჩვენებია (EHRA 2021)!]' if liver_3x else ''

    postop_oac_res = (
        ' · აღდგენა: ~24 სთ-ში ჰემოსტაზით' if (is_evla or is_pta)
        else (' · აღდგენა: თერაპ. დოზა 48–72 სთ-ში ჰემოსტაზით (ეპიდურული კათეტერიდან ≥24 სთ! HIT: ჰეპარინი/LMWH აკრძალულია!)' if has_hit
        else (' · აღდგენა: თერაპ. დოზა 48–72 სთ-ში ჰემოსტაზით (ეპიდურული კათეტერიდან ≥24 სთ! CrCl<30: UFH ან anti-Xa კორექტირებული LMWH)' if severe_renal
        else ' · აღდგენა: თერაპ. დოზა 48–72 სთ-ში ჰემოსტაზით (D+1 პროფილაქტ. LMWH; ეპიდურული კათეტერიდან ≥24 სთ!)'))
    )

    repro_pregnant = is_pregnancy_eligible(patient) and (bool(q.get('ven_pregnant')) or bool(q.get('pregnant_possible')))
    tl_days = rule_value('CEA_ACUTE_NEURO_THROMBOLYSIS')
    is_cea_ich = (pw == PreopPatient.Pathway.CEA and s.get('brain_imaging') == 'large_or_hemorrhage')
    is_cea_rtpa_6d = (pw == PreopPatient.Pathway.CEA and bool(q.get('cea_thrombolysis')) and (cea_d is None or cea_d <= tl_days))
    is_cea_rtpa_ap_stop = is_cea_rtpa_6d and not (cea_d is not None and cea_d >= 1 and s.get('brain_imaging') == 'normal_small')
    return {
        'patient': patient, 'q': q, 's': s, 'pw': pw, 'cea_d': cea_d,
        'is_evla': is_evla, 'is_phleb': is_phleb, 'is_pta': is_pta,
        'is_symp_cea': is_symp_cea, 'has_stroke_hx': has_stroke_hx, 'is_open_major': is_open_major,
        'is_ia_first_pass': is_ia_first_pass, 'uses_contrast': uses_contrast,
        'is_cea_ich': is_cea_ich, 'is_cea_bleed_stop': is_cea_ich or is_cea_rtpa_ap_stop,
        'is_cea_oac_p2y_stop': is_cea_ich or is_cea_rtpa_6d,
        'is_cea_rtpa_asa_caution': is_cea_rtpa_6d and not is_cea_ich and not is_cea_rtpa_ap_stop,
        'anesth_plan': anesth_plan, 'is_neuraxial': is_neuraxial,
        'on_dialysis': on_dialysis, 'eff_renal': eff_renal, 'renal_cl': eff_renal,
        'creat_mismatch': creat_mismatch, 'creat_ambig': creat_ambig, 'creat_extreme_hi': creat_extreme_hi,
        'cl_label': cl_label, 'frail_no_wt_note': frail_no_wt_note, 'severe_renal': severe_renal,
        'liver_3x': liver_3x, 'rows': rows, 'd_str': d_str, 'hold_str': hold_str, 'add_row': add_row,
        'med_chips': med_chips, 'cad_chips': cad_chips, 'ven_dvt_chips': ven_dvt_chips,
        'vasc_chips': vasc_chips, 'min_abi': min_abi, 'lido_alg': lido_alg, 'ven_dup': s.get('ven_duplex_status') or '',
        'eff_allergy_chips': eff_allergy_chips, 'ft_meds': ft_meds, 'has_hit': has_hit,
        'has_mech_valve': has_mech_valve, 'has_acs_stent_12m': has_acs_stent_12m,
        'has_full_oac': has_full_oac, 'p2y12_cnt': p2y12_cnt, 'total_at_cnt': total_at_cnt,
        'repro_pregnant': repro_pregnant,
        'bp_sys_cur': bp_sys_cur, 'bp_inverted_only': bp_inverted_only,
        'hr_cur': hr_cur, 'lvef_cur': lvef_cur, 'k_cur': k_cur,
        'chf_any': chf_any, 'has_true_hfref': has_true_hfref, 'oac_count': oac_count,
        'dup_oac_warn': dup_oac_warn,
        'doac_extra_warn': f'{mech_doac_warn}{dup_oac_warn}{liver_doac_warn}',
        'force_doac_red': bool(has_mech_valve or oac_count >= 2 or liver_3x),
        'postop_oac_res': postop_oac_res,
    }


def _cea_bleed_rule(c, label):
    if c['is_cea_ich']:
        return f'თავის ტვინის სისხლჩაქცევა / >1/3 MCA ინფარქტი — {label} დაუყოვნებლივ შეათანხმეთ ნეიროქირურგთან / ნევროლოგთან!'
    if c['cea_d'] is not None and c['cea_d'] >= 1 and c['s'].get('brain_imaging') == 'normal_small':
        return f'თრომბოლიზისი (rt-PA, 1–6 დღე; მე-{c["cea_d"]} დღე) — ნაადრევი სისტემური ანტიკოაგულაცია/P2Y12 საშიშია ჰემორაგიული ტრანსფორმაციის გამო, {label} დაუყოვნებლივ შეათანხმეთ ნევროლოგთან!'
    if c['cea_d'] is not None and c['cea_d'] >= 1:
        return f'თრომბოლიზისი (rt-PA, 1–6 დღე; მე-{c["cea_d"]} დღე) — საკონტროლო თავის ტვინის CT-ით სისხლჩაქცევის გამორიცხვამდე {label} დაუყოვნებლივ შეათანხმეთ ნევროლოგთან!'
    return f'თრომბოლიზიდან (rt-PA) <24 სთ — {label} დაუყოვნებლივ შეათანხმეთ ნევროლოგთან (საკონტროლო CT-მდე უკუნაჩვენებია)!'


def _append_antiplatelet_rows(c):
    from .registry import rule_value
    asa_cfg = rule_value('MED_ASPIRIN_CONTINUE')
    p2y_cfg = rule_value('MED_P2Y12_HOLD_DAYS')
    cilo_cfg = rule_value('MED_CILOSTAZOL_BLACKBOX')
    triple_min = rule_value('MED_ANTITHROMBOTIC_CONFLICTS')
    pad_abi_lo = rule_value('PAD_IC_BMT_3M')[0]
    car_mod_min = rule_value('CEA_STENOSIS_THRESHOLDS')['symp_min']
    q, s, pw = c['q'], c['s'], c['pw']
    add_row, d_str, hold_str = c['add_row'], c['d_str'], c['hold_str']
    is_phleb, is_evla, is_pta, is_symp_cea, is_neuraxial = c['is_phleb'], c['is_evla'], c['is_pta'], c['is_symp_cea'], c['is_neuraxial']
    has_acs_stent_12m, eff_allergy_chips = c['has_acs_stent_12m'], c['eff_allergy_chips']
    has_vasc_pad = bool(
        q.get('claudication') or (c['min_abi'] is not None and c['min_abi'] <= pad_abi_lo)
        or c['vasc_chips'] or (_num(s.get('aaa_mm'), 15, 200) or 0) >= asa_cfg['aaa_ectasia_mm']
        or max(_num(s.get('carotid_r'), 0, 100) or 0, _num(s.get('carotid_l'), 0, 100) or 0) >= car_mod_min
    )
    has_cardiac_risk = bool(q.get('cad') or c['cad_chips'] or q.get('neuro') or q.get('recent_stent') or c['chf_any'] or s.get('ecg') == 'ischemia_arrhythmia' or has_vasc_pad)
    asa_allergy = 'nsaid' in eff_allergy_chips
    clop_intol = 'clopidogrel_intol' in eff_allergy_chips
    asa_sub_txt = (
        'ასპირინზე ალერგია — ალტერნატივა შეათანხმეთ ექიმთან!' if asa_allergy
        else ('ასპირინის ფონზე!' if q.get('med_aspirin') else 'ასპირინი 75–100 მგ-ით ჩანაცვლებით!')
    )

    if q.get('med_aspirin'):
        if c['is_cea_bleed_stop']:
            add_row('ასპირინი (75–100 მგ)', _cea_bleed_rule(c, 'ანტიაგრეგანტის რეჟიმი'), 'red', 'შეათანხმეთ ნეიროქირურგთან / ნევროლოგთან!', hold_or_skip=False, force_date_plan=True, contraindicated=True)
        elif asa_allergy:
            add_row('ასპირინი (75–100 მგ)', 'ასპირინზე / აასს-ზე ალერგია — პრეპარატი საშიშია, დაუყოვნებლივ შეცვალეთ ალტერნატივით ექიმთან!', 'red', 'შეცვალეთ ექიმთან (ალერგია!)', hold_or_skip=False, force_date_plan=True, contraindicated=True)
        elif c['is_cea_rtpa_asa_caution']:
            rtpa_asa_dp = f'გრძელდება ნევროლოგთან ({d_str(0)})' if d_str(0) else 'გრძელდება ნევროლოგთან'
            add_row('ასპირინი (75–100 მგ)', f'თრომბოლიზისი (rt-PA, 1–6 დღე; მე-{c["cea_d"]} დღე) — CT-ზე სისხლჩაქცევის გამორიცხვისას ასპირინი გრძელდება ნევროლოგთან შეთანხმებით!', 'amber', rtpa_asa_dp, hold_or_skip=False, force_date_plan=True)
        elif is_phleb and not has_cardiac_risk:
            asa_ph_d = asa_cfg['phleb_low_risk_days'] if isinstance(asa_cfg, dict) else 5
            add_row('ასპირინი (75–100 მგ)', f'შეთანხმება ქირურგთან (დაბალი კარდიორისკისას შესაძლოა მოიხსნას {asa_ph_d}–7 დღით ადრე)', 'amber', f'შეათანხმეთ ({d_str(-asa_ph_d)}-დან?)', hold_or_skip=True, reconcile_only=True)
        else:
            asa_cont_d = asa_cfg['continue_days'] if isinstance(asa_cfg, dict) else 0
            t3_warn = ' [≥3 ანტითრომბოზული — შეათანხმეთ დე-ესკალაცია ქირურგთან!]' if c['total_at_cnt'] >= triple_min else ''
            add_row('ასპირინი (75–100 მგ)', f'გრძელდება უწყვეტად ოპერაციის დილის ჩათვლით (ESVS Class I){t3_warn}', 'amber' if t3_warn else 'ok', f'მიიღეთ {d_str(asa_cont_d)}-ის დილის ჩათვლით', hold_or_skip=False)

    if q.get('med_clopidogrel'):
        clop_intol_warn = ' [კლოპიდოგრელის აუტანლობა/ალერგია მონიშნულია — დაუყოვნებლივ გადაამოწმეთ დანიშნულება!]' if clop_intol else ''
        dup_p2y_red = c['p2y12_cnt'] >= 2
        dup_p2y_warn = ' [ყურადღება: ≥2 P2Y12 დუბლირება — გადაამოწმეთ სქემა!]' if dup_p2y_red else ''
        t3_clop_warn = ' [≥3 ანტითრომბოზული — შეათანხმეთ დე-ესკალაცია ქირურგთან!]' if c['total_at_cnt'] >= triple_min else ''
        if c['is_cea_oac_p2y_stop']:
            add_row('კლოპიდოგრელი / P2Y12', f'{_cea_bleed_rule(c, "თვითნებურად არ გააგრძელოთ,")}{clop_intol_warn}', 'red', 'შეათანხმეთ ნეიროქირურგთან / ნევროლოგთან!', hold_or_skip=False, force_date_plan=True, contraindicated=True)
        elif clop_intol:
            add_row('კლოპიდოგრელი / P2Y12', f'კლოპიდოგრელის აუტანლობა/ალერგია — პრეპარატი საშიშია, დაუყოვნებლივ შეცვალეთ ალტერნატივით ექიმთან!{clop_intol_warn}', 'red', 'შეცვალეთ ექიმთან (ალერგია!)', hold_or_skip=False, force_date_plan=True, contraindicated=True)
        elif q.get('recent_stent') or has_acs_stent_12m:
            stent_note = 'სტენტი <6 თვ' if q.get('recent_stent') else 'ინფარქტის შემდგომი სტენტი 6–12 თვ (DAPT 12 თვე)'
            add_row('კლოპიდოგრელი / P2Y12', f'{stent_note} — თვითნებური მოხსნა აკრძალულია! კარდიოლოგ-ქირურგის კონსილიუმი{dup_p2y_warn}', 'red', 'არ მოხსნათ კონსილიუმის გარეშე!', hold_or_skip=False, force_date_plan=True, contraindicated=dup_p2y_red)
        elif is_symp_cea:
            add_row('კლოპიდოგრელი / P2Y12', f'გრძელდება უწყვეტად CEA-მდე! (ESVS 2023 — სიმპტომურ CEA-ზე მოხსნა ზრდის ინსულტის რისკს){dup_p2y_warn}{t3_clop_warn}', 'red' if dup_p2y_red else ('amber' if t3_clop_warn else 'ok'), f'მიიღეთ {d_str(0)}-ის ჩათვლით', hold_or_skip=False, contraindicated=dup_p2y_red)
        elif is_pta:
            add_row('კლოპიდოგრელი / P2Y12', f'გრძელდება უწყვეტად (DAPT რეჟიმი სტენტირებისთვის){dup_p2y_warn}{t3_clop_warn}', 'red' if dup_p2y_red else ('amber' if t3_clop_warn else 'ok'), f'მიიღეთ {d_str(0)}-ის ჩათვლით', hold_or_skip=False, contraindicated=dup_p2y_red)
        elif is_evla and not is_neuraxial and not dup_p2y_red:
            add_row('კლოპიდოგრელი / P2Y12', 'ESVS 2022: EVLA შესაძლებელია მოუხსნელადაც — დააზუსტეთ ქირურგთან', 'amber', f'დააზუსტეთ ქირურგთან ({d_str(0)})', hold_or_skip=True, reconcile_only=True)
        elif asa_allergy and (pw != PreopPatient.Pathway.VENOUS or q.get('cad') or c['cad_chips'] or q.get('neuro')):
            clop_cite = 'ESVS 2023 Rec 10' if pw == PreopPatient.Pathway.CEA else 'ESVS / ESC'
            add_row('კლოპიდოგრელი / P2Y12', f'ასპირინზე/აასს-ზე ალერგია ({clop_cite}) — კლოპიდოგრელის მონოთერაპია თვითნებურად არ მოხსნათ, შეათანხმეთ ქირურგთან!{dup_p2y_warn}', 'red', 'შეათანხმეთ ქირურგთან (ასპირინზე ალერგია!)', hold_or_skip=False, force_date_plan=True, contraindicated=dup_p2y_red)
        elif not q.get('med_aspirin') and (pw != PreopPatient.Pathway.VENOUS or q.get('cad') or c['cad_chips'] or q.get('neuro')):
            asra_tag = ' (ASRA სპინალურ/ეპიდურულამდე მინ. 5–7 დღე)' if is_neuraxial else ''
            hold_d = p2y_cfg['clop_nx'] if is_neuraxial else p2y_cfg['clop_std']
            add_row('კლოპიდოგრელი / P2Y12', f'შეჩერება ოპერაციამდე 5–7 დღით ადრე{asra_tag} — სავალდებულოა ჩანაცვლება ასპირინი 75–100 მგ-ით (უანტიაგრეგანტოდ დატოვება საშიშია!){dup_p2y_warn}', 'red' if dup_p2y_red else 'amber', hold_str(hold_d, ' (ასპირინით!)'), hold_or_skip=True, contraindicated=dup_p2y_red, mandatory_hold_days=hold_d)
        else:
            asra_tag = ' (ASRA ნეირაქსიალურ ბლოკადამდე მინ. 5–7 დღე)' if is_neuraxial else ''
            hold_d = p2y_cfg['clop_nx'] if is_neuraxial else p2y_cfg['clop_std']
            asa_sfx = ' (ასპირინის ფონზე)' if (q.get('med_aspirin') and not asa_allergy) else ''
            add_row('კლოპიდოგრელი / P2Y12', f'შეჩერება ოპერაციამდე 5–7 დღით ადრე{asra_tag}{asa_sfx}{dup_p2y_warn}', 'red' if dup_p2y_red else 'amber', hold_str(hold_d), hold_or_skip=True, contraindicated=dup_p2y_red, mandatory_hold_days=hold_d)

    _append_potent_p2y12_rows(c, p2y_cfg, asa_allergy, clop_intol, asa_sub_txt)
    _append_cilostazol_row(c, cilo_cfg)


def _append_potent_p2y12_rows(c, p2y_cfg, asa_allergy, clop_intol, asa_sub_txt):
    q, pw, med_chips = c['q'], c['pw'], c['med_chips']
    add_row, d_str, hold_str = c['add_row'], c['d_str'], c['hold_str']
    is_pta, is_symp_cea, is_neuraxial, has_acs_stent_12m = c['is_pta'], c['is_symp_cea'], c['is_neuraxial'], c['has_acs_stent_12m']
    is_bleed = c['is_cea_oac_p2y_stop']
    solo_p2y_asa_alg = asa_allergy and (not q.get('med_clopidogrel') or clop_intol) and (pw != PreopPatient.Pathway.VENOUS or q.get('cad') or c['cad_chips'] or q.get('neuro'))

    if q.get('med_ticagrelor'):
        tica_bad_combo = c['has_full_oac'] or c['p2y12_cnt'] >= 2 or ('rivaroxaban_2_5' in med_chips)
        if is_bleed:
            add_row('ტიკაგრელორი (ბრილინტა)', _cea_bleed_rule(c, 'ტიკაგრელორი'), 'red', 'შეათანხმეთ ნეიროქირურგთან / ნევროლოგთან!', hold_or_skip=False, force_date_plan=True, contraindicated=True)
        elif q.get('recent_stent') or has_acs_stent_12m:
            add_row('ტიკაგრელორი (ბრილინტა)', 'კორონარული სტენტი DAPT რეჟიმში — თვითნებური მოხსნა აკრძალულია! კარდიოლოგ-ქირურგის კონსილიუმი', 'red', 'არ მოხსნათ კონსილიუმის გარეშე!', hold_or_skip=False, force_date_plan=True, contraindicated=tica_bad_combo)
        elif is_symp_cea:
            cea_t_alt = 'გაგრძელება ან კონსილიუმით შერჩევა (ალერგიების გამო!)' if (asa_allergy and clop_intol) else ('გაგრძელება ან ასპირინზე გადაყვანა' if clop_intol else ('გაგრძელება ან კლოპიდოგრელზე გადაყვანა' if asa_allergy else 'გაგრძელება ან კლოპიდოგრელ/ასპირინზე გადაყვანა'))
            add_row('ტიკაგრელორი (ბრილინტა)', f'სიმპტომური CEA — თვითნებურად არ მოხსნათ! შეათანხმეთ ქირურგთან ({cea_t_alt})', 'red', 'არ მოხსნათ შეთანხმების გარეშე!', hold_or_skip=False, force_date_plan=True, contraindicated=tica_bad_combo)
        elif is_pta:
            rule_t = 'OAC/დუბლირებულ P2Y12-თან ერთად ESC Class III სისხლდენის რისკი — შეათანხმეთ დე-ესკალაცია კარდიოლოგთან!' if tica_bad_combo else 'გრძელდება ან შეთანხმდება ქირურგთან (ენდოვასკულური DAPT)'
            add_row('ტიკაგრელორი (ბრილინტა)', rule_t, 'red' if tica_bad_combo else 'ok', 'შეათანხმეთ დე-ესკალაცია!' if tica_bad_combo else f'მიიღეთ {d_str(0)}-ის ჩათვლით', hold_or_skip=False, force_date_plan=tica_bad_combo, contraindicated=tica_bad_combo)
        elif solo_p2y_asa_alg:
            add_row('ტიკაგრელორი (ბრილინტა)', 'ასპირინზე/აასს-ზე ალერგია — ტიკაგრელორის მონოთერაპია თვითნებურად არ მოხსნათ (უანტიაგრეგანტოდ დატოვება საშიშია)! შეათანხმეთ ქირურგთან/კარდიოლოგთან!', 'red', 'შეათანხმეთ ქირურგთან (ასპირინზე ალერგია!)', hold_or_skip=False, force_date_plan=True, contraindicated=tica_bad_combo)
        elif is_neuraxial:
            add_row('ტიკაგრელორი (ბრილინტა)', f'შეჩერება სპინალურ/ეპიდურულ ანესთეზიამდე 5–7 დღით ადრე (ASRA 5th Ed: 5–7 დღე) — {asa_sub_txt}', 'red' if (tica_bad_combo or asa_allergy) else 'amber', hold_str(p2y_cfg['tica_nx'], ' (5–7 დღე)'), hold_or_skip=True, contraindicated=tica_bad_combo, mandatory_hold_days=p2y_cfg['tica_nx'])
        else:
            add_row('ტიკაგრელორი (ბრილინტა)', f'შეჩერება ოპერაციამდე 3–5 დღით ადრე (ESC 2022 / FDA; {asa_sub_txt})', 'red' if (tica_bad_combo or asa_allergy) else 'amber', hold_str(p2y_cfg['tica_std']), hold_or_skip=True, contraindicated=tica_bad_combo, mandatory_hold_days=p2y_cfg['tica_std'])

    if q.get('med_prasugrel'):
        has_stroke_hx = c['has_stroke_hx']
        stroke_warn = ' [გადატანილ ინსულტ/TIA-ზე პრასუგრელი უკუნაჩვენებია — გადაამოწმეთ!]' if has_stroke_hx else ''
        pras_bad_combo = c['has_full_oac'] or c['p2y12_cnt'] >= 2 or ('rivaroxaban_2_5' in med_chips)
        pras_contra = bool(stroke_warn or pras_bad_combo)
        if is_bleed:
            add_row('პრასუგრელი (ეფიენტი)', f'{_cea_bleed_rule(c, "პრასუგრელი")}{stroke_warn}', 'red', 'შეათანხმეთ ნეიროქირურგთან / ნევროლოგთან!', hold_or_skip=False, force_date_plan=True, contraindicated=True)
        elif q.get('recent_stent') or has_acs_stent_12m:
            add_row('პრასუგრელი (ეფიენტი)', f'კორონარული სტენტი — თვითნებური მოხსნა აკრძალულია! კარდიოლოგ-ქირურგის კონსილიუმი{stroke_warn}', 'red', 'არ მოხსნათ კონსილიუმის გარეშე!', hold_or_skip=False, force_date_plan=True, contraindicated=pras_contra)
        elif has_stroke_hx:
            add_row('პრასუგრელი (ეფიენტი)', f'გადატანილი ინსულტის / TIA-ს ანამნეზში პრასუგრელი აბსოლუტურად უკუნაჩვენებია (FDA Boxed Warning) — დაუყოვნებლივ შეცვალეთ კარდიოლოგთან ({asa_sub_txt})!', 'red', 'უკუნაჩვენებია — დაუყოვნებლივ შეცვალეთ ექიმთან!', hold_or_skip=False, force_date_plan=True, contraindicated=True)
        elif is_pta and pras_bad_combo:
            add_row('პრასუგრელი (ეფიენტი)', f'OAC/დუბლირებულ P2Y12-თან ერთად ESC Class III სისხლდენის რისკი — შეათანხმეთ დე-ესკალაცია კარდიოლოგთან!{stroke_warn}', 'red', 'შეათანხმეთ დე-ესკალაცია!', hold_or_skip=False, force_date_plan=True, contraindicated=True)
        elif is_pta and not is_neuraxial and not stroke_warn and (clop_intol or solo_p2y_asa_alg):
            add_row('პრასუგრელი (ეფიენტი)', 'ენდოვასკულური PTA (ალერგიის/აუტანლობის ფონზე) — თვითნებურად არ მოხსნათ, შეათანხმეთ უსაფრთხო ალტერნატივა ქირურგთან/კარდიოლოგთან!', 'red', 'შეათანხმეთ კონსილიუმით!', hold_or_skip=False, force_date_plan=True, contraindicated=pras_contra)
        elif is_pta and not is_neuraxial and not stroke_warn:
            add_row('პრასუგრელი (ეფიენტი)', 'ენდოვასკულური PTA — შეათანხმეთ ქირურგთან (გაგრძელება ან კლოპიდოგრელზე დეესკალაცია)', 'amber', f'შეათანხმეთ ქირურგთან ({d_str(0)})', hold_or_skip=True, reconcile_only=True)
        elif solo_p2y_asa_alg and not stroke_warn:
            add_row('პრასუგრელი (ეფიენტი)', 'ასპირინზე/აასს-ზე ალერგია — პრასუგრელის მონოთერაპია თვითნებურად არ მოხსნათ (უანტიაგრეგანტოდ დატოვება საშიშია)! შეათანხმეთ ქირურგთან/კარდიოლოგთან!', 'red', 'შეათანხმეთ ქირურგთან (ასპირინზე ალერგია!)', hold_or_skip=False, force_date_plan=True, contraindicated=pras_contra)
        else:
            hold_d = p2y_cfg['pras_nx'] if is_neuraxial else p2y_cfg['pras_std']
            add_row('პრასუგრელი (ეფიენტი)', f'შეჩერება ოპერაციამდე და სპინალურ/ეპიდურულ ანესთეზიამდე 7–10 დღით ადრე (ASRA/ESC) — {asa_sub_txt}{stroke_warn}', 'red', hold_str(hold_d), hold_or_skip=True, contraindicated=pras_contra, mandatory_hold_days=hold_d)


_IMMED_STOP_PLAN = 'შეწყვიტეთ დაუყოვნებლივ (ექიმთან შეთანხმებით)!'


def _append_cilostazol_row(c, cilo_cfg):
    q, s, med_chips = c['q'], c['s'], c['med_chips']
    if 'cilostazol' not in med_chips:
        return
    cilostazol_contra = (
        c['is_cea_oac_p2y_stop'] or c['chf_any'] or (c['lvef_cur'] is not None and c['lvef_cur'] < cilo_cfg['lvef_min'])
        or c['on_dialysis'] or c['creat_extreme_hi'] or (c['eff_renal'] is not None and c['eff_renal'] <= cilo_cfg['crcl_min'])
        or (s.get('ecg') == 'ischemia_arrhythmia') or (c['hr_cur'] is not None and c['hr_cur'] > cilo_cfg['hr_max'])
        or c['liver_3x'] or bool(q.get('ulcer_active_symp')) or (s.get('gastro_result') == 'acute_ulcer')
        or bool(q.get('recent_stent')) or c['has_acs_stent_12m'] or c['repro_pregnant']
    )
    ppi_cyp_warn = ' [CYP2C19: ომეპრაზოლს მოერიდეთ — უპირატესია პანტოპრაზოლი]' if ('ppi' in med_chips or q.get('ulcer') or s.get('ppi_started') or c['ft_meds']['has_omeprazole']) else ''
    if cilostazol_contra:
        c['add_row']('ცილოსტაზოლი (პლეტალი)', f'უკუნაჩვენებია გულის უკმარისობის / LVEF <50% / CrCl ≤25 / ღვიძლის ≥3x / სტენტის / წყლულის / ტაქიარითმიის / სისხლჩაქცევის დროს (FDA Black Box / EMA) — შეათანხმეთ მოხსნა!{ppi_cyp_warn}', 'red', 'უკუნაჩვენებია — შეათანხმეთ მოხსნა!', hold_or_skip=False, force_date_plan=True, contraindicated=True)
    elif c['creat_mismatch']:
        c['add_row']('ცილოსტაზოლი (პლეტალი)', f'კრეატინინის ერთეული დასაზუსტებელია — CrCl >25 უსაფრთხოებისთვის გადაამოწმეთ µmol/L / mg/dL!{ppi_cyp_warn}', 'red', 'ერთეული დასაზუსტებელია', hold_or_skip=True, force_date_plan=True, contraindicated=True, reconcile_only=True)
    elif c['creat_ambig'] or c['eff_renal'] is None:
        c_msg = 'კრეატინინის ერთეული დასაზუსტებელია (15–35 ზონა) — CrCl >25 უსაფრთხოებისთვის მიუთითეთ µmol/L თუ mg/dL!' if c['creat_ambig'] else 'შეიყვანეთ კრეატინინი (CrCl / eGFR) — ცილოსტაზოლის უსაფრთხოებისთვის (CrCl >25) და 48 სთ შეჩერებამდე!'
        c_dp = 'ერთეული დასაზუსტებელია' if c['creat_ambig'] else 'შეიყვანეთ კრეატინინი თარიღისთვის'
        c['add_row']('ცილოსტაზოლი (პლეტალი)', f'{c_msg}{ppi_cyp_warn}', 'red', c_dp, hold_or_skip=True, force_date_plan=True, reconcile_only=True)
    else:
        c['add_row']('ცილოსტაზოლი (პლეტალი)', f'შეჩერება ოპერაციამდე / სპინალურ ბლოკადამდე 48 საათით (2 დღით) ადრე (ASRA){ppi_cyp_warn}', 'amber', c['hold_str'](cilo_cfg['hold_days'], ' (48 სთ)'), hold_or_skip=True, mandatory_hold_days=cilo_cfg['hold_days'])


def _append_doac_dabigatran_rows(c):
    from .registry import rule_value
    doac_cfg, dabi_cfg = rule_value('MED_DOAC_HOLD_INTERVALS'), rule_value('MED_DABIGATRAN_HOLD_INTERVALS')
    q, add_row, d_str, hold_str = c['q'], c['add_row'], c['d_str'], c['hold_str']
    has_mech_valve, is_evla, is_neuraxial, liver_3x = c['has_mech_valve'], c['is_evla'], c['is_neuraxial'], c['liver_3x']
    on_dialysis, renal_cl, cl_label, frail_no_wt_note = c['on_dialysis'], c['renal_cl'], c['cl_label'], c['frail_no_wt_note']
    dup_oac_warn, doac_extra_warn, force_doac_red = c['dup_oac_warn'], c['doac_extra_warn'], c['force_doac_red']
    contra_doac = bool(c['oac_count'] >= 2 or liver_3x)
    res_oac = '' if contra_doac else c['postop_oac_res']
    doac_low, doac_mid = doac_cfg['crcl_low'], doac_cfg['crcl_mid']
    dabi_low, dabi_mid, dabi_hi = dabi_cfg['crcl_low'], dabi_cfg['crcl_mid'], dabi_cfg['crcl_hi']

    if q.get('med_doac'):
        if c['is_cea_oac_p2y_stop']:
            add_row('DOAC (ქსარელტო / ელიქვისი)', _cea_bleed_rule(c, 'DOAC'), 'red', 'შეათანხმეთ ნეიროქირურგთან / ნევროლოგთან!', hold_or_skip=False, force_date_plan=True, contraindicated=True)
        elif has_mech_valve:
            add_row('DOAC (ქსარელტო / ელიქვისი)', f'მექანიკურ სარქველზე DOAC აბსოლუტურად უკუნაჩვენებია (ESC Class III — სარქვლის თრომბოზის რისკი)! სასწრაფოდ გადაამოწმეთ ვარფარინი (VKA) კარდიოლოგთან!{dup_oac_warn}', 'red', 'უკუნაჩვენებია — კარდიოლოგი!', hold_or_skip=False, force_date_plan=True, contraindicated=True, acute_hold=True)
        elif c['repro_pregnant']:
            add_row('DOAC (ქსარელტო / ელიქვისი)', f'ორსულობის დროს DOAC აბსოლუტურად უკუნაჩვენებია (პლაცენტური გადასვლა / ნაყოფის რისკი) — დაუყოვნებლივ შეცვალეთ LMWH/UFH-ით მეან-გინეკოლოგთან და კარდიოლოგთან!{dup_oac_warn}', 'red', _IMMED_STOP_PLAN, hold_or_skip=False, force_date_plan=True, contraindicated=True, acute_hold=True)
        elif on_dialysis:
            add_row('DOAC (ქსარელტო / ელიქვისი)', f'დიალიზი / თირკმლის ტერმინალური უკმარისობა — შეჩერება ≥96–120 სთ + ანტი-Xa კონტროლი და ნეფროლოგ/ქირურგის კონსილიუმი!{doac_extra_warn}', 'red', _IMMED_STOP_PLAN, hold_or_skip=True, force_date_plan=True, contraindicated=True, mandatory_hold_days=doac_cfg['dialysis_days'])
        elif c['creat_mismatch']:
            add_row('DOAC (ქსარელტო / ელიქვისი)', f'კრეატინინის ერთეული დასაზუსტებელია — DOAC-ის შეჩერების დროის (48–96+ სთ) დასადგენად გადაამოწმეთ µmol/L / mg/dL!{doac_extra_warn}', 'red', 'ერთეული დასაზუსტებელია', hold_or_skip=True, force_date_plan=True, contraindicated=True, reconcile_only=True)
        elif c['creat_extreme_hi'] or (renal_cl is not None and renal_cl < doac_low):
            cl_hdr = cl_label if cl_label else 'CrCl/eGFR'
            add_row('DOAC (ქსარელტო / ელიქვისი)', f'{cl_hdr} (<{doac_low}) — შეჩერება ≥96 სთ-ით ადრე + ნეფროლოგ/ქირურგთან შეთანხმება (ნეირაქსიალური ბლოკადა მოერიდეთ!){frail_no_wt_note}{doac_extra_warn}', 'red', _IMMED_STOP_PLAN, hold_or_skip=True, force_date_plan=True, contraindicated=True, mandatory_hold_days=doac_cfg['crcl_lt30_days'])
        elif renal_cl is None:
            cr_msg = 'კრეატინინის ერთეული დასაზუსტებელია (µmol/L თუ mg/dL) — DOAC-ის შეჩერების დრო (48–96+ სთ) დამოკიდებულია თირკმლის ფუნქციაზე!' if c['creat_ambig'] else 'შეიყვანეთ კრეატინინი (CrCl / eGFR) — DOAC-ის შეჩერების დრო (48–96+ სთ) დამოკიდებულია თირკმლის ფუნქციაზე!'
            cr_dp = _IMMED_STOP_PLAN if contra_doac else ('ერთეული დასაზუსტებელია' if c['creat_ambig'] else 'შეიყვანეთ კრეატინინი თარიღისთვის')
            add_row('DOAC (ქსარელტო / ელიქვისი)', f'{cr_msg}{doac_extra_warn}', 'red', cr_dp, hold_or_skip=True, force_date_plan=True, contraindicated=contra_doac, reconcile_only=True)
        elif is_evla and not is_neuraxial and not contra_doac:
            add_row('DOAC (ქსარელტო / ელიქვისი)', f'ESVS 2022: EVLA-ზე შესაძლებელია მოუხსნელად ან დილის დოზის გამოტოვებით{doac_extra_warn}{res_oac}', 'red' if force_doac_red else 'amber', f'გამოტოვეთ {d_str(0)}-ის დილას', hold_or_skip=True, contraindicated=contra_doac, reconcile_only=True)
        elif is_neuraxial:
            add_row('DOAC (ქსარელტო / ელიქვისი)', f'სპინალური/ეპიდურული ანესთეზია (ASRA 5th Ed): სავალდებულო შეჩერება მინიმუმ 72 საათით (3 დღით) ადრე! ({cl_label}){frail_no_wt_note}{doac_extra_warn}{res_oac}', 'red' if force_doac_red else 'amber', _IMMED_STOP_PLAN if contra_doac else hold_str(doac_cfg['neuraxial_days'], ' (72 სთ)'), hold_or_skip=True, force_date_plan=contra_doac, contraindicated=contra_doac, mandatory_hold_days=doac_cfg['neuraxial_days'])
        elif renal_cl < doac_mid:
            add_row('DOAC (ქსარელტო / ელიქვისი)', f'{cl_label} (30–49) — შეჩერება ოპერაციამდე 72 საათით (3 დღით) ადრე (EHRA 2021 / PAUSE){frail_no_wt_note}{doac_extra_warn}{res_oac}', 'red' if force_doac_red else 'amber', _IMMED_STOP_PLAN if contra_doac else hold_str(doac_cfg['crcl_30_49_days'], ' (72 სთ)'), hold_or_skip=True, force_date_plan=contra_doac, contraindicated=contra_doac, mandatory_hold_days=doac_cfg['crcl_30_49_days'])
        else:
            add_row('DOAC (ქსარელტო / ელიქვისი)', f'შეჩერება ოპერაციამდე 48 საათით (2 დღით) ადრე (EHRA 2021 / PAUSE; {cl_label}){frail_no_wt_note}{doac_extra_warn}{res_oac}', 'red' if force_doac_red else 'amber', _IMMED_STOP_PLAN if contra_doac else hold_str(doac_cfg['crcl_ge50_days'], ' (48 სთ)'), hold_or_skip=True, force_date_plan=contra_doac, contraindicated=contra_doac, mandatory_hold_days=doac_cfg['crcl_ge50_days'])

    if q.get('med_dabigatran'):
        d_map = dabi_cfg['nx'] if is_neuraxial else dabi_cfg['std']
        if c['is_cea_oac_p2y_stop']:
            add_row('დაბიგატრანი (პრადაქსა)', _cea_bleed_rule(c, 'დაბიგატრანი'), 'red', 'შეათანხმეთ ნეიროქირურგთან / ნევროლოგთან!', hold_or_skip=False, force_date_plan=True, contraindicated=True)
        elif has_mech_valve:
            add_row('დაბიგატრანი (პრადაქსა)', f'მექანიკურ სარქველზე დაბიგატრანი აბსოლუტურად უკუნაჩვენებია (RE-ALIGN / ESC Class III — თრომბოზის რისკი)! სასწრაფოდ გადაამოწმეთ ვარფარინი (VKA)!{dup_oac_warn}', 'red', 'უკუნაჩვენებია — კარდიოლოგი!', hold_or_skip=False, force_date_plan=True, contraindicated=True, acute_hold=True)
        elif c['repro_pregnant']:
            add_row('დაბიგატრანი (პრადაქსა)', f'ორსულობის დროს დაბიგატრანი აბსოლუტურად უკუნაჩვენებია (პლაცენტური გადასვლა / ნაყოფის რისკი) — დაუყოვნებლივ შეცვალეთ LMWH/UFH-ით მეან-გინეკოლოგთან და კარდიოლოგთან!{dup_oac_warn}', 'red', _IMMED_STOP_PLAN, hold_or_skip=False, force_date_plan=True, contraindicated=True, acute_hold=True)
        elif on_dialysis:
            add_row('დაბიგატრანი (პრადაქსა)', f'დიალიზი — დაბიგატრანი უკუნაჩვენებია (EHRA 2021), სავალდებულოა თრომბინის დროის (TT) კონტროლი და კონსილიუმი!{doac_extra_warn}', 'red', _IMMED_STOP_PLAN, hold_or_skip=True, force_date_plan=True, contraindicated=True, reconcile_only=True, mandatory_hold_days=d_map['lt30'])
        elif c['creat_mismatch']:
            add_row('დაბიგატრანი (პრადაქსა)', f'კრეატინინის ერთეული დასაზუსტებელია — პრადაქსას შეჩერებისა და უკუჩვენების დასადგენად გადაამოწმეთ µmol/L / mg/dL!{doac_extra_warn}', 'red', 'ერთეული დასაზუსტებელია', hold_or_skip=True, force_date_plan=True, contraindicated=True, reconcile_only=True)
        elif c['creat_extreme_hi'] or (renal_cl is not None and renal_cl < dabi_low):
            nx_tag = 'სპინალური/ეპიდურული ბლოკადა უკუნაჩვენებია (ASRA 5th Ed)! ' if is_neuraxial else ''
            cl_hdr = cl_label if cl_label else 'CrCl/eGFR'
            add_row('დაბიგატრანი (პრადაქსა)', f'{cl_hdr} (<{dabi_low}) — დაბიგატრანი უკუნაჩვენებია (EMA/EHRA 2021)! {nx_tag}შეჩერება ≥120 საათით + TT კონტროლი და ნეფროლოგ/კარდიოლოგთან ჩანაცვლება!{frail_no_wt_note}{doac_extra_warn}', 'red', _IMMED_STOP_PLAN, hold_or_skip=True, force_date_plan=True, contraindicated=True, mandatory_hold_days=d_map['lt30'])
        elif renal_cl is None:
            cr_dmsg = 'კრეატინინის ერთეული დასაზუსტებელია (µmol/L თუ mg/dL) — პრადაქსას შეჩერების დრო (48–120 სთ) მკაცრად დამოკიდებულია თირკმლის ფუნქციაზე!' if c['creat_ambig'] else 'შეიყვანეთ კრეატინინი და წონა (CrCl) — პრადაქსას შეჩერების დრო (48–120 სთ) მკაცრად დამოკიდებულია თირკმლის ფუნქციაზე!'
            cr_ddp = _IMMED_STOP_PLAN if contra_doac else ('ერთეული დასაზუსტებელია' if c['creat_ambig'] else 'შეიყვანეთ CrCl თარიღისთვის')
            add_row('დაბიგატრანი (პრადაქსა)', f'{cr_dmsg}{doac_extra_warn}', 'red', cr_ddp, hold_or_skip=True, force_date_plan=True, contraindicated=contra_doac, reconcile_only=True)
        elif is_evla and not is_neuraxial and not contra_doac:
            add_row('დაბიგატრანი (პრადაქსა)', f'ESVS 2022: EVLA-ზე შესაძლებელია მოუხსნელად ან დილის დოზის გამოტოვებით — დააზუსტეთ ქირურგთან{doac_extra_warn}{res_oac}', 'red' if force_doac_red else 'amber', f'გამოტოვეთ {d_str(0)}-ის დილას', hold_or_skip=True, contraindicated=contra_doac, reconcile_only=True)
        else:
            tier_k = '30_49' if renal_cl < dabi_mid else ('50_79' if renal_cl < dabi_hi else 'ge80')
            hrs = d_map[tier_k] * 24
            tier_rng = '30–49' if tier_k == '30_49' else ('50–79' if tier_k == '50_79' else '≥80')
            pfx = f'სპინალური/ეპიდურული (ASRA, {cl_label} {tier_rng}): შეჩერება ' if is_neuraxial else f'{cl_label} ({tier_rng}) — შეჩერება ოპერაციამდე '
            tone_d = 'red' if (force_doac_red or tier_k == '30_49') else 'amber'
            add_row('დაბიგატრანი (პრადაქსა)', f'{pfx}{hrs} საათით ({d_map[tier_k]} დღით) ადრე{frail_no_wt_note}{doac_extra_warn}{res_oac}', tone_d, _IMMED_STOP_PLAN if contra_doac else hold_str(d_map[tier_k], f' ({hrs} სთ)'), hold_or_skip=True, force_date_plan=contra_doac, contraindicated=contra_doac, mandatory_hold_days=d_map[tier_k])


def _append_rivaroxaban_row(c):
    from .registry import rule_value
    voy_cfg, triple_min = rule_value('MED_VOYAGER_RIVAROXABAN_2_5'), rule_value('MED_ANTITHROMBOTIC_CONFLICTS')
    q, s, add_row, d_str, hold_str = c['q'], c['s'], c['add_row'], c['d_str'], c['hold_str']
    if 'rivaroxaban_2_5' not in c['med_chips']:
        return
    potent_p2y_riva = bool(q.get('med_ticagrelor') or q.get('med_prasugrel'))
    contra_riva = bool(c['oac_count'] >= 2 or c['liver_3x'] or potent_p2y_riva)
    riva_red = c['force_doac_red'] or potent_p2y_riva
    doac_extra_warn, renal_cl = c['doac_extra_warn'], c['renal_cl']
    if c['is_cea_oac_p2y_stop']:
        add_row('ქსარელტო 2.5 მგ x2', _cea_bleed_rule(c, 'რივაროქსაბანი 2.5 მგ'), 'red', 'შეათანხმეთ ნეიროქირურგთან / ნევროლოგთან!', hold_or_skip=False, force_date_plan=True, contraindicated=True)
    elif c['has_mech_valve']:
        add_row('ქსარელტო 2.5 მგ x2', f'მექანიკურ სარქველზე რივაროქსაბანი აბსოლუტურად უკუნაჩვენებია (ESC Class III)! გადაამოწმეთ ვარფარინი!{c["dup_oac_warn"]}', 'red', _IMMED_STOP_PLAN, hold_or_skip=False, force_date_plan=True, contraindicated=True, acute_hold=True)
    elif c['repro_pregnant']:
        add_row('ქსარელტო 2.5 მგ x2', f'ორსულობის დროს რივაროქსაბანი 2.5 მგ ×2 (VOYAGER PAD) აბსოლუტურად უკუნაჩვენებია — დაუყოვნებლივ შეწყვიტეთ და შეათანხმეთ მეან-გინეკოლოგთან!{doac_extra_warn}', 'red', _IMMED_STOP_PLAN, hold_or_skip=False, force_date_plan=True, contraindicated=True, acute_hold=True)
    elif 'nsaid' in c['eff_allergy_chips']:
        add_row('ქსარელტო 2.5 მგ x2', f'ასპირინზე / აასს-ზე ალერგია — VOYAGER PAD რეჟიმი უკუნაჩვენებია (მოითხოვს ასპირინს)! დაუყოვნებლივ შეცვალეთ ალტერნატივით ქირურგთან!{doac_extra_warn}', 'red', _IMMED_STOP_PLAN, hold_or_skip=False, force_date_plan=True, contraindicated=True, acute_hold=True)
    elif q.get('ulcer_active_symp') or s.get('gastro_result') == 'acute_ulcer':
        add_row('ქსარელტო 2.5 მგ x2', f'აქტიური კუჭ-ნაწლავის სისხლდენის / მწვავე წყლულის დროს რივაროქსაბანი 2.5 მგ ×2 (VOYAGER PAD) აბსოლუტურად უკუნაჩვენებია — შეწყვიტეთ დაუყოვნებლივ და შეათანხმეთ ქირურგთან!{doac_extra_warn}', 'red', _IMMED_STOP_PLAN, hold_or_skip=False, force_date_plan=True, contraindicated=True, acute_hold=True)
    elif c['has_stroke_hx']:
        add_row('ქსარელტო 2.5 მგ x2', f'გადატანილი ინსულტის / TIA-ს ანამნეზში რივაროქსაბანი 2.5 მგ ×2 (VOYAGER PAD) უკუნაჩვენებია (ქალასშიდა სისხლდენის რისკი) — შეათანხმეთ მოხსნა/შეცვლა ქირურგთან!{doac_extra_warn}', 'red', _IMMED_STOP_PLAN, hold_or_skip=False, force_date_plan=True, contraindicated=True, acute_hold=True)
    elif c['creat_mismatch']:
        add_row('ქსარელტო 2.5 მგ x2', f'კრეატინინის ერთეული დასაზუსტებელია — რივაროქსაბანის შეჩერებისა და უსაფრთხოებისთვის გადაამოწმეთ µmol/L / mg/dL!{doac_extra_warn}', 'red', 'ერთეული დასაზუსტებელია', hold_or_skip=True, force_date_plan=True, contraindicated=True, reconcile_only=True)
    elif c['on_dialysis'] or c['creat_extreme_hi'] or (renal_cl is not None and renal_cl < voy_cfg['min_egfr']):
        add_row('ქსარელტო 2.5 მგ x2', f'თირკმლის მძიმე უკმარისობა / დიალიზი — შეჩერება ≥72–96 სთ-ით ადრე (ASRA 5th Ed დაბალ-დოზიანი ≥30 სთ) და სქემის გადახედვა ქირურგთან!{doac_extra_warn}', 'red', _IMMED_STOP_PLAN, hold_or_skip=True, force_date_plan=True, contraindicated=True, mandatory_hold_days=voy_cfg['renal_days'])
    elif renal_cl is None:
        cr_rmsg = 'კრეატინინის ერთეული დასაზუსტებელია (µmol/L თუ mg/dL) — რივაროქსაბანის შეჩერების დრო დამოკიდებულია თირკმლის ფუნქციაზე!' if c['creat_ambig'] else 'შეიყვანეთ კრეატინინი (CrCl / eGFR) — რივაროქსაბანის შეჩერების დრო დამოკიდებულია თირკმლის ფუნქციაზე!'
        cr_rdp = _IMMED_STOP_PLAN if contra_riva else ('ერთეული დასაზუსტებელია' if c['creat_ambig'] else 'შეიყვანეთ კრეატინინი თარიღისთვის')
        add_row('ქსარელტო 2.5 მგ x2', f'{cr_rmsg}{doac_extra_warn}', 'red', cr_rdp, hold_or_skip=True, force_date_plan=True, contraindicated=contra_riva, reconcile_only=True)
    elif c['is_evla'] and not c['is_neuraxial'] and not riva_red:
        t3_ev = c['total_at_cnt'] >= triple_min
        add_row('ქსარელტო 2.5 მგ x2', 'EVLA-ზე შესაძლებელია დილის დოზის გამოტოვებით (ასპირინი გრძელდება) · აღდგენა: ~24 სთ-ში ჰემოსტაზით' + (' [≥3 ანტითრომბოზული — დე-ესკალაცია!]' if t3_ev else ''), 'amber' if t3_ev else 'ok', f'გამოტოვეთ {d_str(0)}-ის დილას', hold_or_skip=True, reconcile_only=True)
    elif c['is_neuraxial']:
        nx_res = '' if contra_riva else '; აღდგენა ≥6 სთ კათეტერიდან'
        add_row('ქსარელტო 2.5 მგ x2', f'შეჩერება სპინალურ/ეპიდურულ ბლოკადამდე ≥24–48 სთ ადრე (ASRA 5th Ed დაბალი დოზა ≥24 სთ{nx_res}) — ასპირინი გრძელდება{doac_extra_warn}', 'red' if riva_red else 'amber', _IMMED_STOP_PLAN if contra_riva else hold_str(voy_cfg['nx_days']), hold_or_skip=True, force_date_plan=contra_riva, contraindicated=contra_riva, mandatory_hold_days=voy_cfg['nx_days'])
    else:
        p2y_sfx = ' [OAC + ტიკაგრელორი/პრასუგრელი ESC Class III — შეათანხმეთ დე-ესკალაცია!]' if potent_p2y_riva else ''
        riva_res = '' if contra_riva else ' · აღდგენა: 24–48 სთ-ში ჰემოსტაზით'
        add_row('ქსარელტო 2.5 მგ x2', f'შეჩერება ოპერაციამდე 24–48 საათით ადრე (ასპირინი 75–100 მგ გრძელდება უწყვეტად){riva_res}{doac_extra_warn}{p2y_sfx}', 'red' if riva_red else 'amber', _IMMED_STOP_PLAN if contra_riva else hold_str(voy_cfg['std_days']), hold_or_skip=True, force_date_plan=contra_riva, contraindicated=contra_riva, mandatory_hold_days=voy_cfg['std_days'])


def _append_warfarin_row(c):
    from .calculators import _s_raw, _sat_num
    from .registry import rule_value
    vka_cfg, cea_recent_d = rule_value('MED_WARFARIN_HOLD_DAYS'), rule_value('SCORE_ASA_PHYSICAL_STATUS')['cea_recent_d']
    q, s, add_row, d_str, hold_str = c['q'], c['s'], c['add_row'], c['d_str'], c['hold_str']
    if not q.get('med_warfarin'):
        return
    has_mech_valve, is_evla, is_neuraxial, dup_oac_warn = c['has_mech_valve'], c['is_evla'], c['is_neuraxial'], c['dup_oac_warn']
    has_afib_m = ('afib' in c['cad_chips']) or ((s.get('ecg') or 'pending') == 'afib_controlled') or bool(s.get('irregular_hr'))
    afib_recent_stroke = has_afib_m and c['is_symp_cea'] and (bool(q.get('cea_crescendo')) or c['cea_d'] is None or c['cea_d'] <= cea_recent_d)
    high_thromb_vka = (
        has_mech_valve or afib_recent_stroke
        or ((q.get('ven_dvt_history') or bool(c['ven_dvt_chips'])) and q.get('ven_thrombophilia'))
        or ('vte_recent_3m' in c['ven_dvt_chips']) or (c['ven_dup'] == 'acute_dvt')
    )
    liver_vka_warn = ' [ღვიძლის ≥3x დისფუნქცია: ვარფარინის გამორეცხვა შეიძლება გახანგრძლივდეს >5 დღემდე — გადაამოწმეთ INR!]' if c['liver_3x'] else ''
    dup_vka = c['oac_count'] >= 2
    inr_cur, dos_inr_cur = _sat_num(_s_raw(s, 'inr'), 0.4, 20.0), _sat_num(_s_raw(s, 'dos_inr'), 0.5, 15.0)
    eff_inr_vka = dos_inr_cur if dos_inr_cur is not None else inr_cur
    if c['is_cea_oac_p2y_stop']:
        add_row('ვარფარინი', f'{_cea_bleed_rule(c, "ვარფარინი / ანტიკოაგულაცია")}{liver_vka_warn}{dup_oac_warn}', 'red', 'შეათანხმეთ ნეიროქირურგთან / ნევროლოგთან!', hold_or_skip=False, force_date_plan=True, contraindicated=True)
    elif is_evla and not is_neuraxial and not dup_vka:
        if eff_inr_vka is not None and eff_inr_vka > vka_cfg['inr_evla_max']:
            add_row('ვარფარინი', f'სუპრათერაპიული INR ({eff_inr_vka} > {vka_cfg["inr_evla_max"]}) EVLA-ს წინ — სისხლდენის რისკი, გადაამოწმეთ/შეაჩერეთ დოზა ქირურგთან!{liver_vka_warn}{dup_oac_warn}', 'red', f'INR {eff_inr_vka} > {vka_cfg["inr_evla_max"]} — გადაამოწმეთ!', hold_or_skip=False, force_date_plan=True, contraindicated=True)
        else:
            add_row('ვარფარინი', f'ESVS 2022: EVLA შესაძლებელია მოუხსნელად, თუ INR ≤ {vka_cfg["inr_evla_max"]}{liver_vka_warn}{dup_oac_warn}', 'red' if c['liver_3x'] else 'amber', f'INR ≤{vka_cfg["inr_evla_max"]} შემოწმება {d_str(-1)}', hold_or_skip=False)
    else:
        inr_target = 'INR ≤ 1.4 (ASRA 5th Ed: ნეირაქსიულამდე სასურველია ≤1.2 ნორმალიზება!)' if is_neuraxial else f'INR ≤ {vka_cfg["inr_surg_max"]} (ქირურგიული ჰემოსტაზისთვის)'
        if has_mech_valve and c['has_hit']:
            liver_arg = ' (ღვიძლის ≥3x-ზე უპირატესია ბივალირუდინი; არგატრობანი −75%!)' if c['liver_3x'] else ''
            bridge_txt = f'მექანიკური სარქველი + HIT — უხიდოდ დატოვება და ჰეპარინი/LMWH აკრძალულია! სავალდებულოა სტაციონარული IV ბივალირუდინი ან არგატრობანი (aPTT კონტროლით){liver_arg}!'
        elif c['has_hit'] and high_thromb_vka:
            liver_hit_sfx = ' (ღვიძლის ≥3x-ზე უპირატესია ბივალირუდინი — არგატრობანი -75% / ფონდაპარინუქსს მოერიდეთ!)' if c['liver_3x'] else ''
            bridge_txt = f'HIT ანამნეზში + მაღალი თრომბოემბოლიური რისკი — LMWH/ჰეპარინის ხიდი კატეგორიულად აკრძალულია! განიხილეთ არაჰეპარინული ხიდი (IV ბივალირუდინი/არგატრობანი ან ფონდაპარინუქსი CrCl ≥30-ზე) ჰემატოლოგთან{liver_hit_sfx}!'
        elif c['has_hit']:
            bridge_txt = 'HIT ანამნეზში — LMWH/ჰეპარინის ხიდი კატეგორიულად აკრძალულია!'
        elif high_thromb_vka and c['severe_renal']:
            bridge_txt = 'მექანიკური სარქველი / მაღალი თრომბოემბოლიური რისკი + CrCl/eGFR <30 (ან დიალიზი) — თერაპიული LMWH აკუმულირდება! სავალდებულოა სტაციონარული IV UFH ხიდი (aPTT კონტროლით, შეჩერება ოპერაციამდე 4–6 სთ; პოსტოპ. აღდგენა 24–48 სთ-ში თერაპიულ INR-მდე) ან anti-Xa კონტროლირებადი LMWH ნეფროლოგ/კარდიოლოგთან!'
        elif high_thromb_vka:
            d3_lbl, d1_lbl = d_str(-3) or 'D-3', d_str(-1) or 'D-1'
            post_br = '48–72 სთ-ში' if c['is_open_major'] else '24–48 სთ-ში'
            bridge_txt = f'მექანიკური სარქველი / მაღალი თრომბოემბოლიური რისკი — LMWH ხიდი (CHEST 2022 / ESC 2022): იწყება INR <2.0-ზე (~{d3_lbl}), წინა დილას ({d1_lbl}) კეთდება დღიური დოზის 50% (ნახევარი), წინა საღამოს დოზა გამოტოვეთ (ბოლო დოზა ≥24 სთ ოპერაციამდე); პოსტოპ. LMWH ხიდის აღდგენა {post_br} ჰემოსტაზით, თერაპიულ INR ≥2.0-მდე!'
        else:
            bridge_txt = 'რუტინული LMWH ხიდი AFib-ზე არ ტარდება (ESC 2022 Class III); თუ დაინიშნა პროფილაქტიკური LMWH, ჩერდება -12 სთ, თერაპიული -24 სთ'
        vka_res_sfx = '' if dup_vka else ' · აღდგენა: 12–24 სთ-ში (საღამოს/D+1)'
        vka_dp = _IMMED_STOP_PLAN if dup_vka else f'{hold_str(vka_cfg["hold_days"])} · INR კონტროლი {d_str(-1)}'
        add_row('ვარფარინი', f'შეჩერება {vka_cfg["hold_days"]} დღით ადრე + წინა დღეს საკონტროლო {inr_target}. {bridge_txt}{vka_res_sfx}{liver_vka_warn}{dup_oac_warn}', 'red', vka_dp, hold_or_skip=True, force_date_plan=dup_vka, contraindicated=dup_vka, mandatory_hold_days=vka_cfg['hold_days'])


def _append_metabolic_rows(c):
    from .registry import rule_value
    sglt2_cfg = rule_value('MED_SGLT2_HOLD_DAYS')
    glp_cfg = rule_value('MED_GLP1_FASTING_PROTOCOL')
    metf_cfg = rule_value('MED_METFORMIN_RENAL_CONTRAST')
    q, add_row, d_str, hold_str = c['q'], c['add_row'], c['d_str'], c['hold_str']
    on_dialysis, eff_renal, liver_3x = c['on_dialysis'], c['eff_renal'], c['liver_3x']

    if q.get('dm_insulin'):
        pure_local_evla_ins = c['is_evla'] and not c['is_neuraxial'] and not c['lido_alg'] and c['anesth_plan'] in ('auto', 'local') and not (q.get('glp1') or c['ft_meds']['has_glp1'])
        if pure_local_evla_ins:
            add_row('ინსულინი', 'ადგილობრივი EVLA-ს დილას მსუბუქი საუზმე დაშვებულია (თუ ექიმმა უზმოზე მოსვლა არ გთხოვათ) — მიიღეთ ჩვეული დილის ინსულინი საკვებთან ერთად (უზმოზე ყოფნისას: სწრაფი არა, ბაზალური 75–80%) + გლუკოზის კონტროლი!', 'amber', f'{d_str(0)} დილას: საუზმით — ჩვეული დოზა (უზმოზე: სწრაფი არა · ბაზალური 75–80%)', hold_or_skip=True, reconcile_only=True)
        else:
            add_row('ინსულინი', 'ოპერაციის წინა საღამოს/დილას ხანგრძლივი ბაზალური ანალოგი (ლანტუსი/ტუჯეო/ტრესიბა) მცირდება 75–80%-მდე (NPH / შერეული ინსულინი — 50%-მდე!); სწრაფი (საკვებისწინა) ინსულინი უზმოზე გამოტოვეთ + დილის გლუკოზის კონტროლი!', 'amber', f'{d_str(0)} დილას: სწრაფი არა · ბაზალური 75–80% (NPH 50%)', hold_or_skip=True)

    if q.get('sglt2'):
        hold_sglt2 = sglt2_cfg['ertugliflozin_days'] if c['ft_meds']['ertugliflozin'] else sglt2_cfg['std_days']
        hrs_sglt2 = f'{hold_sglt2 * 24} სთ'
        glp_dka = f' [GLP-1-ის {glp_cfg["liquid_hours"]} სთ თხევადი დიეტა აძლიერებს DKA-ს რისკს — კეტონების მკაცრი კონტროლი!]' if (q.get('glp1') or c['ft_meds']['has_glp1']) else ''
        add_row(
            'SGLT2 (ჯარდიანსი / ფორსიგა)',
            f'სავალდებულო შეჩერება ოპერაციამდე 72 საათით (3 დღით) ადრე (ერტუგლიფლოზინი/სტეგლატრო — 96 სთ / 4 დღით ადრე) + ოპერაციის დილას სისხლის კეტონების კონტროლი (ეუგლიკემიური DKA-ს პრევენცია)! აღდგენა მხოლოდ ნორმალური პერორალური კვების აღდგენის შემდეგ.{glp_dka}',
            'amber', hold_str(hold_sglt2, f' ({hrs_sglt2})'), hold_or_skip=True, mandatory_hold_days=hold_sglt2,
        )

    if q.get('glp1'):
        daily_glp = c['ft_meds']['daily_glp1']
        wk_d = glp_cfg['weekly_hold_days']
        hold_hint = f'შეჩერება ({d_str(0)} დილას, დღიური)' if daily_glp else f'შეჩერება ({d_str(-wk_d)}-დან) მხოლოდ ექიმით'
        add_row(
            'GLP-1 (ოზემპიკი / მუნჯარო / საქსენდა)',
            f'Multi-Society 2024 (ASA/AGA/ASMBS/ISPCOP/SAGES): გულისრევა/ღებინებისა და დოზის მატების ფაზის გარეშე უმეტეს პაციენტთან გრძელდება + სავალდებულოა ოპერაციამდე {glp_cfg["liquid_hours"]} სთ სუფთა თხევადი დიეტა! შეჩერება (კვირეული {wk_d} დღით / დღიური ოპერაციის დღეს) — მხოლოდ ექიმის/ანესთეზიოლოგის გადაწყვეტილებით მაღალი GI რისკისას.',
            'amber', f'{d_str(-1)} თხევადი დიეტა ({glp_cfg["liquid_hours"]} სთ) · {hold_hint}', hold_or_skip=True, glp1_diet=True, reconcile_only=True,
        )

    if q.get('metformin'):
        pure_local_evla = c['is_evla'] and not c['is_neuraxial'] and not c['lido_alg'] and c['anesth_plan'] not in ('general', 'plexus', 'sedation')
        dose_cap = ' [CrCl/eGFR 30–44: მაქს. დღიური დოზა ≤1000 მგ/დღ!]' if (eff_renal is not None and eff_renal < metf_cfg['dose_cap_renal']) else ''
        if c['creat_mismatch']:
            add_row(
                'მეტფორმინი',
                'კრეატინინის ერთეული დასაზუსტებელია — მეტფორმინის უსაფრთხოებისთვის (CrCl/eGFR ≥30) დაუყოვნებლივ გადაამოწმეთ µmol/L / mg/dL!',
                'red', 'ერთეული დასაზუსტებელია', hold_or_skip=False, force_date_plan=True, contraindicated=True, reconcile_only=True,
            )
        elif on_dialysis or c['creat_extreme_hi'] or (eff_renal is not None and eff_renal < metf_cfg['contra_renal']):
            add_row(
                'მეტფორმინი',
                f'{c["cl_label"] or "CrCl/eGFR"} < 30 / დიალიზი — მეტფორმინი უკუნაჩვენებია (ლაქტატ-აციდოზის რისკი)! შეწყვიტეთ დაუყოვნებლივ და შეათანხმეთ ენდოკრინოლოგთან!',
                'red', 'შეწყვიტეთ დაუყოვნებლივ!', hold_or_skip=False, force_date_plan=True, contraindicated=True,
            )
        elif liver_3x:
            add_row(
                'მეტფორმინი',
                'ღვიძლის მძიმე დისფუნქცია (≥3x ULN) — მეტფორმინი უკუნაჩვენებია ლაქტატ-აციდოზის გამო! შეწყვიტეთ ექიმთან შეთანხმებით!',
                'red', 'შეწყვიტეთ დაუყოვნებლივ!', hold_or_skip=False, force_date_plan=True, contraindicated=True,
            )
        elif c['creat_ambig'] or eff_renal is None:
            m_miss_rule = (
                'კრეატინინის ერთეული დასაზუსტებელია (15–35 ზონა) — მეტფორმინის უსაფრთხოებისთვის (CrCl/eGFR ≥30, ლაქტატ-აციდოზის გამორიცხვა) მიუთითეთ µmol/L თუ mg/dL!'
                if c['creat_ambig'] else
                'შეიყვანეთ კრეატინინი (eGFR/CrCl) — მეტფორმინის უსაფრთხოებისთვის (≥30) და 48 სთ შეჩერების საჭიროების დასადგენად!'
            )
            m_miss_dp = 'ერთეული დასაზუსტებელია' if c['creat_ambig'] else 'შეიყვანეთ კრეატინინი რეჟიმისთვის'
            add_row('მეტფორმინი', m_miss_rule, 'red', m_miss_dp, hold_or_skip=True, force_date_plan=True, reconcile_only=True)
        elif (
            c['is_ia_first_pass']
            or (c['uses_contrast'] and eff_renal < metf_cfg['contrast_hold_renal'])
            or c['is_open_major']
            or (not pure_local_evla and eff_renal < metf_cfg['dose_cap_renal'])
        ):
            metf_day_lbl = 'კონტრასტის/ჩარევის დღეს' if c['uses_contrast'] else 'ოპერაციის დღეს'
            h_hrs = metf_cfg['hold_hours']
            add_row(
                'მეტფორმინი',
                f'შეჩერება {metf_day_lbl} {h_hrs} სთ-ით — აღდგენა მხოლოდ {h_hrs} სთ-ის შემდეგ თირკმლის საკონტროლო კრეატინინის/eGFR გადამოწმებით (ESUR v10 / FDA){dose_cap}',
                'amber', f'არ მიიღოთ {d_str(0)}–{d_str(1)} ({h_hrs} სთ · კრეატინინის კონტროლით)', hold_or_skip=True,
            )
        elif pure_local_evla:
            add_row('მეტფორმინი', f'გამოტოვება პროცედურის დილას (ადგილობრივ არაკონტრასტულ EVLA-ზე 48 სთ შეჩერება არ ესაჭიროება){dose_cap}', 'amber' if dose_cap else 'ok', f'გამოტოვეთ {d_str(0)}-ის დილას', hold_or_skip=True)
        else:
            add_row('მეტფორმინი', f'გამოტოვება ოპერაციის დილას (უზმოზე; არაკონტრასტულ/მეორადი-გავლის ჩარევაზე 48 სთ შეჩერება არ ესაჭიროება){dose_cap}', 'amber' if dose_cap else 'ok', f'გამოტოვეთ {d_str(0)}-ის დილას', hold_or_skip=True)


def _append_cardio_other_rows(c):
    from .registry import rule_value
    vaso_cfg = rule_value('MED_CARDIO_VASOACTIVE_STEROID')
    amio_renal = rule_value('MED_DOAC_HOLD_INTERVALS')['amio_renal']
    patient, q, pw = c['patient'], c['q'], c['pw']
    add_row, d_str, med_chips = c['add_row'], c['d_str'], c['med_chips']
    bp_sys_cur, bp_inv_only, hr_cur, k_cur, eff_renal = c['bp_sys_cur'], c['bp_inverted_only'], c['hr_cur'], c['k_cur'], c['eff_renal']

    if 'statin' in med_chips:
        if c['repro_pregnant']:
            add_row('სტატინი', 'ორსულობის დროს სტატინი უკუნაჩვენებია (ტერატოგენული რისკი) — დაუყოვნებლივ შეწყვიტეთ და შეათანხმეთ ექიმთან!', 'red', _IMMED_STOP_PLAN, hold_or_skip=False, force_date_plan=True, contraindicated=True, acute_hold=True)
        elif c['liver_3x']:
            add_row('სტატინი', 'ღვიძლის ფერმენტები ≥3x ULN — გადაამოწმეთ მწვავე ტრანსამინიტი ექიმთან სტატინის გაგრძელებამდე!', 'amber', f'შეათანხმეთ ექიმთან ({d_str(0)})', hold_or_skip=False)
        else:
            add_row('სტატინი', 'გრძელდება უწყვეტად ოპერაციის დილის ჩათვლით (ESVS/ESC Class I)', 'ok', f'მიიღეთ {d_str(0)}-ის დილის ჩათვლით', hold_or_skip=False)
    if 'beta_blocker' in med_chips:
        bb_hold_vitals = bp_inv_only or (bp_sys_cur is not None and bp_sys_cur < vaso_cfg['bb_min_sbp']) or (hr_cur is not None and hr_cur < vaso_cfg['bb_min_hr'])
        bb_plan = 'გადაამოწმეთ წნევა დილის დოზამდე! (SBP≤DBP)' if bp_inv_only else ('გამოტოვეთ დილას! (SBP<100 ან HR<55)' if bb_hold_vitals else f'მიიღეთ {d_str(0)}-ის დილას (თუ SBP≥100, HR≥55)')
        add_row(
            'ბეტა-ბლოკატორი',
            'გრძელდება უწყვეტად ოპერაციის დილას ყლუპი წყლით, თუ SBP ≥ 100 mmHg და HR ≥ 55/წთ (დაბალ წნევა/პულსზე დილის დოზა გამოტოვეთ! — ESC 2022 Class I)',
            'amber' if bb_hold_vitals else 'ok', bb_plan, hold_or_skip=bb_hold_vitals, force_date_plan=bb_hold_vitals, acute_hold=bb_hold_vitals,
        )
    if 'acei_arb' in med_chips:
        ace_hold_vitals = bp_inv_only or (bp_sys_cur is not None and bp_sys_cur < vaso_cfg['bb_min_sbp']) or (k_cur is not None and k_cur >= vaso_cfg['ace_max_k'])
        if c['repro_pregnant']:
            add_row('ACEi / ARB (პრილი/სარტანი)', 'ორსულობის დროს ACEi / ARB აბსოლუტურად უკუნაჩვენებია (ნაყოფის ნეფროტოქსიკურობა — FDA Boxed Warning) — დაუყოვნებლივ შეცვალეთ ექიმთან!', 'red', _IMMED_STOP_PLAN, hold_or_skip=False, force_date_plan=True, contraindicated=True, acute_hold=True)
        elif ace_hold_vitals:
            ace_plan = 'გადაამოწმეთ წნევა დილის დოზამდე! (SBP≤DBP)' if bp_inv_only else 'გამოტოვეთ დილას! (SBP<100 / K+≥5.2)'
            add_row('ACEi / ARB (პრილი/სარტანი)', 'საბაზისო წნევა არასწორია/დაბალია (SBP < 100 / SBP≤DBP) ან K+ ≥ 5.2 მმოლ/ლ — ოპერაციის დილის დოზა გადაამოწმეთ / გამოტოვეთ!', 'amber', ace_plan, hold_or_skip=True, force_date_plan=True, acute_hold=True)
        elif c['has_true_hfref']:
            add_row('ACEi / ARB (პრილი/სარტანი)', 'ჭეშმარიტი HFrEF (LVEF <40%) — გრძელდება ანესთეზიოლოგთან შეთანხმებით (თუ SBP ≥ 100 mmHg და K+ < 5.2 — ESC 2022)', 'amber', f'შეათანხმეთ HFrEF-ზე ({d_str(0)})', hold_or_skip=False)
        elif c['is_open_major'] or c['is_neuraxial'] or pw == PreopPatient.Pathway.CEA or c['anesth_plan'] == 'general' or patient.op_subtype in ('evar', 'tbd'):
            add_row('ACEi / ARB (პრილი/სარტანი)', 'გამოტოვება ოპერაციის დილას (ინტრაოპერაციული ჰიპოტენზიის პრევენცია — POISE-3; HFpEF-ზეც დილას ჩერდება)', 'amber', f'გამოტოვეთ {d_str(0)}-ის დილას', hold_or_skip=True)
        else:
            add_row('ACEi / ARB (პრილი/სარტანი)', 'გრძელდება ჩვეულ რეჟიმში (თუ წნევა სტაბილურია ≥100 mmHg)', 'ok', f'მიიღეთ ჩვეულ რეჟიმში ({d_str(0)})', hold_or_skip=False)
    if 'ccb' in med_chips:
        ccb_hold = bp_inv_only or (bp_sys_cur is not None and bp_sys_cur < vaso_cfg['ccb_min_sbp'])
        ccb_plan = 'გადაამოწმეთ წნევა დილის დოზამდე! (SBP≤DBP)' if bp_inv_only else ('გამოტოვეთ დილას! (SBP<110)' if ccb_hold else f'მიიღეთ {d_str(0)}-ის დილას (SBP≥110)')
        add_row(
            'ამლოდიპინი / CCB',
            'გამოტოვეთ დილის დოზა დაბალი/არასწორი წნევის (SBP < 110 / SBP≤DBP) გამო!' if ccb_hold else 'გრძელდება ჩვეულ რეჟიმში ოპერაციის დილას, თუ SBP ≥ 110 mmHg',
            'amber' if ccb_hold else 'ok', ccb_plan, hold_or_skip=ccb_hold, force_date_plan=ccb_hold, acute_hold=ccb_hold,
        )
    if 'amiodarone' in med_chips:
        amio_doac = (q.get('med_doac') or q.get('med_dabigatran') or 'rivaroxaban_2_5' in med_chips) and (c['on_dialysis'] or (eff_renal is not None and eff_renal < amio_renal))
        amio_pgp_warn = ' [P-gp ურთიერთქმედება: DOAC/დაბიგატრანთან და CrCl <60/დიალიზზე ზრდის სისხლდენის რისკს!]' if amio_doac else ''
        amio_vka_warn = ' [CYP2C9: ვარფარინთან აძლიერებს ანტიკოაგულაციას — გადაამოწმეთ INR!]' if q.get('med_warfarin') else ''
        add_row('ამიოდარონი', f'გრძელდება უწყვეტად ანტიარითმიული სქემით ოპერაციის დილის ჩათვლით{amio_pgp_warn}{amio_vka_warn}', 'amber' if (amio_doac or amio_vka_warn) else 'ok', f'მიიღეთ {d_str(0)}-ის დილას', hold_or_skip=False)
    if 'diuretic' in med_chips:
        add_row('დიურეტიკი', 'გამოტოვება ოპერაციის დილას', 'amber', f'გამოტოვეთ {d_str(0)}-ის დილას', hold_or_skip=True)
    _append_hormone_ppi_steroid_ft_rows(c)


def _append_hormone_ppi_steroid_ft_rows(c):
    from .registry import rule_value
    ppi_age = rule_value('GI_CAROTID_CONCOMITANT_AND_PPI')['ppi_age']
    patient, q, s = c['patient'], c['q'], c['s']
    add_row, d_str, med_chips = c['add_row'], c['d_str'], c['med_chips']
    if q.get('ven_hormones'):
        horm_contra = bool(c['liver_3x'] or ('vte_recent_3m' in c['ven_dvt_chips']) or c['ven_dup'] == 'acute_dvt')
        if horm_contra:
            add_row(
                'ჰორმონული პრეპარატი / კონტრაცეპტივი',
                'უკუნაჩვენებია ღვიძლის მძიმე დისფუნქციის (≥3x ULN) ან ახალი/მწვავე VTE (<3 თვე / DVT) ფონზე — დაუყოვნებლივ შეათანხმეთ შეწყვეტა ექიმთან!',
                'red', 'უკუნაჩვენებია — შეათანხმეთ მოხსნა!', hold_or_skip=False, force_date_plan=True, contraindicated=True,
            )
        else:
            if c['has_hit']:
                vte_opt = 'არაჰეპარინული პროფილაქტიკა (HIT: LMWH აკრძალულია!)'
            elif c['severe_renal']:
                vte_opt = 'მექანიკური (IPC) ან კანქვეშა UFH პროფილაქტიკა (CrCl<30/დიალიზი: სტანდარტული LMWH აკრძალულია!)'
            else:
                vte_opt = 'LMWH პროფილაქტიკა'
            if c['is_evla']:
                evla_vte_sfx = f' — {vte_opt}' if (c['has_hit'] or c['severe_renal']) else ''
                horm_rule = f'EVLA-ზე შესაძლებელია გაგრძელება VTE რისკის შეფასებით (Caprini); ფლებექტომიაზე/მაღალ რისკზე შეათანხმეთ თრომბოპროფილაქტიკა (ESVS 2022){evla_vte_sfx}'
            else:
                horm_rule = f'ესტროგენული/ჰორმონული პრეპარატი ზრდის VTE რისკს — შეათანხმეთ ქირურგთან (4 კვ-ით ადრე მოხსნა ან {vte_opt})'
            add_row('ჰორმონული პრეპარატი / კონტრაცეპტივი', horm_rule, 'amber', f'VTE შეფასება ({d_str(0)})', hold_or_skip=True, reconcile_only=True)
    has_dapt_med = (bool(q.get('med_aspirin')) and c['p2y12_cnt'] >= 1) or (c['p2y12_cnt'] >= 2)
    has_ap_any_med = bool(q.get('med_aspirin') or c['p2y12_cnt'] >= 1 or 'cilostazol' in med_chips)
    has_high_gi_ppi_ind = (
        bool(q.get('ulcer')) or bool(q.get('ulcer_active_symp')) or (s.get('gastro_result') == 'acute_ulcer')
        or has_dapt_med or (c['has_full_oac'] and has_ap_any_med)
        or (((_int(patient.age, lo=1, hi=120) or 0) >= ppi_age) and 'steroid' in med_chips and (c['has_full_oac'] or has_ap_any_med or 'rivaroxaban_2_5' in med_chips))
    )
    if 'ppi' in med_chips or c['ft_meds']['has_omeprazole'] or has_high_gi_ppi_ind or s.get('ppi_started'):
        ft_keys = c['ft_meds'].get('matched_keys') or set()
        cyp_ppi_sensitive = bool(
            q.get('med_clopidogrel') or 'cilostazol' in med_chips
            or 'clopidogrel' in ft_keys or 'cilostazol' in ft_keys
        )
        ppi_drug_lbl = 'პანტოპრაზოლი 40 მგ (PPI)' if cyp_ppi_sensitive else 'ომეპრაზოლი / პანტოპრაზოლი (PPI)'
        ppi_cyp_note = ' — ომეპრაზოლი/ეზომეპრაზოლი შეცვალეთ პანტოპრაზოლით (CYP2C19)!' if cyp_ppi_sensitive else ''
        add_row(ppi_drug_lbl, f'გრძელდება / ინიშნება უწყვეტად პერიოპერაციულად კუჭის დაცვისთვის (ESC Class I){ppi_cyp_note}', 'amber' if (cyp_ppi_sensitive and ('ppi' in med_chips or c['ft_meds']['has_omeprazole'])) else 'ok', f'მიიღეთ {d_str(0)}-ის დილას', hold_or_skip=False)
    if 'steroid' in med_chips:
        add_row(
            'სტეროიდი / იმუნოსუპრესანტი',
            'არ მოხსნათ! სტრეს-დოზა (Assoc. of Anaesthetists 2020): არტერიულ/დიდ ოპერაციაზე — ჰიდროკორტიზონი 100 მგ IV ინდუქციაზე + 200 მგ/24 სთ ინფუზია (ან 50 მგ IV ყოველ 6 სთ-ში; მცირე ვენურზე — ჩვეული დოზა + 25–50 მგ IV); მოერიდეთ ეტომიდატს!',
            'amber', f'არ შეწყვიტოთ ({d_str(0)} IV სტრეს-დოზა)', hold_or_skip=False,
        )
    if c['ft_meds']['unstructured_labels']:
        labels = ', '.join(c['ft_meds']['unstructured_labels'])
        ft_bleed_contra = bool(c['is_cea_oac_p2y_stop'] and c['ft_meds']['unstructured_antithrombotic'])
        ft_rule = (
            f"თავისუფალ ტექსტში („{q.get('other_meds')}“) მითითებულია მაღალი რისკის ანტითრომბოზული პრეპარატი — სისხლჩაქცევის/თრომბოლიზისის ფონზე დაუყოვნებლივ შეათანხმეთ ნეიროქირურგთან / ნევროლოგთან!"
            if ft_bleed_contra else
            f"თავისუფალ ტექსტში („{q.get('other_meds')}“) მითითებულია მაღალი რისკის პრეპარატი — მონიშნეთ ჩამრთველი შეჩერების ზუსტი თარიღისთვის!"
        )
        add_row(
            f'დასაზუსტებელი წამალი ({labels})', ft_rule, 'red',
            'დააზუსტეთ ექიმთან — არასტრუქტურირებულია!', hold_or_skip=(not ft_bleed_contra),
            force_date_plan=True, contraindicated=ft_bleed_contra, unstructured=True, reconcile_only=True,
        )


def build_med_schedule(patient, q, s, egfr, crcl=None, stop_blocked=False, has_neuraxial_override=None, today=None):
    """Deterministic perioperative medication hold/continue table (ESVS 2023/2024 + ESC 2022 + ASRA 5th Ed)."""
    q_d = q if isinstance(q, dict) else {}
    s_d = s if isinstance(s, dict) else {}
    c = _med_schedule_ctx(patient, q_d, s_d, egfr, crcl, has_neuraxial_override, today=today)
    _append_antiplatelet_rows(c)
    _append_doac_dabigatran_rows(c)
    _append_rivaroxaban_row(c)
    _append_warfarin_row(c)
    _append_metabolic_rows(c)
    _append_cardio_other_rows(c)
    if stop_blocked:
        _apply_stop_lock_to_med_schedule(c['rows'])
    return c['rows']
