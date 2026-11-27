"""Round-13 adversarial clinical stress-test regression suite (Registry v1.2.12)."""
import datetime
import json
import subprocess
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse

from ..models import BoardEntry, BoardPatient, PreopFile, PreopPatient, StaffProfile
from ..preop import (
    build_med_schedule,
    calc_caprini,
    evaluate,
    generate_anamnesis,
    make_self_fill_token,
)
from ..preop.meds_freetext import _detect_freetext_high_risk_meds
from ..preop.nurse_plan import build_patient_plain_explanation, build_plain_georgian_missing_items
from ..preop.registry import match_rule_for_text, rule_value
from ..preop.utils import has_prior_stroke_or_tia
from ..preop.views.board import admit_preop_to_board
from ..preop.views.parsers import preop_admission_block_reason
from ..preop.vignettes import VIGNETTES_BY_ID, evaluate_vignette

TODAY = datetime.date(2026, 11, 27)
OP_DATE = '2026-12-10'
BASE_LABS_DATE = '2026-11-25'


def _clean_patient(op_subtype='evla', age=64, sex='M', side='R', q=None, s=None):
    pw = PreopPatient.SUBTYPE_PATHWAY[op_subtype]
    q_d = {'filled': True, 'mets_ok': True, 'allergy_iodine_asked': True}
    if q:
        q_d.update(q)
    s_d = {
        'hb': 136, 'plt': 230, 'inr': 1.0, 'creat': 80, 'creat_unit': 'umol',
        'glucose': 5.2, 'k': 4.2, 'bp_sys': 126, 'bp_dia': 76, 'hr': 72, 'spo2': 98,
        'weight_kg': 76, 'height_cm': 172, 'ecg': 'normal', 'labs_date': BASE_LABS_DATE,
        'planned_surgery_date': OP_DATE,
    }
    if s:
        s_d.update(s)
    return PreopPatient(
        patient_name='სინთეზური R13 პაციენტი',
        age=age, sex=sex, side=side, pathway=pw, op_subtype=op_subtype,
        visit_date=TODAY, started_on=TODAY, questionnaire=q_d, studies=s_d,
    )


class Round13ClinicalSafetyTests(TestCase):
    """Covers Round-12 Clin + Combos S1/S2/S3 clinical logic, free-text & vignette fixes."""

    def test_active_gi_bleed_blocks_pharm_anticoag_across_caprini_venous_cea_and_board(self):
        # 1. Venous high & moderate Caprini + acute_ulcer / unremitted ulcer_active_symp
        for gastro_s, q_extra in (('acute_ulcer', {}), ('', {'ulcer_active_symp': True})):
            p = _clean_patient(
                'evla', age=76, sex='M', side='R',
                q={'ven_dvt_history': True, 'ven_thrombophilia': True, **q_extra},
                s={'gastro_result': gastro_s, 'ven_duplex_status': 'reflux_only', 'abi_r': 1.0},
            )
            ev = evaluate(p, today=TODAY)
            self.assertGreaterEqual(ev['caprini_score'], 5)
            for txt in (ev['caprini_advice'], ev['surgical_brief']['postop_antithrombotic']):
                self.assertIn('IPC', txt)
                self.assertIn('ენდოსკოპიურ ჰემოსტაზამდე', txt)
                self.assertNotIn('LMWH', txt)
                self.assertNotIn('DOAC', txt)
            vte_tasks = [t['label'] for t in ev['tasks'] if t['key'] == 'vte']
            self.assertTrue(vte_tasks and 'მხოლოდ IPC' in vte_tasks[0] and 'LMWH' not in vte_tasks[0])
            cap_amb = [a for a in ev['amber_flags'] if 'Caprini' in a]
            self.assertTrue(cap_amb and 'IPC' in cap_amb[0] and 'LMWH' not in cap_amb[0])

        # Moderate Caprini (3-4) directly via calc_caprini
        p_mod = _clean_patient('evla', age=50, q={'ulcer_active_symp': True}, s={'weight_kg': 70, 'height_cm': 170})
        pts, _, adv = calc_caprini(p_mod, p_mod.questionnaire, bmi=24.2, today=TODAY)
        self.assertIn(pts, (3, 4))
        self.assertIn('IPC', adv)
        self.assertNotIn('LMWH', adv)

        # 2. CEA + active GI bleed without baseline antiplatelet -> NO mandatory aspirin reflex
        p_cea = _clean_patient(
            'cea_symp', age=68, side='R',
            q={'med_chips': ['statin']},
            s={'carotid_r': 80, 'gastro_result': 'acute_ulcer', 'brain_imaging': 'normal_small'},
        )
        ev_cea = evaluate(p_cea, today=TODAY)
        self.assertNotIn('cea_antiplatelet', {r['key'] for r in ev_cea['reflex_orders']})
        self.assertFalse(any('ანტიაგრეგანტული თერაპია სავალდებულოა' in r for r in ev_cea['red_flags']))
        self.assertTrue(any('ენდოსკოპიურ ჰემოსტაზამდე' in a for a in ev_cea['amber_flags']))

        # 3. CEA rt-PA Day 2 (normal CT) + active GI bleed -> postop brief blocks aspirin
        p_rtpa = _clean_patient(
            'cea_symp', age=66, side='R',
            q={'cea_thrombolysis': True, 'cea_event_date': '2026-11-25', 'med_aspirin': True, 'med_chips': ['statin']},
            s={'carotid_r': 80, 'gastro_result': 'acute_ulcer', 'brain_imaging': 'normal_small'},
        )
        ev_rtpa = evaluate(p_rtpa, today=TODAY)
        self.assertIn('ენდოსკოპიურ ჰემოსტაზამდე', ev_rtpa['surgical_brief']['postop_antithrombotic'])
        self.assertNotIn('დასაშვებია მხოლოდ ასპირინი', ev_rtpa['surgical_brief']['postop_antithrombotic'])

        # 4. Active GI bleed + HIT on Board handoff -> mechanical IPC only (NO fondaparinux/argatroban!)
        p_hit = _clean_patient(
            'pta_clti', age=70, side='R',
            q={'allergy_chips': ['hit_heparin'], 'med_chips': ['statin']},
            s={'gastro_result': 'acute_ulcer', 'abi_r': 0.45, 'pta_lesion_site': 'femoropopliteal', 'pta_access_site': 'cfa_clean'},
        )
        p_hit.save()
        doc_u = get_user_model().objects.create_user(username='r13_clin_doc', password='pw')
        bp = admit_preop_to_board(p_hit, doc_u)[0]
        tasks_blob = ' | '.join(BoardEntry.objects.filter(patient=bp, kind=BoardEntry.TASK).values_list('text', flat=True))
        self.assertIn('მხოლოდ მექანიკური პროფილაქტიკა (IPC)', tasks_blob)
        self.assertNotIn('ფონდაპარინუქსი / არგატრობანი', tasks_blob)

    def test_freetext_negation_active_oac_statin_xarelto_and_amiodarone_evla_cap(self):
        # All 10 Clin S1-2 adversarial free-text strings
        cases = [
            ('ასპირინი მოხსნილია და ვარფარინი 5მგ', {'warfarin'}, False),
            ('არ იღებს ასპირინს და ქსარელტო 20მგ', {'doac'}, False),
            ('no aspirin and warfarin 5mg', {'warfarin'}, False),
            ('stopped clopidogrel and apixaban 5mg', {'doac'}, False),
            ('ვარფარინი 5მგ სისხლდენა არ აქვს', {'warfarin'}, False),
            ('xarelto 20mg not stopped', {'doac'}, False),
            ('eliquis 5mg without bleeding', {'doac'}, False),
            ('plavix 75mg no aspirin', {'clopidogrel'}, False),
            ('ატორვასტატინს არ ვსვამ', set(), False),
            ('არ ვსვამ ასპირინს და ვსვამ ვარფარინს', {'warfarin'}, False),
        ]
        for txt, expected_keys, expected_statin in cases:
            res = _detect_freetext_high_risk_meds(txt, q={})
            self.assertEqual(res['matched_keys'], expected_keys, f'Failed on: {txt}')
            self.assertEqual(res['has_ft_statin'], expected_statin, f'Statin failed on: {txt}')

        # All 7 EU/Georgian statin brands satisfy ctx.has_statin (zero missing-statin amber)
        for brand in ('liprimar 20mg', 'crestor 20mg', 'lipitor 40mg', 'ლიპიტორი 20მგ', 'როქსერა 20მგ', 'მერტენილი 10მგ', 'ატორისი 20მგ'):
            p_st = _clean_patient(
                'cea_asymp', age=68, side='R',
                q={'med_aspirin': True, 'other_meds': brand},
                s={'carotid_r': 75},
            )
            ev_st = evaluate(p_st, today=TODAY)
            self.assertFalse(any('სტატინ' in a for a in ev_st['amber_flags']), f'Missing-statin amber on {brand}')

        # Xarelto 2.5 mg + evening statin must NOT falsely escalate to therapeutic Xa-DOAC
        p_voy = _clean_patient(
            'bypass_clti', age=65, side='R',
            q={'med_aspirin': True, 'med_chips': ['rivaroxaban_2_5', 'statin'], 'other_meds': 'ქსარელტო 2.5 მგ + ატორვასტატინი 20 მგ საღამოს'},
            s={'abi_r': 0.50, 'pad_runoff_status': 'popliteal_patent', 'pad_gsv_status': 'adequate'},
        )
        ev_voy = evaluate(p_voy, today=TODAY)
        self.assertFalse(any('Xa-DOAC' in r for r in ev_voy['red_flags']))

        # Free-text amiodarone enforces 300 mg EVLA lidocaine cap (90 kg patient)
        p_amio = _clean_patient(
            'evla', age=55, side='R',
            q={'other_meds': 'კორდარონი 200 მგ'},
            s={'weight_kg': 90, 'ven_duplex_status': 'reflux_only'},
        )
        self.assertEqual(evaluate(p_amio, today=TODAY)['lidocaine_max_mg'], 300)

    def test_pregnancy_mech_valve_hit_and_severe_renal_ufh_warnings(self):
        # Pregnancy + mechanical valve + HIT on warfarin -> med_schedule mentions HIT + hematology
        p_pmh = _clean_patient(
            'pta_clti', age=30, sex='F', side='R',
            q={'pregnant_possible': True, 'cad_chips': ['mech_valve'], 'allergy_chips': ['hit_heparin'], 'med_warfarin': True},
            s={'abi_r': 0.45, 'inr': 2.5},
        )
        ev_pmh = evaluate(p_pmh, today=TODAY)
        vka_row = next(r for r in ev_pmh['med_schedule'] if 'ვარფარინი' in r['drug'])
        self.assertIn('HIT', vka_row['rule'])
        self.assertIn('ჰემატოლოგის', vka_row['rule'])
        self.assertIn('აკრძალულია', vka_row['date_plan'])

        # Pregnancy + CrCl < 30 / dialysis (no mech valve) on Warfarin & DOAC -> UFH renal warning
        for q_med, s_ren, q_dial in (
            ({'med_warfarin': True}, {'creat': 3.5, 'creat_unit': 'mgdl', 'weight_kg': 60}, False),
            ({'med_doac': True}, {'creat': 600, 'creat_unit': 'umol', 'weight_kg': 60}, True),
        ):
            p_pr = _clean_patient(
                'pta_clti', age=30, sex='F', side='R',
                q={'pregnant_possible': True, 'on_dialysis': q_dial, **q_med},
                s={'abi_r': 0.45, **s_ren},
            )
            ev_pr = evaluate(p_pr, today=TODAY)
            self.assertTrue(any('LMWH კუმულირდება' in r and 'UFH' in r for r in ev_pr['red_flags']))
            self.assertTrue(any('LMWH კუმულირდება' in r['rule'] for r in ev_pr['med_schedule']))
            self.assertIn('UFH', ev_pr['surgical_brief']['postop_antithrombotic'])

    def test_solo_neuraxial_with_asra_blockers_emits_asra_red_flag_and_nurse_alert(self):
        p_nx = _clean_patient(
            'open', age=68, sex='M',
            q={'allergy_chips': ['lidocaine']},
            s={'aaa_mm': 58, 'plt': 60, 'fibrinogen': 1.2, 'anesthesia_plan': 'neuraxial', 'anesth_lines_chips': ['epidural']},
        )
        ev_nx = evaluate(p_nx, today=TODAY)
        self.assertTrue(ev_nx['solo_neuraxial_anatomic_mismatch'])
        self.assertTrue(ev_nx['has_explicit_nx_blockers'])
        self.assertTrue(any('ASRA 5th Ed' in r for r in ev_nx['red_flags']))
        self.assertFalse(any('± ეპიდურული ანალგეზია ღია აორტაზე' in r for r in ev_nx['red_flags']))
        items = build_plain_georgian_missing_items(p_nx, ev_nx, TODAY)
        self.assertTrue(any('ASRA 5th Ed' in item['label'] for item in items))

    def test_registry_attribution_dynamic_messages_standalone_parity_and_vignettes(self):
        # 1. Explicit dapt/sapt + active GI bleed maps cleanly in rule registry
        for plan in ('dapt', 'sapt', 'voyager'):
            p_ep = _clean_patient(
                'bypass_clti', age=66, side='R',
                q={'med_aspirin': True, 'med_chips': ['statin']},
                s={'abi_r': 0.50, 'gastro_result': 'acute_ulcer', 'post_antithrombotic_plan': plan},
            )
            ev_ep = evaluate(p_ep, today=TODAY)
            gi_rfs = [r for r in ev_ep['red_flags'] if 'აქტიური კუჭ-ნაწლავის სისხლდენის' in r]
            self.assertTrue(gi_rfs, f'Expected GI red flag for {plan}')
            for rf in gi_rfs:
                self.assertIsNotNone(match_rule_for_text(rf), f'Unattributed red flag: {rf}')

        # 2. Dynamic Hb ambiguous range (22.1-34.9) & stale labs (>14 days)
        p_hb = _clean_patient('evla', s={'hb': 30, 'ven_duplex_status': 'reflux_only'})
        ev_hb = evaluate(p_hb, today=TODAY)
        self.assertTrue(any('22.1–34.9' in r for r in ev_hb['red_flags']))
        self.assertFalse(any('22.1–39.9' in r for r in ev_hb['red_flags']))
        self.assertTrue(any('22.1–34.9' in u['label'] for u in ev_hb['unknown_state_flags']))

        stale_days = rule_value('INF_STALE_LABS_DAYS')
        p_st = _clean_patient('open', s={'aaa_mm': 58, 'labs_date': '2026-11-01'})
        blk = preop_admission_block_reason(evaluate(p_st, today=TODAY), p_st.studies)
        self.assertIn(f'>{stale_days} დღე', blk)
        self.assertNotIn('>30 დღე', blk)

        # 3. has_prior_stroke_or_tia rejects future date & blank chips
        p_pr = _clean_patient('open', q={'med_prasugrel': True, 'cea_event_date': '2099-01-01', 'cea_event_chips': ['', '   ']}, s={'aaa_mm': 58})
        self.assertFalse(has_prior_stroke_or_tia(p_pr, today=TODAY))
        ev_pr = evaluate(p_pr, today=TODAY)
        self.assertFalse(any('FDA Boxed Warning' in r for r in ev_pr['red_flags']))

        # 4. Standalone build_med_schedule parity on phlebectomy auto + D0 dos_inr=1.8
        p_ph = _clean_patient('phlebectomy', side='R', q={'med_warfarin': True}, s={'inr': 1.0, 'dos_inr': 1.8, 'anesthesia_plan': 'auto', 'planned_surgery_date': TODAY.isoformat()})
        ev_ph = evaluate(p_ph, today=TODAY)
        st_rows = build_med_schedule(p_ph, ev_ph['q'], ev_ph['s'], ev_ph['egfr'], crcl=ev_ph['crcl'], today=TODAY)
        self.assertEqual(ev_ph['med_schedule'][0]['rule'], st_rows[0]['rule'])
        self.assertIn('INR ≤ 1.5', st_rows[0]['rule'])

        # 5. Mutation-proof V01 & V17 vignettes
        v01 = VIGNETTES_BY_ID['V01_AAA_FEMALE_50MM']
        self.assertTrue(evaluate_vignette(v01)['passed'])
        v01_male = v01.__class__(**{**v01.__dict__, 'sex': 'M'})
        self.assertFalse(evaluate_vignette(v01_male)['passed'])
        with mock.patch('apps.mdf.preop.rules.aaa.rule_value', side_effect=lambda k: 55 if k == 'AAA_FEMALE_MIN_MM' else rule_value(k)):
            self.assertFalse(evaluate_vignette(v01)['passed'])

        v17 = VIGNETTES_BY_ID['V17_PAD_NONCOMPRESSIBLE_ABI_TBI']
        self.assertTrue(evaluate_vignette(v17)['passed'])
        v17_mut = v17.__class__(**{**v17.__dict__, 's': tuple(('abi_r', 0.50) if k == 'abi_r' else (k, val) for k, val in v17.s)})
        self.assertFalse(evaluate_vignette(v17_mut)['passed'])

    def test_patient_plain_guide_scrubs_edge_jargon_and_negated_allergy_anamnesis(self):
        jargon_forbidden = ('CYP2C9', 'INR', '≥3x', 'DAPT', 'დე-ესკალაცი', 'მექ. სარქვ', 'კონსილიუმ')
        edge_patients = [
            _clean_patient('open', q={'med_warfarin': True, 'med_chips': ['amiodarone']}, s={'aaa_mm': 58, 'planned_surgery_date': ''}),
            _clean_patient('open', q={'med_chips': ['statin']}, s={'aaa_mm': 58, 'liver_status': '3x'}),
            _clean_patient('pta_clti', side='R', q={'med_aspirin': True, 'med_clopidogrel': True, 'med_warfarin': True}, s={'abi_r': 0.45, 'planned_surgery_date': ''}),
            _clean_patient('pta_clti', age=30, sex='F', side='R', q={'pregnant_possible': True, 'cad_chips': ['mech_valve'], 'allergy_chips': ['hit_heparin'], 'med_warfarin': True}, s={'abi_r': 0.45}),
        ]
        for p in edge_patients:
            guide = build_patient_plain_explanation(p, evaluate(p, today=TODAY), today=TODAY)
            blob = ' | '.join(
                f"{m['drug']} {m['spoken']}"
                for k in ('stop_meds', 'caution_meds', 'urgent_meds', 'keep_meds') for m in guide[k]
            )
            for tok in jargon_forbidden:
                self.assertNotIn(tok, blob, f'Jargon {tok} leaked in: {blob}')

        # Negated allergy_text omitted from anamnesis (renders clean denial, never raw 'NKDA'/'არა')
        for neg in ('არა', 'NKDA', 'არ აქვს ალერგია', 'no allergies'):
            p_na = _clean_patient('evla', q={'allergy_text': neg})
            anam = generate_anamnesis(p_na, evaluate(p_na, today=TODAY))
            self.assertIn('ალერგია: უარყოფს', anam, f'Expected denial for {neg}: {anam}')
            self.assertNotIn(f'ალერგია: {neg}', anam, f'Negated allergy leaked for {neg}')


class Round13HttpRbacAndJsGuardTests(TestCase):
    """Covers File Delete RBAC, DoS gate parity, Self-Fill DOB, Tab Q preservation & Node.js live guards."""

    def setUp(self):
        User = get_user_model()
        self.nurse = User.objects.create_user(username='r13_nurse', password='pw')
        self.doctor = User.objects.create_user(username='r13_doctor', password='pw')
        StaffProfile.objects.create(user=self.doctor)
        self.admin = User.objects.create_user(username='r13_admin', password='pw', is_staff=True)
        self.client = Client()

    def test_file_delete_enforces_audit_protection_and_rbac(self):
        p = _clean_patient('open', s={'aaa_mm': 58, 'case_outcome_status': 'operated'})
        p.save()
        f_doc = PreopFile.objects.create(
            patient=p, caption='Lab PDF',
            file=SimpleUploadedFile('lab.pdf', b'%PDF-1.4 test', content_type='application/pdf'),
            created_by=self.doctor,
        )
        self.client.force_login(self.nurse)
        self.client.post(reverse('preop-file-delete', args=[f_doc.pk]))
        self.assertTrue(PreopFile.objects.filter(pk=f_doc.pk).exists())

        # Doctor also blocked on operated/audit-protected patient (only staff/superuser)
        self.client.force_login(self.doctor)
        self.client.post(reverse('preop-file-delete', args=[f_doc.pk]))
        self.assertTrue(PreopFile.objects.filter(pk=f_doc.pk).exists())

        # Unprotected patient: nurse cannot delete doctor's file, CAN delete own file
        p2 = _clean_patient('evla', s={'ven_duplex_status': 'reflux_only'})
        p2.save()
        f_other = PreopFile.objects.create(
            patient=p2, caption='Doctor ECG',
            file=SimpleUploadedFile('ecg.pdf', b'%PDF-1.4 d', content_type='application/pdf'),
            created_by=self.doctor,
        )
        f_own = PreopFile.objects.create(
            patient=p2, caption='Nurse Lab',
            file=SimpleUploadedFile('nlab.pdf', b'%PDF-1.4 n', content_type='application/pdf'),
            created_by=self.nurse,
        )
        self.client.force_login(self.nurse)
        self.client.post(reverse('preop-file-delete', args=[f_other.pk]))
        self.assertTrue(PreopFile.objects.filter(pk=f_other.pk).exists())
        self.client.post(reverse('preop-file-delete', args=[f_own.pk]))
        self.assertFalse(PreopFile.objects.filter(pk=f_own.pk).exists())

        # Staff CAN delete audit-protected file
        self.client.force_login(self.admin)
        self.client.post(reverse('preop-file-delete', args=[f_doc.pk]))
        self.assertFalse(PreopFile.objects.filter(pk=f_doc.pk).exists())

    def test_dos_operated_and_complete_and_board_block_elapsed_hold_stale_labs_unfilled_and_cancelled(self):
        self.client.force_login(self.doctor)
        tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
        old_labs = (datetime.date.today() - datetime.timedelta(days=20)).isoformat()

        # 1. Elapsed warfarin hold window (tomorrow, unverified) -> BOTH save_dos(operated) & complete_and_board blocked + NO success toast
        p_warf = _clean_patient('open', q={'med_warfarin': True}, s={'aaa_mm': 58, 'planned_surgery_date': tomorrow})
        p_warf.save()
        resp = self.client.post(
            reverse('preop-patient', args=[p_warf.pk]),
            {'section': 'dos_vqi', 'dos_partial': '1', 'case_outcome_status': 'operated'},
            follow=True,
        )
        p_warf.refresh_from_db()
        self.assertNotEqual(p_warf.studies.get('case_outcome_status'), 'operated')
        msgs = [(m.level_tag, str(m)) for m in resp.context['messages']]
        self.assertTrue(any(lvl == 'error' for lvl, _ in msgs))
        self.assertFalse(any(lvl == 'success' for lvl, _ in msgs))

        self.client.post(
            reverse('preop-patient', args=[p_warf.pk]),
            {'section': 'dos_vqi', 'action': 'complete_and_board', 'dos_partial': '1', 'case_outcome_status': 'operated'},
        )
        p_warf.refresh_from_db()
        self.assertIsNone((p_warf.studies or {}).get('board_patient_id'))
        self.assertNotEqual(p_warf.studies.get('case_outcome_status'), 'operated')

        # 2. Stale labs (20d) -> blocked on save_dos(operated) & complete_and_board
        p_stale = _clean_patient('open', s={'aaa_mm': 58, 'labs_date': old_labs, 'planned_surgery_date': (datetime.date.today() + datetime.timedelta(days=5)).isoformat()})
        p_stale.save()
        self.client.post(
            reverse('preop-patient', args=[p_stale.pk]),
            {'section': 'dos_vqi', 'action': 'complete_and_board', 'dos_partial': '1', 'case_outcome_status': 'operated'},
        )
        p_stale.refresh_from_db()
        self.assertIsNone((p_stale.studies or {}).get('board_patient_id'))

        # 3. Unfilled questionnaire (q.filled=False) & cancelled_medical -> blocked on /admit-board/ & complete_and_board
        for label, q_ov, s_ov in (
            ('unfilled', {'filled': False}, {}),
            ('cancelled', {'filled': True}, {'case_outcome_status': 'cancelled_medical'}),
        ):
            p_edge = _clean_patient('evla', q=q_ov, s={'ven_duplex_status': 'reflux_only', 'labs_date': datetime.date.today().isoformat(), **s_ov})
            p_edge.save()
            self.client.post(reverse('preop-admit-board', args=[p_edge.pk]))
            p_edge.refresh_from_db()
            self.assertIsNone((p_edge.studies or {}).get('board_patient_id'), f'{label} bypassed /admit-board/')
            self.client.post(
                reverse('preop-patient', args=[p_edge.pk]),
                {'section': 'dos_vqi', 'action': 'complete_and_board', 'dos_partial': '1', 'case_outcome_status': 'operated'},
            )
            p_edge.refresh_from_db()
            self.assertIsNone((p_edge.studies or {}).get('board_patient_id'), f'{label} bypassed complete_and_board')
        self.assertEqual(BoardPatient.objects.count(), 0)

    def test_tab_q_preserves_ic_bmt_3m_filters_negated_allergy_and_self_fill_rejects_pediatric_dob(self):
        self.client.force_login(self.doctor)
        # 1. CLTI Tab Q preserves ic_bmt_3m=True and does NOT set allergy_other on negated allergy_text
        p = _clean_patient('bypass_clti', side='R', q={'ic_bmt_3m': True}, s={'abi_r': 0.50})
        p.save()
        self.client.post(
            reverse('preop-patient', args=[p.pk]),
            {'section': 'q', 'allergy_text': 'არ აქვს ალერგია', 'mets_ok': '1'},
        )
        p.refresh_from_db()
        self.assertTrue(p.questionnaire.get('ic_bmt_3m'))
        self.assertFalse(p.questionnaire.get('allergy_other'))

        # 2. Self-Fill rejects pediatric birth_date (< 12y) and filters negated allergy_text
        p_sf = _clean_patient('evla', age=None, q={'filled': False}, s={'ven_duplex_status': 'reflux_only'})
        p_sf.save()
        tok = make_self_fill_token(p_sf)
        ped_dob = (datetime.date.today() - datetime.timedelta(days=365 * 5)).isoformat()
        anon = Client()
        anon.post(
            reverse('preop-self-fill', args=[tok]),
            {'birth_date': ped_dob, 'allergy_text': 'NKDA', 'mets_ok': '1'},
        )
        p_sf.refresh_from_db()
        self.assertIsNone(p_sf.age)
        self.assertIn('birth_date', (p_sf.studies or {}).get('_invalid_inputs', {}))
        self.assertFalse(p_sf.questionnaire.get('allergy_other'))

    def test_js_live_guards_full_coverage_in_node_vm(self):
        repo_root = Path(__file__).resolve().parents[3]
        patient_js = str(repo_root / 'static' / 'js' / 'preop-patient.js')
        nurse_js = str(repo_root / 'static' / 'js' / 'preop-nurse.js')
        proc = subprocess.run(
            ['node', '-e', _NODE_JS_GUARD_HARNESS, patient_js, nurse_js],
            capture_output=True, text=True, check=True, timeout=15,
        )
        out = json.loads(proc.stdout)
        self.assertFalse(out['p_hr_bad']['hidden'])
        self.assertIn('crit', out['p_hr_bad']['cls'])
        self.assertIn('crit', out['p_spo2_low']['cls'])
        self.assertIn('crit', out['p_wbc_hi']['cls'])
        self.assertIn('crit', out['p_alb_low']['cls'])
        self.assertTrue(out['p_dos_gl_75']['hidden'])
        self.assertFalse(out['p_dos_gl_11']['hidden'])
        self.assertIn('warn', out['p_dos_gl_11']['cls'])
        self.assertFalse(out['n_hb_ambig']['hidden'])
        self.assertFalse(out['n_hr_bad']['hidden'])
        self.assertIn('warn', out['n_dos_inr_warn']['cls'])
        self.assertIn('warn', out['n_bp_warn']['cls'])
        self.assertIn('warn', out['n_abi_warn']['cls'])
        self.assertIn('warn', out['n_dos_gl_11']['cls'])


_NODE_JS_GUARD_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');
function mkEl(tag, name, id, cls) {
    const el = {
        tagName: tag.toUpperCase(), name: name || '', id: id || '', value: '', hidden: false,
        textContent: '', innerHTML: '', className: cls || '', dataset: {}, _listeners: {}, _children: [], _parent: null,
        classList: {
            add(...xs) { const s = new Set((el.className || '').split(/\s+/).filter(Boolean)); xs.forEach(x => s.add(x)); el.className = [...s].join(' '); },
            remove(...xs) { const s = new Set((el.className || '').split(/\s+/).filter(Boolean)); xs.forEach(x => s.delete(x)); el.className = [...s].join(' '); },
            contains(x) { return (el.className || '').split(/\s+/).includes(x); },
            toggle(x, on) { if (on) this.add(x); else this.remove(x); },
        },
        addEventListener(ev, fn) { (this._listeners[ev] = this._listeners[ev] || []).push(fn); },
        dispatchEvent(e) { (this._listeners[e.type] || []).forEach(fn => fn(e)); },
        appendChild(c) { c._parent = this; this._children.push(c); return c; },
        closest(sel) {
            let cur = this;
            while (cur) {
                if (sel.startsWith('#') && cur.id === sel.slice(1)) return cur;
                if (sel.startsWith('.') && cur.classList.contains(sel.slice(1))) return cur;
                if (sel === 'form' && cur.tagName === 'FORM') return cur;
                cur = cur._parent;
            }
            return null;
        },
        querySelector(sel) { return this.querySelectorAll(sel)[0] || null; },
        querySelectorAll(sel) {
            const out = [];
            const walk = (n) => {
                for (const c of n._children) {
                    if (matches(c, sel)) out.push(c);
                    walk(c);
                }
            };
            walk(this);
            return out;
        },
    };
    return el;
}
function matches(el, sel) {
    return sel.split(',').some(part => {
        const s = part.trim();
        if (s.startsWith('.')) return el.classList.contains(s.slice(1));
        if (s.startsWith('#')) return el.id === s.slice(1);
        const m = s.match(/^(\w+)\[name="([^"]+)"\]$/);
        if (m) return el.tagName === m[1].toUpperCase() && el.name === m[2];
        return el.tagName === s.toUpperCase();
    });
}
function runJs(jsPath, fields) {
    const root = mkEl('div', '', 'root', '');
    const form = root.appendChild(mkEl('form', '', 'embed-form', ''));
    const byName = {};
    for (const [tag, name] of fields) {
        const lbl = form.appendChild(mkEl('label', '', '', 'fld'));
        const inp = lbl.appendChild(mkEl(tag, name, '', ''));
        byName[name] = inp;
    }
    const ctx = {
        window: { location: { origin: 'http://test', hash: '' }, addEventListener() {}, setTimeout() {} },
        document: {
            getElementById(id) { return root.querySelector('#' + id); },
            querySelector(s) { return root.querySelector(s); },
            querySelectorAll(s) { return root.querySelectorAll(s); },
            createElement(t) { return mkEl(t, '', '', ''); },
            addEventListener() {},
        },
        Event: function(t) { this.type = t; },
        navigator: {},
        localStorage: { getItem() { return null; }, setItem() {}, removeItem() {} },
        sessionStorage: { getItem() { return null; }, setItem() {}, removeItem() {} },
        setTimeout() {}, clearTimeout() {}, console,
    };
    vm.runInNewContext(fs.readFileSync(jsPath, 'utf8'), ctx);
    return byName;
}
function probe(byName, name, val) {
    const el = byName[name];
    el.value = String(val);
    el.dispatchEvent({ type: 'input' });
    const h = el.closest('.fld').querySelector('.fld-live-hint');
    return h ? { hidden: h.hidden, cls: h.className, text: h.textContent } : null;
}
const allFields = [
    ['input','hb'],['input','k'],['input','inr'],['input','dos_inr'],['input','plt'],
    ['input','creat'],['select','creat_unit'],['input','glucose'],['input','dos_glucose'],
    ['input','fibrinogen'],['input','ldl'],['input','hr'],['input','spo2'],
    ['input','weight_kg'],['input','height_cm'],['input','wbc'],['input','albumin'],
    ['input','bp_sys'],['input','bp_dia'],['input','bp_sys_l'],['input','bp_dia_l'],
    ['input','dos_bp_sys'],['input','dos_bp_dia'],['input','abi_r'],['input','abi_l'],
];
const pMap = runJs(process.argv[1], allFields);
const nMap = runJs(process.argv[2], allFields);
console.log(JSON.stringify({
    p_hr_bad: probe(pMap, 'hr', '300'),
    p_spo2_low: probe(pMap, 'spo2', '88'),
    p_wbc_hi: probe(pMap, 'wbc', '15'),
    p_alb_low: probe(pMap, 'albumin', '2.8'),
    p_dos_gl_75: probe(pMap, 'dos_glucose', '7.5'),
    p_dos_gl_11: probe(pMap, 'dos_glucose', '11.0'),
    n_hb_ambig: probe(nMap, 'hb', '30'),
    n_hr_bad: probe(nMap, 'hr', '300'),
    n_dos_inr_warn: probe(nMap, 'dos_inr', '1.4'),
    n_bp_warn: probe(nMap, 'bp_sys', '165'),
    n_abi_warn: probe(nMap, 'abi_r', '0.70'),
    n_dos_gl_11: probe(nMap, 'dos_glucose', '11.0'),
}));
"""
