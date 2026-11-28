"""Regression tests for Stage-4 Round-26 (v1.2.25) NLP, rAAA, EVLA, pregnancy/HIT, amiodarone, ARNI & parity fixes."""
import datetime
import time

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from apps.mdf.models import PreopPatient, StaffProfile
from apps.mdf.preop import build_med_schedule, evaluate
from apps.mdf.preop.meds_freetext import (
    _detect_freetext_allergies,
    _detect_freetext_high_risk_meds,
    _has_unrecognized_allergy_clause,
)
from apps.mdf.preop.nurse_plan import detect_postponement_impacts
from apps.mdf.preop.nurse_ui import build_nurse_handover_queues
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
        patient_name='Test R26', age=age, sex=sex, side=side,
        pathway=pw_map[subtype], op_subtype=subtype, questionnaire=base_q, studies=base_s,
        started_on=TODAY, visit_date=TODAY,
    )


class PreopStressFixesRound26Tests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.doc = User.objects.create_user('r26_doc', password='pw', is_staff=True, is_superuser=True)
        StaffProfile.objects.create(user=self.doc, display_name='ტესტ R26 ქირურგი', full_name='ტესტ R26 ქირურგი')
        self.c_doc = Client()
        self.c_doc.force_login(self.doc)

    def test_r26_nlp_double_neg_except_decimal_comma_2verb_neg_allergies_and_stems(self):
        self.assertEqual(REGISTRY_VERSION, '1.2.25')
        # Fuzz A-S1-1: Georgian SOV double negation & English modal double negation
        for txt, want in (
            ('ვარფარინი მოხსნილი არ არის', {'warfarin'}),
            ('ვარფარინი შეწყვეტილი არ აქვს', {'warfarin'}),
            ('ელიქვისი მოხსნილი არაა', {'doac'}),
            ('კლოპიდოგრელი შეწყვეტილი არ მაქვს', {'clopidogrel'}),
            ('პრადაქსა შეჩერებული არ არის', {'dabigatran'}),
            ('ქსარელტო არა არის მოხსნილი', {'doac'}),
            ("don't stop warfarin", {'warfarin'}),
            ('cannot stop warfarin', {'warfarin'}),
            ("can't stop clopidogrel", {'clopidogrel'}),
            ("won't stop apixaban", {'doac'}),
        ):
            self.assertEqual(_detect_freetext_high_risk_meds(txt, q={})['matched_keys'], want, txt)

        # Clin CA-S1-2: negated English allergy/switch & Georgian no-allergy phrases
        for txt in (
            'patient is not allergic to heparin',
            'never allergic to lidocaine',
            'not allergic to penicillin',
            'not allergic to iodine',
        ):
            self.assertEqual(_detect_freetext_allergies(txt), set(), txt)
        self.assertEqual(_detect_freetext_high_risk_meds('not switched to warfarin', q={})['matched_keys'], set())
        for txt in (
            'ალერგია არაფერზე არ მაქვს',
            'ალერგია არაფერზე არ აქვს',
            'არაფერზე არ აქვს ალერგია',
            'არცერთ წამალზე არ აქვს ალერგია',
        ):
            self.assertFalse(_has_unrecognized_allergy_clause(txt), txt)

        # Fuzz A-S1-2: Except/გარდა comma lists, newlines/semicolons, modifiers, leading & postpositional doses
        for txt, want in (
            ('Takes nothing. Except warfarin, apixaban.', {'warfarin', 'doac'}),
            ('არაფერს არ სვამს. გარდა ვარფარინისა, ელიქვისისა.', {'warfarin', 'doac'}),
            ('გარდა ასპირინისა, კლოპიდოგრელისა და ვარფარინისა არაფერს არ იღებს', {'aspirin', 'clopidogrel', 'warfarin'}),
            ('Takes nothing. Except warfarin for AFib.', {'warfarin'}),
            ('არაფერს არ სვამს. გარდა ვარფარინისა არითმიისთვის.', {'warfarin'}),
            ('გარდა 5მგ ვარფარინისა არაფერს არ სვამს', {'warfarin'}),
            ('Takes all meds.\nExcept warfarin 5mg.', set()),
            ('Takes all meds;Except warfarin 5mg.', set()),
            ('იღებს წამლებს.\nგარდა ვარფარინისა.', set()),
            ('იღებს ყველა წამალს\nგარდა ასპირინისა', set()),
            ('Takes all meds. Except warfarin, apixaban.', set()),
            ('Takes all meds. Except aspirin and warfarin 5mg.', set()),
            ('იღებს ყველა წამალს. გარდა ასპირინისა და ვარფარინისა 5მგ.', set()),
            ('იღებს ყველა წამალს ვარფარინის 5მგ გარდა', set()),
        ):
            self.assertEqual(_detect_freetext_high_risk_meds(txt, q={})['matched_keys'], want, txt)
        self.assertEqual(_detect_freetext_allergies('No allergies. Except iodine, penicillin.'), {'iodine', 'beta_lactam'})
        self.assertEqual(_detect_freetext_allergies('ალერგია არ აქვს. გარდა იოდისა, პენიცილინისა.'), {'iodine', 'beta_lactam'})
        self.assertEqual(_detect_freetext_allergies('Allergic to all antibiotics.\nExcept penicillin.'), set())

        # Fuzz A-S1-3: Decimal comma (2,5) stays intact across negation & VOYAGER detection
        for txt in ('ქსარელტო 2,5 მგ მოხსნილია', 'ვარფარინი 2,5 მგ არ იღებს', 'warfarin 2,5mg stopped'):
            self.assertEqual(_detect_freetext_high_risk_meds(txt, q={})['matched_keys'], set(), txt)
        ft_voy = _detect_freetext_high_risk_meds('ასპირინს არ სვამს; ქსარელტო 2,5 მგ', q={'med_chips': ['rivaroxaban_2_5']})
        self.assertTrue(ft_voy['has_ft_riva_low'])
        self.assertFalse(ft_voy['has_ft_unstruct_doac'])

        # Fuzz A-S1-4 & A-S2-3: 2-verb neg->pos with dose + duration words before stop verb
        self.assertEqual(_detect_freetext_high_risk_meds('წამალს არ სვამს ვარფარინს 5მგ იღებს', q={})['matched_keys'], {'warfarin'})
        self.assertEqual(_detect_freetext_high_risk_meds('იღებს ასპირინს და ელიქვისს ვარფარინი 5მგ 3 დღის წინ მოხსნილია', q={})['matched_keys'], {'aspirin', 'doac'})
        self.assertEqual(_detect_freetext_high_risk_meds('taking aspirin warfarin 5mg 3 days ago stopped', q={})['matched_keys'], {'aspirin'})

        # Fuzz A-S2-2: Rivaroxaban 2.5 mg with bare durations/counts/dates/times must NOT trigger dual-Xa-DOAC
        for txt in (
            'ქსარელტო 2.5მგ 10 დღეა',
            'xarelto 2.5mg for 10 days',
            'ქსარელტო 2.5მგ 20 აბი',
            'xarelto 2.5mg 15/10/2025',
            'ქსარელტო 2.5მგ x2 10:00',
        ):
            ft_r = _detect_freetext_high_risk_meds(txt, q={'med_chips': ['rivaroxaban_2_5']})
            self.assertTrue(ft_r['has_ft_riva_low'], txt)
            self.assertFalse(ft_r['has_ft_dual_xa'], txt)

        # Fuzz A-S2-4 & Clin CA-S2-3: Naproxen typo fix, hospital beta-lactam stems, and ARNI stems
        self.assertEqual(_detect_freetext_allergies('ნაპროქსენზე ალერგია'), {'nsaid'})
        for txt in ('ცეფტაზიდიმზე ალერგია', 'piperacillin allergy', 'imipenem allergy', 'ბიცილინზე ალერგია'):
            self.assertIn('beta_lactam', _detect_freetext_allergies(txt), txt)
        for arni in ('ენტრესტო 49/51', 'იუპერიო 100მგ', 'entresto 97/103', 'sacubitril 49mg'):
            self.assertTrue(_detect_freetext_high_risk_meds(arni, q={})['has_ft_acei_arb'], arni)

        # O(N) performance on 50k chars
        t0 = time.monotonic()
        _detect_freetext_high_risk_meds('გარდა ასპირინისა, ვარფარინისა და ელიქვისისა ' * 1200, q={})
        self.assertLess(time.monotonic() - t0, 1.0)

    def test_r26_raaa_dos_unblocked_slot_locks_and_no_elective_leaks(self):
        # Combos A-1: rAAA arrival DoS permissive hypotension (80/50) & warfarin DoS INR 2.6 do NOT deadlock emergency approval
        p_rup = _mk(
            'open',
            q={'aaa_pain': True, 'med_warfarin': True, 'med_chips': ['acei_arb']},
            s={
                'aaa_mm': 72, 'bp_sys': 84, 'bp_dia': 52, 'hr': 112,
                'dos_bp_sys': 80, 'dos_bp_dia': 50, 'dos_inr': 2.6,
                'planned_surgery_date': (TODAY + datetime.timedelta(days=5)).isoformat(),
                'prev_planned_surgery_date': (TODAY + datetime.timedelta(days=2)).isoformat(),
            },
        )
        p_rup.save()
        ev_r = evaluate(p_rup, today=TODAY)
        self.assertTrue(ev_r['is_rupture_suspect'])
        self.assertFalse(ev_r['dos_hold_danger'])
        self.assertEqual(ev_r['dos_warnings'], [])
        self.assertEqual(preop_admission_block_reason(ev_r, ev_r['s']), '')
        for flg in ev_r['red_flags'] + ev_r['amber_flags']:
            self.assertIsNotNone(match_rule_for_text(flg), flg)

        # Fuzz A-S2-1 & Combos A-6: rAAA move_ready=1 & header slot_kind='someday:' blocked; no stale date/queue/postpone leaks
        self.c_doc.post(reverse('preop-patient', args=[p_rup.pk]), {'section': 'signoff', 'action': 'approve', 'move_ready': '1'})
        p_rup.refresh_from_db()
        self.assertIsNotNone(p_rup.approved_at)
        self.assertEqual(p_rup.someday_list, '')
        self.assertEqual(p_rup.visit_date, TODAY)

        self.c_doc.post(reverse('preop-patient', args=[p_rup.pk]), {'section': 'header', 'slot_kind': 'someday:'})
        p_rup.refresh_from_db()
        self.assertEqual(p_rup.visit_date, TODAY)
        self.assertEqual(p_rup.someday_list, '')

        self.assertIsNone(detect_postponement_impacts(p_rup, ev_r, TODAY))
        queues = build_nurse_handover_queues([p_rup], {p_rup.pk: ev_r}, today=TODAY)
        self.assertEqual(queues['med_calls'], [])
        wk_html = self.c_doc.get(f'/preop/?week={TODAY.isoformat()}').content.decode('utf-8')
        self.assertNotIn(f'ოპ: {ev_r["planned_surgery_short"]}', wk_html)

    def test_r26_evla_vka_mech_valve_parenteral_dos_inr_preg_hit_amiodarone_and_parity(self):
        # Clin CA-S1-1 Case A: EVLA + mech_valve + warfarin + subther INR 1.3 must NOT tell board "do not resume OAC"
        p_mv = _mk('evla', q={'med_warfarin': True, 'cad_chips': ['mech_valve']}, s={'inr': 1.3})
        ev_mv = evaluate(p_mv, today=TODAY)
        bt_mv = ' '.join(t for _, t in _build_handoff_tasks(ev_mv, ev_mv['q'], ev_mv['s'], ev_mv['pathway'], set(ev_mv['eff_allergy_chips']), ev_mv['eff_renal']))
        self.assertNotIn('უკუნაჩვენები OAC არ განაახლოთ', bt_mv)
        self.assertTrue(any(r.get('contraindicated') for r in ev_mv['med_schedule']))

        # Combos A-2 & Clin CA-S1-1 Case B: EVLA + warfarin + liver_3x + INR 4.5 marks contraindicated & blocks approve
        p_liv = _mk('evla', q={'med_warfarin': True}, s={'inr': 4.5, 'liver_status': '3x'})
        p_liv.save()
        ev_liv = evaluate(p_liv, today=TODAY)
        vka_r = next(r for r in ev_liv['med_schedule'] if 'ვარფარინი' in r['drug'])
        self.assertTrue(vka_r.get('contraindicated'))
        self.assertNotIn('აღდგენა', vka_r['rule'])
        self.assertNotIn('DOAC აკრძალულია', ev_liv['caprini_advice'])
        self.c_doc.post(reverse('preop-patient', args=[p_liv.pk]), {'section': 'signoff', 'action': 'approve'})
        p_liv.refresh_from_db()
        self.assertIsNone(p_liv.approved_at)

        # Combos A-5: EVLA + warfarin + parenteral AC consistent across all surfaces
        p_ep = _mk('evla', q={'med_warfarin': True, 'other_meds': 'enoxaparin 40mg'}, s={'inr': 2.2})
        ev_ep = evaluate(p_ep, today=TODAY)
        self.assertTrue(ev_ep['has_contra_full_oac'])
        self.assertIn('უკუნაჩვენები', ev_ep['caprini_advice'])
        p_ep_ft = _mk('evla', q={'other_meds': 'warfarin 5mg, enoxaparin 40mg'}, s={'inr': 2.2})
        ev_ep_ft = evaluate(p_ep_ft, today=TODAY)
        self.assertTrue(ev_ep_ft['has_contra_full_oac'])
        self.assertTrue(all(r.get('contraindicated') for r in ev_ep_ft['med_schedule']))

        # Combos A-8 & Clin CA-S2-1: Invalid low INR 0.1 & FT warfarin missing INR trigger DoS STOP
        ev_low = evaluate(_mk('evla', q={'med_warfarin': True}, s={'inr': '0.1', 'dos_fasting_confirmed': True}), today=TODAY)
        self.assertTrue(ev_low['dos_hold_danger'])
        self.assertEqual(ev_low['clearance_seal']['code'], 'stop')
        ev_ftv = evaluate(_mk('open', q={'other_meds': 'ვარფარინი 5მგ'}, s={'inr': None, 'dos_fasting_confirmed': True}), today=TODAY)
        self.assertTrue(ev_ftv['dos_hold_danger'])

        # Combos A-3 & A-4: Pregnancy + HIT avoids argatroban -75% and avoids trimester VKA without mech_valve
        p_ph = _mk(
            'pta_clti', age=30, sex='F',
            q={'ven_pregnant': True, 'on_dialysis': True, 'allergy_chips': ['hit_heparin'], 'other_meds': 'enoxaparin 40mg, argatroban'},
            s={'liver_status': '3x'},
        )
        ev_ph = evaluate(p_ph, today=TODAY)
        all_ph = ' '.join(ev_ph['red_flags'] + [r['rule'] for r in ev_ph['med_schedule']])
        self.assertNotIn('75%-ით', all_ph)
        self.assertNotIn('−75%', all_ph)
        self.assertNotIn('ტრიმესტრით VKA', ev_ph['surgical_brief']['heparin_plan'])
        bt_ph = ' '.join(t for _, t in _build_handoff_tasks(ev_ph, ev_ph['q'], ev_ph['s'], ev_ph['pathway'], set(ev_ph['eff_allergy_chips']), ev_ph['eff_renal']))
        self.assertNotIn('ტრიმესტრით VKA', bt_ph)

        # Clin CA-S2-2: Structured & FT amiodarone in pregnancy emit red contraindicated warning
        ev_am = evaluate(_mk('evla', age=30, sex='F', q={'pregnant_possible': True, 'med_chips': ['amiodarone']}), today=TODAY)
        am_row = next(r for r in ev_am['med_schedule'] if 'ამიოდარონი' in r['drug'])
        self.assertEqual(am_row['tone'], 'red')
        self.assertTrue(am_row.get('contraindicated'))
        self.assertTrue(any('ამიოდარონი' in f for f in ev_am['red_flags']))

        # Combos A-7 & Clin CA-S3-1: 100% standalone build_med_schedule parity on q_filled=False across all PPI triggers
        for q_ov, s_ov in (
            ({'filled': False, 'other_meds': 'ომეპრაზოლი 20მგ'}, {}),
            ({'filled': False, 'ulcer': True}, {}),
            ({'filled': False, 'ulcer_active_symp': True}, {}),
            ({'filled': False}, {'gastro_result': 'acute_ulcer'}),
            ({'filled': False}, {'ppi_started': True}),
        ):
            p_p = _mk('evla', q=q_ov, s=s_ov)
            ev_p = evaluate(p_p, today=TODAY)
            st_p = build_med_schedule(p_p, p_p.questionnaire, p_p.studies, ev_p['egfr'], crcl=ev_p['crcl'], has_neuraxial_override=ev_p['has_neuraxial_plan'], today=TODAY)
            self.assertEqual([r['drug'] for r in ev_p['med_schedule']], [r['drug'] for r in st_p])
