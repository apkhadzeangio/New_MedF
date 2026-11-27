"""Stage 4 Adversarial Clinical Stress-Test — Test-First Regression Suite (Category (a) S1/S2 fixes).

Zero real patient data — 100% synthetic scenarios verifying every triaged Category (a) engine,
pharmacology, failsafe, contradiction, comorbidity, and registry-provenance bug fix.
"""
import datetime
from unittest import mock

from django.contrib.auth import get_user_model
from django.http import QueryDict
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from ..models import PreopPatient
from ..preop import (
    apply_questionnaire_post,
    build_self_fill_highlights,
    calc_auto_asa,
    calc_caprini,
    calc_egfr,
    evaluate,
    nurse_plan,
    nurse_ui,
)
from ..preop.calculators import _parse_scr_mgdl
from ..preop.meds import _detect_freetext_high_risk_meds
from ..preop.registry import RULES_BY_ID, STATUS_LOCAL, match_rule_for_text
from ..preop.utils import target_prep_days
from ..preop.views.patient import _parse_s1_numbers_and_dates

TODAY = datetime.date(2026, 11, 26)
OP = '2026-12-06'


def _go_patient(op_subtype='open', **kw):
    pw = PreopPatient.SUBTYPE_PATHWAY[op_subtype]
    q = {
        'filled': True, 'nurse_verified': True, 'allergy_iodine_asked': True, 'mets_ok': True,
        'med_aspirin': pw != 'venous', 'med_chips': ['statin'] if pw != 'venous' else [], 'ic_bmt_3m': True,
    }
    q.update(kw.pop('q', {}))
    s = {
        'labs_date': '2026-11-24', 'planned_surgery_date': OP, 'bp_sys': 125, 'bp_dia': 78,
        'hr': 72, 'spo2': 98, 'weight_kg': 78, 'height_cm': 175, 'hb': 140, 'wbc': 7.0, 'plt': 230,
        'inr': 1.0, 'fibrinogen': 3.0, 'creat': 88.4, 'creat_unit': 'umol', 'k': 4.2, 'na': 140,
        'glucose': 5.4, 'albumin': 42, 'blood_group': 'O+', 'ecg': 'normal', 'cxr': 'normal', 'lvef': 60,
        'aaa_mm': 58 if pw == 'aaa_infra' else None, 'aaa_neck_status': 'friendly_ifu',
        'aaa_iliac_access': 'ok_7mm', 'popliteal_screen_status': 'normal', 'cta_disc': True,
        'carotid_r': 75 if pw == 'cea' else None, 'carotid_l': 20 if pw == 'cea' else None,
        'cea_cta_status': 'concordant', 'abi_r': 0.65 if pw in ('abf', 'pad_bypass', 'pad_pta') else 1.05,
        'abi_l': 0.70 if pw == 'abf' else 1.05, 'abf_clamp_zone': 'infrarenal', 'abf_outflow': 'patent',
        'pad_runoff_status': 'popliteal_patent', 'pad_gsv_status': 'adequate', 'pad_gsv_mm': 4.0,
        'pta_lesion_site': 'femoropopliteal', 'pta_access_site': 'cfa_clean',
        'ven_duplex_status': 'deep_patent_reflux', 'blood_reserved': True, 'evar_sized': True,
        'decolonization_ready': True, 'dapt_ready': True, 'compression_ready': True,
        'vte_prophylaxis_planned': True, 'med_hold_agreed': True, 'anesth_done': True, 'dos_bhcg_negative': True,
    }
    s.update(kw.pop('s', {}))
    defaults = dict(
        patient_name='სატესტო პაციენტი', age=65, sex='M', side='' if pw in ('aaa_infra', 'abf') else 'R',
        pathway=pw, op_subtype=op_subtype, visit_date=datetime.date(2026, 11, 25),
        started_on=datetime.date(2026, 11, 25), cardio_decision='cleared', questionnaire=q, studies=s,
    )
    defaults.update(kw)
    return PreopPatient(**defaults)


class StressCrashDeterminismFailsafeTests(SimpleTestCase):
    """F-01..F-19: No crashes, determinism, implausible-value & unknown-state failsafes, monotonicity."""

    def test_f01_to_f06_zero_crashes_on_pathological_inputs(self):
        ev0 = evaluate(_go_patient(), today=TODAY)
        ev1 = evaluate(_go_patient(s={'k': 6.2}), today=TODAY)
        deltas = nurse_plan.summarize_stage1_clinical_delta(ev0, ev1)
        self.assertIsInstance(deltas, list)
        self.assertTrue(any('შეიცვალა' in d for d in deltas))

        for bad_dt in ('0001-01-01', '9999-12-31'):
            evaluate(_go_patient(q={'med_warfarin': True, 'metformin': True}, s={'planned_surgery_date': bad_dt}), today=TODAY)

        self.assertIsNone(calc_egfr(creat=1, age='0', sex='M'))
        self.assertIsNotNone(evaluate(PreopPatient(patient_name='T', age='65', sex='M', studies={'creat': 88.4}), today=TODAY))
        self.assertIsInstance(calc_caprini(_go_patient(), None), tuple)
        self.assertIsInstance(calc_auto_asa(_go_patient(), {'filled': True}, {}, egfr='50', bmi='25'), str)
        evaluate(_go_patient(approved_at=datetime.datetime(2026, 11, 25, 9, 0)), today=TODAY)
        evaluate(_go_patient(started_on=datetime.datetime(2026, 11, 20, 9, 0), visit_date=datetime.date(2026, 11, 21)), today=TODAY)

        p_s, p_u = _go_patient(), _go_patient()
        p_s.pk, p_u.pk = 1, None
        nurse_ui.build_nurse_handover_queues([p_s, p_u], {1: ev0, None: ev0}, TODAY)

        apply_questionnaire_post({'cea_symp_event': '1', 'cea_event_text': 12345}, _go_patient('cea_symp'), 'Nurse')
        self.assertIsInstance(PreopPatient(patient_name='T', pathway='aaa_infra', op_subtype='open', studies='bad').diagnosis_text, str)

    def test_f07_f09_f10_f15_f19_implausible_and_missing_values_never_yield_go(self):
        self.assertNotEqual(_parse_scr_mgdl(0.15, ''), (None, True))
        for patch in (
            {'k': 13.0}, {'spo2': 25}, {'bp_sys': 350, 'bp_dia': 80},
            {'dos_bp_sys': 310, 'dos_bp_dia': 80}, {'dos_inr': 18.0},
            {'dos_glucose': 1200}, {'na': 210}, {'wbc': 250},
            {'fibrinogen': 0.1}, {'weight_kg': 75000}, {'creat': -5, 'creat_unit': 'umol'},
            {'creat': 5000, 'creat_unit': 'umol'}, {'labs_date': ''},
            {'labs_date': '2099-01-01'}, {'labs_date': '2026-99-99'},
            {'bp_sys': None, 'bp_dia': None, 'spo2': None},
        ):
            ev = evaluate(_go_patient(s=patch), today=TODAY)
            self.assertNotEqual(ev['clearance_seal']['code'], 'go', f'False GO on patch={patch}')

        # Stage 1 parser must preserve/normalize critical SpO2=45, K=10.5, PLT=5000/uL, WBC=400/uL, BP=35/20
        for qs, fld in (('spo2=45', 'spo2'), ('k=10.5', 'k'), ('plt=5000', 'plt'), ('wbc=400', 'wbc'), ('bp_sys=35&bp_dia=20', 'bp_sys')):
            p = _go_patient()
            s = dict(p.studies)
            _parse_s1_numbers_and_dates(QueryDict(qs), s, TODAY)
            p.studies = s
            self.assertIsNotNone(s[fld], f'S1 parser wiped {fld} on {qs}')
            self.assertNotEqual(evaluate(p, today=TODAY)['clearance_seal']['code'], 'go', f'False GO after S1 parse {qs}')

        # Invalid age / missing sex with severe CKD must never yield GO
        for kw in (dict(age=None), dict(age=-5), dict(age=130), dict(sex='')):
            ev = evaluate(_go_patient(s={'creat': 250, 'creat_unit': 'umol'}, **kw), today=TODAY)
            self.assertNotEqual(ev['clearance_seal']['code'], 'go', f'False GO on {kw}')

        # Missing surgical side on CEA & missing CrCl on metformin
        self.assertNotEqual(evaluate(_go_patient('cea_asymp', side=''), today=TODAY)['clearance_seal']['code'], 'go')
        self.assertNotEqual(evaluate(_go_patient(age=None, q={'metformin': True}, s={'weight_kg': None}), today=TODAY)['clearance_seal']['code'], 'go')

        # Monotonicity on dos_inr 14.5 -> 16.0 and PLT 5 -> 4
        self.assertEqual(evaluate(_go_patient(s={'dos_inr': 16.0}), today=TODAY)['clearance_seal']['code'], 'stop')
        self.assertEqual(evaluate(_go_patient(s={'plt': 4}), today=TODAY)['auto_asa'], 'IV')

    def test_f08_f12_f17_verdict_determinism_and_aaa_monotonicity(self):
        # F-08: Blocking unknown state or DoS STOP must NEVER give verdict='ready' or status='ready'
        p_doac = _go_patient(q={'med_doac': True, 'med_chips': ['statin', 'ppi']}, s={'weight_kg': None})
        ev_doac = evaluate(p_doac, today=TODAY)
        self.assertNotEqual(nurse_ui.get_nurse_readiness_verdict(p_doac, ev_doac)['code'], 'ready')

        ev_dos = evaluate(_go_patient(s={'dos_bp_sys': 200, 'dos_bp_dia': 120}), today=TODAY)
        self.assertEqual(ev_dos['clearance_seal']['code'], 'stop')
        self.assertNotEqual(ev_dos['status'], 'ready')
        self.assertFalse(ev_dos['workup_ready'])

        # F-12: Wall-clock invariance in evaluate(patient, today=D)
        p_cea = _go_patient('cea_symp', q={'cea_symp_event': True, 'cea_event_date': '2026-01-01', 'med_clopidogrel': True}, s={'planned_surgery_date': '2026-01-12'})
        with mock.patch('django.utils.timezone.localdate', return_value=datetime.date(2026, 1, 5)):
            ev_a = evaluate(p_cea, today=datetime.date(2026, 1, 5))
        with mock.patch('django.utils.timezone.localdate', return_value=datetime.date(2026, 11, 26)):
            ev_b = evaluate(p_cea, today=datetime.date(2026, 1, 5))
        self.assertEqual(ev_a['med_schedule'], ev_b['med_schedule'])
        clop_row = [r for r in ev_b['med_schedule'] if 'კლოპიდოგრელ' in r['drug']][0]
        self.assertFalse(clop_row['hold_or_skip'])

        # F-17: AAA 60 -> 70 mm must NEVER upgrade clearance_seal from pending to go
        p60 = _go_patient('open', s={'aaa_mm': 60, 'hb': 116, 'ferritin': 20, 'pbm_iv_iron_done': False, 'decolonization_ready': False})
        p70 = _go_patient('open', s={'aaa_mm': 70, 'hb': 116, 'ferritin': 20, 'pbm_iv_iron_done': False, 'decolonization_ready': False})
        self.assertEqual(evaluate(p60, today=TODAY)['clearance_seal']['code'], 'pending')
        self.assertNotEqual(evaluate(p70, today=TODAY)['clearance_seal']['code'], 'go')


class StressPharmacologyAndComboTests(SimpleTestCase):
    """PHARM-01..22, PTA-04/05/11/12, COMBO-F1..F18, M1..M4, D1."""

    def test_pharm_01_05_06_freetext_meds_and_allergies_safety(self):
        # PHARM-01: rivaroxaban_2_5 must NOT mask Eliquis in other_meds
        p1 = _go_patient('bypass_claud', q={'med_chips': ['rivaroxaban_2_5'], 'other_meds': 'Eliquis 5 mg BID'})
        ev1 = evaluate(p1, today=TODAY)
        self.assertTrue(ev1['has_blocking_unknown_state'])
        self.assertTrue(any('DOAC' in f or 'ელიქვის' in f or 'other_meds' in str(ev1['unknown_state_flags']) for f in ev1['red_flags']))

        # PHARM-05 + CONTRA-7.2: Clexane and Sintrom/Sinkumari in other_meds must block neuraxial & GO
        for om in ('Clexane 60 mg BID', 'სინკუმარი 4მგ', 'Brilique 90 mg BID', 'Zibor 3500', 'Arixtra 2.5 mg'):
            ft = _detect_freetext_high_risk_meds(om, q={})
            self.assertTrue(ft['unstructured_antithrombotic'], f'Missed antithrombotic: {om}')
        p_nx = _go_patient('phlebectomy', q={'other_meds': 'Clexane 60 mg BID'}, s={'anesthesia_plan': 'neuraxial'})
        ev_nx = evaluate(p_nx, today=TODAY)
        self.assertTrue(ev_nx['neuraxial_hard_blocked'])

        # CONTRA-4.1 & 4.2: კეფაზოლინი allergy detected & never GO with Cefazolin; highlights never empty
        p_cef = _go_patient('open', q={'allergy_other': False, 'allergy_chips': [], 'allergy_text': 'კეფაზოლინზე ალერგია'})
        ev_cef = evaluate(p_cef, today=TODAY)
        self.assertNotEqual(ev_cef['clearance_seal']['code'], 'go')
        self.assertNotIn('ცეფაზოლინი 2 გ', ev_cef['surgical_brief']['abx_regimen'])
        self.assertTrue(build_self_fill_highlights({'filled': True, 'allergy_text': 'პენიცილინი ანაფილაქსია'}))
        self.assertTrue(build_self_fill_highlights({'filled': True, 'pregnant_possible': True}))

        # CONTRA-7.1: SGLT2/GLP-1 in other_meds must block GO
        p_sglt = _go_patient('phlebectomy', q={'other_meds': 'ჯარდიანსი 10მგ'})
        ev_sglt = evaluate(p_sglt, today=TODAY)
        self.assertTrue(ev_sglt['has_blocking_unknown_state'])
        self.assertTrue(len(ev_sglt['med_schedule']) >= 1)

    def test_pharm_02_03_04_hit_evla_dialysis_and_warfarin_bridging(self):
        # PHARM-02 & COMBO-F1/F2/F12: HIT must NEVER recommend LMWH or fondaparinux on mech valve
        p_hit = _go_patient('open', q={'allergy_chips': ['hit_heparin'], 'med_doac': True})
        ev_hit = evaluate(p_hit, today=TODAY)
        self.assertNotIn('LMWH', ev_hit['med_schedule'][0]['rule'])
        self.assertNotIn('LMWH', ev_hit['surgical_brief']['postop_antithrombotic'])
        self.assertNotIn('≥6–24', ev_hit['med_schedule'][0]['rule'])

        p_mv_hit = _go_patient('open', q={'cad': True, 'cad_chips': ['mech_valve'], 'allergy_chips': ['hit_heparin'], 'med_warfarin': True}, s={'inr': 1.4, 'liver_status': '3x'})
        ev_mv_hit = evaluate(p_mv_hit, today=TODAY)
        self.assertFalse(any('ჰეპარინის ხიდი' in rf for rf in ev_mv_hit['red_flags']))
        self.assertNotIn('ფონდაპარინუქსი', ev_mv_hit['surgical_brief']['heparin_plan'])

        # PHARM-03 / VEN-04 / F-11: EVLA + dialysis dabigatran must be RED contraindicated
        p_evla_d = _go_patient('evla', q={'on_dialysis': True, 'med_dabigatran': True})
        ev_evla_d = evaluate(p_evla_d, today=TODAY)
        dab_row = [r for r in ev_evla_d['med_schedule'] if 'დაბიგატრან' in r['drug']][0]
        self.assertEqual(dab_row['tone'], 'red')
        self.assertNotIn('შეიყვანეთ CrCl', dab_row['date_plan'])

        # PHARM-04: Mechanical valve warfarin includes post-op bridging; AFib + recent stroke triggers bridging
        p_mv = _go_patient('open', q={'cad': True, 'cad_chips': ['mech_valve'], 'med_warfarin': True}, s={'inr': 2.5})
        warf_mv = [r for r in evaluate(p_mv, today=TODAY)['med_schedule'] if 'ვარფარინ' in r['drug']][0]
        self.assertIn('48–72', warf_mv['rule'])
        self.assertNotIn('PAUSE', warf_mv['rule'])

        p_af_str = _go_patient('cea_symp', q={'cad': True, 'cad_chips': ['afib'], 'med_warfarin': True, 'cea_symp_event': True, 'cea_days_since_event': 10}, s={'inr': 2.5})
        warf_af = [r for r in evaluate(p_af_str, today=TODAY)['med_schedule'] if 'ვარფარინ' in r['drug']][0]
        self.assertIn('ხიდ', warf_af['rule'])
        self.assertNotIn('არ ტარდება', warf_af['rule'])

    def test_pharm_07_10_11_12_13_20_21_antiplatelets_bp_stoplock_metformin_ppi(self):
        # PHARM-07: Prasugrel + prior stroke -> red flag
        ev_pras = evaluate(_go_patient('cea_symp', q={'neuro': True, 'med_prasugrel': True}), today=TODAY)
        self.assertTrue(any('პრასუგრელ' in rf for rf in ev_pras['red_flags']))

        # PTA-04 & PTA-05: EVAR metformin eGFR>=60 MUST hold 48h + include creatinine recheck in date_plan; EVLA eGFR 38 must NOT hold 48h
        ev_evar_m = evaluate(_go_patient('evar', q={'dm': True, 'metformin': True}, s={'creat': 75, 'creat_unit': 'umol'}), today=TODAY)
        metf_evar = [r for r in ev_evar_m['med_schedule'] if 'მეტფორმინ' in r['drug']][0]
        self.assertTrue(metf_evar['hold_or_skip'])
        self.assertIn('48', metf_evar['date_plan'])
        self.assertIn('კრეატინინ', metf_evar['date_plan'])

        ev_evla_m = evaluate(_go_patient('evla', q={'dm': True, 'metformin': True}, s={'creat': 1.9, 'creat_unit': 'mgdl'}), today=TODAY)
        metf_evla = [r for r in ev_evla_m['med_schedule'] if 'მეტფორმინ' in r['drug']][0]
        self.assertEqual(ev_evla_m['needs_med_hold'], bool(metf_evla['hold_or_skip']))

        # PHARM-10 / ABF-05 / CONTRA-11.1: Bilateral BP (R 90/60, L 145/80) must NOT hold beta-blocker; L-arm hypotension (R None, L 82/50) MUST hold BB/CCB
        ev_sub = evaluate(_go_patient('open', q={'med_chips': ['statin', 'beta_blocker']}, s={'bp_sys': 90, 'bp_dia': 60, 'bp_sys_l': 145, 'bp_dia_l': 80}), today=TODAY)
        bb_sub = [r for r in ev_sub['med_schedule'] if 'ბეტა' in r['drug']][0]
        self.assertEqual(bb_sub['tone'], 'ok')
        self.assertNotIn('POISE-3', bb_sub['rule'])

        ev_inv = evaluate(_go_patient('evla', q={'med_chips': ['beta_blocker']}, s={'bp_sys': 85, 'bp_dia': 120}), today=TODAY)
        self.assertEqual(ev_inv['auto_asa'], 'IV')
        self.assertTrue(ev_inv['has_blocking_unknown_state'])
        ev_inv_contra = evaluate(_go_patient('evla', q={'med_chips': ['beta_blocker']}, s={'bp_sys': 85, 'bp_dia': 120, 'bp_sys_l': 120, 'bp_dia_l': 80}), today=TODAY)
        self.assertNotEqual(ev_inv_contra['auto_asa'], 'IV')

        # PHARM-11: STOP-lock must NOT say 'do not stop' on SBP 85 / K+ 5.8 ACEi/CCB hold
        ev_sl = evaluate(_go_patient('evla', q={'ven_pregnant': True, 'med_chips': ['acei_arb', 'ccb']}, s={'bp_sys': 85, 'bp_dia': 55, 'k': 5.8}), today=TODAY)
        ace_sl = [r for r in ev_sl['med_schedule'] if 'ACEi' in r['drug']][0]
        self.assertNotIn('არ შეწყვიტოთ', ace_sl['date_plan'])

        # PHARM-12 / ABF-01: Cilostazol does NOT satisfy antiplatelet & flags liver_3x / LVEF 45
        ev_cil = evaluate(_go_patient('bypass_claud', q={'med_aspirin': False, 'med_chips': ['statin', 'cilostazol']}, s={'lvef': 45, 'liver_status': '3x'}), today=TODAY)
        self.assertTrue(any('ანტიაგრეგანტ' in af for af in ev_cil['amber_flags']))
        self.assertTrue(any('ცილოსტაზოლ' in rf for rf in ev_cil['red_flags']))

        # PHARM-13: PPI + clopidogrel emits CYP2C19 switch warning
        ev_ppi = evaluate(_go_patient('open', q={'med_clopidogrel': True, 'med_chips': ['statin', 'ppi']}), today=TODAY)
        self.assertTrue(any('ომეპრაზოლ' in m or 'CYP2C19' in m for m in ev_ppi['amber_flags'] + ev_ppi['red_flags']))

        # PHARM-21 / PTA-11: rivaroxaban_2_5 + ticagrelor -> red flag; PTA + OAC + ticagrelor -> not tone='ok'
        ev_rt = evaluate(_go_patient('bypass_claud', q={'med_aspirin': False, 'med_chips': ['statin', 'rivaroxaban_2_5'], 'med_ticagrelor': True}), today=TODAY)
        self.assertTrue(any('ტიკაგრელორ' in rf or 'რივაროქსაბან' in rf for rf in ev_rt['red_flags']))
        ev_pta_ot = evaluate(_go_patient('pta_clti', q={'med_doac': True, 'med_ticagrelor': True}), today=TODAY)
        tica_row = [r for r in ev_pta_ot['med_schedule'] if 'ტიკაგრელორ' in r['drug']][0]
        self.assertEqual(tica_row['tone'], 'red')

    def test_combo_f3_f4_f6_f7_d1_m1_m2_m4_and_asra(self):
        # COMBO-F3: Severe AS + cardio cleared must STILL hard-block neuraxial
        ev_as = evaluate(_go_patient('bypass_clti', cardio_decision='cleared', s={'echo_valve_issue': True, 'anesthesia_plan': 'neuraxial'}), today=TODAY)
        self.assertTrue(ev_as['neuraxial_hard_blocked'])

        # COMBO-F4: CEA ICH must NOT tell patient to continue aspirin/clopidogrel as tone='ok'
        ev_ich = evaluate(_go_patient('cea_symp', q={'med_aspirin': True, 'med_clopidogrel': True}, s={'brain_imaging': 'large_or_hemorrhage'}), today=TODAY)
        ap_ich_rows = [r for r in ev_ich['med_schedule'] if any(k in r['drug'] for k in ('ასპირინ', 'კლოპიდოგრელ'))]
        self.assertEqual(len(ap_ich_rows), 2)
        for r in ap_ich_rows:
            self.assertEqual(r['tone'], 'red')

        # COMBO-F6: EVLA under General Anesthesia must NEVER allow light breakfast
        p_evla_ga = _go_patient('evla', s={'anesthesia_plan': 'general'})
        plain = nurse_plan.build_patient_plain_explanation(p_evla_ga, evaluate(p_evla_ga, today=TODAY), TODAY)
        self.assertNotIn('მსუბუქი საუზმე დაშვებულია', plain['fasting_plain'])

        # COMBO-F7: Clopidogrel allergy + med_clopidogrel must NEVER emit 'მიიღეთ ... ჩათვლით'
        ev_ci = evaluate(_go_patient('cea_symp', q={'allergy_chips': ['clopidogrel_intol'], 'med_clopidogrel': True, 'cea_symp_event': True}), today=TODAY)
        clop_ci = [r for r in ev_ci['med_schedule'] if 'კლოპიდოგრელ' in r['drug']][0]
        self.assertEqual(clop_ci['tone'], 'red')
        self.assertNotIn('მიიღეთ', clop_ci['date_plan'])

        # COMBO-D1: build_plain_georgian_missing_items must NOT duplicate Stage 2 tasks
        p_d1 = _go_patient('open', cardio_decision='pending', q={'sglt2': True, 'pacemaker': True}, s={'blood_reserved': False, 'anesth_done': False, 'med_hold_agreed': False})
        ev_d1 = evaluate(p_d1, today=TODAY)
        miss_d1 = nurse_plan.build_plain_georgian_missing_items(p_d1, ev_d1, TODAY)
        keys = [m['label'] for m in miss_d1]
        self.assertEqual(len(keys), len(set(keys)), f'Duplicate missing items: {keys}')

        # COMBO-M1, M2, M4: dental_acute infection on PTA, SAPT on full OAC, ulcer_active_symp + DAPT
        ev_m1 = evaluate(_go_patient('pta_clti', q={'active_infection': True, 'inf_chips': ['dental_acute']}), today=TODAY)
        self.assertTrue(bool(ev_m1['red_flags'] or ev_m1['amber_flags']))
        ev_m2 = evaluate(_go_patient('pta_clti', q={'cad': True, 'cad_chips': ['afib'], 'med_doac': True}, s={'post_antithrombotic_plan': 'sapt'}), today=TODAY)
        self.assertTrue(any('ანტიკოაგულანტ' in rf or 'SAPT' in rf or 'OAC' in rf for rf in ev_m2['red_flags']))
        ev_m4 = evaluate(_go_patient('pta_clti', q={'ulcer': True, 'ulcer_active_symp': True, 'med_clopidogrel': True}), today=TODAY)
        self.assertTrue(any('წყლულ' in rf or 'სისხლდენ' in rf for rf in ev_m4['red_flags']))


class StressPathwayAndContradictionTests(TestCase):
    """AAA, CEA, ABF/Bypass, PTA, Venous, Contradictions & Post-Approval Invalidation."""

    def test_aaa_and_cea_clinical_bugs(self):
        # AAA-01: Hemodynamically stable aaa_pain must NOT order permissive hypotension 70-90 mmHg
        ev_stab = evaluate(_go_patient('open', q={'aaa_pain': True}, s={'bp_sys': 148, 'bp_dia': 86, 'hr': 76}), today=TODAY)
        self.assertNotIn('70–90', ev_stab['anesthetic_brief']['bp_corridor'])

        # AAA-03 / ABF-11: Stable CAD sent for CAG must trigger CARP non-compliance
        ev_carp = evaluate(_go_patient('open', cardio_decision='cag', q={'cad': True, 'cad_chips': [], 'mets_ok': True}, s={'ecg': 'normal', 'lvef': 60, 'nt_probnp': 80, 'hs_ctn': 6}), today=TODAY)
        self.assertTrue(any('CARP' in rf for rf in ev_carp['red_flags']))

        # AAA-11: NT-proBNP 340 alone (LVEF 60, no HF) must NOT add CHF to RCRI/VSG-CRI
        ev_bnp = evaluate(_go_patient('open', q={'cad': False, 'cad_chips': []}, s={'lvef': 60, 'nt_probnp': 340}), today=TODAY)
        self.assertEqual(ev_bnp['rcri']['score'], 1)  # Only high-risk surgery (+1), NOT +1 CHF

        # CEA-02: Unset side + contralateral 100% occlusion must NOT fire hard STOP
        ev_occ = evaluate(_go_patient('cea_asymp', side='', s={'carotid_r': 100, 'carotid_l': 80}), today=TODAY)
        self.assertFalse(ev_occ['has_hard_contraindication'])

        # CEA-03 / CONTRA-9.1: cea_asymp upgraded by 3d symptomatic event + recent_stent must NOT advise 6-12m defer
        ev_st = evaluate(_go_patient('cea_asymp', q={'cea_symp_event': True, 'cea_days_since_event': 3, 'recent_stent': True, 'med_clopidogrel': True}), today=TODAY)
        self.assertFalse(any('ასიმპტომური CEA' in rf and 'გადაიდოს' in rf for rf in ev_st['red_flags']))

        # CEA-08: CEA DBP 102 (>90 mmHg) must trigger CEA BP warning, not '<180/110 does not delay'
        ev_bp = evaluate(_go_patient('cea_asymp', s={'bp_sys': 165, 'bp_dia': 102}), today=TODAY)
        self.assertTrue(any('180/90' in m or 'ჰიპერპერფუზი' in m for m in ev_bp['red_flags'] + ev_bp['amber_flags']))

        # CEA-10: target_prep_days honors WF_PREP_TARGET_DAYS registry
        self.assertEqual(target_prep_days(_go_patient('cea_asymp'), today=TODAY), RULES_BY_ID['WF_PREP_TARGET_DAYS'].value['elective_art'])

    def test_abf_pta_venous_and_contradiction_bugs(self):
        # ABF-03: ABI 0.90 is abnormal PAD, NOT "normal ABI" red flag
        ev_090 = evaluate(_go_patient('bypass_claud', s={'abi_r': 0.90}), today=TODAY)
        self.assertFalse(any('ნორმალური ABI' in rf for rf in ev_090['red_flags']))

        # CONTRA-10.1 & 10.2: Wrong-side CLTI (R=1.0, L=0.35) & EVLA missing R-side ABI (R=None, L=0.95) must NOT yield GO
        ev_ws = evaluate(_go_patient('bypass_clti', side='R', q={'abf_rest_pain': True}, s={'abi_r': 1.0, 'abi_l': 0.35}), today=TODAY)
        self.assertNotEqual(ev_ws['clearance_seal']['code'], 'go')
        ev_evla_ws = evaluate(_go_patient('evla', side='R', q={'claudication': True}, s={'abi_r': None, 'abi_l': 0.95}), today=TODAY)
        self.assertNotEqual(ev_evla_ws['clearance_seal']['code'], 'go')

        # VEN-01 & COMBO-F11/M3: Venous ABI 0.55 is hard STOP; TP 25 with ABI 0.95 disables compression
        ev_v55 = evaluate(_go_patient('evla', s={'abi_r': 0.55}), today=TODAY)
        self.assertTrue(ev_v55['has_hard_contraindication'])
        ev_tp25 = evaluate(_go_patient('evla', s={'abi_r': 0.95, 'tp_r': 25}), today=TODAY)
        self.assertTrue(ev_tp25['compression_contra'])
        self.assertFalse(any(t['key'] in ('comp', 'compression') for t in ev_tp25['tasks']))

        # VEN-02 / PHARM-08 / CONTRA-7.3: Unanticoagulated EVLA dos_inr=2.9 must STOP; warfarin EVLA missing INR must warn
        ev_inr = evaluate(_go_patient('evla', q={'med_warfarin': False}, s={'dos_inr': 2.9, 'dos_identity_consent': True, 'dos_site_marked': True, 'dos_fasting_confirmed': True}), today=TODAY)
        self.assertEqual(ev_inr['clearance_seal']['code'], 'stop')
        ev_wm = evaluate(_go_patient('evla', q={'med_warfarin': True}, s={'inr': None, 'dos_inr': None, 'dos_identity_consent': True}), today=TODAY)
        self.assertTrue(any('INR' in w for w in ev_wm['dos_warnings']))

        # VEN-05 / CONTRA-4.3: Lidocaine allergy on EVLA -> hard STOP and lidocaine_max_mg is None
        ev_lid = evaluate(_go_patient('evla', q={'ven_lidocaine_allergy': True}), today=TODAY)
        self.assertTrue(ev_lid['has_hard_contraindication'])
        self.assertIsNone(ev_lid['lidocaine_max_mg'])

        # VEN-10: Reproductive-age female venous patient gets dos_bhcg_negative checkbox
        ev_vf = evaluate(_go_patient('evla', sex='F', age=32, s={'dos_bhcg_negative': False}), today=TODAY)
        self.assertTrue(any(dc['key'] == 'dos_bhcg_negative' for dc in ev_vf['dos_checks']))

        # PTA-15: Manifest hyperthyroidism on EVAR/PTA -> hard STOP
        ev_hyp = evaluate(_go_patient('evar', s={'tsh_status': 'hyperthyroid'}), today=TODAY)
        self.assertTrue(ev_hyp['has_hard_contraindication'])

        # CONTRA-2.1: Phlebectomy cad=False + recent_stent=True must NOT recommend stopping aspirin
        ev_ph_st = evaluate(_go_patient('phlebectomy', q={'cad': False, 'recent_stent': True, 'med_aspirin': True}), today=TODAY)
        asp_row = [r for r in ev_ph_st['med_schedule'] if 'ასპირინ' in r['drug']][0]
        self.assertFalse(asp_row['hold_or_skip'])

        # CONTRA-12.1: q.filled=False with med_warfarin / pregnant_possible must NOT be silently dropped
        ev_unf = evaluate(_go_patient('evar', sex='F', age=30, q={'filled': False, 'pregnant_possible': True, 'med_warfarin': True}), today=TODAY)
        self.assertTrue(bool(ev_unf['red_flags']))

        # CONTRA-13.1/13.2/13.3: approved_at must NOT bypass stale labs, DoS STOP, or blocking unknowns
        ts = timezone.now()
        ev_st_ap = evaluate(_go_patient('evla', approved_at=ts, s={'labs_date': '2026-10-01'}), today=TODAY)
        self.assertTrue(ev_st_ap['needs_stale_labs'])
        self.assertNotEqual(ev_st_ap['clearance_seal']['code'], 'go')

        p_blk_ap = _go_patient('phlebectomy', approved_at=ts, q={'med_doac': True}, s={'creat': None})
        ev_blk_ap = evaluate(p_blk_ap, today=TODAY)
        self.assertNotEqual(ev_blk_ap['status'], 'ready')
        self.assertNotEqual(nurse_ui.get_nurse_readiness_verdict(p_blk_ap, ev_blk_ap)['code'], 'ready')

    def test_post_approval_view_invalidation_and_registry_provenance(self):
        User = get_user_model()
        doc = User.objects.create_user(username='stress_doc', password='StressDocPassword!1', is_staff=True)
        self.client.force_login(doc)
        p = _go_patient('evla', approved_at=timezone.now(), approved_by=doc, anamnesis='ალერგია: უარყოფს.')
        p.save()
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'q', 'filled': '1', 'allergy_text': 'პენიცილინი ანაფილაქსია', 'mets_ok': '1',
        })
        p.refresh_from_db()
        self.assertIsNone(p.approved_at)
        self.assertEqual(p.anamnesis, '')

        # Registry provenance checks (AAA-05, CEA-01, PTA-13, VEN-11)
        self.assertIn('IIa', RULES_BY_ID['AAA_MALE_MIN_MM'].class_level)
        self.assertEqual(RULES_BY_ID['CEA_BILATERAL_STOP'].status, STATUS_LOCAL)
        self.assertEqual(RULES_BY_ID['AAA_GIANT_MM'].status, STATUS_LOCAL)
        self.assertEqual(RULES_BY_ID['VEN_VTE_CAPRINI_PROPHYLAXIS'].section.find('Rec 34'), -1)
        self.assertNotEqual(match_rule_for_text('პაციენტს უჭირს ზურგზე გაუნძრევლად წოლა').id, 'PTA_BTK_IC_STOP')
        self.assertEqual(RULES_BY_ID['PTA_BTK_IC_STOP'].status, STATUS_LOCAL)
        self.assertIn('Level C', RULES_BY_ID['PTA_BTK_IC_STOP'].class_level)
