"""Tests for Pre-op Sprint 6 boundary, combinator, and mutation invariants."""
import datetime
import unittest

import apps.mdf.preop as pm

from ..models import PreopPatient


D0 = datetime.date(2026, 11, 25)


class PreopBoundaryMutationTests(unittest.TestCase):
    """Fast pure-memory boundary & combinator verification across all 22 core functions in preop.py."""

    def test_sprint6_1_age_crcl_contrast_clocks_and_caprini(self):
        # 1. _age_from_dob, calc_crcl, calc_max_contrast_ml, mask_person_id, clocks & CTA months
        self.assertGreaterEqual(pm._age_from_dob(datetime.date(1960, 1, 1)), 65)
        self.assertEqual(pm._age_from_dob(datetime.date(1960, 5, 10), today=datetime.date(2026, 5, 10)), 66)
        self.assertEqual(pm._age_from_dob(datetime.date(1960, 5, 10), today=datetime.date(2026, 5, 9)), 65)

        crcl_lean_m = pm.calc_crcl(1.0, 65, sex='M', weight_kg=80, creat_unit='mgdl', height_cm=180)
        crcl_obese_m = pm.calc_crcl(1.0, 65, sex='M', weight_kg=120, creat_unit='mgdl', height_cm=170)
        crcl_f = pm.calc_crcl(1.0, 65, sex='F', weight_kg=80, creat_unit='mgdl', height_cm=165)
        self.assertEqual(crcl_lean_m, 83)
        self.assertLess(crcl_obese_m, 125)
        self.assertLess(crcl_f, crcl_lean_m)
        self.assertNotEqual(
            pm.calc_crcl(1.0, 65, sex='M', weight_kg=120, creat_unit='mgdl', height_cm=165),
            pm.calc_crcl(1.0, 65, sex='F', weight_kg=120, creat_unit='mgdl', height_cm=165),
        )

        self.assertEqual(pm.calc_max_contrast_ml(29), 29)
        self.assertEqual(pm.calc_max_contrast_ml(30), 45)
        self.assertEqual(pm.calc_max_contrast_ml(59), 118)
        self.assertEqual(pm.calc_max_contrast_ml(60), 180)
        self.assertEqual(pm.mask_person_id(None), '')
        self.assertNotEqual(pm.mask_person_id('01000000012'), '')

        self.assertEqual(pm.target_prep_days(PreopPatient(op_subtype='bypass_claud', pathway='pad_bypass'), today=D0), 14)
        self.assertEqual(pm.target_prep_days(PreopPatient(op_subtype='bypass_clti', pathway='pad_bypass'), today=D0), 7)
        self.assertEqual(pm.target_prep_days(PreopPatient(op_subtype='cea_symp', pathway='cea', questionnaire={'cea_days_since_event': 10}), today=D0), 5)
        self.assertEqual(pm.target_prep_days(PreopPatient(op_subtype='cea_symp', pathway='cea', questionnaire={'cea_days_since_event': 200}), today=D0), 10)
        self.assertEqual(pm.target_prep_days(PreopPatient(op_subtype='cea_asymp', pathway='cea', questionnaire={'filled': True, 'cea_symp_event': True, 'cea_days_since_event': 10}), today=D0), 5)
        self.assertEqual(pm.target_prep_days(PreopPatient(op_subtype='cea_asymp', pathway='cea', questionnaire={'filled': True}), today=D0), 10)
        self.assertEqual(pm.target_prep_days(PreopPatient(op_subtype='pta_clti', pathway='pad_pta'), today=D0), 7)
        self.assertEqual(pm.target_prep_days(PreopPatient(op_subtype='pta_claud', pathway='pad_pta'), today=D0), 14)
        self.assertEqual(pm.target_prep_days(PreopPatient(op_subtype='pta_claud', pathway='pad_pta', questionnaire={'filled': True, 'abf_rest_pain': True}), today=D0), 7)

        p_op_no_dt = PreopPatient(op_subtype='open', pathway='aaa_infra', started_on=D0 - datetime.timedelta(days=5), studies={'case_outcome_status': 'operated'})
        p_op_dt = PreopPatient(op_subtype='open', pathway='aaa_infra', started_on=D0 - datetime.timedelta(days=5), studies={'case_outcome_status': 'operated', 'operated_date': (D0 - datetime.timedelta(days=3)).isoformat()})
        self.assertEqual(pm.prep_days_elapsed(p_op_no_dt, today=D0), 6)
        self.assertEqual(pm.prep_days_elapsed(p_op_dt, today=D0), 3)
        p_hold_same = PreopPatient(op_subtype='open', pathway='aaa_infra', started_on=D0 - datetime.timedelta(days=4), someday_list='hold', studies={'hold_since': (D0 - datetime.timedelta(days=2)).isoformat()})
        self.assertEqual(pm.prep_days_elapsed(p_hold_same, today=D0), 3)

        self.assertEqual(pm.cta_live_months_age({'cta_date': '2026-01-10'}, today=datetime.date(2026, 2, 15)), 2)
        self.assertEqual(pm.cta_live_months_age({'cta_date': '2026-01-10'}, today=datetime.date(2026, 2, 10)), 1)
        self.assertEqual(pm.cta_live_months_age({'cta_date': '2026-01-10'}, today=datetime.date(2026, 2, 5)), 0)

        # 2. calc_caprini
        c_open = pm.calc_caprini(PreopPatient(age=35, op_subtype='open', pathway='aaa_infra'), {}, bmi=22.0, today=D0)
        c_ven = pm.calc_caprini(PreopPatient(age=35, op_subtype='evla', pathway='venous'), {'ven_dvt_history': True}, bmi=22.0, today=D0)
        self.assertNotEqual(c_open[2], c_ven[2])
        self.assertGreater(
            pm.calc_caprini(PreopPatient(age=35, op_subtype='evla', pathway='venous'), {}, today=D0)[0],
            pm.calc_caprini(PreopPatient(age=35, op_subtype='pta_claud', pathway='pad_pta'), {}, today=D0)[0],
        )
        c_liv_norm = pm.calc_caprini(PreopPatient(age=65, op_subtype='evla', pathway='venous'), {'ven_dvt_history': True}, bmi=28.0, today=D0, liver_status='normal')
        c_liv = pm.calc_caprini(PreopPatient(age=65, op_subtype='evla', pathway='venous'), {'ven_dvt_history': True}, bmi=28.0, today=D0, liver_status='3x')
        self.assertNotEqual(c_liv[2], c_liv_norm[2])
        self.assertIn('UFH', c_liv[2])
        self.assertNotIn('UFH', c_liv_norm[2])
        c_bmi25 = pm.calc_caprini(PreopPatient(age=35, op_subtype='evla', pathway='venous'), {}, bmi=25.0, today=D0)
        c_bmi251 = pm.calc_caprini(PreopPatient(age=35, op_subtype='evla', pathway='venous'), {}, bmi=25.1, today=D0)
        self.assertEqual(c_bmi251[0], c_bmi25[0] + 1)
        c_str1 = pm.calc_caprini(PreopPatient(age=35, op_subtype='cea_symp', pathway='cea'), {'cea_symp_event': True, 'cea_days_since_event': 10, 'cea_event_chips': ['minor_stroke']}, today=D0)
        c_str2 = pm.calc_caprini(PreopPatient(age=35, op_subtype='cea_symp', pathway='cea'), {'cea_symp_event': True, 'cea_days_since_event': 10, 'cea_disabling_stroke': True}, today=D0)
        self.assertGreaterEqual(c_str1[0], 7)
        self.assertGreaterEqual(c_str2[0], 7)
        c_pt3 = pm.calc_caprini(PreopPatient(age=42, op_subtype='evla', pathway='venous'), {}, bmi=24.0, today=D0)
        c_pt2 = pm.calc_caprini(PreopPatient(age=35, op_subtype='evla', pathway='venous'), {}, bmi=24.0, today=D0)
        self.assertEqual(c_pt3[0], 3)
        self.assertNotEqual(c_pt3[1], c_pt2[1])
        c_ren25 = pm.calc_caprini(PreopPatient(age=65, op_subtype='evla', pathway='venous'), {'ven_dvt_history': True}, eff_renal=25, today=D0)
        c_ren35 = pm.calc_caprini(PreopPatient(age=65, op_subtype='evla', pathway='venous'), {'ven_dvt_history': True}, eff_renal=35, today=D0)
        self.assertNotEqual(c_ren25[2], c_ren35[2])

    def test_sprint6_2_build_med_schedule_antithrombotics(self):
        p_phl = PreopPatient(age=60, op_subtype='phlebectomy', pathway='venous')
        p_evl = PreopPatient(age=60, op_subtype='evla', pathway='venous')
        p_opn = PreopPatient(age=60, op_subtype='open', pathway='aaa_infra')

        ms_phl_doac = pm.build_med_schedule(p_phl, {'med_doac': True}, {'anesthesia_plan': ''}, egfr=70, crcl=70)
        self.assertIn('72', ms_phl_doac[0]['rule'])
        ms_evl_war_auto = pm.build_med_schedule(p_evl, {'med_warfarin': True}, {'anesthesia_plan': 'auto'}, egfr=70, crcl=70)
        ms_evl_war_nx = pm.build_med_schedule(p_evl, {'med_warfarin': True}, {'anesthesia_plan': 'neuraxial'}, egfr=70, crcl=70)
        self.assertNotEqual(ms_evl_war_auto[0]['rule'], ms_evl_war_nx[0]['rule'])

        p_cs = PreopPatient(age=65, op_subtype='cea_symp', pathway='cea')
        ms_cea_s = pm.build_med_schedule(p_cs, {'med_clopidogrel': True, 'cea_days_since_event': 12}, {}, egfr=70)
        ms_cea_a = pm.build_med_schedule(PreopPatient(age=65, op_subtype='cea_asymp', pathway='cea'), {'med_clopidogrel': True}, {}, egfr=70)
        self.assertEqual(ms_cea_s[0]['tone'], 'ok')
        self.assertEqual(ms_cea_a[0]['tone'], 'amber')
        self.assertEqual(pm.build_med_schedule(p_cs, {'med_clopidogrel': True, 'cea_days_since_event': 200}, {}, egfr=70)[0]['tone'], 'amber')

        ms_75 = pm.build_med_schedule(PreopPatient(age=75, op_subtype='open', pathway='aaa_infra'), {'med_doac': True}, {}, egfr=55, crcl=None)
        ms_74 = pm.build_med_schedule(PreopPatient(age=74, op_subtype='open', pathway='aaa_infra'), {'med_doac': True}, {}, egfr=55, crcl=None)
        self.assertIn('75 წ.', ms_75[0]['rule'])
        self.assertNotIn('75 წ.', ms_74[0]['rule'])

        ms_cil_25 = pm.build_med_schedule(p_opn, {'med_chips': ['cilostazol']}, {}, egfr=25, crcl=25)
        ms_cil_26 = pm.build_med_schedule(p_opn, {'med_chips': ['cilostazol']}, {}, egfr=26, crcl=26)
        self.assertEqual(ms_cil_25[0]['tone'], 'red')
        self.assertEqual(ms_cil_26[0]['tone'], 'amber')

        ms_2oac = pm.build_med_schedule(p_opn, {'med_doac': True, 'med_warfarin': True}, {}, egfr=70, crcl=70)
        ms_1oac = pm.build_med_schedule(p_opn, {'med_doac': True}, {}, egfr=70, crcl=70)
        self.assertEqual(ms_2oac[0]['tone'], 'red')
        self.assertEqual(ms_1oac[0]['tone'], 'amber')

        ms_eq_crcl = pm.build_med_schedule(p_opn, {'med_doac': True}, {}, egfr=55, crcl=55)
        self.assertIn('CrCl 55', ms_eq_crcl[0]['rule'])
        ms_doac_29 = pm.build_med_schedule(p_opn, {'med_doac': True}, {}, egfr=29, crcl=29)
        ms_doac_30 = pm.build_med_schedule(p_opn, {'med_doac': True}, {}, egfr=30, crcl=30)
        self.assertNotEqual(ms_doac_29[0]['rule'], ms_doac_30[0]['rule'])
        self.assertEqual(pm.build_med_schedule(p_opn, {'med_doac': True}, {'planned_surgery_date': '2026-12-10'}, egfr=29, crcl=29)[0]['tone'], 'red')
        self.assertEqual(pm.build_med_schedule(p_opn, {'med_doac': True}, {'planned_surgery_date': '2026-12-10'}, egfr=30, crcl=30)[0]['tone'], 'amber')

        dab_rules = [pm.build_med_schedule(p_opn, {'med_dabigatran': True}, {}, egfr=v, crcl=v)[0]['rule'] for v in (29, 30, 50, 80)]
        self.assertEqual(len(set(dab_rules)), 4)
        dab_nx = [(r['tone'], r['date_plan']) for v in (29, 30, 50, 80) for r in pm.build_med_schedule(p_opn, {'med_dabigatran': True}, {'anesthesia_plan': 'neuraxial', 'planned_surgery_date': '2026-12-10'}, egfr=v, crcl=v)]
        dab_gn = [(r['tone'], r['date_plan']) for v in (29, 30, 50, 80) for r in pm.build_med_schedule(p_opn, {'med_dabigatran': True}, {'anesthesia_plan': 'general', 'planned_surgery_date': '2026-12-10'}, egfr=v, crcl=v)]
        self.assertEqual(len(set(dab_nx)), 4)
        self.assertEqual(len(set(dab_gn)), 4)

        ms_riv_22 = pm.build_med_schedule(p_opn, {'med_chips': ['rivaroxaban_2_5']}, {}, egfr=22, crcl=22)
        ms_riv_40 = pm.build_med_schedule(p_opn, {'med_chips': ['rivaroxaban_2_5']}, {}, egfr=40, crcl=40)
        self.assertEqual(ms_riv_22[0]['tone'], 'red')
        self.assertEqual(ms_riv_40[0]['tone'], 'amber')

        ms_war_22 = pm.build_med_schedule(p_opn, {'med_warfarin': True, 'cad_chips': ['mech_valve']}, {}, egfr=22, crcl=22)
        ms_war_45 = pm.build_med_schedule(p_opn, {'med_warfarin': True, 'cad_chips': ['mech_valve']}, {}, egfr=45, crcl=45)
        self.assertNotEqual(ms_war_22[0]['rule'], ms_war_45[0]['rule'])

        ms_clop_opn = pm.build_med_schedule(p_opn, {'med_clopidogrel': True}, {}, egfr=70)
        ms_clop_phl_clean = pm.build_med_schedule(p_phl, {'med_clopidogrel': True}, {}, egfr=70)
        ms_clop_phl_cad = pm.build_med_schedule(p_phl, {'med_clopidogrel': True, 'cad': True}, {}, egfr=70)
        self.assertIn('ასპირინი', ms_clop_opn[0]['rule'])
        self.assertNotIn('ასპირინი', ms_clop_phl_clean[0]['rule'])
        self.assertIn('ასპირინი', ms_clop_phl_cad[0]['rule'])

    def test_sprint6_3_build_med_schedule_cardio_metabolic_and_scores(self):
        p_evl = PreopPatient(age=60, op_subtype='evla', pathway='venous')
        p_opn = PreopPatient(age=60, op_subtype='open', pathway='aaa_infra')
        p_evr = PreopPatient(age=60, op_subtype='evar', pathway='aaa_infra')

        ms_ace_39 = pm.build_med_schedule(p_opn, {'med_chips': ['acei_arb']}, {'lvef': 39}, egfr=70)
        ms_ace_40 = pm.build_med_schedule(p_opn, {'med_chips': ['acei_arb']}, {'lvef': 40}, egfr=70)
        ms_ace_hf = pm.build_med_schedule(p_opn, {'med_chips': ['acei_arb'], 'cad_chips': ['hf']}, {}, egfr=70)
        ms_ace_evl = pm.build_med_schedule(p_evl, {'med_chips': ['acei_arb']}, {'anesthesia_plan': 'local'}, egfr=70)
        self.assertNotEqual(ms_ace_39[0]['rule'], ms_ace_40[0]['rule'])
        self.assertEqual(ms_ace_39[0]['rule'], ms_ace_hf[0]['rule'])
        self.assertEqual(ms_ace_evl[0]['tone'], 'ok')
        self.assertEqual(pm.build_med_schedule(p_opn, {'med_chips': ['acei_arb']}, {}, egfr=70)[0]['tone'], 'amber')
        self.assertEqual(pm.build_med_schedule(PreopPatient(age=65, op_subtype='cea_asymp', pathway='cea'), {'med_chips': ['acei_arb']}, {}, egfr=70)[0]['tone'], 'amber')
        self.assertEqual(pm.build_med_schedule(p_evr, {'med_chips': ['acei_arb']}, {}, egfr=70)[0]['tone'], 'amber')
        self.assertEqual(pm.build_med_schedule(PreopPatient(age=65, op_subtype='pta_claud', pathway='pad_pta'), {'med_chips': ['acei_arb']}, {'anesthesia_plan': 'general'}, egfr=70)[0]['tone'], 'amber')

        ms_met_59 = pm.build_med_schedule(p_evr, {'metformin': True}, {}, egfr=59, crcl=59)
        ms_met_60 = pm.build_med_schedule(p_evr, {'metformin': True}, {}, egfr=60, crcl=60)
        ms_met_opn = pm.build_med_schedule(p_opn, {'metformin': True}, {}, egfr=80, crcl=80)
        self.assertEqual(ms_met_59[0]['tone'], 'amber')
        self.assertEqual(ms_met_60[0]['tone'], 'ok')
        self.assertEqual(ms_met_opn[0]['tone'], 'amber')

        self.assertEqual(pm.build_med_schedule(p_opn, {'med_chips': ['beta_blocker']}, {'bp_sys': 120, 'hr': 54}, egfr=70)[0]['tone'], 'amber')
        self.assertEqual(pm.build_med_schedule(p_opn, {'med_chips': ['beta_blocker']}, {'bp_sys': 120, 'hr': 55}, egfr=70)[0]['tone'], 'ok')
        self.assertEqual(pm.build_med_schedule(p_opn, {'med_chips': ['beta_blocker']}, {}, egfr=70)[0]['tone'], 'ok')
        self.assertEqual(pm.build_med_schedule(p_opn, {'med_chips': ['ccb']}, {'bp_sys': 109}, egfr=70)[0]['tone'], 'amber')
        self.assertEqual(pm.build_med_schedule(p_opn, {'med_chips': ['ccb']}, {'bp_sys': 110}, egfr=70)[0]['tone'], 'ok')
        self.assertEqual(pm.build_med_schedule(p_opn, {'med_chips': ['ccb']}, {}, egfr=70)[0]['tone'], 'ok')

        ms_st_65 = pm.build_med_schedule(PreopPatient(age=65, op_subtype='open', pathway='aaa_infra'), {'med_doac': True, 'med_chips': ['steroid']}, {}, egfr=70)
        ms_st_64 = pm.build_med_schedule(PreopPatient(age=64, op_subtype='open', pathway='aaa_infra'), {'med_doac': True, 'med_chips': ['steroid']}, {}, egfr=70)
        self.assertTrue(any('პანტოპრაზოლი' in r['drug'] or 'PPI' in r['drug'] for r in ms_st_65))
        self.assertFalse(any('პანტოპრაზოლი' in r['drug'] or 'PPI' in r['drug'] for r in ms_st_64))

        # calc_auto_asa, _ihd_and_chf_flags, calc_rcri, calc_vsg_cri
        self.assertEqual(pm.calc_auto_asa(p_evl, {}, {}, today=D0), 'I')
        self.assertEqual(pm.calc_auto_asa(p_opn, {}, {}, today=D0), 'III')
        self.assertEqual(pm.calc_auto_asa(PreopPatient(op_subtype='cea_symp', pathway='cea'), {'cea_days_since_event': 90}, {}, today=D0), 'IV')
        self.assertEqual(pm.calc_auto_asa(PreopPatient(op_subtype='cea_symp', pathway='cea'), {'cea_days_since_event': 91}, {}, today=D0), 'III')
        self.assertEqual(pm.calc_auto_asa(p_evl, {}, {'aaa_mm': 70}, today=D0), 'IV')
        self.assertEqual(pm.calc_auto_asa(p_evl, {}, {'aaa_mm': 69}, today=D0), 'I')
        self.assertEqual(pm.calc_auto_asa(p_evl, {}, {'lvef': 39}, today=D0), 'IV')
        self.assertEqual(pm.calc_auto_asa(p_evl, {}, {'lvef': 40}, today=D0), 'I')
        self.assertEqual(pm.calc_auto_asa(p_evl, {}, {'bp_sys': 95, 'hr': 96}, today=D0), 'IV')
        self.assertEqual(pm.calc_auto_asa(p_evl, {}, {'bp_sys': 105, 'hr': 96}, today=D0), 'I')
        self.assertEqual(pm.calc_auto_asa(p_evl, {}, {'hb': 69}, today=D0), 'IV')
        self.assertEqual(pm.calc_auto_asa(p_evl, {}, {'hb': 70}, today=D0), 'III')
        self.assertEqual(pm.calc_auto_asa(p_evl, {}, {'hb': 99}, today=D0), 'III')
        self.assertEqual(pm.calc_auto_asa(p_evl, {}, {'hb': 100}, today=D0), 'I')
        self.assertEqual(pm.calc_auto_asa(p_evl, {}, {'fibrinogen': 1.1}, today=D0), 'IV')
        self.assertEqual(pm.calc_auto_asa(p_evl, {}, {'fibrinogen': 1.2}, today=D0), 'I')
        self.assertEqual(pm.calc_auto_asa(p_evl, {}, {'liver_status': '3x', 'inr': 1.6}, today=D0), 'IV')
        self.assertEqual(pm.calc_auto_asa(p_evl, {}, {'liver_status': '3x', 'inr': 1.5}, today=D0), 'III')
        self.assertEqual(pm.calc_auto_asa(p_evl, {}, {'hs_ctn': 52}, today=D0), 'IV')
        self.assertEqual(pm.calc_auto_asa(p_evl, {}, {'hs_ctn': 51}, today=D0), 'I')
        self.assertEqual(pm.calc_auto_asa(p_evl, {}, {}, bmi=40.0, today=D0), 'III')
        self.assertEqual(pm.calc_auto_asa(p_evl, {}, {}, bmi=39.9, today=D0), 'II')
        self.assertEqual(pm.calc_auto_asa(p_evl, {}, {'k': 6.0}, today=D0), 'IV')
        self.assertEqual(pm.calc_auto_asa(p_evl, {}, {'k': 5.9}, today=D0), 'III')
        self.assertEqual(pm.calc_auto_asa(p_evl, {}, {'k': 5.4}, today=D0), 'III')
        self.assertEqual(pm.calc_auto_asa(p_evl, {}, {'k': 5.3}, today=D0), 'I')
        self.assertEqual(pm.calc_auto_asa(p_evl, {}, {'k': 3.2}, today=D0), 'III')

        self.assertEqual(pm._ihd_and_chf_flags({}, {'ecg': ''}), (False, False))
        self.assertEqual(pm._ihd_and_chf_flags({}, {'lvef': 39}), (False, True))
        self.assertEqual(pm._ihd_and_chf_flags({}, {'lvef': 40}), (False, False))

        p_ca = PreopPatient(op_subtype='cea_asymp', pathway='cea')
        p_cs = PreopPatient(op_subtype='cea_symp', pathway='cea')
        self.assertEqual(pm.calc_rcri(p_ca, {'filled': True, 'neuro': True}, {})['score'], 1)
        self.assertEqual(pm.calc_rcri(p_ca, {'filled': True, 'cea_symp_event': True}, {})['score'], 1)
        self.assertEqual(pm.calc_rcri(p_cs, {'filled': True}, {})['score'], 1)
        self.assertEqual(pm.calc_rcri(p_ca, {'filled': True}, {'creat': 2.0, 'creat_unit': 'mgdl'})['score'], 0)
        self.assertEqual(pm.calc_rcri(p_ca, {'filled': True}, {'creat': 2.05, 'creat_unit': 'mgdl'})['score'], 1)
        self.assertEqual(pm.calc_rcri(p_ca, {'filled': True}, {'creat': 220})['score'], 1)

        self.assertIsNone(pm.calc_vsg_cri(p_evl, {'filled': True}, {}))
        v_smk_f = pm.calc_vsg_cri(PreopPatient(age=55, op_subtype='cea_asymp', pathway='cea'), {'filled': False, 'smoker': True}, {})
        v_smk_t = pm.calc_vsg_cri(PreopPatient(age=55, op_subtype='cea_asymp', pathway='cea'), {'filled': True, 'smoker': True}, {})
        self.assertLess(v_smk_f['score'], v_smk_t['score'])
        v_cabg_ok = pm.calc_vsg_cri(PreopPatient(age=55, op_subtype='cea_asymp', pathway='cea'), {'filled': True, 'cad_chips': ['cabg']}, {})
        v_cabg_st = pm.calc_vsg_cri(PreopPatient(age=55, op_subtype='cea_asymp', pathway='cea'), {'filled': True, 'cad_chips': ['cabg'], 'recent_stent': True}, {})
        self.assertNotEqual(v_cabg_ok['score'], v_cabg_st['score'])
        v_69 = pm.calc_vsg_cri(PreopPatient(age=69, op_subtype='cea_asymp', pathway='cea'), {'filled': True}, {})
        v_70 = pm.calc_vsg_cri(PreopPatient(age=70, op_subtype='cea_asymp', pathway='cea'), {'filled': True}, {})
        self.assertGreater(v_70['score'], v_69['score'])
        v_fev69 = pm.calc_vsg_cri(PreopPatient(age=55, op_subtype='cea_asymp', pathway='cea'), {'filled': True}, {'spiro_fev1': 69})
        v_fev70 = pm.calc_vsg_cri(PreopPatient(age=55, op_subtype='cea_asymp', pathway='cea'), {'filled': True}, {'spiro_fev1': 70})
        self.assertGreater(v_fev69['score'], v_fev70['score'])

    def test_sprint6_4_evaluate_general_thresholds_and_abx(self):
        ev_def_today = pm.evaluate(PreopPatient(age=60, sex='M', op_subtype='open', pathway='aaa_infra', questionnaire={'filled': True, 'other_meds': 'როზუვასტატინი 20 მგ'}, studies={'aaa_mm': 58}))
        self.assertFalse(any('სტატინ' in rf and 'არ იღებს' in rf for rf in ev_def_today['amber_flags']))

        ev_spo89 = pm.evaluate(PreopPatient(age=60, sex='M', op_subtype='evla', pathway='venous', side='R', questionnaire={'filled': True}, studies={'spo2': 89}), today=D0)
        ev_spo90 = pm.evaluate(PreopPatient(age=60, sex='M', op_subtype='evla', pathway='venous', side='R', questionnaire={'filled': True}, studies={'spo2': 90}), today=D0)
        self.assertGreater(len(ev_spo89['red_flags']), len(ev_spo90['red_flags']))

        ev_nx145 = pm.evaluate(PreopPatient(age=60, sex='M', op_subtype='phlebectomy', pathway='venous', side='R', questionnaire={'filled': True}, studies={'anesthesia_plan': 'neuraxial', 'inr': 1.45}), today=D0)
        ev_nx135 = pm.evaluate(PreopPatient(age=60, sex='M', op_subtype='phlebectomy', pathway='venous', side='R', questionnaire={'filled': True}, studies={'anesthesia_plan': 'neuraxial', 'inr': 1.35}), today=D0)
        self.assertTrue(ev_nx145['neuraxial_hard_blocked'])
        self.assertFalse(ev_nx135['neuraxial_hard_blocked'])

        ev_stale14 = pm.evaluate(PreopPatient(age=60, sex='M', op_subtype='open', pathway='aaa_infra', started_on=D0, questionnaire={'filled': True}, studies={'labs_date': (D0 - datetime.timedelta(days=14)).isoformat()}), today=D0)
        ev_stale15 = pm.evaluate(PreopPatient(age=60, sex='M', op_subtype='open', pathway='aaa_infra', started_on=D0, questionnaire={'filled': True}, studies={'labs_date': (D0 - datetime.timedelta(days=15)).isoformat()}), today=D0)
        self.assertFalse(ev_stale14['needs_stale_labs'])
        self.assertTrue(ev_stale15['needs_stale_labs'])

        ev_ot_eq = pm.evaluate(PreopPatient(age=60, sex='M', op_subtype='open', pathway='aaa_infra', started_on=D0 - datetime.timedelta(days=6), questionnaire={'filled': True}), today=D0)
        ev_ot_gt = pm.evaluate(PreopPatient(age=60, sex='M', op_subtype='open', pathway='aaa_infra', started_on=D0 - datetime.timedelta(days=7), questionnaire={'filled': True}), today=D0)
        self.assertFalse(ev_ot_eq['over_target_days'])
        self.assertTrue(ev_ot_gt['over_target_days'])

        ev_del74 = pm.evaluate(PreopPatient(age=74, sex='M', op_subtype='open', pathway='aaa_infra', questionnaire={'filled': True}), today=D0)
        ev_del75 = pm.evaluate(PreopPatient(age=75, sex='M', op_subtype='open', pathway='aaa_infra', questionnaire={'filled': True}), today=D0)
        self.assertNotEqual(ev_del74['anesthetic_brief'], ev_del75['anesthetic_brief'])

        ev_wt119 = pm.evaluate(PreopPatient(age=60, sex='M', op_subtype='open', pathway='aaa_infra', questionnaire={'filled': True}, studies={'weight_kg': 119, 'height_cm': 175}), today=D0)
        ev_wt120 = pm.evaluate(PreopPatient(age=60, sex='M', op_subtype='open', pathway='aaa_infra', questionnaire={'filled': True}, studies={'weight_kg': 120, 'height_cm': 175}), today=D0)
        self.assertIn('2 გ', ev_wt119['surgical_brief']['abx_regimen'])
        self.assertIn('3 გ', ev_wt120['surgical_brief']['abx_regimen'])
        self.assertIn('3 გ', next(c['label'] for c in ev_wt120['dos_checks'] if c['key'] == 'dos_abx_ready'))
        self.assertIn('2 გ', next(c['label'] for c in ev_wt119['dos_checks'] if c['key'] == 'dos_abx_ready'))

        ev_pta_clean = pm.evaluate(PreopPatient(age=60, sex='M', op_subtype='pta_claud', pathway='pad_pta', side='R', questionnaire={'filled': True}), today=D0)
        ev_pta_wet = pm.evaluate(PreopPatient(age=60, sex='M', op_subtype='pta_claud', pathway='pad_pta', side='R', questionnaire={'filled': True, 'abf_wet_infection': True}), today=D0)
        self.assertNotEqual(ev_pta_clean['surgical_brief']['abx_regimen'], ev_pta_wet['surgical_brief']['abx_regimen'])

        ev_na149 = pm.evaluate(PreopPatient(age=60, sex='M', op_subtype='open', pathway='aaa_infra', questionnaire={'filled': True}, studies={'na': 149}), today=D0)
        ev_na150 = pm.evaluate(PreopPatient(age=60, sex='M', op_subtype='open', pathway='aaa_infra', questionnaire={'filled': True}, studies={'na': 150}), today=D0)
        self.assertLess(len(ev_na149['red_flags']), len(ev_na150['red_flags']))
        self.assertIn('ჰიპერნატრიემია', pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='evar', pathway='aaa_infra', questionnaire={'filled': True}, studies={'creat': 140, 'creat_unit': 'umol', 'na': 150}), today=D0)['surgical_brief']['contrast_plan'])
        self.assertNotIn('ჰიპერნატრიემია', pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='evar', pathway='aaa_infra', questionnaire={'filled': True}, studies={'creat': 140, 'creat_unit': 'umol', 'na': 149}), today=D0)['surgical_brief']['contrast_plan'])

        ev_fib17 = pm.evaluate(PreopPatient(age=60, sex='M', op_subtype='open', pathway='aaa_infra', questionnaire={'filled': True}, studies={'fibrinogen': 1.7}), today=D0)
        ev_fib18 = pm.evaluate(PreopPatient(age=60, sex='M', op_subtype='open', pathway='aaa_infra', questionnaire={'filled': True}, studies={'fibrinogen': 1.8}), today=D0)
        self.assertNotEqual(ev_fib17['anesthetic_brief']['transfusion_pbm'], ev_fib18['anesthetic_brief']['transfusion_pbm'])
        self.assertIn('1.7', ev_fib17['anesthetic_brief']['transfusion_pbm'])
        self.assertNotIn('1.8 გ/ლ', ev_fib18['anesthetic_brief']['transfusion_pbm'])

        ev_abi039 = pm.evaluate(PreopPatient(age=60, sex='M', op_subtype='bypass_claud', pathway='pad_bypass', side='R', questionnaire={'filled': True}, studies={'abi_r': 0.39, 'abi_l': 0.9}), today=D0)
        ev_abi040 = pm.evaluate(PreopPatient(age=60, sex='M', op_subtype='bypass_claud', pathway='pad_bypass', side='R', questionnaire={'filled': True}, studies={'abi_r': 0.40, 'abi_l': 0.9}), today=D0)
        self.assertTrue(ev_abi039['key_badge_urgent'])
        self.assertFalse(ev_abi040['key_badge_urgent'])

        ev_aaa69 = pm.evaluate(PreopPatient(age=60, sex='M', op_subtype='open', pathway='aaa_infra', questionnaire={'filled': True}, studies={'aaa_mm': 69}), today=D0)
        ev_aaa70 = pm.evaluate(PreopPatient(age=60, sex='M', op_subtype='open', pathway='aaa_infra', questionnaire={'filled': True}, studies={'aaa_mm': 70}), today=D0)
        self.assertFalse(ev_aaa69['key_badge_urgent'])
        self.assertTrue(ev_aaa70['key_badge_urgent'])

        ev_inc_f50 = pm.evaluate(PreopPatient(age=65, sex='F', op_subtype='cea_asymp', pathway='cea', side='R', questionnaire={'filled': True}, studies={'aaa_mm': 50}), today=D0)
        ev_inc_m50 = pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='cea_asymp', pathway='cea', side='R', questionnaire={'filled': True}, studies={'aaa_mm': 50}), today=D0)
        self.assertNotEqual(ev_inc_f50['red_flags'], ev_inc_m50['red_flags'])
        self.assertGreater(len(ev_inc_f50['red_flags']), len(ev_inc_m50['red_flags']))

        ev_hb_f = pm.evaluate(PreopPatient(age=60, sex='F', op_subtype='open', pathway='aaa_infra', questionnaire={'filled': True}, studies={'hb': 170}), today=D0)
        ev_hb_m = pm.evaluate(PreopPatient(age=60, sex='M', op_subtype='open', pathway='aaa_infra', questionnaire={'filled': True}, studies={'hb': 170}), today=D0)
        self.assertGreater(len(ev_hb_f['amber_flags']), len(ev_hb_m['amber_flags']))

    def test_sprint6_5_statin_aaa_subtypes_liver_and_antithrombotics(self):
        # Statin & LDL boundaries
        ev_st_chip = pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='bypass_claud', pathway='pad_bypass', side='R', questionnaire={'filled': True, 'med_chips': ['statin']}), today=D0)
        ev_no_st = pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='bypass_claud', pathway='pad_bypass', side='R', questionnaire={'filled': True}), today=D0)
        self.assertLess(len(ev_st_chip['amber_flags']), len(ev_no_st['amber_flags']))
        ev_ldl13 = pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='pta_claud', pathway='pad_pta', side='R', questionnaire={'filled': True, 'med_chips': ['statin']}, studies={'ldl': 1.3}), today=D0)
        ev_ldl14 = pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='pta_claud', pathway='pad_pta', side='R', questionnaire={'filled': True, 'med_chips': ['statin']}, studies={'ldl': 1.4}), today=D0)
        self.assertLess(len(ev_ldl13['amber_flags']), len(ev_ldl14['amber_flags']))

        # Modality, AAA subtypes, neck/iliac & rupture vs elective tasks
        ev_phl_auto = pm.evaluate(PreopPatient(age=55, sex='F', op_subtype='phlebectomy', pathway='venous', side='R', questionnaire={'filled': True}), today=D0)
        ev_op_auto = pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='open', pathway='aaa_infra', questionnaire={'filled': True}), today=D0)
        self.assertTrue(ev_phl_auto['has_neuraxial_plan'])
        self.assertFalse(ev_op_auto['has_neuraxial_plan'])
        self.assertTrue(len(ev_op_auto['anesthetic_brief']['lines_and_cied']) > 5)
        self.assertIn('ASRA', pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='evar', pathway='aaa_infra', questionnaire={'filled': True}), today=D0)['anesthetic_brief']['neuraxial_gate'])
        self.assertNotIn('ASRA', ev_op_auto['anesthetic_brief']['neuraxial_gate'])

        ev_tbd = pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='tbd', pathway='aaa_infra', questionnaire={'filled': True}, studies={'aaa_mm': 58, 'aaa_neck_status': 'infrarenal', 'aaa_iliac_access': 'normal'}), today=D0)
        ev_evar = pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='evar', pathway='aaa_infra', questionnaire={'filled': True}, studies={'aaa_mm': 58, 'aaa_neck_status': 'pending', 'aaa_iliac_access': 'normal'}), today=D0)
        self.assertIn('ზუსტდება CTA', ev_tbd['effective_anesth_label'])
        self.assertIn('ზუსტდება CTA', ev_tbd['surgical_brief']['anatomy_summary'])
        self.assertIn('SBP 90–110', ev_evar['anesthetic_brief']['bp_corridor'])
        self.assertNotEqual(ev_tbd['surgical_brief']['anatomy_summary'], ev_evar['surgical_brief']['anatomy_summary'])

        ev_nk_p = pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='evar', pathway='aaa_infra', questionnaire={'filled': True}, studies={'aaa_mm': 58, 'aaa_neck_status': 'pending', 'aaa_iliac_access': 'normal', 'cta_disc': True}), today=D0)
        ev_il_p = pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='evar', pathway='aaa_infra', questionnaire={'filled': True}, studies={'aaa_mm': 58, 'aaa_neck_status': 'infrarenal', 'aaa_iliac_access': 'pending', 'cta_disc': True}), today=D0)
        ev_ev_ok = pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='evar', pathway='aaa_infra', questionnaire={'filled': True}, studies={'aaa_mm': 58, 'aaa_neck_status': 'infrarenal', 'aaa_iliac_access': 'normal', 'cta_disc': True}), today=D0)
        self.assertGreater(len(ev_nk_p['amber_flags']), len(ev_ev_ok['amber_flags']))
        self.assertGreater(len(ev_il_p['amber_flags']), len(ev_ev_ok['amber_flags']))
        self.assertNotIn('პროქს. ყელი: —', pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='evar', pathway='aaa_infra', questionnaire={'filled': True}, studies={'aaa_mm': 58, 'aaa_neck_status': ''}), today=D0)['surgical_brief']['tactical_note'])
        self.assertIn('პროქს. ყელი: —', pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='evar', pathway='aaa_infra', questionnaire={'filled': True}, studies={'aaa_mm': 58, 'aaa_neck_status': 'unknown_val'}), today=D0)['surgical_brief']['tactical_note'])

        ev_pain_t = pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='open', pathway='aaa_infra', questionnaire={'filled': True, 'aaa_pain': True}), today=D0)
        ev_pain_f = pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='open', pathway='aaa_infra', questionnaire={'filled': False, 'aaa_pain': True}), today=D0)
        self.assertGreater(len(ev_pain_t['red_flags']), len(ev_pain_f['red_flags']))

        # Liver, iodine contrast premed, P2Y12 / triple AT / recent stent / mech valve
        ev_liv3 = pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='open', pathway='aaa_infra', questionnaire={'filled': True}, studies={'liver_status': '3x'}), today=D0)
        ev_liv2 = pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='open', pathway='aaa_infra', questionnaire={'filled': True}, studies={'liver_status': '2x'}), today=D0)
        self.assertGreater(len(ev_liv3['red_flags']), len(ev_liv2['red_flags']))
        self.assertTrue(pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='evar', pathway='aaa_infra', questionnaire={'filled': True, 'allergy_iodine': True}), today=D0)['needs_contrast_premed'])
        self.assertFalse(pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='evla', pathway='venous', side='R', questionnaire={'filled': True, 'allergy_iodine': True}), today=D0)['needs_contrast_premed'])
        self.assertFalse(pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='evar', pathway='aaa_infra', questionnaire={'filled': False, 'allergy_iodine': True}), today=D0)['needs_contrast_premed'])
        ev_iod_cea_t = pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='cea_asymp', pathway='cea', side='R', questionnaire={'filled': True, 'allergy_iodine': True}), today=D0)
        ev_iod_cea_f = pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='cea_asymp', pathway='cea', side='R', questionnaire={'filled': False, 'allergy_iodine': True}), today=D0)
        self.assertGreater(len(ev_iod_cea_t['amber_flags']), len(ev_iod_cea_f['amber_flags']))

        ev_2p2 = pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='open', pathway='aaa_infra', questionnaire={'filled': True, 'med_clopidogrel': True, 'med_ticagrelor': True}), today=D0)
        ev_1p2 = pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='open', pathway='aaa_infra', questionnaire={'filled': True, 'med_clopidogrel': True}), today=D0)
        self.assertGreater(len(ev_2p2['red_flags']), len(ev_1p2['red_flags']))
        ev_3at = pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='open', pathway='aaa_infra', questionnaire={'filled': True, 'med_aspirin': True, 'med_clopidogrel': True, 'med_doac': True}), today=D0)
        ev_2at = pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='open', pathway='aaa_infra', questionnaire={'filled': True, 'med_aspirin': True, 'med_clopidogrel': True}), today=D0)
        self.assertGreater(len(ev_3at['red_flags']), len(ev_2at['red_flags']))
        ev_st0 = pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='pta_clti', pathway='pad_pta', side='R', questionnaire={'filled': True, 'recent_stent': True}), today=D0)
        ev_st1 = pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='pta_clti', pathway='pad_pta', side='R', questionnaire={'filled': True, 'recent_stent': True, 'med_clopidogrel': True}), today=D0)
        self.assertNotEqual(ev_st0['red_flags'], ev_st1['red_flags'])
        ev_mv_bad = pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='bypass_claud', pathway='pad_bypass', side='R', questionnaire={'filled': True, 'cad_chips': ['mech_valve'], 'med_warfarin': True}, studies={'post_antithrombotic_plan': 'sapt'}), today=D0)
        ev_mv_ok = pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='bypass_claud', pathway='pad_bypass', side='R', questionnaire={'filled': True, 'cad_chips': ['mech_valve'], 'med_warfarin': True}, studies={'post_antithrombotic_plan': 'oac_sapt'}), today=D0)
        self.assertGreater(len(ev_mv_bad['red_flags']), len(ev_mv_ok['red_flags']))

    def test_sprint6_6_echo_nephro_renal_electrolytes_and_tsh(self):
        self.assertTrue(pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='open', pathway='aaa_infra', questionnaire={'filled': True}), today=D0)['needs_echo_or_bnp'])
        self.assertFalse(pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='cea_asymp', pathway='cea', side='R', questionnaire={'filled': True, 'mets_ok': True}), today=D0)['needs_echo_or_bnp'])
        self.assertTrue(pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='cea_asymp', pathway='cea', side='R', questionnaire={'filled': True, 'cad': True}), today=D0)['needs_echo_or_bnp'])

        self.assertTrue(pm.evaluate(PreopPatient(age=70, sex='M', op_subtype='open', pathway='aaa_infra', questionnaire={'filled': True}, studies={'creat': 150, 'creat_unit': 'umol'}), today=D0)['needs_nephro_panel'])
        self.assertFalse(pm.evaluate(PreopPatient(age=70, sex='M', op_subtype='open', pathway='aaa_infra', questionnaire={'filled': True}, studies={'creat': 125, 'creat_unit': 'umol'}), today=D0)['needs_nephro_panel'])
        ev_rup_dial = pm.evaluate(PreopPatient(age=70, sex='M', op_subtype='open', pathway='aaa_infra', questionnaire={'filled': True, 'aaa_pain': True, 'on_dialysis': True, 'allergy_chips': ['hit_heparin']}), today=D0)
        ev_rup_ckd = pm.evaluate(PreopPatient(age=70, sex='M', op_subtype='open', pathway='aaa_infra', questionnaire={'filled': True, 'aaa_pain': True}, studies={'creat': 180, 'creat_unit': 'umol', 'urine_nitrite': True}), today=D0)
        self.assertTrue(any(t['key'] == 'nephro' for t in ev_rup_dial['tasks']))
        self.assertFalse(any(t['key'] == 'nephro' for t in ev_rup_ckd['tasks']))
        self.assertFalse(any(t['key'] == 'uti' for t in ev_rup_ckd['tasks']))
        self.assertTrue(any(t['key'] == 'uti' for t in pm.evaluate(PreopPatient(age=70, sex='M', op_subtype='open', pathway='aaa_infra', questionnaire={'filled': True}, studies={'urine_nitrite': True}), today=D0)['tasks']))
        self.assertIn('არგატრობან', ev_rup_dial['surgical_brief']['heparin_plan'])
        self.assertNotEqual(
            ev_rup_dial['surgical_brief']['heparin_plan'],
            pm.evaluate(PreopPatient(age=60, sex='M', op_subtype='open', pathway='aaa_infra', questionnaire={'filled': True, 'allergy_chips': ['hit_heparin']}, studies={'creat': 75, 'creat_unit': 'umol'}), today=D0)['surgical_brief']['heparin_plan'],
        )

        ev_doac_r22 = pm.evaluate(PreopPatient(age=70, sex='M', op_subtype='evar', pathway='aaa_infra', questionnaire={'filled': True, 'med_doac': True}, studies={'creat': 240, 'creat_unit': 'umol'}), today=D0)
        ev_doac_r70 = pm.evaluate(PreopPatient(age=70, sex='M', op_subtype='evar', pathway='aaa_infra', questionnaire={'filled': True, 'med_doac': True}, studies={'creat': 85, 'creat_unit': 'umol'}), today=D0)
        self.assertGreater(len(ev_doac_r22['red_flags']), len(ev_doac_r70['red_flags']))
        self.assertTrue(ev_doac_r22['needs_hydration'])
        self.assertFalse(ev_doac_r70['needs_hydration'])
        self.assertIn('CO2', ev_doac_r22['surgical_brief']['contrast_plan'])
        self.assertTrue(pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='evar', pathway='aaa_infra', questionnaire={'filled': True, 'metformin': True}, studies={'creat': 125, 'creat_unit': 'umol'}), today=D0)['needs_med_hold'])

        self.assertTrue(pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='phlebectomy', pathway='venous', side='R', questionnaire={'filled': True, 'med_doac': True, 'on_dialysis': True}, studies={'anesthesia_plan': 'neuraxial'}), today=D0)['neuraxial_hard_blocked'])
        self.assertTrue(pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='phlebectomy', pathway='venous', side='R', questionnaire={'filled': True, 'med_dabigatran': True}, studies={'anesthesia_plan': 'neuraxial', 'creat': 240, 'creat_unit': 'umol'}), today=D0)['neuraxial_hard_blocked'])
        self.assertFalse(pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='phlebectomy', pathway='venous', side='R', questionnaire={'filled': True, 'med_doac': True}, studies={'anesthesia_plan': 'neuraxial', 'creat': 150, 'creat_unit': 'umol'}), today=D0)['neuraxial_hard_blocked'])

        cr_45 = next(cr for cr in range(110, 190) if pm.calc_egfr(cr, 65, 'M', 'umol') == 45)
        cr_25 = next(cr for cr in range(190, 320) if pm.calc_egfr(cr, 65, 'M', 'umol') == 25)
        cr_26 = next(cr for cr in range(190, 320) if pm.calc_egfr(cr, 65, 'M', 'umol') == 26)
        self.assertFalse(pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='open', pathway='aaa_infra', questionnaire={'filled': True}, studies={'creat': cr_45, 'creat_unit': 'umol'}), today=D0)['needs_nephro_panel'])
        ev_r25 = pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='open', pathway='aaa_infra', questionnaire={'filled': True, 'med_chips': ['cilostazol']}, studies={'creat': cr_25, 'creat_unit': 'umol'}), today=D0)
        ev_r26 = pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='open', pathway='aaa_infra', questionnaire={'filled': True, 'med_chips': ['cilostazol']}, studies={'creat': cr_26, 'creat_unit': 'umol'}), today=D0)
        self.assertIn('ცილოსტაზოლი', ' '.join(ev_r25['red_flags']))
        self.assertNotIn('ცილოსტაზოლი', ' '.join(ev_r26['red_flags']))

        for q_k, s_k in (
            ({'filled': True, 'on_dialysis': True}, {'hb': 130}),
            ({'filled': True}, {'hb': 130, 'creat': 140, 'creat_unit': 'umol'}),
            ({'filled': True, 'med_chips': ['acei_arb']}, {'hb': 130, 'creat': 75, 'creat_unit': 'umol'}),
            ({'filled': True, 'med_chips': ['diuretic']}, {'hb': 130, 'creat': 75, 'creat_unit': 'umol'}),
        ):
            ev_k = pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='open', pathway='aaa_infra', questionnaire=q_k, studies=s_k), today=D0)
            self.assertTrue(any('კალიუმ' in a or 'K+' in a or 'K⁺' in a for a in ev_k['amber_flags'] + ev_k['red_flags']) or any(m['field'] == 'k' for m in ev_k['missing_items']))

        ev_bmi50 = pm.evaluate(PreopPatient(age=55, sex='M', op_subtype='open', pathway='aaa_infra', questionnaire={'filled': True, 'med_doac': True}, studies={'weight_kg': 128, 'height_cm': 160}), today=D0)
        ev_bmi51 = pm.evaluate(PreopPatient(age=55, sex='M', op_subtype='open', pathway='aaa_infra', questionnaire={'filled': True, 'med_doac': True}, studies={'weight_kg': 131, 'height_cm': 160}), today=D0)
        self.assertNotEqual(ev_bmi50['amber_flags'], ev_bmi51['amber_flags'])
        self.assertFalse(any('ISTH' in a for a in ev_bmi50['amber_flags']))
        self.assertTrue(any('ISTH' in a for a in ev_bmi51['amber_flags']))

        ev_tsh_p = pm.evaluate(PreopPatient(age=60, sex='M', op_subtype='evar', pathway='aaa_infra', questionnaire={'filled': True, 'thyroid_disease': True}, studies={'tsh_status': 'pending'}), today=D0)
        ev_tsh_ok = pm.evaluate(PreopPatient(age=60, sex='M', op_subtype='evar', pathway='aaa_infra', questionnaire={'filled': True, 'thyroid_disease': True}, studies={'tsh_status': 'euthyroid'}), today=D0)
        self.assertFalse(next(t for t in ev_tsh_p['tasks'] if t['key'] == 'tsh')['done'])
        self.assertTrue(next(t for t in ev_tsh_ok['tasks'] if t['key'] == 'tsh')['done'])
        self.assertFalse(next(r for r in ev_tsh_p['reflex_orders'] if 'TSH' in r['action'])['done'])
        self.assertTrue(next(r for r in ev_tsh_ok['reflex_orders'] if 'TSH' in r['action'])['done'])

    def test_sprint6_7_airway_stop_bang_frailty_cea_and_bp(self):
        self.assertTrue(pm.evaluate(PreopPatient(age=60, sex='M', op_subtype='open', pathway='aaa_infra', questionnaire={'filled': True}, studies={'airway_mallampati': 'IV_difficult'}), today=D0)['difficult_airway_alert'])
        self.assertTrue(pm.evaluate(PreopPatient(age=60, sex='M', op_subtype='cea_asymp', pathway='cea', side='R', questionnaire={'filled': True, 'cea_neck_limited': True}, studies={'airway_mallampati': 'III'}), today=D0)['difficult_airway_alert'])
        self.assertFalse(pm.evaluate(PreopPatient(age=60, sex='M', op_subtype='cea_asymp', pathway='cea', side='R', questionnaire={'filled': True, 'cea_neck_limited': False}, studies={'airway_mallampati': 'III'}), today=D0)['difficult_airway_alert'])
        self.assertIn('მორყეული', pm.evaluate(PreopPatient(age=60, sex='M', op_subtype='open', pathway='aaa_infra', questionnaire={'filled': True, 'loose_teeth': True}), today=D0)['anesthetic_brief']['airway_summary'])
        self.assertNotIn('მორყეული', pm.evaluate(PreopPatient(age=60, sex='M', op_subtype='open', pathway='aaa_infra', questionnaire={'filled': False, 'loose_teeth': True}), today=D0)['anesthetic_brief']['airway_summary'])

        ev_sb_low = pm.evaluate(PreopPatient(age=55, sex='F', op_subtype='evla', pathway='venous', side='R', questionnaire={'filled': True, 'pulm_chips': ['snoring']}, studies={'weight_kg': 107, 'height_cm': 175, 'bp_sys': 125, 'bp_dia': 78}), today=D0)
        ev_sb_hi = pm.evaluate(PreopPatient(age=55, sex='F', op_subtype='evla', pathway='venous', side='R', questionnaire={'filled': True, 'pulm_chips': ['snoring']}, studies={'weight_kg': 109, 'height_cm': 175, 'bp_sys': 142, 'bp_dia': 78}), today=D0)
        self.assertNotEqual(ev_sb_low['effective_stop_bang'], ev_sb_hi['effective_stop_bang'])
        self.assertEqual(pm.evaluate(PreopPatient(age=55, sex='M', op_subtype='evla', pathway='venous', side='R', questionnaire={'filled': True}, studies={'weight_kg': 140, 'height_cm': 200}), today=D0)['effective_stop_bang'], 'low')
        self.assertEqual(pm.evaluate(PreopPatient(age=55, sex='M', op_subtype='evla', pathway='venous', side='R', questionnaire={'filled': True}, studies={'weight_kg': 142, 'height_cm': 200}), today=D0)['effective_stop_bang'], 'intermediate')

        self.assertTrue(pm.evaluate(PreopPatient(age=60, sex='M', op_subtype='evla', pathway='venous', side='R', questionnaire={'filled': True}, studies={'asa_class': 'IV'}), today=D0)['needs_icu_bed'])
        self.assertTrue(pm.evaluate(PreopPatient(age=60, sex='M', op_subtype='evla', pathway='venous', side='R', questionnaire={'filled': True}, studies={'lvef': 39}), today=D0)['needs_icu_bed'])
        self.assertFalse(pm.evaluate(PreopPatient(age=60, sex='M', op_subtype='evla', pathway='venous', side='R', questionnaire={'filled': True}, studies={'lvef': 40}), today=D0)['needs_icu_bed'])

        ev_cfs5 = pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='abf_clti', pathway='abf', questionnaire={'filled': True, 'cfs_level': '5_plus'}), today=D0)
        ev_cfs3 = pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='abf_clti', pathway='abf', questionnaire={'filled': True, 'cfs_level': '3'}), today=D0)
        self.assertGreater(len(ev_cfs5['frailty_notes']), len(ev_cfs3['frailty_notes']))
        self.assertGreater(len(ev_cfs5['amber_flags']), len(ev_cfs3['amber_flags']))
        self.assertEqual(pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='open', pathway='aaa_infra', questionnaire={'filled': False, 'cfs_level': '5_plus'}), today=D0)['frailty_notes'], [])
        self.assertFalse(any('PLT' in a for a in pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='open', pathway='aaa_infra', questionnaire={'filled': True}), today=D0)['amber_flags']))

        self.assertFalse(pm.evaluate(PreopPatient(age=55, sex='F', op_subtype='phlebectomy', pathway='venous', side='R', questionnaire={'filled': True, 'pulm': True}), today=D0)['needs_spirometry'])
        self.assertTrue(pm.evaluate(PreopPatient(age=55, sex='F', op_subtype='phlebectomy', pathway='venous', side='R', questionnaire={'filled': True, 'pulm': True}, studies={'spo2': 92}), today=D0)['needs_spirometry'])
        self.assertGreater(
            len(pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='open', pathway='aaa_infra', questionnaire={'filled': True}, studies={'hb': 130}), today=D0)['amber_flags']),
            len(pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='evla', pathway='venous', side='R', questionnaire={'filled': True}, studies={'hb': 130}), today=D0)['amber_flags']),
        )
        self.assertNotEqual(
            pm.evaluate(PreopPatient(age=60, sex='M', op_subtype='open', pathway='aaa_infra', questionnaire={'filled': True, 'dm': True}), today=D0)['anesthetic_brief']['glycemic_target'],
            pm.evaluate(PreopPatient(age=60, sex='M', op_subtype='open', pathway='aaa_infra', questionnaire={'filled': True}), today=D0)['anesthetic_brief']['glycemic_target'],
        )
        self.assertNotEqual(
            pm.evaluate(PreopPatient(age=60, sex='M', op_subtype='evla', pathway='venous', side='R', questionnaire={'filled': True}, studies={'anesth_lines_chips': ['art_line']}), today=D0)['anesthetic_brief']['lines_and_cied'],
            pm.evaluate(PreopPatient(age=60, sex='M', op_subtype='evla', pathway='venous', side='R', questionnaire={'filled': True}), today=D0)['anesthetic_brief']['lines_and_cied'],
        )

        # CEA & BP corridors
        ev_cea_s10 = pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='cea_symp', pathway='cea', side='R', questionnaire={'filled': True, 'cea_days_since_event': 10}), today=D0)
        ev_cea_s200 = pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='cea_symp', pathway='cea', side='R', questionnaire={'filled': True, 'cea_days_since_event': 200}), today=D0)
        ev_cea_a_ev = pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='cea_asymp', pathway='cea', side='R', questionnaire={'filled': True, 'cea_symp_event': True, 'cea_days_since_event': 8}), today=D0)
        self.assertTrue(ev_cea_s10['is_symp_cea'])
        self.assertFalse(ev_cea_s200['is_symp_cea'])
        self.assertTrue(ev_cea_a_ev['is_symp_cea'])
        self.assertTrue(pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='cea_asymp', pathway='cea', side='R', questionnaire={'filled': True, 'neuro': True}), today=D0)['needs_brain_ct'])
        self.assertFalse(pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='cea_asymp', pathway='cea', side='R', questionnaire={'filled': True}), today=D0)['needs_brain_ct'])
        self.assertGreater(
            len(pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='cea_asymp', pathway='cea', side='R', questionnaire={'filled': True}, studies={'laryngoscopy_res': 'palsy'}), today=D0)['red_flags']),
            len(pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='cea_asymp', pathway='cea', side='R', questionnaire={'filled': True}, studies={'laryngoscopy_res': 'normal'}), today=D0)['red_flags']),
        )
        self.assertIn('CAS / TCAR', ' '.join(pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='cea_asymp', pathway='cea', side='R', questionnaire={'filled': True}, studies={'laryngoscopy_res': 'palsy'}), today=D0)['amber_flags']))
        self.assertNotIn('CAS / TCAR', ' '.join(pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='cea_asymp', pathway='cea', side='R', questionnaire={'filled': True}, studies={'laryngoscopy_res': 'normal'}), today=D0)['amber_flags']))
        self.assertIn('Shunt', pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='cea_asymp', pathway='cea', side='R', questionnaire={'filled': True}, studies={'carotid_r': 80, 'carotid_l': 75}), today=D0)['surgical_brief']['tactical_note'])
        self.assertNotIn('Shunt', pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='cea_asymp', pathway='cea', side='R', questionnaire={'filled': True}, studies={'carotid_r': 80, 'carotid_l': 20}), today=D0)['surgical_brief']['tactical_note'])
        self.assertGreater(
            len(pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='open', pathway='aaa_infra', questionnaire={'filled': True}, studies={'carotid_r': 55, 'carotid_symp': True}), today=D0)['red_flags']),
            len(pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='open', pathway='aaa_infra', questionnaire={'filled': True}, studies={'carotid_r': 55, 'carotid_symp': False}), today=D0)['red_flags']),
        )
        self.assertTrue(any('14-დღიანი' in a for a in pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='cea_asymp', pathway='cea', side='R', questionnaire={'filled': True, 'cea_symp_event': True}), today=D0)['amber_flags']))
        self.assertFalse(any('14-დღიანი' in a for a in pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='cea_asymp', pathway='cea', side='R', questionnaire={'filled': False, 'cea_symp_event': True}), today=D0)['amber_flags']))
        self.assertFalse(any('14-დღიანი' in a for a in pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='cea_symp', pathway='cea', side='R', questionnaire={'filled': True, 'cea_symp_event': True}), today=D0)['amber_flags']))
        self.assertEqual(pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='cea_symp', pathway='cea', side='R', questionnaire={'filled': True, 'cea_days_since_event': 8}), today=D0)['key_badge'], '8/14დღ')
        self.assertEqual(pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='cea_asymp', pathway='cea', side='R', questionnaire={'filled': True, 'cea_days_since_event': 8}), today=D0)['key_badge'], '')
        self.assertIsNotNone(pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='cea_asymp', pathway='cea', side='', questionnaire={'filled': True}, studies={'carotid_r': 80}), today=D0))

        self.assertIn('185/85', ' '.join(pm.evaluate(PreopPatient(age=60, sex='M', op_subtype='open', pathway='aaa_infra', questionnaire={'filled': True}, studies={'bp_sys': 185, 'bp_dia': 85}), today=D0)['red_flags']))
        self.assertIn('150/112', ' '.join(pm.evaluate(PreopPatient(age=60, sex='M', op_subtype='open', pathway='aaa_infra', questionnaire={'filled': True}, studies={'bp_sys': 150, 'bp_dia': 112}), today=D0)['red_flags']))
        self.assertIn('165/85', ' '.join(pm.evaluate(PreopPatient(age=60, sex='M', op_subtype='open', pathway='aaa_infra', questionnaire={'filled': True}, studies={'bp_sys': 165, 'bp_dia': 85}), today=D0)['amber_flags']))
        self.assertIn('135/102', ' '.join(pm.evaluate(PreopPatient(age=60, sex='M', op_subtype='open', pathway='aaa_infra', questionnaire={'filled': True}, studies={'bp_sys': 135, 'bp_dia': 102}), today=D0)['amber_flags']))
        self.assertIn('რუპტურა', ' '.join(pm.evaluate(PreopPatient(age=60, sex='M', op_subtype='open', pathway='aaa_infra', questionnaire={'filled': True}, studies={'bp_sys': 82, 'bp_dia': 50}), today=D0)['red_flags']))
        self.assertNotIn('რუპტურა', ' '.join(pm.evaluate(PreopPatient(age=60, sex='M', op_subtype='cea_asymp', pathway='cea', side='R', questionnaire={'filled': True}, studies={'bp_sys': 82, 'bp_dia': 50}), today=D0)['red_flags']))

    def test_sprint6_8_pad_abf_venous_and_s1_requirements(self):
        # Overdue per pathway, ABI/TP/GSV/contra-limb & venous flags
        for sub_ov, pw_ov in (('abf_clti', 'abf'), ('bypass_clti', 'pad_bypass'), ('pta_clti', 'pad_pta')):
            sd = 'R' if pw_ov != 'abf' else ''
            ev_ok_d = pm.evaluate(PreopPatient(age=65, sex='M', op_subtype=sub_ov, pathway=pw_ov, side=sd, started_on=D0 - datetime.timedelta(days=6), questionnaire={'filled': True, 'abf_rest_pain': True}), today=D0)
            ev_lt_d = pm.evaluate(PreopPatient(age=65, sex='M', op_subtype=sub_ov, pathway=pw_ov, side=sd, started_on=D0 - datetime.timedelta(days=7), questionnaire={'filled': True, 'abf_rest_pain': True}), today=D0)
            self.assertFalse(ev_ok_d['over_target_days'])
            self.assertTrue(ev_lt_d['over_target_days'])

        for sub_ov, pw_ov in (('abf_clti', 'abf'), ('pta_clti', 'pad_pta')):
            sd = 'R' if pw_ov != 'abf' else ''
            ev_d7 = pm.evaluate(PreopPatient(age=65, sex='M', op_subtype=sub_ov, pathway=pw_ov, side=sd, started_on=D0 - datetime.timedelta(days=6), questionnaire={'filled': True, 'abf_rest_pain': True}), today=D0)
            ev_d8 = pm.evaluate(PreopPatient(age=65, sex='M', op_subtype=sub_ov, pathway=pw_ov, side=sd, started_on=D0 - datetime.timedelta(days=7), questionnaire={'filled': True, 'abf_rest_pain': True}), today=D0)
            ev_d8_ap = pm.evaluate(PreopPatient(age=65, sex='M', op_subtype=sub_ov, pathway=pw_ov, side=sd, started_on=D0 - datetime.timedelta(days=7), approved_at=datetime.datetime(2026, 6, 15, 12, 0, tzinfo=datetime.timezone.utc), questionnaire={'filled': True, 'abf_rest_pain': True}), today=D0)
            self.assertNotIn('გახანგრძლივებული მომზადება', ' '.join(ev_d7['red_flags']))
            self.assertIn('გახანგრძლივებული მომზადება', ' '.join(ev_d8['red_flags']))
            self.assertNotIn('გახანგრძლივებული მომზადება', ' '.join(ev_d8_ap['red_flags']))

        self.assertFalse(pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='pta_claud', pathway='pad_pta', side='R', questionnaire={'filled': True}, studies={'abi_r': 1.40}), today=D0)['abi_non_compressible'])
        self.assertTrue(pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='pta_claud', pathway='pad_pta', side='R', questionnaire={'filled': True}, studies={'abi_r': 1.41}), today=D0)['abi_non_compressible'])
        self.assertFalse(pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='pta_claud', pathway='pad_pta', side='R', questionnaire={'filled': True}, studies={'abi_r': 0.40}), today=D0)['key_badge_urgent'])
        self.assertTrue(pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='pta_claud', pathway='pad_pta', side='R', questionnaire={'filled': True}, studies={'abi_r': 0.39}), today=D0)['key_badge_urgent'])
        self.assertEqual(pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='pta_claud', pathway='pad_pta', side='R', questionnaire={'filled': True}, studies={'abi_r': 1.40, 'abi_l': 1.45}), today=D0)['key_badge'], 'ABI 1.4')

        ev_gsv_p = pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='bypass_clti', pathway='pad_bypass', side='R', questionnaire={'filled': True}, studies={'pad_gsv_status': 'inadequate', 'alt_vein_mapped': 'pending', 'abi_r': 0.45, 'abi_l': 0.85}), today=D0)
        ev_gsv_bad_l = pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='bypass_clti', pathway='pad_bypass', side='R', questionnaire={'filled': True}, studies={'pad_gsv_status': 'inadequate', 'alt_vein_mapped': 'contra_gsv_ok', 'abi_r': 0.45, 'abi_l': 0.49}), today=D0)
        ev_gsv_ok_l = pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='bypass_clti', pathway='pad_bypass', side='R', questionnaire={'filled': True}, studies={'pad_gsv_status': 'inadequate', 'alt_vein_mapped': 'contra_gsv_ok', 'abi_r': 0.45, 'abi_l': 0.50}), today=D0)
        ev_gsv_empty = pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='bypass_clti', pathway='pad_bypass', side='R', questionnaire={'filled': True}, studies={'pad_gsv_status': 'inadequate', 'alt_vein_mapped': ''}), today=D0)
        self.assertFalse(next(t for t in ev_gsv_p['tasks'] if t['key'] == 'alt_vein')['done'])
        self.assertTrue(next(t for t in ev_gsv_ok_l['tasks'] if t['key'] == 'alt_vein')['done'])
        self.assertGreater(len(ev_gsv_bad_l['red_flags']), len(ev_gsv_ok_l['red_flags']))
        self.assertFalse(next(r for r in ev_gsv_empty['reflex_orders'] if 'აუტოვენ' in r['action'])['done'])
        self.assertTrue(next(r for r in ev_gsv_ok_l['reflex_orders'] if 'აუტოვენ' in r['action'])['done'])

        self.assertTrue(any('7-დღიანი' in a for a in pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='abf_claud', pathway='abf', questionnaire={'filled': True, 'abf_rest_pain': True}), today=D0)['amber_flags']))
        self.assertFalse(any('7-დღიანი' in a for a in pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='abf_claud', pathway='abf', questionnaire={'filled': False, 'abf_rest_pain': True}), today=D0)['amber_flags']))
        self.assertFalse(any('სტატინ' in a for a in pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='abf_claud', pathway='abf', questionnaire={'filled': False}), today=D0)['amber_flags']))
        self.assertTrue(any('სტატინ' in a for a in pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='abf_claud', pathway='abf', questionnaire={'filled': True}), today=D0)['amber_flags']))
        self.assertTrue(pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='abf_clti', pathway='abf', questionnaire={'filled': True}, studies={'abi_r': 0.55}), today=D0)['key_badge_urgent'])
        self.assertEqual(pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='bypass_claud', pathway='pad_bypass', side='R', questionnaire={'filled': True, 'abf_rest_pain': True}), today=D0)['target_days'], 7)
        self.assertEqual(pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='bypass_claud', pathway='pad_bypass', side='R', questionnaire={'filled': True, 'abf_ulcer': True}), today=D0)['target_days'], 7)

        ev_svt = pm.evaluate(PreopPatient(age=45, sex='F', op_subtype='evla', pathway='venous', side='R', questionnaire={'filled': True, 'ven_acute_svt': True}), today=D0)
        ev_lido = pm.evaluate(PreopPatient(age=45, sex='F', op_subtype='evla', pathway='venous', side='R', questionnaire={'filled': True, 'ven_lidocaine_allergy': True}), today=D0)
        ev_c6 = pm.evaluate(PreopPatient(age=45, sex='F', op_subtype='evla', pathway='venous', side='R', questionnaire={'filled': True, 'ven_ceap': 'C6'}), today=D0)
        ev_c2 = pm.evaluate(PreopPatient(age=45, sex='F', op_subtype='evla', pathway='venous', side='R', questionnaire={'filled': True, 'ven_ceap': 'C2'}), today=D0)
        self.assertGreater(len(ev_svt['red_flags']), len(ev_c2['red_flags']))
        self.assertGreater(len(ev_lido['red_flags']), len(ev_c2['red_flags']))
        self.assertTrue(ev_c6['needs_wound_care'])
        self.assertNotEqual(ev_c6['priority_tier'], ev_c2['priority_tier'])
        self.assertEqual(ev_svt['priority_tier'], 'urgent')
        self.assertEqual(pm.evaluate(PreopPatient(age=45, sex='F', op_subtype='evla', pathway='venous', side='R', questionnaire={'filled': True, 'ven_bleeding': True}), today=D0)['priority_tier'], 'urgent')
        self.assertEqual(ev_c6['priority_tier'], 'high')
        self.assertEqual(pm.evaluate(PreopPatient(age=45, sex='F', op_subtype='evla', pathway='venous', side='R', questionnaire={'filled': True, 'ven_ceap': 'C4'}), today=D0)['priority_tier'], 'low')
        self.assertTrue(pm.evaluate(PreopPatient(age=65, sex='F', op_subtype='evla', pathway='venous', side='R', questionnaire={'filled': True, 'ven_ceap': 'C5'}), today=D0)['needs_abi_check'])
        self.assertFalse(pm.evaluate(PreopPatient(age=64, sex='F', op_subtype='evla', pathway='venous', side='R', questionnaire={'filled': True, 'ven_ceap': 'C5'}), today=D0)['needs_abi_check'])
        self.assertGreater(
            pm.evaluate(PreopPatient(age=35, sex='F', op_subtype='evla', pathway='venous', side='R', questionnaire={'filled': True, 'ven_hormones': True}), today=D0)['caprini_score'],
            pm.evaluate(PreopPatient(age=35, sex='F', op_subtype='evla', pathway='venous', side='R', questionnaire={'filled': True}), today=D0)['caprini_score'],
        )
        self.assertFalse(pm.evaluate(PreopPatient(age=62, sex='F', op_subtype='evla', pathway='venous', side='R', questionnaire={'filled': False, 'ven_hormones': True}), today=D0)['needs_vte_prophylaxis'])
        self.assertTrue(pm.evaluate(PreopPatient(age=62, sex='F', op_subtype='evla', pathway='venous', side='R', questionnaire={'filled': True, 'ven_hormones': True}), today=D0)['needs_vte_prophylaxis'])

        # Venous S1 creat / INR requirements, ABI & missing_items checks
        s_evla_base = {'hb': 135, 'plt': 220, 'ven_duplex_status': 'deep_patent_reflux'}
        self.assertTrue(pm.evaluate(PreopPatient(age=35, sex='F', op_subtype='evla', pathway='venous', side='R', questionnaire={'filled': True}, studies=s_evla_base), today=D0)['s1_core_done'])
        self.assertFalse(pm.evaluate(PreopPatient(age=35, sex='F', op_subtype='evla', pathway='venous', side='R', questionnaire={'filled': True, 'med_doac': True}, studies=s_evla_base), today=D0)['s1_core_done'])
        self.assertFalse(pm.evaluate(PreopPatient(age=35, sex='F', op_subtype='evla', pathway='venous', side='R', questionnaire={'filled': True}, studies={**s_evla_base, 'anesthesia_plan': 'neuraxial'}), today=D0)['s1_core_done'])
        self.assertFalse(pm.evaluate(PreopPatient(age=35, sex='F', op_subtype='phlebectomy', pathway='venous', side='R', questionnaire={'filled': True}, studies=s_evla_base), today=D0)['s1_core_done'])
        self.assertNotIn('inr', pm.evaluate(PreopPatient(age=35, sex='F', op_subtype='evla', pathway='venous', side='R', questionnaire={'filled': True}), today=D0)['s1_required_fields'])
        self.assertIn('inr', pm.evaluate(PreopPatient(age=35, sex='F', op_subtype='evla', pathway='venous', side='R', questionnaire={'filled': True, 'med_doac': True}), today=D0)['s1_required_fields'])
        self.assertIn('inr', pm.evaluate(PreopPatient(age=35, sex='F', op_subtype='evla', pathway='venous', side='R', questionnaire={'filled': True}, studies={'liver_status': '2x'}), today=D0)['s1_required_fields'])
        self.assertTrue(any(t['key'] == 'abi' for t in pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='evla', pathway='venous', side='R', questionnaire={'filled': True, 'claudication': True}), today=D0)['tasks']))
        self.assertTrue(any(t['key'] == 'abi' for t in pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='open', pathway='aaa_infra', questionnaire={'filled': True, 'aaa_pedal_pulse': 'reduced'}), today=D0)['tasks']))
        self.assertIn('ecg', [m['field'] for m in pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='pta_claud', pathway='pad_pta', side='R', questionnaire={'filled': True}, studies={'ecg': 'pending'}), today=D0)['missing_items']])
        self.assertNotIn('ecg', [m['field'] for m in pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='pta_claud', pathway='pad_pta', side='R', questionnaire={'filled': True}, studies={'ecg': 'normal'}), today=D0)['missing_items']])
        self.assertIn('cxr', [m['field'] for m in pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='open', pathway='aaa_infra', questionnaire={'filled': True, 'pulm': True}, studies={'cxr': 'pending'}), today=D0)['missing_items']])
        self.assertNotIn('cxr', [m['field'] for m in pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='open', pathway='aaa_infra', questionnaire={'filled': True, 'pulm': True}, studies={'cxr': 'normal'}), today=D0)['missing_items']])
        self.assertFalse(pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='abf_clti', pathway='abf', questionnaire={'filled': True}, studies={'abf_clamp_zone': 'infrarenal', 'abf_outflow': 'patent', 'abi_r': 0.6}), today=D0)['s1_core_done'])
        self.assertIn('abi_r', [m['field'] for m in pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='abf_clti', pathway='abf', questionnaire={'filled': True}), today=D0)['missing_items']])
        self.assertNotIn('abi_r', [m['field'] for m in pm.evaluate(PreopPatient(age=65, sex='M', op_subtype='abf_clti', pathway='abf', questionnaire={'filled': True}, studies={'abi_r': 0.6}), today=D0)['missing_items']])
