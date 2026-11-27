"""Stage 4 Round-8 regression tests covering ALL Category (a) S1/S2/S3 findings from Round-7 3-agent re-verification."""
import datetime

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from ..models import BoardEntry, Case, PreopPatient, StaffProfile
from ..preop import (
    admit_preop_to_board,
    calc_auto_asa,
    evaluate,
    generate_anamnesis,
    make_self_fill_token,
    nurse_plan,
    nurse_ui,
)
from ..preop.meds import _detect_freetext_high_risk_meds, _effective_allergy_state
from ..preop.registry import REGISTRY_VERSION, RULES_BY_ID, STATUS_LOCAL, match_rule_for_text, rule_value
from ..preop.utils import cta_live_months_age

TODAY = datetime.date(2026, 11, 26)
FUTURE_OP = (TODAY + datetime.timedelta(days=10)).isoformat()


def _go(sub='open', **kw):
    q = {'filled': True, 'nurse_verified': True, 'allergy_iodine_asked': True}
    q.update(kw.pop('q', {}))
    pw = PreopPatient.SUBTYPE_PATHWAY[sub]
    s = {
        'labs_date': TODAY.isoformat(),
        'bp_sys': 125, 'bp_dia': 78, 'hr': 72, 'spo2': 98,
        'weight_kg': 80, 'height_cm': 175,
        'hb': 135, 'plt': 220, 'inr': 1.05, 'creat': 85, 'creat_unit': 'umol',
        'glucose': 5.4, 'k': 4.2, 'na': 140, 'ecg': 'normal', 'cxr': 'normal',
        'lvef': 58, 'aaa_mm': 58 if pw == 'aaa_infra' else None, 'cta_disc': True, 'aaa_neck_status': 'friendly_ifu',
        'aaa_iliac_access': 'ok_7mm', 'popliteal_screen_status': 'normal',
        'carotid_r': 75 if pw == 'cea' else None, 'carotid_l': 20 if pw == 'cea' else None, 'cea_cta_status': 'concordant',
        'brain_imaging': 'normal_small', 'abi_r': 1.05 if pw == 'venous' else 0.55, 'abi_l': 0.95,
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
    side = kw.pop('side', 'R' if pw in ('cea', 'pad_bypass', 'pad_pta', 'venous') else '')
    return PreopPatient(
        patient_name=kw.pop('patient_name', 'R8 Test'),
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


class Round8PharmacologyAndSafetyTests(SimpleTestCase):
    """Engine tests for Round-8 pharmacology, OAC/stent/allergy/GLP-1/SGLT2/bridging fixes."""

    def test_s1_contraindicated_oac_never_restarted_on_any_surface_or_pathway(self):
        # Clin A-S1-1 & A-S1-2 & Combos A-S1-2: Contraindicated OAC must NEVER append restart text or advise OAC post-op
        cases = [
            ('bypass_clti', {'med_dabigatran': True, 'on_dialysis': True}, {}),
            ('pta_clti', {'med_doac': True}, {'liver_status': '3x'}),
            ('abf_clti', {'med_doac': True, 'on_dialysis': True}, {}),
            ('open', {'med_doac': True, 'cad_chips': ['mech_valve']}, {}),
            ('evla', {'med_doac': True, 'med_warfarin': True}, {}),
            ('pta_clti', {'med_doac': True}, {'creat': 280, 'weight_kg': 60}),
        ]
        for sub, qx, sx in cases:
            ev = evaluate(_go(sub, q=qx, s=sx), today=TODAY)
            self.assertTrue(ev['has_contra_oac'], f'has_contra_oac False for {sub} {qx} {sx}')
            for row in ev['med_schedule']:
                if row.get('contraindicated'):
                    self.assertNotIn('აღდგენა: თერაპ.', row['rule'], f'Restart text on contra row: {row}')
                    self.assertNotIn('აღდგენა 12–24', row['rule'], f'Restart text on contra row: {row}')
            post_at = ev['surgical_brief']['postop_antithrombotic']
            self.assertNotIn('OAC + კლოპიდოგრელი', post_at, f'Post-op contra OAC on {sub}: {post_at}')
            self.assertNotIn('მუდმივი ანტიკოაგულანტის (OAC) აღდგენა', post_at, f'Post-op contra OAC on {sub}: {post_at}')

        # Rivaroxaban 2.5 mg + potent P2Y12 / liver 3x must not append restart text
        ev_r25 = evaluate(_go('pta_clti', q={'med_chips': ['rivaroxaban_2_5'], 'med_ticagrelor': True}), today=TODAY)
        r25_row = next(r for r in ev_r25['med_schedule'] if '2.5' in r['drug'])
        self.assertTrue(r25_row['contraindicated'])
        self.assertNotIn('აღდგენა', r25_row['rule'])

    def test_s1_stent_actual_p2y12_pta_voya_vokanamet_brands_allergies_and_bridging(self):
        # Combos A-S1-4: Recent coronary stent on ticagrelor / prasugrel must NOT be prescribed clopidogrel
        for drug_k, drug_ka in (('med_ticagrelor', 'ტიკაგრელორ'), ('med_prasugrel', 'პრასუგრელ')):
            for sub in ('open', 'abf_clti', 'bypass_clti', 'pta_clti'):
                ev = evaluate(_go(sub, q={'recent_stent': True, 'med_aspirin': True, drug_k: True}), today=TODAY)
                self.assertIn(drug_ka, ev['surgical_brief']['postop_antithrombotic'], f'{sub}/{drug_k}')
                self.assertNotIn('კლოპიდოგრელ', ev['surgical_brief']['postop_antithrombotic'], f'{sub}/{drug_k}')

        # Clin A-S1-6: PTA + rivaroxaban_2_5 + clopidogrel_intol must NOT recommend ticagrelor
        ev_pv = evaluate(_go('pta_clti', q={'med_chips': ['rivaroxaban_2_5'], 'allergy_chips': ['clopidogrel_intol']}), today=TODAY)
        self.assertFalse(any('გამოიყენეთ ტიკაგრელორი' in rf for rf in ev_pv['red_flags']))
        self.assertTrue(any('აკრძალულია' in rf and 'ტიკაგრელორი' in rf for rf in ev_pv['red_flags']))

        # Clin A-S1-3: Vokanamet (canagliflozin+metformin) detected as SGLT2 + triggers 72h hold + DoS ketones
        for txt in ('ვოკანამეტი 50/1000', 'Vokanamet 150/1000'):
            ft = _detect_freetext_high_risk_meds(txt, q={})
            self.assertIn('sglt2', ft['matched_keys'], txt)
            ev_v = evaluate(_go('evla', q={'other_meds': txt}), today=TODAY)
            self.assertNotEqual(ev_v['clearance_seal']['code'], 'go')
            self.assertTrue(any(c['key'] == 'dos_ketones_ok' for c in ev_v['dos_checks']))

        # Combos A-S1-7: Unrecognized allergen in allergy_text blocks even when beta_lactam chip checked + Georgian brands
        ev_vanc = evaluate(_go('open', q={'allergy_chips': ['beta_lactam'], 'allergy_text': 'ვანკომიცინი'}), today=TODAY)
        self.assertTrue(ev_vanc['has_blocking_unknown_state'])
        self.assertNotIn('ვანკომიცინით', ev_vanc['surgical_brief']['abx_regimen'])
        self.assertIn('კლინდამიცინ', ev_vanc['surgical_brief']['abx_regimen'])
        for brand, exp_key in (
            ('ვარფარექსი 5მგ', 'warfarin'), ('ლოპირელი 75', 'clopidogrel'),
            ('პლოგრილი', 'clopidogrel'), ('კლოპიდექსი', 'clopidogrel'),
            ('ეგითრომბი', 'clopidogrel'), ('ასპიკარდი', 'aspirin'),
        ):
            self.assertIn(exp_key, _detect_freetext_high_risk_meds(brand, q={})['matched_keys'], brand)
        self.assertTrue(_detect_freetext_high_risk_meds('ემანერა 40 მგ', q={})['has_omeprazole'])
        for alg_txt, exp_chip in (
            ('კარდიომაგნილი', 'nsaid'), ('დექსალგინი', 'nsaid'),
            ('ზილტი', 'clopidogrel_intol'), ('ტრომბექსი', 'clopidogrel_intol'),
            ('მარკაინი', 'lidocaine'),
        ):
            chips, _, _ = _effective_allergy_state({'allergy_text': alg_txt})
            self.assertIn(exp_chip, chips, alg_txt)

        # Clin A-S1-7: Warfarin bridging on symp CEA with ECG-documented AFib (without cad_chips=['afib'])
        ev_af = evaluate(_go('cea_symp', q={'med_warfarin': True, 'cea_days_since_event': 7}, s={'ecg': 'afib_controlled'}), today=TODAY)
        vka_row = next(r for r in ev_af['med_schedule'] if 'ვარფარინ' in r['drug'])
        self.assertIn('LMWH', vka_row['rule'])

    def test_s1_s2_elapsed_holds_glp1_venous_lido_crescendo_and_minor_pharm(self):
        # Clin A-S1-5: Non-dialysis CrCl < 30 DOAC/Dabigatran fires red flag & elapsed hold alert when op tomorrow
        tmrw = (TODAY + datetime.timedelta(days=1)).isoformat()
        for med_k in ('med_doac', 'med_dabigatran'):
            ev_cr = evaluate(_go('open', q={med_k: True}, s={'creat': 280, 'weight_kg': 60, 'planned_surgery_date': tmrw}), today=TODAY)
            self.assertTrue(ev_cr['has_elapsed_hold_window'], med_k)
            self.assertTrue(any('ფანჯარა' in rf for rf in ev_cr['red_flags']), med_k)
            self.assertTrue(any('CrCl <30' in rf or 'CrCl < 30' in rf for rf in ev_cr['red_flags']), med_k)

        # Clin A-S2-1 & Fuzz A-S2-3: Weekly GLP-1 does NOT fire false elapsed hold 2-6d before surgery
        op_4d = (TODAY + datetime.timedelta(days=4)).isoformat()
        ev_glp = evaluate(_go('open', q={'glp1': True}, s={'planned_surgery_date': op_4d}), today=TODAY)
        self.assertFalse(ev_glp['has_elapsed_hold_window'])
        self.assertFalse(any('ფანჯარა' in rf for rf in ev_glp['red_flags']))

        # Clin A-S1-4: Venous lidocaine allergy STOP on EVLA+plexus and Phlebectomy+local
        for sub, plan in (('evla', 'plexus'), ('phlebectomy', 'local')):
            ev_vl = evaluate(_go(sub, q={'ven_lidocaine_allergy': True}, s={'anesthesia_plan': plan}), today=TODAY)
            self.assertTrue(ev_vl['has_hard_contraindication'], f'{sub}/{plan}')
            self.assertEqual(ev_vl['clearance_seal']['code'], 'stop')

        # Combos A-S1-3: Active cea_crescendo ALWAYS classifies as symptomatic CEA even if days>180 or op_subtype='cea_asymp'
        for sub, extra_q in (
            ('cea_symp', {'cea_crescendo': True, 'cea_days_since_event': 200, 'med_clopidogrel': True}),
            ('cea_asymp', {'cea_crescendo': True, 'med_clopidogrel': True}),
        ):
            p_cr = _go(sub, q=extra_q)
            ev_cr = evaluate(p_cr, today=TODAY)
            self.assertTrue(ev_cr['is_symp_cea'], sub)
            clop_r = next(r for r in ev_cr['med_schedule'] if 'კლოპიდოგრელ' in r['drug'])
            self.assertFalse(clop_r['hold_or_skip'], sub)
            self.assertNotIn('ასიმპტომური სტენოზის გამო', generate_anamnesis(p_cr, ev_cr))

        # Clin A-S2-3, A-S2-4, A-S2-8, A-S3-1
        ev_abi90 = evaluate(_go('phlebectomy', q={'med_aspirin': True}, s={'abi_r': 0.90}), today=TODAY)
        asa_90 = next(r for r in ev_abi90['med_schedule'] if 'ასპირინ' in r['drug'])
        self.assertFalse(asa_90['hold_or_skip'])
        ev_lm = evaluate(_go('evla', q={'metformin': True, 'ven_lidocaine_allergy': True}, s={'creat': 160, 'anesthesia_plan': 'general'}), today=TODAY)
        self.assertTrue(any('48' in af and 'მეტფორმინ' in af for af in ev_lm['amber_flags']))
        ev_pr_cea = evaluate(_go('cea_symp', q={'med_prasugrel': True, 'cea_days_since_event': 5}), today=TODAY)
        self.assertTrue(ev_pr_cea['needs_cea_antiplatelet'])
        ev_cyp = evaluate(_go('bypass_claud', q={'med_chips': ['cilostazol', 'ppi']}), today=TODAY)
        self.assertTrue(any('ცილოსტაზოლის' in af and 'ტოქსიკურ' in af for af in ev_cyp['amber_flags']))


class Round8InvariantsPathwaysAndRegistryTests(SimpleTestCase):
    """Tests for Round-8 monotonicity, calculators, pathways, nurse_plan, and Registry v1.2.7."""

    def test_s1_s2_nurse_plan_stop_vs_caution_red_flags_and_stale_refreshed(self):
        # Combos A-S1-1: Reconcile-only / unstructured / missing-CrCl / GLP-1 / must-continue drugs NEVER leak into stop_meds
        for sub, qx, sx in (
            ('evar', {'recent_stent': True, 'med_clopidogrel': True, 'med_ticagrelor': True}, {}),
            ('cea_symp', {'med_ticagrelor': True}, {}),
            ('open', {'other_meds': 'თრომბო ასი 100 მგ'}, {}),
            ('open', {'med_doac': True}, {'creat': None}),
            ('open', {'glp1': True}, {}),
            ('evla', {'med_clopidogrel': True}, {}),
        ):
            p_m = _go(sub, q=qx, s=sx)
            ev_m = evaluate(p_m, today=TODAY)
            g = nurse_plan.build_patient_plain_explanation(p_m, ev_m, TODAY)
            self.assertEqual(g['stop_meds'], [], f'Leaked into stop_meds for {sub}/{qx}: {g["stop_meds"]}')
            self.assertTrue(len(g['caution_meds']) >= 1 or len(g['keep_meds']) >= 1)

        # Combos A-S1-5: New Nurse checklist surfaces ALL red flags & solo neuraxial STOP (no silent truncation)
        p_mx = _go(
            'cea_symp',
            q={'allergy_chips': ['hit_heparin'], 'recent_stent': True, 'med_warfarin': True},
            s={'anesthesia_plan': 'neuraxial', 'k': 6.2, 'hb': 72, 'inr': 1.8},
        )
        ev_mx = evaluate(p_mx, today=TODAY)
        miss_mx = nurse_plan.build_plain_georgian_missing_items(p_mx, ev_mx, TODAY)
        joined_miss = ' | '.join(it['label'] for it in miss_mx)
        self.assertIn('ნეირაქსიული', joined_miss)
        self.assertIn('6.2', joined_miss)
        self.assertIn('72', joined_miss)

        # Fuzz A-S2-1 & A-S2-8: stale_labs_refreshed=True clears stale block; conditional seal != unqualified ready
        old_dt = (TODAY - datetime.timedelta(days=20)).isoformat()
        p_st = _go('evla', approved_at=timezone.now(), s={'labs_date': old_dt, 'stale_labs_refreshed': True})
        ev_st = evaluate(p_st, today=TODAY)
        v_st = nurse_ui.get_nurse_readiness_verdict(p_st, ev_st)
        self.assertEqual(v_st['code'], 'ready')
        self.assertEqual(ev_st['clearance_seal']['code'], 'go')

        p_cond = _go('open', approved_at=timezone.now(), s={'blood_reserved': False})
        ev_cond = evaluate(p_cond, today=TODAY)
        self.assertEqual(ev_cond['clearance_seal']['code'], 'conditional')
        self.assertNotEqual(nurse_ui.get_nurse_readiness_verdict(p_cond, ev_cond)['code'], 'ready')

    def test_s2_dates_bp_dos_gl_ali_pta_caprini_asa_chips_and_registry(self):
        # Clin A-S2-2: cta_live_months_age end-of-month accuracy
        self.assertEqual(cta_live_months_age({'cta_date': '2026-01-31'}, today=datetime.date(2026, 8, 30)), 7)
        self.assertEqual(cta_live_months_age({'cta_date': '2026-05-31'}, today=datetime.date(2026, 9, 30)), 4)

        # Clin A-S2-5, A-S2-6, A-S2-7: ALI dental bypass, DoS glucose >1000 saturation, PTA ABI<0.40 amber
        ev_ali_d = evaluate(_go('abf_clti', q={'abf_duration': '< 2 კვირა', 'dental_infection': True}), today=TODAY)
        self.assertFalse(ev_ali_d['needs_dental'])
        self.assertFalse(any(r['key'] == 'dental' for r in ev_ali_d['reflex_orders']))
        ev_dg = evaluate(_go('open', s={'dos_glucose': 1200}), today=TODAY)
        self.assertIn('VRIII', ev_dg['anesthetic_brief']['glycemic_target'])
        ev_pta_i = evaluate(_go('pta_claud', s={'abi_r': 0.35}), today=TODAY)
        self.assertTrue(any('0.35' in af or '0.40' in af for af in ev_pta_i['amber_flags']))

        # Fuzz A-S2-5 & A-S2-6: Bilateral inverted BP monotonicity + pre-1900 birth_date rejected
        ev_b1 = evaluate(_go('pta_claud', age=55, s={'bp_sys': 115, 'bp_dia': 114, 'bp_sys_l': 120, 'bp_dia_l': 80}), today=TODAY)
        ev_b2 = evaluate(_go('pta_claud', age=55, s={'bp_sys': 115, 'bp_dia': 116, 'bp_sys_l': 120, 'bp_dia_l': 80}), today=TODAY)
        self.assertTrue(ev_b1['severe_htn'] and ev_b2['severe_htn'])
        self.assertTrue(ev_b2['needs_cardio'])
        ev_1800 = evaluate(_go('evla', s={'birth_date': '1800-01-01'}), today=TODAY)
        self.assertTrue(ev_1800['has_blocking_unknown_state'])
        self.assertNotEqual(ev_1800['clearance_seal']['code'], 'go')

        # Combos A-S2-3, A-S2-4, A-S2-5 & Clin A-S2-9: DM ASA/contradiction, asthma!=COPD Caprini, LVEF/DVT chips, pathway scoping
        self.assertNotEqual(calc_auto_asa(_go('evla', age=40), {'filled': True}, {'hba1c': 7.0}), 'I')
        ev_dm_i = evaluate(_go('evla', q={'dm': False, 'dm_insulin': True}, s={'hba1c': 7.0}), today=TODAY)
        self.assertFalse(any('არადიაგნოსტირებული' in rf for rf in ev_dm_i['red_flags']))
        self.assertEqual(
            evaluate(_go('evla', age=35, q={'pulm': True, 'pulm_chips': ['asthma']}), today=TODAY)['caprini_score'],
            evaluate(_go('evla', age=35, q={'pulm': False}), today=TODAY)['caprini_score'],
        )
        self.assertGreater(
            evaluate(_go('evla', age=35, q={'ven_dvt_history': False, 'ven_dvt_chips': ['vte_recent_3m']}), today=TODAY)['caprini_score'],
            evaluate(_go('evla', age=35, q={'ven_dvt_history': False}), today=TODAY)['caprini_score'],
        )
        self.assertGreater(
            evaluate(_go('evla', age=35, q={'cad': False}, s={'lvef': 30}), today=TODAY)['caprini_score'],
            evaluate(_go('evla', age=35, q={'cad': False}, s={'lvef': 60}), today=TODAY)['caprini_score'],
        )
        self.assertTrue(evaluate(_go('evla', q={'ven_dvt_history': False, 'ven_dvt_chips': ['vte_recent_3m']}), today=TODAY)['needs_vte_prophylaxis'])
        self.assertTrue(evaluate(_go('open', q={'cad': False}, s={'lvef': 30}), today=TODAY)['needs_cxr'])
        self.assertEqual(calc_auto_asa(_go('evla', age=40), {'filled': True, 'abf_wet_infection': True}, {'aaa_morphology': 'inflammatory_mycotic'}), 'I')

        # Fuzz A-S3-1: Raw negative ven_gsv_mm/aaa_mm never printed verbatim
        ev_neg = evaluate(_go('evla', s={'ven_gsv_mm': -5}), today=TODAY)
        self.assertNotIn('-5', ev_neg['surgical_brief']['anatomy_summary'])
        p_naaa = _go('open', s={'aaa_mm': -99})
        self.assertNotIn('-99', p_naaa.diagnosis_text)

        # Registry v1.2.7 honesty & vein_strip match (Clin A-S2-9, A-S2-10, A-S2-11, A-S2-12)
        self.assertEqual(REGISTRY_VERSION, '1.2.7')
        self.assertEqual(RULES_BY_ID['SCORE_CAPRINI_VTE'].status, STATUS_LOCAL)
        self.assertEqual(RULES_BY_ID['GI_CAROTID_CONCOMITANT_AND_PPI'].status, STATUS_LOCAL)
        self.assertIn('vriii_mmol', rule_value('GLY_GLUCOSE_HBA1C'))
        self.assertIn('k_succ_hi', rule_value('REN_ELECTROLYTES_K_NA'))
        self.assertIn('lvef_iv', rule_value('SCORE_ASA_PHYSICAL_STATUS'))
        ev_vs = evaluate(_go('bypass_clti', q={'vasc_chips': ['vein_strip']}, s={'pad_gsv_status': 'adequate'}), today=TODAY)
        vs_flag = next(a for a in ev_vs['amber_flags'] if 'vein_strip' in a)
        self.assertIsNotNone(match_rule_for_text(vs_flag))


class Round8HttpBoardPassportAndUiTests(TestCase):
    """HTTP workflows, approval invalidation, self-fill safety, Board handoff, Passport & New UI tests."""

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.doc = User.objects.create_user(username='r8_doc', password='DocPassword!1', first_name='გიორგი', last_name='ექიმი', is_staff=True)
        StaffProfile.objects.create(user=cls.doc, display_name='გიორგი ექიმი')
        cls.nurse = User.objects.create_user(username='r8_nurse', password='NursePassword!1', first_name='ნინო', last_name='ექთანი')
        StaffProfile.objects.create(user=cls.nurse, display_name='ნინო ექთანი')

    def _create(self, sub='open', **kw):
        p = _go(sub, surgeon=self.doc, created_by=self.doc, updated_by=self.doc, **kw)
        p.save()
        return p

    def test_http_side_flip_elapsed_medcall_ready_gate_reopen_selffill_and_revoked_token(self):
        c_doc, c_nurse, c_anon = self.client_class(), self.client_class(), self.client_class()
        c_doc.force_login(self.doc)
        c_nurse.force_login(self.nurse)

        # Fuzz A-S1-1: Tab 1 side flip R->L MUST clear approved_at & someday_list='ready' in DB
        p_side = self._create('evla', side='R', approved_at=timezone.now(), approved_by=self.doc, someday_list=PreopPatient.SomedayList.READY)
        c_nurse.post(reverse('preop-patient', args=[p_side.pk]), {'section': 'questionnaire', 'side': 'L', 'ven_ceap': 'C2', 'mets_ok': '1'})
        p_side.refresh_from_db()
        self.assertEqual(p_side.side, 'L')
        self.assertIsNone(p_side.approved_at)
        self.assertNotEqual(p_side.someday_list, PreopPatient.SomedayList.READY)

        # Fuzz A-S1-3: Elapsed hold window blocks both med-call & stage2 med_hold_agreed
        tmrw = (timezone.localdate() + datetime.timedelta(days=1)).isoformat()
        p_el = self._create('open', q={'med_warfarin': True}, s={'planned_surgery_date': tmrw, 'med_hold_agreed': False})
        c_nurse.post(reverse('preop-med-call', args=[p_el.pk]), {'action': 'called'})
        p_el.refresh_from_db()
        self.assertFalse(p_el.studies.get('med_hold_agreed'))
        c_doc.post(reverse('preop-patient', args=[p_el.pk]), {'section': 'stage2', 'med_hold_agreed': '1'})
        p_el.refresh_from_db()
        self.assertFalse(p_el.studies.get('med_hold_agreed'))

        # Fuzz A-S1-4: Approved patient with aged stale labs cannot enter someday:ready
        old_dt = (timezone.localdate() - datetime.timedelta(days=20)).isoformat()
        p_ag = self._create('evla', approved_at=timezone.now(), approved_by=self.doc, s={'labs_date': old_dt, 'stale_labs_refreshed': False})
        c_nurse.post(reverse('preop-move', args=[p_ag.pk]), {'action': 'move', 'slot': 'someday:ready'})
        p_ag.refresh_from_db()
        self.assertNotEqual(p_ag.someday_list, PreopPatient.SomedayList.READY)

        # Fuzz A-S1-5: Reopened self-fill preserves nurse-entered critical flags (med_warfarin, allergy_iodine)
        p_ro = self._create('evla', q={'filled': True, 'nurse_verified': True, 'med_warfarin': True, 'allergy_iodine': True})
        c_nurse.post(reverse('preop-patient', args=[p_ro.pk]), {'section': 'self_link', 'action': 'rotate'})
        p_ro.refresh_from_db()
        tok = make_self_fill_token(p_ro)
        c_anon.post(reverse('preop-self-fill', args=[tok]), {'side': 'R', 'mets_ok': '1'})
        p_ro.refresh_from_db()
        self.assertTrue(p_ro.questionnaire.get('med_warfarin'))
        self.assertTrue(p_ro.questionnaire.get('allergy_iodine'))

        # Fuzz A-S1-6: SelfFillSentView must NEVER resurrect a revoked token
        p_rv = self._create('evla', q={'filled': False, 'nurse_verified': False})
        tok_rv = make_self_fill_token(p_rv)
        c_nurse.post(reverse('preop-patient', args=[p_rv.pk]), {'section': 'self_link', 'action': 'revoke'})
        c_nurse.post(reverse('preop-self-link-sent', args=[p_rv.pk]), {'via': 'copy'})
        self.assertEqual(c_anon.get(reverse('preop-self-fill', args=[tok_rv])).status_code, 404)

        # Fuzz A-S2-2: Self-fill cea_event_chips saves cea_event_date
        p_cea_sf = self._create('cea_symp', q={'filled': False, 'nurse_verified': False})
        ev_dt = (timezone.localdate() - datetime.timedelta(days=3)).isoformat()
        c_anon.post(reverse('preop-self-fill', args=[make_self_fill_token(p_cea_sf)]), {'side': 'R', 'cea_event_chips': ['tia_limb'], 'cea_event_date': ev_dt})
        p_cea_sf.refresh_from_db()
        self.assertEqual(p_cea_sf.questionnaire.get('cea_event_date'), ev_dt)

        # Combos A-S1-6: Pregnant ALI patient CAN be approved by surgeon without falsifying dos_bhcg_negative
        p_preg_ali = self._create(
            'bypass_clti', sex='F', age=30,
            q={'abf_duration': '< 2 კვირა', 'pregnant_possible': True},
            s={'dos_bhcg_negative': False},
        )
        c_doc.post(reverse('preop-patient', args=[p_preg_ali.pk]), {'section': 'signoff', 'action': 'approve'})
        p_preg_ali.refresh_from_db()
        self.assertIsNotNone(p_preg_ali.approved_at)

        # Fuzz A-S2-4 & A-S2-7 & A-S3-3: Past surgery date rejected on dos_vqi & register; sync=1 clears dos_site_marked; file tab allowlisted
        p_dv = self._create('open', s={'planned_surgery_date': FUTURE_OP})
        c_doc.post(reverse('preop-patient', args=[p_dv.pk]), {'section': 'dos_vqi', 'planned_surgery_date': '2020-01-01'})
        p_dv.refresh_from_db()
        self.assertEqual(p_dv.studies.get('planned_surgery_date'), FUTURE_OP)
        self.assertIn('planned_surgery_date', p_dv.studies.get('_invalid_inputs') or {})
        self.assertTrue(evaluate(p_dv)['has_blocking_unknown_state'])

        r_reg_past = c_nurse.post(reverse('preop-nurse-register'), {'patient_name': 'Past Op', 'op_subtype': 'evla', 'side': 'R', 'planned_surgery_date': '2020-01-05'})
        self.assertEqual(r_reg_past.status_code, 400)

        case_s = Case.objects.create(doctor=self.doc, patient_name='SideSync', age=65, sex='M', side='L', diagnosis='ქვემო კიდურის ათეროსკლეროზი')
        p_ss = self._create('bypass_claud', case=case_s, side='', s={'dos_site_marked': True})
        c_doc.get(reverse('preop-new') + f'?case={case_s.pk}&sync=1')
        p_ss.refresh_from_db()
        self.assertFalse(p_ss.studies.get('dos_site_marked'))

        r_f = c_nurse.post(reverse('preop-files', args=[p_ss.pk]), {'tab': 's1&evil=1'})
        self.assertIn('?tab=s1#files', r_f['Location'])

    def test_passport_board_and_new_nurse_ui_honesty_and_parity(self):
        c_doc = self.client_class()
        c_doc.force_login(self.doc)

        # Fuzz A-S1-2 & Combos A-S2-1: Unit mismatch 88 mg/dL & ambiguous 20 never print fabricated eGFR 1 or 20 umol/L
        p_mm = self._create('open', s={'creat': 88, 'creat_unit': 'mgdl'})
        html_mm = c_doc.get(reverse('preop-passport', args=[p_mm.pk])).content.decode('utf-8')
        self.assertNotIn('eGFR 1', html_mm)
        self.assertIn('ერთეული დასაზუსტებელია', html_mm)
        bp_mm, _, _ = admit_preop_to_board(p_mm, self.doc)
        st_mm = BoardEntry.objects.filter(patient=bp_mm, kind=BoardEntry.STATUS).first().text
        self.assertNotIn('eGFR 1', st_mm)

        p_amb = self._create('open', s={'creat': 20, 'creat_unit': ''})
        html_amb = c_doc.get(reverse('preop-passport', args=[p_amb.pk])).content.decode('utf-8')
        self.assertNotIn('20 µmol/L', html_amb)

        # Combos A-S2-2: Passport top banner does not deny iodine allergy when allergy_iodine_asked=False
        p_io = self._create('evla', q={'allergy_iodine_asked': False})
        html_io = c_doc.get(reverse('preop-passport', args=[p_io.pk])).content.decode('utf-8')
        self.assertNotIn('მედიკამენტურ და კონტრასტულ ალერგიას უარყოფს', html_io)

        # Clin A-S1-1 & Combos A-S1-2 & A-S2-6: Board mech_valve+DOAC never resumes OAC; EVLA skips beta-lactam ABX; left-arm BP kept
        p_mv = self._create('evla', q={'med_doac': True, 'cad_chips': ['mech_valve'], 'allergy_chips': ['beta_lactam', 'clopidogrel_intol']}, s={'bp_sys': None, 'bp_dia': None, 'bp_sys_l': 132, 'bp_dia_l': 82})
        bp_mv, _, _ = admit_preop_to_board(p_mv, self.doc)
        mv_tasks = [b.text for b in BoardEntry.objects.filter(patient=bp_mv, kind=BoardEntry.TASK)]
        mv_status = BoardEntry.objects.filter(patient=bp_mv, kind=BoardEntry.STATUS).first().text
        self.assertFalse(any('განახლდეს OAC' in t for t in mv_tasks))
        self.assertFalse(any('ვანკომიცინით' in t for t in mv_tasks))
        self.assertIn('132/82', mv_status)
        self.assertIn('კლოპიდოგრელ', mv_status)

        # Combos A-S1-5 & A-S2-7: New Nurse UI keeps active red flags visible even after ack_alerts + embeds full DoS/VQI
        p_nu = self._create('open', s={'k': 5.8, 'hb': 90})
        c_doc.post(reverse('preop-patient', args=[p_nu.pk]), {'section': 'signoff', 'action': 'ack_alerts'})
        c_doc.post(reverse('preop-ui-mode'), {'mode': 'new'})
        html_nu = c_doc.get(reverse('preop-patient', args=[p_nu.pk])).content.decode('utf-8')
        self.assertIn('5.8', html_nu)
        self.assertIn('case_outcome_status', html_nu)
