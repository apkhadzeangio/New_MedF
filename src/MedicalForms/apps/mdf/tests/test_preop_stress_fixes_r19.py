"""Regression tests for Stage-4 Round-19 (v1.2.18) clinical, NLP, RBAC, registry, and plain-Georgian fixes."""
import datetime
import re
from dataclasses import replace

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.mdf.models import Case, PreopFile, PreopPatient, StaffProfile
from apps.mdf.preop import evaluate, generate_anamnesis
from apps.mdf.preop.anamnesis import build_self_fill_highlights
from apps.mdf.preop.meds_freetext import _detect_freetext_allergies, _detect_freetext_high_risk_meds
from apps.mdf.preop.nurse_plan import (
    build_patient_plain_explanation,
    build_phone_med_script,
    detect_postponement_impacts,
)
from apps.mdf.preop.plain_ka import scrub_plain_ka
from apps.mdf.preop.registry import REGISTRY_VERSION, RULES_BY_ID, match_rule_for_text
from apps.mdf.preop.views.board import _build_handoff_tasks

TODAY = datetime.date(2026, 11, 27)
OP_DATE = (TODAY + datetime.timedelta(days=10)).isoformat()


def _mk(subtype='open', *, age=66, sex='M', side='', q=None, s=None, archived=False, approved=False):
    base_q = {'filled': True, 'mets_ok': True, 'allergy_iodine_asked': True}
    if subtype == 'evla':
        base_q.setdefault('ven_ceap', 'C2')
        side = side or 'R'
    elif subtype.startswith('cea'):
        side = side or 'R'
    elif subtype.startswith(('bypass', 'pta', 'phleb')):
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
    elif subtype == 'evla':
        base_s.update({'ven_duplex_status': 'reflux_only', 'ven_gsv_mm': 7.0, 'compression_ready': True})
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
        patient_name='Test R19', age=age, sex=sex, side=side,
        pathway=pw_map[subtype], op_subtype=subtype, questionnaire=base_q, studies=base_s,
        archived=archived, started_on=TODAY,
    )
    if approved:
        p.approved_at = timezone.now()
    return p


class PreopStressFixesR19Tests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.doc = User.objects.create_user('r19_doc', password='pw', is_staff=True)
        StaffProfile.objects.create(user=self.doc, display_name='ტესტ R19 ქირურგი')
        self.nurse = User.objects.create_user('r19_nurse', password='pw', is_staff=False)
        self.c_doc = Client()
        self.c_doc.force_login(self.doc)
        self.c_nurse = Client()
        self.c_nurse.force_login(self.nurse)

    def test_r19_s1_open_aortic_mismatch_approve_dos_ready_and_passport(self):
        dos_all = {
            'anesthesia_plan': 'local', 'dos_identity_consent': True, 'dos_fasting_confirmed': True,
            'dos_med_hold_verified': True, 'dos_abx_ready': True, 'dos_blood_implant_ready': True,
            'icu_bed_booked': True,
        }
        p = _mk('open', q={'med_aspirin': True, 'med_chips': ['statin']}, s=dos_all)
        p.save()
        ev0 = evaluate(p, today=TODAY)
        self.assertEqual(ev0['clearance_seal']['code'], 'stop')
        self.assertTrue(ev0['solo_neuraxial_anatomic_mismatch'])
        self.assertEqual(ev0['status'], 'needs_doctor')
        self.assertFalse(ev0['dos_ready'])
        self.c_doc.post(reverse('preop-patient', args=[p.pk]), {'section': 'signoff', 'action': 'approve'})
        p.refresh_from_db()
        self.assertIsNone(p.approved_at)
        html = self.c_doc.get(reverse('preop-passport', args=[p.pk])).content.decode('utf-8')
        self.assertNotIn('საოპერაციოში შეყვანა დაშვებულია', html)
        self.assertNotIn('კონსულტირებულია ✓', html)
        self.assertIn('გადასახედია!', html)

    def test_r19_s1_archived_and_inactive_med_dates_dos_ready_and_post_locks(self):
        dos_evla = {'dos_identity_consent': True, 'dos_site_marked': True, 'dos_fasting_confirmed': True}
        for label, arch, out_st in (
            ('archived', True, 'scheduled'),
            ('cancelled_medical', False, 'cancelled_medical'),
            ('cancelled_patient', False, 'cancelled_patient'),
            ('deferred', False, 'deferred'),
        ):
            p = _mk('open', q={'med_warfarin': True}, s={'inr': 2.4, 'case_outcome_status': out_st, **dos_evla}, archived=arch)
            p.save()
            ev = evaluate(p, today=TODAY)
            self.assertEqual(ev['clearance_seal']['code'], 'inactive', label)
            self.assertFalse(ev['dos_ready'], label)
            self.assertNotEqual(ev['status'], 'ready', label)
            script = build_phone_med_script(p, ev, TODAY)
            self.assertEqual(script['stop_rows'], [], label)
            self.assertEqual(script['caution_rows'], [], label)
            guide_html = self.c_doc.get(reverse('preop-patient-guide', args=[p.pk])).content.decode('utf-8')
            self.assertNotIn('01.12', guide_html, label)
            self.assertNotIn('02.12', guide_html, label)
            pp_html = self.c_doc.get(reverse('preop-passport', args=[p.pk])).content.decode('utf-8')
            self.assertNotIn('01.12', pp_html, label)
            self.assertNotIn('საოპერაციოში შეყვანა დაშვებულია', pp_html, label)

        # Fuzz F3 + F7: cancelled_patient POST locks + archived file delete blocked even for staff/superuser
        p_cp = _mk('evla', s={'case_outcome_status': 'cancelled_patient'})
        p_cp.save()
        self.c_doc.post(reverse('preop-patient', args=[p_cp.pk]), {'section': 'header', 'patient_name': 'Mutated'})
        p_cp.refresh_from_db()
        self.assertEqual(p_cp.patient_name, 'Test R19')
        self.c_doc.post(reverse('preop-files', args=[p_cp.pk]), {'category': 'labs', 'file': SimpleUploadedFile('a.pdf', b'%PDF-1.4')})
        self.assertEqual(PreopFile.objects.filter(patient=p_cp).count(), 0)
        p_ar = _mk('evla', archived=True)
        p_ar.save()
        f_ar = PreopFile.objects.create(patient=p_ar, category='labs', file=SimpleUploadedFile('b.pdf', b'%PDF-1.4'), original_name='b.pdf')
        self.doc.is_superuser = True
        self.doc.save(update_fields=['is_superuser'])
        self.c_doc.post(reverse('preop-file-delete', args=[f_ar.pk]))
        self.assertTrue(PreopFile.objects.filter(pk=f_ar.pk).exists())

    def test_r19_s1_evla_uninterrupted_warfarin_mech_valve_hit_parity(self):
        p = _mk('evla', q={'med_warfarin': True, 'cad_chips': ['mech_valve'], 'allergy_chips': ['hit_heparin']}, s={'inr': 2.5})
        ev = evaluate(p, today=TODAY)
        self.assertTrue(ev['is_evla_warfarin_only'])
        vte_amb = ' | '.join(a for a in ev['amber_flags'] if 'თრომბოპროფილაქტიკა' in a)
        vte_task = next(t['label'] for t in ev['tasks'] if t['key'] == 'vte')
        self.assertNotIn('ბივალირუდინი', vte_amb)
        self.assertNotIn('ბივალირუდინი', vte_task)
        self.assertIn('მოუხსნელად', vte_amb)
        self.assertIn('მოუხსნელად', vte_task)

    def test_r19_s1_nlp_double_neg_unpunctuated_switch_contractions_and_doses(self):
        for txt, exp in (
            ('ვარფარინი არ მოუხსნეს', {'warfarin'}),
            ('ვარფარინი არ შეუწყვიტეს', {'warfarin'}),
            ('has not been stopped warfarin', {'warfarin'}),
            ('warfarin 5mg stopped apixaban 5mg', {'doac'}),
            ('warfarin stopped apixaban', {'doac'}),
            ('ვარფარინი 5მგ მოხსნილია ელიქვისი 5მგ', {'doac'}),
            ('apixaban 5mg warfarin stopped', {'doac'}),
            ('პლავიქსი 75 მგ შეწყვეტილია', set()),
            ('ვარფარინი 5 მგ მოხსნილია', set()),
            ('ვარფარინი შეაჩერა', set()),
            ('ვარფარინი 5 მგ შეაჩერა', set()),
            ("doesn't take warfarin", set()),
            ("isn't taking apixaban", set()),
            ("don't take clopidogrel", set()),
            ("haven't taken warfarin", set()),
            ('never took warfarin', set()),
        ):
            got = _detect_freetext_high_risk_meds(txt, q={})['matched_keys']
            self.assertEqual(got, exp, f'NLP mismatch for {txt!r}: got={got}')
        self.assertEqual(_detect_freetext_allergies("doesn't have penicillin allergy"), set())

    def test_r19_s2_saturated_ckd_med_schedule_and_unknown_crcl_neuraxial_gate(self):
        for q_ov, drug_kw in (
            ({'med_doac': True}, 'DOAC'),
            ({'med_dabigatran': True}, 'დაბიგატრანი'),
            ({'metformin': True}, 'მეტფორმინი'),
            ({'med_chips': ['rivaroxaban_2_5']}, 'ქსარელტო 2.5'),
            ({'med_chips': ['cilostazol']}, 'ცილოსტაზოლი'),
        ):
            p = _mk('open', age=85, sex='F', q=q_ov, s={'weight_kg': 20, 'creat': 1.6, 'creat_unit': 'mgdl'})
            ev = evaluate(p, today=TODAY)
            row = next(r for r in ev['med_schedule'] if drug_kw in r['drug'])
            self.assertTrue(row.get('contraindicated'), f'{drug_kw} row not contraindicated on saturated CrCl<30')
            self.assertEqual(row['tone'], 'red', drug_kw)
        for q_ov in ({'med_dabigatran': True}, {'med_doac': True}, {'med_chips': ['rivaroxaban_2_5']}):
            p_nx = _mk('phlebectomy', q=q_ov, s={'anesthesia_plan': 'neuraxial', 'creat': None, 'dos_med_hold_verified': True})
            ev_nx = evaluate(p_nx, today=TODAY)
            self.assertTrue(ev_nx['neuraxial_hard_blocked'], q_ov)
            self.assertNotIn('კრიტერიუმები დაცულია', ev_nx['anesthetic_brief']['neuraxial_gate'])

    def test_r19_s2_venous_to_nonvenous_switch_no_catch22_deadlock(self):
        p = _mk('evla', sex='F', age=42, q={'ven_hormones': True, 'ven_dvt_chips': ['vte_recent_3m']}, s={'liver_status': '3x', 'ven_duplex_status': 'acute_dvt'})
        p.save()
        self.c_doc.post(reverse('preop-patient', args=[p.pk]), {'section': 'header', 'patient_name': p.patient_name, 'op_subtype': 'open', 'age': '42', 'sex': 'F'})
        html_q = self.c_doc.get(reverse('preop-patient', args=[p.pk]) + '?tab=q').content.decode('utf-8')
        self.assertIn('ven_hormones_presented', html_q)
        self.assertIn('ven_dvt_chips_presented', html_q)
        html_s1 = self.c_doc.get(reverse('preop-patient', args=[p.pk]) + '?tab=s1').content.decode('utf-8')
        self.assertIn('name="ven_duplex_status"', html_s1)
        self.c_doc.post(reverse('preop-patient', args=[p.pk]), {'section': 'questionnaire', 'ven_hormones_presented': '1', 'ven_dvt_chips_presented': '1', 'mets_ok': '1'})
        self.c_doc.post(reverse('preop-patient', args=[p.pk]), {'section': 'stage1', 'ven_duplex_status': 'pending'})
        p.refresh_from_db()
        self.assertFalse(p.questionnaire.get('ven_hormones'))
        self.assertEqual(p.questionnaire.get('ven_dvt_chips'), [])
        self.assertEqual(p.studies.get('ven_duplex_status'), 'pending')

    def test_r19_s2_freetext_riva_low_parenteral_oac_dti_and_p2y12_alerts(self):
        # Free-text Xarelto 2.5 mg: rAAA 4F-PCC, ESC Class III, explicit DAPT guard
        ev_rup = evaluate(_mk('open', q={'aaa_pain': True, 'other_meds': 'Xarelto 2.5 mg x2'}, s={'bp_sys': 80, 'bp_dia': 50, 'hr': 115}), today=TODAY)
        self.assertIn('4F-PCC', ev_rup['surgical_brief']['tactical_note'])
        ev_esc = evaluate(_mk('bypass_clti', q={'med_ticagrelor': True, 'other_meds': 'Xarelto 2.5 mg BID'}), today=TODAY)
        self.assertTrue(any('ESC Class III' in f for f in ev_esc['red_flags']))
        ev_dapt = evaluate(_mk('bypass_clti', q={'other_meds': 'Xarelto 2.5 mg x2'}, s={'post_antithrombotic_plan': 'dapt'}), today=TODAY)
        self.assertTrue(any('სამმაგი' in f or 'DAPT' in f for f in ev_dapt['red_flags']))
        # Structured riva_2_5 + free-text Brilinta
        ev_r25 = evaluate(_mk('bypass_clti', q={'med_chips': ['rivaroxaban_2_5'], 'other_meds': 'Brilinta 90mg'}), today=TODAY)
        r_r25 = next(r for r in ev_r25['med_schedule'] if 'ქსარელტო 2.5' in r['drug'])
        self.assertTrue(r_r25['contraindicated'])
        self.assertEqual(r_r25['tone'], 'red')
        # Parenteral OAC/DTI: Arixtra+MV, EVLA+Warfarin+Clexane, pure IV DTI
        ev_arx = evaluate(_mk('bypass_claud', q={'cad_chips': ['mech_valve'], 'allergy_chips': ['hit_heparin'], 'other_meds': 'Arixtra 2.5 mg'}), today=TODAY)
        self.assertTrue(ev_arx['has_bad_med'])
        ev_evp = evaluate(_mk('evla', q={'med_warfarin': True, 'other_meds': 'Clexane 40 mg'}, s={'inr': 2.5}), today=TODAY)
        self.assertFalse(ev_evp['is_evla_warfarin_only'])
        self.assertTrue(ev_evp['has_bad_med'])
        ev_dti = evaluate(_mk('open', q={'allergy_chips': ['hit_heparin'], 'other_meds': 'Bivalirudin'}), today=TODAY)
        r_dti = next(r for r in ev_dti['med_schedule'] if 'არაჰეპარინული' in r['drug'])
        self.assertNotIn('LMWH', r_dti['rule'])
        self.assertIn('−2–4 სთ', r_dti['rule'])
        # Structured amiodarone+warfarin CYP2C9 + ticagrelor/prasugrel red flags
        ev_amio = evaluate(_mk('open', q={'med_warfarin': True, 'med_chips': ['amiodarone']}, s={'inr': 2.3}), today=TODAY)
        self.assertTrue(any('CYP2C9' in a for a in ev_amio['amber_flags']))
        ev_pr78 = evaluate(_mk('bypass_clti', age=78, q={'med_prasugrel': True}, s={'weight_kg': 54}), today=TODAY)
        self.assertTrue(any('≥75 წლის' in f for f in ev_pr78['red_flags']))
        self.assertTrue(any('<60 კგ' in f for f in ev_pr78['red_flags']))
        ev_tl = evaluate(_mk('bypass_clti', sex='F', age=30, q={'pregnant_possible': True, 'med_ticagrelor': True}, s={'liver_status': '3x'}), today=TODAY)
        self.assertTrue(any('ორსულობის დროს ტიკაგრელორი' in f for f in ev_tl['red_flags']))
        self.assertTrue(any('ტიკაგრელორი უკუნაჩვენებია' in f for f in ev_tl['red_flags']))

    def test_r19_s2_mycotic_aaa_abx_parity_passport_draft_and_stop_banner(self):
        # Mycotic AAA DoS ABX & Board task parity
        ev_myc = evaluate(_mk('open', s={'aaa_morphology': 'inflammatory_mycotic'}), today=TODAY)
        dos_abx = next(c['label'] for c in ev_myc['dos_checks'] if c['key'] == 'dos_abx_ready')
        self.assertNotIn('ცეფაზოლინი', dos_abx)
        self.assertIn('ემპირიული', dos_abx)
        b_tasks = [t for _, t in _build_handoff_tasks(ev_myc, ev_myc['q'], ev_myc['s'], ev_myc['pathway'], set(), 80)]
        self.assertTrue(any('მიკოზური AAA' in t and 'ემპირიული' in t for t in b_tasks))
        # Fuzz F5: container with 10**5000 does not crash
        ev_big = evaluate(_mk('open', s={'weight_kg': [10**5000], 'hb': {'a': 10**5000}}), today=TODAY)
        self.assertIn('clearance_seal', ev_big)
        # Fuzz F4: conditional approved EVLA card shows draft banner & no green approval on passport.html
        p_cond = _mk('evla', s={'compression_ready': False}, approved=True)
        p_cond.save()
        pp_cond = self.c_doc.get(reverse('preop-passport', args=[p_cond.pk])).content.decode('utf-8')
        self.assertIn('სამუშაო (დაუდასტურებელი) ასლი', pp_cond)
        self.assertNotIn('[დადასტურებულია ექიმთან ✓]', pp_cond)
        # Combos S2-7: STOP banner distinguishes contraindicated meds & locked rule clears routine hold text
        p_st = _mk('open', q={'med_dabigatran': True, 'med_warfarin': True}, s={'anesthesia_plan': 'neuraxial', 'creat': 320, 'creat_unit': 'umol', 'inr': 2.4})
        p_st.save()
        pp_st = self.c_doc.get(reverse('preop-passport', args=[p_st.pk])).content.decode('utf-8')
        self.assertIn('უკუნაჩვენები/დაუზუსტებელი ან მწვავე მედიკამენტების გამო', pp_st)
        p_stw = _mk('open', q={'med_warfarin': True}, s={'anesthesia_plan': 'neuraxial', 'inr': 2.4})
        p_stw.save()
        pp_stw = self.c_doc.get(reverse('preop-passport', args=[p_stw.pk])).content.decode('utf-8')
        self.assertNotIn('შეჩერება 5 დღით ადრე', pp_stw)
        self.assertIn('გეგმური შეჩერების რეჟიმი დროებით გაუქმებულია', pp_stw)
        # Fuzz F8 & F9: birth_date clears stale invalid age & conference Case pediatric age not copied blindly
        p_age = _mk('evla')
        p_age.save()
        self.c_doc.post(reverse('preop-patient', args=[p_age.pk]), {'section': 'header', 'patient_name': p_age.patient_name, 'op_subtype': 'evla', 'side': 'R', 'age': '5', 'birth_date': '1970-01-01', 'sex': 'M'})
        p_age.refresh_from_db()
        self.assertEqual(p_age.age, 56)
        self.assertNotIn('age', p_age.studies.get('_invalid_inputs') or {})
        case_ped = Case.objects.create(patient_name='PedCaseR19', age=8, diagnosis='AAA', side='R', doctor=self.doc)
        self.c_doc.post(reverse('preop-new'), {'case': str(case_ped.pk)})
        p_ped = PreopPatient.objects.get(case=case_ped)
        self.assertIsNone(p_ped.age)

    def test_r19_s2_anamnesis_self_fill_postponement_and_plain_georgian_html(self):
        # Combos S2-8: generate_anamnesis never claims no chronic disease with pacemaker/BNP/riva2.5/FT diabetic meds
        for q_ov, s_ov in (
            ({'pacemaker': True}, {}),
            ({}, {'nt_probnp': 1000}),
            ({'med_chips': ['rivaroxaban_2_5']}, {}),
            ({'other_meds': 'Glucophage 1000 mg'}, {}),
            ({'other_meds': 'Jardiance 10 mg'}, {}),
            ({'other_meds': 'Lantus 20 U'}, {}),
        ):
            p_a = _mk('evla', q=q_ov, s=s_ov)
            txt_a = generate_anamnesis(p_a, evaluate(p_a, today=TODAY))
            self.assertNotIn('მნიშვნელოვანი ქრონიკული დაავადებების გარეშე', txt_a)
        # Clin S2-9: build_self_fill_highlights preserves drug class names in Georgian with zero mg leak
        hl = build_self_fill_highlights({'filled': True, 'other_meds': 'Brilinta 90mg, Xarelto 2.5 mg BID'})
        hl_txt = ' '.join(t for _, t in hl)
        self.assertNotIn('mg', hl_txt.lower())
        self.assertIn('ტიკაგრელორი', hl_txt)
        # Combos S3-1 & S3-2: postponement escalates unstructured drug & giant AAA >14d
        p_u = _mk('open', q={'other_meds': 'Xarelto 20 mg'}, s={'prev_planned_surgery_date': OP_DATE, 'planned_surgery_date': (TODAY + datetime.timedelta(days=15)).isoformat()})
        imp_u = detect_postponement_impacts(p_u, evaluate(p_u, today=TODAY), TODAY)['impacts']
        self.assertTrue(any(i['tone'] == 'danger' and 'უკუნაჩვენები ან დაუყოვნებლივ' in i['text'] for i in imp_u))
        p_g = _mk('open', s={'aaa_mm': 75, 'cta_date': TODAY.isoformat(), 'prev_planned_surgery_date': OP_DATE, 'planned_surgery_date': (TODAY + datetime.timedelta(days=25)).isoformat()})
        imp_g = detect_postponement_impacts(p_g, evaluate(p_g, today=TODAY), TODAY)['impacts']
        self.assertTrue(any(i['tone'] == 'danger' and 'გიგანტური' in i['text'] for i in imp_g))
        # Clin S2-6, S2-7, S2-8, S3-7: plain_ka fragments + ICU + week_new + patient_new file cat + #nw-hdr-op
        p_icu = _mk('open', s={'case_outcome_status': 'cancelled_logistics'})
        expl = build_patient_plain_explanation(p_icu, evaluate(p_icu, today=TODAY), TODAY)
        self.assertNotIn('ICU', expl['inactive_banner'])
        for raw, bad in (
            ('ტიკაგრელორი (EMA Brilique SmPC 4.3 / FDA 5.5 — სისხლდენის რისკი)', '5.5'),
            ('ტიკაგრელორი (EMA Brilique SmPC 4.3 / FDA 5.5 — სისხლდენის რისკი)', '(—'),
            ('[ღვიძლის ≥3x: მძიმე სისხლდენის რისკი — FDA PI 5.1!]', '—!'),
            ('(RE-ALIGN / ESC Class III — თრომბოზის რისკი)', '( /'),
            ('(ALI, Rutherford IIa/IIb)', ', )'),
            ('მიუთითეთ (R ან L) და ვიტ. K', '(R ან L)'),
        ):
            cleaned = scrub_plain_ka(raw, second_person=False, strip_quoted_ft=True)
            self.assertNotIn(bad, cleaned, f'{raw!r} -> {cleaned!r}')
        self.c_nurse.post(reverse('preop-ui-mode'), {'mode': 'new'})
        wk_html = self.c_nurse.get(reverse('preop')).content.decode('utf-8')
        m_sel = re.search(r'<select id="nw-add-op"[^>]*>.*?</select>', wk_html, re.S)
        self.assertIsNotNone(m_sel)
        for acro in ('EVAR', 'CEA', 'CLTI', 'EVLA'):
            self.assertNotIn(acro, m_sel.group(0))
        p_nw = _mk('evla')
        p_nw.save()
        pn_html = self.c_nurse.get(reverse('preop-patient', args=[p_nw.pk])).content.decode('utf-8')
        m_cat = re.search(r'<select id="nw-file-cat"[^>]*>.*?</select>', pn_html, re.S)
        self.assertIsNotNone(m_cat)
        self.assertNotIn('ABI', m_cat.group(0))
        m_hop = re.search(r'<select id="nw-hdr-op"[^>]*>.*?</select>', pn_html, re.S)
        self.assertIsNotNone(m_hop)
        for acro in ('EVAR', 'CEA', 'CLTI', 'EVLA'):
            self.assertNotIn(acro, m_hop.group(0))

    def test_r19_s3_registry_mutations_and_html_step_any(self):
        self.assertEqual(REGISTRY_VERSION, '1.2.24')
        # Clin S2-5: mutated cea_asymp_futility_age attributes to live ANES_AIRWAY_STOPBANG_ICU
        orig_sb = RULES_BY_ID['ANES_AIRWAY_STOPBANG_ICU']
        try:
            RULES_BY_ID['ANES_AIRWAY_STOPBANG_ICU'] = replace(orig_sb, value={**orig_sb.value, 'cea_asymp_futility_age': 76})
            ev_fut = evaluate(_mk('cea_asymp', age=77, q={'on_dialysis': True}, s={'carotid_r': 75, 'asa_class': 'IV'}), today=TODAY)
            hit = next(f for f in ev_fut['red_flags'] if '≥76' in f)
            self.assertIs(match_rule_for_text(hit), RULES_BY_ID['ANES_AIRWAY_STOPBANG_ICU'])
        finally:
            RULES_BY_ID['ANES_AIRWAY_STOPBANG_ICU'] = orig_sb
        # Clin S3-1, S3-3, S3-4, S3-6: dynamic triple_min, dabi range, GLP-1 hours, CEA >days
        orig_tr = RULES_BY_ID['MED_ANTITHROMBOTIC_CONFLICTS']
        try:
            RULES_BY_ID['MED_ANTITHROMBOTIC_CONFLICTS'] = replace(orig_tr, value=4)
            ev_t4 = evaluate(_mk('open', q={'med_warfarin': True, 'med_aspirin': True, 'med_clopidogrel': True, 'med_chips': ['cilostazol']}), today=TODAY)
            self.assertTrue(any('≥4 ანტითრომბოზულ' in f for f in ev_t4['red_flags']))
        finally:
            RULES_BY_ID['MED_ANTITHROMBOTIC_CONFLICTS'] = orig_tr
        orig_glp = RULES_BY_ID['MED_GLP1_FASTING_PROTOCOL']
        try:
            RULES_BY_ID['MED_GLP1_FASTING_PROTOCOL'] = replace(orig_glp, value={**orig_glp.value, 'liquid_hours': 36})
            ev_g36 = evaluate(_mk('open', q={'glp1': True, 'dm': False}), today=TODAY)
            self.assertTrue(any('36 სთ თხევადი დიეტა' in a for a in ev_g36['amber_flags']))
        finally:
            RULES_BY_ID['MED_GLP1_FASTING_PROTOCOL'] = orig_glp
        # Fuzz F11: step="any" on tp_r, tp_l, spiro_fev1
        p_pad = _mk('bypass_clti', q={'pulm': True}, s={'abi_r': 1.55, 'spo2': 92})
        p_pad.save()
        h1 = self.c_doc.get(reverse('preop-patient', args=[p_pad.pk]) + '?tab=s1').content.decode('utf-8')
        h2 = self.c_doc.get(reverse('preop-patient', args=[p_pad.pk]) + '?tab=s2').content.decode('utf-8')
        self.assertIn('step="any" name="tp_r"', h1)
        self.assertIn('step="any" name="tp_l"', h1)
        self.assertIn('step="any" name="spiro_fev1"', h2)
