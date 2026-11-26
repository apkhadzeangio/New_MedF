"""Track A4: Automated tests for all 40 synthetic clinical vignettes and '/preop/vignettes/' view."""
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from ..models import PreopRuleSignoff
from ..preop import build_med_schedule
from ..preop.registry import match_rule_for_reflex, match_rule_for_text
from ..preop.vignettes import CATEGORY_LABELS, VIGNETTES, VIGNETTES_BY_ID, evaluate_all_vignettes


class ClinicalVignettesExecutionTests(SimpleTestCase):
    """Guarantee all 40 clinical vignettes pass 100% of their clinical assertions and registry mapping."""

    def test_exact_40_balanced_synthetic_vignettes_all_pass(self):
        self.assertEqual(len(VIGNETTES), 40)
        self.assertEqual(len(VIGNETTES_BY_ID), 40)

        by_cat = {}
        for v in VIGNETTES:
            self.assertIn(v.category, CATEGORY_LABELS)
            by_cat[v.category] = by_cat.get(v.category, 0) + 1
        self.assertEqual(by_cat, {'aaa': 7, 'cea': 6, 'pad': 8, 'venous': 5, 'pharm': 14})

        run = evaluate_all_vignettes()
        failures = []
        for r in run['results']:
            if not r['passed']:
                v = r['vignette']
                for f in r['failures']:
                    failures.append(f'{v.id}: {f["label"]} (actual: {f["actual"]})')
        self.assertEqual(failures, [], 'Clinical vignette assertion failures:\n' + '\n'.join(failures))
        self.assertEqual(run['passed_count'], 40)
        self.assertEqual(run['failed_count'], 0)

    def test_all_40_vignettes_emissions_covered_by_rule_registry(self):
        unmatched = []
        run = evaluate_all_vignettes()
        for r in run['results']:
            vid = r['vignette'].id
            ev = r['ev']
            for bucket in ('red_flags', 'amber_flags', 'dos_warnings'):
                for msg in ev.get(bucket) or ():
                    if match_rule_for_text(msg) is None:
                        unmatched.append(f'{vid}.{bucket}: {msg}')
            for ro in ev.get('reflex_orders') or ():
                if match_rule_for_reflex(ro.get('key'), f"{ro.get('trigger', '')} {ro.get('action', '')}") is None:
                    unmatched.append(f'{vid}.reflex[{ro.get("key")}]')
            meds = build_med_schedule(r['patient'], ev['q'], ev['s'], ev['egfr'], crcl=ev['crcl'])
            for row in meds:
                if match_rule_for_text(f"{row.get('drug', '')} {row.get('rule', '')}", domain_hint='meds') is None:
                    unmatched.append(f'{vid}.med[{row.get("drug")}]: {row.get("rule")}')
        self.assertEqual(unmatched, [], 'Unmatched vignette emissions:\n' + '\n'.join(unmatched[:20]))


class PreopVignettesViewTests(TestCase):
    """Verify '/preop/vignettes/' authentication, filtering, and clinician sign-off."""

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.doc = User.objects.create_user(
            username='vig_doc', password='VigDocPassword!1',
            first_name='მარიამ', last_name='ექიმი', is_staff=True,
        )

    def test_vignettes_view_filters_and_signoff_persistence(self):
        url = reverse('preop-vignettes')
        self.assertEqual(self.client.get(url).status_code, 302)

        self.client.force_login(self.doc)
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '40 კლინიკური ვინიეტა')
        self.assertContains(resp, 'V01_AAA_FEMALE_50MM')
        self.assertContains(resp, 'V40_INVERTED_BP_AND_SHOCK')

        resp_cea = self.client.get(url + '?cat=cea')
        self.assertContains(resp_cea, 'V10_CEA_OCCLUSION_100_STOP')
        self.assertNotContains(resp_cea, 'V01_AAA_FEMALE_50MM')

        post_resp = self.client.post(url, {
            'vignette_id': 'V36_GLUCOSE_40_MGDL_HYPOGLYCEMIA',
            'verdict': 'approved',
            'note': 'გადამოწმებულია — 40 mg/dL სწორად გადადის 2.2 მმოლ/ლ-ში',
            'ret_cat': 'pharm',
        })
        self.assertEqual(post_resp.status_code, 302)
        self.assertIn('#vig-V36_GLUCOSE_40_MGDL_HYPOGLYCEMIA', post_resp['Location'])
        so = PreopRuleSignoff.objects.get(item_kind='vignette', item_id='V36_GLUCOSE_40_MGDL_HYPOGLYCEMIA')
        self.assertEqual(so.verdict, PreopRuleSignoff.Verdict.APPROVED)
        self.assertEqual(so.signed_by, self.doc)
