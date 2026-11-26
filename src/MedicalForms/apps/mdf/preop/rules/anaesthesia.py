"""Structured anesthesiology (ASA, airway, STOP-Bang, wait guard, fasting), DoS WHO Sign-In, VQI & 2D triage."""
from ...models import PreopPatient
from ..calculators import build_surveillance_plan, normalize_glucose_mmol
from ..constants import (
    ANESTH_PLAN_CHOICES,
    CASE_OUTCOME_CHOICES,
    CIED_PLAN_CHOICES,
    MALLAMPATI_CHOICES,
    STATUS_ALERT,
    STATUS_NO_Q,
    STATUS_PROGRESS,
    STATUS_READY,
    VQI_30D_CHOICES,
)
from ..registry import rule_value
from ..utils import (
    _alerts_signature,
    _clean_person_id,
    _date,
    _fmt_ka_date,
    _int,
    _num,
    chip_summary,
    is_pregnancy_eligible,
)


def _eval_asa_airway_and_modality(ctx):
    patient, pathway, q, s, q_filled = ctx.patient, ctx.pathway, ctx.q, ctx.s, ctx.q_filled
    sb_cfg = rule_value('ANES_AIRWAY_STOPBANG_ICU')
    ctx.effective_asa = f"ASA {ctx.asa_roman}{'-E' if (s.get('asa_emergency') or ctx.is_acute_emergency) else ''}"
    if pathway == PreopPatient.Pathway.CEA and not ctx.is_symp_cea and (ctx.age or 0) >= 80 and ctx.asa_roman == 'IV':
        ctx.red_flags.append(
            'ასიმპტომური CEA ≥80 წლის ASA IV პაციენტთან — ESVS 2023-ით 5-წლიანი სარგებელი საეჭვოა, '
            'გადაამოწმეთ კონსერვატიული მედიკამენტური ტაქტიკა (BMT) ქირურგთან!'
        )

    mallampati = s.get('airway_mallampati') if s.get('airway_mallampati') in ('I_II', 'III', 'IV_difficult') else 'I_II'
    ctx.mallampati_label = dict(MALLAMPATI_CHOICES).get(mallampati, 'Mallampati I–II')
    ctx.difficult_airway_alert = (mallampati == 'IV_difficult') or (q_filled and bool(q.get('cea_neck_limited')) and mallampati == 'III')
    if mallampati == 'IV_difficult':
        ctx.amber_flags.append(
            'მოსალოდნელი რთული ინტუბაცია (Mallampati IV) — საოპერაციოში ვიდეოლარინგოსკოპისა და რთული სასუნთქი გზების ნაკრების მზადყოფნა!'
        )

    pulm_chip_set = set(q.get('pulm_chips') or [])
    has_htn_sb = bool(q_filled and q.get('htn')) or bool((ctx.eff_sys is not None and ctx.eff_sys >= 140) or (ctx.eff_dia is not None and ctx.eff_dia >= 90))
    stop_bang_auto = sum([
        bool(ctx.bmi is not None and ctx.bmi > 35),
        bool((ctx.age or 0) > 50),
        has_htn_sb,
        bool(patient.sex == 'M'),
    ]) + (3 if 'osa' in pulm_chip_set else 0)
    auto_sb_tier = 'high' if ('osa' in pulm_chip_set or stop_bang_auto >= sb_cfg['stop_bang_high']) else ('intermediate' if stop_bang_auto >= sb_cfg['stop_bang_int'] else 'low')
    sb_raw = s.get('stop_bang_risk') if s.get('stop_bang_risk') in ('auto', 'low', 'intermediate', 'high') else 'auto'
    effective_stop_bang = auto_sb_tier if sb_raw == 'auto' else sb_raw
    ctx.stop_bang_auto = stop_bang_auto
    ctx.effective_stop_bang = effective_stop_bang
    ctx.stop_bang_label = {'low': 'დაბალი (0–2)', 'intermediate': 'საშუალო (3–4)', 'high': 'მაღალი OSA რისკი (≥5)'}[effective_stop_bang]
    if effective_stop_bang == 'high' and (not ctx.is_venous or ctx.is_phleb):
        ctx.amber_flags.append(
            'მაღალი ობსტრუქციული ძილის აპნოეს რისკი (STOP-Bang ≥5 / OSA) — ოპიოიდების დაზოგვა, პოსტოპ. პულსოქსიმეტრია და CPAP მზადყოფნა.'
        )

    lido_alg = bool(q.get('ven_lidocaine_allergy') or 'lidocaine' in ctx.allergy_chips)
    has_glp1_any = bool(q.get('glp1') or ctx.ft_meds.get('has_glp1'))
    needs_ett_airway = (
        ctx.open_aortic
        or pathway == PreopPatient.Pathway.CEA
        or (bool(q.get('aaa_pain')) or has_glp1_any)
        or (ctx.bmi is not None and ctx.bmi >= 40)
    )
    anesth_plan_raw = ctx.anesth_plan_raw
    if ctx.neuraxial_hard_blocked:
        eff_lbl = 'ზოგადი ანესთეზია (არჩეული ნეირაქსიული უკუნაჩვენებია — შესაცვლელია!)'
    elif lido_alg and (anesth_plan_raw in ('local', 'plexus') or (anesth_plan_raw == 'auto' and (patient.op_subtype == PreopPatient.OpSubtype.EVLA or ctx.is_pta))):
        eff_lbl = 'ანესთეზიოლოგთან შესათანხმებელი (ლიდოკაინზე ალერგია!)'
    elif anesth_plan_raw == 'general' and needs_ett_airway:
        eff_lbl = 'ზოგადი ენდოტრაქეული ანესთეზია' + (' + ეპიდურული ანალგეზია' if 'epidural' in ctx.anesth_lines_set else '')
    elif anesth_plan_raw != 'auto':
        eff_lbl = dict(ANESTH_PLAN_CHOICES).get(anesth_plan_raw, anesth_plan_raw)
    elif patient.op_subtype == PreopPatient.OpSubtype.EVLA:
        eff_lbl = 'ტუმესცენტური ადგილობრივი ანესთეზია'
    elif ctx.is_pta:
        eff_lbl = 'სედაცია / ანესთეზიოლოგიური მხარდაჭერა (ზურგზე წოლის შეზღუდვა)' if (q_filled and q.get('pta_cant_lie_flat')) else 'ადგილობრივი ანესთეზია (± სედაცია)'
    elif pathway == PreopPatient.Pathway.CEA:
        eff_lbl = (
            'ზოგადი ენდოტრაქეული ანესთეზია (ლიდოკაინზე ალერგია — ადგილობრივი/წნულის გაუტკივარება უკუნაჩვენებია!)'
            if lido_alg else 'ზოგადი ენდოტრაქეული ან რეგიონული ბლოკადა (ცერებრული მონიტორინგით)'
        )
    elif pathway == PreopPatient.Pathway.AAA_INFRA and patient.op_subtype == 'tbd':
        eff_lbl = (
            'ზოგადი ენდოტრაქეული ანესთეზია (ლიდოკაინზე ალერგია — გაუტკივარება ანესთეზიოლოგთან!)'
            if lido_alg else 'ზუსტდება CTA ტაქტიკით (ღია: ზოგადი ენდოტრაქეული vs EVAR: ზოგადი / ადგილობრივი)'
        )
    elif ctx.is_phleb or patient.op_subtype == 'evar' or ctx.is_pad_bypass:
        if ctx.hard_nx_reasons:
            base_ga_lbl = 'ზოგადი ენდოტრაქეული ანესთეზია' if needs_ett_airway else 'ზოგადი ანესთეზია'
            eff_lbl = f'{base_ga_lbl} (ნეირაქსიული შეზღუდულია!)'
        elif ctx.is_phleb:
            eff_lbl = 'სპინალური / ზოგადი ანესთეზია'
        elif patient.op_subtype == 'evar':
            eff_lbl = (
                'ზოგადი ენდოტრაქეული ანესთეზია (ლიდოკაინზე ალერგია!)'
                if lido_alg else 'ზოგადი ან ადგილობრივი ანესთეზია (სპინალურის არჩევისას — ASRA 72 სთ რეჟიმი)'
            )
        else:
            eff_lbl = 'ზოგადი ანესთეზია (სპინალურის არჩევისას — ASRA 72 სთ რეჟიმი)'
    else:
        if 'epidural' in ctx.anesth_lines_set:
            eff_lbl = 'ზოგადი ენდოტრაქეული + ეპიდურული ანალგეზია'
        elif ctx.hard_nx_reasons:
            eff_lbl = 'ზოგადი ენდოტრაქეული ანესთეზია (ეპიდურული შეზღუდულია!)'
        else:
            eff_lbl = 'ზოგადი ენდოტრაქეული ანესთეზია'
    ctx.effective_anesth_label = eff_lbl
    ctx.anesth_lines_txt = chip_summary(s.get('anesth_lines_chips'), 'anesth_lines_chips')
    cied_plan_raw = s.get('cied_plan') if s.get('cied_plan') in ('pending', 'bipolar_only', 'magnet_async', 'ep_reprogram') else 'pending'
    ctx.cied_plan_label = dict(CIED_PLAN_CHOICES).get(cied_plan_raw, '') if ctx.needs_cied and cied_plan_raw != 'pending' else ''
    ctx.needs_icu_bed = bool(
        ctx.open_aortic or ctx.asa_roman == 'IV'
        or (ctx.lvef is not None and ctx.lvef < 40)
        or bool(q.get('cea_crescendo'))
        or bool(ctx.anesth_lines_set & {'art_line', 'cvc', 'epidural'})
    )


def _eval_wait_fasting_and_allergy_summary(ctx):
    patient, pathway, q, s, q_filled = ctx.patient, ctx.pathway, ctx.q, ctx.s, ctx.q_filled
    cea_win_d = rule_value('CEA_SYMP_WINDOW_DAYS')[0]
    aaa_wait_giant, aaa_wait_std = rule_value('AAA_WAIT_TARGET_DAYS')
    clti_inf_d, clti_std_d = rule_value('PAD_CLTI_WAIT_DAYS')
    glp_cfg = rule_value('MED_GLP1_FASTING_PROTOCOL')
    ctx.person_id = _clean_person_id(s.get('person_id'))
    ctx.birth_date_ka = _fmt_ka_date(s.get('birth_date'))
    op_dt = _date(s.get('planned_surgery_date'))
    ctx.planned_surgery_ka, ctx.planned_surgery_short = _fmt_ka_date(op_dt), _fmt_ka_date(op_dt, short=True)
    ctx.operated_date_ka = _fmt_ka_date(s.get('operated_date'))
    ctx.case_outcome_label = dict(CASE_OUTCOME_CHOICES).get(ctx.case_outcome, '')

    if op_dt and ctx.is_active_case:
        ref_start = _date(patient.started_on) or _date(patient.visit_date) or ctx.today
        wait_to_op = (op_dt - ref_start).days
        if pathway == PreopPatient.Pathway.CEA and ctx.is_symp_cea:
            ev_dt = _date(q.get('cea_event_date'))
            symp_to_op = (
                (op_dt - ev_dt).days if ev_dt
                else ((ctx.days_since_ev + (op_dt - ctx.today).days) if (ctx.days_since_ev is not None and op_dt >= ctx.today) else None)
            )
            if symp_to_op is not None and symp_to_op > cea_win_d:
                ctx.amber_flags.append(
                    f'დაგეგმილი CEA-ს თარიღი ({ctx.planned_surgery_ka}) სცდება სიმპტომიდან 14-დღიან ფანჯარას '
                    f'({symp_to_op} დღე — ESVS 2023 / NVR სამიზნე ≤14 დღე)! განიხილეთ უფრო ადრეული საოპერაციო სლოტი.'
                )
        elif pathway == PreopPatient.Pathway.AAA_INFRA and ctx.aaa_mm is not None:
            min_aaa_thresh = rule_value('AAA_FEMALE_MIN_MM') if patient.sex == 'F' else rule_value('AAA_MALE_MIN_MM')
            if ctx.aaa_mm >= rule_value('AAA_GIANT_MM') and wait_to_op > aaa_wait_giant:
                ctx.amber_flags.append(
                    f'გიგანტური AAA ({ctx.aaa_mm} მმ): დაგეგმილი ოპერაციის თარიღი ({ctx.planned_surgery_ka}) დაშორებულია {wait_to_op} დღით (>14 დღე — მაღალი რუპტურის რისკი)!'
                )
            elif ctx.aaa_mm >= min_aaa_thresh and wait_to_op > aaa_wait_std:
                ctx.amber_flags.append(
                    f'AAA ({ctx.aaa_mm} მმ): დაგეგმილი ოპერაციის მოლოდინი {wait_to_op} დღეა (UK NVR / ESVS სამიზნე ≤8 კვირა / 56 დღე).'
                )
        elif getattr(ctx, 'is_ali_acute', False):
            if wait_to_op > clti_inf_d:
                ctx.amber_flags.append(
                    f'მწვავე კიდურის იშემია (ALI): დაგეგმილი ოპერაცია ({ctx.planned_surgery_ka}) დაშორებულია {wait_to_op} დღით (>{clti_inf_d} დღე) — კიდურის დაკარგვის უმაღლესი რისკი!'
                )
        elif ctx.is_clti_any:
            clti_lim = clti_inf_d if (q_filled and q.get('abf_wet_infection')) else clti_std_d
            if wait_to_op > clti_lim:
                ctx.amber_flags.append(
                    f'CLTI პაციენტის დაგეგმილი ოპერაცია ({ctx.planned_surgery_ka}) დაშორებულია {wait_to_op} დღით (UK NVR სამიზნე ≤{clti_lim} დღე) — კიდურის დაკარგვის რისკი!'
                )

    lido_alg = bool(q.get('ven_lidocaine_allergy') or 'lidocaine' in ctx.allergy_chips)
    has_glp1_any = bool(q.get('glp1') or ctx.ft_meds.get('has_glp1'))
    is_local_evla_fasting = (
        patient.op_subtype == PreopPatient.OpSubtype.EVLA and not ctx.has_neuraxial_plan
        and ctx.anesth_plan_raw in ('auto', 'local') and not lido_alg and not bool(ctx.hard_contraindications)
    )
    ctx.is_local_evla_fasting, ctx.has_glp1_any = is_local_evla_fasting, has_glp1_any
    if has_glp1_any:
        ctx.fasting_instruction = (
            f"GLP-1 პროტოკოლი (Multi-Society 2024 / ESAIC 2025): გართულების გარეშე უმეტეს პაციენტთან პრეპარატი გრძელდება, "
            f"სავალდებულოა ოპერაციამდე {glp_cfg['liquid_hours']} სთ მხოლოდ სუფთა თხევადი დიეტა, მყარი საკვები შეწყდეს ≥{glp_cfg['solid_hours']} სთ-ით ადრე, "
            f"სუფთა წყალი — {glp_cfg['water_hours']} სთ-ით ადრე (GI სიმპტომებისას — შეჩერების განხილვა და კუჭის ექო-სკრინინგი / RSI)."
        )
    elif is_local_evla_fasting:
        ctx.fasting_instruction = (
            'ადგილობრივი ტუმესცენტური EVLA: მსუბუქი საუზმე დაშვებულია (თუ სედაცია ან უზმოზე მოსვლა არ არის დაგეგმილი — ESVS 2022).'
        )
    else:
        ctx.fasting_instruction = (
            'ESAIC / ERAS უზმოდ ყოფნის წესი: მყარი საკვები შეწყდეს ოპერაციამდე 6 საათით ადრე · '
            'სუფთა გამჭვირვალე წყალი დასაშვებია ოპერაციამდე 2 საათით ადრე (დილის დაშვებული წამლები — ყლუპი წყლით).'
        )
    ctx.surveillance_plan = build_surveillance_plan(patient)

    allergy_bits = []
    if 'hit_heparin' in ctx.allergy_chips:
        allergy_bits.append('HIT — ჰეპარინი/LMWH აკრძალულია!')
    if 'beta_lactam' in ctx.allergy_chips:
        allergy_bits.append('β-ლაქტამი (ცეფაზოლინი არა!)')
    elif 'penicillin_mild' in ctx.allergy_chips:
        allergy_bits.append('პენიცილინი (მსუბუქი გამონაყარი — ცეფაზოლინი დასაშვებია)')
    if lido_alg:
        allergy_bits.append('ლიდოკაინი / ადგილობრივი ანესთეტიკი!')
    if 'latex_chlorhex' in ctx.allergy_chips:
        allergy_bits.append('ლატექსი / ქლორჰექსიდინი!')
    if ctx.eff_allergy_iodine:
        allergy_bits.append('იოდი / კონტრასტი!')
    if 'nsaid' in ctx.allergy_chips:
        allergy_bits.append('აასს / NSAID ალერგია!')
    if 'clopidogrel_intol' in ctx.allergy_chips:
        allergy_bits.append('კლოპიდოგრელის აუტანლობა')
    raw_alg_txt = (q.get('allergy_text') or '').strip()
    is_dup_lido_txt = lido_alg and raw_alg_txt.lower() in ('ლიდოკაინი', 'ლიდოკაინზე ალერგია', 'ალერგია ლიდოკაინზე', 'lidocaine', 'lidocaine allergy')
    if raw_alg_txt and not is_dup_lido_txt and not any(raw_alg_txt.lower() in b.lower() for b in allergy_bits):
        allergy_bits.append(raw_alg_txt)
    elif not raw_alg_txt and (q.get('allergy_other') or ctx.unclarified_allergy_other) and not allergy_bits:
        allergy_bits.append('მედიკამენტური ალერგია (დასაზუსტებელია!)')
    ctx.allergy_summary = '; '.join(allergy_bits)


def _build_dos_checks(ctx):
    patient, pathway, q, s = ctx.patient, ctx.pathway, ctx.q, ctx.s
    cefaz_3g_kg = rule_value('ALLERGY_HIT_ABX_PROPHYLAXIS')['cefazolin_3g_kg']
    glp_cfg, ketone_max = rule_value('MED_GLP1_FASTING_PROTOCOL'), rule_value('MED_SGLT2_HOLD_DAYS')['ketone_max']
    dos_checks = [{
        'key': 'dos_identity_consent',
        'label': 'პაციენტის იდენტიფიკაცია (პირადი N / დაბ. თარიღი) და ხელმოწერილი ინფორმირებული თანხმობა',
        'done': bool(s.get('dos_identity_consent')),
    }]
    if pathway not in (PreopPatient.Pathway.AAA_INFRA, PreopPatient.Pathway.ABF):
        valid_dos_sides = ('R', 'L') if pathway == PreopPatient.Pathway.CEA else ('R', 'L', 'B')
        side_hint = (
            'CEA-ზე მხარე B უკუნაჩვენებია — მიუთითეთ R ან L!'
            if (pathway == PreopPatient.Pathway.CEA and patient.side == 'B')
            else {'R': 'მარჯვენა', 'L': 'მარცხენა', 'B': 'ორმხრივი'}.get(patient.side, 'მხარე მიუთითეთ!')
        )
        dos_checks.append({
            'key': 'dos_site_marked', 'label': f'საოპერაციო მხარის მარკირება კანზე ({side_hint}) — WHO Sign-In',
            'done': bool(s.get('dos_site_marked')) and (patient.side in valid_dos_sides),
        })
    if ctx.has_glp1_any:
        dos_fast_lbl = f"უზმოდ ყოფნის რეჟიმი დაცულია: GLP-1 პროტოკოლი ({glp_cfg['liquid_hours']} სთ სუფთა თხევადი დიეტა / ≥{glp_cfg['solid_hours']} სთ მყარი საკვები / {glp_cfg['water_hours']} სთ წყალი)"
    elif ctx.is_local_evla_fasting:
        dos_fast_lbl = 'ადგილობრივი EVLA: სედაციის გარეშე მსუბუქი საუზმე დაშვებულია (სედაციისას — 6 სთ მყარი საკვები / 2 სთ წყალი)'
    else:
        dos_fast_lbl = 'უზმოდ ყოფნის რეჟიმი დაცულია (6 სთ მყარი საკვები / 2 სთ სუფთა წყალი)'
    dos_checks.append({'key': 'dos_fasting_confirmed', 'label': dos_fast_lbl, 'done': bool(s.get('dos_fasting_confirmed'))})
    if ctx.needs_med_hold or ctx.has_any_antithrombotic or any(r.get('hold_or_skip') for r in (ctx.med_schedule or ())) or ('steroid' in ctx.med_chips):
        dos_checks.append({
            'key': 'dos_med_hold_verified',
            'label': 'ანტიკოაგულანტის / P2Y12 / SGLT2 / ინსულინის / სტეროიდის რეჟიმი და დილის დაშვებული წამლების მიღება გადამოწმდა',
            'done': bool(s.get('dos_med_hold_verified')),
        })
    if q.get('sglt2') or ('sglt2' in ctx.ft_meds.get('matched_keys', ())):
        dos_checks.append({
            'key': 'dos_ketones_ok',
            'label': f'SGLT2 უსაფრთხოება (ეუგლიკემიური DKA-ს გამორიცხვა): სისხლის/შარდის კეტონები ნორმაშია (β-OHB < {ketone_max} მმოლ/ლ) და პრეპარატი შეჩერებულია ≥72–96 სთ',
            'done': bool(s.get('dos_ketones_ok')),
        })
    k_eff = getattr(ctx, 'k_val_sat', ctx.k_val)
    if ctx.on_dialysis or (k_eff is not None and k_eff >= 5.4):
        k_lbl = (
            'დიალიზის შემდგომი საკონტროლო კალიუმი (K+ < 5.4 მმოლ/ლ) გადამოწმებულია და AV ფისტულის მკლავი დაცულია (მანჟეტი/კათეტერი აკრძალულია!)'
            if ctx.on_dialysis else
            f'საბაზისო ჰიპერკალიემიის (K+ {ctx.k_val if ctx.k_val is not None else k_eff}) შემდგომი საკონტროლო კალიუმი (K+ < 5.4 მმოლ/ლ) გადამოწმებულია'
        )
        dos_checks.append({'key': 'dos_k_verified', 'label': k_lbl, 'done': bool(s.get('dos_k_verified'))})
    if 'latex_chlorhex' in ctx.allergy_chips:
        ant_lbl = (
            'ალტერნატიული უიოდო ანტისეპტიკი (ლატექსი/ქლორჰექსიდინი + იოდზე ალერგია!)'
            if ctx.eff_allergy_iodine else 'პოვიდონ-იოდის ანტისეპტიკი (ქლორჰექსიდინის გარეშე!)'
        )
        dos_checks.append({'key': 'dos_latex_free_or', 'label': f'ულატექსო საოპერაციო გარემო (ხელთათმანები/კათეტერი) და {ant_lbl} მზადაა', 'done': bool(s.get('dos_latex_free_or'))})
    needs_bhcg = is_pregnancy_eligible(patient) and (
        bool(q.get('pregnant_possible') or q.get('ven_pregnant'))
        or (patient.sex == 'F' and (patient.op_subtype in (PreopPatient.OpSubtype.EVAR, PreopPatient.OpSubtype.TBD) or ctx.is_pta or ctx.is_venous))
    )
    if needs_bhcg:
        dos_checks.append({
            'key': 'dos_bhcg_negative',
            'label': 'ორსულობის გამორიცხვა (β-hCG უარყოფითია) — ფლუოროსკოპიის/კონტრასტის/ვენური ჩარევის უსაფრთხოება (12–55 წ. ქალი)',
            'done': bool(s.get('dos_bhcg_negative')),
        })
    ctx.is_uncomplicated_pta_abx = ctx.is_pta and not (q.get('abf_wet_infection') or q.get('abf_groin_issue'))
    if not ((ctx.is_venous and patient.op_subtype == PreopPatient.OpSubtype.EVLA) or ctx.is_uncomplicated_pta_abx):
        wt_eff = getattr(ctx, 'weight_sat', ctx.weight_num)
        cefaz_dose = f'3 გ (≥{cefaz_3g_kg} კგ!)' if (wt_eff is not None and wt_eff >= cefaz_3g_kg) else '2 გ'
        abx_hint = 'ვანკომიცინი / კლინდამიცინი (β-ლაქტამზე ალერგია!)' if 'beta_lactam' in ctx.allergy_chips else f'ცეფაზოლინი {cefaz_dose} (ჭრილობამდე ≤60 წთ)'
        if ctx.unclarified_allergy_other:
            abx_hint += ' [დააზუსტეთ უცნობი ალერგია შეყვანამდე!]'
        dos_checks.append({'key': 'dos_abx_ready', 'label': f'ანტიბიოტიკოპროფილაქტიკა მზადაა: {abx_hint}', 'done': bool(s.get('dos_abx_ready'))})
    if ctx.open_major or patient.op_subtype == 'evar' or ctx.has_synthetic_implant:
        decol_sfx = ''
        if ctx.has_synthetic_implant:
            decol_sfx = (
                ' · ანტისეპტიკა: ალტერნატიული უიოდო (ქლორჰექსიდინი + იოდის ალერგია!)'
                if ('latex_chlorhex' in ctx.allergy_chips and ctx.eff_allergy_iodine) else (
                    ' · ანტისეპტიკა: პოვიდონ-იოდი (ქლორჰექსიდინზე ალერგია!)' if 'latex_chlorhex' in ctx.allergy_chips
                    else (' · ქლორჰექსიდინის შხაპი ✓' if s.get('decolonization_ready') else ' · ანტისეპტიკური შხაპი')
                )
            )
        dos_checks.append({
            'key': 'dos_blood_implant_ready',
            'label': f'ერითრომასის რეზერვი / სისხლის ჯგუფი და სისხლძარღვოვანი პროთეზი/სტენტ-გრაფტი საოპერაციოშია{decol_sfx}',
            'done': bool(s.get('dos_blood_implant_ready')),
        })
    if ctx.needs_icu_bed:
        dos_checks.append({'key': 'icu_bed_booked', 'label': 'პოსტოპერაციული რეანიმაციის (ICU / HDU) საწოლი დაჯავშნილია და დადასტურებულია', 'done': bool(s.get('icu_bed_booked'))})
    return dos_checks


def _eval_dos_and_vqi(ctx):
    patient, q, s = ctx.patient, ctx.q, ctx.s
    raw_s = getattr(ctx, 'raw_s', None) or {}
    inv_in = s.get('_invalid_inputs') if isinstance(s.get('_invalid_inputs'), dict) else {}
    dos_cfg = rule_value('DOS_WHO_SIGNIN_THRESHOLDS')
    dos_checks = _build_dos_checks(ctx)

    dos_sys, dos_dia = _int(s.get('dos_bp_sys'), lo=40, hi=300), _int(s.get('dos_bp_dia'), lo=20, hi=200)
    dos_gl = normalize_glucose_mmol(s.get('dos_glucose'))
    dos_inr = _num(s.get('dos_inr'), lo=0.5, hi=15)
    dos_warnings = list(ctx.hard_contraindications)

    # Extreme out-of-bounds Day-of-Surgery values must trigger a hard DoS STOP rather than silently vanishing
    for raw_k, parsed_v, lbl in (
        ('dos_bp_sys', dos_sys, 'ოპერაციის დილის წნევა (SBP)'),
        ('dos_bp_dia', dos_dia, 'ოპერაციის დილის წნევა (DBP)'),
        ('dos_glucose', dos_gl, 'ოპერაციის დილის გლიკემია'),
        ('dos_inr', dos_inr, 'ოპერაციის დილის INR'),
    ):
        raw_v = raw_s[raw_k] if raw_k in raw_s else s.get(raw_k)
        if (raw_v is None or str(raw_v).strip() == '') and raw_k in inv_in:
            raw_v = inv_in[raw_k]
        if raw_v is not None and str(raw_v).strip() != '' and parsed_v is None:
            dos_warnings.append(f'{lbl} კრიტიკულად დაუშვებელია ({raw_v}) — გადაამოწმეთ მაჩვენებელი საოპერაციოში შეყვანამდე!')

    if dos_sys is not None and dos_dia is not None and dos_sys <= dos_dia:
        dos_warnings.append(f'ოპერაციის დილის წნევის არასწორი ჩანაწერი (SBP ≤ DBP: {dos_sys}/{dos_dia} mmHg) — გადაამოწმეთ მანჟეტი და მაჩვენებელი!')
    elif (dos_sys and dos_sys >= dos_cfg['sbp_hi']) or (dos_dia and dos_dia >= dos_cfg['dbp_hi']):
        dos_warnings.append(f'ოპერაციის დილის კრიტიკული ჰიპერტენზია ({dos_sys or "?"}/{dos_dia or "?"} mmHg >= 180/110) — გეგმური ინდუქცია საშიშია, საჭიროა სტაბილიზაცია!')
    elif dos_sys and dos_sys < dos_cfg['sbp_lo']:
        dos_warnings.append(f'ოპერაციის დილის ჰიპოტენზია ({dos_sys}/{dos_dia or "?"} mmHg < 90) — სასწრაფოდ შეფასდეს ჰემოდინამიკა!')
    if dos_gl is not None and (dos_gl >= dos_cfg['gl_hi'] or dos_gl < dos_cfg['gl_lo']):
        dos_warnings.append(f'ოპერაციის დილის კრიტიკული გლიკემია ({dos_gl} მმოლ/ლ) — საჭიროა კორექცია საოპერაციოში შეყვანამდე!')
    elif dos_gl is not None and dos_gl > dos_cfg['gl_amb']:
        ctx.amber_flags.append(f'ოპერაციის დილის ჰიპერგლიკემია ({dos_gl} მმოლ/ლ > 10.0) — სასურველია ინსულინით კორექცია პერიოპერაციული ინფექციის შესამცირებლად.')

    is_evla_warfarin_only = (
        ctx.is_venous and patient.op_subtype == PreopPatient.OpSubtype.EVLA
        and not ctx.has_neuraxial_plan and bool(q.get('med_warfarin'))
    )
    inr_lim = dos_cfg['inr_evla'] if is_evla_warfarin_only else (dos_cfg['inr_nx'] if ctx.has_neuraxial_plan else dos_cfg['inr_std'])
    any_dos_ticked = any(c['done'] for c in dos_checks)
    if dos_inr is not None:
        if dos_inr > inr_lim:
            dos_warnings.append(f'ოპერაციის დილის INR ({dos_inr}) აჭარბებს უსაფრთხო ზღვარს (≤ {inr_lim}) — სისხლდენის / სპინალური ჰემატომის რისკი!')
    elif any_dos_ticked:
        if is_evla_warfarin_only and ctx.inr is None:
            dos_warnings.append('ვარფარინის ფონზე EVLA-მდე აუცილებელია საკონტროლო INR (≤ 3.0) — ამჟამად INR შეყვანილი არ არის!')
        elif ctx.inr is not None and ctx.inr > inr_lim:
            dos_warnings.append(f'საბაზისო INR ({ctx.inr}) აჭარბებს ზღვარს (≤ {inr_lim}), ხოლო ოპერაციის დილის საკონტროლო INR შეყვანილი არ არის!')
        elif q.get('med_warfarin') and not is_evla_warfarin_only:
            dos_warnings.append('ვარფარინის ფონზე ოპერაციის დილის საკონტროლო INR სავალდებულოა საოპერაციოში შეყვანამდე!')

    ctx.dos_checks, ctx.dos_warnings, ctx.dos_gl = dos_checks, dos_warnings, dos_gl
    ctx.has_hard_contraindication = bool(ctx.hard_contraindications)
    ctx.dos_hold_danger = bool(dos_warnings)
    if ctx.dos_hold_danger or ctx.has_hard_contraindication or ctx.solo_neuraxial_anatomic_mismatch:
        ctx.is_local_evla_fasting = False
        if ctx.has_glp1_any:
            ctx.fasting_instruction = 'STOP — ოპერაცია დაბლოკილია! 24-საათიანი თხევადი დიეტა და GLP-1 რეჟიმი არ დაიწყოთ ექიმის დადასტურებამდე!'
        else:
            ctx.fasting_instruction = 'STOP — ოპერაცია შეჩერებულია საოპერაციო დილის / უკუჩვენების დაზუსტებამდე (უზმოდ ყოფნის რეჟიმი შეათანხმეთ ექიმთან)!'
    ctx.dos_done_count = sum(1 for c in dos_checks if c['done'])
    ctx.dos_total_count = len(dos_checks)
    ctx.vqi_bundle_applicable = not ctx.is_venous
    ctx.vqi_bundle_done = (
        bool(s.get('vqi_discharge_antiplatelet') and s.get('vqi_discharge_statin'))
        if ctx.vqi_bundle_applicable else bool(s.get('vqi_surveillance_scheduled'))
    )
    ctx.vqi_30d_status = s.get('vqi_30d_status') if s.get('vqi_30d_status') in dict(VQI_30D_CHOICES) else 'pending'
    ctx.vqi_30d_label = dict(VQI_30D_CHOICES).get(ctx.vqi_30d_status, '')


def _eval_triage_and_passport_cautions(ctx):
    patient, q, s, q_filled = ctx.patient, ctx.q, ctx.s, ctx.q_filled

    if ctx.all_tasks_done and ctx.priority_flags:
        prio_set = set(ctx.priority_flags)
        ctx.red_flags = [f for f in ctx.red_flags if f not in prio_set]
        for pf in ctx.priority_flags:
            ctx.amber_flags.insert(0, f'[სასწრაფო პრიორიტეტი] {pf}')
    ctx.amber_flags = list(dict.fromkeys(ctx.amber_flags))

    passport_cautions = [af for af in ctx.amber_flags if af.startswith('[სასწრაფო პრიორიტეტი]')]
    if ctx.cardio_cleared and s.get('echo_valve_issue'):
        passport_cautions.append(
            'კარდიო-სიფრთხილე (დაშვებულია ✓): მნიშვნელოვანი სარქვლოვანი პათოლოგია / AS — მოერიდეთ სწრაფ ვაზოდილატაციას/ჰიპოტენზიას (A-line, ვაზოპრესორი)!'
        )
    if ctx.cardio_cleared and ctx.lvef is not None and ctx.lvef < 40:
        passport_cautions.append(
            f'კარდიო-სიფრთხილე (დაშვებულია ✓): დაქვეითებული განდევნის ფრაქცია (LVEF {ctx.lvef}%) — ფრთხილი ინფუზია და ჰემოდინამიკური კონტროლი.'
        )
    if ctx.anesth_cleared and ctx.spiro_fev1 is not None and ctx.spiro_fev1 < 50:
        passport_cautions.append(
            f'პულმო-სიფრთხილე (დაშვებულია ✓): მძიმე სპირომეტრიული დარღვევა (FEV1 {ctx.spiro_fev1}%) — ფილტვ-დამზოგველი ვენტილაცია და პოსტოპ. მონიტორინგი.'
        )
    for af in ctx.amber_flags:
        if len(passport_cautions) >= 6:
            break
        if af not in passport_cautions and any(
            kw in af for kw in (
                'ასიმეტრია', 'სასაზღვრო ჰიპოტენზია', 'ვიწრო პულსური', 'STOP-Bang',
                '14-დღიან ფანჯარას', 'რუპტურის რისკი', 'კიდურის დაკარგვის', 'NT-proBNP', 'Mallampati IV',
            )
        ):
            passport_cautions.append(af)
    ctx.passport_cautions = passport_cautions

    if ctx.is_urgent_case:
        priority_tier = 'urgent'
    elif (
        (ctx.aaa_mm is not None and ctx.aaa_mm >= 65)
        or (ctx.ipsi_car is not None and ctx.ipsi_car >= 90)
        or (ctx.is_venous and q_filled and q.get('ven_ceap') == 'C6')
        or (not ctx.is_venous and not ctx.is_on_hold and ctx.is_active_case and ctx.days >= max(4, ctx.t_days - 1))
    ):
        priority_tier = 'high'
    elif ctx.is_venous:
        priority_tier = 'low'
    else:
        priority_tier = 'moderate'
    ctx.priority_tier = priority_tier

    ctx.current_sig = _alerts_signature(ctx.red_flags)
    ctx.alerts_acked = bool(patient.alerts_ack_at and s.get('alerts_ack_sig') == ctx.current_sig)
    ctx.has_unacked_red = bool(ctx.red_flags) and not ctx.alerts_acked
    ctx.dos_ready = bool(
        q_filled and (ctx.dos_done_count == ctx.dos_total_count)
        and not ctx.dos_hold_danger and not ctx.has_unacked_red and not ctx.neuraxial_hard_blocked
    )

    if ctx.has_unacked_red or ctx.has_hard_contraindication or ctx.dos_hold_danger or ctx.neuraxial_hard_blocked:
        ctx.status = STATUS_ALERT
    elif not q_filled:
        ctx.status = STATUS_NO_Q
    elif patient.approved_at or ctx.all_tasks_done:
        ctx.status = STATUS_READY
    else:
        ctx.status = STATUS_PROGRESS
    ctx.over_target = ctx.days > ctx.t_days and not patient.approved_at and not ctx.is_venous and not ctx.is_on_hold and ctx.is_active_case


def eval_anaesthesia_and_triage(ctx):
    _eval_asa_airway_and_modality(ctx)
    _eval_wait_fasting_and_allergy_summary(ctx)
    _eval_dos_and_vqi(ctx)
    _eval_triage_and_passport_cautions(ctx)
