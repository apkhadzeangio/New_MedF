"""Peripheral Angioplasty / Stenting (PAD PTA) pathway clinical rules (ESVS 2024 / ACC-AHA)."""
from ...models import PreopPatient
from ..registry import rule_value


def eval_pta(ctx):
    patient, q, s, q_filled = ctx.patient, ctx.q, ctx.s, ctx.q_filled
    hemo_cfg = rule_value('PAD_CLTI_HEMO_THRESHOLDS')
    abi_norm_lo, abi_norm_hi = rule_value('PAD_IC_BMT_3M')
    ldl_target = rule_value('CEA_ANTIPLATELET_STATIN_MANDATORY')
    is_clti = (patient.op_subtype == PreopPatient.OpSubtype.PTA_CLTI) or (
        q_filled and (bool(q.get('abf_rest_pain')) or bool(q.get('abf_ulcer')))
    )
    ctx.is_clti_any = is_clti
    if q_filled and patient.side not in ('R', 'L', 'B'):
        ctx.amber_flags.append('მიუთითეთ ჩარევის მხარე (მარჯვენა, მარცხენა ან ორმხრივი).')
    if q_filled and q.get('abf_wet_infection'):
        ctx.red_flags.append('ტერფის სველი ინფექცია / ფლეგმონა — რევასკულარიზაციასთან ერთად საჭიროა გადაუდებელი ქირურგიული სანაცია!')
    if q_filled and q.get('abf_groin_issue'):
        ctx.red_flags.append('საზარდულის არეში ინტერტრიგო / ინფექცია — პუნქციის ზონის ინფიცირების რისკი! განიხილეთ ალტერნატიული მიდგომა და სანაცია.')
    if ctx.days > ctx.t_days and not patient.approved_at and not ctx.is_on_hold and ctx.is_active_case:
        msg = f'გახანგრძლივებული მომზადება PTA/სტენტირებაზე: გავიდა {ctx.days} დღე (ლიმიტი: {ctx.t_days} დღე).'
        ctx.red_flags.append(msg)
        ctx.priority_flags.append(msg)

    if not is_clti and (s.get('pta_lesion_site') == rule_value('PTA_BTK_IC_STOP')):
        btk_ic_msg = (
            'ხანგამოშვებითი კოჭლობის (IC) დროს წვივის/ტერფის არტერიებზე (BTK / Infrapopliteal) ენდოვასკულური ჩარევა '
            'უკუნაჩვენებია (ESVS 2024 / ACC-AHA 2024 Class III — Harm)! გადაამოწმეთ ჩვენება ქირურგთან.'
        )
        ctx.red_flags.append(btk_ic_msg)
        ctx.hard_contraindications.append(btk_ic_msg)
    elif not is_clti and (s.get('pta_lesion_site') == 'multilevel'):
        ctx.amber_flags.append(
            'მრავალდონიანი დაზიანება ხანგამოშვებითი კოჭლობისას — დარწმუნდით, რომ არ იგეგმება იზოლირებული წვივის (BTK) არტერიების ჩარევა (ESVS 2024 Class III).'
        )
    if not is_clti and patient.side in ('R', 'L'):
        t_abi_pta = ctx.abi_r if patient.side == 'R' else ctx.abi_l
        t_tp_pta = ctx.tp_r if patient.side == 'R' else ctx.tp_l
        if t_abi_pta is not None and abi_norm_lo <= t_abi_pta <= abi_norm_hi and not (t_tp_pta is not None and t_tp_pta < 60):
            ctx.red_flags.append(
                f'საოპერაციო კიდურზე ({patient.side}) ფიქსირდება ნორმალური ABI ({t_abi_pta} — 0.90–1.40) კოჭლობის ფონზე — '
                'გადაამოწმეთ არჩეული მხარე (R/L) ან ჩვენება ქირურგთან!'
            )
    if q_filled and not is_clti:
        if not q.get('ic_bmt_3m') or q.get('abf_duration') in ('< 2 კვირა', '1–3 თვე'):
            ctx.amber_flags.append(
                'ხანგამოშვებითი კოჭლობა (ESVS 2024 Class I): ინვაზიურ ჩარევამდე რეკომენდებულია ≥3 თვიანი ოპტიმალური მედიკამენტური თერაპია (BMT) და სავარჯიშო თერაპია (SET).'
            )

    if s.get('pta_access_site') == 'cfa_calcified_scar':
        ctx.amber_flags.append('პუნქციის ზონაში (CFA) ნაწიბური / მძიმე კალციფიკაცია — განიხილეთ კონტრალატერალური, სხივის (Radial) ან მხრის მიდგომა ექო-კონტროლით.')
    if q_filled and q.get('pta_cant_lie_flat'):
        ctx.amber_flags.append('პაციენტს უჭირს ზურგზე გაუნძრევლად წოლა 60–90 წთ — ავტომატურად ჩაირთო ანესთეზიოლოგი (სედაციისთვის).')
    if 'clopidogrel_intol' in ctx.allergy_chips:
        has_oac_pta = bool(q_filled and (q.get('med_warfarin') or q.get('med_doac') or q.get('med_dabigatran')))
        alt_pta_txt = (
            'OAC + ასპირინი 75–100 მგ (OAC + ტიკაგრელორი ESC Class III სისხლდენის რისკის გამო არ ინიშნება!)'
            if has_oac_pta else 'ალტერნატიული P2Y12 (ტიკაგრელორი)'
        )
        ctx.red_flags.append(f'ანამნეზში კლოპიდოგრელის აუტანლობა — სტენტირებამდე შეათანხმეთ {alt_pta_txt} ქირურგთან!')
    if q_filled and not ctx.has_any_antithrombotic:
        ctx.amber_flags.append('პაციენტი არ იღებს ანტიაგრეგანტს PTA/სტენტირებამდე (ESVS 2024 Class I).')
    if q_filled and not ctx.has_statin and not ctx.liver_3x:
        ctx.amber_flags.append('პაციენტს არ უფიქსირდება სტატინი (ESVS 2024 Class I).')
    elif ctx.ldl_val is not None and ctx.ldl_val >= ldl_target and not ctx.liver_3x:
        ctx.amber_flags.append(f'LDL ქოლესტერინი {ctx.ldl_val} მმოლ/ლ (>= {ldl_target} მმოლ/ლ) — განიხილეთ სტატინის ინტენსიფიკაცია ± ეზეტიმიბი.')

    if ctx.min_comp_abi is not None:
        ctx.key_badge = f'ABI {ctx.min_comp_abi}'
        ctx.key_badge_urgent = bool(is_clti or ctx.min_comp_abi < hemo_cfg['abi_severe'])
    elif ctx.min_tp is not None:
        ctx.key_badge = f'TP {ctx.min_tp}'
        ctx.key_badge_urgent = bool(is_clti or ctx.min_tp < hemo_cfg['tp_severe'])
    elif is_clti:
        ctx.key_badge = 'CLTI'
        ctx.key_badge_urgent = True
    ctx.needs_wound_care = q_filled and (bool(q.get('abf_ulcer')) or bool(q.get('abf_wet_infection')) or bool(q.get('abf_groin_issue')))
