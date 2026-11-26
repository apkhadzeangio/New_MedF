"""Track A1/A2 Clinical Rule Registry integrity, catalog view, sign-off, and ⓘ citation tests."""
import json
import re

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from .models import PreopPatient, PreopRuleSignoff
from .preop import build_med_schedule, evaluate
from .preop.registry import (
    CHANGELOG,
    DOMAIN_LABELS,
    KIND_LABELS,
    REGISTRY_UPDATED_ON,
    REGISTRY_VERSION,
    RULES,
    RULES_BY_ID,
    STATUS_LOCAL,
    STATUS_VERIFIED,
    match_rule_for_reflex,
    match_rule_for_text,
    registry_summary,
    rule_value,
)
from .test_preop_golden import build_golden_scenarios
from .test_preop_sim import SIM_TODAY


class PreopRuleRegistryTests(SimpleTestCase):
    """Guarantee every rule entry is complete, valid, and covers every emitted clinical signal."""

    def test_registry_schema_and_metadata_completeness(self):
        self.assertGreaterEqual(len(RULES), 40)
        self.assertEqual(len(RULES), len(RULES_BY_ID))
        self.assertTrue(REGISTRY_VERSION)
        self.assertTrue(REGISTRY_UPDATED_ON)
        self.assertGreaterEqual(len(CHANGELOG), 1)
        valid_statuses = {STATUS_VERIFIED, STATUS_LOCAL}
        for r in RULES:
            self.assertRegex(r.id, r'^[A-Z0-9_]+$')
            self.assertIn(r.domain, DOMAIN_LABELS, f'Unknown domain on {r.id}')
            self.assertIn(r.kind, KIND_LABELS, f'Unknown kind on {r.id}')
            self.assertIn(r.status, valid_statuses, f'Unknown status on {r.id}')
            self.assertTrue(r.title_ka.strip(), f'Empty title_ka on {r.id}')
            self.assertTrue(r.description_ka.strip(), f'Empty description_ka on {r.id}')
            self.assertTrue(r.source.strip(), f'Empty source on {r.id}')
            self.assertGreaterEqual(r.year, 1975, f'Invalid year on {r.id}')
            self.assertTrue(r.section.strip(), f'Empty section on {r.id}')
            self.assertTrue(r.class_level.strip(), f'Empty class_level on {r.id}')
            self.assertTrue(r.source_wording.strip(), f'Empty source_wording on {r.id}')
            self.assertTrue(r.value_label.strip(), f'Empty value_label on {r.id}')
            self.assertTrue(r.verified_on.strip(), f'Empty verified_on on {r.id}')
        summary = registry_summary()
        self.assertEqual(summary['total'], len(RULES))
        self.assertGreater(summary['verified_count'], 0)
        self.assertGreater(summary['local_count'], 0)

    def test_every_emitted_alert_reflex_and_med_row_maps_to_registry(self):
        unmatched = []
        for sid, patient, _ in build_golden_scenarios():
            ev = evaluate(patient, today=SIM_TODAY)
            for bucket in ('red_flags', 'amber_flags', 'hard_contraindications', 'dos_warnings'):
                for msg in ev.get(bucket) or ():
                    if match_rule_for_text(msg) is None:
                        unmatched.append(f'{sid}.{bucket}: {msg}')
            for ro in ev.get('reflex_orders') or ():
                if match_rule_for_reflex(ro.get('key'), f"{ro.get('trigger', '')} {ro.get('action', '')}") is None:
                    unmatched.append(f'{sid}.reflex[{ro.get("key")}]: {ro.get("trigger")}')
            meds = build_med_schedule(patient, ev['q'], ev['s'], ev['egfr'], crcl=ev['crcl'])
            for row in meds:
                if match_rule_for_text(f"{row.get('drug', '')} {row.get('rule', '')}", domain_hint='meds') is None:
                    unmatched.append(f'{sid}.med[{row.get("drug")}]: {row.get("rule")}')
        self.assertEqual(unmatched, [], 'Unmatched clinical outputs without a Rule Registry entry:\n' + '\n'.join(unmatched[:25]))

    def test_registry_values_used_by_engine(self):
        self.assertEqual(rule_value('AAA_MALE_MIN_MM'), 55)
        self.assertEqual(rule_value('AAA_FEMALE_MIN_MM'), 50)
        self.assertEqual(rule_value('CEA_SYMP_WINDOW_DAYS')[0], 14)
        self.assertEqual(rule_value('ASRA_NEURAXIAL_COAG_GATE')['inr_max'], 1.4)
        self.assertEqual(rule_value('MED_WARFARIN_HOLD_DAYS')['hold_days'], 5)
        self.assertEqual(rule_value('INF_STALE_LABS_DAYS'), 14)


class PreopRulesCatalogAndCitationUITests(TestCase):
    """Verify '/preop/rules/' catalog, clinician sign-off persistence, and patient ⓘ citations."""

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.doc = User.objects.create_user(
            username='rules_doc', password='RulesDocPassword!1',
            first_name='ნინო', last_name='ექიმი', is_staff=True,
        )

    def test_rules_catalog_auth_filters_and_doctor_signoff(self):
        url = reverse('preop-rules')
        self.assertEqual(self.client.get(url).status_code, 302)

        self.client.force_login(self.doc)
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'კლინიკური წესების რეესტრი')
        self.assertContains(resp, 'AAA_MALE_MIN_MM')

        resp_local = self.client.get(url + f'?status={STATUS_LOCAL}')
        self.assertEqual(resp_local.status_code, 200)
        self.assertContains(resp_local, 'INF_STALE_LABS_DAYS')
        self.assertNotContains(resp_local, 'id="rule-AAA_MALE_MIN_MM"')

        resp_q = self.client.get(url + '?domain=meds&q=ASRA')
        self.assertEqual(resp_q.status_code, 200)
        self.assertContains(resp_q, 'ASRA_NEURAXIAL_COAG_GATE')

        post_resp = self.client.post(url, {
            'rule_id': 'INF_STALE_LABS_DAYS',
            'verdict': 'approved',
            'note': 'განყოფილების პროტოკოლით დადასტურებულია 14 დღე',
            'ret_status': 'unsigned-local',
        })
        self.assertEqual(post_resp.status_code, 302)
        self.assertIn('#rule-INF_STALE_LABS_DAYS', post_resp['Location'])
        so = PreopRuleSignoff.objects.get(item_kind='rule', item_id='INF_STALE_LABS_DAYS')
        self.assertEqual(so.verdict, PreopRuleSignoff.Verdict.APPROVED)
        self.assertEqual(so.signed_by, self.doc)
        self.assertEqual(so.registry_version, REGISTRY_VERSION)

        resp_unsigned = self.client.get(url + '?status=unsigned-local')
        self.assertNotContains(resp_unsigned, 'id="rule-INF_STALE_LABS_DAYS"')

        bad_resp = self.client.post(url, {'rule_id': 'NON_EXISTENT_RULE', 'verdict': 'approved'})
        self.assertEqual(bad_resp.status_code, 302)
        self.assertFalse(PreopRuleSignoff.objects.filter(item_id='NON_EXISTENT_RULE').exists())

    def test_patient_page_renders_inline_rule_citations_and_modal_payload(self):
        self.client.force_login(self.doc)
        patient = PreopPatient.objects.create(
            patient_name='სატესტო პაციენტი',
            age=72, sex='M',
            pathway=PreopPatient.Pathway.AAA_INFRA,
            op_subtype=PreopPatient.OpSubtype.OPEN,
            questionnaire={'filled': True, 'med_doac': True, 'dm': True, 'sglt2': True, 'mets_ok': True},
            studies={'aaa_mm': 62, 'hb': 98, 'creat': 160, 'weight_kg': 80, 'dos_bp_sys': 192},
        )
        resp = self.client.get(reverse('preop-patient', args=[patient.pk]) + '?tab=doc')
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode('utf-8')
        self.assertIn('id="po-rule-modal"', html)
        m = re.search(r'<script id="po-rules-data" type="application/json">(.*?)</script>', html, re.DOTALL)
        self.assertIsNotNone(m, 'Missing #po-rules-data JSON payload')
        rules_map = json.loads(m.group(1))
        cite_ids = set(re.findall(r'class="po-cite-btn" data-rule-id="([^"]+)"', html))
        self.assertGreaterEqual(len(cite_ids), 8)
        for rid in cite_ids:
            self.assertIn(rid, rules_map, f'Button references {rid} missing from #po-rules-data')
            self.assertIn(rid, RULES_BY_ID, f'Button references unknown rule {rid}')
        for required_score in (
            'SCORE_ASA_PHYSICAL_STATUS', 'SCORE_RCRI_LEE_1999',
            'SCORE_VSG_CRI_BERTGES_2010', 'SCORE_ARISCAT_CANET_2010',
            'SCORE_CAPRINI_VTE', 'SCORE_RENAL_CKDEPI_CG',
        ):
            self.assertIn(required_score, cite_ids)


class PreopFailSafeUnknownStateTests(TestCase):
    """Track A5: Missing critical data triggers Fail-Safe banners and blocks false green GO."""

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.doc = User.objects.create_user(
            username='fs_doc', password='FsDocPassword!1',
            first_name='თამარ', last_name='ექიმი', is_staff=True,
        )

    def test_all_six_unknown_state_conditions_and_score_completeness(self):
        p = PreopPatient(
            patient_name='არასრული სატესტო',
            age=35, sex='F', side='',
            pathway=PreopPatient.Pathway.AAA_INFRA,
            op_subtype=PreopPatient.OpSubtype.EVAR,
            questionnaire={
                'filled': True, 'filled_by': 'პაციენტი (ბმულით)', 'nurse_verified': False,
                'med_doac': True, 'allergy_other': True, 'other_meds': 'ქსარელტო 20მგ',
                'pregnant_possible': False, 'mets_ok': True,
            },
            studies={'aaa_mm': 52, 'hb': 126},  # no labs_date, no creat, no weight, no spo2
        )
        ev = evaluate(p, today=SIM_TODAY)
        keys = {u['key'] for u in ev['unknown_state_flags']}
        self.assertTrue({
            'unverified_self_fill', 'renal_drug_no_crcl', 'unclarified_allergy',
            'labs_missing_date', 'incomplete_risk_scores', 'reproductive_pregnancy_unverified',
        }.issubset(keys))
        self.assertTrue(ev['has_blocking_unknown_state'])
        self.assertTrue(ev['score_completeness']['rcri']['incomplete'])
        self.assertTrue(ev['score_completeness']['ariscat']['incomplete'])
        self.assertNotEqual(ev['clearance_seal']['code'], 'go')

    def test_doac_without_weight_blocks_false_go_until_crcl_computed_and_renders_banners(self):
        self.client.force_login(self.doc)
        p = PreopPatient.objects.create(
            patient_name='დოაკ წონაძე',
            age=70, sex='M', side='R',
            pathway=PreopPatient.Pathway.CEA,
            op_subtype=PreopPatient.OpSubtype.CEA_ASYMP,
            questionnaire={
                'filled': True, 'nurse_verified': True, 'med_aspirin': True,
                'med_doac': True, 'med_chips': ['statin'], 'mets_ok': True,
            },
            studies={
                'hb': 138, 'plt': 230, 'inr': 1.0, 'creat': 88, 'spo2': 98,
                'carotid_r': 82, 'carotid_l': 25, 'cea_cta_status': 'concordant',
                'ecg': 'normal', 'med_hold_agreed': True, 'decolonization_ready': True,
                'ppi_started': True, 'anesth_done': True, 'cta_disc': True, 'blood_group': 'O (I) Rh (+)',
            },
        )
        ev_no_wt = evaluate(p, today=SIM_TODAY)
        self.assertIsNone(ev_no_wt['crcl'])
        self.assertTrue(ev_no_wt['has_blocking_unknown_state'])
        self.assertEqual(ev_no_wt['clearance_seal']['code'], 'pending')

        resp_pt = self.client.get(reverse('preop-patient', args=[p.pk]) + '?tab=doc')
        self.assertContains(resp_pt, 'შეფასება არასრულია — უცნობი მონაცემები')
        self.assertContains(resp_pt, 'WF_UNKNOWN_STATE_FAILSAFE')
        resp_pp = self.client.get(reverse('preop-passport', args=[p.pk]))
        self.assertContains(resp_pp, 'შეფასება არასრულია — უცნობი მონაცემები')

        # Adding weight_kg + labs_date computes CrCl, clears blocking unknown state, and unlocks GO!
        p.studies['weight_kg'] = 80
        p.studies['labs_date'] = SIM_TODAY.isoformat()
        p.save(update_fields=['studies'])
        ev_ok = evaluate(p, today=SIM_TODAY)
        self.assertIsNotNone(ev_ok['crcl'])
        self.assertFalse(ev_ok['has_blocking_unknown_state'])
        self.assertEqual(ev_ok['unknown_state_flags'], [])
        self.assertEqual(ev_ok['clearance_seal']['code'], 'go')


class PreopChangelogAndPassportVersionTests(TestCase):
    """Track A6: Verify '/preop/changelog/' timeline and Page 2 A4 Passport version colophon."""

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.doc = User.objects.create_user(
            username='cl_doc', password='ClDocPassword!1',
            first_name='დავით', last_name='ექიმი', is_staff=True,
        )

    def test_changelog_page_and_passport_protocol_version_footer(self):
        url = reverse('preop-changelog')
        self.assertEqual(self.client.get(url).status_code, 302)

        self.client.force_login(self.doc)
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'ცვლილებების ჟურნალი')
        self.assertContains(resp, f'v{REGISTRY_VERSION}')
        self.assertContains(resp, 'v1.0.0')

        p = PreopPatient.objects.create(
            patient_name='პასპორტ ვერსიაძე', age=68, sex='M',
            pathway=PreopPatient.Pathway.AAA_INFRA,
            op_subtype=PreopPatient.OpSubtype.EVAR,
            questionnaire={'filled': True, 'mets_ok': True},
            studies={'aaa_mm': 58, 'hb': 138, 'creat': 86, 'inr': 1.0, 'ecg': 'normal'},
        )
        pp = self.client.get(reverse('preop-passport', args=[p.pk]))
        self.assertEqual(pp.status_code, 200)
        self.assertContains(pp, f'კლინიკური პროტოკოლი v{REGISTRY_VERSION}')
        self.assertContains(pp, REGISTRY_UPDATED_ON)



