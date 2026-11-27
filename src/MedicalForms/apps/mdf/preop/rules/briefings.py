"""Quantitative risk scores, nurse guided UX, clearance seal, and surgical & anesthetic briefings."""
from ...models import PreopPatient
from ..anamnesis import build_self_fill_highlights
from ..calculators import _ihd_and_chf_flags, calc_ariscat_pulm, calc_rcri, calc_vsg_cri
from ..constants import AAA_MORPHOLOGY_CHOICES, AAA_NECK_CHOICES, POPLITEAL_SCREEN_CHOICES, STATUS_READY
from ..meds import _apply_stop_lock_to_med_schedule
from ..registry import rule_value
from ..utils import _num, is_self_filled_q


def _eval_scores_and_frailty(ctx):
    patient, q, s, q_filled = ctx.patient, ctx.q, ctx.s, ctx.q_filled
    nut_cfg = rule_value('LAB_NUTRITION_ALBUMIN_LIVER')
    sb_cfg = rule_value('ANES_AIRWAY_STOPBANG_ICU')
    ctx.rcri = calc_rcri(patient, q, s, is_symp_cea=ctx.is_symp_cea)
    ctx.vsg_cri = calc_vsg_cri(patient, q, s)
    ctx.ariscat = calc_ariscat_pulm(patient, q, s, hb_gl=ctx.hb_gl)
    ctx.ihd_flag, ctx.chf_flag = _ihd_and_chf_flags(q, s)

    frailty_notes = []
    if q_filled and q.get('cfs_level') == '5_plus':
        frailty_notes.append('კლინიკური დაუძლურება (CFS ≥ 5)')
    elif q_filled and q.get('cfs_level') == '4':
        frailty_notes.append('მოწყვლადობა (CFS 4)')
    if ctx.alb_gl is not None and ctx.alb_gl < nut_cfg['alb_amb']:
        frailty_notes.append(f'ჰიპოალბუმინემია ({ctx.alb_gl} გ/ლ)')
    if ctx.bmi is not None and ctx.bmi < nut_cfg['bmi_cachexia']:
        frailty_notes.append(f'მძიმე კახექსია (BMI {ctx.bmi}!)')
    elif ctx.bmi is not None and ctx.bmi < nut_cfg['bmi_underweight']:
        frailty_notes.append(f'დეფიციტური წონა (BMI {ctx.bmi})')
    elif ctx.bmi is not None and ctx.bmi >= sb_cfg['sb_bmi']:
        frailty_notes.append(f'სიმსუქნე II–III ხ. (BMI {ctx.bmi})')
    if q_filled and q.get('cea_disabling_stroke'):
        frailty_notes.append('შეზღუდული მობილობა (mRS ≥ 3)')
    age_eff_f = ctx.age if ctx.age is not None else getattr(ctx, 'age_sat', None)
    if (age_eff_f or 0) >= sb_cfg['cea_asymp_futility_age']:
        frailty_notes.append(f'ასაკი {ctx.age if ctx.age is not None else int(age_eff_f)} წ.')
    ctx.needs_delirium_guard = bool((q_filled and q.get('cfs_level') in ('4', '5_plus')) or (age_eff_f or 0) >= sb_cfg['delirium_age'])
    if ctx.needs_delirium_guard and not ctx.is_venous:
        frailty_notes.append('დელირიუმის პრევენცია (ESAIC 2024)')
    ctx.frailty_notes = frailty_notes


def _eval_scores_and_missing_s1(ctx):
    _eval_scores_and_frailty(ctx)
    patient, pathway, q, s, q_filled = ctx.patient, ctx.pathway, ctx.q, ctx.s, ctx.q_filled

    s1_required_fields = []
    missing_items = []
    if not q_filled:
        missing_items.append({'tab': 'q', 'field': 'mets_ok', 'label': 'კითხვარი'})
    elif is_self_filled_q(q) and not q.get('nurse_verified'):
        missing_items.append({'tab': 'q', 'field': 'btn-verify-self', 'label': 'პაციენტის კითხვარის გადამოწმება'})

    def _req_s1(fname, missing_cond, lbl):
        s1_required_fields.append(fname)
        if missing_cond:
            missing_items.append({'tab': 's1', 'field': fname, 'label': lbl})

    if ctx.is_venous:
        is_lean_evla_ux = (not ctx.is_phleb) and (not ctx.has_neuraxial_plan) and (ctx.anesth_plan_raw != 'general')
        ven_needs_creat_ux = ctx.is_phleb or ctx.has_neuraxial_plan or bool(
            q.get('med_doac') or q.get('med_dabigatran') or q.get('metformin')
            or ('rivaroxaban_2_5' in ctx.med_chips) or ('cilostazol' in ctx.med_chips)
        )
        ven_needs_inr_ux = (not is_lean_evla_ux) or ctx.on_oral_anticoag or (s.get('liver_status') in ('2x', '3x'))
        ven_needs_gl_ux = getattr(ctx, 'ven_needs_gl', not is_lean_evla_ux)
        _req_s1('hb', ctx.hb_gl is None, 'Hb')
        _req_s1('plt', ctx.plt is None, 'PLT')
        if ven_needs_inr_ux:
            _req_s1('inr', ctx.inr is None, 'INR')
        if ven_needs_gl_ux:
            _req_s1('glucose', ctx.gl_mmol is None, 'გლუკოზა')
        if ven_needs_creat_ux and not ctx.on_dialysis:
            _req_s1('creat', not ctx.renal_lab_present, 'კრეატინინი')
        _req_s1('ven_duplex_status', (s.get('ven_duplex_status') or 'pending') == 'pending', 'ვენების დუპლექსი')
        if ctx.needs_ecg_venous:
            _req_s1('ecg', ctx.ecg == 'pending', 'ეკგ')
    elif ctx.is_pta:
        _req_s1('hb', ctx.hb_gl is None, 'Hb')
        _req_s1('plt', ctx.plt is None, 'PLT')
        _req_s1('inr', ctx.inr is None, 'INR')
        if not ctx.on_dialysis:
            _req_s1('creat', not ctx.renal_lab_present, 'კრეატინინი')
        _req_s1('glucose', ctx.gl_mmol is None, 'გლუკოზა')
        _req_s1('abi_r' if patient.side != 'L' else 'abi_l', not ctx.target_limb_hemo_present, 'ABI / TP')
        _req_s1('pta_access_site', (s.get('pta_access_site') or 'pending') == 'pending', 'პუნქციის მიდგომა')
        _req_s1('ecg', ctx.ecg == 'pending', 'ეკგ')
    else:
        _req_s1('hb', ctx.hb_gl is None, 'Hb')
        _req_s1('inr', ctx.inr is None, 'INR')
        if not ctx.on_dialysis:
            _req_s1('creat', not ctx.renal_lab_present, 'კრეატინინი')
        if ctx.has_uti_symptoms:
            _req_s1('urine_wbc', ctx.urine_wbc is None, 'შარდის ლეიკოციტები')
        _req_s1('ecg', ctx.ecg == 'pending', 'ეკგ')
        if ctx.needs_echo_or_bnp:
            s1_required_fields.extend(['lvef', 'nt_probnp'])
            if not ctx.cardiac_screen_done:
                missing_items.append({'tab': 's1', 'field': 'lvef', 'label': 'ექო (LVEF) ან NT-proBNP'})
        if ctx.needs_cxr:
            _req_s1('cxr', ctx.cxr == 'pending', 'რენტგენი')
        if pathway == PreopPatient.Pathway.CEA:
            _req_s1('carotid_r', ctx.car_r is None, 'მარჯვ. ICA %')
            _req_s1('carotid_l', ctx.car_l is None, 'მარცხ. ICA %')
            _req_s1('cea_cta_status', (s.get('cea_cta_status') or 'pending') == 'pending', 'კისრის CTA')
            if ctx.needs_brain_ct:
                _req_s1('brain_imaging', (s.get('brain_imaging') or 'pending') == 'pending', 'ტვინის CT/MRI')
        else:
            if ctx.needs_carotid_duplex:
                _req_s1('carotid_r', ctx.car_r is None, 'მარჯვ. საძილე')
                _req_s1('carotid_l', ctx.car_l is None, 'მარცხ. საძილე')
            if pathway == PreopPatient.Pathway.ABF:
                _req_s1('abi_r', (ctx.abi_r is None and ctx.abi_l is None and not ctx.tbi_measured), 'ABI')
                s1_required_fields.append('abi_l')
                _req_s1('abf_clamp_zone', (s.get('abf_clamp_zone') or 'pending') == 'pending', 'აორტის საკლამპე ზონა')
                _req_s1('abf_outflow', (s.get('abf_outflow') or 'pending') == 'pending', 'ბარძაყის Outflow')
            elif ctx.is_pad_bypass:
                _req_s1('abi_r' if patient.side != 'L' else 'abi_l', not ctx.target_limb_hemo_present, 'ABI / TP')
                _req_s1('pad_runoff_status', (s.get('pad_runoff_status') or 'pending') == 'pending', 'Runoff კალაპოტი')
                _req_s1('pad_gsv_status', (s.get('pad_gsv_status') or 'pending') == 'pending', 'GSV კარტირება')
            else:
                _req_s1('aaa_mm', ctx.aaa_mm is None, 'AAA დიამეტრი (მმ)')
    ctx.s1_required_fields = s1_required_fields
    ctx.missing_items = missing_items


_S2_FIELD_MAP = {
    'stale_labs': ('stale_labs_refreshed', 'ანალიზების განახლება'),
    'anemia': ('ferritin', 'ფერიტინი / TSAT'),
    'pbm_iron': ('pbm_iv_iron_done', 'IV რკინა (PBM)'),
    'hba1c': ('hba1c', 'HbA1c'),
    'endo': ('endo_consult_done', 'ენდოკრინოლოგი'),
    'nephro': ('nephro_panel_done', 'ნეფროლოგი / დიალიზი'),
    'hydration': ('hydration_planned', 'ჰიდრატაციის გეგმა'),
    'tsh': ('tsh_status', 'TSH / FT4'),
    'dental': ('dental_sanitation_done', 'სტომატოლოგი'),
    'premed': ('contrast_premed_done', 'იოდის პრემედიკაცია'),
    'cied': ('cied_checked', 'CIED შემოწმება'),
    'alt_vein': ('alt_vein_mapped', 'ალტ. ვენის კარტირება'),
    'cea_antiplatelet': ('cea_antiplatelet_started', 'CEA ანტიაგრეგანტი'),
    'neuro': ('neuro_consult_done', 'ნევროლოგი'),
    'laryng': ('laryngoscopy_res', 'ლარინგოსკოპია'),
    'abi': ('abi_r', 'საბაზისო ABI'),
    'vte': ('vte_prophylaxis_planned', 'VTE პროფილაქტიკა'),
    'spiro': ('spiro_fev1', 'სპირომეტრია'),
    'gastro': ('gastro_result', 'გასტროსკოპია'),
    'ppi': ('ppi_started', 'PPI გასტროპროტექცია'),
    'uti': ('urine_culture_done', 'შარდის ნათესი'),
    'wound': ('wound_care_done', 'ჭრილობის/კანის სანაცია'),
    'comp': ('compression_ready', 'კომპრესიული წინდა'),
    'dapt': ('dapt_ready', 'DAPT / ანტითრომბოზული'),
    'blood': ('blood_reserved', 'სისხლის რეზერვი'),
    'sizing': ('evar_sized', 'EVAR ზომების დათვლა'),
    'decol': ('decolonization_ready', 'ანტისეპტიკური შხაპი'),
    'med_hold': ('med_hold_agreed', 'წამლების სქემის შეთანხმება'),
    'cardio': ('cardio_decision', 'კარდიოლოგი'),
    'anesth': ('anesth_done', 'ანესთეზიოლოგი'),
}


def _eval_missing_s2_and_clearance_seal(ctx):
    patient, pathway, q = ctx.patient, ctx.pathway, ctx.q
    for t_obj in ctx.tasks:
        tk = t_obj['key']
        if tk in ('q', 's1') or t_obj['done']:
            continue
        if tk == 'tbi':
            fld_name, short_lbl = ('tp_r' if patient.side != 'L' else 'tp_l', 'Toe Pressure / TBI')
        else:
            fld_name, short_lbl = _S2_FIELD_MAP.get(tk, ('', t_obj['label']))
        ctx.missing_items.append({'tab': 's2', 'field': fld_name, 'label': short_lbl})

    if ctx.has_hard_contraindication:
        next_step_hint = 'უკუჩვენება — შეაჩერეთ!'
    elif not ctx.q_filled:
        next_step_hint = 'აკლია: კითხვარი'
    elif ctx.missing_items:
        top_lbls = ', '.join(m['label'] for m in ctx.missing_items[:2])
        more_cnt = len(ctx.missing_items) - 2
        next_step_hint = f'აკლია: {top_lbls}' + (f' (+{more_cnt})' if more_cnt > 0 else '')
    elif ctx.has_unacked_red:
        next_step_hint = 'საჭიროებს ქირურგის გაცნობას'
    elif not patient.approved_at:
        next_step_hint = 'მზადაა ქირურგის დასტურისთვის'
    else:
        next_step_hint = '✓ დადასტურებულია'
    ctx.next_step_hint = next_step_hint

    is_self_filled = is_self_filled_q(q)
    ctx.is_self_filled = is_self_filled
    ctx.self_fill_highlights = build_self_fill_highlights(q) if is_self_filled else []
    side_s = patient.side if isinstance(patient.side, str) else ''
    ctx.side_badge = {
        'R': 'მარჯვენა (R)', 'L': 'მარცხენა (L)', 'B': 'ორმხრივი (B)',
    }.get(side_s, 'აორტა / ცენტრალური' if pathway in (PreopPatient.Pathway.AAA_INFRA, PreopPatient.Pathway.ABF) else 'მხარე დასაზუსტებელია')

    stale_pending = bool(ctx.needs_stale_labs and not ctx.s.get('stale_labs_refreshed'))
    if ctx.has_hard_contraindication or ctx.dos_hold_danger or ctx.neuraxial_hard_blocked or ctx.solo_neuraxial_anatomic_mismatch:
        ctx.clearance_seal = {
            'code': 'stop', 'badge': 'STOP — დაშვება დაბლოკილია',
            'detail': (
                'არჩეული ნეირაქსიული / ეპიდურული გაუტკივარება უკუნაჩვენებია (ASRA 5th Ed) — შეცვალეთ გაუტკივარების მეთოდი!'
                if (ctx.neuraxial_hard_blocked and not ctx.has_hard_contraindication and not ctx.dos_hold_danger)
                else 'ფიქსირდება აბსოლუტური კლინიკური უკუჩვენება ან საოპერაციო დილის საშიში მაჩვენებელი!'
            ),
        }
        _apply_stop_lock_to_med_schedule(ctx.med_schedule)
    elif is_self_filled and not q.get('nurse_verified'):
        ctx.clearance_seal = {
            'code': 'pending', 'badge': 'მომზადების პროცესში — სავალდებულოა ექთნის ვერიფიკაცია!',
            'detail': 'კითხვარი შევსებულია პაციენტის ბმულით; დაშვებამდე სავალდებულოა ექთნის მიერ გადამოწმება.',
        }
    elif ctx.has_blocking_unknown_state:
        ctx.clearance_seal = {
            'code': 'pending', 'badge': 'შეფასება არასრულია — კრიტიკული მონაცემი დაუზუსტებელია!',
            'detail': 'აკლია კლინიკურად გადამწყვეტი მონაცემი (მაგ. CrCl ანტიკოაგულანტზე, ანალიზების თარიღი ან დაუზუსტებელი ალერგია) — შევსებამდე GO ბეჭედი არ გაიცემა.',
        }
    elif ctx.status == STATUS_READY and ctx.all_tasks_done:
        ctx.clearance_seal = {
            'code': 'go', 'badge': 'GO — უსაფრთხოდ მზადაა ოპერაციისთვის',
            'detail': 'ყველა პროტოკოლური კვლევა, რეფლექს-ორდერი და კონსულტაცია შესრულებულია.',
        }
    elif patient.approved_at and not ctx.has_unacked_red and not stale_pending:
        ctx.clearance_seal = {
            'code': 'conditional', 'badge': 'GO (პირობითი) — დაშვებულია ქირურგის მიერ',
            'detail': f'ქირურგმა დაადასტურა; საოპერაციომდე გადაამოწმეთ დარჩენილი პუნქტები ({ctx.done_count}/{len(ctx.tasks)}).',
        }
    else:
        ctx.clearance_seal = {
            'code': 'pending', 'badge': 'მომზადების პროცესში',
            'detail': f'შესრულებულია {ctx.done_count}/{len(ctx.tasks)} პროტოკოლური დავალება — საჭიროებს დასრულებას დაშვებამდე.',
        }


def _build_surgical_anatomy_brief(ctx):
    patient, pathway, q, s, q_filled = ctx.patient, ctx.pathway, ctx.q, ctx.s, ctx.q_filled
    if pathway == PreopPatient.Pathway.CEA:
        cea_cta_lbl = {'concordant': 'ტიპური ბიფურკაცია, დუპლექსთან შესაბამისი', 'high_calcified': 'მაღალი ბიფურკაცია (C2) / მძიმე კალციფიკაცია / კინკინგი (!)', 'pending': 'CTA შესატანია'}.get(s.get('cea_cta_status') or 'pending', 'ზუსტდება')
        br_lbl = {'normal_small': 'სისხლჩაქცევის გარეშე / მცირე კერა', 'large_or_hemorrhage': 'ფართო ინფარქტი (>1/3 MCA) ან ჰემორაგია — უკუჩვენება!', 'pending': 'შესატანია' if ctx.needs_brain_ct else 'არ იყო ნაჩვენები'}.get(s.get('brain_imaging') or 'pending', 'ზუსტდება')
        contra_crit = rule_value('CEA_STENOSIS_THRESHOLDS')['contra_crit']
        shunt_note = (
            f'ინტრაოპერაციული შუნტის (Shunt) სავალდებულო მზადყოფნა (კონტრალატ. სტენოზი/ოკლუზია ≥{contra_crit}% ან გადატანილი ინსულტი)!'
            if ((ctx.contra_car is not None and ctx.contra_car >= contra_crit) or (q_filled and q.get('neuro')) or ctx.is_symp_cea)
            else 'სტანდარტული ცერებრული მონიტორინგი / სელექციური შუნტირება'
        )
        return (
            f"NASCET სტენოზი: იფსილატერალური {ctx.ipsi_car if ctx.ipsi_car is not None else '—'}% / კონტრალატერალური {ctx.contra_car if ctx.contra_car is not None else '—'}% · კისრის CTA: {cea_cta_lbl}",
            f'ტვინის CT/MRI: {br_lbl} · ცერებრული დაცვა: {shunt_note}',
        )
    if pathway == PreopPatient.Pathway.ABF:
        clamp_lbl = {'infrarenal': 'სუფთა ინფრარენალური საკლამპე ზონა', 'juxtarenal_calcified': 'იუქსტარენალური ოკლუზია / მძიმე კალციფიკაცია (სუპრარენალური კლამპის მზადყოფნა!)', 'pending': 'CTA საკლამპე ზონა შესატანია'}.get(s.get('abf_clamp_zone') or 'pending', 'ზუსტდება')
        out_lbl = {'patent': 'CFA და Profunda ორმხრივ გამავალია', 'profunda_stenosis': 'Profunda სტენოზი — დაგეგმეთ პროფუნდოპლასტიკა!', 'poor_outflow': 'მძიმე დისტალური კალაპოტი (GSV კარტირება / კომბინირებული შუნტი!)', 'pending': 'Outflow შესატანია'}.get(s.get('abf_outflow') or 'pending', 'ზუსტდება')
        mes_lbl = {'normal': 'SMA გამავალია', 'ima_dominant_iia_occ': 'დომინანტური IMA / ორმხრივი IIA ოკლუზია — IMA რეიმპლანტაციის საკითხი!', 'sma_stenosis': 'SMA სტენოზი/ოკლუზია — ნაწლავის იშემიის უმაღლესი რისკი! განიხილეთ წინასწარი/თანმხლები SMA სტენტირება ან შუნტირება (ESVS)!'}.get(s.get('abf_mesenteric') or 'normal', 'SMA გამავალია')
        tactical_note = f"ვისცერული/მენჯის კოლატერალები: {mes_lbl} · ABI R {ctx.abi_r if ctx.abi_r is not None else '—'} / L {ctx.abi_l if ctx.abi_l is not None else '—'}"
        if q.get('abf_wet_infection'):
            tactical_note += ' · სასწრაფო ტერფის დრენირება/სანაცია + ემპირიული ფართო სპექტრის IV ანტიბიოტიკი!'
        return f"საკლამპე ზონა: {clamp_lbl} · Outflow: {out_lbl}", tactical_note
    if ctx.is_pad_bypass:
        gsv_min = rule_value('PAD_GSV_MIN_MM')['gsv_min_mm']
        run_lbl = {'popliteal_patent': 'მუხლქვეშა არტერია + ≥2 წვივის არტერია გამავალია', 'tibial_single': 'ერთადერთი გამავალი წვივის/ტერფის არტერია (აუცილებელია აუტოვენა ≥3.0 მმ!)', 'desert_foot': 'Desert Foot — მძიმე დისტალური ოკლუზია (!)', 'pending': 'Runoff შესატანია'}.get(s.get('pad_runoff_status') or 'pending', 'ზუსტდება')
        alt_vein_lbl = {'pending': 'შესაფასებელია', 'contra_gsv_ok': 'კონტრალატერალური GSV ვარგისია', 'arm_ssv_ok': 'ხელის ვენა (Cephalic/Basilic) / SSV ვარგისია', 'no_autovein_prosthetic': 'აუტოვენა არ მოიძებნა — სინთეზური/კომპოზიტური პროთეზი'}.get(s.get('alt_vein_mapped') or 'pending', 'შესაფასებელია')
        gsv_is_small = (ctx.pad_gsv_mm is not None and ctx.pad_gsv_mm < gsv_min)
        gsv_stripped = 'vein_strip' in ctx.vasc_chips
        gsv_raw_st = 'inadequate' if (gsv_is_small or gsv_stripped) else (s.get('pad_gsv_status') or 'pending')
        gsv_inad_why = 'ამოღებულია/აბლაცია; ' if gsv_stripped else (f'{ctx.pad_gsv_mm} მმ < {gsv_min} მმ; ' if gsv_is_small else '')
        gsv_lbl = {
            'adequate': f"იფსილატერალური GSV ვარგისია ({ctx.pad_gsv_mm if ctx.pad_gsv_mm is not None else '≥3.0'} მმ)",
            'inadequate': f"იფსილატერალური GSV უვარგისია ({gsv_inad_why}ალტ. ვენა: {alt_vein_lbl})",
            'pending': 'GSV კარტირება შესატანია',
        }.get(gsv_raw_st, 'ზუსტდება')
        return f"Runoff კალაპოტი: {run_lbl}", f"კონდუიტი: {gsv_lbl} · სამიზნე ჰემოდინამიკა: ABI min {ctx.min_abi if ctx.min_abi is not None else '—'}" + (f", TP min {ctx.min_tp} mmHg" if ctx.min_tp is not None else '')
    if ctx.is_pta:
        seg_lbl = {'iliac': 'თეძოს არტერიები (Iliac)', 'femoropopliteal': 'ბარძაყ-მუხლქვეშა სეგმენტი (SFA/Popliteal)', 'infrapopliteal': 'წვივის/ტერფის არტერიები (BTK)', 'multilevel': 'მრავალდონიანი დაზიანება'}.get(s.get('pta_lesion_site') or '', 'დასაზუსტებელია')
        cfa_scar_lbl = (
            'CFA კალციფიცირებული/ნაწიბუროვანია — განიხილეთ კონტრალატერალური მიდგომა ექო-კონტროლით (დიალიზზე რადიალური/ბრაქიალური მიდგომა აკრძალულია AV ფისტულის დაცვის გამო!)'
            if ctx.on_dialysis else
            'CFA კალციფიცირებული/ნაწიბუროვანია — განიხილეთ კონტრალატერალური ან რადიალური/ბრაქიალური მიდგომა!'
        )
        acc_lbl = {'cfa_clean': 'საერთო ბარძაყის არტერია (CFA) თავისუფალია პუნქციისთვის', 'cfa_calcified_scar': cfa_scar_lbl, 'pending': 'პუნქციის ზონა შესაფასებელია'}.get(s.get('pta_access_site') or 'pending', 'ზუსტდება')
        return f"სამიზნე სეგმენტი: {seg_lbl} · ABI min {ctx.min_abi if ctx.min_abi is not None else '—'}" + (f" · TP min {ctx.min_tp} mmHg" if ctx.min_tp is not None else ''), f"არტერიული მიდგომა: {acc_lbl}"
    if ctx.is_venous:
        dup_lbl = {'deep_patent_reflux': 'ღრმა ვენები გამავალია, ზედაპირული რეფლუქსი დადასტურებულია', 'acute_dvt': 'მწვავე DVT — ოპერაცია უკუნაჩვენებია!', 'deep_obstruction': 'ღრმა ვენების ოკლუზია — აბლაცია/ფლებექტომია უკუნაჩვენებია!', 'superficial_thrombus': 'მწვავე აღმავალი თრომბოფლებიტი (SFJ/SPJ საყურადღებოა!)', 'pending': 'დუპლექსი შესატანია'}.get(s.get('ven_duplex_status') or 'pending', 'ზუსტდება')
        vein_pool = {'gsv': 'GSV', 'ssv': 'SSV', 'gsv_ssv': 'GSV + SSV', 'aagsv_trib': 'AAGSV / შენაკადები / პერფორანტები'}.get(s.get('ven_target_vein') or 'gsv', 'GSV')
        anat_bits = []
        if s.get('ven_epifascial'):
            anat_bits.append('ეპიფასციური ღერო (<5 მმ — ჭარბი ტუმესცენცია კანის დამწვრობის პრევენციისთვის!)')
        if s.get('ven_tortuous'):
            anat_bits.append('დაკლაკნილი ღერო / სინექიები (ორმაგი პუნქციის მზადყოფნა)')
        if s.get('ven_perforator'):
            anat_bits.append('ინკომპეტენტური პერფორანტი ≥3.5 მმ')
        ven_gsv_mm = _num(s.get('ven_gsv_mm'), 1.0, 50.0)
        return (
            f"CEAP {q.get('ven_ceap') or 'C2'} · აუზი: {vein_pool}" + (f" ({ven_gsv_mm} მმ)" if ven_gsv_mm is not None else '') + f" · დუპლექსი: {dup_lbl}",
            '; '.join(anat_bits) if anat_bits else 'სტანდარტული ენდოვენური / ქირურგიული ანატომია',
        )

    morph_map, neck_map, pop_map = dict(AAA_MORPHOLOGY_CHOICES), dict(AAA_NECK_CHOICES), dict(POPLITEAL_SCREEN_CHOICES)
    iliac_lbl = {'ok_7mm': 'თეძო-ბარძაყის მიდგომა ≥7 მმ ✓', 'narrow_calcified': 'ვიწრო/კალციფ. მიდგომა (<7 მმ — Iliac Conduit!)', 'iliac_aneurysm': 'თეძოს ანევრიზმა (IIA შენარჩუნება!)', 'pending': 'მიდგომა შესაფასებელია'}.get(s.get('aaa_iliac_access') or 'pending', '—')
    tbd_sfx = ' · ტაქტიკა: ღია რეზექცია vs EVAR ზუსტდება CTA-ით' if patient.op_subtype == 'tbd' else ''
    anat_summary = f"ინფრარენალური AAA: {ctx.aaa_mm if ctx.aaa_mm is not None else '—'} მმ · {morph_map.get(s.get('aaa_morphology') or 'fusiform', 'ფუზიფორმული')}{tbd_sfx}"
    if patient.op_subtype in ('evar', 'tbd'):
        neck_part = f"პროქს. ყელი: {neck_map.get(s.get('aaa_neck_status') or 'pending', '—')} · მიდგომა: {iliac_lbl} · "
    elif (s.get('aaa_neck_status') or 'pending') == 'hostile_neck':
        neck_part = 'პროქს. ყელი: Hostile / იუქსტარენალური (სუპრარენალური კლამპი + თირკმლის ცივი პერფუზია >25 წთ!) · '
    else:
        neck_part = ''
    rv_part = ' · რეტროაორტული/ცირკუმაორტული მარცხენა თირკმლის ვენა (!)' if s.get('aaa_renal_vein_anom') else ''
    myc_part = ' · მიკოზური AAA: სისხლის ნათესები ×2 + ემპირიული IV ანტიბიოტიკი!' if s.get('aaa_morphology') == 'inflammatory_mycotic' else ''
    rup_oac_bits = []
    if ctx.is_rupture_suspect and (ctx.on_oral_anticoag or 'rivaroxaban_2_5' in ctx.med_chips):
        if q.get('med_warfarin'):
            rup_oac_bits.append('4F-PCC 25–50 სე/კგ IV + ვიტამინი K 10 მგ IV')
        if q.get('med_dabigatran'):
            rup_oac_bits.append('იდარუციზუმაბი (Praxbind) 5 გ IV')
        if q.get('med_doac'):
            rup_oac_bits.append('Xa-DOAC: 4F-PCC 50 სე/კგ IV (მოერიდეთ ანდექსანეტ ალფას კლამპირებამდე — ESVS 2024)')
        elif 'rivaroxaban_2_5' in ctx.med_chips:
            rup_oac_bits.append('რივაროქსაბანი 2.5 მგ (Xa): საჭიროებისას 4F-PCC კლინიკური სისხლდენის მიხედვით')
    rup_oac_part = f' · სასწრაფო OAC რევერსია: {" + ".join(rup_oac_bits)}!' if rup_oac_bits else ''
    return anat_summary, f"{neck_part}მუხლქვეშა სკრინინგი: {pop_map.get(s.get('popliteal_screen_status') or 'pending', '—')}{rv_part}{myc_part}{rup_oac_part}"


def _build_surgical_abx_regimen(ctx):
    patient, pathway, q, s = ctx.patient, ctx.pathway, ctx.q, ctx.s
    abx_cfg = rule_value('ALLERGY_HIT_ABX_PROPHYLAXIS')
    eff_ren_abx = None if getattr(ctx, 'creat_unit_mismatch', False) else getattr(ctx, 'eff_renal_sat', ctx.eff_renal)
    wt_num = getattr(ctx, 'weight_sat', None)
    if wt_num is None:
        wt_num = _num(s.get('weight_kg'), lo=25, hi=260)
    is_std_evla_abx = ctx.is_venous and patient.op_subtype == PreopPatient.OpSubtype.EVLA and not (
        q.get('ven_ceap') == 'C6' or q.get('ven_acute_svt') or q.get('ven_skin_infection')
    )
    if is_std_evla_abx:
        abx_regimen = 'რუტინული ანტიბიოტიკოპროფილაქტიკა სტანდარტულ EVLA-ზე არ ესაჭიროება (ESVS 2022)'
    elif 'beta_lactam' in ctx.allergy_chips:
        needs_gram_neg_groin = pathway in (PreopPatient.Pathway.AAA_INFRA, PreopPatient.Pathway.ABF, PreopPatient.Pathway.PAD_BYPASS, PreopPatient.Pathway.PAD_PTA)
        core_alt = (
            'β-ლაქტამსა და ვანკომიცინზე ალერგია (ცეფაზოლინი და ვანკომიცინი აკრძალულია!) → კლინდამიცინი 900 მგ IV'
            if getattr(ctx, 'has_vancomycin_allergy', False) else
            'β-ლაქტამური ალერგია (ცეფაზოლინი აკრძალულია!) → ვანკომიცინი 15–20 მგ/კგ IV (მაქს. 2 გ, 90–120 წთ ნელი ინფუზიით) ან კლინდამიცინი 900 მგ IV'
        )
        if not needs_gram_neg_groin:
            abx_regimen = core_alt
        else:
            avoid_gent = ctx.on_dialysis or (eff_ren_abx is not None and eff_ren_abx < abx_cfg['gentamicin_avoid_renal']) or ctx.open_aortic
            gram_neg_addon = (
                '+ აზტრეონამი 2 გ ან ციპროფლოქსაცინი 400 მგ IV საზარდულის/მუცლის მიდგომისას — დიალიზზე, CrCl/eGFR <45-სა და ღია აორტაზე გენტამიცინს მოერიდეთ (გენტამიცინი აკრძალულია)!'
                if avoid_gent else '+ გენტამიცინი 5 მგ/კგ IV საზარდულის მიდგომისას (ან აზტრეონამი 2 გ / ციპროფლოქსაცინი 400 მგ IV)'
            )
            abx_regimen = f'{core_alt} ({gram_neg_addon})'
    else:
        cefaz_3g_kg = abx_cfg['cefazolin_3g_kg']
        redose_str = 'ყოველ 8–12 სთ-ში (CrCl/eGFR <30 / დიალიზი)' if (ctx.on_dialysis or (eff_ren_abx is not None and eff_ren_abx < abx_cfg['cefazolin_redose_renal'])) else 'ყოველ 4 სთ-ში'
        cef_dose_str = f'3 გ IV (წონა ≥ {cefaz_3g_kg} კგ)' if (wt_num is not None and wt_num >= cefaz_3g_kg) else '2 გ IV'
        abx_regimen = f'ცეფაზოლინი {cef_dose_str} განაკვეთამდე 30–60 წთ-ით ადრე (რე-დოზირება {redose_str} ან >1500 მლ სისხლის დანაკარგზე)'
        if 'penicillin_mild' in ctx.allergy_chips:
            abx_regimen += ' [მსუბუქი პენიცილინის გამონაყარი — ცეფაზოლინი დასაშვებია AAAAI 2022]'
        if ctx.is_uncomplicated_pta_abx:
            abx_regimen = f'ESVS 2024: გართულების გარეშე პერკუტანულ PTA-ზე რუტინული პროფილაქტიკა აუცილებელი არ არის; სტენტ-გრაფტის / მაღალი რისკის საზარდულისას — {abx_regimen}'
    if ctx.unclarified_allergy_other and not is_std_evla_abx:
        abx_regimen += ' [დააზუსტეთ უცნობი მედიკამენტური ალერგია შეყვანამდე!]'
    return abx_regimen


def _build_surgical_brief(ctx):
    patient, pathway, s = ctx.patient, ctx.pathway, ctx.s
    ren_red = rule_value('REN_CKD_NEPHRO_HYDRATION')['red']
    na_hi = rule_value('REN_ELECTROLYTES_K_NA')['na_hi']
    anat_summary, tactical_note = _build_surgical_anatomy_brief(ctx)
    abx_regimen = _build_surgical_abx_regimen(ctx)

    eff_ren_b = None if getattr(ctx, 'creat_unit_mismatch', False) else getattr(ctx, 'eff_renal_sat', ctx.eff_renal)
    hit_severe_renal = ctx.on_dialysis or (eff_ren_b is not None and eff_ren_b < ren_red)
    has_hit = 'hit_heparin' in ctx.allergy_chips
    cea_bleed_b = pathway == PreopPatient.Pathway.CEA and (s.get('brain_imaging') == 'large_or_hemorrhage' or getattr(ctx, 'active_thrombolysis_window', False))
    if cea_bleed_b:
        hit_cea_sfx = ' (ასევე HIT ანამნეზში — UFH/LMWH კატეგორიულად აკრძალულია!)' if has_hit else ''
        heparin_plan = f'ყურადღება! ტვინის ჰემორაგია / ფართო ინფარქტი ან თრომბოლიზისი ≤6 დღეში — ნაადრევი სისტემური ანტიკოაგულაცია/ჰეპარინიზაცია საშიშია ჰემორაგიული ტრანსფორმაციის გამო (ESVS 2023)!{hit_cea_sfx}'
    elif has_hit:
        if ctx.is_venous and patient.op_subtype == PreopPatient.OpSubtype.EVLA and not ctx.needs_vte_prophylaxis:
            heparin_plan = 'HIT ანამნეზში! არაფრაქციული ჰეპარინი (UFH) და LMWH კატეგორიულად აკრძალულია · სტანდარტულ EVLA-ზე ინტრაოპ. ანტიკოაგულაცია არ ესაჭიროება (საჭიროებისას — ბივალირუდინი ან არგატრობანი; ადრეული აქტივაცია)'
        elif ctx.has_mech_valve:
            liver_arg = ' (ღვიძლის ≥3x-ზე უპირატესია ბივალირუდინი; არგატრობანი −75%!)' if ctx.liver_3x else ''
            heparin_plan = f'HIT ანამნეზში + მექანიკური სარქველი! UFH, LMWH და DOAC აკრძალულია → IV ბივალირუდინი ან არგატრობანი{liver_arg} პოსტოპ. ვარფარინზე (VKA) გადასვლით!'
        elif hit_severe_renal:
            liver_arg = ' (ღვიძლის ≥3x-ზე არგატრობანის დოზა მცირდება 75%-ით!)' if ctx.liver_3x else ''
            heparin_plan = f'HIT ანამნეზში! UFH, LMWH, ფონდაპარინუქსი და DOAC (CrCl/eGFR <30 / დიალიზი) კატეგორიულად აკრძალულია → ინტრაოპ./პოსტოპ. ბივალირუდინი ან არგატრობანი{liver_arg}!'
        elif ctx.liver_3x:
            heparin_plan = 'HIT ანამნეზში + ღვიძლის ≥3x დაზიანება! UFH და LMWH კატეგორიულად აკრძალულია (DOAC და ფონდაპარინუქსი არარეკომენდებულია) → უპირატესია ბივალირუდინი (არგატრობანის დოზა მცირდება 75%-ით!) და პოსტოპ. IPC / IV ბივალირუდინი!'
        else:
            heparin_plan = 'HIT ანამნეზში! არაფრაქციული ჰეპარინი (UFH) და LMWH კატეგორიულად აკრძალულია → ბივალირუდინი ან არგატრობანი (პოსტოპ. ფონდაპარინუქსი/DOAC CrCl ≥30-ზე)!'
    elif ctx.is_venous:
        heparin_plan = f'ინტრაოპერაციული UFH არ ესაჭიროება · VTE პროფილაქტიკა: Caprini {ctx.caprini_score} ქ. ({ctx.caprini_tier}) — {ctx.caprini_advice}'
    else:
        plt_s = f'{ctx.plt} ×10^9/L' if ctx.plt is not None else 'შესატანია!'
        heparin_plan = f'არაფრაქციული ჰეპარინი (UFH) 70–100 სე/კგ IV კლამპირებამდე/ჩარევამდე (სამიზნე ACT > 200–250 წმ · საბაზისო PLT: {plt_s})'

    contrast_plan = ''
    na_eff = getattr(ctx, 'na_val_sat', ctx.na_val)
    hyd_fluid_note = (
        'ჰიპერნატრიემია Na+ ≥150 — 0.9% NaCl-ით დატვირთვა აკრძალულია!' + (' · HFrEF სითხით გადატვირთვის რისკი!' if ctx.chf_hyd_flag else '')
        if (na_eff is not None and na_eff >= na_hi) else
        ('ფრთხილი 0.9% NaCl 0.5 მლ/კგ/სთ (HFrEF!)' if ctx.chf_hyd_flag else 'პერიპროცედურული 0.9% NaCl 1 მლ/კგ/სთ ჰიდრატაცია')
    )
    if ctx.max_contrast_ml is not None:
        contrast_plan = f'მაქს. იოდის კონტრასტის ბიუჯეტი (Gurm): ≤ {ctx.max_contrast_ml} მლ'
        if ctx.co2_angio_advised:
            contrast_plan += f' · CrCl/eGFR < 30: უპირატესია CO2 ანგიოგრაფია / IVUS + {hyd_fluid_note}!'
        elif ctx.needs_hydration:
            contrast_plan += f' + {hyd_fluid_note}'
        if ctx.eff_allergy_iodine:
            contrast_plan += ' · იოდზე ალერგია: სავალდებულო პრემედიკაცია (სტეროიდი + H1-ბლოკატორი)!'
    elif ctx.eff_allergy_iodine and not ctx.is_venous:
        contrast_plan = 'იოდზე ალერგია — სავალდებულო პრემედიკაცია (სტეროიდი + H1-ბლოკატორი) კონტრასტის გამოყენებისას!'

    statin_tail = '' if (ctx.liver_3x or getattr(ctx, 'eff_pregnant', False)) else ' + მაღალი ინტენსივობის სტატინი (LDL <1.4 მმოლ/ლ — ESVS Class I)'
    if ctx.is_pta:
        pause_oac_win = '~24 სთ-ში'
    elif has_hit:
        pause_oac_win = '48–72 სთ-ში (HIT: ჰეპარინი/კლექსანი აკრძალულია — IPC / არაჰეპარინული სქემა!)'
    elif hit_severe_renal:
        pause_oac_win = '48–72 სთ-ში (CrCl<30: D+1 პროფილაქტ. კანქვეშა UFH / IPC)'
    else:
        pause_oac_win = '48–72 სთ-ში (D+1 პროფილაქტ. LMWH)'
    asa_allergy = 'nsaid' in ctx.allergy_chips
    stent_p2y = getattr(ctx, 'stent_p2y12_lbl', ctx.alt_p2y12_lbl)
    is_cea_ich_b = (pathway == PreopPatient.Pathway.CEA and s.get('brain_imaging') == 'large_or_hemorrhage')
    is_cea_rtpa_b = (pathway == PreopPatient.Pathway.CEA and bool(getattr(ctx, 'active_thrombolysis_window', False)))
    if is_cea_ich_b or (is_cea_rtpa_b and not (s.get('brain_imaging') == 'normal_small' and ctx.days_since_ev is not None and ctx.days_since_ev >= 1)):
        default_postop_at = 'ტვინის სისხლჩაქცევის / თრომბოლიზისის ფონზე ანტიკოაგულაციისა და ანტიაგრეგანტების აღდგენა დაბლოკილია — სავალდებულოა ნეიროქირურგისა და ნევროლოგის კონსილიუმი!'
    elif is_cea_rtpa_b:
        default_postop_at = (
            'თრომბოლიზისი ≤6 დღე + ასპირინზე ალერგია: სისტემური ანტიკოაგულაცია/LMWH/P2Y12 და ასპირინი დაბლოკილია — შეათანხმეთ ინდივიდუალური რეჟიმი ნევროლოგთან!'
            if asa_allergy else
            'თრომბოლიზისი ≤6 დღე: სისტემური ანტიკოაგულაციის/LMWH/DAPT აღდგენა დაბლოკილია — CT-ზე ჰემორაგიის გამორიცხვისას დასაშვებია მხოლოდ ასპირინი 75–100 მგ ნევროლოგთან შეთანხმებით!'
        )
    elif ctx.is_venous:
        default_postop_at = ctx.caprini_advice
    elif ctx.post_antithrombotic_label:
        default_postop_at = ctx.post_antithrombotic_label + (statin_tail if 'სტატინი' not in ctx.post_antithrombotic_label else '')
    elif ctx.has_mech_valve:
        mv_ap_b = f' + ჩვენებისას {ctx.oac_partner_lbl}' if asa_allergy else ' + ჩვენებისას ასპირინი 75–100 მგ'
        default_postop_at = f'უვადო ვარფარინი (VKA) კარდიოლოგის სამიზნე INR-ით{mv_ap_b} (DOAC აკრძალულია!){statin_tail}'
    elif getattr(ctx, 'has_contra_oac', False):
        default_postop_at = f'უკუნაჩვენები OAC არ განაახლოთ (თირკმლის/ღვიძლის/დუბლირების უკუჩვენება) — შეარჩიეთ უსაფრთხო ანტიკოაგულაციის სქემა კარდიოლოგ/ნეფროლოგთან!{statin_tail}'
    elif ctx.has_full_oac:
        default_postop_at = f'მუდმივი ანტიკოაგულანტის (OAC) აღდგენა {pause_oac_win} ჰემოსტაზით (PAUSE) + ჩვენებისას {ctx.oac_partner_lbl} (სამმაგი თერაპიის გარეშე){statin_tail}'
    elif ctx.has_active_stent_dapt:
        st_b = f'{stent_p2y} (ასპირინზე ალერგია — კონსილიუმი!)' if asa_allergy else f'ასპირინი 100 მგ + {stent_p2y}'
        default_postop_at = f'უწყვეტი DAPT: {st_b} (კორონარული სტენტის დაცვა!){statin_tail}'
    elif asa_allergy:
        default_postop_at = f'{ctx.alt_p2y12_lbl} უწყვეტად (ასპირინზე/აასს-ზე ალერგია!){statin_tail}'
    elif 'clopidogrel_intol' in ctx.allergy_chips:
        default_postop_at = f'ასპირინი 100 მგ უწყვეტად (კლოპიდოგრელის აუტანლობა!){statin_tail}'
    else:
        default_postop_at = f'ასპირინი (ან კლოპიდოგრელი) უწყვეტად{statin_tail}'

    ctx.surgical_brief = {
        'side_badge': ctx.side_badge, 'anatomy_summary': anat_summary, 'tactical_note': tactical_note,
        'abx_regimen': abx_regimen, 'heparin_plan': heparin_plan, 'contrast_plan': contrast_plan,
        'postop_antithrombotic': default_postop_at, 'surveillance_plan': ctx.surveillance_plan,
    }


def _build_anesthetic_neuraxial_and_bp(ctx):
    patient, pathway, q, s = ctx.patient, ctx.pathway, ctx.q, ctx.s
    asra_cfg = rule_value('ASRA_NEURAXIAL_COAG_GATE')
    arm_diff_lim = rule_value('VIT_BP_HYPOTENSION_SHOCK')['arm_diff']
    neuraxial_blockers = list(ctx.hard_nx_reasons)
    if ctx.solo_neuraxial_anatomic_mismatch:
        neuraxial_blockers.insert(0, 'ანატომიურად შეუსაბამო სოლო-ნეირაქსიული გეგმა!')
    asra_ufh_note = (
        ' · ASRA 2025 UFH წესი: ნეირაქსიული პუნქციიდან IV ჰეპარინამდე ≥60 წთ (სისხლიანი პუნქციისას — გადადება/კონსილიუმი); '
        'კათეტერის ამოღება UFH-დან ≥4–6 სთ-ში (ნორმალური aPTT) და P2Y12/DOAC/VOYAGER დაწყებამდე!'
        if (ctx.has_neuraxial_plan and not ctx.is_venous) else ''
    )
    vka_inr_caution = (
        f' [ვარფარინის ფონზე ASRA 5th Ed რეკომენდაციით სასურველია INR-ის სრული ნორმალიზება ≤{asra_cfg["inr_vka_norm"]} (მიმდინარე: {ctx.eff_inr_nx})!]'
        if (q.get('med_warfarin') and ctx.eff_inr_nx is not None and asra_cfg['inr_vka_norm'] < ctx.eff_inr_nx <= asra_cfg['inr_max']) else ''
    )
    if ctx.has_neuraxial_plan:
        if ctx.plt is None:
            neuraxial_blockers.append('PLT უცნობია')
        if ctx.eff_inr_nx is None:
            neuraxial_blockers.append('INR უცნობია')
        if not ctx.uninterrupted_p2y12 and (
            q.get('med_doac') or q.get('med_dabigatran') or q.get('med_warfarin')
            or q.get('med_clopidogrel') or q.get('med_ticagrelor') or q.get('med_prasugrel')
            or ('rivaroxaban_2_5' in ctx.med_chips) or ('cilostazol' in ctx.med_chips)
        ):
            if not (s.get('med_hold_agreed') or s.get('dos_med_hold_verified')):
                neuraxial_blockers.append('ASRA წამლის შეჩერების ინტერვალი დასადასტურებელია')
        if ctx.neuraxial_hard_blocked:
            neuraxial_gate = 'უკუნაჩვენებია (ASRA 5th Ed): ' + ', '.join(neuraxial_blockers) + vka_inr_caution + asra_ufh_note
        elif neuraxial_blockers:
            neuraxial_gate = 'საყურადღებო/შეზღუდულია (ASRA 5th Ed): ' + ', '.join(neuraxial_blockers) + vka_inr_caution + asra_ufh_note
        else:
            neuraxial_gate = f'ASRA 5th Ed კრიტერიუმები დაცულია (INR {ctx.eff_inr_nx}, PLT {ctx.plt}) ✓{vka_inr_caution}{asra_ufh_note}'
    elif ctx.auto_spinal_candidate and ctx.hard_nx_reasons:
        neuraxial_gate = 'ნეირაქსიული შეზღუდულია (ავტო-გადართვა ზოგადზე — ASRA 5th Ed): ' + ', '.join(ctx.hard_nx_reasons)
    elif ctx.auto_spinal_candidate:
        neuraxial_gate = (
            f'ASRA 5th Ed: ნეირაქსიული დასაშვებია (INR {ctx.eff_inr_nx if ctx.eff_inr_nx is not None else "—"}, PLT {ctx.plt if ctx.plt is not None else "—"}) '
            '— არჩევისას მიუთითეთ „სპინალური / ეპიდურული“ (72 სთ DOAC / ≥60 წთ UFH წესისთვის)'
        )
    else:
        neuraxial_gate = 'ნეირაქსიული ბლოკადა/ეპიდურული კათეტერი არ იგეგმება'

    arm_note = ''
    if ctx.bp_arm_diff is not None and ctx.bp_arm_diff >= arm_diff_lim:
        hi_arm = 'მარჯვენა' if (ctx.bp_sys or 0) >= (ctx.bp_sys_l or 0) else 'მარცხენა'
        arm_note = f' [წნევის მანჟეტი / A-line სავალდებულოა {hi_arm} მკლავზე — ΔBP {ctx.bp_arm_diff} mmHg!]'

    if pathway == PreopPatient.Pathway.CEA:
        bp_corridor = 'კლამპირებისას: MAP 85–100 mmHg / SBP 110–160 mmHg (მოერიდეთ <110 და >170 mmHg!) · რეპერფუზიის შემდეგ: მკაცრად SBP < 140–150 mmHg (ჰიპერპერფუზიის სინდრომისა და ჰემატომის პრევენცია — ESVS 2023)!' + arm_note
    elif ctx.is_rupture_suspect:
        bp_corridor = 'პერმისიული ჰიპოტენზია SBP 70–90 mmHg (გონების შენარჩუნებით) პროქსიმალურ კლამპირებამდე / ბალონურ ოკლუზიამდე — მოერიდეთ აგრესიულ კრისტალოიდურ ინფუზიას (ESVS 2024)!' + arm_note
    elif getattr(ctx, 'is_symp_aaa_intact', False):
        bp_corridor = 'ჰემოდინამიკურად სტაბილური სიმპტომური AAA (ESVS 2024): მკაცრი ნორმოტენზია (SBP 100–130 mmHg — მოერიდეთ ჰიპერტენზიულ პიკებს და სასწრაფოდ გამოირიცხოს რუპტურა CTA-ით)!' + arm_note
    elif pathway == PreopPatient.Pathway.AAA_INFRA and patient.op_subtype == 'tbd':
        bp_corridor = 'MAP ≥ 65–75 mmHg · ღია რეზექციაზე: კლამპირება/დეკლამპირების კონტროლი; EVAR-ზე: იმპლანტაციისას SBP 90–110 mmHg.' + arm_note
    elif ctx.open_aortic:
        bp_corridor = 'MAP ≥ 65–75 mmHg (კორონარული/საძილე სტენოზისას ≥ 75–80 mmHg); აორტის კლამპირების წინ afterload-ის კონტროლი, დეკლამპირების წინ ინფუზიური მზადყოფნა და ვაზოპრესორი (declamping hypotension პრევენცია)!' + arm_note
    elif pathway == PreopPatient.Pathway.AAA_INFRA and patient.op_subtype == 'evar':
        bp_corridor = 'MAP ≥ 65–75 mmHg; სტენტ-გრაფტის გაშლისას კონტროლირებადი SBP 90–110 mmHg ზუსტი იმპლანტაციისთვის.' + arm_note
    elif ctx.is_pad_bypass or ctx.is_pta:
        bp_corridor = 'MAP ≥ 70–80 mmHg (ინტრაოპერაციული ჰიპოტენზიისა და რეკონსტრუქციის ადრეული თრომბოზის პრევენცია).' + arm_note
    else:
        lido_part = f' · ტუმესცენტური ლიდოკაინი (ადრენალინით) ≤ {ctx.lidocaine_max_mg} მგ (LAST ანტიდოტი: 20% Intralipid მზადყოფნაში!)' if ctx.lidocaine_max_mg is not None else ''
        bp_corridor = 'სტაბილური ნორმოტენზია' + lido_part + arm_note
    return neuraxial_gate, bp_corridor


def _build_anesthetic_disposition(ctx, sb_cfg, cb_cfg, ren_hyd):
    from ..calculators import _s_raw, _sat_num
    patient, q, s, q_filled = ctx.patient, ctx.q, ctx.s, ctx.q_filled
    base_disp = (
        ('პოსტოპ. რეანიმაცია / HDU (დაჯავშნილია ✓)' if s.get('icu_bed_booked') else 'პოსტოპ. რეანიმაცია / HDU — საწოლი დასაჯავშნია!')
        if ctx.needs_icu_bed else
        ('დღის სტაციონარი / ამბულატორიული გაწერა ადრეული აქტივაციით' if (ctx.is_venous and patient.op_subtype == PreopPatient.OpSubtype.EVLA) else 'სისხლძარღვთა ქირურგიის სტაციონარული პალატა')
    )
    disp_extras = []
    age_eff = ctx.age if ctx.age is not None else getattr(ctx, 'age_sat', None)
    bnp_mins = ctx.nt_probnp if ctx.nt_probnp is not None else _sat_num(_s_raw(s, 'nt_probnp'), 1, 100000)
    ctn_mins = ctx.hs_ctn if ctx.hs_ctn is not None else _sat_num(_s_raw(s, 'hs_ctn'), 0.1, 50000)
    egfr_mins = None if getattr(ctx, 'creat_unit_mismatch', False) else getattr(ctx, 'egfr_sat', ctx.egfr)
    mins_cv_risk = bool(
        (age_eff or 0) >= sb_cfg['mins_age']
        or ctx.ihd_flag or ctx.chf_flag or ctx.on_dialysis
        or (egfr_mins is not None and egfr_mins < ren_hyd)
        or (q_filled and (ctx.has_known_dm or q.get('neuro') or q.get('htn')))
    )
    if not ctx.is_venous and (
        ctx.open_aortic
        or ((patient.op_subtype == 'evar' or ctx.is_pad_bypass) and mins_cv_risk)
        or ctx.rcri['score'] >= sb_cfg['mins_rcri']
        or (bnp_mins is not None and bnp_mins >= cb_cfg['bnp_amb'])
        or (ctn_mins is not None and ctn_mins >= cb_cfg['ctn_amb'])
    ):
        disp_extras.append('MINS მონიტორინგი: hs-cTn 24 სთ და 48 სთ (ESC 2022)')
    if ctx.needs_delirium_guard and not ctx.is_venous:
        disp_extras.append('დელირიუმის პრევენცია (ბენზოდიაზეპინების გარეშე — ESAIC 2024)')
    return base_disp + ((' · ' + ' · '.join(disp_extras)) if disp_extras else '')


def _build_anesthetic_brief(ctx):
    pathway, q, s, q_filled = ctx.pathway, ctx.q, ctx.s, ctx.q_filled
    hb_cfg, coag_cfg = rule_value('LAB_HB_PBM_THRESHOLDS'), rule_value('LAB_WBC_PLT_COAG')
    gly_cfg, cb_cfg = rule_value('GLY_GLUCOSE_HBA1C'), rule_value('CARDIO_BIOMARKERS_ECHO')
    sb_cfg, el_cfg = rule_value('ANES_AIRWAY_STOPBANG_ICU'), rule_value('REN_ELECTROLYTES_K_NA')
    inr_std = rule_value('DOS_WHO_SIGNIN_THRESHOLDS')['inr_std']
    ren_hyd = rule_value('REN_CKD_NEPHRO_HYDRATION')['hydration']
    neuraxial_gate, bp_corridor = _build_anesthetic_neuraxial_and_bp(ctx)
    high_ischemic_rbc = ctx.ihd_flag or ctx.chf_flag or ctx.has_mech_valve or (pathway == PreopPatient.Pathway.CEA) or (ctx.max_car is not None and ctx.max_car >= hb_cfg['ischemic_carotid_pct']) or ctx.is_clti_any
    rbc_thresh = 'Hb ≥ 80–90 გ/ლ (გიდ / გულის უკმარისობა / ცერებრული / კრიტიკული იშემიის ზღვარი)' if high_ischemic_rbc else 'Hb ≥ 70–80 გ/ლ (რესტრიქციული PBM ზღვარი)'
    bg_lbl = s.get('blood_group') or 'დაუდგენელია'
    if ctx.is_rupture_suspect:
        transfusion_pbm = f'ჯგუფი: {bg_lbl} · სასწრაფო MTP პროტოკოლი: O Rh(-) / ჯგუფ-სპეციფიკური ერითრომასა + ს.გ.პ. + თრომბოკონცენტრატი (1:1:1) + Cell Saver + TXA 1 გ IV!'
    elif ctx.open_major:
        res_lbl = 'ერითრომასის რეზერვი მზადაა ✓' if s.get('blood_reserved') else 'ერითრომასის რეზერვი მოსამზადებელია!'
        cs_lbl = ' · Cell Saver + TXA მზადაა ✓' if s.get('cell_saver_ready') else (' · განიხილეთ Cell Saver + TXA' if ctx.open_aortic else '')
        transfusion_pbm = f'ჯგუფი: {bg_lbl} · სამიზნე ტრიგერი: {rbc_thresh} · {res_lbl}{cs_lbl}'
    else:
        transfusion_pbm = f'ჯგუფი: {bg_lbl} · ტრანსფუზიული ტრიგერი საჭიროებისას: {rbc_thresh}'
    coag_pbm_bits = []
    if ctx.plt is not None and ctx.plt < coag_cfg['plt_red']:
        if 'hit_heparin' in ctx.allergy_chips:
            coag_pbm_bits.append('PLT <100 + HIT: პროფილაქტიკური თრომბოკონცენტრატის გადასხმა უკუნაჩვენებია (მხოლოდ სიცოცხლისთვის საშიშ სისხლდენაზე)!')
        else:
            coag_pbm_bits.append(f'თრომბოკონცენტრატის მზადყოფნა (PLT {ctx.plt} <100 — სამიზნე ≥80–100)')
    if ctx.fib is not None and ctx.fib < coag_cfg['fib_low']:
        coag_pbm_bits.append(f'ფიბრინოგენის კონცენტრატი 3–4 გ IV / კრიოპრეციპიტატი (Fib {ctx.fib} <1.8 გ/ლ — ESAIC 2023)')
    inr_pbm = ctx.inr if ctx.inr is not None else getattr(ctx, 'inr_sat', None)
    if inr_pbm is not None and inr_pbm > inr_std and not ctx.on_oral_anticoag:
        coag_pbm_bits.append(f'კოაგულოპათია OAC-ის გარეშე (INR {ctx.inr if ctx.inr is not None else inr_pbm} >1.5): ვიტამინი K 10 მგ IV ± 4F-PCC (ESAIC 2023)')
    if coag_pbm_bits:
        transfusion_pbm += ' · ' + ' · '.join(coag_pbm_bits)
    airway_bits = [ctx.mallampati_label, f'STOP-Bang: {ctx.stop_bang_label}']
    if ctx.is_rupture_suspect:
        airway_bits.append('RSI ინდუქცია მხოლოდ საოპერაციო ველის დამუშავებისა და ქირურგიული მზადყოფნის შემდეგ!')
    k_eff = getattr(ctx, 'k_val_sat', ctx.k_val)
    if (k_eff is not None and k_eff >= el_cfg['k_succ_hi']) or (q_filled and (q.get('cea_disabling_stroke') or (q.get('neuro') and ctx.is_symp_cea))):
        airway_bits.append('სუქცინილქოლინი უკუნაჩვენებია (ლეტალური ჰიპერკალიემიის რისკი — როკურონიუმი + სუგამადექსი)!')
    if q.get('glp1') or ctx.ft_meds.get('has_glp1'):
        airway_bits.append('GLP-1 ასპირაციის დაცვა (კუჭის POCUS / RSI)')
    if q_filled and q.get('cea_neck_limited'):
        airway_bits.append('კისრის გაშლა შეზღუდულია!')
    if q_filled and q.get('loose_teeth'):
        airway_bits.append('მორყეული კბილები / პროთეზი')
    if (s.get('laryngoscopy_res') or 'pending') == 'palsy':
        airway_bits.append('სახმო იოგის პარეზი (!)')
    if ctx.open_aortic:
        default_lines_lbl = 'A-line + CVC + 2 მსხვილი პერიფერიული კათეტერი + ფოლი + აქტიური გათბობა ≥36.0°C'
    elif pathway == PreopPatient.Pathway.CEA or ctx.asa_roman == 'IV':
        default_lines_lbl = 'A-line (ინვაზიური წნევა) + ECG/SpO2 + აქტიური გათბობა'
    elif ctx.is_venous or ctx.is_pta:
        default_lines_lbl = 'სტანდარტული არაინვაზიური მონიტორინგი (ECG, SpO2, NIBP)'
    else:
        default_lines_lbl = 'A-line / ინვაზიური მონიტორინგი ქირურგიული მასშტაბით + აქტიური გათბობა'
    cied_brief_sfx = f' · CIED: {ctx.cied_plan_label}' if ctx.cied_plan_label else (' · CIED: რეჟიმი დასაზუსტებელია (!) — გარე პედების მზადყოფნა!' if ctx.needs_cied else '')
    fistula_sfx = ' · AV ფისტულის მკლავის დაცვა (წნევის მანჟეტი, პუნქცია და კათეტერი აკრძალულია!)' if ctx.on_dialysis else ''
    gl_eff = getattr(ctx, 'gl_mmol_sat', ctx.gl_mmol)
    dos_gl_eff = ctx.dos_gl if ctx.dos_gl is not None else getattr(ctx, 'dos_gl_sat', None)
    hba1c_eff = getattr(ctx, 'hba1c_sat', ctx.hba1c)
    needs_glycemic_corridor = bool(ctx.has_known_dm or (gl_eff is not None and gl_eff >= gly_cfg['gl_hba1c']) or (hba1c_eff is not None and hba1c_eff >= gly_cfg['hba1c_dm']) or (dos_gl_eff is not None and dos_gl_eff >= gly_cfg['dos_corridor_mmol']))
    needs_vriii = bool((q_filled and q.get('dm_insulin') and ctx.open_aortic) or (gl_eff is not None and gl_eff >= gly_cfg['vriii_mmol']) or (dos_gl_eff is not None and dos_gl_eff >= gly_cfg['vriii_mmol']))
    vriii_sfx = ' · ინტრაოპ./ICU: IV ინსულინის ინფუზია (VRIII) საათობრივი გლუკოზისა და K+ კონტროლით!' if needs_vriii else ''
    ctx.anesthetic_brief = {
        'modality': f'{ctx.effective_asa} · {ctx.effective_anesth_label}',
        'neuraxial_gate': neuraxial_gate,
        'airway_summary': ' · '.join(airway_bits),
        'bp_corridor': bp_corridor,
        'lines_and_cied': (ctx.anesth_lines_txt or default_lines_lbl) + cied_brief_sfx + fistula_sfx,
        'transfusion_pbm': transfusion_pbm,
        'glycemic_target': (
            f'პერიოპერაციული გლიკემიური კორიდორი 5.5–10.0 მმოლ/ლ (ჰიპოგლიკემიის <3.9 და >10.0 თავიდან აცილება — ESC/ESAIC){vriii_sfx}'
            if needs_glycemic_corridor else 'სტანდარტული ნორმოგლიკემია (<10.0 მმოლ/ლ)'
        ),
        'disposition': _build_anesthetic_disposition(ctx, sb_cfg, cb_cfg, ren_hyd),
    }


def eval_briefings(ctx):
    _eval_scores_and_missing_s1(ctx)
    _eval_missing_s2_and_clearance_seal(ctx)
    _build_surgical_brief(ctx)
    _build_anesthetic_brief(ctx)
