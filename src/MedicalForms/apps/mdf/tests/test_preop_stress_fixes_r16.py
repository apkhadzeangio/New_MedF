"""Regression tests for Stage 4 Adversarial Stress-Test Round 16 (Registry v1.2.15)."""
import datetime
import json
import subprocess
import time
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from ..models import PreopPatient, StaffProfile
from ..preop import (
    admit_preop_to_board,
    build_med_schedule,
    evaluate,
    generate_anamnesis,
    nurse_plan,
)
from ..preop.meds_freetext import (
    _detect_freetext_allergies,
    _detect_freetext_high_risk_meds,
    _has_unrecognized_allergy_clause,
    _positive_clauses_text,
)
from ..preop.registry import REGISTRY_VERSION, RULES_BY_ID, match_rule_for_text
from ..preop.views.board import _build_handoff_tasks

TODAY = datetime.date(2026, 12, 1)
OP_DATE = (TODAY + datetime.timedelta(days=10)).isoformat()
STATIC_JS_DIR = Path(__file__).resolve().parents[3] / 'static' / 'js'


def _mk(sub='open', *, age=66, sex='M', side='R', q=None, s=None, archived=False):
    pw = PreopPatient.SUBTYPE_PATHWAY[sub]
    base_q = {'filled': True, 'mets_ok': True}
    if q:
        base_q.update(q)
    base_s = {
        'planned_surgery_date': OP_DATE,
        'labs_date': TODAY.isoformat(),
        'weight_kg': 78,
        'height_cm': 174,
        'bp_sys': 126,
        'bp_dia': 78,
        'hr': 72,
        'spo2': 98,
        'hb': 134,
        'wbc': 7.1,
        'plt': 230,
        'inr': 1.0,
        'creat': 85,
        'glucose': 5.4,
        'k': 4.2,
        'na': 140,
        'ecg': 'normal',
        'cxr': 'normal',
        'lvef': 60,
    }
    if s:
        base_s.update(s)
    return PreopPatient(
        patient_name='ტესტ რაუნდთექვსმეტი',
        age=age,
        sex=sex,
        side=side,
        pathway=pw,
        op_subtype=sub,
        questionnaire=base_q,
        studies=base_s,
        visit_date=TODAY,
        started_on=TODAY,
        archived=archived,
    )


class Round16ClinicalSafetyTests(TestCase):
    """Clinical pharmacology, NLP, PLT/WBC Decimal precision, and cross-surface parity fixes (v1.2.15)."""

    def test_plt_and_wbc_decimal_floor_across_engine_standalone_asa_and_js(self):
        # 1. PLT 99.95, 99.99, 99600 never round up to 100 -> ASRA <100 blocks neuraxial + matches standalone
        for raw_plt in (99.95, 99.99, '99.996', 99600, 99999):
            p_nx = _mk('phlebectomy', q={'med_doac': True}, s={'plt': raw_plt, 'anesthesia_plan': 'neuraxial'})
            ev_nx = evaluate(p_nx, today=TODAY)
            self.assertLess(ev_nx['plt'], 100, raw_plt)
            self.assertTrue(ev_nx['neuraxial_hard_blocked'], raw_plt)
            self.assertEqual(ev_nx['clearance_seal']['code'], 'stop', raw_plt)

            p_auto = _mk('phlebectomy', q={'med_doac': True}, s={'plt': raw_plt, 'anesthesia_plan': 'auto'})
            ev_a = evaluate(p_auto, today=TODAY)
            st_a = build_med_schedule(p_auto, ev_a['q'], ev_a['s'], ev_a['egfr'], ev_a['crcl'], today=TODAY)
            self.assertEqual(ev_a['med_schedule'], st_a, f'standalone drift on plt={raw_plt}')

        # 2. PLT 49.6 / 49600 -> ASA IV (<50), 149.99 -> <150 amber
        for raw_low in (49.6, 49.99, 49600, 49999):
            ev_asa = evaluate(_mk('evla', s={'plt': raw_low}), today=TODAY)
            self.assertLess(ev_asa['plt'], 50, raw_low)
            self.assertEqual(ev_asa['auto_asa'], 'IV', raw_low)
        ev_149 = evaluate(_mk('evla', s={'plt': 149.99}), today=TODAY)
        self.assertLess(ev_149['plt'], 150)

        # 3. WBC 3496..3499 -> <3.5, 12001..12004 -> >12.0
        ev_wlo = evaluate(_mk('evla', s={'wbc': 3498}), today=TODAY)
        ev_whi = evaluate(_mk('evla', s={'wbc': 12002}), today=TODAY)
        self.assertLess(ev_wlo['wbc'], 3.5)
        self.assertGreater(ev_whi['wbc'], 12.0)

        # 4. Live Node.js VM check on preop-patient.js & preop-nurse.js PLT/WBC guards
        node_script = r"""
const fs = require('fs'), vm = require('vm');
function mkEl(tag, attrs) {
  const ls = {};
  const cls = new Set((attrs.className || '').split(/\s+/).filter(Boolean));
  return {
    tagName: tag.toUpperCase(), name: attrs.name || '', id: attrs.id || '', value: attrs.value || '',
    type: attrs.type || 'text', hidden: !!attrs.hidden, textContent: '', innerHTML: '', dataset: {},
    classList: { add: (...a) => a.forEach(c => cls.add(c)), remove: (...a) => a.forEach(c => cls.delete(c)), contains: c => cls.has(c) },
    get className() { return [...cls].join(' '); },
    set className(v) { cls.clear(); String(v).split(/\s+/).filter(Boolean).forEach(c => cls.add(c)); },
    _children: [], _parent: null,
    appendChild(c) { c._parent = this; this._children.push(c); return c; },
    querySelector(sel) {
      if (sel === '.fld-live-hint') return this._children.find(c => c.className.includes('fld-live-hint')) || null;
      return this._children.find(c => (sel.includes(c.name) && c.name)) || null;
    },
    querySelectorAll() { return []; },
    closest(sel) {
      let cur = this;
      while (cur) {
        if (sel === '.fld' && cur.className.includes('fld')) return cur;
        if (sel.startsWith('#') && cur.id === sel.slice(1)) return cur;
        cur = cur._parent;
      }
      return null;
    },
    addEventListener(ev, fn) { (ls[ev] = ls[ev] || []).push(fn); },
    dispatchEvent(ev) { (ls[ev.type] || []).forEach(fn => fn({ target: this })); },
  };
}
function runFile(path) {
  const fldPlt = mkEl('div', { className: 'fld' });
  const inpPlt = mkEl('input', { name: 'plt', value: '99500' });
  fldPlt.appendChild(inpPlt);
  const fldWbc = mkEl('div', { className: 'fld' });
  const inpWbc = mkEl('input', { name: 'wbc', value: '3498' });
  fldWbc.appendChild(inpWbc);
  const root = mkEl('div', { id: 'nw-patient-root' });
  const all = [inpPlt, inpWbc];
  const doc = {
    getElementById: id => id === 'nw-patient-root' ? root : null,
    querySelector: () => null,
    querySelectorAll: sel => all.filter(e => sel.includes('name="' + e.name + '"')),
    createElement: tag => mkEl(tag, {}),
    addEventListener: () => {},
  };
  const ctx = { document: doc, window: { addEventListener: () => {}, location: { search: '', pathname: '/' } }, Event: function(t){ this.type = t; }, localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} }, setTimeout, clearTimeout, console };
  vm.runInNewContext(fs.readFileSync(path, 'utf8'), ctx);
  const hPlt = fldPlt.querySelector('.fld-live-hint');
  const hWbc = fldWbc.querySelector('.fld-live-hint');
  const wbcLowCls = hWbc ? hWbc.className : '';
  inpWbc.value = '12002';
  inpWbc.dispatchEvent(new ctx.Event('input'));
  return { pltCls: hPlt ? hPlt.className : '', pltTxt: hPlt ? hPlt.textContent : '', wbcLowCls, wbcHiCls: hWbc ? hWbc.className : '' };
}
console.log(JSON.stringify({
  patient: runFile(process.argv[1]),
  nurse: runFile(process.argv[2]),
}));
"""
        proc = subprocess.run(
            ['node', '-e', node_script, str(STATIC_JS_DIR / 'preop-patient.js'), str(STATIC_JS_DIR / 'preop-nurse.js')],
            capture_output=True, text=True, check=True, timeout=15,
        )
        res = json.loads(proc.stdout)
        for key in ('patient', 'nurse'):
            self.assertIn('crit', res[key]['pltCls'], res)
            self.assertIn('<100', res[key]['pltTxt'], res)
            self.assertIn('crit', res[key]['wbcLowCls'], res)
            self.assertIn('crit', res[key]['wbcHiCls'], res)

    def test_freetext_nlp_linear_speed_sov_unpunctuated_and_contraindications(self):
        # 1. Active drug & allergy retention on tricky SOV / unpunctuated / compound sentences
        for txt, exp in (
            ('არ იღებს ასპირინს და იკეთებს კლექსანს', {'parenteral_oac'}),
            ('არ იღებს ასპირინს და აგრძელებს ვარფარინს', {'warfarin'}),
            ('stopped aspirin and started warfarin', {'warfarin'}),
            ('ვარფარინი 5მგ შეწყვეტის გარეშე', {'warfarin'}),
            ('eliquis 5mg not taking aspirin', {'doac'}),
            ('ვარფარინი 5მგ ასპირინს არ იღებს', {'warfarin'}),
            ('ვარფარინს სვამს ასპირინს არ იღებს', {'warfarin'}),
            ('ქსარელტოს 20მგ სვამს ასპირინს არ იღებს', {'doac'}),
            ('პლავიქსს იღებს ვარფარინს არ სვამს', {'clopidogrel'}),
            ('taking warfarin not taking aspirin', {'warfarin'}),
            ('ასპირინს არ სვამს ვარფარინს სვამს', {'warfarin'}),
            ('ასპირინი და პლავიქსი მოხსნილია', set()),
            ('ასპირინს და ვარფარინს არ იღებს', set()),
            ('stopped aspirin 100mg and clopidogrel 75mg', set()),
        ):
            self.assertEqual(_detect_freetext_high_risk_meds(txt)['matched_keys'], exp, txt)

        for txt, exp in (
            ('არ აქვს ალერგია იოდზე და აქვს პენიცილინზე', {'beta_lactam'}),
            ('no iodine allergy and has hit', {'hit_heparin'}),
            ('no allergy to latex and vancomycin allergy', {'vancomycin'}),
            ('პენიცილინზე არა ანაფილაქსიური ალერგია', {'beta_lactam'}),
            ('არა-სტეროიდული ალერგია', {'nsaid'}),
            ('ჰეპარინზე HIT აქვს იოდზე არ აქვს', {'hit_heparin'}),
            ('პენიცილინზე და ცეფაზოლინზე ალერგია არ აქვს', set()),
            ('პენიცილინზე და იოდზე ალერგია არ აქვს', set()),
            ('უარყოფს პენიცილინზე და იოდზე ალერგიას', set()),
            ('პენიცილინზე ალერგიული არ არის', set()),
            ('ალერგია არ არის', set()),
            ('არანაირი ალერგია', set()),
            ('ალერგია არ აწუხებს', set()),
        ):
            self.assertEqual(_detect_freetext_allergies(txt), exp, txt)
            if not exp:
                self.assertFalse(_has_unrecognized_allergy_clause(txt), txt)

        # 2. O(N) linear runtime on 20,000 spaces (ReDoS = 0)
        t0 = time.perf_counter()
        _positive_clauses_text('a' + ' ' * 20000 + 'b')
        self.assertLess(time.perf_counter() - t0, 0.1)

        # 3. HIT + free-text heparin/LMWH, liver_3x + free-text statin, GI bleed + free-text AT under STOP lock
        ev_hit = evaluate(_mk('open', q={'allergy_chips': ['hit_heparin'], 'other_meds': 'კლექსანი 40მგ'}), today=TODAY)
        hit_row = next(r for r in ev_hit['med_schedule'] if 'პარენტერული' in r['drug'])
        self.assertTrue(hit_row['contraindicated'])
        self.assertFalse(hit_row['hold_or_skip'])
        self.assertIn('HIT', hit_row['date_plan'])

        ev_st = evaluate(_mk('open', q={'other_meds': 'კრესტორი 20მგ'}, s={'liver_status': '3x'}), today=TODAY)
        self.assertTrue(any('სტატინი' in r['drug'] and r['contraindicated'] for r in ev_st['med_schedule']))
        self.assertTrue(any('ჰეპატოტოქსიკურობის' in rf for rf in ev_st['red_flags']))

        ev_gi_stop = evaluate(
            _mk('evla', q={'other_meds': 'ელიქვისი 5მგ', 'ulcer_active_symp': True}, s={'ven_duplex_status': 'acute_dvt'}),
            today=TODAY,
        )
        self.assertEqual(ev_gi_stop['clearance_seal']['code'], 'stop')
        gi_row = next(r for r in ev_gi_stop['med_schedule'] if 'დასაზუსტებელი წამალი' in r['drug'])
        self.assertIn('სისხლდენა', gi_row['date_plan'])

    def test_epidural_evla_hit_oac_preg_mech_valve_oac_sapt_pta_and_liver_renal(self):
        # 1. EVLA + warfarin + epidural -> NOT uninterrupted warfarin!
        ev_epi = evaluate(
            _mk('evla', q={'med_warfarin': True}, s={'inr': 2.4, 'anesthesia_plan': 'general', 'anesth_lines_chips': ['epidural'], 'ven_duplex_status': 'reflux_only'}),
            today=TODAY,
        )
        self.assertNotIn('გრძელდება მოუხსნელად', ev_epi['caprini_advice'])
        self.assertNotIn('გრძელდება მოუხსნელად', ev_epi['surgical_brief']['heparin_plan'])

        # 2. EVLA + warfarin + liver_3x -> consistent hold/caution across med_schedule and caprini
        ev_lv = evaluate(_mk('evla', q={'med_warfarin': True}, s={'inr': 2.4, 'liver_status': '3x', 'ven_duplex_status': 'reflux_only'}), today=TODAY)
        vka_lv = next(r for r in ev_lv['med_schedule'] if 'ვარფარინი' in r['drug'])
        self.assertNotIn('შესაძლებელია მოუხსნელად', vka_lv['rule'])

        # 3. Invalid dos_inr='abc' -> standalone parity & EVLA not declared safe
        p_bad_i = _mk('phlebectomy', q={'med_warfarin': True}, s={'inr': 2.4, 'dos_inr': 'abc'})
        ev_bi = evaluate(p_bad_i, today=TODAY)
        st_bi = build_med_schedule(
            p_bad_i, ev_bi['q'], ev_bi['s'], ev_bi['egfr'], ev_bi['crcl'],
            today=TODAY, stop_blocked=(ev_bi['clearance_seal']['code'] == 'stop'),
        )
        self.assertEqual(ev_bi['med_schedule'], st_bi)
        ev_evla_bi = evaluate(_mk('evla', q={'med_warfarin': True}, s={'inr': 2.4, 'dos_inr': 'abc', 'ven_duplex_status': 'reflux_only'}), today=TODAY)
        self.assertNotIn('გრძელდება მოუხსნელად', ev_evla_bi['caprini_advice'])

        # 4. HIT + ongoing OAC -> NO fondaparinux/DOAC double anticoagulation on surgical_brief or Board
        p_ho = _mk('evla', q={'med_warfarin': True, 'allergy_chips': ['hit_heparin']}, s={'inr': 2.4, 'ven_duplex_status': 'reflux_only'})
        ev_ho = evaluate(p_ho, today=TODAY)
        self.assertIn('ორმაგი ანტიკოაგულაციის გარეშე', ev_ho['surgical_brief']['heparin_plan'])
        b_ho = ' '.join(t for _, t in _build_handoff_tasks(ev_ho, ev_ho['q'], ev_ho['s'], ev_ho['pathway'], set(ev_ho['eff_allergy_chips']), ev_ho['eff_renal']))
        self.assertIn('ორმაგი ანტიკოაგულაციის გარეშე', b_ho)

        # 5. Pregnancy + Mech Valve + Dialysis with EXPLICIT post_antithrombotic_plan -> keeps LMWH accumulation warning
        ev_pmv = evaluate(
            _mk('bypass_claud', age=30, sex='F', q={'pregnant_possible': True, 'cad': True, 'cad_chips': ['mech_valve'], 'on_dialysis': True}, s={'post_antithrombotic_plan': 'voyager'}),
            today=TODAY,
        )
        self.assertIn('LMWH კუმულირდება', ev_pmv['post_antithrombotic_label'])

        # 6. Pregnancy + explicit oac_sapt (no preop OAC/mech valve) -> red flag + pregnancy warning
        ev_poac = evaluate(
            _mk('bypass_claud', age=29, sex='F', q={'pregnant_possible': True}, s={'post_antithrombotic_plan': 'oac_sapt'}),
            today=TODAY,
        )
        self.assertTrue(any('OAC + ანტიაგრეგანტი' in rf for rf in ev_poac['red_flags']))
        self.assertIn('უკუნაჩვენებია', ev_poac['post_antithrombotic_label'])

        # 7. PTA clopidogrel_intol in pregnancy / dialysis / mech_valve never recommends rivaroxaban 2.5 mg
        for extra_q in (
            {'pregnant_possible': True, 'med_doac': True},
            {'on_dialysis': True, 'med_warfarin': True},
            {'cad_chips': ['mech_valve'], 'med_warfarin': True},
        ):
            ev_pta = evaluate(_mk('pta_clti', age=30, sex='F', q={'clopidogrel_intol': True, **extra_q}), today=TODAY)
            clop_rf = ' '.join(rf for rf in ev_pta['red_flags'] if 'კლოპიდოგრელის აუტანლობა' in rf)
            self.assertNotIn('OAC / რივაროქსაბანი 2.5 მგ + ასპირინი', clop_rf)

        # 8. liver_3x + CrCl<30 AND saturated renal (age=130, creat=350) consistent across Caprini/venous/cardiac
        ev_lr = evaluate(_mk('evla', q={'on_dialysis': True, 'ven_dvt_history': True}, s={'liver_status': '3x', 'ven_duplex_status': 'reflux_only'}), today=TODAY)
        self.assertIn('ღვიძლის ≥3x', ev_lr['caprini_advice'])
        self.assertTrue(any('ღვიძლის ≥3x' in af for af in ev_lr['amber_flags']))
        ev_sat = evaluate(_mk('evla', age=130, q={'ven_dvt_history': True}, s={'creat': 350, 'ven_duplex_status': 'reflux_only'}), today=TODAY)
        self.assertFalse(any('(LMWH / DOAC)' in af for af in ev_sat['amber_flags']))

    def test_cea_hypotension_saccular_aaa_prior_stroke_today_sanitization_and_registry(self):
        # 1. CEA asymmetric-arm BP: R DBP=92 (no R SBP), L 85/55 -> hypotension red flag + no '85/92'
        ev_bp = evaluate(_mk('cea_asymp', s={'bp_sys': None, 'bp_dia': 92, 'bp_sys_l': 85, 'bp_dia_l': 55}), today=TODAY)
        self.assertTrue(any('ჰიპოტენზია (85/55' in rf for rf in ev_bp['red_flags']), ev_bp['red_flags'])
        self.assertFalse(any('85/92' in f for f in ev_bp['amber_flags'] + ev_bp['red_flags']))

        # 2. Saccular AAA <55mm scheduled 64d out -> emits 56-day wait warning
        wait64 = (TODAY + datetime.timedelta(days=64)).isoformat()
        ev_sac = evaluate(_mk('open', s={'aaa_mm': 42, 'aaa_morphology': 'saccular', 'planned_surgery_date': wait64}), today=TODAY)
        self.assertTrue(any('56 დღე' in af for af in ev_sac['amber_flags']), ev_sac['amber_flags'])

        # 3. Non-q.neuro CVD flags + future today propagation in calc_rcri / MINS / CARP / rules/meds
        future_td = datetime.date(2030, 6, 1)
        p_cvd = _mk(
            'evar', age=55,
            q={'cea_event_date': '2030-01-15', 'med_prasugrel': True},
            s={'aaa_mm': 58},
        )
        p_cvd.cardio_decision = 'stress'
        ev_cvd = evaluate(p_cvd, today=future_td)
        self.assertGreaterEqual(ev_cvd['rcri']['score'], 1)
        self.assertIn('MINS', ev_cvd['anesthetic_brief']['disposition'])
        self.assertFalse(ev_cvd['cardio_non_compliant'])
        self.assertTrue(ev_cvd['needs_echo_or_bnp'])

        # 4. 50k string, raw nested dict, and patient.cardiologist=NaN never leak
        huge = 'X' * 50000
        p_fuzz = _mk(
            'open',
            q={'other_meds': 'ელიქვისი 5მგ ' + huge, 'allergy_text': 'სულფა ' + huge, 'inf_chips': ['skin_ulcer'], 'infection_text': huge},
            s={'creat': {'a': float('nan')}, 'blood_group': huge, 'anesthesia_plan': huge, 'ecg': huge, 'cxr': huge},
        )
        p_fuzz.cardiologist = float('nan')
        ev_f = evaluate(p_fuzz, today=TODAY)
        for rf in ev_f['red_flags']:
            self.assertLess(len(rf), 600)
        self.assertLess(len(ev_f['allergy_summary']), 300)
        self.assertNotIsInstance(ev_f['creat_display_raw'], dict)
        self.assertNotIn('nan', str(ev_f['tasks']).lower())
        self.assertLess(len(generate_anamnesis(p_fuzz, ev_f)), 5000)

        # 5. GI-bleed antiplatelet-block amber attribution + NSAID jargon scrub + registry version
        self.assertEqual(REGISTRY_VERSION, '1.2.21')
        self.assertEqual(len(RULES_BY_ID), 83)
        ev_gi_ap = evaluate(_mk('pta_clti', q={'ulcer_active_symp': True}), today=TODAY)
        gi_amb = next(a for a in ev_gi_ap['amber_flags'] if 'ანტიაგრეგანტის დაწყება დაბლოკილია' in a)
        self.assertIsNotNone(match_rule_for_text(gi_amb))

        p_nsaid = _mk('open', q={'allergy_chips': ['nsaid'], 'other_meds': 'ასპირინი 100მგ'})
        ev_ns = evaluate(p_nsaid, today=TODAY)
        ph_ns = nurse_plan.build_phone_med_script(p_nsaid, ev_ns, TODAY)
        gd_ns = nurse_plan.build_patient_plain_explanation(p_nsaid, ev_ns, TODAY)
        vis_ph = ' '.join(
            f"{r['drug']} {r['spoken']}"
            for key in ('stop_rows', 'caution_rows', 'keep_rows')
            for r in ph_ns.get(key, ())
        )
        vis_gd = ' '.join(
            list(gd_ns.get('arrival_items', ()))
            + [gd_ns.get('fasting_plain', ''), gd_ns.get('emergency_sign', '')]
            + [f"{r['drug']} {r['spoken']}" for key in ('stop_meds', 'caution_meds', 'urgent_meds', 'keep_meds') for r in gd_ns.get(key, ())]
        )
        self.assertNotIn('NSAID', vis_ph)
        self.assertNotIn('NSAID', vis_gd)


class Round16HttpRbacAndWorkflowTests(TestCase):
    """Live HTTP verification of Stage-1 PLT POST, archive RBAC/gates, inactive outcomes, toasts & pediatric age."""

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.doc = User.objects.create_user(username='r16_doc', password='pw', first_name='ექიმი', last_name='R16')
        StaffProfile.objects.create(user=cls.doc, full_name='ექიმი R16')
        cls.nurse = User.objects.create_user(username='r16_nurse', password='pw', first_name='ექთანი', last_name='R16')

    def test_stage1_plt_99600_and_wbc_3498_http_post_no_roundup_bypass(self):
        c_doc = Client()
        c_doc.force_login(self.doc)
        p = _mk('phlebectomy', s={'anesthesia_plan': 'neuraxial', 'ven_duplex_status': 'reflux_only'})
        p.save()
        c_doc.post(reverse('preop-patient', args=[p.pk]), {'section': 'stage1', 's1_partial': '1', 'plt': '99600', 'wbc': '3498'})
        p.refresh_from_db()
        ev = evaluate(p, today=TODAY)
        self.assertLess(p.studies['plt'], 100)
        self.assertLess(ev['plt'], 100)
        self.assertTrue(ev['neuraxial_hard_blocked'])
        self.assertEqual(ev['clearance_seal']['code'], 'stop')
        self.assertLess(ev['wbc'], 3.5)

    def test_archived_rbac_and_all_inactive_outcome_mutation_gates(self):
        c_nurse, c_doc = Client(), Client()
        c_nurse.force_login(self.nurse)
        c_doc.force_login(self.doc)
        tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
        stale = (datetime.date.today() - datetime.timedelta(days=45)).isoformat()
        p = _mk('open', q={'med_warfarin': True}, s={'aaa_mm': 58, 'planned_surgery_date': tomorrow, 'labs_date': stale})
        p.save()

        # 1. Plain nurse CANNOT archive patient (HTTP 403 on AJAX)
        r_arch = c_nurse.post(reverse('preop-move', args=[p.pk]), {'action': 'archive'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r_arch.status_code, 403)
        p.refresh_from_db()
        self.assertFalse(p.archived)

        # 2. Even after doctor archives p, ALL clinical mutation gates stay blocked!
        c_doc.post(reverse('preop-move', args=[p.pk]), {'action': 'archive'})
        p.refresh_from_db()
        self.assertTrue(p.archived)
        c_doc.post(reverse('preop-med-call', args=[p.pk]), {'action': 'called'})
        c_doc.post(reverse('preop-patient', args=[p.pk]), {'section': 'signoff', 'action': 'approve'})
        c_doc.post(reverse('preop-patient', args=[p.pk]), {'section': 'signoff', 'action': 'ack_alerts'})
        c_doc.post(reverse('preop-admit-board', args=[p.pk]))
        c_doc.post(reverse('preop-patient', args=[p.pk]), {'section': 'dos_vqi', 'action': 'complete_and_board', 'dos_partial': '1'})
        p.refresh_from_db()
        self.assertIsNone(p.approved_at)
        self.assertIsNone(p.alerts_ack_at)
        self.assertFalse(p.studies.get('med_hold_agreed'))
        self.assertIsNone(p.studies.get('board_patient_id'))

        # 3. Operated & deferred outcomes block ack_alerts, unapprove, med-call clear, reschedule & hide buttons in _tab_doc
        p_op = _mk('evla', q={'med_warfarin': True}, s={
            'ven_duplex_status': 'reflux_only',
            'case_outcome_status': 'operated',
            'nurse_med_called_at': '01.12.2026 09:00',
            'med_hold_agreed': True,
        })
        p_op.approved_at = datetime.datetime(2026, 12, 1, 9, 0, tzinfo=datetime.UTC)
        p_op.approved_by = self.doc
        p_op.save()
        c_doc.post(reverse('preop-patient', args=[p_op.pk]), {'section': 'signoff', 'action': 'unapprove'})
        c_doc.post(reverse('preop-patient', args=[p_op.pk]), {'section': 'signoff', 'action': 'ack_alerts'})
        c_doc.post(reverse('preop-med-call', args=[p_op.pk]), {'action': 'clear'})
        p_op.refresh_from_db()
        self.assertIsNotNone(p_op.approved_at)
        self.assertTrue(p_op.studies.get('med_hold_agreed'))
        self.assertEqual(p_op.studies.get('nurse_med_called_at'), '01.12.2026 09:00')
        doc_html = c_doc.get(reverse('preop-patient', args=[p_op.pk]) + '?tab=doc').content.decode('utf-8')
        self.assertNotIn('value="unapprove"', doc_html)
        self.assertNotIn('value="ack_alerts"', doc_html)

        for st_out in ('deferred', 'cancelled_logistics'):
            p_def = _mk('evla', s={'ven_duplex_status': 'reflux_only', 'case_outcome_status': st_out, 'planned_surgery_date': OP_DATE})
            p_def.save()
            c_doc.post(reverse('preop-reschedule', args=[p_def.pk]), {'action': 'shift_3d'})
            p_def.refresh_from_db()
            self.assertEqual(p_def.studies['planned_surgery_date'], OP_DATE, st_out)

    def test_ready_demotion_toast_and_pediatric_age_contract(self):
        c_doc = Client()
        c_doc.force_login(self.doc)
        # 1. READY approved patient edits age -> demoted to CONF with warning toast, NO success toast
        p_rdy = _mk('evla', age=50, s={'ven_duplex_status': 'reflux_only', 'labs_date': datetime.date.today().isoformat()})
        p_rdy.approved_at = datetime.datetime(2026, 12, 1, 9, 0, tzinfo=datetime.UTC)
        p_rdy.approved_by = self.doc
        p_rdy.someday_list = PreopPatient.SomedayList.READY
        p_rdy.visit_date = None
        p_rdy.save()
        with mock.patch('django.utils.timezone.localdate', return_value=TODAY):
            resp = c_doc.post(
                reverse('preop-patient', args=[p_rdy.pk]),
                {'section': 'header', 'patient_name': p_rdy.patient_name, 'age': '55', 'sex': 'M', 'op_subtype': 'evla', 'side': 'R'},
                follow=True,
            )
        p_rdy.refresh_from_db()
        self.assertEqual(p_rdy.someday_list, PreopPatient.SomedayList.CONF)
        self.assertIsNone(p_rdy.approved_at)
        msgs = [(m.level_tag, str(m)) for m in resp.context['messages']]
        self.assertTrue(any(lvl == 'warning' for lvl, _ in msgs), msgs)
        self.assertFalse(any(lvl == 'success' for lvl, _ in msgs), msgs)

        # 2. Pediatric age (<12) rejected consistently on PreopNurseRegisterView (400) & ev['age'] is None
        r_ped = c_doc.post(reverse('preop-nurse-register'), {'patient_name': 'ბავშვი ტესტი', 'age': '5', 'op_subtype': 'evla', 'sex': 'M'})
        self.assertEqual(r_ped.status_code, 400)
        self.assertIsNone(evaluate(_mk('evla', age=5), today=TODAY)['age'])

        # 3. Clean scheduled patient still admits to board cleanly (no over-blocking)
        p_ok = _mk('evla', s={'ven_duplex_status': 'reflux_only', 'labs_date': datetime.date.today().isoformat()})
        p_ok.save()
        bp, created, _ = admit_preop_to_board(p_ok, self.doc)
        self.assertTrue(created)
        self.assertIsNotNone(bp.pk)
