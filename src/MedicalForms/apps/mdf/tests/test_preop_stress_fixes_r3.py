"""Test-first regression suite for Phase 4 Round-2 re-verification findings (R3-S1-01..14 & R3-S2-01..10)."""
import datetime

from django.contrib.auth import get_user_model
from django.http import QueryDict
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from ..models import PreopPatient
from ..preop import (
    STATUS_READY,
    build_self_fill_highlights,
    evaluate,
    generate_anamnesis,
    nurse_plan,
    nurse_ui,
)
from ..preop.forms import apply_questionnaire_post
from ..preop.meds import _detect_freetext_high_risk_meds
from ..preop.registry import (
    RULES_BY_ID,
    STATUS_LOCAL,
    annotate_med_rows,
    match_rule_for_text,
)
from ..preop.views.patient import _parse_s1_numbers_and_dates, _set_num_field

TODAY = datetime.date(2026, 11, 26)
_PW = {
    'open': 'aaa_infra', 'evar': 'aaa_infra', 'tbd': 'aaa_infra',
    'cea_symp': 'cea', 'cea_asymp': 'cea', 'abf_clti': 'abf', 'abf_claud': 'abf',
    'bypass_clti': 'pad_bypass', 'bypass_claud': 'pad_bypass',
    'pta_clti': 'pad_pta', 'pta_claud': 'pad_pta', 'evla': 'venous', 'phlebectomy': 'venous',
}


def _go(sub='open', **kw):
    pw = _PW[sub]
    q = {'filled': True, 'nurse_verified': True, 'mets_ok': True, 'med_aspirin': True, 'med_chips': ['statin']}
    q.update(kw.pop('q', {}))
    s = {
        'labs_date': '2026-11-25', 'weight_kg': 75, 'height_cm': 172,
        'bp_sys': 126, 'bp_dia': 76, 'hr': 72, 'spo2': 98, 'hb': 140, 'wbc': 7.0, 'plt': 230,
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
        patient_name='სატესტო რ3', age=65, sex='M', side='' if pw in ('aaa_infra', 'abf') else 'R',
        pathway=pw, op_subtype=sub, visit_date=datetime.date(2026, 11, 25),
        started_on=datetime.date(2026, 11, 25), cardio_decision='cleared', questionnaire=q, studies=s,
    )
    defaults.update(kw)
    return PreopPatient(**defaults)


class StressR3ClinicalSafetyTests(SimpleTestCase):
    """R3-S1-02..08, R3-S1-10..14 & R3-S2-07..09 engine & pharmacology tests."""

    def test_r3_s1_02_03_04_05_06_afib_stop_rtpa_regex_and_unstructured(self):
        # R3-S1-02: Rapid AFib HR>110 + LVEF<40 + cardio_cleared MUST emit red flag & block GO
        ev_af = evaluate(_go('open', s={'ecg': 'afib_controlled', 'hr': 135, 'bp_sys': 150, 'bp_dia': 85, 'lvef': 35}), today=TODAY)
        self.assertTrue(any('ტაქისისტოლია' in rf for rf in ev_af['red_flags']))
        self.assertNotEqual(ev_af['clearance_seal']['code'], 'go')

        # R3-S1-03: STOP-locked anticoagulated patient MUST NOT appear in nurse med_calls queue
        p_stop = _go('phlebectomy', pk=11, q={'cad_chips': ['mech_valve'], 'med_warfarin': True}, s={'ven_duplex_status': 'acute_dvt', 'planned_surgery_date': '2026-11-30'})
        ev_stop = evaluate(p_stop, today=TODAY)
        nq = nurse_ui.build_nurse_handover_queues([p_stop], {11: ev_stop}, TODAY)
        self.assertEqual([x['p'].pk for x in nq['med_calls']], [])

        # R3-S1-04: CEA post-rtPA days 2-6 with brain_imaging='pending' MUST mark all AT red/contraindicated
        ev_tl = evaluate(_go('cea_symp', q={'cea_thrombolysis': True, 'cea_days_since_event': 2, 'med_aspirin': True, 'med_clopidogrel': True}, s={'brain_imaging': 'pending'}), today=TODAY)
        for row in ev_tl['med_schedule']:
            if any(k in row['drug'] for k in ('ასპირინი', 'კლოპიდოგრელი')):
                self.assertEqual(row['tone'], 'red')
                self.assertTrue(row.get('contraindicated'))
        ev_ich = evaluate(_go('cea_symp', q={'med_doac': True}, s={'brain_imaging': 'large_or_hemorrhage'}), today=TODAY)
        self.assertNotIn('OAC აღდგენა 48–72', ev_ich['surgical_brief']['postop_antithrombotic'])

        # R3-S1-05: No-space rivaroxaban 20მგ / 20mg / 2.5მგ regex boundaries
        ft1 = _detect_freetext_high_risk_meds('ქსარელტო 2.5 მგ, ქსარელტო 20მგ', q={'med_chips': ['rivaroxaban_2_5']})
        self.assertTrue(ft1['unstructured_antithrombotic'])
        ft2 = _detect_freetext_high_risk_meds('Xarelto 2.5 mg + Xarelto 20mg', q={'med_chips': ['rivaroxaban_2_5']})
        self.assertTrue(ft2['unstructured_antithrombotic'])
        ft3 = _detect_freetext_high_risk_meds('ქსარელტო 2.5მგ', q={'med_chips': ['rivaroxaban_2_5']})
        self.assertFalse(ft3['unstructured_antithrombotic'])

        # R3-S1-06: Unstructured high-risk med on bleed STOP stays in guide['urgent_meds']
        p_un = _go('cea_symp', q={'other_meds': 'ქსარელტო 20 მგ'}, s={'brain_imaging': 'large_or_hemorrhage'})
        guide_un = nurse_plan.build_patient_plain_explanation(p_un, evaluate(p_un, today=TODAY), today=TODAY)
        self.assertTrue(any('ქსარელტო' in m['drug'] or 'DOAC' in m['drug'] for m in guide_un['urgent_meds']))

    def test_r3_s1_07_08_14_nsaid_allergy_unfilled_and_ali_clti(self):
        # R3-S1-07: Clopidogrel mono + ASA allergy + neuraxial MUST hard-block ASRA!
        ev_nx = evaluate(_go('bypass_clti', q={'med_aspirin': False, 'med_clopidogrel': True, 'allergy_chips': ['nsaid']}, s={'anesthesia_plan': 'neuraxial'}), today=TODAY)
        self.assertTrue(ev_nx['neuraxial_hard_blocked'])

        # R3-S1-07: ASA/NSAID allergy blocks VOYAGER & suppresses aspirin recommendations across CEA/PTA/ABF & solo ticagrelor hold
        ev_voy = evaluate(_go('bypass_clti', q={'allergy_text': 'ასპირინზე ანაფილაქსია'}, s={'post_antithrombotic_plan': 'voyager'}), today=TODAY)
        self.assertTrue(any('VOYAGER' in rf for rf in ev_voy['red_flags']))
        self.assertNotIn('ასპირინი 100 მგ', ev_voy['surgical_brief']['postop_antithrombotic'])
        ev_cea = evaluate(_go('cea_symp', q={'med_aspirin': False, 'allergy_chips': ['nsaid']}), today=TODAY)
        cea_ap = [r for r in ev_cea['reflex_orders'] if r['key'] == 'cea_antiplatelet']
        self.assertTrue(cea_ap and 'კლოპიდოგრელ' in cea_ap[0]['action'] and 'ასპირინი' not in cea_ap[0]['action'])
        ev_pta = evaluate(_go('pta_clti', q={'med_doac': True, 'allergy_chips': ['clopidogrel_intol', 'nsaid']}), today=TODAY)
        self.assertFalse(any('OAC + ასპირინი' in rf for rf in ev_pta['red_flags']))
        ev_tic = evaluate(_go('open', q={'med_aspirin': False, 'med_ticagrelor': True, 'allergy_chips': ['nsaid']}, s={'planned_surgery_date': '2026-12-05'}), today=TODAY)
        tic_row = next(r for r in ev_tic['med_schedule'] if 'ტიკაგრელორი' in r['drug'])
        self.assertEqual(tic_row['tone'], 'red')
        self.assertFalse(tic_row['hold_or_skip'])

        # R3-S1-08: filled=False / self_filled=True MUST NOT drop metformin/sglt2/glp1/mech-valve/lidocaine/wet-infection
        ev_mf = evaluate(_go('open', q={'filled': False, 'metformin': True}, s={'creat': 300, 'weight_kg': 60}), today=TODAY)
        self.assertTrue(any('მეტფორმინი' in rf for rf in ev_mf['red_flags']))
        self.assertTrue(any('მეტფორმინი' in r['drug'] for r in ev_mf['med_schedule']))
        ev_sg = evaluate(_go('open', q={'filled': False, 'sglt2': True}), today=TODAY)
        self.assertTrue(any(dc['key'] == 'dos_ketones_ok' for dc in ev_sg['dos_checks']))
        ev_gl = evaluate(_go('evla', q={'filled': False, 'glp1': True}), today=TODAY)
        self.assertTrue(ev_gl['needs_anesth'])
        ev_mv = evaluate(_go('open', q={'filled': False, 'self_filled': True, 'nurse_verified': False, 'cad_chips': ['mech_valve'], 'med_doac': True}), today=TODAY)
        self.assertTrue(any('მექანიკურ სარქველზე' in rf for rf in ev_mv['red_flags']))
        self.assertTrue(ev_mv['has_blocking_unknown_state'])
        ev_wet = evaluate(_go('abf_clti', q={'filled': False, 'abf_wet_infection': True}), today=TODAY)
        self.assertTrue(any('სველი' in rf for rf in ev_wet['red_flags']))

        # R3-S1-14: ALI <2w MUST fire even when abf_rest_pain=True or CLTI subtype
        for sub in ('abf_clti', 'bypass_clti', 'pta_clti'):
            ev_ali = evaluate(_go(sub, q={'abf_duration': '< 2 კვირა', 'abf_rest_pain': True}), today=TODAY)
            self.assertTrue(any('ALI' in rf for rf in ev_ali['red_flags']), f'Missed ALI on {sub}')

    def test_r3_s1_10_11_12_13_neuraxial_plt_pregnancy_and_renal_saturation(self):
        # R3-S1-10: Neuraxial MUST stay hard-blocked on bp_sys=25, hr=270, creat=3500+DOAC, direct nan dos_inr/fibrinogen
        for patch_q, patch_s in (
            ({}, {'anesthesia_plan': 'neuraxial', 'bp_sys': 25, 'bp_dia': 20}),
            ({}, {'anesthesia_plan': 'neuraxial', 'hr': 270}),
            ({'med_doac': True}, {'anesthesia_plan': 'neuraxial', 'creat': 3500, 'creat_unit': 'umol'}),
            ({}, {'anesthesia_plan': 'neuraxial', 'dos_inr': 'nan'}),
            ({}, {'anesthesia_plan': 'neuraxial', 'fibrinogen': 'abc'}),
        ):
            ev = evaluate(_go('phlebectomy', q=patch_q, s=patch_s), today=TODAY)
            self.assertTrue(ev['neuraxial_hard_blocked'], f'Failed neuraxial block for {patch_s}')
            self.assertNotIn('✓', ev['anesthetic_brief']['neuraxial_gate'])

        # R3-S1-11: PLT 2500..2999 NEVER yields GO and never drops ASA IV -> I
        ev_2600 = evaluate(_go('open', s={'plt': 2600}), today=TODAY)
        self.assertNotEqual(ev_2600['clearance_seal']['code'], 'go')
        self.assertNotEqual(ev_2600['status'], STATUS_READY)
        ev_2999 = evaluate(_go('phlebectomy', s={'plt': 2999}), today=TODAY)
        ev_3000 = evaluate(_go('phlebectomy', s={'plt': 3000}), today=TODAY)
        self.assertGreaterEqual(['I', 'II', 'III', 'IV'].index(ev_2999['auto_asa']), ['I', 'II', 'III', 'IV'].index(ev_3000['auto_asa']))

        # R3-S1-12: Reproductive female with pregnant_possible=True + ack_alerts on EVAR/PTA-CLTI NEVER yields GO
        for sub in ('evar', 'tbd', 'pta_clti'):
            p = _go(sub, sex='F', age=30, q={'pregnant_possible': True}, s={'dos_bhcg_negative': False})
            ev0 = evaluate(p, today=TODAY)
            p.alerts_ack_at = timezone.now()
            p.studies['alerts_ack_sig'] = ev0['alerts_sig']
            ev1 = evaluate(p, today=TODAY)
            self.assertNotEqual(ev1['clearance_seal']['code'], 'go', f'False GO on pregnant_possible {sub}')

        # R3-S1-13: creat > 3000 umol/L preserves worst-case renal contraindications monotonically
        ev_3500 = evaluate(_go('bypass_claud', q={'metformin': True, 'med_chips': ['statin', 'cilostazol', 'rivaroxaban_2_5']}, s={'creat': 3500, 'creat_unit': 'umol', 'post_antithrombotic_plan': 'voyager'}), today=TODAY)
        self.assertTrue(any('მეტფორმინი' in rf for rf in ev_3500['red_flags']))
        self.assertTrue(any('VOYAGER' in rf for rf in ev_3500['red_flags']))
        self.assertTrue(any('ცილოსტაზოლი' in rf for rf in ev_3500['red_flags']))

    def test_r3_s2_03_04_05_07_08_09_10_monotonicity_crashes_meds_and_dates(self):
        # R3-S2-03: S1 POST & Tab 1 POST & BMI > 90 / < 8 & inverted BP preserve monotonicity
        p = _go('open', s={'creat_unit': 'umol'})
        s1 = dict(p.studies)
        _parse_s1_numbers_and_dates(QueryDict('creat=3500&creat_unit=umol&lvef=4&spo2=5&k=25'), s1, TODAY)
        p.studies = s1
        ev_s1 = evaluate(p, today=TODAY)
        self.assertEqual(ev_s1['auto_asa'], 'IV')
        self.assertGreaterEqual(ev_s1['rcri']['score'], 2)
        apply_questionnaire_post(QueryDict('aaa_mm=250&mets_ok=1&med_aspirin=1'), p, 'Nurse')
        self.assertTrue(evaluate(p, today=TODAY)['has_blocking_unknown_state'])
        ev_bmi_hi = evaluate(_go('evla', age=50, s={'weight_kg': 141, 'height_cm': 125}), today=TODAY)
        self.assertGreaterEqual(['I', 'II', 'III', 'IV'].index(ev_bmi_hi['auto_asa']), 2)
        ev_bmi_lo = evaluate(_go('evla', age=50, s={'weight_kg': 31, 'height_cm': 200}), today=TODAY)
        self.assertEqual(ev_bmi_lo['auto_asa'], 'IV')
        ev_inv = evaluate(_go('phlebectomy', s={'anesthesia_plan': 'neuraxial', 'bp_sys': 75, 'bp_dia': 80}), today=TODAY)
        self.assertTrue(ev_inv['neuraxial_hard_blocked'])

        # R3-S2-04 & 05: ISO datetime today, non-iterable chips, non-dict _invalid_inputs, future cta_date
        ev_iso = evaluate(_go('cea_symp', q={'cea_symp_event': True, 'cea_event_date': '2025-05-10'}), today='2025-05-15T10:30:00')
        self.assertEqual(ev_iso['days_since_ev'], 5)
        self.assertIsInstance(build_self_fill_highlights({'filled': True, 'allergy_chips': 123}), list)
        s_bad = {'_invalid_inputs': 'bad'}
        _set_num_field(s_bad, 'k', '25', lo=0.5, hi=20)
        self.assertIsNone(s_bad['k'])
        ev_fut = evaluate(_go('open', s={'cta_date': '2027-01-01'}), today=TODAY)
        self.assertNotEqual(ev_fut['clearance_seal']['code'], 'go')

        # R3-S2-07 & 08: Metformin EVLA+neuraxial CKD3b, metformin STOP-lock & blank op_date, DoS STOP EVLA fasting, free-text GLP-1
        ev_m_nx = evaluate(_go('evla', q={'metformin': True}, s={'anesthesia_plan': 'neuraxial', 'creat': 160, 'creat_unit': 'umol'}), today=TODAY)
        mf_row = next(r for r in ev_m_nx['med_schedule'] if 'მეტფორმინი' in r['drug'])
        self.assertIn('48 სთ', mf_row['rule'])
        ev_m_stop = evaluate(_go('cea_symp', q={'metformin': True}, s={'carotid_r': 100, 'planned_surgery_date': '2026-12-06'}), today=TODAY)
        mf_stop = next(r for r in ev_m_stop['med_schedule'] if 'მეტფორმინი' in r['drug'])
        self.assertNotIn('06.12', mf_stop['date_plan'])
        p_m_blank = _go('evla', q={'metformin': True}, s={'planned_surgery_date': ''})
        sc_blank = nurse_plan.build_phone_med_script(p_m_blank, evaluate(p_m_blank, today=TODAY), today=TODAY)
        self.assertFalse(any('მეტფორმინი' in r['drug'] for r in sc_blank['keep_rows']))
        ev_dos_evla = evaluate(_go('evla', s={'dos_bp_sys': 195, 'dos_bp_dia': 115}), today=TODAY)
        self.assertNotIn('მსუბუქი საუზმე', ev_dos_evla['fasting_instruction'])
        ev_ft_glp = evaluate(_go('evar', q={'other_meds': 'ოზემპიკი 1 მგ კვირაში'}), today=TODAY)
        self.assertIn('24 სთ', ev_ft_glp['fasting_instruction'])

        # R3-S2-09 & 10: EVLA mech valve dos_inr<2.0, anamnesis age 56 pregnancy, RCRI nurse badge, lidocaine PTA
        ev_ev_inr = evaluate(_go('evla', q={'cad_chips': ['mech_valve'], 'med_warfarin': True}, s={'inr': 2.5, 'dos_inr': 1.4}), today=TODAY)
        self.assertTrue(any('1.4' in rf or 'სუბთერაპიული' in rf for rf in ev_ev_inr['red_flags']))
        p_56 = _go('evla', sex='F', age=56, q={'ven_pregnant': True})
        self.assertNotIn('ორსულობა', generate_anamnesis(p_56, evaluate(p_56, today=TODAY)))
        ev_rcri1 = evaluate(_go('evla', q={'cad': True}), today=TODAY)
        scores_b = {b['label']: b for b in nurse_ui.plain_risk_badges(ev_rcri1)}
        self.assertEqual(scores_b['გულის რისკი']['tone'], 'ok')
        ev_lid_pta = evaluate(_go('pta_clti', q={'ven_lidocaine_allergy': True}), today=TODAY)
        self.assertTrue(any('ლიდოკაინზე' in rf for rf in ev_lid_pta['red_flags']))

    def test_r3_s2_01_02_registry_attribution_and_honesty(self):
        # Verify critical previously hijacked live emissions map to their EXACT canonical rule
        ev_rtpa = evaluate(_go('cea_symp', q={'cea_thrombolysis': True, 'cea_days_since_event': 0, 'med_aspirin': True}), today=TODAY)
        asp_rtpa = next(r for r in annotate_med_rows(ev_rtpa['med_schedule']) if 'ასპირინი' in r['drug'])
        self.assertEqual(asp_rtpa['rule_meta']['id'], 'CEA_ACUTE_NEURO_THROMBOLYSIS')
        ev_asa_al = evaluate(_go('open', q={'med_aspirin': True, 'allergy_chips': ['nsaid']}), today=TODAY)
        asp_al = next(r for r in annotate_med_rows(ev_asa_al['med_schedule']) if 'ასპირინი' in r['drug'])
        self.assertEqual(asp_al['rule_meta']['id'], 'ALLERGY_HIT_ABX_PROPHYLAXIS')
        ev_mv_w = evaluate(_go('open', q={'cad_chips': ['mech_valve'], 'med_warfarin': True}), today=TODAY)
        w_mv = next(r for r in annotate_med_rows(ev_mv_w['med_schedule']) if 'ვარფარინი' in r['drug'])
        self.assertEqual(w_mv['rule_meta']['id'], 'MED_WARFARIN_HOLD_DAYS')
        ev_cilo = evaluate(_go('bypass_clti', q={'med_chips': ['statin', 'cilostazol', 'ppi']}, s={'lvef': 35}), today=TODAY)
        cilo_r = next(r for r in annotate_med_rows(ev_cilo['med_schedule']) if 'ცილოსტაზოლი' in r['drug'])
        self.assertEqual(cilo_r['rule_meta']['id'], 'MED_CILOSTAZOL_BLACKBOX')
        ev_st = evaluate(_go('open', q={'med_chips': ['statin', 'amiodarone']}), today=TODAY)
        for row in annotate_med_rows(ev_st['med_schedule']):
            if row['drug'] in ('სტატინი', 'ამიოდარონი'):
                self.assertEqual(row['rule_meta']['id'], 'MED_CARDIO_VASOACTIVE_STEROID')
        ev_voy_r = evaluate(_go('bypass_clti', q={'med_chips': ['statin', 'rivaroxaban_2_5']}, s={'creat': 300, 'creat_unit': 'umol'}), today=TODAY)
        voy_r = next(r for r in annotate_med_rows(ev_voy_r['med_schedule']) if '2.5' in r['drug'])
        self.assertEqual(voy_r['rule_meta']['id'], 'MED_VOYAGER_RIVAROXABAN_2_5')
        ev_cyp = evaluate(_go('open', q={'med_clopidogrel': True, 'med_chips': ['statin', 'ppi']}), today=TODAY)
        cyp_af = next(a for a in ev_cyp['amber_flags'] if 'CYP2C19' in a)
        self.assertEqual(match_rule_for_text(cyp_af).id, 'MED_ANTITHROMBOTIC_CONFLICTS')
        self.assertEqual(RULES_BY_ID['ASRA_NEURAXIAL_COAG_GATE'].status, STATUS_LOCAL)
        self.assertEqual(RULES_BY_ID['LAB_WBC_PLT_COAG'].status, STATUS_LOCAL)
        self.assertIn('18.0', RULES_BY_ID['GLY_UNIT_CONVERSION_MGDL'].value_label)
        self.assertNotIn('18.0182', RULES_BY_ID['GLY_UNIT_CONVERSION_MGDL'].value_label)


class StressR3HttpAndTemplateTests(TestCase):
    """R3-S1-01, R3-S1-09, R3-S2-04, R3-S2-06, R3-S2-10 live Django view & template tests."""

    def setUp(self):
        User = get_user_model()
        self.doc = User.objects.create_user(username='r3_doc', password='R3DocPassword!1', is_staff=True)
        self.client.force_login(self.doc)

    def test_r3_s1_01_09_partial_new_ui_forms_and_dos_invalid_stop(self):
        # R3-S1-01: patient_new.html nw-vitals-form MUST NOT wipe S1 safety booleans!
        p = _go('bypass_clti', s={'anesthesia_plan': 'neuraxial', 'aptt_prolonged': True, 'echo_valve_issue': True, 'urine_nitrite': True, 'vqi_discharge_antiplatelet': True, 'icu_bed_booked': True})
        p.save()
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'stage1', 's1_partial': '1', 'bp_sys': '128', 'bp_dia': '78', 'hr': '72', 'spo2': '98',
        })
        p.refresh_from_db()
        self.assertTrue(p.studies.get('aptt_prolonged'))
        self.assertTrue(p.studies.get('echo_valve_issue'))
        self.assertTrue(p.studies.get('urine_nitrite'))
        self.assertTrue(evaluate(p, today=TODAY)['neuraxial_hard_blocked'])

        # R3-S1-01 (S2-2): patient_new.html nw-dos-form MUST NOT wipe unrendered VQI flags (or unrendered ICU flag on non-ICU patient)!
        self.client.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'dos_vqi', 'dos_partial': '1', 'dos_identity_consent': '1', 'icu_bed_booked': '1',
        })
        p.refresh_from_db()
        self.assertTrue(p.studies.get('vqi_discharge_antiplatelet'))
        self.assertTrue(p.studies.get('icu_bed_booked'))
        p_no_icu = _go('evla', s={'icu_bed_booked': True, 'vqi_discharge_statin': True})
        p_no_icu.save()
        self.client.post(reverse('preop-patient', args=[p_no_icu.pk]), {
            'section': 'dos_vqi', 'dos_partial': '1', 'dos_identity_consent': '1',
        })
        p_no_icu.refresh_from_db()
        self.assertTrue(p_no_icu.studies.get('icu_bed_booked'))
        self.assertTrue(p_no_icu.studies.get('vqi_discharge_statin'))

        # R3-S1-09: Extreme/garbage DoS vitals POST MUST keep dos_hold_danger=True STOP & invalidate approved_at!
        p_app = _go('open', approved_at=timezone.now(), approved_by=self.doc)
        p_app.save()
        self.client.post(reverse('preop-patient', args=[p_app.pk]), {
            'section': 'dos_vqi', 'dos_form_present': '1', 'dos_bp_sys': '400', 'dos_bp_dia': '80',
        })
        p_app.refresh_from_db()
        ev_d = evaluate(p_app, today=TODAY)
        self.assertTrue(ev_d['dos_hold_danger'])
        self.assertEqual(ev_d['clearance_seal']['code'], 'stop')
        self.assertIsNone(p_app.approved_at)

    def test_r3_s2_04_06_10_week_overflow_signoff_med_hold_and_templates(self):
        # R3-S2-04: Extreme week & reschedule dates NEVER HTTP 500
        self.assertEqual(self.client.get(reverse('preop'), {'week': '9999-12-31'}).status_code, 200)
        self.assertEqual(self.client.get(reverse('preop'), {'week': '0001-01-01'}).status_code, 200)
        p_max = _go('open', s={'planned_surgery_date': '9999-12-30'})
        p_max.save()
        self.assertIn(self.client.post(reverse('preop-reschedule', args=[p_max.pk]), {'action': 'shift_3d'}).status_code, (200, 302))

        # R3-S2-06: Stale labs blocks approve; S2 introducing STOP clears med_hold_agreed; blank->date reschedule invalidates approval
        p_st = _go('open', s={'labs_date': (TODAY - datetime.timedelta(days=20)).isoformat(), 'stale_labs_refreshed': False})
        p_st.save()
        self.client.post(reverse('preop-patient', args=[p_st.pk]), {'section': 'signoff', 'action': 'approve', 'move_ready': '1'})
        p_st.refresh_from_db()
        self.assertIsNone(p_st.approved_at)
        p_s2 = _go('evla', q={'med_doac': True}, s={'med_hold_agreed': False})
        p_s2.save()
        self.client.post(reverse('preop-patient', args=[p_s2.pk]), {'section': 'stage2', 'tp_r': '20', 'med_hold_agreed': '1'})
        p_s2.refresh_from_db()
        self.assertFalse(p_s2.studies.get('med_hold_agreed'))
        p_res = _go('open', approved_at=timezone.now(), approved_by=self.doc, someday_list=PreopPatient.SomedayList.READY, s={'planned_surgery_date': ''})
        p_res.save()
        self.client.post(reverse('preop-reschedule', args=[p_res.pk]), {'action': 'set_date', 'planned_surgery_date': '2026-12-10'})
        p_res.refresh_from_db()
        self.assertIsNone(p_res.approved_at)
        self.assertEqual(p_res.someday_list, PreopPatient.SomedayList.CONF)

        # R3-S2-10: Passport normalizes wbc=7500/plt=80000, highlights BP/LVEF, renders date_plan when force_date_plan=True without op date
        p_pp = _go('open', q={'cad_chips': ['mech_valve'], 'med_doac': True}, s={'planned_surgery_date': '', 'wbc': 7500, 'plt': 80000, 'bp_sys': 205, 'bp_dia': 120, 'lvef': 28})
        p_pp.save()
        h_pp = self.client.get(reverse('preop-passport', args=[p_pp.pk])).content.decode()
        self.assertIn('უკუნაჩვენებია — კარდიოლოგი!', h_pp)
        self.assertIn('class="bad">205/120', h_pp)
        self.assertIn('class="bad">LVEF 28%', h_pp)
        p_nrm = _go('open', s={'wbc': 7500, 'plt': 250000})
        p_nrm.save()
        h_nrm = self.client.get(reverse('preop-passport', args=[p_nrm.pk])).content.decode()
        self.assertNotIn('class="bad">140 გ/ლ · 7.5 · 250', h_nrm)
