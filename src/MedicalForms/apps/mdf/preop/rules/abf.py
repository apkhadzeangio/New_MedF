"""Aortobifemoral Bypass (ABF) and Infrainguinal PAD Bypass pathway clinical rules (ESVS 2024)."""
from ...models import PreopPatient
from ..registry import rule_value


def _eval_abf_clinical(ctx, is_clti):
    patient, q, q_filled = ctx.patient, ctx.q, ctx.q_filled
    if patient.op_subtype == PreopPatient.OpSubtype.ABF_CLAUD and q_filled and (q.get('abf_rest_pain') or q.get('abf_ulcer')):
        ctx.amber_flags.append(
            'კითხვარით დაფიქსირდა მოსვენებითი ტკივილი ან წყლული (CLTI) — სასურველია ქვეტიპის გადართვა კრიტიკულ იშემიაზე (7-დღიანი რეჟიმი).'
        )
    if q.get('abf_duration') == '< 2 კვირა':
        ctx.red_flags.append(
            'სიმპტომების ხანგრძლივობა < 2 კვირა — გამოირიცხოს მწვავე კიდურის იშემია (ALI, ESVS 2020)! '
            'სასწრაფოდ აცნობეთ ქირურგს (გეგმური 3-თვიანი SET აქ არ მიესადაგება)!'
        )
    elif patient.op_subtype == PreopPatient.OpSubtype.ABF_CLAUD and q_filled and not is_clti and (not q.get('ic_bmt_3m') or q.get('abf_duration') == '1–3 თვე'):
        ctx.amber_flags.append(
            'ხანგამოშვებითი კოჭლობა (ESVS 2024 Class I): ინვაზიურ ჩარევამდე რეკომენდებულია ≥3 თვიანი ოპტიმალური მედიკამენტური თერაპია (BMT) და სავარჯიშო თერაპია (SET).'
        )
    if q.get('abf_wet_infection'):
        ctx.red_flags.append(
            'ტერფის სველი ნეკროზი / ჩირქოვანი გამონადენი / ფლეგმონა — სინთეზური ბიფურკაციული პროთეზის სეფსისის უმაღლესი რისკი! '
            'სასწრაფოდ აცნობეთ ქირურგს სანაციისა და ეტაპობრივი ტაქტიკის გადასაწყვეტად.'
        )
    if q.get('abf_groin_issue'):
        ctx.red_flags.append(
            'საზარდულის არეში სოკოვანი გამოყელვა (ინტერტრიგო) / გამონაყარი / ანთებითი კვანძი — '
            'ბარძაყის ანასტომოზისა და პროთეზის ინფიცირების უმაღლესი რისკი! აცნობეთ ქირურგს.'
        )

    if ctx.days > ctx.t_days and not patient.approved_at and not ctx.is_on_hold and ctx.is_active_case:
        if is_clti:
            msg = f'გახანგრძლივებული მომზადება CLTI პაციენტთან: გავიდა {ctx.days} დღე (სამიზნე ლიმიტი: {ctx.t_days} დღე) — კიდურის დაკარგვის რისკი!'
        else:
            msg = f'გახანგრძლივებული წინასაოპერაციო მომზადება: გავიდა {ctx.days} დღე (სამიზნე ლიმიტი: {ctx.t_days} დღე).'
        ctx.red_flags.append(msg)
        ctx.priority_flags.append(msg)
    elif not patient.approved_at and not ctx.is_on_hold and ctx.is_active_case and ctx.days >= ctx.t_days - 2:
        ctx.amber_flags.append(f'მომზადების მე-{ctx.days} დღე ({ctx.t_days} დღიდან) — დააჩქარეთ დარჩენილი კვლევები.')


def eval_abf(ctx):
    patient, q, s, q_filled = ctx.patient, ctx.q, ctx.s, ctx.q_filled
    hemo_cfg = rule_value('PAD_CLTI_HEMO_THRESHOLDS')
    abi_norm_lo, abi_norm_hi = rule_value('PAD_IC_BMT_3M')
    ldl_target = rule_value('CEA_ANTIPLATELET_STATIN_MANDATORY')
    is_clti = (patient.op_subtype == PreopPatient.OpSubtype.ABF_CLTI) or (
        q_filled and (bool(q.get('abf_rest_pain')) or bool(q.get('abf_ulcer')))
    )
    ctx.is_clti_any = is_clti
    _eval_abf_clinical(ctx, is_clti)

    clamp_zone = s.get('abf_clamp_zone') or 'pending'
    if clamp_zone == 'juxtarenal_calcified':
        ctx.red_flags.append(
            'CTA-ზე იუქსტარენალური აორტის ოკლუზია ან მძიმე კალციფიკაცია — საჭიროა სუპრარენალური კლამპირებისა და თირკმლის დაცვის დაგეგმვა ქირურგთან!'
        )

    outflow = s.get('abf_outflow') or 'pending'
    if outflow == 'poor_outflow':
        ctx.red_flags.append(
            'არადამაკმაყოფილებელი დისტალური Outflow (Profunda + SFA მძიმე დაზიანება) — შუნტის თრომბოზის რისკი! '
            'შეათანხმეთ ჰიბრიდული/დისტალური რეკონსტრუქცია ქირურგთან.'
        )
        ctx.needs_alt_vein = True
        ctx.reflex_orders.append({
            'key': 'abf_gsv',
            'trigger': 'CTA-ზე მძიმე დისტალური Outflow დაზიანება',
            'action': 'დიდი საჩინო ვენის (GSV) რეფლექს ექო-კარტირება (შესაძლო თანმხლები ბარძაყ-მუხლქვეშა შუნტირებისთვის)',
            'done': (s.get('alt_vein_mapped') or 'pending') != 'pending',
        })
    elif outflow == 'profunda_stenosis':
        ctx.amber_flags.append('CFA / Profunda Femoris სტენოზი CTA-ზე — გასათვალისწინებელია თანმხლები ენდარტერექტომია / პროფუნდოპლასტიკა.')

    if s.get('abf_mesenteric') == 'sma_stenosis':
        ctx.red_flags.append('CTA-ზე ზემო ჯორჯლის არტერიის (SMA) მნიშვნელოვანი სტენოზი/ოკლუზია — მსხვილი/წვრილი ნაწლავის იშემიის უმაღლესი რისკი აორტაზე ჩარევისას!')
    elif s.get('abf_mesenteric') == 'ima_dominant_iia_occ':
        ctx.amber_flags.append('დომინანტური IMA კოლატერალი / ორმხრივი IIA ოკლუზია CTA-ზე — დასაგეგმია IMA რეიმპლანტაცია / მენჯის პერფუზიის შენარჩუნება.')
    if s.get('abf_renal_stenosis'):
        ctx.amber_flags.append('თანმხლები თირკმლის არტერიის სტენოზი (≥60%) CTA-ზე — განიხილეთ თანმხლები რენალური ჩარევა / ნეფროპროტექცია.')

    if ctx.min_comp_abi is not None:
        ctx.key_badge = f'ABI {ctx.min_comp_abi}'
        ctx.key_badge_urgent = bool(is_clti or ctx.min_comp_abi < hemo_cfg['abi_severe'])
        if ctx.min_comp_abi < hemo_cfg['abi_severe'] and not is_clti:
            ctx.amber_flags.append(f'კრიტიკულად დაბალი გოჯ-მხრის ინდექსი (ABI {ctx.min_comp_abi} < 0.40) — შეესაბამება მძიმე იშემიას.')
        elif (
            not is_clti and abi_norm_lo < ctx.min_comp_abi <= abi_norm_hi
            and not ctx.abi_non_compressible
            and not (ctx.min_tp is not None and ctx.min_tp < hemo_cfg['normal_tp'])
            and not (ctx.min_tbi is not None and ctx.min_tbi < hemo_cfg['normal_tbi'])
        ):
            ctx.red_flags.append(
                f'ფიქსირდება ნორმალური ABI ({ctx.min_comp_abi} — 0.91–1.40) ორივე კიდურზე კოჭლობის ფონზე — '
                'გადაამოწმეთ დატვირთვის შემდგომი ABI ან აორტო-ილიაკური ჩვენება ქირურგთან!'
            )
    elif ctx.min_tp is not None:
        ctx.key_badge = f'TP {ctx.min_tp}'
        ctx.key_badge_urgent = bool(is_clti or ctx.min_tp < hemo_cfg['tp_severe'])
    elif ctx.min_tbi is not None:
        ctx.key_badge = f'TBI {ctx.min_tbi}'
        ctx.key_badge_urgent = bool(is_clti or ctx.min_tbi < hemo_cfg['tbi_severe'])
    elif is_clti:
        ctx.key_badge = 'CLTI'
        ctx.key_badge_urgent = True
    elif q.get('abf_claud_m'):
        ctx.key_badge = f"{q['abf_claud_m']} მ"

    if q_filled and not s.get('cta_disc'):
        ctx.amber_flags.append('აორტო-ილიაკური / ქვემო კიდურების CTA DICOM დისკი ჯერ არ არის მოტანილი / შემოწმებული.')
    if q_filled and not ctx.has_any_antithrombotic:
        if 'nsaid' in ctx.allergy_chips:
            ap_rec = 'ალტერნატიული P2Y12 (ასპირინზე ალერგია!)' if 'clopidogrel_intol' in ctx.allergy_chips else 'კლოპიდოგრელი (ასპირინზე ალერგია!)'
        elif 'clopidogrel_intol' in ctx.allergy_chips:
            ap_rec = 'ასპირინი (კლოპიდოგრელზე აუტანლობა!)'
        else:
            ap_rec = 'ასპირინი ან კლოპიდოგრელი'
        ctx.amber_flags.append(f'პაციენტი არ იღებს ანტიაგრეგანტს (ESVS 2024 Class I: {ap_rec}).')
    if q_filled and not ctx.has_statin and not ctx.liver_3x:
        ctx.amber_flags.append('პაციენტს არ უფიქსირდება სტატინი (ESVS 2024 Class I: მაღალი ინტენსივობის სტატინი).')
    if ctx.ldl_val is not None and ctx.ldl_val >= ldl_target and not ctx.liver_3x:
        ctx.amber_flags.append(f'LDL ქოლესტერინი {ctx.ldl_val} მმოლ/ლ (>= {ldl_target} მმოლ/ლ) — განიხილეთ ლიპიდდამწევი თერაპიის გაძლიერება (ESVS 2024).')

    ctx.needs_wound_care = bool(q.get('abf_wet_infection') or q.get('abf_groin_issue')) or (q_filled and bool(q.get('abf_ulcer')))


def _eval_bypass_conduit(ctx, is_clti):
    patient, s = ctx.patient, ctx.s
    gsv_cfg = rule_value('PAD_GSV_MIN_MM')
    gsv_st = s.get('pad_gsv_status') or 'pending'
    gsv_inadequate = (
        gsv_st == 'inadequate'
        or (ctx.pad_gsv_mm is not None and ctx.pad_gsv_mm < gsv_cfg['gsv_min_mm'])
        or ('vein_strip' in ctx.vasc_chips)
    )
    if gsv_inadequate:
        ctx.needs_alt_vein = True
        ctx.reflex_orders.append({
            'key': 'alt_vein',
            'trigger': 'იფსილატერალური GSV უვარგისია (< 3.0 მმ ან ამოღებულია)',
            'action': 'ალტერნატიული აუტოვენის რეფლექს-კარტირება (კონტრალატერალური GSV, SSV და ხელის ვენები — Cephalic/Basilic)',
            'done': (s.get('alt_vein_mapped') or 'pending') != 'pending',
        })
    contra_abi, contra_tp = ctx.contra_abi, ctx.contra_tp
    if s.get('alt_vein_mapped') == 'contra_gsv_ok':
        if patient.side == 'B':
            ctx.red_flags.append('ორმხრივი შუნტირებისას (მხარე B) „კონტრალატერალური GSV“ წინააღმდეგობრივია — დააზუსტეთ საოპერაციო მხარე და კონდუიტი!')
        elif (contra_abi is not None and contra_abi < gsv_cfg['donor_abi_red']) or (contra_tp is not None and contra_tp < gsv_cfg['donor_tp_red']):
            ctx.red_flags.append(
                f'კონტრალატერალურ კიდურზე მძიმე არტერიული იშემიაა (ABI {contra_abi if contra_abi is not None else "—"} / '
                f'TP {contra_tp if contra_tp is not None else "—"} mmHg) — იშემიური კიდურიდან GSV-ის აღება საშიშია ჭრილობის ნეკროზის გამო! განიხილეთ ხელის ვენა (Arm Vein).'
            )
        elif (contra_abi is not None and contra_abi < gsv_cfg['donor_abi_amb']) or (contra_tp is not None and contra_tp < gsv_cfg['donor_tp_amb']):
            ctx.amber_flags.append(
                f'დონორ (კონტრალატერალურ) კიდურზე დაქვეითებული პერფუზიაა (ABI {contra_abi if contra_abi is not None else "—"} / '
                f'TP {contra_tp if contra_tp is not None else "—"}) — GSV-ის აღებისას საყურადღებოა ჭრილობის შეხორცება!'
            )
        elif contra_abi is not None and contra_abi > rule_value('VEN_ARTERIAL_ISCHEMIA_COMPRESSION')['abi_noncomp'] and not (contra_tp is not None and contra_tp >= gsv_cfg['donor_tp_amb']):
            ctx.amber_flags.append(
                f'დონორ კიდურზე არაკომპრესირებადი ABI ({contra_abi} > 1.40) — კონტრალატერალური GSV-ის აღებამდე გადაამოწმეთ Toe Pressure ≥60 mmHg!'
            )
        elif contra_abi is None and contra_tp is None:
            ctx.amber_flags.append('კონტრალატერალური GSV-ის აღებამდე გადაამოწმეთ დონორი კიდურის ABI / პულსაცია!')
    if s.get('alt_vein_mapped') == 'no_autovein_prosthetic':
        if not is_clti and not ctx.is_ali_acute and rule_value('PAD_PROSTHETIC_IC_STOP'):
            pros_ic_msg = (
                'ხანგამოშვებითი კოჭლობის (IC) დროს აუტოვენის არარსებობისას სინთეზური ინფრაინგვინალური შუნტირება '
                'უკუნაჩვენებია (ESVS 2024 / ACC-AHA Class III — Harm)! გადაამოწმეთ ჩვენება ქირურგთან!'
            )
            ctx.red_flags.append(pros_ic_msg)
            ctx.hard_contraindications.append(pros_ic_msg)
        elif is_clti or ctx.is_ali_acute:
            ctx.red_flags.append('ვარგისი აუტოვენა ვერცერთ კიდურზე ვერ მოიძებნა — შეათანხმეთ სინთეზური/კომპოზიტური პროთეზის ან ენდოვასკულური ტაქტიკა ქირურგთან!')


def _eval_pad_bypass_side_and_hemo(ctx, is_clti, hemo_cfg, abi_norm_lo, abi_norm_hi):
    patient, q, q_filled = ctx.patient, ctx.q, ctx.q_filled
    if patient.side in ('R', 'L'):
        t_abi_byp = ctx.abi_r if patient.side == 'R' else ctx.abi_l
        t_tp_byp = ctx.tp_r if patient.side == 'R' else ctx.tp_l
        t_tbi_byp = ctx.tbi_r if patient.side == 'R' else ctx.tbi_l
        t_normal_hemo = (
            t_abi_byp is not None and abi_norm_lo < t_abi_byp <= abi_norm_hi
            and not (t_tp_byp is not None and t_tp_byp < hemo_cfg['normal_tp'])
            and not (t_tbi_byp is not None and t_tbi_byp < hemo_cfg['normal_tbi'])
        )
        if t_normal_hemo and (not is_clti or (ctx.contra_abi is not None and ctx.contra_abi < hemo_cfg['wrong_side_contra_abi'])):
            ctx.red_flags.append(
                f'საოპერაციო კიდურზე ({patient.side}) ფიქსირდება ნორმალური ABI ({t_abi_byp} — 0.91–1.40)'
                + (f', ხოლო კონტრალატერალურზე ABI {ctx.contra_abi}!' if ctx.contra_abi is not None and ctx.contra_abi < hemo_cfg['wrong_side_contra_abi'] else ' კოჭლობის ფონზე')
                + ' — გადაამოწმეთ არჩეული მხარე (R/L) ან დატვირთვის შემდგომი პერფუზია ქირურგთან!'
            )
    if ctx.min_comp_abi is not None and ctx.min_comp_abi < hemo_cfg['abi_severe'] and not is_clti:
        ctx.amber_flags.append(f'კრიტიკულად დაბალი გოჯ-მხრის ინდექსი (ABI {ctx.min_comp_abi} < 0.40) — შეესაბამება მძიმე იშემიას.')
    if patient.op_subtype == PreopPatient.OpSubtype.BYPASS_CLAUD and q_filled and (q.get('abf_rest_pain') or q.get('abf_ulcer')):
        ctx.amber_flags.append(
            'კითხვარით დაფიქსირდა მოსვენებითი ტკივილი ან წყლული (CLTI) — სასურველია ქვეტიპის გადართვა კრიტიკულ იშემიაზე (7-დღიანი რეჟიმი).'
        )
    if q.get('abf_duration') == '< 2 კვირა':
        ctx.red_flags.append(
            'სიმპტომების ხანგრძლივობა < 2 კვირა — გამოირიცხოს მწვავე კიდურის იშემია (ALI, ESVS 2020)! '
            'სასწრაფოდ აცნობეთ ქირურგს!'
        )
    elif q_filled and not is_clti and (not q.get('ic_bmt_3m') or q.get('abf_duration') == '1–3 თვე'):
        ctx.amber_flags.append(
            'ხანგამოშვებითი კოჭლობა (ESVS 2024 Class I): ინვაზიურ ჩარევამდე რეკომენდებულია ≥3 თვიანი ოპტიმალური მედიკამენტური თერაპია (BMT) და სავარჯიშო თერაპია (SET).'
        )


def eval_pad_bypass(ctx):
    patient, q, s, q_filled = ctx.patient, ctx.q, ctx.s, ctx.q_filled
    hemo_cfg = rule_value('PAD_CLTI_HEMO_THRESHOLDS')
    abi_norm_lo, abi_norm_hi = rule_value('PAD_IC_BMT_3M')
    gsv_min = rule_value('PAD_GSV_MIN_MM')['gsv_min_mm']
    ldl_target = rule_value('CEA_ANTIPLATELET_STATIN_MANDATORY')
    is_clti = (patient.op_subtype == PreopPatient.OpSubtype.BYPASS_CLTI) or (
        q_filled and (bool(q.get('abf_rest_pain')) or bool(q.get('abf_ulcer')))
    )
    ctx.is_clti_any = is_clti
    if patient.side == 'B':
        ctx.red_flags.append(
            'ერთდროული ორმხრივი ინფრაინგვინალური შუნტირება (მხარე B) დაკავშირებულია მაღალ გართულებებთან — დააზუსტეთ ეტაპობრივი მხარე (R ან L) ქირურგთან!'
        )
    elif q_filled and patient.side not in ('R', 'L'):
        ctx.amber_flags.append('მიუთითეთ საოპერაციო კიდურის მხარე (მარჯვენა ან მარცხენა).')
    if q.get('abf_wet_infection'):
        ctx.red_flags.append('ტერფის სველი განგრენა / ჩირქოვანი ინფექცია — სასწრაფოდ აცნობეთ ქირურგს დრენირებისა და ანტიბიოტიკოთერაპიისთვის!')
    if q.get('abf_groin_issue'):
        ctx.red_flags.append('საზარდულის არეში ინტერტრიგო / ინფექცია — შუნტის ინფიცირების მაღალი რისკი! აცნობეთ ქირურგს.')
    if ctx.days > ctx.t_days and not patient.approved_at and not ctx.is_on_hold and ctx.is_active_case:
        msg = f'გახანგრძლივებული მომზადება პერიფერიულ შუნტირებაზე: გავიდა {ctx.days} დღე (ლიმიტი: {ctx.t_days} დღე).'
        ctx.red_flags.append(msg)
        ctx.priority_flags.append(msg)

    runoff = s.get('pad_runoff_status') or 'pending'
    if runoff == 'desert_foot':
        if not is_clti and not ctx.is_ali_acute:
            df_msg = (
                'ხანგამოშვებითი კოჭლობის (IC) დროს Desert foot კალაპოტზე ინფრაინგვინალური შუნტირება '
                'უკუნაჩვენებია (ESVS 2024 Class III — თრომბოზისა და კიდურის დაკარგვის რისკი)! გადაამოწმეთ ჩვენება ქირურგთან!'
            )
            ctx.red_flags.append(df_msg)
            ctx.hard_contraindications.append(df_msg)
        else:
            ctx.red_flags.append('CTA/ანგიოგრაფიით კრიტიკულად დაზიანებული დისტალური კალაპოტი (Desert foot) — შუნტის ადრეული თრომბოზის უმაღლესი რისკი! აცნობეთ ქირურგს.')
    elif runoff == 'tibial_single':
        ctx.amber_flags.append('დისტალური მიმღები მხოლოდ წვივის ერთი არტერიაა — აუცილებელია ხარისხიანი აუტოვენა (GSV ≥ 3.0 მმ).')

    _eval_bypass_conduit(ctx, is_clti)
    _eval_pad_bypass_side_and_hemo(ctx, is_clti, hemo_cfg, abi_norm_lo, abi_norm_hi)

    if 'vein_strip' in ctx.vasc_chips:
        ctx.key_badge = 'GSV ამოღ.'
        ctx.key_badge_urgent = True
        if s.get('pad_gsv_status') == 'adequate':
            ctx.amber_flags.append('წინააღმდეგობა: ანამნეზში მონიშნულია საჩინო ვენის ამოღება (vein_strip), ხოლო ექოზე — „ადეკვატური GSV“! გადაამოწმეთ კონდუიტის ვარგისიანობა.')
    elif ctx.pad_gsv_mm is not None:
        ctx.key_badge = f'GSV {ctx.pad_gsv_mm}მმ'
        ctx.key_badge_urgent = bool(ctx.pad_gsv_mm < gsv_min or is_clti)
    elif ctx.min_comp_abi is not None:
        ctx.key_badge = f'ABI {ctx.min_comp_abi}'
        ctx.key_badge_urgent = bool(is_clti or ctx.min_comp_abi < hemo_cfg['abi_severe'])
    elif ctx.min_tp is not None:
        ctx.key_badge = f'TP {ctx.min_tp}'
        ctx.key_badge_urgent = bool(is_clti or ctx.min_tp < hemo_cfg['tp_severe'])
    elif is_clti:
        ctx.key_badge = 'CLTI'
        ctx.key_badge_urgent = True

    if q_filled and not s.get('cta_disc'):
        ctx.amber_flags.append('ქვემო კიდურების ანგიოგრაფიის / CTA DICOM დისკი ჯერ არ არის მოტანილი.')
    if q_filled and not ctx.has_any_antithrombotic:
        ctx.amber_flags.append('პაციენტი არ იღებს ანტიაგრეგანტს (ESVS 2024 Class I).')
    if q_filled and not ctx.has_statin and not ctx.liver_3x:
        ctx.amber_flags.append('პაციენტს არ უფიქსირდება სტატინი (ESVS 2024 Class I).')
    if ctx.ldl_val is not None and ctx.ldl_val >= ldl_target and not ctx.liver_3x:
        ctx.amber_flags.append(f'LDL ქოლესტერინი {ctx.ldl_val} მმოლ/ლ (>= {ldl_target} მმოლ/ლ) — განიხილეთ სტატინის ინტენსიფიკაცია ± ეზეტიმიბი.')
    ctx.needs_wound_care = bool(q.get('abf_wet_infection') or q.get('abf_groin_issue')) or (q_filled and bool(q.get('abf_ulcer')))
