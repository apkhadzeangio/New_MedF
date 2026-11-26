"""Test-first regression suite for Round-5 independent audit findings (Fuzz, Combos, Clinical & Registry v1.2.5)."""
import datetime
import json

from django.contrib.auth import get_user_model
from django.http import QueryDict
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from ..models import BoardEntry, PreopPatient, StaffProfile
from ..preop import (
    _clean_person_id,
    _num,
    _parse_scr_mgdl,
    admit_preop_to_board,
    build_med_schedule,
    build_self_fill_highlights,
    calc_ariscat_pulm,
    calc_auto_asa,
    calc_caprini,
    calc_max_contrast_ml,
    evaluate,
    generate_anamnesis,
    make_self_fill_token,
    nurse_plan,
)
from ..preop.meds import _detect_freetext_high_risk_meds
from ..preop.registry import RULES_BY_ID, STATUS_LOCAL, match_rule_for_text
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
        patient_name='სატესტო რ6', age=65, sex='M', side='' if pw in ('aaa_infra', 'abf') else 'R',
        pathway=pw, op_subtype=sub, visit_date=datetime.date(2026, 11, 25),
        started_on=datetime.date(2026, 11, 25), cardio_decision='cleared', questionnaire=q, studies=s,
    )
    defaults.update(kw)
    return PreopPatient(**defaults)


class PreopStressFixesRound6UnitTests(SimpleTestCase):
    """Unit regressions for Round-5 Fuzzer, Combos, Clinical & Registry findings."""

    def test_fuzz_s1_hydration_evla_inr_and_rupture_hr_saturation(self):
        # F-S1-02: Non-contrast CKD3 + Na >= 150 / >190 never recommends isotonic hydration
        for sub, na_v in (('open', 195), ('cea_asymp', 155), ('abf_clti', 155)):
            ev = evaluate(_go(sub, s={'creat': 160, 'creat_unit': 'umol', 'na': na_v}), today=TODAY)
            self.assertFalse(any('იზოტონური ჰიდრატაცია' in a for a in ev['amber_flags']), f'{sub} Na={na_v}')
        # HFrEF on non-contrast CKD3 also avoids blind full isotonic hydration
        ev_hf = evaluate(_go('open', q={'cad_chips': ['hf']}, s={'creat': 160, 'creat_unit': 'umol', 'lvef': 30}), today=TODAY)
        self.assertFalse(any('იზოტონური ჰიდრატაცია.' in a for a in ev_hf['amber_flags']))

        # F-S1-03: EVLA + Warfarin INR=25 stays red/contraindicated and in stop_rows (never keep_rows)
        p25 = _go('evla', q={'med_warfarin': True}, s={'inr': 25})
        ev25 = evaluate(p25, today=TODAY)
        w25 = next(r for r in ev25['med_schedule'] if 'ვარფარინ' in r['drug'])
        self.assertEqual(w25['tone'], 'red')
        self.assertTrue(w25.get('contraindicated'))
        sc25 = nurse_plan.build_phone_med_script(p25, ev25, TODAY)
        self.assertTrue(any('ვარფარინ' in r['raw_drug'] for r in sc25['stop_rows']))
        self.assertFalse(any('ვარფარინ' in r['raw_drug'] for r in sc25['keep_rows']))

        # F-S1-05: Symptomatic AAA HR=270 stays rupture suspect with MTP and permissive hypotension
        p_r = _go('open', q={'aaa_pain': True}, s={'bp_sys': 110, 'bp_dia': 70, 'hr': 270})
        ev_r = evaluate(p_r, today=TODAY)
        self.assertTrue(ev_r['is_rupture_suspect'])
        self.assertIn('MTP', ev_r['anesthetic_brief']['transfusion_pbm'])
        self.assertNotIn('სტაბილური', ev_r['anesthetic_brief']['bp_corridor'])

    def test_fuzz_s2_inverted_bp_direct_helpers_and_extreme_saturation(self):
        # F-S2-02: Inverted BP monotonicity across ASA, needs_cardio, STOP-Bang, ICU & neuraxial
        e120 = evaluate(_go('evla', s={'bp_sys': 120, 'bp_dia': 115}), today=TODAY)
        e115 = evaluate(_go('evla', s={'bp_sys': 115, 'bp_dia': 115}), today=TODAY)
        e105 = evaluate(_go('evla', s={'bp_sys': 105, 'bp_dia': 115}), today=TODAY)
        self.assertEqual(e120['auto_asa'], 'III')
        self.assertEqual(e115['auto_asa'], 'III')
        self.assertEqual(e105['auto_asa'], 'III')
        self.assertTrue(e115['needs_cardio'] and e105['needs_cardio'])
        self.assertEqual(e115['effective_stop_bang'], e120['effective_stop_bang'])
        e85_95 = evaluate(_go('evla', s={'bp_sys': 85, 'bp_dia': 95}), today=TODAY)
        e85_105 = evaluate(_go('evla', s={'bp_sys': 85, 'bp_dia': 105}), today=TODAY)
        self.assertEqual(e85_95['auto_asa'], 'IV')
        self.assertEqual(e85_105['auto_asa'], 'IV')
        self.assertTrue(e85_105['needs_icu_bed'])
        for sb, db in ((120, 230), (200, 230), (350, 360)):
            ev_nx = evaluate(_go('phlebectomy', s={'anesthesia_plan': 'neuraxial', 'bp_sys': sb, 'bp_dia': db}), today=TODAY)
            self.assertTrue(ev_nx['neuraxial_hard_blocked'], f'bp {sb}/{db}')

        # F-S2-04: calc_ariscat_pulm guards non-numeric & bool hb_gl
        p_ari = _go('open')
        base_ari = calc_ariscat_pulm(p_ari, p_ari.questionnaire, p_ari.studies, hb_gl=None)
        self.assertEqual(calc_ariscat_pulm(p_ari, p_ari.questionnaire, p_ari.studies, hb_gl=True)['score'], base_ari['score'])
        self.assertIsInstance(calc_ariscat_pulm(p_ari, p_ari.questionnaire, p_ari.studies, hb_gl='90')['score'], int)
        self.assertIsInstance(calc_ariscat_pulm(p_ari, p_ari.questionnaire, p_ari.studies, hb_gl=[])['score'], int)

        # F-S2-05: Direct renal helpers negative clamp & high CrCl support
        self.assertEqual(calc_auto_asa(_go('evla'), {}, {}, egfr=-5), 'IV')
        cap_neg = calc_caprini(_go('evla'), {}, {}, eff_renal=-5)
        self.assertIn('აკრძალულია', cap_neg[2])
        self.assertEqual(calc_max_contrast_ml(-5), 15)
        self.assertEqual(calc_max_contrast_ml(600), 250)
        p_hi = _go('open', q={'med_doac': True})
        ms_hi = build_med_schedule(p_hi, p_hi.questionnaire, p_hi.studies, egfr=120, crcl=700, today=TODAY)
        d_row = next(r for r in ms_hi if 'DOAC' in r['drug'])
        self.assertNotIn('შეიყვანეთ კრეატინინი', d_row['date_plan'])

    def test_fuzz_s2_extreme_bmi_aaa_ica_abi_and_cardiopulmonary_saturation(self):
        # F-S2-06: weight > 260 kg preserves BMI > 50 DOAC alert & STOP-Bang
        ev_w300 = evaluate(_go('open', q={'med_doac': True}, s={'weight_kg': 300, 'height_cm': 170}), today=TODAY)
        self.assertTrue(any('ექსტრემალური სიმსუქნე' in a and '> 50' in a for a in ev_w300['amber_flags']))
        self.assertEqual(ev_w300['effective_stop_bang'], 'intermediate')

        # F-S2-07: AAA > 200mm stays urgent; ICA 105% stays STOP+med lock; ABI -0.1 stays STOP+compression_contra
        ev_aaa = evaluate(_go('open', s={'aaa_mm': 210}), today=TODAY)
        self.assertTrue(ev_aaa['is_urgent_case'])
        self.assertEqual(ev_aaa['priority_tier'], 'urgent')
        ev_ica = evaluate(_go('cea_asymp', q={'med_warfarin': True}, s={'carotid_r': 105}), today=TODAY)
        self.assertTrue(ev_ica['has_hard_contraindication'])
        self.assertEqual(ev_ica['clearance_seal']['code'], 'stop')
        self.assertIn('STOP', next(r for r in ev_ica['med_schedule'] if 'ვარფარინ' in r['drug'])['date_plan'])
        ev_abi = evaluate(_go('evla', q={'med_doac': True}, s={'abi_r': -0.1}), today=TODAY)
        self.assertTrue(ev_abi['has_hard_contraindication'])
        self.assertTrue(ev_abi['compression_contra'])
        self.assertEqual(ev_abi['clearance_seal']['code'], 'stop')

        # F-S2-08: Glucose >1000, LVEF <5, BNP >100k, cTn >50k, SpO2 <30, Hb <1, Age >120, SBP >320
        self.assertTrue(evaluate(_go('evla', s={'glucose': 1200}), today=TODAY)['needs_ecg_venous'])
        self.assertTrue(evaluate(_go('open', s={'lvef': 4, 'asa_class': 'III'}), today=TODAY)['needs_icu_bed'])
        ev_bnp = evaluate(_go('open', s={'nt_probnp': 120000}), today=TODAY)
        self.assertIn('hs-cTn', ev_bnp['anesthetic_brief']['disposition'])
        ev_spo = evaluate(_go('open', s={'spo2': 29}), today=TODAY)
        self.assertTrue(ev_spo['needs_cxr'] and ev_spo['needs_spirometry'])
        ev_hb = evaluate(_go('open', s={'hb': 0.5}), today=TODAY)
        self.assertTrue(ev_hb['needs_anemia_panel'] and ev_hb['needs_gastro'])
        self.assertTrue(evaluate(_go('evla', age=125), today=TODAY)['needs_ecg_venous'])
        self.assertEqual(evaluate(_go('evla', s={'bp_sys': 350, 'bp_dia': 100}), today=TODAY)['effective_stop_bang'], 'intermediate')

    def test_fuzz_s3_nan_ascii_digits_creat_ambig_and_anamnesis_sanitization(self):
        # F-S3-01: Nested NaN/Inf in lists & depth>=2 dicts stripped
        p_nan = _go('open', s={'extra_list': [1, float('nan'), {'z': float('inf')}], '_invalid_inputs': {'nested': {'b': float('inf')}}})
        ev_nan = evaluate(p_nan, today=TODAY)
        json.dumps(ev_nan['s'], allow_nan=False)

        # F-S3-03: Anamnesis prints normalized PLT & sanitized BP/LVEF/FEV1, and prints PLT/INR on arterial pathways (Clin S3-4)
        p_an = _go('open', s={'plt': 200000, 'inr': 1.1, 'bp_sys': -9999, 'bp_dia': 1e10, 'lvef': -50, 'spiro_fev1': 999})
        ev_an = evaluate(p_an, today=TODAY)
        txt = generate_anamnesis(p_an, ev_an)
        self.assertNotIn('200000', txt)
        self.assertIn('PLT 200', txt)
        self.assertIn('INR 1.1', txt)
        self.assertNotIn('-9999', txt)
        self.assertNotIn('-50%', txt)
        self.assertNotIn('999%', txt)

        # F-S3-04: _parse_scr_mgdl(3500, 'mgdl') returns ambig=True & score_completeness parity
        self.assertEqual(_parse_scr_mgdl(3500, 'mgdl'), (None, True))
        p_d = _go('open', s={'creat': 3500, 'creat_unit': 'umol'})
        p_p = _go('open')
        sm = dict(p_p.studies)
        _parse_s1_numbers_and_dates(QueryDict('creat=3500&creat_unit=umol'), sm, TODAY)
        p_p.studies = sm
        self.assertEqual(
            evaluate(p_d, today=TODAY)['score_completeness']['rcri']['incomplete'],
            evaluate(p_p, today=TODAY)['score_completeness']['rcri']['incomplete'],
        )

        # F-S3-05: Reject non-ASCII Arabic-Indic digits & underscores in _num and _clean_person_id
        self.assertIsNone(_num('١٢٠'))
        self.assertIsNone(_num('1_000'))
        self.assertEqual(_clean_person_id('٠١٢٣٤٥٦٧٨٩٠'), '')
        self.assertEqual(_clean_person_id('01024012345'), '01024012345')

    def test_combos_s1_stop_lock_freetext_ali_unfilled_stent_and_cea_vte(self):
        # Combos S1-1: EVLA + Warfarin without surgery date during STOP never leaks into keep_rows
        p_ev = _go('evla', q={'med_warfarin': True}, s={'planned_surgery_date': '', 'ven_duplex_status': 'acute_dvt'})
        ev_ev = evaluate(p_ev, today=TODAY)
        w_ev = next(r for r in ev_ev['med_schedule'] if 'ვარფარინ' in r['drug'])
        self.assertIn('STOP', w_ev['date_plan'])
        self.assertEqual(w_ev['tone'], 'red')
        sc_ev = nurse_plan.build_phone_med_script(p_ev, ev_ev, TODAY)
        self.assertFalse(any('ვარფარინ' in r['raw_drug'] for r in sc_ev['keep_rows']))

        # Combos S1-4 & S2-7: Unicode ×2 / 2× Xarelto, space-separated aspirin, no false positive across co-meds
        q_v = {'med_chips': ['rivaroxaban_2_5']}
        q_ns = {'allergy_chips': ['nsaid']}
        self.assertIn('doac', _detect_freetext_high_risk_meds('ქსარელტო 2.5 მგ ×2, 20 მგ', q=q_v)['matched_keys'])
        self.assertTrue(any('DOAC' in lbl for lbl in _detect_freetext_high_risk_meds('ქსარელტო 2.5 მგ ×2, 20 მგ', q=q_v)['unstructured_labels']))
        self.assertTrue(any('DOAC' in lbl for lbl in _detect_freetext_high_risk_meds('ქსარელტო 2.5 მგ 2×, 15 მგ', q=q_v)['unstructured_labels']))
        self.assertEqual(_detect_freetext_high_risk_meds('ქსარელტო 2.5 მგ და ამლოდიპინი 10 მგ', q=q_v)['unstructured_labels'], [])
        self.assertEqual(_detect_freetext_high_risk_meds('ქსარელტო 2.5 მგ + ბისოპროლოლი 10 მგ', q=q_v)['unstructured_labels'], [])
        self.assertIn('aspirin', _detect_freetext_high_risk_meds('თრომბო ასი 100 მგ', q=q_ns)['matched_keys'])
        self.assertIn('aspirin', _detect_freetext_high_risk_meds('ASA 100 mg', q=q_ns)['matched_keys'])
        self.assertIn('aspirin', _detect_freetext_high_risk_meds('trombo ass 100mg', q=q_ns)['matched_keys'])

        # Combos S1-5: ALI on claudication subtypes exempt from elective-claudication pregnancy STOP & stent deferral
        for sub in ('abf_claud', 'bypass_claud', 'pta_claud'):
            ev_ap = evaluate(_go(sub, sex='F', age=35, q={'abf_duration': '< 2 კვირა', 'pregnant_possible': True}), today=TODAY)
            self.assertFalse(ev_ap['has_hard_contraindication'], sub)
            ev_as = evaluate(_go(sub, q={'abf_duration': '< 2 კვირა', 'recent_stent': True, 'med_clopidogrel': True}), today=TODAY)
            self.assertFalse(any('წმინდა გეგმური ჩარევა' in rf for rf in ev_as['red_flags']), sub)

        # Combos S1-10 & Clin S1-7: Explicit sapt/voyager & PTA dapt task with fresh stent + NSAID allergy
        for plan in ('sapt', 'voyager'):
            ev_sa = evaluate(_go(
                'pta_clti',
                q={'recent_stent': True, 'allergy_chips': ['nsaid'], 'med_clopidogrel': True, 'med_aspirin': False},
                s={'post_antithrombotic_plan': plan},
            ), today=TODAY)
            self.assertIn('ალერგი', ev_sa['post_antithrombotic_label'])
            dapt_t = next(t for t in ev_sa['tasks'] if t['key'] == 'dapt')
            self.assertIn('ალერგი', dapt_t['label'])

        # Combos S1-11: CEA ICH or active rt-PA <=6d never recommends pharmacologic LMWH/DOAC in Caprini
        ev_ich = evaluate(_go('cea_symp', s={'brain_imaging': 'large_or_hemorrhage'}), today=TODAY)
        self.assertNotIn('LMWH / DOAC', ev_ich['caprini_advice'])
        ev_rt = evaluate(_go('cea_symp', q={'cea_thrombolysis': True, 'cea_days_since_event': 2}, s={'brain_imaging': 'normal_small'}), today=TODAY)
        self.assertNotIn('LMWH / DOAC', ev_rt['caprini_advice'])

    def test_combos_s1_9_unfilled_s2_highlights_amber_keep_and_rtpa_p2y12(self):
        # Combos S1-9: Unfilled critical alerts, med_schedule, CEA symp parity & wet infection
        ev_st = evaluate(_go('open', q={'filled': False, 'cad_chips': ['stent_6_12m', 'mi']}), today=TODAY)
        self.assertTrue(any('სტენტ' in rf for rf in ev_st['red_flags']))
        for vf in ('ven_acute_svt', 'ven_skin_infection', 'ven_bleeding'):
            ev_v = evaluate(_go('evla', q={'filled': False, vf: True}), today=TODAY)
            self.assertTrue(len(ev_v['red_flags']) > 0, vf)
        ev_cs = evaluate(_go('cea_asymp', q={'filled': False, 'cea_symp_event': True, 'cea_days_since_event': 5}), today=TODAY)
        self.assertTrue(ev_cs['is_symp_cea'])
        ev_bb = evaluate(_go('open', q={'filled': False, 'med_aspirin': False, 'med_chips': ['beta_blocker']}, s={'bp_sys': 85, 'bp_dia': 55}), today=TODAY)
        self.assertTrue(len(ev_bb['med_schedule']) > 0)
        ev_wi = evaluate(_go('abf_clti', q={'filled': False, 'abf_wet_infection': True}, s={'planned_surgery_date': '2026-12-06'}), today=TODAY)
        self.assertIn('დრენირება', ev_wi['surgical_brief']['tactical_note'])

        # Combos S2-3: All 6 self-fill highlights surfaced
        for k, v in (
            ('ven_skin_infection', True), ('cea_hoarseness', True), ('cea_prior_neck', True),
            ('pta_cant_lie_flat', True), ('ven_cancer_immob', True), ('med_chips', ['amiodarone']),
        ):
            self.assertTrue(len(build_self_fill_highlights({'filled': True, k: v})) > 0, f'missing highlight for {k}')

        # Combos S2-6: Amber caution meds (statin+liver_3x, EVLA warfarin) stay out of green keep_rows
        p_st = _go('open', q={'med_chips': ['statin']}, s={'liver_status': '3x'})
        sc_st = nurse_plan.build_phone_med_script(p_st, evaluate(p_st, today=TODAY), TODAY)
        self.assertFalse(any('სტატინ' in r['raw_drug'] for r in sc_st['keep_rows']))

        # Combos S2-9: CEA rt-PA days 1-6 on P2Y12 monotherapy (without aspirin) sets needs_cea_antiplatelet=True
        ev_p2 = evaluate(_go(
            'cea_symp',
            q={'cea_thrombolysis': True, 'cea_days_since_event': 2, 'med_aspirin': False, 'med_clopidogrel': True},
            s={'brain_imaging': 'normal_small'},
        ), today=TODAY)
        self.assertTrue(ev_p2['needs_cea_antiplatelet'])

        # Combos S3-1: Triple-therapy clopidogrel on CEA/PTA includes triple-AT warning in rule
        ev_tr = evaluate(_go('pta_clti', q={'med_warfarin': True, 'med_aspirin': True, 'med_clopidogrel': True}), today=TODAY)
        cl_r = next(r for r in ev_tr['med_schedule'] if 'კლოპიდოგრელ' in r['drug'])
        self.assertIn('≥3', cl_r['rule'])

    def test_clin_s1_pharmacology_and_pathway_safety(self):
        # Clin S1-1: PTA duplicate P2Y12 (clopidogrel + prasugrel) hard-blocks neuraxial
        ev_dp = evaluate(_go(
            'pta_clti', q={'med_clopidogrel': True, 'med_prasugrel': True}, s={'anesthesia_plan': 'neuraxial'},
        ), today=TODAY)
        self.assertTrue(ev_dp['neuraxial_hard_blocked'])
        self.assertNotIn('დაცულია', ev_dp['anesthetic_brief']['neuraxial_gate'])

        # Clin S1-2: Prasugrel Boxed Warning fires on CEA >180d stroke/TIA and brain_imaging=large_or_hemorrhage
        ev_pr1 = evaluate(_go('cea_asymp', q={'cea_symp_event': True, 'cea_days_since_event': 200, 'med_prasugrel': True}), today=TODAY)
        self.assertTrue(any('პრასუგრელ' in rf for rf in ev_pr1['red_flags']))
        self.assertTrue(next(r for r in ev_pr1['med_schedule'] if 'პრასუგრელ' in r['drug']).get('contraindicated'))
        ev_pr2 = evaluate(_go('cea_asymp', q={'med_prasugrel': True}, s={'brain_imaging': 'large_or_hemorrhage'}), today=TODAY)
        self.assertTrue(any('პრასუგრელ' in rf for rf in ev_pr2['red_flags']))

        # Clin S1-3: Cilostazol blocked on gastro_result='acute_ulcer' and ACS stent 6-12m
        for kw in ({'s': {'gastro_result': 'acute_ulcer'}}, {'q': {'cad_chips': ['stent_6_12m', 'mi']}}):
            q = {'med_chips': ['statin', 'cilostazol'], **kw.get('q', {})}
            ev_c = evaluate(_go('bypass_claud', q=q, s=kw.get('s', {})), today=TODAY)
            self.assertTrue(any('ცილოსტაზოლ' in rf for rf in ev_c['red_flags']), str(kw))
            self.assertTrue(next(r for r in ev_c['med_schedule'] if 'ცილოსტაზოლ' in r['drug']).get('contraindicated'))

        # Clin S1-4: Dialysis + PTA hostile CFA tactical_note never recommends radial/brachial
        ev_dh = evaluate(_go('pta_clti', q={'on_dialysis': True}, s={'pta_access_site': 'cfa_calcified_scar'}), today=TODAY)
        self.assertIn('აკრძალულია', ev_dh['surgical_brief']['tactical_note'])

        # Clin S1-6: Phlebectomy + CABG (cad_chips) + NSAID allergy does NOT blindly wash out clopidogrel
        ev_cb = evaluate(_go(
            'phlebectomy',
            q={'med_aspirin': False, 'med_clopidogrel': True, 'cad_chips': ['cabg'], 'allergy_chips': ['nsaid']},
            s={'anesthesia_plan': 'neuraxial'},
        ), today=TODAY)
        self.assertTrue(ev_cb['neuraxial_hard_blocked'])
        self.assertTrue(any('კლოპიდოგრელ' in rf or 'P2Y12' in rf for rf in ev_cb['red_flags']))

    def test_clin_s2_s3_and_registry_v1_2_5(self):
        # Clin S2-5: vein_strip in vasc_chips marks GSV inadequate in surgical_brief & anamnesis
        p_vs = _go('bypass_clti', q={'vasc_chips': ['vein_strip']}, s={'pad_gsv_status': 'adequate'})
        ev_vs = evaluate(p_vs, today=TODAY)
        self.assertIn('უვარგისი', ev_vs['surgical_brief']['tactical_note'])
        self.assertIn('უვარგისი', generate_anamnesis(p_vs, ev_vs))

        # Clin S2-6: ALI (< 2 weeks) gets emergency ASA-E and bypasses elective reflexes
        ev_ali = evaluate(_go('bypass_clti', q={'abf_duration': '< 2 კვირა'}, s={'hb': 95}), today=TODAY)
        self.assertTrue(ev_ali['is_acute_emergency'])
        self.assertTrue(ev_ali['effective_asa'].endswith('-E'))

        # Clin S2-7: Anamnesis CEA rt-PA >6d does NOT claim "ბოლო 6 დღეში!"
        p_r10 = _go('cea_symp', q={'cea_thrombolysis': True, 'cea_days_since_event': 10})
        self.assertNotIn('ბოლო 6 დღეში', generate_anamnesis(p_r10, evaluate(p_r10, today=TODAY)))

        # Clin S2-8: PTA tasks['dapt'] reflects explicit post_antithrombotic_plan='sapt'
        ev_sp = evaluate(_go('pta_clti', s={'post_antithrombotic_plan': 'sapt'}), today=TODAY)
        self.assertIn('მონოთერაპია', next(t for t in ev_sp['tasks'] if t['key'] == 'dapt')['label'])

        # Clin S2-9: EVLA + C6 includes dos_abx_ready
        ev_c6 = evaluate(_go('evla', q={'ven_ceap': 'C6'}), today=TODAY)
        self.assertTrue(any(c['key'] == 'dos_abx_ready' for c in ev_c6['dos_checks']))

        # Clin S2-10: EVLA + Metformin CKD 3b emits <=1000 mg/day dose cap
        ev_m = evaluate(_go('evla', q={'metformin': True}, s={'creat': 160, 'creat_unit': 'umol'}), today=TODAY)
        m_row = next(r for r in ev_m['med_schedule'] if 'მეტფორმინ' in r['drug'])
        self.assertIn('1000', m_row['rule'])
        self.assertTrue(any('1000' in a for a in ev_m['amber_flags']))

        # Clin S2-11: Phlebectomy + lidocaine allergy auto plan does not offer spinal
        ev_la = evaluate(_go('phlebectomy', q={'allergy_chips': ['lidocaine']}), today=TODAY)
        self.assertTrue(ev_la['effective_anesth_label'].startswith('ზოგადი'))

        # Clin S2-12: Amiodarone + DOAC P-gp amber fires even when q.filled=False
        ev_am = evaluate(_go('bypass_clti', q={'filled': False, 'med_doac': True, 'med_chips': ['amiodarone']}, s={'creat': 130, 'creat_unit': 'umol'}), today=TODAY)
        self.assertTrue(any('ამიოდარონ' in a for a in ev_am['amber_flags']))

        # Clin S3-1: No Georgian genitive typo '!)ს' in decol reflex order
        ev_dc = evaluate(_go('open', q={'allergy_chips': ['latex_chlorhex']}), today=TODAY)
        dc = next(o for o in ev_dc['reflex_orders'] if o['key'] == 'decol')
        self.assertNotIn('!)ს', dc['action'])

        # Clin S2-1..S2-4: Registry v1.2.5 integrity, status honesty & semantic attribution
        self.assertIsInstance(RULES_BY_ID['MED_ASPIRIN_CONTINUE'].value, dict)
        for rid in ('MED_WARFARIN_HOLD_DAYS', 'GLY_GLUCOSE_HBA1C', 'CARDIO_BIOMARKERS_ECHO', 'CEA_SYMP_WINDOW_DAYS', 'PULM_CXR_SPIROMETRY'):
            self.assertEqual(RULES_BY_ID[rid].status, STATUS_LOCAL, rid)
        self.assertEqual(match_rule_for_text('არტერიული წნევის არასწორი ჩანაწერი (SBP ≤ DBP)', 'red').id, 'WF_UNKNOWN_STATE_FAILSAFE')
        self.assertEqual(match_rule_for_text('კრეატინინის ერთეული საეჭვოა (25)', 'red').id, 'SCORE_RENAL_CKDEPI_CG')
        self.assertNotEqual(match_rule_for_text('დადებითი ვირუსული მარკერები (B/C/HIV)', 'amber').id, 'LAB_NUTRITION_ALBUMIN_LIVER')
        self.assertNotEqual(match_rule_for_text('აქტიური ინფექცია (სიცხე / რესპირატორული ინფექცია)', 'red').id, 'INF_UTI_VS_ASB')


class PreopStressFixesRound6HTTPTests(TestCase):
    """HTTP workflow, Board handoff & rendered HTML regressions for Round-5 findings."""

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser('r6_doc', 'r6@t.local', 'pw')
        StaffProfile.objects.get_or_create(user=self.user)
        self.client.force_login(self.user)

    def _create(self, sub='open', **kw):
        p = _go(sub, **kw)
        p.save()
        return p

    def test_board_handoff_hypernatremia_hfref_hit_liver_and_cea_ich(self):
        # F-S1-01: Board handoff Na+ > 190 via S1 POST & HFrEF avoids blind isotonic 0.9% NaCl
        p_na = self._create('evar', s={'creat': 160, 'creat_unit': 'umol'})
        self.client.post(reverse('preop-patient', args=[p_na.pk]), {'section': 'stage1', 's1_partial': '1', 'na': '195'})
        p_na.refresh_from_db()
        bp_na, _, _ = admit_preop_to_board(p_na, self.user)
        t_na = [b.text for b in BoardEntry.objects.filter(patient=bp_na, kind=BoardEntry.TASK) if 'ნეფროპროტექცია' in b.text][0]
        self.assertIn('0.9% NaCl-ის გარეშე', t_na)

        p_hf = self._create('evar', q={'cad_chips': ['hf']}, s={'creat': 160, 'creat_unit': 'umol', 'lvef': 30})
        bp_hf, _, _ = admit_preop_to_board(p_hf, self.user)
        t_hf = [b.text for b in BoardEntry.objects.filter(patient=bp_hf, kind=BoardEntry.TASK) if 'ნეფროპროტექცია' in b.text][0]
        self.assertIn('0.5', t_hf)

        # Combos S1-12 & Clin S1-5: Board HIT + liver_3x (normal renal & CrCl<30/dialysis)
        p_hl = self._create('open', q={'allergy_chips': ['hit_heparin'], 'med_warfarin': True}, s={'liver_status': '3x'})
        bp_hl, _, _ = admit_preop_to_board(p_hl, self.user)
        t_hl = [b.text for b in BoardEntry.objects.filter(patient=bp_hl, kind=BoardEntry.TASK) if 'HIT' in b.text][0]
        self.assertIn('−75%', t_hl)
        self.assertNotIn('ფონდაპარინუქსი (CrCl', t_hl)

        p_hlr = self._create('open', q={'allergy_chips': ['hit_heparin'], 'on_dialysis': True}, s={'liver_status': '3x'})
        bp_hlr, _, _ = admit_preop_to_board(p_hlr, self.user)
        t_hlr = [b.text for b in BoardEntry.objects.filter(patient=bp_hlr, kind=BoardEntry.TASK) if 'HIT' in b.text][0]
        self.assertIn('−75%', t_hlr)
        self.assertIn('აკრძალულია', t_hlr)

        # Combos S1-11: Board VTE task on CEA ICH & rt-PA <=6d never prescribes LMWH
        p_ich = self._create('cea_symp', s={'brain_imaging': 'large_or_hemorrhage'})
        bp_ich, _, _ = admit_preop_to_board(p_ich, self.user)
        vte_tasks = [b.text for b in BoardEntry.objects.filter(patient=bp_ich, kind=BoardEntry.TASK) if 'VTE' in b.text]
        self.assertTrue(all('LMWH პროფილაქტიკური' not in t for t in vte_tasks))

    def test_http_header_bd_self_fill_rotate_med_call_signoff_and_overflow(self):
        # F-S1-04: Future birth_date via header POST is recorded as invalid & blocks GO/approve
        p_bd = self._create('open')
        fut_bd = (datetime.date.today() + datetime.timedelta(days=365)).isoformat()
        self.client.post(reverse('preop-patient', args=[p_bd.pk]), {
            'section': 'header', 'patient_name': p_bd.patient_name, 'age': '65', 'sex': 'M',
            'op_subtype': 'open', 'birth_date': fut_bd,
        })
        p_bd.refresh_from_db()
        self.assertEqual(p_bd.studies.get('_invalid_inputs', {}).get('birth_date'), fut_bd)
        self.assertNotEqual(evaluate(p_bd)['clearance_seal']['code'], 'go')

        # F-S2-01 & Combos S2-8: self_link rotate unlocks link (incl. legacy self_filled=True) & self_fill_used parity
        p_rot = self._create('evla', q={'filled': True, 'self_filled': True, 'self_fill_submitted': False, 'filled_by': 'პაციენტი (ბმულით)', 'nurse_verified': False})
        self.client.post(reverse('preop-ui-mode'), {'mode': 'old'})
        r_leg = self.client.get(reverse('preop-patient', args=[p_rot.pk]) + '?tab=q')
        self.assertTrue(r_leg.context['self_fill_used'])
        self.client.post(reverse('preop-patient', args=[p_rot.pk]), {'section': 'self_link', 'action': 'rotate'})
        p_rot.refresh_from_db()
        tok2 = make_self_fill_token(p_rot)
        anon = self.client_class()
        self.assertFalse(anon.get(reverse('preop-self-fill', args=[tok2])).context['locked'])

        # Combos S1-6: self_fill age/sex update changing CrCl invalidates med_hold_agreed
        p_sf = self._create('open', age=None, sex='', q={'filled': False, 'nurse_verified': False, 'med_doac': True}, s={'creat': 170, 'creat_unit': 'umol', 'weight_kg': 55, 'med_hold_agreed': True})
        tok_sf = make_self_fill_token(p_sf)
        anon.post(reverse('preop-self-fill', args=[tok_sf]), {'age': '85', 'sex': 'F', 'med_doac': '1', 'mets_ok': '1'})
        p_sf.refresh_from_db()
        self.assertFalse(p_sf.studies.get('med_hold_agreed'))

        # Combos S1-7: med-call clear invalidates approved_at & someday_list
        p_cl = self._create('open', q={'med_doac': True})
        self.client.post(reverse('preop-patient', args=[p_cl.pk]), {'section': 'signoff', 'action': 'approve', 'move_ready': '1'})
        p_cl.refresh_from_db()
        self.assertIsNotNone(p_cl.approved_at)
        self.assertEqual(p_cl.someday_list, PreopPatient.SomedayList.READY)
        self.client.post(reverse('preop-med-call', args=[p_cl.pk]), {'action': 'clear'})
        p_cl.refresh_from_db()
        self.assertIsNone(p_cl.approved_at)
        self.assertNotEqual(p_cl.someday_list, PreopPatient.SomedayList.READY)

        # Combos S1-8 & F-S3-06: med-call called blocked on missing-CrCl / contraindicated / unstructured meds
        for qx, sx in (
            ({'med_doac': True}, {'creat': None}),
            ({'med_aspirin': True, 'allergy_chips': ['nsaid']}, {}),
            ({'other_meds': 'ქსარელტო 20 მგ'}, {}),
        ):
            p_mc = self._create('open', q=qx, s={**sx, 'med_hold_agreed': False})
            self.client.post(reverse('preop-med-call', args=[p_mc.pk]), {'action': 'called'})
            p_mc.refresh_from_db()
            self.assertFalse(p_mc.studies.get('med_hold_agreed'), str(qx))

        # F-S2-03 & Combos S3-2: approve on unacked red flag sets alerts_ack_at; ack_alerts refused on STOP
        p_rf = self._create('open', s={'bp_sys': 200, 'bp_dia': 120})
        self.client.post(reverse('preop-patient', args=[p_rf.pk]), {'section': 'signoff', 'action': 'approve'})
        p_rf.refresh_from_db()
        self.assertIsNotNone(p_rf.approved_at)
        self.assertIsNotNone(p_rf.alerts_ack_at)
        p_st = self._create('evla', s={'ven_duplex_status': 'acute_dvt'})
        self.client.post(reverse('preop-patient', args=[p_st.pk]), {'section': 'signoff', 'action': 'ack_alerts'})
        p_st.refresh_from_db()
        self.assertIsNone(p_st.alerts_ack_at)

        # F-S3-02: Corrupted sibling board_patient_id >= 2**64 never causes HTTP 500
        self._create('open', patient_name='ძმა ოუვერფლოუ', s={'board_patient_id': 10**25})
        p_ok = self._create('open', patient_name='ძმა ოუვერფლოუ')
        self.assertEqual(self.client.get(reverse('preop-patient', args=[p_ok.pk])).status_code, 200)

    def test_rendered_templates_dual_allergy_rtpa_mech_valve_and_jump_targets(self):
        # Combos S1-2: _tab_s2.html never writes პოვიდონ-იოდ when dual latex_chlorhex + allergy_iodine
        p_du = self._create('open', q={'allergy_chips': ['latex_chlorhex'], 'allergy_iodine': True})
        self.client.post(reverse('preop-ui-mode'), {'mode': 'old'})
        html_du = self.client.get(reverse('preop-patient', args=[p_du.pk])).content.decode('utf-8')
        self.assertNotIn('პოვიდონ-იოდ', html_du)

        # Combos S1-3: _tab_s2.html never writes ± კლოპიდოგრელი on CEA rt-PA days 1-6
        p_rt = self._create('cea_symp', q={'cea_thrombolysis': True, 'cea_days_since_event': 2, 'med_aspirin': False}, s={'brain_imaging': 'normal_small'})
        html_rt = self.client.get(reverse('preop-patient', args=[p_rt.pk])).content.decode('utf-8')
        self.assertNotIn('± კლოპიდოგრელი', html_rt)

        # Combos S2-5: _tab_s2.html PTA dapt row respects mech_valve (VKA / DOAC forbidden)
        p_mv = self._create('pta_clti', q={'cad_chips': ['mech_valve'], 'med_warfarin': True})
        html_mv = self.client.get(reverse('preop-patient', args=[p_mv.pk])).content.decode('utf-8')
        self.assertIn('DOAC / VOYAGER აკრძალულია', html_mv)

        # Combos S2-2: Healthy all-negative self-fill still renders #btn-verify-self in _tab_q.html
        p_hn = self._create('evla', q={'filled': True, 'self_fill_submitted': True, 'nurse_verified': False, 'med_aspirin': False, 'med_chips': []})
        html_hn = self.client.get(reverse('preop-patient', args=[p_hn.pk])).content.decode('utf-8')
        self.assertIn('id="btn-verify-self"', html_hn)

        # Combos S2-4: patient_new.html renders editable header & Stage-1 study inputs for missing items
        self.client.post(reverse('preop-ui-mode'), {'mode': 'new'})
        p_nw = self._create('cea_symp', age=None, sex='', side='', s={'ecg': '', 'carotid_r': None})
        html_nw = self.client.get(reverse('preop-patient', args=[p_nw.pk])).content.decode('utf-8')
        self.assertIn('id="nw-sec-header"', html_nw)
        self.assertIn('id="nw-sec-s1"', html_nw)
        self.assertIn('name="carotid_r"', html_nw)
