"""Round-17 adversarial stress-test regression suite (v1.2.16).

Covers all Round-16 Fuzz, Combos, and Clinical/Registry/Plain-Georgian audit findings
using strictly synthetic test patients (no real patient data, BMP-only).
"""
import datetime
import re
import time
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from ..models import BoardPatient, PreopPatient, StaffProfile
from ..preop import build_med_schedule, evaluate, nurse_plan, nurse_ui
from ..preop.calculators import calc_auto_asa, normalize_glucose_mmol
from ..preop.meds_freetext import (
    _detect_freetext_allergies,
    _detect_freetext_high_risk_meds,
    _positive_clauses_text,
)
from ..preop.registry import REGISTRY_VERSION, RULES_BY_ID, STATUS_LOCAL, STATUS_VERIFIED
from ..preop.views.board import _build_handoff_tasks
from ..preop.views.patient import PreopPatientView

TODAY = datetime.date(2026, 11, 27)
OP_DATE = (TODAY + datetime.timedelta(days=10)).isoformat()
PREV_OP_DATE = (TODAY + datetime.timedelta(days=7)).isoformat()
LABS_DATE = (TODAY - datetime.timedelta(days=2)).isoformat()


def _mk(sub='open', *, age=66, sex='M', side='R', q=None, s=None, archived=False, cardio_decision=''):
    pw = PreopPatient.SUBTYPE_PATHWAY[sub]
    base_q = {'filled': True, 'meds_verified': True, 'no_allergies': True}
    if pw == PreopPatient.Pathway.VENOUS:
        base_q['ven_ceap'] = 'C2'
    if q:
        base_q.update(q)
    base_s = {
        'planned_surgery_date': OP_DATE, 'labs_date': LABS_DATE,
        'weight_kg': 76, 'height_cm': 174, 'bp_sys': 126, 'bp_dia': 76,
        'hr': 70, 'spo2': 98, 'hb': 138, 'wbc': 6.4, 'plt': 230,
        'inr': 1.0, 'fibrinogen': 3.1, 'creat': 84, 'creat_unit': 'umol',
        'k': 4.1, 'na': 140, 'glucose': 5.2, 'albumin': 42,
        'ecg': 'normal', 'cxr': 'normal', 'abi_r': 1.0, 'abi_l': 1.0,
        'birth_date': '1960-05-10',
    }
    if pw == PreopPatient.Pathway.VENOUS:
        base_s.update({'ven_duplex_status': 'deep_patent_reflux', 'ven_gsv_mm': 7, 'compression_ready': True, 'vte_prophylaxis_planned': True, 'anesth_done': True, 'anesthesia_plan': 'local'})
    if s:
        base_s.update(s)
    return PreopPatient(
        patient_name='სინთეზური R17', age=age, sex=sex, side=side,
        pathway=pw, op_subtype=sub, visit_date=TODAY, started_on=TODAY,
        questionnaire=base_q, studies=base_s, archived=archived,
        cardio_decision=cardio_decision,
    )


def _board_txt(ev):
    return ' '.join(
        t for _, t in _build_handoff_tasks(
            ev, ev['q'], ev['s'], ev['pathway'], set(ev['eff_allergy_chips']), ev['eff_renal'],
        )
    )


class PreopStressFixesR17EngineTests(TestCase):
    """Fuzz 16 F2-F5, Combos 16 S1/S2/S3, and Clin 16 Pillar 1-3 engine regressions."""

    def test_precision_boundaries_crashes_and_standalone_med_parity(self):
        # F2: Unrounded float precision preserves STOPs, ASRA blocks, ASA/RCRI, and ambiguous-unit blocks
        self.assertEqual(normalize_glucose_mmol(70.15), 3.89)
        self.assertTrue(evaluate(_mk('open', s={'dos_inr': '1.504'}), today=TODAY)['dos_hold_danger'])
        self.assertTrue(evaluate(_mk('bypass_clti', s={'anesthesia_plan': 'neuraxial', 'dos_inr': '1.404'}), today=TODAY)['neuraxial_hard_blocked'])
        self.assertTrue(evaluate(_mk('open', s={'dos_glucose': '3.896'}), today=TODAY)['dos_hold_danger'])
        self.assertTrue(evaluate(_mk('open', s={'dos_glucose': '70.15'}), today=TODAY)['dos_hold_danger'])
        self.assertTrue(evaluate(_mk('open', s={'dos_bp_sys': '89.6', 'dos_bp_dia': '60'}), today=TODAY)['dos_hold_danger'])
        self.assertEqual(evaluate(_mk('evla', s={'abi_r': '0.596'}), today=TODAY)['clearance_seal']['code'], 'stop')
        self.assertTrue(evaluate(_mk('bypass_clti', s={'anesthesia_plan': 'neuraxial', 'fibrinogen': '149.6'}), today=TODAY)['neuraxial_hard_blocked'])
        self.assertEqual(evaluate(_mk('open', s={'creat': '2.004', 'creat_unit': 'mgdl'}), today=TODAY)['rcri']['score'], 2)
        self.assertEqual(evaluate(_mk('open', s={'hb': '69.996'}), today=TODAY)['auto_asa'], 'IV')
        self.assertEqual(evaluate(_mk('open', s={'k': '2.996'}), today=TODAY)['auto_asa'], 'IV')
        for bad_s in ({'hb': '22.004'}, {'creat': '34.996', 'creat_unit': ''}, {'creat': '14.996', 'creat_unit': 'umol'}, {'plt': '2500.004'}, {'plt': '2999.996'}):
            ev_b = evaluate(_mk('evla', age=45, s=bad_s), today=TODAY)
            self.assertNotEqual(ev_b['clearance_seal']['code'], 'go', bad_s)
            self.assertFalse(ev_b['workup_ready'], bad_s)

        # F3: Standalone build_med_schedule parity (bp_sys=94.6, caller s_mod override, sanitized ev['s'])
        p_bp = _mk('phlebectomy', q={'med_dabigatran': True}, s={'bp_sys': '94.6', 'bp_dia': '60', 'anesthesia_plan': 'auto'})
        ev_bp = evaluate(p_bp, today=TODAY)
        st_bp = build_med_schedule(p_bp, ev_bp['q'], ev_bp['s'], ev_bp['egfr'], crcl=ev_bp['crcl'], today=TODAY, stop_blocked=(ev_bp['clearance_seal']['code'] == 'stop'))
        self.assertEqual(ev_bp['med_schedule'], st_bp)
        p_ov = _mk('phlebectomy', q={'med_dabigatran': True}, s={'dos_inr': 1.0, 'anesthesia_plan': 'auto'})
        s_mod = dict(p_ov.studies, dos_inr='abc')
        st_ov = build_med_schedule(p_ov, p_ov.questionnaire, s_mod, 80, crcl=80, today=TODAY)
        self.assertIn('48 საათით', st_ov[0]['rule'])

        # F4: Scientific-notation PLT '1e5000' and 10**5000 never crash evaluate()
        self.assertIsNotNone(evaluate(_mk('open', s={'plt': '1e5000'}), today=TODAY))
        self.assertIsNotNone(evaluate(_mk('open', s={'creat': 10**5000}), today=TODAY))

    def test_freetext_nlp_negation_brands_and_linear_50k_performance(self):
        # F5 + Fuzz 16 S3-01: Georgian/English double-neg, gareshe, non-prefix, SVO/SOV, brands
        med_cases = [
            ('ქსარელტო არაა შეწყვეტილი', {'doac'}),
            ('პლავიქსი არაა მოხსნილი', {'clopidogrel'}),
            ('ქსარელტო ვერ შეწყვიტა', {'doac'}),
            ('patient did not stop plavix', {'clopidogrel'}),
            ('never discontinued eliquis', {'doac'}),
            ('warfarin not yet discontinued', {'warfarin'}),
            ('ქსარელტო 20მგ ექიმის დანიშნულების გარეშე იღებს', {'doac'}),
            ('ვარფარინი INR კონტროლის გარეშე', {'warfarin'}),
            ('არა ფრაქციული ჰეპარინი', {'parenteral_oac'}),
            ('არა ვიტამინ K ანტიკოაგულანტი ქსარელტო 20მგ', {'doac'}),
            ('არ სვამს ვარფარინს სვამს ელიქვისს', {'doac'}),
            ('არ სვამს ვარფარინს ელიქვისს სვამს', {'doac'}),
            ('წამალს არ იღებს ელიქვისს იღებს', {'doac'}),
            ('პლავიქსი არ მოუხსნია და ასპირინი შეწყვიტა', {'clopidogrel'}),
            ('არანაირი ჩივილი პლავიქსს იღებს', {'clopidogrel'}),
            ('ვარფარინი შეწყვეტილია გადავიდა ელიქვისზე', {'doac'}),
            ('stopped warfarin switched to apixaban', {'doac'}),
            ('stopped aspirin or taking warfarin', {'warfarin'}),
            ('eliquis 5mg not taking aspirin', {'doac'}),
            ('eliquis 5mg and not taking aspirin', {'doac'}),
            ('პლავიკსი 75მგ', {'clopidogrel'}),
            ('პრადაკსა 150მგ', {'dabigatran'}),
            ('აპიკსაბანი 5მგ', {'doac'}),
            ('რივაროკსაბანი 20მგ', {'doac'}),
            ('პლავიქსი შეწყვიტა 1 თვის წინ', set()),
            ('ვარფარინი მოხსნა 2 კვირის წინ', set()),
        ]
        for txt, exp in med_cases:
            self.assertEqual(_detect_freetext_high_risk_meds(txt)['matched_keys'], exp, txt)

        for txt, exp in [
            ('პენიცილინზე ალერგია შოკის გარეშე', {'beta_lactam'}),
            ('იოდზე გამონაყარი შეშუპების გარეშე', {'iodine'}),
            ('ალერგია არ აქვს პენიცილინზე ლიდოკაინზე აქვს', {'lidocaine'}),
            ('no penicillin allergy or allergic to lidocaine', {'lidocaine'}),
        ]:
            self.assertEqual(_detect_freetext_allergies(txt), exp, txt)

        # No O(N^2) freeze on 50k worst-case strings
        for payload in (
            'no ' + ' and '.join(['a'] * 8000),
            ' და '.join(['ა'] * 9000) + ' არ აქვს',
            'ქსარელტო' + 'ა' * 50000,
        ):
            t0 = time.perf_counter()
            _positive_clauses_text(payload)
            self.assertLess(time.perf_counter() - t0, 0.35)

    def test_combos_s1_evla_warfarin_rivaroxaban_freetext_and_saturated_ckd(self):
        # S1-01 & S1-02: EVLA + warfarin + GI bleed / liver_3x / supratherapeutic / invalid dos_inr
        ev_gi = evaluate(_mk('evla', q={'med_warfarin': True, 'ulcer_active_symp': True}, s={'inr': 2.2, 'dos_inr': 2.8}), today=TODAY)
        self.assertFalse(ev_gi['is_evla_warfarin_only'])
        self.assertTrue(ev_gi['dos_hold_danger'])
        self.assertFalse(any('INR <= 3.0' in a for a in ev_gi['amber_flags']))
        ev_liv = evaluate(_mk('evla', q={'med_warfarin': True}, s={'inr': 2.5, 'liver_status': '3x'}), today=TODAY)
        self.assertFalse(any('INR <= 3.0' in a for a in ev_liv['amber_flags']))
        for s_ov in ({'inr': 3.5}, {'inr': 2.2, 'dos_inr': '99'}):
            ev_hi = evaluate(_mk('evla', q={'med_warfarin': True, 'allergy_chips': ['hit_heparin']}, s=s_ov), today=TODAY)
            self.assertNotIn('გრძელდება მოუხსნელად', _board_txt(ev_hi))

        # S1-03: Contraindicated rivaroxaban_2_5 without full OAC keeps stent DAPT & skips pregnancy LMWH
        ev_st = evaluate(_mk('bypass_clti', q={'med_chips': ['rivaroxaban_2_5'], 'recent_stent': True, 'med_aspirin': True, 'med_clopidogrel': True, 'neuro': True}), today=TODAY)
        self.assertIn('DAPT', ev_st['post_antithrombotic_label'])
        ev_pr = evaluate(_mk('bypass_clti', sex='F', age=35, q={'med_chips': ['rivaroxaban_2_5'], 'pregnant_possible': True, 'med_aspirin': True}), today=TODAY)
        self.assertNotIn('LMWH/UFH', ev_pr['post_antithrombotic_label'])

        # S1-04 & S1-05 & S3-01: Free-text OAC + PTA clop_intol, NSAIDs, double OAC, DTI labeling
        ev_ft_pta = evaluate(_mk('pta_clti', q={'allergy_chips': ['clopidogrel_intol'], 'other_meds': 'Eliquis 5mg'}), today=TODAY)
        self.assertIn('OAC + ასპირინი', ev_ft_pta['post_antithrombotic_label'])
        self.assertNotIn('ასპირინი + ტიკაგრელორი', ev_ft_pta['post_antithrombotic_label'])
        self.assertNotIn('რივაროქსაბანი 2.5', ev_ft_pta['post_antithrombotic_label'])
        ev_ns = evaluate(_mk('open', q={'allergy_chips': ['nsaid'], 'ulcer_active_symp': True, 'other_meds': 'Ibuprofen 400mg, Ketorolac 10mg'}, s={'creat': 350}), today=TODAY)
        self.assertTrue(_detect_freetext_high_risk_meds('Ibuprofen 400mg, Ketorolac 10mg')['has_ft_nsaid'])
        self.assertTrue(any(r.get('contraindicated') for r in ev_ns['med_schedule']))
        ev_dbl = evaluate(_mk('open', q={'med_warfarin': True, 'other_meds': 'Xarelto 20mg'}), today=TODAY)
        self.assertTrue(any('2 ან მეტი' in r or 'დუბლირებ' in r for r in ev_dbl['red_flags']))
        ev_arg = evaluate(_mk('open', q={'allergy_chips': ['hit_heparin'], 'other_meds': 'Argatroban IV'}, s={'liver_status': '3x'}), today=TODAY)
        arg_row = next(r for r in ev_arg['med_schedule'] if 'არგატრობან' in r['drug'])
        self.assertNotIn('LMWH', arg_row['drug'])
        self.assertTrue(arg_row.get('contraindicated'))

        # S1-06 & S3-04: Saturated severe CKD (age=130, creat=350) -> IV UFH, no gentamicin, numeric renal red flag
        ev_vka = evaluate(_mk('open', age=130, q={'med_warfarin': True, 'cad_chips': ['mech_valve']}, s={'creat': 350}), today=TODAY)
        self.assertNotIn('სავალდებულო LMWH ხიდით', ev_vka['med_schedule'][0]['rule'])
        self.assertFalse(any('(CrCl/eGFR მლ/წთ < 30)' in r for r in ev_vka['red_flags']))
        self.assertTrue(any(re.search(r'(CrCl|eGFR)[^<]*\d+.*მლ/წთ < 30', r) for r in ev_vka['red_flags']))
        ev_bl = evaluate(_mk('bypass_clti', age=130, q={'allergy_chips': ['beta_lactam']}, s={'creat': 350}), today=TODAY)
        self.assertNotIn('(+ გენტამიცინი', _board_txt(ev_bl))
        self.assertIsNotNone(evaluate(_mk('evar', age=130, q={'metformin': True}, s={'creat': 350}), today=TODAY)['max_contrast_ml'])

    def test_combos_s1_s2_pregnancy_stroke_bp_epidural_cardio_and_postpone(self):
        # S1-07: STOP-locked postponement NEVER returns green 'ok'
        p_sp = _mk('evla', sex='F', age=30, q={'ven_pregnant': True, 'med_doac': True}, s={'prev_planned_surgery_date': PREV_OP_DATE})
        imp = nurse_plan.detect_postponement_impacts(p_sp, evaluate(p_sp, today=TODAY), TODAY)['impacts']
        self.assertTrue(any(i['tone'] == 'danger' for i in imp))
        self.assertFalse(any(i['tone'] == 'ok' for i in imp))

        # S1-08: Stale cross-pathway urgency flags never bypass pregnancy STOP
        for sub, qx, sx in (
            ('pta_claud', {'pregnant_possible': True, 'aaa_pain': True}, {}),
            ('evar', {'pregnant_possible': True, 'cea_crescendo': True}, {}),
            ('cea_asymp', {'pregnant_possible': True, 'med_aspirin': True}, {'aaa_morphology': 'inflammatory_mycotic'}),
        ):
            self.assertEqual(evaluate(_mk(sub, sex='F', age=32, q=qx, s=sx), today=TODAY)['clearance_seal']['code'], 'stop')

        # S1-09 & S1-10: carotid_symp survives bypass->pta switch & venous; acute hs-cTn/echo not low cardiac risk
        p_mig = _mk('bypass_clti', q={'neuro': False, 'med_prasugrel': True}, s={'carotid_symp': True})
        s_m = dict(p_mig.studies)
        PreopPatientView._migrate_header_pathway_flags(p_mig, PreopPatient.Pathway.PAD_BYPASS, PreopPatient.Pathway.PAD_PTA, s_m, [])
        self.assertTrue(p_mig.questionnaire.get('neuro'))
        self.assertFalse(s_m.get('carotid_symp'))
        ev_ven_p = evaluate(_mk('phlebectomy', q={'neuro': False, 'med_prasugrel': True}, s={'carotid_symp': True}), today=TODAY)
        self.assertTrue(ev_ven_p['med_schedule'][0].get('contraindicated'))
        ev_cl = evaluate(_mk('phlebectomy', q={'neuro': False, 'cea_event_date': '2026-01-15', 'allergy_chips': ['nsaid'], 'med_clopidogrel': True}), today=TODAY)
        self.assertFalse(ev_cl['has_neuraxial_plan'])
        self.assertNotIn('ბოლო:', ev_cl['med_schedule'][0]['date_plan'])
        ev_ctn = evaluate(_mk('phlebectomy', q={'cad': False, 'med_aspirin': True}, s={'hs_ctn': 80}), today=TODAY)
        self.assertNotIn('დაბალი კარდიორისკისას', ev_ctn['med_schedule'][0]['rule'])

        # S1-11 & S1-12: Mech-valve GI bleed / HIT+liver_3x & explicit sapt in pregnancy+OAC+HIT
        ev_mv_gi = evaluate(_mk('open', q={'med_warfarin': True, 'cad_chips': ['mech_valve'], 'ulcer_active_symp': True}, s={'inr': 1.4}), today=TODAY)
        self.assertFalse(any('ჰეპარინის ხიდი' in r for r in ev_mv_gi['red_flags']))
        ev_mv_hl = evaluate(_mk('open', q={'med_warfarin': True, 'cad_chips': ['mech_valve'], 'allergy_chips': ['hit_heparin']}, s={'inr': 1.4, 'liver_status': '3x'}), today=TODAY)
        self.assertTrue(all('75' in r for r in ev_mv_hl['red_flags'] if 'არგატრობან' in r))
        ev_sp_p = evaluate(_mk('pta_clti', sex='F', age=32, q={'pregnant_possible': True, 'med_doac': True, 'allergy_chips': ['hit_heparin']}, s={'post_antithrombotic_plan': 'sapt'}), today=TODAY)
        self.assertIn('ორსულობა', ev_sp_p['post_antithrombotic_label'])
        self.assertIn('HIT', ev_sp_p['post_antithrombotic_label'])
        self.assertFalse(any('შეარჩიეთ OAC +' in r for r in ev_sp_p['red_flags']))

        # S2-01..S2-08 & S3-02: Board EVLA/preg, mycotic 56d wait, epidural modality, BP typo vs monotonicity, stroke/drugs
        self.assertIn('მოუხსნელად', _board_txt(evaluate(_mk('evla', q={'med_warfarin': True}, s={'inr': 2.2}), today=TODAY)))
        self.assertIn('~24 სთ', _board_txt(evaluate(_mk('evla', q={'med_doac': True}, s={'creat': 85}), today=TODAY)))
        self.assertIn('ორსულ', _board_txt(evaluate(_mk('open', sex='F', age=32, q={'pregnant_possible': True}, s={'creat': 340, 'weight_kg': 58}), today=TODAY)))
        op70 = (TODAY + datetime.timedelta(days=70)).isoformat()
        self.assertTrue(any('მოლოდინი 70 დღეა' in a for a in evaluate(_mk('open', s={'aaa_mm': 48, 'aaa_morphology': 'inflammatory_mycotic', 'planned_surgery_date': op70}), today=TODAY)['amber_flags']))
        ev_urg_p = evaluate(_mk('pta_clti', sex='F', age=32, q={'pregnant_possible': True}, s={'dos_bhcg_negative': False}), today=TODAY)
        self.assertFalse(any(u['key'] == 'reproductive_pregnancy_unverified' and u['blocking'] for u in ev_urg_p['unknown_state_flags']))
        self.assertFalse(any('გეგმური ჩარევის გადადება' in r for r in ev_urg_p['red_flags']))
        ev_cea_ep = evaluate(_mk('cea_asymp', q={'med_aspirin': True}, s={'anesthesia_plan': 'general', 'anesth_lines_chips': ['epidural']}), today=TODAY)
        self.assertTrue(ev_cea_ep['solo_neuraxial_anatomic_mismatch'])
        self.assertIn('ეპიდურულ', evaluate(_mk('bypass_clti', s={'anesthesia_plan': 'general', 'anesth_lines_chips': ['epidural']}), today=TODAY)['anesthetic_brief']['modality'])
        ev_bp_t = evaluate(_mk('evla', age=50, s={'bp_sys': 80, 'bp_dia': 120, 'bp_sys_l': 130, 'bp_dia_l': 80}), today=TODAY)
        self.assertFalse(ev_bp_t['severe_htn'])
        self.assertEqual(ev_bp_t['effective_asa'], 'ASA I')
        self.assertEqual(calc_auto_asa(_mk('evla'), {'filled': True}, {'bp_sys': 120, 'bp_dia': 80, 'bp_sys_l': 185, 'bp_dia_l': 190}), 'III')
        ev_2bp = evaluate(_mk('cea_asymp', s={'bp_sys': 80, 'bp_dia': 120, 'bp_sys_l': 90, 'bp_dia_l': 110}), today=TODAY)
        inv_reds = [r for r in ev_2bp['red_flags'] if 'ინვერტირებულია' in r or 'SBP ≤ DBP' in r]
        self.assertEqual(len(inv_reds), 1)
        self.assertIn('90/110', inv_reds[0])
        self.assertEqual(evaluate(_mk('evla', q={'cad': False, 'med_doac': True}), today=TODAY)['effective_asa'], 'ASA II')
        self.assertTrue(evaluate(_mk('bypass_clti', age=50, q={'dm': False, 'sglt2': True}), today=TODAY)['has_known_dm'])
        self.assertTrue(evaluate(_mk('evar', q={'med_doac': True, 'other_meds': 'Cordarone 200mg'}, s={'creat': 160}), today=TODAY)['needs_tsh'])
        ev_uf = evaluate(_mk('evla', sex='F', age=32, q={'filled': False, 'allergy_chips': ['hit_heparin'], 'ven_pregnant': True, 'cad_chips': ['mech_valve']}, s={'liver_status': '3x'}), today=TODAY)
        self.assertIn('ორსულ', ev_uf['surgical_brief']['heparin_plan'])
        self.assertIn('ღვიძლის ≥3x', ev_uf['surgical_brief']['heparin_plan'])

    def test_clin16_registry_honesty_vignettes_and_zero_jargon_leaks(self):
        # Pillar 1: Registry v1.2.16 honesty (0 UNVERIFIED in STATUS_VERIFIED) + live threshold reactivity
        self.assertEqual(REGISTRY_VERSION, '1.2.25')
        ver = [r for r in RULES_BY_ID.values() if r.status == STATUS_VERIFIED]
        loc = [r for r in RULES_BY_ID.values() if r.status == STATUS_LOCAL]
        self.assertEqual(len(ver), 13)
        self.assertEqual(len(loc), 70)
        for r in ver:
            self.assertNotIn('UNVERIFIED', f'{r.source} {r.section} {r.source_wording}'.upper(), r.id)
        mut_doac = dict(RULES_BY_ID['MED_DOAC_HOLD_INTERVALS'].value, crcl_low=40)
        with mock.patch('apps.mdf.preop.registry.rule_value', side_effect=lambda k: mut_doac if k == 'MED_DOAC_HOLD_INTERVALS' else RULES_BY_ID[k].value):
            ev_m = evaluate(_mk('open', q={'med_doac': True}, s={'creat': 175, 'weight_kg': 70}), today=TODAY)
            self.assertTrue(any(r.get('contraindicated') for r in ev_m['med_schedule']))

        # Pillar 2: V21 (aspirin + active GI bleed), V22 (tica+liver_3x / pras>=75), V41 (ft cilostazol+LVEF30)
        ev_v21 = evaluate(_mk('open', q={'med_aspirin': True, 'ulcer_active_symp': True}), today=TODAY)
        self.assertTrue(ev_v21['med_schedule'][0].get('acute_hold'))
        ev_v22 = evaluate(_mk('cea_asymp', age=78, q={'med_ticagrelor': True, 'med_prasugrel': True}, s={'liver_status': '3x'}), today=TODAY)
        self.assertTrue(any('ტიკაგრელორი' in r['drug'] and r.get('contraindicated') for r in ev_v22['med_schedule']))
        self.assertTrue(any('პრასუგრელი' in r['drug'] and '75' in r['rule'] for r in ev_v22['med_schedule']))
        ev_v41 = evaluate(_mk('bypass_claud', q={'other_meds': 'Pletal 100mg'}, s={'lvef': 30}), today=TODAY)
        self.assertTrue(any(r.get('contraindicated') for r in ev_v41['med_schedule']))

        # Pillar 3: Zero English clinical jargon leaks across nurse/patient plain-Georgian surfaces
        jargon_re = re.compile(
            r'\b(RCRI|ARISCAT|CAPRINI|GLASS|ESVS|ESC|ASRA|EHRA|NSAID|SGLT2|DOAC|VKA|LMWH|UFH|HIT|'
            r'CLTI|ABI|TBI|CrCl|eGFR|INR|HbA1c|LVEF|SBP|DBP|SpO2|CTA|DAPT|SAPT|VOYAGER|VRIII|PBM|CIED|VQI|GO)\b'
        )
        p_j = _mk('bypass_clti', age=76, q={'med_warfarin': True, 'med_clopidogrel': True, 'sglt2': True, 'glp1': True, 'allergy_chips': ['hit_heparin', 'nsaid'], 'cad_chips': ['mech_valve']}, s={'creat': 260, 'hb': 88, 'hba1c': 9.2, 'lvef': 32, 'prev_planned_surgery_date': PREV_OP_DATE})
        ev_j = evaluate(p_j, today=TODAY)
        surfaces = []
        surfaces.extend(m['label'] + ' ' + m.get('hint', '') for m in nurse_plan.build_plain_georgian_missing_items(p_j, ev_j, TODAY))
        ph = nurse_plan.build_phone_med_script(p_j, ev_j, TODAY)
        for k in ('stop_rows', 'caution_rows', 'keep_rows'):
            surfaces.extend(f"{r['drug']} {r['spoken']}" for r in ph.get(k, ()))
        gd = nurse_plan.build_patient_plain_explanation(p_j, ev_j, TODAY)
        for k in ('urgent_meds', 'stop_meds', 'keep_meds'):
            surfaces.extend(f"{r['drug']} {r['spoken']}" for r in gd.get(k, ()))
        surfaces.extend(b['label'] for b in nurse_ui.plain_risk_badges(ev_j))
        v_j = nurse_ui.get_nurse_readiness_verdict(p_j, ev_j)
        surfaces.append(v_j.get('detail', ''))
        surfaces.extend(i['text'] for i in nurse_plan.detect_postponement_impacts(p_j, ev_j, TODAY)['impacts'])
        for txt in surfaces:
            self.assertIsNone(jargon_re.search(txt), f'Jargon leak: {txt!r}')


class PreopStressFixesR17HttpWorkflowTests(TestCase):
    """Fuzz 16 F1, F6, template visit_date, pediatric rejection, READY demotion toasts & board doctor."""

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.nurse = User.objects.create_user(username='r17_nurse', password='pw', first_name='ნინო', last_name='ექთანი')
        cls.doc = User.objects.create_user(username='r17_doc', password='pw', first_name='გიორგი', last_name='ქირურგი')
        StaffProfile.objects.create(user=cls.doc, display_name='გიორგი ქირურგი')

    def _create_ready_saved(self, name='R17Ready', archived=False, outcome='scheduled', approved=True, someday='ready', visit_date=None):
        p = _mk('evla', age=46, sex='M', side='R', s={'case_outcome_status': outcome})
        p.patient_name = name
        p.surgeon = self.doc
        p.created_by = p.updated_by = self.doc
        p.archived = archived
        p.someday_list = someday
        p.visit_date = None if someday else (visit_date or TODAY)
        if approved:
            p.approved_at = timezone.now()
            p.approved_by = self.doc
        p.save()
        return p

    def test_f1_rbac_and_f6_archived_inactive_gates_and_templates(self):
        c_nur, c_doc = Client(), Client()
        c_nur.force_login(self.nurse)
        c_doc.force_login(self.doc)

        # F1: Plain nurse action=delete NEVER archives approved/operated/board-linked card
        p_ap = self._create_ready_saved('R17Ap')
        r_nd = c_nur.post(reverse('preop-move', args=[p_ap.pk]), {'action': 'delete'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        p_ap.refresh_from_db()
        self.assertEqual(r_nd.status_code, 403)
        self.assertFalse(p_ap.archived)

        # F6: Archived card blocks ALL mutations (header, q, s1, s2, dos_vqi, signoff, self_link, files, move, reschedule)
        p_ar = self._create_ready_saved('R17Arch', archived=True, someday='')
        c_doc.post(reverse('preop-patient', args=[p_ar.pk]), {'section': 'header', 'patient_name': 'Hacked', 'age': '50', 'sex': 'M', 'op_subtype': 'evla', 'side': 'R'})
        c_doc.post(reverse('preop-patient', args=[p_ar.pk]), {'section': 'stage1', 'hb': '111'})
        c_doc.post(reverse('preop-patient', args=[p_ar.pk]), {'section': 'self_link', 'action': 'rotate'})
        c_doc.post(reverse('preop-reschedule', args=[p_ar.pk]), {'action': 'ack_postpone', 'sig': 'hacked'})
        c_doc.post(reverse('preop-move', args=[p_ar.pk]), {'action': 'move', 'slot': 'someday:hold'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        c_doc.post(reverse('preop-files', args=[p_ar.pk]), {'category': 'labs', 'file': SimpleUploadedFile('a.pdf', b'%PDF-1.4\n%%EOF\n', content_type='application/pdf')})
        p_ar.refresh_from_db()
        self.assertEqual(p_ar.patient_name, 'R17Arch')
        self.assertEqual(p_ar.studies.get('hb'), 138)
        self.assertIsNone(p_ar.studies.get('self_fill_nonce'))
        self.assertNotEqual(p_ar.studies.get('postpone_ack_sig'), 'hacked')
        self.assertEqual(p_ar.someday_list, '')
        self.assertEqual(p_ar.files.count(), 0)

        # Template bug fix: patient.html visit_date input preserves ISO YYYY-MM-DD (never blanked by double |date)
        fut = TODAY + datetime.timedelta(days=5)
        p_vd = self._create_ready_saved('R17VD', approved=False, someday='', visit_date=fut)
        html_vd = c_doc.get(reverse('preop-patient', args=[p_vd.pk])).content.decode('utf-8')
        self.assertIn(f'name="visit_date" value="{fut.isoformat()}"', html_vd)

        # Pediatric age=5 rejected on preop-new & preop-nurse-register
        c_nur.post(reverse('preop-new'), {'patient_name': 'ChildNew', 'age': '5', 'op_subtype': 'evla', 'side': 'R', 'slot': 'someday:ready'})
        self.assertFalse(PreopPatient.objects.filter(patient_name='ChildNew').exists())
        c_nur.post(reverse('preop-nurse-register'), {'patient_name': 'ChildReg', 'age': '5', 'op_subtype': 'evla', 'side': 'R'})
        self.assertFalse(PreopPatient.objects.filter(patient_name='ChildReg').exists())

        # S3-03: passport_guide.html Section 4 heading adapts under STOP-lock
        p_stp = _mk('evla', sex='F', age=30, q={'ven_pregnant': True, 'med_aspirin': True})
        p_stp.surgeon = p_stp.created_by = p_stp.updated_by = self.doc
        p_stp.save()
        html_pg = c_doc.get(reverse('preop-passport', args=[p_stp.pk]) + '?view=guide').content.decode('utf-8')
        self.assertNotIn('რომელი წამალი აუცილებლად დალიოთ დილას', html_pg)

    def test_ready_demotion_warning_toasts_and_board_doctor_rbac(self):
        c_doc, c_nur = Client(), Client()
        c_doc.force_login(self.doc)
        c_nur.force_login(self.nurse)

        # READY -> CONF demotion emits warning message on header, q, stage1, stage2, dos_vqi
        cases = [
            {'section': 'header', 'patient_name': 'DemH', 'age': '47', 'sex': 'M', 'op_subtype': 'evla', 'side': 'R', 'slot_kind': 'someday:ready'},
            {'section': 'questionnaire', 'no_allergies': '1', 'ven_ceap': 'C3'},
            {'section': 'stage1', 'hb': '132'},
            {'section': 'stage2', 'nurse_note_custom': 'updated'},
            {'section': 'dos_vqi', 'action': 'save_dos', 'planned_surgery_date': (TODAY + datetime.timedelta(days=14)).isoformat()},
        ]
        for idx, payload in enumerate(cases):
            p = self._create_ready_saved(f'Dem_{idx}')
            resp = c_doc.post(reverse('preop-patient', args=[p.pk]), payload, follow=True)
            p.refresh_from_db()
            self.assertEqual(p.someday_list, PreopPatient.SomedayList.CONF, payload['section'])
            msgs = list(resp.context['messages'])
            self.assertTrue(any(m.level_tag == 'warning' for m in msgs), f"No warning toast on {payload['section']}")

        # Fuzz 16 S3-02: admit-board with patient.surgeon=None by plain nurse does NOT set nurse as BoardPatient.doctor
        p_ns = self._create_ready_saved('R17NoSurg')
        p_ns.surgeon = None
        p_ns.save(update_fields=['surgeon'])
        c_nur.post(reverse('preop-admit-board', args=[p_ns.pk]), {'unit': 'ward'})
        p_ns.refresh_from_db()
        bpid = p_ns.studies.get('board_patient_id')
        self.assertIsNotNone(bpid)
        bp = BoardPatient.objects.get(pk=bpid)
        self.assertIsNone(bp.doctor)
