"""Tests for foot angiosomes, map suggestions, DSA presentation/print, and Georgian protocols."""
import io
import json
import shutil
from unittest import skipUnless

from django.conf import settings
from django.contrib.auth.models import User
from django.core.cache import cache, caches
from django.core.management import call_command
from django.http import QueryDict
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse

from ..models import DutyNote, Medicine, NivForm, PrescriptionTemplate, StaffProfile
from ..utils import validate_take_medicine
from ..views import NivDocumentView
from .helpers import PrivateMediaTest


class StagingFootAngiosomesTests(PrivateMediaTest):
    """T4: lateral-oblique pedal arch inset, 6 Attinger angiosomes, and symmetric 4-zone zoom."""

    def test_angiosome_direct_indirect_and_arch_evaluation(self):
        from ..staging import angiosome_status, compute

        # Unspecified or 'none' -> no angiosome status
        self.assertIsNone(angiosome_status({'wound_angiosome': ''}))
        self.assertIsNone(angiosome_status({'wound_angiosome': 'none', 'tap': 'pt'}))
        self.assertNotIn('angiosome', compute({'wound_angiosome': 'none', 'tap': 'pt', 'fp': 2, 'ip': 1}))

        # Selected angiosome before TAP is chosen -> pending hint naming the source artery
        pending = angiosome_status({'wound_angiosome': 'medial_plantar'})
        self.assertEqual((pending['status'], pending['source_tap'], pending['direct']), ('pending', 'pt', None))

        # 1. Direct revascularization: PT -> medial plantar with patent MPA
        direct = compute({'wound_angiosome': 'medial_plantar', 'tap': 'pt', 'pedal': 'P0'})['angiosome']
        self.assertEqual((direct['status'], direct['direct'], direct['tone']), ('direct', True, 'ok'))

        # 2. Matching tibial TAP, but terminal foot branch (MPA) is occluded -> collateral/arch dependent
        coll = compute({
            'wound_angiosome': 'medial_plantar', 'tap': 'pt',
            'segments': {'mpa': {'stenosis': 'occlusion'}},
        })['angiosome']
        self.assertEqual((coll['status'], coll['direct'], coll['tone']), ('collateral', False, 'warn'))

        # 3. Indirect revascularization via intact pedal arch (AT -> medial plantar, arch patent, P0)
        ind_ok = compute({'wound_angiosome': 'medial_plantar', 'tap': 'at', 'pedal': 'P0'})['angiosome']
        self.assertEqual((ind_ok['status'], ind_ok['direct'], ind_ok['tone']), ('indirect_arch', False, 'warn'))

        # 4. Indirect revascularization with diseased/absent pedal arch (arch occluded OR P1/P2)
        ind_bad = compute({
            'wound_angiosome': 'lateral_heel', 'tap': 'at',
            'segments': {'arch': {'stenosis': 'occlusion'}},
        })['angiosome']
        self.assertEqual((ind_bad['status'], ind_bad['direct'], ind_bad['tone']), ('indirect_poor', False, 'bad'))
        ind_p1 = compute({'wound_angiosome': 'dorsal', 'tap': 'pt', 'pedal': 'P1'})['angiosome']
        self.assertEqual(ind_p1['status'], 'indirect_poor')

    def test_foot_inset_zoom_zones_and_page_integration(self):
        from ..staging import foot_context, zoom_zones

        # Every zoom viewBox is centered on axis=300 so R and mirrored L legs stay centered
        zones = zoom_zones()
        self.assertEqual([z[0] for z in zones], ['all', 'inflow', 'fp', 'btk'])
        for _key, _label, vbox in zones:
            vx, _vy, vw, _vh = (float(x) for x in vbox.split())
            self.assertAlmostEqual(2.0 * vx + vw, 300.0, places=3)

        # Foot context: 7 pedal/ankle segments, peroneal communicating branches, Miller hood & TMA line
        fm_pat = foot_context(
            {'mpa': {'stenosis': 'severe', 'calc': 3}},
            'R',
            [{'type': 'bypass', 'conduit_group': 'prosthetic', 'conduit': 'ptfe', 'cuff': 'miller',
              'proximal': 'cfa', 'distal': 'dp', 'status': 'patent'},
             {'type': 'amputation', 'subtype': 'tma', 'status': 'nonhealing'}],
            'medial_plantar',
        )
        self.assertEqual([s['key'] for s in fm_pat['segments']], ['at3', 'pt3', 'per3', 'dp', 'mpa', 'lpa', 'arch'])
        self.assertEqual(len(fm_pat['peroneal_comm']), 2)
        self.assertIsNotNone(fm_pat['angiosome'])
        self.assertEqual(len(fm_pat['distal_hoods']), 1)
        self.assertTrue(fm_pat['distal_hoods'][0]['miller'])
        self.assertEqual(len(fm_pat['amputations']), 1)
        self.assertFalse(fm_pat['amputations'][0]['healed'])

        # Occluding per3 hides peroneal communicating branches
        fm_occ = foot_context({'per3': {'stenosis': 'occlusion'}}, 'R')
        self.assertEqual(fm_occ['peroneal_comm'], [])

        # Angiography page has interactive foot map without WIfI/Doppler inputs; WIfI page has wound_angiosome
        case = self.make_case(side='R')
        url = reverse('case-staging', args=[case.pk, 'R'])
        page = self.client.get(url)
        self.assertContains(page, 'class="am am-foot am-edit"')
        self.assertContains(page, 'data-zoom="btk"')
        self.assertNotContains(page, 'name="wound_angiosome"')
        wifi_page = self.client.get(reverse('case-wifi', args=[case.pk, 'R']))
        self.assertContains(wifi_page, 'name="wound_angiosome"')

        payload = {
            'segments': json.dumps({'arch': {'stenosis': 'none'}, 'mpa': {'stenosis': 'moderate'}}),
            'interventions': json.dumps([]),
            'wound_angiosome': 'medial_plantar',
            'tap': 'pt', 'fp': '2', 'ip': '2', 'pedal': 'P0',
            'w': '2', 'i': '2', 'fi': '1', 'risk': 'avg', 'vein': 'yes', 'cfa': 'A',
        }
        preview = self.client.post(url, {**payload, 'preview': '1'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest').json()
        self.assertIn('foot_html', preview)
        self.assertIn('am-angio-zone', preview['foot_html'])
        self.assertIn('პირდაპირი (ანგიოსომაზე მიმართული) რევასკულარიზაცია', preview['html'])

        self.client.post(url, payload)
        case.refresh_from_db()
        self.assertEqual(case.staging['R']['wound_angiosome'], 'medial_plantar')

        for rendered in (self.client.get(reverse('case', args=[case.pk])),
                         self.client.get(reverse('cases-present'), {'ids': case.pk})):
            self.assertContains(rendered, 'am am-foot')
            self.assertContains(rendered, 'am-angio-zone')
            self.assertContains(rendered, 'პირდაპირი (ანგიოსომაზე მიმართული) რევასკულარიზაცია')


class StagingSuggestionAndInspectorTests(PrivateMediaTest):
    """T5: suggest_from_map() (GVG Figs 5.1-5.3, Table 5.2, SVS Runoff 1-19), inspector & manual override safety."""

    def test_suggest_from_map_inflow_fp_and_calc_boundaries(self):
        from ..staging import suggest_from_map

        empty = suggest_from_map({})
        self.assertFalse(empty['has_map'])
        self.assertEqual((empty['svs_runoff'], empty['foot_runoff'], empty['runoff_count']), (1.0, 1.0, 3))

        # Inflow Table 5.2
        self.assertEqual(suggest_from_map({'segments': {'cia': {'stenosis': 'occlusion'}}})['ai'], 'I')
        self.assertEqual(suggest_from_map({'segments': {'cia': {'stenosis': 'occlusion'},
                                                        'eia': {'stenosis': 'occlusion'}}})['ai'], 'II')
        self.assertEqual(suggest_from_map({'segments': {'aorta': {'stenosis': 'occlusion'}}})['ai'], 'II')
        self.assertEqual(suggest_from_map({'segments': {'cia': {'aneurysm': True}}})['ai'], 'II')
        self.assertEqual(suggest_from_map({'segments': {'eia': {'small_caliber': True}}})['ai'], 'II')
        self.assertEqual(suggest_from_map({'interventions': [
            {'type': 'stent', 'subtype': 'bms', 'segments': ['eia'], 'status': 'isr_2'},
        ]})['ai'], 'II')
        self.assertEqual(suggest_from_map({'segments': {'cfa': {'stenosis': 'moderate'}}})['cfa'], 'B')

        # GLASS FP Fig 5.2 boundaries
        fp_of = lambda segs: suggest_from_map({'segments': segs})['fp']
        self.assertEqual(fp_of({'sfa2': {'stenosis': 'mild'}}), 0)
        self.assertEqual(fp_of({'sfa2': {'stenosis': 'severe', 'length_cm': 4.0}}), 1)
        self.assertEqual(fp_of({'sfa2': {'stenosis': 'occlusion', 'length_cm': 3.0}}), 1)
        self.assertEqual(fp_of({'sfa1': {'stenosis': 'occlusion', 'length_cm': 3.0, 'flush': True}}), 3)
        self.assertEqual(fp_of({'sfa1': {'stenosis': 'occlusion', 'length_cm': 2.0},
                                'sfa3': {'stenosis': 'occlusion', 'length_cm': 2.0}}), 2)
        self.assertEqual(fp_of({'sfa2': {'stenosis': 'occlusion', 'length_cm': 7.5}}), 2)
        self.assertEqual(fp_of({'sfa1': {'stenosis': 'severe', 'length_cm': 15.0}}), 2)
        self.assertEqual(fp_of({'sfa2': {'stenosis': 'occlusion', 'length_cm': 15.0}}), 3)
        self.assertEqual(fp_of({'sfa1': {'stenosis': 'severe', 'length_cm': 25.0}}), 3)
        self.assertEqual(fp_of({'sfa1': {'stenosis': 'occlusion', 'length_cm': 25.0}}), 4)
        self.assertEqual(fp_of({'pop2': {'stenosis': 'severe', 'length_cm': 1.5}}), 2)
        self.assertEqual(fp_of({'pop2': {'stenosis': 'severe', 'length_cm': 3.0}}), 3)
        self.assertEqual(fp_of({'pop2': {'stenosis': 'severe', 'length_cm': 6.0}}), 4)
        self.assertEqual(fp_of({'pop3': {'stenosis': 'severe', 'length_cm': 1.5, 'trifurcation': True}}), 4)
        self.assertEqual(fp_of({'pop1': {'stenosis': 'occlusion', 'length_cm': 1.5}}), 4)

        # PACSS >= 3 or coral -> +1 calcification modifier
        self.assertFalse(suggest_from_map({'segments': {'sfa2': {'stenosis': 'severe', 'calc': 2}}})['fp_calc'])
        self.assertTrue(suggest_from_map({'segments': {'sfa2': {'stenosis': 'severe', 'calc': 3}}})['fp_calc'])
        self.assertTrue(suggest_from_map({'segments': {'pop2': {'coral': True}}})['fp_calc'])

    def test_suggest_from_map_ip_tap_pedal_and_svs_runoff(self):
        from ..staging import suggest_from_map

        # TPT CTO makes PT & PER IP 4 while leaving AT IP 0 -> suggests AT and reflects TPT loss in SVS runoff
        s_tpt = suggest_from_map({'segments': {'tpt': {'stenosis': 'occlusion'}}})
        self.assertEqual(s_tpt['ip_by_tap']['at']['ip'], 0)
        self.assertEqual(s_tpt['ip_by_tap']['pt']['ip'], 4)
        self.assertEqual(s_tpt['ip_by_tap']['per']['ip'], 4)
        self.assertEqual(s_tpt['tap'], 'at')
        self.assertEqual((s_tpt['svs_runoff'], s_tpt['runoff_count'], s_tpt['estimated_length']), (6.0, 1, False))

        # Tibial focal <3 cm stenosis (IP 1), non-ostial focal CTO <3 cm (IP 2), ostial flush CTO <3 cm (IP 3)
        self.assertEqual(suggest_from_map({'segments': {'at2': {'stenosis': 'severe', 'length_cm': 2.0}}})
                         ['ip_by_tap']['at']['ip'], 1)
        self.assertEqual(suggest_from_map({'segments': {'at2': {'stenosis': 'occlusion', 'length_cm': 2.0}}})
                         ['ip_by_tap']['at']['ip'], 2)
        self.assertEqual(suggest_from_map({'segments': {'at1': {'stenosis': 'occlusion', 'length_cm': 2.0, 'flush': True}}})
                         ['ip_by_tap']['at']['ip'], 3)
        self.assertEqual(suggest_from_map({'segments': {'tpt': {'stenosis': 'severe', 'length_cm': 2.0}}})
                         ['ip_by_tap']['pt']['ip'], 2)

        # Wound angiosome breaks tie when AT and PT have equal IP grade
        s_tie = suggest_from_map({
            'segments': {'at2': {'stenosis': 'severe', 'length_cm': 2.0},
                         'pt2': {'stenosis': 'severe', 'length_cm': 2.0}},
            'wound_angiosome': 'medial_plantar',
        })
        self.assertEqual(s_tie['tap'], 'pt')
        self.assertEqual(s_tie['pedal'], 'P0')

        # When both AT and PT have severe IP >= 3 disease, patent peroneal becomes TAP
        self.assertEqual(suggest_from_map({
            'segments': {'at1': {'stenosis': 'occlusion'}, 'pt1': {'stenosis': 'occlusion'}},
        })['tap'], 'per')

        # Pedal P1 when arch is occluded; P2 when no artery crosses ankle into foot
        self.assertEqual(suggest_from_map({'segments': {'arch': {'stenosis': 'occlusion'}}})['pedal'], 'P1')
        self.assertEqual(suggest_from_map({'segments': {
            'dp': {'stenosis': 'occlusion'}, 'mpa': {'stenosis': 'occlusion'}, 'lpa': {'stenosis': 'occlusion'},
        }})['pedal'], 'P2')

        # Modified SVS Runoff (1.0 - 19.0) & Foot Score (1.0 - 10.0) (Stoner et al. 2016)
        worst = suggest_from_map({'segments': {
            'pop1': {'stenosis': 'occlusion'}, 'pop2': {'stenosis': 'occlusion'}, 'pop3': {'stenosis': 'occlusion'},
            'at1': {'stenosis': 'occlusion'}, 'at2': {'stenosis': 'occlusion'},
            'pt1': {'stenosis': 'occlusion'}, 'pt2': {'stenosis': 'occlusion'},
            'per1': {'stenosis': 'occlusion'}, 'per2': {'stenosis': 'occlusion'},
            'dp': {'stenosis': 'occlusion'}, 'mpa': {'stenosis': 'occlusion'}, 'lpa': {'stenosis': 'occlusion'},
        }})
        self.assertEqual((worst['svs_runoff'], worst['foot_runoff'], worst['runoff_count']), (19.0, 10.0, 0))

        mixed = suggest_from_map({'segments': {
            'pop1': {'stenosis': 'occlusion'},       # 4 / 12 cm <= 0.5 -> 2.5 * 3 + 1 = 8.5
            'at2': {'stenosis': 'moderate'},         # + 2.0
            'pt2': {'stenosis': 'mild'},             # + 1.0 (PER = 0.0)
        }})
        self.assertEqual((mixed['svs_runoff'], mixed['svs_runoff_text'], mixed['runoff_count']), (11.5, '11.5', 3))

    def test_manual_selections_never_silently_overwritten_and_page_renders_inspector(self):
        case = self.make_case(side='R')
        url = reverse('case-staging', args=[case.pk, 'R'])
        page = self.client.get(url)
        self.assertContains(page, 'id="st-seg-inspector"')
        self.assertContains(page, '+ მორფოლოგია')
        self.assertContains(page, 'data-inspect="1"')

        # Map points to FP 4 & TAP AT, while surgeon explicitly selected FP 1, IP 1, TAP PT -> GLASS I preserved!
        payload = {
            'segments': json.dumps({'pop2': {'stenosis': 'occlusion'}, 'tpt': {'stenosis': 'occlusion'}}),
            'interventions': json.dumps([]),
            'tap': 'pt', 'fp': '1', 'ip': '1', 'pedal': 'P0',
            'w': '1', 'i': '2', 'fi': '0', 'risk': 'avg', 'vein': 'yes', 'cfa': 'A',
        }
        preview = self.client.post(url, {**payload, 'preview': '1'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest').json()
        self.assertIn('glass-I', preview['html'])
        self.assertIn('id="st-apply-sug"', preview['html'])
        self.assertIn('რუკიდან შევსება', preview['html'])
        self.assertIn('SVS ქულა:', preview['html'])

        self.client.post(url, payload)
        case.refresh_from_db()
        saved = case.staging['R']
        self.assertEqual((saved['fp'], saved['ip'], saved['tap']), (1, 1, 'pt'))

        # Read-only detail & presentation show SVS runoff summary, never the edit-only suggestion button
        for rendered in (self.client.get(reverse('case', args=[case.pk])),
                         self.client.get(reverse('cases-present'), {'ids': case.pk})):
            self.assertContains(rendered, 'SVS ქულა:')
            self.assertNotContains(rendered, 'id="st-apply-sug"')


class StagingDsaPresentAndPrintTests(PrivateMediaTest):
    """T6: Anatomical <-> DSA toggle, dedicated per-limb presentation slides, D on all slides, and A4 print."""

    def test_dsa_toggle_and_print_controls_on_staging_and_detail(self):
        case = self.make_case(side='R')
        unstaged_detail = self.client.get(reverse('case', args=[case.pk]))
        self.assertContains(unstaged_detail, 'cs-print-doc')
        self.assertContains(unstaged_detail, 'id="cs-print-btn"')
        self.assertNotContains(unstaged_detail, 'data-map-view="dsa"')

        staging_page = self.client.get(reverse('case-staging', args=[case.pk, 'R']))
        self.assertContains(staging_page, 'cs-print-doc')
        self.assertContains(staging_page, 'data-map-view="anat"')
        self.assertContains(staging_page, 'data-map-view="dsa"')

        self.client.post(reverse('case-staging', args=[case.pk, 'R']), {
            'segments': json.dumps({'sfa2': {'stenosis': 'occlusion'}}),
            'fp': '2', 'ip': '0', 'pedal': 'P0', 'tap': 'at', 'cfa': 'A',
        })
        staged_detail = self.client.get(reverse('case', args=[case.pk]))
        self.assertContains(staged_detail, 'data-map-view="dsa"')

    def test_bilateral_presentation_renders_separate_limb_slides_and_decision_footers(self):
        from ..models import DopplerStudy
        from ..staging import save_limb

        st_map = {}
        st_map = save_limb(st_map, 'R', {'segments': {'sfa1': {'stenosis': 'occlusion'}}, 'fp': 3, 'ip': 1, 'tap': 'at'})
        st_map = save_limb(st_map, 'L', {'segments': {'pt2': {'stenosis': 'severe'}}, 'fp': 0, 'ip': 2, 'tap': 'pt'})
        case = self.make_case(side='B', staging=st_map)
        DopplerStudy.objects.create(
            case=case, performed_on='2026-11-20', created_by=self.user,
            data={'indices': {'R': {'abi_dp': 0.52}, 'L': {'abi_pt': 0.68}}, 'pressures': {}, 'waveforms': {}},
        )

        pres = self.client.get(reverse('cases-present'), {'ids': case.pk})
        html = pres.content.decode()
        self.assertContains(pres, 'id="pz-dsa"')
        self.assertContains(pres, 'data-side="R"')
        self.assertContains(pres, 'data-side="L"')
        # Two separate staging slides (one per limb)
        self.assertEqual(html.count('class="pz-slide pz-stage-slide"'), 2)
        # Decision footer present on main slide + 2 staging slides + 1 Doppler slide = 4 footers
        self.assertEqual(html.count('class="pz-decide-toggle"'), 4)
        self.assertEqual(html.count(f'data-decision="{reverse("case-decision", args=[case.pk])}"'), 4)


class StagingSplitWorkflowAndProtocolTests(PrivateMediaTest):
    """Split entry windows (Angiography vs WIfI/PLAN vs Doppler), mandatory presentation strip, PLAN in proposal, and protocol generator."""

    def test_three_separate_windows_preserve_each_others_fields(self):
        case = self.make_case(side='R', proposal='ბარძაყ-წვივის აუტოვენური შუნტირება')
        angio_url = reverse('case-staging', args=[case.pk, 'R'])
        wifi_url = reverse('case-wifi', args=[case.pk, 'R'])
        doppler_url = reverse('case-doppler', args=[case.pk])

        # 1. Angiography window has zero Doppler and zero WIfI input fields
        angio_page = self.client.get(angio_url)
        self.assertContains(angio_page, 'id="st-proto-text"')
        self.assertContains(angio_page, 'id="st-copy-proto"')
        for forbidden in ('name="abi"', 'name="tbi"', 'name="ap"', 'name="tp"',
                          'name="w"', 'name="i"', 'name="fi"', 'name="wound_angiosome"'):
            self.assertNotContains(angio_page, forbidden)

        # 2. WIfI window has W, I, fI, angiosome, risk & vein, and zero Doppler or segment inputs
        wifi_page = self.client.get(wifi_url)
        for expected in ('name="w"', 'name="i"', 'name="fi"', 'name="wound_angiosome"', 'name="risk"', 'name="vein"'):
            self.assertContains(wifi_page, expected)
        for forbidden in ('name="abi"', 'name="tbi"', 'name="segments"', 'name="fp"'):
            self.assertNotContains(wifi_page, forbidden)
        self.assertEqual(self.client.get(reverse('case-wifi', args=[case.pk, 'X'])).status_code, 404)

        # 3. Doppler window GET + manual POST saves ABI, TBI, AP, and Toe pressure without creating a fake map slide
        doppler_page = self.client.get(doppler_url)
        self.assertContains(doppler_page, 'name="abi_R"')
        self.assertContains(doppler_page, 'name="tbi_R"')
        self.assertContains(doppler_page, 'name="ap_R"')
        self.assertContains(doppler_page, 'name="tp_R"')

        r_dop = self.client.post(doppler_url, {
            'action': 'manual',
            'abi_R': '0,48', 'tbi_R': '0,24', 'ap_R': '58', 'tp_R': '24',
            'abi_L': '', 'tbi_L': '', 'ap_L': '', 'tp_L': '',
        })
        self.assertRedirects(r_dop, reverse('case', args=[case.pk]) + '#doppler', fetch_redirect_response=False)
        case.refresh_from_db()
        self.assertEqual(
            (case.staging['R']['abi'], case.staging['R']['tbi'], case.staging['R']['ap'], case.staging['R']['tp']),
            (0.48, 0.24, 58.0, 24.0),
        )
        self.assertNotIn('L', case.staging)     # untouched side stays absent
        # Standalone Doppler numbers do not create a blank artery-map slide
        pres_after_dop = self.client.get(reverse('cases-present'), {'ids': case.pk})
        self.assertNotContains(pres_after_dop, 'class="pz-slide pz-stage-slide"')
        self.assertContains(pres_after_dop, '0.48')
        self.assertContains(pres_after_dop, '0.24')
        self.assertContains(pres_after_dop, '24')

        # 4. WIfI window saves W2, auto-ischemia (from TP=24 -> I3), fI1, risk=avg, vein=yes -> WIfI stage 4
        r_wifi = self.client.post(wifi_url, {
            'w': '2', 'i': '', 'fi': '1', 'wound_angiosome': 'medial_plantar',
            'risk': 'avg', 'vein': 'yes', 'note': 'მედიალური ქუსლისა და ძირის წყლული',
        })
        self.assertRedirects(r_wifi, reverse('case', args=[case.pk]) + '#staging', fetch_redirect_response=False)
        case.refresh_from_db()
        self.assertEqual((case.staging['R']['w'], case.staging['R']['fi'], case.staging['R']['abi'], case.staging['R']['tbi']),
                         (2, 1, 0.48, 0.24))

        # 5. Angiography window (_form='angio') saves segments + GLASS III while preserving WIfI & Doppler!
        r_angio = self.client.post(angio_url, {
            '_form': 'angio',
            'segments': json.dumps({'sfa1': {'stenosis': 'occlusion', 'calc': 4, 'flush': True},
                                    'sfa2': {'stenosis': 'occlusion'},
                                    'sfa3': {'stenosis': 'occlusion', 'reconstitution': 'yes'}}),
            'interventions': json.dumps([]),
            'tap': 'pt', 'fp': '4', 'fp_calc': 'on', 'ip': '2', 'pedal': 'P0', 'ai': '', 'cfa': 'A',
        })
        self.assertRedirects(r_angio, reverse('case', args=[case.pk]) + '#staging', fetch_redirect_response=False)
        case.refresh_from_db()
        limb = case.staging['R']
        self.assertEqual((limb['fp'], limb['ip'], limb['w'], limb['fi'], limb['wound_angiosome']),
                         (4, 2, 2, 1, 'medial_plantar'))
        self.assertEqual((limb['abi'], limb['tbi'], limb['ap'], limb['tp']), (0.48, 0.24, 58.0, 24.0))

        # 6. Conference presentation Slide 1 shows WIfI, GLASS, ABI, TBI, Toe pressure AND PLAN inside 'შეთავაზება'
        pres = self.client.get(reverse('cases-present'), {'ids': case.pk})
        self.assertContains(pres, 'class="pz-vitals"')
        self.assertContains(pres, '<small>WIfI</small>')
        self.assertContains(pres, '<small>GLASS</small>')
        self.assertContains(pres, '<small>ABI</small>')
        self.assertContains(pres, '<small>TBI</small>')
        self.assertContains(pres, '<small>Toe pressure</small>')
        self.assertContains(pres, '0.48')
        self.assertContains(pres, '0.24')
        # Doctor's own proposal AND automatic PLAN recommendation both appear inside 'შეთავაზება'
        self.assertContains(pres, 'ბარძაყ-წვივის აუტოვენური შუნტირება')
        self.assertContains(pres, 'PLAN ალგორითმი')
        self.assertContains(pres, 'ღია შუნტირება — საშუალო რისკი, ვარგისი ვენა')

        # Detail card & Case edit form also surface PLAN recommendation and 1-click insert button
        detail = self.client.get(reverse('case', args=[case.pk]))
        self.assertContains(detail, 'PLAN ალგორითმი')
        self.assertContains(detail, 'ღია შუნტირება — საშუალო რისკი, ვარგისი ვენა')
        edit_form = self.client.get(reverse('case-edit', args=[case.pk]))
        self.assertContains(edit_form, 'id="cs-insert-plan"')
        self.assertContains(edit_form, 'PLAN: ღია შუნტირება')

    def test_angiography_protocol_single_and_bilateral_generation(self):
        from ..staging import angiography_protocol, angiography_protocol_bilateral, save_limb

        # 1. Normal right limb
        normal_txt = angiography_protocol({}, 'R')
        self.assertIn('ქვედა კიდურის არტერიოგრაფია — მარჯვენა კიდური', normal_txt)
        self.assertIn('ინფრარენული აორტა: გამავალია', normal_txt)
        self.assertIn('ჰემოდინამიკურად მნიშვნელოვანი სტენოზისა და ოკლუზიის გარეშე', normal_txt)
        self.assertNotIn('პუნქცია', normal_txt)
        self.assertNotIn('კონტრასტ', normal_txt)

        # 2. Multi-level disease + prior bypass -> grouped anatomical prose + GLASS conclusion
        diseased_r = {
            'segments': {
                'aorta': {'stenosis': 'moderate', 'calc': 3},
                'sfa2': {'stenosis': 'occlusion', 'calc': 4},
                'sfa3': {'stenosis': 'occlusion', 'reconstitution': 'yes'},
                'at1': {'stenosis': 'severe', 'length_cm': 2.5},
                'arch': {'stenosis': 'occlusion'},
            },
            'interventions': [
                {'type': 'bypass', 'conduit_group': 'vein', 'conduit': 'gsv_reversed',
                 'proximal': 'cfa', 'distal': 'pop3', 'performed_on': '06.2023', 'status': 'stenosed', 'site': 'distal'},
            ],
            'fp': 3, 'ip': 1, 'pedal': 'P1', 'tap': 'pt',
        }
        r_txt = angiography_protocol(diseased_r, 'R')
        self.assertIn('ინფრარენული აორტა: 50–69%-იანი სტენოზი', r_txt)
        self.assertIn('შუა და დისტალურ მესამედში — ოკლუზია', r_txt)
        self.assertIn('დისტალური სეგმენტი აღდგება კოლატერალებით', r_txt)
        self.assertIn('ტერფის რკალი შეკრული არ არის', r_txt)
        self.assertIn('შუნტირება: CFA → P3', r_txt)
        self.assertIn('GLASS სტადია II (FP 3, IP 1; P1; TAP — უკანა წვივის არტერია (PT))', r_txt)

        # 3. Bilateral protocol & live AJAX preview on second leg
        st_map = save_limb({}, 'R', diseased_r)
        st_map = save_limb(st_map, 'L', {'segments': {'cia': {'stenosis': 'occlusion', 'flush': True}}, 'fp': 0, 'ip': 0})
        bi_txt = angiography_protocol_bilateral(st_map)
        self.assertIn('ქვედა კიდურების არტერიოგრაფია', bi_txt)
        self.assertEqual(bi_txt.count('• ინფრარენული აორტა:'), 1)
        self.assertIn('მარჯვენა ქვედა კიდური:', bi_txt)
        self.assertIn('მარცხენა ქვედა კიდური:', bi_txt)
        self.assertIn('საერთო თეძოს არტერია — სათავის (flush) ოკლუზია', bi_txt)

        case = self.make_case(side='B', staging=st_map)
        preview = self.client.post(
            reverse('case-staging', args=[case.pk, 'L']),
            {'_form': 'angio', 'segments': json.dumps({'pt2': {'stenosis': 'severe'}}), 'fp': '0', 'ip': '2', 'preview': '1'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        ).json()
        self.assertIn('უკანა წვივის არტერია: შუა მესამედში — 70–99%-იანი კრიტიკული სტენოზი', preview['protocol_text'])
        self.assertIn('მარჯვენა ქვედა კიდური:', preview['protocol_bilateral_text'])
        self.assertIn('უკანა წვივის არტერია: შუა მესამედში — 70–99%-იანი კრიტიკული სტენოზი', preview['protocol_bilateral_text'])
