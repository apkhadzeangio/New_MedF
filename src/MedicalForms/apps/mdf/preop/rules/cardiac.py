"""Dual-track cardiac biomarkers/echo, ESC 2022/CARP audit, Stage 1 completeness & dynamic tasks."""
from ...models import PreopPatient
from ..calculators import _s_raw, _sat_num, calc_auto_asa
from ..constants import PATHWAY_SHORT
from ..registry import rule_value
from ..utils import _int, _num


def _eval_cardiac_biomarkers_and_audit(ctx):
    patient, pathway, q, s, q_filled = ctx.patient, ctx.pathway, ctx.q, ctx.s, ctx.q_filled
    cb = rule_value('CARDIO_BIOMARKERS_ECHO')
    carp = rule_value('CARDIO_CARP_AUDIT_GATE')
    lvef = _int(s.get('lvef'), lo=5, hi=95)
    nt_probnp = _int(s.get('nt_probnp'), lo=1, hi=100000)
    hs_ctn = _num(s.get('hs_ctn'), lo=0.1, hi=50000)
    lvef_eff = lvef if lvef is not None else _sat_num(_s_raw(s, 'lvef'), 5, 95)
    bnp_eff = nt_probnp if nt_probnp is not None else _sat_num(_s_raw(s, 'nt_probnp'), 1, 100000)
    ctn_eff = hs_ctn if hs_ctn is not None else _sat_num(_s_raw(s, 'hs_ctn'), 0.1, 50000)
    hr_eff = getattr(ctx, 'hr_sat', ctx.hr)
    ecg = s.get('ecg') or 'pending'
    ctx.lvef, ctx.nt_probnp, ctx.hs_ctn, ctx.ecg = lvef, nt_probnp, hs_ctn, ecg

    if bnp_eff is not None and bnp_eff >= cb['bnp_amb']:
        bnp_d = nt_probnp if nt_probnp is not None else int(bnp_eff)
        if ctx.cardio_cleared:
            ctx.amber_flags.append(f'NT-proBNP {bnp_d} პგ/მლ (≥125) — შეფასებულია და დაშვებულია კარდიოლოგის მიერ ✓')
        elif bnp_eff >= cb['bnp_red']:
            echo_req_s = f'ექოკარდიოგრაფია ჩატარებულია (LVEF {lvef}%) — სავალდებულოა კარდიოლოგის შეფასება.' if lvef is not None else 'სავალდებულოა ექოკარდიოგრაფია და კარდიოლოგის შეფასება.'
            ctx.red_flags.append(
                f'მაღალი NT-proBNP ({bnp_d} პგ/მლ >= {cb["bnp_red"]}) — გულის უკმარისობისა და პერიოპერაციული MACE მაღალი რისკი! {echo_req_s}'
            )
        elif lvef is not None:
            ctx.amber_flags.append(f'მომატებული NT-proBNP ({bnp_d} პგ/მლ >= {cb["bnp_amb"]}; LVEF {lvef}% შეფასებულია) — ESC 2022-ით საჭიროა კარდიოლოგის კონსულტაცია.')
        else:
            tte_cls = 'Class I' if (ctx.open_aortic or ctx.true_low_mets) else 'Class IIb'
            ctx.amber_flags.append(f'მომატებული NT-proBNP ({bnp_d} პგ/მლ >= {cb["bnp_amb"]}) — ESC 2022 {tte_cls}-ით განიხილეთ ექოკარდიოგრაფია და კარდიოლოგი.')

    if ctn_eff is not None and ctn_eff >= cb['ctn_amb']:
        ctn_d = hs_ctn if hs_ctn is not None else ctn_eff
        if ctx.cardio_cleared:
            ctx.amber_flags.append(f'საბაზისო hs-cTn {ctn_d} ნგ/ლ — შეფასებულია კარდიოლოგის მიერ ✓')
        elif ctn_eff >= cb['ctn_red']:
            ctx.red_flags.append(f'მნიშვნელოვნად მომატებული საბაზისო ტროპონინი (hs-cTn {ctn_d} ნგ/ლ) — გამოირიცხოს მწვავე მიოკარდიუმის დაზიანება კარდიოლოგთან!')
        else:
            ctx.amber_flags.append(f'საბაზისო hs-cTn {ctn_d} ნგ/ლ (>ULN) — დაფიქსირებულია პოსტოპერაციული MINS შედარებისთვის; შეაფასოს კარდიოლოგმა.')

    if ecg == 'afib_controlled' and hr_eff is not None and hr_eff > 110:
        ctx.red_flags.append(f'მოციმციმე არითმია — ტაქისისტოლია (HR {ctx.hr if ctx.hr is not None else int(hr_eff)}/წთ > 110) — სავალდებულოა რიტმის/სიხშირის კონტროლი კარდიოლოგთან!')
    if (lvef_eff is not None and lvef_eff < cb['lvef_red']) or s.get('echo_valve_issue') or ecg == 'ischemia_arrhythmia':
        if ctx.cardio_cleared:
            ctx.amber_flags.append('კარდიორისკი (LVEF < 40% / სარქვლოვანი პათოლოგია / ეკგ) შეფასებულია და დაშვებულია კარდიოლოგის მიერ ✓')
        else:
            ctx.red_flags.append('კარდიოლოგიური Red Flag (LVEF < 40% / მძიმე სარქვლოვანი მანკი / მწვავე ეკგ ცვლილება) — აცნობეთ ქირურგსა და კარდიოლოგს!')
    elif ecg == 'afib_controlled' and not (hr_eff is not None and hr_eff > 110):
        if ctx.cardio_cleared:
            ctx.amber_flags.append('ნორმოსისტოლური მოციმციმე არითმია — შეფასებულია და დაშვებულია კარდიოლოგის მიერ ✓')
        else:
            ctx.amber_flags.append('ეკგ-ზე ნორმოსისტოლური მოციმციმე არითმია — საჭიროა კარდიოლოგის შეფასება.')
    elif lvef_eff is not None and lvef_eff < cb['lvef_amb']:
        ctx.amber_flags.append(f'ზომიერად დაქვეითებული განდევნის ფრაქცია (LVEF {lvef or int(lvef_eff)}%).')

    is_pure_local_pta = (
        ctx.is_pta
        and not (q_filled and bool(q.get('pta_cant_lie_flat')))
        and (s.get('anesthesia_plan') in (None, '', 'auto', 'local'))
    )
    ckd_cardio_thresh = carp['ckd_pta'] if is_pure_local_pta else carp['ckd_std']
    # ESC 2022 Class III / CARP: stable asymptomatic CAD alone does NOT justify routine preoperative CAG/PCI!
    guideline_justifies_cag = (
        ctx.true_low_mets or (q_filled and (bool(q.get('recent_stent')) or 'angina' in ctx.cad_chips))
        or (lvef_eff is not None and lvef_eff < cb['lvef_red']) or (bnp_eff is not None and bnp_eff >= cb['bnp_red'])
        or (ctn_eff is not None and ctn_eff >= cb['ctn_amb']) or bool(s.get('echo_valve_issue')) or ecg == 'ischemia_arrhythmia'
    )
    guideline_justifies_extra_cardio = (
        guideline_justifies_cag or ctx.true_low_mets or (q_filled and bool(q.get('cad')))
        or (lvef_eff is not None and lvef_eff < cb['lvef_amb']) or ctx.needs_cied or (bnp_eff is not None and bnp_eff >= cb['bnp_amb'])
        or ecg == 'afib_controlled' or (hr_eff is not None and (hr_eff > 110 or hr_eff < 45))
        or (pathway not in (PreopPatient.Pathway.CEA, PreopPatient.Pathway.VENOUS) and q_filled and bool(q.get('neuro')))
        or (not ctx.is_venous and (ctx.on_dialysis or (ctx.egfr is not None and ctx.egfr < ckd_cardio_thresh)))
        or (not ctx.is_venous and q_filled and bool(q.get('dm_insulin')))
    )
    cardio_non_compliant = False
    if ctx.cardio_decision in ('stress', 'cag', 'pci'):
        cardio_non_compliant = not (guideline_justifies_cag if ctx.cardio_decision in ('cag', 'pci') else guideline_justifies_extra_cardio)
        who_cardio = patient.cardiologist or 'კარდიოლოგი'
        dec_disp = patient.get_cardio_decision_display()
        verb_dec = dec_disp if dec_disp.startswith('მოითხოვა') else f'მოითხოვა {dec_disp}'
        audit_tag = ' [ESC 2022 Class III / CARP — დაბალი/სტაბილური რისკის პაციენტთან!]' if cardio_non_compliant else ''
        ctx.red_flags.append(f'კარდიოლოგმა ({who_cardio}) {verb_dec}{audit_tag} — დაყოვნებამდე ან სტენტირებამდე სავალდებულოა ქირურგთან შეთანხმება!')

    ctx.guideline_justifies_extra_cardio, ctx.cardio_non_compliant = guideline_justifies_extra_cardio, cardio_non_compliant
    ctx.needs_cardio = ctx.open_aortic or guideline_justifies_extra_cardio or bool(s.get('irregular_hr')) or ctx.severe_htn or bool(ctx.cardio_decision)
    ctx.needs_echo_or_bnp = ctx.open_aortic or guideline_justifies_extra_cardio
    mandatory_tte = bool(('hf' in ctx.cad_chips) or ctx.has_mech_valve or s.get('echo_valve_issue') or (bnp_eff is not None and bnp_eff >= cb['bnp_red']))
    ctx.cardiac_screen_done = (lvef is not None or (not mandatory_tte and nt_probnp is not None and nt_probnp < cb['bnp_amb']))
    ctx.needs_cxr = (q_filled and (bool(q.get('pulm')) or 'hf' in ctx.cad_chips or 'fever_resp' in ctx.inf_chip_set)) or (ctx.spo2 is not None and ctx.spo2 < 94)


def _eval_stage1_completeness(ctx):
    patient, pathway, q, s, q_filled = ctx.patient, ctx.pathway, ctx.q, ctx.s, ctx.q_filled
    auto_asa = calc_auto_asa(patient, q, s, egfr=ctx.egfr, bmi=ctx.bmi, today=ctx.today)
    valid_asa = ('auto', *rule_value('SCORE_ASA_PHYSICAL_STATUS')['classes'])
    asa_raw = s.get('asa_class') if s.get('asa_class') in valid_asa else 'auto'
    asa_roman = auto_asa if asa_raw == 'auto' else asa_raw
    ctx.auto_asa, ctx.asa_roman = auto_asa, asa_roman

    ctx.needs_anesth = (
        (not ctx.is_venous and not ctx.is_pta)
        or ctx.is_phleb
        or (asa_roman == 'IV')
        or ctx.has_neuraxial_plan
        or (s.get('anesthesia_plan') in ('neuraxial', 'general', 'plexus'))
        or (ctx.is_pta and q_filled and bool(q.get('pta_cant_lie_flat')))
        or ctx.needs_cied
        or bool(q.get('glp1')) or bool(ctx.ft_meds.get('has_glp1'))
        or bool(q.get('ven_lidocaine_allergy')) or ('lidocaine' in ctx.allergy_chips)
        or (q_filled and (bool(q.get('anesth_issue')) or ctx.true_low_mets))
        or bool(s.get('anesth_done'))
    )

    renal_lab_present = ctx.on_dialysis or (ctx.egfr is not None and not ctx.creat_unit_ambiguous)
    target_limb_hemo_present = bool(ctx.scoped_abis or ctx.scoped_tps or ctx.scoped_tbis)
    ctx.renal_lab_present, ctx.target_limb_hemo_present = renal_lab_present, target_limb_hemo_present

    if ctx.is_venous:
        is_lean_evla = (not ctx.is_phleb) and (not ctx.has_neuraxial_plan) and (ctx.anesth_plan_raw != 'general')
        ven_needs_creat = (
            ctx.is_phleb or ctx.has_neuraxial_plan
            or bool(q.get('med_doac') or q.get('med_dabigatran') or q.get('metformin'))
            or ('rivaroxaban_2_5' in ctx.med_chips)
        )
        ven_needs_inr = (not is_lean_evla) or ctx.on_oral_anticoag or (s.get('liver_status') in ('2x', '3x'))
        ven_needs_gl = (
            (not is_lean_evla)
            or bool(q.get('dm') or q.get('dm_insulin') or q.get('metformin') or q.get('sglt2') or q.get('glp1'))
            or (ctx.hba1c is not None and ctx.hba1c >= 6.5)
            or (ctx.bmi is not None and ctx.bmi >= 30)
            or ((ctx.age or 0) >= 45)
        )
        ven_labs_ok = (
            ctx.hb_gl is not None and ctx.plt is not None
            and (not ven_needs_inr or ctx.inr is not None)
            and (not ven_needs_gl or ctx.gl_mmol is not None)
            and (not ven_needs_creat or renal_lab_present)
        )
        ven_ecg_ok = (not ctx.needs_ecg_venous) or (ctx.ecg != 'pending')
        ctx.s1_core_done = ven_labs_ok and ven_ecg_ok and ((s.get('ven_duplex_status') or 'pending') != 'pending')
    elif ctx.is_pta:
        ctx.s1_core_done = (
            ctx.hb_gl is not None and ctx.plt is not None and ctx.inr is not None
            and renal_lab_present and ctx.gl_mmol is not None
            and ctx.ecg != 'pending'
            and target_limb_hemo_present
            and (s.get('pta_access_site') or 'pending') != 'pending'
        )
    else:
        needs_urin_s1 = ctx.has_uti_symptoms
        common_s1 = (
            ctx.hb_gl is not None and renal_lab_present and ctx.inr is not None
            and (not needs_urin_s1 or ctx.urine_wbc is not None)
            and (not ctx.needs_carotid_duplex or (ctx.car_r is not None and ctx.car_l is not None))
            and (not ctx.needs_cxr or ctx.cxr != 'pending')
            and ctx.ecg != 'pending'
            and (not ctx.needs_echo_or_bnp or ctx.cardiac_screen_done)
        )
        if pathway == PreopPatient.Pathway.CEA:
            ctx.s1_core_done = (
                common_s1 and ctx.car_r is not None and ctx.car_l is not None
                and (s.get('cea_cta_status') or 'pending') != 'pending'
                and (not ctx.needs_brain_ct or (s.get('brain_imaging') or 'pending') != 'pending')
            )
        elif pathway == PreopPatient.Pathway.ABF:
            ctx.s1_core_done = (
                common_s1
                and (s.get('abf_clamp_zone') or 'pending') != 'pending'
                and (s.get('abf_outflow') or 'pending') != 'pending'
                and (ctx.abi_r is not None or ctx.abi_l is not None or ctx.tbi_measured)
            )
        elif ctx.is_pad_bypass:
            ctx.s1_core_done = (
                common_s1
                and (s.get('pad_runoff_status') or 'pending') != 'pending'
                and (s.get('pad_gsv_status') or 'pending') != 'pending'
                and target_limb_hemo_present
            )
        else:
            ctx.s1_core_done = common_s1 and (ctx.aaa_mm is not None)


def _build_dynamic_tasks_part1(ctx, tasks, bypass_em, bypass_sep):
    pathway, s = ctx.pathway, ctx.s
    if ctx.needs_stale_labs:
        tasks.append({'key': 'stale_labs', 'label': 'რეფლექს-განახლება: ხანდაზმული ანალიზების (>14 დღე) გადაბარება', 'done': bool(s.get('stale_labs_refreshed'))})
    if ctx.needs_anemia_panel and not bypass_em:
        tasks.append({'key': 'anemia', 'label': 'რეფლექს-პაკეტი (Hb < 115): ფერიტინი, შრატის რკინა/TSAT, რეტიკულოციტები', 'done': ctx.anemia_panel_satisfied})
    if ctx.pbm_iron_required:
        tasks.append({'key': 'pbm_iron', 'label': 'PBM პროტოკოლი (რკინადეფიციტი): ინტრავენური რკინის ინფუზია / შეთანხმება', 'done': bool(s.get('pbm_iv_iron_done'))})
    if ctx.needs_hba1c_reflex and not bypass_em:
        tasks.append({'key': 'hba1c', 'label': 'რეფლექს-ანალიზი (გლუკოზა >= 7.0): HbA1c (%)', 'done': ctx.hba1c is not None})
    if ctx.needs_endo_reflex and not bypass_em:
        tasks.append({'key': 'endo', 'label': 'რეფლექს-კონსულტაცია: ენდოკრინოლოგი (გლიკემიის კორექცია)', 'done': bool(s.get('endo_consult_done'))})
    if ctx.needs_nephro_panel and (ctx.on_dialysis or not bypass_em):
        nephro_lean_t = ctx.is_venous or ctx.is_pta or bool(s.get('cta_disc')) or (ctx.eff_renal is not None and ctx.eff_renal >= 30)
        n_lbl = (
            'დიალიზის გრაფიკი (უჰეპარინო სესია) და K+ შეთანხმება ნეფროლოგთან'
            if ctx.on_dialysis else
            ('რეფლექს-პაკეტი (CrCl/eGFR < 45): შარდოვანა, ელექტროლიტები და ნეფროლოგი' if nephro_lean_t
             else 'რეფლექს-პაკეტი (CrCl/eGFR < 30): შარდოვანა, Ca/P, თირკმლის ექო და ნეფროლოგი')
        )
        tasks.append({'key': 'nephro', 'label': n_lbl, 'done': bool(s.get('nephro_panel_done'))})
    if ctx.needs_hydration and not ctx.is_rupture_suspect:
        c_tag = f' (მაქს. კონტრასტი ≤ {ctx.max_contrast_ml} მლ)' if ctx.max_contrast_ml else ''
        na_eff = getattr(ctx, 'na_val_sat', ctx.na_val)
        if na_eff is not None and na_eff >= 150:
            hyd_mode = 'ჰიპერნატრიემია — 0.9% NaCl-ის გარეშე!'
        elif ctx.chf_hyd_flag:
            hyd_mode = 'შეზღუდული ჰიდრატაცია (0.5 მლ/კგ/სთ — HFrEF!)'
        else:
            hyd_mode = 'იზოტონური ჰიდრატაცია'
        tasks.append({'key': 'hydration', 'label': f'კონტრასტ-ნეფროპროტექცია (CrCl/eGFR < 60): {hyd_mode}{c_tag}', 'done': bool(s.get('hydration_planned'))})
    if ctx.needs_tsh and not bypass_em:
        tsh_lbl = 'რეფლექს-ანალიზი იოდის კონტრასტამდე: TSH და FT4' if ctx.uses_contrast else 'რეფლექს-ანალიზი ოპერაციამდე: TSH და FT4'
        tasks.append({'key': 'tsh', 'label': tsh_lbl, 'done': ctx.tsh_res != 'pending'})
    if ctx.needs_dental and not bypass_sep:
        tasks.append({'key': 'dental', 'label': 'სტომატოლოგის სანაცია პროთეზირებამდე (წერილობითი ცნობა)', 'done': bool(s.get('dental_sanitation_done'))})
    if ctx.needs_contrast_premed:
        tasks.append({'key': 'premed', 'label': 'იოდზე ალერგიის პრემედიკაციის სქემა (სტეროიდი + ანტიჰისტამინი)', 'done': bool(s.get('contrast_premed_done'))})
    if ctx.needs_cied:
        tasks.append({'key': 'cied', 'label': 'კარდიოსტიმულატორის / ICD შემოწმება (მაგნიტის რეჟიმი / ბიპოლარი)', 'done': ctx.cied_ok})
    if ctx.needs_tbi_check:
        tbi_lbl = (
            'რეფლექს-დოპლერი (ABI > 1.40): თითის წნევა (Toe Pressure) / TBI'
            if ctx.abi_non_compressible
            else 'რეფლექს-დოპლერი (დიაბეტი / დიალიზი + CLTI): თითის წნევა (Toe Pressure) / TBI'
        )
        tasks.append({'key': 'tbi', 'label': tbi_lbl, 'done': ctx.tbi_measured})
    if ctx.needs_alt_vein:
        tasks.append({'key': 'alt_vein', 'label': 'რეფლექს ექო-კარტირება: კონტრალატერალური GSV / SSV / ხელის ვენები', 'done': (s.get('alt_vein_mapped') or 'pending') != 'pending'})
    if pathway == PreopPatient.Pathway.CEA and ctx.needs_cea_antiplatelet:
        if getattr(ctx, 'active_thrombolysis_window', False):
            ap_t_lbl = 'ასპირინი 75–100 მგ (rt-PA ≤6 დღე — P2Y12/DAPT არა!)'
        elif 'nsaid' in ctx.allergy_chips:
            ap_t_lbl = 'ალტერნატიული P2Y12 (ასპირინზე ალერგია!)' if 'clopidogrel_intol' in ctx.allergy_chips else 'კლოპიდოგრელი 75 მგ (ასპირინზე ალერგია!)'
        elif 'clopidogrel_intol' in ctx.allergy_chips:
            ap_t_lbl = 'ასპირინი (კლოპიდოგრელზე აუტანლობა!)'
        else:
            ap_t_lbl = 'ასპირინი ± კლოპიდოგრელი'
        tasks.append({'key': 'cea_antiplatelet', 'label': f'CEA ანტიაგრეგანტი ({ap_t_lbl}) დაწყებულია', 'done': bool(s.get('cea_antiplatelet_started'))})
    if pathway == PreopPatient.Pathway.CEA and ctx.needs_neuro:
        tasks.append({'key': 'neuro', 'label': 'ნევროლოგის წინასაოპერაციო კონსულტაცია (mRS / სტატუსი)', 'done': bool(s.get('neuro_consult_done'))})
    if pathway == PreopPatient.Pathway.CEA and ctx.needs_laryngoscopy:
        tasks.append({'key': 'laryng', 'label': 'ლარინგოსკოპია — სახმო იოგების შემოწმება (ENT)', 'done': (s.get('laryngoscopy_res') or 'pending') != 'pending'})


def _build_dynamic_tasks_part2(ctx, tasks, bypass_em):
    patient, pathway, q, s = ctx.patient, ctx.pathway, ctx.q, ctx.s
    if (ctx.is_venous or pathway == PreopPatient.Pathway.AAA_INFRA) and ctx.needs_abi_check and not ctx.is_rupture_suspect:
        tasks.append({'key': 'abi', 'label': 'გოჯ-მხრის ინდექსი (ABI)', 'done': ctx.min_abi is not None})
    if ctx.is_venous and ctx.needs_vte_prophylaxis:
        ven_severe_renal_t = ctx.on_dialysis or (ctx.eff_renal is not None and ctx.eff_renal < 30)
        has_hit = 'hit_heparin' in ctx.allergy_chips
        if 'mech_valve' in ctx.cad_chips:
            vte_lbl = 'ვარფარინი (VKA) + IV ბივალირუდინი/არგატრობანი (HIT!)' if has_hit else 'ვარფარინი (VKA) — DOAC აკრძალულია!'
        elif has_hit:
            vte_lbl = 'IPC / არგატრობანი-ბივალირუდინი (HIT + CrCl<30!)' if ven_severe_renal_t else ('IPC / IV ბივალირუდინი (HIT + ღვიძლის ≥3x!)' if ctx.liver_3x else 'ფონდაპარინუქსი / DOAC (HIT!)')
        elif ven_severe_renal_t or ctx.liver_3x:
            vte_lbl = 'კანქვეშა UFH / დოზა-კორექტირებული LMWH (DOAC არა!)'
        else:
            vte_lbl = 'LMWH / DOAC'
        tasks.append({'key': 'vte', 'label': f'თრომბოპროფილაქტიკის სქემის შეთანხმება (Caprini {ctx.caprini_score} — {vte_lbl})', 'done': bool(s.get('vte_prophylaxis_planned'))})
    if ctx.needs_spirometry and not bypass_em:
        tasks.append({'key': 'spiro', 'label': 'სპირომეტრია (FEV1%)', 'done': ctx.spiro_fev1 is not None})
    if ctx.needs_gastro and not bypass_em:
        tasks.append({'key': 'gastro', 'label': 'გასტროსკოპია (ეგდს)', 'done': ctx.gastro_res != 'pending'})
    if ctx.needs_ppi:
        ppi_t_lbl = (
            'გასტროპროტექცია (პანტოპრაზოლი 40 მგ — CYP2C19 უსაფრთხო)'
            if (q.get('med_clopidogrel') or 'cilostazol' in ctx.med_chips)
            else 'გასტროპროტექცია (PPI: ომეპრაზოლი / პანტოპრაზოლი 40 მგ)'
        )
        tasks.append({'key': 'ppi', 'label': ppi_t_lbl, 'done': bool(s.get('ppi_started')) or ('ppi' in ctx.med_chips)})
    if ctx.uti_suspected and not bypass_em:
        tasks.append({'key': 'uti', 'label': 'შარდის ბაქტერიოლოგია + ანტიბიოტიკოგრამა / სანაცია', 'done': bool(s.get('urine_culture_done'))})
    if (pathway in (PreopPatient.Pathway.ABF, PreopPatient.Pathway.PAD_BYPASS, PreopPatient.Pathway.PAD_PTA) or ctx.is_venous) and ctx.needs_wound_care:
        w_lbl = 'ვენური წყლულის / კანის მოვლის შეთანხმება ქირურგთან' if ctx.is_venous else 'ტერფის ნაცხი / საზარდულის კანის სანაცია'
        tasks.append({'key': 'wound', 'label': w_lbl, 'done': bool(s.get('wound_care_done'))})
    if ctx.is_venous and not ctx.compression_contra:
        tbi_comp_ok = (not ctx.abi_non_compressible) or ctx.tbi_comp_safe or ctx.tbi_reduced_comp
        comp_lbl = (
            'მსუბუქი I კლასის კომპრესიული წინდის (<20 mmHg) შერჩევა (სასაზღვრო TP/TBI)'
            if ctx.tbi_reduced_comp else '2 კლასის კომპრესიული წინდის / გეტრის ზომის შერჩევა'
        )
        tasks.append({'key': 'comp', 'label': comp_lbl, 'done': bool(s.get('compression_ready')) and tbi_comp_ok})
    if ctx.is_pta and ctx.q_filled:
        if ctx.has_mech_valve:
            dapt_lbl = 'ანტითრომბოზული სქემა მექ. სარქველზე: ვარფარინი (VKA) ± მონო-ანტიაგრეგანტი (DOAC / VOYAGER აკრძალულია!)'
        elif ctx.has_full_oac:
            dapt_lbl = f'ანტითრომბოზული სქემა OAC ფონზე: ანტიკოაგულანტი + {ctx.oac_partner_lbl} (სამმაგი თერაპიის გარეშე!)'
        elif ctx.has_active_stent_dapt:
            dapt_lbl = f'უწყვეტი DAPT კორონარული სტენტისთვის ({ctx.alt_p2y12_lbl})'
        elif 'nsaid' in ctx.allergy_chips:
            dapt_lbl = f'ანტითრომბოზული მზაობა: {ctx.alt_p2y12_lbl} (ასპირინზე/აასს-ზე ალერგია!)'
        elif getattr(ctx, 'voyager_safe', False):
            dapt_lbl = f'ანტითრომბოზული მზაობა (DAPT: ასპირინი + {ctx.alt_p2y12_lbl} ან VOYAGER PAD)'
        else:
            dapt_lbl = f'ანტითრომბოზული მზაობა (DAPT: ასპირინი + {ctx.alt_p2y12_lbl})'
        tasks.append({'key': 'dapt', 'label': dapt_lbl, 'done': bool(s.get('dapt_ready'))})
    if ctx.open_major:
        tasks.append({'key': 'blood', 'label': 'სისხლის კომპონენტების რეზერვის მომზადება', 'done': bool(s.get('blood_reserved'))})
    if pathway == PreopPatient.Pathway.AAA_INFRA and patient.op_subtype == 'evar':
        tasks.append({'key': 'sizing', 'label': 'EVAR სტენტ-გრაფტის ზომების დათვლა და მზაობა', 'done': bool(s.get('evar_sized'))})
    if ctx.needs_med_hold:
        tasks.append({'key': 'med_hold', 'label': 'მედიკამენტების მიღების/შეჩერების სქემის დადასტურება', 'done': bool(s.get('med_hold_agreed'))})
    if ctx.needs_cardio and not bypass_em:
        tasks.append({'key': 'cardio', 'label': f'კარდიოლოგის კონსულტაცია ({patient.cardiologist or "ლიკა ინჯია"})', 'done': ctx.cardio_cleared})
    if ctx.needs_anesth:
        tasks.append({'key': 'anesth', 'label': 'ანესთეზიოლოგის კონსულტაცია', 'done': ctx.anesth_cleared})


def eval_cardiac_and_tasks(ctx):
    _eval_cardiac_biomarkers_and_audit(ctx)
    _eval_stage1_completeness(ctx)

    pw_short = PATHWAY_SHORT.get(ctx.pathway, 'AAA')
    ctx.pw_short = pw_short
    tasks = [
        {'key': 'q', 'label': f'კითხვარების შევსება (ზოგადი + {pw_short})', 'done': ctx.q_filled},
        {
            'key': 's1',
            'label': (
                'საფეხური 1: ვენების დუპლექსი და საბაზისო ანალიზები' if ctx.is_venous
                else ('საფეხური 1: ანალიზები, eGFR, ეკგ, ABI და მიდგომა' if ctx.is_pta
                      else 'საფეხური 1: საბაზისო ანალიზები და ჩვენებითი კვლევები')
            ),
            'done': ctx.s1_core_done,
        },
    ]
    bypass_em = ctx.is_rupture_suspect
    bypass_sep = ctx.is_acute_emergency or ctx.is_septic_or_mycotic_urgent
    _build_dynamic_tasks_part1(ctx, tasks, bypass_em, bypass_sep)
    _build_dynamic_tasks_part2(ctx, tasks, bypass_em)

    ctx.tasks = tasks
    ctx.done_count = sum(1 for t in tasks if t['done'])
    ctx.all_tasks_done = (ctx.done_count == len(tasks))
    ctx.all_reflex_done = bool(ctx.reflex_orders) and all(r['done'] for r in ctx.reflex_orders)
