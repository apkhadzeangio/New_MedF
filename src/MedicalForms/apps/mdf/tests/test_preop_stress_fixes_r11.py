"""Stage 4 Round-11 adversarial regression tests (all 3 independent Round-10 audit findings)."""
import datetime
import json
import subprocess

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from ..models import BoardEntry, Case, PreopPatient, StaffProfile
from ..preop import evaluate, generate_anamnesis, nurse_plan, nurse_ui
from ..preop.anamnesis import build_self_fill_highlights
from ..preop.calculators import calc_ariscat_pulm, calc_auto_asa
from ..preop.meds_freetext import (
    _detect_freetext_allergies,
    _detect_freetext_high_risk_meds,
    _has_unrecognized_allergy_clause,
)
from ..preop.registry import REGISTRY_VERSION, RULES_BY_ID, match_rule_for_text
from ..preop.utils import has_prior_stroke_or_tia, target_prep_days
from ..preop.views.board import admit_preop_to_board
from ..preop.views.parsers import med_hold_agreement_block_reason

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


class PreopStressFixesR11ClinicalSafetyTests(SimpleTestCase):
    """Clin A1-S1..A11-S2, Combos S1-1..S1-4 & S2-2..S2-7, Registry A12-S3..A16-S3."""

    def test_open_aortic_solo_neuraxial_with_epidural_still_blocked_and_phleb_auto_vka(self):
        # Clin A1-S1: open AAA / TBD / ABF with anesthesia_plan='neuraxial' + epidural chip MUST stay STOP
        for sub in ('open', 'tbd', 'abf_clti'):
            ev = evaluate(_go(sub, s={'anesthesia_plan': 'neuraxial', 'anesth_lines_chips': ['epidural']}), today=TODAY)
            self.assertTrue(ev['solo_neuraxial_anatomic_mismatch'], sub)
            self.assertEqual(ev['clearance_seal']['code'], 'stop', sub)

        # Clin A2-S1: Phlebectomy auto on warfarin with INR 6.5 or on D0 must NOT keep spinal plan
        for op_d, inr_v in ((TODAY + datetime.timedelta(days=10), 6.5), (TODAY, 2.5), (TODAY, 6.5)):
            ev = evaluate(
                _go('phlebectomy', q={'med_warfarin': True}, s={'anesthesia_plan': 'auto', 'inr': inr_v, 'planned_surgery_date': op_d.isoformat()}),
                today=TODAY,
            )
            self.assertFalse(ev['has_neuraxial_plan'], f'op={op_d} inr={inr_v}')
            self.assertIn('ნეირაქსიული შეზღუდულია', ev['effective_anesth_label'])

    def test_contraindicated_p2y12_and_active_gi_bleed_never_emit_future_dates(self):
        # Clin A3-S1: ESC Class III OAC + ticagrelor/prasugrel and duplicate P2Y12 emit immediate stop, zero future hold dates
        for sub, extra_q in (
            ('open', {'med_ticagrelor': True, 'med_doac': True}),
            ('open', {'med_prasugrel': True, 'med_doac': True}),
            ('open', {'med_ticagrelor': True, 'med_chips': ['rivaroxaban_2_5']}),
            ('open', {'med_clopidogrel': True, 'med_ticagrelor': True}),
            ('pta_clti', {'med_clopidogrel': True, 'med_ticagrelor': True}),
            ('cea_symp', {'med_clopidogrel': True, 'med_ticagrelor': True}),
        ):
            ev = evaluate(_go(sub, q=extra_q), today=TODAY)
            for row in ev['med_schedule']:
                if row.get('contraindicated'):
                    self.assertNotIn('ბოლო:', row['date_plan'], f'{sub}/{extra_q}/{row}')
                    self.assertNotIn('მიიღეთ', row['date_plan'], f'{sub}/{extra_q}/{row}')

        # Clin A11-S2: Active GI bleed gates ASA, P2Y12, DOAC, Dabigatran, Warfarin (but remission clears it)
        ev_gi = evaluate(
            _go('open', q={'ulcer_active_symp': True, 'med_aspirin': True, 'med_clopidogrel': True, 'med_doac': True, 'med_warfarin': True}),
            today=TODAY,
        )
        for row in ev_gi['med_schedule']:
            if 'პანტოპრაზოლი' in row['drug']:
                continue
            self.assertTrue(row.get('contraindicated'), row['drug'])
            self.assertEqual(row['tone'], 'red', row['drug'])
            self.assertNotIn('ბოლო:', row['date_plan'], row['drug'])
        ev_rem = evaluate(_go('open', q={'ulcer_active_symp': True, 'med_aspirin': True}, s={'gastro_result': 'remission'}), today=TODAY)
        asa_rem = next(r for r in ev_rem['med_schedule'] if 'ასპირინი' in r['drug'])
        self.assertFalse(bool(asa_rem.get('contraindicated')))

    def test_pregnancy_warfarin_hit_freetext_teratogens_and_hormones(self):
        # Clin A4-S1 + Combos S1-4: Pregnant warfarin non-mech-valve vs mech-valve
        ev_pw = evaluate(_go('bypass_clti', age=31, sex='F', q={'pregnant_possible': True, 'med_warfarin': True}), today=TODAY)
        self.assertTrue(ev_pw['has_contra_oac'])
        self.assertTrue(any('ვარფარინი' in rf and 'ტერატოგენ' in rf for rf in ev_pw['red_flags']))
        w_row = next(r for r in ev_pw['med_schedule'] if 'ვარფარინი' in r['drug'])
        self.assertTrue(w_row.get('contraindicated'))
        self.assertNotIn('ბოლო:', w_row['date_plan'])
        self.assertNotIn('OAC + კლოპიდოგრელი', ev_pw['surgical_brief']['postop_antithrombotic'])

        ev_mv = evaluate(_go('bypass_clti', age=31, sex='F', q={'pregnant_possible': True, 'med_warfarin': True, 'cad_chips': ['mech_valve']}), today=TODAY)
        self.assertTrue(any('მექანიკური სარქველი' in rf and 'კონსილიუმი' in rf for rf in ev_mv['red_flags']))
        self.assertIn('მეან-გინეკოლოგ', ev_mv['surgical_brief']['postop_antithrombotic'])

        # Combos S1-3: Pregnancy + HIT + DOAC never suggests LMWH/UFH or DOAC anywhere
        ev_ph = evaluate(
            _go('open', age=32, sex='F', q={'pregnant_possible': True, 'allergy_chips': ['hit_heparin'], 'med_doac': True}),
            today=TODAY,
        )
        self.assertNotIn('შეცვალეთ LMWH/UFH-ით', ' '.join(ev_ph['red_flags']))
        self.assertNotIn('DOAC CrCl', ev_ph['surgical_brief']['heparin_plan'])
        self.assertIn('მეან-გინეკოლოგ', ev_ph['surgical_brief']['heparin_plan'])
        self.assertIn('მეან-გინეკოლოგ', ev_ph['surgical_brief']['postop_antithrombotic'])
        self.assertNotIn('თირკმლის/ღვიძლის', ev_ph['surgical_brief']['postop_antithrombotic'])

        # Clin A4-S1: Free-text statin + ACEi/ARB and hormones in pregnancy
        ev_ft = evaluate(
            _go('bypass_clti', age=31, sex='F', q={'pregnant_possible': True, 'other_meds': 'ატორვასტატინი 40 მგ, პრესტარიუმი 5 მგ', 'ven_hormones': True}),
            today=TODAY,
        )
        self.assertTrue(any('სტატინი უკუნაჩვენებია' in rf for rf in ev_ft['red_flags']))
        self.assertTrue(any('ACEi / ARB' in rf for rf in ev_ft['red_flags']))
        self.assertTrue(any('ჰორმონული/ესტროგენული' in rf for rf in ev_ft['red_flags']))
        self.assertTrue(all(r.get('contraindicated') for r in ev_ft['med_schedule']))
        self.assertIn('ორსულობა', generate_anamnesis(_go('open', age=32, sex='F', q={'ven_pregnant': True}), ev=evaluate(_go('open', age=32, sex='F', q={'ven_pregnant': True}), today=TODAY)))

    def test_bad_med_and_elapsed_hold_block_go_while_warfarin_icu_bed_has_no_deadlock(self):
        # Combos S1-1: Contraindicated med (mech valve + DOAC) or elapsed hold window never gets seal='go' / status='ready'
        p_mv = _go('open', q={'cad': True, 'cad_chips': ['mech_valve'], 'med_doac': True})
        ev0 = evaluate(p_mv, today=TODAY)
        p_mv.alerts_ack_at = timezone.now()
        p_mv.studies['alerts_ack_sig'] = ev0['alerts_sig']
        ev_ack = evaluate(p_mv, today=TODAY)
        self.assertTrue(ev_ack['has_bad_med'])
        self.assertNotEqual(ev_ack['clearance_seal']['code'], 'go')
        self.assertNotEqual(ev_ack['status'], 'ready')
        self.assertFalse(ev_ack['dos_ready'])
        self.assertNotEqual(nurse_ui.get_nurse_readiness_verdict(p_mv, ev_ack)['code'], 'ready')

        # Combos S3-3: liver_3x + statin marks statin contraindicated=True
        ev_ls = evaluate(_go('open', q={'med_chips': ['statin']}, s={'liver_status': '3x'}), today=TODAY)
        st_row = next(r for r in ev_ls['med_schedule'] if 'სტატინი' in r['drug'])
        self.assertTrue(st_row.get('contraindicated'))
        self.assertTrue(ev_ls['has_bad_med'])

        # Combos S1-2: Clean open AAA on warfarin with Stage-2 icu_bed_booked=True must NOT trigger STOP/dos_hold_danger
        p_w = _go('open', q={'med_warfarin': True}, s={'inr': 1.2, 'icu_bed_booked': True, 'med_hold_agreed': True})
        ev_w = evaluate(p_w, today=TODAY)
        self.assertFalse(ev_w['dos_hold_danger'])
        self.assertNotEqual(ev_w['clearance_seal']['code'], 'stop')
        w_row = next(r for r in ev_w['med_schedule'] if 'ვარფარინი' in r['drug'])
        self.assertNotIn('STOP', w_row['date_plan'])
        # Combos S2-2: Warfarin brief says 12-24h, NOT 48-72h PAUSE
        self.assertIn('12–24 სთ', ev_w['surgical_brief']['postop_antithrombotic'])
        self.assertNotIn('PAUSE', ev_w['surgical_brief']['postop_antithrombotic'])

    def test_pathways_stroke_clti_cea_label_discordance_and_freetext_parser(self):
        # Clin A5-S2: Stage-1 carotid_symp triggers has_prior_stroke_or_tia -> blocks prasugrel & rivaroxaban_2_5
        p_cs = _go('open', q={'med_prasugrel': True, 'med_chips': ['rivaroxaban_2_5']}, s={'carotid_symp': True, 'carotid_r': 65})
        self.assertTrue(has_prior_stroke_or_tia(p_cs, p_cs.questionnaire, p_cs.studies))
        ev_cs = evaluate(p_cs, today=TODAY)
        self.assertTrue(any('პრასუგრელი' in rf for rf in ev_cs['red_flags']))
        self.assertTrue(any('რივაროქსაბანი 2.5' in rf for rf in ev_cs['red_flags']))

        # Clin A6-S2: explicit post_antithrombotic_plan='dapt' on rivaroxaban_2_5 never recommends rivaroxaban + clopidogrel
        ev_rv = evaluate(_go('bypass_clti', q={'med_chips': ['rivaroxaban_2_5']}, s={'post_antithrombotic_plan': 'dapt'}), today=TODAY)
        self.assertNotIn('რივაროქსაბანი 2.5 + კლოპიდოგრელი', ev_rv['post_antithrombotic_label'])

        # Clin A7-S2 / Combos S2-3: Clean cea_asymp label is never corrupted to 'აასიმპტომური'
        ev_ca = evaluate(_go('cea_asymp'), today=TODAY)
        self.assertNotIn('აასიმპტომური', ev_ca['effective_operation_label'])
        self.assertEqual(ev_ca['effective_operation_label'], 'CEA (მარჯვ.) — ასიმპტომური')

        # Combos S2-4: cea_asymp + old cea_event_chips (>180d) does not emit '<6 months' alert
        old_d = (TODAY - datetime.timedelta(days=210)).isoformat()
        ev_old = evaluate(_go('cea_asymp', q={'cea_event_chips': ['minor_stroke'], 'cea_event_date': old_d}), today=TODAY)
        self.assertFalse(any('ბოლო 6 თვეში' in a for a in ev_old['amber_flags']))
        self.assertTrue(any('>6 თვე' in a for a in ev_old['amber_flags']))

        # Clin A8-S2 / Combos S2-4: abf_wet_infection upgrades target_prep_days to 7 & triggers subtype-upgrade alert
        p_wet = _go('abf_claud', q={'abf_wet_infection': True})
        self.assertEqual(target_prep_days(p_wet, today=TODAY), 7)
        ev_wet = evaluate(p_wet, today=TODAY)
        self.assertTrue(any('ქვეტიპის გადართვა კრიტიკულ იშემიაზე' in a for a in ev_wet['amber_flags']))

        # Combos S2-7: diagnosis_text respects q.filled=False
        p_unf = _go('bypass_claud', q={'filled': False, 'abf_rest_pain': True})
        self.assertNotIn('CLTI', p_unf.diagnosis_text)

        # Combos S3-2: Discordant PAD hemodynamics emit amber warning
        ev_disc = evaluate(_go('bypass_clti', s={'abi_r': 1.55, 'tp_r': 25, 'tbi_r': 0.80}), today=TODAY)
        self.assertTrue(any('ჰემოდინამიკური მაჩვენებლების შეუსაბამობა' in a for a in ev_disc['amber_flags']))

        # Clin A9-S2 & A10-S2: Free-text negation, iodine word boundary, Georgia/EU brands
        self.assertEqual(_detect_freetext_allergies('პერიოდული გამონაყარი, ამიოდარონი, დიოდური ლაზერი'), set())
        self.assertEqual(_detect_freetext_allergies('პენიცილინზე ალერგია არ აქვს; no iodine allergy; ლიდოკაინზე ალერგია არ აქვს'), set())
        self.assertFalse(_has_unrecognized_allergy_clause('ალერგია არ აქვს'))
        self.assertIn('nsaid', _detect_freetext_allergies('ვოლტარენი, ნუროფენი'))
        self.assertIn('iodine', _detect_freetext_allergies('უროგრაფინი, iohexol'))
        self.assertEqual(_detect_freetext_high_risk_meds('ვარფარინი არ სვამს; stopped xarelto 2 months ago; no aspirin')['matched_keys'], set())
        ft_duo = _detect_freetext_high_risk_meds('დუოპლავინი 75/100, ელიკვისი 5მგ, ფარქსიგა 10, ჯანუმეტი, პრედნიზონი 10 მგ')
        self.assertTrue({'aspirin', 'clopidogrel', 'doac', 'sglt2', 'metformin', 'steroid'}.issubset(ft_duo['matched_keys']))

    def test_registry_citations_attribution_and_plain_patient_guide(self):
        # Clin A12-S3..A16-S3
        self.assertGreaterEqual(tuple(int(x) for x in REGISTRY_VERSION.split('.')), (1, 2, 10))
        self.assertEqual(RULES_BY_ID['ASRA_NEURAXIAL_COAG_GATE'].value['vka_prewashout_inr_max'], 4.5)
        self.assertEqual(RULES_BY_ID['MED_DABIGATRAN_HOLD_INTERVALS'].status, 'local-decision')
        self.assertEqual(RULES_BY_ID['MED_P2Y12_HOLD_DAYS'].status, 'local-decision')
        self.assertEqual(RULES_BY_ID['END_THYROID_TSH'].status, 'local-decision')
        self.assertIn('Rec 49', RULES_BY_ID['CEA_ACUTE_NEURO_THROMBOLYSIS'].section)
        self.assertNotIn('Rec 49', RULES_BY_ID['CEA_OCCLUSION_100_STOP'].section)
        self.assertIn('Level C', RULES_BY_ID['INF_DENTAL_DECOLONIZATION'].class_level)

        ev_vka_nx = evaluate(_go('bypass_clti', q={'med_warfarin': True}, s={'anesthesia_plan': 'neuraxial', 'inr': 2.5}), today=TODAY)
        vka_amb = next(a for a in ev_vka_nx['amber_flags'] if 'ნეირაქსიული' in a)
        self.assertEqual(match_rule_for_text(vka_amb).id, 'ASRA_NEURAXIAL_COAG_GATE')

        ev_ven_la = evaluate(_go('evla', q={'ven_lidocaine_allergy': True}), today=TODAY)
        la_red = next(r for r in ev_ven_la['red_flags'] if 'ლიდოკაინზე' in r)
        self.assertEqual(match_rule_for_text(la_red).id, 'ALLERGY_HIT_ABX_PROPHYLAXIS')

        # Combos S2-5 + Clin B3 + B4: Plain guide scrubs jargon even when dated; undated clopidogrel plain; shortened hold -> danger
        p_j = _go(
            'open',
            q={'med_chips': ['beta_blocker', 'acei_arb', 'steroid'], 'med_warfarin': True, 'other_meds': 'ელიქვისი, ოზემპიკი'},
            s={'bp_sys': 95, 'bp_dia': 60, 'k': 5.3},
        )
        guide = nurse_plan.build_patient_plain_explanation(p_j, evaluate(p_j, today=TODAY), today=TODAY)
        guide_rows = guide['stop_meds'] + guide['keep_meds'] + guide['caution_meds'] + guide['urgent_meds']
        guide_blob = ' '.join(f"{r['drug']} {r['spoken']}" for r in guide_rows)
        for raw_tok in ('SBP<100', 'K+≥5.2', 'IV სტრეს-დოზა', 'Xa-DOAC', 'არასტრუქტურირებული', 'INR'):
            self.assertNotIn(raw_tok, guide_blob)

        p_clop_und = _go('open', q={'med_clopidogrel': True}, s={'planned_surgery_date': ''})
        g_clop = nurse_plan.build_patient_plain_explanation(p_clop_und, evaluate(p_clop_und, today=TODAY), today=TODAY)
        self.assertNotIn('5–7 დღით ადრე — სავალდებულოა', ' '.join(r['spoken'] for r in g_clop['stop_meds'] + g_clop['caution_meds']))

        p_short = _go(
            'open', q={'med_warfarin': True},
            s={'prev_planned_surgery_date': (TODAY + datetime.timedelta(days=15)).isoformat(), 'planned_surgery_date': (TODAY + datetime.timedelta(days=2)).isoformat()},
        )
        imp = nurse_plan.detect_postponement_impacts(p_short, evaluate(p_short, today=TODAY), today=TODAY)
        self.assertTrue(any(it['tone'] == 'danger' for it in imp['impacts']))


class PreopStressFixesR11FuzzerAndScoringTests(SimpleTestCase):
    """Fuzz A-S1-1, A-S1-3, A-S2-1, A-S2-2, A-S2-4, A-S3-1, A-S3-2."""

    def test_postponement_cta_future_no_crash_and_contradictory_pregnancy_blocks_go(self):
        # Fuzz A-S1-1: cta_date > planned_surgery_date never raises TypeError
        p_cta = _go(
            'open',
            s={
                'cta_date': '2026-11-20',
                'prev_planned_surgery_date': '2026-12-10',
                'planned_surgery_date': '2026-11-15',
            },
        )
        imp = nurse_plan.detect_postponement_impacts(p_cta, evaluate(p_cta, today=TODAY), today=TODAY)
        self.assertIsInstance(imp, dict)

        # Fuzz A-S1-3 + Combos S2-7: sex='M' + pregnancy blocks GO & self-fill highlight warns instead of urgent pregnancy
        for sub, qx in (('evla', {'ven_pregnant': True}), ('evar', {'pregnant_possible': True})):
            p_m = _go(sub, age=30, sex='M', q=qx)
            ev_m = evaluate(p_m, today=TODAY)
            self.assertTrue(ev_m['has_blocking_unknown_state'], sub)
            self.assertNotEqual(ev_m['clearance_seal']['code'], 'go', sub)
            self.assertFalse(ev_m['workup_ready'], sub)
            hl = build_self_fill_highlights(p_m.questionnaire, patient=p_m)
            self.assertTrue(any(lvl == 'warn' and 'გადაამოწმეთ სქესი' in txt for lvl, txt in hl))

    def test_ariscat_hb_35_39_and_asa_egfr_sat_monotonicity(self):
        # Fuzz A-S2-1: Hb 3.5..3.99 g/dL keeps +11 ARISCAT anemia points
        ev_40 = evaluate(_go('open', s={'hb': 40}), today=TODAY)
        ev_35 = evaluate(_go('open', s={'hb': 3.5}), today=TODAY)
        ev_39 = evaluate(_go('open', s={'hb': 3.99}), today=TODAY)
        self.assertEqual(ev_35['ariscat']['score'], ev_40['ariscat']['score'])
        self.assertEqual(ev_39['ariscat']['score'], ev_40['ariscat']['score'])
        self.assertEqual(calc_ariscat_pulm(_go('open'), {}, {}, hb_gl=35)['score'], ev_40['ariscat']['score'])

        # Fuzz A-S2-2: age=121 or age=None with creat=4.0/15.0 mg/dL keeps ASA IV & ICU need
        ev_120 = evaluate(_go('evla', age=120, s={'creat': 4.0, 'creat_unit': 'mgdl'}), today=TODAY)
        ev_121 = evaluate(_go('evla', age=121, s={'creat': 4.0, 'creat_unit': 'mgdl'}), today=TODAY)
        ev_none = evaluate(_go('evla', age=None, s={'creat': 15.0, 'creat_unit': 'mgdl'}), today=TODAY)
        self.assertEqual(ev_120['auto_asa'], 'IV')
        self.assertEqual(ev_121['auto_asa'], 'IV')
        self.assertTrue(ev_121['needs_icu_bed'])
        self.assertEqual(ev_none['auto_asa'], 'IV')
        self.assertTrue(ev_none['needs_icu_bed'])
        self.assertEqual(calc_auto_asa(_go('evla', age=None), {}, {'creat': 15.0, 'creat_unit': 'mgdl'}), 'IV')

    def test_med_call_reconcile_only_and_evla_warfarin_plus_direct_caller_safety(self):
        # Fuzz A-S2-4: reconcile_only without op_date is NOT blocked; EVLA warfarin gets needs_call=True
        for sub, qx in (
            ('evla', {'ven_hormones': True}),
            ('evla', {'med_doac': True}),
            ('open', {'glp1': True}),
            ('phlebectomy', {'med_aspirin': True}),
        ):
            p = _go(sub, q=qx, s={'planned_surgery_date': ''})
            ev = evaluate(p, today=TODAY)
            self.assertEqual(med_hold_agreement_block_reason(ev, ev['s']), '', f'{sub}/{qx}')

        p_ew = _go('evla', q={'med_warfarin': True}, s={'inr': 2.2})
        ev_ew = evaluate(p_ew, today=TODAY)
        self.assertTrue(ev_ew['needs_med_hold'])
        self.assertTrue(nurse_ui._med_call_status(ev_ew, TODAY)['needs_call'])

        # Fuzz A-S3-1: direct callers with datetime.date / unhashable fields do not crash
        p_bad = _go('open')
        self.assertTrue(has_prior_stroke_or_tia(p_bad, {'cea_event_date': datetime.date(2026, 5, 1)}))
        p_bad.op_subtype = {}
        p_bad.side = []
        p_bad.phone = 123
        ev_ok = evaluate(_go('open'), today=TODAY)
        self.assertIsInstance(generate_anamnesis(p_bad, ev=ev_ok), str)
        self.assertIsInstance(nurse_ui._plain_procedure_ka(p_bad, ev_ok), str)

    def test_js_live_guards_strict_numbers_and_bp(self):
        # Fuzz A-S3-2: Verify Node execution of strict numeric & BP guards in both JS files
        js_patient = (settings.BASE_DIR / 'static' / 'js' / 'preop-patient.js').read_text(encoding='utf-8')
        js_nurse = (settings.BASE_DIR / 'static' / 'js' / 'preop-nurse.js').read_text(encoding='utf-8')
        self.assertIn('parseStrictNum', js_patient)
        self.assertIn('input[name="bp_sys_l"]', js_patient)
        self.assertIn('input[name="bp_dia"]', js_patient)
        self.assertIn('STRICT_NUM_RE', js_nurse)
        script = r"""
const re = /^[+-]?(?:\d+(?:[.,]\d+)?|[.,]\d+)$/;
const parse = (r) => { const s = String(r ?? '').trim(); if (!s || !re.test(s)) return NaN; const n = Number(s.replace(/,/g, '.')); return isFinite(n) ? n : NaN; };
const out = {
  bad_text: Number.isNaN(parse('12abc')),
  bad_inf: Number.isNaN(parse('Infinity')),
  comma_ok: parse('1,25') === 1.25,
  zero_ok: parse('0') === 0,
};
console.log(JSON.stringify(out));
"""
        res = subprocess.run(['node', '-e', script], capture_output=True, text=True, check=True)
        parsed = json.loads(res.stdout)
        self.assertTrue(all(parsed.values()), parsed)


class PreopStressFixesR11HttpAndBoardTests(TestCase):
    """Fuzz A-S1-2, A-S2-3, B-5; Combos S1-5, S1-6, S2-1, S2-6, S3-3."""

    def setUp(self):
        User = get_user_model()
        self.doc = User.objects.create_user(username='r11_doc', password='pw', first_name='ექიმი', last_name='ტესტი', is_staff=True)
        StaffProfile.objects.create(user=self.doc, display_name='ექიმი ტესტი')
        self.nurse = User.objects.create_user(username='r11_nurse', password='pw', first_name='ექთანი', last_name='ტესტი', is_staff=False)

    def test_ack_alerts_doctor_only_rbac_and_cea_bilateral_blocked(self):
        # Fuzz A-S1-2: non-doctor cannot ack_alerts
        p = _go('evla', s={'k': 6.2}, surgeon=self.doc)
        p.save()
        self.client.force_login(self.nurse)
        self.client.post(reverse('preop-patient', args=[p.pk]), {'section': 'signoff', 'action': 'ack_alerts'})
        p.refresh_from_db()
        self.assertIsNone(p.alerts_ack_by)
        self.assertTrue(evaluate(p, today=TODAY)['has_unacked_red'])

        # Fuzz A-S2-3: CEA side='B' blocked in header, subtype switch, and Tab Q
        p_cea = _go('cea_asymp', side='R', surgeon=self.doc)
        p_cea.save()
        self.client.post(reverse('preop-patient', args=[p_cea.pk]), {'section': 'header', 'op_subtype': 'cea_asymp', 'side': 'B'})
        p_cea.refresh_from_db()
        self.assertEqual(p_cea.side, 'R')
        self.client.post(reverse('preop-patient', args=[p_cea.pk]), {'section': 'questionnaire', 'side': 'B', 'mets_ok': '1'})
        p_cea.refresh_from_db()
        self.assertEqual(p_cea.side, 'R')

        p_ev = _go('evla', side='B', surgeon=self.doc)
        p_ev.save()
        self.client.post(reverse('preop-patient', args=[p_ev.pk]), {'section': 'header', 'op_subtype': 'cea_symp'})
        p_ev.refresh_from_db()
        self.assertEqual(p_ev.pathway, PreopPatient.Pathway.CEA)
        self.assertEqual(p_ev.side, '')

    def test_venous_tab_q_preserves_infection_and_s1_preserves_hidden_flags(self):
        # Combos S1-5: Venous self-fill active_infection survives nurse Tab Q save
        p_ven = _go('evla', q={'active_infection': True, 'inf_chips': ['fever_resp']}, surgeon=self.doc)
        p_ven.save()
        self.client.force_login(self.nurse)
        resp = self.client.get(reverse('preop-patient', args=[p_ven.pk]) + '?tab=q')
        self.assertContains(resp, 'name="active_infection_presented"')
        # Even if a client omits active_infection_presented, parser preserves existing flag
        self.client.post(reverse('preop-patient', args=[p_ven.pk]), {'section': 'questionnaire', 'mets_ok': '1'})
        p_ven.refresh_from_db()
        self.assertTrue(p_ven.questionnaire['active_infection'])
        self.assertIn('fever_resp', p_ven.questionnaire['inf_chips'])

        # Combos S2-1: Tab S1 full save on Venous/PTA preserves echo_valve_issue & carotid_symp
        p_ven.studies['echo_valve_issue'] = True
        p_ven.save()
        self.client.post(reverse('preop-patient', args=[p_ven.pk]), {'section': 'stage1', 'hb': '135', 'creat': '80'})
        p_ven.refresh_from_db()
        self.assertTrue(p_ven.studies['echo_valve_issue'])

    def test_board_handoff_stop_guard_statin_omission_and_case_link(self):
        # Combos S1-6 & Fuzz B-5: STOP patient blocked from /admit-board/ and case_outcome='operated'
        p_stop = _go('evla', age=30, sex='F', q={'ven_pregnant': True}, surgeon=self.doc)
        p_stop.save()
        self.client.force_login(self.doc)
        self.client.post(reverse('preop-admit-board', args=[p_stop.pk]))
        p_stop.refresh_from_db()
        self.assertFalse(p_stop.studies.get('board_patient_id'))
        self.client.post(reverse('preop-patient', args=[p_stop.pk]), {'section': 'dos_vqi', 'action': 'save_dos', 'case_outcome_status': 'operated'})
        p_stop.refresh_from_db()
        self.assertNotEqual(p_stop.studies.get('case_outcome_status'), 'operated')

        # Combos S1-6 & S2-6: Direct admit_preop_to_board surfaces STOP/pregnancy/liver_3x & omits statin when contraindicated
        p_pad = _go('abf_clti', age=32, sex='F', q={'pregnant_possible': True}, s={'liver_status': '3x'}, surgeon=self.doc)
        p_pad.save()
        bp, _, _ = admit_preop_to_board(p_pad, self.doc)
        status_txt = BoardEntry.objects.filter(patient=bp, kind=BoardEntry.STATUS).latest('id').text
        self.assertIn('ორსულობა', status_txt)
        self.assertIn('≥3x ULN', status_txt)
        tasks = list(BoardEntry.objects.filter(patient=bp, kind=BoardEntry.TASK).values_list('text', flat=True))
        self.assertFalse(any('სტატინი' in t for t in tasks), tasks)

        # Combos S3-3: Case detail links directly to existing PreopPatient
        case = Case.objects.create(patient_name='ქეისის პაციენტი', doctor=self.doc, created_by=self.doc, conference_on=timezone.localdate())
        p_link = _go('bypass_clti', case=case, surgeon=self.doc)
        p_link.save()
        resp = self.client.get(reverse('case', args=[case.pk]))
        self.assertContains(resp, reverse('preop-patient', args=[p_link.pk]))
