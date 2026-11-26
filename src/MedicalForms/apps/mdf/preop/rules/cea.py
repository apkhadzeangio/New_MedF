"""Carotid Endarterectomy (CEA) pathway clinical rules (ESVS 2023 / SVS 2022)."""
from ...models import PreopPatient
from ..registry import rule_value
from ..utils import _int, cea_live_days_since_event, is_symp_cea_patient


def _eval_cea_symptoms_and_clocks(ctx):
    patient, q, s, q_filled = ctx.patient, ctx.q, ctx.s, ctx.q_filled
    win_days, warn_days, symp_max_days = rule_value('CEA_SYMP_WINDOW_DAYS')
    tl_days = rule_value('CEA_ACUTE_NEURO_THROMBOLYSIS')
    days_since_ev = cea_live_days_since_event(q, today=ctx.today, s=s)
    is_symp_cea = is_symp_cea_patient(patient, q=q, s=s, today=ctx.today)
    ctx.days_since_ev = days_since_ev
    ctx.is_symp_cea = is_symp_cea
    ctx.active_thrombolysis_window = bool(
        q_filled and q.get('cea_thrombolysis')
        and (days_since_ev is None or days_since_ev <= tl_days)
    )

    if days_since_ev is not None and days_since_ev > symp_max_days and (patient.op_subtype == PreopPatient.OpSubtype.CEA_SYMP or q.get('cea_symp_event')):
        ctx.amber_flags.append(
            f'ნევროლოგიური ეპიზოდიდან გასულია {days_since_ev} დღე (>6 თვე) — ESVS 2023 გაიდლაინით განიხილება როგორც ასიმპტომური სტენოზი.'
        )
    elif patient.op_subtype == PreopPatient.OpSubtype.CEA_ASYMP and q_filled and q.get('cea_symp_event'):
        ctx.amber_flags.append(
            'კითხვარით დაფიქსირდა ნევროლოგიური სიმპტომი ბოლო 6 თვეში — გადაამოწმეთ სიმპტომური CEA-ს სტატუსი (14-დღიანი ფანჯარა)!'
        )
    if q_filled and q.get('cea_crescendo'):
        ctx.red_flags.append(
            'Crescendo TIA / მერყევი ნევროლოგიური დეფიციტი ბოლო 24–48 სთ-ში — სასწრაფოდ აცნობეთ ქირურგსა და ნევროლოგს!'
        )
    if ctx.active_thrombolysis_window:
        if (s.get('brain_imaging') == 'normal_small') and days_since_ev is not None and days_since_ev >= 1:
            ctx.red_flags.append(
                f'ინტრავენური თრომბოლიზისი ბოლო {tl_days} დღეში (ESVS 2023) — >24 სთ-ის შემდეგ საკონტროლო CT-ით ჰემორაგიის გამორიცხვისას '
                f'დასაშვებია ასპირინი 75–100 მგ ქირურგ/ნევროლოგთან შეთანხმებით, ხოლო ნაადრევი სისტემური ჰეპარინიზაცია/CEA ≤{tl_days} დღეში საშიშია!'
            )
        else:
            ctx.red_flags.append(
                f'ინტრავენური თრომბოლიზისი ბოლო {tl_days} დღეში (ESVS 2023) — ნაადრევი ჰეპარინიზაციის/სისხლჩაქცევის რისკი, შეათანხმეთ ოპერაციის თარიღი ქირურგთან!'
            )
    if q_filled and q.get('cea_disabling_stroke'):
        ctx.red_flags.append(
            'მძიმე ნარჩენი ნევროლოგიური დეფიციტი (mRS >= 3) — გადაამოწმეთ CEA-ს ჩვენება და ვადები ქირურგთან და ნევროლოგთან!'
        )

    if is_symp_cea and days_since_ev is not None and not patient.approved_at and not ctx.is_on_hold and ctx.is_active_case:
        if days_since_ev > win_days:
            msg = f'ინდექსური სიმპტომიდან გავიდა {days_since_ev} დღე — გადაცილებულია ESVS 2023-ის {win_days}-დღიანი ოპტიმალური ფანჯარა! დააჩქარეთ ქირურგის შეფასება.'
            ctx.red_flags.append(msg)
            ctx.priority_flags.append(msg)
        elif days_since_ev >= warn_days:
            ctx.amber_flags.append(
                f'სიმპტომიდან გასულია {days_since_ev} დღე (ESVS {win_days}-დღიანი ფანჯრიდან დარჩა {win_days - days_since_ev} დღე) — დააჩქარეთ კვლევები!'
            )

    if ctx.days > ctx.t_days and not patient.approved_at and not ctx.is_on_hold and ctx.is_active_case:
        if is_symp_cea:
            msg = f'გახანგრძლივებული მომზადება სიმპტომურ CEA-ზე: გავიდა {ctx.days} დღე (სამიზნე ლიმიტი: {ctx.t_days} დღე) — განმეორებითი ინსულტის რისკი!'
        else:
            msg = f'გახანგრძლივებული წინასაოპერაციო მომზადება: გავიდა {ctx.days} დღე (სამიზნე ლიმიტი: {ctx.t_days} დღე).'
        ctx.red_flags.append(msg)
        ctx.priority_flags.append(msg)
    elif not patient.approved_at and not ctx.is_on_hold and ctx.is_active_case and ctx.days >= max(4, ctx.t_days - 1):
        ctx.amber_flags.append(f'მომზადების მე-{ctx.days} დღე ({ctx.t_days} დღიდან) — დააჩქარეთ დარჩენილი კვლევები.')


def _eval_cea_stenosis_and_imaging(ctx):
    patient, q_filled = ctx.patient, ctx.q_filled
    is_symp_cea, car_r, car_l = ctx.is_symp_cea, ctx.car_r, ctx.car_l
    st_cfg = rule_value('CEA_STENOSIS_THRESHOLDS')
    occ_pct = rule_value('CEA_OCCLUSION_100_STOP')

    if patient.side == rule_value('CEA_BILATERAL_STOP'):
        bilat_cea_msg = (
            'ერთდროული ორმხრივი კაროტიდული ენდარტერექტომია (CEA, მხარე B) აბსოლუტურად უკუნაჩვენებია — '
            'ორმხრივი კრანიალური ნერვების დამბლისა და ასფიქსიის ლეტალური რისკი (ESVS 2023)! დაგეგმეთ ეტაპობრივად (R ან L).'
        )
        ctx.red_flags.append(bilat_cea_msg)
        ctx.hard_contraindications.append(bilat_cea_msg)
    elif q_filled and patient.side not in ('R', 'L'):
        ctx.amber_flags.append('მიუთითეთ დაგეგმილი CEA-ს მხარე (მარჯვენა ან მარცხენა) პაციენტის ბარათში.')

    side_known = patient.side in ('R', 'L')
    ipsi_car = car_r if patient.side == 'R' else (car_l if patient.side == 'L' else ctx.max_car)
    contra_car = (
        car_l if patient.side == 'R'
        else (car_r if patient.side == 'L' else (min(car_r, car_l) if (car_r is not None and car_l is not None) else None))
    )
    ctx.ipsi_car, ctx.contra_car = ipsi_car, contra_car

    if ipsi_car is not None:
        side_prefix = f'{patient.side} ' if side_known else ''
        ctx.key_badge = f'{side_prefix}{ipsi_car}%'
        ctx.key_badge_urgent = bool(is_symp_cea or ipsi_car >= 90)
        if side_known and ipsi_car >= occ_pct:
            occ_msg = (
                'საოპერაციო მხარეს ICA-ს სრული ოკლუზია (100%) — CEA სრულ ქრონიკულ ოკლუზიაზე არ ტარდება! '
                'სასწრაფოდ გადაამოწმეთ CTA ქირურგთან (near-occlusion vs ოკლუზია).'
            )
            ctx.red_flags.append(occ_msg)
            ctx.hard_contraindications.append(occ_msg)
        elif not side_known and ipsi_car >= occ_pct:
            ctx.red_flags.append(
                'ერთ-ერთ საძილე არტერიაზე ფიქსირდება 100% ოკლუზია — დაუყოვნებლივ მიუთითეთ დაგეგმილი CEA-ს მხარე (R ან L), '
                'რომ გამოირიცხოს ოკლუზირებულ მხარეზე ჩარევა!'
            )
        elif side_known and ipsi_car < st_cfg['symp_min'] and contra_car is not None and contra_car >= 70:
            ctx.red_flags.append(
                f'საოპერაციო მხარეს სტენოზი ({ipsi_car}%) < {st_cfg["symp_min"]}%, ხოლო კონტრალატერალურ მხარეს {contra_car}% — '
                'შესაძლოა არასწორი მხარეა არჩეული (R/L)! სასწრაფოდ გადაამოწმეთ ქირურგთან.'
            )
        elif is_symp_cea and ipsi_car < st_cfg['symp_min']:
            ctx.red_flags.append(f'საოპერაციო მხარეს სტენოზი ({ipsi_car}%) < {st_cfg["symp_min"]}% (NASCET) — გადაამოწმეთ CEA-ს ჩვენება ქირურგთან.')
        elif not is_symp_cea and ipsi_car < st_cfg['asymp_min']:
            ctx.red_flags.append(f'ასიმპტომურ საოპერაციო მხარეს სტენოზი ({ipsi_car}%) < {st_cfg["asymp_min"]}% (NASCET) — გადაამოწმეთ CEA-ს ჩვენება ქირურგთან.')
        elif not is_symp_cea and st_cfg['asymp_min'] <= ipsi_car < occ_pct:
            ctx.amber_flags.append(
                f'ასიმპტომური სტენოზი {ipsi_car}% (60–99% NASCET — ESVS 2023 Rec 19): '
                'გადაამოწმეთ მაღალი რისკის ბალთის ნიშნები CTA/დუპლექსზე და >5-წლიანი მოსალოდნელი სიცოცხლის ხანგრძლივობა.'
            )
    elif is_symp_cea and ctx.days_since_ev is not None:
        ctx.key_badge = f'{ctx.days_since_ev}/14დღ'
        ctx.key_badge_urgent = True

    if contra_car is not None:
        if contra_car >= st_cfg['contra_crit']:
            ctx.red_flags.append(
                f'კონტრალატერალური საძილე არტერიის კრიტიკული სტენოზი / ოკლუზია ({contra_car}%) — '
                'ინტრაოპერაციული შუნტის (Shunt) მზადყოფნა და ჰიპერპერფუზიის მაღალი რისკი!'
            )
        elif contra_car >= st_cfg['contra_mod']:
            ctx.amber_flags.append(f'კონტრალატერალური საძილე არტერიის სტენოზი ({contra_car}%) — საყურადღებოა ცერებრული პერფუზიისთვის.')


def eval_cea(ctx):
    q, s, q_filled = ctx.q, ctx.s, ctx.q_filled
    _eval_cea_symptoms_and_clocks(ctx)
    _eval_cea_stenosis_and_imaging(ctx)

    cea_cta = s.get('cea_cta_status') or 'pending'
    if cea_cta == 'high_calcified':
        ctx.amber_flags.append('კისრის/რკალის CTA-ზე მაღალი ბიფურკაცია (C2) / მძიმე კალციფიკაცია / კინკინგი — აცნობეთ ქირურგს.')
    if q_filled and q.get('cea_neck_limited'):
        ctx.amber_flags.append('კისრის გაშლა მკვეთრად შეზღუდულია — საყურადღებოა საოპერაციო პოზიციონირებისა და ინტუბაციისთვის.')

    ctx.needs_brain_ct = ctx.is_symp_cea or (q_filled and bool(q.get('neuro')))
    brain_im = s.get('brain_imaging') or 'pending'
    if brain_im == rule_value('CEA_BRAIN_HEMORRHAGE_STOP'):
        hem_msg = (
            'თავის ტვინის CT/MRI-ზე სისხლჩაქცევა ან ფართო მწვავე ინფარქტი (>1/3 MCA) — '
            'ნაადრევი CEA საშიშია, სასწრაფოდ აცნობეთ ქირურგსა და ნევროლოგს!'
        )
        ctx.red_flags.append(hem_msg)
        ctx.hard_contraindications.append(hem_msg)
    if q_filled and not s.get('cta_disc'):
        ctx.amber_flags.append('კისრის/თავის ტვინის CTA/MRI DICOM დისკი ჯერ არ არის მოტანილი / შემოწმებული.')

    ctx.needs_neuro = ctx.is_symp_cea or (q_filled and bool(q.get('neuro')))
    ctx.needs_laryngoscopy = q_filled and (bool(q.get('cea_prior_neck')) or bool(q.get('cea_hoarseness')))
    laryng_res = s.get('laryngoscopy_res') or 'pending'
    if laryng_res == 'palsy':
        ctx.red_flags.append(
            'ლარინგოსკოპიით გამოვლინდა სახმო იოგის პარეზი — ორმხრივი ნერვის დაზიანებისა და ასფიქსიის საფრთხე! '
            'სასწრაფოდ შეათანხმეთ ქირურგთან (CAS vs CEA).'
        )

    cea_neck_set = set(q.get('cea_neck_chips') or [])
    lvef_prelim = _int(s.get('lvef'), lo=5, hi=95)
    ctx.cea_high_surg_risk = bool(
        ('neck_rad' in cea_neck_set) or ('ipsi_restenosis' in cea_neck_set)
        or laryng_res == 'palsy' or cea_cta == 'high_calcified'
        or (lvef_prelim is not None and lvef_prelim < rule_value('CARDIO_BIOMARKERS_ECHO')['lvef_red'])
    )
    if ctx.cea_high_surg_risk:
        ctx.amber_flags.append(
            'ESVS 2023 მაღალი ქირურგიული რისკი ღია CEA-სთვის (დასხივება / რესტენოზი / C2 მაღალი ბიფურკაცია / სახმო იოგი / LVEF <40%) — '
            'ქირურგთან განიხილეთ კაროტიდული სტენტირება (CAS / TCAR).'
        )

    tl_blocks_ap = ctx.active_thrombolysis_window and not (
        brain_im == 'normal_small' and ctx.days_since_ev is not None and ctx.days_since_ev >= 1
    )
    antiplatelet_prohibited_cea = (brain_im == rule_value('CEA_BRAIN_HEMORRHAGE_STOP')) or tl_blocks_ap
    if q_filled and not ctx.has_antiplatelet and not antiplatelet_prohibited_cea:
        ctx.needs_cea_antiplatelet = True
        has_oac_only = bool(q.get('med_doac') or q.get('med_dabigatran') or q.get('med_warfarin'))
        ap_trigger = (
            'პაციენტი იღებს მხოლოდ ანტიკოაგულანტს (რომელიც ოპერაციამდე ჩერდება) ანტიაგრეგანტის გარეშე!'
            if has_oac_only else 'CEA-ს წინ პაციენტი არ იღებს ანტიაგრეგანტს (ESVS 2023 Class I)'
        )
        ap_opt = (
            'ასპირინი 75–100 მგ (კლოპიდოგრელზე აუტანლობა!)'
            if 'clopidogrel_intol' in ctx.allergy_chips
            else 'ასპირინი (± კლოპიდოგრელი სიმპტომურ CEA-ზე)'
        )
        ctx.reflex_orders.append({
            'key': 'cea_antiplatelet',
            'trigger': ap_trigger,
            'action': f'თავის ტვინის CT-ით სისხლჩაქცევის გამორიცხვის შემდეგ დაუყოვნებლივ დაიწყოს {ap_opt} ქირურგთან შეთანხმებით',
            'done': bool(s.get('cea_antiplatelet_started')),
        })
        if not s.get('cea_antiplatelet_started'):
            ap_name = 'ასპირინი (კლოპიდოგრელის აუტანლობა!)' if 'clopidogrel_intol' in ctx.allergy_chips else 'ასპირინი / კლოპიდოგრელი'
            ctx.red_flags.append(
                f'პაციენტი არ იღებს ანტიაგრეგანტს ({ap_name})! ESVS 2023 Class I-ით CEA-ს წინ '
                'ანტიაგრეგანტული თერაპია სავალდებულოა ინსულტის პრევენციისთვის — აცნობეთ ქირურგს.'
            )
    ldl_target = rule_value('CEA_ANTIPLATELET_STATIN_MANDATORY')
    if q_filled and not ctx.has_statin and not ctx.liver_3x:
        ctx.amber_flags.append('პაციენტს არ უფიქსირდება სტატინის მიღება (ESVS 2023 Class I: CEA-ს წინ ნაჩვენებია მაღალი ინტენსივობის სტატინი).')
    if ctx.ldl_val is not None and ctx.ldl_val >= ldl_target and not ctx.liver_3x:
        ctx.amber_flags.append(f'LDL ქოლესტერინი {ctx.ldl_val} მმოლ/ლ (>= {ldl_target} მმოლ/ლ) — ESVS 2023/ESC სამიზნისთვის განიხილეთ სტატინის მაქსიმალური დოზა ± ეზეტიმიბი.')
