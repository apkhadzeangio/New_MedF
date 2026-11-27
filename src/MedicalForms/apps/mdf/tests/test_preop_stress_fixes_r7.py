"""Stage 4 Round-7 regression tests covering ALL Category (a) S1/S2/S3 findings from Round-6 3-agent re-verification."""
import datetime
import json

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from ..models import BoardEntry, Case, PreopPatient, StaffProfile
from ..preop import (
    admit_preop_to_board,
    build_self_fill_highlights,
    calc_ariscat_pulm,
    calc_auto_asa,
    calc_crcl,
    calc_egfr,
    calc_vsg_cri,
    evaluate,
    generate_anamnesis,
    make_self_fill_token,
    nurse_plan,
)
from ..preop.calculators import normalize_albumin_gl, normalize_fibrinogen_gl, normalize_hb_gl
from ..preop.meds import _detect_freetext_high_risk_meds, _effective_allergy_state
from ..preop.registry import REGISTRY_VERSION, RULES_BY_ID, STATUS_LOCAL, rule_value
from ..preop.utils import _num, _strip_non_finite, target_prep_days

TODAY = datetime.date(2026, 11, 26)
FUTURE_OP = (TODAY + datetime.timedelta(days=10)).isoformat()


def _go(sub='open', **kw):
    q = {'filled': True, 'nurse_verified': True}
    q.update(kw.pop('q', {}))
    s = {
        'labs_date': TODAY.isoformat(),
        'bp_sys': 125, 'bp_dia': 78, 'hr': 72, 'spo2': 98,
        'weight_kg': 80, 'height_cm': 175,
        'hb': 135, 'plt': 220, 'inr': 1.05, 'creat': 85, 'creat_unit': 'umol',
        'glucose': 5.4, 'k': 4.2, 'na': 140, 'ecg': 'normal', 'cxr': 'normal',
        'lvef': 58, 'aaa_mm': 58, 'cta_disc': True, 'aaa_neck_status': 'friendly_ifu',
        'aaa_iliac_access': 'ok_7mm', 'popliteal_screen_status': 'normal',
        'carotid_r': 75, 'carotid_l': 20, 'cea_cta_status': 'concordant',
        'brain_imaging': 'normal_small', 'abi_r': 0.55, 'abi_l': 0.95,
        'abf_clamp_zone': 'infrarenal', 'abf_outflow': 'patent',
        'pad_runoff_status': 'popliteal_patent', 'pad_gsv_status': 'adequate', 'pad_gsv_mm': 4.0,
        'pta_lesion_site': 'femoropopliteal', 'pta_access_site': 'cfa_clean',
        'ven_duplex_status': 'deep_patent_reflux', 'blood_group': 'A+',
        'blood_reserved': True, 'evar_sized': True, 'decolonization_ready': True,
        'dapt_ready': True, 'compression_ready': True, 'vte_prophylaxis_planned': True,
        'med_hold_agreed': True, 'anesth_done': True, 'dos_bhcg_negative': True,
        'planned_surgery_date': FUTURE_OP,
    }
    s.update(kw.pop('s', {}))
    pw = PreopPatient.SUBTYPE_PATHWAY[sub]
    side = kw.pop('side', 'R' if pw in ('cea', 'pad_bypass', 'pad_pta', 'venous') else '')
    return PreopPatient(
        patient_name=kw.pop('patient_name', 'R7 Test'),
        age=kw.pop('age', 65),
        sex=kw.pop('sex', 'M'),
        side=side,
        op_subtype=sub,
        pathway=pw,
        cardio_decision=kw.pop('cardio_decision', 'cleared'),
        questionnaire=q,
        studies=s,
        **kw,
    )


class Round7EngineAndInvariantsTests(SimpleTestCase):
    """Pure engine/calculator/pharmacology/pathway tests for Round-7 fixes."""

    def test_s1_rupture_bilateral_venous_dos_inr_mgdl_vriii_and_failsafe(self):
        # Fuzz A-S1-02: AAA rupture suspect stays True on inverted shock BP (85/85, HR=110)
        q_rup = {'aaa_pain': True, 'med_warfarin': True}
        ev_rup = evaluate(_go('open', q=q_rup, s={'aaa_mm': 65, 'bp_sys': 85, 'bp_dia': 85, 'hr': 110}), today=TODAY)
        self.assertTrue(ev_rup['is_rupture_suspect'])
        self.assertIn('70–90', ev_rup['anesthetic_brief']['bp_corridor'])

        # Fuzz A-S1-03: Bilateral venous hard STOP preserved when contralateral limb is negative
        ev_abi = evaluate(_go('evla', side='B', s={'abi_r': 1.05, 'abi_l': -0.1}), today=TODAY)
        self.assertTrue(ev_abi['has_hard_contraindication'])
        self.assertEqual(ev_abi['clearance_seal']['code'], 'stop')
        ev_tp = evaluate(_go('evla', side='B', s={'tp_r': 90, 'tp_l': -5}), today=TODAY)
        self.assertTrue(ev_tp['has_hard_contraindication'])

        # Fuzz A-S1-04 & Combos A-S3-2: Baseline INR > 20 still triggers DoS STOP & VitK/4F-PCC brief
        ev_i21 = evaluate(_go('open', s={'inr': 21.0, 'dos_identity_consent': True}), today=TODAY)
        self.assertTrue(ev_i21['dos_hold_danger'])
        self.assertEqual(ev_i21['clearance_seal']['code'], 'stop')
        self.assertIn('ვიტამინი K', ev_i21['anesthetic_brief']['transfusion_pbm'])

        # Fuzz A-S1-05 / R8 Fuzz A-S1-2: Explicit creat_unit='mgdl' > 35 never fabricates eGFR 1 while keeping Metformin/ASA failsafe
        q_m = {'metformin': True, 'dm': True}
        ev_u = evaluate(_go('evar', q=q_m, s={'creat': 3182.4, 'creat_unit': 'umol'}), today=TODAY)
        ev_g = evaluate(_go('evar', q=q_m, s={'creat': 36.0, 'creat_unit': 'mgdl'}), today=TODAY)
        self.assertIsNone(ev_g['egfr'])
        self.assertIsNone(ev_g['crcl'])
        self.assertTrue(ev_g['creat_unit_mismatch'])
        self.assertEqual(ev_g['auto_asa'], ev_u['auto_asa'])
        met_g = next(r for r in ev_g['med_schedule'] if 'მეტფორმინ' in r['drug'])
        self.assertTrue(met_g['contraindicated'])
        self.assertTrue(ev_g['has_blocking_unknown_state'])

        # Fuzz A-S1-06: Fibrinogen-only / empty demographics / partial venous vitals block GO
        p_fib = PreopPatient(
            patient_name='FibOnly', age=None, sex='', side='', op_subtype='open',
            pathway='aaa_infra', questionnaire={'filled': True, 'nurse_verified': True},
            studies={'fibrinogen': 3.0},
        )
        ev_fib = evaluate(p_fib, today=TODAY)
        self.assertTrue(ev_fib['has_blocking_unknown_state'])
        keys_fib = {u['key'] for u in ev_fib['unknown_state_flags']}
        self.assertTrue({'labs_missing_date', 'missing_demographics', 'missing_core_vitals'} <= keys_fib)
        ev_vp = evaluate(_go('evla', s={'bp_dia': None, 'hr': None, 'spo2': None}), today=TODAY)
        self.assertNotEqual(ev_vp['clearance_seal']['code'], 'go')
        self.assertTrue(ev_vp['has_blocking_unknown_state'])

        # Fuzz A-S1-09: DoS severe hyperglycemia (>=12 mmol/L) emits VRIII even with normal baseline glucose
        ev_vr = evaluate(_go('open', s={'glucose': 5.5, 'dos_glucose': 18.0}), today=TODAY)
        self.assertIn('VRIII', ev_vr['anesthetic_brief']['glycemic_target'])

    def test_s1_cea_bleed_precedence_freetext_meds_elapsed_holds_and_ali_bypass(self):
        # Combos A-S1-2 / Clin CAT_A-S1-01: CEA ICH / rt-PA <=6d overrides HIT in surgical_brief.heparin_plan
        ev_ch = evaluate(_go(
            'cea_symp', q={'allergy_chips': ['hit_heparin']}, s={'brain_imaging': 'large_or_hemorrhage'},
        ), today=TODAY)
        hep_txt = ev_ch['surgical_brief']['heparin_plan']
        self.assertIn('ჰემორაგ', hep_txt)
        self.assertIn('HIT', hep_txt)
        self.assertNotIn('ფონდაპარინუქსი', hep_txt)
        self.assertNotIn('ბივალირუდინი', hep_txt)

        # Combos A-S1-3: passport_guide / phone stop_rows exclude MUST-CONTINUE non-hold drugs (Steroid, EVLA Warfarin, Recent-Stent P2Y12, PPI)
        for sub, qx, sx in (
            ('evar', {'med_chips': ['steroid']}, {}),
            ('evla', {'med_warfarin': True}, {'inr': 2.2}),
            ('evar', {'recent_stent': True, 'med_clopidogrel': True}, {}),
            ('cea_symp', {'med_clopidogrel': True, 'med_chips': ['ppi']}, {}),
        ):
            p_c = _go(sub, q=qx, s=sx)
            ev_c = evaluate(p_c, today=TODAY)
            guide_c = nurse_plan.build_patient_plain_explanation(p_c, ev_c, TODAY)
            self.assertEqual(guide_c['stop_meds'], [], f'Non-hold drug leaked into stop_meds: {qx}')
            self.assertTrue(len(guide_c['caution_meds']) >= 1, f'Missing caution_meds for {qx}')
            self.assertFalse(any(r['raw_drug'] in {c['raw_drug'] for c in guide_c['caution_meds']} for r in guide_c['keep_meds']))

        # Combos A-S1-5, Fuzz A-S1-12, Combos A-S2-5, Combos A-S3-3: Free-text Aspirin, Marevan, Rivaroxaban 20mg, VOYAGER co-meds, Omeprazole, Georgian allergy brands
        ft_asa = _detect_freetext_high_risk_meds('თრომბო ასი 100 მგ', q={})
        self.assertIn('aspirin', ft_asa['matched_keys'])
        self.assertTrue(ft_asa['unstructured_antithrombotic'])
        ft_mar = _detect_freetext_high_risk_meds('მარევანი 3 მგ', q={})
        self.assertIn('warfarin', ft_mar['matched_keys'])
        self.assertTrue(ft_mar['unstructured_antithrombotic'])
        ft_riva_esc = _detect_freetext_high_risk_meds(
            'ქსარელტო 2.5 მგ დღეში ორჯერ დილით და საღამოს, ხოლო კარდიოლოგმა დაუნიშნა 20 მგ',
            q={'med_chips': ['rivaroxaban_2_5']},
        )
        self.assertTrue(ft_riva_esc['unstructured_antithrombotic'])
        for ok_txt in (
            'ქსარელტო 2.5 მგ + 10 მგ კონკორი',
            'ქსარელტო 2.5 მგ და 10 მგ ამლოდიპინი',
            'xarelto 2.5 mg x2 + 10 mg bisoprolol',
        ):
            ft_ok = _detect_freetext_high_risk_meds(ok_txt, q={'med_chips': ['rivaroxaban_2_5']})
            self.assertEqual(ft_ok['unstructured_labels'], [], ok_txt)
        ev_om = evaluate(_go('cea_symp', q={'med_clopidogrel': True, 'other_meds': 'ნექსიუმი 40 მგ'}), today=TODAY)
        self.assertTrue(any('პანტოპრაზოლ' in r['drug'] for r in ev_om['med_schedule']))
        for brand, exp in (('ბეტადინი', 'iodine'), ('ოგმენტინი', 'beta_lactam'), ('ნიმესილი', 'nsaid'), ('ულტრაკაინი', 'lidocaine')):
            chips, eff_iod, _ = _effective_allergy_state({'allergy_text': brand})
            if exp == 'iodine':
                self.assertTrue(eff_iod, brand)
            else:
                self.assertIn(exp, chips, brand)

        # Fuzz A-S1-08: Already-elapsed drug hold window (tomorrow op + Warfarin 5d hold) raises red alert
        tmrw = (TODAY + datetime.timedelta(days=1)).isoformat()
        ev_el = evaluate(_go('open', q={'med_warfarin': True}, s={'planned_surgery_date': tmrw}), today=TODAY)
        self.assertTrue(any('ფანჯარა' in rf and 'გასული' in rf for rf in ev_el['red_flags']))

        # Clin CAT_A-S1-02 / Combos A-S2-8: ALI bypasses elective workup tasks/reflexes, adds +8 ARISCAT, no elective postpone wording on pregnancy
        ev_ali = evaluate(_go(
            'bypass_clti', sex='F', age=35,
            q={'abf_duration': '< 2 კვირა', 'ulcer_active_symp': True, 'pregnant_possible': True},
            s={'hb': 95, 'glucose': 15.0, 'creat': 170, 'urine_wbc': 30, 'urine_nitrite': True, 'nt_probnp': 600},
        ), today=TODAY)
        task_keys = {t['key'] for t in ev_ali['tasks']}
        for el_k in ('anemia', 'hba1c', 'endo', 'nephro', 'gastro', 'uti', 'cardio', 'tbi'):
            self.assertNotIn(el_k, task_keys, f'Elective task {el_k} not bypassed on ALI')
        self.assertFalse(any('გეგმური ოპერაციის გადადების' in rf for rf in ev_ali['red_flags']))
        ar_ali = calc_ariscat_pulm(_go('bypass_clti', q={'abf_duration': '< 2 კვირა'}), {'abf_duration': '< 2 კვირა'}, {})
        ar_chr = calc_ariscat_pulm(_go('bypass_clti', q={'abf_duration': '> 3 თვე'}), {'abf_duration': '> 3 თვე'}, {})
        self.assertEqual(ar_ali['score'] - ar_chr['score'], 8)

    def test_s2_monotonicity_calculators_overflow_tuples_and_unhashable(self):
        # Fuzz A-S2-01: Inverted BP monotonicity (3 cases)
        self.assertEqual(calc_auto_asa(_go('evla'), {'filled': True}, {'bp_sys': 95, 'bp_dia': 95, 'hr': 96}), 'IV')
        self.assertEqual(calc_auto_asa(_go('evla'), {'filled': True}, {'bp_sys': 120, 'bp_dia': 80, 'bp_sys_l': 185, 'bp_dia_l': 190}), 'III')
        ev_bp_c = evaluate(_go('pta_claud', age=55, s={'bp_sys': 85, 'bp_dia': 115}), today=TODAY)
        self.assertTrue(ev_bp_c['severe_htn'] and ev_bp_c['needs_cardio'])

        # Fuzz A-S2-02: Fibrinogen idempotency in 15.1..19.9 mg/dL
        self.assertEqual(normalize_fibrinogen_gl(16), 0.16)
        self.assertEqual(normalize_fibrinogen_gl(normalize_fibrinogen_gl(16)), 0.16)

        # Fuzz A-S2-03: Out-of-range saturation monotonicity
        ev_a120 = evaluate(_go('evla', age=120, q={'med_aspirin': True, 'med_chips': ['steroid']}, s={'creat': 200}), today=TODAY)
        ev_a125 = evaluate(_go('evla', age=125, q={'med_aspirin': True, 'med_chips': ['steroid']}, s={'creat': 200}), today=TODAY)
        self.assertLessEqual(ev_a125['lidocaine_max_mg'], ev_a120['lidocaine_max_mg'])
        self.assertTrue(ev_a125['needs_ppi'])
        ev_hb_low = evaluate(_go('open', s={'hb': 0.5, 'ferritin': 15}), today=TODAY)
        self.assertTrue(ev_hb_low['pbm_iv_iron_indicated'])
        self.assertTrue(any(r['key'] == 'anemia' for r in ev_hb_low['reflex_orders']))
        ev_a1c = evaluate(_go('bypass_clti', s={'hba1c': 26}), today=TODAY)
        self.assertTrue(ev_a1c['needs_endo_reflex'] and ev_a1c['needs_tbi_check'])
        ev_abi35 = evaluate(_go('open', s={'abi_r': 3.5}), today=TODAY)
        self.assertTrue(ev_abi35['abi_non_compressible'] and ev_abi35['needs_tbi_check'])
        ev_aaa210 = evaluate(_go('open', s={'aaa_mm': 210, 'cta_months': 4}), today=TODAY)
        self.assertTrue(any('ხანდაზმულია' in rf for rf in ev_aaa210['red_flags']))

        # Fuzz A-S2-04: calc_crcl clamps height_cm (no upward jump at 100->99 or 230->231)
        self.assertLessEqual(calc_crcl(130, 65, 'M', weight_kg=120, height_cm=99), calc_crcl(130, 65, 'M', weight_kg=120, height_cm=100))
        self.assertLessEqual(calc_crcl(130, 65, 'M', weight_kg=160, height_cm=231), calc_crcl(130, 65, 'M', weight_kg=160, height_cm=230))

        # Fuzz A-S2-05, A-S2-06, A-S3-01: 10**400 OverflowError, tuple NaN/Inf, unhashable side/op_subtype
        self.assertIsNone(_num(10**400))
        self.assertIsNone(calc_egfr(10**400, 65, 'M'))
        ev_big = evaluate(_go('open', s={'creat': 10**400, 'extra_tup': (float('nan'), 1.0)}), today=TODAY)
        json.dumps(ev_big['s'], allow_nan=False)
        cleaned = _strip_non_finite({'k': (float('nan'), float('inf'), 2.0)})
        json.dumps(cleaned, allow_nan=False)
        p_uh = _go('evla')
        p_uh.side = [1, 2]
        evaluate(p_uh, today=TODAY)
        p_uh2 = _go('open')
        p_uh2.op_subtype = {}
        evaluate(p_uh2, today=TODAY)

    def test_s2_pharmacology_pathways_anamnesis_and_registry_honesty(self):
        # Clin CAT_A-S2-01: Postop brief does not instruct resuming contraindicated dabigatran/DOAC
        ev_dd = evaluate(_go('open', q={'on_dialysis': True, 'med_dabigatran': True}), today=TODAY)
        self.assertNotIn('მუდმივი ანტიკოაგულანტის (OAC) აღდგენა', ev_dd['surgical_brief']['postop_antithrombotic'])
        ev_dl = evaluate(_go('open', q={'med_doac': True}, s={'liver_status': '3x'}), today=TODAY)
        self.assertNotIn('მუდმივი ანტიკოაგულანტის (OAC) აღდგენა', ev_dl['surgical_brief']['postop_antithrombotic'])

        # Clin CAT_A-S2-02: Contraindicated Prasugrel emits immediate stop/switch instead of future calendar
        ev_pr = evaluate(_go('cea_symp', q={'med_prasugrel': True, 'cea_days_since_event': 8}), today=TODAY)
        pr_r = next(r for r in ev_pr['med_schedule'] if 'პრასუგრელ' in r['drug'])
        self.assertNotIn('ბოლო:', pr_r['date_plan'])
        self.assertIn('უკუნაჩვენებია', pr_r['date_plan'])

        # Combos A-S2-1: PTA + rivaroxaban_2_5 + clopidogrel_intol no Ticagrelor contradiction
        ev_pta_r = evaluate(_go('pta_clti', q={'med_chips': ['rivaroxaban_2_5'], 'allergy_chips': ['clopidogrel_intol']}), today=TODAY)
        self.assertNotIn('OAC + ტიკაგრელორი', ' '.join(ev_pta_r['red_flags']))

        # Clin CAT_A-S2-04..08 & Combos A-S2-3..9
        ev_cilo = evaluate(_go('bypass_claud', q={'med_chips': ['cilostazol']}, s={'creat': None}), today=TODAY)
        self.assertIn('renal_drug_no_crcl', {u['key'] for u in ev_cilo['unknown_state_flags']})
        ev_pad_asa = evaluate(_go('phlebectomy', q={'med_aspirin': True, 'claudication': True}, s={'abi_r': 0.72}), today=TODAY)
        asa_r = next(r for r in ev_pad_asa['med_schedule'] if 'ასპირინ' in r['drug'])
        self.assertFalse(asa_r['hold_or_skip'])
        ev_hl = evaluate(_go('evla', q={'ven_hormones': True}, s={'liver_status': '3x'}), today=TODAY)
        hm_r = next(r for r in ev_hl['med_schedule'] if 'ჰორმონ' in r['drug'])
        self.assertEqual(hm_r['tone'], 'red')
        self.assertTrue(hm_r['contraindicated'])
        ev_tbi = evaluate(_go('evla', s={'abi_r': 1.65, 'tbi_r': 0.22, 'tp_r': None}), today=TODAY)
        self.assertTrue(ev_tbi['has_hard_contraindication'])
        ev_leak = evaluate(_go('evla', age=42, q={'aaa_pain': True, 'cea_crescendo': True}), today=TODAY)
        self.assertFalse(ev_leak['is_acute_emergency'])
        self.assertEqual(ev_leak['auto_asa'], 'I')
        ev_ph_vka = evaluate(_go('phlebectomy', q={'med_warfarin': True}, s={'inr': 2.2, 'anesthesia_plan': 'auto'}), today=TODAY)
        if ev_ph_vka['has_neuraxial_plan']:
            self.assertNotIn('ნეირაქსიული შეზღუდულია', ev_ph_vka['effective_anesth_label'])
        ev_hit_l = evaluate(_go('evla', q={'allergy_chips': ['hit_heparin'], 'cad_chips': ['mech_valve']}, s={'liver_status': '3x', 'vte_prophylaxis_planned': False}), today=TODAY)
        vte_t = next(t for t in ev_hit_l['tasks'] if t['key'] == 'vte')
        self.assertIn('−75%', vte_t['label'])
        ev_na_hf = evaluate(_go('evar', q={'cad_chips': ['hf']}, s={'creat': 170, 'na': 154, 'lvef': 30}), today=TODAY)
        self.assertIn('HFrEF', ev_na_hf['surgical_brief']['contrast_plan'])
        ev_lido_m = evaluate(_go('evla', q={'metformin': True, 'ven_lidocaine_allergy': True}, s={'creat': 170, 'anesthesia_plan': 'auto'}), today=TODAY)
        met_l = next(r for r in ev_lido_m['med_schedule'] if 'მეტფორმინ' in r['drug'])
        self.assertNotIn('ადგილობრივ არაკონტრასტულ EVLA-ზე 48 სთ შეჩერება არ ესაჭიროება', met_l['rule'])
        ev_cad = evaluate(_go('evar', q={'cad': False, 'cad_chips': ['mech_valve']}, cardio_decision='stress'), today=TODAY)
        self.assertTrue(ev_cad['needs_cardio'] and ev_cad['needs_echo_or_bnp'])
        self.assertFalse(ev_cad['cardio_non_compliant'])
        vsg_unf = calc_vsg_cri(_go('open'), {'filled': False, 'recent_stent': True, 'cad_chips': ['stent_old']}, {})
        self.assertFalse(any('(-1)' in f for f in vsg_unf['factors']))

        # Clin CAT_A-S2-09 & Combos A-S2-2 & S3: Anamnesis, highlights, target_prep_days, vein_strip badge, Caprini 4 text, Registry honesty
        hl_old_rtpa = build_self_fill_highlights({'filled': True, 'cea_thrombolysis': True, 'cea_days_since_event': 10})
        self.assertFalse(any('≤6' in lbl for _, lbl in hl_old_rtpa))
        p_ca = _go('cea_asymp', q={'cea_thrombolysis': True, 'cea_crescendo': True, 'cea_disabling_stroke': True})
        ev_ca = evaluate(p_ca, today=TODAY)
        self.assertTrue(ev_ca['needs_brain_ct'] and ev_ca['needs_neuro'])
        anam_ca = generate_anamnesis(p_ca, ev_ca)
        self.assertNotIn('ინსულტს / TIA-ს უარყოფს', anam_ca)
        self.assertIn('თრომბოლიზისი', anam_ca)
        self.assertEqual(target_prep_days(_go('cea_asymp', q={'filled': False, 'cea_symp_event': True}), today=TODAY), 5)
        p_vn = _go('evla', q={'ven_cancer_immob': True, 'ven_skin_infection': True, 'active_infection': True, 'inf_chips': ['skin_boil']}, s={'hba1c': 7.8})
        anam_vn = generate_anamnesis(p_vn, evaluate(p_vn, today=TODAY))
        self.assertIn('ონკოლოგ', anam_vn)
        self.assertIn('ინფექც', anam_vn)
        self.assertIn('HbA1c', anam_vn)
        self.assertTrue(evaluate(p_vn, today=TODAY)['needs_ecg_venous'])
        ev_cap4 = evaluate(_go('evla', age=35, sex='F', q={'ven_hormones': True}, s={'weight_kg': 90, 'height_cm': 168, 'vte_prophylaxis_planned': False}), today=TODAY)
        self.assertFalse(any('მაღალი თრომბოემბოლიური რისკი (Caprini 4)' in a for a in ev_cap4['amber_flags']))
        ev_vs = evaluate(_go('bypass_clti', q={'vasc_chips': ['vein_strip']}, s={'pad_gsv_status': 'adequate', 'pad_gsv_mm': 4.0}), today=TODAY)
        self.assertNotEqual(ev_vs['key_badge'], 'GSV 4.0მმ')
        ev_empty_p = evaluate(_go('cea_symp', q={'cea_thrombolysis': True, 'cea_days_since_event': 3, 'med_aspirin': True}, s={'planned_surgery_date': ''}), today=TODAY)
        self.assertFalse(any('()' in r['date_plan'] for r in ev_empty_p['med_schedule']))
        ev_ph_h = evaluate(_go('phlebectomy', q={'ven_hormones': True}, s={'med_hold_agreed': False}), today=TODAY)
        self.assertFalse(any('ანტიკოაგულანტს / კლოპიდოგრელს / SGLT2 / ინსულინს' in a for a in ev_ph_h['amber_flags']))
        p_abf_bad = _go('abf_claud', q={'abf_claud_m': -50}, s={'abi_r': 999, 'abi_l': -5})
        an_abf = generate_anamnesis(p_abf_bad, evaluate(p_abf_bad, today=TODAY))
        self.assertNotIn('999', an_abf)
        self.assertNotIn('-50', an_abf)
        p_gsv26 = _go('bypass_clti', s={'pad_gsv_mm': 26.5})
        self.assertIn('26.5', generate_anamnesis(p_gsv26, evaluate(p_gsv26, today=TODAY)))

        # Registry honesty (v1.2.6+)
        self.assertGreaterEqual(tuple(int(x) for x in REGISTRY_VERSION.split('.')), (1, 2, 6))
        for rid in (
            'VEN_VTE_CAPRINI_PROPHYLAXIS', 'ANES_AIRWAY_STOPBANG_ICU',
            'SCORE_ASA_PHYSICAL_STATUS', 'VEN_ARTERIAL_ISCHEMIA_COMPRESSION',
            'MED_CILOSTAZOL_BLACKBOX', 'LAB_UNIT_AUTO_CONVERSION',
        ):
            self.assertEqual(RULES_BY_ID[rid].status, STATUS_LOCAL, rid)
        self.assertIn('<= 6', RULES_BY_ID['CEA_ACUTE_NEURO_THROMBOLYSIS'].title_ka)
        self.assertEqual(normalize_hb_gl(13.5), 135.0)
        self.assertEqual(normalize_albumin_gl(3.5), 35.0)
        self.assertIn('hb_gdl_hi', rule_value('LAB_UNIT_AUTO_CONVERSION'))


class Round7HttpAndTemplateTests(TestCase):
    """HTTP workflow, template rendering, Board handoff, and New Nurse UI tests for Round-7 fixes."""

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.doc = User.objects.create_user(username='r7_doc', password='DocPassword!1', first_name='დავით', last_name='ექიმი', is_staff=True)
        StaffProfile.objects.create(user=cls.doc, display_name='დავით ექიმი')
        cls.nurse = User.objects.create_user(username='r7_nurse', password='NursePassword!1', first_name='მარი', last_name='ექთანი')
        StaffProfile.objects.create(user=cls.nurse, display_name='მარი ექთანი')

    def _create(self, sub='open', **kw):
        p = _go(sub, surgeon=self.doc, created_by=self.doc, updated_by=self.doc, **kw)
        p.save()
        return p

    def test_http_selffill_rotate_medcall_reschedule_sync_stage2_move_and_invalid_inputs(self):
        c_doc = self.client_class()
        c_doc.force_login(self.doc)
        c_nurse = self.client_class()
        c_nurse.force_login(self.nurse)
        c_anon = self.client_class()

        # Fuzz A-S1-01 / Combos A-S1-1: rotate preserves unverified_self_fill block, AND unlocks after nurse_verified=True
        p_rot = self._create('evla', q={'filled': True, 'self_fill_submitted': True, 'filled_by': 'პაციენტი (ბმულით)', 'nurse_verified': False})
        c_nurse.post(reverse('preop-patient', args=[p_rot.pk]), {'section': 'self_link', 'action': 'rotate'})
        p_rot.refresh_from_db()
        ev_r = evaluate(p_rot)
        self.assertNotEqual(ev_r['clearance_seal']['code'], 'go')
        self.assertTrue(ev_r['has_blocking_unknown_state'])
        c_doc.post(reverse('preop-patient', args=[p_rot.pk]), {'section': 'signoff', 'action': 'approve'})
        p_rot.refresh_from_db()
        self.assertIsNone(p_rot.approved_at)
        # Rotate after nurse_verified=True unlocks new token for patient re-submission
        p_rot.questionnaire = {**p_rot.questionnaire, 'nurse_verified': True}
        p_rot.save(update_fields=['questionnaire'])
        c_nurse.post(reverse('preop-patient', args=[p_rot.pk]), {'section': 'self_link', 'action': 'rotate'})
        p_rot.refresh_from_db()
        tok_new = make_self_fill_token(p_rot)
        self.assertFalse(c_anon.get(reverse('preop-self-fill', args=[tok_new])).context['locked'])

        # Fuzz A-S1-07 / Combos A-S1-6: med-call & stage2 block med_hold_agreed without surgery date / on unsafe states
        for qx, sx in (
            ({'med_warfarin': True}, {'planned_surgery_date': '', 'med_hold_agreed': False}),
            ({'med_doac': True, 'cad_chips': ['mech_valve']}, {'med_hold_agreed': False}),
            ({'med_doac': True}, {'creat': None, 'med_hold_agreed': False}),
            ({'other_meds': 'ქსარელტო 20 მგ'}, {'med_hold_agreed': False}),
            ({'filled': True, 'self_fill_submitted': True, 'nurse_verified': False, 'med_warfarin': True}, {'med_hold_agreed': False}),
            ({'filled': False, 'nurse_verified': False, 'med_warfarin': True}, {'med_hold_agreed': False}),
        ):
            p_g = self._create('open', q=qx, s=sx)
            c_nurse.post(reverse('preop-med-call', args=[p_g.pk]), {'action': 'called'})
            p_g.refresh_from_db()
            self.assertFalse(p_g.studies.get('med_hold_agreed'), f'med-call allowed: {qx}')
            c_doc.post(reverse('preop-patient', args=[p_g.pk]), {'section': 'stage2', 'med_hold_agreed': '1'})
            p_g.refresh_from_db()
            self.assertFalse(p_g.studies.get('med_hold_agreed'), f'stage2 allowed: {qx}')

        # Fuzz A-S1-08: Reschedule rejects past planned_surgery_date
        p_res = self._create('open', s={'planned_surgery_date': FUTURE_OP})
        c_nurse.post(reverse('preop-reschedule', args=[p_res.pk]), {'action': 'set_date', 'planned_surgery_date': '2020-01-05'})
        p_res.refresh_from_db()
        self.assertEqual(p_res.studies.get('planned_surgery_date'), FUTURE_OP)

        # Fuzz A-S1-10: sync=1 invalidates stale approval, ready queue, med_hold_agreed, anamnesis
        case_obj = Case.objects.create(
            doctor=self.doc, patient_name='Sync Case', age=70, sex='M', side='R',
            diagnosis='ქვემო კიდურის ათეროსკლეროზი',
            staging={'R': {'w': 3, 'i': 3, 'fi': 3, 'unsalvageable': True, 'vein': 'no'}},
        )
        p_sy = self._create(
            'bypass_claud', case=case_obj, approved_at=timezone.now(), approved_by=self.doc,
            someday_list=PreopPatient.SomedayList.READY, anamnesis='Old anamnesis',
        )
        c_doc.get(reverse('preop-new') + f'?case={case_obj.pk}&sync=1')
        p_sy.refresh_from_db()
        self.assertIsNone(p_sy.approved_at)
        self.assertNotEqual(p_sy.someday_list, PreopPatient.SomedayList.READY)
        self.assertFalse(p_sy.studies.get('med_hold_agreed'))
        self.assertEqual(p_sy.anamnesis, '')

        # Fuzz A-S1-11: Partial stage2 POST preserves omitted booleans & notes
        p_s2 = self._create('open', s={'blood_reserved': True, 'anesth_done': True, 'nurse_note': 'Keep me', 'nurse_note_chips': ['disc_pending']})
        c_doc.post(reverse('preop-patient', args=[p_s2.pk]), {'section': 'stage2', 'cardio_decision': 'cleared'})
        p_s2.refresh_from_db()
        self.assertTrue(p_s2.studies.get('blood_reserved') and p_s2.studies.get('anesth_done'))
        self.assertEqual(p_s2.studies.get('nurse_note'), 'Keep me')

        # Fuzz A-S2-09: Unapproved / STOP patient cannot be moved into someday:ready
        p_mv = self._create('evla', s={'ven_duplex_status': 'acute_dvt'})
        c_nurse.post(reverse('preop-move', args=[p_mv.pk]), {'action': 'move', 'slot': 'someday:ready'})
        p_mv.refresh_from_db()
        self.assertNotEqual(p_mv.someday_list, PreopPatient.SomedayList.READY)

        # Fuzz A-S2-08: Invalid header age/date, self_fill CEA side='B', and HTTP BP 20/80 parity
        p_hd = self._create('open', age=72)
        c_doc.post(reverse('preop-patient', args=[p_hd.pk]), {
            'section': 'header', 'patient_name': 'Hdr', 'age': '999', 'planned_surgery_date': 'not-a-date',
            'op_subtype': 'open', 'sex': 'M', 'side': '',
        })
        p_hd.refresh_from_db()
        self.assertIn('age', p_hd.studies.get('_invalid_inputs') or {})
        self.assertIn('planned_surgery_date', p_hd.studies.get('_invalid_inputs') or {})
        p_sf_cea = self._create('cea_symp', side='', q={'filled': False, 'nurse_verified': False})
        tok_c = make_self_fill_token(p_sf_cea)
        c_anon.post(reverse('preop-self-fill', args=[tok_c]), {'side': 'B', 'weight_kg': '999'})
        p_sf_cea.refresh_from_db()
        self.assertNotEqual(p_sf_cea.side, 'B')
        self.assertIn('weight_kg', p_sf_cea.studies.get('_invalid_inputs') or {})
        p_bp = self._create('open')
        c_doc.post(reverse('preop-patient', args=[p_bp.pk]), {'section': 'stage1', 's1_partial': '1', 'bp_sys': '20', 'bp_dia': '80'})
        p_bp.refresh_from_db()
        self.assertTrue(any('ინვერტირებულია' in f for f in evaluate(p_bp)['implausible_fields']))

    def test_templates_board_and_new_nurse_ui_parity(self):
        c_doc = self.client_class()
        c_doc.force_login(self.doc)

        # Combos A-S1-2 / Clin CAT_A-S1-01 & A-S2-01 & A-S2-6: Board handoff CEA bleed, contraindicated OAC, liver_3x VTE, no spurious EVLA/HFrEF hydration
        p_cb = self._create('cea_symp', q={'med_doac': True}, s={'brain_imaging': 'large_or_hemorrhage'})
        bp_cb, _, _ = admit_preop_to_board(p_cb, self.doc)
        cb_tasks = [b.text for b in BoardEntry.objects.filter(patient=bp_cb, kind=BoardEntry.TASK)]
        self.assertFalse(any('განახლდეს OAC' in t for t in cb_tasks))
        p_ev_ckd = self._create('evla', s={'creat': 125})
        bp_ev, _, _ = admit_preop_to_board(p_ev_ckd, self.doc)
        self.assertFalse(any('იზოტონური ჰიდრატაცია' in b.text for b in BoardEntry.objects.filter(patient=bp_ev, kind=BoardEntry.TASK)))
        p_lv = self._create('evla', q={'ven_hormones': True}, s={'liver_status': '3x'})
        bp_lv, _, _ = admit_preop_to_board(p_lv, self.doc)
        vte_lv = [b.text for b in BoardEntry.objects.filter(patient=bp_lv, kind=BoardEntry.TASK) if 'VTE პროფილაქტიკა' in b.text]
        self.assertTrue(any('კოაგულოგრამ' in t for t in vte_lv))

        # Combos A-S1-4 & Clin CAT_A-S2-03 & Fuzz A-S3-02: _tab_s2 Na=195, explicit post_antithrombotic_plan, passport creatinine unit
        p_na = self._create('evar', s={'creat': 170, 'na': 195})
        c_doc.post(reverse('preop-ui-mode'), {'mode': 'old'})
        r_na = c_doc.get(reverse('preop-patient', args=[p_na.pk]) + '?tab=s2')
        self.assertNotIn('იზოტონური ჰიდრატაცია (0.9% NaCl) ჩარევის წინ', r_na.content.decode('utf-8'))
        p_vy = self._create('pta_clti', s={'post_antithrombotic_plan': 'voyager'})
        r_vy = c_doc.get(reverse('preop-patient', args=[p_vy.pk]) + '?tab=s2')
        self.assertIn('VOYAGER', r_vy.content.decode('utf-8'))
        r_pp = c_doc.get(reverse('preop-passport', args=[p_vy.pk]))
        self.assertIn('µmol/L', r_pp.content.decode('utf-8'))

        # Combos A-S1-7: New Nurse UI renders #po-rule-modal & po-rules-data and wires sub-panels
        p_nu = self._create('open', q={'filled': False, 'nurse_verified': False})
        c_doc.post(reverse('preop-ui-mode'), {'mode': 'new'})
        r_nu = c_doc.get(reverse('preop-patient', args=[p_nu.pk]))
        html_nu = r_nu.content.decode('utf-8')
        self.assertIn('id="po-rule-modal"', html_nu)
        self.assertIn('id="po-rules-data"', html_nu)
        # Combos A-S1-3: passport_guide renders non-hold caution meds outside Section 3 STOP list
        p_st = self._create('evar', q={'med_chips': ['steroid']})
        r_pg = c_doc.get(reverse('preop-patient-guide', args=[p_st.pk]))
        html_pg = r_pg.content.decode('utf-8')
        self.assertIn('წინასწარ შესაჩერებელი წამალი არ არის მითითებული', html_pg)
        self.assertIn('სტეროიდი', html_pg)
