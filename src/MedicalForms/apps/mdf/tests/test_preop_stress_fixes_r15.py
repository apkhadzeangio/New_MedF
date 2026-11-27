"""Round-15 adversarial clinical stress-test regression suite (Registry v1.2.14)."""
import dataclasses
import datetime
import json
import re
import subprocess
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import Client, TestCase
from django.urls import reverse

from ..models import BoardEntry, PreopPatient, StaffProfile
from ..preop import (
    build_med_schedule,
    evaluate,
    generate_anamnesis,
    make_self_fill_token,
)
from ..preop.meds_freetext import _detect_freetext_high_risk_meds
from ..preop.nurse_plan import build_patient_plain_explanation, build_phone_med_script
from ..preop.registry import RULES_BY_ID
from ..preop.views.board import admit_preop_to_board
from ..preop.vignettes import VIGNETTES_BY_ID, evaluate_vignette

TODAY = datetime.date(2026, 11, 27)
OP_DATE = '2026-12-10'
BASE_LABS_DATE = '2026-11-25'


def _clean_patient(op_subtype='evla', age=64, sex='M', side='R', q=None, s=None):
    pw = PreopPatient.SUBTYPE_PATHWAY[op_subtype]
    q_d = {'filled': True, 'mets_ok': True, 'allergy_iodine_asked': True}
    if q:
        q_d.update(q)
    s_d = {
        'hb': 136, 'plt': 230, 'inr': 1.0, 'creat': 80, 'creat_unit': 'umol',
        'glucose': 5.2, 'k': 4.2, 'bp_sys': 126, 'bp_dia': 76, 'hr': 72, 'spo2': 98,
        'weight_kg': 76, 'height_cm': 172, 'ecg': 'normal', 'labs_date': BASE_LABS_DATE,
        'planned_surgery_date': OP_DATE,
    }
    if op_subtype in ('evar', 'open'):
        s_d.update({'aaa_mm': 58, 'cta_date': '2026-10-15', 'cta_disc': True})
    if s:
        s_d.update(s)
    return PreopPatient(
        patient_name='სინთეზური R15 პაციენტი',
        age=age, sex=sex, side=side, pathway=pw, op_subtype=op_subtype,
        visit_date=TODAY, started_on=TODAY, questionnaire=q_d, studies=s_d,
    )


class Round15ClinicalSafetyTests(TestCase):
    """Covers Round-14 Clin + Combos S1/S2/S3 clinical logic, free-text & registry fixes."""

    def test_venous_full_oac_no_double_anticoagulation(self):
        # EVLA on uninterrupted Warfarin (INR 2.4, high Caprini) & EVLA/Phleb on DOAC/Dabi
        cases = [
            ('evla', {'med_warfarin': True, 'ven_dvt_history': True}, {'inr': 2.4}),
            ('evla', {'med_doac': True, 'ven_dvt_history': True}, {}),
            ('phlebectomy', {'med_dabigatran': True, 'ven_dvt_history': True}, {}),
        ]
        for sub, q_ex, s_ex in cases:
            p = _clean_patient(
                sub, age=68, sex='M', side='R', q=q_ex,
                s={'ven_duplex_status': 'reflux_only', 'abi_r': 1.0, **s_ex},
            )
            ev = evaluate(p, today=TODAY)
            vte_tasks = [t['label'] for t in ev['tasks'] if t['key'] == 'vte']
            cap_amb = [a for a in ev['amber_flags'] if 'Caprini' in a]
            surfaces = [
                ev['caprini_advice'],
                ev['surgical_brief']['heparin_plan'],
                ev['surgical_brief']['postop_antithrombotic'],
                *vte_tasks,
                *cap_amb,
            ]
            for txt in surfaces:
                self.assertIn('ორმაგი ანტიკოაგულაციის გარეშე', txt, (sub, q_ex, txt))
                self.assertNotIn('LMWH / DOAC 7–10 დღე', txt, (sub, q_ex, txt))

    def test_pregnancy_mech_valve_ckd_beta_lactam_abx_and_board_liver_ckd(self):
        doc_u = get_user_model().objects.create_user(username='r15_clin_doc', password='pw')
        # 1. Pregnancy + Mechanical Valve + CrCl<30 / dialysis (no HIT) -> UFH/VKA + LMWH accumulation warning
        for sub, extra_q, extra_s in (
            ('abf_clti', {'on_dialysis': True}, {}),
            ('pta_clti', {}, {'creat': 340, 'creat_unit': 'umol'}),
            ('open', {'on_dialysis': True}, {}),
            ('evla', {}, {'creat': 340, 'creat_unit': 'umol'}),
        ):
            p = _clean_patient(
                sub, age=32, sex='F', side='R',
                q={'ven_pregnant': True, 'cad_chips': ['mech_valve'], 'med_warfarin': True, 'ven_dvt_history': True, **extra_q},
                s={'abi_r': 0.8, 'pta_lesion_site': 'femoropopliteal', 'ven_duplex_status': 'reflux_only', **extra_s},
            )
            p.save()
            ev = evaluate(p, today=TODAY)
            bp = admit_preop_to_board(p, doc_u)[0]
            b_tasks = ' | '.join(BoardEntry.objects.filter(patient=bp, kind=BoardEntry.TASK).values_list('text', flat=True))
            check_blobs = [ev['surgical_brief']['postop_antithrombotic'], ev['caprini_advice'], b_tasks]
            if sub in ('abf_clti', 'pta_clti'):
                check_blobs.append(ev['post_antithrombotic_label'])
            if sub == 'pta_clti':
                check_blobs.extend(t['label'] for t in ev['tasks'] if t['key'] == 'dapt')
            if sub == 'evla':
                check_blobs.extend(t['label'] for t in ev['tasks'] if t['key'] == 'vte')
                check_blobs.extend(a for a in ev['amber_flags'] if 'Caprini' in a)
            for txt in check_blobs:
                self.assertIn('LMWH კუმულირდება', txt, (sub, txt))
                self.assertNotIn('VKA vs LMWH/UFH', txt, (sub, txt))

        # 2. Pregnancy + beta-lactam allergy -> Aztreonam ONLY (no gentamicin/ciprofloxacin recommendation)
        for sub, extra_q, extra_s in (
            ('evar', {}, {}),
            ('open', {'aaa_pain': True}, {}),
            ('abf_clti', {'on_dialysis': True, 'abf_wet_infection': True}, {}),
        ):
            p_abx = _clean_patient(
                sub, age=31, sex='F', side='R',
                q={'pregnant_possible': True, 'allergy_chips': ['beta_lactam'], **extra_q},
                s=extra_s,
            )
            p_abx.save()
            ev_abx = evaluate(p_abx, today=TODAY)
            abx_s = ev_abx['surgical_brief']['abx_regimen']
            bp_abx = admit_preop_to_board(p_abx, doc_u)[0]
            b_abx = ' | '.join(BoardEntry.objects.filter(patient=bp_abx, kind=BoardEntry.TASK).values_list('text', flat=True))
            for txt in (abx_s, b_abx):
                self.assertIn('აზტრეონამი 2 გ IV', txt, sub)
                self.assertIn('გენტამიცინი და ფტორქინოლონები', txt, sub)
                self.assertNotIn('+ გენტამიცინი', txt, sub)
                self.assertNotIn('ან ციპროფლოქსაცინი', txt, sub)

        # 3. Board VTE task: liver_status='3x' + on_dialysis / CrCl<30 -> UFH + IPC + LMWH accumulation warning
        p_lc = _clean_patient('open', age=66, sex='M', q={'on_dialysis': True}, s={'liver_status': '3x'})
        p_lc.save()
        bp_lc = admit_preop_to_board(p_lc, doc_u)[0]
        vte_b = [t for t in BoardEntry.objects.filter(patient=bp_lc, kind=BoardEntry.TASK).values_list('text', flat=True) if 'VTE' in t]
        self.assertTrue(vte_b and 'კანქვეშა UFH + IPC' in vte_b[0] and 'LMWH კუმულირდება' in vte_b[0])

    def test_freetext_sov_negation_conjunction_hyphen_and_extended_vocab(self):
        med_cases = [
            ('ასპირინს არ იღებს და ვარფარინს სვამს', {'warfarin'}),
            ('ელიქვისი 5მგ და ასპირინს არ იღებს', {'doac'}),
            ('eliquis 5mg and not taking aspirin', {'doac'}),
            ('ვარფარინი 5მგ და ასპირინი მოხსნილია', {'warfarin'}),
            ('warfarin 5mg and aspirin stopped', {'warfarin'}),
            ('არ იღებს ასპირინს და კლოპიდოგრელს', set()),
            ('ვარფარინი 5მგ სისხლდენის გარეშე', {'warfarin'}),
            ('ასპირინი მოუხსნეს', set()),
            ('ვარფარინი შეუწყვიტეს', set()),
            ('ასპირინს ვერ იღებს', set()),
            ('aspirin held', set()),
        ]
        for txt, expected in med_cases:
            ft = _detect_freetext_high_risk_meds(txt, q={})
            self.assertEqual(ft['matched_keys'], expected, f'failed for {txt!r}')

        # Hyphenated ara-anaphylactic beta-lactam allergy + conjunction allergy split
        for alg in (
            'პენიცილინზე არა-ანაფილაქსიური ალერგია',
            'პენიცილინზე ალერგია და იოდზე არ აქვს',
            'პენიცილინზე ალერგია (ანაფილაქსიის გარეშე)',
        ):
            p = _clean_patient('evar', q={'allergy_text': alg})
            ev = evaluate(p, today=TODAY)
            self.assertIn('beta_lactam', ev['eff_allergy_chips'], alg)
            self.assertNotIn('iodine', ev['eff_allergy_chips'], alg)
            self.assertIn('ცეფაზოლინი აკრძალულია', ev['surgical_brief']['abx_regimen'], alg)

        # Extended Georgian allergy denials remain clean across Tab Q HTML round-trip
        doc = get_user_model().objects.create_user(username='r15_alg_doc', password='pw')
        StaffProfile.objects.create(user=doc, display_name='ექიმი R15')
        c = Client()
        c.force_login(doc)
        for neg_alg in ('არ აღენიშნება ალერგია', 'ალერგიის გარეშე', 'არ უფიქსირდება ალერგია', 'ალერგია არაქვს'):
            p_n = _clean_patient('evla', q={'allergy_text': neg_alg}, s={'ven_duplex_status': 'reflux_only', 'abi_r': 1.0})
            p_n.save()
            url = reverse('preop-patient', args=[p_n.pk])
            c.post(url, {'action': 'save_q', 'mets_ok': '1', 'allergy_iodine_asked': '1', 'allergy_text': neg_alg})
            p_n.refresh_from_db()
            self.assertFalse(p_n.questionnaire.get('allergy_other'), neg_alg)
            html = c.get(url + '?tab=q').content.decode('utf-8')
            m = re.search(r'<input[^>]*name="allergy_other"[^>]*>', html)
            self.assertIsNotNone(m)
            self.assertNotIn('checked', m.group(0), neg_alg)
            self.assertFalse(evaluate(p_n, today=TODAY)['has_blocking_unknown_state'], neg_alg)

    def test_anamnesis_age_rcri_stroke_plt_rounding_aaa_day7_and_ft_gi_bleed(self):
        # 1. Valid age exposed in ev['age'] and generate_anamnesis(); invalid still omitted
        p_ok = _clean_patient('evar', age=64)
        ev_ok = evaluate(p_ok, today=TODAY)
        self.assertEqual(ev_ok['age'], 64)
        self.assertTrue(generate_anamnesis(p_ok, ev_ok).startswith('64 წ. '))
        p_bad = _clean_patient('evar', age=999)
        self.assertIsNone(evaluate(p_bad, today=TODAY)['age'])
        self.assertNotIn('999', generate_anamnesis(p_bad, evaluate(p_bad, today=TODAY)).splitlines()[0])

        # 2. calc_rcri aligns with has_prior_stroke_or_tia() (disabling stroke / thrombolysis / remote >180d)
        for q_ex in (
            {'cea_disabling_stroke': True},
            {'cea_thrombolysis': True},
            {'cea_event_date': '2025-01-01', 'cea_event_chips': ['minor_stroke']},
        ):
            p_r = _clean_patient('cea_asymp', age=68, q=q_ex, s={'carotid_r': 80})
            ev_r = evaluate(p_r, today=TODAY)
            self.assertGreaterEqual(ev_r['rcri']['score'], 1, q_ex)
            self.assertTrue(any('ინსულტი' in f for f in ev_r['rcri']['factors']), q_ex)

        # 3. PLT 99,600 / 99,999 /uL and 99.6 stay <100, block neuraxial, and match standalone build_med_schedule
        for raw_plt in (99600, 99999, 99.6):
            p_pl = _clean_patient('phlebectomy', q={'med_doac': True}, s={'anesthesia_plan': 'auto', 'plt': raw_plt})
            ev_pl = evaluate(p_pl, today=TODAY)
            self.assertLess(ev_pl['plt'], 100, raw_plt)
            self.assertFalse(ev_pl['has_neuraxial_plan'], raw_plt)
            st = build_med_schedule(p_pl, ev_pl['q'], ev_pl['s'], ev_pl['egfr'], crcl=ev_pl['crcl'], has_neuraxial_override=None, today=TODAY)
            self.assertEqual([(r['drug'], r['date_plan'], r['rule']) for r in st], [(r['drug'], r['date_plan'], r['rule']) for r in ev_pl['med_schedule']])

        # 4. AAA Day 7/7 prep warning stays active (monotonic 5->6->7 amber, 8 red)
        p_d7 = _clean_patient('evar')
        p_d7.started_on = TODAY - datetime.timedelta(days=6)  # inclusive day 7 of 7
        ev_d7 = evaluate(p_d7, today=TODAY)
        self.assertEqual((ev_d7['days_elapsed'], ev_d7['target_days']), (7, 7))
        self.assertTrue(any('მე-7 დღე' in a for a in ev_d7['amber_flags']))

        # 5. Active GI bleed + free-text antithrombotic sets contraindicated=True
        p_fg = _clean_patient('evar', q={'other_meds': 'ელიქვისი 5მგ'}, s={'gastro_result': 'acute_ulcer'})
        ft_rows = [r for r in evaluate(p_fg, today=TODAY)['med_schedule'] if r.get('unstructured')]
        self.assertEqual(len(ft_rows), 1)
        self.assertTrue(ft_rows[0]['contraindicated'])
        self.assertFalse(ft_rows[0]['hold_or_skip'])
        self.assertIn('სისხლდენ', ft_rows[0]['rule'])

    def test_jargon_scrub_phone_script_and_patient_guide_plus_canonical_inr_evla(self):
        banned = (
            'CYP2C19', 'CYP2C9', 'P-gp', 'LMWH', 'UFH', 'HIT', 'CrCl', 'eGFR', 'NPH',
            'VTE', 'SBP', 'HR<', 'ASRA', 'ESVS', 'ESC', 'EHRA', 'PAUSE', 'CHEST', 'ESUR',
            'FDA', 'Multi-Society', 'DOAC', 'SGLT2', 'GLP-1', 'INR', 'aPTT', 'anti-Xa',
            'AFib', 'EVLA', 'PTA', 'DAPT', 'Class III', 'პერიოპერაციულად', '()', ' / CCB', '(PPI)',
        )
        profiles = [
            ('pta_clti', {'med_clopidogrel': True, 'med_chips': ['ppi']}, {'planned_surgery_date': ''}),
            ('open', {'med_warfarin': True, 'cad_chips': ['mech_valve'], 'allergy_chips': ['hit_heparin']}, {'planned_surgery_date': OP_DATE}),
            ('evar', {'med_doac': True, 'sglt2': True, 'glp1': True, 'dm_insulin': True, 'metformin': True, 'med_chips': ['acei_arb', 'ccb', 'amiodarone', 'steroid']}, {'creat': 340, 'planned_surgery_date': ''}),
            ('evla', {'ven_hormones': True, 'med_warfarin': True}, {'inr': 2.4, 'planned_surgery_date': OP_DATE}),
            ('pta_clti', {'med_clopidogrel': True, 'med_ticagrelor': True, 'med_prasugrel': True}, {'planned_surgery_date': ''}),
        ]
        for sub, q_ex, s_ex in profiles:
            p = _clean_patient(sub, age=60, sex='F', q=q_ex, s=s_ex)
            ev = evaluate(p, today=TODAY)
            guide = build_patient_plain_explanation(p, ev, today=TODAY)
            phone = build_phone_med_script(p, ev, today=TODAY)
            for grp in ('stop_meds', 'caution_meds', 'keep_meds'):
                for row in guide[grp]:
                    blob = f"{row['drug']} {row['spoken']}"
                    for tok in banned:
                        self.assertNotIn(tok, blob, ('guide', sub, tok, blob))
            for grp in ('stop_rows', 'caution_rows', 'keep_rows'):
                for row in phone[grp]:
                    blob = f"{row['drug']} {row['spoken']}"
                    for tok in banned:
                        self.assertNotIn(tok, blob, ('phone', sub, tok, blob))

        # Single canonical inr_evla_max owner & V22 mutation kill
        self.assertNotIn('inr_evla', RULES_BY_ID['LAB_WBC_PLT_COAG'].value)
        self.assertNotIn('inr_evla', RULES_BY_ID['DOS_WHO_SIGNIN_THRESHOLDS'].value)
        v22 = VIGNETTES_BY_ID['V22_VEN_EVLA_ON_WARFARIN_CONTINUE']
        self.assertTrue(evaluate_vignette(v22)['passed'])
        mut_vka = dataclasses.replace(
            RULES_BY_ID['MED_WARFARIN_HOLD_DAYS'],
            value={**RULES_BY_ID['MED_WARFARIN_HOLD_DAYS'].value, 'inr_evla_max': 2.0},
        )
        with mock.patch.dict(RULES_BY_ID, {'MED_WARFARIN_HOLD_DAYS': mut_vka}):
            self.assertFalse(evaluate_vignette(v22)['passed'])


class Round15WorkflowGatesToastsAndJsTests(TestCase):
    """Covers Round-14 Fuzz + Combos DoS gates, reschedule, toasts, Self-Fill, _tab_doc & JS toggles."""

    def setUp(self):
        User = get_user_model()
        self.nurse = User.objects.create_user(username='r15_nurse', password='pw')
        self.doc = User.objects.create_user(username='r15_doc', password='pw')
        StaffProfile.objects.create(user=self.doc, display_name='ქირურგი R15')

    def test_complete_and_board_med_hold_reschedule_and_tab_doc_inactive_outcomes(self):
        c = Client()
        c.force_login(self.doc)
        for out_st in ('deferred', 'cancelled_medical'):
            p = _clean_patient('evla', q={'med_doac': True}, s={
                'case_outcome_status': out_st, 'ven_duplex_status': 'reflux_only',
                'abi_r': 1.0, 'compression_ready': True, 'anesth_cleared': True,
            })
            p.save()
            url_pat = reverse('preop-patient', args=[p.pk])
            # 1. complete_and_board with case_outcome_status='scheduled' MUST stay blocked
            c.post(url_pat, {'section': 'dos_vqi', 'action': 'complete_and_board', 'case_outcome_status': 'scheduled'})
            p.refresh_from_db()
            self.assertEqual(p.studies.get('case_outcome_status'), out_st)
            self.assertFalse(p.studies.get('board_patient_id'))

            # 2. med_hold_agreed via stage2 AND /med-call/ MUST be blocked
            c.post(url_pat, {'section': 'stage2', 'med_hold_agreed': '1'})
            p.refresh_from_db()
            self.assertFalse(p.studies.get('med_hold_agreed'))
            c.post(reverse('preop-med-call', args=[p.pk]), {'action': 'mark_called'})
            p.refresh_from_db()
            self.assertFalse(p.studies.get('med_hold_agreed'))

            # 3. _tab_doc.html disables #btn-surgeon-approve
            html_doc = c.get(url_pat + '?tab=doc').content.decode('utf-8')
            m = re.search(r'<button[^>]*id="btn-surgeon-approve"[^>]*>', html_doc)
            self.assertIsNotNone(m)
            self.assertIn('disabled', m.group(0), out_st)

        # 4. PreopNurseRescheduleView blocked on cancelled_medical & operated
        for out_st in ('cancelled_medical', 'operated'):
            p_rs = _clean_patient('evla', s={'case_outcome_status': out_st, 'planned_surgery_date': OP_DATE})
            p_rs.save()
            c.post(reverse('preop-reschedule', args=[p_rs.pk]), {'action': 'shift_3d'})
            p_rs.refresh_from_db()
            self.assertEqual(p_rs.studies.get('planned_surgery_date'), OP_DATE, out_st)

    def test_dos_warnings_sanitized_single_toasts_self_fill_parity_and_js_deferred(self):
        # 1. _eval_dos_and_vqi sanitizes NaN / nested containers / huge strings
        for bad_val in (float('nan'), {'a': float('nan')}, [float('inf')], 'X' * 50000):
            p_d = _clean_patient('evar', s={'dos_bp_sys': bad_val})
            dw = ' '.join(evaluate(p_d, today=TODAY)['dos_warnings'])
            self.assertNotIn('nan', dw.lower())
            self.assertNotIn('inf', dw.lower())
            self.assertLess(len(dw), 500)

        # 2. Header & stage2 rejections emit error-only toast (no dual error+success toast!)
        c = Client()
        c.force_login(self.doc)
        p_t = _clean_patient('cea_symp', side='R', q={'med_doac': True}, s={'carotid_r': 80, 'case_outcome_status': 'deferred'})
        p_t.save()
        url_t = reverse('preop-patient', args=[p_t.pk])
        for payload in (
            {'section': 'header', 'patient_name': p_t.patient_name, 'op_subtype': 'cea_symp', 'side': 'B'},
            {'section': 'header', 'patient_name': p_t.patient_name, 'op_subtype': 'cea_symp', 'surgeon': str(self.nurse.pk)},
            {'section': 'header', 'patient_name': p_t.patient_name, 'op_subtype': 'cea_symp', 'slot_kind': 'someday:ready'},
            {'section': 'stage2', 'med_hold_agreed': '1'},
        ):
            resp = c.post(url_t, payload)
            tags = [m.level_tag for m in get_messages(resp.wsgi_request)]
            self.assertIn('error', tags, payload)
            self.assertNotIn('success', tags, payload)

        # 3. Self-Fill GET/POST parity on unrecognized/corrupt outcome string
        p_sf = _clean_patient('evla', q={'filled': False}, s={'case_outcome_status': 'corrupt_val_xyz'})
        p_sf.save()
        tok = make_self_fill_token(p_sf)
        sf_url = reverse('preop-self-fill', args=[tok])
        c_anon = Client()
        self.assertIn('<form', c_anon.get(sf_url).content.decode('utf-8'))
        r_post = c_anon.post(sf_url, {'mets_ok': '1'})
        self.assertEqual(r_post.status_code, 302)
        self.assertNotIn('locked=1', r_post['Location'])

        # 4. Live Node check: preop-patient.js & preop-nurse.js keep #wrap-cancel-reasons visible on 'deferred'
        root_dir = Path(__file__).resolve().parents[3]
        res = subprocess.run(
            ['node', '-e', _NODE_DEFERRED_TOGGLE_SCRIPT,
             str(root_dir / 'static' / 'js' / 'preop-patient.js'),
             str(root_dir / 'static' / 'js' / 'preop-nurse.js')],
            capture_output=True, text=True, check=True, timeout=15,
        )
        out = json.loads(res.stdout)
        for key in ('patient_js', 'nurse_js'):
            self.assertFalse(out[key]['deferred'], key)
            self.assertFalse(out[key]['cancelled'], key)
            self.assertTrue(out[key]['scheduled'], key)


_NODE_DEFERRED_TOGGLE_SCRIPT = r"""
const fs = require('fs'), vm = require('vm');
function checkJs(jsPath) {
    const els = {};
    function mk(id) {
        const el = {
            id, value: 'scheduled', hidden: true, dataset: {}, _ev: {},
            classList: { contains() { return false; }, add() {}, remove() {} },
            addEventListener(t, fn) { (el._ev[t] = el._ev[t] || []).push(fn); },
            dispatchEvent(e) { (el._ev[e.type] || []).forEach(fn => fn(e)); },
            closest() { return null; }, querySelector() { return null; }, querySelectorAll() { return []; },
        };
        els[id] = el;
        return el;
    }
    const root = mk('nw-patient-root');
    root.dataset.patientPk = '1';
    const sel = mk('sel-case-outcome'), wrap = mk('wrap-cancel-reasons');
    const ctx = {
        window: { location: { origin: 'http://test', hash: '' }, addEventListener() {}, setTimeout() {} },
        document: {
            getElementById(id) { return els[id] || null; },
            querySelector() { return null; }, querySelectorAll() { return []; },
            createElement() { return mk('_tmp'); }, addEventListener() {},
        },
        Event: function(t) { this.type = t; },
        navigator: {},
        localStorage: { getItem() { return null; }, setItem() {}, removeItem() {} },
        sessionStorage: { getItem() { return null; }, setItem() {}, removeItem() {} },
        setTimeout() {}, clearTimeout() {}, console,
    };
    vm.runInNewContext(fs.readFileSync(jsPath, 'utf8'), ctx);
    function probe(v) { sel.value = v; sel.dispatchEvent({ type: 'change' }); return wrap.hidden; }
    return { deferred: probe('deferred'), cancelled: probe('cancelled_medical'), scheduled: probe('scheduled') };
}
console.log(JSON.stringify({ patient_js: checkJs(process.argv[1]), nurse_js: checkJs(process.argv[2]) }));
"""
