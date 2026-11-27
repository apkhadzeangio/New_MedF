"""Round-14 adversarial clinical stress-test regression suite (Registry v1.2.13)."""
import dataclasses
import datetime
import json
import re
import subprocess
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse

from ..models import BoardEntry, PreopFile, PreopPatient, StaffProfile
from ..preop import (
    build_med_schedule,
    evaluate,
    generate_anamnesis,
    make_self_fill_token,
)
from ..preop.meds_freetext import _detect_freetext_high_risk_meds
from ..preop.nurse_plan import build_patient_plain_explanation, build_phone_med_script
from ..preop.registry import RULES_BY_ID
from ..preop.views.board import admit_preop_to_board
from ..preop.views.parsers import preop_admission_block_reason

TODAY = datetime.date(2026, 11, 27)
OP_DATE = '2026-12-10'
BASE_LABS_DATE = '2026-11-25'


def _clean_patient(op_subtype='evar', age=64, sex='M', side='R', q=None, s=None):
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
    if op_subtype in ('evar', 'open'):
        s_d.update({'aaa_mm': 58, 'cta_date': '2026-10-15', 'cta_disc': True})
    if s:
        s_d.update(s)
    return PreopPatient(
        patient_name='სინთეზური R14 პაციენტი',
        age=age, sex=sex, side=side, pathway=pw, op_subtype=op_subtype,
        visit_date=TODAY, started_on=TODAY, questionnaire=q_d, studies=s_d,
    )


class Round14ClinicalSafetyTests(TestCase):
    """Covers Round-13 Clin + Combos S1/S2/S3 pharmacology, free-text, GI bleed & parity fixes."""

    def test_freetext_negation_parentheses_dashes_georgian_verbs_and_vocab(self):
        med_cases = [
            ('ვარფარინი 5მგ (ასპირინი მოხსნილია)', {'warfarin'}),
            ('ვარფარინი 5მგ — ასპირინს არ იღებს', {'warfarin'}),
            ('პლავიქსი 75მგ არ იღებს ასპირინს', {'clopidogrel'}),
            ('eliquis 5mg (no aspirin)', {'doac'}),
            ('ვარფარინი 5მგ ალერგია არ აქვს', {'warfarin'}),
            ('ასპირინი შეჩერებულია და იღებს ელიქვისს', {'doac'}),
            ('ატორვასტატინს არ ვსვამ', set()),
        ]
        for txt, expected in med_cases:
            ft = _detect_freetext_high_risk_meds(txt, q={})
            self.assertEqual(ft['matched_keys'], expected, f'failed for {txt!r}')
            if expected:
                p = _clean_patient('evar', q={'other_meds': txt})
                ev = evaluate(p, today=TODAY)
                self.assertNotEqual(ev['clearance_seal']['code'], 'go', f'false GO on {txt!r}')
                self.assertTrue(ev['med_schedule'], f'empty med_schedule on {txt!r}')

        alg_cases = [
            'პენიცილინზე ალერგია იოდზე არ აქვს',
            'პენიცილინზე ალერგია (იოდზე არ აქვს)',
            'penicillin allergy (no iodine)',
        ]
        for alg in alg_cases:
            p = _clean_patient('evar', q={'allergy_text': alg})
            ev = evaluate(p, today=TODAY)
            self.assertIn('beta_lactam', ev['eff_allergy_chips'], alg)
            self.assertNotIn('iodine', ev['eff_allergy_chips'], alg)
            self.assertIn('ცეფაზოლინი აკრძალულია', ev['surgical_brief']['abx_regimen'], alg)
            self.assertIn('ვანკომიცინი', ev['surgical_brief']['abx_regimen'], alg)

        # Colloquial Georgian allergy denials stay clean
        for neg_alg in ('ლიდოკაინზე ალერგია არა აქვს', 'არაა ალერგიული'):
            p_neg = _clean_patient('evla', q={'allergy_text': neg_alg})
            ev_neg = evaluate(p_neg, today=TODAY)
            self.assertEqual(ev_neg['eff_allergy_chips'], [], neg_alg)
            self.assertFalse(ev_neg['has_blocking_unknown_state'], neg_alg)

        # Latin mertenil statin (triggers pregnancy contraindication) + cordaron/paceron unstructured labels
        ft_mert = _detect_freetext_high_risk_meds('mertenil 20mg', q={})
        self.assertTrue(ft_mert['has_ft_statin'])
        p_preg = _clean_patient('evla', age=30, sex='F', q={'ven_pregnant': True, 'other_meds': 'mertenil 20mg'})
        ev_preg = evaluate(p_preg, today=TODAY)
        self.assertTrue(any('სტატინ' in r['drug'] and r.get('contraindicated') for r in ev_preg['med_schedule']))
        for amio in ('cordaron 200mg', 'paceron 200mg'):
            ft_a = _detect_freetext_high_risk_meds(amio, q={})
            self.assertTrue(ft_a['has_ft_amiodarone'], amio)
            self.assertTrue(ft_a['unstructured_labels'], amio)
            p_ev = _clean_patient('evla', q={'other_meds': amio}, s={'weight_kg': 90})
            self.assertEqual(evaluate(p_ev, today=TODAY)['lidocaine_max_mg'], 300)

    def test_active_gi_bleed_blocks_hit_heparin_cea_rtpa_hormones_and_pad_antiplatelet(self):
        # 1. Active GI bleed + HIT: heparin_plan never recommends Bivalirudin/Argatroban/Fondaparinux/DOAC
        for sub in ('evla', 'phlebectomy', 'open', 'abf_clti'):
            for g_res, q_ex in (('acute_ulcer', {}), ('', {'ulcer_active_symp': True})):
                p = _clean_patient(
                    sub, age=74, q={'allergy_chips': ['hit_heparin'], 'ven_dvt_history': True, **q_ex},
                    s={'gastro_result': g_res, 'ven_duplex_status': 'reflux_only', 'abi_r': 0.8},
                )
                hp = evaluate(p, today=TODAY)['surgical_brief']['heparin_plan']
                self.assertIn('ენდოსკოპიურ ჰემოსტაზამდე', hp, (sub, g_res))
                self.assertIn('HIT', hp, (sub, g_res))
                for banned in ('ბივალირუდინ', 'არგატრობან', 'ფონდაპარინუქს', 'DOAC'):
                    self.assertNotIn(banned, hp, (sub, g_res, banned))

        # 2. CEA rt-PA Day 1-6 + clean CT + active GI bleed: red flag blocks aspirin
        for has_asa in (True, False):
            p_cea = _clean_patient(
                'cea_symp', age=67, side='R',
                q={'cea_thrombolysis': True, 'cea_event_date': '2026-11-25', 'med_aspirin': has_asa, 'med_chips': ['statin']},
                s={'carotid_r': 80, 'gastro_result': 'acute_ulcer', 'brain_imaging': 'normal_small'},
            )
            ev_cea = evaluate(p_cea, today=TODAY)
            rtpa_rf = [r for r in ev_cea['red_flags'] if 'თრომბოლიზისი' in r]
            self.assertTrue(rtpa_rf)
            self.assertIn('ენდოსკოპიურ ჰემოსტაზამდე', rtpa_rf[0])
            self.assertNotIn('დასაშვებია ასპირინი', rtpa_rf[0])

        # 3. Active GI bleed + ven_hormones: contraindicated IPC-only row (no LMWH/UFH/non-heparin prophylaxis)
        for sub, extra_q, extra_s in (
            ('phlebectomy', {}, {}),
            ('open', {'allergy_chips': ['hit_heparin']}, {}),
            ('evla', {}, {'creat': 340, 'creat_unit': 'umol'}),
        ):
            p_h = _clean_patient(
                sub, age=48, sex='F', q={'ven_hormones': True, **extra_q},
                s={'gastro_result': 'acute_ulcer', **extra_s},
            )
            h_rows = [r for r in evaluate(p_h, today=TODAY)['med_schedule'] if 'ჰორმონ' in r['drug']]
            self.assertEqual(len(h_rows), 1)
            self.assertTrue(h_rows[0].get('contraindicated'))
            self.assertIn('IPC', h_rows[0]['rule'])
            for bad in ('LMWH პროფილაქტიკა', 'არაჰეპარინული პროფილაქტიკა', 'კანქვეშა UFH პროფილაქტიკა'):
                self.assertNotIn(bad, h_rows[0]['rule'])

        # 4. ABF / Bypass / PTA missing-antiplatelet & PTA clopidogrel_intol guarded during active GI bleed
        for sub in ('abf_clti', 'bypass_clti', 'pta_clti'):
            p_ap = _clean_patient(
                sub, age=70, q={'med_chips': ['statin']},
                s={'gastro_result': 'acute_ulcer', 'abi_r': 0.45, 'pta_lesion_site': 'femoropopliteal'},
            )
            ev_ap = evaluate(p_ap, today=TODAY)
            self.assertFalse(any('არ იღებს ანტიაგრეგანტს' in a for a in ev_ap['amber_flags']), sub)
        p_ci = _clean_patient(
            'pta_clti', age=70, q={'allergy_chips': ['clopidogrel_intol'], 'med_chips': ['statin']},
            s={'gastro_result': 'acute_ulcer', 'abi_r': 0.45, 'pta_lesion_site': 'femoropopliteal'},
        )
        ev_ci = evaluate(p_ci, today=TODAY)
        self.assertFalse(any('ალტერნატიული P2Y12 (ტიკაგრელორი)' in r for r in ev_ci['red_flags']))
        self.assertTrue(any('ენდოსკოპიურ ჰემოსტაზამდე' in r for r in ev_ci['red_flags']))

        # 5. Venous active GI bleed + HIT propagates HIT ban to venous amber flag & Stage-2 VTE task
        p_vh = _clean_patient(
            'evla', age=76, q={'ven_dvt_history': True, 'ven_thrombophilia': True, 'allergy_chips': ['hit_heparin']},
            s={'gastro_result': 'acute_ulcer', 'ven_duplex_status': 'reflux_only', 'abi_r': 1.0},
        )
        ev_vh = evaluate(p_vh, today=TODAY)
        cap_amb = [a for a in ev_vh['amber_flags'] if 'Caprini' in a]
        vte_t = [t['label'] for t in ev_vh['tasks'] if t['key'] == 'vte']
        self.assertTrue(cap_amb and 'HIT: ჰეპარინი/LMWH აკრძალულია' in cap_amb[0])
        self.assertTrue(vte_t and 'HIT: ჰეპარინი/LMWH აკრძალულია' in vte_t[0])

    def test_pregnancy_severe_renal_oac_and_pta_dapt_task_parity(self):
        doc_u = get_user_model().objects.create_user(username='r14_preg_doc', password='pw')
        for sub, extra_q, extra_s in (
            ('abf_clti', {'med_warfarin': True}, {'creat': 340, 'creat_unit': 'umol'}),
            ('bypass_clti', {'med_doac': True, 'on_dialysis': True}, {}),
            ('pta_clti', {'med_warfarin': True}, {'creat': 340, 'creat_unit': 'umol'}),
        ):
            p = _clean_patient(
                sub, age=32, sex='F', side='R',
                q={'ven_pregnant': True, 'med_chips': ['statin'], **extra_q},
                s={'abi_r': 0.45, 'pta_lesion_site': 'femoropopliteal', 'pta_access_site': 'cfa_clean', **extra_s},
            )
            p.save()
            ev = evaluate(p, today=TODAY)
            for txt in (ev['post_antithrombotic_label'], ev['surgical_brief']['postop_antithrombotic']):
                self.assertIn('LMWH კუმულირდება', txt, sub)
                self.assertNotIn('შეარჩიეთ LMWH/UFH', txt, sub)
            bp = admit_preop_to_board(p, doc_u)[0]
            task_texts = list(BoardEntry.objects.filter(patient=bp, kind=BoardEntry.TASK).values_list('text', flat=True))
            pad_t = [t for t in task_texts if t.startswith('ანტითრომბოზული სქემა:')]
            self.assertTrue(pad_t and 'LMWH კუმულირდება' in pad_t[0] and 'შეარჩიეთ LMWH/UFH' not in pad_t[0], sub)

        # PTA Stage-2 dapt task on has_contra_oac + pregnancy + severe renal / HIT / plain
        for label, extra_q, extra_s, must_have in (
            ('sev_ren', {'med_warfarin': True}, {'creat': 340}, ('მეან-გინეკოლოგ', 'LMWH კუმულირდება', 'UFH')),
            ('hit', {'med_warfarin': True, 'allergy_chips': ['hit_heparin']}, {}, ('მეან-გინეკოლოგ', 'ჰემატოლოგ', 'ჰეპარინი/LMWH აკრძალულია')),
            ('plain', {'med_doac': True}, {}, ('მეან-გინეკოლოგ', 'ორსულობა')),
        ):
            p_pta = _clean_patient(
                'pta_clti', age=32, sex='F', side='R',
                q={'ven_pregnant': True, **extra_q},
                s={'abi_r': 0.45, 'pta_lesion_site': 'femoropopliteal', **extra_s},
            )
            dapt_t = [t['label'] for t in evaluate(p_pta, today=TODAY)['tasks'] if t['key'] == 'dapt']
            self.assertTrue(dapt_t, label)
            for tok in must_have:
                self.assertIn(tok, dapt_t[0], (label, tok))

    def test_standalone_build_med_schedule_phlebectomy_auto_neuraxial_parity(self):
        cases = [
            ('vka_inr25_undated', {'med_warfarin': True}, {'inr': 2.5, 'planned_surgery_date': ''}),
            ('vka_inr25_d3', {'med_warfarin': True}, {'inr': 2.5, 'planned_surgery_date': '2026-11-30'}),
            ('vka_inr25_d7', {'med_warfarin': True}, {'inr': 2.5, 'planned_surgery_date': '2026-12-04'}),
            ('fib_low_doac', {'med_doac': True}, {'fibrinogen': 1.2}),
            ('lido_chip_doac', {'med_doac': True, 'allergy_chips': ['lidocaine']}, {}),
            ('lido_ft_doac', {'med_doac': True, 'allergy_text': 'ლიდოკაინზე ალერგია'}, {}),
            ('echo_valve_doac', {'med_doac': True}, {'echo_valve_issue': True}),
            ('hypotension_doac', {'med_doac': True}, {'bp_sys': 85, 'bp_dia': 55}),
            ('inverted_bp_doac', {'med_doac': True}, {'bp_sys': 90, 'bp_dia': 100}),
            ('recent_stent_doac', {'med_doac': True, 'med_clopidogrel': True, 'recent_stent': True}, {}),
            ('ft_antithromb_doac', {'med_doac': True, 'other_meds': 'prasugrel 10mg'}, {}),
            ('ambig_plt_doac', {'med_doac': True}, {'plt': 2700}),
            ('d0_dos_inr_hi', {'med_warfarin': True}, {'dos_inr': 1.8, 'planned_surgery_date': TODAY.isoformat()}),
        ]
        for tag, q_ex, s_ex in cases:
            p = _clean_patient('phlebectomy', q=q_ex, s={'anesthesia_plan': 'auto', **s_ex})
            ev = evaluate(p, today=TODAY)
            stop_blk = bool(
                ev['has_hard_contraindication'] or ev['clearance_seal']['code'] == 'stop'
                or ev['dos_hold_danger'] or ev['solo_neuraxial_anatomic_mismatch']
            )
            standalone = build_med_schedule(
                p, ev['q'], ev['s'], ev['egfr'], crcl=ev['crcl'],
                stop_blocked=stop_blk, has_neuraxial_override=None, today=TODAY,
            )
            ev_sig = [(r['drug'], r['tone'], r['date_plan'], r['rule']) for r in ev['med_schedule']]
            st_sig = [(r['drug'], r['tone'], r['date_plan'], r['rule']) for r in standalone]
            self.assertEqual(st_sig, ev_sig, f'standalone parity drift on {tag}')

    def test_patient_guide_phone_script_jargon_and_dynamic_stale_labs(self):
        banned = ('დე-ესკალაცი', 'დეესკალაცი', 'ენდოვასკულური PTA', 'ESC', 'Class III')
        jargon_cases = [
            ('pta_clti', {'med_clopidogrel': True, 'med_ticagrelor': True}, {'planned_surgery_date': OP_DATE}),
            ('cea_symp', {'med_clopidogrel': True, 'med_ticagrelor': True}, {'planned_surgery_date': ''}),
            ('pta_clti', {'med_prasugrel': True}, {'planned_surgery_date': ''}),
            ('pta_clti', {'med_doac': True, 'med_prasugrel': True}, {'planned_surgery_date': OP_DATE}),
        ]
        for sub, q_ex, s_ex in jargon_cases:
            p = _clean_patient(sub, q=q_ex, s=s_ex)
            ev = evaluate(p, today=TODAY)
            guide = build_patient_plain_explanation(p, ev, today=TODAY)
            phone = build_phone_med_script(p, ev, today=TODAY)
            for grp in ('stop_meds', 'caution_meds', 'keep_meds'):
                for row in guide[grp]:
                    blob = f"{row['drug']} {row['spoken']}"
                    for tok in banned:
                        self.assertNotIn(tok, blob, (sub, q_ex, tok, blob))
            for grp in ('stop_rows', 'caution_rows', 'keep_rows'):
                for row in phone[grp]:
                    for tok in banned:
                        self.assertNotIn(tok, row['spoken'], (sub, q_ex, tok, row['spoken']))

        # Dynamic INF_STALE_LABS_DAYS (14 -> 21) across labs reflex, cardiac task, failsafe & _tab_doc.html
        mut_rule = dataclasses.replace(RULES_BY_ID['INF_STALE_LABS_DAYS'], value=21)
        with mock.patch.dict(RULES_BY_ID, {'INF_STALE_LABS_DAYS': mut_rule}):
            p_stale = _clean_patient('evar', s={'labs_date': '2026-11-01'})
            ev_s = evaluate(p_stale, today=TODAY)
            self.assertEqual(ev_s['stale_labs_days'], 21)
            ref = [r['trigger'] for r in ev_s['reflex_orders'] if r['key'] == 'stale_labs']
            tsk = [t['label'] for t in ev_s['tasks'] if t['key'] == 'stale_labs']
            self.assertTrue(ref and '>21 დღე' in ref[0] and '>14' not in ref[0])
            self.assertTrue(tsk and '>21 დღე' in tsk[0] and '>14' not in tsk[0])

            p_nodt = _clean_patient('evar', s={'labs_date': ''})
            ev_n = evaluate(p_nodt, today=TODAY)
            unk = [u['detail'] for u in ev_n['unknown_state_flags'] if u['key'] == 'labs_missing_date']
            self.assertTrue(unk and '21-დღიანი' in unk[0] and '14-დღიანი' not in unk[0])

            p_stale.save()
            doc_u = get_user_model().objects.create_user(username='r14_stale_doc', password='pw')
            StaffProfile.objects.create(user=doc_u, display_name='ექიმი R14')
            c = Client()
            c.force_login(doc_u)
            resp = c.get(reverse('preop-patient', args=[p_stale.pk]) + '?tab=doc')
            html = resp.content.decode('utf-8')
            self.assertIn('&gt;21 დღე', html)
            self.assertNotIn('&gt;14 დღე', html)


class Round14WorkflowSecurityAndFuzzTests(TestCase):
    """Covers Round-13 Fuzz + Combos RBAC, deferred gates, Tab Q round-trip, invariants & JS guards."""

    def setUp(self):
        User = get_user_model()
        self.nurse = User.objects.create_user(username='r14_nurse', password='pw')
        self.doc = User.objects.create_user(username='r14_doc', password='pw')
        StaffProfile.objects.create(user=self.doc, display_name='ქირურგი R14')
        self.staff = User.objects.create_user(username='r14_staff', password='pw', is_staff=True)

    def _attach_doc_file(self, patient):
        return PreopFile.objects.create(
            patient=patient,
            category=PreopFile.Category.LABS,
            caption='Doctor Lab PDF',
            file=SimpleUploadedFile('lab_r14.pdf', b'%PDF-1.4\n%test\n', content_type='application/pdf'),
            created_by=self.doc,
        )

    def test_preop_move_delete_rbac_and_audit_protection(self):
        c_nurse, c_doc, c_staff = Client(), Client(), Client()
        c_nurse.force_login(self.nurse)
        c_doc.force_login(self.doc)
        c_staff.force_login(self.staff)

        # 1. Approved patient with doctor file: plain_nurse AND non-staff doctor blocked, file preserved
        p_app = _clean_patient('evar')
        p_app.approved_at = datetime.datetime(2026, 11, 26, 10, 0, tzinfo=datetime.UTC)
        p_app.approved_by = self.doc
        p_app.save()
        f_app = self._attach_doc_file(p_app)
        url_app = reverse('preop-move', args=[p_app.pk])
        r1 = c_nurse.post(url_app, {'action': 'delete'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r1.status_code, 403)
        self.assertTrue(PreopPatient.objects.filter(pk=p_app.pk, archived=False).exists())
        self.assertTrue(PreopFile.objects.filter(pk=f_app.pk).exists())
        r2 = c_doc.post(url_app, {'action': 'delete'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r2.status_code, 403)
        self.assertTrue(PreopPatient.objects.filter(pk=p_app.pk, archived=True).exists())
        self.assertTrue(PreopFile.objects.filter(pk=f_app.pk).exists())

        # 2. Archived patient with doctor file: plain_nurse blocked
        p_arch = _clean_patient('evla')
        p_arch.archived = True
        p_arch.save()
        f_arch = self._attach_doc_file(p_arch)
        r3 = c_nurse.post(reverse('preop-move', args=[p_arch.pk]), {'action': 'delete'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r3.status_code, 403)
        self.assertTrue(PreopPatient.objects.filter(pk=p_arch.pk).exists())
        self.assertTrue(PreopFile.objects.filter(pk=f_arch.pk).exists())

        # 3. Unprotected patient containing doctor file: plain_nurse blocked
        p_docf = _clean_patient('evla')
        p_docf.created_by = self.nurse
        p_docf.save()
        f_docf = self._attach_doc_file(p_docf)
        r4 = c_nurse.post(reverse('preop-move', args=[p_docf.pk]), {'action': 'delete'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r4.status_code, 403)
        self.assertTrue(PreopPatient.objects.filter(pk=p_docf.pk).exists())
        self.assertTrue(PreopFile.objects.filter(pk=f_docf.pk).exists())

        # 4. Unprotected self-owned patient without foreign files or surgeon: plain_nurse allowed
        p_own = _clean_patient('evla')
        p_own.created_by = self.nurse
        p_own.save()
        r5 = c_nurse.post(reverse('preop-move', args=[p_own.pk]), {'action': 'delete'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r5.status_code, 200)
        self.assertFalse(PreopPatient.objects.filter(pk=p_own.pk).exists())

        # 5. Staff admin CAN delete audit-protected patient
        r6 = c_staff.post(url_app, {'action': 'delete'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r6.status_code, 200)
        self.assertFalse(PreopPatient.objects.filter(pk=p_app.pk).exists())

    def test_case_outcome_deferred_blocks_all_gates_and_self_fill_get_post_parity(self):
        p = _clean_patient('evla', s={
            'case_outcome_status': 'deferred', 'ven_duplex_status': 'reflux_only',
            'abi_r': 1.0, 'compression_ready': True, 'anesth_cleared': True,
        })
        p.save()
        ev = evaluate(p, today=TODAY)
        self.assertEqual(ev['case_outcome'], 'deferred')
        self.assertTrue(preop_admission_block_reason(ev, s=p.studies))

        c = Client()
        c.force_login(self.doc)
        url_pat = reverse('preop-patient', args=[p.pk])
        c.post(url_pat, {'action': 'approve'})
        p.refresh_from_db()
        self.assertIsNone(p.approved_at)

        r_mv = c.post(reverse('preop-move', args=[p.pk]), {'action': 'move', 'slot': 'someday:ready'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r_mv.status_code, 400)
        c.post(url_pat, {'action': 'save_header', 'patient_name': p.patient_name, 'op_subtype': 'evla', 'someday_list': 'ready'})
        p.refresh_from_db()
        self.assertNotEqual(p.someday_list, 'ready')

        c.post(reverse('preop-admit-board', args=[p.pk]), {})
        p.refresh_from_db()
        self.assertFalse(p.studies.get('board_patient_id'))

        for cab in ('0', '1'):
            c.post(url_pat, {'action': 'save_dos', 'case_outcome_status': 'operated', 'complete_and_board': cab})
            p.refresh_from_db()
            self.assertEqual(p.studies.get('case_outcome_status'), 'deferred')
            self.assertFalse(p.studies.get('board_patient_id'))

        # Self-Fill GET & POST both locked on deferred
        tok = make_self_fill_token(p)
        sf_url = reverse('preop-self-fill', args=[tok])
        c_anon = Client()
        r_get = c_anon.get(sf_url)
        self.assertEqual(r_get.status_code, 200)
        self.assertNotIn('<form', r_get.content.decode('utf-8'))
        r_post = c_anon.post(sf_url, {'mets_ok': '1'})
        self.assertEqual(r_post.status_code, 302)
        self.assertIn('locked=1', r_post['Location'])

    def test_tab_q_html_roundtrip_preserves_negated_allergy_text(self):
        c = Client()
        c.force_login(self.doc)
        for phrase in ('არ აქვს ალერგია', 'NKDA', 'არა', 'no allergy', 'ალერგია არა აქვს', 'არაა ალერგიული'):
            p = _clean_patient('evla', q={'allergy_text': phrase}, s={'ven_duplex_status': 'reflux_only', 'abi_r': 1.0})
            p.save()
            url = reverse('preop-patient', args=[p.pk])
            c.post(url, {'action': 'save_q', 'mets_ok': '1', 'allergy_iodine_asked': '1', 'allergy_text': phrase})
            p.refresh_from_db()
            self.assertFalse(p.questionnaire.get('allergy_other'), phrase)
            html = c.get(url + '?tab=q').content.decode('utf-8')
            m = re.search(r'<input[^>]*name="allergy_other"[^>]*>', html)
            self.assertIsNotNone(m, phrase)
            self.assertNotIn('checked', m.group(0), f'Tab Q poisoned allergy_other checkbox on {phrase!r}')
            # Faithful round-trip Submit #2 without allergy_other
            c.post(url, {'action': 'save_q', 'mets_ok': '1', 'allergy_iodine_asked': '1', 'allergy_text': phrase})
            p.refresh_from_db()
            ev2 = evaluate(p, today=TODAY)
            self.assertFalse(p.questionnaire.get('allergy_other'), phrase)
            self.assertFalse(ev2['has_blocking_unknown_state'], phrase)

    def test_anamnesis_age_validation_nested_nonfinite_and_canonical_chip_order(self):
        for bad_age in (999, -5, float('nan'), float('inf'), 'bad'):
            p = _clean_patient('evar')
            p.age = bad_age
            anam = generate_anamnesis(p, evaluate(p, today=TODAY))
            first_line = anam.splitlines()[0]
            self.assertNotIn(str(bad_age), first_line, f'anamnesis leaked invalid age {bad_age!r}')

        for nested in ({'k': float('nan')}, [float('inf')], {'x': '-inf'}):
            p_nf = _clean_patient('evar', s={'aaa_mm': nested})
            ev_nf = evaluate(p_nf, today=TODAY)
            blob = ' '.join(ev_nf['implausible_fields']) + ' ' + ' '.join(u['label'] for u in ev_nf['unknown_state_flags'])
            self.assertIn('არასწორი რიცხვი', blob)
            self.assertNotIn('nan', blob.lower())
            self.assertNotIn('inf', blob.lower())

        p1 = _clean_patient('open', q={'cad_chips': ['mi', 'stent_gt1y', 'hf']}, s={'anesth_lines_chips': ['art_line', 'cvc']})
        p2 = _clean_patient('open', q={'cad_chips': ['hf', 'stent_gt1y', 'mi']}, s={'anesth_lines_chips': ['cvc', 'art_line']})
        ev1, ev2 = evaluate(p1, today=TODAY), evaluate(p2, today=TODAY)
        self.assertEqual(ev1['anesth_lines_txt'], ev2['anesth_lines_txt'])
        self.assertEqual(ev1['anesthetic_brief'], ev2['anesthetic_brief'])
        self.assertEqual(generate_anamnesis(p1, ev1), generate_anamnesis(p2, ev2))

    def test_nurse_js_vitals_creatinine_and_dos_draft_restore_live_node(self):
        root_dir = Path(__file__).resolve().parents[3]
        nurse_js = root_dir / 'static' / 'js' / 'preop-nurse.js'
        res = subprocess.run(
            ['node', '-e', _NODE_NURSE_R14_SCRIPT, str(nurse_js)],
            capture_output=True, text=True, check=True, timeout=15,
        )
        out = json.loads(res.stdout)
        self.assertTrue(out['cr_80']['hidden'])
        self.assertFalse(out['cr_130']['hidden'])
        self.assertIn('თირკმლის', out['cr_130']['html'])
        self.assertFalse(out['cr_500']['hidden'])
        self.assertIn('RCRI', out['cr_500']['html'])
        self.assertFalse(out['cr_5_5']['hidden'])
        self.assertIn('RCRI', out['cr_5_5']['html'])
        self.assertFalse(out['dos_restored']['hidden'])
        self.assertIn('195', out['dos_restored']['html'])


_NODE_NURSE_R14_SCRIPT = r"""
const fs = require('fs'), vm = require('vm');
function mkEl(tag, id, name, cls) {
    const el = {
        tagName: tag.toUpperCase(), id: id || '', name: name || '', className: cls || '',
        type: 'text', value: '', checked: false, hidden: true, innerHTML: '', textContent: '',
        dataset: {}, _children: [], _parent: null, _ev: {},
        get elements() { return el.querySelectorAll('input, select, textarea'); },
        classList: { contains(c) { return el.className.split(/\s+/).includes(c); }, add() {}, remove() {} },
        appendChild(c) { c._parent = el; el._children.push(c); return c; },
        addEventListener(t, fn) { (el._ev[t] = el._ev[t] || []).push(fn); },
        dispatchEvent(e) { (el._ev[e.type] || []).forEach(fn => fn(e)); },
        closest() { return null; },
        querySelector(sel) { return el.querySelectorAll(sel)[0] || null; },
        querySelectorAll(sel) {
            const out = [];
            const walk = (n) => {
                for (const c of n._children) {
                    if (matches(c, sel)) out.push(c);
                    walk(c);
                }
            };
            walk(el);
            return out;
        },
    };
    return el;
}
function matches(el, sel) {
    return sel.split(',').some(part => {
        const s = part.trim();
        if (s === 'form[data-nw-draft]') return el.tagName === 'FORM' && Boolean(el.dataset.nwDraft);
        if (s.startsWith('#')) return el.id === s.slice(1);
        if (s.startsWith('.')) return el.classList.contains(s.slice(1));
        const m = s.match(/^(\w+)\[name="([^"]+)"\]$/);
        if (m) return el.tagName === m[1].toUpperCase() && el.name === m[2];
        return el.tagName === s.toUpperCase();
    });
}
function boot(draftJson) {
    const root = mkEl('div', 'nw-patient-root', '', '');
    root.dataset.patientPk = '999';
    const banner = root.appendChild(mkEl('div', 'nw-draft-banner', '', ''));
    const vForm = root.appendChild(mkEl('form', 'nw-vitals-form', '', ''));
    vForm.dataset.nwDraft = 'vitals';
    const vGuard = root.appendChild(mkEl('div', 'nw-live-guard', '', ''));
    const cr = vForm.appendChild(mkEl('input', 'nw-creat', 'creat', ''));
    const cu = vForm.appendChild(mkEl('select', 'nw-creat-unit', 'creat_unit', ''));
    const dForm = root.appendChild(mkEl('form', 'nw-dos-form', '', ''));
    dForm.dataset.nwDraft = 'dos';
    const dGuard = root.appendChild(mkEl('div', 'nw-dos-live-guard', '', ''));
    const dosSbp = dForm.appendChild(mkEl('input', 'nw-dos-sbp', 'dos_bp_sys', ''));
    const store = draftJson ? { 'preop_nurse_draft_v1_999': draftJson } : {};
    const ctx = {
        window: { location: { origin: 'http://test', hash: '' }, addEventListener() {}, setTimeout() {} },
        document: {
            getElementById(id) { return id === 'nw-patient-root' ? root : root.querySelector('#' + id); },
            querySelector(s) { return root.querySelector(s); },
            querySelectorAll(s) { return root.querySelectorAll(s); },
            createElement(t) { return mkEl(t, '', '', ''); },
            addEventListener() {},
        },
        Event: function(t) { this.type = t; },
        navigator: {},
        localStorage: { getItem(k) { return store[k] || null; }, setItem(k, v) { store[k] = v; }, removeItem(k) { delete store[k]; } },
        sessionStorage: { getItem() { return null; }, setItem() {}, removeItem() {} },
        setTimeout() {}, clearTimeout() {}, console,
    };
    vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), ctx);
    return { cr, cu, vForm, vGuard, dGuard, banner, dosSbp };
}
const a = boot(null);
function testCr(val, unit) {
    a.cr.value = val; a.cu.value = unit || '';
    a.vForm.dispatchEvent({ type: 'input' });
    return { hidden: a.vGuard.hidden, html: a.vGuard.innerHTML };
}
const b = boot(JSON.stringify({ savedAt: Date.now(), forms: { dos: { dos_bp_sys: '195' } } }));
console.log(JSON.stringify({
    cr_80: testCr('80', 'umol'),
    cr_130: testCr('130', 'umol'),
    cr_500: testCr('500', 'umol'),
    cr_5_5: testCr('5,5', ''),
    dos_restored: { hidden: b.dGuard.hidden, html: b.dGuard.innerHTML },
}));
"""
