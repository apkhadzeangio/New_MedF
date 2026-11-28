"""Chronic Venous Disease (EVLA / Phlebectomy) pathway rules and cross-pathway urgency flags."""
from ...models import PreopPatient
from ..calculators import _ihd_and_chf_flags, _inverted_bp_arms, _s_raw, _sat_num, _valid_bilateral_sbp
from ..registry import rule_value
from ..utils import _num, is_pregnancy_eligible


def _eval_venous_lido_and_contra(ctx, ceap, ven_dup, gsv_mm):
    patient, q, s, q_filled = ctx.patient, ctx.q, ctx.s, ctx.q_filled
    lido_cfg = rule_value('VEN_LIDOCAINE_MAX_DOSE')
    has_lido_allergy = bool(q.get('ven_lidocaine_allergy') or 'lidocaine' in ctx.allergy_chips)
    if patient.op_subtype == PreopPatient.OpSubtype.EVLA and ctx.weight_num is not None and not has_lido_allergy:
        ht_cm_lido = _num(s.get('height_cm'), lo=100, hi=230)
        if ctx.bmi is not None and ctx.bmi >= lido_cfg['abw_bmi'] and ht_cm_lido is not None:
            ibw_lido = (45.5 if patient.sex == 'F' else 50.0) + 0.91 * max(0.0, ht_cm_lido - 152.4)
            dosing_wt_lido = min(ctx.weight_num, ibw_lido + 0.4 * max(0.0, ctx.weight_num - ibw_lido))
        else:
            dosing_wt_lido = ctx.weight_num
        _, chf_lido = _ihd_and_chf_flags(q, s)
        age_lido = ctx.age if ctx.age is not None else getattr(ctx, 'age_sat', None)
        ren_lido = ctx.eff_renal if ctx.eff_renal is not None else getattr(ctx, 'eff_renal_sat', None)
        reduced_lido_cl = bool(
            chf_lido
            or (s.get('liver_status') in ('2x', '3x'))
            or ('amiodarone' in ctx.med_chips)
            or bool(ctx.ft_meds.get('has_ft_amiodarone'))
            or ((age_lido or 0) >= lido_cfg['elder_age'] and (ctx.on_dialysis or (ren_lido is not None and ren_lido < lido_cfg['elder_renal'])))
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
            'ალერგია ლიდოკაინზე / ადგილობრივ ანესთეტიკზე — ტუმესცენტური ან ნეირაქსიული ანესთეზია საშიშია! '
            'სავალდებულოა ქირურგისა და ანესთეზიოლოგის ჩართვა.'
        )
        ctx.red_flags.append(lido_msg)
        lido_plan = s.get('anesthesia_plan') or 'auto'
        if (
            (patient.op_subtype == PreopPatient.OpSubtype.EVLA and lido_plan in ('auto', 'local', 'plexus', 'neuraxial'))
            or (patient.op_subtype == PreopPatient.OpSubtype.PHLEB and lido_plan in ('local', 'plexus', 'neuraxial'))
            or ('epidural' in ctx.anesth_lines_set)
        ):
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
    side_s = patient.side if isinstance(patient.side, str) else ''
    if q_filled and side_s not in ('R', 'L', 'B'):
        ctx.amber_flags.append('მიუთითეთ საოპერაციო კიდური (მარჯვენა, მარცხენა ან ორმხრივი).')


def _eval_venous_vte_prophylaxis(ctx, ceap, age_eff, bmi_eff):
    q, s, q_filled = ctx.q, ctx.s, ctx.q_filled
    vte_cfg = rule_value('VEN_VTE_CAPRINI_PROPHYLAXIS')
    ren_red = rule_value('REN_CKD_NEPHRO_HYDRATION')['red']
    high_vte = (
        bool(q.get('ven_dvt_history')) or bool(q.get('ven_dvt_chips'))
        or bool(q.get('ven_thrombophilia'))
        or bool(q.get('ven_cancer_immob')) or bool(q.get('ven_acute_svt'))
        or ('hit_heparin' in ctx.allergy_chips)
    )
    mod_vte_count = sum([
        bool((age_eff or 0) >= vte_cfg['mod_age']),
        bool(bmi_eff is not None and bmi_eff >= vte_cfg['mod_bmi']),
        bool(q_filled and q.get('ven_hormones')),
        ctx.is_phleb,
        bool(q_filled and ceap in ('C5', 'C6')),
    ])
    cap_thresh = vte_cfg['caprini_high']
    ctx.needs_vte_prophylaxis = high_vte or (q_filled and (mod_vte_count >= 2 or ctx.caprini_score >= cap_thresh))
    if ctx.needs_vte_prophylaxis and not s.get('vte_prophylaxis_planned'):
        from ..caprini import _oac_venous_vte_flags, _par_vte_advice, _preg_vte_advice
        eff_ren_v = None if getattr(ctx, 'creat_unit_mismatch', False) else getattr(ctx, 'eff_renal_sat', ctx.eff_renal)
        ven_severe_renal = ctx.on_dialysis or (eff_ren_v is not None and eff_ren_v < ren_red)
        has_hit = 'hit_heparin' in ctx.allergy_chips
        arg_liv = ' (ღვიძლის ≥3x: არგატრობანი −75% / უპირატესია ბივალირუდინი!)' if ctx.liver_3x else ''
        evla_inr_lim = rule_value('MED_WARFARIN_HOLD_DAYS')['inr_evla_max']
        has_full_oac_v, is_evla_vka_v, has_contra_oac_v, has_ft_par_v, _ = _oac_venous_vte_flags(
            ctx.patient, q, s, ven_severe_renal, ctx.liver_3x,
            active_gi_bleed=getattr(ctx, 'active_gi_bleed', False),
            eff_preg=getattr(ctx, 'eff_pregnant', False),
        )
        if getattr(ctx, 'active_gi_bleed', False):
            hit_gi_v = ' (HIT: ჰეპარინი/LMWH აკრძალულია!)' if has_hit else ''
            vte_agents = f'მხოლოდ მექანიკური პროფილაქტიკა (IPC) — აქტიური კუჭ-ნაწლავის სისხლდენის / მწვავე წყლულის გამო ფარმაკოლოგიური ანტიკოაგულაცია დაბლოკილია ენდოსკოპიურ ჰემოსტაზამდე!{hit_gi_v}'
        elif 'mech_valve' in ctx.cad_chips and getattr(ctx, 'eff_pregnant', False):
            mv_rh = f' · CrCl<{ren_red}/დიალიზი: ფონდაპარინუქსი აკრძალულია!' if (has_hit and ven_severe_renal) else ''
            mv_lv = ' · ღვიძლის ≥3x: კოაგულოგრამის კონტროლით — უპირატესია ბივალირუდინი (არგატრობანი მოერიდეთ)!' if (ctx.liver_3x and has_hit) else (' · ღვიძლის ≥3x: კოაგულოგრამის კონტროლით!' if ctx.liver_3x else '')
            vte_agents = (
                f'IPC / არაჰეპარინული სქემა ჰემატოლოგის, კარდიოლოგისა და მეან-გინეკოლოგის კონსილიუმით (ორსულობა + მექ. სარქველი + HIT — UFH/LMWH/DOAC აკრძალულია!){mv_rh}{mv_lv}'
                if has_hit else (
                    f'მეან-გინეკოლოგის, კარდიოლოგისა და ნეფროლოგის კონსილიუმი (ორსულობა + მექ. სარქველი + CrCl<{ren_red}/დიალიზი: LMWH კუმულირდება, DOAC აკრძალულია — IV/SC UFH ან ტრიმესტრით VKA!){mv_lv}'
                    if ven_severe_renal else f'მეან-გინეკოლოგისა და კარდიოლოგის კონსილიუმი (ორსულობა + მექ. სარქველი: VKA vs LMWH/UFH ტრიმესტრით — DOAC აკრძალულია!){mv_lv}'
                )
            )
        elif has_full_oac_v and is_evla_vka_v:
            hit_oac_s = ' (HIT: ჰეპარინი/LMWH აკრძალულია!)' if has_hit else ''
            vte_agents = f'თერაპიული ვარფარინი გრძელდება მოუხსნელად (INR ≤ {evla_inr_lim}) — დამატებითი პროფილაქტიკური ანტიკოაგულაცია არ დაუმატოთ (ორმაგი ანტიკოაგულაციის გარეშე!){hit_oac_s}'
        elif 'mech_valve' in ctx.cad_chips:
            vte_agents = (
                f'ვარფარინი (VKA) + IV ბივალირუდინი/არგატრობანი (HIT + მექ. სარქველი — ჰეპარინი, ფონდაპარინუქსი და DOAC აკრძალულია!){arg_liv}'
                if has_hit else 'ვარფარინი (VKA) კარდიოლოგის სქემით — DOAC აკრძალულია!'
            )
        elif getattr(ctx, 'eff_pregnant', False):
            vte_agents = _preg_vte_advice(True, has_hit, ven_severe_renal, ctx.liver_3x, has_ft_par_v, ren_red, '')
        elif has_full_oac_v:
            hit_oac_s = ' (HIT: ჰეპარინი/LMWH აკრძალულია!)' if has_hit else ''
            if has_contra_oac_v:
                vte_agents = f'უკუნაჩვენები OAC არ განაახლოთ — შეარჩიეთ უსაფრთხო ანტიკოაგულაციის სქემა (ორმაგი ანტიკოაგულაციის გარეშე!){hit_oac_s}'
            elif is_evla_vka_v:
                vte_agents = f'თერაპიული ვარფარინი გრძელდება მოუხსნელად (INR ≤ {evla_inr_lim}) — დამატებითი პროფილაქტიკური ანტიკოაგულაცია არ დაუმატოთ (ორმაგი ანტიკოაგულაციის გარეშე!){hit_oac_s}'
            else:
                vte_agents = f'მუდმივი თერაპიული OAC-ის აღდგენა ჰემოსტაზით (+ IPC აღდგენამდე) — დამატებითი პროფილაქტიკური ანტიკოაგულაცია არ დაუმატოთ (ორმაგი ანტიკოაგულაციის გარეშე!){hit_oac_s}'
        elif has_ft_par_v:
            hit_dtis_v = 'IV ბივალირუდინი (არგატრობანი ღვიძლის ≥3x-ზე ერიდეთ/დოზა -75%!)' if ctx.liver_3x else 'IV ბივალირუდინი/არგატრობანი'
            vte_agents = _par_vte_advice(has_hit, ven_severe_renal, ctx.liver_3x, hit_dtis_v, '')
        elif has_hit:
            vte_agents = (
                f'IPC / არგატრობანი-ბივალირუდინი — HIT + CrCl<{ren_red}: ჰეპარინი, ფონდაპარინუქსი და DOAC აკრძალულია!{arg_liv}'
                if ven_severe_renal else
                ('IPC / IV ბივალირუდინი — HIT + ღვიძლის ≥3x: ჰეპარინი, DOAC და ფონდაპარინუქსი არარეკომენდებულია!' if ctx.liver_3x else 'ფონდაპარინუქსი / DOAC — HIT: ჰეპარინი აკრძალულია!')
            )
        elif ven_severe_renal and ctx.liver_3x:
            vte_agents = f'კანქვეშა UFH + IPC კოაგულოგრამის კონტროლით (CrCl/eGFR <{ren_red} + ღვიძლის ≥3x: DOAC/ფონდაპარინუქსი აკრძალულია, LMWH კუმულირდება!)'
        elif ven_severe_renal:
            vte_agents = 'კანქვეშა UFH / დოზა-კორექტირებული LMWH (DOAC არა!)'
        elif ctx.liver_3x:
            vte_agents = 'LMWH / UFH კოაგულოგრამის კონტროლით (ღვიძლის ≥3x-ზე DOAC არა!)'
        else:
            vte_agents = 'LMWH / DOAC'
        vte_tier_lbl = (
            f'მაღალი თრომბოემბოლიური რისკი (Caprini {ctx.caprini_score})'
            if (high_vte or ctx.caprini_score >= cap_thresh)
            else f'მომატებული/საშუალო თრომბოემბოლიური რისკი (Caprini {ctx.caprini_score} — ≥2 რისკ-ფაქტორი)'
        )
        ctx.amber_flags.append(
            f'{vte_tier_lbl} — შეათანხმეთ ინდივიდუალური თრომბოპროფილაქტიკა ({vte_agents}) ქირურგთან.'
        )


def _eval_venous_arterial_and_vte(ctx, ceap):
    q, s, q_filled = ctx.q, ctx.s, ctx.q_filled
    art_cfg = rule_value('VEN_ARTERIAL_ISCHEMIA_COMPRESSION')
    gly_cfg = rule_value('GLY_GLUCOSE_HBA1C')
    vte_cfg = rule_value('VEN_VTE_CAPRINI_PROPHYLAXIS')
    gl_eff = ctx.gl_mmol if ctx.gl_mmol is not None else getattr(ctx, 'gl_mmol_sat', None)
    hba1c_v = _sat_num(_s_raw(s, 'hba1c'), 3.0, 25.0)
    age_eff = ctx.age if ctx.age is not None else getattr(ctx, 'age_sat', None)
    bmi_eff = ctx.bmi if ctx.bmi is not None else getattr(ctx, 'bmi_sat', None)
    eff_dm = bool(ctx.has_known_dm or (gl_eff is not None and gl_eff >= gly_cfg['gl_endo']) or (hba1c_v is not None and hba1c_v >= gly_cfg['hba1c_dm']))
    ctx.needs_abi_check = q_filled and (
        bool(q.get('claudication'))
        or ceap == 'C6'
        or (ceap == 'C5' and (eff_dm or (age_eff or 0) >= vte_cfg['c5_abi_age']))
    )
    abi_eff = min([v for v in (ctx.min_abi, getattr(ctx, 'min_comp_abi_sat', None)) if v is not None], default=None)
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
    tp_eff = min([v for v in (ctx.min_tp, getattr(ctx, 'min_tp_sat', None)) if v is not None], default=None)
    tbi_eff = min([v for v in (ctx.min_tbi, getattr(ctx, 'min_tbi_sat', None)) if v is not None], default=None)
    tp_is_stop = bool((tp_eff is not None and tp_eff < art_cfg['tp_stop']) or (tbi_eff is not None and tbi_eff < art_cfg['tbi_stop']))
    max_abi_disp = ctx.max_abi if ctx.max_abi is not None else getattr(ctx, 'max_abi_sat', None)
    if tp_is_stop:
        tp_tbi_lbl = (
            f'TP {tp_eff} mmHg < {art_cfg["tp_stop"]} mmHg'
            if (tp_eff is not None and tp_eff < art_cfg['tp_stop'])
            else f'TBI {tbi_eff} < {art_cfg["tbi_stop"]:.2f}'
        )
        tp_ven_isch = (
            f'კრიტიკულად დაბალი თითის პერფუზია ({tp_tbi_lbl}) — მძიმე არტერიული იშემია, '
            'გეგმური ვენური ჩარევა და კომპრესია უკუნაჩვენებია (ESVS 2022)!'
        )
        ctx.red_flags.append(tp_ven_isch)
        ctx.hard_contraindications.append(tp_ven_isch)
    elif tp_eff is not None and tp_eff < art_cfg['tp_red'] and not ctx.abi_non_compressible:
        ctx.red_flags.append(
            f'დაბალი თითის წნევა (TP {tp_eff} mmHg < {art_cfg["tp_red"]} mmHg) — არტერიული უკმარისობა, კომპრესიული ტრიკოტაჟი უკუნაჩვენებია!'
        )
    elif tbi_eff is not None and tbi_eff < art_cfg['tbi_red'] and not ctx.abi_non_compressible:
        ctx.red_flags.append(
            f'დაბალი თით-მხრის ინდექსი (TBI {tbi_eff} < {art_cfg["tbi_red"]:.2f}) — არტერიული უკმარისობა, კომპრესიული ტრიკოტაჟი უკუნაჩვენებია!'
        )
    if ctx.abi_non_compressible and not tp_is_stop:
        if (tp_eff is not None and tp_eff < art_cfg['tp_red']) or (tbi_eff is not None and tbi_eff < art_cfg['tbi_red']):
            ctx.red_flags.append(
                f'არაკომპრესირებადი ABI ({max_abi_disp}) + დაბალი Toe Pressure/TBI (TP {tp_eff if tp_eff is not None else "—"} / '
                f'TBI {tbi_eff if tbi_eff is not None else "—"}) — არტერიული უკმარისობა, კომპრესიული ტრიკოტაჟი უკუნაჩვენებია!'
            )
        elif ctx.tbi_reduced_comp:
            ctx.amber_flags.append(
                f'არაკომპრესირებადი არტერია (ABI {max_abi_disp} > {art_cfg["abi_noncomp"]:.2f}) + სასაზღვრო TP/TBI '
                f'(TP {ctx.min_tp if ctx.min_tp is not None else "—"} / TBI {ctx.min_tbi if ctx.min_tbi is not None else "—"}) — '
                '2 კლასის ნაცვლად დასაშვებია მხოლოდ მსუბუქი I კლასის კომპრესია (<20 mmHg) ქირურგთან შეთანხმებით!'
            )
        elif not ctx.tbi_comp_safe:
            ctx.amber_flags.append(
                f'არაკომპრესირებადი არტერია (ABI {max_abi_disp} > {art_cfg["abi_noncomp"]:.2f}) — 2 კლასის კომპრესიული წინდის დანიშვნამდე '
                f'სამიზნე კიდურზე სავალდებულოა Toe Pressure (≥{art_cfg["tp_comp_ok"]} mmHg) ან TBI (≥{art_cfg["tbi_comp_ok"]:.2f})!'
            )
    elif not tp_is_stop and not ctx.compression_contra and ctx.tbi_reduced_comp:
        ctx.amber_flags.append(
            f'სასაზღვრო თითის პერფუზია (TP {ctx.min_tp if ctx.min_tp is not None else "—"} / TBI {ctx.min_tbi if ctx.min_tbi is not None else "—"}) — '
            '2 კლასის ნაცვლად დასაშვებია მხოლოდ მსუბუქი I კლასის კომპრესია (<20 mmHg) ქირურგთან შეთანხმებით!'
        )
    _eval_venous_vte_prophylaxis(ctx, ceap, age_eff, bmi_eff)


def eval_venous(ctx):
    patient, q, s, q_filled = ctx.patient, ctx.q, ctx.s, ctx.q_filled
    gly_cfg = rule_value('GLY_GLUCOSE_HBA1C')
    cb = rule_value('CARDIO_BIOMARKERS_ECHO')
    vte_cfg = rule_value('VEN_VTE_CAPRINI_PROPHYLAXIS')
    ceap = q.get('ven_ceap') if q.get('ven_ceap') in ('C2', 'C3', 'C4', 'C5', 'C6') else 'C2'
    ven_dup = s.get('ven_duplex_status') or 'pending'
    gsv_mm = _num(s.get('ven_gsv_mm'), lo=1, hi=50)
    _eval_venous_lido_and_contra(ctx, ceap, ven_dup, gsv_mm)
    _eval_venous_arterial_and_vte(ctx, ceap)

    lvef_v = _sat_num(_s_raw(s, 'lvef'), 5, 95)
    bnp_v = _sat_num(_s_raw(s, 'nt_probnp'), 1, 100000)
    gl_eff = ctx.gl_mmol if ctx.gl_mmol is not None else getattr(ctx, 'gl_mmol_sat', None)
    hba1c_v = _sat_num(_s_raw(s, 'hba1c'), 3.0, 25.0)
    age_eff = ctx.age if ctx.age is not None else getattr(ctx, 'age_sat', None)
    eff_dm = bool(ctx.has_known_dm or (gl_eff is not None and gl_eff >= gly_cfg['gl_endo']) or (hba1c_v is not None and hba1c_v >= gly_cfg['hba1c_dm']))
    ctn_v = _sat_num(_s_raw(s, 'hs_ctn'), 0, 50000)
    eff_cardiac = bool(
        q.get('cad') or ctx.cad_chips or q.get('recent_stent')
        or (lvef_v is not None and lvef_v < cb['lvef_red'])
        or (bnp_v is not None and bnp_v >= cb['bnp_red'])
        or (ctn_v is not None and ctn_v >= cb['ctn_amb'])
        or bool(s.get('echo_valve_issue'))
        or (s.get('ecg') in ('afib_controlled', 'ischemia_arrhythmia'))
        or bool(
            q.get('med_warfarin') or q.get('med_doac') or q.get('med_dabigatran')
            or ('rivaroxaban_2_5' in ctx.med_chips) or getattr(ctx, 'has_ft_full_oac', False)
        )
        or ('amiodarone' in ctx.med_chips) or bool(ctx.ft_meds.get('has_ft_amiodarone'))
    )
    ctx.needs_wound_care = q_filled and (ceap == 'C6' or bool(q.get('ven_skin_infection')))
    ctx.needs_ecg_venous = (
        ctx.is_phleb
        or ((age_eff or 0) >= vte_cfg['ecg_age'])
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


def _eval_cross_pathway_urgency_flags(ctx, aaa_eff):
    pathway, q, s = ctx.pathway, ctx.q, ctx.s
    hemo_cfg = rule_value('PAD_CLTI_HEMO_THRESHOLDS')
    hypo_cfg = rule_value('VIT_BP_HYPOTENSION_SHOCK')
    # Distinguish hemodynamically unstable rupture suspect (SBP <90 or Shock Index >=1.0) from intact symptomatic AAA (ESVS 2024)
    eff_sbp_pre = _valid_bilateral_sbp(s)
    if eff_sbp_pre is None:
        inv_pre = _inverted_bp_arms(s)
        eff_sbp_pre = min((min(sv, dv) for sv, dv in inv_pre), default=None)
    hr_pre = _sat_num(_s_raw(s, 'hr'), 15, 260)
    si_pre = round(hr_pre / eff_sbp_pre, 2) if (hr_pre is not None and eff_sbp_pre is not None and eff_sbp_pre > 0) else None
    hemodyn_unstable = bool((eff_sbp_pre is not None and eff_sbp_pre < hypo_cfg['red_sbp']) or (si_pre is not None and si_pre >= hypo_cfg['shock_idx']))
    aaa_pain_active = bool(pathway == PreopPatient.Pathway.AAA_INFRA and q.get('aaa_pain'))
    cea_cresc_active = bool(pathway == PreopPatient.Pathway.CEA and q.get('cea_crescendo'))
    is_pad_pw = pathway in (PreopPatient.Pathway.ABF, PreopPatient.Pathway.PAD_BYPASS, PreopPatient.Pathway.PAD_PTA)
    is_ali_urgent = is_pad_pw and q.get('abf_duration') == '< 2 კვირა'
    ctx.is_rupture_suspect = bool(aaa_pain_active and hemodyn_unstable)
    ctx.is_symp_aaa_intact = bool(aaa_pain_active and not hemodyn_unstable)
    ctx.is_acute_emergency = bool(aaa_pain_active or cea_cresc_active or is_ali_urgent)
    ctx.is_septic_or_mycotic_urgent = bool(
        (pathway == PreopPatient.Pathway.AAA_INFRA and s.get('aaa_morphology') == 'inflammatory_mycotic')
        or (is_pad_pw and bool(q.get('abf_wet_infection')))
    )
    ctx.is_urgent_case = bool(
        ctx.is_acute_emergency
        or ctx.is_septic_or_mycotic_urgent
        or is_ali_urgent
        or (pathway == PreopPatient.Pathway.AAA_INFRA and aaa_eff is not None and aaa_eff >= rule_value('AAA_GIANT_MM'))
        or ctx.is_symp_cea
        or ctx.is_clti_any
        or (ctx.min_comp_abi_sat is not None and ctx.min_comp_abi_sat < hemo_cfg['abi_severe'])
        or (ctx.min_tp_sat is not None and ctx.min_tp_sat < hemo_cfg['tp_severe'])
        or (ctx.min_tbi_sat is not None and ctx.min_tbi_sat < hemo_cfg['tbi_severe'])
        or (ctx.is_venous and (bool(q.get('ven_acute_svt')) or bool(q.get('ven_bleeding'))))
        or (ctx.days > ctx.t_days and not ctx.is_venous and not ctx.is_on_hold and ctx.is_active_case)
    )


def eval_cross_pathway_and_urgency(ctx):
    patient, pathway, q, s = ctx.patient, ctx.pathway, ctx.q, ctx.s
    gly_cfg = rule_value('GLY_GLUCOSE_HBA1C')
    hba1c_x = _sat_num(_s_raw(s, 'hba1c'), 3.0, 25.0)
    gl_eff = ctx.gl_mmol if ctx.gl_mmol is not None else getattr(ctx, 'gl_mmol_sat', None)
    eff_dm = bool(ctx.has_known_dm or (gl_eff is not None and gl_eff >= gly_cfg['gl_endo']) or (hba1c_x is not None and hba1c_x >= gly_cfg['hba1c_dm']))

    if (
        pathway in (PreopPatient.Pathway.ABF, PreopPatient.Pathway.PAD_BYPASS, PreopPatient.Pathway.PAD_PTA)
        and ctx.is_clti_any and (eff_dm or ctx.on_dialysis)
        and not ctx.abi_non_compressible and not ctx.is_ali_acute
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
    is_urgent_preg = bool(
        not ctx.is_venous and (
            ctx.is_ali_acute
            or ctx.is_clti_any
            or ctx.is_symp_cea
            or (pathway == PreopPatient.Pathway.CEA and bool(q.get('cea_crescendo')))
            or (pathway == PreopPatient.Pathway.AAA_INFRA and (
                bool(q.get('aaa_pain'))
                or s.get('aaa_morphology') == 'inflammatory_mycotic'
                or (ctx.aaa_mm_sat is not None and ctx.aaa_mm_sat >= rule_value('AAA_GIANT_MM'))
            ))
        )
    )
    ctx.is_urgent_preg = is_urgent_preg
    eff_pregnant = bool(q.get('pregnant_possible') or q.get('ven_pregnant'))
    if eff_pregnant and not ctx.is_venous:
        if not is_pregnancy_eligible(patient):
            ctx.amber_flags.append(
                'კითხვარში მონიშნულია „ორსულობა“, რაც შეუსაბამოა პაციენტის სქესთან/ასაკთან — გადაამოწმეთ ჩანაწერი!'
            )
        else:
            if ctx.is_ali_acute:
                preg_art_msg = (
                    'ორსულობა / შესაძლო ორსულობა მწვავე იშემიის (ALI) ფონზე — '
                    'სასწრაფო მულტიდისციპლინური შეთანხმება მეან-გინეკოლოგთან და სისხლძარღვთა ქირურგთან (ნაყოფის დაცვით)!'
                )
            elif is_urgent_preg:
                rad_note = ' (ნაყოფის დასხივებისა და კონტრასტის მინიმიზაცია — რადიაციული დაცვა!)' if ((patient.op_subtype in ('evar', 'tbd')) or ctx.is_pta) else ' (ნაყოფის დაცვით!)'
                preg_art_msg = (
                    f'ორსულობა / შესაძლო ორსულობა გადაუდებელი სისხლძარღვოვანი ჩვენების ფონზე — '
                    f'სავალდებულოა სასწრაფო მულტიდისციპლინური კონსილიუმი მეან-გინეკოლოგთან, ქირურგთან და ანესთეზიოლოგთან{rad_note}'
                )
            elif (patient.op_subtype in ('evar', 'tbd')) or ctx.is_pta:
                preg_art_msg = (
                    'ორსულობა / შესაძლო ორსულობა რენტგენო-ფლუოროსკოპიულ ჩარევაზე (EVAR / PTA) — ნაყოფის დასხივებისა და იოდის კონტრასტის რისკი! '
                    'სავალდებულოა β-hCG ტესტი, მეან-გინეკოლოგის ჩართვა და გეგმური ჩარევის გადადება!'
                )
            else:
                preg_art_msg = (
                    'ორსულობა / შესაძლო ორსულობა — სავალდებულოა β-hCG ვერიფიკაცია, მეან-გინეკოლოგის კონსულტაცია და გეგმური ოპერაციის გადადების განხილვა!'
                )
            ctx.red_flags.append(preg_art_msg)
            if not is_urgent_preg:
                ctx.hard_contraindications.append(preg_art_msg)

    if not ctx.is_venous and (bool(q.get('ven_lidocaine_allergy')) or 'lidocaine' in ctx.allergy_chips):
        anesth_p = s.get('anesthesia_plan') or 'auto'
        la_active_plan = (anesth_p in ('local', 'plexus')) or (ctx.is_pta and anesth_p == 'auto')
        if la_active_plan or (patient.op_subtype in ('evar', 'tbd') and anesth_p == 'auto') or anesth_p == 'neuraxial' or ('epidural' in ctx.anesth_lines_set):
            la_msg = (
                'ალერგია ლიდოკაინზე / ადგილობრივ ანესთეტიკზე — ადგილობრივი/წნულის/ნეირაქსიული ანესთეზია საშიშია (ანაფილაქსიის რისკი)! '
                'სავალდებულოა ანესთეზიოლოგის ჩართვა და ზოგად ანესთეზიაზე გადაყვანა.'
            )
            ctx.red_flags.append(la_msg)
            if la_active_plan:
                ctx.hard_contraindications.append(la_msg)
    _eval_cross_pathway_urgency_flags(ctx, aaa_eff)
