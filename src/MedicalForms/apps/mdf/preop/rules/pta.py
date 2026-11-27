"""Peripheral Angioplasty / Stenting (PAD PTA) pathway clinical rules (ESVS 2024 / ACC-AHA)."""
from ...models import PreopPatient
from ..registry import rule_value
from .abf import _eval_pad_hemo_discordance


def eval_pta(ctx):
    patient, q, s, q_filled = ctx.patient, ctx.q, ctx.s, ctx.q_filled
    hemo_cfg = rule_value('PAD_CLTI_HEMO_THRESHOLDS')
    abi_norm_lo, abi_norm_hi = rule_value('PAD_IC_BMT_3M')
    ldl_target = rule_value('CEA_ANTIPLATELET_STATIN_MANDATORY')
    is_clti = (patient.op_subtype == PreopPatient.OpSubtype.PTA_CLTI) or (
        q_filled and (
            bool(q.get('abf_rest_pain')) or bool(q.get('abf_ulcer'))
            or bool(q.get('ulcer_chips')) or bool(q.get('abf_wet_infection'))
        )
    )
    ctx.is_clti_any = is_clti
    if q_filled and patient.side not in ('R', 'L', 'B'):
        ctx.amber_flags.append('მიუთითეთ ჩარევის მხარე (მარჯვენა, მარცხენა ან ორმხრივი).')
    if q.get('abf_wet_infection'):
        ctx.red_flags.append('ტერფის სველი ინფექცია / ფლეგმონა — რევასკულარიზაციასთან ერთად საჭიროა გადაუდებელი ქირურგიული სანაცია!')
    if q.get('abf_groin_issue'):
        ctx.red_flags.append('საზარდულის არეში ინტერტრიგო / ინფექცია — პუნქციის ზონის ინფიცირების რისკი! განიხილეთ ალტერნატიული მიდგომა და სანაცია.')
    if ctx.days > ctx.t_days and not patient.approved_at and not ctx.is_on_hold and ctx.is_active_case:
        msg = f'გახანგრძლივებული მომზადება PTA/სტენტირებაზე: გავიდა {ctx.days} დღე (ლიმიტი: {ctx.t_days} დღე).'
        ctx.red_flags.append(msg)
        ctx.priority_flags.append(msg)

    if not is_clti and not ctx.is_ali_acute and (s.get('pta_lesion_site') == rule_value('PTA_BTK_IC_STOP')):
        btk_ic_msg = (
            'ხანგამოშვებითი კოჭლობის (IC) დროს წვივის/ტერფის არტერიებზე (BTK / Infrapopliteal) ენდოვასკულური ჩარევა '
            'უკუნაჩვენებია (ESVS 2024 / ACC-AHA 2024 Class III — Harm)! გადაამოწმეთ ჩვენება ქირურგთან.'
        )
        ctx.red_flags.append(btk_ic_msg)
        ctx.hard_contraindications.append(btk_ic_msg)
    elif not is_clti and not ctx.is_ali_acute and (s.get('pta_lesion_site') == 'multilevel'):
        ctx.amber_flags.append(
            'მრავალდონიანი დაზიანება ხანგამოშვებითი კოჭლობისას — დარწმუნდით, რომ არ იგეგმება იზოლირებული წვივის (BTK) არტერიების ჩარევა (ESVS 2024 Class III).'
        )
    if patient.side in ('R', 'L'):
        t_abi_pta = ctx.abi_r if patient.side == 'R' else ctx.abi_l
        t_tp_pta = ctx.tp_r if patient.side == 'R' else ctx.tp_l
        t_tbi_pta = ctx.tbi_r if patient.side == 'R' else ctx.tbi_l
        t_normal_hemo = (
            t_abi_pta is not None and abi_norm_lo < t_abi_pta <= abi_norm_hi
            and not (t_tp_pta is not None and t_tp_pta < hemo_cfg['normal_tp'])
            and not (t_tbi_pta is not None and t_tbi_pta < hemo_cfg['normal_tbi'])
        )
        if t_normal_hemo and (not is_clti or (ctx.contra_abi is not None and ctx.contra_abi < hemo_cfg['wrong_side_contra_abi'])):
            ctx.red_flags.append(
                f'საოპერაციო კიდურზე ({patient.side}) ფიქსირდება ნორმალური ABI ({t_abi_pta} — {abi_norm_lo + 0.01:.2f}–{abi_norm_hi:.2f})'
                + (f', ხოლო კონტრალატერალურზე ABI {ctx.contra_abi}!' if ctx.contra_abi is not None and ctx.contra_abi < hemo_cfg['wrong_side_contra_abi'] else ' კოჭლობის ფონზე')
                + ' — გადაამოწმეთ არჩეული მხარე (R/L) ან ჩვენება ქირურგთან!'
            )
    if ctx.min_comp_abi is not None and ctx.min_comp_abi < hemo_cfg['abi_severe'] and not is_clti:
        ctx.amber_flags.append(f'კრიტიკულად დაბალი გოჯ-მხრის ინდექსი (ABI {ctx.min_comp_abi} < {hemo_cfg["abi_severe"]:.2f}) — შეესაბამება მძიმე იშემიას.')
    _eval_pad_hemo_discordance(ctx, hemo_cfg)
    if patient.op_subtype == PreopPatient.OpSubtype.PTA_CLAUD and q_filled and (
        q.get('abf_rest_pain') or q.get('abf_ulcer') or q.get('ulcer_chips') or q.get('abf_wet_infection')
    ):
        clti_d = rule_value('WF_PREP_TARGET_DAYS')['aaa_clti']
        ctx.amber_flags.append(
            f'კითხვარით დაფიქსირდა მოსვენებითი ტკივილი ან წყლული (CLTI) — სასურველია ქვეტიპის გადართვა კრიტიკულ იშემიაზე ({clti_d}-დღიანი რეჟიმი).'
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

    _eval_pta_access_and_meds(ctx, is_clti, hemo_cfg, ldl_target)


def _eval_pta_access_and_meds(ctx, is_clti, hemo_cfg, ldl_target):
    q, s, q_filled = ctx.q, ctx.s, ctx.q_filled
    if s.get('pta_access_site') == 'cfa_calcified_scar':
        if ctx.on_dialysis:
            ctx.amber_flags.append(
                'პუნქციის ზონაში (CFA) ნაწიბური / მძიმე კალციფიკაცია — განიხილეთ კონტრალატერალური მიდგომა ექო-კონტროლით '
                '(დიალიზზე სხივის/მხრის მიდგომას მოერიდეთ AV ფისტულის დაცვის გამო!).'
            )
        else:
            ctx.amber_flags.append('პუნქციის ზონაში (CFA) ნაწიბური / მძიმე კალციფიკაცია — განიხილეთ კონტრალატერალური, სხივის (Radial) ან მხრის მიდგომა ექო-კონტროლით.')
    if q_filled and q.get('pta_cant_lie_flat'):
        ctx.amber_flags.append('პაციენტს უჭირს ზურგზე გაუნძრევლად წოლა 60–90 წთ — ავტომატურად ჩაირთო ანესთეზიოლოგი (სედაციისთვის).')
    if 'clopidogrel_intol' in ctx.allergy_chips:
        if getattr(ctx, 'active_gi_bleed', False):
            ctx.red_flags.append('ანამნეზში კლოპიდოგრელის აუტანლობა + აქტიური კუჭ-ნაწლავის სისხლდენა / მწვავე წყლული — ანტიაგრეგანტული თერაპია დაბლოკილია ენდოსკოპიურ ჰემოსტაზამდე (შეათანხმეთ გასტროენტეროლოგთან და ქირურგთან)!')
        else:
            ft_keys = ctx.ft_meds.get('matched_keys') or set()
            has_oac_pta = bool(
                q.get('med_warfarin') or q.get('med_doac') or q.get('med_dabigatran')
                or ('rivaroxaban_2_5' in ctx.med_chips)
                or s.get('post_antithrombotic_plan') in ('voyager', 'voyager_pad', 'oac_sapt')
                or bool(ft_keys & {'warfarin', 'doac', 'dabigatran', 'rivaroxaban_2_5'})
            )
            ren_red_pta = rule_value('REN_CKD_NEPHRO_HYDRATION')['red']
            eff_r_pta = None if getattr(ctx, 'creat_unit_mismatch', False) else getattr(ctx, 'eff_renal_sat', ctx.eff_renal)
            sev_ren_pta = bool(ctx.on_dialysis or (eff_r_pta is not None and eff_r_pta < ren_red_pta))
            has_mv_pta = 'mech_valve' in ctx.cad_chips
            has_preg_pta = bool(getattr(ctx, 'eff_pregnant', False))
            has_hit_pta = 'hit_heparin' in ctx.allergy_chips
            asa_allergy_pta = 'nsaid' in ctx.allergy_chips
            if has_oac_pta and has_preg_pta:
                alt_pta_txt = 'ინდივიდუალური რეჟიმი მეან-გინეკოლოგისა და კარდიოლოგის კონსილიუმით (ორსულობაზე DOAC/რივაროქსაბანი 2.5 მგ და პერორალური OAC უკუნაჩვენებია; ტიკაგრელორი + OAC აკრძალულია!)'
            elif has_oac_pta and has_mv_pta:
                mv_ap = 'ინდივიდუალური კონსილიუმის რეჟიმი (ასპირინზე და კლოპიდოგრელზე ალერგია!)' if asa_allergy_pta else 'ასპირინი 75–100 მგ'
                alt_pta_txt = f'უვადო ვარფარინი (VKA) + {mv_ap} (მექ. სარქველზე DOAC / რივაროქსაბანი 2.5 მგ და ტიკაგრელორი + OAC აკრძალულია!)'
            elif has_oac_pta and (sev_ren_pta or ctx.liver_3x or has_hit_pta or asa_allergy_pta):
                c_tags = [t for cond, t in ((sev_ren_pta, f'CrCl<{ren_red_pta}/დიალიზი'), (ctx.liver_3x, 'ღვიძლის ≥3x'), (has_hit_pta, 'HIT'), (asa_allergy_pta, 'ასპირინზე ალერგია')) if cond]
                alt_pta_txt = f'ინდივიდუალური უსაფრთხო ანტიკოაგულაციის რეჟიმი კონსილიუმით ({", ".join(c_tags)} — DOAC/რივაროქსაბანი 2.5 მგ და OAC + ტიკაგრელორი უკუნაჩვენებია!)'
            elif has_oac_pta:
                alt_pta_txt = 'უსაფრთხო OAC + ასპირინი 75–100 მგ (OAC / რივაროქსაბანი 2.5 მგ + ტიკაგრელორი ESC Class III სისხლდენის რისკის გამო აკრძალულია!)'
            elif ctx.liver_3x or has_preg_pta:
                blk_rsn = 'ღვიძლის ≥3x დისფუნქცია (EMA SmPC 4.3)' if ctx.liver_3x else 'ორსულობა (EMA SmPC 4.6)'
                alt_pta_txt = (
                    f'ინდივიდუალური რეჟიმი კონსილიუმით ({blk_rsn} — ტიკაგრელორი უკუნაჩვენებია; ასპირინზეც ალერგია!)'
                    if asa_allergy_pta else
                    f'უსაფრთხო ალტერნატიული რეჟიმი კონსილიუმით / ასპირინი 75–100 მგ ({blk_rsn} — ტიკაგრელორი უკუნაჩვენებია!)'
                )
            else:
                alt_pta_txt = 'ალტერნატიული P2Y12 (ტიკაგრელორი)'
            ctx.red_flags.append(f'ანამნეზში კლოპიდოგრელის აუტანლობა — სტენტირებამდე შეათანხმეთ {alt_pta_txt} ქირურგთან!')
    if q_filled and not ctx.has_any_antithrombotic:
        if getattr(ctx, 'active_gi_bleed', False):
            ctx.amber_flags.append('აქტიური კუჭ-ნაწლავის სისხლდენა / მწვავე წყლული — PTA-ს წინ ანტიაგრეგანტის დაწყება დაბლოკილია ენდოსკოპიურ ჰემოსტაზამდე (შეათანხმეთ გასტროენტეროლოგთან და ქირურგთან)!')
        else:
            ctx.amber_flags.append('პაციენტი არ იღებს ანტიაგრეგანტს PTA/სტენტირებამდე (ESVS 2024 Class I).')
    if q_filled and not ctx.has_statin and not ctx.liver_3x and not getattr(ctx, 'eff_pregnant', False):
        ctx.amber_flags.append('პაციენტს არ უფიქსირდება სტატინი (ESVS 2024 Class I).')
    if ctx.ldl_val is not None and ctx.ldl_val >= ldl_target and not ctx.liver_3x and not getattr(ctx, 'eff_pregnant', False):
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
    ctx.needs_wound_care = bool(q.get('abf_wet_infection') or q.get('abf_groin_issue')) or (q_filled and bool(q.get('abf_ulcer') or q.get('ulcer_chips')))
