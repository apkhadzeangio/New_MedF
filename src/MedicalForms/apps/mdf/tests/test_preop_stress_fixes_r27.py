"""Regression tests for Stage-4 Round-27 (v1.2.26) NLP, rAAA DoS, emergency β-hCG, mech-valve Board, Caprini & Plain-KA fixes."""
import datetime
import time

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from apps.mdf.models import BoardEntry, BoardPatient, PreopPatient, StaffProfile
from apps.mdf.preop import evaluate
from apps.mdf.preop.meds_freetext import (
    _detect_freetext_allergies,
    _detect_freetext_high_risk_meds,
    _has_unrecognized_allergy_clause,
    _positive_clauses_text,
)
from apps.mdf.preop.plain_ka import scrub_plain_ka
from apps.mdf.preop.registry import REGISTRY_VERSION, match_rule_for_text
from apps.mdf.preop.views.board import _build_handoff_tasks
from apps.mdf.preop.views.parsers import preop_admission_block_reason

TODAY = datetime.date(2026, 11, 28)
OP_DATE = (TODAY + datetime.timedelta(days=10)).isoformat()


def _mk(subtype='open', *, age=66, sex='M', side='', q=None, s=None):
    base_q = {'filled': True, 'mets_ok': True, 'allergy_iodine_asked': True}
    if subtype == 'evla':
        base_q.setdefault('ven_ceap', 'C2')
        side = side or 'R'
    elif subtype.startswith(('cea', 'bypass', 'pta', 'phleb')):
        side = side or 'R'
    base_s = {
        'bp_sys': 126, 'bp_dia': 76, 'hr': 72, 'spo2': 98,
        'weight_kg': 76, 'height_cm': 174, 'hb': 136, 'wbc': 6.8, 'plt': 230,
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
    return PreopPatient(
        patient_name='Test R27', age=age, sex=sex, side=side,
        pathway=pw_map[subtype], op_subtype=subtype, questionnaire=base_q, studies=base_s,
        started_on=TODAY, visit_date=TODAY,
    )


class PreopStressFixesRound27Tests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.doc = User.objects.create_user('r27_doc', password='pw', is_staff=True, is_superuser=True)
        StaffProfile.objects.create(user=self.doc, display_name='ტესტ R27 ქირურგი', full_name='ტესტ R27 ქირურგი')
        self.nurse = User.objects.create_user('r27_nurse', password='pw')
        StaffProfile.objects.create(user=self.nurse, display_name='ტესტ R27 ექთანი', full_name='ტესტ R27 ექთანი', preop_ui_mode='new')
        self.c_doc = Client()
        self.c_doc.force_login(self.doc)
        self.c_nurse = Client()
        self.c_nurse.force_login(self.nurse)

    def test_r27_nlp_inline_predicates_subject_negations_and_allergy_orphan(self):
        self.assertEqual(REGISTRY_VERSION, '1.2.27')
        # Fuzz A-S1-1 & Clin CA-S1-2: negated English/Georgian predicates & subject-prefixed negations
        for txt in (
            'stopped taking warfarin',
            'discontinued taking clopidogrel',
            'held taking warfarin',
            'no longer taking warfarin',
            'no longer on apixaban',
            'denies taking warfarin',
            'has not been taking warfarin',
            'not regularly taking aspirin',
            'not actively on apixaban',
            'warfarin not taken',
            'პაციენტი არ იღებს ვარფარინს',
            'პაციენტმა შეწყვიტა ვარფარინი',
            'პაციენტს მოუხსნეს ვარფარინი',
            'პაციენტი არ იღებს ვარფარინს და ელიქვისს',
            'patient stopped warfarin',
            'he discontinued apixaban',
            'patient denies taking warfarin',
            'patient stopped warfarin and clopidogrel',
        ):
            self.assertEqual(_detect_freetext_high_risk_meds(txt, q={})['matched_keys'], set(), txt)
        # Positive controls stay active
        self.assertEqual(_detect_freetext_high_risk_meds('warfarin stopped takes aspirin', q={})['matched_keys'], {'aspirin'})
        self.assertEqual(_detect_freetext_high_risk_meds('takes aspirin no warfarin', q={})['matched_keys'], {'aspirin'})
        self.assertEqual(_detect_freetext_high_risk_meds('პაციენტი არ იღებს ვარფარინს სვამს ასპირინს', q={})['matched_keys'], {'aspirin'})

        # Negated allergies + Fuzz A-S2-2 leading Georgian 'ალერგია <X> არ აქვს'
        for txt in (
            'denies being allergic to heparin',
            'not actually allergic to heparin',
            'heparin allergy not reported',
            'not known to be allergic to iodine',
            'penicillin allergy not present',
            'patient is not allergic to heparin',
            'patient is not allergic to aspirin or ibuprofen',
            'patient denies allergy to penicillin and heparin',
            'პაციენტს არ აქვს ალერგია იოდზე და პენიცილინზე',
            'ალერგია პენიცილინზე არ აქვს',
            'ალერგია ჰეპარინზე არ აქვს',
            'ალერგია იოდზე არ მაქვს',
            'ალერგია წამლებზე არ აქვს',
        ):
            self.assertEqual(_detect_freetext_allergies(txt), set(), txt)
            self.assertFalse(_has_unrecognized_allergy_clause(txt), txt)
            self.assertEqual(_positive_clauses_text(txt), '', txt)
        self.assertEqual(_detect_freetext_allergies('იოდზე ალერგია პენიცილინზე არ აქვს'), {'iodine'})

    def test_r27_nlp_double_neg_passive_except_dash_paren_and_riva_pack(self):
        # Fuzz A-S1-2: English passive/infinitive/progressive/wouldn't & KA compact/VSO double-negation
        for txt, want in (
            ('clopidogrel cannot be stopped', {'clopidogrel'}),
            ("apixaban can't be discontinued", {'doac'}),
            ('advised not to stop clopidogrel', {'clopidogrel'}),
            ('patient is not stopping warfarin', {'warfarin'}),
            ("wouldn't stop warfarin", {'warfarin'}),
            ('not discontinuing aspirin', {'aspirin'}),
            ('ვარფარინი არა აქვს მოხსნილი', {'warfarin'}),
            ('ვარფარინი შეწყვეტილი არაქვს', {'warfarin'}),
            ('კლოპიდოგრელი მოხსნილი არმაქვს', {'clopidogrel'}),
            ('ვარფარინი არ ჰქონია მოხსნილი', {'warfarin'}),
        ):
            self.assertEqual(_detect_freetext_high_risk_meds(txt, q={})['matched_keys'], want, txt)

        # Fuzz A-S1-3: Mid-sentence passive & fronted Except/გარდა
        for txt, want in (
            ('All meds except warfarin were stopped', {'warfarin'}),
            ('All blood thinners except aspirin stopped', {'aspirin'}),
            ('All anticoagulants, except warfarin, are stopped', {'warfarin'}),
            ('Except warfarin, continues aspirin', {'aspirin'}),
            ('Except warfarin, off all meds', {'warfarin'}),
            ('ყველა წამალი გარდა ვარფარინისა მოხსნილია', {'warfarin'}),
            ('ყველა წამალი, გარდა ვარფარინისა, მოხსნილია', {'warfarin'}),
            ('ასპირინის გარდა ვარფარინი მოხსნილია', {'aspirin'}),
        ):
            self.assertEqual(_detect_freetext_high_risk_meds(txt, q={})['matched_keys'], want, txt)

        # Fuzz A-S2-1: Dash & parenthetical stop/negation annotations
        for txt, want in (
            ('ვარფარინი - მოხსნილია', set()),
            ('ვარფარინი — მოხსნილია', set()),
            ('ვარფარინი 5მგ (მოხსნილია); ასპირინი 100მგ', {'aspirin'}),
            ('ვარფარინი (5მგ) მოხსნილია; ასპირინი 100მგ', {'aspirin'}),
            ('warfarin 5mg (stopped), aspirin 100mg', {'aspirin'}),
            ('warfarin - stopped', set()),
            ('clopidogrel — discontinued', set()),
            ('ასპირინი 100მგ (ვარფარინი მოხსნილია)', {'aspirin'}),
            ('ასპირინი 100მგ - არ იღებს ვარფარინს', {'aspirin'}),
        ):
            self.assertEqual(_detect_freetext_high_risk_meds(txt, q={})['matched_keys'], want, txt)
        for txt in ('ჰეპარინი - არა', 'იოდი - არ აქვს', 'პენიცილინი — ალერგია არ აქვს'):
            self.assertEqual(_detect_freetext_allergies(txt), set(), txt)

        # Fuzz A-S2-3: Pack counts (№20/#20) & KA duration suffixes do not trigger false Dual-Xa
        for txt in (
            'ქსარელტო 2.5მგ №20',
            'ქსარელტო 2.5მგ #20',
            'xarelto 2.5mg #20',
            'ქსარელტო 2.5მგ 10 კვირაა',
            'ქსარელტო 2.5მგ 15 თვეა',
            'ქსარელტო 2.5მგ 10 წელია',
            'ქსარელტო 2.5მგ 20 წლიდან',
        ):
            ft = _detect_freetext_high_risk_meds(txt, q={'med_chips': ['rivaroxaban_2_5']})
            self.assertFalse(ft['has_ft_dual_xa'], txt)
            self.assertTrue(ft['has_ft_riva_low'], txt)

        # Strict O(N) guard on 50k chars with dashes/parens/subjects
        big = ('პაციენტმა შეწყვიტა ვარფარინი (5მგ) - მოხსნილია; ' * 1000)
        t0 = time.monotonic()
        res = _detect_freetext_high_risk_meds(big, q={})
        self.assertLess(time.monotonic() - t0, 1.5)
        self.assertEqual(res['matched_keys'], set())

    def test_r27_raaa_dos_glucose_oob_and_emergency_repro_female_bhcg(self):
        # Combos #1 & Clin CA-S1-1: rAAA arrival DoS glucose / shock BP / extreme INR never deadlock emergency OR transfer
        for s_ov in (
            {'dos_bp_sys': 80, 'dos_bp_dia': 50, 'dos_glucose': 16.0},
            {'dos_bp_sys': 80, 'dos_bp_dia': 50, 'dos_glucose': 3.2},
            {'dos_bp_sys': 35, 'dos_bp_dia': 20},
            {'dos_bp_sys': 80, 'dos_bp_dia': 50, 'dos_inr': 16.5},
        ):
            p = _mk('open', q={'aaa_pain': True, 'med_warfarin': True}, s={'aaa_mm': 76, 'bp_sys': 82, 'bp_dia': 50, 'hr': 115, 'inr': 2.4, **s_ov})
            p.surgeon = self.doc
            p.save()
            ev = evaluate(p, today=TODAY)
            self.assertTrue(ev['is_rupture_suspect'])
            self.assertFalse(ev['dos_hold_danger'])
            self.assertEqual(ev['dos_warnings'], [])
            self.assertEqual(preop_admission_block_reason(ev, ev['s']), '')
            for rf in ev['red_flags']:
                self.assertIsNotNone(match_rule_for_text(rf), rf)
            self.c_doc.post(reverse('preop-patient', args=[p.pk]), {'section': 'signoff', 'action': 'approve'})
            p.refresh_from_db()
            self.assertIsNotNone(p.approved_at)

        # Combos #2: Reproductive-age females in emergency/urgent vascular scenarios are never hard-blocked on pregnant_possible=False
        for sub, age, side, q_ex, s_ex in (
            ('evar', 45, '', {'aaa_pain': True}, {'aaa_mm': 76, 'bp_sys': 84, 'bp_dia': 52, 'hr': 114, 'evar_sized': True}),
            ('tbd', 45, '', {'aaa_pain': True}, {'aaa_mm': 76, 'bp_sys': 84, 'bp_dia': 52, 'hr': 114}),
            ('pta_clti', 35, 'R', {'abf_duration': '< 2 კვირა'}, {'abi_r': 0.3, 'pta_access_site': 'cfa_clean', 'dapt_ready': True}),
            ('pta_clti', 35, 'R', {}, {'abi_r': 0.3, 'pta_access_site': 'cfa_clean', 'dapt_ready': True}),
        ):
            p_f = _mk(sub, age=age, sex='F', side=side, q=q_ex, s=s_ex)
            p_f.surgeon = self.doc
            p_f.save()
            ev_f = evaluate(p_f, today=TODAY)
            self.assertFalse(any(u.get('blocking') and u['key'] == 'reproductive_pregnancy_unverified' for u in ev_f['unknown_state_flags']))
            self.c_doc.post(reverse('preop-patient', args=[p_f.pk]), {'section': 'signoff', 'action': 'approve'})
            p_f.refresh_from_db()
            self.assertIsNotNone(p_f.approved_at)
        # Control: elective 35F EVAR without β-hCG remains hard-blocked
        ev_elec = evaluate(_mk('evar', age=35, sex='F', s={'aaa_mm': 55, 'evar_sized': True}), today=TODAY)
        self.assertTrue(any(u.get('blocking') and u['key'] == 'reproductive_pregnancy_unverified' for u in ev_elec['unknown_state_flags']))

    def test_r27_mech_valve_board_caprini_and_plain_ka(self):
        # Combos #3 & Clin CA-S2-1: oac_count export + mechanical valve VKA/bridging mandate across all Board branches
        p_mv_dual = _mk('bypass_clti', q={'cad_chips': ['mech_valve'], 'med_warfarin': True, 'med_chips': ['rivaroxaban_2_5']}, s={'inr': 2.5, 'abi_r': 0.35, 'pad_runoff_status': 'popliteal_patent', 'pad_gsv_status': 'adequate'})
        ev_mv = evaluate(p_mv_dual, today=TODAY)
        self.assertEqual(ev_mv['oac_count'], 2)
        vte_mv = [t for _, t in _build_handoff_tasks(ev_mv, ev_mv['q'], ev_mv['s'], ev_mv['pathway'], set(ev_mv['eff_allergy_chips']), ev_mv['eff_renal']) if 'VTE / ანტიკოაგულაცია:' in t][0]
        self.assertIn('მექანიკური სარქველი', vte_mv)
        self.assertIn('VKA', vte_mv)
        self.assertIn('უკუნაჩვენები OAC არ განაახლოთ', vte_mv)

        # Live HTTP ICU Board handoff for rAAA + mech_valve + med_doac
        p_raaa_mv = _mk('open', q={'aaa_pain': True, 'cad_chips': ['mech_valve'], 'med_doac': True}, s={'aaa_mm': 80, 'bp_sys': 82, 'bp_dia': 50, 'hr': 112, 'med_hold_agreed': True})
        p_raaa_mv.surgeon = self.doc
        p_raaa_mv.save()
        self.c_doc.post(reverse('preop-admit-board', args=[p_raaa_mv.pk]), {'unit': 'icu', 'bed': 'ICU-R27'})
        p_raaa_mv.refresh_from_db()
        bp = BoardPatient.objects.get(pk=p_raaa_mv.studies['board_patient_id'])
        vte_live = [t for t in BoardEntry.objects.filter(patient=bp, kind=BoardEntry.TASK).values_list('text', flat=True) if t.startswith('VTE')][0]
        self.assertIn('მექანიკური სარქველი', vte_live)
        self.assertIn('VKA', vte_live)

        # EVLA + mech_valve + warfarin + enoxaparin & Pregnant + mech_valve + HIT + warfarin
        ev_3b = evaluate(_mk('evla', q={'cad_chips': ['mech_valve'], 'med_warfarin': True, 'other_meds': 'კლექსანი 0.4 მლ'}, s={'inr': 2.5}), today=TODAY)
        vte_3b = [t for _, t in _build_handoff_tasks(ev_3b, ev_3b['q'], ev_3b['s'], ev_3b['pathway'], set(ev_3b['eff_allergy_chips']), ev_3b['eff_renal']) if t.startswith('VTE')][0]
        self.assertIn('მექანიკური სარქველი', vte_3b)
        self.assertIn('VKA', vte_3b)
        self.assertNotIn('უკუნაჩვენები OAC არ განაახლოთ', vte_3b)

        ev_3c = evaluate(_mk('open', age=32, sex='F', q={'pregnant_possible': True, 'cad_chips': ['mech_valve'], 'allergy_chips': ['hit_heparin'], 'med_warfarin': True}, s={'inr': 2.5}), today=TODAY)
        vte_3c = [t for _, t in _build_handoff_tasks(ev_3c, ev_3c['q'], ev_3c['s'], ev_3c['pathway'], set(ev_3c['eff_allergy_chips']), ev_3c['eff_renal']) if t.startswith('HIT')][0]
        self.assertNotIn('უკუნაჩვენები OAC არ განაახლოთ', vte_3c)

        # Combos #4 & #5 + Clin CA-S2-2: Caprini warfarin-only severe_renal/liver & ft dabigatran+xarelto 2.5mg
        ev_4a = evaluate(_mk('evla', q={'med_warfarin': True}, s={'creat': 340, 'inr': 3.5}), today=TODAY)
        self.assertNotIn('უკუნაჩვენები OAC არ განაახლოთ', ev_4a['caprini_advice'])
        self.assertIn('ვარფარინის (VKA)', ev_4a['caprini_advice'])

        ev_4c = evaluate(_mk('evla', q={'med_warfarin': True}, s={'creat': 340, 'liver_status': '3x', 'inr': 3.5}), today=TODAY)
        self.assertIn('ვარფარინის (VKA)', ev_4c['caprini_advice'])

        ev_5a = evaluate(_mk('bypass_clti', q={'other_meds': 'xarelto 2.5mg, dabigatran 150mg'}, s={'liver_status': '3x', 'abi_r': 0.35}), today=TODAY)
        self.assertNotIn('ვარფარინი', ev_5a['caprini_advice'])
        self.assertIn('DOAC აკრძალულია', ev_5a['caprini_advice'])

        ev_5b = evaluate(_mk('evla', q={'med_warfarin': True, 'other_meds': 'ქსარელტო 2.5მგ'}, s={'liver_status': '3x', 'inr': 2.3}), today=TODAY)
        self.assertIn('DOAC აკრძალულია', ev_5b['caprini_advice'])

        # Clin CA-S3-1: Plain-KA scrubs '(ESC 2018 / FDA Category D)' completely (no '(2018 / D)' or '(D)')
        raw = 'ორსულობის დროს ამიოდარონი იწვევს ნაყოფის თირეოიდულ ტოქსიკურობას/ჰიპოთირეოზს და ბრადიკარდიას (ESC 2018 / FDA Category D) — თვითნებურად არ შეწყვიტოთ!'
        cleaned = scrub_plain_ka(raw, second_person=False, strip_quoted_ft=True)
        self.assertNotIn('(2018 / D)', cleaned)
        self.assertNotIn('(D)', cleaned)
        p_amio = _mk('pta_clti', age=30, sex='F', side='R', q={'pregnant_possible': True, 'med_chips': ['amiodarone']}, s={'abi_r': 0.35, 'pta_access_site': 'cfa_clean'})
        p_amio.save()
        html = self.c_nurse.get(reverse('preop-patient', args=[p_amio.pk])).content.decode('utf-8')
        self.assertNotIn('(2018 / D)', html)
        self.assertNotIn('(D)', html)
