"""Test-first regression suite for Round-4 independent audit findings (Fuzz, Combos, Clinical & Registry v1.2.4)."""
import datetime
import json

from django.contrib.auth import get_user_model
from django.http import QueryDict
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from ..models import PreopPatient
from ..preop import (
    build_med_schedule,
    build_self_fill_highlights,
    calc_max_contrast_ml,
    evaluate,
    generate_anamnesis,
    make_self_fill_token,
    nurse_plan,
)
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
        patient_name='სატესტო რ5', age=65, sex='M', side='' if pw in ('aaa_infra', 'abf') else 'R',
        pathway=pw, op_subtype=sub, visit_date=datetime.date(2026, 11, 25),
        started_on=datetime.date(2026, 11, 25), cardio_decision='cleared', questionnaire=q, studies=s,
    )
    defaults.update(kw)
    return PreopPatient(**defaults)


class PreopStressFixesRound5UnitTests(SimpleTestCase):
    """Engine, saturation, pharmacology, combination, and registry regressions for Round-5."""

    def test_fuzz_s1_s2_s3_inverted_bp_hypernatremia_extreme_saturation_and_crcl(self):
        # F-S1-1: Inverted BP (100/105 right or left) hard-blocks neuraxial AND blocks GO on venous/arterial
        for s_bp in ({'bp_sys': 100, 'bp_dia': 105, 'hr': 110}, {'bp_sys_l': 100, 'bp_dia_l': 105, 'hr': 140}):
            ev_nx = evaluate(_go('phlebectomy', s={'anesthesia_plan': 'neuraxial', **s_bp}), today=TODAY)
            self.assertTrue(ev_nx['neuraxial_hard_blocked'], f'F-S1-1 failed for {s_bp}')
            self.assertNotIn('დაცულია', ev_nx['anesthetic_brief']['neuraxial_gate'])
            self.assertTrue(ev_nx['has_blocking_unknown_state'], f'F-S1-1 unknown block failed for {s_bp}')
            self.assertNotEqual(ev_nx['clearance_seal']['code'], 'go')

        # F-S1-2: Extreme Na+ > 190 (e.g. 195 via S1 POST or direct) preserves hypernatremia & avoids 0.9% NaCl
        p_na = _go('evar', s={'creat': 160, 'creat_unit': 'umol'})
        sm = dict(p_na.studies)
        _parse_s1_numbers_and_dates(QueryDict('na=195'), sm, TODAY)
        p_na.studies = sm
        ev_na = evaluate(p_na, today=TODAY)
        hyd_ord = next(o for o in ev_na['reflex_orders'] if o['key'] == 'hydration')
        self.assertIn('მოერიდეთ 0.9% NaCl', hyd_ord['action'])
        self.assertIn('აკრძალულია', ev_na['surgical_brief']['contrast_plan'])
        self.assertTrue(any('ჰიპერნატრიემია' in rf for rf in ev_na['red_flags']))

        # F-S1-3: Non-inverted DBP >= 110 (160/120) -> ASA III on EVLA; inverted 85/100 never drops to ASA I & blocks GO
        self.assertEqual(evaluate(_go('evla', s={'bp_sys': 160, 'bp_dia': 120}), today=TODAY)['auto_asa'], 'III')
        self.assertEqual(evaluate(_go('evla', s={'bp_sys': 179, 'bp_dia': 120}), today=TODAY)['auto_asa'], 'III')
        ev_85_100 = evaluate(_go('evla', s={'bp_sys': 85, 'bp_dia': 100, 'hr': 72}), today=TODAY)
        self.assertIn(ev_85_100['auto_asa'], ('III', 'IV'))
        self.assertTrue(ev_85_100['has_blocking_unknown_state'])
        self.assertNotEqual(ev_85_100['clearance_seal']['code'], 'go')

        # F-S2-1: HR > 260 preserves AFib tachycardia ecg_label, red flag, and Cilostazol Black Box red flag
        ev_hr = evaluate(_go('evla', q={'med_chips': ['statin', 'cilostazol']}, s={'ecg': 'afib_controlled', 'hr': 265}), today=TODAY)
        self.assertIn('ტაქისისტოლია', ev_hr['ecg_label'])
        self.assertTrue(any('ტაქისისტოლია' in rf for rf in ev_hr['red_flags']))
        self.assertTrue(any('ცილოსტაზოლ' in rf for rf in ev_hr['red_flags']))

        # F-S2-2: Extreme LVEF < 5, NT-proBNP > 100000, hs-cTn > 50000, BP > 320 preserve cardio/CEA/cilo flags
        ev_lv3 = evaluate(_go('cea_asymp', cardio_decision='', s={'lvef': 3}), today=TODAY)
        self.assertTrue(ev_lv3['needs_cardio'])
        self.assertTrue(ev_lv3['cea_high_surg_risk'])
        ev_bnp = evaluate(_go('evla', cardio_decision='', q={'med_chips': ['statin', 'cilostazol']}, s={'nt_probnp': 150000}), today=TODAY)
        self.assertTrue(ev_bnp['needs_cardio'])
        self.assertTrue(any('ცილოსტაზოლ' in rf for rf in ev_bnp['red_flags']))
        self.assertTrue(evaluate(_go('evla', cardio_decision='', s={'hs_ctn': 60000}), today=TODAY)['needs_cardio'])
        self.assertTrue(evaluate(_go('evla', cardio_decision='', s={'bp_sys': 350, 'bp_dia': 90}), today=TODAY)['needs_cardio'])

        # F-S2-3: Extreme glucose > 1000 mg/dL preserves needs_endo_reflex & VRIII glycemic target
        ev_gl = evaluate(_go('open', s={'glucose': 1500}), today=TODAY)
        self.assertTrue(ev_gl['needs_endo_reflex'])
        self.assertIn('VRIII', ev_gl['anesthetic_brief']['glycemic_target'])

        # F-S2-4: Super-obesity weight_kg > 260 preserves Cefazolin 3 g IV
        ev_wt = evaluate(_go('open', s={'weight_kg': 265}), today=TODAY)
        self.assertIn('3 გ', ev_wt['surgical_brief']['abx_regimen'])
        abx_chk = next(c for c in ev_wt['dos_checks'] if c['key'] == 'dos_abx_ready')
        self.assertIn('3 გ', abx_chk['label'])

        # F-S2-5: Valid physiological CrCl > 300 NOT discarded; negative egfr on direct build_med_schedule keeps Metformin RED
        ev_hi_crcl = evaluate(_go('open', age=20, sex='M', q={'med_doac': True}, s={'weight_kg': 90, 'height_cm': 185, 'creat': 0.4, 'creat_unit': 'mgdl'}), today=TODAY)
        doac_r = next(r for r in ev_hi_crcl['med_schedule'] if 'DOAC' in r['drug'])
        self.assertNotIn('შეიყვანეთ წონა', doac_r['rule'])
        direct_metf = build_med_schedule(_go('open', q={'metformin': True}), {'filled': True, 'metformin': True}, {}, egfr=-5, crcl=-5, today=TODAY)
        self.assertTrue(any(r['drug'] == 'მეტფორმინი' and r.get('contraindicated') for r in direct_metf))

        # F-S3-1 & F-S3-2: calc_max_contrast_ml safe on NaN/Inf/1e308/str; future birth_date & nested NaN guarded
        for bad_v in (float('nan'), float('inf'), 1e308, '', None):
            calc_max_contrast_ml(bad_v)
        ev_bd = evaluate(_go('open', s={'birth_date': '2099-01-01', '_invalid_inputs': {'x': float('nan')}}), today=TODAY)
        self.assertTrue(ev_bd['has_blocking_unknown_state'])
        self.assertNotEqual(ev_bd['clearance_seal']['code'], 'go')
        json.dumps(ev_bd['s'], allow_nan=False)

    def test_pharma_s1_aspirin_allergy_dialysis_doac_rtpa_riva_postop_stoplock_ali(self):
        # P-S1-1 (Clin A-S1-1 & Combos A-S1-1): med_aspirin=True + nsaid allergy -> red flag, needs_med_hold, never GO, not counted as safe antiplatelet
        for sub in ('open', 'cea_symp', 'pta_clti', 'evla'):
            ev_asa = evaluate(_go(sub, q={'med_aspirin': True, 'allergy_chips': ['nsaid']}, s={'med_hold_agreed': False}), today=TODAY)
            self.assertTrue(any('ასპირინ' in rf and 'ალერგია' in rf for rf in ev_asa['red_flags']), f'Missing red flag on {sub}')
            self.assertTrue(ev_asa['needs_med_hold'], f'needs_med_hold False on {sub}')
            self.assertNotEqual(ev_asa['clearance_seal']['code'], 'go', f'False GO on {sub}')
            if sub == 'cea_symp':
                self.assertTrue(ev_asa['needs_cea_antiplatelet'])

        # P-S1-2 (Combos A-S1-2): Dabigatran & Xa-DOAC on dialysis emit RED flag and never false GREEN
        for drug_k in ('med_dabigatran', 'med_doac'):
            ev_d = evaluate(_go('open', q={'on_dialysis': True, drug_k: True}, s={'nephro_panel_done': True, 'med_hold_agreed': True}), today=TODAY)
            self.assertTrue(ev_d['red_flags'], f'Missing red flag for {drug_k} on dialysis')
            self.assertNotEqual(ev_d['clearance_seal']['code'], 'go')

        # P-S1-3 (Clin A-S1-2 & Combos A-S1-6): CEA rt-PA days 1-6 normal_small respects NSAID allergy & suppresses conflicting cea_antiplatelet
        ev_rt_alg = evaluate(_go('cea_symp', q={'cea_symp_event': True, 'cea_days_since_event': 2, 'cea_thrombolysis': True, 'med_aspirin': False, 'allergy_chips': ['nsaid']}, s={'brain_imaging': 'normal_small'}), today=TODAY)
        self.assertNotIn('დასაშვებია მხოლოდ ასპირინი', ev_rt_alg['surgical_brief']['postop_antithrombotic'])
        self.assertFalse(any('დასაშვებია ასპირინი' in rf for rf in ev_rt_alg['red_flags']))
        self.assertFalse(any(ro['key'] == 'cea_antiplatelet' and 'კლოპიდოგრელი' in ro['action'] for ro in ev_rt_alg['reflex_orders']))
        ev_rt_noap = evaluate(_go('cea_symp', q={'cea_symp_event': True, 'cea_days_since_event': 2, 'cea_thrombolysis': True, 'med_aspirin': False}, s={'brain_imaging': 'normal_small'}), today=TODAY)
        self.assertFalse(any(ro['key'] == 'cea_antiplatelet' and 'კლოპიდოგრელი' in ro['action'] for ro in ev_rt_noap['reflex_orders']))

        # P-S1-4 (Combos A-S1-4): Comma/semicolon therapeutic rivaroxaban detected, no false positive on other drugs
        for txt in ('ქსარელტო 2.5 მგ, 20 მგ', 'xarelto 2.5 mg x2, 15 mg', 'ქსარელტო 2.5მგ; 10მგ'):
            self.assertTrue(_detect_freetext_high_risk_meds(txt, q={'med_chips': ['rivaroxaban_2_5']})['unstructured_antithrombotic'], txt)
        self.assertFalse(_detect_freetext_high_risk_meds('ქსარელტო 2.5 მგ, ამლოდიპინი 10 მგ', q={'med_chips': ['rivaroxaban_2_5']})['unstructured_antithrombotic'])

        # P-S1-5 (Clin A-S2-3 & Combos A-S1-5): Explicit post_antithrombotic_plan oac_sapt dual-allergy & stent SAPT/VOYAGER
        ev_oac_da = evaluate(_go('bypass_clti', q={'med_warfarin': True, 'med_aspirin': False, 'allergy_chips': ['nsaid', 'clopidogrel_intol']}, s={'post_antithrombotic_plan': 'oac_sapt'}), today=TODAY)
        self.assertTrue(any('ალერგია' in rf for rf in ev_oac_da['red_flags']))
        self.assertNotIn('ერთი ანტიაგრეგანტი', ev_oac_da['post_antithrombotic_label'])
        ev_sapt_st = evaluate(_go('bypass_clti', q={'recent_stent': True, 'med_aspirin': False, 'med_ticagrelor': True, 'allergy_chips': ['nsaid', 'clopidogrel_intol']}, s={'post_antithrombotic_plan': 'sapt'}), today=TODAY)
        self.assertTrue(any('სტენტ' in rf and 'SAPT' in rf for rf in ev_sapt_st['red_flags']))
        self.assertIn('უკუნაჩვენებია', ev_sapt_st['post_antithrombotic_label'])
        ev_voy_st = evaluate(_go('bypass_clti', q={'recent_stent': True, 'med_aspirin': True, 'med_clopidogrel': True}, s={'post_antithrombotic_plan': 'voyager'}), today=TODAY)
        self.assertIn('DAPT', ev_voy_st['post_antithrombotic_label'])

        # P-S1-6 (Combos A-S1-7 & Clin A-S2-1): EVLA Warfarin STOP-lock & dos_inr > 3.0 & NO tone='red' rows in keep_rows
        p_ev_st = _go('evla', q={'med_warfarin': True}, s={'ven_duplex_status': 'acute_dvt', 'inr': 2.5})
        ev_ev_st = evaluate(p_ev_st, today=TODAY)
        vka_st = next(r for r in ev_ev_st['med_schedule'] if r['drug'] == 'ვარფარინი')
        self.assertIn('STOP', vka_st['date_plan'])
        p_ev_hi = _go('evla', q={'med_warfarin': True}, s={'inr': 2.5, 'dos_inr': 3.8, 'med_hold_agreed': False})
        ev_ev_hi = evaluate(p_ev_hi, today=TODAY)
        self.assertEqual(next(r for r in ev_ev_hi['med_schedule'] if r['drug'] == 'ვარფარინი')['tone'], 'red')
        self.assertFalse(any('მოუხსნელადაც' in af for af in ev_ev_hi['amber_flags']))
        self.assertFalse(any('INR > 1.35' in rf for rf in ev_ev_hi['red_flags']))
        script_hi = nurse_plan.build_phone_med_script(p_ev_hi, ev_ev_hi, TODAY)
        self.assertEqual([r for r in script_hi['keep_rows'] if r['tone'] == 'red'], [])
        p_cea_ti = _go('cea_symp', q={'cea_symp_event': True, 'med_ticagrelor': True})
        script_ti = nurse_plan.build_phone_med_script(p_cea_ti, evaluate(p_cea_ti, today=TODAY), TODAY)
        self.assertEqual([r for r in script_ti['keep_rows'] if r['tone'] == 'red'], [])

        # P-S1-7 (Combos A-S1-8): ALI (< 2 weeks) on *_claud suppresses IC-only Class III STOPs & adds wait/anamnesis
        ev_ali_btk = evaluate(_go('pta_claud', q={'abf_duration': '< 2 კვირა'}, s={'pta_lesion_site': 'infrapopliteal'}), today=TODAY)
        self.assertFalse(ev_ali_btk['has_hard_contraindication'])
        ev_ali_df = evaluate(_go('bypass_claud', q={'abf_duration': '< 2 კვირა'}, s={'pad_runoff_status': 'desert_foot', 'pad_gsv_status': 'inadequate', 'alt_vein_mapped': 'none_prosthetic'}), today=TODAY)
        self.assertFalse(ev_ali_df['has_hard_contraindication'])
        p_ali_w = _go('bypass_claud', q={'abf_duration': '< 2 კვირა'}, s={'planned_surgery_date': '2026-12-20'})
        ev_ali_w = evaluate(p_ali_w, today=TODAY)
        self.assertTrue(any('ALI' in rf or 'მწვავე' in rf for rf in ev_ali_w['red_flags'] + ev_ali_w['amber_flags']))
        self.assertIn('ALI', generate_anamnesis(p_ali_w, ev_ali_w))

        # P-S1-8 (Combos A-S1-9): Unfilled critical signals (active_infection, PTA wet infection ABx, pacemaker bradycardia)
        ev_inf = evaluate(_go('open', q={'filled': False, 'active_infection': True, 'inf_chips': ['fever_resp']}), today=TODAY)
        self.assertTrue(any('ინფექც' in rf or 'სიცხე' in rf for rf in ev_inf['red_flags']))
        ev_pta_w = evaluate(_go('pta_clti', q={'filled': False, 'abf_wet_infection': True}), today=TODAY)
        self.assertIn('dos_abx_ready', [c['key'] for c in ev_pta_w['dos_checks']])
        ev_pm_br = evaluate(_go('open', q={'filled': False, 'pacemaker': True}, s={'hr': 50}), today=TODAY)
        self.assertTrue(any('ბრადიკარდია' in rf or '50' in rf for rf in ev_pm_br['red_flags']))

    def test_clin_s2_pta_dapt_hepatic_renal_tsh_evar_lido_antiseptic_and_registry(self):
        # Clin A-S2-2: PTA dapt task NEVER shows VOYAGER PAD when contraindicated (dialysis/mech_valve/stent)
        for q_c in ({'on_dialysis': True}, {'cad_chips': ['mech_valve'], 'med_warfarin': True}, {'recent_stent': True, 'med_clopidogrel': True}):
            ev_pd = evaluate(_go('pta_clti', q=q_c), today=TODAY)
            dapt_t = next(t for t in ev_pd['tasks'] if t['key'] == 'dapt')
            self.assertNotIn('VOYAGER PAD', dapt_t['label'], f'PTA task recommended VOYAGER PAD for {q_c}')
            if 'VOYAGER' in dapt_t['label']:
                self.assertIn('აკრძალულია', dapt_t['label'], f'PTA task leaked VOYAGER for {q_c}')

        # Clin A-S2-4: Warfarin+HIT+vte_recent_3m+liver_3x & ven_hormones on dialysis
        ev_wh = evaluate(_go('open', q={'med_warfarin': True, 'allergy_chips': ['hit_heparin'], 'ven_dvt_history': True, 'ven_dvt_chips': ['vte_recent_3m']}, s={'liver_status': '3x'}), today=TODAY)
        self.assertIn('-75%', next(r for r in ev_wh['med_schedule'] if r['drug'] == 'ვარფარინი')['rule'])
        ev_vh = evaluate(_go('evla', sex='F', age=40, q={'on_dialysis': True, 'ven_hormones': True}), today=TODAY)
        self.assertNotIn('LMWH პროფილაქტიკა', next(r for r in ev_vh['med_schedule'] if 'ჰორმონ' in r['drug'])['rule'])

        # Clin A-S2-5: Non-aortic non-contrast CEA with tsh_status != pending NEVER says 'ღია აორტული'
        ev_tsh = evaluate(_go('cea_asymp', q={'thyroid_disease': True}, s={'tsh_status': 'euthyroid'}), today=TODAY)
        tsh_ro = next(r for r in ev_tsh['reflex_orders'] if r['key'] == 'tsh')
        self.assertNotIn('ღია აორტული', tsh_ro['trigger'])

        # Clin A-S2-6: EVAR & AAA tbd with lidocaine allergy do NOT recommend local anesthesia & emit red flag
        for sub in ('evar', 'tbd'):
            ev_el = evaluate(_go(sub, q={'allergy_chips': ['lidocaine']}), today=TODAY)
            self.assertNotIn('ადგილობრივი', ev_el['effective_anesth_label'])
            self.assertTrue(any('ლიდოკაინ' in rf for rf in ev_el['red_flags']))

        # Combos A-S2-7: Dual latex_chlorhex + allergy_iodine does NOT blindly order povidone-iodine
        ev_di = evaluate(_go('open', q={'allergy_chips': ['latex_chlorhex'], 'allergy_iodine': True}), today=TODAY)
        self.assertFalse(any('პოვიდონ-იოდის ანტისეპტიკი!' in af for af in ev_di['amber_flags']))
        self.assertFalse(any('პოვიდონ-იოდის შხაპი' in ro['action'] for ro in ev_di['reflex_orders']))

        # Combos A-S2-5: build_self_fill_highlights surfaces critical self-fill answers & generate_anamnesis works when self_filled=True, filled=False
        p_sfh = _go('open', q={'filled': False, 'self_filled': True, 'abf_wet_infection': True, 'ven_lidocaine_allergy': True, 'cad_chips': ['mech_valve'], 'med_chips': ['rivaroxaban_2_5']})
        hi_txt = ' | '.join(t for _, t in build_self_fill_highlights(p_sfh.questionnaire))
        for kw in ('სველი', 'ლიდოკაინ', 'მექანიკური სარქველი', 'რივაროქსაბანი 2.5'):
            self.assertIn(kw, hi_txt)
        self.assertTrue(generate_anamnesis(p_sfh, evaluate(p_sfh, today=TODAY)))

        # Clin A-S2-7: CEA rt-PA days 1-6 med_schedule maps ALL drugs to CEA_ACUTE_NEURO_THROMBOLYSIS
        ev_rtm = evaluate(_go('cea_symp', q={'cea_symp_event': True, 'cea_days_since_event': 2, 'cea_thrombolysis': True, 'med_aspirin': True, 'med_clopidogrel': True, 'med_ticagrelor': True, 'med_dabigatran': True}, s={'brain_imaging': 'normal_small'}), today=TODAY)
        for row in annotate_med_rows(ev_rtm['med_schedule']):
            if any(k in row['drug'] for k in ('ასპირინი', 'კლოპიდოგრელი', 'ტიკაგრელორი', 'დაბიგატრანი')):
                self.assertEqual(row['rule_meta']['id'], 'CEA_ACUTE_NEURO_THROMBOLYSIS', f"Misattributed {row['drug']}")

        # Clin A-S2-8: Routine metformin-only & single-drug EVLA amber do NOT cite MED_ANTITHROMBOTIC_CONFLICTS
        ev_met = evaluate(_go('open', q={'med_aspirin': False, 'metformin': True}, s={'med_hold_agreed': False}), today=TODAY)
        met_af = next(af for af in ev_met['amber_flags'] if 'შეათანხმეთ მიღების/შეჩერების' in af)
        self.assertNotEqual(match_rule_for_text(met_af).id, 'MED_ANTITHROMBOTIC_CONFLICTS')
        ev_evc = evaluate(_go('evla', q={'med_clopidogrel': True}, s={'med_hold_agreed': False}), today=TODAY)
        evc_af = next(af for af in ev_evc['amber_flags'] if 'EVLA შესაძლებელია' in af)
        self.assertNotEqual(match_rule_for_text(evc_af).id, 'MED_ANTITHROMBOTIC_CONFLICTS')

        # Clin A-S2-10: All 10 local-cutoff rules honestly tagged STATUS_LOCAL
        for rid in (
            'MED_CARDIO_VASOACTIVE_STEROID', 'REN_ELECTROLYTES_K_NA', 'VIT_HR_SPO2_METS',
            'VIT_BP_HYPOTENSION_SHOCK', 'LAB_HB_PBM_THRESHOLDS', 'SCORE_RENAL_CKDEPI_CG',
            'AAA_WAIT_TARGET_DAYS', 'MED_METFORMIN_RENAL_CONTRAST',
            'VEN_DUPLEX_ANATOMY_AND_CEAP', 'CEA_STENOSIS_THRESHOLDS',
        ):
            self.assertEqual(RULES_BY_ID[rid].status, STATUS_LOCAL, f'{rid} must be STATUS_LOCAL')

        # Combos A-S2-1 & A-S2-2: neuraxial_hard_blocked + acked never returns empty missing_list; DOM IDs exist; multi-DoS preserved
        p_nx = _go('phlebectomy', s={'anesthesia_plan': 'neuraxial', 'plt': 80})
        ev_nx0 = evaluate(p_nx, today=TODAY)
        p_nx.alerts_ack_at = timezone.now()
        p_nx.studies['alerts_ack_sig'] = ev_nx0['alerts_sig']
        miss_nx = nurse_plan.build_plain_georgian_missing_items(p_nx, evaluate(p_nx, today=TODAY), TODAY)
        self.assertTrue(miss_nx)
        p_mdos = _go('open', s={'dos_bp_sys': 200, 'dos_bp_dia': 115, 'dos_glucose': 18.0})
        miss_mdos = nurse_plan.build_plain_georgian_missing_items(p_mdos, evaluate(p_mdos, today=TODAY), TODAY)
        stop_lbls = ' | '.join(it['label'] for it in miss_mdos if it['kind'] == 'stop')
        self.assertIn('200', stop_lbls)
        self.assertIn('18', stop_lbls)
        for it in miss_mdos:
            self.assertNotIn(it['target_section'], ('nw-dos-card', 'nw-sec-header'))


class PreopStressFixesRound5HTTPTests(TestCase):
    """HTTP workflow invalidation, template consistency, and legacy self-fill lock regressions for Round-5."""

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.doc = User.objects.create_user(
            username='r5_doc', password='R5Password!1', first_name='ნინო', last_name='ექიმი', is_staff=True,
        )

    def setUp(self):
        self.client.force_login(self.doc)

    def test_w_s1_3_med_hold_invalidation_across_rule_and_no_opdate_shifts(self):
        # 1. Stage 1 creat 85 -> 310 on mech_valve + warfarin (LMWH -> IV UFH bridge) clears med_hold_agreed
        p1 = _go('open', q={'cad_chips': ['mech_valve'], 'med_warfarin': True}, s={'creat': 85, 'creat_unit': 'umol', 'med_hold_agreed': True})
        p1.save()
        self.client.post(reverse('preop-patient', args=[p1.pk]), {'section': 'stage1', 's1_partial': '1', 'creat': '310', 'creat_unit': 'umol'})
        p1.refresh_from_db()
        self.assertFalse(p1.studies.get('med_hold_agreed'))

        # 2. Self-fill adding mech_valve + HIT on pre-existing warfarin clears med_hold_agreed
        p2 = _go('open', q={'filled': False, 'nurse_verified': False, 'self_fill_submitted': False, 'med_warfarin': True}, s={'med_hold_agreed': True})
        p2.save()
        self.client.post(reverse('preop-self-fill', args=[make_self_fill_token(p2)]), {
            'med_warfarin': '1', 'cad_chips': ['mech_valve'], 'allergy_chips': ['hit_heparin'], 'mets_ok': '1',
        })
        p2.refresh_from_db()
        self.assertFalse(p2.studies.get('med_hold_agreed'))

        # 3. Stage 2 anesthesia_plan general -> neuraxial with NO planned_surgery_date clears med_hold_agreed
        p3 = _go('bypass_clti', q={'med_doac': True}, s={'anesthesia_plan': 'general', 'planned_surgery_date': '', 'med_hold_agreed': True})
        p3.save()
        self.client.post(reverse('preop-patient', args=[p3.pk]), {'section': 'stage2', 'anesthesia_plan': 'neuraxial', 'med_hold_agreed': '1'})
        p3.refresh_from_db()
        self.assertFalse(p3.studies.get('med_hold_agreed'))

        # 4. DoS subtherapeutic mech_valve dos_inr=1.3 clears BOTH approved_at AND med_hold_agreed
        p4 = _go('evla', q={'cad_chips': ['mech_valve'], 'med_warfarin': True}, s={'inr': 2.5, 'med_hold_agreed': True})
        p4.approved_at = timezone.now()
        p4.save()
        self.client.post(reverse('preop-patient', args=[p4.pk]), {'section': 'dos_vqi', 'dos_partial': '1', 'action': 'save_dos', 'dos_inr': '1.3'})
        p4.refresh_from_db()
        self.assertIsNone(p4.approved_at)
        self.assertFalse(p4.studies.get('med_hold_agreed'))

    def test_w_s2_3_4_6_and_f_s3_3_templates_dos_icu_and_legacy_selffill_lock(self):
        # W-S2-3: Classic patient.html does NOT collapse solo_neuraxial_anatomic_mismatch STOP alerts after ack
        p_solo = _go('cea_asymp', s={'anesthesia_plan': 'neuraxial'})
        p_solo.save()
        self.client.post(reverse('preop-patient', args=[p_solo.pk]), {'section': 'signoff', 'action': 'ack_alerts'})
        html_solo = self.client.get(reverse('preop-patient', args=[p_solo.pk])).content.decode('utf-8')
        self.assertNotIn('ქირურგმა გაეცნო', html_solo)
        self.assertNotIn('ყველა გაფრთხილება გაცნობილია', html_solo)

        # W-S2-4: Classic _tab_dos.html (dos_form_present=1) preserves icu_bed_booked=True when needs_icu_bed=False
        p_noicu = _go('evla', s={'icu_bed_booked': True})
        p_noicu.save()
        self.client.post(reverse('preop-patient', args=[p_noicu.pk]), {'section': 'dos_vqi', 'dos_form_present': '1', 'action': 'save_dos', 'dos_identity_consent': '1'})
        p_noicu.refresh_from_db()
        self.assertTrue(p_noicu.studies.get('icu_bed_booked'))

        # W-S2-6: passport.html shows unfilled warning when q.filled=False, shows L-arm DBP when bp_sys_l is None, highlights undiagnosed DM HbA1c>=6.5
        p_pp = _go('open', q={'filled': False, 'dm': False}, s={'bp_sys_l': None, 'bp_dia_l': 115, 'hba1c': 7.0})
        p_pp.save()
        html_pp = self.client.get(reverse('preop-passport', args=[p_pp.pk])).content.decode('utf-8')
        self.assertIn('კითხვარი ჯერ არ არის შევსებული', html_pp)
        self.assertIn('L —/115', html_pp)
        self.assertIn('class="bad">5.4 მმოლ/ლ · 7.0%', html_pp)

        # F-S3-3: Legacy self_filled=True, self_fill_submitted=False record is locked in PreopSelfFillView
        p_leg = _go('open', q={'filled': False, 'self_filled': True, 'self_fill_submitted': False, 'nurse_verified': False})
        p_leg.save()
        tok = make_self_fill_token(p_leg)
        r_post = self.client.post(reverse('preop-self-fill', args=[tok]), {'med_warfarin': '1'})
        self.assertEqual(r_post.status_code, 302)
        self.assertIn('locked=1', r_post['Location'])
