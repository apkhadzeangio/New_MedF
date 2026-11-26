"""Quantitative risk scores, nurse guided UX, clearance seal, and surgical & anesthetic briefings."""
from ...models import PreopPatient
from ..anamnesis import build_self_fill_highlights
from ..calculators import _ihd_and_chf_flags, calc_ariscat_pulm, calc_rcri, calc_vsg_cri
from ..constants import AAA_MORPHOLOGY_CHOICES, AAA_NECK_CHOICES, POPLITEAL_SCREEN_CHOICES, STATUS_READY
from ..meds import _apply_stop_lock_to_med_schedule
from ..utils import _num


def _eval_scores_and_frailty(ctx):
    patient, q, s, q_filled = ctx.patient, ctx.q, ctx.s, ctx.q_filled
    ctx.rcri = calc_rcri(patient, q, s, is_symp_cea=ctx.is_symp_cea)
    ctx.vsg_cri = calc_vsg_cri(patient, q, s)
    ctx.ariscat = calc_ariscat_pulm(patient, q, s, hb_gl=ctx.hb_gl)
    ctx.ihd_flag, ctx.chf_flag = _ihd_and_chf_flags(q, s)

    frailty_notes = []
    if q_filled and q.get('cfs_level') == '5_plus':
        frailty_notes.append('კლინიკური დაუძლურება (CFS ≥ 5)')
    elif q_filled and q.get('cfs_level') == '4':
        frailty_notes.append('მოწყვლადობა (CFS 4)')
    if ctx.alb_gl is not None and ctx.alb_gl < 35:
        frailty_notes.append(f'ჰიპოალბუმინემია ({ctx.alb_gl} გ/ლ)')
    if ctx.bmi is not None and ctx.bmi < 16.0:
        frailty_notes.append(f'მძიმე კახექსია (BMI {ctx.bmi}!)')
    elif ctx.bmi is not None and ctx.bmi < 18.5:
        frailty_notes.append(f'დეფიციტური წონა (BMI {ctx.bmi})')
    elif ctx.bmi is not None and ctx.bmi >= 35:
        frailty_notes.append(f'სიმსუქნე II–III ხ. (BMI {ctx.bmi})')
    if q_filled and q.get('cea_disabling_stroke'):
        frailty_notes.append('შეზღუდული მობილობა (mRS ≥ 3)')
    if (ctx.age or 0) >= 80:
        frailty_notes.append(f'ასაკი {ctx.age} წ.')
    ctx.needs_delirium_guard = bool((q_filled and q.get('cfs_level') in ('4', '5_plus')) or (ctx.age or 0) >= 75)
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
    elif (q.get('self_fill_submitted') or q.get('filled_by') == 'პაციენტი (ბმულით)') and not q.get('nurse_verified'):
        missing_items.append({'tab': 'q', 'field': 'btn-verify-self', 'label': 'პაციენტის კითხვარის გადამოწმება'})

    def _req_s1(fname, missing_cond, lbl):
        s1_required_fields.append(fname)
        if missing_cond:
            missing_items.append({'tab': 's1', 'field': fname, 'label': lbl})

    if ctx.is_venous:
        is_lean_evla_ux = (not ctx.is_phleb) and (not ctx.has_neuraxial_plan) and (ctx.anesth_plan_raw != 'general')
        ven_needs_creat_ux = ctx.is_phleb or ctx.has_neuraxial_plan or bool(
            q.get('med_doac') or q.get('med_dabigatran') or q.get('metformin') or ('rivaroxaban_2_5' in ctx.med_chips)
        )
        ven_needs_inr_ux = (not is_lean_evla_ux) or ctx.on_oral_anticoag or (s.get('liver_status') in ('2x', '3x'))
        ven_needs_gl_ux = (not is_lean_evla_ux) or bool(
            q.get('dm') or q.get('dm_insulin') or q.get('metformin') or q.get('sglt2') or q.get('glp1')
        ) or (ctx.hba1c is not None and ctx.hba1c >= 6.5) or (ctx.bmi is not None and ctx.bmi >= 30) or ((ctx.age or 0) >= 45)
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


def _eval_missing_s2_and_clearance_seal(ctx):
    patient, pathway, q = ctx.patient, ctx.pathway, ctx.q
    s2_field_map = {
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
        'tbi': ('tp_r' if patient.side != 'L' else 'tp_l', 'Toe Pressure / TBI'),
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
    for t_obj in ctx.tasks:
        tk = t_obj['key']
        if tk in ('q', 's1') or t_obj['done']:
            continue
        fld_name, short_lbl = s2_field_map.get(tk, ('', t_obj['label']))
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

    is_self_filled = bool(q.get('self_fill_submitted') or q.get('filled_by') == 'პაციენტი (ბმულით)')
    ctx.self_fill_highlights = build_self_fill_highlights(q) if is_self_filled else []
    ctx.side_badge = {
        'R': 'მარჯვენა (R)', 'L': 'მარცხენა (L)', 'B': 'ორმხრივი (B)',
    }.get(patient.side or '', 'აორტა / ცენტრალური' if pathway in (PreopPatient.Pathway.AAA_INFRA, PreopPatient.Pathway.ABF) else 'მხარე დასაზუსტებელია')

    if ctx.has_hard_contraindication or ctx.dos_hold_danger or ctx.solo_neuraxial_anatomic_mismatch:
        ctx.clearance_seal = {
            'code': 'stop', 'badge': 'STOP — დაშვება დაბლოკილია',
            'detail': 'ფიქსირდება აბსოლუტური კლინიკური უკუჩვენება ან საოპერაციო დილის საშიში მაჩვენებელი!',
        }
        _apply_stop_lock_to_med_schedule(ctx.med_schedule)
    elif ctx.neuraxial_hard_blocked:
        ctx.clearance_seal = {
            'code': 'pending', 'badge': 'მომზადების პროცესში — ანესთეზიის გეგმა შესაცვლელია!',
            'detail': 'არჩეული ნეირაქსიული / ეპიდურული გაუტკივარება უკუნაჩვენებია (ASRA 5th Ed) — შეცვალეთ გაუტკივარების მეთოდი.',
        }
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
    elif patient.approved_at and not ctx.has_unacked_red and not ctx.needs_stale_labs:
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
        shunt_note = (
            'ინტრაოპერაციული შუნტის (Shunt) სავალდებულო მზადყოფნა (კონტრალატ. სტენოზი/ოკლუზია ≥80% ან გადატანილი ინსულტი)!'
            if ((ctx.contra_car is not None and ctx.contra_car >= 80) or (q_filled and q.get('neuro')) or ctx.is_symp_cea)
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
        if q_filled and q.get('abf_wet_infection'):
            tactical_note += ' · სასწრაფო ტერფის დრენირება/სანაცია + ემპირიული ფართო სპექტრის IV ანტიბიოტიკი!'
        return f"საკლამპე ზონა: {clamp_lbl} · Outflow: {out_lbl}", tactical_note
    if ctx.is_pad_bypass:
        run_lbl = {'popliteal_patent': 'მუხლქვეშა არტერია + ≥2 წვივის არტერია გამავალია', 'tibial_single': 'ერთადერთი გამავალი წვივის/ტერფის არტერია (აუცილებელია აუტოვენა ≥3.0 მმ!)', 'desert_foot': 'Desert Foot — მძიმე დისტალური ოკლუზია (!)', 'pending': 'Runoff შესატანია'}.get(s.get('pad_runoff_status') or 'pending', 'ზუსტდება')
        alt_vein_lbl = {'pending': 'შესაფასებელია', 'contra_gsv_ok': 'კონტრალატერალური GSV ვარგისია', 'arm_ssv_ok': 'ხელის ვენა (Cephalic/Basilic) / SSV ვარგისია', 'no_autovein_prosthetic': 'აუტოვენა არ მოიძებნა — სინთეზური/კომპოზიტური პროთეზი'}.get(s.get('alt_vein_mapped') or 'pending', 'შესაფასებელია')
        gsv_is_small = (ctx.pad_gsv_mm is not None and ctx.pad_gsv_mm < 3.0)
        gsv_raw_st = 'inadequate' if gsv_is_small else (s.get('pad_gsv_status') or 'pending')
        gsv_lbl = {
            'adequate': f"იფსილატერალური GSV ვარგისია ({ctx.pad_gsv_mm if ctx.pad_gsv_mm is not None else '≥3.0'} მმ)",
            'inadequate': f"იფსილატერალური GSV უვარგისია ({f'{ctx.pad_gsv_mm} მმ < 3.0 მმ; ' if gsv_is_small else ''}ალტ. ვენა: {alt_vein_lbl})",
            'pending': 'GSV კარტირება შესატანია',
        }.get(gsv_raw_st, 'ზუსტდება')
        return f"Runoff კალაპოტი: {run_lbl}", f"კონდუიტი: {gsv_lbl} · სამიზნე ჰემოდინამიკა: ABI min {ctx.min_abi if ctx.min_abi is not None else '—'}" + (f", TP min {ctx.min_tp} mmHg" if ctx.min_tp is not None else '')
    if ctx.is_pta:
        seg_lbl = {'iliac': 'თეძოს არტერიები (Iliac)', 'femoropopliteal': 'ბარძაყ-მუხლქვეშა სეგმენტი (SFA/Popliteal)', 'infrapopliteal': 'წვივის/ტერფის არტერიები (BTK)', 'multilevel': 'მრავალდონიანი დაზიანება'}.get(s.get('pta_lesion_site') or '', 'დასაზუსტებელია')
        acc_lbl = {'cfa_clean': 'საერთო ბარძაყის არტერია (CFA) თავისუფალია პუნქციისთვის', 'cfa_calcified_scar': 'CFA კალციფიცირებული/ნაწიბუროვანია — განიხილეთ კონტრალატერალური ან რადიალური/ბრაქიალური მიდგომა!', 'pending': 'პუნქციის ზონა შესაფასებელია'}.get(s.get('pta_access_site') or 'pending', 'ზუსტდება')
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
        return (
            f"CEAP {q.get('ven_ceap') or 'C2'} · აუზი: {vein_pool}" + (f" ({s.get('ven_gsv_mm')} მმ)" if s.get('ven_gsv_mm') is not None else '') + f" · დუპლექსი: {dup_lbl}",
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
    if ctx.is_rupture_suspect and ctx.on_oral_anticoag:
        if q.get('med_warfarin'):
            rup_oac_bits.append('4F-PCC 25–50 სე/კგ IV + ვიტამინი K 10 მგ IV')
        if q.get('med_dabigatran'):
            rup_oac_bits.append('იდარუციზუმაბი (Praxbind) 5 გ IV')
        if q.get('med_doac'):
            rup_oac_bits.append('Xa-DOAC: 4F-PCC 50 სე/კგ IV (მოერიდეთ ანდექსანეტ ალფას კლამპირებამდე — ESVS 2024)')
    rup_oac_part = f' · სასწრაფო OAC რევერსია: {" + ".join(rup_oac_bits)}!' if rup_oac_bits else ''
    return anat_summary, f"{neck_part}მუხლქვეშა სკრინინგი: {pop_map.get(s.get('popliteal_screen_status') or 'pending', '—')}{rv_part}{myc_part}{rup_oac_part}"


def _build_surgical_abx_regimen(ctx):
    patient, pathway, q, s, q_filled = ctx.patient, ctx.pathway, ctx.q, ctx.s, ctx.q_filled
    wt_num = _num(s.get('weight_kg'), lo=25, hi=260)
    is_std_evla_abx = ctx.is_venous and patient.op_subtype == PreopPatient.OpSubtype.EVLA and not (
        q_filled and (q.get('ven_ceap') == 'C6' or q.get('ven_acute_svt') or q.get('ven_skin_infection'))
    )
    if is_std_evla_abx:
        abx_regimen = 'რუტინული ანტიბიოტიკოპროფილაქტიკა სტანდარტულ EVLA-ზე არ ესაჭიროება (ESVS 2022)'
    elif 'beta_lactam' in ctx.allergy_chips:
        needs_gram_neg_groin = pathway in (PreopPatient.Pathway.AAA_INFRA, PreopPatient.Pathway.ABF, PreopPatient.Pathway.PAD_BYPASS, PreopPatient.Pathway.PAD_PTA)
        if not needs_gram_neg_groin:
            abx_regimen = 'β-ლაქტამური ალერგია (ცეფაზოლინი აკრძალულია!) → ვანკომიცინი 15–20 მგ/კგ IV (მაქს. 2 გ, 90–120 წთ ნელი ინფუზიით) ან კლინდამიცინი 900 მგ IV'
        else:
            avoid_gent = ctx.on_dialysis or (ctx.eff_renal is not None and ctx.eff_renal < 45) or ctx.open_aortic
            gram_neg_addon = (
                '+ აზტრეონამი 2 გ ან ციპროფლოქსაცინი 400 მგ IV საზარდულის/მუცლის მიდგომისას — დიალიზზე, CrCl/eGFR <45-სა და ღია აორტაზე გენტამიცინს მოერიდეთ (გენტამიცინი აკრძალულია)!'
                if avoid_gent else '+ გენტამიცინი 5 მგ/კგ IV საზარდულის მიდგომისას (ან აზტრეონამი 2 გ / ციპროფლოქსაცინი 400 მგ IV)'
            )
            abx_regimen = f'β-ლაქტამური ალერგია (ცეფაზოლინი აკრძალულია!) → ვანკომიცინი 15–20 მგ/კგ IV (მაქს. 2 გ, 90–120 წთ ნელი ინფუზიით) ან კლინდამიცინი 900 მგ IV ({gram_neg_addon})'
    else:
        redose_str = 'ყოველ 8–12 სთ-ში (CrCl/eGFR <30 / დიალიზი)' if (ctx.on_dialysis or (ctx.eff_renal is not None and ctx.eff_renal < 30)) else 'ყოველ 4 სთ-ში'
        cef_dose_str = '3 გ IV (წონა ≥ 120 კგ)' if (wt_num is not None and wt_num >= 120) else '2 გ IV'
        abx_regimen = f'ცეფაზოლინი {cef_dose_str} განაკვეთამდე 30–60 წთ-ით ადრე (რე-დოზირება {redose_str} ან >1500 მლ სისხლის დანაკარგზე)'
        if 'penicillin_mild' in ctx.allergy_chips:
            abx_regimen += ' [მსუბუქი პენიცილინის გამონაყარი — ცეფაზოლინი დასაშვებია AAAAI 2022]'
        if ctx.is_uncomplicated_pta_abx:
            abx_regimen = f'ESVS 2024: გართულების გარეშე პერკუტანულ PTA-ზე რუტინული პროფილაქტიკა აუცილებელი არ არის; სტენტ-გრაფტის / მაღალი რისკის საზარდულისას — {abx_regimen}'
    if ctx.unclarified_allergy_other and not is_std_evla_abx:
        abx_regimen += ' [დააზუსტეთ უცნობი მედიკამენტური ალერგია შეყვანამდე!]'
    return abx_regimen


def _build_surgical_brief(ctx):
    patient, pathway, s, q_filled = ctx.patient, ctx.pathway, ctx.s, ctx.q_filled
    anat_summary, tactical_note = _build_surgical_anatomy_brief(ctx)
    abx_regimen = _build_surgical_abx_regimen(ctx)

    hit_severe_renal = ctx.on_dialysis or (ctx.eff_renal is not None and ctx.eff_renal < 30)
    has_hit = 'hit_heparin' in ctx.allergy_chips
    if has_hit:
        if ctx.is_venous and patient.op_subtype == PreopPatient.OpSubtype.EVLA and not ctx.needs_vte_prophylaxis:
            heparin_plan = 'HIT ანამნეზში! არაფრაქციული ჰეპარინი (UFH) და LMWH კატეგორიულად აკრძალულია · სტანდარტულ EVLA-ზე ინტრაოპ. ანტიკოაგულაცია არ ესაჭიროება (საჭიროებისას — ბივალირუდინი ან არგატრობანი; ადრეული აქტივაცია)'
        elif ctx.has_mech_valve:
            liver_arg = ' (ღვიძლის ≥3x-ზე უპირატესია ბივალირუდინი; არგატრობანი −75%!)' if ctx.liver_3x else ''
            heparin_plan = f'HIT ანამნეზში + მექანიკური სარქველი! UFH, LMWH და DOAC აკრძალულია → IV ბივალირუდინი ან არგატრობანი{liver_arg} პოსტოპ. ვარფარინზე (VKA) გადასვლით!'
        elif hit_severe_renal:
            liver_arg = ' (ღვიძლის ≥3x-ზე არგატრობანის დოზა მცირდება 75%-ით!)' if ctx.liver_3x else ''
            heparin_plan = f'HIT ანამნეზში! UFH, LMWH, ფონდაპარინუქსი და DOAC (CrCl/eGFR <30 / დიალიზი) კატეგორიულად აკრძალულია → ინტრაოპ./პოსტოპ. ბივალირუდინი ან არგატრობანი{liver_arg}!'
        elif ctx.liver_3x:
            heparin_plan = 'HIT ანამნეზში + ღვიძლის ≥3x დაზიანება! UFH, LMWH, DOAC და ფონდაპარინუქსი არარეკომენდებულია → უპირატესია ბივალირუდინი (არგატრობანის დოზა მცირდება 75%-ით!) და პოსტოპ. IPC / IV ბივალირუდინი!'
        else:
            heparin_plan = 'HIT ანამნეზში! არაფრაქციული ჰეპარინი (UFH) და LMWH კატეგორიულად აკრძალულია → ბივალირუდინი ან არგატრობანი (პოსტოპ. ფონდაპარინუქსი/DOAC CrCl ≥30-ზე)!'
    elif pathway == PreopPatient.Pathway.CEA and (s.get('brain_imaging') == 'large_or_hemorrhage' or getattr(ctx, 'active_thrombolysis_window', False)):
        heparin_plan = 'ყურადღება! ტვინის ჰემორაგია / ფართო ინფარქტი ან თრომბოლიზისი ≤6 დღეში — ნაადრევი სისტემური ჰეპარინიზაცია საშიშია ჰემორაგიული ტრანსფორმაციის გამო (ESVS 2023)!'
    elif ctx.is_venous:
        heparin_plan = f'ინტრაოპერაციული UFH არ ესაჭიროება · VTE პროფილაქტიკა: Caprini {ctx.caprini_score} ქ. ({ctx.caprini_tier}) — {ctx.caprini_advice}'
    else:
        plt_s = f'{ctx.plt} ×10^9/L' if ctx.plt is not None else 'შესატანია!'
        heparin_plan = f'არაფრაქციული ჰეპარინი (UFH) 70–100 სე/კგ IV კლამპირებამდე/ჩარევამდე (სამიზნე ACT > 200–250 წმ · საბაზისო PLT: {plt_s})'

    contrast_plan = ''
    hyd_fluid_note = (
        'ჰიპერნატრიემია Na+ ≥150 — 0.9% NaCl-ით დატვირთვა აკრძალულია!'
        if (ctx.na_val is not None and ctx.na_val >= 150) else
        ('ფრთხილი 0.9% NaCl 0.5 მლ/კგ/სთ (HFrEF!)' if ctx.chf_hyd_flag else 'პერიპროცედურული 0.9% NaCl 1 მლ/კგ/სთ ჰიდრატაცია')
    )
    if ctx.max_contrast_ml is not None:
        contrast_plan = f'მაქს. იოდის კონტრასტის ბიუჯეტი (Gurm): ≤ {ctx.max_contrast_ml} მლ'
        if ctx.co2_angio_advised:
            contrast_plan += f' · CrCl/eGFR < 30: უპირატესია CO2 ანგიოგრაფია / IVUS + {hyd_fluid_note}!'
        elif ctx.needs_hydration:
            contrast_plan += f' + {hyd_fluid_note}'
        if q_filled and ctx.eff_allergy_iodine:
            contrast_plan += ' · იოდზე ალერგია: სავალდებულო პრემედიკაცია (სტეროიდი + H1-ბლოკატორი)!'
    elif q_filled and ctx.eff_allergy_iodine and not ctx.is_venous:
        contrast_plan = 'იოდზე ალერგია — სავალდებულო პრემედიკაცია (სტეროიდი + H1-ბლოკატორი) კონტრასტის გამოყენებისას!'

    statin_tail = '' if ctx.liver_3x else ' + მაღალი ინტენსივობის სტატინი (LDL <1.4 მმოლ/ლ — ESVS Class I)'
    if ctx.is_pta:
        pause_oac_win = '~24 სთ-ში'
    elif has_hit:
        pause_oac_win = '48–72 სთ-ში (HIT: ჰეპარინი/კლექსანი აკრძალულია — IPC / არაჰეპარინული სქემა!)'
    elif hit_severe_renal:
        pause_oac_win = '48–72 სთ-ში (CrCl<30: D+1 პროფილაქტ. კანქვეშა UFH / IPC)'
    else:
        pause_oac_win = '48–72 სთ-ში (D+1 პროფილაქტ. LMWH)'
    if ctx.is_venous:
        default_postop_at = ctx.caprini_advice
    elif ctx.post_antithrombotic_label:
        default_postop_at = ctx.post_antithrombotic_label + (statin_tail if 'სტატინი' not in ctx.post_antithrombotic_label else '')
    elif ctx.has_mech_valve:
        default_postop_at = f'უვადო ვარფარინი (VKA) კარდიოლოგის სამიზნე INR-ით + ჩვენებისას ასპირინი 75–100 მგ (DOAC აკრძალულია!){statin_tail}'
    elif ctx.has_full_oac:
        default_postop_at = f'მუდმივი ანტიკოაგულანტის (OAC) აღდგენა {pause_oac_win} ჰემოსტაზით (PAUSE) + ჩვენებისას {ctx.oac_partner_lbl} (სამმაგი თერაპიის გარეშე){statin_tail}'
    elif ctx.has_active_stent_dapt:
        default_postop_at = f'უწყვეტი DAPT: ასპირინი 100 მგ + {ctx.alt_p2y12_lbl} (კორონარული სტენტის დაცვა!){statin_tail}'
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
    patient, pathway, q, s, q_filled = ctx.patient, ctx.pathway, ctx.q, ctx.s, ctx.q_filled
    neuraxial_blockers = list(ctx.hard_nx_reasons)
    if ctx.solo_neuraxial_anatomic_mismatch:
        neuraxial_blockers.insert(0, 'ანატომიურად შეუსაბამო სოლო-ნეირაქსიული გეგმა!')
    asra_ufh_note = (
        ' · ASRA 2025 UFH წესი: ნეირაქსიული პუნქციიდან IV ჰეპარინამდე ≥60 წთ (სისხლიანი პუნქციისას — გადადება/კონსილიუმი); '
        'კათეტერის ამოღება UFH-დან ≥4–6 სთ-ში (ნორმალური aPTT) და P2Y12/DOAC/VOYAGER დაწყებამდე!'
        if (ctx.has_neuraxial_plan and not ctx.is_venous) else ''
    )
    vka_inr_caution = (
        f' [ვარფარინის ფონზე ASRA 5th Ed რეკომენდაციით სასურველია INR-ის სრული ნორმალიზება ≤1.2 (მიმდინარე: {ctx.eff_inr_nx})!]'
        if (q_filled and q.get('med_warfarin') and ctx.eff_inr_nx is not None and 1.2 < ctx.eff_inr_nx <= 1.4) else ''
    )
    if ctx.has_neuraxial_plan:
        if ctx.plt is None:
            neuraxial_blockers.append('PLT უცნობია')
        if ctx.eff_inr_nx is None:
            neuraxial_blockers.append('INR უცნობია')
        if not ctx.uninterrupted_p2y12 and q_filled and (
            q.get('med_doac') or q.get('med_dabigatran') or q.get('med_warfarin')
            or q.get('med_clopidogrel') or q.get('med_ticagrelor') or q.get('med_prasugrel')
            or ('rivaroxaban_2_5' in ctx.med_chips) or ('cilostazol' in ctx.med_chips)
        ):
            if not (s.get('med_hold_agreed') or s.get('dos_med_hold_verified')):
                neuraxial_blockers.append('ASRA წამლის შეჩერების ინტერვალი დასადასტურებელია')
        neuraxial_gate = (
            'საყურადღებო/შეზღუდულია (ASRA 5th Ed): ' + ', '.join(neuraxial_blockers) + vka_inr_caution + asra_ufh_note
            if (neuraxial_blockers or ctx.neuraxial_hard_blocked) else
            f'ASRA 5th Ed კრიტერიუმები დაცულია (INR {ctx.eff_inr_nx}, PLT {ctx.plt}) ✓{vka_inr_caution}{asra_ufh_note}'
        )
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
    if ctx.bp_arm_diff is not None and ctx.bp_arm_diff >= 20:
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


def _build_anesthetic_brief(ctx):
    patient, pathway, q, s, q_filled = ctx.patient, ctx.pathway, ctx.q, ctx.s, ctx.q_filled
    neuraxial_gate, bp_corridor = _build_anesthetic_neuraxial_and_bp(ctx)

    high_ischemic_rbc = ctx.ihd_flag or ctx.chf_flag or ctx.has_mech_valve or (pathway == PreopPatient.Pathway.CEA) or (ctx.max_car is not None and ctx.max_car >= 70) or ctx.is_clti_any
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
    if ctx.plt is not None and ctx.plt < 100:
        if 'hit_heparin' in ctx.allergy_chips:
            coag_pbm_bits.append('PLT <100 + HIT: პროფილაქტიკური თრომბოკონცენტრატის გადასხმა უკუნაჩვენებია (მხოლოდ სიცოცხლისთვის საშიშ სისხლდენაზე)!')
        else:
            coag_pbm_bits.append(f'თრომბოკონცენტრატის მზადყოფნა (PLT {ctx.plt} <100 — სამიზნე ≥80–100)')
    if ctx.fib is not None and ctx.fib < 1.8:
        coag_pbm_bits.append(f'ფიბრინოგენის კონცენტრატი 3–4 გ IV / კრიოპრეციპიტატი (Fib {ctx.fib} <1.8 გ/ლ — ESAIC 2023)')
    if ctx.inr is not None and ctx.inr > 1.5 and not ctx.on_oral_anticoag:
        coag_pbm_bits.append(f'კოაგულოპათია OAC-ის გარეშე (INR {ctx.inr} >1.5): ვიტამინი K 10 მგ IV ± 4F-PCC (ESAIC 2023)')
    if coag_pbm_bits:
        transfusion_pbm += ' · ' + ' · '.join(coag_pbm_bits)

    airway_bits = [ctx.mallampati_label, f'STOP-Bang: {ctx.stop_bang_label}']
    if ctx.is_rupture_suspect:
        airway_bits.append('RSI ინდუქცია მხოლოდ საოპერაციო ველის დამუშავებისა და ქირურგიული მზადყოფნის შემდეგ!')
    if (ctx.k_val is not None and ctx.k_val >= 5.5) or (q_filled and (q.get('cea_disabling_stroke') or (q.get('neuro') and ctx.is_symp_cea))):
        airway_bits.append('სუქცინილქოლინი უკუნაჩვენებია (ლეტალური ჰიპერკალიემიის რისკი — როკურონიუმი + სუგამადექსი)!')
    if q_filled and q.get('glp1'):
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

    needs_glycemic_corridor = bool((q_filled and (q.get('dm') or q.get('dm_insulin'))) or (ctx.gl_mmol is not None and ctx.gl_mmol >= 7.0) or (ctx.hba1c is not None and ctx.hba1c >= 6.5))
    needs_vriii = bool((q_filled and q.get('dm_insulin') and ctx.open_aortic) or (ctx.gl_mmol is not None and ctx.gl_mmol >= 12.0) or (ctx.dos_gl is not None and ctx.dos_gl >= 12.0))
    vriii_sfx = ' · ინტრაოპ./ICU: IV ინსულინის ინფუზია (VRIII) საათობრივი გლუკოზისა და K+ კონტროლით!' if needs_vriii else ''

    base_disp = (
        ('პოსტოპ. რეანიმაცია / HDU (დაჯავშნილია ✓)' if s.get('icu_bed_booked') else 'პოსტოპ. რეანიმაცია / HDU — საწოლი დასაჯავშნია!')
        if ctx.needs_icu_bed else
        ('დღის სტაციონარი / ამბულატორიული გაწერა ადრეული აქტივაციით' if (ctx.is_venous and patient.op_subtype == PreopPatient.OpSubtype.EVLA) else 'სისხლძარღვთა ქირურგიის სტაციონარული პალატა')
    )
    disp_extras = []
    mins_cv_risk = bool(
        (ctx.age or 0) >= 65
        or ctx.ihd_flag or ctx.chf_flag or ctx.on_dialysis
        or (ctx.egfr is not None and ctx.egfr < 60)
        or (q_filled and (q.get('dm') or q.get('neuro') or q.get('htn')))
    )
    if not ctx.is_venous and (
        ctx.open_aortic
        or ((patient.op_subtype == 'evar' or ctx.is_pad_bypass) and mins_cv_risk)
        or ctx.rcri['score'] >= 2
        or (ctx.nt_probnp is not None and ctx.nt_probnp >= 125)
        or (ctx.hs_ctn is not None and ctx.hs_ctn >= 14)
    ):
        disp_extras.append('MINS მონიტორინგი: hs-cTn 24 სთ და 48 სთ (ESC 2022)')
    if ctx.needs_delirium_guard and not ctx.is_venous:
        disp_extras.append('დელირიუმის პრევენცია (ბენზოდიაზეპინების გარეშე — ESAIC 2024)')

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
        'disposition': base_disp + ((' · ' + ' · '.join(disp_extras)) if disp_extras else ''),
    }


def eval_briefings(ctx):
    _eval_scores_and_missing_s1(ctx)
    _eval_missing_s2_and_clearance_seal(ctx)
    _build_surgical_brief(ctx)
    _build_anesthetic_brief(ctx)
