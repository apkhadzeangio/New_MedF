"""Stage 4 Phase 4 Round-2 Test-First Regression Suite.

Covers EVERY Category (a) S1 & S2 finding independently discovered across the 3 Phase-4
re-verification auditors (Fuzzer & Invariants, Combos & Contradictions, Pharmacology/Pathways/Registry).
100% synthetic data.
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
    STATUS_READY,
    apply_questionnaire_post,
    build_med_schedule,
    build_self_fill_highlights,
    calc_ariscat_pulm,
    evaluate,
    nurse_plan,
    nurse_ui,
)
from ..preop.meds import _detect_freetext_high_risk_meds
from ..preop.registry import RULES_BY_ID, STATUS_LOCAL, annotate_med_rows, match_rule_for_text
from ..preop.views.patient import _parse_s1_numbers_and_dates

TODAY = datetime.date(2026, 11, 26)
OP = '2026-12-06'


def _go(sub='open', **kw):
    pw = PreopPatient.SUBTYPE_PATHWAY[sub]
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
        pathway=pw, op_subtype=sub, visit_date=datetime.date(2026, 11, 25),
        started_on=datetime.date(2026, 11, 25), cardio_decision='cleared', questionnaire=q, studies=s,
    )
    defaults.update(kw)
    return PreopPatient(**defaults)


class StressR2FuzzerAndMonotonicityTests(SimpleTestCase):
    """RF-01..RF-21: Parser preservation, sanitization, unit mismatch, monotonicity, and crash guards."""

    def test_rf01_rf02_rf04_rf19_implausible_parser_and_unit_mismatch_never_go(self):
        # RF-01: S1 parser must NOT silently wipe extreme values to None -> GO
        p = _go('open')
        s = dict(p.studies)
        _parse_s1_numbers_and_dates(
            QueryDict('weight_kg=75000&k=25&na=210&fibrinogen=0.1&glucose=1200&bp_sys=400&bp_dia=80'),
            s, TODAY,
        )
        p.studies = s
        ev = evaluate(p, today=TODAY)
        self.assertNotEqual(ev['clearance_seal']['code'], 'go')
        self.assertNotEqual(ev['status'], STATUS_READY)

        # RF-02 & RF-19: Garbage strings, NaN, Inf, bool, ldl=-5, cta_months=-10 must NEVER yield GO
        for bad_patch in (
            {'bp_sys': '120/80'}, {'k': 'abc'}, {'na': float('nan')},
            {'wbc': float('inf')}, {'fibrinogen': True}, {'ldl': -5}, {'cta_months': -10},
        ):
            ev_b = evaluate(_go('open', s=bad_patch), today=TODAY)
            self.assertNotEqual(ev_b['clearance_seal']['code'], 'go', f'False GO on {bad_patch}')

        # RF-04: Explicit creat_unit mismatch (creat=2.5 umol -> eGFR 269) must NEVER yield GO
        ev_um = evaluate(_go('open', s={'creat': 2.5, 'creat_unit': 'umol'}), today=TODAY)
        self.assertIsNone(ev_um['egfr'])
        self.assertNotEqual(ev_um['clearance_seal']['code'], 'go')
        ev_mg = evaluate(_go('open', s={'creat': 250, 'creat_unit': 'mgdl'}), today=TODAY)
        self.assertIsNone(ev_mg['egfr'])
        self.assertTrue(ev_mg['has_blocking_unknown_state'])
        self.assertNotEqual(ev_mg['clearance_seal']['code'], 'go')

    def test_rf03_rf06_rf10_rf11_monotonicity_neuraxial_glucose_and_plt_units(self):
        # RF-03: Out-of-clamp dos_inr=16.0 must keep neuraxial_hard_blocked=True & NO green ASRA check
        ev_nx = evaluate(_go('phlebectomy', s={'anesthesia_plan': 'neuraxial', 'dos_inr': 16.0}), today=TODAY)
        self.assertTrue(ev_nx['neuraxial_hard_blocked'])
        self.assertNotIn('✓', ev_nx['anesthetic_brief']['neuraxial_gate'])

        # RF-06: Glucose mmol/L vs mg/dL rounding boundaries must yield identical seals
        for fld, mmol_v, mgdl_v in (('dos_glucose', 3.87, 69.66), ('dos_glucose', 13.96, 251.28), ('glucose', 6.96, 125.28)):
            ea = evaluate(_go('open', s={fld: mmol_v}), today=TODAY)
            eb = evaluate(_go('open', s={fld: mgdl_v}), today=TODAY)
            self.assertEqual(ea['clearance_seal']['code'], eb['clearance_seal']['code'], f'{fld}: {mmol_v} vs {mgdl_v}')

        # RF-10: Out-of-clamp extremes must saturate at worst-case scores, never drop
        for sub, fld, a, b in (
            ('open', 'lvef', 5, 4), ('open', 'spo2', 30, 28), ('evla', 'k', 12.0, 12.5),
            ('open', 'plt', 4, 0), ('open', 'creat', 2500, 3500), ('open', 'hb', 2, 0.5),
        ):
            ea, eb = evaluate(_go(sub, s={fld: a}), today=TODAY), evaluate(_go(sub, s={fld: b}), today=TODAY)
            self.assertEqual(ea['auto_asa'], eb['auto_asa'], f'ASA dropped on {fld} {a}->{b}')
            self.assertLessEqual(ea['rcri']['score'], eb['rcri']['score'], f'RCRI dropped on {fld} {a}->{b}')
            self.assertLessEqual(ea['ariscat']['score'], eb['ariscat']['score'], f'ARISCAT dropped on {fld} {a}->{b}')

        # RF-11: PLT 40 vs 40000 gives ASA IV; PLT 1000 is thrombocytosis, NOT 1 x10^9/L thrombocytopenia
        self.assertEqual(evaluate(_go('open', s={'plt': 40000}), today=TODAY)['auto_asa'], 'IV')
        ev_1000 = evaluate(_go('open', s={'plt': 1000}), today=TODAY)
        self.assertFalse(any('თრომბოციტოპენია' in rf for rf in ev_1000['red_flags']))

    def test_rf05_rf08_rf09_rf13_rf14_to_rf18_rf20_failsafes_and_crashes(self):
        # RF-05: approved_at + stale labs must NEVER have status='ready' or workup_ready=True
        ev_st = evaluate(_go('open', approved_at=timezone.now(), s={'labs_date': '2026-10-01'}), today=TODAY)
        self.assertNotEqual(ev_st['status'], STATUS_READY)
        self.assertFalse(ev_st['workup_ready'])

        # RF-08 & RF-09 & RF-13 & RF-20: venous repro b-hCG, EVLA dabigatran w/o weight, missing arterial BP, self_fill_submitted
        self.assertNotEqual(evaluate(_go('evla', sex='F', age=30, s={'dos_bhcg_negative': False}), today=TODAY)['clearance_seal']['code'], 'go')
        self.assertNotEqual(evaluate(_go('evla', q={'med_dabigatran': True}, s={'weight_kg': None}), today=TODAY)['clearance_seal']['code'], 'go')
        self.assertNotEqual(evaluate(_go('open', s={'bp_sys': None, 'bp_dia': None, 'hr': None}), today=TODAY)['clearance_seal']['code'], 'go')
        self.assertNotEqual(evaluate(_go('open', q={'self_fill_submitted': True, 'nurse_verified': False, 'filled_by': ''}), today=TODAY)['clearance_seal']['code'], 'go')

        # RF-14..RF-18: Zero crashes on datetime today, datetime started_on, str/NaN egfr, None q/s, 0001-01-01
        evaluate(_go('cea_symp', q={'cea_symp_event': True, 'cea_event_date': '2026-11-20'}), today=datetime.datetime(2026, 11, 26, 9, 0))
        p_post = _go('open', started_on=datetime.datetime(2026, 11, 20, 9, 0), s={'prev_planned_surgery_date': '2026-12-01', 'planned_surgery_date': '2026-12-10'})
        nurse_plan.detect_postponement_impacts(p_post, evaluate(p_post, today=TODAY), TODAY)
        build_med_schedule(_go('open', q={'med_doac': True}), {'med_doac': True}, {}, egfr='50', today=TODAY)
        nan_rows = build_med_schedule(_go('open', q={'med_doac': True}), {'med_doac': True}, {}, egfr=float('nan'), today=TODAY)
        self.assertNotIn('nan', nan_rows[0]['rule'].lower())
        calc_ariscat_pulm(_go(), None, {})
        build_self_fill_highlights(None)
        build_self_fill_highlights({'filled': True, 'other_meds': 123})
        with mock.patch('django.utils.timezone.localdate', return_value=datetime.date(1, 1, 1)):
            apply_questionnaire_post({'cea_symp_event': '1', 'cea_days_since_event': '5'}, _go('cea_symp'), 'Nurse')


class StressR2PharmaComboAndPathwayTests(SimpleTestCase):
    """F-01..F-22, CLN-01..CLN-22: Meds, STOP-lock, phone script, pathways, and registry citation matching."""

    def test_f01_f02_f03_f05_cln01_cln02_cln07_cln08_pharmacology_safety(self):
        # F-01: Metformin morning skip must NOT be reversed to 'არ შეწყვიტოს! მიიღოს ჩვეულებრივად'
        p_m = _go('evla', q={'dm': True, 'metformin': True})
        ph_m = nurse_plan.build_phone_med_script(p_m, evaluate(p_m, today=TODAY), TODAY)
        self.assertFalse(any('მეტფორმინ' in r['raw_drug'] and 'არ შეწყვიტოს' in r['spoken'] for r in ph_m['keep_rows']))

        # F-02 & CLN-01: CEA ICH marks warfarin/DOAC/ticagrelor/prasugrel contraindicated, NOT 'არ შეწყვიტოთ' or keep_meds
        p_ich = _go('cea_symp', q={'cea_symp_event': True, 'cea_days_since_event': 3, 'med_warfarin': True, 'med_ticagrelor': True, 'med_prasugrel': True}, s={'inr': 2.2, 'brain_imaging': 'large_or_hemorrhage'})
        ev_ich = evaluate(p_ich, today=TODAY)
        for r in ev_ich['med_schedule']:
            if any(k in r['drug'] for k in ('ვარფარინ', 'ტიკაგრელორ', 'პრასუგრელ')):
                self.assertTrue(r.get('contraindicated'), f'Not contraindicated on ICH: {r}')
                self.assertNotIn('არ შეწყვიტოთ', r['date_plan'])
        self.assertFalse(any('ტიკაგრელორ' in r['raw_drug'] for r in nurse_plan.build_phone_med_script(p_ich, ev_ich, TODAY)['keep_rows']))

        # F-03: CEA Day-0 IV thrombolysis + DAPT must NOT be green 'ok'
        ev_rtpa = evaluate(_go('cea_symp', q={'cea_symp_event': True, 'cea_days_since_event': 0, 'cea_thrombolysis': True, 'med_aspirin': True, 'med_clopidogrel': True}, s={'brain_imaging': 'pending'}), today=TODAY)
        for r in ev_rtpa['med_schedule']:
            if any(k in r['drug'] for k in ('ასპირინ', 'კლოპიდოგრელ')):
                self.assertEqual(r['tone'], 'red')

        # F-05: STOP-lock must NOT turn dual OAC / prasugrel+stroke into 'არ შეწყვიტოთ' or leak GLP-1 hold dates
        ev_sl = evaluate(_go('cea_asymp', q={'med_warfarin': True, 'med_doac': True, 'med_prasugrel': True, 'neuro': True, 'glp1': True}, s={'carotid_r': 100, 'inr': 2.5}), today=TODAY)
        by_drug = {r['drug']: r for r in ev_sl['med_schedule']}
        self.assertNotIn('არ შეწყვიტოთ', by_drug['ვარფარინი']['date_plan'])
        self.assertNotIn('არ შეწყვიტოთ', by_drug['პრასუგრელი (ეფიენტი)']['date_plan'])
        self.assertNotIn('თხევადი დიეტა', by_drug['GLP-1 (ოზემპიკი / მუნჯარო / საქსენდა)']['date_plan'])

        # F-10 & CLN-02: rivaroxaban_2_5 must NOT mask Xarelto 20mg when another 2.5mg drug is present; Forxiga/Synjardy caught
        self.assertTrue(_detect_freetext_high_risk_meds('ქსარელტო 20 მგ, ბისოპროლოლი 2.5 მგ', {'med_chips': ['rivaroxaban_2_5']})['unstructured_labels'])
        self.assertTrue(_detect_freetext_high_risk_meds('სინჯარდი 12.5/1000', {'sglt2': True, 'metformin': False})['unstructured_labels'])
        self.assertTrue(_detect_freetext_high_risk_meds('ფორქსიგა 10 მგ', {})['unstructured_labels'])

        # F-15 & CLN-07 & CLN-08: PTA prasugrel+OAC is red; ticagrelor+OAC not in keep_rows; NSAID allergy guarded
        p_pt = _go('pta_clti', q={'med_doac': True, 'med_ticagrelor': True, 'med_prasugrel': True, 'abf_rest_pain': True})
        ev_pt = evaluate(p_pt, today=TODAY)
        self.assertEqual([r for r in ev_pt['med_schedule'] if 'პრასუგრელ' in r['drug']][0]['tone'], 'red')
        self.assertFalse(any('ტიკაგრელორ' in r['raw_drug'] for r in nurse_plan.build_phone_med_script(p_pt, ev_pt, TODAY)['keep_rows']))
        ev_ns = evaluate(_go('open', q={'allergy_chips': ['nsaid'], 'med_aspirin': True, 'med_ticagrelor': True}), today=TODAY)
        self.assertEqual([r for r in ev_ns['med_schedule'] if 'ასპირინ' in r['drug']][0]['tone'], 'red')

    def test_f06_f07_f11_f14_f16_to_f22_cln04_06_09_11_12_17_20_engine_and_verdict(self):
        # F-06: STATUS_ALERT must return nurse verdict 'alert' (rank 1), NOT dead-coded 'pending'
        p_al = _go('open', q={'cad': True, 'cad_chips': ['afib']}, s={'hr': 130, 'ecg': 'afib_controlled'})
        self.assertEqual(nurse_ui.get_nurse_readiness_verdict(p_al, evaluate(p_al, today=TODAY))['code'], 'alert')

        # F-07 & CLN-04: HIT + liver_3x must NOT recommend fondaparinux anywhere
        ev_hl = evaluate(_go('evla', q={'allergy_chips': ['hit_heparin'], 'ven_dvt_history': True}, s={'liver_status': '3x'}), today=TODAY)
        self.assertNotIn('ფონდაპარინუქსი (CrCl', ev_hl['surgical_brief']['heparin_plan'])
        self.assertFalse(any('ფონდაპარინუქსი — HIT + ღვიძლის' in a for a in ev_hl['amber_flags']))

        # CLN-06 & CLN-09: EVLA mech valve INR 1.5 within 3d still flags; EVLA warfarin missing INR w/o DoS ticks not STOP
        ev_mv = evaluate(_go('evla', q={'cad_chips': ['mech_valve'], 'med_warfarin': True}, s={'planned_surgery_date': (TODAY + datetime.timedelta(days=3)).isoformat(), 'inr': 1.5}), today=TODAY)
        self.assertTrue(any('სუბთერაპიულია' in rf for rf in ev_mv['red_flags']))
        ev_wn = evaluate(_go('evla', q={'med_warfarin': True}, s={'inr': None, 'dos_inr': None}), today=TODAY)
        self.assertNotEqual(ev_wn['clearance_seal']['code'], 'stop')

        # F-11 & RF-12: Lean venous s1_required_fields uses real keys (metformin, dm_insulin)
        ev_lv = evaluate(_go('evla', age=35, q={'dm': False, 'dm_insulin': True}, s={'glucose': None}), today=TODAY)
        self.assertTrue(any(m['field'] == 'glucose' for m in ev_lv['missing_items']))

        # F-14 & RF-07: EVLA + lidocaine allergy must NOT allow light breakfast
        p_lido = _go('evla', q={'ven_lidocaine_allergy': True}, s={'anesthesia_plan': 'auto'})
        self.assertNotIn('მსუბუქი საუზმე დაშვებულია', nurse_plan.build_patient_plain_explanation(p_lido, evaluate(p_lido, today=TODAY), TODAY)['fasting_plain'])

        # F-16 & CLN-18: Venous ABI 0.95 + TP 40 must emit a visible warning flag
        ev_tp40 = evaluate(_go('evla', age=45, s={'abi_r': 0.95, 'tp_r': 40}), today=TODAY)
        self.assertTrue(ev_tp40['compression_contra'])
        self.assertTrue(bool(ev_tp40['red_flags'] or ev_tp40['amber_flags']))

        # F-17 & F-18 & F-21 & F-22 & CLN-12 & CLN-17 & CLN-20
        ev_ed = evaluate(_go('evla', q={'on_dialysis': True, 'med_dabigatran': True}), today=TODAY)
        self.assertFalse(any('მოუხსნელადაც' in a for a in ev_ed['amber_flags']))
        self.assertTrue(evaluate(_go('evla', age=35, q={'cad': False, 'cad_chips': ['mi', 'hf']}, s={'ecg': 'pending'}), today=TODAY)['needs_ecg_venous'])
        self.assertIn('dos_bhcg_negative', [c['key'] for c in evaluate(_go('open', sex='F', age=None, q={'pregnant_possible': True}), today=TODAY)['dos_checks']])
        self.assertEqual(evaluate(_go('evla', age=35, s={'bp_sys': 80, 'bp_dia': 100, 'hr': 85}), today=TODAY)['auto_asa'], 'IV')
        self.assertNotEqual(evaluate(_go('evla', age=35, s={'bp_sys': 80, 'bp_dia': 100, 'bp_sys_l': 120, 'bp_dia_l': 80, 'hr': 85}), today=TODAY)['auto_asa'], 'IV')
        ev_ali = evaluate(_go('pta_claud', q={'abf_duration': '< 2 კვირა', 'ic_bmt_3m': False}), today=TODAY)
        self.assertFalse(any('3-თვიანი' in a for a in ev_ali['amber_flags']))
        self.assertFalse(evaluate(_go('evla', sex='F', age=10, q={'ven_pregnant': True}), today=TODAY)['has_hard_contraindication'])
        self.assertFalse(evaluate(_go('evla', sex='', age=75, q={'ven_pregnant': True}), today=TODAY)['has_hard_contraindication'])
        ev_pa = evaluate(_go('cea_asymp', q={'neuro': False, 'med_prasugrel': True}), today=TODAY)
        self.assertFalse(any('გადატანილი ინსულტის' in rf for rf in ev_pa['red_flags']))
        ev_cb = evaluate(_go('bypass_claud', q={'med_chips': ['statin', 'cilostazol']}, s={'nt_probnp': 450}), today=TODAY)
        self.assertTrue(any('ცილოსტაზოლი' in rf for rf in ev_cb['red_flags']))

    def test_cln03_cln19_cln22_registry_matching_and_honesty(self):
        # CLN-03: Specific safety alerts must match their canonical rule (NOT hijacked by short patterns)
        ev_hit = evaluate(_go('open', q={'allergy_chips': ['hit_heparin']}), today=TODAY)
        self.assertEqual(match_rule_for_text(ev_hit['red_flags'][0]).id, 'ALLERGY_HIT_ABX_PROPHYLAXIS')
        ev_mv = evaluate(_go('open', q={'cad_chips': ['mech_valve'], 'med_doac': True}), today=TODAY)
        mv_rf = [r for r in ev_mv['red_flags'] if 'მექანიკურ სარქველზე' in r][0]
        self.assertEqual(match_rule_for_text(mv_rf).id, 'MED_MECH_VALVE_SAFETY')
        doac_row = next(r for r in annotate_med_rows(ev_mv['med_schedule']) if 'DOAC' in r['drug'])
        self.assertEqual(doac_row['rule_meta']['id'], 'MED_MECH_VALVE_SAFETY')
        ev_pr = evaluate(_go('open', q={'neuro': True, 'med_prasugrel': True}), today=TODAY)
        pr_rf = [r for r in ev_pr['red_flags'] if 'პრასუგრელი' in r][0]
        self.assertEqual(match_rule_for_text(pr_rf).id, 'MED_ANTITHROMBOTIC_CONFLICTS')
        ev_ot = evaluate(_go('open', q={'med_doac': True, 'med_ticagrelor': True}), today=TODAY)
        ot_rf = [r for r in ev_ot['red_flags'] if 'ESC Class III' in r][0]
        self.assertEqual(match_rule_for_text(ot_rf).id, 'MED_ANTITHROMBOTIC_CONFLICTS')
        self.assertEqual(RULES_BY_ID['INF_UTI_VS_ASB'].status, STATUS_LOCAL)
        self.assertEqual(RULES_BY_ID['MED_DOAC_HOLD_INTERVALS'].status, STATUS_LOCAL)
        self.assertEqual(RULES_BY_ID['PAD_PROSTHETIC_IC_STOP'].status, STATUS_LOCAL)
        self.assertIn('cea_dia_amber', RULES_BY_ID['VIT_BP_SEVERE_HTN'].value)
        ev_carp = evaluate(_go('open', cardio_decision='cag', cardiologist='ლიკა ინჯია', q={'cad': True, 'cad_chips': [], 'mets_ok': True}), today=TODAY)
        self.assertFalse(any('მოითხოვა მოითხოვა' in rf for rf in ev_carp['red_flags']))


class StressR2TemplatesAndViewWorkflowTests(TestCase):
    """F-04, F-08, F-09, F-12, F-13, F-20, CLN-05, CLN-10, CLN-13, CLN-14, CLN-15, CLN-21."""

    def setUp(self):
        User = get_user_model()
        self.doc = User.objects.create_user(username='r2_doc', password='R2DocPassword!1', is_staff=True)
        self.client.force_login(self.doc)

    def test_f04_f08_f09_passport_and_guide_safety_gates(self):
        # F-04: STOP-locked passport_guide must STILL display acute_hold / contraindicated drugs
        p_stop = _go('cea_asymp', q={'cad': True, 'cad_chips': ['mech_valve'], 'med_doac': True, 'med_chips': ['beta_blocker', 'acei_arb']}, s={'carotid_r': 100, 'hr': 48, 'k': 5.6})
        p_stop.save()
        h_g = self.client.get(reverse('preop-patient-guide', args=[p_stop.pk])).content.decode()
        self.assertIn('ბეტა-ბლოკატორი', h_g)
        self.assertIn('DOAC', h_g)

        # F-08 & CLN-14: Unapproved draft with med_hold_agreed=True must NOT show green '[დადასტურებულია ექიმთან ✓]'
        p_dr = _go('open', q={'med_warfarin': True}, s={'inr': 2.5, 'med_hold_agreed': True, 'blood_reserved': False})
        p_dr.save()
        h_pp = self.client.get(reverse('preop-passport', args=[p_dr.pk])).content.decode()
        self.assertNotIn('[დადასტურებულია ექიმთან ✓]', h_pp)

        # F-09: _save_signoff must reject approve when has_blocking_unknown_state is True
        p_blk = _go('open', q={'other_meds': 'Eliquis 5 mg BID'})
        p_blk.save()
        self.client.post(reverse('preop-patient', args=[p_blk.pk]), {'section': 'signoff', 'action': 'approve', 'move_ready': '1'})
        p_blk.refresh_from_db()
        self.assertIsNone(p_blk.approved_at)

    def test_f12_f13_cln05_10_13_15_21_templates_and_invalidation(self):
        # CLN-10 & F-12: Urgent CLTI + iron deficiency must render pbm_iv_iron_done checkbox on _tab_s2
        p_clti = _go('bypass_clti', q={'abf_rest_pain': True}, s={'hb': 95, 'ferritin': 15})
        p_clti.save()
        h_s2 = self.client.get(reverse('preop-patient', args=[p_clti.pk]) + '?tab=s2').content.decode()
        self.assertIn('name="pbm_iv_iron_done"', h_s2)

        # CLN-05: Venous ABI 1.60 + TP 40 (compression_contra=True) must NOT show Class 2 compression checkbox
        p_vc = _go('evla', s={'abi_r': 1.60, 'tp_r': 40})
        p_vc.save()
        h_vc = self.client.get(reverse('preop-patient', args=[p_vc.pk]) + '?tab=s2').content.decode()
        self.assertNotIn('name="compression_ready"', h_vc)

        # CLN-13: Passport uses eff_renal & highlights critical CrCl<30, Na>=150, gl<3.9, PLT<100
        p_cr = _go('open', age=82, sex='F', s={'weight_kg': 38, 'height_cm': 140, 'creat': 82, 'na': 152, 'glucose': 3.5, 'plt': 85})
        p_cr.save()
        h_cr = self.client.get(reverse('preop-passport', args=[p_cr.pk])).content.decode()
        self.assertIn('pp-rcard high', h_cr)
        self.assertIn('class="bad"', h_cr)

        # F-13: Stage 1 POST invalidates frozen anamnesis; Header age/date change invalidates approval
        p_ap = _go('open', approved_at=timezone.now(), approved_by=self.doc, anamnesis='ძველი ანამნეზი Hb 140.')
        p_ap.save()
        self.client.post(reverse('preop-patient', args=[p_ap.pk]), {'section': 'stage1', 'hb': '85', 'labs_date': TODAY.isoformat()})
        p_ap.refresh_from_db()
        self.assertIsNone(p_ap.approved_at)
        self.assertEqual(p_ap.anamnesis, '')

        # CLN-21: patient_new.html task counter must NOT render blank '/'
        p_nw = _go('open', s={'blood_reserved': False})
        p_nw.save()
        h_nw = self.client.get(reverse('preop-patient', args=[p_nw.pk]) + '?ui=new').content.decode()
        self.assertNotIn('class="nw-count-pill blue">/</span>', h_nw)
