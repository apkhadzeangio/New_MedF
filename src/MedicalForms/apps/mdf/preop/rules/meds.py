"""Medication safety, allergy prophylaxis switches, HIT, CIED, mechanical valve, VOYAGER PAD & DAPT rules."""
from ...models import PreopPatient
from ..constants import POST_ANTITHROMBOTIC_CHOICES
from ..meds import build_med_schedule
from ..registry import rule_value
from ..utils import _date, _int, is_pregnancy_eligible


def _eval_allergy_cied_and_med_hold(ctx):
    patient, q, s, q_filled = ctx.patient, ctx.q, ctx.s, ctx.q_filled
    metf_cfg = rule_value('MED_METFORMIN_RENAL_CONTRAST')
    abx_prophylaxis_note = ''
    if 'hit_heparin' in ctx.allergy_chips:
        ctx.red_flags.append(
            'საეჭვოა ჰეპარინ-ინდუცირებული თრომბოციტოპენია (HIT) — არაფრაქციული ჰეპარინი და LMWH უკუნაჩვენებია! '
            'სასწრაფოდ შეათანხმეთ ალტერნატიული ანტიკოაგულაცია ქირურგთან!'
        )
    if 'beta_lactam' in ctx.allergy_chips:
        abx_prophylaxis_note = 'β-ლაქტამზე ალერგია: პროფილაქტიკური ცეფაზოლინის ნაცვლად გამოიყენება ვანკომიცინი ან კლინდამიცინი!'
        ctx.amber_flags.append(abx_prophylaxis_note)
    elif 'penicillin_mild' in ctx.allergy_chips:
        ctx.amber_flags.append('მსუბუქი არა-ანაფილაქსიური გამონაყარი პენიცილინზე — AAAAI 2022 გაიდლაინით ცეფაზოლინი უსაფრთხოდ დასაშვებია.')
    if 'latex_chlorhex' in ctx.allergy_chips:
        ctx.amber_flags.append('ალერგია ლატექსზე / ქლორჰექსიდინზე — გამოიყენეთ ულატექსო ხელთათმანები/კათეტერი და პოვიდონ-იოდის ანტისეპტიკი!')
    if ctx.unclarified_allergy_other:
        ctx.amber_flags.append('დაფიქსირებულია დაუზუსტებელი მედიკამენტური ალერგია — ცეფაზოლინის შეყვანამდე დააზუსტეთ ალერგენი!')
    if ctx.ft_meds['unstructured_labels']:
        ctx.red_flags.append(
            f"თავისუფალ ტექსტში („{q.get('other_meds')}“) მითითებულია მაღალი რისკის პრეპარატი ({', '.join(ctx.ft_meds['unstructured_labels'])}), "
            'რომელიც სტრუქტურულ ჩიპებში მონიშნული არ არის — სასწრაფოდ დააზუსტეთ შეჩერების სქემისთვის!'
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

    ctx.needs_cied = q_filled and bool(q.get('pacemaker'))
    ctx.cied_ok = bool(s.get('cied_checked')) or ((s.get('cied_plan') or 'pending') != 'pending')
    if ctx.needs_cied:
        ctx.reflex_orders.append({
            'key': 'cied',
            'trigger': 'იმპლანტირებული კარდიოსტიმულატორი / დეფიბრილატორი (CIED)',
            'action': 'მოწყობილობის პასპორტი/ბოლო შემოწმება, მაგნიტის რეჟიმი და ბიპოლარული კოაგულაციის დაგეგმვა კარდიოლოგ/ანესთეზიოლოგთან',
            'done': ctx.cied_ok,
        })

    has_crit_q_meds = bool(
        q.get('med_warfarin') or q.get('med_doac') or q.get('med_dabigatran')
        or q.get('med_clopidogrel') or q.get('med_ticagrelor') or q.get('med_prasugrel')
        or q.get('metformin') or q.get('sglt2') or q.get('glp1') or q.get('dm_insulin')
        or q.get('recent_stent') or ctx.ft_meds['unstructured_labels']
        or q.get('self_filled') or q.get('self_fill_submitted')
    )
    ctx.med_schedule = build_med_schedule(
        patient, q, s, ctx.egfr, crcl=ctx.crcl,
        has_neuraxial_override=ctx.has_neuraxial_plan, today=ctx.today,
    ) if (q_filled or has_crit_q_meds) else []
    clop_needs_hold = bool(q.get('med_clopidogrel') and not ctx.is_pta and not ctx.is_symp_cea)
    is_evla_only = (
        ctx.is_venous and patient.op_subtype == PreopPatient.OpSubtype.EVLA
        and not ctx.has_neuraxial_plan and ctx.anesth_plan_raw not in ('general', 'sedation')
    )
    is_ia_first_pass = patient.op_subtype in ('evar', 'tbd')
    metf_contra_any = bool(q.get('metformin')) and (ctx.on_dialysis or (ctx.eff_renal is not None and ctx.eff_renal < metf_cfg['contra_renal']) or ctx.liver_3x)
    metf_hold_48h = bool(q.get('metformin')) and (
        is_ia_first_pass
        or (ctx.uses_contrast and (ctx.eff_renal is None or ctx.eff_renal < metf_cfg['contrast_hold_renal']))
        or ctx.open_major
        or (not is_evla_only and ctx.eff_renal is not None and ctx.eff_renal < metf_cfg['dose_cap_renal'])
    )
    ctx.metf_contra_any = metf_contra_any
    ctx.needs_med_hold = (q_filled or has_crit_q_meds) and (
        clop_needs_hold
        or bool(q.get('med_ticagrelor') and not ctx.is_pta)
        or bool(q.get('med_prasugrel'))
        or bool(q.get('med_doac'))
        or bool(q.get('med_dabigatran'))
        or bool(q.get('med_warfarin'))
        or ('cilostazol' in ctx.med_chips)
        or ('rivaroxaban_2_5' in ctx.med_chips)
        or bool(q.get('glp1'))
        or bool(q.get('sglt2'))
        or bool(q.get('dm_insulin'))
        or bool(q.get('metformin'))
        or metf_contra_any
        or metf_hold_48h
        or bool(ctx.ft_meds['unstructured_labels'])
    )
    if (q_filled or has_crit_q_meds) and metf_contra_any:
        ctx.red_flags.append(
            'მეტფორმინი უკუნაჩვენებია თირკმლის მძიმე უკმარისობის (CrCl/eGFR <30 / დიალიზი) ან ღვიძლის ≥3x დისფუნქციის დროს '
            '(ლაქტატ-აციდოზის რისკი) — შეწყვიტეთ დაუყოვნებლივ და შეათანხმეთ ენდოკრინოლოგთან!'
        )
    elif (q_filled or has_crit_q_meds) and metf_hold_48h and not ctx.uses_contrast and ctx.eff_renal is not None and ctx.eff_renal < metf_cfg['dose_cap_renal']:
        ctx.amber_flags.append(
            f'მეტფორმინი CrCl/eGFR {ctx.eff_renal} (30–44) ფონზე: დღიური დოზა ≤1000 მგ და ოპერაციაზე 48 სთ შეჩერება კრეატინინის კონტროლით.'
        )


def _eval_antithrombotic_conflicts(ctx):
    q, s, q_filled = ctx.q, ctx.s, ctx.q_filled
    cilo_min_crcl = rule_value('MED_CILOSTAZOL_BLACKBOX')['crcl_min']
    voy_min_egfr = rule_value('MED_VOYAGER_RIVAROXABAN_2_5')['min_egfr']
    mech_inr_min = rule_value('MED_MECH_VALVE_SAFETY')
    vka_hold_days = rule_value('MED_WARFARIN_HOLD_DAYS')['hold_days']
    triple_min = rule_value('MED_ANTITHROMBOTIC_CONFLICTS')
    has_mech_valve = 'mech_valve' in ctx.cad_chips
    has_acs_stent_12m = ('stent_6_12m' in ctx.cad_chips) and ('mi' in ctx.cad_chips)
    has_afib = ('afib' in ctx.cad_chips) or ((s.get('ecg') or 'pending') == 'afib_controlled')
    oac_count = sum([
        bool(q.get('med_warfarin')), bool(q.get('med_doac')),
        bool(q.get('med_dabigatran')), ('rivaroxaban_2_5' in ctx.med_chips),
    ])
    p2y12_count = sum([
        bool(q.get('med_clopidogrel')), bool(q.get('med_ticagrelor')), bool(q.get('med_prasugrel')),
    ])
    total_at_count = (
        oac_count + p2y12_count
        + (1 if q.get('med_aspirin') else 0)
        + (1 if 'cilostazol' in ctx.med_chips else 0)
    )
    ctx.has_mech_valve, ctx.has_acs_stent_12m, ctx.has_afib = has_mech_valve, has_acs_stent_12m, has_afib
    ctx.p2y12_count, ctx.has_full_oac = p2y12_count, ctx.on_oral_anticoag

    if oac_count >= 2:
        ctx.red_flags.append(
            'პაციენტთან ერთდროულად მონიშნულია 2 ან მეტი პერორალური ანტიკოაგულანტი — '
            'ლეტალური დუბლირების/სისხლდენის რისკი, დაუყოვნებლივ დააზუსტეთ სქემა!'
        )
    if p2y12_count >= 2:
        ctx.red_flags.append('ერთდროულად მონიშნულია ორი P2Y12 ანტიაგრეგანტი — გადაამოწმეთ მედიკამენტური დუბლირება!')
    if total_at_count >= triple_min:
        ctx.red_flags.append(
            'პაციენტი ერთდროულად იღებს ≥3 ანტითრომბოზულ პრეპარატს (სამმაგი ანტითრომბოზული თერაპია — სისხლდენის მაღალი რისკი)! '
            'ESC / ESVS / EMA გაიდლაინით გადაამოწმეთ დე-ესკალაცია (ასპირინის/ცილოსტაზოლის მოხსნა და OAC + მონო-ანტიაგრეგანტი).'
        )
    if (ctx.has_full_oac or 'rivaroxaban_2_5' in ctx.med_chips) and (q.get('med_ticagrelor') or q.get('med_prasugrel')):
        ctx.red_flags.append(
            'პაციენტი იღებს პერორალურ ანტიკოაგულანტს (OAC / რივაროქსაბანი 2.5 მგ) ტიკაგრელორთან ან პრასუგრელთან ერთად (ESC Class III — სისხლდენის უმაღლესი რისკი)! '
            'შეათანხმეთ დე-ესკალაცია კლოპიდოგრელზე ან ასპირინზე კარდიოლოგთან!'
        )
    if q.get('med_prasugrel') and (q.get('neuro') or ctx.is_symp_cea):
        ctx.red_flags.append(
            'პრასუგრელი აბსოლუტურად უკუნაჩვენებია გადატანილი ინსულტის / TIA-ს ანამნეზში (FDA Boxed Warning — ქალასშიდა სისხლჩაქცევის რისკი)! '
            'დაუყოვნებლივ შეცვალეთ ანტიაგრეგანტი კარდიოლოგთან!'
        )
    if 'clopidogrel_intol' in ctx.allergy_chips and q.get('med_clopidogrel'):
        ctx.red_flags.append(
            'პაციენტს მონიშნული აქვს კლოპიდოგრელის აუტანლობა/ალერგია, მაგრამ იღებს კლოპიდოგრელს — დაუყოვნებლივ გადაამოწმეთ დანიშნულება!'
        )
    if (q.get('med_clopidogrel') or 'cilostazol' in ctx.med_chips) and ('ppi' in ctx.med_chips or ctx.ft_meds['has_omeprazole']):
        ctx.amber_flags.append(
            'CYP2C19 ურთიერთქმედება: კლოპიდოგრელთან/ცილოსტაზოლთან ომეპრაზოლი და ეზომეპრაზოლი ამცირებს ეფექტს — შეცვალეთ პანტოპრაზოლით (40 მგ)!'
        )

    lvef_early = _int(s.get('lvef'), lo=5, hi=95)
    bnp_early = _int(s.get('nt_probnp'), lo=1, hi=100000)
    repro_preg = is_pregnancy_eligible(ctx.patient) and (bool(q.get('ven_pregnant')) or bool(q.get('pregnant_possible')))
    cea_bleed = bool(
        ctx.pathway == PreopPatient.Pathway.CEA
        and (
            s.get('brain_imaging') == 'large_or_hemorrhage'
            or (getattr(ctx, 'active_thrombolysis_window', False) and not (s.get('brain_imaging') == 'normal_small' and ctx.days_since_ev is not None and ctx.days_since_ev >= 1))
        )
    )
    cilostazol_contra = bool(
        cea_bleed
        or ctx.chf_hyd_flag or (lvef_early is not None and lvef_early < 50)
        or (bnp_early is not None and bnp_early >= 300) or ctx.on_dialysis
        or (ctx.eff_renal is not None and ctx.eff_renal <= cilo_min_crcl)
        or ((s.get('ecg') or 'pending') == 'ischemia_arrhythmia') or (ctx.hr is not None and ctx.hr > 110)
        or ctx.liver_3x or bool(q.get('ulcer_active_symp')) or bool(q.get('recent_stent')) or repro_preg
    )
    if 'cilostazol' in ctx.med_chips and cilostazol_contra:
        ctx.red_flags.append(
            'ცილოსტაზოლი უკუნაჩვენებია გულის უკმარისობის / LVEF <50% / CrCl ≤25 / ღვიძლის ≥3x / სტენტის / წყლულის / ტაქიარითმიის / სისხლჩაქცევის დროს (FDA Black Box / EMA) — შეათანხმეთ მოხსნა!'
        )
    if 'rivaroxaban_2_5' in ctx.med_chips and (ctx.on_dialysis or (ctx.eff_renal is not None and ctx.eff_renal < voy_min_egfr)):
        ctx.red_flags.append(
            f'რივაროქსაბანი 2.5 მგ ×2 (VOYAGER PAD) უკუნაჩვენებია/საშიშია თირკმლის მძიმე უკმარისობისას (CrCl/eGFR <{voy_min_egfr} ან დიალიზი) — გადაამოწმეთ სქემა!'
        )
    if ctx.liver_3x and (q.get('med_doac') or q.get('med_dabigatran') or 'rivaroxaban_2_5' in ctx.med_chips):
        ctx.red_flags.append(
            'ღვიძლის მძიმე დისფუნქცია (≥3x ULN) ანტიკოაგულანტის ფონზე: კოაგულოპათიის / Child-Pugh B–C დროს DOAC უკუნაჩვენებია (EHRA 2021) — გადაამოწმეთ სქემა!'
        )
    if q_filled and has_afib and not ctx.has_full_oac:
        ctx.amber_flags.append(
            'ფიქსირდება მოციმციმე არითმია (AFib) პერორალური ანტიკოაგულაციის გარეშე — შეაფასეთ CHA2DS2-VA და ინსულტის პროფილაქტიკა კარდიოლოგთან!'
        )
    _eval_mech_valve_conflicts(ctx, has_mech_valve, mech_inr_min, vka_hold_days)


def _eval_mech_valve_conflicts(ctx, has_mech_valve, mech_inr_min, vka_hold_days):
    from ..utils import _num
    q, s = ctx.q, ctx.s
    if not has_mech_valve:
        return
    op_dt_early = _date(s.get('planned_surgery_date'))
    has_hit = 'hit_heparin' in ctx.allergy_chips
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
        )
        in_vka_washout = bool(op_dt_early and 0 <= (op_dt_early - ctx.today).days <= vka_hold_days and not is_evla_cont)
        if eff_inr_mv is not None and eff_inr_mv < mech_inr_min and not in_vka_washout:
            bridge_opt = 'IV ბივალირუდინის/არგატრობანის ხიდი (HIT!)' if has_hit else 'ჰეპარინის ხიდი'
            ctx.red_flags.append(
                f'მექანიკურ სარქველზე INR სუბთერაპიულია ({eff_inr_mv} < {mech_inr_min}) — სარქვლის თრომბოზის რისკი, სასწრაფოდ შეაფასეთ {bridge_opt} კარდიოლოგთან!'
            )
    if has_hit:
        ctx.red_flags.append(
            'მექანიკური სარქველი + HIT ანამნეზში — ვარფარინის მოხსნისას უხიდოდ დატოვება და ჰეპარინი/LMWH აკრძალულია! '
            'სავალდებულოა სტაციონარული IV ბივალირუდინი ან არგატრობანი (aPTT კონტროლით)!'
        )


def _eval_postop_voyager(ctx):
    pathway, q, s, q_filled = ctx.pathway, ctx.q, ctx.s, ctx.q_filled
    voy_cfg = rule_value('MED_VOYAGER_RIVAROXABAN_2_5')
    post_at_raw = s.get('post_antithrombotic_plan') or 'auto'
    post_antithrombotic_label = ''
    asa_allergy = 'nsaid' in ctx.allergy_chips
    voyager_safe = (
        (ctx.egfr is None or ctx.egfr >= voy_cfg['min_egfr'])
        and (ctx.crcl is None or ctx.crcl >= voy_cfg['min_egfr'])
        and not ctx.on_dialysis
        and not q.get('ulcer_active_symp')
        and (s.get('gastro_result') != 'acute_ulcer')
        and not q.get('neuro')
        and not ctx.liver_3x
        and not ctx.has_full_oac
        and not ctx.has_mech_valve
        and not ctx.has_afib
        and not q.get('med_ticagrelor')
        and not q.get('med_prasugrel')
        and not q.get('recent_stent')
        and not ctx.has_acs_stent_12m
        and not asa_allergy
    )
    alt_p2y12_lbl = 'ტიკაგრელორი' if 'clopidogrel_intol' in ctx.allergy_chips else 'კლოპიდოგრელი 75 მგ'
    if 'clopidogrel_intol' in ctx.allergy_chips:
        oac_partner_lbl = (
            'ინდივიდუალური რეჟიმი კონსილიუმით (ასპირინზე და კლოპიდოგრელზე ალერგია!)'
            if asa_allergy else
            'ასპირინი 75–100 მგ (OAC + ტიკაგრელორი ESC Class III სისხლდენის რისკის გამო არ ინიშნება — შეათანხმეთ კონსილიუმით!)'
        )
    else:
        oac_partner_lbl = 'კლოპიდოგრელი 75 მგ'
    has_active_stent_dapt = bool((q_filled or q.get('recent_stent')) and (q.get('recent_stent') or ctx.has_acs_stent_12m))
    ctx.alt_p2y12_lbl, ctx.oac_partner_lbl, ctx.has_active_stent_dapt = alt_p2y12_lbl, oac_partner_lbl, has_active_stent_dapt

    if pathway in (PreopPatient.Pathway.ABF, PreopPatient.Pathway.PAD_BYPASS, PreopPatient.Pathway.PAD_PTA):
        if post_at_raw != 'auto':
            post_antithrombotic_label = dict(POST_ANTITHROMBOTIC_CHOICES).get(post_at_raw, '')
            if post_at_raw == 'voyager' and asa_allergy:
                post_antithrombotic_label = 'VOYAGER PAD უკუნაჩვენებია (ასპირინზე ალერგია!) — შეარჩიეთ კლოპიდოგრელი / ალტერნატივა'
            if ctx.has_mech_valve and post_at_raw in ('voyager', 'dapt', 'sapt'):
                ctx.red_flags.append(
                    'მექანიკური სარქვლის მქონე პაციენტთან პოსტოპ. სავალდებულოა უვადო ვარფარინი (VKA) ± მონო-ანტიაგრეგანტი — VOYAGER / მონო-SAPT დაუშვებელია!'
                )
            elif post_at_raw == 'sapt' and ctx.has_full_oac:
                ctx.red_flags.append(
                    'პაციენტი მუდმივ პერორალურ ანტიკოაგულანტზეა (OAC) — პოსტოპ. მონო-ანტიაგრეგანტზე (SAPT) ჩამოქვეითება ანტიკოაგულაციის გარეშე საშიშია! შეარჩიეთ OAC + მონო-ანტიაგრეგანტი.'
                )
            elif post_at_raw in ('voyager', 'dapt') and ctx.has_full_oac:
                ctx.red_flags.append(
                    'არჩეულია პოსტოპ. VOYAGER / DAPT პაციენტთან, რომელიც მუდმივ ანტიკოაგულანტზეა (OAC) — სამმაგი თერაპიის სისხლდენის მაღალი რისკი! შეცვალეთ OAC + მონო-ანტიაგრეგანტით.'
                )
            elif post_at_raw == 'voyager' and not voyager_safe:
                ctx.red_flags.append(
                    'პოსტოპ. VOYAGER PAD (რივაროქსაბანი 2.5 მგ ×2) უკუნაჩვენებია/საშიშია (დიალიზი, CrCl/eGFR დაბალი, მექ. სარქველი, AFib, სტენტი/DAPT, ინსულტი, წყლული, ღვიძლის 3x ან ასპირინზე ალერგია) — შეარჩიეთ ალტერნატივა!'
                )
            elif has_active_stent_dapt and post_at_raw == 'sapt' and not ctx.has_full_oac:
                ctx.red_flags.append('ახალი კორონარული სტენტის (<6–12 თვ) ფონზე პოსტოპ. მონოთერაპიაზე (SAPT) ჩამოქვეითება საშიშია — შეინარჩუნეთ DAPT!')
            elif post_at_raw == 'dapt' and 'clopidogrel_intol' in ctx.allergy_chips and not ctx.has_full_oac:
                ctx.amber_flags.append('კლოპიდოგრელის აუტანლობის გამო პოსტოპ. DAPT სქემაში გამოიყენეთ ალტერნატიული P2Y12 (ტიკაგრელორი).')
            elif post_at_raw == 'oac_sapt' and 'clopidogrel_intol' in ctx.allergy_chips and not asa_allergy:
                ctx.amber_flags.append('კლოპიდოგრელის აუტანლობის გამო OAC-ის ფონზე გამოიყენეთ ასპირინი 75–100 მგ (OAC + ტიკაგრელორი ESC Class III არ ინიშნება!).')
        elif ctx.has_mech_valve:
            mv_ap = f' + ჩვენებისას {alt_p2y12_lbl}' if asa_allergy else ' + ჩვენებისას ასპირინი 75–100 მგ'
            post_antithrombotic_label = f'უვადო ვარფარინი (VKA) კარდიოლოგის სამიზნე INR-ით{mv_ap} (DOAC / VOYAGER აკრძალულია!)'
        elif ctx.has_full_oac:
            post_antithrombotic_label = f'OAC + {oac_partner_lbl} (სამმაგი თერაპიის გარეშე — ESC/ESVS)'
        elif has_active_stent_dapt:
            st_lbl = f'{alt_p2y12_lbl} (ასპირინზე ალერგია — კარდიოლოგის კონსილიუმი!)' if asa_allergy else f'ასპირინი 100 მგ + {alt_p2y12_lbl}'
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


def _eval_stent_dapt_and_hold_alerts(ctx):
    patient, q, s, q_filled = ctx.patient, ctx.q, ctx.s, ctx.q_filled
    has_active_stent_dapt = ctx.has_active_stent_dapt
    asa_allergy_solo_p2y12 = (
        'nsaid' in ctx.allergy_chips and not q.get('med_aspirin') and ctx.p2y12_count >= 1
        and (not ctx.is_venous or bool(q.get('cad')) or bool(q.get('neuro')))
    )
    uninterrupted_p2y12 = (
        (ctx.p2y12_count >= 1)
        and (
            bool(q.get('recent_stent'))
            or ctx.has_acs_stent_12m
            or asa_allergy_solo_p2y12
            or (ctx.is_pta and not q.get('med_prasugrel'))
            or (ctx.is_symp_cea and not q.get('med_prasugrel'))
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
                'კორონარული სტენტი (<6–12 თვ) აქტიური P2Y12 ანტიაგრეგანტის გარეშე — სტენტის მწვავე თრომბოზის ლეტალური რისკი! '
                'სასწრაფოდ გადაამოწმეთ DAPT კარდიოლოგთან!'
            )
        is_purely_elective_stent = (
            (ctx.pathway == PreopPatient.Pathway.CEA and not ctx.is_symp_cea)
            or (ctx.pathway in (PreopPatient.Pathway.ABF, PreopPatient.Pathway.PAD_BYPASS, PreopPatient.Pathway.PAD_PTA) and not ctx.is_clti_any)
            or (ctx.is_venous and not (q.get('ven_bleeding') or q.get('ven_ceap') == 'C6'))
        )
        if is_purely_elective_stent:
            ctx.red_flags.append(
                'ESC 2022 Class I: ახალი კორონარული სტენტის (<6–12 თვ სავალდებულო DAPT ფანჯარა) ფონზე წმინდა გეგმური ჩარევა '
                '(კოჭლობა / ასიმპტომური CEA / გეგმური ვარიკოზი) რეკომენდებულია გადაიდოს DAPT ვადის დასრულებამდე!'
            )
    if q.get('recent_stent'):
        ctx.red_flags.append(
            'კორონარული სტენტი ჩადგმულია ბოლო 6 თვეში (DAPT) — თვითნებური მოხსნა საშიშია! '
            'შეათანხმეთ ანტიაგრეგანტული რეჟიმი და ოპერაციის ტაქტიკა ქირურგთან.'
        )
    elif q_filled and ctx.has_acs_stent_12m and not ctx.cardio_cleared:
        ctx.red_flags.append(
            'ინფარქტის შემდგომი კორონარული სტენტი 6–12 თვეში (ESC: სტანდარტული DAPT 12 თვეა!) — '
            'ანტიაგრეგანტის შეჩერებამდე სავალდებულოა კარდიოლოგისა და ქირურგის შეთანხმება!'
        )
    if ctx.needs_med_hold and not s.get('med_hold_agreed'):
        has_red_med_row = any(r.get('tone') == 'red' or r.get('contraindicated') for r in ctx.med_schedule)
        if (
            ctx.is_venous and patient.op_subtype == PreopPatient.OpSubtype.EVLA
            and not ctx.has_neuraxial_plan and not q.get('sglt2') and not q.get('glp1')
            and not q.get('dm_insulin') and not q.get('metformin') and not ctx.metf_contra_any and not has_red_med_row
        ):
            ctx.amber_flags.append('პაციენტი იღებს ანტიკოაგულანტს / ანტიაგრეგანტს — დააზუსტეთ ქირურგთან (ESVS 2022-ით EVLA შესაძლებელია მოუხსნელადაც).')
        elif ctx.has_neuraxial_plan:
            proc_word = 'ეპიდურული / სპინალური ანესთეზიისა და ფლებექტომიის' if ctx.is_phleb else 'ეპიდურული / სპინალური ანესთეზიის'
            ctx.amber_flags.append(
                f'პაციენტი იღებს ანტიკოაგულანტს / კლოპიდოგრელს / SGLT2 / ინსულინს — {proc_word} წინ '
                'სავალდებულოა შეჩერების დღის შეთანხმება (იხ. ASRA მედიკამენტების ცხრილი)!'
            )
        else:
            ctx.amber_flags.append('პაციენტი იღებს ანტიკოაგულანტს / P2Y12 / SGLT2 / GLP-1 / ინსულინს / მეტფორმინს — შეათანხმეთ მიღების/შეჩერების დღე ქირურგთან.')


def eval_meds_rules(ctx):
    _eval_allergy_cied_and_med_hold(ctx)
    _eval_antithrombotic_conflicts(ctx)
    _eval_postop_voyager(ctx)
    _eval_stent_dapt_and_hold_alerts(ctx)
