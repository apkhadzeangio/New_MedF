"""Chronic Venous Disease (EVLA / Phlebectomy) pathway rules and cross-pathway urgency flags."""
from ...models import PreopPatient
from ..calculators import _ihd_and_chf_flags, _valid_bilateral_sbp
from ..registry import rule_value
from ..utils import _num, is_pregnancy_eligible


def _eval_venous_lido_and_contra(ctx, ceap, ven_dup, gsv_mm):
    patient, q, s, q_filled = ctx.patient, ctx.q, ctx.s, ctx.q_filled
    lido_cfg = rule_value('VEN_LIDOCAINE_MAX_DOSE')
    has_lido_allergy = bool(q.get('ven_lidocaine_allergy') or 'lidocaine' in ctx.allergy_chips)
    if patient.op_subtype == PreopPatient.OpSubtype.EVLA and ctx.weight_num is not None and not has_lido_allergy:
        ht_cm_lido = _num(s.get('height_cm'), lo=100, hi=230)
        if ctx.bmi is not None and ctx.bmi >= 30 and ht_cm_lido is not None:
            ibw_lido = (45.5 if patient.sex == 'F' else 50.0) + 0.91 * max(0.0, ht_cm_lido - 152.4)
            dosing_wt_lido = min(ctx.weight_num, ibw_lido + 0.4 * max(0.0, ctx.weight_num - ibw_lido))
        else:
            dosing_wt_lido = ctx.weight_num
        _, chf_lido = _ihd_and_chf_flags(q, s)
        reduced_lido_cl = bool(
            chf_lido
            or (s.get('liver_status') in ('2x', '3x'))
            or ('amiodarone' in ctx.med_chips)
            or ((ctx.age or 0) >= 80 and (ctx.on_dialysis or (ctx.eff_renal is not None and ctx.eff_renal < 45)))
        )
        if reduced_lido_cl:
            ctx.lidocaine_max_mg = min(lido_cfg['max_reduced'], int(round(dosing_wt_lido * lido_cfg['mg_per_kg'] * lido_cfg['reduced_factor'])))
        else:
            ctx.lidocaine_max_mg = min(lido_cfg['max_std'], int(round(dosing_wt_lido * lido_cfg['mg_per_kg'])))

    dvt_stop, obs_stop = rule_value('VEN_ACUTE_DVT_OBSTRUCTION_STOP')
    if ven_dup == dvt_stop:
        dvt_msg = 'ვენების დუპლექსით მწვავე ღრმა ვენების თრომბოზი (DVT) — გეგმური EVLA / ფლებექტომია აბსოლუტურად უკუნაჩვენებია! სასწრაფოდ აცნობეთ ქირურგს.'
        ctx.red_flags.append(dvt_msg)
        ctx.hard_contraindications.append(dvt_msg)
    elif ven_dup == obs_stop:
        obs_msg = 'ღრმა ვენური სისტემის ოკლუზია (ზედაპირული ვენა მთავარი კოლატერალია) — აბლაცია/ფლებექტომია უკუნაჩვენებია! აცნობეთ ქირურგს.'
        ctx.red_flags.append(obs_msg)
        ctx.hard_contraindications.append(obs_msg)
    elif ven_dup == 'superficial_thrombus' or q.get('ven_acute_svt'):
        ctx.red_flags.append(
            'მწვავე აღმავალი თრომბოფლებიტი (SVT) — გადაამოწმეთ SFJ/SPJ-სთან დისტანცია დუპლექსით და შეათანხმეთ ტაქტიკა ქირურგთან!'
        )

    if q.get('ven_pregnant') or q.get('pregnant_possible'):
        if is_pregnancy_eligible(patient):
            preg_msg = 'ორსულობა / ლაქტაცია / მშობიარობის შემდგომი პერიოდი (<3 თვ) — გეგმური EVLA / ფლებექტომია უკუნაჩვენებია (ESVS 2022)!'
            ctx.red_flags.append(preg_msg)
            ctx.hard_contraindications.append(preg_msg)
        else:
            ctx.amber_flags.append(
                'კითხვარში მონიშნულია „ორსულობა“, რაც შეუსაბამოა პაციენტის სქესთან/ასაკთან — გადაამოწმეთ ჩანაწერი!'
            )
    if has_lido_allergy:
        lido_msg = (
            'ალერგია ლიდოკაინზე / ადგილობრივ ანესთეტიკზე — ტუმესცენტური ანესთეზია საშიშია! '
            'სავალდებულოა ქირურგისა და ანესთეზიოლოგის ჩართვა.'
        )
        ctx.red_flags.append(lido_msg)
        if patient.op_subtype == PreopPatient.OpSubtype.EVLA and (s.get('anesthesia_plan') in (None, '', 'auto', 'local')):
            ctx.hard_contraindications.append(lido_msg)
    if q.get('ven_skin_infection'):
        ctx.red_flags.append('მწვავე კანის ინფექცია / წითელი ქარი / ჩირქოვანი ეგზემა პუნქციის/ჭრილობის ზონაში — საჭიროა წინასწარი სანაცია!')
    if q.get('ven_bleeding'):
        ctx.red_flags.append('ანამნეზში სისხლდენა ვარიკოზული კვანძიდან (ESVS 2022) — აცნობეთ ქირურგს პრიორიტეტული ჩარევისთვის!')

    gsv_large_mm = rule_value('VEN_DUPLEX_ANATOMY_AND_CEAP')['gsv_large_mm']
    if gsv_mm is not None and gsv_mm >= gsv_large_mm:
        ctx.amber_flags.append(f'საჩინო ვენის დიდი დიამეტრი ({gsv_mm} მმ >= {gsv_large_mm} მმ) — გაითვალისწინეთ EHIT-ის პრევენცია და ენერგიის რეჟიმი.')
    if s.get('ven_epifascial'):
        ctx.amber_flags.append('დუპლექსით ეპიფასციური / კანთან ახლო ღერო (<5 მმ) — საჭიროა ჭარბი ტუმესცენტური ბალიში კანის დამწვრობის/პიგმენტაციის ასაცილებლად.')
    if s.get('ven_tortuous'):
        ctx.amber_flags.append('დუპლექსით ძლიერ დაკლაკნილი ღერო / სინექიები — გაითვალისწინეთ დამატებითი პუნქციის ან ფლებექტომიის საჭიროება.')
    if q_filled and ceap == 'C6' and not q.get('ven_skin_infection'):
        ctx.amber_flags.append('აქტიური ვენური წყლული (CEAP C6) — ESVS 2022 Class I-ით ადრეული EVLA აჩქარებს წყლულის შეხორცებას.')
    if q_filled and patient.side not in ('R', 'L', 'B'):
        ctx.amber_flags.append('მიუთითეთ საოპერაციო კიდური (მარჯვენა, მარცხენა ან ორმხრივი).')


def _eval_venous_arterial_and_vte(ctx, ceap):
    q, s, q_filled = ctx.q, ctx.s, ctx.q_filled
    art_cfg = rule_value('VEN_ARTERIAL_ISCHEMIA_COMPRESSION')
    gl_eff = ctx.gl_mmol if ctx.gl_mmol is not None else getattr(ctx, 'gl_mmol_sat', None)
    age_eff = ctx.age if ctx.age is not None else getattr(ctx, 'age_sat', None)
    bmi_eff = ctx.bmi if ctx.bmi is not None else getattr(ctx, 'bmi_sat', None)
    eff_dm = bool(q.get('dm') or q.get('dm_insulin') or (gl_eff is not None and gl_eff >= 11.1))
    ctx.needs_abi_check = q_filled and (
        bool(q.get('claudication'))
        or ceap == 'C6'
        or (ceap == 'C5' and (eff_dm or (age_eff or 0) >= 65))
    )
    abi_eff = ctx.min_abi if ctx.min_abi is not None else getattr(ctx, 'min_comp_abi_sat', None)
    if abi_eff is not None:
        if abi_eff < art_cfg['abi_stop']:
            sev_ven_isch = (
                f'ქვემო კიდურის მძიმე არტერიული იშემია (ABI {abi_eff} < {art_cfg["abi_stop"]:.2f}) — '
                'ვენური აბლაცია/ფლებექტომია და კომპრესია აბსოლუტურად უკუნაჩვენებია (ESVS 2022)! სასწრაფოდ აცნობეთ ქირურგს.'
            )
            ctx.red_flags.append(sev_ven_isch)
            ctx.hard_contraindications.append(sev_ven_isch)
        elif abi_eff < art_cfg['abi_red']:
            ctx.red_flags.append(
                f'ქვემო კიდურის არტერიული უკმარისობა (ABI {abi_eff} < {art_cfg["abi_red"]:.2f}) — კომპრესიული ტრიკოტაჟი და ვენური ჩარევა საშიშია! აცნობეთ ქირურგს.'
            )
        elif abi_eff <= art_cfg['abi_border']:
            ctx.amber_flags.append(f'სასაზღვრო გოჯ-მხრის ინდექსი (ABI {abi_eff}) — კომპრესიის ხარისხი შეათანხმეთ ქირურგთან.')
    tp_eff = ctx.min_tp if ctx.min_tp is not None else getattr(ctx, 'min_tp_sat', None)
    tbi_eff = ctx.min_tbi if ctx.min_tbi is not None else getattr(ctx, 'min_tbi_sat', None)
    tp_is_stop = bool(tp_eff is not None and tp_eff < art_cfg['tp_stop'])
    if tp_is_stop:
        tp_ven_isch = (
            f'კრიტიკულად დაბალი თითის წნევა (TP {tp_eff} mmHg < {art_cfg["tp_stop"]} mmHg) — მძიმე არტერიული იშემია, '
            'გეგმური ვენური ჩარევა და კომპრესია უკუნაჩვენებია (ESVS 2022)!'
        )
        ctx.red_flags.append(tp_ven_isch)
        ctx.hard_contraindications.append(tp_ven_isch)
    elif tp_eff is not None and tp_eff < 50 and not ctx.abi_non_compressible:
        ctx.red_flags.append(
            f'დაბალი თითის წნევა (TP {tp_eff} mmHg < 50 mmHg) — არტერიული უკმარისობა, კომპრესიული ტრიკოტაჟი უკუნაჩვენებია!'
        )
    elif tbi_eff is not None and tbi_eff < 0.50 and not ctx.abi_non_compressible:
        ctx.red_flags.append(
            f'დაბალი თით-მხრის ინდექსი (TBI {tbi_eff} < 0.50) — არტერიული უკმარისობა, კომპრესიული ტრიკოტაჟი უკუნაჩვენებია!'
        )
    if ctx.abi_non_compressible and not tp_is_stop:
        if (tp_eff is not None and tp_eff < 50) or (tbi_eff is not None and tbi_eff < 0.50):
            ctx.red_flags.append(
                f'არაკომპრესირებადი ABI ({ctx.max_abi}) + დაბალი Toe Pressure/TBI (TP {tp_eff if tp_eff is not None else "—"} / '
                f'TBI {tbi_eff if tbi_eff is not None else "—"}) — არტერიული უკმარისობა, კომპრესიული ტრიკოტაჟი უკუნაჩვენებია!'
            )
        elif ctx.tbi_reduced_comp:
            ctx.amber_flags.append(
                f'არაკომპრესირებადი არტერია (ABI {ctx.max_abi} > 1.40) + სასაზღვრო TP/TBI '
                f'(TP {ctx.min_tp if ctx.min_tp is not None else "—"} / TBI {ctx.min_tbi if ctx.min_tbi is not None else "—"}) — '
                '2 კლასის ნაცვლად დასაშვებია მხოლოდ მსუბუქი I კლასის კომპრესია (<20 mmHg) ქირურგთან შეთანხმებით!'
            )
        elif not ctx.tbi_comp_safe:
            ctx.amber_flags.append(
                f'არაკომპრესირებადი არტერია (ABI {ctx.max_abi} > 1.40) — 2 კლასის კომპრესიული წინდის დანიშვნამდე '
                'სამიზნე კიდურზე სავალდებულოა Toe Pressure (≥60 mmHg) ან TBI (≥0.70)!'
            )

    high_vte = q_filled and (
        bool(q.get('ven_dvt_history')) or bool(q.get('ven_thrombophilia'))
        or bool(q.get('ven_cancer_immob')) or bool(q.get('ven_acute_svt'))
        or ('hit_heparin' in ctx.allergy_chips)
    )
    mod_vte_count = sum([
        bool((age_eff or 0) >= 60),
        bool(bmi_eff is not None and bmi_eff >= 30),
        bool(q_filled and q.get('ven_hormones')),
        ctx.is_phleb,
        bool(q_filled and ceap in ('C5', 'C6')),
    ])
    ctx.needs_vte_prophylaxis = high_vte or (q_filled and (mod_vte_count >= 2 or ctx.caprini_score >= rule_value('VEN_VTE_CAPRINI_PROPHYLAXIS')))
    if ctx.needs_vte_prophylaxis and not s.get('vte_prophylaxis_planned'):
        ven_severe_renal = ctx.on_dialysis or (ctx.eff_renal is not None and ctx.eff_renal < 30)
        has_hit = 'hit_heparin' in ctx.allergy_chips
        if 'mech_valve' in ctx.cad_chips:
            vte_agents = (
                'ვარფარინი (VKA) + IV ბივალირუდინი/არგატრობანი (HIT + მექ. სარქველი — ჰეპარინი, ფონდაპარინუქსი და DOAC აკრძალულია!)'
                if has_hit else 'ვარფარინი (VKA) კარდიოლოგის სქემით — DOAC აკრძალულია!'
            )
        elif has_hit:
            vte_agents = (
                'IPC / არგატრობანი-ბივალირუდინი — HIT + CrCl<30: ჰეპარინი, ფონდაპარინუქსი და DOAC აკრძალულია!'
                if ven_severe_renal else
                ('IPC / IV ბივალირუდინი — HIT + ღვიძლის ≥3x: ჰეპარინი, DOAC და ფონდაპარინუქსი არარეკომენდებულია!' if ctx.liver_3x else 'ფონდაპარინუქსი / DOAC — HIT: ჰეპარინი აკრძალულია!')
            )
        elif ven_severe_renal:
            vte_agents = 'კანქვეშა UFH / დოზა-კორექტირებული LMWH (DOAC არა!)'
        elif ctx.liver_3x:
            vte_agents = 'LMWH / UFH კოაგულოგრამის კონტროლით (ღვიძლის ≥3x-ზე DOAC არა!)'
        else:
            vte_agents = 'LMWH / DOAC'
        ctx.amber_flags.append(
            f'მაღალი თრომბოემბოლიური რისკი (Caprini {ctx.caprini_score}) — შეათანხმეთ ინდივიდუალური თრომბოპროფილაქტიკა ({vte_agents}) ქირურგთან.'
        )


def eval_venous(ctx):
    from ..calculators import _s_raw, _sat_num
    patient, q, s, q_filled = ctx.patient, ctx.q, ctx.s, ctx.q_filled
    ceap = q.get('ven_ceap') if q.get('ven_ceap') in ('C2', 'C3', 'C4', 'C5', 'C6') else 'C2'
    ven_dup = s.get('ven_duplex_status') or 'pending'
    gsv_mm = _num(s.get('ven_gsv_mm'), lo=1, hi=50)
    _eval_venous_lido_and_contra(ctx, ceap, ven_dup, gsv_mm)
    _eval_venous_arterial_and_vte(ctx, ceap)

    lvef_v = _sat_num(_s_raw(s, 'lvef'), 5, 95)
    bnp_v = _sat_num(_s_raw(s, 'nt_probnp'), 1, 100000)
    gl_eff = ctx.gl_mmol if ctx.gl_mmol is not None else getattr(ctx, 'gl_mmol_sat', None)
    age_eff = ctx.age if ctx.age is not None else getattr(ctx, 'age_sat', None)
    eff_dm = bool(q.get('dm') or q.get('dm_insulin') or (gl_eff is not None and gl_eff >= 11.1))
    eff_cardiac = bool(q.get('cad') or ctx.cad_chips or q.get('recent_stent') or (lvef_v is not None and lvef_v < 40) or (bnp_v is not None and bnp_v >= 300))
    ctx.needs_wound_care = q_filled and (ceap == 'C6' or bool(q.get('ven_skin_infection')))
    ctx.needs_ecg_venous = (
        ctx.is_phleb
        or ((age_eff or 0) >= 50)
        or (q_filled and bool(q.get('htn')))
        or eff_cardiac or eff_dm
        or bool(s.get('irregular_hr'))
    )
    if ctx.days > ctx.t_days and not patient.approved_at and not ctx.is_on_hold and ctx.is_active_case:
        ctx.amber_flags.append(f'წინასაოპერაციო მომზადებიდან გასულია {ctx.days} დღე — გადაამოწმეთ ანალიზების აქტუალობა.')

    if q_filled:
        gsv_part = f' · {int(gsv_mm)}მმ' if gsv_mm else ''
        ctx.key_badge = f'{ceap}{gsv_part}'
        ctx.key_badge_urgent = bool(q.get('ven_acute_svt') or q.get('ven_bleeding') or ceap == 'C6')
    elif gsv_mm:
        ctx.key_badge = f'{int(gsv_mm)} მმ'


def eval_cross_pathway_and_urgency(ctx):
    from ..calculators import _s_raw, _sat_num
    patient, pathway, q, s = ctx.patient, ctx.pathway, ctx.q, ctx.s
    hemo_cfg = rule_value('PAD_CLTI_HEMO_THRESHOLDS')
    hba1c_x = _num(s.get('hba1c'), lo=3.0, hi=25.0)
    gl_eff = ctx.gl_mmol if ctx.gl_mmol is not None else getattr(ctx, 'gl_mmol_sat', None)
    eff_dm = bool(q.get('dm') or q.get('dm_insulin') or (gl_eff is not None and gl_eff >= 11.1) or (hba1c_x is not None and hba1c_x >= 6.5))

    if (
        pathway in (PreopPatient.Pathway.ABF, PreopPatient.Pathway.PAD_BYPASS, PreopPatient.Pathway.PAD_PTA)
        and ctx.is_clti_any and (eff_dm or ctx.on_dialysis)
        and not ctx.abi_non_compressible
    ):
        ctx.needs_tbi_check = True
        ctx.reflex_orders.append({
            'key': 'tbi',
            'trigger': 'შაქრიანი დიაბეტი / დიალიზი + CLTI (მონკებერგის კალციფიკაციისა და ფსევდონორმალური ABI-ის რისკი — ESVS 2024 / WIfI)',
            'action': 'სამიზნე კიდურზე თითის წნევის (Toe Pressure, mmHg) ან TBI განსაზღვრა WIfI სტადირებისა და პერფუზიის ზუსტი შეფასებისთვის',
            'done': ctx.tbi_measured,
        })

    aaa_eff = ctx.aaa_mm if ctx.aaa_mm is not None else getattr(ctx, 'aaa_mm_sat', None)
    if pathway != PreopPatient.Pathway.AAA_INFRA and aaa_eff is not None:
        min_t_cross = rule_value('AAA_FEMALE_MIN_MM') if patient.sex == 'F' else rule_value('AAA_MALE_MIN_MM')
        if aaa_eff >= min_t_cross:
            ctx.red_flags.append(
                f'თანმხლები მუცლის აორტის ანევრიზმა ({aaa_eff} მმ >= {min_t_cross} მმ) — შეათანხმეთ პრიორიტეტულობა და ეტაპობრივი ტაქტიკა ქირურგთან!'
            )
    if not ctx.is_venous and (s.get('ven_duplex_status') == 'acute_dvt'):
        ctx.red_flags.append(
            'ვენების დუპლექსით გამოვლინდა მწვავე ღრმა ვენების თრომბოზი (DVT) — გეგმურ ჩარევამდე სასწრაფოდ შეათანხმეთ ანტიკოაგულაცია და ტაქტიკა ქირურგთან!'
        )

    ctx.show_pregnancy_check = bool(patient.sex == 'F' and is_pregnancy_eligible(patient))
    eff_pregnant = bool(q.get('pregnant_possible') or q.get('ven_pregnant'))
    if eff_pregnant and not ctx.is_venous:
        if not is_pregnancy_eligible(patient):
            ctx.amber_flags.append(
                'კითხვარში მონიშნულია „ორსულობა“, რაც შეუსაბამოა პაციენტის სქესთან/ასაკთან — გადაამოწმეთ ჩანაწერი!'
            )
        else:
            is_elective_claud = pathway in (
                PreopPatient.Pathway.ABF, PreopPatient.Pathway.PAD_BYPASS, PreopPatient.Pathway.PAD_PTA,
            ) and not ctx.is_clti_any and not ctx.is_ali_acute
            if (patient.op_subtype in ('evar', 'tbd')) or ctx.is_pta:
                preg_art_msg = (
                    'ორსულობა / შესაძლო ორსულობა რენტგენო-ფლუოროსკოპიულ ჩარევაზე (EVAR / PTA) — ნაყოფის დასხივებისა და იოდის კონტრასტის რისკი! '
                    'სავალდებულოა β-hCG ტესტი, მეან-გინეკოლოგის ჩართვა და გეგმური ჩარევის გადადება!'
                )
            else:
                preg_art_msg = (
                    'ორსულობა / შესაძლო ორსულობა — სავალდებულოა β-hCG ვერიფიკაცია, მეან-გინეკოლოგის კონსულტაცია და გეგმური ოპერაციის გადადების განხილვა!'
                )
            ctx.red_flags.append(preg_art_msg)
            if is_elective_claud:
                ctx.hard_contraindications.append(preg_art_msg)

    if not ctx.is_venous and (bool(q.get('ven_lidocaine_allergy')) or 'lidocaine' in ctx.allergy_chips):
        if ctx.is_pta or patient.op_subtype in ('evar', 'tbd') or s.get('anesthesia_plan') in ('local', 'plexus'):
            ctx.red_flags.append(
                'ალერგია ლიდოკაინზე / ადგილობრივ ანესთეტიკზე — ადგილობრივი/წნულის ანესთეზია საშიშია! '
                'სავალდებულოა ანესთეზიოლოგის ჩართვა და ალტერნატიული გაუტკივარების შეთანხმება.'
            )
    # Distinguish hemodynamically unstable rupture suspect (SBP <90 or Shock Index >=1.0) from intact symptomatic AAA (ESVS 2024)
    eff_sbp_pre = _valid_bilateral_sbp(s)
    hr_pre = _sat_num(_s_raw(s, 'hr'), 15, 260)
    si_pre = round(hr_pre / eff_sbp_pre, 2) if (hr_pre is not None and eff_sbp_pre is not None and eff_sbp_pre > 0) else None
    hemodyn_unstable = bool((eff_sbp_pre is not None and eff_sbp_pre < 90) or (si_pre is not None and si_pre >= 1.0))
    aaa_pain_active = bool(pathway == PreopPatient.Pathway.AAA_INFRA and q.get('aaa_pain'))
    is_ali_urgent = (
        pathway in (PreopPatient.Pathway.ABF, PreopPatient.Pathway.PAD_BYPASS, PreopPatient.Pathway.PAD_PTA)
        and q.get('abf_duration') == '< 2 კვირა'
    )
    ctx.is_rupture_suspect = bool(aaa_pain_active and hemodyn_unstable)
    ctx.is_symp_aaa_intact = bool(aaa_pain_active and not hemodyn_unstable)
    ctx.is_acute_emergency = bool(bool(q.get('aaa_pain')) or bool(q.get('cea_crescendo')) or is_ali_urgent)
    ctx.is_septic_or_mycotic_urgent = bool(
        (pathway == PreopPatient.Pathway.AAA_INFRA and s.get('aaa_morphology') == 'inflammatory_mycotic')
        or bool(q.get('abf_wet_infection'))
    )
    ctx.is_urgent_case = bool(
        ctx.is_acute_emergency
        or ctx.is_septic_or_mycotic_urgent
        or is_ali_urgent
        or (aaa_eff is not None and aaa_eff >= rule_value('AAA_GIANT_MM'))
        or ctx.is_symp_cea
        or ctx.is_clti_any
        or (ctx.min_comp_abi_sat is not None and ctx.min_comp_abi_sat < hemo_cfg['abi_severe'])
        or (ctx.min_tp_sat is not None and ctx.min_tp_sat < hemo_cfg['tp_severe'])
        or (ctx.min_tbi_sat is not None and ctx.min_tbi_sat < hemo_cfg['tbi_severe'])
        or (ctx.is_venous and (bool(q.get('ven_acute_svt')) or bool(q.get('ven_bleeding'))))
        or (ctx.days > ctx.t_days and not ctx.is_venous and not ctx.is_on_hold and ctx.is_active_case)
    )
