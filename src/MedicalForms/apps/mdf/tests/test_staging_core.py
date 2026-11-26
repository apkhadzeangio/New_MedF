"""Tests for GLASS/WIfI/PLAN tables, schema v2, staging pages, and vector artery map."""
import json

from django.test import TestCase
from django.urls import reverse

from .helpers import PrivateMediaTest


class StagingTablesTests(TestCase):
    """GLASS / WIfI / PLAN against the guideline (Conte et al. 2019, PMC author manuscript)."""

    def test_wifi_stage_table_3_5(self):
        from ..staging import WIFI_STAGE
        self.assertEqual(len(WIFI_STAGE), 64)
        cases = {(0, 0, 0): 1, (0, 0, 1): 1, (1, 1, 0): 1, (0, 0, 2): 2, (0, 3, 0): 2, (2, 0, 1): 2,
                 (0, 0, 3): 3, (1, 3, 1): 3, (3, 0, 1): 3, (2, 1, 0): 3, (2, 2, 1): 4,   # the paper's example: WIfI 221 -> 4
                 (2, 3, 0): 4, (3, 1, 0): 4, (3, 0, 2): 4, (1, 2, 2): 4,
                 (0, 1, 2): 3, (0, 2, 1): 2}                                               # the two cells the print leaves unclear
        for key, stage in cases.items():
            self.assertEqual(WIFI_STAGE[key], stage, key)
        for (w, i, f), stage in WIFI_STAGE.items():            # never lower when any grade rises
            for nxt in ((w + 1, i, f), (w, i + 1, f), (w, i, f + 1)):
                if nxt in WIFI_STAGE:
                    self.assertGreaterEqual(WIFI_STAGE[nxt], stage, ((w, i, f), nxt))

    def test_ischemia_table_3_3(self):
        from ..staging import grade_abi, grade_ap, grade_tp, suggest_ischemia
        self.assertEqual([grade_abi(v) for v in (0.8, 0.79, 0.6, 0.59, 0.4, 0.39)], [0, 1, 1, 2, 2, 3])
        self.assertEqual([grade_ap(v) for v in (101, 100, 70, 69, 50, 49)], [0, 1, 1, 2, 2, 3])
        self.assertEqual([grade_tp(v) for v in (60, 59, 40, 39, 30, 29)], [0, 1, 1, 2, 2, 3])
        self.assertEqual(suggest_ischemia(abi=0.95, ap=114, tp=25), (3, 'TP'))      # TP decides
        self.assertEqual(suggest_ischemia(abi=0.55, ap=66), (2, 'ABI/AP'))         # the worse of the two
        self.assertEqual(suggest_ischemia(abi=1.5, ap=180), (0, 'AP'))             # incompressible ABI
        self.assertEqual(suggest_ischemia(), (None, ''))

    def test_glass_table_5_3_and_calcification(self):
        from ..staging import GLASS_MATRIX, compute
        self.assertEqual(GLASS_MATRIX[0], (None, 'I', 'I', 'II', 'III'))
        self.assertEqual(GLASS_MATRIX[2], ('I', 'II', 'II', 'II', 'III'))
        self.assertEqual(GLASS_MATRIX[3], ('II', 'II', 'II', 'III', 'III'))
        self.assertEqual(set(GLASS_MATRIX[4]), {'III'})
        r = compute({'fp': 2, 'ip': 2, 'fp_calc': True})
        self.assertEqual((r['fp_final'], r['ip_final'], r['glass']), (3, 2, 'II'))
        self.assertEqual(compute({'fp': 4, 'ip': 3, 'fp_calc': True, 'ip_calc': True})['fp_final'], 4)   # max 4
        self.assertIsNone(compute({'fp': 0, 'ip': 0})['glass'])

    def test_plan_fig_6_4_and_benefit_fig_6_3(self):
        from ..staging import compute
        base = {'risk': 'avg', 'vein': 'yes'}
        def plan(fp, ip, w, i, fi, **kw):
            return compute({**base, 'fp': fp, 'ip': ip, 'w': w, 'i': i, 'fi': fi, **kw})
        r = plan(1, 0, 1, 2, 0)                                  # GLASS I, WIfI stage 2
        self.assertEqual((r['glass'], r['wifi'], r['plan']), ('I', 2, 'endo'))
        r = plan(4, 4, 2, 2, 1)                                  # GLASS III, WIfI 4
        self.assertEqual((r['glass'], r['wifi'], r['plan'], r['benefit']), ('III', 4, 'open', 4))
        self.assertEqual(plan(3, 3, 1, 2, 1)['plan'], 'open')    # GLASS III, stage 3
        self.assertEqual(plan(4, 0, 0, 2, 0)['plan'], 'unsure')  # GLASS III, stage 2
        self.assertEqual(plan(2, 2, 1, 2, 1)['plan'], 'unsure')  # GLASS II, stage 3
        self.assertEqual(plan(2, 2, 0, 2, 0)['plan'], 'endo')    # GLASS II, stage 2
        self.assertEqual(plan(4, 4, 0, 0, 0)['plan'], 'none')    # WIfI stage 1
        self.assertEqual(plan(4, 4, 0, 0, 0)['benefit'], 1)
        self.assertEqual(plan(2, 2, 0, 2, 0)['benefit'], 2)      # I2, stage 2
        self.assertEqual(plan(2, 2, 3, 3, 3, unsalvageable=True)['plan'], 'unsalvageable')
        self.assertIn('6.41', plan(4, 4, 2, 2, 1, vein='no')['plan_label'])
        self.assertIn('6.33', plan(4, 4, 2, 2, 1, risk='high')['plan_label'])
        self.assertIn('6.37', plan(4, 4, 2, 2, 1, risk='high')['plan_label'])
        self.assertIn('6.34', plan(1, 0, 1, 2, 0, risk='high')['plan_label'])
        self.assertTrue(any('P2' in n for n in plan(1, 0, 1, 2, 0, pedal='P2')['notes']))
        self.assertTrue(any('Inflow' in n for n in plan(1, 0, 1, 2, 0, ai='I')['notes']))

    def test_ischemia_from_doppler_and_clean(self):
        from ..staging import clean, compute, doppler_measurements
        study = {'indices': {'R': {'abi_pt': 0.55, 'abi_dp': 0.56}}, 'pressures': {'R': {'pt': 66, 'dp': 68, 'toe': 39}}}
        m = doppler_measurements(study, 'R')
        self.assertEqual(m, {'abi': 0.56, 'ap': 68, 'tp': 39})
        r = compute({'w': 1, 'fi': 0, 'fp': 1, 'ip': 1}, m)
        self.assertEqual((r['i'], r['i_auto'], r['i_from'], r['wifi']), (2, True, 'TP', 2))
        limb = clean({'segments': {'sfa2': {'s': 'oc', 'ca': True, 'x': 1}, 'bogus': {'s': 'oc'}, 'pop1': {'s': 'n'}},
                      'fp': '7', 'ip': '2', 'w': 'a', 'risk': 'weird', 'tap': 'pt', 'abi': '0,6'})
        self.assertEqual(limb['v'], 2)
        self.assertEqual(limb['segments'], {'sfa2': {'stenosis': 'occlusion', 'calc': 3}})
        self.assertEqual((limb['fp'], limb['ip'], limb['w'], limb['risk'], limb['tap'], limb['abi']),
                         (None, 2, None, 'avg', 'pt', 0.6))


class StagingSchemaV2Tests(TestCase):
    """Schema v2: 26 segments, lossless v1 -> v2 upgrade, validation, and shared infrarenal aorta."""

    def test_26_segments_and_anatomical_paths(self):
        from ..staging import SEGMENT_KEYS, SEGMENTS, map_context, segment_path
        self.assertEqual(len(SEGMENTS), 26)
        for key in ('aorta', 'cia', 'iia', 'eia', 'cfa', 'pfa', 'sfa1', 'pop1', 'pop2', 'pop3',
                    'tpt', 'at1', 'pt1', 'per1', 'dp', 'mpa', 'lpa', 'arch'):
            self.assertIn(key, SEGMENT_KEYS)
        self.assertNotIn('plantar', SEGMENT_KEYS)
        self.assertEqual(segment_path('cfa', 'pop3'), ['cfa', 'sfa1', 'sfa2', 'sfa3', 'pop1', 'pop2', 'pop3'])
        self.assertEqual(segment_path('pop3', 'lpa'), ['pop3', 'tpt', 'pt1', 'pt2', 'pt3', 'lpa'])
        self.assertEqual(segment_path('contralateral_cfa', 'cfa'), ['cfa'])
        right = map_context({}, 'R')
        left = map_context({}, 'L')
        self.assertEqual(len(right['segments']), 26)
        self.assertEqual(len(left['segments']), 26)

    def test_v1_upgrade_is_lossless_and_idempotent(self):
        import copy

        from ..staging import clean, compute, map_context, upgrade_limb
        v1 = {
            'segments': {
                'cia': {'s': 'st', 'rx': True},
                'eia': {'rx': True},                    # connected to cia across iia in SEGMENTS
                'sfa2': {'s': 'oc', 'ca': True, 'rx': True},
                'sfa3': {'s': 'oc', 'rx': True},
                'cfa': {'by': True},
                'plantar': {'s': 'mild', 'by': True},   # legacy plantar -> lpa
                'pop2': {'s': 'st'},
            },
            'fp': 3, 'ip': 2, 'fp_calc': True, 'pedal': 'P1', 'tap': 'pt',
            'ai': 'I', 'cfa': 'A', 'w': 2, 'i': 2, 'fi': 1, 'risk': 'avg', 'vein': 'yes',
        }
        snapshot = copy.deepcopy(v1)
        up = upgrade_limb(v1)
        self.assertEqual(v1, snapshot)                  # never mutates caller's dict
        self.assertEqual(up['v'], 2)
        self.assertEqual(up['segments'], {
            'cia': {'stenosis': 'severe'},
            'sfa2': {'stenosis': 'occlusion', 'calc': 3},
            'sfa3': {'stenosis': 'occlusion'},
            'lpa': {'stenosis': 'mild'},
            'pop2': {'stenosis': 'severe'},
        })
        self.assertEqual(up['interventions'], [
            {'id': 'iv1', 'type': 'stent', 'subtype': 'unspecified', 'segments': ['cia', 'eia'],
             'performed_on': '', 'status': 'isr_1', 'note': ''},
            {'id': 'iv2', 'type': 'stent', 'subtype': 'unspecified', 'segments': ['sfa2', 'sfa3'],
             'performed_on': '', 'status': 'occluded', 'note': ''},
            {'id': 'iv3', 'type': 'bypass', 'conduit_group': 'unspecified', 'conduit': '', 'cuff': '',
             'proximal': 'cfa', 'distal': 'lpa', 'performed_on': '', 'status': 'patent', 'site': '', 'note': ''},
        ])
        cleaned = clean(v1)
        self.assertEqual(clean(cleaned), cleaned)       # idempotent
        self.assertEqual(compute(cleaned), compute(v1)) # clinical staging unchanged
        by_key = {s['key']: s for s in map_context(cleaned['segments'], 'R', cleaned['interventions'])['segments']}
        self.assertEqual(by_key['sfa2']['css'], 'sg sg-oc sg-ca sg-rx sg-by')
        self.assertEqual(by_key['eia']['css'], 'sg sg-n sg-rx')
        self.assertEqual(by_key['lpa']['css'], 'sg sg-mild sg-by')

    def test_v2_segment_and_intervention_validation(self):
        import datetime

        from ..staging import clean, clean_interventions, clean_partial_date, clean_segments
        today = datetime.date(2026, 11, 24)
        self.assertEqual(clean_partial_date('2021', today=today), '2021')
        self.assertEqual(clean_partial_date('2026-11', today=today), '2026-11')
        self.assertEqual(clean_partial_date('2026-11-24', today=today), '2026-11-24')
        self.assertEqual(clean_partial_date('2026-12', today=today), '')        # future month
        self.assertEqual(clean_partial_date('2026-11-25', today=today), '')     # future day
        self.assertEqual(clean_partial_date('2024-02-30', today=today), '')     # invalid calendar date
        self.assertEqual(clean_partial_date('1920', today=today), '')

        segs = clean_segments({
            'aorta': {'stenosis': 'moderate', 'length_cm': '4,26', 'calc': 4, 'morph': 'eccentric',
                      'flush': True, 'reconstitution': 'yes', 'aneurysm': True},
            'sfa1': {'stenosis': 'occlusion', 'length_cm': 12, 'calc': 0, 'flush': True,
                     'reconstitution': 'delayed', 'coral': True, 'thrombus': True},
            'cia': {'stenosis': 'none', 'aneurysm': True, 'length_cm': 3.5, 'small_caliber': True, 'morph': 'concentric'},
            'pop3': {'stenosis': 'severe', 'calc': True, 'trifurcation': True, 'ulcerated': True},
            'eia': {'stenosis': 'none', 'length_cm': 8.0, 'flush': True},       # normal with stale CTO/length -> dropped
        })
        # flush/reconstitution stripped on non-CTO aorta; length rounded to 1 decimal
        self.assertEqual(segs['aorta'], {'stenosis': 'moderate', 'length_cm': 4.3, 'calc': 4,
                                         'morph': 'eccentric', 'aneurysm': True})
        self.assertEqual(segs['sfa1'], {'stenosis': 'occlusion', 'length_cm': 12.0, 'flush': True,
                                        'reconstitution': 'delayed', 'coral': True, 'thrombus': True})
        self.assertEqual(segs['cia'], {'length_cm': 3.5, 'aneurysm': True, 'small_caliber': True})
        self.assertEqual(segs['pop3'], {'stenosis': 'severe', 'trifurcation': True, 'ulcerated': True})
        self.assertNotIn('eia', segs)

        ivs = clean_interventions([
            {'type': 'bypass', 'conduit': 'gsv_reversed', 'cuff': 'miller', 'proximal': 'cfa', 'distal': 'pt3',
             'performed_on': '2023-05', 'status': 'stenosed', 'site': 'distal', 'note': '  ანასტომოზი  '},
            {'type': 'bypass', 'conduit_group': 'prosthetic', 'conduit': 'ptfe_ringed',
             'proximal': 'contralateral_cfa', 'distal': 'cfa', 'status': 'patent', 'site': 'distal'},
            {'type': 'stent', 'subtype': 'des', 'segments': ['sfa3', 'sfa2', 'sfa3'], 'status': 'isr_2'},
            {'type': 'angioplasty', 'subtype': 'dcb', 'segments': ['at2'], 'status': 'restenosed'},
            {'type': 'endarterectomy', 'subtype': 'patch_bovine', 'segments': ['cfa', 'pfa'], 'status': 'patent'},
            {'type': 'amputation', 'subtype': 'toes', 'status': 'healed', 'performed_on': '2024'},
            {'type': 'thrombectomy', 'subtype': 'fogarty', 'segments': ['pop1'], 'status': 'reoccluded'},
            {'type': 'bypass', 'proximal': 'cfa', 'distal': 'contralateral_cfa'},   # invalid distal -> skipped
            {'type': 'stent', 'segments': []},                                       # empty segments -> skipped
            {'type': 'amputation', 'subtype': 'unknown'},                            # invalid subtype -> skipped
        ], today=today)
        self.assertEqual(len(ivs), 7)
        self.assertEqual((ivs[0]['conduit_group'], ivs[0]['cuff'], ivs[0]['site'], ivs[0]['note']),
                         ('vein', 'miller', 'distal', 'ანასტომოზი'))
        self.assertEqual(ivs[1]['site'], '')                                    # site cleared when status != stenosed
        self.assertEqual(ivs[2]['segments'], ['sfa2', 'sfa3'])                  # deduplicated & proximal->distal
        self.assertEqual(ivs[6]['status'], 'occluded')                          # reoccluded normalized to occluded

        limb = clean({'fp': True, 'wound_angiosome': 'medial_plantar'}, today=today)
        self.assertIsNone(limb['fp'])                                           # booleans rejected as integers
        self.assertEqual(limb['wound_angiosome'], 'medial_plantar')

    def test_lumen_sync_dates_and_bypass_note(self):
        import datetime

        from ..staging import clean, clean_partial_date, compute, format_partial_date, interventions_view
        today = datetime.date(2026, 11, 24)
        self.assertEqual(clean_partial_date('06.2023', today=today), '2023-06')
        self.assertEqual(clean_partial_date('5.6.2023', today=today), '2023-06-05')
        self.assertEqual(clean_partial_date('05/2022', today=today), '2022-05')
        self.assertEqual(format_partial_date('2023-06-05'), '05.06.2023')
        self.assertEqual(format_partial_date('2023-06'), '06.2023')
        self.assertEqual(format_partial_date('2023'), '2023')

        limb = clean({
            'segments': {
                'cfa': {'stenosis': 'occlusion', 'calc': 3, 'flush': True},
                'at1': {'stenosis': 'occlusion', 'reconstitution': 'yes'},
            },
            'interventions': [
                # Occluded stent -> marks sfa2 & sfa3 occluded automatically
                {'type': 'stent', 'subtype': 'des', 'segments': ['sfa2', 'sfa3'], 'status': 'occluded'},
                # Restenosed DCB -> downgrades at1 from occlusion to severe and clears CTO flags
                {'type': 'angioplasty', 'subtype': 'dcb', 'segments': ['at1'], 'status': 'restenosed'},
                # Patent patch endarterectomy -> clears cfa occlusion while preserving calc=3
                {'type': 'endarterectomy', 'subtype': 'patch_bovine', 'segments': ['cfa'], 'status': 'patent'},
                # Newer 2024 patent thrombectomy entered BEFORE older 2021 occluded stent -> 2024 wins
                {'type': 'thrombectomy', 'subtype': 'fogarty', 'segments': ['pop1'],
                 'performed_on': '06.2024', 'status': 'patent'},
                {'type': 'stent', 'subtype': 'bms', 'segments': ['pop1'],
                 'performed_on': '2021', 'status': 'occluded'},
            ],
        }, today=today)
        self.assertEqual(limb['segments']['sfa2'], {'stenosis': 'occlusion'})
        self.assertEqual(limb['segments']['sfa3'], {'stenosis': 'occlusion'})
        self.assertEqual(limb['segments']['at1'], {'stenosis': 'severe'})
        self.assertEqual(limb['segments']['cfa'], {'calc': 3})
        self.assertNotIn('pop1', limb['segments'])                              # patent 2024 thrombectomy beat 2021 stent

        # Functioning bypass triggers GLASS reminder note; occluded bypass does not
        r_pat = compute({'fp': 3, 'ip': 1, 'interventions': [
            {'type': 'bypass', 'conduit': 'gsv_reversed', 'proximal': 'cfa', 'distal': 'pop3', 'status': 'patent'},
        ]})
        self.assertTrue(any('ფუნქციონირებს შუნტი' in n for n in r_pat['notes']))
        r_occ = compute({'fp': 3, 'ip': 1, 'interventions': [
            {'type': 'bypass', 'conduit': 'ptfe', 'proximal': 'cfa', 'distal': 'pop1', 'status': 'occluded'},
        ]})
        self.assertFalse(any('ფუნქციონირებს შუნტი' in n for n in r_occ['notes']))

        rows = interventions_view([
            {'type': 'bypass', 'conduit': 'ptfe_ringed', 'cuff': 'miller', 'proximal': 'cfa', 'distal': 'pt3',
             'performed_on': '2022-06', 'status': 'stenosed', 'site': 'distal', 'note': '75%'},
            {'type': 'amputation', 'subtype': 'tma', 'performed_on': '2024-03-10', 'status': 'nonhealing'},
        ])
        self.assertEqual((rows[0]['title'], rows[0]['date_display'], rows[0]['tone']),
                         ('შუნტირება: CFA → PT დისტ.', '06.2022', 'warn'))
        self.assertIn('Miller-ის მანჟეტი', rows[0]['detail'])
        self.assertIn('დისტალური ანასტომოზი', rows[0]['status_label'])
        self.assertEqual((rows[1]['title'], rows[1]['date_display'], rows[1]['tone']),
                         ('ტრანსმეტატარზული (TMA)', '10.03.2024', 'bad'))


class StagingPageTests(PrivateMediaTest):

    def test_page_preview_save_and_show(self):
        case = self.make_case(side='R')
        url = reverse('case-staging', args=[case.pk, 'R'])
        page = self.client.get(url)
        self.assertContains(page, 'data-seg="aorta"')
        self.assertContains(page, 'data-seg="sfa2"')
        self.assertContains(page, 'data-seg="pop3"')
        self.assertContains(page, 'data-seg="mpa"')
        self.assertContains(page, 'id="st-iv-editor"')
        self.assertNotContains(page, 'data-flag="rx"')
        self.assertNotContains(page, 'data-flag="by"')
        self.assertContains(page, 'FP 4')
        self.assertEqual(self.client.get(reverse('case-staging', args=[case.pk, 'X'])).status_code, 404)
        data = {'segments': json.dumps({'sfa2': {'s': 'oc'}, 'pop1': {'s': 'st', 'ca': True}}),
                'fp': '3', 'ip': '1', 'pedal': 'P0', 'tap': 'at', 'w': '2', 'i': '2', 'fi': '1',
                'risk': 'avg', 'vein': 'yes', 'cfa': 'A'}
        preview = self.client.post(url, {**data, 'preview': '1'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest').json()
        self.assertIn('id="st-res"', preview['html'])
        self.assertIn('plan-unsure', preview['html'])               # FP3 + IP1 = GLASS II; WIfI 221 = 4 -> indeterminate (Fig 6.4)
        case.refresh_from_db()
        self.assertEqual(case.staging, {})                          # a preview saves nothing
        self.client.post(url, data)
        case.refresh_from_db()
        self.assertEqual(case.staging['R']['v'], 2)
        self.assertEqual(case.staging['R']['segments'],
                         {'sfa2': {'stenosis': 'occlusion'}, 'pop1': {'stenosis': 'severe', 'calc': 3}})
        detail = self.client.get(reverse('case', args=[case.pk]))
        self.assertContains(detail, 'sg sg-oc')
        self.assertContains(detail, 'GLASS')
        self.assertContains(self.client.get(reverse('cases-present'), {'ids': case.pk}), 'pz-stage-slide')

    def test_shared_aorta_syncs_across_limbs_and_v1_interventions_persist(self):
        from ..cases import staging_views
        case = self.make_case(side='B')
        r_url = reverse('case-staging', args=[case.pk, 'R'])
        l_url = reverse('case-staging', args=[case.pk, 'L'])

        # 1. Saving R with infrarenal aorta disease does not create a phantom L limb,
        #    but opening L pre-populates the shared aorta automatically.
        self.client.post(r_url, {
            'segments': json.dumps({'aorta': {'stenosis': 'occlusion', 'calc': 4, 'flush': True},
                                    'sfa1': {'stenosis': 'severe'}}),
            'fp': '2', 'ip': '0', 'cfa': 'A',
        })
        case.refresh_from_db()
        self.assertEqual(list(case.staging.keys()), ['R'])
        self.assertEqual([v['side'] for v in staging_views(case)], ['R'])

        l_page = self.client.get(l_url)
        self.assertEqual(l_page.context['segments_json'],
                         {'aorta': {'stenosis': 'occlusion', 'calc': 4, 'flush': True}})

        # 2. Updating aorta on L syncs back onto R while preserving R's limb-specific segments.
        self.client.post(l_url, {
            'segments': json.dumps({'aorta': {'stenosis': 'moderate', 'calc': 2}, 'pt1': {'stenosis': 'occlusion'}}),
            'fp': '0', 'ip': '3', 'cfa': 'A',
        })
        case.refresh_from_db()
        self.assertEqual(case.staging['R']['segments'],
                         {'aorta': {'stenosis': 'moderate', 'calc': 2}, 'sfa1': {'stenosis': 'severe'}})
        self.assertEqual(case.staging['L']['segments'],
                         {'aorta': {'stenosis': 'moderate', 'calc': 2}, 'pt1': {'stenosis': 'occlusion'}})

        # 3. Clearing aorta on R clears it on L too.
        self.client.post(r_url, {'segments': json.dumps({'sfa1': {'stenosis': 'severe'}}), 'cfa': 'A'})
        case.refresh_from_db()
        self.assertNotIn('aorta', case.staging['R']['segments'])
        self.assertNotIn('aorta', case.staging['L']['segments'])
        self.assertEqual(case.staging['L']['segments'], {'pt1': {'stenosis': 'occlusion'}})

        # 4. Existing v1 record with rx/by keeps migrated interventions when re-saved without interventions field.
        v1_case = self.make_case(side='R', staging={
            'R': {'segments': {'sfa2': {'s': 'oc', 'rx': True}, 'cfa': {'by': True}, 'pop1': {'by': True}}, 'fp': 3, 'ip': 1}
        })
        self.client.post(reverse('case-staging', args=[v1_case.pk, 'R']),
                         {'segments': json.dumps({'sfa2': {'stenosis': 'occlusion'}}), 'fp': '3', 'ip': '1', 'cfa': 'A'})
        v1_case.refresh_from_db()
        self.assertEqual(len(v1_case.staging['R']['interventions']), 2)

    def test_interventions_crud_summary_and_presentation(self):
        case = self.make_case(side='R', past_history='კორონარული შუნტირება 2019')
        url = reverse('case-staging', args=[case.pk, 'R'])
        interventions = [
            {'id': 'iv1', 'type': 'bypass', 'conduit_group': 'vein', 'conduit': 'gsv_reversed',
             'proximal': 'cfa', 'distal': 'pop3', 'performed_on': '06.2022',
             'status': 'stenosed', 'site': 'distal', 'note': 'ანასტომოზის 75% სტენოზი'},
            {'id': 'iv2', 'type': 'stent', 'subtype': 'des', 'segments': ['sfa2', 'sfa3'],
             'performed_on': '2021', 'status': 'isr_2', 'note': ''},
            {'id': 'iv3', 'type': 'angioplasty', 'subtype': 'dcb', 'segments': ['at2'],
             'performed_on': '2023-09', 'status': 'patent', 'note': ''},
            {'id': 'iv4', 'type': 'endarterectomy', 'subtype': 'patch_bovine', 'segments': ['cfa', 'pfa'],
             'performed_on': '06.2022', 'status': 'patent', 'note': ''},
            {'id': 'iv5', 'type': 'amputation', 'subtype': 'toes', 'performed_on': '12.10.2024',
             'status': 'healed', 'note': 'I თითი'},
            {'id': 'iv6', 'type': 'thrombectomy', 'subtype': 'fogarty', 'segments': ['pop1'],
             'performed_on': '2024-01', 'status': 'occluded', 'note': ''},
        ]
        payload = {
            'segments': json.dumps({}),
            'interventions': json.dumps(interventions),
            'fp': '3', 'ip': '1', 'pedal': 'P0', 'tap': 'at',
            'w': '2', 'i': '2', 'fi': '0', 'risk': 'avg', 'vein': 'yes', 'cfa': 'A',
        }
        # Live preview includes the functioning-bypass GLASS reminder note
        preview = self.client.post(url, {**payload, 'preview': '1'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest').json()
        self.assertIn('ფუნქციონირებს შუნტი', preview['html'])

        self.client.post(url, payload)
        case.refresh_from_db()
        self.assertEqual(case.past_history, 'კორონარული შუნტირება 2019')     # card free-text untouched
        saved = case.staging['R']
        self.assertEqual(len(saved['interventions']), 6)
        # Stent ISR-2 and occluded thrombectomy automatically synced onto native segment lumen
        self.assertEqual(saved['segments']['sfa2']['stenosis'], 'severe')
        self.assertEqual(saved['segments']['sfa3']['stenosis'], 'severe')
        self.assertEqual(saved['segments']['pop1']['stenosis'], 'occlusion')

        # Shown on both Case detail page and Conference Presentation slide
        for page in (self.client.get(reverse('case', args=[case.pk])),
                     self.client.get(reverse('cases-present'), {'ids': case.pk})):
            self.assertContains(page, 'წარსული ჩარევები')
            self.assertContains(page, 'შუნტირება: CFA → P3')
            self.assertContains(page, '06.2022')
            self.assertContains(page, 'Tosaka II')
            self.assertContains(page, 'ქსენოპერიკარდიუმის პატჩით')
            self.assertContains(page, 'თით(ებ)ის ამპუტაცია')
            self.assertContains(page, 'Fogarty')

        # Deleting interventions leaves only remaining items
        self.client.post(url, {**payload, 'interventions': json.dumps([interventions[4]])})
        case.refresh_from_db()
        self.assertEqual([iv['id'] for iv in case.staging['R']['interventions']], ['iv5'])


class StagingVectorMapTests(PrivateMediaTest):
    """T3: pure-vector surgical anatomy, 3-layer tubular vessels, pathology, grafts, and mirroring."""

    def test_vector_layers_pathology_grafts_and_left_leg_mirroring(self):
        from ..staging import map_context
        segments = {
            'cia': {'stenosis': 'moderate', 'length_cm': 4.0, 'calc': 2, 'morph': 'eccentric', 'aneurysm': True},
            'sfa1': {'stenosis': 'occlusion', 'calc': 4, 'flush': True, 'coral': True},
            'sfa2': {'stenosis': 'occlusion', 'reconstitution': 'yes', 'thrombus': True},
            'pop2': {'stenosis': 'severe', 'length_cm': 3.0, 'morph': 'concentric', 'ulcerated': True},
        }
        interventions = [
            {'type': 'bypass', 'conduit_group': 'vein', 'conduit': 'gsv_reversed',
             'proximal': 'cfa', 'distal': 'pop3', 'status': 'patent'},
            {'type': 'bypass', 'conduit_group': 'prosthetic', 'conduit': 'ptfe_ringed', 'cuff': 'miller',
             'proximal': 'contralateral_cfa', 'distal': 'cfa', 'status': 'stenosed', 'site': 'distal'},
            {'type': 'stent', 'subtype': 'covered', 'segments': ['eia'], 'status': 'fractured'},
            {'type': 'endarterectomy', 'subtype': 'patch_vein', 'segments': ['cfa'], 'status': 'patent'},
            {'type': 'angioplasty', 'subtype': 'dcb', 'segments': ['at1'], 'status': 'patent'},
            {'type': 'amputation', 'subtype': 'tma', 'status': 'healed'},
        ]
        right = map_context(segments, 'R', interventions)
        left = map_context(segments, 'L', interventions)

        # 26 adventitial tubes, plaque/CTO beds, collateral arcade, unilateral + bilateral PACSS plates
        self.assertEqual(len(right['adventitia']), 26)
        self.assertEqual(len(right['plaque_beds']), 4)
        self.assertTrue(len(right['collaterals']) >= 1)
        self.assertEqual(len(right['calc_plates']), 3)          # PACSS 2 (1 wall) + PACSS 4 (2 walls)
        self.assertTrue(len(right['coral_nodules']) >= 1)
        self.assertEqual(len(right['thrombi']), 3)

        # Interventions: covered fractured stent, patch + sutures, balloon marks, 2 bypasses, TMA line
        self.assertEqual(len(right['iv']['stents']), 1)
        self.assertTrue(bool(right['iv']['stents'][0]['sleeve']))
        self.assertIsNotNone(right['iv']['stents'][0]['fracture'])
        self.assertEqual(len(right['iv']['patches']), 1)
        self.assertTrue(len(right['iv']['patches'][0]['ticks']) >= 2)
        self.assertEqual(len(right['iv']['bypasses']), 2)
        self.assertTrue(len(right['iv']['bypasses'][1]['rings']) > 0)
        self.assertIsNotNone(right['iv']['bypasses'][1]['miller'])
        self.assertEqual(len(right['iv']['amputations']), 1)

        # Left leg mirrors coordinates around axis=300 and flips start<->end anchors without flipping text
        self.assertEqual(right['axis'], 300)
        for r_lbl, l_lbl in zip(right['labels'], left['labels'], strict=True):
            self.assertEqual(r_lbl['text'], l_lbl['text'])
            self.assertAlmostEqual(r_lbl['x'] + l_lbl['x'], 300.0, places=1)
            self.assertTrue(-58 <= l_lbl['x'] <= 358, l_lbl)
            if r_lbl['anchor'] in ('start', 'end'):
                self.assertNotEqual(r_lbl['anchor'], l_lbl['anchor'])

    def test_rendered_svg_has_no_raster_image_and_updates_in_preview(self):
        case = self.make_case(side='B')
        url_l = reverse('case-staging', args=[case.pk, 'L'])
        page = self.client.get(url_l)
        self.assertNotContains(page, 'leg-anterior.webp')       # pure vector, no raster image
        self.assertContains(page, 'class="am-limb"')
        self.assertContains(page, 'class="am-bone"')
        self.assertContains(page, 'ადუქტორული ხვრელი')
        self.assertContains(page, 'translate(300,0) scale(-1,1)')

        preview = self.client.post(url_l, {
            'segments': json.dumps({'sfa1': {'stenosis': 'occlusion', 'calc': 4}}),
            'interventions': json.dumps([{'type': 'bypass', 'conduit_group': 'vein', 'conduit': 'gsv_reversed',
                                          'proximal': 'cfa', 'distal': 'pop3', 'status': 'patent'}]),
            'cfa': 'A', 'preview': '1',
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest').json()
        self.assertIn('map_html', preview)
        self.assertIn('am-cto-core', preview['map_html'])
        self.assertIn('am-coll', preview['map_html'])
        self.assertIn('am-bypass vein', preview['map_html'])
