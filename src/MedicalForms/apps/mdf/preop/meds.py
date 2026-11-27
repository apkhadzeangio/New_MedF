"""Free-text medication/allergy re-exports and perioperative medication hold/continue scheduler."""
from ..models import PreopPatient
from .meds_freetext import (
    _ASA_WORD_RE,
    _ASPIRIN_FT_STEMS,
    _RIVA_LOW_RE,
    _detect_freetext_allergies,
    _detect_freetext_high_risk_meds,
    _effective_allergy_state,
    append_freetext_med_rows,
)
from .meds_helpers import (
    _GI_BLEED_PLAN,
    _IMMED_STOP_PLAN,
    _apply_stop_lock_to_med_schedule,
    _make_med_row_helpers,
    _med_renal_and_flags,
    _pick_study_raw,
    _standalone_is_neuraxial,
    _valid_med_sbp,
)
from .utils import (
    _date,
    _has_raw_scalar,
    _int,
    _num,
    _resolve_pathway,
    _str_list,
    cea_live_days_since_event,
    is_pregnancy_eligible,
)

__all__ = [
    '_ASA_WORD_RE', '_ASPIRIN_FT_STEMS', '_RIVA_LOW_RE',
    '_apply_stop_lock_to_med_schedule',
    '_detect_freetext_allergies', '_detect_freetext_high_risk_meds', '_effective_allergy_state',
    'build_med_schedule',
]


def _med_schedule_ctx(patient, q, s, egfr, crcl, has_neuraxial_override, today=None):
    from .calculators import _inverted_bp_arms, _parse_scr_mgdl, _s_raw, _sat_num, is_creat_unit_mismatch
    from .registry import rule_value
    from .utils import has_prior_stroke_or_tia, is_symp_cea_patient
    pw = _resolve_pathway(patient)
    is_evla, is_phleb, is_pta = (patient.op_subtype == PreopPatient.OpSubtype.EVLA), (patient.op_subtype == PreopPatient.OpSubtype.PHLEB), (pw == PreopPatient.Pathway.PAD_PTA)
    cea_d = cea_live_days_since_event(q, today=today, s=s) if pw == PreopPatient.Pathway.CEA else None
    is_symp_cea = is_symp_cea_patient(patient, q, today=today, s=s, require_filled=False)
    has_stroke_hx = bool(is_symp_cea or has_prior_stroke_or_tia(patient, q=q, s=s, today=today))
    is_open_major = pw in (PreopPatient.Pathway.AAA_INFRA, PreopPatient.Pathway.ABF, PreopPatient.Pathway.PAD_BYPASS) and patient.op_subtype != 'evar'
    is_ia_first_pass = patient.op_subtype in ('evar', 'tbd')
    uses_contrast, anesth_plan = is_ia_first_pass or is_pta, s.get('anesthesia_plan') or 'auto'
    has_epidural_line = 'epidural' in _str_list(s.get('anesth_lines_chips'))
    on_dialysis, eff_renal, cl_label, frail_no_wt_note = _med_renal_and_flags(patient, q, s, egfr, crcl)
    raw_cr_m, cr_u_m = _s_raw(s, 'creat'), s.get('creat_unit') or ''
    creat_mismatch = (not on_dialysis) and is_creat_unit_mismatch(raw_cr_m, cr_u_m)
    creat_ambig = (not on_dialysis) and (not creat_mismatch) and bool(_parse_scr_mgdl(raw_cr_m, cr_u_m)[1])
    creat_extreme_hi = (not on_dialysis) and (eff_renal is None) and ((_num(raw_cr_m) or 0) > 3000)
    op_dt, liver_3x = _date(s.get('planned_surgery_date')), (s.get('liver_status') == '3x')
    rows, d_str, hold_str, add_row = _make_med_row_helpers(op_dt)
    med_chips, cad_chips = set(_str_list(q.get('med_chips'))), set(_str_list(q.get('cad_chips')))
    ven_dvt_chips, vasc_chips = set(_str_list(q.get('ven_dvt_chips'))), set(_str_list(q.get('vasc_chips')))
    eff_allergy_chips, _, _ = _effective_allergy_state(q)
    ft_meds = _detect_freetext_high_risk_meds(q.get('other_meds') or '', q=q)
    ft_unstruct = ft_meds.get('unstructured_keys') or set()
    has_ft_full_oac = bool(ft_meds.get('has_ft_full_oac'))
    has_ft_unstruct_warfarin = bool(ft_meds.get('has_ft_unstruct_warfarin'))
    has_ft_unstruct_doac = bool(ft_meds.get('has_ft_unstruct_doac'))
    has_ft_unstruct_riva_low = bool(ft_meds.get('has_ft_unstruct_riva_low'))
    raw_s_pat = patient.studies if isinstance(patient.studies, dict) else {}
    is_neuraxial = bool(has_neuraxial_override) if has_neuraxial_override is not None else _standalone_is_neuraxial(patient, anesth_plan, is_phleb, has_epidural_line, q, s, today, eff_allergy_chips, ft_meds, cad_chips, med_chips, on_dialysis, None if creat_mismatch else eff_renal, raw_s=raw_s_pat)
    has_mech_valve, has_acs_stent_12m = 'mech_valve' in cad_chips, ('stent_6_12m' in cad_chips) and ('mi' in cad_chips)
    bp_sys_cur, bp_inverted_only = _valid_med_sbp(s)
    hr_cur, lvef_cur = _sat_num(_s_raw(s, 'hr'), 15, 260), _sat_num(_s_raw(s, 'lvef'), 5, 95)
    nt_probnp_cur, k_cur = _sat_num(_s_raw(s, 'nt_probnp'), 1, 100000), _sat_num(_s_raw(s, 'k'), 1.0, 12.0)
    cilo_cfg, lvef_red_lim = rule_value('MED_CILOSTAZOL_BLACKBOX'), rule_value('CARDIO_BIOMARKERS_ECHO')['lvef_red']
    ren_red_lim, vaso_cfg = rule_value('REN_CKD_NEPHRO_HYDRATION')['red'], rule_value('MED_CARDIO_VASOACTIVE_STEROID')
    hypo_cfg = rule_value('VIT_BP_HYPOTENSION_SHOCK')
    sbp_rup = bp_sys_cur if bp_sys_cur is not None else min((min(sv, dv) for sv, dv in _inverted_bp_arms(s)), default=None)
    si_rup = round(hr_cur / sbp_rup, 2) if (hr_cur is not None and sbp_rup is not None and sbp_rup > 0) else None
    is_rupture_suspect = bool(pw == PreopPatient.Pathway.AAA_INFRA and q.get('aaa_pain') and ((sbp_rup is not None and sbp_rup < hypo_cfg['red_sbp']) or (si_rup is not None and si_rup >= hypo_cfg['shock_idx'])))
    lido_alg = bool(q.get('ven_lidocaine_allergy')) or ('lidocaine' in eff_allergy_chips)
    abi_vals = [v for v in (_num(s.get('abi_r'), 0, 3), _num(s.get('abi_l'), 0, 3)) if v is not None]
    min_abi = min(abi_vals) if abi_vals else None
    chf_any = ('hf' in cad_chips) or (lvef_cur is not None and lvef_cur < lvef_red_lim) or (nt_probnp_cur is not None and nt_probnp_cur >= cilo_cfg['bnp_max'])
    has_true_hfref = (lvef_cur is not None and lvef_cur < lvef_red_lim) or (('hf' in cad_chips) and lvef_cur is None)
    has_full_oac = bool(q.get('med_warfarin') or q.get('med_doac') or q.get('med_dabigatran'))
    p2y12_cnt = sum([bool(q.get('med_clopidogrel')), bool(q.get('med_ticagrelor')), bool(q.get('med_prasugrel'))]) + len(ft_unstruct & {'clopidogrel', 'ticagrelor', 'prasugrel'})
    oac_count = sum([bool(q.get('med_warfarin')), bool(q.get('med_doac')), bool(q.get('med_dabigatran')), ('rivaroxaban_2_5' in med_chips)]) + len(ft_unstruct & {'warfarin', 'doac', 'dabigatran'})
    total_at_cnt = oac_count + p2y12_cnt + (1 if (q.get('med_aspirin') or 'aspirin' in ft_unstruct) else 0) + (1 if ('cilostazol' in med_chips or 'cilostazol' in ft_unstruct) else 0)
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
    active_gi_bleed = (s.get('gastro_result') == 'acute_ulcer') or (bool(q.get('ulcer_active_symp')) and s.get('gastro_result') != 'remission')
    acei_acute_hold = bool(bp_inverted_only or (bp_sys_cur is not None and bp_sys_cur < vaso_cfg['bb_min_sbp']) or (k_cur is not None and k_cur >= vaso_cfg['ace_max_k']))
    preg_oac_alt = (
        'არაჰეპარინული ალტერნატივით (HIT: LMWH/UFH აკრძალულია!) ჰემატოლოგთან და მეან-გინეკოლოგთან!' if has_hit
        else ('არასფრაქციული ჰეპარინით (UFH — თირკმლის მძიმე უკმარისობის/დიალიზის გამო LMWH კუმულირდება!) მეან-გინეკოლოგთან და კარდიოლოგთან!' if severe_renal
        else 'LMWH/UFH-ით მეან-გინეკოლოგთან და კარდიოლოგთან!')
    )
    tl_days = rule_value('CEA_ACUTE_NEURO_THROMBOLYSIS')
    is_cea_ich = (pw == PreopPatient.Pathway.CEA and s.get('brain_imaging') == 'large_or_hemorrhage')
    is_cea_rtpa_6d = (pw == PreopPatient.Pathway.CEA and bool(q.get('cea_thrombolysis')) and (cea_d is None or cea_d <= tl_days))
    is_cea_rtpa_ap_stop = is_cea_rtpa_6d and not (cea_d is not None and cea_d >= 1 and s.get('brain_imaging') == 'normal_small')
    return {
        'patient': patient, 'q': q, 's': s, 'pw': pw, 'cea_d': cea_d, 'tl_days': tl_days, 'is_rupture_suspect': is_rupture_suspect,
        'is_evla': is_evla, 'is_phleb': is_phleb, 'is_pta': is_pta, 'is_symp_cea': is_symp_cea, 'has_stroke_hx': has_stroke_hx,
        'is_open_major': is_open_major, 'is_ia_first_pass': is_ia_first_pass, 'uses_contrast': uses_contrast,
        'is_cea_ich': is_cea_ich, 'is_cea_bleed_stop': is_cea_ich or is_cea_rtpa_ap_stop,
        'is_cea_oac_p2y_stop': is_cea_ich or is_cea_rtpa_6d, 'is_cea_rtpa_asa_caution': is_cea_rtpa_6d and not is_cea_ich and not is_cea_rtpa_ap_stop,
        'anesth_plan': anesth_plan, 'is_neuraxial': is_neuraxial, 'on_dialysis': on_dialysis, 'eff_renal': eff_renal, 'renal_cl': eff_renal,
        'creat_mismatch': creat_mismatch, 'creat_ambig': creat_ambig, 'creat_extreme_hi': creat_extreme_hi,
        'cl_label': cl_label, 'frail_no_wt_note': frail_no_wt_note, 'severe_renal': severe_renal, 'liver_3x': liver_3x,
        'rows': rows, 'd_str': d_str, 'hold_str': hold_str, 'add_row': add_row, 'med_chips': med_chips, 'cad_chips': cad_chips,
        'ven_dvt_chips': ven_dvt_chips, 'vasc_chips': vasc_chips, 'min_abi': min_abi, 'lido_alg': lido_alg, 'ven_dup': s.get('ven_duplex_status') or '',
        'eff_allergy_chips': eff_allergy_chips, 'ft_meds': ft_meds, 'has_ft_full_oac': has_ft_full_oac,
        'has_ft_unstruct_warfarin': has_ft_unstruct_warfarin, 'has_ft_unstruct_doac': has_ft_unstruct_doac, 'has_ft_unstruct_riva_low': has_ft_unstruct_riva_low,
        'has_hit': has_hit, 'has_mech_valve': has_mech_valve, 'has_acs_stent_12m': has_acs_stent_12m,
        'has_full_oac': has_full_oac, 'p2y12_cnt': p2y12_cnt, 'total_at_cnt': total_at_cnt,
        'repro_pregnant': repro_pregnant, 'active_gi_bleed': active_gi_bleed, 'acei_acute_hold': acei_acute_hold, 'preg_oac_alt': preg_oac_alt,
        'bp_sys_cur': bp_sys_cur, 'bp_inverted_only': bp_inverted_only, 'hr_cur': hr_cur, 'lvef_cur': lvef_cur, 'k_cur': k_cur,
        'chf_any': chf_any, 'has_true_hfref': has_true_hfref, 'oac_count': oac_count, 'dup_oac_warn': dup_oac_warn,
        'doac_extra_warn': f'{mech_doac_warn}{dup_oac_warn}{liver_doac_warn}', 'force_doac_red': bool(has_mech_valve or oac_count >= 2 or liver_3x),
        'postop_oac_res': postop_oac_res,
    }


def _cea_bleed_rule(c, label):
    tl_d = c['tl_days']
    if c['is_cea_ich']:
        return f'თავის ტვინის სისხლჩაქცევა / >1/3 MCA ინფარქტი — {label} დაუყოვნებლივ შეათანხმეთ ნეიროქირურგთან / ნევროლოგთან!'
    if c['cea_d'] is not None and c['cea_d'] >= 1 and c['s'].get('brain_imaging') == 'normal_small':
        return f'თრომბოლიზისი (rt-PA, 1–{tl_d} დღე; მე-{c["cea_d"]} დღე) — ნაადრევი სისტემური ანტიკოაგულაცია/P2Y12 საშიშია ჰემორაგიული ტრანსფორმაციის გამო, {label} დაუყოვნებლივ შეათანხმეთ ნევროლოგთან!'
    if c['cea_d'] is not None and c['cea_d'] >= 1:
        return f'თრომბოლიზისი (rt-PA, 1–{tl_d} დღე; მე-{c["cea_d"]} დღე) — საკონტროლო თავის ტვინის CT-ით სისხლჩაქცევის გამორიცხვამდე {label} დაუყოვნებლივ შეათანხმეთ ნევროლოგთან!'
    return f'თრომბოლიზიდან (rt-PA) <24 სთ — {label} დაუყოვნებლივ შეათანხმეთ ნევროლოგთან (საკონტროლო CT-მდე უკუნაჩვენებია)!'


def _append_antiplatelet_rows(c):
    from .calculators import _s_raw, _sat_num
    from .registry import rule_value
    asa_cfg = rule_value('MED_ASPIRIN_CONTINUE')
    p2y_cfg = rule_value('MED_P2Y12_HOLD_DAYS')
    cilo_cfg = rule_value('MED_CILOSTAZOL_BLACKBOX')
    triple_min = rule_value('MED_ANTITHROMBOTIC_CONFLICTS')
    pad_abi_lo = rule_value('PAD_IC_BMT_3M')[0]
    car_mod_min = rule_value('CEA_STENOSIS_THRESHOLDS')['symp_min']
    ctn_amb = rule_value('CARDIO_BIOMARKERS_ECHO')['ctn_amb']
    q, s, pw = c['q'], c['s'], c['pw']
    add_row, d_str, hold_str = c['add_row'], c['d_str'], c['hold_str']
    is_phleb, is_evla, is_pta, is_symp_cea, is_neuraxial = c['is_phleb'], c['is_evla'], c['is_pta'], c['is_symp_cea'], c['is_neuraxial']
    has_acs_stent_12m, eff_allergy_chips = c['has_acs_stent_12m'], c['eff_allergy_chips']
    has_vasc_pad = bool(
        q.get('claudication') or (c['min_abi'] is not None and c['min_abi'] <= pad_abi_lo)
        or c['vasc_chips'] or (_num(s.get('aaa_mm'), 15, 200) or 0) >= asa_cfg['aaa_ectasia_mm']
        or max(_num(s.get('carotid_r'), 0, 100) or 0, _num(s.get('carotid_l'), 0, 100) or 0) >= car_mod_min
    )
    hs_ctn_cur = _sat_num(_s_raw(s, 'hs_ctn'), 0, 50000)
    has_cardiac_risk = bool(
        q.get('cad') or c['cad_chips'] or c['has_stroke_hx'] or q.get('recent_stent') or c['chf_any']
        or c['p2y12_cnt'] >= 1
        or s.get('ecg') in ('ischemia_arrhythmia', 'afib_controlled') or bool(s.get('echo_valve_issue'))
        or (hs_ctn_cur is not None and hs_ctn_cur >= ctn_amb) or has_vasc_pad
    )
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
        elif c['active_gi_bleed']:
            add_row('ასპირინი (75–100 მგ)', 'აქტიური კუჭ-ნაწლავის სისხლდენა / მწვავე წყლული — სასწრაფოდ შეათანხმეთ გასტროენტეროლოგთან და ქირურგთან (სტენტისას — კარდიოლოგის კონსილიუმი)!', 'red', _GI_BLEED_PLAN, hold_or_skip=False, force_date_plan=True, contraindicated=True, acute_hold=True)
        elif c['is_cea_rtpa_asa_caution']:
            rtpa_asa_dp = f'გრძელდება ნევროლოგთან ({d_str(0)})' if d_str(0) else 'გრძელდება ნევროლოგთან'
            add_row('ასპირინი (75–100 მგ)', f'თრომბოლიზისი (rt-PA, 1–{c["tl_days"]} დღე; მე-{c["cea_d"]} დღე) — CT-ზე სისხლჩაქცევის გამორიცხვისას ასპირინი გრძელდება ნევროლოგთან შეთანხმებით!', 'amber', rtpa_asa_dp, hold_or_skip=False, force_date_plan=True)
        elif is_phleb and not has_cardiac_risk:
            asa_ph_d = asa_cfg['phleb_low_risk_days'] if isinstance(asa_cfg, dict) else 5
            add_row('ასპირინი (75–100 მგ)', f'შეთანხმება ქირურგთან (დაბალი კარდიორისკისას შესაძლოა მოიხსნას {asa_ph_d}–{asa_ph_d + 2} დღით ადრე)', 'amber', f'შეათანხმეთ ({d_str(-asa_ph_d)}-დან?)', hold_or_skip=True, reconcile_only=True)
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
        elif c['active_gi_bleed']:
            add_row('კლოპიდოგრელი / P2Y12', f'აქტიური კუჭ-ნაწლავის სისხლდენა / მწვავე წყლული — სასწრაფოდ შეათანხმეთ გასტროენტეროლოგთან და ქირურგთან (სტენტისას — კარდიოლოგის კონსილიუმი)!{dup_p2y_warn}', 'red', _GI_BLEED_PLAN, hold_or_skip=False, force_date_plan=True, contraindicated=True, acute_hold=True)
        elif q.get('recent_stent') or has_acs_stent_12m:
            stent_note = 'სტენტი <6 თვ' if q.get('recent_stent') else 'ინფარქტის შემდგომი სტენტი 6–12 თვ (DAPT 12 თვე)'
            add_row('კლოპიდოგრელი / P2Y12', f'{stent_note} — თვითნებური მოხსნა აკრძალულია! კარდიოლოგ-ქირურგის კონსილიუმი{dup_p2y_warn}', 'red', 'არ მოხსნათ კონსილიუმის გარეშე!', hold_or_skip=False, force_date_plan=True, contraindicated=dup_p2y_red)
        elif is_symp_cea:
            c_rule = (
                '≥2 P2Y12 დუბლირება სიმპტომურ CEA-ზე — თვითნებურად არ გააგრძელოთ ორივე! დაუყოვნებლივ შეათანხმეთ ერთი P2Y12 სქემა ქირურგთან (ESVS 2023)!'
                if dup_p2y_red else
                f'გრძელდება უწყვეტად CEA-მდე! (ESVS 2023 — სიმპტომურ CEA-ზე მოხსნა ზრდის ინსულტის რისკს){t3_clop_warn}'
            )
            add_row('კლოპიდოგრელი / P2Y12', c_rule, 'red' if dup_p2y_red else ('amber' if t3_clop_warn else 'ok'), 'შეათანხმეთ დე-ესკალაცია ქირურგთან!' if dup_p2y_red else f'მიიღეთ {d_str(0)}-ის ჩათვლით', hold_or_skip=False, force_date_plan=dup_p2y_red, contraindicated=dup_p2y_red)
        elif is_pta:
            c_rule = (
                '≥2 P2Y12 დუბლირება PTA-ზე — თვითნებურად არ გააგრძელოთ ორივე! შეათანხმეთ ერთი P2Y12 (DAPT) ქირურგთან/კარდიოლოგთან!'
                if dup_p2y_red else
                f'გრძელდება უწყვეტად (DAPT რეჟიმი სტენტირებისთვის){t3_clop_warn}'
            )
            add_row('კლოპიდოგრელი / P2Y12', c_rule, 'red' if dup_p2y_red else ('amber' if t3_clop_warn else 'ok'), 'შეათანხმეთ დე-ესკალაცია!' if dup_p2y_red else f'მიიღეთ {d_str(0)}-ის ჩათვლით', hold_or_skip=False, force_date_plan=dup_p2y_red, contraindicated=dup_p2y_red)
        elif is_evla and not is_neuraxial and not dup_p2y_red:
            add_row('კლოპიდოგრელი / P2Y12', 'ESVS 2022: EVLA შესაძლებელია მოუხსნელადაც — დააზუსტეთ ქირურგთან', 'amber', f'დააზუსტეთ ქირურგთან ({d_str(0)})', hold_or_skip=True, reconcile_only=True)
        elif asa_allergy and (pw != PreopPatient.Pathway.VENOUS or q.get('cad') or c['cad_chips'] or c['has_stroke_hx']):
            clop_cite = 'ESVS 2023 Rec 10' if pw == PreopPatient.Pathway.CEA else 'ESVS / ESC'
            add_row('კლოპიდოგრელი / P2Y12', f'ასპირინზე/აასს-ზე ალერგია ({clop_cite}) — კლოპიდოგრელის მონოთერაპია თვითნებურად არ მოხსნათ, შეათანხმეთ ქირურგთან!{dup_p2y_warn}', 'red', 'შეათანხმეთ ქირურგთან (ასპირინზე ალერგია!)', hold_or_skip=False, force_date_plan=True, contraindicated=dup_p2y_red)
        elif not q.get('med_aspirin') and (pw != PreopPatient.Pathway.VENOUS or q.get('cad') or c['cad_chips'] or c['has_stroke_hx']):
            hold_d = p2y_cfg['clop_nx'] if is_neuraxial else p2y_cfg['clop_std']
            clop_span = f'{p2y_cfg["clop_std"]}–{p2y_cfg["clop_nx"]}' if p2y_cfg['clop_std'] < p2y_cfg['clop_nx'] else f'{hold_d}'
            asra_tag = f' (ASRA სპინალურ/ეპიდურულამდე მინ. {clop_span} დღე)' if is_neuraxial else ''
            add_row('კლოპიდოგრელი / P2Y12', f'შეჩერება ოპერაციამდე {clop_span} დღით ({hold_d} დღე) ადრე{asra_tag} — სავალდებულოა ჩანაცვლება ასპირინი 75–100 მგ-ით (უანტიაგრეგანტოდ დატოვება საშიშია!){dup_p2y_warn}', 'red' if dup_p2y_red else 'amber', hold_str(hold_d, ' (ასპირინით!)'), hold_or_skip=True, contraindicated=dup_p2y_red, mandatory_hold_days=hold_d)
        else:
            hold_d = p2y_cfg['clop_nx'] if is_neuraxial else p2y_cfg['clop_std']
            clop_span = f'{p2y_cfg["clop_std"]}–{p2y_cfg["clop_nx"]}' if p2y_cfg['clop_std'] < p2y_cfg['clop_nx'] else f'{hold_d}'
            asra_tag = f' (ASRA ნეირაქსიალურ ბლოკადამდე მინ. {clop_span} დღე)' if is_neuraxial else ''
            asa_sfx = ' (ასპირინის ფონზე)' if (q.get('med_aspirin') and not asa_allergy) else ''
            add_row('კლოპიდოგრელი / P2Y12', f'შეჩერება ოპერაციამდე {clop_span} დღით ({hold_d} დღე) ადრე{asra_tag}{asa_sfx}{dup_p2y_warn}', 'red' if dup_p2y_red else 'amber', hold_str(hold_d), hold_or_skip=True, contraindicated=dup_p2y_red, mandatory_hold_days=hold_d)

    _append_potent_p2y12_rows(c, p2y_cfg, asa_allergy, clop_intol, asa_sub_txt)
    _append_cilostazol_row(c, cilo_cfg)


def _append_potent_p2y12_rows(c, p2y_cfg, asa_allergy, clop_intol, asa_sub_txt):
    from .calculators import _sat_age
    q, pw, med_chips = c['q'], c['pw'], c['med_chips']
    add_row, d_str, hold_str = c['add_row'], c['d_str'], c['hold_str']
    is_pta, is_symp_cea, is_neuraxial, has_acs_stent_12m = c['is_pta'], c['is_symp_cea'], c['is_neuraxial'], c['has_acs_stent_12m']
    is_bleed = c['is_cea_oac_p2y_stop']
    solo_p2y_asa_alg = asa_allergy and (not q.get('med_clopidogrel') or clop_intol) and (pw != PreopPatient.Pathway.VENOUS or q.get('cad') or c['cad_chips'] or c['has_stroke_hx'])

    if q.get('med_ticagrelor'):
        tica_bad_combo = c['has_full_oac'] or c['has_ft_full_oac'] or c['has_ft_unstruct_riva_low'] or c['p2y12_cnt'] >= 2 or ('rivaroxaban_2_5' in med_chips)
        tica_esc_warn = ' [OAC / რივაროქსაბანი 2.5 / დუბლირებულ P2Y12-თან ერთად ESC Class III სისხლდენის რისკი — შეათანხმეთ დე-ესკალაცია!]' if tica_bad_combo else ''
        tica_org_warn = (' [ღვიძლის ≥3x: ტიკაგრელორი უკუნაჩვენებია — EMA SmPC 4.3!]' if c['liver_3x'] else '') + (' [ორსულობა: ტიკაგრელორი არ არის რეკომენდებული — EMA SmPC 4.6!]' if c['repro_pregnant'] else '')
        tica_contra_any = bool(tica_bad_combo or c['liver_3x'] or c['repro_pregnant'])
        if is_bleed:
            add_row('ტიკაგრელორი (ბრილინტა)', _cea_bleed_rule(c, 'ტიკაგრელორი'), 'red', 'შეათანხმეთ ნეიროქირურგთან / ნევროლოგთან!', hold_or_skip=False, force_date_plan=True, contraindicated=True)
        elif c['active_gi_bleed']:
            add_row('ტიკაგრელორი (ბრილინტა)', 'აქტიური კუჭ-ნაწლავის სისხლდენა / მწვავე წყლული — სასწრაფოდ შეათანხმეთ გასტროენტეროლოგთან და კარდიოლოგთან!', 'red', _GI_BLEED_PLAN, hold_or_skip=False, force_date_plan=True, contraindicated=True, acute_hold=True)
        elif q.get('recent_stent') or has_acs_stent_12m:
            add_row('ტიკაგრელორი (ბრილინტა)', f'კორონარული სტენტი DAPT რეჟიმში — თვითნებური მოხსნა აკრძალულია! კარდიოლოგ-ქირურგის კონსილიუმი{tica_esc_warn}{tica_org_warn}', 'red', 'არ მოხსნათ კონსილიუმის გარეშე!', hold_or_skip=False, force_date_plan=True, contraindicated=tica_contra_any)
        elif is_symp_cea:
            cea_t_alt = 'გაგრძელება ან კონსილიუმით შერჩევა (ალერგიების გამო!)' if (asa_allergy and clop_intol) else ('გაგრძელება ან ასპირინზე გადაყვანა' if clop_intol else ('გაგრძელება ან კლოპიდოგრელზე გადაყვანა' if asa_allergy else 'გაგრძელება ან კლოპიდოგრელ/ასპირინზე გადაყვანა'))
            add_row('ტიკაგრელორი (ბრილინტა)', f'სიმპტომური CEA — თვითნებურად არ მოხსნათ! შეათანხმეთ ქირურგთან ({cea_t_alt}){tica_esc_warn}{tica_org_warn}', 'red', 'არ მოხსნათ შეთანხმების გარეშე!', hold_or_skip=False, force_date_plan=True, contraindicated=tica_contra_any)
        elif c['liver_3x']:
            add_row('ტიკაგრელორი (ბრილინტა)', f'ღვიძლის მძიმე დისფუნქცია (≥3x ULN) — ტიკაგრელორი აბსოლუტურად უკუნაჩვენებია (EMA Brilique SmPC 4.3 / FDA 5.5 — სისხლდენის რისკი)! დაუყოვნებლივ შეცვალეთ კარდიოლოგთან!{tica_esc_warn}', 'red', _IMMED_STOP_PLAN, hold_or_skip=False, force_date_plan=True, contraindicated=True, acute_hold=True)
        elif c['repro_pregnant']:
            add_row('ტიკაგრელორი (ბრილინტა)', f'ორსულობის დროს ტიკაგრელორი არ არის რეკომენდებული (EMA SmPC 4.6) — დაუყოვნებლივ შეათანხმეთ უსაფრთხო ალტერნატივა მეან-გინეკოლოგთან და კარდიოლოგთან!{tica_esc_warn}', 'red', _IMMED_STOP_PLAN, hold_or_skip=False, force_date_plan=True, contraindicated=True, acute_hold=True)
        elif is_pta:
            rule_t = 'OAC/დუბლირებულ P2Y12-თან ერთად ESC Class III სისხლდენის რისკი — შეათანხმეთ დე-ესკალაცია კარდიოლოგთან!' if tica_bad_combo else 'გრძელდება ან შეთანხმდება ქირურგთან (ენდოვასკულური DAPT)'
            add_row('ტიკაგრელორი (ბრილინტა)', rule_t, 'red' if tica_bad_combo else 'ok', 'შეათანხმეთ დე-ესკალაცია!' if tica_bad_combo else f'მიიღეთ {d_str(0)}-ის ჩათვლით', hold_or_skip=False, force_date_plan=tica_bad_combo, contraindicated=tica_bad_combo)
        elif solo_p2y_asa_alg:
            add_row('ტიკაგრელორი (ბრილინტა)', f'ასპირინზე/აასს-ზე ალერგია — ტიკაგრელორის მონოთერაპია თვითნებურად არ მოხსნათ (უანტიაგრეგანტოდ დატოვება საშიშია)! შეათანხმეთ ქირურგთან/კარდიოლოგთან!{tica_esc_warn}', 'red', 'შეათანხმეთ ქირურგთან (ასპირინზე ალერგია!)', hold_or_skip=False, force_date_plan=True, contraindicated=tica_bad_combo)
        elif is_neuraxial:
            tnx = p2y_cfg['tica_nx']
            add_row('ტიკაგრელორი (ბრილინტა)', f'შეჩერება სპინალურ/ეპიდურულ ანესთეზიამდე {tnx} დღით ადრე (ASRA 5th Ed) — {asa_sub_txt}{tica_esc_warn}', 'red' if (tica_bad_combo or asa_allergy) else 'amber', hold_str(tnx, f' ({tnx} დღე)'), hold_or_skip=True, contraindicated=tica_bad_combo, mandatory_hold_days=tnx)
        else:
            tstd = p2y_cfg['tica_std']
            add_row('ტიკაგრელორი (ბრილინტა)', f'შეჩერება ოპერაციამდე {tstd} დღით ({tstd}–{max(tstd, p2y_cfg["tica_nx"])} დღე) ადრე (ESC 2022 / FDA; {asa_sub_txt}){tica_esc_warn}', 'red' if (tica_bad_combo or asa_allergy) else 'amber', hold_str(tstd), hold_or_skip=True, contraindicated=tica_bad_combo, mandatory_hold_days=tstd)

    if q.get('med_prasugrel'):
        has_stroke_hx = c['has_stroke_hx']
        stroke_warn = ' [გადატანილ ინსულტ/TIA-ზე პრასუგრელი უკუნაჩვენებია — გადაამოწმეთ!]' if has_stroke_hx else ''
        pras_bad_combo = c['has_full_oac'] or c['has_ft_full_oac'] or c['has_ft_unstruct_riva_low'] or c['p2y12_cnt'] >= 2 or ('rivaroxaban_2_5' in med_chips)
        pras_esc_warn = ' [OAC / რივაროქსაბანი 2.5 / დუბლირებულ P2Y12-თან ერთად ESC Class III სისხლდენის რისკი — შეათანხმეთ დე-ესკალაცია!]' if pras_bad_combo else ''
        age_p = _sat_age(c['patient'].age) or 0
        wt_p = _num(c['s'].get('weight_kg'), lo=25, hi=260)
        pras_age75, pras_wt60 = age_p >= 75, (wt_p is not None and wt_p < 60)
        pras_risk_warn = (
            (' [≥75 წ. — FDA Boxed Warning: პრასუგრელი ზოგადად არ არის რეკომენდებული ფატალური/ქალასშიდა სისხლდენის რისკის გამო!]' if pras_age75 else '')
            + (' [<60 კგ — FDA Boxed Warning: სისხლდენის რისკი, განიხილეთ 5 მგ/დღ დოზა ან კლოპიდოგრელზე დეესკალაცია!]' if pras_wt60 else '')
            + (' [ღვიძლის ≥3x: მძიმე სისხლდენის რისკი — FDA PI 5.1!]' if c['liver_3x'] else '')
            + (' [ორსულობა: პრასუგრელი არ არის რეკომენდებული — FDA / EMA!]' if c['repro_pregnant'] else '')
        )
        pras_contra = bool(stroke_warn or pras_bad_combo or c['liver_3x'] or c['repro_pregnant'])
        if is_bleed:
            add_row('პრასუგრელი (ეფიენტი)', f'{_cea_bleed_rule(c, "პრასუგრელი")}{stroke_warn}', 'red', 'შეათანხმეთ ნეიროქირურგთან / ნევროლოგთან!', hold_or_skip=False, force_date_plan=True, contraindicated=True)
        elif c['active_gi_bleed']:
            add_row('პრასუგრელი (ეფიენტი)', f'აქტიური კუჭ-ნაწლავის სისხლდენა / მწვავე წყლული — სასწრაფოდ შეათანხმეთ გასტროენტეროლოგთან და კარდიოლოგთან!{stroke_warn}', 'red', _GI_BLEED_PLAN, hold_or_skip=False, force_date_plan=True, contraindicated=True, acute_hold=True)
        elif q.get('recent_stent') or has_acs_stent_12m:
            add_row('პრასუგრელი (ეფიენტი)', f'კორონარული სტენტი — თვითნებური მოხსნა აკრძალულია! კარდიოლოგ-ქირურგის კონსილიუმი{stroke_warn}{pras_esc_warn}{pras_risk_warn}', 'red', 'არ მოხსნათ კონსილიუმის გარეშე!', hold_or_skip=False, force_date_plan=True, contraindicated=pras_contra)
        elif has_stroke_hx:
            add_row('პრასუგრელი (ეფიენტი)', f'გადატანილი ინსულტის / TIA-ს ანამნეზში პრასუგრელი აბსოლუტურად უკუნაჩვენებია (FDA Boxed Warning) — დაუყოვნებლივ შეცვალეთ კარდიოლოგთან ({asa_sub_txt})!', 'red', 'უკუნაჩვენებია — დაუყოვნებლივ შეცვალეთ ექიმთან!', hold_or_skip=False, force_date_plan=True, contraindicated=True)
        elif c['liver_3x']:
            add_row('პრასუგრელი (ეფიენტი)', f'ღვიძლის მძიმე დისფუნქცია (≥3x ULN) — პრასუგრელი მკვეთრად ზრდის სისხლდენის რისკს (FDA Effient PI 5.1) — დაუყოვნებლივ შეათანხმეთ მოხსნა/ჩანაცვლება კარდიოლოგთან!{pras_esc_warn}{pras_risk_warn}', 'red', _IMMED_STOP_PLAN, hold_or_skip=False, force_date_plan=True, contraindicated=True, acute_hold=True)
        elif c['repro_pregnant']:
            add_row('პრასუგრელი (ეფიენტი)', f'ორსულობის დროს პრასუგრელი არ არის რეკომენდებული (FDA / EMA) — დაუყოვნებლივ შეათანხმეთ უსაფრთხო ალტერნატივა მეან-გინეკოლოგთან და კარდიოლოგთან!{pras_esc_warn}{pras_risk_warn}', 'red', _IMMED_STOP_PLAN, hold_or_skip=False, force_date_plan=True, contraindicated=True, acute_hold=True)
        elif is_pta and pras_bad_combo:
            add_row('პრასუგრელი (ეფიენტი)', f'OAC/დუბლირებულ P2Y12-თან ერთად ESC Class III სისხლდენის რისკი — შეათანხმეთ დე-ესკალაცია კარდიოლოგთან!{stroke_warn}{pras_risk_warn}', 'red', 'შეათანხმეთ დე-ესკალაცია!', hold_or_skip=False, force_date_plan=True, contraindicated=True)
        elif is_pta and not is_neuraxial and not stroke_warn and (clop_intol or solo_p2y_asa_alg):
            add_row('პრასუგრელი (ეფიენტი)', f'ენდოვასკულური PTA (ალერგიის/აუტანლობის ფონზე) — თვითნებურად არ მოხსნათ, შეათანხმეთ უსაფრთხო ალტერნატივა ქირურგთან/კარდიოლოგთან!{pras_risk_warn}', 'red', 'შეათანხმეთ კონსილიუმით!', hold_or_skip=False, force_date_plan=True, contraindicated=pras_contra)
        elif is_pta and not is_neuraxial and not stroke_warn:
            add_row('პრასუგრელი (ეფიენტი)', f'ენდოვასკულური PTA — შეათანხმეთ ქირურგთან (გაგრძელება ან კლოპიდოგრელზე დეესკალაცია){pras_risk_warn}', 'red' if pras_age75 else 'amber', f'შეათანხმეთ ქირურგთან ({d_str(0)})', hold_or_skip=True, reconcile_only=True)
        elif solo_p2y_asa_alg and not stroke_warn:
            add_row('პრასუგრელი (ეფიენტი)', f'ასპირინზე/აასს-ზე ალერგია — პრასუგრელის მონოთერაპია თვითნებურად არ მოხსნათ (უანტიაგრეგანტოდ დატოვება საშიშია)! შეათანხმეთ ქირურგთან/კარდიოლოგთან!{pras_esc_warn}{pras_risk_warn}', 'red', 'შეათანხმეთ ქირურგთან (ასპირინზე ალერგია!)', hold_or_skip=False, force_date_plan=True, contraindicated=pras_contra)
        else:
            hold_d = p2y_cfg['pras_nx'] if is_neuraxial else p2y_cfg['pras_std']
            pras_span = f'{p2y_cfg["pras_std"]}–{p2y_cfg["pras_nx"]}' if p2y_cfg['pras_std'] < p2y_cfg['pras_nx'] else f'{hold_d}'
            add_row('პრასუგრელი (ეფიენტი)', f'შეჩერება ოპერაციამდე და სპინალურ/ეპიდურულ ანესთეზიამდე {pras_span} დღით ({hold_d} დღე) ადრე (ASRA/ESC) — {asa_sub_txt}{stroke_warn}{pras_esc_warn}{pras_risk_warn}', 'red', hold_str(hold_d), hold_or_skip=True, contraindicated=pras_contra, mandatory_hold_days=hold_d)


def _append_cilostazol_row(c, cilo_cfg):
    q, s, med_chips = c['q'], c['s'], c['med_chips']
    if 'cilostazol' not in med_chips:
        return
    cilo_d, cilo_hrs = cilo_cfg['hold_days'], cilo_cfg['hold_days'] * 24
    cilo_crcl, cilo_lvef = cilo_cfg['crcl_min'], cilo_cfg['lvef_min']
    cilostazol_contra = (
        c['is_cea_oac_p2y_stop'] or c['chf_any'] or (c['lvef_cur'] is not None and c['lvef_cur'] < cilo_lvef)
        or c['on_dialysis'] or c['creat_extreme_hi'] or (c['eff_renal'] is not None and c['eff_renal'] <= cilo_crcl)
        or (s.get('ecg') == 'ischemia_arrhythmia') or (c['hr_cur'] is not None and c['hr_cur'] > cilo_cfg['hr_max'])
        or c['liver_3x'] or c['active_gi_bleed']
        or bool(q.get('recent_stent')) or c['has_acs_stent_12m'] or c['repro_pregnant']
    )
    ppi_cyp_warn = ' [CYP2C19: ომეპრაზოლს მოერიდეთ — უპირატესია პანტოპრაზოლი]' if ('ppi' in med_chips or q.get('ulcer') or s.get('ppi_started') or c['ft_meds']['has_omeprazole']) else ''
    if cilostazol_contra:
        c['add_row']('ცილოსტაზოლი (პლეტალი)', f'უკუნაჩვენებია გულის უკმარისობის / LVEF <{cilo_lvef}% / CrCl ≤{cilo_crcl} / ღვიძლის ≥3x / სტენტის / წყლულის / ტაქიარითმიის / სისხლჩაქცევის დროს (FDA Black Box / EMA) — შეათანხმეთ მოხსნა!{ppi_cyp_warn}', 'red', 'უკუნაჩვენებია — შეათანხმეთ მოხსნა!', hold_or_skip=False, force_date_plan=True, contraindicated=True)
    elif c['creat_mismatch']:
        c['add_row']('ცილოსტაზოლი (პლეტალი)', f'კრეატინინის ერთეული დასაზუსტებელია — CrCl >{cilo_crcl} უსაფრთხოებისთვის გადაამოწმეთ µmol/L / mg/dL!{ppi_cyp_warn}', 'red', 'ერთეული დასაზუსტებელია', hold_or_skip=True, force_date_plan=True, contraindicated=True, reconcile_only=True)
    elif c['creat_ambig'] or c['eff_renal'] is None:
        from .registry import rule_value
        ren_am = rule_value('SCORE_RENAL_CKDEPI_CG')
        c_msg = f'კრეატინინის ერთეული დასაზუსტებელია ({int(ren_am["ambig_lo"])}–{int(ren_am["ambig_hi"])} ზონა) — CrCl >{cilo_crcl} უსაფრთხოებისთვის მიუთითეთ µmol/L თუ mg/dL!' if c['creat_ambig'] else f'შეიყვანეთ კრეატინინი (CrCl / eGFR) — ცილოსტაზოლის უსაფრთხოებისთვის (CrCl >{cilo_crcl}) და {cilo_hrs} სთ შეჩერებამდე!'
        c_dp = 'ერთეული დასაზუსტებელია' if c['creat_ambig'] else 'შეიყვანეთ კრეატინინი თარიღისთვის'
        c['add_row']('ცილოსტაზოლი (პლეტალი)', f'{c_msg}{ppi_cyp_warn}', 'red', c_dp, hold_or_skip=True, force_date_plan=True, reconcile_only=True)
    else:
        c['add_row']('ცილოსტაზოლი (პლეტალი)', f'შეჩერება ოპერაციამდე / სპინალურ ბლოკადამდე {cilo_hrs} საათით ({cilo_d} დღით) ადრე (ASRA){ppi_cyp_warn}', 'amber', c['hold_str'](cilo_d, f' ({cilo_hrs} სთ)'), hold_or_skip=True, mandatory_hold_days=cilo_d)


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
        nx_d, nx_h = doac_cfg['neuraxial_days'], doac_cfg['neuraxial_days'] * 24
        m_d, m_h = doac_cfg['crcl_30_49_days'], doac_cfg['crcl_30_49_days'] * 24
        g_d, g_h = doac_cfg['crcl_ge50_days'], doac_cfg['crcl_ge50_days'] * 24
        lt_d, lt_h = doac_cfg['crcl_lt30_days'], doac_cfg['crcl_lt30_days'] * 24
        if c['is_cea_oac_p2y_stop']:
            add_row('DOAC (ქსარელტო / ელიქვისი)', _cea_bleed_rule(c, 'DOAC'), 'red', 'შეათანხმეთ ნეიროქირურგთან / ნევროლოგთან!', hold_or_skip=False, force_date_plan=True, contraindicated=True)
        elif c['is_rupture_suspect']:
            add_row('DOAC (ქსარელტო / ელიქვისი)', f'საეჭვო რუპტურირებული AAA — გადაუდებელი ოპერაცია დაუყოვნებელი რევერსიით: 4F-PCC 50 სე/კგ IV (მოერიდეთ ანდექსანეტ ალფას კლამპირებამდე — ESVS 2024; გეგმური შეჩერების მოლოდინი დაუშვებელია!){doac_extra_warn}', 'red', 'გადაუდებელი რევერსია (4F-PCC) — გადავადების გარეშე!', hold_or_skip=False, force_date_plan=True, contraindicated=True, acute_hold=True)
        elif has_mech_valve:
            add_row('DOAC (ქსარელტო / ელიქვისი)', f'მექანიკურ სარქველზე DOAC აბსოლუტურად უკუნაჩვენებია (ESC Class III — სარქვლის თრომბოზის რისკი)! სასწრაფოდ გადაამოწმეთ ვარფარინი (VKA) კარდიოლოგთან!{dup_oac_warn}', 'red', 'უკუნაჩვენებია — კარდიოლოგი!', hold_or_skip=False, force_date_plan=True, contraindicated=True, acute_hold=True)
        elif c['repro_pregnant']:
            add_row('DOAC (ქსარელტო / ელიქვისი)', f'ორსულობის დროს DOAC აბსოლუტურად უკუნაჩვენებია (პლაცენტური გადასვლა / ნაყოფის რისკი) — დაუყოვნებლივ შეცვალეთ {c["preg_oac_alt"]}{dup_oac_warn}', 'red', _IMMED_STOP_PLAN, hold_or_skip=False, force_date_plan=True, contraindicated=True, acute_hold=True)
        elif c['active_gi_bleed']:
            add_row('DOAC (ქსარელტო / ელიქვისი)', f'აქტიური კუჭ-ნაწლავის სისხლდენა / მწვავე წყლული — DOAC სასწრაფოდ შეათანხმეთ შეჩერება/ჰემოსტაზზე ექიმთან!{doac_extra_warn}', 'red', _GI_BLEED_PLAN, hold_or_skip=False, force_date_plan=True, contraindicated=True, acute_hold=True)
        elif on_dialysis:
            dial_h = doac_cfg['dialysis_days'] * 24
            add_row('DOAC (ქსარელტო / ელიქვისი)', f'დიალიზი / თირკმლის ტერმინალური უკმარისობა — შეჩერება ≥{lt_h}–{dial_h} სთ + ანტი-Xa კონტროლი და ნეფროლოგ/ქირურგის კონსილიუმი!{doac_extra_warn}', 'red', _IMMED_STOP_PLAN, hold_or_skip=True, force_date_plan=True, contraindicated=True, mandatory_hold_days=doac_cfg['dialysis_days'])
        elif c['creat_mismatch']:
            add_row('DOAC (ქსარელტო / ელიქვისი)', f'კრეატინინის ერთეული დასაზუსტებელია — DOAC-ის შეჩერების დროის ({g_h}–{lt_h}+ სთ) დასადგენად გადაამოწმეთ µmol/L / mg/dL!{doac_extra_warn}', 'red', 'ერთეული დასაზუსტებელია', hold_or_skip=True, force_date_plan=True, contraindicated=True, reconcile_only=True)
        elif c['creat_extreme_hi'] or (renal_cl is not None and renal_cl < doac_low):
            cl_hdr = cl_label if cl_label else 'CrCl/eGFR'
            add_row('DOAC (ქსარელტო / ელიქვისი)', f'{cl_hdr} (<{doac_low}) — შეჩერება ≥{lt_h} სთ-ით ადრე + ნეფროლოგ/ქირურგთან შეთანხმება (ნეირაქსიალური ბლოკადა მოერიდეთ!){frail_no_wt_note}{doac_extra_warn}', 'red', _IMMED_STOP_PLAN, hold_or_skip=True, force_date_plan=True, contraindicated=True, mandatory_hold_days=lt_d)
        elif renal_cl is None:
            cr_msg = f'კრეატინინის ერთეული დასაზუსტებელია (µmol/L თუ mg/dL) — DOAC-ის შეჩერების დრო ({g_h}–{lt_h}+ სთ) დამოკიდებულია თირკმლის ფუნქციაზე!' if c['creat_ambig'] else f'შეიყვანეთ კრეატინინი (CrCl / eGFR) — DOAC-ის შეჩერების დრო ({g_h}–{lt_h}+ სთ) დამოკიდებულია თირკმლის ფუნქციაზე!'
            cr_dp = _IMMED_STOP_PLAN if contra_doac else ('ერთეული დასაზუსტებელია' if c['creat_ambig'] else 'შეიყვანეთ კრეატინინი თარიღისთვის')
            add_row('DOAC (ქსარელტო / ელიქვისი)', f'{cr_msg}{doac_extra_warn}', 'red', cr_dp, hold_or_skip=True, force_date_plan=True, contraindicated=contra_doac, reconcile_only=True)
        elif is_evla and not is_neuraxial and not contra_doac:
            add_row('DOAC (ქსარელტო / ელიქვისი)', f'ESVS 2022: EVLA-ზე შესაძლებელია მოუხსნელად ან დილის დოზის გამოტოვებით{doac_extra_warn}{res_oac}', 'red' if force_doac_red else 'amber', f'გამოტოვეთ {d_str(0)}-ის დილას', hold_or_skip=True, contraindicated=contra_doac, reconcile_only=True)
        elif is_neuraxial:
            add_row('DOAC (ქსარელტო / ელიქვისი)', f'სპინალური/ეპიდურული ანესთეზია (ASRA 5th Ed): სავალდებულო შეჩერება მინიმუმ {nx_h} საათით ({nx_d} დღით) ადრე! ({cl_label}){frail_no_wt_note}{doac_extra_warn}{res_oac}', 'red' if force_doac_red else 'amber', _IMMED_STOP_PLAN if contra_doac else hold_str(nx_d, f' ({nx_h} სთ)'), hold_or_skip=True, force_date_plan=contra_doac, contraindicated=contra_doac, mandatory_hold_days=nx_d)
        elif renal_cl < doac_mid:
            add_row('DOAC (ქსარელტო / ელიქვისი)', f'{cl_label} ({doac_low}–{doac_mid - 1}) — შეჩერება ოპერაციამდე {m_h} საათით ({m_d} დღით) ადრე (EHRA 2021 / PAUSE){frail_no_wt_note}{doac_extra_warn}{res_oac}', 'red' if force_doac_red else 'amber', _IMMED_STOP_PLAN if contra_doac else hold_str(m_d, f' ({m_h} სთ)'), hold_or_skip=True, force_date_plan=contra_doac, contraindicated=contra_doac, mandatory_hold_days=m_d)
        else:
            add_row('DOAC (ქსარელტო / ელიქვისი)', f'შეჩერება ოპერაციამდე {g_h} საათით ({g_d} დღით) ადრე (EHRA 2021 / PAUSE; {cl_label}){frail_no_wt_note}{doac_extra_warn}{res_oac}', 'red' if force_doac_red else 'amber', _IMMED_STOP_PLAN if contra_doac else hold_str(g_d, f' ({g_h} სთ)'), hold_or_skip=True, force_date_plan=contra_doac, contraindicated=contra_doac, mandatory_hold_days=g_d)

    if q.get('med_dabigatran'):
        d_map = dabi_cfg['nx'] if is_neuraxial else dabi_cfg['std']
        if c['is_cea_oac_p2y_stop']:
            add_row('დაბიგატრანი (პრადაქსა)', _cea_bleed_rule(c, 'დაბიგატრანი'), 'red', 'შეათანხმეთ ნეიროქირურგთან / ნევროლოგთან!', hold_or_skip=False, force_date_plan=True, contraindicated=True)
        elif c['is_rupture_suspect']:
            add_row('დაბიგატრანი (პრადაქსა)', f'საეჭვო რუპტურირებული AAA — გადაუდებელი ოპერაცია დაუყოვნებელი რევერსიით: იდარუციზუმაბი (Praxbind) 5 გ IV (გეგმური შეჩერების მოლოდინი დაუშვებელია!){doac_extra_warn}', 'red', 'გადაუდებელი რევერსია (იდარუციზუმაბი 5 გ IV) — გადავადების გარეშე!', hold_or_skip=False, force_date_plan=True, contraindicated=True, acute_hold=True)
        elif has_mech_valve:
            add_row('დაბიგატრანი (პრადაქსა)', f'მექანიკურ სარქველზე დაბიგატრანი აბსოლუტურად უკუნაჩვენებია (RE-ALIGN / ESC Class III — თრომბოზის რისკი)! სასწრაფოდ გადაამოწმეთ ვარფარინი (VKA)!{dup_oac_warn}', 'red', 'უკუნაჩვენებია — კარდიოლოგი!', hold_or_skip=False, force_date_plan=True, contraindicated=True, acute_hold=True)
        elif c['repro_pregnant']:
            add_row('დაბიგატრანი (პრადაქსა)', f'ორსულობის დროს დაბიგატრანი აბსოლუტურად უკუნაჩვენებია (პლაცენტური გადასვლა / ნაყოფის რისკი) — დაუყოვნებლივ შეცვალეთ {c["preg_oac_alt"]}{dup_oac_warn}', 'red', _IMMED_STOP_PLAN, hold_or_skip=False, force_date_plan=True, contraindicated=True, acute_hold=True)
        elif c['active_gi_bleed']:
            add_row('დაბიგატრანი (პრადაქსა)', f'აქტიური კუჭ-ნაწლავის სისხლდენა / მწვავე წყლული — დაბიგატრანი სასწრაფოდ შეათანხმეთ შეჩერება/ჰემოსტაზზე ექიმთან!{doac_extra_warn}', 'red', _GI_BLEED_PLAN, hold_or_skip=False, force_date_plan=True, contraindicated=True, acute_hold=True)
        elif on_dialysis:
            add_row('დაბიგატრანი (პრადაქსა)', f'დიალიზი — დაბიგატრანი უკუნაჩვენებია (EHRA 2021), სავალდებულოა თრომბინის დროის (TT) კონტროლი და კონსილიუმი!{doac_extra_warn}', 'red', _IMMED_STOP_PLAN, hold_or_skip=True, force_date_plan=True, contraindicated=True, reconcile_only=True, mandatory_hold_days=d_map['lt30'])
        elif c['creat_mismatch']:
            add_row('დაბიგატრანი (პრადაქსა)', f'კრეატინინის ერთეული დასაზუსტებელია — პრადაქსას შეჩერებისა და უკუჩვენების დასადგენად გადაამოწმეთ µmol/L / mg/dL!{doac_extra_warn}', 'red', 'ერთეული დასაზუსტებელია', hold_or_skip=True, force_date_plan=True, contraindicated=True, reconcile_only=True)
        elif c['creat_extreme_hi'] or (renal_cl is not None and renal_cl < dabi_low):
            nx_tag = 'სპინალური/ეპიდურული ბლოკადა უკუნაჩვენებია (ASRA 5th Ed)! ' if is_neuraxial else ''
            cl_hdr = cl_label if cl_label else 'CrCl/eGFR'
            add_row('დაბიგატრანი (პრადაქსა)', f'{cl_hdr} (<{dabi_low}) — დაბიგატრანი უკუნაჩვენებია (EMA/EHRA 2021)! {nx_tag}შეჩერება ≥{d_map["lt30"] * 24} საათით + TT კონტროლი და ნეფროლოგ/კარდიოლოგთან ჩანაცვლება!{frail_no_wt_note}{doac_extra_warn}', 'red', _IMMED_STOP_PLAN, hold_or_skip=True, force_date_plan=True, contraindicated=True, mandatory_hold_days=d_map['lt30'])
        elif renal_cl is None:
            d_min_h, d_max_h = min(d_map.values()) * 24, max(d_map.values()) * 24
            cr_dmsg = f'კრეატინინის ერთეული დასაზუსტებელია (µmol/L თუ mg/dL) — პრადაქსას შეჩერების დრო ({d_min_h}–{d_max_h} სთ) მკაცრად დამოკიდებულია თირკმლის ფუნქციაზე!' if c['creat_ambig'] else f'შეიყვანეთ კრეატინინი და წონა (CrCl) — პრადაქსას შეჩერების დრო ({d_min_h}–{d_max_h} სთ) მკაცრად დამოკიდებულია თირკმლის ფუნქციაზე!'
            cr_ddp = _IMMED_STOP_PLAN if contra_doac else ('ერთეული დასაზუსტებელია' if c['creat_ambig'] else 'შეიყვანეთ CrCl თარიღისთვის')
            add_row('დაბიგატრანი (პრადაქსა)', f'{cr_dmsg}{doac_extra_warn}', 'red', cr_ddp, hold_or_skip=True, force_date_plan=True, contraindicated=contra_doac, reconcile_only=True)
        elif is_evla and not is_neuraxial and not contra_doac:
            add_row('დაბიგატრანი (პრადაქსა)', f'ESVS 2022: EVLA-ზე შესაძლებელია მოუხსნელად ან დილის დოზის გამოტოვებით — დააზუსტეთ ქირურგთან{doac_extra_warn}{res_oac}', 'red' if force_doac_red else 'amber', f'გამოტოვეთ {d_str(0)}-ის დილას', hold_or_skip=True, contraindicated=contra_doac, reconcile_only=True)
        else:
            tier_k = '30_49' if renal_cl < dabi_mid else ('50_79' if renal_cl < dabi_hi else 'ge80')
            hrs = d_map[tier_k] * 24
            tier_rng = f'{dabi_low}–{dabi_mid - 1}' if tier_k == '30_49' else (f'{dabi_mid}–{dabi_hi - 1}' if tier_k == '50_79' else f'≥{dabi_hi}')
            pfx = f'სპინალური/ეპიდურული (ASRA, {cl_label} {tier_rng}): შეჩერება ' if is_neuraxial else f'{cl_label} ({tier_rng}) — შეჩერება ოპერაციამდე '
            tone_d = 'red' if (force_doac_red or tier_k == '30_49') else 'amber'
            add_row('დაბიგატრანი (პრადაქსა)', f'{pfx}{hrs} საათით ({d_map[tier_k]} დღით) ადრე{frail_no_wt_note}{doac_extra_warn}{res_oac}', tone_d, _IMMED_STOP_PLAN if contra_doac else hold_str(d_map[tier_k], f' ({hrs} სთ)'), hold_or_skip=True, force_date_plan=contra_doac, contraindicated=contra_doac, mandatory_hold_days=d_map[tier_k])


def _append_rivaroxaban_row(c):
    from .registry import rule_value
    voy_cfg, triple_min = rule_value('MED_VOYAGER_RIVAROXABAN_2_5'), rule_value('MED_ANTITHROMBOTIC_CONFLICTS')
    q, add_row, d_str, hold_str = c['q'], c['add_row'], c['d_str'], c['hold_str']
    if 'rivaroxaban_2_5' not in c['med_chips']:
        return
    potent_p2y_riva = bool(q.get('med_ticagrelor') or q.get('med_prasugrel') or ((c['ft_meds'].get('unstructured_keys') or set()) & {'ticagrelor', 'prasugrel'}))
    contra_riva = bool(c['oac_count'] >= 2 or c['liver_3x'] or potent_p2y_riva)
    riva_red = c['force_doac_red'] or potent_p2y_riva
    doac_extra_warn, renal_cl = c['doac_extra_warn'], c['renal_cl']
    if c['is_cea_oac_p2y_stop']:
        add_row('ქსარელტო 2.5 მგ x2', _cea_bleed_rule(c, 'რივაროქსაბანი 2.5 მგ'), 'red', 'შეათანხმეთ ნეიროქირურგთან / ნევროლოგთან!', hold_or_skip=False, force_date_plan=True, contraindicated=True)
    elif c['is_rupture_suspect']:
        add_row('ქსარელტო 2.5 მგ x2', f'საეჭვო რუპტურირებული AAA — გადაუდებელი ოპერაცია (სისხლდენისას 4F-PCC; გეგმური შეჩერების მოლოდინი დაუშვებელია!){doac_extra_warn}', 'red', 'გადაუდებელი ოპერაცია — გადავადების გარეშე!', hold_or_skip=False, force_date_plan=True, contraindicated=True, acute_hold=True)
    elif c['has_mech_valve']:
        add_row('ქსარელტო 2.5 მგ x2', f'მექანიკურ სარქველზე რივაროქსაბანი აბსოლუტურად უკუნაჩვენებია (ESC Class III)! გადაამოწმეთ ვარფარინი!{c["dup_oac_warn"]}', 'red', _IMMED_STOP_PLAN, hold_or_skip=False, force_date_plan=True, contraindicated=True, acute_hold=True)
    elif c['repro_pregnant']:
        add_row('ქსარელტო 2.5 მგ x2', f'ორსულობის დროს რივაროქსაბანი 2.5 მგ ×2 (VOYAGER PAD) აბსოლუტურად უკუნაჩვენებია — დაუყოვნებლივ შეცვალეთ {c["preg_oac_alt"]}{doac_extra_warn}', 'red', _IMMED_STOP_PLAN, hold_or_skip=False, force_date_plan=True, contraindicated=True, acute_hold=True)
    elif 'nsaid' in c['eff_allergy_chips']:
        add_row('ქსარელტო 2.5 მგ x2', f'ასპირინზე / აასს-ზე ალერგია — VOYAGER PAD რეჟიმი უკუნაჩვენებია (მოითხოვს ასპირინს)! დაუყოვნებლივ შეცვალეთ ალტერნატივით ქირურგთან!{doac_extra_warn}', 'red', _IMMED_STOP_PLAN, hold_or_skip=False, force_date_plan=True, contraindicated=True, acute_hold=True)
    elif c['active_gi_bleed']:
        add_row('ქსარელტო 2.5 მგ x2', f'აქტიური კუჭ-ნაწლავის სისხლდენის / მწვავე წყლულის დროს რივაროქსაბანი 2.5 მგ ×2 (VOYAGER PAD) აბსოლუტურად უკუნაჩვენებია — შეწყვიტეთ დაუყოვნებლივ და შეათანხმეთ ქირურგთან!{doac_extra_warn}', 'red', _GI_BLEED_PLAN, hold_or_skip=False, force_date_plan=True, contraindicated=True, acute_hold=True)
    elif c['has_stroke_hx']:
        add_row('ქსარელტო 2.5 მგ x2', f'გადატანილი ინსულტის / TIA-ს ანამნეზში რივაროქსაბანი 2.5 მგ ×2 (VOYAGER PAD) უკუნაჩვენებია (ქალასშიდა სისხლდენის რისკი) — შეათანხმეთ მოხსნა/შეცვლა ქირურგთან!{doac_extra_warn}', 'red', _IMMED_STOP_PLAN, hold_or_skip=False, force_date_plan=True, contraindicated=True, acute_hold=True)
    elif c['creat_mismatch']:
        add_row('ქსარელტო 2.5 მგ x2', f'კრეატინინის ერთეული დასაზუსტებელია — რივაროქსაბანის შეჩერებისა და უსაფრთხოებისთვის გადაამოწმეთ µmol/L / mg/dL!{doac_extra_warn}', 'red', 'ერთეული დასაზუსტებელია', hold_or_skip=True, force_date_plan=True, contraindicated=True, reconcile_only=True)
    elif c['on_dialysis'] or c['creat_extreme_hi'] or (renal_cl is not None and renal_cl < voy_cfg['min_egfr']):
        ren_h = voy_cfg['renal_days'] * 24
        add_row('ქსარელტო 2.5 მგ x2', f'თირკმლის მძიმე უკმარისობა / დიალიზი — შეჩერება ≥{max(24, ren_h - 24)}–{ren_h} სთ-ით ადრე (ASRA 5th Ed დაბალ-დოზიანი ≥30 სთ) და სქემის გადახედვა ქირურგთან!{doac_extra_warn}', 'red', _IMMED_STOP_PLAN, hold_or_skip=True, force_date_plan=True, contraindicated=True, mandatory_hold_days=voy_cfg['renal_days'])
    elif renal_cl is None:
        cr_rmsg = 'კრეატინინის ერთეული დასაზუსტებელია (µmol/L თუ mg/dL) — რივაროქსაბანის შეჩერების დრო დამოკიდებულია თირკმლის ფუნქციაზე!' if c['creat_ambig'] else 'შეიყვანეთ კრეატინინი (CrCl / eGFR) — რივაროქსაბანის შეჩერების დრო დამოკიდებულია თირკმლის ფუნქციაზე!'
        cr_rdp = _IMMED_STOP_PLAN if contra_riva else ('ერთეული დასაზუსტებელია' if c['creat_ambig'] else 'შეიყვანეთ კრეატინინი თარიღისთვის')
        add_row('ქსარელტო 2.5 მგ x2', f'{cr_rmsg}{doac_extra_warn}', 'red', cr_rdp, hold_or_skip=True, force_date_plan=True, contraindicated=contra_riva, reconcile_only=True)
    elif c['is_evla'] and not c['is_neuraxial'] and not riva_red:
        t3_ev = c['total_at_cnt'] >= triple_min
        add_row('ქსარელტო 2.5 მგ x2', 'EVLA-ზე შესაძლებელია დილის დოზის გამოტოვებით (ასპირინი გრძელდება) · აღდგენა: ~24 სთ-ში ჰემოსტაზით' + (f' [≥{triple_min} ანტითრომბოზული — დე-ესკალაცია!]' if t3_ev else ''), 'amber' if t3_ev else 'ok', f'გამოტოვეთ {d_str(0)}-ის დილას', hold_or_skip=True, reconcile_only=True)
    elif c['is_neuraxial']:
        nx_h = voy_cfg['nx_days'] * 24
        nx_res = '' if contra_riva else '; აღდგენა ≥6 სთ კათეტერიდან'
        add_row('ქსარელტო 2.5 მგ x2', f'შეჩერება სპინალურ/ეპიდურულ ბლოკადამდე ≥{max(24, nx_h - 24)}–{nx_h} სთ ადრე (ASRA 5th Ed დაბალი დოზა ≥24 სთ{nx_res}) — ასპირინი გრძელდება{doac_extra_warn}', 'red' if riva_red else 'amber', _IMMED_STOP_PLAN if contra_riva else hold_str(voy_cfg['nx_days']), hold_or_skip=True, force_date_plan=contra_riva, contraindicated=contra_riva, mandatory_hold_days=voy_cfg['nx_days'])
    else:
        std_h = voy_cfg['std_days'] * 24
        p2y_sfx = ' [OAC + ტიკაგრელორი/პრასუგრელი ESC Class III — შეათანხმეთ დე-ესკალაცია!]' if potent_p2y_riva else ''
        riva_res = '' if contra_riva else ' · აღდგენა: 24–48 სთ-ში ჰემოსტაზით'
        add_row('ქსარელტო 2.5 მგ x2', f'შეჩერება ოპერაციამდე {max(24, std_h - 24)}–{std_h} საათით ადრე (ასპირინი 75–100 მგ გრძელდება უწყვეტად){riva_res}{doac_extra_warn}{p2y_sfx}', 'red' if riva_red else 'amber', _IMMED_STOP_PLAN if contra_riva else hold_str(voy_cfg['std_days']), hold_or_skip=True, force_date_plan=contra_riva, contraindicated=contra_riva, mandatory_hold_days=voy_cfg['std_days'])


def _append_warfarin_row(c):
    from .calculators import _s_raw, _sat_num
    from .registry import rule_value
    vka_cfg, cea_recent_d = rule_value('MED_WARFARIN_HOLD_DAYS'), rule_value('SCORE_ASA_PHYSICAL_STATUS')['cea_recent_d']
    mech_inr_min = rule_value('MED_MECH_VALVE_SAFETY')
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
    liver_vka_warn = f' [ღვიძლის ≥3x დისფუნქცია: ვარფარინის გამორეცხვა შეიძლება გახანგრძლივდეს >{vka_cfg["hold_days"]} დღემდე — გადაამოწმეთ INR!]' if c['liver_3x'] else ''
    dup_vka = c['oac_count'] >= 2
    has_ft_par_vka = bool((c['ft_meds'].get('matched_keys') or set()) & {'parenteral_oac', 'parenteral_dti'})
    asra_cfg = rule_value('ASRA_NEURAXIAL_COAG_GATE')
    asra_inr_max, asra_inr_norm = asra_cfg['inr_max'], asra_cfg['inr_vka_norm']
    raw_s_v = c['patient'].studies if isinstance(c['patient'].studies, dict) else {}
    inv_v = s.get('_invalid_inputs') if isinstance(s.get('_invalid_inputs'), dict) else {}
    raw_dos_v = _pick_study_raw(s, raw_s_v, inv_v, 'dos_inr')
    dos_inr_pres_v = _has_raw_scalar(raw_dos_v) or ('dos_inr' in inv_v)
    dos_inr_cur = _sat_num(raw_dos_v, 0.5, 15.0)
    dos_inr_bad_v = dos_inr_pres_v and (_num(raw_dos_v, 0.5, 15.0) is None)
    inr_cur = _sat_num(_s_raw(s, 'inr'), 0.4, 20.0)
    eff_inr_vka = dos_inr_cur if dos_inr_pres_v else inr_cur
    evla_subther_mv = bool(has_mech_valve and eff_inr_vka is not None and eff_inr_vka < mech_inr_min)
    if c['is_cea_oac_p2y_stop']:
        add_row('ვარფარინი', f'{_cea_bleed_rule(c, "ვარფარინი / ანტიკოაგულაცია")}{liver_vka_warn}{dup_oac_warn}', 'red', 'შეათანხმეთ ნეიროქირურგთან / ნევროლოგთან!', hold_or_skip=False, force_date_plan=True, contraindicated=True)
    elif c['is_rupture_suspect']:
        add_row('ვარფარინი', f'საეჭვო რუპტურირებული AAA — გადაუდებელი ოპერაცია დაუყოვნებელი რევერსიით: 4F-PCC 25–50 სე/კგ IV + ვიტამინი K 10 მგ IV (ESVS 2024; გეგმური {vka_cfg["hold_days"]}-დღიანი შეჩერების მოლოდინი დაუშვებელია!){liver_vka_warn}{dup_oac_warn}', 'red', 'გადაუდებელი რევერსია (4F-PCC + ვიტ. K) — გადავადების გარეშე!', hold_or_skip=False, force_date_plan=True, contraindicated=True, acute_hold=True)
    elif c['repro_pregnant']:
        if has_mech_valve and c['has_hit']:
            add_row('ვარფარინი', f'ორსულობა + მექანიკური სარქველი + HIT — ვარფარინი ტერატოგენულია, თვითნებური მოხსნა იწვევს თრომბოზს; UFH, LMWH და DOAC აკრძალულია! სასწრაფოდ მეან-გინეკოლოგის, კარდიოლოგისა და ჰემატოლოგის კონსილიუმი!{dup_oac_warn}', 'red', 'ორსულობა + მექანიკური სარქველი + HIT — ჰემატოლოგის, კარდიოლოგისა და მეანის კონსილიუმი (UFH/LMWH აკრძალულია)!', hold_or_skip=False, force_date_plan=True, contraindicated=True, acute_hold=True)
        elif has_mech_valve:
            add_row('ვარფარინი', f'ორსულობა + მექანიკური სარქველი — ვარფარინი ტერატოგენულია, თვითნებური მოხსნა იწვევს თრომბოზს! სასწრაფოდ მეან-გინეკოლოგისა და კარდიოლოგის კონსილიუმი!{dup_oac_warn}', 'red', 'ორსულობა + მექანიკური სარქველი — კონსილიუმი!', hold_or_skip=False, force_date_plan=True, contraindicated=True, acute_hold=True)
        else:
            add_row('ვარფარინი', f'ორსულობის დროს ვარფარინი (VKA) ტერატოგენულია (ნაყოფის ვარფარინის სინდრომი) — დაუყოვნებლივ შეცვალეთ {c["preg_oac_alt"]}{dup_oac_warn}', 'red', _IMMED_STOP_PLAN, hold_or_skip=False, force_date_plan=True, contraindicated=True, acute_hold=True)
    elif c['active_gi_bleed']:
        add_row('ვარფარინი', f'აქტიური კუჭ-ნაწლავის სისხლდენა / მწვავე წყლული — ვარფარინი სასწრაფოდ შეათანხმეთ შეჩერება/ჰემოსტაზზე ექიმთან (მექ. სარქველზე — კარდიოლოგის კონსილიუმი)!{liver_vka_warn}{dup_oac_warn}', 'red', _GI_BLEED_PLAN, hold_or_skip=False, force_date_plan=True, contraindicated=True, acute_hold=True)
    elif is_evla and not is_neuraxial and not dup_vka and not c['liver_3x'] and not has_ft_par_vka:
        if dos_inr_bad_v:
            add_row('ვარფარინი', f'ოპერაციის დილის INR დაუშვებელია EVLA-ს წინ — გადაამოწმეთ საკონტროლო INR (≤ {vka_cfg["inr_evla_max"]}) ჩარევამდე!{dup_oac_warn}', 'red', 'INR დაუშვებელია — გადაამოწმეთ!', hold_or_skip=False, force_date_plan=True, contraindicated=True)
        elif eff_inr_vka is not None and eff_inr_vka > vka_cfg['inr_evla_max']:
            add_row('ვარფარინი', f'სუპრათერაპიული INR ({eff_inr_vka} > {vka_cfg["inr_evla_max"]}) EVLA-ს წინ — სისხლდენის რისკი, გადაამოწმეთ/შეაჩერეთ დოზა ქირურგთან!{liver_vka_warn}{dup_oac_warn}', 'red', f'INR {eff_inr_vka} > {vka_cfg["inr_evla_max"]} — გადაამოწმეთ!', hold_or_skip=False, force_date_plan=True, contraindicated=True)
        elif evla_subther_mv:
            add_row('ვარფარინი', f'მექანიკურ სარქველზე INR სუბთერაპიულია ({eff_inr_vka} < {mech_inr_min}) EVLA-ს წინ — სარქვლის თრომბოზის რისკი! არ შეწყვიტოთ ვარფარინი, სასწრაფოდ შეაფასეთ ხიდი კარდიოლოგთან!', 'red', f'INR {eff_inr_vka} < {mech_inr_min} — სასწრაფოდ კარდიოლოგთან!', hold_or_skip=False, force_date_plan=True, contraindicated=True)
        else:
            add_row('ვარფარინი', f'ESVS 2022: EVLA შესაძლებელია მოუხსნელად, თუ INR ≤ {vka_cfg["inr_evla_max"]}{liver_vka_warn}{dup_oac_warn}', 'amber', f'INR ≤{vka_cfg["inr_evla_max"]} შემოწმება {d_str(-1)}', hold_or_skip=False)
    else:
        inr_target = f'INR ≤ {asra_inr_max} (ASRA 5th Ed: ნეირაქსიულამდე სასურველია ≤{asra_inr_norm} ნორმალიზება!)' if is_neuraxial else f'INR ≤ {vka_cfg["inr_surg_max"]} (ქირურგიული ჰემოსტაზისთვის)'
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
        bridge_dp = ''
        if not dup_vka and high_thromb_vka:
            if has_mech_valve and c['has_hit']:
                bridge_dp = ' · სავალდებულოა სტაციონარული IV ხიდი (ბივალირუდინი/არგატრობანი — HIT: LMWH/ჰეპარინი არა!)'
            elif c['has_hit']:
                bridge_dp = ' · მაღალი თრომბორისკი + HIT: არაჰეპარინული ხიდი ჰემატოლოგთან (LMWH/ჰეპარინი არა!)'
            elif c['severe_renal']:
                bridge_dp = ' · სავალდებულოა სტაციონარული IV UFH ხიდი (CrCl<30/დიალიზი: თვითნებურად არ შეწყვიტოთ!)'
            else:
                bridge_dp = f' · სავალდებულო LMWH ხიდით (~{d_str(-3)}-დან, 50% დოზა {d_str(-1)} დილას)'
        vka_dp = _IMMED_STOP_PLAN if dup_vka else f'{hold_str(vka_cfg["hold_days"])} · INR კონტროლი {d_str(-1)}{bridge_dp}'
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
        if c['repro_pregnant']:
            add_row('SGLT2 (ჯარდიანსი / ფორსიგა)', 'ორსულობის დროს SGLT2 ინჰიბიტორი უკუნაჩვენებია (ნაყოფის ნეფროტოქსიკურობის რისკი) — დაუყოვნებლივ შეწყვიტეთ და შეათანხმეთ ენდოკრინოლოგთან და მეან-გინეკოლოგთან!', 'red', _IMMED_STOP_PLAN, hold_or_skip=False, force_date_plan=True, contraindicated=True, acute_hold=True)
        elif on_dialysis:
            add_row('SGLT2 (ჯარდიანსი / ფორსიგა)', 'პროგრამულ დიალიზზე SGLT2 ინჰიბიტორი უკუნაჩვენებია (FDA / KDIGO) — დაუყოვნებლივ შეათანხმეთ მოხსნა ნეფროლოგთან და ენდოკრინოლოგთან!', 'red', _IMMED_STOP_PLAN, hold_or_skip=False, force_date_plan=True, contraindicated=True, acute_hold=True)
        else:
            std_d, ert_d = sglt2_cfg['std_days'], sglt2_cfg['ertugliflozin_days']
            hold_sglt2 = ert_d if c['ft_meds']['ertugliflozin'] else std_d
            hrs_sglt2 = f'{hold_sglt2 * 24} სთ'
            glp_dka = f' [GLP-1-ის {glp_cfg["liquid_hours"]} სთ თხევადი დიეტა აძლიერებს DKA-ს რისკს — კეტონების მკაცრი კონტროლი!]' if (q.get('glp1') or c['ft_meds']['has_glp1']) else ''
            add_row(
                'SGLT2 (ჯარდიანსი / ფორსიგა)',
                f'სავალდებულო შეჩერება ოპერაციამდე {std_d * 24} საათით ({std_d} დღით) ადრე (ერტუგლიფლოზინი/სტეგლატრო — {ert_d * 24} სთ / {ert_d} დღით ადრე) + ოპერაციის დილას სისხლის კეტონების კონტროლი (ეუგლიკემიური DKA-ს პრევენცია)! აღდგენა მხოლოდ ნორმალური პერორალური კვების აღდგენის შემდეგ.{glp_dka}',
                'amber', hold_str(hold_sglt2, f' ({hrs_sglt2})'), hold_or_skip=True, mandatory_hold_days=hold_sglt2,
            )

    if q.get('glp1'):
        if c['repro_pregnant']:
            add_row('GLP-1 (ოზემპიკი / მუნჯარო / საქსენდა)', 'ორსულობის დროს GLP-1 აგონისტი უკუნაჩვენებია (ნაყოფზე ტოქსიკური რისკი) — დაუყოვნებლივ შეწყვიტეთ და შეათანხმეთ ენდოკრინოლოგთან და მეან-გინეკოლოგთან!', 'red', _IMMED_STOP_PLAN, hold_or_skip=False, force_date_plan=True, contraindicated=True, acute_hold=True)
        else:
            daily_glp = c['ft_meds']['daily_glp1']
            wk_d = glp_cfg['weekly_hold_days']
            hold_hint = f'შეჩერება ({d_str(0)} დილას, დღიური)' if daily_glp else f'შეჩერება ({d_str(-wk_d)}-დან) მხოლოდ ექიმით'
            add_row(
                'GLP-1 (ოზემპიკი / მუნჯარო / საქსენდა)',
                f'Multi-Society 2024 (ASA/AGA/ASMBS/ISPCOP/SAGES): გულისრევა/ღებინებისა და დოზის მატების ფაზის გარეშე უმეტეს პაციენტთან გრძელდება + სავალდებულოა ოპერაციამდე {glp_cfg["liquid_hours"]} სთ სუფთა თხევადი დიეტა! შეჩერება (კვირეული {wk_d} დღით / დღიური ოპერაციის დღეს) — მხოლოდ ექიმის/ანესთეზიოლოგის გადაწყვეტილებით მაღალი GI რისკისას.',
                'amber', f'{d_str(-1)} თხევადი დიეტა ({glp_cfg["liquid_hours"]} სთ) · {hold_hint}', hold_or_skip=True, glp1_diet=True, reconcile_only=True,
            )

    if q.get('metformin'):
        m_con, m_cap, h_hrs = metf_cfg['contra_renal'], metf_cfg['dose_cap_renal'], metf_cfg['hold_hours']
        pure_local_evla = c['is_evla'] and not c['is_neuraxial'] and not c['lido_alg'] and c['anesth_plan'] not in ('general', 'plexus', 'sedation')
        dose_cap = f' [CrCl/eGFR {m_con}–{m_cap - 1}: მაქს. დღიური დოზა ≤1000 მგ/დღ!]' if (eff_renal is not None and eff_renal < m_cap) else ''
        if c['creat_mismatch']:
            add_row(
                'მეტფორმინი',
                f'კრეატინინის ერთეული დასაზუსტებელია — მეტფორმინის უსაფრთხოებისთვის (CrCl/eGFR ≥{m_con}) დაუყოვნებლივ გადაამოწმეთ µmol/L / mg/dL!',
                'red', 'ერთეული დასაზუსტებელია', hold_or_skip=False, force_date_plan=True, contraindicated=True, reconcile_only=True,
            )
        elif on_dialysis or c['creat_extreme_hi'] or (eff_renal is not None and eff_renal < m_con):
            add_row(
                'მეტფორმინი',
                f'{c["cl_label"] or "CrCl/eGFR"} < {m_con} / დიალიზი — მეტფორმინი უკუნაჩვენებია (ლაქტატ-აციდოზის რისკი)! შეწყვიტეთ დაუყოვნებლივ და შეათანხმეთ ენდოკრინოლოგთან!',
                'red', 'შეწყვიტეთ დაუყოვნებლივ!', hold_or_skip=False, force_date_plan=True, contraindicated=True,
            )
        elif liver_3x:
            add_row(
                'მეტფორმინი',
                'ღვიძლის მძიმე დისფუნქცია (≥3x ULN) — მეტფორმინი უკუნაჩვენებია ლაქტატ-აციდოზის გამო! შეწყვიტეთ ექიმთან შეთანხმებით!',
                'red', 'შეწყვიტეთ დაუყოვნებლივ!', hold_or_skip=False, force_date_plan=True, contraindicated=True,
            )
        elif c['creat_ambig'] or eff_renal is None:
            ren_am = rule_value('SCORE_RENAL_CKDEPI_CG')
            m_miss_rule = (
                f'კრეატინინის ერთეული დასაზუსტებელია ({int(ren_am["ambig_lo"])}–{int(ren_am["ambig_hi"])} ზონა) — მეტფორმინის უსაფრთხოებისთვის (CrCl/eGFR ≥{m_con}, ლაქტატ-აციდოზის გამორიცხვა) მიუთითეთ µmol/L თუ mg/dL!'
                if c['creat_ambig'] else
                f'შეიყვანეთ კრეატინინი (eGFR/CrCl) — მეტფორმინის უსაფრთხოებისთვის (≥{m_con}) და {h_hrs} სთ შეჩერების საჭიროების დასადგენად!'
            )
            m_miss_dp = 'ერთეული დასაზუსტებელია' if c['creat_ambig'] else 'შეიყვანეთ კრეატინინი რეჟიმისთვის'
            add_row('მეტფორმინი', m_miss_rule, 'red', m_miss_dp, hold_or_skip=True, force_date_plan=True, reconcile_only=True)
        elif (
            c['is_ia_first_pass']
            or (c['uses_contrast'] and eff_renal < metf_cfg['contrast_hold_renal'])
            or c['is_open_major']
            or (not pure_local_evla and eff_renal < m_cap)
        ):
            metf_day_lbl = 'კონტრასტის/ჩარევის დღეს' if c['uses_contrast'] else 'ოპერაციის დღეს'
            add_row(
                'მეტფორმინი',
                f'შეჩერება {metf_day_lbl} {h_hrs} სთ-ით — აღდგენა მხოლოდ {h_hrs} სთ-ის შემდეგ თირკმლის საკონტროლო კრეატინინის/eGFR გადამოწმებით (ESUR v10 / FDA){dose_cap}',
                'amber', f'არ მიიღოთ {d_str(0)}–{d_str(1)} ({h_hrs} სთ · კრეატინინის კონტროლით)', hold_or_skip=True,
            )
        elif pure_local_evla:
            add_row('მეტფორმინი', f'გამოტოვება პროცედურის დილას (ადგილობრივ არაკონტრასტულ EVLA-ზე {h_hrs} სთ შეჩერება არ ესაჭიროება){dose_cap}', 'amber' if dose_cap else 'ok', f'გამოტოვეთ {d_str(0)}-ის დილას', hold_or_skip=True)
        else:
            add_row('მეტფორმინი', f'გამოტოვება ოპერაციის დილას (უზმოზე; არაკონტრასტულ/მეორადი-გავლის ჩარევაზე {h_hrs} სთ შეჩერება არ ესაჭიროება){dose_cap}', 'amber' if dose_cap else 'ok', f'გამოტოვეთ {d_str(0)}-ის დილას', hold_or_skip=True)


def _append_cardio_other_rows(c):
    from .registry import rule_value
    vaso_cfg = rule_value('MED_CARDIO_VASOACTIVE_STEROID')
    amio_renal = rule_value('MED_DOAC_HOLD_INTERVALS')['amio_renal']
    lvef_red_lim = rule_value('CARDIO_BIOMARKERS_ECHO')['lvef_red']
    bb_sbp, bb_hr, ace_k, ccb_sbp = vaso_cfg['bb_min_sbp'], vaso_cfg['bb_min_hr'], vaso_cfg['ace_max_k'], vaso_cfg['ccb_min_sbp']
    patient, q, pw = c['patient'], c['q'], c['pw']
    add_row, d_str, med_chips = c['add_row'], c['d_str'], c['med_chips']
    bp_sys_cur, bp_inv_only, hr_cur, k_cur, eff_renal = c['bp_sys_cur'], c['bp_inverted_only'], c['hr_cur'], c['k_cur'], c['eff_renal']

    if 'statin' in med_chips:
        if c['repro_pregnant']:
            add_row('სტატინი', 'ორსულობის დროს სტატინი უკუნაჩვენებია (ტერატოგენული რისკი) — დაუყოვნებლივ შეწყვიტეთ და შეათანხმეთ ექიმთან!', 'red', _IMMED_STOP_PLAN, hold_or_skip=False, force_date_plan=True, contraindicated=True, acute_hold=True)
        elif c['liver_3x']:
            add_row('სტატინი', 'ღვიძლის ფერმენტები ≥3x ULN — გადაამოწმეთ მწვავე ტრანსამინიტი ექიმთან სტატინის გაგრძელებამდე!', 'red', 'ღვიძლი ≥3x — შეათანხმეთ ექიმთან!', hold_or_skip=False, force_date_plan=True, contraindicated=True)
        else:
            add_row('სტატინი', 'გრძელდება უწყვეტად ოპერაციის დილის ჩათვლით (ESVS/ESC Class I)', 'ok', f'მიიღეთ {d_str(0)}-ის დილის ჩათვლით', hold_or_skip=False)
    if 'beta_blocker' in med_chips:
        bb_hold_vitals = bp_inv_only or (bp_sys_cur is not None and bp_sys_cur < bb_sbp) or (hr_cur is not None and hr_cur < bb_hr)
        bb_plan = 'გადაამოწმეთ წნევა დილის დოზამდე! (SBP≤DBP)' if bp_inv_only else (f'გამოტოვეთ დილას! (SBP<{bb_sbp} ან HR<{bb_hr})' if bb_hold_vitals else f'მიიღეთ {d_str(0)}-ის დილას (თუ SBP≥{bb_sbp}, HR≥{bb_hr})')
        add_row('ბეტა-ბლოკატორი', f'გრძელდება უწყვეტად ოპერაციის დილას ყლუპი წყლით, თუ SBP ≥ {bb_sbp} mmHg და HR ≥ {bb_hr}/წთ (დაბალ წნევა/პულსზე დილის დოზა გამოტოვეთ! — ESC 2022 Class I)', 'amber' if bb_hold_vitals else 'ok', bb_plan, hold_or_skip=bb_hold_vitals, force_date_plan=bb_hold_vitals, acute_hold=bb_hold_vitals)
    if 'acei_arb' in med_chips:
        ace_hold_vitals = bp_inv_only or (bp_sys_cur is not None and bp_sys_cur < bb_sbp) or (k_cur is not None and k_cur >= ace_k)
        if c['repro_pregnant']:
            add_row('ACEi / ARB (პრილი/სარტანი)', 'ორსულობის დროს ACEi / ARB აბსოლუტურად უკუნაჩვენებია (ნაყოფის ნეფროტოქსიკურობა — FDA Boxed Warning) — დაუყოვნებლივ შეცვალეთ ექიმთან!', 'red', _IMMED_STOP_PLAN, hold_or_skip=False, force_date_plan=True, contraindicated=True, acute_hold=True)
        elif ace_hold_vitals:
            ace_plan = 'გადაამოწმეთ წნევა დილის დოზამდე! (SBP≤DBP)' if bp_inv_only else f'გამოტოვეთ დილას! (SBP<{bb_sbp} / K+≥{ace_k})'
            add_row('ACEi / ARB (პრილი/სარტანი)', f'საბაზისო წნევა არასწორია/დაბალია (SBP < {bb_sbp} / SBP≤DBP) ან K+ ≥ {ace_k} მმოლ/ლ — ოპერაციის დილის დოზა გადაამოწმეთ / გამოტოვეთ!', 'amber', ace_plan, hold_or_skip=True, force_date_plan=True, acute_hold=True)
        elif c['has_true_hfref']:
            add_row('ACEi / ARB (პრილი/სარტანი)', f'ჭეშმარიტი HFrEF (LVEF <{lvef_red_lim}%) — გრძელდება ანესთეზიოლოგთან შეთანხმებით (თუ SBP ≥ {bb_sbp} mmHg და K+ < {ace_k} — ESC 2022)', 'amber', f'შეათანხმეთ HFrEF-ზე ({d_str(0)})', hold_or_skip=False)
        elif c['is_open_major'] or c['is_neuraxial'] or pw == PreopPatient.Pathway.CEA or c['anesth_plan'] == 'general' or patient.op_subtype in ('evar', 'tbd'):
            add_row('ACEi / ARB (პრილი/სარტანი)', 'გამოტოვება ოპერაციის დილას (ინტრაოპერაციული ჰიპოტენზიის პრევენცია — POISE-3; HFpEF-ზეც დილას ჩერდება)', 'amber', f'გამოტოვეთ {d_str(0)}-ის დილას', hold_or_skip=True)
        else:
            add_row('ACEi / ARB (პრილი/სარტანი)', f'გრძელდება ჩვეულ რეჟიმში (თუ წნევა სტაბილურია ≥{bb_sbp} mmHg)', 'ok', f'მიიღეთ ჩვეულ რეჟიმში ({d_str(0)})', hold_or_skip=False)
    if 'ccb' in med_chips:
        ccb_hold = bp_inv_only or (bp_sys_cur is not None and bp_sys_cur < ccb_sbp)
        ccb_plan = 'გადაამოწმეთ წნევა დილის დოზამდე! (SBP≤DBP)' if bp_inv_only else (f'გამოტოვეთ დილას! (SBP<{ccb_sbp})' if ccb_hold else f'მიიღეთ {d_str(0)}-ის დილას (SBP≥{ccb_sbp})')
        add_row('ამლოდიპინი / CCB', f'გამოტოვეთ დილის დოზა დაბალი/არასწორი წნევის (SBP < {ccb_sbp} / SBP≤DBP) გამო!' if ccb_hold else f'გრძელდება ჩვეულ რეჟიმში ოპერაციის დილას, თუ SBP ≥ {ccb_sbp} mmHg', 'amber' if ccb_hold else 'ok', ccb_plan, hold_or_skip=ccb_hold, force_date_plan=ccb_hold, acute_hold=ccb_hold)
    if 'amiodarone' in med_chips:
        amio_doac = (q.get('med_doac') or q.get('med_dabigatran') or 'rivaroxaban_2_5' in med_chips or c['has_ft_unstruct_doac'] or c['has_ft_unstruct_riva_low']) and (c['on_dialysis'] or (eff_renal is not None and eff_renal < amio_renal))
        amio_pgp_warn = f' [P-gp ურთიერთქმედება: DOAC/დაბიგატრანთან და CrCl <{amio_renal}/დიალიზზე ზრდის სისხლდენის რისკს!]' if amio_doac else ''
        amio_vka_warn = ' [CYP2C9: ვარფარინთან აძლიერებს ანტიკოაგულაციას — გადაამოწმეთ INR!]' if (q.get('med_warfarin') or c['has_ft_unstruct_warfarin']) else ''
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
        if c['repro_pregnant']:
            add_row('ჰორმონული პრეპარატი / კონტრაცეპტივი', 'ორსულობის დროს ესტროგენული/ჰორმონული პრეპარატები უკუნაჩვენებია — დაუყოვნებლივ შეწყვიტეთ და შეათანხმეთ მეან-გინეკოლოგთან!', 'red', _IMMED_STOP_PLAN, hold_or_skip=False, force_date_plan=True, contraindicated=True, acute_hold=True)
        elif c['active_gi_bleed']:
            hit_gi_h = ' (HIT: ჰეპარინი/LMWH აკრძალულია!)' if c['has_hit'] else ''
            add_row('ჰორმონული პრეპარატი / კონტრაცეპტივი', f'აქტიური კუჭ-ნაწლავის სისხლდენა / მწვავე წყლული — ფარმაკოლოგიური თრომბოპროფილაქტიკა დაბლოკილია ენდოსკოპიურ ჰემოსტაზამდე (მხოლოდ მექანიკური IPC; ჰორმონის შეწყვეტა შეათანხმეთ ექიმთან)!{hit_gi_h}', 'red', _GI_BLEED_PLAN, hold_or_skip=False, force_date_plan=True, contraindicated=True)
        elif c['liver_3x'] or ('vte_recent_3m' in c['ven_dvt_chips']) or c['ven_dup'] == 'acute_dvt':
            add_row('ჰორმონული პრეპარატი / კონტრაცეპტივი', 'უკუნაჩვენებია ღვიძლის მძიმე დისფუნქციის (≥3x ULN) ან ახალი/მწვავე VTE (<3 თვე / DVT) ფონზე — დაუყოვნებლივ შეათანხმეთ შეწყვეტა ექიმთან!', 'red', 'უკუნაჩვენებია — შეათანხმეთ მოხსნა!', hold_or_skip=False, force_date_plan=True, contraindicated=True)
        else:
            vte_opt = 'არაჰეპარინული პროფილაქტიკა (HIT: LMWH აკრძალულია!)' if c['has_hit'] else ('მექანიკური (IPC) ან კანქვეშა UFH პროფილაქტიკა (CrCl<30/დიალიზი: სტანდარტული LMWH აკრძალულია!)' if c['severe_renal'] else 'LMWH პროფილაქტიკა')
            if c['is_evla']:
                evla_vte_sfx = f' — {vte_opt}' if (c['has_hit'] or c['severe_renal']) else ''
                horm_rule = f'EVLA-ზე შესაძლებელია გაგრძელება VTE რისკის შეფასებით (Caprini); ფლებექტომიაზე/მაღალ რისკზე შეათანხმეთ თრომბოპროფილაქტიკა (ESVS 2022){evla_vte_sfx}'
            else:
                horm_rule = f'ესტროგენული/ჰორმონული პრეპარატი ზრდის VTE რისკს — შეათანხმეთ ქირურგთან (4 კვ-ით ადრე მოხსნა ან {vte_opt})'
            add_row('ჰორმონული პრეპარატი / კონტრაცეპტივი', horm_rule, 'amber', f'VTE შეფასება ({d_str(0)})', hold_or_skip=True, reconcile_only=True)
    ft_u_ppi = c['ft_meds'].get('unstructured_keys') or set()
    has_dapt_med = (bool(q.get('med_aspirin') or 'aspirin' in ft_u_ppi) and c['p2y12_cnt'] >= 1) or (c['p2y12_cnt'] >= 2)
    has_ap_any_med = bool(q.get('med_aspirin') or 'aspirin' in ft_u_ppi or c['p2y12_cnt'] >= 1 or 'cilostazol' in med_chips)
    has_any_oac_med = c['has_full_oac'] or c['has_ft_full_oac']
    has_high_gi_ppi_ind = (
        bool(q.get('ulcer')) or bool(q.get('ulcer_active_symp')) or (s.get('gastro_result') == 'acute_ulcer')
        or has_dapt_med or (has_any_oac_med and has_ap_any_med)
        or (((_int(patient.age, lo=12, hi=120) or 0) >= ppi_age) and 'steroid' in med_chips and (has_any_oac_med or has_ap_any_med or 'rivaroxaban_2_5' in med_chips))
    )
    if 'ppi' in med_chips or c['ft_meds']['has_omeprazole'] or has_high_gi_ppi_ind or s.get('ppi_started'):
        ft_keys = c['ft_meds'].get('matched_keys') or set()
        cyp_ppi_sensitive = bool(q.get('med_clopidogrel') or 'cilostazol' in med_chips or 'clopidogrel' in ft_keys or 'cilostazol' in ft_keys)
        ppi_drug_lbl = 'პანტოპრაზოლი 40 მგ (PPI)' if cyp_ppi_sensitive else 'ომეპრაზოლი / პანტოპრაზოლი (PPI)'
        ppi_cyp_note = ' — ომეპრაზოლი/ეზომეპრაზოლი შეცვალეთ პანტოპრაზოლით (CYP2C19)!' if cyp_ppi_sensitive else ''
        add_row(ppi_drug_lbl, f'გრძელდება / ინიშნება უწყვეტად პერიოპერაციულად კუჭის დაცვისთვის (ESC Class I){ppi_cyp_note}', 'amber' if (cyp_ppi_sensitive and ('ppi' in med_chips or c['ft_meds']['has_omeprazole'])) else 'ok', f'მიიღეთ {d_str(0)}-ის დილას', hold_or_skip=False)
    if 'steroid' in med_chips:
        add_row('სტეროიდი / იმუნოსუპრესანტი', 'არ მოხსნათ! სტრეს-დოზა (Assoc. of Anaesthetists 2020): არტერიულ/დიდ ოპერაციაზე — ჰიდროკორტიზონი 100 მგ IV ინდუქციაზე + 200 მგ/24 სთ ინფუზია (ან 50 მგ IV ყოველ 6 სთ-ში; მცირე ვენურზე — ჩვეული დოზა + 25–50 მგ IV); მოერიდეთ ეტომიდატს!', 'amber', f'არ შეწყვიტოთ ({d_str(0)} IV სტრეს-დოზა)', hold_or_skip=False)
    append_freetext_med_rows(c, _IMMED_STOP_PLAN, _GI_BLEED_PLAN)


def build_med_schedule(patient, q, s, egfr, crcl=None, stop_blocked=False, has_neuraxial_override=None, today=None):
    """Deterministic perioperative medication hold/continue table (ESVS 2023/2024 + ESC 2022 + ASRA 5th Ed)."""
    from .constants import INACTIVE_CASE_OUTCOMES
    from .meds_helpers import _apply_inactive_lock_to_med_schedule
    q_d = q if isinstance(q, dict) else {}
    s_d = s if isinstance(s, dict) else {}
    c = _med_schedule_ctx(patient, q_d, s_d, egfr, crcl, has_neuraxial_override, today=today)
    _append_antiplatelet_rows(c)
    _append_doac_dabigatran_rows(c)
    _append_rivaroxaban_row(c)
    _append_warfarin_row(c)
    _append_metabolic_rows(c)
    _append_cardio_other_rows(c)
    is_inactive = bool(getattr(patient, 'archived', False)) or (s_d.get('case_outcome_status') in INACTIVE_CASE_OUTCOMES)
    if is_inactive:
        _apply_inactive_lock_to_med_schedule(c['rows'])
    elif stop_blocked:
        _apply_stop_lock_to_med_schedule(c['rows'])
    return c['rows']
