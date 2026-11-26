"""Vitals, hematology/ASRA, renal/contrast, glycemia/thyroid, infection, pulmonary & GI/carotid rules."""
from ...models import PreopPatient
from ..calculators import (
    _ihd_and_chf_flags,
    _s_raw,
    _sat_gl_mmol,
    _sat_num,
    calc_max_contrast_ml,
    normalize_fibrinogen_gl,
)
from ..constants import PATHWAY_SHORT
from ..registry import rule_value
from ..utils import _date, _fmt_ka_date, _int, _num, chip_summary


def _parse_wbc(raw):
    val = _num(raw, lo=0.1, hi=150000)
    if val is not None and val >= 200:
        val = round(val / 1000.0, 2)
    return _num(val, lo=0.1, hi=200.0) if val is not None else None


def _parse_plt(raw):
    val = _num(raw, lo=1, hi=2000000)
    if val is not None and val >= 3000:
        val = int(round(val / 1000.0))
    return _int(val, lo=1, hi=2500) if val is not None else None


def _extract_bp_and_sat(s):
    bp_sys, bp_dia = _int(s.get('bp_sys'), lo=30, hi=320), _int(s.get('bp_dia'), lo=20, hi=220)
    bp_sys_l, bp_dia_l = _int(s.get('bp_sys_l'), lo=30, hi=320), _int(s.get('bp_dia_l'), lo=20, hi=220)
    bp_r_inv = (bp_sys is not None and bp_dia is not None and bp_sys <= bp_dia)
    bp_l_inv = (bp_sys_l is not None and bp_dia_l is not None and bp_sys_l <= bp_dia_l)
    s_r_sat, d_r_sat = _sat_num(_s_raw(s, 'bp_sys'), 30, 320), _sat_num(_s_raw(s, 'bp_dia'), 20, 220)
    s_l_sat, d_l_sat = _sat_num(_s_raw(s, 'bp_sys_l'), 30, 320), _sat_num(_s_raw(s, 'bp_dia_l'), 20, 220)
    r_sat_inv = bp_r_inv or (s_r_sat is not None and d_r_sat is not None and s_r_sat <= d_r_sat)
    l_sat_inv = bp_l_inv or (s_l_sat is not None and d_l_sat is not None and s_l_sat <= d_l_sat)
    eff_sys_sat = max((v for v, inv in ((s_r_sat, r_sat_inv), (s_l_sat, l_sat_inv)) if v is not None and not inv), default=None)
    eff_dia_sat = max((v for v, inv in ((d_r_sat, r_sat_inv), (d_l_sat, l_sat_inv)) if v is not None and not inv), default=None)
    return bp_sys, bp_dia, bp_sys_l, bp_dia_l, bp_r_inv, bp_l_inv, eff_sys_sat, eff_dia_sat


def eval_vitals_bp(ctx):
    patient, pathway, q, s, q_filled = ctx.patient, ctx.pathway, ctx.q, ctx.s, ctx.q_filled
    htn_cfg, hypo_cfg, hr_cfg = rule_value('VIT_BP_SEVERE_HTN'), rule_value('VIT_BP_HYPOTENSION_SHOCK'), rule_value('VIT_HR_SPO2_METS')
    cardio_decision = patient.cardio_decision or ''
    ctx.cardio_decision, ctx.cardio_cleared = cardio_decision, (cardio_decision == 'cleared')
    ctx.anesth_cleared = bool(s.get('anesth_done'))
    ctx.anesth_plan_raw = s.get('anesthesia_plan') or 'auto'
    ctx.explicit_neuraxial = (ctx.anesth_plan_raw == 'neuraxial') or ('epidural' in ctx.anesth_lines_set)

    bp_sys, bp_dia, bp_sys_l, bp_dia_l, bp_r_inverted, bp_l_inverted, eff_sys_sat, eff_dia_sat = _extract_bp_and_sat(s)
    ctx.bp_inverted = bool(bp_r_inverted or bp_l_inverted)
    if ctx.bp_inverted:
        inv_s, inv_d = (bp_sys, bp_dia) if bp_r_inverted else (bp_sys_l, bp_dia_l)
        ctx.red_flags.append(
            f'არტერიული წნევის არასწორი ჩანაწერი (SBP ≤ DBP: {inv_s}/{inv_d} mmHg) — გადაამოწმეთ სისტოლური და დიასტოლური მაჩვენებლები!'
        )
    valid_sys_vals = [v for v, inv in ((bp_sys, bp_r_inverted), (bp_sys_l, bp_l_inverted)) if v is not None and not inv]
    valid_dia_vals = [v for v, inv in ((bp_dia, bp_r_inverted), (bp_dia_l, bp_l_inverted)) if v is not None and not inv]
    eff_sys, eff_dia = max(valid_sys_vals, default=None), max(valid_dia_vals, default=None)
    bp_arm_diff = abs(bp_sys - bp_sys_l) if (bp_sys is not None and bp_sys_l is not None and not bp_r_inverted and not bp_l_inverted) else None
    ctx.bp_sys, ctx.bp_sys_l, ctx.eff_sys, ctx.eff_dia, ctx.bp_arm_diff = bp_sys, bp_sys_l, eff_sys, eff_dia, bp_arm_diff

    if bp_arm_diff is not None and bp_arm_diff >= hypo_cfg['arm_diff']:
        higher_arm = 'მარჯვენა' if bp_sys >= bp_sys_l else 'მარცხენა'
        ctx.amber_flags.append(
            f'მკლავებს შორის წნევის ასიმეტრია ({bp_arm_diff} mmHg >= 20 mmHg) — საეჭვოა ლავიწქვეშა არტერიის სტენოზი! '
            f'არტერიული წნევის მონიტორინგი უნდა წარმოებდეს მაღალი წნევის ({higher_arm}) მკლავზე.'
        )

    hr, spo2 = _int(s.get('hr'), lo=15, hi=260), _int(s.get('spo2'), lo=30, hi=100)
    ctx.hr_sat = hr if hr is not None else _sat_num(_s_raw(s, 'hr'), 15, 260)
    shock_index = round(hr / eff_sys, 2) if (hr is not None and eff_sys is not None and eff_sys > 0) else None
    severe_htn = bool((eff_sys_sat is not None and eff_sys_sat >= htn_cfg['red_sbp']) or (eff_dia_sat is not None and eff_dia_sat >= htn_cfg['red_dbp']))
    ctx.hr, ctx.spo2, ctx.shock_index, ctx.severe_htn = hr, spo2, shock_index, severe_htn

    if severe_htn:
        disp_s = eff_sys if eff_sys is not None else (int(eff_sys_sat) if eff_sys_sat is not None else '?')
        disp_d = eff_dia if eff_dia is not None else (int(eff_dia_sat) if eff_dia_sat is not None else '?')
        if pathway == PreopPatient.Pathway.CEA:
            ctx.red_flags.append(
                f'მაღალი არტერიული წნევა ({disp_s}/{disp_d} mmHg >= 180/110) — CEA-ზე ცერებრული ჰიპერპერფუზიის, '
                'სისხლჩაქცევისა და კისრის ჰემატომის უმაღლესი რისკი! სავალდებულოა სტაბილიზაცია ოპერაციამდე.'
            )
        else:
            ctx.red_flags.append(f'მაღალი არტერიული წნევა ({disp_s}/{disp_d} mmHg >= 180/110) — საჭიროა კორექცია ოპერაციამდე.')
    elif pathway == PreopPatient.Pathway.CEA and eff_dia and eff_dia > htn_cfg['cea_dia_amber']:
        ctx.amber_flags.append(
            f'მომატებული არტერიული წნევა CEA-ზე ({eff_sys or "?"}/{eff_dia} mmHg; DBP > 90 mmHg — ESVS 2023 Class IIa/C) — '
            'ცერებრული ჰიპერპერფუზიისა და კისრის ჰემატომის პრევენციისთვის საჭიროა კორექცია ოპერაციამდე.'
        )
    elif eff_sys and eff_sys < hypo_cfg['red_sbp']:
        if pathway == PreopPatient.Pathway.AAA_INFRA:
            ctx.red_flags.append(f'ჰიპოტენზია ({eff_sys}/{eff_dia or "?"} mmHg) — სასწრაფოდ გამოირიცხოს რუპტურა / შოკი!')
        else:
            ctx.red_flags.append(f'ჰიპოტენზია ({eff_sys}/{eff_dia or "?"} mmHg) — სასწრაფოდ შეფასდეს ჰემოდინამიკა!')
    elif eff_sys and eff_sys < hypo_cfg['amb_sbp']:
        ctx.amber_flags.append(
            f'სასაზღვრო ჰიპოტენზია ({eff_sys}/{eff_dia or "?"} mmHg — SBP 90–99): შეაფასეთ ვოლემია და დილის ანტიჰიპერტენზიული წამლების გამოტოვება.'
        )
    elif (eff_sys and eff_sys >= htn_cfg['amb_sbp']) or (eff_dia and eff_dia >= htn_cfg['amb_dbp']):
        ctx.amber_flags.append(f'მომატებული არტერიული წნევა ({eff_sys or "?"}/{eff_dia or "?"} mmHg) — სასურველია ამბულატორიული კორექცია (ESC 2022: სტადია 2 ჰიპერტენზია <180/110 ოპერაციას არ აყოვნებს).')

    if shock_index is not None and shock_index >= hypo_cfg['shock_idx']:
        ctx.red_flags.append(f'შოკის ინდექსი მომატებულია (HR/SBP = {shock_index} >= 1.0) — ფარული ჰიპოვოლემიის / სისხლდენის / სეფსისის რისკი! სასწრაფოდ შეფასდეს ჰემოდინამიკა!')
    if eff_sys is not None and eff_dia is not None and 0 < (eff_sys - eff_dia) < hypo_cfg['narrow_pp']:
        ctx.amber_flags.append(f'ვიწრო პულსური წნევა ({eff_sys - eff_dia} mmHg < 20 mmHg) — გამოირიცხოს ჰიპოვოლემია, აორტის სტენოზი ან დაბალი განდევნა.')

    is_afib_tachy = ((s.get('ecg') or 'pending') == 'afib_controlled') and bool(ctx.hr_sat and ctx.hr_sat > hr_cfg['hr_hi'])
    if hr and (hr > hr_cfg['hr_hi'] or hr < hr_cfg['hr_lo']):
        if not is_afib_tachy:
            ctx.red_flags.append(f'პულსის კრიტიკული ცვლილება (HR {hr}/წთ) — საჭიროა ეკგ და კარდიოლოგის შეფასება.')
    elif s.get('irregular_hr'):
        if ctx.cardio_cleared:
            ctx.amber_flags.append(f'არარეგულარული რიტმი (HR {hr or "?"}/წთ) — შეფასებულია და დაშვებულია კარდიოლოგის მიერ ✓')
        else:
            ctx.red_flags.append(f'პულსის/რიტმის საყურადღებო ცვლილება (HR {hr or "?"}/წთ) — საჭიროა ეკგ და კარდიოლოგის შეფასება.')
    elif hr and hr_cfg['hr_lo'] <= hr < hr_cfg['hr_brady_amb']:
        if q.get('pacemaker') or ((s.get('ecg') or 'pending') == 'ischemia_arrhythmia'):
            ctx.red_flags.append(f'ბრადიკარდია (HR {hr}/წთ < 55) კარდიოსტიმულატორის / ეკგ ცვლილების ფონზე — გადაამოწმეთ სტიმულატორი და AV გამტარებლობა!')
        else:
            ctx.amber_flags.append(f'ზომიერი ბრადიკარდია (HR {hr}/წთ < 55) — დილის ბეტა-ბლოკატორის დოზა შეაფასეთ პულსის მიხედვით.')

    if spo2 is not None:
        if spo2 < hr_cfg['spo2_red']:
            ctx.red_flags.append(f'მძიმე ჰიპოქსემია (SpO2 {spo2}% < 90%) — აცნობეთ ქირურგსა და ანესთეზიოლოგს!')
        elif spo2 < hr_cfg['spo2_amb']:
            ctx.amber_flags.append(f'დაქვეითებული სატურაცია (SpO2 {spo2}%).')

    ctx.mets_limb_only = q_filled and (not q.get('mets_ok', True)) and (q.get('mets_limitation') == 'limb')
    ctx.true_low_mets = q_filled and (not q.get('mets_ok', True)) and (q.get('mets_limitation') != 'limb')
    if q_filled and not q.get('mets_ok', True):
        if ctx.mets_limb_only:
            ctx.amber_flags.append('2 სართულზე ასვლა შეზღუდულია მხოლოდ ფეხის იშემიური ტკივილით (კოჭლობით) — კარდიო-რესპირატორული ქოშინის გარეშე.')
        else:
            ctx.amber_flags.append('დაქვეითებული ფუნქციური ამტანობა (< 4 METs — ქოშინი/დაღლა 2 სართულზე ასვლისას).')


def _eval_early_neuraxial_gate(ctx):
    patient, pathway, q, s = ctx.patient, ctx.pathway, ctx.q, ctx.s
    raw_s = getattr(ctx, 'raw_s', None) or {}
    asra_cfg = rule_value('ASRA_NEURAXIAL_COAG_GATE')
    open_aortic = (
        pathway == PreopPatient.Pathway.ABF
        or (pathway == PreopPatient.Pathway.AAA_INFRA and patient.op_subtype != 'evar')
    )
    ctx.open_aortic = open_aortic
    ctx.open_major = open_aortic or ctx.is_pad_bypass
    plt = _parse_plt(s.get('plt'))
    inr = _num(s.get('inr'), lo=0.4, hi=20.0)
    dos_inr_early = _num(s.get('dos_inr'), lo=0.5, hi=15)
    raw_dos_inr = raw_s['dos_inr'] if 'dos_inr' in raw_s else s.get('dos_inr')
    dos_inr_present = raw_dos_inr is not None and str(raw_dos_inr).strip() != ''
    eff_inr_nx = dos_inr_early if dos_inr_present else inr
    fib = normalize_fibrinogen_gl(s.get('fibrinogen'))
    on_oral_anticoag = bool(q.get('med_warfarin') or q.get('med_doac') or q.get('med_dabigatran'))
    ctx.plt, ctx.inr, ctx.eff_inr_nx, ctx.fib, ctx.on_oral_anticoag = plt, inr, eff_inr_nx, fib, on_oral_anticoag

    has_acs_stent_early = ('stent_6_12m' in ctx.cad_chips) and ('mi' in ctx.cad_chips)
    p2y12_cnt_early = sum([bool(q.get('med_clopidogrel')), bool(q.get('med_ticagrelor')), bool(q.get('med_prasugrel'))])
    asa_allergy_solo_p2y12 = (
        'nsaid' in ctx.allergy_chips and p2y12_cnt_early >= 1
        and (not ctx.is_venous or bool(q.get('cad')) or bool(q.get('neuro')))
    )
    uninterrupted_p2y12_early = (p2y12_cnt_early >= 1) and (
        bool(q.get('recent_stent')) or has_acs_stent_early or asa_allergy_solo_p2y12
        or (ctx.is_pta and not q.get('med_prasugrel')) or (ctx.is_symp_cea and not q.get('med_prasugrel'))
    )
    prewashout_vka = bool(q.get('med_warfarin') and not dos_inr_present)
    hard_nx_reasons = []
    inv_in = s.get('_invalid_inputs') if isinstance(s.get('_invalid_inputs'), dict) else {}
    def _has_bad(k, parsed):
        rv = raw_s[k] if k in raw_s else s.get(k)
        return parsed is None and ((rv is not None and str(rv).strip() != '') or k in inv_in)
    if _has_bad('dos_inr', dos_inr_early):
        hard_nx_reasons.append('ოპერაციის დილის INR დაუშვებელია')
    elif not dos_inr_present and _has_bad('inr', inr):
        hard_nx_reasons.append('INR დაუშვებელია')
    if _has_bad('plt', plt):
        hard_nx_reasons.append('PLT დაუშვებელია')
    if _has_bad('fibrinogen', fib):
        hard_nx_reasons.append('ფიბრინოგენი დაუშვებელია')
    if plt is not None and plt < asra_cfg['plt_min']:
        hard_nx_reasons.append(f'PLT {plt} < 100')
    if eff_inr_nx is not None and eff_inr_nx > asra_cfg['inr_max']:
        hard_nx_reasons.append(f'INR {eff_inr_nx} > 1.4')
    if fib is not None and fib < asra_cfg['fib_min']:
        hard_nx_reasons.append(f'ფიბრინოგენი {fib} < 1.5 გ/ლ')
    if s.get('aptt_prolonged'):
        hard_nx_reasons.append('APTT გახანგრძლივებულია')
    if uninterrupted_p2y12_early:
        hard_nx_reasons.append('უწყვეტი P2Y12/DAPT — ნეირაქსიული ბლოკადა უკუნაჩვენებია!')
    if ctx.ft_meds.get('unstructured_antithrombotic'):
        hard_nx_reasons.append('დაუზუსტებელი ანტიკოაგულანტი/ანტიაგრეგანტი ტექსტში (other_meds)')
    if 'hit_heparin' in ctx.allergy_chips and (not ctx.is_venous or 'mech_valve' in ctx.cad_chips):
        hard_nx_reasons.append('HIT არტერიულ/მექ. სარქვლის ოპერაციაზე (IV DTI) — ნეირაქსიული ბლოკადა უკუნაჩვენებია!')
    if (q.get('med_doac') or q.get('med_dabigatran') or 'rivaroxaban_2_5' in ctx.med_chips) and (
        ctx.on_dialysis or (ctx.eff_renal is not None and ctx.eff_renal < 30)
    ):
        hard_nx_reasons.append('CrCl/eGFR < 30 ანტიკოაგულანტზე — ნეირაქსიული ბლოკადა მოერიდეთ!')
    if bool(s.get('echo_valve_issue')):
        hard_nx_reasons.append('მძიმე სარქვლოვანი მანკი / AS — სპინალური ბლოკადა საშიშია!')
    if getattr(ctx, 'bp_inverted', False):
        hard_nx_reasons.append('არტერიული წნევის ინვერტირებული ჩანაწერი (SBP ≤ DBP) — გადამოწმებამდე დაბლოკილია!')
    raw_sbp_low = any(
        (v := _num(_s_raw(raw_s, k) if _s_raw(raw_s, k) is not None else _s_raw(s, k))) is not None
        and (v < 30 or (ctx.eff_sys is None and v < asra_cfg['sbp_min']))
        for k in ('bp_sys', 'bp_sys_l')
    )
    raw_hr_v = _num(_s_raw(raw_s, 'hr') if _s_raw(raw_s, 'hr') is not None else _s_raw(s, 'hr'))
    if (ctx.eff_sys is not None and ctx.eff_sys < asra_cfg['sbp_min']) or raw_sbp_low or (ctx.shock_index is not None and ctx.shock_index >= 1.0) or (raw_hr_v is not None and (raw_hr_v > 260 or raw_hr_v < 15)):
        hard_nx_reasons.append('ჰიპოტენზია / შოკი (SBP <95 ან SI ≥1.0) — სიმპათექტომია საშიშია!')

    solo_neuraxial_anatomic_mismatch = (ctx.anesth_plan_raw == 'neuraxial') and (
        pathway == PreopPatient.Pathway.CEA or (open_aortic and 'epidural' not in ctx.anesth_lines_set)
    )
    if solo_neuraxial_anatomic_mismatch:
        ctx.red_flags.append(
            'არჩეულია მხოლოდ სპინალური/ეპიდურული გაუტკივარება კისრის (CEA) ან ღია აორტის ოპერაციაზე — ანატომიურად შეუსაბამოა! '
            'შეარჩიეთ ზოგადი ენდოტრაქეული ანესთეზია (± ეპიდურული ანალგეზია ღია აორტაზე) ან კისრის წნულის ბლოკადა.'
        )

    # Do not let pre-washout baseline warfarin INR suppress auto-spinal hold intervals on phlebectomy
    auto_nx_blockers = [r for r in hard_nx_reasons if not (prewashout_vka and r.startswith('INR '))]
    ctx.hard_nx_reasons = hard_nx_reasons
    ctx.solo_neuraxial_anatomic_mismatch = solo_neuraxial_anatomic_mismatch
    ctx.auto_spinal_candidate = (ctx.anesth_plan_raw == 'auto') and (ctx.is_phleb or patient.op_subtype == 'evar' or ctx.is_pad_bypass)
    ctx.has_neuraxial_plan = ctx.explicit_neuraxial or ((ctx.anesth_plan_raw == 'auto') and ctx.is_phleb and not auto_nx_blockers)
    ctx.neuraxial_hard_blocked = bool(ctx.explicit_neuraxial and (hard_nx_reasons or solo_neuraxial_anatomic_mismatch))
    if ctx.neuraxial_hard_blocked and not solo_neuraxial_anatomic_mismatch:
        ctx.red_flags.append(
            f"არჩეული ნეირაქსიული / ეპიდურული გაუტკივარება უკუნაჩვენებია ({', '.join(hard_nx_reasons)}) — სპინალური ჰემატომის უმაღლესი რისკი! შეცვალეთ ანესთეზიის გეგმა (ASRA 5th Ed)!"
        )


def eval_hematology_coag(ctx):
    patient, q, s, hb_gl = ctx.patient, ctx.q, ctx.s, ctx.hb_gl
    _eval_early_neuraxial_gate(ctx)
    plt, eff_inr, fib = ctx.plt, ctx.eff_inr_nx, ctx.fib
    hb_cfg = rule_value('LAB_HB_PBM_THRESHOLDS')
    coag_cfg = rule_value('LAB_WBC_PLT_COAG')

    ferritin_val = _num(s.get('ferritin'), lo=1, hi=5000)
    tsat_val = _num(s.get('tsat'), lo=1, hi=100)
    hb_norm = hb_cfg['hb_norm_f'] if patient.sex == 'F' else hb_cfg['hb_norm_m']
    ctx.tsat_val = tsat_val
    ctx.needs_anemia_panel = (hb_gl is not None and hb_gl < hb_cfg['hb_reflex'])
    ctx.anemia_panel_satisfied = bool(s.get('anemia_panel_done')) or ferritin_val is not None or tsat_val is not None
    ctx.pbm_iv_iron_indicated = bool(
        hb_gl is not None and hb_gl < hb_norm and (
            (ferritin_val is not None and ferritin_val < hb_cfg['ferritin_abs'])
            or (ferritin_val is not None and ferritin_val < hb_cfg['ferritin_func'] and tsat_val is not None and tsat_val < hb_cfg['tsat_low'])
        )
    )
    ctx.pbm_iron_required = bool(ctx.pbm_iv_iron_indicated and not (ctx.is_acute_emergency or ctx.is_septic_or_mycotic_urgent))
    if hb_gl is not None:
        hb_poly_hi = hb_cfg['hb_poly_f'] if patient.sex == 'F' else hb_cfg['hb_poly_m']
        if ctx.needs_anemia_panel:
            ctx.reflex_orders.append({
                'key': 'anemia',
                'trigger': f'ანემია (Hb {hb_gl} გ/ლ < 115)',
                'action': 'ფერიტინი + შრატის რკინა/TSAT (%) + რეტიკულოციტები (ESAIC PBM)',
                'done': ctx.anemia_panel_satisfied,
            })
        if ctx.pbm_iron_required:
            ctx.reflex_orders.append({
                'key': 'pbm_iron',
                'trigger': f'რკინადეფიციტური ანემია (Hb {hb_gl} გ/ლ, ფერიტინი {ferritin_val} ნგ/მლ{f", TSAT {tsat_val}%" if tsat_val is not None else ""})',
                'action': 'PBM პროტოკოლი (ESAIC / ESVS): ინტრავენური რკინის ინფუზია (ფერიკარბოქსიმალტოზა) ალოგენური ტრანსფუზიის შესამცირებლად',
                'done': bool(s.get('pbm_iv_iron_done')),
            })
        if hb_gl < hb_cfg['hb_red']:
            ctx.red_flags.append(f'მნიშვნელოვანი ანემია (Hb {hb_gl} გ/ლ < 100) — აცნობეთ ქირურგს (მიზეზის ძიება / PBM კორექცია).')
        elif hb_gl > hb_poly_hi:
            ctx.amber_flags.append(f'ერითროციტოზი / ჰემოკონცენტრაცია (Hb {hb_gl} გ/ლ > {hb_poly_hi}) — მომატებული თრომბოზული რისკი, საჭიროა ჰიდრატაცია და შეფასება.')
        elif hb_gl < hb_norm and not ctx.is_venous:
            ctx.amber_flags.append(f'მსუბუქი ანემია (Hb {hb_gl} გ/ლ) — ღია ოპერაციაზე სავალდებულოა ერითრომასის რეზერვი.')

    wbc = _parse_wbc(s.get('wbc'))
    ctx.wbc = wbc
    if wbc is not None and (wbc > coag_cfg['wbc_hi'] or wbc < coag_cfg['wbc_lo']):
        ctx.red_flags.append(f'ლეიკოციტების ცვლილება (WBC {wbc} x10^9/L) — გამოირიცხოს მწვავე ინფექცია ჩარევამდე!')

    if plt is not None:
        if plt < coag_cfg['plt_red']:
            spinal_note = ' (სპინალური/ეპიდურული ჰემატომისა და სისხლდენის რისკი!)' if ctx.has_neuraxial_plan else ''
            ctx.red_flags.append(f'თრომბოციტოპენია (PLT {plt} x10^9/L < 100){spinal_note} — აცნობეთ ქირურგს.')
        elif plt < coag_cfg['plt_amb']:
            ctx.amber_flags.append(f'ზომიერი თრომბოციტოპენია (PLT {plt} x10^9/L) — საყურადღებოა ანესთეზიოლოგისთვის.')
    elif hb_gl is not None and not ctx.is_venous:
        ctx.amber_flags.append('სისხლის საერთო ანალიზში შეიტანეთ თრომბოციტების (PLT) რაოდენობა (ჰეპარინიზაციისა და ნეირაქსიული უსაფრთხოებისთვის).')

    if ctx.open_major and hb_gl is not None and not s.get('blood_group'):
        ctx.amber_flags.append('ღია სისხლძარღვოვანი ოპერაციის წინ მიუთითეთ სისხლის ჯგუფი და რეზუსი (Type & Screen).')

    if fib is not None and fib < coag_cfg['fib_sev']:
        ctx.red_flags.append(f'მძიმე ჰიპოფიბრინოგენემია (ფიბრინოგენი {fib} გ/ლ < 1.5) — კოაგულოპათიური სისხლდენის მაღალი რისკი (ESAIC 2023)!')
    is_evla_vka_ctx = (
        ctx.is_venous and patient.op_subtype == PreopPatient.OpSubtype.EVLA
        and not ctx.has_neuraxial_plan and bool(q.get('med_warfarin'))
    )
    evla_vka_ok = (
        is_evla_vka_ctx and eff_inr and eff_inr <= coag_cfg['inr_evla']
        and not s.get('aptt_prolonged') and (fib is None or fib >= coag_cfg['fib_low'])
    )
    fib_mild_low = (fib is not None and coag_cfg['fib_sev'] <= fib < coag_cfg['fib_low'])
    if evla_vka_ok:
        if eff_inr > coag_cfg['inr_red']:
            ctx.amber_flags.append(f'INR {eff_inr} ვარფარინის ფონზე — EVLA-ს წინ დააზუსტეთ ქირურგთან (ESVS 2022-ით მოხსნა აუცილებელი არ არის, თუ INR <= 3.0).')
    elif is_evla_vka_ctx and eff_inr and eff_inr > coag_cfg['inr_evla'] and not s.get('aptt_prolonged') and not fib_mild_low:
        ctx.red_flags.append(f'სუპრათერაპიული INR ვარფარინის ფონზე EVLA-ს წინ (INR {eff_inr} > {coag_cfg["inr_evla"]}) — სისხლდენის რისკი, გადაამოწმეთ ქირურგთან!')
    elif (eff_inr and eff_inr > coag_cfg['inr_red']) or s.get('aptt_prolonged') or fib_mild_low:
        ctx.red_flags.append('კოაგულოგრამის დარღვევა (INR > 1.35 / გახანგრძლივებული APTT / დაბალი ფიბრინოგენი) — აცნობეთ ქირურგს!')


def eval_renal_electrolytes(ctx):
    patient, q, s = ctx.patient, ctx.q, ctx.s
    egfr, crcl, eff_renal, on_dialysis = ctx.egfr, ctx.crcl, ctx.eff_renal, ctx.on_dialysis
    ren_cfg = rule_value('REN_CKD_NEPHRO_HYDRATION')
    el_cfg = rule_value('REN_ELECTROLYTES_K_NA')
    na_val = _num(s.get('na'), lo=95, hi=190)
    na_eff = na_val if na_val is not None else _sat_num(_s_raw(s, 'na'), 95, 190)
    _, chf_hyd_flag = _ihd_and_chf_flags(q, s)
    uses_contrast = (patient.op_subtype in ('evar', 'tbd')) or ctx.is_pta
    max_contrast_ml = calc_max_contrast_ml(eff_renal, on_dialysis=on_dialysis) if uses_contrast else None
    co2_angio_advised = bool(uses_contrast and not on_dialysis and eff_renal is not None and eff_renal < ren_cfg['red'])
    needs_nephro_panel = on_dialysis or (eff_renal is not None and eff_renal < ren_cfg['nephro'])
    needs_hydration = (not on_dialysis) and (eff_renal is not None and eff_renal < ren_cfg['hydration']) and uses_contrast
    ctx.na_val, ctx.na_val_sat, ctx.chf_hyd_flag, ctx.uses_contrast = na_val, na_eff, chf_hyd_flag, uses_contrast
    ctx.max_contrast_ml, ctx.co2_angio_advised = max_contrast_ml, co2_angio_advised
    ctx.needs_nephro_panel, ctx.needs_hydration = needs_nephro_panel, needs_hydration

    renal_disp = f'CrCl {crcl} / eGFR {egfr}' if (crcl is not None and egfr is not None and crcl < egfr) else f'eGFR {egfr}'
    if on_dialysis:
        ctx.reflex_orders.append({
            'key': 'nephro',
            'trigger': 'პროგრამული დიალიზი / თირკმლის ტერმინალური უკმარისობა',
            'action': 'დიალიზის გრაფიკის (ოპერაციის წინა დღეს უჰეპარინოდ), K+ კონტროლისა და მოცულობის შეთანხმება ნეფროლოგთან',
            'done': bool(s.get('nephro_panel_done')),
        })
        if not s.get('nephro_panel_done'):
            ctx.red_flags.append('პაციენტი იმყოფება დიალიზზე — სავალდებულოა დიალიზის სესიისა და კალიუმის შეთანხმება ნეფროლოგთან და ქირურგთან!')
    elif ctx.creat_unit_ambiguous:
        if ctx.creat_unit in ('umol', 'mgdl'):
            ctx.red_flags.append(
                f'კრეატინინის მნიშვნელობა და არჩეული ერთეული არ ემთხვევა ({s.get("creat")} {ctx.creat_unit})! '
                'გადაამოწმეთ ზუსტი ერთეული (µmol/L თუ mg/dL) eGFR / CrCl უსაფრთხო გამოთვლისთვის.'
            )
        else:
            ctx.red_flags.append(
                f'კრეატინინის ერთეული საეჭვოა ({s.get("creat")} — 15–35 შუალედი)! '
                'მიუთითეთ ზუსტი ერთეული (µmol/L თუ mg/dL) eGFR / CrCl უსაფრთხო გამოთვლისთვის.'
            )
    elif s.get('creat') is not None and egfr is None:
        ctx.amber_flags.append('კრეატინინის კლირენსის / eGFR გამოსათვლელად მიუთითეთ პაციენტის ასაკი (12–120 წ.) და სქესი.')
    elif eff_renal is not None:
        if needs_nephro_panel:
            nephro_lean = ctx.is_venous or ctx.is_pta or bool(s.get('cta_disc')) or (eff_renal >= ren_cfg['red'])
            nephro_act = (
                'შარდოვანა, ელექტროლიტები (K+/HCO3-) და ნეფროლოგის კონსულტაცია (CTA-ით / ზომიერ CKD 3b-ზე რუტინული თირკმლის ექო და Ca/P სავალდებულო არ არის)'
                if nephro_lean
                else 'შარდოვანა, Ca/P, შარდის ცილა + თირკმელების ექოსკოპია + ნეფროლოგის კონსულტაცია'
            )
            ctx.reflex_orders.append({
                'key': 'nephro',
                'trigger': f'დაქვეითებული თირკმლის ფუნქცია ({renal_disp} მლ/წთ < 45)',
                'action': nephro_act,
                'done': bool(s.get('nephro_panel_done')),
            })
        if needs_hydration:
            c_lim = f', მაქს. კონტრასტი ≤ {max_contrast_ml} მლ' if max_contrast_ml else ''
            metf_sfx = ' + მეტფორმინის შეჩერება' if q.get('metformin') else ''
            if na_eff is not None and na_eff >= el_cfg['na_hi']:
                hyd_fluid = f'ჰიპერნატრიემია (Na+ {na_val if na_val is not None else na_eff} ≥150) — მოერიდეთ 0.9% NaCl-ით დატვირთვას! ნელი ჰიპოტონური კორექცია (ΔNa+ ≤ 8 მმოლ/ლ/24 სთ){c_lim}{metf_sfx}'
            elif chf_hyd_flag:
                hyd_fluid = f'ფრთხილი მოცულობა-კონტროლირებადი ჰიდრატაცია (0.5 მლ/კგ/სთ LVEDP/შეშუპების კონტროლით — HFrEF სითხით გადატვირთვის რისკი!){c_lim}{metf_sfx}'
            else:
                hyd_fluid = f'იზოტონური ჰიდრატაციის პროტოკოლი (0.9% NaCl){c_lim}{metf_sfx}'
            ctx.reflex_orders.append({
                'key': 'hydration',
                'trigger': f'{renal_disp} მლ/წთ (< 60) კონტრასტული ჩარევის წინ',
                'action': hyd_fluid,
                'done': bool(s.get('hydration_planned')),
            })
        if eff_renal < ren_cfg['red']:
            co2_note = f' (მაქს. იოდის კონტრასტი ≤ {max_contrast_ml} მლ — განიხილეთ CO2 ანგიოგრაფია / IVUS!)' if co2_angio_advised else ''
            ctx.red_flags.append(f'თირკმლის მძიმე უკმარისობა ({renal_disp} მლ/წთ < 30){co2_note} — სავალდებულოა ნეფროლოგი და ტაქტიკის შეთანხმება ქირურგთან!')
        elif eff_renal < ren_cfg['hydration'] and not ctx.is_venous and not needs_hydration:
            ctx.amber_flags.append(f'{renal_disp} მლ/წთ (CKD 3) — კონტრასტული კვლევისას საჭიროა იზოტონური ჰიდრატაცია.')

    k_val = _num(s.get('k'), lo=1.0, hi=12.0)
    ctx.k_val = k_val
    ctx.k_val_sat = _sat_num(_s_raw(s, 'k'), 1.0, 12.0)
    if k_val is not None:
        if k_val < el_cfg['k_red_lo'] or k_val >= el_cfg['k_red_hi']:
            ctx.red_flags.append(f'კალიუმის კრიტიკული დონე (K+ {k_val} მმოლ/ლ) — არითმიის რისკი! აცნობეთ ექიმს.')
        elif k_val < el_cfg['k_amb_lo'] or k_val > el_cfg['k_amb_hi']:
            ctx.amber_flags.append(f'კალიუმის სასაზღვრო დონე (K+ {k_val} მმოლ/ლ) — სასურველია კორექცია ოპერაციამდე.')
    elif ctx.hb_gl is not None and not ctx.is_venous and (
        on_dialysis or (eff_renal is not None and eff_renal < ren_cfg['hydration']) or ('acei_arb' in ctx.med_chips) or ('diuretic' in ctx.med_chips)
    ):
        ctx.amber_flags.append('CKD / დიალიზი / ACEi-დიურეტიკის ფონზე შეიტანეთ შრატის კალიუმი (K+) ოპერაციამდე.')

    if na_eff is not None and na_eff >= el_cfg['na_hi']:
        ctx.red_flags.append(f'კრიტიკული ჰიპერნატრიემია (Na+ {na_val if na_val is not None else na_eff} მმოლ/ლ >= 150) — მოერიდეთ 0.9% NaCl-ით დატვირთვას, ნელი კორექცია ΔNa+ ≤ 8 მმოლ/ლ/24 სთ! აცნობეთ ანესთეზიოლოგს.')
    elif na_eff is not None and na_eff < el_cfg['na_lo']:
        ctx.red_flags.append(f'კრიტიკული ჰიპონატრიემია (Na+ {na_val if na_val is not None else na_eff} მმოლ/ლ < 130) — ნელი კორექცია ΔNa+ ≤ 8 მმოლ/ლ/24 სთ (ოსმოსური დემიელინიზაციის პრევენცია)! აცნობეთ ანესთეზიოლოგს.')


def eval_glycemia_endocrine(ctx):
    q, s, q_filled, gl_mmol = ctx.q, ctx.s, ctx.q_filled, ctx.gl_mmol
    gly_cfg = rule_value('GLY_GLUCOSE_HBA1C')
    nut_cfg = rule_value('LAB_NUTRITION_ALBUMIN_LIVER')
    hba1c = _num(s.get('hba1c'), lo=3.0, hi=25.0)
    gl_eff = gl_mmol if gl_mmol is not None else _sat_gl_mmol(_s_raw(s, 'glucose'))
    ctx.gl_mmol_sat = gl_eff
    needs_hba1c_reflex = (gl_eff is not None and gl_eff >= gly_cfg['gl_hba1c'] and hba1c is None)
    needs_endo_reflex = (hba1c is not None and hba1c >= gly_cfg['hba1c_endo']) or (gl_eff is not None and gl_eff >= gly_cfg['gl_endo'])
    ctx.hba1c, ctx.needs_hba1c_reflex, ctx.needs_endo_reflex = hba1c, needs_hba1c_reflex, needs_endo_reflex
    gl_d = gl_mmol if gl_mmol is not None else gl_eff

    if needs_hba1c_reflex:
        ctx.reflex_orders.append({
            'key': 'hba1c',
            'trigger': f'უზმოდ გლუკოზა მომატებულია ({gl_d} მმოლ/ლ >= 7.0)',
            'action': 'გლიკირებული ჰემოგლობინის (HbA1c %) განსაზღვრა ექიმის ჩართვამდე',
            'done': hba1c is not None,
        })
    if needs_endo_reflex:
        ctx.reflex_orders.append({
            'key': 'endo',
            'trigger': f'მაღალი გლიკემია / HbA1c ({gl_d or "—"} მმოლ/ლ, HbA1c {hba1c or "—"}%)',
            'action': 'ენდოკრინოლოგის კონსულტაცია და გლიკემიის კორექცია ოპერაციამდე',
            'done': bool(s.get('endo_consult_done')),
        })
    if gl_eff is not None and (gl_eff >= gly_cfg['gl_red_hi'] or gl_eff < gly_cfg['gl_red_lo']):
        ctx.red_flags.append(f'კრიტიკული გლიკემია (გლუკოზა {gl_d} მმოლ/ლ) — სასწრაფო კორექცია და ენდოკრინოლოგის ჩართვა!')
    elif (q_filled and q.get('dm') and hba1c is None and not ctx.is_venous and not needs_hba1c_reflex):
        ctx.amber_flags.append('შაქრიანი დიაბეტი — გადაამოწმეთ HbA1c.')
    if hba1c is not None:
        if hba1c >= gly_cfg['hba1c_dm'] and q_filled and not q.get('dm'):
            ctx.red_flags.append(
                f'HbA1c {hba1c}% (≥ 6.5%) დიაბეტის ანამნეზის გარეშე — გამოვლენილია არადიაგნოსტირებული შაქრიანი დიაბეტი! '
                'ჩართეთ პერიოპერაციული გლიკემიური მონიტორინგი და ენდოკრინოლოგი.'
            )
        if hba1c >= gly_cfg['hba1c_endo']:
            ctx.amber_flags.append(
                f'ცუდად კონტროლირებადი გლიკემია (HbA1c {hba1c}% ≥ 8.5% — CPOC / ESC) — '
                'მომატებულია ჭრილობის/პროთეზის ინფექციის რისკი; საჭიროა პერიოპერაციული ინსულინოთერაპიის ოპტიმიზაცია.'
            )

    if q_filled and (q.get('med_doac') or q.get('med_dabigatran') or 'rivaroxaban_2_5' in ctx.med_chips):
        if (q.get('med_doac') or q.get('med_dabigatran')) and ctx.bmi is not None and ctx.bmi > 50:
            ctx.amber_flags.append(
                f'ექსტრემალური სიმსუქნე (BMI {ctx.bmi} > 50 კგ/მ²) DOAC/დაბიგატრანის ფონზე (ISTH 2021) — '
                'პლაზმური კონცენტრაცია შესაძლოა არაპროგნოზირებადი იყოს; განიხილეთ ანტი-Xa/დონის კონტროლი ან ვარფარინი.'
            )
        if 'amiodarone' in ctx.med_chips and (ctx.on_dialysis or (ctx.eff_renal is not None and ctx.eff_renal < 60)):
            ctx.amber_flags.append(
                'ამიოდარონი + DOAC/დაბიგატრანი დაქვეითებული თირკმლის ფუნქციის (CrCl/eGFR < 60) ფონზე — '
                'P-gp ინჰიბირება ზრდის პრეპარატის აკუმულაციას (EHRA 2021: განიხილეთ +24 სთ-ით ადრე შეჩერება)!'
            )

    _eval_tsh_and_nutrition(ctx, nut_cfg)


def _eval_tsh_and_nutrition(ctx, nut_cfg):
    q, s, q_filled = ctx.q, ctx.s, ctx.q_filled
    tsh_res = s.get('tsh_status') or 'pending'
    needs_tsh = q_filled and (not ctx.is_venous) and (
        ((bool(q.get('thyroid_disease')) or 'amiodarone' in ctx.med_chips) and (ctx.uses_contrast or ctx.open_aortic))
        or (tsh_res != 'pending')
    )
    ctx.tsh_res, ctx.needs_tsh = tsh_res, needs_tsh
    if needs_tsh:
        if ctx.uses_contrast:
            tsh_trig = 'ფარისებრი ჯირკვლის პათოლოგია / ამიოდარონი + იოდის კონტრასტი'
        elif ctx.open_aortic:
            tsh_trig = 'ფარისებრი ჯირკვლის პათოლოგია / ამიოდარონი — ღია აორტული ოპერაციის წინ'
        else:
            tsh_trig = 'ფარისებრი ჯირკვლის პათოლოგია / ამიოდარონი (TSH/FT4 კონტროლი)'
        ctx.reflex_orders.append({
            'key': 'tsh',
            'trigger': tsh_trig,
            'action': 'TSH და FT4 კონტროლი თირეოტოქსიკოზის / ჰიპოთირეოზის გამოსარიცხად',
            'done': tsh_res != 'pending',
        })
    if tsh_res == 'hyperthyroid':
        if ctx.uses_contrast:
            hyp_msg = (
                'მანიფესტური ჰიპერთირეოზი (დაბალი TSH / მაღალი FT4) — იოდის კონტრასტი კატეგორიულად უკუნაჩვენებია '
                '(ESUR — თირეოტოქსიკური კრიზის საფრთხე)! გეგმური კონტრასტული ჩარევა დაბლოკილია სტაბილიზაციამდე!'
            )
            ctx.red_flags.append(hyp_msg)
            ctx.hard_contraindications.append(hyp_msg)
        else:
            ctx.red_flags.append('ჰიპერთირეოზი (დაბალი TSH / მაღალი FT4) — პერიოპერაციული თირეოტოქსიკური კრიზისა და არითმიის რისკი! აცნობეთ ენდოკრინოლოგსა და ანესთეზიოლოგს.')
    elif tsh_res == 'hypothyroid_severe':
        ctx.red_flags.append(
            'მძიმე დეკომპენსირებული ჰიპოთირეოზი (მაღალი TSH / დაბალი FT4) — ანესთეზიისას მიქსედემური კომის, '
            'ჰიპოთერმიისა და რეფრაქტერული ჰიპოტენზიის უმაღლესი რისკი! გეგმურ ჩარევამდე აცნობეთ ენდოკრინოლოგსა და ანესთეზიოლოგს!'
        )

    if ctx.bmi is not None and ctx.bmi < nut_cfg['bmi_cachexia']:
        ctx.red_flags.append(
            f'მძიმე კახექსია / კვებითი გამოფიტვა (BMI {ctx.bmi} < 16.0 კგ/მ²) — ჭრილობის შეუხორცებლობისა და პოსტოპერაციული გართულებების '
            'უმაღლესი რისკი! განიხილეთ წინასაოპერაციო ნუტრიციული ოპტიმიზაცია ქირურგთან.'
        )
    if q_filled and q.get('cfs_level') == '5_plus' and ctx.pathway in (PreopPatient.Pathway.ABF, PreopPatient.Pathway.PAD_BYPASS):
        ctx.amber_flags.append(
            'გამოხატული კლინიკური დაუძლურება (CFS ≥ 5 — CPOC / ESVS) ღია არტერიული რეკონსტრუქციის წინ — '
            'განიხილეთ ენდოვასკულური ალტერნატივა (PTA/სტენტირება) და გერიატრიული დელირიუმის პრევენცია.'
        )

    if ctx.open_major and ctx.alb_gl is not None:
        if ctx.alb_gl < nut_cfg['alb_red']:
            ctx.red_flags.append(f'მძიმე ჰიპოალბუმინემია ({ctx.alb_gl} გ/ლ < 30) — ღია აორტული ოპერაციის მაღალი რისკი, აცნობეთ ქირურგს!')
        elif ctx.alb_gl < nut_cfg['alb_amb']:
            ctx.amber_flags.append(f'დაბალი ალბუმინი ({ctx.alb_gl} გ/ლ < 35) — მომატებული ქირურგიული/ჭრილობის გართულების რისკი.')

    if s.get('liver_status') == '3x':
        ctx.red_flags.append('ღვიძლის ფერმენტების/ბილირუბინის მნიშვნელოვანი მატება (>=3x ULN) — აცნობეთ ექიმს.')
    if s.get('viral_positive'):
        ctx.amber_flags.append('დადებითი ვირუსული მარკერი (B/C/HIV/RW) — საოპერაციო ეპიდ-უსაფრთხოება და ღვიძლის სტატუსის გათვალისწინება.')


def eval_infection_stale_labs(ctx):
    pathway, q, s = ctx.pathway, ctx.q, ctx.s
    uti_cfg = rule_value('INF_UTI_VS_ASB')
    stale_days = rule_value('INF_STALE_LABS_DAYS')
    has_synthetic_implant = (
        pathway in (PreopPatient.Pathway.AAA_INFRA, PreopPatient.Pathway.ABF, PreopPatient.Pathway.CEA)
        or (ctx.is_pad_bypass and s.get('alt_vein_mapped') == 'no_autovein_prosthetic')
    )
    inf_chip_set = set(q.get('inf_chips') or [])
    has_uti_symptoms = ('dysuria' in inf_chip_set) or (bool(q.get('active_infection')) and not inf_chip_set)
    needs_dental = has_synthetic_implant and (bool(q.get('dental_infection')) or 'dental_acute' in inf_chip_set)
    non_uti_chips = inf_chip_set - {'dysuria'}
    if needs_dental:
        non_uti_chips = non_uti_chips - {'dental_acute'}
    has_non_uti_active_inf = bool(non_uti_chips)
    if has_non_uti_active_inf:
        inf_desc = chip_summary(list(non_uti_chips), 'inf_chips', q.get('infection_text'))
        ctx.red_flags.append(f'აქტიური არასაშარდე ინფექციური კერა ({inf_desc}) — გეგმურ ჩარევამდე აცნობეთ ქირურგს სანაციისთვის!')

    urine_wbc = _int(s.get('urine_wbc'), lo=0, hi=2000)
    uti_suspected = (
        has_uti_symptoms
        or bool(s.get('urine_nitrite'))
        or (has_synthetic_implant and urine_wbc is not None and urine_wbc >= uti_cfg['wbc_implant_red'])
    )
    ctx.has_synthetic_implant, ctx.inf_chip_set = has_synthetic_implant, inf_chip_set
    ctx.has_uti_symptoms, ctx.urine_wbc, ctx.uti_suspected = has_uti_symptoms, urine_wbc, uti_suspected

    if uti_suspected:
        ctx.reflex_orders.append({
            'key': 'uti',
            'trigger': 'სიმპტომური საშარდე/აქტიური ინფექცია, ნიტრიტები+ ან გამოხატული პიურია (≥25)',
            'action': 'შარდის ბაქტერიოლოგია + ანტიბიოტიკოგრამა და ინფექციური კერის სანაცია',
            'done': bool(s.get('urine_culture_done')),
        })
        if not s.get('urine_culture_done'):
            if ctx.is_venous:
                ctx.red_flags.append('აქტიური ინფექციური კერა / საშარდე ინფექცია — გეგმური ვენური ჩარევის წინ აცნობეთ ქირურგს!')
            else:
                ctx.red_flags.append(
                    'საეჭვოა სიმპტომური საშარდე ან სხვა აქტიური ინფექცია (ნიტრიტები+ / დიზურია / ლეიკოციტურია ≥25) — '
                    'სინთეზური პროთეზის/პატჩის ინფიცირების მაღალი რისკი! აცნობეთ ქირურგს.'
                )
    elif urine_wbc is not None and urine_wbc > uti_cfg['wbc_asb'] and not ctx.is_venous:
        ctx.amber_flags.append(
            f'უსიმპტომო ლეიკოციტურია (შარდში ლეიკოციტები {urine_wbc} მხ/არ, ნიტრიტებისა და დიზურიის გარეშე) — '
            'IDSA 2019 გაიდლაინით უსიმპტომო ბაქტერიურიის რუტინული მკურნალობა ოპერაციას არ აყოვნებს.'
        )

    dual_chx_iod = ('latex_chlorhex' in ctx.allergy_chips) and ctx.eff_allergy_iodine
    ctx.needs_dental = needs_dental
    if needs_dental:
        antiseptic_lbl = (
            'ალტერნატიული უიოდო სავლები (ქლორჰექსიდინი + იოდზე ალერგია!)' if dual_chx_iod
            else ('პოვიდონ-იოდის სავლები (ქლორჰექსიდინზე ალერგია!)' if 'latex_chlorhex' in ctx.allergy_chips else 'ქლორჰექსიდინის დეკოლონიზაცია')
        )
        ctx.reflex_orders.append({
            'key': 'dental',
            'trigger': 'კარიესული/ჩირქოვანი კბილი სინთეზური პროთეზის/პატჩის ჩადგმამდე',
            'action': f'სტომატოლოგის სანაცია (წერილობითი ცნობა) + {antiseptic_lbl}',
            'done': bool(s.get('dental_sanitation_done')),
        })
        if not s.get('dental_sanitation_done'):
            ctx.red_flags.append(
                'აქტიური სტომატოლოგიური ინფექციური კერა სინთეზური პროთეზის/პატჩის ჩადგმამდე — '
                'პროთეზის ინფიცირების მაღალი რისკი, სავალდებულოა სტომატოლოგის სანაცია!'
            )
    if has_synthetic_implant and not ctx.is_rupture_suspect:
        shower_antiseptic = (
            'ალტერნატიული უიოდო ანტისეპტიკური შხაპი (ქლორჰექსიდინი + იოდზე ალერგია!)' if dual_chx_iod
            else ('პოვიდონ-იოდის შხაპი (ქლორჰექსიდინზე ალერგია!)' if 'latex_chlorhex' in ctx.allergy_chips else 'ქლორჰექსიდინის ანტისეპტიკური შხაპი')
        )
        ctx.reflex_orders.append({
            'key': 'decol',
            'trigger': 'სინთეზური სისხლძარღვოვანი პროთეზის / პატჩის / სტენტ-გრაფტის იმპლანტაცია',
            'action': f'პროთეზის ინფექციის პრევენცია: წინასაოპერაციო {shower_antiseptic}ს ინსტრუქტაჟი',
            'done': bool(s.get('decolonization_ready')),
        })

    labs_dt = _date(s.get('labs_date'))
    ctx.labs_date_ka = _fmt_ka_date(labs_dt)
    labs_age_days = (ctx.today - labs_dt).days if (labs_dt and labs_dt <= ctx.today) else None
    needs_stale_labs = bool(
        labs_age_days is not None and labs_age_days > stale_days
        and ctx.is_active_case and not ctx.is_rupture_suspect
    )
    ctx.labs_age_days, ctx.needs_stale_labs = labs_age_days, needs_stale_labs
    if needs_stale_labs:
        ctx.reflex_orders.append({
            'key': 'stale_labs',
            'trigger': f'ანალიზები ჩაბარებულია {labs_age_days} დღის წინ (>14 დღე)',
            'action': 'საკონტროლო სისხლის საერთო, კოაგულოგრამა და კრეატინინი განახლდეს ოპერაციამდე',
            'done': bool(s.get('stale_labs_refreshed')),
        })


def eval_pulmonary(ctx):
    q, s, q_filled = ctx.q, ctx.s, ctx.q_filled
    pulm_cfg = rule_value('PULM_CXR_SPIROMETRY')
    cxr = s.get('cxr') or 'pending'
    ctx.cxr = cxr
    if cxr == 'infiltrate':
        ctx.red_flags.append('გულმკერდის რენტგენზე ინფილტრაცია / გამონაჟონი / საყურადღებო კერა — აცნობეთ ქირურგს!')

    needs_spirometry = (
        (ctx.open_aortic and (
            (q_filled and bool(q.get('pulm')))
            or cxr == 'emphysema'
            or ctx.true_low_mets
        ))
        or (ctx.spo2 is not None and ctx.spo2 < 94)
        or (s.get('spiro_fev1') is not None)
    )
    ctx.needs_spirometry = needs_spirometry
    if q_filled and q.get('smoker') and not ctx.is_venous:
        spiro_sfx = '' if needs_spirometry else ' (ESAIC 2024: უსიმპტომო მწეველთან რუტინული სპირომეტრია ოპერაციას არ აყოვნებს)'
        ctx.amber_flags.append(f'თამბაქოს მწეველი — რეკომენდებულია თამბაქოს შეწყვეტა და სუნთქვითი ვარჯიშები{spiro_sfx}.')

    spiro_fev1 = _int(s.get('spiro_fev1'), lo=10, hi=160)
    ctx.spiro_fev1 = spiro_fev1
    if needs_spirometry and spiro_fev1 is not None:
        if spiro_fev1 < pulm_cfg['fev1_red']:
            if ctx.anesth_cleared:
                ctx.amber_flags.append(f'სპირომეტრიაზე მძიმე დარღვევა (FEV1 {spiro_fev1}% < 50%) — შეფასებულია ანესთეზიოლოგის მიერ ✓')
            else:
                ctx.red_flags.append(
                    f'სპირომეტრიაზე მძიმე დარღვევა (FEV1 {spiro_fev1}% < 50%) — '
                    'მაღალი რესპირატორული რისკი! აცნობეთ ქირურგსა და ანესთეზიოლოგს.'
                )
        elif spiro_fev1 < pulm_cfg['fev1_amb']:
            ctx.amber_flags.append(f'სპირომეტრიაზე ზომიერი დაქვეითება (FEV1 {spiro_fev1}%) — საყურადღებოა ანესთეზიოლოგისთვის.')


def eval_carotid_gastro(ctx):
    pathway, q, s, q_filled = ctx.pathway, ctx.q, ctx.s, ctx.q_filled
    gi_cfg = rule_value('GI_CAROTID_CONCOMITANT_AND_PPI')
    needs_carotid_duplex = (
        pathway == PreopPatient.Pathway.CEA
        or (
            pathway in (PreopPatient.Pathway.AAA_INFRA, PreopPatient.Pathway.ABF, PreopPatient.Pathway.PAD_BYPASS)
            and q_filled and (bool(q.get('neuro')) or bool(q.get('carotid_bruit_symp')) or bool(s.get('carotid_symp')))
        )
    )
    ctx.needs_carotid_duplex = needs_carotid_duplex
    if pathway in (PreopPatient.Pathway.AAA_INFRA, PreopPatient.Pathway.ABF, PreopPatient.Pathway.PAD_BYPASS) and ctx.max_car is not None:
        op_short = PATHWAY_SHORT.get(pathway, 'ოპერაცია')
        if ctx.max_car >= gi_cfg['car_red'] or (ctx.max_car >= gi_cfg['car_amb'] and s.get('carotid_symp')):
            ctx.red_flags.append(
                f'ჰემოდინამიკურად მნიშვნელოვანი კაროტიდული სტენოზი ({ctx.max_car}%) — '
                f'აცნობეთ ქირურგს ეტაპობრივი ტაქტიკის (CEA <-> {op_short}) გადასაწყვეტად!'
            )
        elif ctx.max_car >= gi_cfg['car_amb']:
            ctx.amber_flags.append(f'ასიმპტომური კაროტიდული სტენოზი ({ctx.max_car}%) — ინტრაოპერაციული ჰიპოტენზიის თავიდან აცილება.')

    gastro_res = s.get('gastro_result') or 'pending'
    if ctx.is_venous or ctx.is_pta:
        needs_gastro = (
            bool(q.get('ulcer_active_symp'))
            or ((q_filled and bool(q.get('ulcer'))) and (ctx.hb_gl is not None and ctx.hb_gl < 100) and not ctx.is_urgent_case and not ctx.on_dialysis)
            or gastro_res != 'pending'
        )
    else:
        needs_gastro = (
            bool(q.get('ulcer_active_symp'))
            or ((ctx.hb_gl is not None and ctx.hb_gl < 100) and not ctx.is_urgent_case and not ctx.on_dialysis)
            or gastro_res != 'pending'
        )
    ctx.gastro_res, ctx.needs_gastro = gastro_res, needs_gastro
    if needs_gastro and gastro_res == 'acute_ulcer':
        ctx.red_flags.append('გასტროსკოპიაზე მწვავე წყლული / ეროზიული გასტრიტი — აცნობეთ ქირურგს.')
    elif q.get('ulcer_active_symp') and gastro_res == 'pending':
        ctx.red_flags.append(
            'აქტიური გასტროინტესტინური სისხლდენის / მწვავე წყლულის სიმპტომები (ტკივილი/მელენა) — '
            'აუცილებელია ეგდს, PPI გასტროპროტექცია და ქირურგის შეფასება!'
        )

    p2y12_early_cnt = sum([bool(q.get('med_clopidogrel')), bool(q.get('med_ticagrelor')), bool(q.get('med_prasugrel'))])
    has_dapt_high_gi = (p2y12_early_cnt >= 2) or (bool(q.get('med_aspirin')) and p2y12_early_cnt >= 1)
    has_oac_ap_ppi = ctx.on_oral_anticoag and (ctx.has_antiplatelet or ('cilostazol' in ctx.med_chips))
    has_elder_steroid_ppi = q_filled and ((ctx.age or 0) >= gi_cfg['ppi_age']) and ('steroid' in ctx.med_chips) and ctx.has_any_antithrombotic
    needs_ppi = bool(
        (
            bool(q.get('ulcer_active_symp'))
            or (gastro_res == 'acute_ulcer')
            or has_dapt_high_gi or has_oac_ap_ppi
            or (q_filled and (bool(q.get('ulcer')) or has_elder_steroid_ppi))
        )
        and 'ppi' not in ctx.med_chips
    )
    ctx.needs_ppi = needs_ppi
    if needs_ppi:
        ppi_pref_lbl = (
            'პანტოპრაზოლი 40 მგ (კლოპიდოგრელ/ცილოსტაზოლზე ომეპრაზოლს მოერიდეთ — CYP2C19!)'
            if (q.get('med_clopidogrel') or 'cilostazol' in ctx.med_chips)
            else 'ომეპრაზოლი / პანტოპრაზოლი 40 მგ'
        )
        ppi_trig_lbl = (
            'ანამნეზში წყლულოვანი დაავადება / აქტიური GI სიმპტომი / ეგდს წყლული'
            if (q.get('ulcer') or q.get('ulcer_active_symp') or gastro_res == 'acute_ulcer')
            else 'მაღალი გასტროინტესტინური სისხლდენის რისკი (DAPT / OAC+ანტიაგრეგანტი / ≥65 წ. სტეროიდზე — ESC 2022 Class I)'
        )
        ctx.reflex_orders.append({
            'key': 'ppi',
            'trigger': ppi_trig_lbl,
            'action': f'პერიოპერაციული გასტროპროტექცია — PPI ({ppi_pref_lbl}, ESC 2022 Class I)',
            'done': bool(s.get('ppi_started')) or ('ppi' in ctx.med_chips),
        })
