"""Track A1/A3 Clinical Rule Registry integrity and 100% emission coverage tests."""
from django.test import SimpleTestCase

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
