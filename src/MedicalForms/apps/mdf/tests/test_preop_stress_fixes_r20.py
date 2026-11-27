"""Regression tests for Stage-4 Round-20 (v1.2.19) clinical, NLP, ReDoS, RBAC, UI, and registry fixes."""
import datetime
import time
from dataclasses import replace

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.mdf.models import BoardEntry, PreopFile, PreopPatient, StaffProfile
from apps.mdf.preop import build_med_schedule, evaluate, generate_anamnesis, make_self_fill_token
from apps.mdf.preop.anamnesis import build_self_fill_highlights
from apps.mdf.preop.meds_freetext import _detect_freetext_allergies, _detect_freetext_high_risk_meds
from apps.mdf.preop.nurse_plan import build_phone_med_script
from apps.mdf.preop.plain_ka import plain_asa_ka, plain_drug_name, scrub_plain_ka
from apps.mdf.preop.registry import REGISTRY_VERSION, RULES_BY_ID, match_rule_for_text
from apps.mdf.preop.utils import mask_person_id, mask_phone

TODAY = datetime.date(2026, 11, 27)
OP_DATE = (TODAY + datetime.timedelta(days=10)).isoformat()


def _mk(subtype='open', *, age=66, sex='M', side='', q=None, s=None, archived=False, approved=False):
    base_q = {'filled': True, 'mets_ok': True, 'allergy_iodine_asked': True}
    if subtype == 'evla':
        base_q.setdefault('ven_ceap', 'C2')
        side = side or 'R'
    elif subtype.startswith(('cea', 'bypass', 'pta', 'phleb')):
        side = side or 'R'
    base_s = {
        'bp_sys': 126, 'bp_dia': 76, 'hr': 72, 'spo2': 98,
        'weight_kg': 78, 'height_cm': 174, 'hb': 136, 'wbc': 6.8, 'plt': 230,
        'inr': 1.02, 'fibrinogen': 3.1, 'creat': 84, 'creat_unit': 'umol',
        'k': 4.2, 'na': 140, 'glucose': 5.3, 'albumin': 42, 'ldl': 1.2,
        'labs_date': TODAY.isoformat(), 'ecg': 'normal', 'cxr': 'normal',
        'blood_group': 'O(I) Rh+', 'blood_reserved': True, 'anesth_done': True,
        'planned_surgery_date': OP_DATE,
    }
    if subtype in ('open', 'evar', 'tbd'):
        base_s.update({'aaa_mm': 58, 'cta_date': TODAY.isoformat(), 'cta_disc': True, 'popliteal_screen_status': 'normal'})
    elif subtype in ('evla', 'phlebectomy'):
        base_s.update({'ven_duplex_status': 'deep_patent_reflux', 'ven_gsv_mm': 7.0, 'compression_ready': True})
    if q:
        base_q.update(q)
    if s:
        base_s.update(s)
    pw_map = {
        'open': PreopPatient.Pathway.AAA_INFRA, 'evar': PreopPatient.Pathway.AAA_INFRA, 'tbd': PreopPatient.Pathway.AAA_INFRA,
        'cea_symp': PreopPatient.Pathway.CEA, 'cea_asymp': PreopPatient.Pathway.CEA,
        'abf_claud': PreopPatient.Pathway.ABF, 'abf_clti': PreopPatient.Pathway.ABF,
        'bypass_claud': PreopPatient.Pathway.PAD_BYPASS, 'bypass_clti': PreopPatient.Pathway.PAD_BYPASS,
        'pta_claud': PreopPatient.Pathway.PAD_PTA, 'pta_clti': PreopPatient.Pathway.PAD_PTA,
        'evla': PreopPatient.Pathway.VENOUS, 'phlebectomy': PreopPatient.Pathway.VENOUS,
    }
    p = PreopPatient(
        patient_name='Test R20', age=age, sex=sex, side=side,
        pathway=pw_map[subtype], op_subtype=subtype, questionnaire=base_q, studies=base_s,
        archived=archived, started_on=TODAY,
    )
    if approved:
        p.approved_at = timezone.now()
    return p


class PreopStressFixesR20Tests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.doc = User.objects.create_user('r20_doc', password='pw', is_staff=True, is_superuser=True)
        StaffProfile.objects.create(user=self.doc, display_name='ტესტ R20 ქირურგი')
        self.nurse = User.objects.create_user('r20_nurse', password='pw', is_staff=False)
        self.c_doc = Client()
        self.c_doc.force_login(self.doc)
        self.c_nurse = Client()
        self.c_nurse.force_login(self.nurse)

    def test_r20_s1_redos_linear_time_huge_int_and_paren_cleanup(self):
        t0 = time.monotonic()
        res_b = scrub_plain_ka('[' + '-' * 50000 + 'X', second_person=False, strip_quoted_ft=True)
        res_p = scrub_plain_ka('(' + '-' * 50000 + '1', second_person=False, strip_quoted_ft=True)
        self.assertLess(time.monotonic() - t0, 1.0)
        self.assertEqual(res_b, '[X')
        self.assertEqual(res_p, '(1')
        cleaned_nested = scrub_plain_ka('უკუნაჩვენებია (ტექსტში ()) — შეამოწმეთ', second_person=False, strip_quoted_ft=True)
        self.assertNotIn(' )', cleaned_nested)
        self.assertIn('(ტექსტში)', cleaned_nested)
        inv_bp = scrub_plain_ka('R 90/100 და L 88/95', second_person=False, strip_quoted_ft=True)
        self.assertIn('მარჯვენა 90/100', inv_bp)
        self.assertIn('მარცხენა 88/95', inv_bp)
        huge = 10**5000
        for bad in (huge, [huge], {'k': huge}):
            self.assertEqual(plain_asa_ka(bad), 'შეფასების პროცესში')
            self.assertEqual(scrub_plain_ka(bad), '')
            self.assertEqual(plain_drug_name(bad), '')
            self.assertEqual(mask_person_id(bad), '')
            self.assertEqual(mask_phone(bad), '')

    def test_r20_s1_nlp_lmwh_georgian_negation_and_beta_lactam(self):
        for phrase in ('დაბალმოლეკულური ჰეპარინი', 'low molecular weight heparin', 'LMWH 40mg', 'tinzaparin 4500 IU'):
            ft = _detect_freetext_high_risk_meds(phrase)
            self.assertTrue(ft['has_ft_lmwh'], phrase)
            self.assertFalse(ft['has_ft_ufh'], phrase)
            self.assertIn('parenteral_oac', ft['matched_keys'], phrase)
            self.assertIn('hit_heparin', _detect_freetext_allergies(phrase), phrase)
            ev = evaluate(_mk('open', q={'other_meds': phrase}, s={'creat': 320, 'creat_unit': 'umol'}), today=TODAY)
            self.assertTrue(any(r.get('contraindicated') for r in ev['med_schedule']), phrase)
        self.assertIn('beta_lactam', _detect_freetext_allergies('cephalexin 500mg'))
        self.assertIn('beta_lactam', _detect_freetext_allergies('cefuroxime 500mg'))
        for txt, want_key, absent_key in (
            ('ვარფარინი არ იღებს პლავიქსი 75 მგ', 'clopidogrel', 'warfarin'),
            ('არ იღებს კლოპიდოგრელს, იღებს ელიქვისს 5 მგ', 'doac', 'clopidogrel'),
            ('Stopped warfarin but not apixaban', 'doac', 'warfarin'),
            ('ვარფარინი მოხსნილია, მაგრამ ელიქვისი არა', 'doac', 'warfarin'),
        ):
            ft = _detect_freetext_high_risk_meds(txt)
            self.assertIn(want_key, ft['matched_keys'], txt)
            self.assertNotIn(absent_key, ft['matched_keys'], txt)

    def test_r20_s1_hit_lmwh_raaa_reversal_mycotic_triple_and_pad_gsv(self):
        # HIT + CrCl < 30 + FT LMWH must NOT advise UFH; must recommend bivalirudin/argatroban
        ev_hit = evaluate(
            _mk('open', q={'allergy_chips': ['hit_heparin'], 'other_meds': 'Clexane 40 mg'}, s={'creat': 340, 'creat_unit': 'umol'}),
            today=TODAY,
        )
        par_row = next(r for r in ev_hit['med_schedule'] if 'პარენტერული' in r['drug'])
        self.assertIn('ბივალირუდინ', par_row['rule'])
        self.assertNotIn('არაფრაქციულ ჰეპარინზე (UFH) გადაყვანა', par_row['rule'])
        # rAAA + FT Xarelto 2.5 mg & FT Clexane -> emergency reversal
        ev_rv = evaluate(_mk('open', q={'aaa_pain': True, 'other_meds': 'Xarelto 2.5 mg BID'}, s={'bp_sys': 85, 'bp_dia': 50}), today=TODAY)
        self.assertTrue(any('4F-PCC' in r['rule'] for r in ev_rv['med_schedule']))
        ev_pr = evaluate(_mk('open', q={'aaa_pain': True, 'other_meds': 'Clexane 60 mg'}, s={'bp_sys': 85, 'bp_dia': 50}), today=TODAY)
        self.assertTrue(any('პროტამინ' in r['rule'] for r in ev_pr['med_schedule']))
        # Mycotic AAA handoff does not leave duplicate ABX tasks when re-admitted after allergy/morphology change
        p_myc = _mk('open', s={'aaa_morphology': 'inflammatory_mycotic', 'aaa_mm': 62})
        p_myc.save()
        self.c_doc.post(reverse('preop-admit-board', args=[p_myc.pk]), {'unit': 'ward'})
        p_myc.refresh_from_db()
        p_myc.questionnaire = {**p_myc.questionnaire, 'allergy_chips': ['beta_lactam']}
        p_myc.studies = {**p_myc.studies, 'aaa_morphology': 'fusiform'}
        p_myc.save()
        self.c_doc.post(reverse('preop-admit-board', args=[p_myc.pk]), {'unit': 'ward'})
        p_myc.refresh_from_db()
        bp_tasks = list(BoardEntry.objects.filter(patient_id=p_myc.studies['board_patient_id'], kind=BoardEntry.TASK).values_list('text', flat=True))
        self.assertFalse(any(t.startswith('მიკოზური AAA') for t in bp_tasks))
        self.assertEqual(sum(1 for t in bp_tasks if 'ბეტა-ლაქტამური ალერგია' in t), 1)
        # FT Xarelto 2.5 mg + Aspirin + Clopidogrel -> explicit triple therapy red flag
        ev_tri = evaluate(_mk('open', q={'med_aspirin': True, 'med_clopidogrel': True, 'other_meds': 'Xarelto 2.5 mg BID'}), today=TODAY)
        self.assertTrue(any('სამმაგი ანტითრომბოზული' in f for f in ev_tri['red_flags']))
        # PAD bypass: adequate GSV ignores stale prosthetic alt_vein_mapped & clears on S1 POST; pending GSV keeps STOP
        p_adq = _mk('bypass_claud', s={'pad_gsv_status': 'adequate', 'alt_vein_mapped': 'no_autovein_prosthetic', 'abi_r': 0.55})
        p_adq.save()
        ev_adq = evaluate(p_adq, today=TODAY)
        self.assertNotEqual(ev_adq['clearance_seal']['code'], 'stop')
        self.c_doc.post(reverse('preop-patient', args=[p_adq.pk]), {'section': 'stage1', 'pad_gsv_status': 'adequate'})
        p_adq.refresh_from_db()
        self.assertEqual(p_adq.studies.get('alt_vein_mapped'), 'pending')
        p_pnd = _mk('bypass_claud', s={'pad_gsv_status': 'pending', 'alt_vein_mapped': 'no_autovein_prosthetic', 'abi_r': 0.55})
        p_pnd.save()
        self.assertEqual(evaluate(p_pnd, today=TODAY)['clearance_seal']['code'], 'stop')
        s2_html = self.c_doc.get(reverse('preop-patient', args=[p_pnd.pk]) + '?tab=s2').content.decode('utf-8')
        self.assertIn('name="alt_vein_mapped"', s2_html)

    def test_r20_s1_carried_over_controls_venous_reflux_and_step_any(self):
        p_sw = _mk(
            'phlebectomy',
            s={'anesthesia_plan': 'neuraxial', 'echo_valve_issue': True, 'fibrinogen': 1.4, 'liver_status': '3x', 'abi_l': 0.4},
        )
        p_sw.save()
        h_s1 = self.c_doc.get(reverse('preop-patient', args=[p_sw.pk]) + '?tab=s1').content.decode('utf-8')
        self.assertIn('value="deep_patent_reflux"', h_s1)
        self.assertNotIn('value="reflux_only"', h_s1)
        for fld in ('echo_valve_issue', 'fibrinogen', 'liver_status', 'abi_l'):
            self.assertIn(f'name="{fld}"', h_s1)
        self.assertIn('step="any" name="ven_gsv_mm"', h_s1)
        p_q = _mk('open', q={'ven_thrombophilia': True, 'ven_cancer_immob': True})
        p_q.save()
        h_q = self.c_doc.get(reverse('preop-patient', args=[p_q.pk]) + '?tab=q').content.decode('utf-8')
        self.assertIn('name="ven_thrombophilia"', h_q)
        self.assertIn('name="ven_cancer_immob"', h_q)

    def test_r20_s2_rbac_archive_delete_dos_lock_and_selffill_age_clear(self):
        User = get_user_model()
        reg_doc = User.objects.create_user('r20_regdoc', password='pw', is_staff=False)
        StaffProfile.objects.create(user=reg_doc, display_name='რეგულარული ექიმი R20')
        c_regdoc = Client()
        c_regdoc.force_login(reg_doc)
        for label, arch, out_st in (('archived', True, 'scheduled'), ('cancelled', False, 'cancelled_medical'), ('operated', False, 'operated')):
            p = _mk('open', s={'case_outcome_status': out_st, 'planned_surgery_date': OP_DATE}, archived=arch)
            p.save()
            del_cli = self.c_doc if arch else c_regdoc
            r_del = del_cli.post(reverse('preop-move', args=[p.pk]), {'action': 'delete'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
            self.assertEqual(r_del.status_code, 403, label)
            self.assertTrue(PreopPatient.objects.filter(pk=p.pk).exists(), label)
            self.c_doc.post(
                reverse('preop-patient', args=[p.pk]),
                {
                    'section': 'dos_vqi', 'case_outcome_status': out_st,
                    'planned_surgery_date': '2026-12-20', 'dos_bp_sys': '190', 'dos_glucose': '18',
                    'dos_identity_consent': '1', 'icu_bed_booked': '1',
                },
            )
            p.refresh_from_db()
            self.assertEqual(p.studies.get('planned_surgery_date'), OP_DATE, label)
            self.assertIsNone(p.studies.get('dos_bp_sys'), label)
            self.assertFalse(p.studies.get('dos_identity_consent'), label)
            pp_html = self.c_doc.get(reverse('preop-passport', args=[p.pk])).content.decode('utf-8')
            self.assertNotIn('ოპერაცია: 07.12.2026', pp_html, label)
        # Unlocked self-fill clears stale _invalid_inputs['age'] on valid birth_date or age
        p_sf = _mk('evla', age=None, s={'_invalid_inputs': {'age': '5'}})
        p_sf.save()
        tok = make_self_fill_token(p_sf)
        self.c_nurse.post(reverse('preop-self-fill', args=[tok]), {'birth_date': '1965-06-15', 'sex': 'M'})
        p_sf.refresh_from_db()
        self.assertEqual(p_sf.age, 61)
        self.assertNotIn('age', p_sf.studies.get('_invalid_inputs') or {})

    def test_r20_s2_neuraxial_parity_prasugrel_anamnesis_stop_and_registry(self):
        self.assertEqual(REGISTRY_VERSION, '1.2.19')
        # Standalone build_med_schedule fail-closed neuraxial parity on missing/ambiguous CrCl
        for cr_s in ({'creat': None}, {'creat': 22, 'creat_unit': ''}, {'creat': 1.2, 'creat_unit': 'umol'}):
            p_nx = _mk('phlebectomy', q={'med_doac': True}, s={'anesthesia_plan': 'auto', **cr_s})
            ev_nx = evaluate(p_nx, today=TODAY)
            ms_sa = build_med_schedule(p_nx, p_nx.questionnaire, p_nx.studies, ev_nx['egfr'], today=TODAY, crcl=ev_nx['crcl'])
            self.assertEqual(ev_nx['med_schedule'][0]['rule'], ms_sa[0]['rule'], cr_s)
            self.assertNotIn('ნეირაქსიული / ეპიდურული ბლოკადა: შეჩერება', ms_sa[0]['rule'], cr_s)
        # Prasugrel age >= 75 de-escalation in stent_p2y12_lbl + borderline fibrinogen in neuraxial_gate
        ev_pr = evaluate(_mk('bypass_clti', age=78, q={'med_prasugrel': True, 'recent_stent': True}, s={'post_antithrombotic_plan': 'sapt'}), today=TODAY)
        self.assertIn('პრასუგრელის დე-ესკალაციით', ev_pr['post_antithrombotic_label'])
        ev_fib = evaluate(_mk('phlebectomy', s={'anesthesia_plan': 'neuraxial', 'fibrinogen': 1.6}), today=TODAY)
        self.assertIn('სასაზღვრო ჰიპოფიბრინოგენემია (1.6 < 1.8', ev_fib['anesthetic_brief']['neuraxial_gate'])
        # Objective chronic-disease flags suppress false "no chronic disease" in anamnesis
        for q_ov, s_ov in (
            ({}, {'creat': 160, 'creat_unit': 'umol'}),
            ({}, {'liver_status': '3x'}),
            ({}, {'bp_sys': 168, 'bp_dia': 102}),
            ({}, {'hb': 95}),
            ({'other_meds': 'Xarelto 2.5 mg BID'}, {}),
        ):
            p_an = _mk('evla', q=q_ov, s=s_ov)
            self.assertNotIn('მნიშვნელოვანი ქრონიკული დაავადებების გარეშე', generate_anamnesis(p_an, evaluate(p_an, today=TODAY)))
        # Self-fill highlights preserve English drugs & allergy without erasing to bare dose or 'D'
        for om, want in (('paracetamol 500mg', 'ტკივილგამაყუჩებელი'), ('concor 5mg', 'ბეტა-ბლოკერი'), ('vitamin D 2000 IU', 'ვიტამინი დ')):
            hl = ' '.join(t for _, t in build_self_fill_highlights({'filled': True, 'other_meds': om}))
            self.assertIn(want, hl, om)
        # STOP + acute_hold ACEi/ARB sets has_urgent_med_action; STOP + Warfarin suppresses routine 5-day text
        p_ac = _mk('open', q={'med_chips': ['acei_arb']}, s={'anesthesia_plan': 'local', 'bp_sys': 88, 'bp_dia': 56})
        p_ac.save()
        ev_ac = evaluate(p_ac, today=TODAY)
        self.assertTrue(ev_ac['has_urgent_med_action'])
        pp_ac = self.c_doc.get(reverse('preop-passport', args=[p_ac.pk])).content.decode('utf-8')
        self.assertIn('უკუნაჩვენები/დაუზუსტებელი ან მწვავე მედიკამენტების გამო', pp_ac)
        p_sw = _mk('open', q={'med_warfarin': True}, s={'anesthesia_plan': 'local', 'inr': 2.4})
        p_sw.save()
        ev_sw = evaluate(p_sw, today=TODAY)
        self.assertNotIn('5 დღით ადრე', self.c_doc.get(reverse('preop-patient', args=[p_sw.pk]) + '?tab=s2').content.decode('utf-8'))
        sc_sw = build_phone_med_script(p_sw, ev_sw, TODAY)
        self.assertEqual(sc_sw['urgent_rows'], [])
        self.assertNotIn('5 დღით ადრე', sc_sw['stop_rows'][0]['rule'])
        # New nurse UI file category headers use plain Georgian label
        PreopFile.objects.create(patient=p_sw, category=PreopFile.Category.DUPLEX, original_name='d.pdf')
        self.c_nurse.post(reverse('preop-ui-mode'), {'mode': 'new'})
        pn_html = self.c_nurse.get(reverse('preop-patient', args=[p_sw.pk])).content.decode('utf-8')
        fc_start = pn_html.index('id="nw-files-card"')
        fc_sec = pn_html[fc_start:pn_html.index('</section>', fc_start)]
        self.assertNotIn('ABI', fc_sec)
        self.assertIn('დუპლექს-სკანირება / ფეხის არტერიული ინდექსი', fc_sec)
        # Dynamic registry mutations: MED_WARFARIN_HOLD_DAYS, MED_VOYAGER_RIVAROXABAN_2_5, MED_MECH_VALVE_SAFETY
        orig_v = RULES_BY_ID['MED_VOYAGER_RIVAROXABAN_2_5']
        orig_m = RULES_BY_ID['MED_MECH_VALVE_SAFETY']
        try:
            RULES_BY_ID['MED_VOYAGER_RIVAROXABAN_2_5'] = replace(orig_v, value={**orig_v.value, 'min_egfr': 20})
            ev_v = evaluate(_mk('phlebectomy', q={'med_chips': ['rivaroxaban_2_5']}, s={'anesthesia_plan': 'neuraxial', 'creat': 320, 'creat_unit': 'umol'}), today=TODAY)
            self.assertIn('< 20', ev_v['anesthetic_brief']['neuraxial_gate'])
            RULES_BY_ID['MED_MECH_VALVE_SAFETY'] = replace(orig_m, value=2.5)
            ev_m = evaluate(_mk('open', q={'med_warfarin': True}), today=TODAY)
            self.assertIn('INR ≥2.5-მდე', ev_m['surgical_brief']['postop_antithrombotic'])
        finally:
            RULES_BY_ID['MED_VOYAGER_RIVAROXABAN_2_5'] = orig_v
            RULES_BY_ID['MED_MECH_VALVE_SAFETY'] = orig_m
        self.assertIs(match_rule_for_text('პროგრამულ დიალიზზე SGLT2'), RULES_BY_ID['MED_SGLT2_HOLD_DAYS'])
        self.assertIs(match_rule_for_text('მითითებულია სტატინი — ღვიძლის'), RULES_BY_ID['MED_CARDIO_VASOACTIVE_STEROID'])
