"""Stage 4 Round-12 adversarial regression tests (all 3 independent Round-11 audit findings)."""
import datetime
import json
import subprocess
from dataclasses import replace

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client, SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from ..models import BoardEntry, BoardPatient, Case, PreopPatient, StaffProfile
from ..preop import evaluate, make_self_fill_token, nurse_plan
from ..preop.anamnesis import build_self_fill_highlights
from ..preop.calculators import is_hb_unit_ambiguous, normalize_albumin_gl, normalize_hb_gl
from ..preop.case_import import detect_case_preop
from ..preop.meds_freetext import _detect_freetext_high_risk_meds, _positive_clauses_text
from ..preop.registry import REGISTRY_VERSION, RULES_BY_ID, match_rule_for_text, rule_value
from ..preop.utils import has_prior_stroke_or_tia
from ..preop.views.board import admit_preop_to_board, resolve_preop_for_board
from ..preop.views.parsers import can_enter_ready_queue, preop_admission_block_reason
from ..preop.vignettes import VIGNETTES_BY_ID, evaluate_vignette

TODAY = datetime.date(2026, 11, 26)


def _go(sub='open', side='', q=None, s=None, age=65, sex='M', **kw):
    pw = PreopPatient.SUBTYPE_PATHWAY[sub]
    if not side and pw in ('cea', 'pad_bypass', 'pad_pta', 'venous'):
        side = 'R'
    q_d = {'filled': True, 'nurse_verified': True, 'allergy_iodine_asked': True, 'mets_ok': True}
    if q:
        q_d.update(q)
    s_d = {
        'labs_date': TODAY.isoformat(),
        'planned_surgery_date': (TODAY + datetime.timedelta(days=10)).isoformat(),
        'bp_sys': 128, 'bp_dia': 76, 'hr': 72, 'spo2': 98,
        'weight_kg': 75, 'height_cm': 172,
        'hb': 135, 'plt': 220, 'creat': 84, 'inr': 1.0, 'glucose': 5.4, 'k': 4.2, 'na': 140,
        'ecg': 'normal', 'cxr': 'normal', 'cta_disc': True,
        'anesth_done': True, 'blood_reserved': True, 'evar_sized': True,
        'compression_ready': True, 'dapt_ready': True, 'decolonization_ready': True,
        'med_hold_agreed': True, 'icu_bed_booked': True, 'cardio_decision': 'cleared',
        'lvef': 60, 'aaa_mm': 56 if pw == 'aaa_infra' else None,
        'carotid_r': 75 if pw == 'cea' else None,
        'cea_cta_status': 'concordant' if pw == 'cea' else 'pending',
        'brain_imaging': 'normal_small' if pw == 'cea' else 'pending',
        'ven_duplex_status': 'deep_patent_reflux' if pw == 'venous' else 'pending',
        'abi_r': 0.50 if pw in ('abf', 'pad_bypass', 'pad_pta') else None,
        'pad_gsv_status': 'adequate' if pw == 'pad_bypass' else 'pending',
        'pad_runoff_status': 'popliteal_patent' if pw == 'pad_bypass' else 'pending',
    }
    if s:
        s_d.update(s)
    return PreopPatient(
        patient_name='სატესტო პაციენტი', age=age, sex=sex, side=side,
        op_subtype=sub, pathway=pw, started_on=TODAY,
        questionnaire=q_d, studies=s_d, **kw,
    )


class PreopStressFixesR12ClinicalEngineTests(SimpleTestCase):
    """Clin S1-01..S3-04, Combos S1-1..S3-4 (engine), Fuzz S2-1..S2-4."""

    def test_freetext_pregnancy_teratogens_and_compound_and_negation(self):
        # Clin S1-01: Georgian/EU pregnancy teratogen brand stems in other_meds
        for txt in ('ლიპრიმარი 20 მგ', 'liprimar 40mg', 'სტატინი', 'enap 10mg', 'პრესტანსი 5/5'):
            ev = evaluate(_go('bypass_clti', age=30, sex='F', q={'pregnant_possible': True, 'other_meds': txt}), today=TODAY)
            self.assertTrue(any('ორსულობ' in rf and ('ტერატოგენ' in rf or 'ნეფროტოქსიკ' in rf) for rf in ev['red_flags']), txt)
            self.assertTrue(any(r.get('contraindicated') and r['tone'] == 'red' for r in ev['med_schedule']), txt)

        # Clin S1-02: Compound 'და'/'and' negation MUST NOT erase active second-clause antithrombotics
        for txt, expected_key in (
            ('ასპირინი შეწყვეტილია და სვამს პლავიქსს', 'clopidogrel'),
            ('stopped aspirin and takes xarelto 20mg', 'doac'),
            ('არ სვამს ასპირინს და იღებს ელიქვისს 5მგ', 'doac'),
        ):
            self.assertIn(expected_key, _detect_freetext_high_risk_meds(txt)['matched_keys'], txt)
            ev = evaluate(_go('open', q={'other_meds': txt}), today=TODAY)
            self.assertTrue(any(r.get('unstructured') for r in ev['med_schedule']), txt)
            self.assertTrue(ev['has_blocking_unknown_state'], txt)

        # Clin S2-05: Negated allergy does not block GO; negated statin does not suppress Class I missing-statin amber
        for neg_alg in ('არა', 'NKDA', 'ალერგია არ მაქვს', 'no allergies'):
            self.assertEqual(_positive_clauses_text(neg_alg), '', neg_alg)
            ev_ok = evaluate(_go('open', q={'med_chips': ['statin'], 'allergy_text': neg_alg}), today=TODAY)
            self.assertFalse(any(u['key'] == 'unconfirmed_freetext_allergy' for u in ev_ok['unknown_state_flags']), neg_alg)
            self.assertNotIn(neg_alg, ev_ok['allergy_summary'])
            self.assertFalse(any(neg_alg in h['val'] for h in build_self_fill_highlights(ev_ok['q'], 'open')))
        ev_nst = evaluate(_go('cea_asymp', q={'other_meds': 'ატორვასტატინი შეწყვეტილია'}), today=TODAY)
        self.assertTrue(any('სტატინ' in af for af in ev_nst['amber_flags']))

    def test_pregnancy_mech_valve_hit_and_gi_bleed_postop_consistency(self):
        # Combos S1-1 + Clin S2-02: Pregnancy + Mech Valve + HIT never recommends LMWH/UFH on ANY postop surface
        for sub in ('open', 'pta_clti', 'bypass_clti', 'evla'):
            for plan in ('auto', 'oac_sapt', 'sapt', 'dapt', 'voyager'):
                ev = evaluate(
                    _go(sub, age=32, sex='F',
                        q={'pregnant_possible': True, 'cad': True, 'cad_chips': ['mech_valve'],
                           'med_warfarin': True, 'allergy_chips': ['hit_heparin']},
                        s={'post_antithrombotic_plan': plan, 'inr': 2.5}),
                    today=TODAY,
                )
                postop = ev['surgical_brief']['postop_antithrombotic']
                heparin = ev['surgical_brief']['heparin_plan']
                self.assertIn('მეან-გინეკოლოგ', heparin, f'{sub}/{plan}')
                self.assertIn('მეან-გინეკოლოგ', postop, f'{sub}/{plan}')
                self.assertNotIn('VKA vs LMWH/UFH', postop, f'{sub}/{plan}')
                self.assertNotIn('VKA vs LMWH/UFH', ev['post_antithrombotic_label'], f'{sub}/{plan}')
                if sub == 'pta_clti':
                    dapt_t = next((t for t in ev['tasks'] if t['key'] == 'dapt'), None)
                    if dapt_t:
                        self.assertIn('HIT', dapt_t['label'])
                        self.assertIn('ორსულობა', dapt_t['label'])

        # Pregnancy + Mech Valve (no HIT): Caprini & explicit oac_sapt include OB-GYN trimester warning
        ev_ven = evaluate(
            _go('evla', age=32, sex='F',
                q={'ven_pregnant': True, 'cad': True, 'cad_chips': ['mech_valve'], 'med_warfarin': True},
                s={'inr': 2.6}),
            today=TODAY,
        )
        self.assertIn('მეან-გინეკოლოგ', ev_ven['caprini_advice'])
        # Combos S3-4: Pregnant EVLA + warfarin must NOT emit ESVS continue-warfarin amber reassurance
        self.assertFalse(any('ESVS 2022-ით მოხსნა აუცილებელი არ არის' in af for af in ev_ven['amber_flags']))

        ev_oac = evaluate(
            _go('bypass_clti', age=32, sex='F',
                q={'pregnant_possible': True, 'cad': True, 'cad_chips': ['mech_valve'], 'med_warfarin': True},
                s={'post_antithrombotic_plan': 'oac_sapt', 'inr': 2.5}),
            today=TODAY,
        )
        self.assertIn('მეან-გინეკოლოგ', ev_oac['post_antithrombotic_label'])

        # Combos S1-3 + Clin S2-03: Active GI bleed overrides postop recommendations on all arterial pathways & explicit plans
        for sub, plan in (('open', 'auto'), ('cea_asymp', 'auto'), ('bypass_clti', 'dapt'), ('pta_clti', 'voyager')):
            ev_gi = evaluate(
                _go(sub, q={'med_aspirin': True}, s={'gastro_result': 'acute_ulcer', 'post_antithrombotic_plan': plan}),
                today=TODAY,
            )
            self.assertTrue(ev_gi['active_gi_bleed'], sub)
            self.assertIn('ენდოსკოპიურ ჰემოსტაზამდე', ev_gi['surgical_brief']['postop_antithrombotic'], f'{sub}/{plan}')
            if plan == 'voyager':
                self.assertTrue(any('VOYAGER' in rf for rf in ev_gi['red_flags']))
            if sub == 'pta_clti':
                dapt_t = next((t for t in ev_gi['tasks'] if t['key'] == 'dapt'), None)
                self.assertIsNotNone(dapt_t)
                self.assertIn('ენდოსკოპიურ ჰემოსტაზამდე', dapt_t['label'])

    def test_mech_valve_bridging_p2y12_undated_and_egd_remission_parity(self):
        # Combos S1-2: Mech valve (+HIT / +CrCl<30 / plain) warfarin date_plan, phone script & patient guide carry bridging
        for label, extra_q, extra_s, needle_phone, needle_guide in (
            ('plain', {'cad': True, 'cad_chips': ['mech_valve'], 'med_warfarin': True}, {'inr': 2.5}, 'LMWH ხიდი', 'დამცავი ნემს'),
            ('hit', {'cad': True, 'cad_chips': ['mech_valve'], 'med_warfarin': True, 'allergy_chips': ['hit_heparin']}, {'inr': 2.5}, 'ბივალირუდინი', 'კლინიკაში წინასწარ წოლა'),
            ('ckd', {'cad': True, 'cad_chips': ['mech_valve'], 'med_warfarin': True}, {'inr': 2.5, 'creat': 320, 'weight_kg': 60}, 'IV UFH ხიდი', 'კლინიკაში წინასწარ წოლა'),
        ):
            p = _go('bypass_claud', q=extra_q, s=extra_s)
            ev = evaluate(p, today=TODAY)
            vka_row = next(r for r in ev['med_schedule'] if 'ვარფარინი' in r['drug'])
            self.assertIn(needle_phone, vka_row['date_plan'], label)
            ph = nurse_plan.build_phone_med_script(p, ev, TODAY)['stop_rows']
            gd = nurse_plan.build_patient_plain_explanation(p, ev, TODAY)['stop_meds']
            self.assertTrue(any(needle_phone in r['spoken'] for r in ph), f'{label}: {ph}')
            self.assertTrue(any(needle_guide in r['spoken'] for r in gd), f'{label}: {gd}')

        # Clin S2-01: EGD remission parity for Cilostazol and VOYAGER PAD
        ev_cilo = evaluate(
            _go('bypass_claud', q={'ulcer_active_symp': True, 'med_chips': ['cilostazol']}, s={'gastro_result': 'remission'}),
            today=TODAY,
        )
        cilo_row = next(r for r in ev_cilo['med_schedule'] if 'ცილოსტაზოლი' in r['drug'])
        self.assertFalse(bool(cilo_row.get('contraindicated')))
        ev_voy = evaluate(
            _go('bypass_clti', q={'abf_rest_pain': True, 'ulcer_active_symp': True, 'med_aspirin': True, 'med_chips': ['statin', 'rivaroxaban_2_5']},
                s={'gastro_result': 'remission', 'post_antithrombotic_plan': 'voyager'}),
            today=TODAY,
        )
        self.assertFalse(any('VOYAGER' in rf for rf in ev_voy['red_flags']))

        # Clin S2-04: Undated contraindicated P2Y12 never emits empty date_plan, names ESC Class III OAC conflict, and avoids CEA/PTA dup-P2Y12 contradiction
        ev_oac_t = evaluate(_go('open', q={'med_warfarin': True, 'med_ticagrelor': True}, s={'planned_surgery_date': ''}), today=TODAY)
        t_row = next(r for r in ev_oac_t['med_schedule'] if 'ტიკაგრელორი' in r['drug'])
        self.assertTrue(t_row.get('contraindicated'))
        self.assertIn('შეწყვიტეთ დაუყოვნებლივ', t_row['date_plan'])
        self.assertIn('ESC Class III', t_row['rule'])

        for sub in ('cea_symp', 'pta_clti'):
            ev_dup = evaluate(_go(sub, q={'med_clopidogrel': True, 'med_ticagrelor': True}, s={'planned_surgery_date': ''}), today=TODAY)
            c_row = next(r for r in ev_dup['med_schedule'] if 'კლოპიდოგრელი' in r['drug'])
            self.assertTrue(c_row.get('contraindicated'), sub)
            self.assertIn('შეათანხმეთ დე-ესკალაცია', c_row['date_plan'], sub)
            self.assertNotIn('გრძელდება უწყვეტად', c_row['rule'], sub)

    def test_unit_equivalence_monotonicity_invalid_dates_and_elective_pregnancy_stop(self):
        # Fuzz S2-1: Hb 3.7 g/dL == 37 g/L unit parity & 40 -> 38 g/L monotonicity & 25/29 ambiguous None
        self.assertTrue(is_hb_unit_ambiguous(25))
        self.assertIsNone(normalize_hb_gl(25))
        self.assertFalse(is_hb_unit_ambiguous(37))
        ev_37_gdl = evaluate(_go('open', s={'hb': 3.7}), today=TODAY)
        ev_37_gl = evaluate(_go('open', s={'hb': 37}), today=TODAY)
        for k in ('hb_gl', 'hb_unit_ambiguous', 'auto_asa', 'alerts_sig'):
            self.assertEqual(ev_37_gdl[k], ev_37_gl[k], k)
        self.assertEqual(ev_37_gdl['ariscat']['score'], ev_37_gl['ariscat']['score'])
        ev_40 = evaluate(_go('open', s={'hb': 40}), today=TODAY)
        ev_38 = evaluate(_go('open', s={'hb': 38}), today=TODAY)
        self.assertEqual(ev_40['auto_asa'], ev_38['auto_asa'])
        self.assertGreaterEqual(ev_38['ariscat']['score'], ev_40['ariscat']['score'])

        # Fuzz S2-2: Albumin 0.8 g/dL == 8.0 g/L AND 6.5 g/dL == 65.0 g/L
        self.assertEqual(normalize_albumin_gl(0.8), 8.0)
        self.assertEqual(normalize_albumin_gl(6.5), 65.0)
        ev_a1, ev_a2 = evaluate(_go('open', s={'albumin': 6.5}), today=TODAY), evaluate(_go('open', s={'albumin': 65.0}), today=TODAY)
        self.assertEqual(ev_a1['alerts_sig'], ev_a2['alerts_sig'])
        self.assertEqual(ev_a1['clearance_seal']['code'], ev_a2['clearance_seal']['code'])

        # Fuzz S2-3 & S2-4: Invalid stroke/date inputs never yield false prasugrel contra or false GO
        for bad_q in ({'cea_days_since_event': False}, {'cea_days_since_event': 'abc'}, {'cea_days_since_event': -5}, {'cea_event_date': 'not-a-date'}):
            self.assertFalse(has_prior_stroke_or_tia(None, q=bad_q, s={}), bad_q)
        for sub, extra_q, extra_s in (
            ('open', {}, {'planned_surgery_date': 'garbage-date'}),
            ('open', {}, {'cta_date': TODAY.isoformat(), 'planned_surgery_date': (TODAY - datetime.timedelta(days=2)).isoformat()}),
            ('cea_symp', {'cea_days_since_event': 'abc'}, {}),
            ('cea_symp', {'cea_days_since_event': -5}, {}),
        ):
            ev_b = evaluate(_go(sub, q=extra_q, s=extra_s), today=TODAY)
            self.assertNotEqual(ev_b['clearance_seal']['code'], 'go', f'{sub}/{extra_q}/{extra_s}')
            self.assertFalse(ev_b['workup_ready'])
            self.assertTrue(any(u['key'] == 'implausible_values' for u in ev_b['unknown_state_flags']), f'{sub}/{extra_q}/{extra_s}')

        # Combos S2-2: Elective fluoroscopic/arterial pregnancy -> STOP + stop_locked; urgent ALI/CLTI stays non-STOP
        for sub in ('evar', 'tbd', 'open', 'cea_asymp'):
            ev_ep = evaluate(_go(sub, age=32, sex='F', q={'pregnant_possible': True, 'med_clopidogrel': True}), today=TODAY)
            self.assertEqual(ev_ep['clearance_seal']['code'], 'stop', sub)
            self.assertTrue(ev_ep['has_hard_contraindication'], sub)
            clop = next(r for r in ev_ep['med_schedule'] if 'კლოპიდოგრელი' in r['drug'])
            self.assertIn('STOP — თვითნებურად არ შეწყვიტოთ!', clop['date_plan'], sub)
            self.assertNotIn('ბოლო:', clop['date_plan'], sub)
        ev_urg = evaluate(_go('bypass_clti', age=32, sex='F', q={'pregnant_possible': True, 'abf_rest_pain': True}), today=TODAY)
        self.assertFalse(ev_urg['has_hard_contraindication'])

        # Combos S3-3: PAD pseudonormalization discordance warning (normal ABI + critical TP/TBI)
        ev_pn = evaluate(_go('bypass_clti', q={'abf_rest_pain': True}, s={'abi_r': 1.05, 'tp_r': 22}), today=TODAY)
        self.assertTrue(any('ჰემოდინამიკური მაჩვენებლების შეუსაბამობა' in af and 'კალციფიკაცია' in af for af in ev_pn['amber_flags']))

        # Combos S3-2 (diagnosis_text): venous CEAP ignored when filled=False
        p_unf = PreopPatient(pathway='venous', op_subtype='evla', side='R', questionnaire={'filled': False, 'ven_ceap': 'C6'})
        self.assertNotIn('C6', p_unf.diagnosis_text)

    def test_postponement_plain_guide_jargon_registry_and_vignette_mutations(self):
        # Clin S2-06, S3-01 & Fuzz S2-4: postponement catches static cea_days_since_event, EVLA warfarin, and cta_date > cur_dt
        p_cea = _go('cea_symp', q={'cea_symp_event': True, 'cea_days_since_event': 8}, s={
            'prev_planned_surgery_date': (TODAY + datetime.timedelta(days=2)).isoformat(),
            'planned_surgery_date': (TODAY + datetime.timedelta(days=14)).isoformat(),
        })
        imp_cea = nurse_plan.detect_postponement_impacts(p_cea, evaluate(p_cea, today=TODAY), TODAY)
        self.assertTrue(any(i['tone'] == 'danger' and '14-დღიანი' in i['text'] for i in imp_cea['impacts']))

        p_evla = _go('evla', q={'med_warfarin': True}, s={
            'inr': 2.4,
            'prev_planned_surgery_date': (TODAY + datetime.timedelta(days=5)).isoformat(),
            'planned_surgery_date': (TODAY + datetime.timedelta(days=10)).isoformat(),
        })
        imp_evla = nurse_plan.detect_postponement_impacts(p_evla, evaluate(p_evla, today=TODAY), TODAY)
        self.assertTrue(any('ვარფარინ' in i['text'] for i in imp_evla['impacts']))

        p_cta = _go('open', s={
            'cta_date': TODAY.isoformat(),
            'prev_planned_surgery_date': (TODAY + datetime.timedelta(days=5)).isoformat(),
            'planned_surgery_date': (TODAY - datetime.timedelta(days=2)).isoformat(),
        })
        imp_cta = nurse_plan.detect_postponement_impacts(p_cta, evaluate(p_cta, today=TODAY), TODAY)
        self.assertTrue(any(i['tone'] == 'danger' and 'CTA' in i['text'] for i in imp_cta['impacts']))

        # Combos S3-1 & Clin S3-02: Patient plain-Georgian guide scrubs clinician prompts & abbreviations
        for p_case in (
            _go('open', q={'med_doac': True, 'med_dabigatran': True, 'metformin': True, 'med_chips': ['sglt2', 'glp1', 'ccb', 'ppi']}, s={'creat': None}),
            _go('evla', q={'metformin': True, 'med_chips': ['steroid']}, s={'planned_surgery_date': ''}),
            _go('bypass_clti', age=30, sex='F', q={'pregnant_possible': True, 'other_meds': 'ლიპრიმარი 20 მგ, ენაპი 10 მგ'}),
        ):
            gd = nurse_plan.build_patient_plain_explanation(p_case, evaluate(p_case, today=TODAY), TODAY)
            visible_parts = list(gd['arrival_items']) + [gd['fasting_plain'], gd['emergency_sign']]
            for grp in ('stop_meds', 'caution_meds', 'urgent_meds', 'keep_meds'):
                for row in gd[grp]:
                    visible_parts.extend([row['drug'], row['spoken']])
            blob = ' | '.join(visible_parts)
            for forbidden in ('შეიყვანეთ კრეატინინი', 'შეიყვანეთ CrCl', 'შეიყვანეთ თირკმლის', '(ტექსტიდან)', 'ESUR', 'FDA', 'eGFR', 'Assoc.', 'EVLA', '(DOAC)', '(SGLT2)', '(GLP-1)', '(PPI)', 'CCB', '(CTA)', '(CPAP)'):
                self.assertNotIn(forbidden, blob, f'Leaked {forbidden} in {blob[:240]}')

        # Clin S2-07, S3-03, S3-04: Registry v1.2.11+ citations, rule_value() thresholds & AAA rapid-growth attribution
        self.assertGreaterEqual(tuple(int(x) for x in REGISTRY_VERSION.split('.')), (1, 2, 11))
        self.assertIn('Section 10.1', RULES_BY_ID['AAA_SYMPTOMATIC_OR_MYCOTIC'].section)
        self.assertIn('Section 10.5', RULES_BY_ID['AAA_YOUNG_AGE_GENETIC'].section)
        self.assertIn('Rec 26', RULES_BY_ID['AAA_POPLITEAL_SCREEN'].section)
        self.assertIn('Section 6.8', RULES_BY_ID['VIT_BP_SEVERE_HTN'].section)
        self.assertIn('Section 5.3.1.2', RULES_BY_ID['MED_RECENT_STENT_DAPT'].section)
        self.assertIn('Global Vascular Guidelines on CLTI', RULES_BY_ID['ABF_ANATOMY_AND_INFECTION'].source)
        self.assertEqual((rule_value('REN_CKD_NEPHRO_HYDRATION')['min_ml'], rule_value('REN_CKD_NEPHRO_HYDRATION')['max_ml']), (15, 250))
        self.assertEqual(rule_value('SCORE_RENAL_CKDEPI_CG')['cg_abw_bmi'], 30.0)
        self.assertEqual(rule_value('LAB_UNIT_AUTO_CONVERSION')['plt_ul_ge'], 3000)

        ev_rg = evaluate(_go('open', s={'aaa_mm': 52, 'aaa_morphology': 'rapid_growth'}), today=TODAY)
        rg_af = next(af for af in ev_rg['amber_flags'] if 'სწრაფი ზრდის დინამიკა' in af)
        self.assertEqual(match_rule_for_text(rg_af).id, 'AAA_SYMPTOMATIC_OR_MYCOTIC')

        # Clin S2-08: Non-vacuous mutation proof on V19, V26, V37, V38
        for vid in ('V19_PAD_VOYAGER_SAFE', 'V26_VEN_EVLA_OBESE_LIDOCAINE_CAP', 'V37_POLYALLERGY_BETALACTAM_LATEX', 'V38_CIED_ICD_MAGNET_WARNING'):
            self.assertTrue(evaluate_vignette(VIGNETTES_BY_ID[vid])['passed'], vid)
        v19_mut = replace(VIGNETTES_BY_ID['V19_PAD_VOYAGER_SAFE'], q=VIGNETTES_BY_ID['V19_PAD_VOYAGER_SAFE'].q + (('neuro', True),))
        self.assertFalse(evaluate_vignette(v19_mut)['passed'])
        v26_mut = replace(VIGNETTES_BY_ID['V26_VEN_EVLA_OBESE_LIDOCAINE_CAP'], s=tuple((k, 50 if k == 'weight_kg' else v) for k, v in VIGNETTES_BY_ID['V26_VEN_EVLA_OBESE_LIDOCAINE_CAP'].s))
        self.assertFalse(evaluate_vignette(v26_mut)['passed'])
        v37_mut = replace(VIGNETTES_BY_ID['V37_POLYALLERGY_BETALACTAM_LATEX'], q=tuple((k, ['latex_chlorhex'] if k == 'allergy_chips' else v) for k, v in VIGNETTES_BY_ID['V37_POLYALLERGY_BETALACTAM_LATEX'].q))
        self.assertFalse(evaluate_vignette(v37_mut)['passed'])
        v38_mut = replace(VIGNETTES_BY_ID['V38_CIED_ICD_MAGNET_WARNING'], s=tuple((k, 'reprogram_async' if k == 'cied_plan' else v) for k, v in VIGNETTES_BY_ID['V38_CIED_ICD_MAGNET_WARNING'].s))
        self.assertFalse(evaluate_vignette(v38_mut)['passed'])


class PreopStressFixesR12HttpAndJsTests(TestCase):
    """Fuzz S1-1..S3-3, Combos S2-1..S3-2 HTTP, Board, Self-Fill, RBAC & Live Node.js tests."""

    def setUp(self):
        User = get_user_model()
        self.doc = User.objects.create_user(username='r12_doc', password='R12DocPassword!1')
        StaffProfile.objects.create(user=self.doc)
        self.nurse = User.objects.create_user(username='r12_nurse', password='R12NursePassword!1')
        self.c_doc = Client()
        self.c_doc.force_login(self.doc)
        self.c_nurse = Client()
        self.c_nurse.force_login(self.nurse)

    def _save_go(self, sub='open', **kw):
        p = _go(sub, **kw)
        p.surgeon = self.doc
        p.visit_date = timezone.localdate()
        p.started_on = timezone.localdate()
        p.studies['labs_date'] = timezone.localdate().isoformat()
        p.studies['planned_surgery_date'] = (timezone.localdate() + datetime.timedelta(days=10)).isoformat()
        p.save()
        return p

    def test_rbac_no_self_escalation_and_cea_case_import_side_guard(self):
        # Fuzz S1-1: Non-doctor nurse cannot self-assign as surgeon or signoff/approve/ack_alerts
        p = self._save_go('open', q={'med_chips': ['statin']})
        self.c_nurse.post(reverse('preop-patient', args=[p.pk]), {
            'section': 'header', 'patient_name': p.patient_name, 'op_subtype': 'open', 'surgeon': str(self.nurse.pk),
        })
        p.refresh_from_db()
        self.assertEqual(p.surgeon_id, self.doc.pk)

        # Even if DB has surgeon=nurse, _save_signoff strictly enforces is_doctor_user
        p.surgeon = self.nurse
        p.save(update_fields=['surgeon'])
        self.c_nurse.post(reverse('preop-patient', args=[p.pk]), {'section': 'signoff', 'action': 'approve', 'move_ready': '1'})
        p.refresh_from_db()
        self.assertIsNone(p.approved_at)

        # Create / Nurse Register / Case transfer never assign non-doctor nurse as surgeon
        self.c_nurse.post(reverse('preop-new'), {'patient_name': 'ტესტ ნერსი', 'op_subtype': 'open', 'surgeon': str(self.nurse.pk)})
        self.assertIsNone(PreopPatient.objects.get(patient_name='ტესტ ნერსი').surgeon_id)
        self.c_nurse.post(reverse('preop-nurse-register'), {'patient_name': 'რეგ ნერსი', 'op_subtype': 'open', 'surgeon': str(self.nurse.pk)})
        self.assertIsNone(PreopPatient.objects.get(patient_name='რეგ ნერსი').surgeon_id)

        # Fuzz S2-5: Conference Case transfer & sync=1 never set side='B' on CEA
        case_cea_b = Case.objects.create(
            patient_name='ბილატ კაროტიდი', age=68, sex='M', side='B',
            diagnosis='ორმხრივი ICA სტენოზი 80%', proposal='კაროტიდული ენდარტერექტომია (CEA)',
            created_by=self.nurse,
        )
        self.assertEqual(detect_case_preop(case_cea_b), ('cea_asymp', ''))
        self.c_nurse.post(reverse('preop-new'), {'case': case_cea_b.pk})
        p_cb = PreopPatient.objects.get(case=case_cea_b)
        self.assertEqual(p_cb.side, '')
        self.assertIsNone(p_cb.surgeon_id)
        self.c_doc.post(reverse('preop-new'), {'case': case_cea_b.pk, 'sync': '1'})
        p_cb.refresh_from_db()
        self.assertEqual(p_cb.side, '')

    def test_board_no_same_name_hijack_and_admission_outcome_gates(self):
        # Fuzz S1-2: Unlinked active BoardPatient with same name is NEVER hijacked or mislinked
        bp_unlinked = BoardPatient.objects.create(
            name='დავით მსგავსი', bed='Ward-5', diagnosis='მწვავე თრომბოზი (სასწრაფო მიღება)',
            unit=BoardPatient.WARD, doctor=self.doc, created_by=self.doc,
        )
        p_diff = self._save_go('evla', side='R')
        p_diff.patient_name = 'დავით მსგავსი'
        p_diff.save(update_fields=['patient_name'])
        self.assertIsNone(resolve_preop_for_board(bp_unlinked))
        bp_new, created, _ = admit_preop_to_board(p_diff, self.doc, unit=BoardPatient.WARD, bed='Ward-9')
        self.assertTrue(created)
        self.assertNotEqual(bp_new.pk, bp_unlinked.pk)
        bp_unlinked.refresh_from_db()
        self.assertEqual(bp_unlinked.bed, 'Ward-5')

        # Combos S2-1 & Fuzz S2-7: bad_med / elapsed_hold / blocking_unknown / stale_labs block admit, complete_and_board & operated (without leaking operated_date)
        p_bad = self._save_go('open', q={'med_doac': True}, s={'creat': 350, 'weight_kg': 55})
        self.assertTrue(bool(preop_admission_block_reason(evaluate(p_bad), p_bad.studies)))
        self.c_doc.post(reverse('preop-admit-board', args=[p_bad.pk]), {'bed': '2'})
        p_bad.refresh_from_db()
        self.assertIsNone(p_bad.studies.get('board_patient_id'))

        self.c_doc.post(reverse('preop-patient', args=[p_bad.pk]), {
            'section': 'dos_vqi', 'dos_form_present': '1',
            'case_outcome_status': 'operated', 'operated_date': timezone.localdate().isoformat(),
        })
        p_bad.refresh_from_db()
        self.assertEqual(p_bad.studies.get('case_outcome_status'), 'scheduled')
        self.assertEqual(p_bad.studies.get('operated_date') or '', '')

        # Fuzz S2-6: cancelled_medical / operated patients cannot be approved or moved to someday:ready
        p_can = self._save_go('open', q={'med_chips': ['statin']})
        p_can.studies['case_outcome_status'] = 'cancelled_medical'
        p_can.save(update_fields=['studies'])
        self.c_doc.post(reverse('preop-patient', args=[p_can.pk]), {'section': 'signoff', 'action': 'approve', 'move_ready': '1'})
        p_can.refresh_from_db()
        self.assertIsNone(p_can.approved_at)
        self.assertFalse(can_enter_ready_queue(p_can, evaluate(p_can)))
        p_can.approved_at = timezone.now()
        p_can.save(update_fields=['approved_at'])
        self.c_doc.post(reverse('preop-move', args=[p_can.pk]), {'slot': 'someday:ready'})
        p_can.refresh_from_db()
        self.assertNotEqual(p_can.someday_list, 'ready')

        # Combos S1-1 Board Handoff: Pregnancy + Mech Valve + HIT has zero LMWH/UFH tasks
        p_pmh = self._save_go(
            'open', age=32, sex='F',
            q={'pregnant_possible': True, 'cad': True, 'cad_chips': ['mech_valve'], 'med_warfarin': True, 'allergy_chips': ['hit_heparin']},
            s={'inr': 2.5},
        )
        bp_pmh, _, _ = admit_preop_to_board(p_pmh, self.doc)
        tasks_blob = ' | '.join(BoardEntry.objects.filter(patient=bp_pmh, kind=BoardEntry.TASK).values_list('text', flat=True))
        self.assertNotIn('VKA vs LMWH/UFH', tasks_blob)
        self.assertIn('მეან-გინეკოლოგ', tasks_blob)

    def test_self_fill_wifi_preserve_done_session_buttock_roundtrip_and_niv_prefill(self):
        # Fuzz S1-3: First Self-Fill (filled=False) preserves conference WIfI ulcer & wet-infection flags
        case_wifi = Case.objects.create(
            patient_name='ვაიფაი პაციენტი', age=66, sex='M', side='R',
            diagnosis='მარჯვენა ქვემო კიდურის კრიტიკული იშემია CLTI, ტერფის ღრმა წყლული, ინფექცია',
            proposal='ფემორო-პოპლიტეალური შუნტირება',
            staging={'R': {'w': 2, 'i': 2, 'fi': 2}}, created_by=self.doc, doctor=self.doc,
        )
        self.c_doc.post(reverse('preop-new'), {'case': case_wifi.pk})
        p_w = PreopPatient.objects.get(case=case_wifi)
        self.assertFalse(p_w.questionnaire.get('filled'))
        self.assertTrue(p_w.questionnaire.get('abf_ulcer'))
        self.assertTrue(p_w.questionnaire.get('abf_wet_infection'))
        sf_url = reverse('preop-self-fill', args=[make_self_fill_token(p_w)])
        c_pat = Client()
        c_pat.post(sf_url, {'sf_present': '1', 'abf_buttock_pain': '1', 'mets_ok': '1'})
        p_w.refresh_from_db()
        self.assertTrue(p_w.questionnaire.get('filled'))
        self.assertTrue(p_w.questionnaire.get('abf_ulcer'))
        self.assertTrue(p_w.questionnaire.get('abf_wet_infection'))
        self.assertTrue(p_w.questionnaire.get('abf_buttock_pain'))

        # Combos S2-3: Nurse Tab Q renders and preserves abf_buttock_pain on pad_bypass & pad_pta
        r_tabq = self.c_doc.get(reverse('preop-patient', args=[p_w.pk]) + '?tab=q')
        self.assertContains(r_tabq, 'name="abf_buttock_pain"')
        self.c_doc.post(reverse('preop-patient', args=[p_w.pk]), {'section': 'questionnaire', 'abf_buttock_pain': '1', 'mets_ok': '1'})
        p_w.refresh_from_db()
        self.assertTrue(p_w.questionnaire.get('abf_buttock_pain'))

        # Fuzz S3-1: ?done=1 emergency_symptom banner is strictly session-gated
        p_em = PreopPatient.objects.create(patient_name='სასწრაფო ტესტ', age=70, sex='M', pathway='aaa_infra', op_subtype='open', surgeon=self.doc)
        sf_em = reverse('preop-self-fill', args=[make_self_fill_token(p_em)])
        c_sub, c_stranger = Client(), Client()
        r_sub = c_sub.post(sf_em, {'sf_present': '1', 'aaa_pain': '1'}, follow=True)
        self.assertContains(r_sub, 'სასწრაფო სამედიცინო გაფრთხილება')
        r_stranger = c_stranger.get(sf_em + '?done=1')
        self.assertNotContains(r_stranger, 'სასწრაფო სამედიცინო გაფრთხილება')

        # Combos S3-2: NIV-300-9/a prefill detects dm_insulin/metformin and positive allergy_text (ignoring negated 'NKDA')
        p_niv = self._save_go('open', q={'dm': False, 'dm_insulin': True, 'allergy_other': False, 'allergy_text': 'ცეფტრიაქსონი'})
        niv = self.c_doc.get(reverse('niv-cover') + f'?preop={p_niv.pk}').context['niv']
        self.assertTrue(niv.diabetes)
        self.assertTrue(niv.allergy)
        p_niv2 = self._save_go('open', q={'dm': False, 'allergy_other': False, 'allergy_text': 'NKDA'})
        niv2 = self.c_doc.get(reverse('niv-cover') + f'?preop={p_niv2.pk}').context['niv']
        self.assertFalse(niv2.allergy)

    def test_nodejs_live_guards_patient_and_nurse_js_bounds(self):
        # Fuzz S3-2 & S3-3: Live Node.js execution of unmodified preop-patient.js & preop-nurse.js
        js_dir = str(settings.BASE_DIR / 'static' / 'js')
        proc = subprocess.run(['node', '-e', _NODE_JS_GUARD_SCRIPT, js_dir], capture_output=True, text=True, check=True)
        out = json.loads(proc.stdout)
        p_res = out['pRes']
        self.assertIn('დაუჯერებელი PLT', p_res['plt_hi']['msg'])
        self.assertIn('დაუჯერებელი INR', p_res['dos_inr_lo']['msg'])
        self.assertIn('დაუჯერებელი INR', p_res['dos_inr_hi']['msg'])
        self.assertNotIn('დაუჯერებელი', p_res['fib_ok']['msg'])
        self.assertIn('დაუჯერებელი LDL', p_res['ldl_lo']['msg'])
        self.assertIn('დაუჯერებელი LDL', p_res['ldl_hi']['msg'])
        self.assertIn('დაუჯერებელი', p_res['dos_sbp_lo']['msg'])
        self.assertIn('დაუჯერებელი', p_res['dos_dbp_hi']['msg'])
        self.assertIn('მძიმე ანემია', p_res['hb_37']['msg'])
        self.assertIn('საეჭვო Hb', p_res['hb_25']['msg'])
        self.assertIn('მძიმე ანემია', out['vg'])
        self.assertIn('PLT ერთეული', out['vg'])
        self.assertIn('კრიტიკული კალიუმი', out['vg'])
        self.assertIn('დილის ზედა წნევა', out['dg'])
        self.assertIn('დაუჯერებელი დილის INR', out['dg'])
        self.assertIn('არ შეიძლება აღემატებოდეს', out['eDia'])


_NODE_JS_GUARD_SCRIPT = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const dir = process.argv[1];
class CL {
  constructor() { this.s = new Set(); }
  add(...a) { a.forEach(x => x && this.s.add(x)); }
  remove(...a) { a.forEach(x => this.s.delete(x)); }
  contains(x) { return this.s.has(x); }
  toggle(x, f) { const on = f === undefined ? !this.s.has(x) : !!f; on ? this.s.add(x) : this.s.delete(x); return on; }
}
class Ev { constructor(t) { this.type = t; } preventDefault() {} }
class El {
  constructor(tag, a = {}) {
    this.tagName = tag.toUpperCase(); this.id = a.id || ''; this.name = a.name || '';
    this.value = a.value || ''; this.hidden = !!a.hidden; this.textContent = ''; this.innerHTML = '';
    this.dataset = { ...(a.dataset || {}) }; this.parentNode = null; this.children = [];
    this.listeners = {}; this.classList = new CL();
    if (a.className) a.className.split(/\s+/).forEach(c => this.classList.add(c));
  }
  get className() { return Array.from(this.classList.s).join(' '); }
  set className(v) { this.classList.s = new Set(String(v || '').split(/\s+/).filter(Boolean)); }
  appendChild(c) { c.parentNode = this; this.children.push(c); return c; }
  addEventListener(t, fn) { (this.listeners[t] ||= []).push(fn); }
  dispatchEvent(ev) { let cur = this; while (cur) { (cur.listeners[ev.type] || []).forEach(fn => fn(ev)); cur = cur.parentNode; } }
  matches(sel) {
    return sel.split(',').some(p => {
      let r = p.trim();
      const tm = r.match(/^([a-zA-Z0-9_-]+)/); if (tm) { if (this.tagName !== tm[1].toUpperCase()) return false; r = r.slice(tm[0].length); }
      while (r) {
        if (r[0] === '.') { const m = r.match(/^\.([a-zA-Z0-9_-]+)/); if (!m || !this.classList.contains(m[1])) return false; r = r.slice(m[0].length); }
        else if (r[0] === '#') { const m = r.match(/^#([a-zA-Z0-9_-]+)/); if (!m || this.id !== m[1]) return false; r = r.slice(m[0].length); }
        else if (r[0] === '[') { const m = r.match(/^\[name="([^"]*)"\]/); if (!m || this.name !== m[1]) return false; r = r.slice(m[0].length); }
        else return false;
      }
      return true;
    });
  }
  closest(s) { let c = this; while (c) { if (c.matches(s)) return c; c = c.parentNode; } return null; }
  querySelectorAll(s) { const o = []; const w = n => { for (const c of n.children) { if (c.matches(s)) o.push(c); w(c); } }; w(this); return o; }
  querySelector(s) { return this.querySelectorAll(s)[0] || null; }
}
function runJs(code, root) {
  const doc = {
    body: root, createElement: t => new El(t),
    getElementById: id => { const w = n => { if (n.id === id) return n; for (const c of n.children) { const h = w(c); if (h) return h; } return null; }; return w(root); },
    querySelectorAll: s => root.querySelectorAll(s), querySelector: s => root.querySelector(s), addEventListener: () => {},
  };
  vm.runInNewContext(code, {
    document: doc, window: { location: { origin: 'http://localhost', hash: '' }, addEventListener: () => {} },
    navigator: {}, localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
    Event: Ev, FormData: class {}, fetch: () => Promise.resolve({ ok: false }),
    setTimeout: () => 0, clearTimeout: () => {}, console,
  });
}
const pRoot = new El('div'); const pForm = pRoot.appendChild(new El('form'));
const pf = {};
for (const n of ['hb', 'plt', 'dos_inr', 'fibrinogen', 'ldl', 'dos_bp_sys', 'dos_bp_dia']) {
  const lbl = pForm.appendChild(new El('label', { className: 'fld' }));
  pf[n] = { lbl, el: lbl.appendChild(new El('input', { name: n })) };
}
runJs(fs.readFileSync(dir + '/preop-patient.js', 'utf8'), pRoot);
const chkP = (n, v) => { pf[n].el.value = v; pf[n].el.dispatchEvent(new Ev('input')); const h = pf[n].lbl.querySelector('.fld-live-hint'); return { hidden: h.hidden, msg: h.textContent }; };
const pRes = {
  plt_hi: chkP('plt', '2000001'), dos_inr_lo: chkP('dos_inr', '0.45'), dos_inr_hi: chkP('dos_inr', '16'),
  fib_ok: chkP('fibrinogen', '0.15'), ldl_lo: chkP('ldl', '0.1'), ldl_hi: chkP('ldl', '550'),
  dos_sbp_lo: chkP('dos_bp_sys', '35'), dos_dbp_hi: chkP('dos_bp_dia', '210'),
  hb_37: chkP('hb', '37'), hb_25: chkP('hb', '25'),
};
const nRoot = new El('div', { id: 'nw-patient-root', dataset: { patientPk: '1' } });
const vf = nRoot.appendChild(new El('form', { id: 'nw-vitals-form' }));
const vg = vf.appendChild(new El('div', { id: 'nw-live-guard', hidden: true }));
const nf = {};
for (const [id, name] of [['nw-bp-sys','bp_sys'],['nw-bp-dia','bp_dia'],['nw-hr','hr'],['nw-spo2','spo2'],['nw-wt','weight_kg'],['nw-ht','height_cm'],['nw-hb','hb'],['nw-plt','plt'],['nw-creat','creat'],['nw-inr','inr'],['nw-k','k'],['nw-gl','glucose']]) {
  nf[name] = vf.appendChild(new El('input', { id, name }));
}
const df = nRoot.appendChild(new El('form', { id: 'nw-dos-form' }));
const dg = df.appendChild(new El('div', { id: 'nw-dos-live-guard', hidden: true }));
for (const [id, name] of [['nw-dos-sbp','dos_bp_sys'],['nw-dos-dbp','dos_bp_dia'],['nw-dos-gl','dos_glucose'],['nw-dos-inr','dos_inr']]) {
  nf[name] = df.appendChild(new El('input', { id, name }));
}
const ef = nRoot.appendChild(new El('form', { id: 'emb-s1' }));
const eLbl = {};
for (const name of ['bp_sys_l', 'bp_dia_l', 'fibrinogen', 'ldl', 'abi_r']) {
  const lbl = ef.appendChild(new El('label', { className: 'fld' }));
  eLbl[name] = lbl; nf[name] = lbl.appendChild(new El('input', { name }));
}
runJs(fs.readFileSync(dir + '/preop-nurse.js', 'utf8'), nRoot);
nf.hb.value = '75'; nf.plt.value = '2501'; nf.k.value = '8,5'; vf.dispatchEvent(new Ev('input'));
nf.dos_bp_sys.value = '100'; nf.dos_bp_dia.value = '130'; nf.dos_inr.value = '16'; df.dispatchEvent(new Ev('input'));
nf.bp_sys_l.value = '100'; nf.bp_dia_l.value = '130'; nf.bp_dia_l.dispatchEvent(new Ev('input'));
console.log(JSON.stringify({ pRes, vg: vg.innerHTML, dg: dg.innerHTML, eDia: eLbl.bp_dia_l.querySelector('.fld-live-hint').textContent }));
"""
