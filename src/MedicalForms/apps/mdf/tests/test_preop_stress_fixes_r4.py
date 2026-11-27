"""Test-first regression suite for Phase 4 Round-3 re-verification findings (F1-F10, R3V-S1-01..07, R3V-S2-01..10, A1-A18)."""
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
    make_self_fill_token,
    nurse_plan,
    nurse_ui,
)
from ..preop.forms import apply_questionnaire_post
from ..preop.meds import _detect_freetext_high_risk_meds
from ..preop.registry import RULES_BY_ID, STATUS_LOCAL, annotate_med_rows, match_rule_for_text
from ..preop.views.patient import _parse_s1_numbers_and_dates

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
        'planned_surgery_date': '2026-12-06',
    }
    s.update(kw.pop('s', {}))
    defaults = dict(
        patient_name='სატესტო რ4', age=65, sex='M', side='' if pw in ('aaa_infra', 'abf') else 'R',
        pathway=pw, op_subtype=sub, visit_date=datetime.date(2026, 11, 25),
        started_on=datetime.date(2026, 11, 25), cardio_decision='cleared', questionnaire=q, studies=s,
    )
    defaults.update(kw)
    return PreopPatient(**defaults)


class PreopStressFixesRound4UnitTests(SimpleTestCase):
    """Pure engine/calculator/meds/nurse/registry regressions for Round-4 fixes."""

    def test_f1_f2_f4_f8_extreme_and_inverted_monotonicity(self):
        # F1: S1 POST extreme BP/HR and direct <0 / >600 MUST keep neuraxial_hard_blocked=True
        for payload in ('bp_sys=25&bp_dia=20', 'hr=275', 'hr=10'):
            p = _go('phlebectomy', s={'anesthesia_plan': 'neuraxial'})
            sm = dict(p.studies)
            _parse_s1_numbers_and_dates(QueryDict(payload), sm, TODAY)
            p.studies = sm
            ev = evaluate(p, today=TODAY)
            self.assertTrue(ev['neuraxial_hard_blocked'], f'F1 failed for S1 POST {payload}')
            self.assertNotIn('დაცულია', ev['anesthetic_brief']['neuraxial_gate'])
        for direct_s in ({'bp_sys': -5, 'bp_dia': 20}, {'hr': 700}, {'hr': -10}):
            p = _go('phlebectomy', s={'anesthesia_plan': 'neuraxial', **direct_s})
            self.assertTrue(evaluate(p, today=TODAY)['neuraxial_hard_blocked'], f'F1 direct {direct_s}')

        # F2: S1 POST creat=3500 umol/L saturates to worst-case renal failure, preserving NX & renal contra
        p_cr = _go(
            'bypass_claud',
            q={'med_doac': True, 'metformin': True, 'med_chips': ['statin', 'cilostazol', 'rivaroxaban_2_5']},
            s={'anesthesia_plan': 'neuraxial', 'creat': 250, 'creat_unit': 'umol', 'post_antithrombotic_plan': 'voyager'},
        )
        sm = dict(p_cr.studies)
        _parse_s1_numbers_and_dates(QueryDict('creat=3500&creat_unit=umol'), sm, TODAY)
        p_cr.studies = sm
        ev_cr = evaluate(p_cr, today=TODAY)
        self.assertIsNotNone(ev_cr['egfr'])
        self.assertLess(ev_cr['egfr'], 10)
        self.assertTrue(ev_cr['neuraxial_hard_blocked'])
        contra_drugs = {r['drug'] for r in ev_cr['med_schedule'] if r.get('contraindicated')}
        self.assertTrue({'მეტფორმინი', 'ცილოსტაზოლი (პლეტალი)', 'ქსარელტო 2.5 მგ x2'}.issubset(contra_drugs))
        self.assertTrue(any('VOYAGER' in rf for rf in ev_cr['red_flags']))

        # F4: Extreme LVEF/K+/BP/HR never drop contraindications, acute holds, succinylcholine guard, or dos_k_verified
        ev_lvef = evaluate(_go('evla', q={'med_chips': ['statin', 'cilostazol']}, s={'lvef': 4}), today=TODAY)
        cilo_row = next(r for r in ev_lvef['med_schedule'] if 'ცილოსტაზოლ' in r['drug'])
        self.assertTrue(cilo_row.get('contraindicated'))
        ev_hr_hi = evaluate(_go('evla', q={'med_chips': ['statin', 'cilostazol']}, s={'hr': 275}), today=TODAY)
        self.assertTrue(next(r for r in ev_hr_hi['med_schedule'] if 'ცილოსტაზოლ' in r['drug']).get('contraindicated'))
        ev_hr_lo = evaluate(_go('evla', q={'med_chips': ['statin', 'beta_blocker']}, s={'hr': 10}), today=TODAY)
        bb_lo = next(r for r in ev_hr_lo['med_schedule'] if 'ბეტა' in r['drug'])
        self.assertTrue(bb_lo.get('acute_hold'))
        self.assertEqual([r for r in nurse_plan.build_phone_med_script(_go('evla'), ev_hr_lo, TODAY)['keep_rows'] if 'ბეტა' in r['raw_drug']], [])
        ev_bp_lo = evaluate(_go('evla', q={'med_chips': ['statin', 'beta_blocker', 'acei_arb', 'ccb']}, s={'bp_sys': 25, 'bp_dia': 20}), today=TODAY)
        self.assertEqual({r['drug'] for r in ev_bp_lo['med_schedule'] if r.get('acute_hold')}, {'ბეტა-ბლოკატორი', 'ACEi / ARB (პრილი/სარტანი)', 'ამლოდიპინი / CCB'})
        ev_k_hi = evaluate(_go('evla', q={'med_chips': ['statin', 'acei_arb']}, s={'k': 15.0}), today=TODAY)
        self.assertTrue(next(r for r in ev_k_hi['med_schedule'] if 'ACEi' in r['drug']).get('acute_hold'))
        self.assertIn('სუქცინილქოლინი', ev_k_hi['anesthetic_brief']['airway_summary'])
        self.assertIn('dos_k_verified', [c['key'] for c in ev_k_hi['dos_checks']])

        # F8: Inverted BP never drops ASA IV/III or BB/ACEi/CCB acute holds (while preserving 85/120 & 80/100 != IV)
        ev_inv_lo = evaluate(_go('evla', q={'med_chips': ['statin', 'beta_blocker', 'acei_arb', 'ccb']}, s={'bp_sys': 85, 'bp_dia': 90, 'hr': 72}), today=TODAY)
        self.assertEqual(ev_inv_lo['auto_asa'], 'IV')
        self.assertEqual({r['drug'] for r in ev_inv_lo['med_schedule'] if r.get('acute_hold')}, {'ბეტა-ბლოკატორი', 'ACEi / ARB (პრილი/სარტანი)', 'ამლოდიპინი / CCB'})
        ev_inv_hi = evaluate(_go('evla', s={'bp_sys': 190, 'bp_dia': 195, 'hr': 72}), today=TODAY)
        self.assertEqual(ev_inv_hi['auto_asa'], 'III')
        self.assertEqual(evaluate(_go('evla', s={'bp_sys': 85, 'bp_dia': 120, 'hr': 72}), today=TODAY)['auto_asa'], 'IV')
        self.assertEqual(evaluate(_go('evla', s={'bp_sys': 80, 'bp_dia': 100, 'hr': 85}), today=TODAY)['auto_asa'], 'IV')
        self.assertNotEqual(evaluate(_go('evla', s={'bp_sys': 85, 'bp_dia': 120, 'bp_sys_l': 120, 'bp_dia_l': 80, 'hr': 72}), today=TODAY)['auto_asa'], 'IV')

    def test_f3_f6_f7_f9_f10_failsafe_and_unhashable(self):
        # F3: pregnant_possible=True + dos_bhcg_negative=False permanently blocks GO on elective arterial subtypes
        # and blocks GO before multidisciplinary consilium ack on urgent CLTI/symp-CEA without Catch-22 deadlock
        for sub in ('open', 'evar', 'cea_asymp', 'abf_claud', 'bypass_claud', 'pta_claud'):
            p = _go(sub, sex='F', age=30, q={'pregnant_possible': True}, s={'dos_bhcg_negative': False})
            ev0 = evaluate(p, today=TODAY)
            p.alerts_ack_at = timezone.now()
            p.studies['alerts_ack_sig'] = ev0['alerts_sig']
            ev1 = evaluate(p, today=TODAY)
            self.assertNotEqual(ev1['clearance_seal']['code'], 'go', f'F3 failed on {sub}')
            self.assertNotEqual(ev1['status'], STATUS_READY, f'F3 status failed on {sub}')
            self.assertTrue(ev1['has_blocking_unknown_state'], f'F3 blocking unknown failed on {sub}')
        for sub in ('cea_symp', 'abf_clti', 'bypass_clti', 'pta_clti'):
            p = _go(sub, sex='F', age=30, q={'pregnant_possible': True}, s={'dos_bhcg_negative': False})
            ev0 = evaluate(p, today=TODAY)
            self.assertNotEqual(ev0['clearance_seal']['code'], 'go', f'F3 urgent unacked failed on {sub}')
            self.assertTrue(ev0['has_unacked_red'], f'F3 urgent red flag failed on {sub}')

        # F6: Unhashable dict in chip list never crashes build_self_fill_highlights / chip_summary
        hi = build_self_fill_highlights({'filled': True, 'allergy_chips': [{'bad': 1}]})
        self.assertIsInstance(hi, list)

        # F7: Ambiguous creat=25 with blank unit blocks GO even on lean EVLA
        p_amb = _go('evla', s={'creat': 25, 'creat_unit': ''})
        ev_amb0 = evaluate(p_amb, today=TODAY)
        p_amb.alerts_ack_at = timezone.now()
        p_amb.studies['alerts_ack_sig'] = ev_amb0['alerts_sig']
        ev_amb = evaluate(p_amb, today=TODAY)
        self.assertTrue(ev_amb['has_blocking_unknown_state'])
        self.assertNotEqual(ev_amb['clearance_seal']['code'], 'go')

        # F9 + R3V-S2-05: self_fill_status_info and build_self_fill_highlights honor self_filled=True even if filled=False
        for q_sf in (
            {'filled': True, 'self_filled': True, 'self_fill_submitted': False, 'nurse_verified': False},
            {'filled': False, 'self_filled': True, 'nurse_verified': False, 'aaa_pain': True, 'med_warfarin': True},
        ):
            ev_sf = evaluate(_go('open', q=q_sf), today=TODAY)
            info = nurse_ui.self_fill_status_info(ev_sf)
            self.assertEqual(info['code'], 'returned')
            self.assertEqual(info['tone'], 'warn')
            self.assertTrue(ev_sf['is_self_filled'])
            if q_sf.get('aaa_pain'):
                self.assertTrue(ev_sf['self_fill_highlights'])

        # F10: Future cea_event_date='2099-01-01' blocks GO (direct AND Tab 1 POST)
        p_cea = _go('cea_symp', q={'cea_symp_event': True, 'cea_event_date': '2099-01-01'})
        ev_cea = evaluate(p_cea, today=TODAY)
        self.assertTrue(ev_cea['has_blocking_unknown_state'])
        self.assertNotEqual(ev_cea['clearance_seal']['code'], 'go')
        p_cea2 = _go('cea_symp', q={'cea_symp_event': True})
        apply_questionnaire_post(QueryDict('cea_symp_event=1&cea_event_date=2099-01-01'), p_cea2, 'ექთანი')
        self.assertTrue(evaluate(p_cea2, today=TODAY)['has_blocking_unknown_state'])

    def test_r3v_s1_01_02_03_04_07_a1_a2_pharmacology_allergies_rtpa(self):
        # R3V-S1-01: Mechanical valve + NSAID allergy + Clopidogrel intol NEVER recommends VKA + Ticagrelor
        for sub in ('open', 'bypass_clti', 'pta_clti'):
            ev_mv = evaluate(_go(sub, q={'cad_chips': ['mech_valve'], 'med_warfarin': True, 'med_aspirin': False, 'allergy_chips': ['nsaid', 'clopidogrel_intol']}), today=TODAY)
            self.assertNotIn('ტიკაგრელორ', ev_mv['surgical_brief']['postop_antithrombotic'])
            self.assertNotIn('ტიკაგრელორ', ev_mv['post_antithrombotic_label'])

        # R3V-S1-02: PTA dapt task never prints Aspirin or VOYAGER when ASA/NSAID allergic
        ev_pta_alg = evaluate(_go('pta_clti', q={'med_aspirin': False, 'med_clopidogrel': True, 'allergy_chips': ['nsaid']}), today=TODAY)
        dapt_task = next(t for t in ev_pta_alg['tasks'] if t['key'] == 'dapt')
        self.assertNotIn('ასპირინი', dapt_task['label'])
        self.assertNotIn('VOYAGER', dapt_task['label'])

        # R3V-S1-03: Explicit post_antithrombotic_plan flags & overrides allergic/contraindicated labels
        ev_dapt_alg = evaluate(_go('bypass_clti', q={'med_aspirin': False, 'allergy_chips': ['nsaid', 'clopidogrel_intol']}, s={'post_antithrombotic_plan': 'dapt'}), today=TODAY)
        self.assertTrue(any('DAPT' in rf or 'ალერგია' in rf for rf in ev_dapt_alg['red_flags']))
        self.assertNotIn('DAPT: ასპირინი + კლოპიდოგრელი', ev_dapt_alg['post_antithrombotic_label'])
        ev_sapt_dbl = evaluate(_go('bypass_clti', q={'med_aspirin': False, 'allergy_chips': ['nsaid', 'clopidogrel_intol']}, s={'post_antithrombotic_plan': 'sapt'}), today=TODAY)
        self.assertTrue(any('SAPT' in rf or 'ალერგია' in rf for rf in ev_sapt_dbl['red_flags']))
        self.assertNotIn('კლოპიდოგრელი 75 მგ ან ასპირინი', ev_sapt_dbl['post_antithrombotic_label'])
        ev_voy_dbl = evaluate(_go('bypass_clti', q={'med_aspirin': False, 'allergy_chips': ['nsaid', 'clopidogrel_intol']}, s={'post_antithrombotic_plan': 'voyager'}), today=TODAY)
        self.assertNotIn('შეარჩიეთ კლოპიდოგრელი', ev_voy_dbl['post_antithrombotic_label'])
        ev_voy_dial = evaluate(_go('bypass_clti', q={'on_dialysis': True}, s={'post_antithrombotic_plan': 'voyager'}), today=TODAY)
        self.assertIn('უკუნაჩვენებია', ev_voy_dial['post_antithrombotic_label'])

        # A1 + R3V-S1-04: ASA allergy + med_aspirin=True + P2Y12 NEVER says 'ასპირინის ფონზე' and hard-blocks NX; rivaroxaban_2_5 respects allergy & Triple AT / Ticagrelor
        ev_a1 = evaluate(_go('bypass_clti', q={'allergy_chips': ['nsaid'], 'med_aspirin': True, 'med_clopidogrel': True}, s={'anesthesia_plan': 'neuraxial'}), today=TODAY)
        clop_a1 = next(r for r in ev_a1['med_schedule'] if 'კლოპიდოგრელი' in r['drug'])
        self.assertNotIn('ასპირინის ფონზე', clop_a1['rule'])
        self.assertFalse(clop_a1['hold_or_skip'])
        self.assertTrue(ev_a1['neuraxial_hard_blocked'])
        ev_a1_tica = evaluate(_go('open', q={'allergy_chips': ['nsaid'], 'med_aspirin': True, 'med_ticagrelor': True}), today=TODAY)
        self.assertNotIn('ასპირინის ფონზე', next(r for r in ev_a1_tica['med_schedule'] if 'ტიკაგრელორი' in r['drug'])['rule'])
        ev_r25_alg = evaluate(_go('bypass_clti', q={'med_aspirin': False, 'allergy_chips': ['nsaid'], 'med_chips': ['statin', 'rivaroxaban_2_5']}), today=TODAY)
        r25_row = next(r for r in ev_r25_alg['med_schedule'] if 'ქსარელტო 2.5' in r['drug'])
        self.assertEqual(r25_row['tone'], 'red')
        self.assertTrue(r25_row.get('contraindicated'))
        self.assertNotIn('ასპირინი', r25_row['rule'].split('ალერგია')[0])
        self.assertTrue(any('რივაროქსაბანი 2.5' in rf or 'VOYAGER' in rf for rf in ev_r25_alg['red_flags']))
        ev_r25_t3 = evaluate(_go('evla', q={'med_aspirin': True, 'med_clopidogrel': True, 'med_chips': ['statin', 'rivaroxaban_2_5']}), today=TODAY)
        self.assertNotEqual(next(r for r in ev_r25_t3['med_schedule'] if 'ქსარელტო 2.5' in r['drug'])['tone'], 'ok')
        ev_r25_tica = evaluate(_go('evla', q={'med_aspirin': False, 'med_ticagrelor': True, 'med_chips': ['statin', 'rivaroxaban_2_5']}), today=TODAY)
        self.assertEqual(next(r for r in ev_r25_tica['med_schedule'] if 'ქსარელტო 2.5' in r['drug'])['tone'], 'red')

        # R3V-S1-07: Unfilled PTA + clopidogrel_intol + med_doac NEVER recommends Ticagrelor
        ev_pta_un = evaluate(_go('pta_clti', q={'filled': False, 'allergy_chips': ['clopidogrel_intol'], 'med_doac': True}), today=TODAY)
        clop_rf = [rf for rf in ev_pta_un['red_flags'] if 'კლოპიდოგრელის აუტანლობა' in rf][0]
        self.assertNotIn('ალტერნატიული P2Y12 (ტიკაგრელორი)', clop_rf)

        # A2: CEA post-rt-PA day 2 with brain_imaging='normal_small' blocks Warfarin LMWH bridging, DOAC, Clopidogrel, Cilostazol while keeping Aspirin allowed with neurologist consensus
        ev_rtpa_vka = evaluate(_go('cea_symp', q={'cea_symp_event': True, 'cea_days_since_event': 2, 'cea_thrombolysis': True, 'med_warfarin': True, 'cad_chips': ['afib']}, s={'brain_imaging': 'normal_small', 'inr': 2.3}), today=TODAY)
        vka_r = next(r for r in ev_rtpa_vka['med_schedule'] if r['drug'] == 'ვარფარინი')
        self.assertEqual(vka_r['tone'], 'red')
        self.assertTrue(vka_r.get('contraindicated'))
        self.assertNotIn('LMWH ხიდი', vka_r['rule'])
        self.assertNotIn('D+1 პროფილაქტ. LMWH', ev_rtpa_vka['surgical_brief']['postop_antithrombotic'])
        ev_rtpa_clop = evaluate(_go('cea_symp', q={'cea_symp_event': True, 'cea_days_since_event': 2, 'cea_thrombolysis': True, 'med_aspirin': True, 'med_clopidogrel': True}, s={'brain_imaging': 'normal_small'}), today=TODAY)
        clop_rt = next(r for r in ev_rtpa_clop['med_schedule'] if 'კლოპიდოგრელი' in r['drug'])
        asa_rt = next(r for r in ev_rtpa_clop['med_schedule'] if 'ასპირინი' in r['drug'])
        self.assertEqual(clop_rt['tone'], 'red')
        self.assertTrue(clop_rt.get('contraindicated'))
        self.assertIn('გრძელდება', asa_rt['rule'])
        self.assertEqual(asa_rt['tone'], 'amber')

        # R3V-S2-10 + A15: No clopidogrel advice when clopidogrel_intol; Warfarin+HIT+recent VTE mentions non-heparin bridging
        ev_pr_dbl = evaluate(_go('pta_clti', q={'med_prasugrel': True, 'med_aspirin': False, 'allergy_chips': ['nsaid', 'clopidogrel_intol']}), today=TODAY)
        pr_r = next(r for r in ev_pr_dbl['med_schedule'] if 'პრასუგრელი' in r['drug'])
        self.assertEqual(pr_r['tone'], 'red')
        self.assertFalse(pr_r['hold_or_skip'])
        self.assertNotIn('კლოპიდოგრელზე', pr_r['rule'])
        ev_hit_vte = evaluate(_go('open', q={'med_warfarin': True, 'allergy_chips': ['hit_heparin'], 'ven_dvt_history': True, 'ven_dvt_chips': ['vte_recent_3m']}), today=TODAY)
        vka_hv = next(r for r in ev_hit_vte['med_schedule'] if r['drug'] == 'ვარფარინი')
        self.assertIn('არაჰეპარინ', vka_hv['rule'])

    def test_r3v_s1_06_s2_05_a6_a7_a8_a9_a10_unfilled_ppi_tsh_plexus_evla(self):
        # R3V-S1-06: filled=False + DOAC/VKA + neuraxial NEVER outputs false green ASRA gate
        ev_nx_un = evaluate(_go('bypass_clti', q={'filled': False, 'med_doac': True}, s={'anesthesia_plan': 'neuraxial', 'med_hold_agreed': False}), today=TODAY)
        self.assertNotIn('დაცულია', ev_nx_un['anesthetic_brief']['neuraxial_gate'])
        ev_nx_vka = evaluate(_go('bypass_clti', q={'filled': False, 'med_warfarin': True}, s={'anesthesia_plan': 'neuraxial', 'inr': 1.3, 'med_hold_agreed': True}), today=TODAY)
        self.assertIn('≤1.2', ev_nx_vka['anesthetic_brief']['neuraxial_gate'])

        # R3V-S2-05 + A10: Unfilled safety signals preserved (CIED, dialysis K+, groin/wound, iodine contrast, mech_valve+HIT ASA IV, rivaroxaban_2_5, cilostazol, aspirin+allergy)
        self.assertTrue(evaluate(_go('open', q={'filled': False, 'pacemaker': True}), today=TODAY)['needs_cied'])
        self.assertIn('dos_k_verified', [c['key'] for c in evaluate(_go('open', q={'filled': False, 'on_dialysis': True}), today=TODAY)['dos_checks']])
        ev_gr = evaluate(_go('abf_clti', q={'filled': False, 'abf_groin_issue': True, 'abf_wet_infection': True}), today=TODAY)
        self.assertTrue(any('საზარდულის' in rf for rf in ev_gr['red_flags']))
        self.assertTrue(ev_gr['needs_wound_care'])
        self.assertIn('პრემედიკაცია', evaluate(_go('evar', q={'filled': False, 'allergy_iodine': True}), today=TODAY)['surgical_brief']['contrast_plan'])
        self.assertEqual(evaluate(_go('open', q={'filled': False, 'cad_chips': ['mech_valve'], 'allergy_chips': ['hit_heparin']}), today=TODAY)['auto_asa'], 'IV')
        ev_un_r25 = evaluate(_go('bypass_clti', q={'filled': False, 'med_chips': ['rivaroxaban_2_5']}, s={'creat': 250, 'creat_unit': 'umol'}), today=TODAY)
        self.assertTrue(any('ქსარელტო 2.5' in r['drug'] for r in ev_un_r25['med_schedule']))
        self.assertTrue(ev_un_r25['needs_med_hold'])
        ev_un_cil = evaluate(_go('bypass_clti', q={'filled': False, 'cad_chips': ['hf'], 'med_chips': ['cilostazol']}), today=TODAY)
        self.assertTrue(any('ცილოსტაზოლ' in r['drug'] for r in ev_un_cil['med_schedule']))
        ev_un_asa = evaluate(_go('open', q={'filled': False, 'med_aspirin': True, 'allergy_chips': ['nsaid']}), today=TODAY)
        self.assertTrue(any('ასპირინი' in r['drug'] and r.get('contraindicated') for r in ev_un_asa['med_schedule']))

        # A6: PPI reflex triggers for DAPT at age <65 AND is NOT suppressed when needs_gastro=True or gastro_result='acute_ulcer'
        self.assertTrue(evaluate(_go('open', age=58, q={'med_aspirin': True, 'med_clopidogrel': True, 'med_chips': ['statin']}), today=TODAY)['needs_ppi'])
        self.assertTrue(evaluate(_go('open', age=70, q={'med_aspirin': True, 'med_clopidogrel': True, 'med_chips': ['statin']}, s={'hb': 95}), today=TODAY)['needs_ppi'])
        ev_acu = evaluate(_go('open', age=55, q={'med_aspirin': True, 'med_chips': ['statin']}, s={'gastro_result': 'acute_ulcer'}), today=TODAY)
        self.assertTrue(ev_acu['needs_ppi'])
        self.assertTrue(any('PPI' in r['drug'] for r in ev_acu['med_schedule']))

        # A7: Amiodarone on Open AAA / ABF triggers TSH reflex
        self.assertTrue(evaluate(_go('open', q={'med_chips': ['statin', 'amiodarone']}), today=TODAY)['needs_tsh'])
        self.assertTrue(evaluate(_go('abf_clti', q={'med_chips': ['statin', 'amiodarone']}), today=TODAY)['needs_tsh'])

        # A8: EVLA + anesthesia_plan='plexus' + metformin + CKD 3b emits 48h hold + <=1000 mg/d
        ev_plx = evaluate(_go('evla', q={'dm': True, 'metformin': True}, s={'anesthesia_plan': 'plexus', 'creat': 155, 'creat_unit': 'umol'}), today=TODAY)
        metf_plx = next(r for r in ev_plx['med_schedule'] if 'მეტფორმინ' in r['drug'])
        self.assertEqual(metf_plx['tone'], 'amber')
        self.assertIn('48 სთ', metf_plx['rule'])

        # A9: EVLA Ticagrelor / Cilostazol does NOT emit 'მოუხსნელადაც' amber flag
        for q_ev in ({'med_chips': ['statin', 'cilostazol']}, {'med_aspirin': True, 'med_ticagrelor': True}):
            ev_ec = evaluate(_go('evla', q=q_ev, s={'med_hold_agreed': False}), today=TODAY)
            self.assertFalse(any('მოუხსნელადაც' in af for af in ev_ec['amber_flags']))

        # A16 + A17 + R3V-S3-01 + R3V-S3-03 + R3V-S3-04
        self.assertTrue(evaluate(_go('evla', sex='F', age=40, q={'med_aspirin': False, 'ven_hormones': True}), today=TODAY)['needs_med_hold'])
        self.assertTrue(evaluate(_go('phlebectomy', q={'med_aspirin': True, 'cad': False, 'neuro': False}), today=TODAY)['needs_med_hold'])
        self.assertIn('dos_ketones_ok', [c['key'] for c in evaluate(_go('open', q={'sglt2': False, 'other_meds': 'ჯარდიანსი 10მგ'}), today=TODAY)['dos_checks']])
        p_glp_ft = _go('evar', q={'glp1': False, 'other_meds': 'ოზემპიკი 1მგ'})
        ev_glp_ft = evaluate(p_glp_ft, today=TODAY)
        self.assertTrue(ev_glp_ft['has_glp1_any'])
        self.assertIn('GLP-1', generate_anamnesis(p_glp_ft, ev_glp_ft))
        ev_cea_lido = evaluate(_go('cea_symp', q={'allergy_chips': ['lidocaine']}, s={'anesthesia_plan': 'auto'}), today=TODAY)
        self.assertNotIn('რეგიონული ბლოკადა', ev_cea_lido['effective_anesth_label'])
        ev_ft_ich = evaluate(_go('cea_symp', q={'other_meds': 'ქსარელტო 20მგ'}, s={'brain_imaging': 'large_or_hemorrhage'}), today=TODAY)
        ft_row = next(r for r in ev_ft_ich['med_schedule'] if r.get('unstructured'))
        self.assertFalse(ft_row['hold_or_skip'])

    def test_r3v_s2_01_03_04_06_09_nurse_plan_stop_fasting_riva_ali_cautions(self):
        # R3V-S2-01 + R3V-S3-02: build_plain_georgian_missing_items includes blocking unknown_state_flags AND DoS STOP warning
        p_bhcg = _go('evar', sex='F', age=30, s={'dos_bhcg_negative': False})
        ev_bhcg = evaluate(p_bhcg, today=TODAY)
        miss_bhcg = nurse_plan.build_plain_georgian_missing_items(p_bhcg, ev_bhcg, TODAY)
        self.assertTrue(any('ორსულობის' in it['label'] for it in miss_bhcg))
        p_dos_stop = _go('open', s={'dos_bp_sys': 200, 'dos_bp_dia': 115})
        ev_dos_stop = evaluate(p_dos_stop, today=TODAY)
        miss_dos = nurse_plan.build_plain_georgian_missing_items(p_dos_stop, ev_dos_stop, TODAY)
        self.assertTrue(any('200' in it['label'] or 'ჰიპერტენზია' in it['label'] for it in miss_dos))

        # R3V-S2-03: STOP-lock suppresses active 6h/2h fasting for ALL patients AND postponement drug-hold advice
        self.assertIn('STOP', ev_dos_stop['fasting_instruction'])
        self.assertNotIn('6 საათით', ev_dos_stop['fasting_instruction'])
        p_post = _go('open', q={'med_warfarin': True}, s={'dos_bp_sys': 200, 'prev_planned_surgery_date': '2026-12-01', 'planned_surgery_date': '2026-12-05'})
        imp = nurse_plan.detect_postponement_impacts(p_post, evaluate(p_post, today=TODAY), TODAY)
        self.assertFalse(any('ახალი თარიღები' in it['text'] for it in (imp or {}).get('impacts', [])))

        # R3V-S2-04: Free-text therapeutic rivaroxaban 10/15/20 mg detected even alongside 2.5 mg
        for txt in ('ქსარელტო 2.5მგ და 15მგ', 'rivaroxaban 2.5 mg (also 20 mg)'):
            ft = _detect_freetext_high_risk_meds(txt, q={'med_chips': ['rivaroxaban_2_5']})
            self.assertTrue(ft['unstructured_antithrombotic'], f'Failed on {txt}')

        # R3V-S2-06: ALI (< 2 weeks) on *_claud escalates is_urgent_case, target_days, nurse queue, and PAD anamnesis
        p_ali = _go('bypass_claud', q={'abf_duration': '< 2 კვირა', 'abf_rest_pain': False, 'abf_ulcer': False})
        ev_ali = evaluate(p_ali, today=TODAY)
        self.assertTrue(ev_ali['is_urgent_case'])
        self.assertEqual(ev_ali['priority_tier'], 'urgent')
        self.assertLessEqual(ev_ali['target_days'], 5)
        self.assertIn('< 2 კვირა', generate_anamnesis(p_ali, ev_ali))
        p_ali.pk = 999
        queues = nurse_ui.build_nurse_handover_queues([p_ali], {999: ev_ali}, TODAY)
        self.assertTrue(any(it['p'].pk == 999 for it in queues['or_urgent']))

        # R3V-S2-09: passport_cautions keeps BOTH echo_valve_issue AND LVEF < 40%
        ev_both = evaluate(_go('open', s={'lvef': 25, 'echo_valve_issue': True}), today=TODAY)
        caut_txt = ' | '.join(ev_both['passport_cautions'])
        self.assertIn('სარქვლოვანი', caut_txt)
        self.assertIn('LVEF 25%', caut_txt)

    def test_a3_a4_a5_a11_a12_a13_a14_a18_registry_attribution_and_honesty(self):
        # A3: EVLA clopidogrel row & non-warfarin EVLA amber NOT hijacked by MED_WARFARIN_HOLD_DAYS
        ev_ev_clop = evaluate(_go('evla', q={'med_clopidogrel': True, 'med_warfarin': False}, s={'med_hold_agreed': False}), today=TODAY)
        clop_m = next(r for r in annotate_med_rows(ev_ev_clop['med_schedule']) if 'კლოპიდოგრელი' in r['drug'])
        self.assertEqual(clop_m['rule_meta']['id'], 'MED_P2Y12_HOLD_DAYS')
        ev_af = [af for af in ev_ev_clop['amber_flags'] if 'EVLA შესაძლებელია' in af][0]
        self.assertNotEqual(match_rule_for_text(ev_af).id, 'MED_WARFARIN_HOLD_DAYS')

        # A4: Female AAA < 50 -> AAA_FEMALE_MIN_MM; Male concomitant AAA >= 55 -> AAA_MALE_MIN_MM
        ev_f47 = evaluate(_go('open', sex='F', s={'aaa_mm': 47}), today=TODAY)
        rf_f47 = next(rf for rf in ev_f47['red_flags'] if '< 50 მმ' in rf)
        self.assertEqual(match_rule_for_text(rf_f47).id, 'AAA_FEMALE_MIN_MM')
        ev_m58 = evaluate(_go('cea_asymp', sex='M', s={'aaa_mm': 58}), today=TODAY)
        rf_m58 = next(rf for rf in ev_m58['red_flags'] if '55 მმ' in rf)
        self.assertEqual(match_rule_for_text(rf_m58).id, 'AAA_MALE_MIN_MM')

        # A5: Venous CEAP C6 & PTA lidocaine allergy NOT routed to VEN_LIDOCAINE_MAX_DOSE
        ev_c6 = evaluate(_go('evla', q={'ven_ceap': 'C6'}), today=TODAY)
        af_c6 = next(af for af in ev_c6['amber_flags'] if 'ვენური წყლული' in af)
        self.assertEqual(match_rule_for_text(af_c6).id, 'VEN_DUPLEX_ANATOMY_AND_CEAP')
        ev_pta_lido = evaluate(_go('pta_clti', q={'allergy_chips': ['lidocaine']}), today=TODAY)
        rf_pl = next(rf for rf in ev_pta_lido['red_flags'] if 'ლიდოკაინზე' in rf)
        self.assertEqual(match_rule_for_text(rf_pl).id, 'ALLERGY_HIT_ABX_PROPHYLAXIS')

        # A11: DOS_WHO_SIGNIN_THRESHOLDS & CARDIO_CARP_AUDIT_GATE honestly marked STATUS_LOCAL
        self.assertEqual(RULES_BY_ID['DOS_WHO_SIGNIN_THRESHOLDS'].status, STATUS_LOCAL)
        self.assertEqual(RULES_BY_ID['CARDIO_CARP_AUDIT_GATE'].status, STATUS_LOCAL)

        # A12 + A13 + A14 + A18
        ev_asra_af = evaluate(_go('bypass_clti', q={'med_warfarin': True}, s={'anesthesia_plan': 'neuraxial', 'med_hold_agreed': False}), today=TODAY)
        asra_af = next(af for af in ev_asra_af['amber_flags'] if 'ASRA მედიკამენტების ცხრილი' in af)
        self.assertEqual(match_rule_for_text(asra_af).id, 'ASRA_NEURAXIAL_COAG_GATE')
        ev_cea_ns = evaluate(_go('cea_asymp', side=''), today=TODAY)
        ns_af = next(af for af in ev_cea_ns['amber_flags'] if 'მხარე' in af)
        self.assertEqual(match_rule_for_text(ns_af).id, 'WF_UNKNOWN_STATE_FAILSAFE')
        self.assertIn('HIT', RULES_BY_ID['SCORE_CAPRINI_VTE'].description_ka)
        self.assertNotIn('asymp_grey', RULES_BY_ID['CEA_STENOSIS_THRESHOLDS'].value)


class PreopStressFixesRound4HTTPTests(TestCase):
    """Live Django HTTP, workflow invalidation, and template rendering tests for Round-4 fixes."""

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.doc = User.objects.create_user(
            username='r4_doc', password='R4Password!1', first_name='ნინო', last_name='ექიმი', is_staff=True,
        )

    def setUp(self):
        self.client.force_login(self.doc)

    def test_f5_unicode_superscript_zero_500_across_endpoints(self):
        p = _go('open')
        p.save()
        for m, url, data in (
            ('GET', reverse('preop-new'), {'case': '²'}),
            ('POST', reverse('preop-new'), {'patient_name': 'ტესტ', 'surgeon': '²', 'slot': '2026-11-26'}),
            ('POST', reverse('preop-nurse-register'), {'patient_name': 'ტესტ', 'surgeon': '²', 'op_subtype': 'cea_symp', 'side': 'R'}),
            ('POST', reverse('preop-patient', args=[p.pk]), {'section': 'header', 'patient_name': 'ტესტ', 'surgeon': '²'}),
        ):
            r = self.client.get(url, data) if m == 'GET' else self.client.post(url, data)
            self.assertLess(r.status_code, 500, f'F5 HTTP 500 on {m} {url}')

    def test_r3v_s1_05_and_s3_05_med_hold_and_approval_invalidation(self):
        # 1. Stage 2 anesthesia shift (general -> neuraxial) invalidates med_hold_agreed
        p_s2 = _go('bypass_clti', q={'med_doac': True}, s={'anesthesia_plan': 'general', 'med_hold_agreed': True})
        p_s2.save()
        self.client.post(reverse('preop-patient', args=[p_s2.pk]), {
            'section': 'stage2', 'anesthesia_plan': 'neuraxial', 'med_hold_agreed': '1',
        })
        p_s2.refresh_from_db()
        self.assertFalse(p_s2.studies.get('med_hold_agreed'))

        # 2. Header age/sex/subtype shift altering DOAC hold invalidates med_hold_agreed
        p_hdr = _go('evla', age=55, sex='M', q={'med_doac': True}, s={'creat': 140, 'creat_unit': 'umol', 'weight_kg': 60, 'med_hold_agreed': True})
        p_hdr.save()
        self.client.post(reverse('preop-patient', args=[p_hdr.pk]), {
            'section': 'header', 'patient_name': p_hdr.patient_name, 'age': '85', 'sex': 'F',
            'side': 'R', 'op_subtype': 'phlebectomy', 'visit_date': '2026-11-25',
        })
        p_hdr.refresh_from_db()
        self.assertFalse(p_hdr.studies.get('med_hold_agreed'))

        # 3. Questionnaire clinical shift (CEA rt-PA) invalidates med_hold_agreed
        p_q = _go('cea_asymp', q={'med_clopidogrel': True, 'med_aspirin': True}, s={'med_hold_agreed': True})
        p_q.save()
        self.client.post(reverse('preop-patient', args=[p_q.pk]), {
            'section': 'questionnaire', 'med_clopidogrel': '1', 'med_aspirin': '1',
            'cea_symp_event': '1', 'cea_days_since_event': '2', 'cea_thrombolysis': '1',
        })
        p_q.refresh_from_db()
        self.assertFalse(p_q.studies.get('med_hold_agreed'))

        # 4. DoS subtherapeutic mechanical-valve INR invalidates approved_at; DoS STOP clears med_hold_agreed
        p_mv = _go('evla', q={'cad_chips': ['mech_valve'], 'med_warfarin': True}, s={'inr': 2.5, 'med_hold_agreed': True})
        p_mv.approved_at = timezone.now()
        p_mv.save()
        self.client.post(reverse('preop-patient', args=[p_mv.pk]), {
            'section': 'dos_vqi', 'dos_partial': '1', 'action': 'save_dos', 'dos_inr': '1.4',
        })
        p_mv.refresh_from_db()
        self.assertIsNone(p_mv.approved_at)

        p_ds = _go('open', q={'med_warfarin': True}, s={'med_hold_agreed': True})
        p_ds.save()
        self.client.post(reverse('preop-patient', args=[p_ds.pk]), {
            'section': 'dos_vqi', 'dos_partial': '1', 'action': 'save_dos', 'dos_bp_sys': '200',
        })
        p_ds.refresh_from_db()
        self.assertFalse(p_ds.studies.get('med_hold_agreed'))

        # 5. R3V-S3-05: Self-fill adding warfarin invalidates pre-existing med_hold_agreed
        p_sf = _go('open', q={'filled': False, 'nurse_verified': False, 'self_fill_submitted': False}, s={'med_hold_agreed': True})
        p_sf.save()
        tok = make_self_fill_token(p_sf)
        self.client.post(reverse('preop-self-fill', args=[tok]), {'med_warfarin': '1', 'mets_ok': '1'})
        p_sf.refresh_from_db()
        self.assertFalse(p_sf.studies.get('med_hold_agreed'))

    def test_r3v_s2_02_07_08_and_f9_templates_and_dos_icu_uncheck(self):
        # R3V-S2-02: dos_partial=1 can uncheck icu_bed_booked while preserving unrendered VQI booleans
        p_icu = _go('open', s={'icu_bed_booked': True, 'vqi_discharge_antiplatelet': True})
        p_icu.save()
        self.client.post(reverse('preop-patient', args=[p_icu.pk]), {
            'section': 'dos_vqi', 'dos_partial': '1', 'action': 'save_dos', 'dos_identity_consent': '1',
        })
        p_icu.refresh_from_db()
        self.assertFalse(p_icu.studies.get('icu_bed_booked'))
        self.assertTrue(p_icu.studies.get('vqi_discharge_antiplatelet'))

        # R3V-S2-07 + F9: passport.html highlights L-arm critical BP, inverted BP, SI>=1.0, and unverified self-fill
        p_pp = _go('open', q={'self_filled': True, 'self_fill_submitted': True, 'filled_by': '', 'nurse_verified': False}, s={'bp_sys': 120, 'bp_dia': 80, 'bp_sys_l': 195, 'bp_dia_l': 115})
        p_pp.save()
        html_pp = self.client.get(reverse('preop-passport', args=[p_pp.pk])).content.decode('utf-8')
        self.assertIn('class="bad">120/80 (L 195/115)', html_pp)
        self.assertIn('გადასამოწმებელია', html_pp)
        p_pp_inv = _go('open', s={'bp_sys': 95, 'bp_dia': 105, 'hr': 100})
        p_pp_inv.save()
        html_inv = self.client.get(reverse('preop-passport', args=[p_pp_inv.pk])).content.decode('utf-8')
        self.assertIn('class="bad">95/105', html_inv)

        # R3V-S2-08: Classic patient.html .po-next-bar never shows green ready/approved during DoS STOP or workup_ready=False
        p_st = _go('open', s={'dos_bp_sys': 200, 'dos_bp_dia': 115})
        p_st.save()
        html_st = self.client.get(reverse('preop-patient', args=[p_st.pk])).content.decode('utf-8')
        self.assertNotIn('ყველა კვლევა და რეფლექს-ორდერი შესრულებულია', html_st)
        p_ua = _go('evar', sex='F', age=30, s={'dos_bhcg_negative': False})
        p_ua.approved_at = timezone.now()
        p_ua.save()
        html_ua = self.client.get(reverse('preop-patient', args=[p_ua.pk])).content.decode('utf-8')
        self.assertNotIn('✓ პაციენტი დადასტურებულია ოპერაციისთვის', html_ua)
