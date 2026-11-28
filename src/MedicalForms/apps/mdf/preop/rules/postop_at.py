"""Postoperative antithrombotic plan evaluation (VOYAGER PAD, DAPT, SAPT, OAC, parenteral AC, HIT, pregnancy)."""
from ...models import PreopPatient
from ..constants import POST_ANTITHROMBOTIC_CHOICES
from ..registry import rule_value


def _preg_mech_valve_postop_msg(has_hit, severe_renal=False):
    if has_hit:
        return (
            'ორსულობა + მექანიკური სარქველი + HIT — UFH, LMWH და DOAC კატეგორიულად აკრძალულია! '
            'სავალდებულოა ჰემატოლოგის, კარდიოლოგისა და მეან-გინეკოლოგის კონსილიუმი (არაჰეპარინული IV სქემა / ტრიმესტრით VKA)!'
        )
    if severe_renal:
        return (
            'ორსულობა + მექანიკური სარქველი + თირკმლის მძიმე უკმარისობა/დიალიზი — DOAC / VOYAGER აკრძალულია, LMWH კუმულირდება! '
            'სავალდებულოა მეან-გინეკოლოგის, კარდიოლოგისა და ნეფროლოგის კონსილიუმი (IV/SC UFH ან ტრიმესტრით VKA — თვითნებური მოხსნა დაუშვებელია)!'
        )
    return (
        'ორსულობა + მექანიკური სარქველი — სავალდებულოა მეან-გინეკოლოგისა და კარდიოლოგის კონსილიუმი '
        '(DOAC / VOYAGER აკრძალულია; VKA vs LMWH/UFH ტრიმესტრით — თვითნებური მოხსნა დაუშვებელია)!'
    )


def _gi_bleed_postop_msg(ctx):
    extra = []
    if ctx.has_mech_valve:
        extra.append('მექანიკური სარქველი')
    if getattr(ctx, 'has_active_stent_dapt', False):
        extra.append('ახალი კორონარული სტენტი')
    sfx = f' ({", ".join(extra)} — კარდიოლოგის ჩართვით!)' if extra else ''
    return (
        f'აქტიური კუჭ-ნაწლავის სისხლდენა / მწვავე წყლული — ანტითრომბოზული თერაპიის აღდგენა დაბლოკილია '
        f'ენდოსკოპიურ ჰემოსტაზამდე! სავალდებულოა გასტროენტეროლოგისა და ქირურგის კონსილიუმი{sfx}'
    )


def _contra_full_oac_postop_label(ctx, oac_partner_lbl):
    has_hit = 'hit_heparin' in ctx.allergy_chips
    if getattr(ctx, 'eff_pregnant', False):
        ren_red = rule_value('REN_CKD_NEPHRO_HYDRATION')['red']
        eff_r = None if getattr(ctx, 'creat_unit_mismatch', False) else getattr(ctx, 'eff_renal_sat', ctx.eff_renal)
        sev_ren = bool(ctx.on_dialysis or (eff_r is not None and eff_r < ren_red))
        if has_hit:
            return f'ორსულობა + HIT — პერორალური OAC და ჰეპარინი/LMWH აკრძალულია! შეარჩიეთ არაჰეპარინული სქემა (+ ჩვენებისას {oac_partner_lbl}) ჰემატოლოგთან, მეან-გინეკოლოგთან და კარდიოლოგთან!'
        if sev_ren:
            return f'ორსულობა + CrCl/eGFR <{ren_red}/დიალიზი — პერორალური OAC აკრძალულია, LMWH კუმულირდება! შეარჩიეთ არასფრაქციული ჰეპარინი (UFH) (+ ჩვენებისას {oac_partner_lbl}) მეან-გინეკოლოგთან და კარდიოლოგთან!'
        return f'ორსულობა — პერორალური OAC (DOAC/ვარფარინი) უკუნაჩვენებია! არ განაახლოთ, შეარჩიეთ LMWH/UFH (+ ჩვენებისას {oac_partner_lbl}) მეან-გინეკოლოგთან და კარდიოლოგთან!'
    hit_sfx = ' (HIT: ჰეპარინი/LMWH აკრძალულია!)' if has_hit else ''
    return f'უკუნაჩვენები OAC არ განაახლოთ — შეარჩიეთ უსაფრთხო ანტიკოაგულაციის სქემა (+ ჩვენებისას {oac_partner_lbl}) კარდიოლოგ/ნეფროლოგთან!{hit_sfx}'


def _parenteral_caveat_suffix(ctx):
    ren_red = rule_value('REN_CKD_NEPHRO_HYDRATION')['red']
    eff_r = None if getattr(ctx, 'creat_unit_mismatch', False) else getattr(ctx, 'eff_renal_sat', ctx.eff_renal)
    has_hit, sev_ren = ('hit_heparin' in ctx.allergy_chips), bool(ctx.on_dialysis or (eff_r is not None and eff_r < ren_red))
    flags = []
    if has_hit and sev_ren:
        flags.append(f'HIT + CrCl/eGFR <{ren_red}/დიალიზი: ჰეპარინი/UFH/LMWH/ფონდაპარინუქსი აკრძალულია — არაჰეპარინული IV სქემა (ბივალირუდინი/არგატრობანი) ჰემატოლოგთან!')
    elif has_hit:
        flags.append('HIT: ჰეპარინი/LMWH აკრძალულია — არაჰეპარინული სქემა!')
    elif sev_ren:
        flags.append(f'CrCl/eGFR <{ren_red}/დიალიზი: LMWH/ფონდაპარინუქსი კუმულირდება — უპირატესია UFH!')
    if getattr(ctx, 'eff_pregnant', False):
        flags.append('ორსულობა: მეან-გინეკოლოგის ჩართვით, DOAC/VKA/ფონდაპარინუქსი/არგატრობანი მოერიდეთ!')
    if ctx.liver_3x:
        flags.append('ღვიძლის ≥3x: კოაგულოგრამის კონტროლით — არგატრობანი −75%!')
    return f' ({" · ".join(flags)})' if flags else ''


def _eval_explicit_oac_sapt(ctx, asa_allergy, clop_intol, oac_partner_lbl):
    if getattr(ctx, 'eff_pregnant', False):
        ctx.red_flags.append(
            'ორსულობის ფონზე არჩეულია პოსტოპ. OAC + ანტიაგრეგანტი — DOAC აკრძალულია, ვარფარინი (VKA) ტერატოგენულია! '
            'სავალდებულოა მეან-გინეკოლოგისა და კარდიოლოგის კონსილიუმი!'
        )
        return _contra_full_oac_postop_label(ctx, oac_partner_lbl)
    if getattr(ctx, 'has_contra_full_oac', False) and not ctx.has_mech_valve:
        ctx.red_flags.append('არჩეულია პოსტოპ. OAC + ანტიაგრეგანტი, მაგრამ მიმდინარე OAC უკუნაჩვენებია (თირკმლის/ღვიძლის/დუბლირების გამო) — შეარჩიეთ უსაფრთხო ანტიკოაგულანტი კონსილიუმით!')
        return _contra_full_oac_postop_label(ctx, oac_partner_lbl)
    has_any_oac_on_file = bool(ctx.has_full_oac or getattr(ctx, 'has_ft_full_oac', False) or getattr(ctx, 'has_ft_parenteral', False) or ctx.has_mech_valve)
    if not has_any_oac_on_file:
        ctx.amber_flags.append('არჩეულია პოსტოპ. OAC + ანტიაგრეგანტი, თუმცა ბარათში მუდმივი ანტიკოაგულანტი (OAC / პარენტერული) მითითებული არ არის — დააზუსტეთ ანტიკოაგულაციის ჩვენება ქირურგთან/კარდიოლოგთან!')
    oac_base = 'ვარფარინი (VKA)' if ctx.has_mech_valve else 'OAC'
    mv_sfx = ' (DOAC აკრძალულია!)' if ctx.has_mech_valve else ''
    if asa_allergy and clop_intol:
        ctx.red_flags.append('არჩეულია პოსტოპ. OAC + ანტიაგრეგანტი, მაგრამ პაციენტს აქვს ასპირინზე და კლოპიდოგრელზე ალერგია — შეათანხმეთ ინდივიდუალური რეჟიმი კონსილიუმით!')
        return f'{oac_base} + {oac_partner_lbl}{mv_sfx}'
    if clop_intol:
        ctx.amber_flags.append('კლოპიდოგრელის აუტანლობის გამო OAC-ის ფონზე გამოიყენეთ ასპირინი 75–100 მგ (OAC + ტიკაგრელორი ESC Class III არ ინიშნება!).')
        return f'{oac_base} + {oac_partner_lbl}{mv_sfx}'
    if asa_allergy:
        return f'{oac_base} + კლოპიდოგრელი 75 მგ (ასპირინზე ალერგია!){mv_sfx}'
    if ctx.has_mech_valve:
        return f'ვარფარინი (VKA) + {oac_partner_lbl} (DOAC აკრძალულია!)'
    return None


def _eval_explicit_triple_therapy(ctx, asa_allergy, voyager_safe, stent_p2y12, oac_partner_lbl, has_full_oac_any):
    if getattr(ctx, 'has_contra_full_oac', False) and not ctx.has_mech_valve:
        ctx.red_flags.append('არჩეულია პოსტოპ. VOYAGER / DAPT, მაგრამ მიმდინარე OAC უკუნაჩვენებია (თირკმლის/ღვიძლის/ორსულობის/დუბლირების გამო) — უკუნაჩვენები OAC არ განაახლოთ, შეარჩიეთ უსაფრთხო სქემა კონსილიუმით!')
        return _contra_full_oac_postop_label(ctx, oac_partner_lbl)
    if ('rivaroxaban_2_5' in ctx.med_chips or bool(ctx.ft_meds.get('has_ft_unstruct_riva_low'))) and not has_full_oac_any and not getattr(ctx, 'has_ft_parenteral', False):
        if not voyager_safe:
            ctx.red_flags.append('რივაროქსაბანი 2.5 მგ ×2 (VOYAGER PAD) უკუნაჩვენებია (ორსულობა / დიალიზი / CrCl / ინსულტი / ღვიძლი ≥3x / სტენტი / ალერგია) და DAPT-თან სამმაგი თერაპია დაუშვებელია — შეწყვიტეთ რივაროქსაბანი 2.5 მგ და შეარჩიეთ უსაფრთხო სქემა!')
            if asa_allergy:
                return 'DAPT და რივაროქსაბანი 2.5 მგ უკუნაჩვენებია ასპირინზე ალერგიისას — შეარჩიეთ უსაფრთხო სქემა ქირურგთან!'
            return f'რივაროქსაბანი 2.5 მგ უკუნაჩვენებია — შეწყვიტეთ რივაროქსაბანი და გამოიყენეთ DAPT (ასპირინი + {stent_p2y12}) რივაროქსაბანის გარეშე!'
        ctx.red_flags.append('არჩეულია პოსტოპ. DAPT პაციენტთან, რომელიც იღებს რივაროქსაბან 2.5 მგ ×2-ს — სამმაგი თერაპიისა და რივაროქსაბანი 2.5 + ტიკაგრელორის სისხლდენის მაღალი რისკი (ESC Class III)! შეარჩიეთ VOYAGER PAD (რივაროქსაბანი 2.5 + ასპირინი) ან შეაჩერეთ რივაროქსაბანი DAPT-ისთვის.')
        return 'DAPT უკუნაჩვენებია რივაროქსაბან 2.5 მგ-თან (სამმაგი თერაპია)! შეცვალეთ: რივაროქსაბანი 2.5 + ასპირინი 75–100 მგ (VOYAGER PAD) ან DAPT რივაროქსაბანის გარეშე!'
    ctx.red_flags.append('არჩეულია პოსტოპ. VOYAGER / DAPT პაციენტთან, რომელიც მუდმივ ანტიკოაგულანტზეა (OAC / პარენტერული) — სამმაგი თერაპიის სისხლდენის მაღალი რისკი! შეცვალეთ ერთიანი ანტიკოაგულაციით + მონო-ანტიაგრეგანტით.')
    if not has_full_oac_any and getattr(ctx, 'has_ft_parenteral', False):
        return f'არჩეული სქემა უკუნაჩვენებია პარენტერულ ანტიკოაგულაციაზე (სამმაგი თერაპია)! შეათანხმეთ: პარენტერული ანტიკოაგულაცია{_parenteral_caveat_suffix(ctx)} + ჩვენებისას {oac_partner_lbl} (ორმაგი ანტიკოაგულაციის გარეშე!)'
    if getattr(ctx, 'eff_pregnant', False):
        return _contra_full_oac_postop_label(ctx, oac_partner_lbl)
    return f'არჩეული სქემა უკუნაჩვენებია ანტიკოაგულანტზე (სამმაგი თერაპია)! შეცვალეთ: OAC + {oac_partner_lbl}'


def _eval_explicit_postop_plan(ctx, post_at_raw, asa_allergy, voyager_safe, alt_p2y12_lbl, oac_partner_lbl, has_active_stent_dapt):
    clop_intol = 'clopidogrel_intol' in ctx.allergy_chips
    stent_p2y12 = getattr(ctx, 'stent_p2y12_lbl', alt_p2y12_lbl)
    has_full_oac_any = bool(ctx.has_full_oac or getattr(ctx, 'has_ft_full_oac', False))
    has_ft_par = bool(getattr(ctx, 'has_ft_parenteral', False))
    label = dict(POST_ANTITHROMBOTIC_CHOICES).get(post_at_raw, '')
    if getattr(ctx, 'active_gi_bleed', False):
        plan_tag = f' ({label})' if label else ''
        ctx.red_flags.append(f'აქტიური კუჭ-ნაწლავის სისხლდენის / მწვავე წყლულის ფონზე არჩეული პოსტოპ. სქემა{plan_tag} უკუნაჩვენებია — სავალდებულოა გასტროენტეროლოგისა და ქირურგის კონსილიუმი ენდოსკოპიურ ჰემოსტაზამდე!')
        return _gi_bleed_postop_msg(ctx)
    if ctx.has_mech_valve and getattr(ctx, 'eff_pregnant', False):
        ren_red = rule_value('REN_CKD_NEPHRO_HYDRATION')['red']
        eff_r_mv = None if getattr(ctx, 'creat_unit_mismatch', False) else getattr(ctx, 'eff_renal_sat', ctx.eff_renal)
        sev_ren_mv = bool(ctx.on_dialysis or (eff_r_mv is not None and eff_r_mv < ren_red))
        ctx.red_flags.append('ორსულობისა და მექანიკური სარქვლის ფონზე პოსტოპ. ანტიკოაგულაციის სქემა სავალდებულოდ უნდა შეთანხმდეს მეან-გინეკოლოგისა და კარდიოლოგის კონსილიუმით (DOAC / VOYAGER აკრძალულია)!')
        return _preg_mech_valve_postop_msg('hit_heparin' in ctx.allergy_chips, severe_renal=sev_ren_mv)
    if ctx.has_mech_valve and post_at_raw in ('voyager', 'dapt', 'sapt'):
        ctx.red_flags.append('მექანიკური სარქვლის მქონე პაციენტთან პოსტოპ. სავალდებულოა უვადო ვარფარინი (VKA) ± მონო-ანტიაგრეგანტი — VOYAGER / მონო-SAPT დაუშვებელია!')
        mv_ap = f' + {oac_partner_lbl} (კორონარული სტენტის დაცვა — ESC Class I!)' if has_active_stent_dapt else (f' + ჩვენებისას {oac_partner_lbl}' if asa_allergy else ' + ჩვენებისას ასპირინი 75–100 მგ')
        return f'არჩეული სქემა უკუნაჩვენებია მექ. სარქველზე! უვადო ვარფარინი (VKA){mv_ap} (DOAC / VOYAGER აკრძალულია!)'
    if post_at_raw == 'sapt' and (has_full_oac_any or has_ft_par):
        if not has_full_oac_any and has_ft_par:
            ctx.red_flags.append('პაციენტი იღებს აქტიურ პარენტერულ ანტიკოაგულანტს — პოსტოპ. მხოლოდ მონო-ანტიაგრეგანტზე (SAPT) ჩამოქვეითება ანტიკოაგულაციის შეთანხმების გარეშე საშიშია! შეათანხმეთ ერთიანი ანტიკოაგულაციური სქემა.')
            return f'SAPT არასაკმარისია პარენტერულ ანტიკოაგულაციაზე! შეათანხმეთ: პარენტერული ანტიკოაგულაცია{_parenteral_caveat_suffix(ctx)} + ჩვენებისას {oac_partner_lbl} (ორმაგი ანტიკოაგულაციის გარეშე!)'
        if getattr(ctx, 'eff_pregnant', False):
            ctx.red_flags.append('ორსულობის ფონზე არჩეულია პოსტოპ. SAPT, მაგრამ პაციენტს აქვს ანტიკოაგულაციის ჩვენება და პერორალური OAC უკუნაჩვენებია — შეარჩიეთ უსაფრთხო არაორალური ანტიკოაგულაცია კონსილიუმით!')
            return _contra_full_oac_postop_label(ctx, oac_partner_lbl)
        ctx.red_flags.append('პაციენტი მუდმივ პერორალურ ანტიკოაგულანტზეა (OAC) — პოსტოპ. მონო-ანტიაგრეგანტზე (SAPT) ჩამოქვეითება ანტიკოაგულაციის გარეშე საშიშია! შეარჩიეთ OAC + მონო-ანტიაგრეგანტი.')
        if getattr(ctx, 'has_contra_full_oac', False) and not ctx.has_mech_valve:
            return _contra_full_oac_postop_label(ctx, oac_partner_lbl)
        return f'SAPT უკუნაჩვენებია OAC-ზე! შეცვალეთ: OAC + {oac_partner_lbl}'
    if (post_at_raw in ('voyager', 'dapt') and (has_full_oac_any or has_ft_par)) or (post_at_raw == 'dapt' and ('rivaroxaban_2_5' in ctx.med_chips or bool(ctx.ft_meds.get('has_ft_unstruct_riva_low')))):
        return _eval_explicit_triple_therapy(ctx, asa_allergy, voyager_safe, stent_p2y12, oac_partner_lbl, has_full_oac_any)
    if post_at_raw == 'voyager' and not voyager_safe:
        ctx.red_flags.append(
            'პოსტოპ. VOYAGER PAD (რივაროქსაბანი 2.5 მგ ×2) უკუნაჩვენებია/საშიშია (დიალიზი, CrCl/eGFR უცნობი ან დაბალი, მექ. სარქველი, AFib, სტენტი/DAPT, ინსულტი, წყლული, ორსულობა, ღვიძლის 3x ან ასპირინზე ალერგია) — შეარჩიეთ ალტერნატივა!'
        )
        if has_active_stent_dapt:
            return (
                f'VOYAGER PAD უკუნაჩვენებია — ახალი სტენტი + ასპირინზე ალერგია: {stent_p2y12} / კარდიოლოგის კონსილიუმი!'
                if asa_allergy else
                f'VOYAGER PAD უკუნაჩვენებია — შეინარჩუნეთ უწყვეტი DAPT (ასპირინი + {stent_p2y12} — კორონარული სტენტის დაცვა!)'
            )
        alt_v = f'{alt_p2y12_lbl} / ალტერნატივა კონსილიუმით' if asa_allergy else f'{alt_p2y12_lbl} ან ასპირინი 100 მგ'
        return f'VOYAGER PAD უკუნაჩვენებია — შეარჩიეთ {alt_v}'
    if post_at_raw == 'dapt' and asa_allergy:
        ctx.red_flags.append('არჩეულია პოსტოპ. DAPT (ასპირინი + P2Y12), მაგრამ პაციენტს აქვს ასპირინზე / აასს-ზე ალერგია — შეცვალეთ უსაფრთხო სქემით!')
        return f'DAPT (ასპირინით) უკუნაჩვენებია — ასპირინზე/აასს-ზე ალერგია! შეარჩიეთ {stent_p2y12} / კონსილიუმი'
    if post_at_raw == 'sapt' and has_active_stent_dapt:
        st_cfg_e = rule_value('MED_RECENT_STENT_DAPT')
        st_win_e = f'{st_cfg_e["elective_months"]}–{st_cfg_e["acs_months"]}'
        if asa_allergy:
            ctx.red_flags.append(f'ახალი კორონარული სტენტის (<{st_win_e} თვ) + ასპირინზე ალერგიის ფონზე SAPT/სტენტის დაცვა შეათანხმეთ კარდიოლოგის კონსილიუმით (უწყვეტი P2Y12 / დესენსიბილიზაცია)!')
            return f'SAPT უკუნაჩვენებია ახალ სტენტზე (<{st_win_e} თვ) + ასპირინზე ალერგია: {stent_p2y12} — კარდიოლოგის კონსილიუმი!'
        ctx.red_flags.append(f'ახალი კორონარული სტენტის (<{st_win_e} თვ) ფონზე პოსტოპ. მონოთერაპიაზე (SAPT) ჩამოქვეითება საშიშია — შეინარჩუნეთ DAPT!')
        return f'SAPT უკუნაჩვენებია ახალ სტენტზე (<{st_win_e} თვ) — შეინარჩუნეთ უწყვეტი DAPT (ასპირინი + {stent_p2y12})!'
    if post_at_raw == 'sapt' and asa_allergy and clop_intol:
        ctx.red_flags.append('არჩეულია პოსტოპ. SAPT (ასპირინი/კლოპიდოგრელი), მაგრამ პაციენტს აქვს ასპირინზე და კლოპიდოგრელზე ალერგია — შეათანხმეთ ალტერნატივა კარდიოლოგთან!')
        return f'სტანდარტული SAPT უკუნაჩვენებია (ასპირინზე და კლოპიდოგრელზე ალერგია!) — {alt_p2y12_lbl}'
    if post_at_raw == 'sapt' and asa_allergy:
        return 'მონოთერაპია: კლოპიდოგრელი 75 მგ (ასპირინზე ალერგია!)'
    if post_at_raw == 'sapt' and clop_intol:
        return 'მონოთერაპია: ასპირინი 100 მგ (კლოპიდოგრელზე აუტანლობა!)'
    if post_at_raw == 'dapt' and clop_intol:
        if getattr(ctx, 'tica_blocked', False):
            ctx.red_flags.append('არჩეულია პოსტოპ. DAPT კლოპიდოგრელის აუტანლობის ფონზე, მაგრამ ტიკაგრელორი უკუნაჩვენებია (ღვიძლის ≥3x / ორსულობა / OAC) — შეათანხმეთ უსაფრთხო რეჟიმი კონსილიუმით!')
            return f'DAPT: ასპირინი + {alt_p2y12_lbl}'
        ctx.amber_flags.append('კლოპიდოგრელის აუტანლობის გამო პოსტოპ. DAPT სქემაში გამოიყენეთ ალტერნატიული P2Y12 (ტიკაგრელორი).')
        return 'DAPT: ასპირინი + ტიკაგრელორი (კლოპიდოგრელზე აუტანლობა!)'
    if post_at_raw == 'oac_sapt':
        res_oac = _eval_explicit_oac_sapt(ctx, asa_allergy, clop_intol, oac_partner_lbl)
        if res_oac is not None:
            return res_oac
    return label


def _eval_postop_voyager(ctx):
    pathway, q, s = ctx.pathway, ctx.q, ctx.s
    voy_cfg = rule_value('MED_VOYAGER_RIVAROXABAN_2_5')
    post_at_raw = s.get('post_antithrombotic_plan') or 'auto'
    post_antithrombotic_label = ''
    asa_allergy = 'nsaid' in ctx.allergy_chips
    active_gi_bleed = getattr(ctx, 'active_gi_bleed', False)
    eff_ren_sat = getattr(ctx, 'eff_renal_sat', None)
    has_full_oac_any = bool(ctx.has_full_oac or getattr(ctx, 'has_ft_full_oac', False))
    has_ft_par = bool(getattr(ctx, 'has_ft_parenteral', False))
    ft_u_keys = ctx.ft_meds.get('unstructured_keys') or set()
    voy_renal_ok = (
        (ctx.egfr is not None or ctx.crcl is not None)
        and not ctx.creat_unit_ambiguous and not getattr(ctx, 'creat_unit_mismatch', False)
        and (ctx.egfr is None or ctx.egfr >= voy_cfg['min_egfr'])
        and (ctx.crcl is None or ctx.crcl >= voy_cfg['min_egfr'])
        and (eff_ren_sat is None or eff_ren_sat >= voy_cfg['min_egfr'])
    )
    voyager_safe = (
        voy_renal_ok and not ctx.on_dialysis and not active_gi_bleed
        and not getattr(ctx, 'has_prior_stroke_tia', False) and not getattr(ctx, 'eff_pregnant', False)
        and not ctx.liver_3x and not has_full_oac_any and not has_ft_par and not ctx.has_mech_valve and not ctx.has_afib
        and not q.get('med_ticagrelor') and not q.get('med_prasugrel')
        and not bool(ft_u_keys & {'ticagrelor', 'prasugrel'})
        and not q.get('recent_stent') and not ctx.has_acs_stent_12m and not asa_allergy
    )
    ctx.voyager_safe = voyager_safe
    tica_blocked = bool(ctx.liver_3x or getattr(ctx, 'eff_pregnant', False) or has_full_oac_any or has_ft_par)
    ctx.tica_blocked = tica_blocked
    if 'clopidogrel_intol' in ctx.allergy_chips:
        alt_p2y12_lbl = 'ინდივიდუალური რეჟიმი კონსილიუმით (P2Y12 აუტანლობა/უკუჩვენება!)' if tica_blocked else 'ტიკაგრელორი'
    else:
        alt_p2y12_lbl = 'კლოპიდოგრელი 75 მგ'
    age_pr = ctx.age if ctx.age is not None else (getattr(ctx, 'age_sat', None) or 0)
    wt_pr = getattr(ctx, 'weight_sat', ctx.weight_num)
    pras_bw = (age_pr >= 75) or (wt_pr is not None and wt_pr < 60)
    if q.get('med_ticagrelor') and not tica_blocked:
        stent_p2y12_lbl = 'ტიკაგრელორი'
    elif q.get('med_prasugrel') and not getattr(ctx, 'has_prior_stroke_tia', False) and not ctx.liver_3x and not getattr(ctx, 'eff_pregnant', False):
        stent_p2y12_lbl = f'{alt_p2y12_lbl} — პრასუგრელის დე-ესკალაციით კარდიოლოგთან (FDA Boxed Warning)!' if pras_bw else 'პრასუგრელი'
    else:
        stent_p2y12_lbl = alt_p2y12_lbl
    ctx.stent_p2y12_lbl = stent_p2y12_lbl
    if 'clopidogrel_intol' in ctx.allergy_chips:
        oac_partner_lbl = (
            'ინდივიდუალური რეჟიმი კონსილიუმით (ასპირინზე და კლოპიდოგრელზე ალერგია!)'
            if asa_allergy else
            'ასპირინი 75–100 მგ (OAC + ტიკაგრელორი ESC Class III სისხლდენის რისკის გამო არ ინიშნება — შეათანხმეთ კონსილიუმით!)'
        )
    else:
        oac_partner_lbl = 'კლოპიდოგრელი 75 მგ'
    has_active_stent_dapt = bool(q.get('recent_stent') or ctx.has_acs_stent_12m)
    ctx.alt_p2y12_lbl, ctx.oac_partner_lbl, ctx.has_active_stent_dapt = alt_p2y12_lbl, oac_partner_lbl, has_active_stent_dapt

    if pathway in (PreopPatient.Pathway.ABF, PreopPatient.Pathway.PAD_BYPASS, PreopPatient.Pathway.PAD_PTA):
        if post_at_raw != 'auto':
            post_antithrombotic_label = _eval_explicit_postop_plan(
                ctx, post_at_raw, asa_allergy, voyager_safe, alt_p2y12_lbl, oac_partner_lbl, has_active_stent_dapt,
            )
        elif active_gi_bleed:
            post_antithrombotic_label = _gi_bleed_postop_msg(ctx)
        elif ctx.has_mech_valve and getattr(ctx, 'eff_pregnant', False):
            ren_red_mv = rule_value('REN_CKD_NEPHRO_HYDRATION')['red']
            eff_ren_mv = None if getattr(ctx, 'creat_unit_mismatch', False) else getattr(ctx, 'eff_renal_sat', ctx.eff_renal)
            sev_ren_mv = ctx.on_dialysis or (eff_ren_mv is not None and eff_ren_mv < ren_red_mv)
            post_antithrombotic_label = _preg_mech_valve_postop_msg('hit_heparin' in ctx.allergy_chips, severe_renal=sev_ren_mv)
        elif ctx.has_mech_valve:
            mv_ap = f' + {oac_partner_lbl} (კორონარული სტენტის დაცვა — ESC Class I!)' if has_active_stent_dapt else (f' + ჩვენებისას {oac_partner_lbl}' if asa_allergy else ' + ჩვენებისას ასპირინი 75–100 მგ')
            post_antithrombotic_label = f'უვადო ვარფარინი (VKA) კარდიოლოგის სამიზნე INR-ით{mv_ap} (DOAC / VOYAGER აკრძალულია!)'
        elif getattr(ctx, 'has_contra_full_oac', False):
            post_antithrombotic_label = _contra_full_oac_postop_label(ctx, oac_partner_lbl)
        elif has_full_oac_any:
            post_antithrombotic_label = f'OAC + {oac_partner_lbl} (სამმაგი თერაპიის გარეშე — ESC/ESVS)'
        elif has_ft_par:
            post_antithrombotic_label = f'აქტიური პარენტერული ანტიკოაგულაცია{_parenteral_caveat_suffix(ctx)} + ჩვენებისას {oac_partner_lbl} (ერთიანი სქემა ჰემოსტაზით — დამატებითი VOYAGER / პროფილაქტიკური LMWH არ დაუმატოთ!)'
        elif has_active_stent_dapt:
            st_lbl = f'{stent_p2y12_lbl} (ასპირინზე ალერგია — კარდიოლოგის კონსილიუმი!)' if asa_allergy else f'ასპირინი 100 მგ + {stent_p2y12_lbl}'
            post_antithrombotic_label = f'უწყვეტი DAPT: {st_lbl} (კორონარული სტენტის დაცვა — მონოთერაპიაზე გადაყვანა აკრძალულია!)'
        elif asa_allergy:
            post_antithrombotic_label = f'მონო-ანტიაგრეგანტი ({alt_p2y12_lbl} — ასპირინზე/აასს-ზე ალერგია!)'
        elif ctx.is_pta:
            if voyager_safe:
                post_antithrombotic_label = f'DAPT (ასპირინი + {alt_p2y12_lbl} 1–3 თვე) ან VOYAGER PAD (რივაროქსაბანი 2.5 მგ ×2 + ასპირინი)'
            else:
                post_antithrombotic_label = f'DAPT (ასპირინი + {alt_p2y12_lbl} 1–3 თვე; VOYAGER შეზღუდულია)'
        elif voyager_safe:
            post_antithrombotic_label = 'VOYAGER PAD (რივაროქსაბანი 2.5 მგ ×2 + ასპირინი 100 მგ — ACC/AHA 2024 Class I) ან SAPT'
        else:
            post_antithrombotic_label = f'მონო-ანტიაგრეგანტი ({alt_p2y12_lbl} ან ასპირინი 100 მგ)'
    ctx.post_antithrombotic_label = post_antithrombotic_label
