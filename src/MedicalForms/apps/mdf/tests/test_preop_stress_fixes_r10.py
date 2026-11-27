"""Stage 4 Round-10 adversarial regression tests (all 3 independent Round-9 audit findings)."""
import datetime
import json
import subprocess

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from ..models import BoardEntry, Case, PreopPatient, PreopRuleSignoff, StaffProfile
from ..preop import evaluate, generate_anamnesis, nurse_plan, nurse_ui
from ..preop.calculators import normalize_albumin_gl, normalize_glucose_mmol, normalize_hb_gl
from ..preop.forms import apply_questionnaire_post, apply_self_fill_post
from ..preop.registry import REGISTRY_VERSION, annotate_med_rows
from ..preop.views.board import admit_preop_to_board, resolve_board_patient
from ..preop.views.parsers import can_enter_ready_queue, med_hold_agreement_block_reason

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
        'aaa_mm': 56 if pw == 'aaa_infra' else None,
        'carotid_r': 75 if pw == 'cea' else None,
        'cea_cta_status': 'concordant' if pw == 'cea' else 'pending',
        'brain_imaging': 'normal_small' if pw == 'cea' else 'pending',
        'ven_duplex_status': 'reflux_only' if pw == 'venous' else 'pending',
    }
    if s:
        s_d.update(s)
    return PreopPatient(
        patient_name='სატესტო პაციენტი', age=age, sex=sex, side=side,
        op_subtype=sub, pathway=pw, started_on=TODAY,
        questionnaire=q_d, studies=s_d, **kw,
    )


class PreopStressFixesR10PharmacologyTests(SimpleTestCase):
    """Batch R10-1: Pharmacology, pregnancy, contraindicated OAC dates, prasugrel, cilostazol, neuraxial VKA."""

    def test_rivaroxaban_active_bleed_and_prior_stroke_contraindicated(self):
        # Clin A-S1-4: rivaroxaban_2_5 + active GI bleed / acute_ulcer / prior stroke
        for sub, extra_q, extra_s in (
            ('evla', {'med_chips': ['rivaroxaban_2_5'], 'ulcer_active_symp': True}, {}),
            ('bypass_clti', {'med_chips': ['rivaroxaban_2_5']}, {'gastro_result': 'acute_ulcer'}),
            ('evla', {'med_chips': ['rivaroxaban_2_5'], 'neuro': True}, {}),
            ('bypass_clti', {'med_chips': ['rivaroxaban_2_5'], 'neuro': True}, {}),
        ):
            ev = evaluate(_go(sub, q=extra_q, s=extra_s), today=TODAY)
            self.assertTrue(ev['has_contra_oac'], f'{sub}/{extra_q}/{extra_s}')
            row = next(r for r in ev['med_schedule'] if 'ქსარელტო 2.5' in r['drug'])
            self.assertEqual(row['tone'], 'red')
            self.assertTrue(row.get('contraindicated'))
            self.assertNotIn('აღდგენა', row['rule'])
            self.assertTrue(any('რივაროქსაბანი 2.5' in rf for rf in ev['red_flags']))

    def test_prasugrel_lifelong_prior_stroke_contraindication_and_pta_oac_sapt(self):
        # Clin A-S1-5: prasugrel contraindicated even when CEA stroke is >180d ago or in cea_event_chips
        old_ev = (TODAY - datetime.timedelta(days=210)).isoformat()
        for sub, extra_q in (
            ('cea_symp', {'med_prasugrel': True, 'cea_event_date': old_ev}),
            ('cea_asymp', {'med_prasugrel': True, 'cea_event_chips': ['minor_stroke'], 'cea_event_date': old_ev}),
        ):
            ev = evaluate(_go(sub, q=extra_q), today=TODAY)
            row = next(r for r in ev['med_schedule'] if 'პრასუგრელი' in r['drug'])
            self.assertTrue(row.get('contraindicated'), sub)
            self.assertIn('უკუნაჩვენებია', row['rule'])
            self.assertTrue(any('პრასუგრელი' in rf for rf in ev['red_flags']), sub)

        # Clin A-S1-3: PTA + clopidogrel_intol + oac_sapt must NOT recommend ESC Class III OAC + ticagrelor
        ev_pta = evaluate(
            _go('pta_clti', q={'allergy_chips': ['clopidogrel_intol']}, s={'post_antithrombotic_plan': 'oac_sapt'}),
            today=TODAY,
        )
        self.assertFalse(any('ალტერნატიული P2Y12 (ტიკაგრელორი)' in rf for rf in ev_pta['red_flags']))
        self.assertTrue(any('ასპირინი' in rf and 'ESC Class III' in rf for rf in ev_pta['red_flags']))

    def test_pregnancy_blocks_doac_voyager_statin_and_acei_everywhere(self):
        # Combos A-S1-1: pregnancy never recommends DOAC/VOYAGER/statin; flags active DOAC/statin/ACEi
        p_preg_pad = _go(
            'bypass_clti', age=32, sex='F',
            q={'pregnant_possible': True, 'ven_acute_svt': True, 'ven_dvt_history': True},
        )
        ev_pad = evaluate(p_preg_pad, today=TODAY)
        self.assertNotIn('LMWH / DOAC', ev_pad['caprini_advice'])
        self.assertIn('მხოლოდ LMWH / UFH', ev_pad['caprini_advice'])
        self.assertNotIn('VOYAGER', ev_pad['post_antithrombotic_label'])
        self.assertNotIn('სტატინი', ev_pad['surgical_brief']['postop_antithrombotic'])
        self.assertFalse(any('სტატინ' in af for af in ev_pad['amber_flags']))

        ev_evla = evaluate(_go('evla', age=30, sex='F', q={'ven_pregnant': True, 'ven_acute_svt': True}), today=TODAY)
        self.assertNotIn('LMWH / DOAC', ev_evla['caprini_advice'])
        self.assertIn('LMWH / UFH', ev_evla['caprini_advice'])
        self.assertFalse(any('LMWH / DOAC' in af for af in ev_evla['amber_flags']))

        ev_meds = evaluate(
            _go('cea_symp', age=32, sex='F', q={'pregnant_possible': True, 'med_doac': True, 'med_chips': ['statin', 'acei_arb']}),
            today=TODAY,
        )
        self.assertTrue(ev_meds['has_contra_oac'])
        for kw in ('DOAC', 'სტატინი', 'ACEi / ARB'):
            row = next(r for r in ev_meds['med_schedule'] if kw in r['drug'])
            self.assertEqual(row['tone'], 'red', kw)
            self.assertTrue(row.get('contraindicated'), kw)
            self.assertNotIn('აღდგენა', row['rule'], kw)
            self.assertNotIn('გრძელდება უწყვეტად', row['rule'], kw)

    def test_contraindicated_oac_immediate_stop_no_future_dates_and_postop_parity(self):
        # Combos A-S1-3: contraindicated OACs never emit future last-dose dates
        for label, extra_q, extra_s in (
            ('doac_dialysis', {'med_doac': True, 'on_dialysis': True}, {}),
            ('dabi_crcl22', {'med_dabigatran': True}, {'creat': 280, 'weight_kg': 60}),
            ('doac_liver3x', {'med_doac': True}, {'liver_status': '3x'}),
            ('dup_oac', {'med_doac': True, 'med_chips': ['rivaroxaban_2_5']}, {}),
        ):
            p = _go('open', q=extra_q, s=extra_s)
            ev = evaluate(p, today=TODAY)
            for row in ev['med_schedule']:
                if row.get('contraindicated'):
                    self.assertNotIn('ბოლო:', row.get('date_plan') or '', f'{label}/{row["drug"]}')
            guide = nurse_plan.build_patient_plain_explanation(p, ev, TODAY)
            for m in guide['urgent_meds']:
                self.assertNotIn('ბოლო აბი', m['spoken'], f'{label}/{m["drug"]}')

        # Combos A-S1-4: PAD + voyager/dapt + contraindicated OAC never advises resuming OAC + clopidogrel
        ev_pad_c = evaluate(
            _go('bypass_clti', q={'med_doac': True, 'on_dialysis': True}, s={'post_antithrombotic_plan': 'voyager'}),
            today=TODAY,
        )
        self.assertIn('უკუნაჩვენები OAC არ განაახლოთ', ev_pad_c['post_antithrombotic_label'])
        self.assertNotIn('შეცვალეთ: OAC +', ev_pad_c['post_antithrombotic_label'])

    def test_evla_dup_oac_prewashout_vka_cilostazol_and_acei_med_call(self):
        # Clin A-S2-1: EVLA + duplicate OAC (Warfarin + DOAC) with INR 2.5 cannot emit INR<=3.0 advisory or pass DoS
        ev_dup = evaluate(
            _go('evla', q={'med_warfarin': True, 'med_doac': True}, s={'inr': 2.5, 'dos_identity_consent': True}),
            today=TODAY,
        )
        self.assertFalse(any('INR <= 3.0' in af for af in ev_dup['amber_flags']))
        self.assertTrue(ev_dup['dos_hold_danger'])

        # Clin A-S2-2: Warfarin + explicit epidural/neuraxial at D-10 with baseline INR 2.5 is pre-washout (not deadlocked)
        p_epi = _go('open', q={'med_warfarin': True}, s={'inr': 2.5, 'anesth_lines_chips': ['epidural']})
        ev_epi = evaluate(p_epi, today=TODAY)
        self.assertFalse(ev_epi['neuraxial_hard_blocked'])
        self.assertEqual(med_hold_agreement_block_reason(ev_epi, ev_epi['s']), '')
        # But on surgery day (D0) without dos_inr, baseline INR 2.5 MUST still hard-block neuraxial!
        p_epi_d0 = _go('open', q={'med_warfarin': True}, s={'inr': 2.5, 'anesth_lines_chips': ['epidural'], 'planned_surgery_date': TODAY.isoformat()})
        self.assertTrue(evaluate(p_epi_d0, today=TODAY)['neuraxial_hard_blocked'])

        # Clin A-S2-5: Cilostazol + ambiguous/missing creatinine -> reconcile_only RED, no dated stop / elapsed hold
        for cr_val in (22, None):
            p_c = _go('bypass_clti', q={'med_chips': ['cilostazol']}, s={'creat': cr_val, 'planned_surgery_date': (TODAY + datetime.timedelta(days=1)).isoformat()})
            ev_c = evaluate(p_c, today=TODAY)
            row = next(r for r in ev_c['med_schedule'] if 'ცილოსტაზოლ' in r['drug'])
            self.assertTrue(row.get('reconcile_only'), cr_val)
            self.assertNotIn('ბოლო:', row.get('date_plan') or '', cr_val)
            self.assertFalse(ev_c['has_elapsed_hold_window'], cr_val)

        # Clin A-S2-3: ACEi/ARB or diuretic-only morning hold sets needs_med_hold=True so nurse can complete call
        for chip in ('acei_arb', 'diuretic'):
            ev_m = evaluate(_go('cea_asymp', q={'med_chips': [chip]}), today=TODAY)
            self.assertTrue(ev_m['needs_med_hold'], chip)
            self.assertEqual(med_hold_agreement_block_reason(ev_m, ev_m['s']), '', chip)


class PreopStressFixesR10PathwaysAndCalcTests(SimpleTestCase):
    """Batch R10-2 & R10-5: Rupture AAA, non-venous LA allergy STOP, neuraxial STOP-lock, TP/TBI, Hb/PLT/BP/creat."""

    def test_ruptured_aaa_suppresses_hydration_and_abi_and_la_allergy_stops_local_plexus(self):
        # Clin A-S1-1 & A-S2-6: Ruptured AAA + CKD + reduced pedal pulse suppresses hydration AND aaa_abi
        ev_rup = evaluate(
            _go('evar', q={'aaa_pain': True, 'aaa_pedal_pulse': 'reduced'}, s={'aaa_mm': 76, 'bp_sys': 80, 'bp_dia': 50, 'hr': 114, 'creat': 160}),
            today=TODAY,
        )
        self.assertTrue(ev_rup['is_rupture_suspect'])
        self.assertFalse(ev_rup['needs_hydration'])
        ro_keys = {r['key'] for r in ev_rup['reflex_orders']}
        self.assertNotIn('hydration', ro_keys)
        self.assertNotIn('aaa_abi', ro_keys)

        # Clin A-S1-2: Non-venous local/plexus (and PTA auto) + LA allergy -> hard STOP
        for sub, plan in (('cea_asymp', 'plexus'), ('pta_clti', 'local'), ('pta_clti', 'auto'), ('evar', 'local')):
            ev_la = evaluate(_go(sub, q={'allergy_chips': ['lidocaine']}, s={'anesthesia_plan': plan}), today=TODAY)
            self.assertTrue(ev_la['has_hard_contraindication'], f'{sub}/{plan}')
            self.assertEqual(ev_la['clearance_seal']['code'], 'stop', f'{sub}/{plan}')

    def test_neuraxial_hard_blocked_stop_locks_schedule_and_exports_solo_mismatch(self):
        # Combos A-S1-5 & A-S2-1 & Clin A-S3-1
        p_nx = _go('bypass_clti', q={'med_doac': True}, s={'anesthesia_plan': 'neuraxial', 'plt': 70})
        ev_nx = evaluate(p_nx, today=TODAY)
        self.assertTrue(ev_nx['neuraxial_hard_blocked'])
        self.assertEqual(ev_nx['clearance_seal']['code'], 'stop')
        script = nurse_plan.build_phone_med_script(p_nx, ev_nx, TODAY)
        self.assertTrue(script['stop_locked'])
        for r in ev_nx['med_schedule']:
            if r.get('hold_or_skip') and not r.get('contraindicated') and not r.get('acute_hold'):
                self.assertIn('STOP', r['date_plan'])

        p_solo = _go('cea_asymp', s={'anesthesia_plan': 'neuraxial'})
        ev_solo = evaluate(p_solo, today=TODAY)
        self.assertTrue(ev_solo.get('solo_neuraxial_anatomic_mismatch'))
        miss = nurse_plan.build_plain_georgian_missing_items(p_solo, ev_solo, TODAY)
        self.assertFalse(any('ASRA 5th Ed' in it['label'] for it in miss))

    def test_venous_tp_tbi_compression_wifi_isolation_and_bp_creat_hb_plt_invariants(self):
        # Clin A-S2-4 & Combos A-S2-3: conflicting TP/TBI, borderline TBI without ABI>1.40, unverified ABI>1.40 task
        for s_patch, exp_red, exp_task_sub in (
            ({'abi_r': 1.55, 'tp_r': 55, 'tbi_r': 0.75}, True, 'I კლასის'),
            ({'abi_r': 1.55, 'tp_r': 65, 'tbi_r': 0.55}, True, 'I კლასის'),
            ({'abi_r': 1.05, 'tbi_r': 0.55}, True, 'I კლასის'),
            ({'abi_r': None, 'tbi_r': 0.55}, True, 'I კლასის'),
            ({'abi_r': 1.50, 'tp_r': None, 'tbi_r': None}, False, 'სავალდებულოა TP/TBI'),
        ):
            ev_v = evaluate(_go('evla', side='R', s=s_patch), today=TODAY)
            self.assertEqual(ev_v['tbi_reduced_comp'], exp_red, s_patch)
            comp_t = next(t for t in ev_v['tasks'] if t['key'] == 'comp')
            self.assertIn(exp_task_sub, comp_t['label'], s_patch)

        # Combos A-S2-11: case_wifi_unsalvageable isolated to PAD pathways only
        for sub in ('open', 'cea_asymp', 'evla'):
            ev_w = evaluate(_go(sub, s={'case_wifi_unsalvageable': True}), today=TODAY)
            self.assertFalse(any('WIfI Stage 5' in rf for rf in ev_w['red_flags']), sub)

        # Fuzzer A-S2-7: swapped normotensive L-arm 70/120 must NOT set severe_htn or suppress CEA DBP>90 warning
        ev_bp = evaluate(_go('cea_asymp', s={'bp_sys': 150, 'bp_dia': 95, 'bp_sys_l': 70, 'bp_dia_l': 120}), today=TODAY)
        self.assertTrue(any('DBP > 90' in af for af in ev_bp['amber_flags']))

        # Fuzzer A-S1-8: creat explicit mgdl > 35 mismatch must NOT fabricate ASA IV / RCRI +1 / ICU
        ev_cm = evaluate(_go('evla', age=40, s={'creat': 88.4, 'creat_unit': 'mgdl'}), today=TODAY)
        self.assertTrue(ev_cm['creat_unit_mismatch'])
        self.assertIsNone(ev_cm['egfr'])
        self.assertNotEqual(ev_cm['auto_asa'], 'IV')
        self.assertEqual(ev_cm['rcri']['score'], 0)
        self.assertFalse(ev_cm['needs_icu_bed'])

        # Fuzzer A-S2-8 & A-S3-2: Hb 25 ambiguous blocks GO without fabricating ASA IV/ICU; Hb 3.4 implausible; PLT=0 no "PLT 1"; unit float parity
        ev_hb25 = evaluate(_go('evla', age=45, s={'hb': 25}), today=TODAY)
        self.assertTrue(ev_hb25['has_blocking_unknown_state'])
        self.assertIn('ambiguous_hb_unit', {u['key'] for u in ev_hb25['unknown_state_flags']})
        self.assertNotEqual(ev_hb25['auto_asa'], 'IV')
        self.assertFalse(ev_hb25['needs_icu_bed'])
        self.assertFalse(ev_hb25['needs_anemia_panel'])

        self.assertIsNone(normalize_hb_gl(3.4))
        ev_hb34 = evaluate(_go('evla', s={'hb': 3.4}), today=TODAY)
        self.assertTrue(ev_hb34['has_blocking_unknown_state'])

        ev_plt0 = evaluate(_go('evla', s={'plt': 0}), today=TODAY)
        self.assertFalse(any('PLT 1 x10^9/L' in rf for rf in ev_plt0['red_flags']))

        ev_hb_a = evaluate(_go('open', s={'hb': 6.9}), today=TODAY)
        ev_hb_b = evaluate(_go('open', s={'hb': 69}), today=TODAY)
        self.assertEqual(ev_hb_a['alerts_sig'], ev_hb_b['alerts_sig'])
        self.assertEqual(str(normalize_glucose_mmol(252)), str(normalize_glucose_mmol(14)))
        self.assertEqual(str(normalize_albumin_gl(2.9)), str(normalize_albumin_gl(29)))

    def test_7surface_labels_jargon_insulin_evla_unhashable_side_and_registry(self):
        # Combos A-S2-5 & A-S2-10 & A-S3-1 & Fuzzer A-S3-1 & Clin A-S3-3
        p_cea_old = _go('cea_symp', q={'cea_event_date': (TODAY - datetime.timedelta(days=240)).isoformat()})
        ev_old = evaluate(p_cea_old, today=TODAY)
        self.assertFalse(ev_old['is_symp_cea'])
        self.assertIn('ასიმპტომური', ev_old['effective_operation_label'])
        self.assertNotIn('14 დღე', nurse_ui._plain_procedure_ka(p_cea_old, ev_old))
        self.assertNotIn('სიმპტომური', p_cea_old.diagnosis_text)

        p_clti_up = _go('bypass_claud', q={'abf_wet_infection': True})
        ev_up = evaluate(p_clti_up, today=TODAY)
        self.assertIn('CLTI', ev_up['effective_operation_label'])
        self.assertIn('CLTI', p_clti_up.diagnosis_text)
        self.assertIn('CLTI', generate_anamnesis(p_clti_up, ev_up))
        self.assertNotIn('სიარულის ტკივილი', nurse_ui._plain_procedure_ka(p_clti_up, ev_up))

        # Local EVLA + insulin must not contradict breakfast with NPO insulin skipping
        p_ins = _go('evla', q={'dm': True, 'dm_insulin': True})
        ev_ins = evaluate(p_ins, today=TODAY)
        ins_row = next(r for r in ev_ins['med_schedule'] if 'ინსულინი' in r['drug'])
        self.assertIn('საუზმ', ins_row['rule'])

        # Unhashable side must not crash diagnosis_text or handover queues
        p_bad_side = _go('cea_symp')
        p_bad_side.side = {'u': 1}
        self.assertIsInstance(p_bad_side.diagnosis_text, str)

        # Patient guide jargon scrub (dated & undated)
        for op_dt in ('', (TODAY + datetime.timedelta(days=10)).isoformat()):
            p_jg = _go('open', q={
                'med_doac': True, 'med_chips': ['rivaroxaban_2_5', 'acei_arb', 'statin'],
                'sglt2': True, 'glp1': True, 'dm_insulin': True, 'ven_hormones': True,
            }, s={'planned_surgery_date': op_dt, 'liver_status': '3x'})
            ev_jg = evaluate(p_jg, today=TODAY)
            g = nurse_plan.build_patient_plain_explanation(p_jg, ev_jg, TODAY)
            blob = json.dumps(g, ensure_ascii=False)
            for jargon in ('DKA', 'NPH', 'POISE-3', 'HFpEF', 'ULN', 'VTE შეფასება'):
                self.assertNotIn(jargon, blob, f'Jargon {jargon} leaked for op_dt={op_dt}')

        # Clin A-S3-3: hormone med_schedule row annotated under VEN_VTE_CAPRINI_PROPHYLAXIS
        ev_h = evaluate(_go('evla', q={'ven_hormones': True}), today=TODAY)
        ann = annotate_med_rows(ev_h['med_schedule'])
        horm_item = next(x for x in ann if 'ჰორმონული' in x['drug'])
        self.assertEqual(horm_item['rule_meta']['id'], 'VEN_VTE_CAPRINI_PROPHYLAXIS')
        self.assertGreaterEqual(tuple(int(x) for x in REGISTRY_VERSION.split('.')), (1, 2, 9))

        # Combos A-S2-2: self-fill SGLT2 alone does NOT fabricate dm=True
        p_hf = _go('open', q={'dm': False, 'sglt2': False})
        apply_self_fill_post({'sglt2': '1', 'cad': '1'}, p_hf)
        self.assertFalse(p_hf.questionnaire.get('dm'))
        self.assertTrue(p_hf.questionnaire.get('sglt2'))

    def test_client_js_live_guards_comma_hb_and_creat_mismatch_parity(self):
        # Fuzzer A-S1-10 & Combos A-S2-9: run preop-patient.js & preop-nurse.js guards in Node.js
        pat_js = (settings.BASE_DIR / 'static' / 'js' / 'preop-patient.js').read_text(encoding='utf-8')
        nur_js = (settings.BASE_DIR / 'static' / 'js' / 'preop-nurse.js').read_text(encoding='utf-8')
        self.assertIn("replace(/,/g, '.')", pat_js)
        self.assertIn("u === 'umol'", pat_js)
        self.assertIn("cunit === 'umol'", nur_js)
        node_probe = r"""
        const fs = require('fs');
        const patSrc = fs.readFileSync(process.argv[1], 'utf8');
        const checks = {
            comma: patSrc.includes(".replace(/,/g, '.')"),
            inr135: patSrc.includes('1.35'),
            hbAmbig: patSrc.includes('22') && patSrc.includes('40') && patSrc.includes('260'),
            creatMismatch: patSrc.includes("u === 'umol'") && patSrc.includes("u === 'mgdl'"),
        };
        console.log(JSON.stringify(checks));
        """
        out = subprocess.check_output(
            ['node', '-e', node_probe, str(settings.BASE_DIR / 'static' / 'js' / 'preop-patient.js')],
            text=True,
        ).strip()
        self.assertEqual(json.loads(out), {'comma': True, 'inr135': True, 'hbAmbig': True, 'creatMismatch': True})


class PreopStressFixesR10BoardAndHTTPTests(TestCase):
    """Batches R10-3, R10-4 & R10-5 HTTP/DB tests: Board re-handoff, auth, signoff, DoS, self-fill, GET CSRF, pathway switch."""

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.doc = User.objects.create_user(username='r10_doc', password='DocPassword!1', first_name='დავით', last_name='ექიმი', is_staff=True)
        StaffProfile.objects.create(user=cls.doc, display_name='დავით ექიმი')
        cls.nurse = User.objects.create_user(username='r10_nurse', password='NursePassword!1', first_name='მარი', last_name='ექთანი', is_staff=False)

    def _create(self, sub='open', **kw):
        s_in = kw.get('s') or {}
        p = _go(sub, **kw)
        p.surgeon = kw.pop('surgeon', self.doc)
        p.visit_date = timezone.localdate()
        p.started_on = timezone.localdate()
        if 'labs_date' not in s_in:
            p.studies['labs_date'] = timezone.localdate().isoformat()
        if 'planned_surgery_date' not in s_in:
            p.studies['planned_surgery_date'] = (timezone.localdate() + datetime.timedelta(days=10)).isoformat()
        p.save()
        return p

    def test_board_rehandoff_dialysis_done_escalation_obsolete_cleanup_name_hijack_bp_and_ppi(self):
        # Fuzzer A-S1-1: CKD hydration -> Dialysis transition replaces hydration task cleanly
        p = self._create('evar', s={'creat': 160, 'bp_sys': 120, 'bp_dia': 70, 'bp_sys_l': 195, 'bp_dia_l': 115})
        bp, _, _ = admit_preop_to_board(p, self.doc)
        st = BoardEntry.objects.filter(patient=bp, kind=BoardEntry.STATUS).latest('id').text
        # Fuzzer A-S1-5 & Combos A-S2-7: bilateral BP prints BOTH arms
        self.assertIn('120/70', st)
        self.assertIn('195/115', st)

        p.questionnaire['on_dialysis'] = True
        p.save(update_fields=['questionnaire'])
        admit_preop_to_board(p, self.doc)
        open_tasks = list(BoardEntry.objects.filter(patient=bp, kind=BoardEntry.TASK, done_at__isnull=True).values_list('text', flat=True))
        self.assertTrue(any('პროგრამული დიალიზი:' in t for t in open_tasks))
        self.assertFalse(any('ნეფროპროტექცია:' in t for t in open_tasks))

        # Fuzzer A-S1-2: Done task must NOT suppress newly escalated HIT / vancomycin / hypernatremia task
        BoardEntry.objects.filter(patient=bp, kind=BoardEntry.TASK).update(done_at=timezone.now())
        p.questionnaire['on_dialysis'] = False
        p.questionnaire['allergy_chips'] = ['hit_heparin', 'beta_lactam', 'vancomycin']
        p.studies['na'] = 156
        p.save(update_fields=['questionnaire', 'studies'])
        admit_preop_to_board(p, self.doc)
        esc_tasks = list(BoardEntry.objects.filter(patient=bp, kind=BoardEntry.TASK, done_at__isnull=True).values_list('text', flat=True))
        self.assertTrue(any('HIT ანამნეზში!' in t for t in esc_tasks))
        self.assertTrue(any('კლინდამიცინით' in t for t in esc_tasks))
        self.assertTrue(any('ჰიპერნატრიემია' in t for t in esc_tasks))

        # Fuzzer A-S1-3: Removing risks & switching pathway retires obsolete active tasks
        p.op_subtype = PreopPatient.OpSubtype.CEA_SYMP
        p.pathway = PreopPatient.Pathway.CEA
        p.side = 'R'
        p.questionnaire['allergy_chips'] = []
        p.studies['na'] = 140
        p.studies['creat'] = 80
        p.save()
        admit_preop_to_board(p, self.doc)
        clean_tasks = list(BoardEntry.objects.filter(patient=bp, kind=BoardEntry.TASK, done_at__isnull=True).values_list('text', flat=True))
        self.assertTrue(any('CEA პოსტოპ.' in t for t in clean_tasks))
        self.assertFalse(any('პოსტოპ. ჰემოდინამიკის' in t for t in clean_tasks))
        self.assertFalse(any('HIT ანამნეზში!' in t for t in clean_tasks))

        # Fuzzer A-S1-4: >30 archived same-name patients must NEVER allow hijacking active owner's BoardPatient
        for _ in range(32):
            PreopPatient.objects.create(patient_name='გიორგი ბერიძე', archived=True, op_subtype='evla', pathway='venous')
        p_owner = self._create('open', side='')
        p_owner.patient_name = 'გიორგი ბერიძე'
        p_owner.save(update_fields=['patient_name'])
        bp_owner, _, _ = admit_preop_to_board(p_owner, self.doc)
        p_other = self._create('evla')
        p_other.patient_name = 'გიორგი ბერიძე'
        p_other.save(update_fields=['patient_name'])
        self.assertIsNone(resolve_board_patient(p_other))
        self.assertEqual(resolve_board_patient(p_owner).pk, bp_owner.pk)

        # Combos A-S2-8: EGD acute_ulcer with ulcer=False + med_aspirin emits Board PPI task
        p_ulc = self._create('open', q={'ulcer': False, 'ulcer_active_symp': True, 'med_aspirin': True, 'med_chips': ['ppi']}, s={'gastro_result': 'acute_ulcer'})
        bp_u, _, _ = admit_preop_to_board(p_ulc, self.doc)
        u_tasks = list(BoardEntry.objects.filter(patient=bp_u, kind=BoardEntry.TASK).values_list('text', flat=True))
        self.assertTrue(any('გასტროპროტექცია:' in t for t in u_tasks))

    def test_auth_signoff_ready_queue_dos_self_fill_get_csrf_and_pathway_switch(self):
        c_nurse = self.client_class()
        c_nurse.force_login(self.nurse)
        c_doc = self.client_class()
        c_doc.force_login(self.doc)

        # Fuzzer A-S1-6 & A-S2-5: Non-doctor nurse cannot approve unassigned patient OR sign off rules/vignettes
        p_unass = self._create('evla', surgeon=None)
        c_nurse.post(reverse('preop-patient', args=[p_unass.pk]), {'section': 'signoff', 'action': 'approve', 'move_ready': '1'})
        p_unass.refresh_from_db()
        self.assertIsNone(p_unass.approved_at)
        self.assertNotEqual(p_unass.someday_list, PreopPatient.SomedayList.READY)

        c_nurse.post(reverse('preop-rules'), {'rule_id': 'AAA_GIANT_MM', 'verdict': 'approved'})
        c_nurse.post(reverse('preop-vignettes'), {'vignette_id': 'V01_AAA_FEMALE_50MM', 'verdict': 'approved'})
        self.assertEqual(PreopRuleSignoff.objects.count(), 0)

        # Fuzzer A-S1-7: approve + move_ready=1 blocked on elapsed hold, contra med, or incomplete workup
        p_el = self._create('open', q={'med_doac': True}, s={'planned_surgery_date': (timezone.localdate() + datetime.timedelta(days=1)).isoformat()})
        c_doc.post(reverse('preop-patient', args=[p_el.pk]), {'section': 'signoff', 'action': 'approve', 'move_ready': '1'})
        p_el.refresh_from_db()
        self.assertIsNone(p_el.approved_at)

        p_cm = self._create('open', q={'med_doac': True, 'cad_chips': ['mech_valve']})
        c_doc.post(reverse('preop-patient', args=[p_cm.pk]), {'section': 'signoff', 'action': 'approve', 'move_ready': '1'})
        p_cm.refresh_from_db()
        self.assertIsNone(p_cm.approved_at)

        p_inc = self._create('open', s={'blood_reserved': False, 'anesth_done': False})
        c_doc.post(reverse('preop-patient', args=[p_inc.pk]), {'section': 'signoff', 'action': 'approve', 'move_ready': '1'})
        p_inc.refresh_from_db()
        self.assertNotEqual(p_inc.someday_list, PreopPatient.SomedayList.READY)
        self.assertFalse(can_enter_ready_queue(p_inc, evaluate(p_inc)))

        # Fuzzer A-S2-2 & A-S2-3: DoS cancelled_medical clears approval/ready; complete_and_board blocked on STOP
        p_can = self._create('open', approved_at=timezone.now(), approved_by=self.doc, someday_list=PreopPatient.SomedayList.READY)
        c_doc.post(reverse('preop-patient', args=[p_can.pk]), {'section': 'dos_vqi', 'case_outcome_status': 'cancelled_medical', 'dos_form_present': '1'})
        p_can.refresh_from_db()
        self.assertIsNone(p_can.approved_at)
        self.assertNotEqual(p_can.someday_list, PreopPatient.SomedayList.READY)

        p_stop = self._create('cea_symp', side='B')
        c_doc.post(reverse('preop-patient', args=[p_stop.pk]), {'section': 'dos_vqi', 'action': 'complete_and_board', 'dos_form_present': '1'})
        p_stop.refresh_from_db()
        self.assertNotEqual(p_stop.studies.get('case_outcome_status'), 'operated')
        self.assertIsNone(p_stop.studies.get('board_patient_id'))

        # Fuzzer A-S1-9 & A-S2-4: rotate clears self_link_sent_at; self-fill re-submission unconditionally clears med_hold_agreed & date
        from ..preop.utils import make_self_fill_token

        p_sf0 = self._create('open', q={'filled': False, 'nurse_verified': False}, s={'self_link_sent_at': '26.11.2026 10:00'})
        self.assertEqual(nurse_ui.self_fill_status_info(evaluate(p_sf0))['code'], 'sent')
        c_doc.post(reverse('preop-patient', args=[p_sf0.pk]), {'section': 'self_link', 'action': 'rotate'})
        p_sf0.refresh_from_db()
        self.assertEqual(p_sf0.studies.get('self_link_sent_at'), '')
        self.assertEqual(nurse_ui.self_fill_status_info(evaluate(p_sf0))['code'], 'unsent')

        p_sf = self._create('open', q={'med_doac': True}, s={'med_hold_agreed': True, 'med_hold_agreed_date': timezone.localdate().isoformat()})
        c_doc.post(reverse('preop-patient', args=[p_sf.pk]), {'section': 'self_link', 'action': 'rotate'})
        p_sf.refresh_from_db()
        tok = make_self_fill_token(p_sf)
        self.client_class().post(reverse('preop-self-fill', args=[tok]), {'med_doac': '1', 'dm': '1'})
        p_sf.refresh_from_db()
        self.assertFalse(p_sf.studies.get('med_hold_agreed'))
        self.assertEqual(p_sf.studies.get('med_hold_agreed_date'), '')

        # Fuzzer A-S2-6 & A-S3-4: GET /preop/new/?case=... does NOT mutate DB; POST /preop/new/ rejects CEA side='B'
        case = Case.objects.create(patient_name='ქეის პაციენტი', diagnosis='AAA 60mm', side='R', doctor=self.doc)
        before_cnt = PreopPatient.objects.count()
        c_doc.get(reverse('preop-new'), {'case': case.pk})
        self.assertEqual(PreopPatient.objects.count(), before_cnt)
        c_doc.post(reverse('preop-new'), {'patient_name': 'ბილატერალ ცეა', 'op_subtype': 'cea_symp', 'side': 'B'})
        self.assertFalse(PreopPatient.objects.filter(patient_name='ბილატერალ ცეა').exists())

        # Combos A-S1-2: Switching Venous -> non-Venous migrates ven_pregnant & ven_lidocaine_allergy so Q save cannot wipe them
        p_sw = self._create('evla', age=30, sex='F', q={'ven_pregnant': True, 'ven_lidocaine_allergy': True, 'ven_hormones': True})
        c_doc.post(reverse('preop-patient', args=[p_sw.pk]), {'section': 'header', 'patient_name': p_sw.patient_name, 'op_subtype': 'abf_claud', 'sex': 'F', 'age': '30'})
        p_sw.refresh_from_db()
        self.assertTrue(p_sw.questionnaire.get('pregnant_possible'))
        self.assertIn('lidocaine', p_sw.questionnaire.get('allergy_chips') or [])
        apply_questionnaire_post({'abf_claud_m': '200', 'pregnant_possible': '1', 'allergy_chips': ['lidocaine']}, p_sw, 'დავით ექიმი')
        self.assertTrue(p_sw.questionnaire.get('ven_hormones'))

        # Fuzzer A-S2-1: Nurse med-call on reconcile_only (GLP-1) shows called_valid=True in New UI
        p_glp = self._create('open', q={'glp1': True})
        c_nurse.post(reverse('preop-med-call', args=[p_glp.pk]), {'action': 'called'})
        p_glp.refresh_from_db()
        self.assertTrue(nurse_ui._med_call_status(evaluate(p_glp), timezone.localdate())['called_valid'])

    def test_template_parity_passport_venous_tab_doc_tab_q_and_audit_crescendo(self):
        self.client.force_login(self.doc)
        # Combos A-S2-6: Normal venous passport never screams iodine-unasked
        p_ven = self._create('evla', q={'allergy_iodine_asked': False})
        r_pp = self.client.get(reverse('preop-passport', args=[p_ven.pk]))
        self.assertNotContains(r_pp, 'იოდზე/კონტრასტზე ალერგია დასაზუსტებელია')

        # Fuzzer A-S3-3: _tab_doc renal tile shows unit mismatch warning
        p_cm = self._create('open', s={'creat': 88.4, 'creat_unit': 'mgdl'})
        r_doc = self.client.get(reverse('preop-patient', args=[p_cm.pk]), {'tab': 'doc'})
        self.assertContains(r_doc, 'ერთეული')

        # Combos A-S2-4: _tab_q shows sub-inf / sub-abdo / sub-ven-rec / sub-cea-symp when child flags set
        p_inf = self._create('open', q={'active_infection': False, 'inf_chips': ['dysuria'], 'prev_abdo_surg': False, 'abdo_chips': ['midline_lap']})
        r_q = self.client.get(reverse('preop-patient', args=[p_inf.pk]), {'tab': 'q'})
        self.assertNotContains(r_q, 'id="sub-inf" hidden')
        self.assertNotContains(r_q, 'id="sub-abdo" hidden')

        # Clin A-S2-7: Operated cea_asymp + cea_crescendo counts in NVR 14d audit & shows effective label
        p_cr = self._create(
            'cea_asymp', q={'cea_crescendo': True, 'cea_event_date': (timezone.localdate() - datetime.timedelta(days=200)).isoformat()},
            s={'case_outcome_status': 'operated', 'operated_date': timezone.localdate().isoformat()},
        )
        r_aud = self.client.get(reverse('preop-audit'))
        self.assertEqual(r_aud.context['cea_symp_operated_total'], 1)
        self.assertContains(r_aud, evaluate(p_cr)['effective_operation_label'])
