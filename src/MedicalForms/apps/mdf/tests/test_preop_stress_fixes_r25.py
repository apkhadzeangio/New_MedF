"""Regression tests for Stage-4 Round-25 (v1.2.24) NLP, O(N) riva, rAAA workflow/locks, EVLA INR, DOAC+parenteral, Caprini & anamnesis registry fixes."""
import datetime
import time
from dataclasses import replace

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from ..models import PreopPatient, StaffProfile
from ..preop import build_med_schedule, evaluate, generate_anamnesis
from ..preop.meds_freetext import (
    _detect_freetext_allergies,
    _detect_freetext_high_risk_meds,
    _riva_has_therapeutic_dose,
)
from ..preop.nurse_ui import build_nurse_handover_queues
from ..preop.registry import REGISTRY_VERSION, RULES_BY_ID, annotate_texts
from ..preop.views.board import _build_handoff_tasks

User = get_user_model()
TODAY = datetime.date(2026, 11, 28)


def _mk(sub='open', age=68, sex='M', side='', q=None, s=None, archived=False, save=False):
    pw = PreopPatient.SUBTYPE_PATHWAY[sub]
    qd = {'filled': True}
    if q:
        qd.update(q)
    sd = {
        'labs_date': TODAY.isoformat(), 'creat': 85, 'creat_unit': 'umol',
        'weight_kg': 80, 'height_cm': 175, 'hb': 135, 'plt': 220, 'inr': 1.0,
        'bp_sys': 130, 'bp_dia': 78, 'hr': 72, 'spo2': 98,
    }
    if s:
        sd.update(s)
    p = PreopPatient(
        patient_name='სინთეზური R25', age=age, sex=sex, side=side,
        pathway=pw, op_subtype=sub, questionnaire=qd, studies=sd,
        visit_date=TODAY, archived=archived,
    )
    if save:
        p.save()
    return p


class PreopStressFixesRound25Tests(TestCase):
    def setUp(self):
        self.doc = User.objects.create_user(username='doc_r25', password='x', is_staff=True)
        StaffProfile.objects.create(user=self.doc, display_name='ექიმი R25', full_name='ექიმი R25')
        self.nurse = User.objects.create_user(username='nurse_r25', password='x', is_staff=False)
        self.c_doc = Client()
        self.c_doc.force_login(self.doc)
        self.c_nurse = Client()
        self.c_nurse.force_login(self.nurse)

    def test_r25_nlp_boundaries_pronouns_conjunctions_adverbs_and_on_riva(self):
        self.assertEqual(REGISTRY_VERSION, '1.2.27')
        self.assertEqual(len(RULES_BY_ID), 83)
        # Fuzz A-S1-1 & Clin A2: sentence-boundary Except/garda + negative pronouns (nothing/არაფერი)
        for txt, exp in (
            ('All blood thinners stopped. Except warfarin.', {'warfarin'}),
            ('Stopped all meds; except for apixaban 5mg.', {'doac'}),
            ('ყველა წამალი მოხსნილია. ვარფარინის გარდა.', {'warfarin'}),
            ('ყველა წამალი მოხსნილია. გარდა ვარფარინისა.', {'warfarin'}),
            ('Takes all meds. Except warfarin.', set()),
            ('ყველა წამალს იღებს. ვარფარინის გარდა.', set()),
            ('არაფერი, ვარფარინის გარდა', {'warfarin'}),
            ('ვარფარინის გარდა არაფერი', {'warfarin'}),
            ('არაფერს ვიღებ, ვარფარინის გარდა', {'warfarin'}),
            ('nothing except warfarin', {'warfarin'}),
            ('taking nothing except apixaban 5mg', {'doac'}),
            # Fuzz A-S1-2: unpunctuated fronted multi-item except/garda
            ('except warfarin and apixaban all meds stopped', {'warfarin', 'doac'}),
            ('გარდა ვარფარინისა და ელიქვისისა ყველა წამალი მოხსნილია', {'warfarin', 'doac'}),
            # Fuzz A-S2-2: comma-preceded prepositional garda after affirmative clause
            ('იღებს ასპირინს, გარდა ვარფარინისა', set()),
            # Fuzz A-S1-3: multi-drug affirmative conjunction + suffix negation / comma lists
            ('იღებს ასპირინს და ელიქვისს ვარფარინს არ სვამს', {'doac'}),
            ('taking aspirin, clopidogrel and apixaban warfarin stopped', {'clopidogrel', 'doac'}),
            ('იღებს ასპირინს, კლოპიდოგრელს და ელიქვისს ვარფარინი მოხსნილია', {'clopidogrel', 'doac'}),
            # Fuzz A-S1-4: /დღეში and /24hr slash doses
            ('მოხსნილია ვარფარინი 5მგ/დღეში ელიქვისი 5მგ', {'doac'}),
            ('stopped warfarin 5mg/24hr apixaban 5mg', {'doac'}),
            # Fuzz A-S2-1: temporal/frequency adverbs before stop verb
            ('ვარფარინი 5მგ გუშინ მოხსნილია', set()),
            ('ვარფარინი 5მგ ყოველდღე მოხსნილია', set()),
            ('warfarin 5mg yesterday stopped', set()),
            ('apixaban 5mg recently discontinued', set()),
        ):
            got = _detect_freetext_high_risk_meds(txt, q={'med_aspirin': True})['unstructured_keys']
            self.assertEqual(got, exp, f'med NLP mismatch for {txt!r}')

        for txt, exp in (
            ('No drug allergies. Except iodine.', {'iodine'}),
            ('ალერგია არ აქვს. იოდის გარდა.', {'iodine'}),
            ('ალერგია არ აქვს\nიოდის გარდა', {'iodine'}),
            ('გარდა იოდისა და ლატექსისა ალერგია არ აქვს', {'iodine', 'latex_chlorhex'}),
            ('არაფერი, იოდის გარდა', {'iodine'}),
            ('იოდის გარდა არაფერი', {'iodine'}),
            ('არაფერზე, იოდის გარდა', {'iodine'}),
            ('nothing except iodine', {'iodine'}),
            ('nothing except penicillin', {'beta_lactam'}),
        ):
            self.assertEqual(_detect_freetext_allergies(txt), exp, f'allergy NLP mismatch for {txt!r}')

        # Fuzz A-S2-3: strictly O(N) _riva_has_therapeutic_dose on 50k chars (< 100 ms)
        big = 'xarelto 2.5mg ' + ' '.join(f'med{i} 20mg' for i in range(4200))
        t0 = time.perf_counter()
        self.assertFalse(_riva_has_therapeutic_dose(big))
        self.assertLess((time.perf_counter() - t0) * 1000, 100.0)
        self.assertTrue(_riva_has_therapeutic_dose('xarelto 2.5mg then increased to 20mg'))
        self.assertTrue(_riva_has_therapeutic_dose('20mg xarelto'))
        self.assertFalse(_riva_has_therapeutic_dose('atorvastatin 20mg xarelto 2.5mg'))

    def test_r25_raaa_unblocked_emergency_workflow_date_locks_fasting_and_ft_acei(self):
        future_iso = (TODAY + datetime.timedelta(days=20)).isoformat()
        new_iso = (TODAY + datetime.timedelta(days=25)).isoformat()
        # Combos S1-1: rAAA on DOAC/warfarin/clopidogrel/enoxaparin can approve, admit to board, and record operated
        for q_extra in (
            {'med_doac': True},
            {'med_warfarin': True},
            {'med_clopidogrel': True},
            {'other_meds': 'enoxaparin 40mg'},
        ):
            p = _mk(
                'open', q={'aaa_pain': True, 'allergy_iodine_asked': True, **q_extra},
                s={'aaa_mm': 75, 'bp_sys': 85, 'bp_dia': 55, 'hr': 120, 'planned_surgery_date': future_iso},
                save=True,
            )
            self.c_doc.post(f'/preop/{p.pk}/', {'section': 'signoff', 'action': 'approve'})
            p.refresh_from_db()
            self.assertIsNotNone(p.approved_at, f'rAAA approve deadlocked for {q_extra}')
            self.c_doc.post(f'/preop/{p.pk}/admit-board/', {'unit': 'icu', 'bed': '1'})
            p.refresh_from_db()
            self.assertTrue(p.studies.get('board_patient_id'), f'rAAA board handoff deadlocked for {q_extra}')
            self.c_doc.post(f'/preop/{p.pk}/', {'section': 'dos_vqi', 'case_outcome_status': 'operated'})
            p.refresh_from_db()
            self.assertEqual(p.studies.get('case_outcome_status'), 'operated', f'rAAA operated deadlocked for {q_extra}')

        # Combos S2-1 & Fuzz A-S2-4: rAAA hides stale date in week_new/amber/passport & blocks all 4 reschedule endpoints
        p_lock = _mk(
            'open', q={'aaa_pain': True, 'glp1': True, 'other_meds': 'პრესტარიუმი 5მგ'},
            s={'aaa_mm': 72, 'bp_sys': 102, 'bp_dia': 62, 'hr': 110, 'planned_surgery_date': future_iso},
            save=True,
        )
        ev_l = evaluate(p_lock, today=TODAY)
        self.assertTrue(ev_l['is_rupture_suspect'])
        self.assertFalse(any('გიგანტური AAA' in a and 'დაგეგმილი ოპერაციის თარიღი' in a for a in ev_l['amber_flags']))
        queues = build_nurse_handover_queues([p_lock], {p_lock.pk: ev_l}, today=TODAY)
        self.assertIn('გადაუდებელი', queues['directory'][0]['op_label'])
        self.assertIsNone(queues['directory'][0]['op_date'])
        # Clin A5: FT ACEi/ARB emits red emergency shock hold
        ft_ace = next(r for r in ev_l['med_schedule'] if 'ACEi' in r['drug'])
        self.assertEqual(ft_ace['tone'], 'red')
        self.assertIn('შოკი', ft_ace['date_plan'])
        # Combos S2-2: dos_fasting_confirmed aligned with emergency RSI / STOP / inactive
        fast_chk = next(c for c in ev_l['dos_checks'] if c['key'] == 'dos_fasting_confirmed')
        self.assertIn('RSI', fast_chk['label'])
        self.assertNotIn('24 სთ სუფთა თხევადი დიეტა', fast_chk['label'])
        for sec in ('header', 'stage2', 'dos_vqi'):
            self.c_nurse.post(f'/preop/{p_lock.pk}/', {'section': sec, 'planned_surgery_date': new_iso})
            p_lock.refresh_from_db()
            self.assertEqual(p_lock.studies.get('planned_surgery_date'), future_iso, f'rAAA lock bypassed on {sec}')
        resp_mv = self.c_nurse.post(f'/preop/{p_lock.pk}/move/', {'slot': new_iso}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(resp_mv.status_code, 400)
        # STOP & inactive dos_fasting_confirmed alignment
        ev_stop = evaluate(_mk('evla', q={'glp1': True}, s={'ven_duplex_status': 'acute_dvt'}), today=TODAY)
        self.assertIn('STOP', next(c for c in ev_stop['dos_checks'] if c['key'] == 'dos_fasting_confirmed')['label'])
        ev_inact = evaluate(_mk('evla', q={'glp1': True}, s={'case_outcome_status': 'cancelled_medical'}), today=TODAY)
        self.assertIn('გაუქმებულია', next(c for c in ev_inact['dos_checks'] if c['key'] == 'dos_fasting_confirmed')['label'])

    def test_r25_evla_inr_doac_parenteral_pta_mv_oac_sapt_caprini_and_anamnesis(self):
        # Combos S1-2 & Clin A1: EVLA warfarin invalid baseline INR ('0.1','abc','25') consistent across all surfaces
        for bad_inr in ('0.1', 'abc', '-2', '25'):
            p_e = _mk('evla', q={'med_warfarin': True}, s={'inr': bad_inr})
            ev_e = evaluate(p_e, today=TODAY)
            self.assertFalse(ev_e['is_evla_warfarin_only'], f'is_evla_warfarin_only leaked for inr={bad_inr}')
            self.assertTrue(ev_e['has_contra_full_oac'], f'has_contra_full_oac missed for inr={bad_inr}')
            self.assertNotIn('გრძელდება მოუხსნელად', ev_e['caprini_advice'])
            bt = [t for _, t in _build_handoff_tasks(ev_e, ev_e['q'], ev_e['s'], ev_e['pathway'], set(ev_e['eff_allergy_chips']), ev_e['eff_renal'])]
            self.assertFalse(any('გრძელდება მოუხსნელად' in t for t in bt))
        for extra_s in ({'liver_status': '3x'}, {'anesthesia_plan': 'neuraxial'}):
            ev_e2 = evaluate(_mk('evla', q={'med_warfarin': True}, s={'inr': '0.1', **extra_s}), today=TODAY)
            w_row = next(r for r in ev_e2['med_schedule'] if r['drug'] == 'ვარფარინი')
            self.assertTrue(w_row.get('contraindicated'), f'contraindicated dropped for {extra_s}')

        # Combos S1-3: concurrent structured DOAC/dabigatran + active parenteral AC
        for sub in ('evla', 'open'):
            p_dp = _mk(sub, q={'med_doac': True, 'other_meds': 'enoxaparin 40mg'})
            ev_dp = evaluate(p_dp, today=TODAY)
            d_row = next(r for r in ev_dp['med_schedule'] if 'DOAC' in r['drug'])
            self.assertEqual(d_row['tone'], 'red')
            self.assertTrue(d_row.get('contraindicated'))
            self.assertNotIn('D+1 პროფილაქტ. LMWH', d_row['rule'])
            self.assertNotIn('მოუხსნელად', d_row['rule'])
            self.assertTrue(ev_dp['has_contra_full_oac'])
            bt_dp = ' '.join(t for _, t in _build_handoff_tasks(ev_dp, ev_dp['q'], ev_dp['s'], ev_dp['pathway'], set(ev_dp['eff_allergy_chips']), ev_dp['eff_renal']))
            self.assertNotIn('DOAC-ის აღდგენა', bt_dp)

        # Clin A3: PAD_PTA + clopidogrel_intol + mech_valve (warfarin unticked) never recommends ticagrelor
        ev_mv = evaluate(_mk('pta_clti', q={'allergy_chips': ['clopidogrel_intol'], 'cad_chips': ['mech_valve']}), today=TODAY)
        self.assertTrue(ev_mv['red_flags'])
        self.assertNotIn('(ტიკაგრელორი)', ev_mv['red_flags'][0])

        # Combos S2-3 & Clin A4: explicit oac_sapt + parenteral AC only preserves HIT/dialysis/liver caveats & attributes
        ev_os = evaluate(
            _mk('pta_clti', q={'on_dialysis': True, 'allergy_chips': ['hit_heparin'], 'other_meds': 'ენოქსაპარინი 40მგ'}, s={'post_antithrombotic_plan': 'oac_sapt'}),
            today=TODAY,
        )
        self.assertIn('პარენტერული ანტიკოაგულაცია', ev_os['post_antithrombotic_label'])
        self.assertIn('HIT', ev_os['post_antithrombotic_label'])
        self.assertTrue(all(x['rule'] for x in annotate_texts(ev_os['amber_flags'])))

        # Combos S2-4, S2-5, S2-6: argatroban non-contradiction, low-Caprini overrides, preg+HIT renal, preg+MV liver_3x
        p_pr = _mk(
            'pta_clti', age=30, sex='F',
            q={'ven_pregnant': True, 'on_dialysis': True, 'allergy_chips': ['hit_heparin'], 'other_meds': 'enoxaparin 40mg'},
            s={'liver_status': '3x'},
        )
        ev_pr = evaluate(p_pr, today=TODAY)
        self.assertNotIn('არგატრობანი −75%', ev_pr['post_antithrombotic_label'])
        self.assertNotIn('(ბივალირუდინი/არგატრობანი)', ev_pr['post_antithrombotic_label'])
        self.assertIn('ფონდაპარინუქსი აკრძალულია', ev_pr['caprini_advice'])
        bt_pr = ' '.join(t for _, t in _build_handoff_tasks(ev_pr, ev_pr['q'], ev_pr['s'], ev_pr['pathway'], set(ev_pr['eff_allergy_chips']), ev_pr['eff_renal']))
        self.assertNotIn('არგატრობანი −75%', bt_pr)

        ev_pmv = evaluate(
            _mk('pta_clti', age=30, sex='F', q={'ven_pregnant': True, 'on_dialysis': True, 'cad_chips': ['mech_valve'], 'allergy_chips': ['hit_heparin']}, s={'liver_status': '3x'}),
            today=TODAY,
        )
        self.assertIn('ღვიძლის ≥3x', ev_pmv['post_antithrombotic_label'])
        self.assertIn('ღვიძლის ≥3x', ev_pmv['caprini_advice'])

        ev_low = evaluate(_mk('pta_clti', age=28, sex='F', q={'ven_pregnant': True}, s={'weight_kg': 60, 'height_cm': 175}), today=TODAY)
        self.assertLessEqual(ev_low['caprini_score'], 2)
        self.assertNotIn('რუტინული ჰეპარინის გარეშე', ev_low['caprini_advice'])
        ev_low_oac = evaluate(_mk('pta_clti', age=28, sex='M', q={'other_meds': 'enoxaparin 40mg'}, s={'weight_kg': 60, 'height_cm': 175}), today=TODAY)
        self.assertLessEqual(ev_low_oac['caprini_score'], 2)
        self.assertNotIn('რუტინული ჰეპარინის გარეშე', ev_low_oac['caprini_advice'])
        ev_low_mv = evaluate(_mk('evla', age=28, sex='M', q={'cad_chips': ['mech_valve'], 'med_chips': ['warfarin']}, s={'weight_kg': 60, 'height_cm': 175, 'inr': 2.5}), today=TODAY)
        self.assertLessEqual(ev_low_mv['caprini_score'], 2)
        self.assertNotIn('რუტინული ჰეპარინის გარეშე', ev_low_mv['caprini_advice'])

        # Clin A6: anamnesis.py reacts to live RULES_BY_ID threshold mutations
        orig_hypo, orig_hb = RULES_BY_ID['VIT_BP_HYPOTENSION_SHOCK'], RULES_BY_ID['LAB_HB_PBM_THRESHOLDS']
        try:
            RULES_BY_ID['VIT_BP_HYPOTENSION_SHOCK'] = replace(orig_hypo, value={**orig_hypo.value, 'arm_diff': 15})
            RULES_BY_ID['LAB_HB_PBM_THRESHOLDS'] = replace(orig_hb, value={**orig_hb.value, 'hb_norm_m': 135})
            p_an = _mk('open', age=68, sex='M', s={'bp_sys': 92, 'bp_sys_l': 108, 'hb': 132})
            an_txt = generate_anamnesis(p_an, evaluate(p_an, today=TODAY))
            self.assertIn('ასიმეტრია', an_txt)
            self.assertIn('ანემია', an_txt)
        finally:
            RULES_BY_ID['VIT_BP_HYPOTENSION_SHOCK'] = orig_hypo
            RULES_BY_ID['LAB_HB_PBM_THRESHOLDS'] = orig_hb

        # Combos S3-1: evaluate() vs standalone build_med_schedule() parity on q_filled=False + med_chips=['ppi']
        p_ppi = _mk('evla', q={'filled': False, 'med_chips': ['ppi']})
        ev_ppi = evaluate(p_ppi, today=TODAY)
        st_ppi = build_med_schedule(p_ppi, p_ppi.questionnaire, p_ppi.studies, ev_ppi['egfr'], crcl=ev_ppi['crcl'], has_neuraxial_override=ev_ppi['has_neuraxial_plan'], today=TODAY)
        self.assertEqual([r['drug'] for r in ev_ppi['med_schedule']], [r['drug'] for r in st_ppi])
