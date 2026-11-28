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


def _apply_inactive_lock_to_med_schedule(rows):
    """Suppress active future discontinuation dates on archived, cancelled, deferred, or operated cards."""
    for r in rows:
        r['hold_or_skip'] = False
        if r.get('contraindicated') or r.get('acute_hold'):
            r['date_plan'] = 'არააქტიური ბარათი — უკუნაჩვენები/საშიში წამალი გადაამოწმეთ მკურნალ ექიმთან!'
            r['tone'] = 'red'
        else:
            r['date_plan'] = 'არააქტიური ბარათი — ძველი თარიღები გაუქმებულია, გადაამოწმეთ ექიმთან!'
            r['rule'] = 'ბარათი დაარქივებული ან არააქტიურია — დაგეგმილი შეჩერების/მიღების რეჟიმი აღარ მოქმედებს; არცერთი წამალი არ შეწყვიტოთ ექიმთან შეთანხმების გარეშე!'
            r['tone'] = 'amber'
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
    if not on_dialysis and (egfr is None or crcl is None):
        raw_cr, cr_u = _s_raw(s, 'creat'), s.get('creat_unit') or ''
        wt_raw = _s_raw(s, 'weight_kg')
        age_raw, cr_raw_n, wt_raw_n = _num(patient.age), _num(raw_cr), _num(wt_raw)
        bad_cr = is_creat_unit_mismatch(raw_cr, cr_u) or _parse_scr_mgdl(raw_cr, cr_u)[1]
        if not bad_cr:
            age_or_cr_oob = bool((age_raw is not None and age_raw > 120) or (cr_raw_n is not None and cr_raw_n > 3000))
            if egfr is None and age_or_cr_oob:
                eg_s = _sat_egfr(raw_cr, patient.age, patient.sex, creat_unit=cr_u)
                egfr = int(round(eg_s)) if eg_s is not None else None
            if crcl is None and (age_or_cr_oob or (wt_raw_n is not None and (wt_raw_n < 25 or wt_raw_n > 260))):
                cr_s = _sat_crcl(raw_cr, patient.age, patient.sex, weight_kg=wt_raw, creat_unit=cr_u, height_cm=_s_raw(s, 'height_cm'))
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
    voy_min = rule_value('MED_VOYAGER_RIVAROXABAN_2_5')['min_egfr']
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
    doac_nx_lims = [lim for cond, lim in ((q.get('med_doac'), doac_low), (q.get('med_dabigatran'), dabi_low), ('rivaroxaban_2_5' in med_chips, voy_min)) if cond]
    doac_renal_blk = bool(doac_nx_lims and (on_dialysis or eff_renal is None or eff_renal < max(doac_nx_lims)))
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


def _evla_ft_vka_contra(c):
    from .calculators import _sat_num
    from .registry import rule_value
    from .utils import _has_raw_scalar
    vka_max = rule_value('MED_WARFARIN_HOLD_DAYS')['inr_evla_max']
    mech_min = rule_value('MED_MECH_VALVE_SAFETY')
    s_ft = c['s']
    raw_s_ft = c['patient'].studies if isinstance(c['patient'].studies, dict) else {}
    inv_ft = s_ft.get('_invalid_inputs') if isinstance(s_ft.get('_invalid_inputs'), dict) else {}
    raw_dos_ft = _pick_study_raw(s_ft, raw_s_ft, inv_ft, 'dos_inr')
    dos_pres = _has_raw_scalar(raw_dos_ft) or ('dos_inr' in inv_ft)
    if dos_pres and (_num(raw_dos_ft, 0.5, 15.0) is None):
        return f'ოპერაციის დილის INR დაუშვებელია EVLA-ს წინ — გადაამოწმეთ საკონტროლო INR (≤ {vka_max}) ჩარევამდე!', 'INR დაუშვებელია — გადაამოწმეთ!'
    raw_base_ft = _pick_study_raw(s_ft, raw_s_ft, inv_ft, 'inr')
    base_pres = (not dos_pres) and (_has_raw_scalar(raw_base_ft) or ('inr' in inv_ft))
    if base_pres and (_num(raw_base_ft, 0.4, 20.0) is None):
        return f'საბაზისო INR დაუშვებელია EVLA-ს წინ — გადაამოწმეთ საკონტროლო INR (≤ {vka_max}) ჩარევამდე!', 'INR დაუშვებელია — გადაამოწმეთ!'
    eff_inr = _sat_num(raw_dos_ft, 0.5, 15.0) if dos_pres else _sat_num(raw_base_ft, 0.4, 20.0)
    if eff_inr is not None and eff_inr > vka_max:
        return f'სუპრათერაპიული INR ({eff_inr} > {vka_max}) EVLA-ს წინ — სისხლდენის რისკი, გადაამოწმეთ/შეაჩერეთ დოზა ქირურგთან!', f'INR {eff_inr} > {vka_max} — გადაამოწმეთ!'
    if c.get('has_mech_valve') and eff_inr is not None and eff_inr < mech_min:
        return f'მექანიკურ სარქველზე INR სუბთერაპიულია ({eff_inr} < {mech_min}) EVLA-ს წინ — სარქვლის თრომბოზის რისკი! არ შეწყვიტოთ ვარფარინი, სასწრაფოდ შეაფასეთ ხიდი კარდიოლოგთან!', f'INR {eff_inr} < {mech_min} — სასწრაფოდ კარდიოლოგთან!'
    return '', ''


def _collect_ft_contraindications(c, ft, u_keys, is_at, immed_stop_plan, gi_bleed_plan):
    from .calculators import _sat_age
    from .registry import rule_value
    ren_red = rule_value('REN_CKD_NEPHRO_HYDRATION')['red']
    glp_h = rule_value('MED_GLP1_FASTING_PROTOCOL')['liquid_hours']
    contra_msgs, extra_notes, plan_override = [], [], ''
    if c.get('is_rupture_suspect') and (ft.get('has_ft_full_oac') or ft.get('has_ft_unstruct_riva_low')):
        contra_msgs.append('რუპტურირებული AAA-ის ეჭვზე აუცილებელია ანტიკოაგულაციის გადაუდებელი რევერსია (4F-PCC / იდარუციზუმაბი)!')
        plan_override = 'გადაუდებელი რევერსია (4F-PCC / იდარუციზუმაბი) — გადავადების გარეშე!'
    if c.get('is_rupture_suspect') and (u_keys & {'clopidogrel', 'ticagrelor', 'prasugrel'}):
        contra_msgs.append('საეჭვო რუპტურა / გადაუდებელი AAA — ოპერაციას P2Y12-ის მოხსნის გამო არ გადავდებთ! გააუქმეთ შემდგომი დოზა და იყავით მზად ჰემოსტაზისა და თრომბოკონცენტრატის ტრანსფუზიისთვის (ESVS 2024)!')
        plan_override = plan_override or 'გადაუდებელი ოპერაცია — შეჩერებას არ ველოდებით!'
    if c.get('is_rupture_suspect') and (u_keys & {'sglt2', 'glp1', 'metformin', 'insulin', 'cilostazol'}):
        contra_msgs.append(f'საეჭვო რუპტურა / გადაუდებელი AAA — გეგმურ შეჩერებას ან {glp_h} სთ თხევად დიეტას არ ველოდებით! გააუქმეთ შემდგომი დოზა და უზრუნველყავით ინტრაოპერაციული RSI/კეტონების/ლაქტატის/გლიკემიის მონიტორინგი!')
        plan_override = plan_override or 'გადაუდებელი ოპერაცია — შეჩერებას არ ველოდებით!'
    if c.get('is_evla') and ft.get('has_ft_unstruct_warfarin'):
        ev_msg, ev_plan = _evla_ft_vka_contra(c)
        if ev_msg:
            contra_msgs.append(ev_msg)
            plan_override = plan_override or ev_plan
    if is_at and c['is_cea_oac_p2y_stop']:
        contra_msgs.append('სისხლჩაქცევის/თრომბოლიზისის ფონზე დაუყოვნებლივ შეათანხმეთ ნეიროქირურგთან / ნევროლოგთან!')
        plan_override = plan_override or 'დაუყოვნებლივ შეათანხმეთ ნეიროქირურგთან / ნევროლოგთან!'
    if is_at and c['active_gi_bleed']:
        contra_msgs.append('აქტიური კუჭ-ნაწლავის სისხლდენის / მწვავე წყლულის ფონზე სასწრაფოდ შეათანხმეთ გასტროენტეროლოგთან და ქირურგთან!')
        plan_override = plan_override or gi_bleed_plan
    if c['repro_pregnant'] and (u_keys & {'warfarin', 'doac', 'dabigatran', 'ticagrelor', 'prasugrel', 'sglt2', 'glp1'}):
        contra_msgs.append('ორსულობის დროს პერორალური OAC / ტიკაგრელორი / პრასუგრელი / SGLT2 / GLP-1 უკუნაჩვენებია — დაუყოვნებლივ შეათანხმეთ მეან-გინეკოლოგთან და ექიმთან!')
        plan_override = plan_override or immed_stop_plan
    if ('mech_valve' in c['cad_chips']) and (u_keys & {'doac', 'dabigatran'}):
        contra_msgs.append('მექანიკური სარქვლის ფონზე DOAC კატეგორიულად აკრძალულია (ESC Class III)!')
        plan_override = plan_override or immed_stop_plan
    if c['severe_renal'] and (u_keys & {'doac', 'dabigatran', 'metformin'}):
        contra_msgs.append(f'CrCl/eGFR <{ren_red} ან დიალიზზე DOAC/დაბიგატრანი/მეტფორმინი უკუნაჩვენებია — სასწრაფოდ შეათანხმეთ ექიმთან!')
        plan_override = plan_override or immed_stop_plan
    if c.get('on_dialysis') and ('sglt2' in u_keys):
        contra_msgs.append('პროგრამულ დიალიზზე SGLT2 ინჰიბიტორი უკუნაჩვენებია — სასწრაფოდ შეათანხმეთ ნეფროლოგთან/ენდოკრინოლოგთან!')
        plan_override = plan_override or immed_stop_plan
    if c['liver_3x'] and (u_keys & {'doac', 'dabigatran', 'metformin', 'ticagrelor', 'prasugrel'}):
        contra_msgs.append('ღვიძლის ≥3x დისფუნქციაზე DOAC/მეტფორმინი/ტიკაგრელორი/პრასუგრელი უკუნაჩვენებია — სასწრაფოდ შეათანხმეთ ექიმთან!')
        plan_override = plan_override or immed_stop_plan
    if c['has_stroke_hx'] and (('prasugrel' in u_keys) or (ft.get('has_ft_riva_low') and 'doac' in u_keys)):
        contra_msgs.append('ინსულტის/TIA-ს ანამნეზში პრასუგრელი / დაბალდოზიანი რივაროქსაბანი უკუნაჩვენებია!')
        plan_override = plan_override or immed_stop_plan
    if ('nsaid' in c['eff_allergy_chips']) and (('aspirin' in u_keys) or (ft.get('has_ft_riva_low') and 'doac' in u_keys)):
        contra_msgs.append('ასპირინზე/აასს-ზე ალერგიის ფონზე ასპირინი / VOYAGER უკუნაჩვენებია — დაუყოვნებლივ შეათანხმეთ ექიმთან!')
        plan_override = plan_override or immed_stop_plan
    if ('clopidogrel_intol' in c['eff_allergy_chips']) and ('clopidogrel' in u_keys):
        contra_msgs.append('კლოპიდოგრელზე აუტანლობის ფონზე პრეპარატი უკუნაჩვენებია — შეარჩიეთ უსაფრთხო ალტერნატივა!')
        plan_override = plan_override or immed_stop_plan
    cilo_cfg = rule_value('MED_CILOSTAZOL_BLACKBOX')
    if ('cilostazol' in u_keys) and (
        c.get('is_cea_oac_p2y_stop') or c.get('chf_any')
        or (c.get('lvef_cur') is not None and c['lvef_cur'] < cilo_cfg['lvef_min'])
        or c.get('on_dialysis') or c.get('creat_extreme_hi')
        or (c.get('eff_renal') is not None and c['eff_renal'] <= cilo_cfg['crcl_min'])
        or (c['s'].get('ecg') == 'ischemia_arrhythmia')
        or (c.get('hr_cur') is not None and c['hr_cur'] > cilo_cfg['hr_max'])
        or c.get('liver_3x') or c.get('active_gi_bleed')
        or bool(c['q'].get('recent_stent')) or c.get('has_acs_stent_12m') or c.get('repro_pregnant')
    ):
        contra_msgs.append(f'გულის უკმარისობის / LVEF <{cilo_cfg["lvef_min"]}% / CrCl ≤{cilo_cfg["crcl_min"]} / ტაქიარითმიის / სტენტის / ორსულობის / თირკმელ-ღვიძლის უკმარისობის ფონზე ცილოსტაზოლი აბსოლუტურად უკუნაჩვენებია (FDA Black Box)!')
        plan_override = plan_override or immed_stop_plan
    has_any_oac = bool(c.get('has_full_oac') or ('rivaroxaban_2_5' in c['med_chips']) or ft.get('has_ft_full_oac') or ft.get('has_ft_unstruct_riva_low') or c.get('has_ft_parenteral'))
    has_potent_p2y = bool(c['q'].get('med_ticagrelor') or c['q'].get('med_prasugrel') or (u_keys & {'ticagrelor', 'prasugrel'}))
    if has_any_oac and has_potent_p2y and (u_keys & {'warfarin', 'doac', 'dabigatran', 'ticagrelor', 'prasugrel'}):
        contra_msgs.append('OAC / პარენტერული ანტიკოაგულანტი + ტიკაგრელორი/პრასუგრელი ESC Class III (ჰემორაგიის რისკი) — დაუყოვნებლივ შეათანხმეთ კარდიოლოგთან!')
        plan_override = plan_override or immed_stop_plan
    if c.get('oac_count', 0) >= 2 and (u_keys & {'warfarin', 'doac', 'dabigatran'}):
        contra_msgs.append('ორმაგი პერორალური ანტიკოაგულაცია (≥2 OAC / რივაროქსაბანი 2.5) — დაუყოვნებლივ დააზუსტეთ ქირურგთან/კარდიოლოგთან!')
        plan_override = plan_override or immed_stop_plan
    if c.get('p2y12_cnt', 0) >= 2 and (u_keys & {'clopidogrel', 'ticagrelor', 'prasugrel'}):
        contra_msgs.append('ორმაგი P2Y12 ანტიაგრეგანტი (ESC Class III ჰემორაგიის რისკი) — დაუყოვნებლივ შეათანხმეთ დე-ესკალაცია კარდიოლოგთან!')
        plan_override = plan_override or immed_stop_plan
    if 'prasugrel' in u_keys:
        age_p = _sat_age(c['patient'].age) or 0
        wt_p = _num(c['s'].get('weight_kg'), lo=25, hi=260)
        if age_p >= 75:
            extra_notes.append('[≥75 წ. — FDA Boxed Warning: პრასუგრელი ზოგადად არ არის რეკომენდებული ფატალური/ქალასშიდა სისხლდენის რისკის გამო!]')
        if wt_p is not None and wt_p < 60:
            extra_notes.append('[<60 კგ — FDA Boxed Warning: სისხლდენის რისკი, განიხილეთ 5 მგ/დღ ან კლოპიდოგრელზე დეესკალაცია!]')
    if 'amiodarone' in u_keys:
        amio_ren = rule_value('MED_DOAC_HOLD_INTERVALS')['amio_renal']
        if c['q'].get('med_warfarin') or ft.get('has_ft_unstruct_warfarin'):
            extra_notes.append('[CYP2C9: ვარფარინთან აძლიერებს ანტიკოაგულაციას — გადაამოწმეთ INR!]')
        if (c['q'].get('med_doac') or c['q'].get('med_dabigatran') or 'rivaroxaban_2_5' in c['med_chips'] or ft.get('has_ft_unstruct_doac') or ft.get('has_ft_unstruct_riva_low')) and (c['on_dialysis'] or (c['eff_renal'] is not None and c['eff_renal'] < amio_ren)):
            extra_notes.append(f'[P-gp ურთიერთქმედება: DOAC/დაბიგატრანთან და CrCl <{amio_ren}/დიალიზზე ზრდის სისხლდენის რისკს!]')
    return contra_msgs, extra_notes, plan_override


def _append_ft_parenteral_row(c, ft, raw_txt, immed_stop_plan, gi_bleed_plan):
    from .registry import rule_value
    matched = ft.get('matched_keys') or set()
    if not (matched & {'parenteral_oac', 'parenteral_dti'}):
        return
    ren_red = rule_value('REN_CKD_NEPHRO_HYDRATION')['red']
    u_keys = ft.get('unstructured_keys') or set()
    q, med_chips, add_row = c['q'], c['med_chips'], c['add_row']
    msgs, plan_ov = [], ''
    if c.get('is_rupture_suspect'):
        if ft.get('has_ft_lmwh') or ft.get('has_ft_ufh'):
            msgs.append('რუპტურირებული AAA-ის ეჭვზე ოპერაცია არ გადაიდოს — დაუყოვნებლივ რევერსია (პროტამინის სულფატი) და სასწრაფო ოპერაციული ჰემოსტაზი!')
            plan_ov = 'გადაუდებელი რევერსია (პროტამინი) — გადავადების გარეშე!'
        else:
            msgs.append('რუპტურირებული AAA-ის ეჭვზე ოპერაცია არ გადაიდოს — შეაჩერეთ დოზა/ინფუზია, დაუყოვნებლივ ჩართეთ ჰემატოლოგი და სასწრაფო ოპერაციული ჰემოსტაზი!')
            plan_ov = 'გადაუდებელი ჰემოსტაზი / ჰემატოლოგი — გადავადების გარეშე!'
    if c['has_hit'] and (ft.get('has_ft_lmwh') or ft.get('has_ft_ufh')):
        msgs.append('HIT ანამნეზში: ჰეპარინი/LMWH აბსოლუტურად უკუნაჩვენებია — დაუყოვნებლივ შეწყვიტეთ და ჩართეთ ჰემატოლოგი!')
        plan_ov = plan_ov or 'STOP — HIT! ჰეპარინი/LMWH აბსოლუტურად აკრძალულია!'
    if c['severe_renal'] and (ft.get('has_ft_lmwh') or ft.get('has_ft_fondaparinux')):
        renal_alt = (
            'HIT-ის გამო ჰეპარინი/UFH აკრძალულია — უპირატესია არაჰეპარინული IV ბივალირუდინი / არგატრობანი ჰემატოლოგთან'
            if c['has_hit'] else 'უპირატესია არაფრაქციული ჰეპარინი — UFH'
        )
        msgs.append(f'CrCl/eGFR <{ren_red} ან დიალიზზე LMWH / ფონდაპარინუქსი აკუმულირდება და უკუნაჩვენებია ({renal_alt}) — სასწრაფოდ შეათანხმეთ ექიმთან!')
        plan_ov = plan_ov or immed_stop_plan
    if c['liver_3x'] and ft.get('has_ft_argatroban'):
        msgs.append('ღვიძლის ≥3x დისფუნქციაზე არგატრობანის დოზა მცირდება 75%-ით (უპირატესია ბივალირუდინი) — სასწრაფოდ შეათანხმეთ ჰემატოლოგთან!')
        plan_ov = plan_ov or immed_stop_plan
    if c['is_cea_oac_p2y_stop']:
        msgs.append('სისხლჩაქცევის/თრომბოლიზისის ფონზე დაუყოვნებლივ შეათანხმეთ ნეიროქირურგთან / ნევროლოგთან!')
        plan_ov = plan_ov or 'დაუყოვნებლივ შეათანხმეთ ნეიროქირურგთან / ნევროლოგთან!'
    if c['active_gi_bleed']:
        msgs.append('აქტიური კუჭ-ნაწლავის სისხლდენის / მწვავე წყლულის ფონზე სასწრაფოდ შეათანხმეთ გასტროენტეროლოგთან და ქირურგთან!')
        plan_ov = plan_ov or gi_bleed_plan
    if c['repro_pregnant'] and (ft.get('has_ft_fondaparinux') or ('parenteral_dti' in matched)):
        msgs.append('ორსულობის დროს ფონდაპარინუქსი / IV DTI შეათანხმეთ მეან-გინეკოლოგთან და ჰემატოლოგთან!')
        plan_ov = plan_ov or immed_stop_plan
    if c['has_mech_valve'] and ft.get('has_ft_fondaparinux'):
        msgs.append('მექანიკურ სარქველზე ფონდაპარინუქსი აკრძალულია (თრომბოზის მაღალი რისკი) — დაუყოვნებლივ შეათანხმეთ კარდიოლოგთან და ჰემატოლოგთან!')
        plan_ov = plan_ov or immed_stop_plan
    if c['is_evla'] and (q.get('med_warfarin') or ft.get('has_ft_unstruct_warfarin')):
        msgs.append('ორმაგი ანტიკოაგულაცია EVLA-ზე (უწყვეტი ვარფარინი + პარენტერული ანტიკოაგულანტი) — დაუყოვნებლივ შეათანხმეთ ქირურგთან/კარდიოლოგთან!')
        plan_ov = plan_ov or immed_stop_plan
    if q.get('med_ticagrelor') or q.get('med_prasugrel') or (u_keys & {'ticagrelor', 'prasugrel'}):
        msgs.append('პარენტერული ანტიკოაგულანტი + ტიკაგრელორი/პრასუგრელი (ESC Class III სისხლდენის მაღალი რისკი) — დაუყოვნებლივ შეათანხმეთ დე-ესკალაცია კარდიოლოგთან!')
        plan_ov = plan_ov or immed_stop_plan
    has_doac_conc = bool(q.get('med_doac') or q.get('med_dabigatran') or 'rivaroxaban_2_5' in med_chips or (u_keys & {'doac', 'dabigatran'}))
    if has_doac_conc:
        msgs.append('ორმაგი ანტიკოაგულაცია (DOAC + პარენტერული ანტიკოაგულანტი) — დაუყოვნებლივ შეათანხმეთ ქირურგთან/კარდიოლოგთან!')
        plan_ov = plan_ov or immed_stop_plan
    is_pure_dti = bool(('parenteral_dti' in matched) and not ft.get('has_ft_lmwh') and not ft.get('has_ft_ufh') and not ft.get('has_ft_fondaparinux'))
    is_pure_ufh = bool(ft.get('has_ft_ufh') and not ft.get('has_ft_lmwh') and not ft.get('has_ft_fondaparinux') and ('parenteral_dti' not in matched))
    if is_pure_dti:
        drug_lbl = 'არაჰეპარინული პარენტერული ანტიკოაგულანტი (არგატრობანი / ბივალირუდინი)'
    elif is_pure_ufh:
        drug_lbl = 'პარენტერული ანტიკოაგულანტი — არაფრაქციული ჰეპარინი (UFH)'
    else:
        drug_lbl = 'პარენტერული ანტიკოაგულანტი (LMWH / ფონდაპარინუქსი / ჰეპარინი)'
    if msgs:
        add_row(
            drug_lbl,
            f'თავისუფალ ტექსტში („{raw_txt}“) მითითებულია პარენტერული ანტიკოაგულანტი — {" · ".join(msgs)}',
            'red', plan_ov, hold_or_skip=False, force_date_plan=True, contraindicated=True, acute_hold=True,
        )
    elif is_pure_dti:
        add_row(
            drug_lbl,
            f'თავისუფალ ტექსტში („{raw_txt}“) მითითებულია არაჰეპარინული პარენტერული ანტიკოაგულანტი (IV DTI — არგატრობანი / ბივალირუდინი) — შეათანხმეთ შეჩერების დრო ჰემატოლოგთან/ქირურგთან (IV ინფუზიის შეჩერება −2–4 სთ aPTT კონტროლით; ASRA ნეირაქსიული ბლოკადა დაბლოკილია!).',
            'amber', 'შეათანხმეთ ქირურგთან (IV −2–4 სთ, aPTT)',
            hold_or_skip=True, force_date_plan=True, contraindicated=False, acute_hold=False, reconcile_only=True,
        )
    elif is_pure_ufh:
        add_row(
            drug_lbl,
            f'თავისუფალ ტექსტში („{raw_txt}“) მითითებულია არაფრაქციული ჰეპარინი (UFH) — შეათანხმეთ შეჩერების დრო ქირურგთან/ანესთეზიოლოგთან (IV UFH −4–6 სთ, SC UFH −12 სთ, ASRA ნეირაქსიული aPTT კონტროლით; CrCl <{ren_red}/დიალიზზე დასაშვებია).',
            'amber', 'შეათანხმეთ ქირურგთან (IV −4–6 სთ / SC −12 სთ)',
            hold_or_skip=True, force_date_plan=True, contraindicated=False, acute_hold=False, reconcile_only=True,
        )
    else:
        add_row(
            drug_lbl,
            f'თავისუფალ ტექსტში („{raw_txt}“) მითითებულია პარენტერული ანტიკოაგულანტი — შეათანხმეთ შეჩერების დრო ქირურგთან/ანესთეზიოლოგთან (LMWH პროფილაქტ. −12 სთ / თერაპ. −24 სთ; ფონდაპარინუქსი −36–48 სთ; ASRA ნეირაქსიული კონტროლი!).',
            'amber', 'შეათანხმეთ ქირურგთან (−12–24 სთ)',
            hold_or_skip=True, force_date_plan=True, contraindicated=False, acute_hold=False, reconcile_only=True,
        )


def append_freetext_med_rows(c, immed_stop_plan, gi_bleed_plan):
    """Append contraindicated/unstructured rows derived from free-text `q.other_meds`."""
    from .registry import rule_value
    from .utils import _safe_display_str
    q, med_chips, add_row = c['q'], c['med_chips'], c['add_row']
    ft, ren_red = c['ft_meds'], rule_value('REN_CKD_NEPHRO_HYDRATION')['red']
    raw_txt = _safe_display_str(q.get('other_meds'), max_len=160)
    u_keys = ft.get('unstructured_keys') or set()
    if ft.get('has_ft_statin') and 'statin' not in med_chips:
        if c['repro_pregnant']:
            add_row('სტატინი (ტექსტიდან)', 'ორსულობის დროს სტატინი უკუნაჩვენებია (ტერატოგენული რისკი) — დაუყოვნებლივ შეწყვიტეთ და შეათანხმეთ ექიმთან!', 'red', immed_stop_plan, hold_or_skip=False, force_date_plan=True, contraindicated=True, acute_hold=True)
        elif c['liver_3x']:
            add_row('სტატინი (ტექსტიდან)', f'თავისუფალ ტექსტში („{raw_txt}“) მითითებულია სტატინი — ღვიძლის ფერმენტების ≥3x მატების ფონზე ჰეპატოტოქსიკურობის გამო სტატინი უკუნაჩვენებია, შეწყვიტეთ და შეათანხმეთ ექიმთან!', 'red', immed_stop_plan, hold_or_skip=False, force_date_plan=True, contraindicated=True, acute_hold=True)
    if ft.get('has_ft_acei_arb') and 'acei_arb' not in med_chips:
        if c['repro_pregnant']:
            add_row('ACEi / ARB (ტექსტიდან)', 'ორსულობის დროს ACEi / ARB აბსოლუტურად უკუნაჩვენებია (ნაყოფის ნეფროტოქსიკურობა — FDA Boxed Warning) — დაუყოვნებლივ შეცვალეთ ექიმთან!', 'red', immed_stop_plan, hold_or_skip=False, force_date_plan=True, contraindicated=True, acute_hold=True)
        elif c.get('acei_acute_hold'):
            add_row('ACEi / ARB (ტექსტიდან)', f'თავისუფალ ტექსტში („{raw_txt}“) მითითებულია ACEi/ARB — დაბალი წნევის ან ჰიპერკალიემიის ფონზე ოპერაციის დილის დოზა გამოტოვეთ და შეათანხმეთ ექიმთან!', 'amber', 'გამოტოვეთ დილას! (დაბალი წნევა / მაღალი K+)', hold_or_skip=True, force_date_plan=True, contraindicated=False, acute_hold=True)
    has_any_oac_ns = bool(c['has_full_oac'] or ('rivaroxaban_2_5' in med_chips) or c['has_ft_full_oac'] or ft.get('has_ft_unstruct_riva_low') or c.get('has_ft_parenteral'))
    has_dapt_ns = bool((q.get('med_aspirin') or 'aspirin' in u_keys) and c['p2y12_cnt'] >= 1) or (c['p2y12_cnt'] >= 2)
    if ft.get('has_ft_nsaid') and (
        ('nsaid' in c['eff_allergy_chips']) or c['active_gi_bleed'] or c['severe_renal']
        or c['repro_pregnant'] or c['liver_3x'] or has_any_oac_ns or has_dapt_ns
    ):
        ns_reasons = [lbl for cond, lbl in (
            ('nsaid' in c['eff_allergy_chips'], 'აასს-ზე ალერგია'),
            (c['active_gi_bleed'], 'აქტიური GI სისხლდენა/წყლული'),
            (c['severe_renal'], f'თირკმლის მძიმე უკმარისობა (<{ren_red})'),
            (c['repro_pregnant'], 'ორსულობა'),
            (c['liver_3x'], 'ღვიძლის მძიმე დისფუნქცია (≥3x)'),
            (has_any_oac_ns, 'ანტიკოაგულანტთან ერთად სისხლდენის მაღალი რისკი'),
            (has_dapt_ns, 'ორმაგ ანტიაგრეგანტთან (DAPT) სისხლდენის მაღალი რისკი'),
        ) if cond]
        add_row(
            'ტკივილგამაყუჩებელი აასს (ტექსტიდან)',
            f'თავისუფალ ტექსტში („{raw_txt}“) მითითებულია არასტეროიდული აასს — უკუნაჩვენებია ({", ".join(ns_reasons)})! შეწყვიტეთ დაუყოვნებლივ და შეათანხმეთ ექიმთან!',
            'red', gi_bleed_plan if c['active_gi_bleed'] else immed_stop_plan,
            hold_or_skip=False, force_date_plan=True, contraindicated=True, acute_hold=True,
        )
    _append_ft_parenteral_row(c, ft, raw_txt, immed_stop_plan, gi_bleed_plan)
    if not ft['unstructured_labels']:
        return
    labels, is_at = ', '.join(ft['unstructured_labels']), bool(ft['unstructured_antithrombotic'])
    contra_msgs, extra_notes, plan_override = _collect_ft_contraindications(c, ft, u_keys, is_at, immed_stop_plan, gi_bleed_plan)
    is_contra = bool(contra_msgs)
    note_sfx = (' ' + ' '.join(extra_notes)) if extra_notes else ''
    ft_rule = (
        f'თავისუფალ ტექსტში („{raw_txt}“) მითითებულია მაღალი რისკის უკუნაჩვენები პრეპარატი — {" · ".join(contra_msgs)}{note_sfx}'
        if is_contra else
        f'თავისუფალ ტექსტში („{raw_txt}“) მითითებულია მაღალი რისკის პრეპარატი — მონიშნეთ ჩამრთველი შეჩერების ზუსტი თარიღისთვის!{note_sfx}'
    )
    add_row(
        f'დასაზუსტებელი წამალი ({labels})', ft_rule, 'red',
        plan_override if is_contra else 'დააზუსტეთ ექიმთან — არასტრუქტურირებულია!',
        hold_or_skip=(not is_contra), force_date_plan=True,
        contraindicated=is_contra, acute_hold=is_contra,
        unstructured=True, reconcile_only=True,
    )


def _parse_med_confirm_date(patient, s, med_schedule):
    """Return earliest verified pre-window hold confirmation date (nurse call, Stage 2, or approval), else None."""
    import datetime as _dt

    from django.utils import timezone

    from .nurse_ui import med_schedule_signature
    if not s.get('med_hold_agreed'):
        return None
    dates = []
    cur_sig = med_schedule_signature({'s': s, 'med_schedule': med_schedule})
    called_at = str(s.get('nurse_med_called_at') or '').strip()
    if called_at and str(s.get('nurse_med_called_sig') or '') == cur_sig:
        for fmt in ('%d.%m.%Y %H:%M', '%d.%m.%Y', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
            try:
                dates.append(_dt.datetime.strptime(called_at[:16], fmt).date())
                break
            except ValueError:
                continue
    agreed_dt = _date(s.get('med_hold_agreed_date'))
    if agreed_dt:
        dates.append(agreed_dt)
    if patient.approved_at:
        ap = patient.approved_at
        if isinstance(ap, _dt.datetime):
            ap_d = timezone.localtime(ap).date() if timezone.is_aware(ap) else ap.date()
        else:
            ap_d = _date(ap)
        if ap_d:
            dates.append(ap_d)
    return min(dates) if dates else None


def _append_raaa_metabolic_rows(c):
    """Emit emergency non-delay / acute-monitoring directives on suspected ruptured AAA instead of elective future hold dates."""
    from .registry import rule_value
    q, add_row = c['q'], c['add_row']
    sglt2_h = rule_value('MED_SGLT2_HOLD_DAYS')['std_days'] * 24
    glp_h = rule_value('MED_GLP1_FASTING_PROTOCOL')['liquid_hours']
    metf_con = rule_value('MED_METFORMIN_RENAL_CONTRAST')['contra_renal']
    emerg_dp = 'გადაუდებელი ოპერაცია — შეჩერებას არ ველოდებით!'
    if q.get('dm_insulin'):
        add_row('ინსულინი', 'საეჭვო რუპტურა / გადაუდებელი AAA — ოპერაციას არ გადავდებთ; შეაჩერეთ კანქვეშა პრანდიალური დოზები და მართეთ გლიკემია ინტრაოპერაციული IV ინსულინითა და ხშირი გლუკოზის კონტროლით!', 'red', emerg_dp, hold_or_skip=False, force_date_plan=True, acute_hold=True)
    if q.get('sglt2'):
        sglt2_c = bool(c['repro_pregnant'] or c['on_dialysis'])
        add_row('SGLT2 (ჯარდიანსი / ფორსიგა)', f'საეჭვო რუპტურა / გადაუდებელი AAA — {sglt2_h} სთ გეგმურ შეჩერებას არ ველოდებით! გააუქმეთ შემდგომი დოზა და აკონტროლეთ სისხლის კეტონები, pH/ლაქტატი და გლიკემია (ეუგლიკემიური DKA-ს პრევენცია — FDA / ADA 2024)!', 'red', emerg_dp, hold_or_skip=False, force_date_plan=True, contraindicated=sglt2_c, acute_hold=True)
    if q.get('glp1'):
        add_row('GLP-1 (ოზემპიკი / მუნჯარო / საქსენდა)', f'საეჭვო რუპტურა / გადაუდებელი AAA — {glp_h} სთ თხევად დიეტას ან გეგმურ შეჩერებას არ ველოდებით! მართეთ „სავსე კუჭის“ პროტოკოლით (RSI / ასპირაციის პროფილაქტიკა — ASA 2024)!', 'red', emerg_dp, hold_or_skip=False, force_date_plan=True, contraindicated=bool(c['repro_pregnant']), acute_hold=True)
    if q.get('metformin'):
        metf_c = bool(c['creat_mismatch'] or c['on_dialysis'] or c['creat_extreme_hi'] or c['liver_3x'] or (c['eff_renal'] is not None and c['eff_renal'] < metf_con))
        add_row('მეტფორმინი', 'საეჭვო რუპტურა / გადაუდებელი AAA — ოპერაციას არ გადავდებთ! გააუქმეთ შემდგომი დოზა და აკონტროლეთ ლაქტატი და თირკმლის ფუნქცია (ლაქტატ-აციდოზის პრევენცია)!', 'red', emerg_dp, hold_or_skip=False, force_date_plan=True, contraindicated=metf_c, acute_hold=True)

