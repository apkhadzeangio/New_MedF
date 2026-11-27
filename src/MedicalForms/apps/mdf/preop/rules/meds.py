"""Medication safety, allergy prophylaxis switches, HIT, CIED, mechanical valve, VOYAGER PAD & DAPT rules."""
from ...models import PreopPatient
from ..calculators import _s_raw, _sat_num
from ..constants import POST_ANTITHROMBOTIC_CHOICES
from ..meds import build_med_schedule
from ..registry import rule_value
from ..utils import _date, _num, _safe_display_str, has_prior_stroke_or_tia, is_pregnancy_eligible


def _eval_allergies_and_cied(ctx):
    q, s = ctx.q, ctx.s
    abx_prophylaxis_note = ''
    if 'hit_heparin' in ctx.allergy_chips:
        ctx.red_flags.append(
            'საეჭვოა ჰეპარინ-ინდუცირებული თრომბოციტოპენია (HIT) — არაფრაქციული ჰეპარინი და LMWH უკუნაჩვენებია! '
            'სასწრაფოდ შეათანხმეთ ალტერნატიული ანტიკოაგულაცია ქირურგთან!'
        )
    if 'beta_lactam' in ctx.allergy_chips:
        if getattr(ctx, 'has_vancomycin_allergy', False):
            abx_prophylaxis_note = 'β-ლაქტამსა და ვანკომიცინზე ალერგია: პროფილაქტიკური ცეფაზოლინი და ვანკომიცინი უკუნაჩვენებია — გამოიყენეთ კლინდამიცინი 900 მგ IV!'
        else:
            abx_prophylaxis_note = 'β-ლაქტამზე ალერგია: პროფილაქტიკური ცეფაზოლინის ნაცვლად გამოიყენება ვანკომიცინი ან კლინდამიცინი!'
        ctx.amber_flags.append(abx_prophylaxis_note)
    elif 'penicillin_mild' in ctx.allergy_chips:
        ctx.amber_flags.append('მსუბუქი არა-ანაფილაქსიური გამონაყარი პენიცილინზე — AAAAI 2022 გაიდლაინით ცეფაზოლინი უსაფრთხოდ დასაშვებია.')
    if getattr(ctx, 'has_vancomycin_allergy', False) and 'beta_lactam' not in ctx.allergy_chips:
        ctx.amber_flags.append('ალერგია ვანკომიცინზე — ვანკომიცინის გამოყენებას მოერიდეთ (გამოიყენეთ ცეფაზოლინი ან კლინდამიცინი)!')
    if 'latex_chlorhex' in ctx.allergy_chips:
        if ctx.eff_allergy_iodine:
            ctx.amber_flags.append(
                'ალერგია ლატექსზე / ქლორჰექსიდინზე და იოდზე — გამოიყენეთ ულატექსო გარემო და უიოდო ალტერნატიული ანტისეპტიკი (ოქტენიდინი/სპირტი — ალერგოლოგთან შეთანხმებით)!'
            )
        else:
            ctx.amber_flags.append('ალერგია ლატექსზე / ქლორჰექსიდინზე — გამოიყენეთ ულატექსო ხელთათმანები/კათეტერი და პოვიდონ-იოდის ანტისეპტიკი!')
    if ctx.unclarified_allergy_other:
        ctx.amber_flags.append('დაფიქსირებულია დაუზუსტებელი მედიკამენტური ალერგია — ცეფაზოლინის შეყვანამდე დააზუსტეთ ალერგენი!')
    raw_other_meds_disp = _safe_display_str(q.get('other_meds'), max_len=160)
    if ctx.ft_meds['unstructured_labels']:
        ctx.red_flags.append(
            f"თავისუფალ ტექსტში („{raw_other_meds_disp}“) მითითებულია მაღალი რისკის პრეპარატი ({', '.join(ctx.ft_meds['unstructured_labels'])}), "
            'რომელიც სტრუქტურულ ჩიპებში მონიშნული არ არის — სასწრაფოდ დააზუსტეთ შეჩერების სქემისთვის!'
        )
    if ctx.liver_3x and ctx.ft_meds.get('has_ft_statin') and 'statin' not in ctx.med_chips:
        ctx.red_flags.append(
            f'თავისუფალ ტექსტში („{raw_other_meds_disp}“) მითითებულია სტატინი ღვიძლის ფერმენტების ≥3x მატების ფონზე — '
            'ჰეპატოტოქსიკურობის გამო სტატინი უკუნაჩვენებია, სასწრაფოდ შეწყვიტეთ და შეათანხმეთ ექიმთან!'
        )
    eff_ren_al = None if getattr(ctx, 'creat_unit_mismatch', False) else getattr(ctx, 'eff_renal_sat', ctx.eff_renal)
    ren_red_al = rule_value('REN_CKD_NEPHRO_HYDRATION')['red']
    if ctx.ft_meds.get('has_ft_nsaid') and (
        'nsaid' in ctx.allergy_chips or getattr(ctx, 'active_gi_bleed', False)
        or ctx.on_dialysis or (eff_ren_al is not None and eff_ren_al < ren_red_al)
        or getattr(ctx, 'eff_pregnant', False)
    ):
        ctx.red_flags.append(
            f'თავისუფალ ტექსტში („{raw_other_meds_disp}“) მითითებულია აასს / NSAID — უკუნაჩვენებია ალერგიის, GI სისხლდენის, თირკმლის უკმარისობის ან ორსულობის ფონზე; '
            'დაუყოვნებლივ შეწყვიტეთ და შეათანხმეთ ექიმთან!'
        )
    ctx.abx_prophylaxis_note = abx_prophylaxis_note
    ctx.needs_contrast_premed = ctx.eff_allergy_iodine and ctx.uses_contrast
    if ctx.eff_allergy_iodine and not ctx.is_venous:
        if ctx.uses_contrast:
            ctx.reflex_orders.append({
                'key': 'contrast_premed',
                'trigger': 'იოდზე / კონტრასტზე ალერგია ანამნეზში',
                'action': 'კონტრასტის წინასწარი პრემედიკაციის სქემა (სტეროიდი + H1-ბლოკატორი) და შეთანხმება ქირურგთან',
                'done': bool(s.get('contrast_premed_done')),
            })
            if not s.get('contrast_premed_done'):
                ctx.amber_flags.append('ანამნეზში ალერგია იოდზე / კონტრასტზე — ჩართულია პრემედიკაციის რეფლექს-პროტოკოლი (სტეროიდი + ანტიჰისტამინი).')
        else:
            ctx.amber_flags.append('ანამნეზში ალერგია იოდზე / კონტრასტზე.')
    ctx.needs_cied = bool(q.get('pacemaker'))
    ctx.cied_ok = bool(s.get('cied_checked')) or ((s.get('cied_plan') or 'pending') != 'pending')
    if ctx.needs_cied:
        ctx.reflex_orders.append({
            'key': 'cied',
            'trigger': 'იმპლანტირებული კარდიოსტიმულატორი / დეფიბრილატორი (CIED)',
            'action': 'მოწყობილობის პასპორტი/ბოლო შემოწმება, მაგნიტის რეჟიმი და ბიპოლარული კოაგულაციის დაგეგმვა კარდიოლოგ/ანესთეზიოლოგთან',
            'done': ctx.cied_ok,
        })


def _eval_allergy_cied_and_med_hold(ctx):
    patient, q, s, q_filled = ctx.patient, ctx.q, ctx.s, ctx.q_filled
    metf_cfg = rule_value('MED_METFORMIN_RENAL_CONTRAST')
    _eval_allergies_and_cied(ctx)

    has_crit_q_meds = bool(
        q.get('med_warfarin') or q.get('med_doac') or q.get('med_dabigatran')
        or q.get('med_clopidogrel') or q.get('med_ticagrelor') or q.get('med_prasugrel')
        or q.get('med_aspirin') or ('rivaroxaban_2_5' in ctx.med_chips) or ('cilostazol' in ctx.med_chips)
        or (ctx.med_chips & {'beta_blocker', 'acei_arb', 'ccb', 'steroid', 'amiodarone', 'diuretic'})
        or q.get('metformin') or q.get('sglt2') or q.get('glp1') or q.get('dm_insulin')
        or q.get('ven_hormones') or q.get('recent_stent') or ctx.has_acs_stent_12m
        or ctx.ft_meds['unstructured_labels'] or ctx.ft_meds.get('has_ft_nsaid')
        or ctx.ft_meds.get('has_ft_acei_arb') or q.get('self_filled') or q.get('self_fill_submitted')
    )
    ctx.med_schedule = build_med_schedule(
        patient, q, s, ctx.egfr, crcl=ctx.crcl,
        has_neuraxial_override=ctx.has_neuraxial_plan, today=ctx.today,
    ) if (q_filled or has_crit_q_meds) else []
    clop_needs_hold = bool(q.get('med_clopidogrel') and not ctx.is_pta and not ctx.is_symp_cea)
    is_evla_only = (
        ctx.is_venous and patient.op_subtype == PreopPatient.OpSubtype.EVLA
        and not ctx.has_neuraxial_plan and ctx.anesth_plan_raw not in ('general', 'plexus', 'sedation')
        and not bool(q.get('ven_lidocaine_allergy') or 'lidocaine' in ctx.allergy_chips)
    )
    is_ia_first_pass = patient.op_subtype in ('evar', 'tbd')
    eff_ren_m = None if getattr(ctx, 'creat_unit_mismatch', False) else getattr(ctx, 'eff_renal_sat', ctx.eff_renal)
    metf_contra_any = bool(q.get('metformin')) and (ctx.on_dialysis or (eff_ren_m is not None and eff_ren_m < metf_cfg['contra_renal']) or ctx.liver_3x)
    metf_hold_48h = bool(q.get('metformin')) and (
        is_ia_first_pass
        or (ctx.uses_contrast and (eff_ren_m is None or eff_ren_m < metf_cfg['contrast_hold_renal']))
        or ctx.open_major
        or (not is_evla_only and eff_ren_m is not None and eff_ren_m < metf_cfg['dose_cap_renal'])
    )
    ctx.metf_contra_any = metf_contra_any
    phleb_asa_hold = bool(
        ctx.is_phleb and q.get('med_aspirin')
        and any('ასპირინი' in r.get('drug', '') and r.get('hold_or_skip') for r in ctx.med_schedule)
    )
    has_red_or_contra_med = any(r.get('contraindicated') or r.get('tone') == 'red' or r.get('hold_or_skip') for r in ctx.med_schedule)
    ctx.needs_med_hold = (q_filled or has_crit_q_meds) and (
        clop_needs_hold or bool(q.get('med_ticagrelor') and not ctx.is_pta) or bool(q.get('med_prasugrel'))
        or bool(q.get('med_doac')) or bool(q.get('med_dabigatran')) or bool(q.get('med_warfarin'))
        or ('cilostazol' in ctx.med_chips) or ('rivaroxaban_2_5' in ctx.med_chips)
        or bool(q.get('glp1')) or bool(q.get('sglt2')) or bool(q.get('dm_insulin'))
        or bool(q.get('metformin')) or bool(q.get('ven_hormones')) or phleb_asa_hold
        or metf_contra_any or metf_hold_48h or bool(ctx.ft_meds['unstructured_labels']) or has_red_or_contra_med
    )
    if (q_filled or has_crit_q_meds) and metf_contra_any:
        ctx.red_flags.append(
            f'მეტფორმინი უკუნაჩვენებია თირკმლის მძიმე უკმარისობის (CrCl/eGFR <{metf_cfg["contra_renal"]} / დიალიზი) ან ღვიძლის ≥3x დისფუნქციის დროს '
            '(ლაქტატ-აციდოზის რისკი) — შეწყვიტეთ დაუყოვნებლივ და შეათანხმეთ ენდოკრინოლოგთან!'
        )
    elif (q_filled or has_crit_q_meds) and q.get('metformin') and not ctx.uses_contrast and eff_ren_m is not None and eff_ren_m < metf_cfg['dose_cap_renal']:
        hold_note = f' და ოპერაციაზე {metf_cfg["hold_hours"]} სთ შეჩერება კრეატინინის კონტროლით' if metf_hold_48h else ''
        ren_d = ctx.eff_renal if ctx.eff_renal is not None else int(round(eff_ren_m))
        ctx.amber_flags.append(
            f'მეტფორმინი CrCl/eGFR {ren_d} ({metf_cfg["contra_renal"]}–{metf_cfg["dose_cap_renal"] - 1}) ფონზე: დღიური დოზა ≤1000 მგ{hold_note}.'
        )


def _eval_cilo_voyager_and_liver_contra(ctx, cilo_min_crcl, voy_min_egfr):
    q, s = ctx.q, ctx.s
    cilo_cfg = rule_value('MED_CILOSTAZOL_BLACKBOX')
    doac_low = rule_value('MED_DOAC_HOLD_INTERVALS')['crcl_low']
    dabi_low = rule_value('MED_DABIGATRAN_HOLD_INTERVALS')['crcl_low']
    eff_ren_c = None if getattr(ctx, 'creat_unit_mismatch', False) else getattr(ctx, 'eff_renal_sat', ctx.eff_renal)
    lvef_early, bnp_early = _sat_num(_s_raw(s, 'lvef'), 5, 95), _sat_num(_s_raw(s, 'nt_probnp'), 1, 100000)
    hr_eff = getattr(ctx, 'hr_sat', ctx.hr)
    repro_preg = is_pregnancy_eligible(ctx.patient) and (bool(q.get('ven_pregnant')) or bool(q.get('pregnant_possible')))
    cea_bleed = bool(ctx.pathway == PreopPatient.Pathway.CEA and (s.get('brain_imaging') == 'large_or_hemorrhage' or getattr(ctx, 'active_thrombolysis_window', False)))
    cilostazol_contra = bool(
        cea_bleed or ctx.chf_hyd_flag or (lvef_early is not None and lvef_early < cilo_cfg['lvef_min'])
        or (bnp_early is not None and bnp_early >= cilo_cfg['bnp_max']) or ctx.on_dialysis
        or (eff_ren_c is not None and eff_ren_c <= cilo_min_crcl)
        or ((s.get('ecg') or 'pending') == 'ischemia_arrhythmia') or (hr_eff is not None and hr_eff > cilo_cfg['hr_max'])
        or ctx.liver_3x or (bool(q.get('ulcer_active_symp')) and s.get('gastro_result') != 'remission') or (s.get('gastro_result') == 'acute_ulcer')
        or bool(q.get('recent_stent')) or ctx.has_acs_stent_12m or repro_preg
    )
    if 'cilostazol' in ctx.med_chips and cilostazol_contra:
        ctx.red_flags.append(f'ცილოსტაზოლი უკუნაჩვენებია გულის უკმარისობის / LVEF <{cilo_cfg["lvef_min"]}% / CrCl ≤{cilo_min_crcl} / ღვიძლის ≥3x / სტენტის / წყლულის / ტაქიარითმიის / სისხლჩაქცევის დროს (FDA Black Box / EMA) — შეათანხმეთ მოხსნა!')
    ven_dvt_set = set(q.get('ven_dvt_chips') or [])
    if q.get('ven_hormones') and (ctx.liver_3x or ('vte_recent_3m' in ven_dvt_set) or (s.get('ven_duplex_status') == 'acute_dvt') or repro_preg):
        if repro_preg:
            ctx.red_flags.append('ორსულობის დროს ჰორმონული/ესტროგენული პრეპარატი უკუნაჩვენებია — დაუყოვნებლივ შეწყვიტეთ და შეათანხმეთ მეან-გინეკოლოგთან!')
        else:
            ctx.red_flags.append('ჰორმონული/ესტროგენული პრეპარატი უკუნაჩვენებია ღვიძლის მძიმე დისფუნქციის (≥3x ULN) ან ახალი/მწვავე VTE (<3 თვე / DVT) ფონზე — დაუყოვნებლივ შეათანხმეთ შეწყვეტა ექიმთან!')
    if ctx.on_dialysis and q.get('med_dabigatran'):
        ctx.red_flags.append('დაბიგატრანი (პრადაქსა) უკუნაჩვენებია პროგრამული დიალიზის ფონზე (EHRA 2021) — სავალდებულოა შეჩერება, თრომბინის დროის (TT) კონტროლი და კარდიოლოგ/ნეფროლოგის კონსილიუმი!')
    elif eff_ren_c is not None and eff_ren_c < dabi_low and q.get('med_dabigatran'):
        ctx.red_flags.append(f'დაბიგატრანი (პრადაქსა) CrCl <{dabi_low} ფონზე უკუნაჩვენებია (EMA / EHRA 2021) — სავალდებულოა შეჩერება ≥120 სთ, თრომბინის დროის (TT) კონტროლი და კარდიოლოგ/ნეფროლოგთან ჩანაცვლება!')
    if ctx.on_dialysis and q.get('med_doac'):
        ctx.red_flags.append('DOAC პროგრამული დიალიზის ფონზე — პრეპარატის აკუმულაციისა და სისხლდენის მაღალი რისკი (EHRA 2021): სავალდებულოა ≥96–120 სთ შეჩერება, ანტი-Xa კონტროლი და ნეფროლოგ/ქირურგის კონსილიუმი!')
    elif eff_ren_c is not None and eff_ren_c < doac_low and q.get('med_doac'):
        ctx.red_flags.append(f'DOAC (CrCl <{doac_low}) ფონზე პრეპარატის აკუმულაციისა და სისხლდენის მაღალი რისკი (EHRA 2021): სავალდებულოა ≥96 სთ შეჩერება, ნეირაქსიული ბლოკადის აკრძალვა და ნეფროლოგ/ქირურგის კონსილიუმი!')
    if 'rivaroxaban_2_5' in ctx.med_chips and (ctx.on_dialysis or (eff_ren_c is not None and eff_ren_c < voy_min_egfr)):
        ctx.red_flags.append(f'რივაროქსაბანი 2.5 მგ ×2 (VOYAGER PAD) უკუნაჩვენებია/საშიშია თირკმლის მძიმე უკმარისობისას (CrCl/eGFR <{voy_min_egfr} ან დიალიზი) — გადაამოწმეთ სქემა!')
    if 'rivaroxaban_2_5' in ctx.med_chips and 'nsaid' in ctx.allergy_chips:
        ctx.red_flags.append('რივაროქსაბანი 2.5 მგ ×2 (VOYAGER PAD) მოითხოვს ასპირინთან კომბინაციას, მაგრამ პაციენტს აქვს ასპირინზე / აასს-ზე ალერგია — დაუყოვნებლივ შეცვალეთ სქემა ქირურგთან!')
    active_gi_bleed = getattr(ctx, 'active_gi_bleed', False)
    if 'rivaroxaban_2_5' in ctx.med_chips and active_gi_bleed:
        ctx.red_flags.append('რივაროქსაბანი 2.5 მგ ×2 (VOYAGER PAD) აბსოლუტურად უკუნაჩვენებია აქტიური კუჭ-ნაწლავის სისხლდენის / მწვავე წყლულის დროს — შეწყვიტეთ დაუყოვნებლივ და შეათანხმეთ ქირურგთან!')
    if 'rivaroxaban_2_5' in ctx.med_chips and getattr(ctx, 'has_prior_stroke_tia', False):
        ctx.red_flags.append('რივაროქსაბანი 2.5 მგ ×2 (VOYAGER PAD) უკუნაჩვენებია გადატანილი ინსულტის / TIA-ს ანამნეზში (ქალასშიდა სისხლდენის რისკი) — გადაამოწმეთ სქემის შეცვლა ქირურგთან!')
    has_hit_preg = 'hit_heparin' in ctx.allergy_chips
    ren_red_preg = rule_value('REN_CKD_NEPHRO_HYDRATION')['red']
    severe_renal_preg = ctx.on_dialysis or (eff_ren_c is not None and eff_ren_c < ren_red_preg)
    if has_hit_preg:
        preg_alt_lbl = 'არაჰეპარინული ალტერნატივით (HIT: LMWH/UFH აკრძალულია!) ჰემატოლოგთან და მეან-გინეკოლოგთან!'
    elif severe_renal_preg:
        preg_alt_lbl = 'არასფრაქციული ჰეპარინით (UFH — თირკმლის მძიმე უკმარისობის/დიალიზის გამო LMWH კუმულირდება!) მეან-გინეკოლოგთან და კარდიოლოგთან!'
    else:
        preg_alt_lbl = 'LMWH/UFH-ით მეან-გინეკოლოგთან და კარდიოლოგთან!'
    if repro_preg and (q.get('med_doac') or q.get('med_dabigatran') or 'rivaroxaban_2_5' in ctx.med_chips):
        ctx.red_flags.append(f'ორსულობის დროს DOAC / დაბიგატრანი / რივაროქსაბანი 2.5 მგ აბსოლუტურად უკუნაჩვენებია (პლაცენტური გადასვლა / ნაყოფის რისკი) — დაუყოვნებლივ შეცვალეთ {preg_alt_lbl}')
    if repro_preg and q.get('med_warfarin'):
        if 'mech_valve' in ctx.cad_chips:
            if has_hit_preg:
                ctx.red_flags.append('ორსულობა + მექანიკური სარქველი + HIT ვარფარინის ფონზე (ტერატოგენული vs სარქვლის თრომბოზი; UFH, LMWH და DOAC აკრძალულია!) — თვითნებურად არ შეწყვიტოთ, დაუყოვნებლივ ჩართეთ მეან-გინეკოლოგის, კარდიოლოგისა და ჰემატოლოგის კონსილიუმი!')
            else:
                ctx.red_flags.append('ორსულობა + მექანიკური სარქველი ვარფარინის ფონზე (ტერატოგენული vs სარქვლის თრომბოზის რისკი) — თვითნებურად არ შეწყვიტოთ, დაუყოვნებლივ ჩართეთ მეან-გინეკოლოგისა და კარდიოლოგის კონსილიუმი!')
        else:
            ctx.red_flags.append(f'ორსულობის დროს ვარფარინი (VKA) ტერატოგენულია (ნაყოფის ვარფარინის სინდრომი) — დაუყოვნებლივ შეცვალეთ {preg_alt_lbl}')
    if repro_preg and ('statin' in ctx.med_chips or ctx.ft_meds.get('has_ft_statin')):
        ctx.red_flags.append('ორსულობის დროს სტატინი უკუნაჩვენებია (ტერატოგენული რისკი) — დაუყოვნებლივ შეწყვიტეთ და შეათანხმეთ ექიმთან!')
    if repro_preg and ('acei_arb' in ctx.med_chips or ctx.ft_meds.get('has_ft_acei_arb')):
        ctx.red_flags.append('ორსულობის დროს ACEi / ARB (პრილი/სარტანი) აბსოლუტურად უკუნაჩვენებია (ნაყოფის ნეფროტოქსიკურობა — FDA Boxed Warning) — დაუყოვნებლივ შეცვალეთ ექიმთან!')
    if ctx.liver_3x and (q.get('med_doac') or q.get('med_dabigatran') or 'rivaroxaban_2_5' in ctx.med_chips):
        ctx.red_flags.append('ღვიძლის მძიმე დისფუნქცია (≥3x ULN) ანტიკოაგულანტის ფონზე: კოაგულოპათიის / Child-Pugh B–C დროს DOAC უკუნაჩვენებია (EHRA 2021) — გადაამოწმეთ სქემა!')
    if ctx.q_filled and ctx.has_afib and not (ctx.has_full_oac or getattr(ctx, 'has_ft_full_oac', False)):
        ctx.amber_flags.append('ფიქსირდება მოციმციმე არითმია (AFib) პერორალური ანტიკოაგულაციის გარეშე — შეაფასეთ CHA2DS2-VA და ინსულტის პროფილაქტიკა კარდიოლოგთან!')


def _eval_antithrombotic_conflicts(ctx):
    q, s = ctx.q, ctx.s
    cilo_min_crcl = rule_value('MED_CILOSTAZOL_BLACKBOX')['crcl_min']
    voy_min_egfr = rule_value('MED_VOYAGER_RIVAROXABAN_2_5')['min_egfr']
    mech_inr_min = rule_value('MED_MECH_VALVE_SAFETY')
    vka_cfg = rule_value('MED_WARFARIN_HOLD_DAYS')
    vka_hold_days, evla_inr_lim = vka_cfg['hold_days'], vka_cfg['inr_evla_max']
    triple_min = rule_value('MED_ANTITHROMBOTIC_CONFLICTS')
    doac_low = rule_value('MED_DOAC_HOLD_INTERVALS')['crcl_low']
    dabi_low = rule_value('MED_DABIGATRAN_HOLD_INTERVALS')['crcl_low']
    has_mech_valve = 'mech_valve' in ctx.cad_chips
    has_acs_stent_12m = ('stent_6_12m' in ctx.cad_chips) and ('mi' in ctx.cad_chips)
    has_afib = ('afib' in ctx.cad_chips) or ((s.get('ecg') or 'pending') == 'afib_controlled')
    ft_u_keys = ctx.ft_meds.get('unstructured_keys') or set()
    oac_count = sum([bool(q.get('med_warfarin')), bool(q.get('med_doac')), bool(q.get('med_dabigatran')), ('rivaroxaban_2_5' in ctx.med_chips)]) + len(ft_u_keys & {'warfarin', 'doac', 'dabigatran', 'rivaroxaban_2_5'})
    p2y12_count = sum([bool(q.get('med_clopidogrel')), bool(q.get('med_ticagrelor')), bool(q.get('med_prasugrel'))])
    total_at_count = oac_count + p2y12_count + (1 if q.get('med_aspirin') else 0) + (1 if 'cilostazol' in ctx.med_chips else 0)
    has_prior_stroke_tia = bool(ctx.is_symp_cea or has_prior_stroke_or_tia(ctx.patient, q=q, s=s, today=ctx.today))
    ctx.has_prior_stroke_tia = has_prior_stroke_tia
    eff_preg_m = bool(getattr(ctx, 'eff_pregnant', False))
    eff_ren_c = None if getattr(ctx, 'creat_unit_mismatch', False) else getattr(ctx, 'eff_renal_sat', ctx.eff_renal)
    active_gi_bleed = getattr(ctx, 'active_gi_bleed', False)
    raw_s = getattr(ctx, 'raw_s', None) or {}
    raw_di = raw_s['dos_inr'] if 'dos_inr' in raw_s else s.get('dos_inr')
    dos_inr_bad_m = bool((raw_di is not None and str(raw_di).strip() != '' and _num(raw_di, lo=0.5, hi=15) is None) or 'dos_inr' in (s.get('_invalid_inputs') or {}))
    evla_vka_bad = bool(ctx.is_venous and ctx.patient.op_subtype == PreopPatient.OpSubtype.EVLA and q.get('med_warfarin') and not ctx.has_neuraxial_plan and (dos_inr_bad_m or (ctx.eff_inr_nx is not None and ctx.eff_inr_nx > evla_inr_lim)))
    has_full_oac_any = bool(ctx.on_oral_anticoag or getattr(ctx, 'has_ft_full_oac', False))
    ft_full_oac_contra = bool(getattr(ctx, 'has_ft_full_oac', False) and (
        oac_count >= 2 or eff_preg_m or active_gi_bleed or (has_mech_valve and bool(ft_u_keys & {'doac', 'dabigatran'}))
        or (bool(ft_u_keys & {'doac', 'dabigatran'}) and (ctx.on_dialysis or ctx.liver_3x or (eff_ren_c is not None and eff_ren_c < (dabi_low if 'dabigatran' in ft_u_keys else doac_low))))
    ))
    ctx.has_contra_full_oac = bool(
        (has_full_oac_any and oac_count >= 2) or (has_mech_valve and (q.get('med_doac') or q.get('med_dabigatran')))
        or (eff_preg_m and has_full_oac_any) or (q.get('med_warfarin') and (active_gi_bleed or evla_vka_bad)) or ft_full_oac_contra
        or (q.get('med_dabigatran') and (ctx.on_dialysis or (eff_ren_c is not None and eff_ren_c < dabi_low) or ctx.liver_3x or active_gi_bleed))
        or (q.get('med_doac') and (ctx.on_dialysis or (eff_ren_c is not None and eff_ren_c < doac_low) or ctx.liver_3x or active_gi_bleed))
    )
    ctx.has_contra_oac = bool(ctx.has_contra_full_oac or ('rivaroxaban_2_5' in ctx.med_chips and (
        oac_count >= 2 or has_mech_valve or eff_preg_m or ctx.on_dialysis or (eff_ren_c is not None and eff_ren_c < voy_min_egfr)
        or ctx.liver_3x or 'nsaid' in ctx.allergy_chips or active_gi_bleed or has_prior_stroke_tia
    )))
    ctx.has_mech_valve, ctx.has_acs_stent_12m, ctx.has_afib = has_mech_valve, has_acs_stent_12m, has_afib
    ctx.p2y12_count, ctx.has_full_oac, ctx.oac_count = p2y12_count, ctx.on_oral_anticoag, oac_count

    if oac_count >= 2:
        ctx.red_flags.append('პაციენტთან ერთდროულად მონიშნულია 2 ან მეტი პერორალური ანტიკოაგულანტი — ლეტალური დუბლირების/სისხლდენის რისკი, დაუყოვნებლივ დააზუსტეთ სქემა!')
    if p2y12_count >= 2:
        ctx.red_flags.append('ერთდროულად მონიშნულია ორი P2Y12 ანტიაგრეგანტი — გადაამოწმეთ მედიკამენტური დუბლირება!')
    if total_at_count >= triple_min:
        ctx.red_flags.append(
            'პაციენტი ერთდროულად იღებს ≥3 ანტითრომბოზულ პრეპარატს (სამმაგი ანტითრომბოზული თერაპია — სისხლდენის მაღალი რისკი)! '
            'ESC / ESVS / EMA გაიდლაინით გადაამოწმეთ დე-ესკალაცია (ასპირინის/ცილოსტაზოლის მოხსნა და OAC + მონო-ანტიაგრეგანტი).'
        )
    if (has_full_oac_any or 'rivaroxaban_2_5' in ctx.med_chips or 'rivaroxaban_2_5' in ft_u_keys) and (q.get('med_ticagrelor') or q.get('med_prasugrel') or bool(ft_u_keys & {'ticagrelor', 'prasugrel'})):
        asa_alg, clop_intol = 'nsaid' in ctx.allergy_chips, 'clopidogrel_intol' in ctx.allergy_chips
        deesc_opt = (
            'ინდივიდუალურ უსაფრთხო რეჟიმზე (ასპირინზე და კლოპიდოგრელზე ალერგია!)' if (clop_intol and asa_alg)
            else ('ასპირინზე (კლოპიდოგრელზე აუტანლობა!)' if clop_intol else ('კლოპიდოგრელზე (ასპირინზე ალერგია!)' if asa_alg else 'კლოპიდოგრელზე ან ასპირინზე'))
        )
        ctx.red_flags.append(
            'პაციენტი იღებს პერორალურ ანტიკოაგულანტს (OAC / რივაროქსაბანი 2.5 მგ) ტიკაგრელორთან ან პრასუგრელთან ერთად (ESC Class III — სისხლდენის უმაღლესი რისკი)! '
            f'შეათანხმეთ დე-ესკალაცია {deesc_opt} კარდიოლოგთან!'
        )
    if q.get('med_prasugrel') and has_prior_stroke_tia:
        ctx.red_flags.append('პრასუგრელი აბსოლუტურად უკუნაჩვენებია გადატანილი ინსულტის / TIA-ს ანამნეზში (FDA Boxed Warning — ქალასშიდა სისხლჩაქცევის რისკი)! დაუყოვნებლივ შეცვალეთ ანტიაგრეგანტი კარდიოლოგთან!')
    if 'nsaid' in ctx.allergy_chips and q.get('med_aspirin'):
        ctx.red_flags.append('პაციენტს მონიშნული აქვს ასპირინზე / აასს-ზე ალერგია, მაგრამ იღებს ასპირინს — ანაფილაქსიის/ბრონქოსპაზმის რისკი, დაუყოვნებლივ შეწყვიტეთ და შეცვალეთ ანტიაგრეგანტი ექიმთან!')
    if 'clopidogrel_intol' in ctx.allergy_chips and q.get('med_clopidogrel'):
        ctx.red_flags.append('პაციენტს მონიშნული აქვს კლოპიდოგრელის აუტანლობა/ალერგია, მაგრამ იღებს კლოპიდოგრელს — დაუყოვნებლივ გადაამოწმეთ დანიშნულება!')
    ft_keys = ctx.ft_meds.get('matched_keys') or set()
    if (q.get('med_clopidogrel') or 'cilostazol' in ctx.med_chips or 'clopidogrel' in ft_keys or 'cilostazol' in ft_keys) and ('ppi' in ctx.med_chips or ctx.ft_meds['has_omeprazole']):
        ctx.amber_flags.append('CYP2C19 ურთიერთქმედება: ომეპრაზოლი/ეზომეპრაზოლი ამცირებს კლოპიდოგრელის აქტივაციას და ზრდის ცილოსტაზოლის კონცენტრაციას/ტოქსიკურობას — შეცვალეთ პანტოპრაზოლით (40 მგ)!')
    _eval_cilo_voyager_and_liver_contra(ctx, cilo_min_crcl, voy_min_egfr)
    _eval_mech_valve_conflicts(ctx, has_mech_valve, mech_inr_min, vka_hold_days)


def _eval_mech_valve_conflicts(ctx, has_mech_valve, mech_inr_min, vka_hold_days):
    q, s = ctx.q, ctx.s
    if not has_mech_valve:
        return
    op_dt_early = _date(s.get('planned_surgery_date'))
    has_hit = 'hit_heparin' in ctx.allergy_chips
    liv_arg = ' (ღვიძლის ≥3x-ზე უპირატესია ბივალირუდინი; არგატრობანი −75%!)' if ctx.liver_3x else ''
    if q.get('med_doac') or q.get('med_dabigatran') or 'rivaroxaban_2_5' in ctx.med_chips:
        ctx.red_flags.append(
            'მექანიკურ სარქველზე DOAC / დაბიგატრანი / რივაროქსაბანი აბსოლუტურად უკუნაჩვენებია '
            '(ESC Class III — სარქვლის თრომბოზის რისკი)! სასწრაფოდ გადაამოწმეთ ვარფარინი (VKA) კარდიოლოგთან!'
        )
    if not q.get('med_warfarin'):
        ctx.red_flags.append(
            'ფიქსირდება მექანიკური სარქველი ვარფარინის (VKA) გარეშე — სარქვლის მწვავე თრომბოზის საფრთხე! გადაამოწმეთ ანტიკოაგულაცია კარდიოლოგთან!'
        )
    else:
        dos_inr_mv = _num(s.get('dos_inr'), lo=0.5, hi=15)
        eff_inr_mv = dos_inr_mv if dos_inr_mv is not None else ctx.inr
        is_evla_cont = (
            ctx.is_venous and ctx.patient.op_subtype == PreopPatient.OpSubtype.EVLA and not ctx.has_neuraxial_plan
            and not ctx.liver_3x and not getattr(ctx, 'active_gi_bleed', False) and not getattr(ctx, 'eff_pregnant', False)
            and getattr(ctx, 'oac_count', 1) == 1
        )
        in_vka_washout = bool(op_dt_early and 0 <= (op_dt_early - ctx.today).days <= vka_hold_days and not is_evla_cont)
        if eff_inr_mv is not None and eff_inr_mv < mech_inr_min and not in_vka_washout:
            if getattr(ctx, 'active_gi_bleed', False):
                ctx.red_flags.append(
                    f'მექანიკურ სარქველზე INR სუბთერაპიულია ({eff_inr_mv} < {mech_inr_min}) აქტიური კუჭ-ნაწლავის სისხლდენის ფონზე — '
                    'ანტიკოაგულაცია დაბლოკილია ენდოსკოპიურ ჰემოსტაზამდე; სასწრაფოდ ჩართეთ კარდიოლოგისა და გასტროენტეროლოგის კონსილიუმი!'
                )
            else:
                bridge_opt = f'IV ბივალირუდინის/არგატრობანის ხიდი (HIT!{liv_arg})' if has_hit else 'ჰეპარინის ხიდი'
                ctx.red_flags.append(
                    f'მექანიკურ სარქველზე INR სუბთერაპიულია ({eff_inr_mv} < {mech_inr_min}) — სარქვლის თრომბოზის რისკი, სასწრაფოდ შეაფასეთ {bridge_opt} კარდიოლოგთან!'
                )
    if has_hit:
        ctx.red_flags.append(
            f'მექანიკური სარქველი + HIT ანამნეზში — ვარფარინის მოხსნისას უხიდოდ დატოვება და ჰეპარინი/LMWH აკრძალულია! '
            f'სავალდებულოა სტაციონარული IV ბივალირუდინი ან არგატრობანი (aPTT კონტროლით){liv_arg}!'
        )


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


def _eval_explicit_postop_plan(ctx, post_at_raw, asa_allergy, voyager_safe, alt_p2y12_lbl, oac_partner_lbl, has_active_stent_dapt):
    clop_intol = 'clopidogrel_intol' in ctx.allergy_chips
    stent_p2y12 = getattr(ctx, 'stent_p2y12_lbl', alt_p2y12_lbl)
    has_full_oac_any = bool(ctx.has_full_oac or getattr(ctx, 'has_ft_full_oac', False))
    label = dict(POST_ANTITHROMBOTIC_CHOICES).get(post_at_raw, '')
    if getattr(ctx, 'active_gi_bleed', False):
        plan_tag = f' ({label})' if label else ''
        ctx.red_flags.append(
            f'აქტიური კუჭ-ნაწლავის სისხლდენის / მწვავე წყლულის ფონზე არჩეული პოსტოპ. სქემა{plan_tag} უკუნაჩვენებია — '
            'სავალდებულოა გასტროენტეროლოგისა და ქირურგის კონსილიუმი ენდოსკოპიურ ჰემოსტაზამდე!'
        )
        return _gi_bleed_postop_msg(ctx)
    if ctx.has_mech_valve and getattr(ctx, 'eff_pregnant', False):
        ren_red = rule_value('REN_CKD_NEPHRO_HYDRATION')['red']
        eff_r_mv = None if getattr(ctx, 'creat_unit_mismatch', False) else getattr(ctx, 'eff_renal_sat', ctx.eff_renal)
        sev_ren_mv = bool(ctx.on_dialysis or (eff_r_mv is not None and eff_r_mv < ren_red))
        ctx.red_flags.append(
            'ორსულობისა და მექანიკური სარქვლის ფონზე პოსტოპ. ანტიკოაგულაციის სქემა სავალდებულოდ უნდა შეთანხმდეს '
            'მეან-გინეკოლოგისა და კარდიოლოგის კონსილიუმით (DOAC / VOYAGER აკრძალულია)!'
        )
        return _preg_mech_valve_postop_msg('hit_heparin' in ctx.allergy_chips, severe_renal=sev_ren_mv)
    if ctx.has_mech_valve and post_at_raw in ('voyager', 'dapt', 'sapt'):
        ctx.red_flags.append(
            'მექანიკური სარქვლის მქონე პაციენტთან პოსტოპ. სავალდებულოა უვადო ვარფარინი (VKA) ± მონო-ანტიაგრეგანტი — VOYAGER / მონო-SAPT დაუშვებელია!'
        )
        mv_ap = f' + ჩვენებისას {oac_partner_lbl}' if asa_allergy else ' + ჩვენებისას ასპირინი 75–100 მგ'
        return f'არჩეული სქემა უკუნაჩვენებია მექ. სარქველზე! უვადო ვარფარინი (VKA){mv_ap} (DOAC / VOYAGER აკრძალულია!)'
    if post_at_raw == 'sapt' and has_full_oac_any:
        if getattr(ctx, 'eff_pregnant', False):
            ctx.red_flags.append(
                'ორსულობის ფონზე არჩეულია პოსტოპ. SAPT, მაგრამ პაციენტს აქვს ანტიკოაგულაციის ჩვენება და პერორალური OAC უკუნაჩვენებია — '
                'შეარჩიეთ უსაფრთხო არაორალური ანტიკოაგულაცია კონსილიუმით!'
            )
            return _contra_full_oac_postop_label(ctx, oac_partner_lbl)
        ctx.red_flags.append(
            'პაციენტი მუდმივ პერორალურ ანტიკოაგულანტზეა (OAC) — პოსტოპ. მონო-ანტიაგრეგანტზე (SAPT) ჩამოქვეითება ანტიკოაგულაციის გარეშე საშიშია! შეარჩიეთ OAC + მონო-ანტიაგრეგანტი.'
        )
        if getattr(ctx, 'has_contra_full_oac', False) and not ctx.has_mech_valve:
            return _contra_full_oac_postop_label(ctx, oac_partner_lbl)
        return f'SAPT უკუნაჩვენებია OAC-ზე! შეცვალეთ: OAC + {oac_partner_lbl}'
    if (post_at_raw in ('voyager', 'dapt') and has_full_oac_any) or (post_at_raw == 'dapt' and 'rivaroxaban_2_5' in ctx.med_chips):
        if getattr(ctx, 'has_contra_full_oac', False) and not ctx.has_mech_valve:
            ctx.red_flags.append(
                'არჩეულია პოსტოპ. VOYAGER / DAPT, მაგრამ მიმდინარე OAC უკუნაჩვენებია (თირკმლის/ღვიძლის/ორსულობის/დუბლირების გამო) — უკუნაჩვენები OAC არ განაახლოთ, შეარჩიეთ უსაფრთხო სქემა კონსილიუმით!'
            )
            return _contra_full_oac_postop_label(ctx, oac_partner_lbl)
        if 'rivaroxaban_2_5' in ctx.med_chips and not has_full_oac_any:
            ctx.red_flags.append(
                'არჩეულია პოსტოპ. DAPT პაციენტთან, რომელიც იღებს რივაროქსაბან 2.5 მგ ×2-ს — სამმაგი თერაპიისა და რივაროქსაბანი 2.5 + ტიკაგრელორის სისხლდენის მაღალი რისკი (ESC Class III)! '
                'შეარჩიეთ VOYAGER PAD (რივაროქსაბანი 2.5 + ასპირინი) ან შეაჩერეთ რივაროქსაბანი DAPT-ისთვის.'
            )
            if asa_allergy:
                return 'DAPT და რივაროქსაბანი 2.5 მგ უკუნაჩვენებია ასპირინზე ალერგიისას — შეარჩიეთ უსაფრთხო სქემა ქირურგთან!'
            return 'DAPT უკუნაჩვენებია რივაროქსაბან 2.5 მგ-თან (სამმაგი თერაპია)! შეცვალეთ: რივაროქსაბანი 2.5 + ასპირინი 75–100 მგ (VOYAGER PAD) ან DAPT რივაროქსაბანის გარეშე!'
        ctx.red_flags.append(
            'არჩეულია პოსტოპ. VOYAGER / DAPT პაციენტთან, რომელიც მუდმივ ანტიკოაგულანტზეა (OAC) — სამმაგი თერაპიის სისხლდენის მაღალი რისკი! შეცვალეთ OAC + მონო-ანტიაგრეგანტით.'
        )
        return f'არჩეული სქემა უკუნაჩვენებია OAC-ზე (სამმაგი თერაპია)! შეცვალეთ: OAC + {oac_partner_lbl}'
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
        if asa_allergy:
            ctx.red_flags.append('ახალი კორონარული სტენტის (<6–12 თვ) + ასპირინზე ალერგიის ფონზე SAPT/სტენტის დაცვა შეათანხმეთ კარდიოლოგის კონსილიუმით (უწყვეტი P2Y12 / დესენსიბილიზაცია)!')
            return f'SAPT უკუნაჩვენებია ახალ სტენტზე (<6–12 თვ) + ასპირინზე ალერგია: {stent_p2y12} — კარდიოლოგის კონსილიუმი!'
        ctx.red_flags.append('ახალი კორონარული სტენტის (<6–12 თვ) ფონზე პოსტოპ. მონოთერაპიაზე (SAPT) ჩამოქვეითება საშიშია — შეინარჩუნეთ DAPT!')
        return f'SAPT უკუნაჩვენებია ახალ სტენტზე (<6–12 თვ) — შეინარჩუნეთ უწყვეტი DAPT (ასპირინი + {stent_p2y12})!'
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
        and not ctx.liver_3x and not has_full_oac_any and not ctx.has_mech_valve and not ctx.has_afib
        and not q.get('med_ticagrelor') and not q.get('med_prasugrel')
        and not bool(ft_u_keys & {'ticagrelor', 'prasugrel'})
        and not q.get('recent_stent') and not ctx.has_acs_stent_12m and not asa_allergy
    )
    ctx.voyager_safe = voyager_safe
    tica_blocked = bool(ctx.liver_3x or getattr(ctx, 'eff_pregnant', False) or has_full_oac_any)
    ctx.tica_blocked = tica_blocked
    if 'clopidogrel_intol' in ctx.allergy_chips:
        alt_p2y12_lbl = 'ინდივიდუალური რეჟიმი კონსილიუმით (P2Y12 აუტანლობა/უკუჩვენება!)' if tica_blocked else 'ტიკაგრელორი'
    else:
        alt_p2y12_lbl = 'კლოპიდოგრელი 75 მგ'
    if q.get('med_ticagrelor') and not tica_blocked:
        stent_p2y12_lbl = 'ტიკაგრელორი'
    elif q.get('med_prasugrel') and not getattr(ctx, 'has_prior_stroke_tia', False) and not ctx.liver_3x and not getattr(ctx, 'eff_pregnant', False):
        stent_p2y12_lbl = 'პრასუგრელი'
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
            mv_ap = f' + ჩვენებისას {oac_partner_lbl}' if asa_allergy else ' + ჩვენებისას ასპირინი 75–100 მგ'
            post_antithrombotic_label = f'უვადო ვარფარინი (VKA) კარდიოლოგის სამიზნე INR-ით{mv_ap} (DOAC / VOYAGER აკრძალულია!)'
        elif getattr(ctx, 'has_contra_full_oac', False):
            post_antithrombotic_label = _contra_full_oac_postop_label(ctx, oac_partner_lbl)
        elif has_full_oac_any:
            post_antithrombotic_label = f'OAC + {oac_partner_lbl} (სამმაგი თერაპიის გარეშე — ESC/ESVS)'
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


def _parse_med_confirm_date(patient, s, med_schedule):
    """Return earliest verified pre-window hold confirmation date (nurse call, Stage 2, or approval), else None."""
    import datetime as _dt

    from django.utils import timezone

    from ..nurse_ui import med_schedule_signature
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


def _eval_elapsed_hold_window_alert(ctx):
    from ..nurse_ui import _extract_hold_start_date
    ctx.has_elapsed_hold_window = False
    s = ctx.s
    op_dt = _date(s.get('planned_surgery_date'))
    if not op_dt or not ctx.is_active_case or ctx.hard_contraindications or ctx.is_rupture_suspect:
        return
    hold_rows = [
        r for r in ctx.med_schedule
        if r.get('hold_or_skip') and not r.get('unstructured') and not r.get('acute_hold')
        and (r.get('mandatory_hold_days', 0) > 0 or (not r.get('contraindicated') and not r.get('reconcile_only')))
    ]
    if not hold_rows:
        return
    elapsed = op_dt < ctx.today
    if not elapsed and not s.get('dos_med_hold_verified'):
        confirmed_dt = _parse_med_confirm_date(ctx.patient, s, ctx.med_schedule)
        for r in hold_rows:
            st_dt = _extract_hold_start_date(r, op_dt)
            if st_dt and st_dt < ctx.today:
                if r.get('contraindicated') or confirmed_dt is None or confirmed_dt > st_dt:
                    elapsed = True
                    break
    if elapsed:
        ctx.has_elapsed_hold_window = True
        ctx.red_flags.append(
            'წამლის შეჩერების ფანჯარა უკვე გასულია — გადაამოწმეთ ოპერაციის თარიღი ან გადავადეთ ჩარევა ქირურგთან!'
        )


def _eval_stent_dapt_and_hold_alerts(ctx):
    patient, q, s = ctx.patient, ctx.q, ctx.s
    st_cfg = rule_value('MED_RECENT_STENT_DAPT')
    st_el_m, st_acs_m = st_cfg['elective_months'], st_cfg['acs_months']
    has_active_stent_dapt = ctx.has_active_stent_dapt
    asa_allergy_solo_p2y12 = (
        'nsaid' in ctx.allergy_chips and ctx.p2y12_count >= 1
        and (not ctx.is_venous or bool(q.get('cad')) or bool(ctx.cad_chips) or getattr(ctx, 'has_prior_stroke_tia', False))
    )
    uninterrupted_p2y12 = (
        (ctx.p2y12_count >= 1)
        and (
            bool(q.get('recent_stent')) or ctx.has_acs_stent_12m or asa_allergy_solo_p2y12 or (ctx.p2y12_count >= 2)
            or (ctx.is_pta and bool(q.get('med_clopidogrel') or q.get('med_ticagrelor')))
            or (ctx.is_symp_cea and bool(q.get('med_clopidogrel') or q.get('med_ticagrelor')))
        )
    )
    ctx.uninterrupted_p2y12 = uninterrupted_p2y12
    if ctx.has_neuraxial_plan and uninterrupted_p2y12 and not any('ნეირაქსიული' in rf and 'P2Y12' in rf for rf in ctx.red_flags):
        ctx.red_flags.append(
            'ნეირაქსიული ანესთეზია / ეპიდურული კათეტერი უკუნაჩვენებია უწყვეტი P2Y12 თერაპიის '
            '(კლოპიდოგრელი/ტიკაგრელორი/პრასუგრელი — სტენტი/DAPT/სიმპტ. CEA) ფონზე (ASRA 5th Ed — სპინალური ჰემატომის უმაღლესი რისკი)! '
            'შეცვალეთ გაუტკივარება ზოგადი/ადგილობრივი მეთოდით.'
        )
    if has_active_stent_dapt:
        if ctx.p2y12_count == 0:
            ctx.red_flags.append(
                f'კორონარული სტენტი (<{st_el_m}–{st_acs_m} თვ) აქტიური P2Y12 ანტიაგრეგანტის გარეშე — სტენტის მწვავე თრომბოზის ლეტალური რისკი! '
                'სასწრაფოდ გადაამოწმეთ DAPT კარდიოლოგთან!'
            )
        is_purely_elective_stent = (
            (ctx.pathway == PreopPatient.Pathway.CEA and not ctx.is_symp_cea)
            or (ctx.pathway in (PreopPatient.Pathway.ABF, PreopPatient.Pathway.PAD_BYPASS, PreopPatient.Pathway.PAD_PTA) and not ctx.is_clti_any and not ctx.is_ali_acute)
            or (ctx.is_venous and not (q.get('ven_bleeding') or q.get('ven_ceap') == 'C6'))
        )
        if is_purely_elective_stent:
            ctx.red_flags.append(
                f'ESC 2022 Class I: ახალი კორონარული სტენტის (<{st_el_m}–{st_acs_m} თვ სავალდებულო DAPT ფანჯარა) ფონზე წმინდა გეგმური ჩარევა '
                '(კოჭლობა / ასიმპტომური CEA / გეგმური ვარიკოზი) რეკომენდებულია გადაიდოს DAPT ვადის დასრულებამდე!'
            )
    if q.get('recent_stent'):
        ctx.red_flags.append(
            f'კორონარული სტენტი ჩადგმულია ბოლო {st_el_m} თვეში (DAPT) — თვითნებური მოხსნა საშიშია! '
            'შეათანხმეთ ანტიაგრეგანტული რეჟიმი და ოპერაციის ტაქტიკა ქირურგთან.'
        )
    elif ctx.has_acs_stent_12m and not ctx.cardio_cleared:
        ctx.red_flags.append(
            f'ინფარქტის შემდგომი კორონარული სტენტი 6–12 თვეში (ESC: სტანდარტული DAPT {st_acs_m} თვეა!) — '
            'ანტიაგრეგანტის შეჩერებამდე სავალდებულოა კარდიოლოგისა და ქირურგის შეთანხმება!'
        )
    if ctx.q_filled:
        if getattr(ctx, 'sglt2_unlisted_dm', False):
            ctx.amber_flags.append('პაციენტი იღებს SGLT2 ინჰიბიტორს დიაბეტის, გულის უკმარისობის ან CKD-ს მითითების გარეშე — დააზუსტეთ ჩვენება (დიაბეტი / HbA1c) და შეჩერების სქემა!')
        has_cv_vte_hx = bool(q.get('cad') or ctx.cad_chips or q.get('recent_stent') or q.get('ven_dvt_history') or q.get('ven_dvt_chips') or q.get('ven_thrombophilia'))
        has_full_oac_r = bool(ctx.has_full_oac or getattr(ctx, 'has_ft_full_oac', False))
        if not has_cv_vte_hx and (has_full_oac_r or (ctx.p2y12_count >= 1 and q.get('med_aspirin')) or 'amiodarone' in ctx.med_chips or ctx.ft_meds.get('has_ft_amiodarone')):
            ctx.amber_flags.append('კითხვარში გულის დაავადება/თრომბოზი მონიშნული არ არის, მაგრამ პაციენტი იღებს ანტიკოაგულანტს / DAPT-ს / ამიოდარონს — დააზუსტეთ ჩვენება და კარდიოლოგიური ანამნეზი!')
    if ctx.needs_med_hold and not s.get('med_hold_agreed'):
        has_red_med_row = any(r.get('tone') == 'red' or r.get('contraindicated') for r in ctx.med_schedule)
        evla_vka_suprather = bool(
            q.get('med_warfarin') and ctx.eff_inr_nx is not None
            and ctx.eff_inr_nx > rule_value('MED_WARFARIN_HOLD_DAYS')['inr_evla_max']
        )
        has_evla_hold_only_drug = bool(
            q.get('med_ticagrelor') or q.get('med_prasugrel') or q.get('ven_hormones')
            or ('cilostazol' in ctx.med_chips) or ('rivaroxaban_2_5' in ctx.med_chips)
            or ctx.ft_meds['unstructured_labels']
        )
        has_core_asra_or_met = bool(
            q.get('med_warfarin') or q.get('med_doac') or q.get('med_dabigatran')
            or q.get('med_clopidogrel') or q.get('med_ticagrelor') or q.get('med_prasugrel')
            or ('cilostazol' in ctx.med_chips) or ('rivaroxaban_2_5' in ctx.med_chips)
            or q.get('sglt2') or q.get('glp1') or q.get('dm_insulin') or q.get('metformin')
            or ctx.ft_meds['unstructured_labels']
        )
        if (
            ctx.is_venous and patient.op_subtype == PreopPatient.OpSubtype.EVLA
            and not ctx.has_neuraxial_plan and not q.get('sglt2') and not q.get('glp1')
            and not q.get('dm_insulin') and not q.get('metformin') and not ctx.metf_contra_any
            and not has_red_med_row and not evla_vka_suprather and not has_evla_hold_only_drug
        ):
            ctx.amber_flags.append('პაციენტი იღებს ანტიკოაგულანტს / ანტიაგრეგანტს — დააზუსტეთ ქირურგთან (ESVS 2022-ით EVLA შესაძლებელია მოუხსნელადაც).')
        elif ctx.has_neuraxial_plan and ctx.is_phleb and not has_core_asra_or_met:
            ctx.amber_flags.append(
                'ჰორმონული პრეპარატი / დაბალი კარდიორისკის ასპირინი — ფლებექტომიის წინ შეათანხმეთ მიღების/შეჩერების და თრომბოპროფილაქტიკის რეჟიმი ქირურგთან.'
            )
        elif ctx.has_neuraxial_plan:
            proc_word = 'ეპიდურული / სპინალური ანესთეზიისა და ფლებექტომიის' if ctx.is_phleb else 'ეპიდურული / სპინალური ანესთეზიის'
            ctx.amber_flags.append(
                f'პაციენტი იღებს ანტიკოაგულანტს / კლოპიდოგრელს / SGLT2 / ინსულინს — {proc_word} წინ '
                'სავალდებულოა შეჩერების დღის შეთანხმება (იხ. ASRA მედიკამენტების ცხრილი)!'
            )
        else:
            ctx.amber_flags.append('პაციენტი იღებს ანტიკოაგულანტს / P2Y12 / SGLT2 / GLP-1 / ინსულინს / მეტფორმინს — შეათანხმეთ მიღების/შეჩერების დღე ქირურგთან.')
    _eval_elapsed_hold_window_alert(ctx)


def eval_meds_rules(ctx):
    ctx.has_acs_stent_12m = ('stent_6_12m' in ctx.cad_chips and 'mi' in ctx.cad_chips)
    _eval_allergy_cied_and_med_hold(ctx)
    _eval_antithrombotic_conflicts(ctx)
    _eval_postop_voyager(ctx)
    _eval_stent_dapt_and_hold_alerts(ctx)
