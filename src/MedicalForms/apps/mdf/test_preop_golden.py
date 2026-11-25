"""Byte-identical Golden Snapshot Guard for Track C pure refactoring.

Covers:
- All 21 high-acuity archetypes, 26 boundary sweeps, and 26 stratified scenarios from
  `test_preop_sim.py`, plus 12 multi-system clinical fixtures (85 scenarios total).
- Canonical outputs of `evaluate()`, `generate_anamnesis()`, and `build_med_schedule()`.
- Rendered HTML of every patient tab (`q`, `s1`, `s2`, `doc`, `dos`), 2-page A4 passport,
  and self-fill page across 46 full-render scenarios (322 rendered pages), with CSRF tokens
  and ephemeral DB PKs/tokens stripped and the patient page JS payload compared byte-for-byte
  whether inline or extracted to `static/js/preop-patient.js`.

NEVER regenerate `fixtures/preop_golden_snapshot.json.xz` to make a failing test pass!
"""
import datetime
import difflib
import json
import lzma
import pathlib
import random
import re
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import PreopPatient
from .preop import build_med_schedule, evaluate, generate_anamnesis, make_self_fill_token
from .test_preop_sim import (
    SIM_TODAY,
    _archetype_patient,
    _boundary_patient,
    _make_unsaved_patient,
    _random_stratified_patient,
)


APP_ROOT = pathlib.Path(__file__).resolve().parent
SNAPSHOT_PATH = APP_ROOT / 'fixtures' / 'preop_golden_snapshot.json.xz'
STATIC_PATIENT_JS = APP_ROOT.parent.parent / 'static' / 'js' / 'preop-patient.js'
SIM_NOW = datetime.datetime(2026, 11, 25, 10, 0, 0, tzinfo=datetime.timezone.utc)
PATIENT_TABS = ('q', 's1', 's2', 'doc', 'dos')

_CSRF_RE = re.compile(r'<input type="hidden" name="csrfmiddlewaretoken" value="[^"]+">')
_PREOP_PK_URL_RE = re.compile(r'/preop/\d+/')
_PREOP_QUERY_PK_RE = re.compile(r'([?&])preop=\d+')
_PREOP_FILL_URL_RE = re.compile(r'/preop/fill/[^/"\'\s<>]+/')
_SURGEON_OPT_RE = re.compile(r'(<option value=")\d+("[^>]*>გიორგი ექიმი</option>)')
_INLINE_PATIENT_SCRIPT_RE = re.compile(
    r'<script>\s*\(function\s*\(\)\s*\{\s*var\s+dirtyPanes\s*=\s*\{\};.*?\}\)\(\);\s*</script>',
    re.DOTALL,
)
_EXTERNAL_PATIENT_SCRIPT_RE = re.compile(
    r'<script\s+src="[^"]*js/preop-patient\.js[^"]*"[^>]*>\s*</script>',
    re.DOTALL,
)


def _json_safe(obj):
    if isinstance(obj, (datetime.date, datetime.datetime)):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in sorted(obj.items(), key=lambda kv: str(kv[0]))}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(x) for x in obj]
    return obj


def _clinical_fixtures():
    """12 synthetic corner-case patients exercising Sprint 1-6 safety rules (no real data)."""
    op_dt = (SIM_TODAY + datetime.timedelta(days=15)).isoformat()
    specs = [
        ('fix_01_mech_valve_doac', PreopPatient.OpSubtype.BYPASS_CLTI, 'R', 'M', 68,
         {'filled': True, 'cad': True, 'cad_chips': ['mech_valve'], 'med_doac': True, 'mets_ok': True},
         {'creat': 90, 'weight_kg': 75, 'inr': 1.02, 'abi_r': 0.42, 'planned_surgery_date': op_dt}),
        ('fix_02_hit_dialysis_beta_lactam', PreopPatient.OpSubtype.ABF_CLTI, 'B', 'F', 84,
         {'filled': True, 'on_dialysis': True, 'allergy_chips': ['hit_heparin', 'beta_lactam'], 'cfs_level': '5_plus', 'mets_ok': False},
         {'creat': 108, 'weight_kg': 82, 'k': 5.6, 'abi_r': 0.38, 'planned_surgery_date': op_dt}),
        ('fix_03_bilat_cea_stop', PreopPatient.OpSubtype.CEA_ASYMP, 'B', 'M', 72,
         {'filled': True, 'med_aspirin': True, 'med_chips': ['statin'], 'mets_ok': True},
         {'carotid_r': 82, 'carotid_l': 78, 'cea_cta_status': 'concordant', 'planned_surgery_date': op_dt}),
        ('fix_04_freetext_allergy_and_meds', PreopPatient.OpSubtype.OPEN, '', 'M', 70,
         {'filled': True, 'allergy_text': 'პენიცილინზე ანაფილაქსია', 'other_meds': 'ქსარელტო 20 მგ', 'mets_ok': True},
         {'aaa_mm': 60, 'anesthesia_plan': 'neuraxial', 'plt': 200, 'inr': 1.0, 'planned_surgery_date': op_dt}),
        ('fix_05_inverted_bp_and_shock', PreopPatient.OpSubtype.EVAR, '', 'M', 71,
         {'filled': True, 'med_chips': ['acei_arb', 'beta_blocker'], 'mets_ok': True},
         {'aaa_mm': 58, 'bp_sys': 94, 'bp_dia': 102, 'hr': 114, 'na': 153, 'planned_surgery_date': op_dt}),
        ('fix_06_symp_aaa_fast_track', PreopPatient.OpSubtype.OPEN, '', 'M', 73,
         {'filled': True, 'aaa_pain': True, 'med_doac': True, 'dental_infection': True, 'pulm': True, 'mets_ok': True},
         {'aaa_mm': 66, 'hba1c': 9.4, 'glucose': 6.0, 'creat': 88, 'weight_kg': 78, 'planned_surgery_date': op_dt}),
        ('fix_07_sglt2_glp1_steroid_dm', PreopPatient.OpSubtype.OPEN, '', 'M', 67,
         {'filled': True, 'dm': True, 'sglt2': True, 'glp1': True, 'med_chips': ['steroid', 'statin'], 'mets_ok': True},
         {'aaa_mm': 61, 'hba1c': 8.9, 'glucose': 12.6, 'creat': 84, 'weight_kg': 90, 'planned_surgery_date': op_dt}),
        ('fix_08_unverified_self_fill', PreopPatient.OpSubtype.EVLA, 'R', 'F', 42,
         {'filled': True, 'filled_by': 'პაციენტი (ბმულით)', 'self_fill_submitted': True, 'nurse_verified': False, 'ven_ceap': 'C2', 'mets_ok': True},
         {'hb': 134, 'plt': 240, 'glucose': 5.1, 'ven_duplex_status': 'deep_patent_reflux', 'compression_ready': True}),
        ('fix_09_dm_clti_calcified_abi', PreopPatient.OpSubtype.BYPASS_CLTI, 'R', 'M', 69,
         {'filled': True, 'dm': True, 'abf_ulcer': True, 'med_aspirin': True, 'med_chips': ['statin'], 'mets_ok': True},
         {'abi_r': 1.05, 'pad_runoff_status': 'tibial_single', 'pad_gsv_status': 'adequate', 'hb': 126, 'plt': 210, 'inr': 1.05, 'creat': 92, 'ecg': 'normal'}),
        ('fix_10_reproductive_f_evar', PreopPatient.OpSubtype.EVAR, '', 'F', 36,
         {'filled': True, 'pregnant_possible': True, 'mets_ok': True},
         {'aaa_mm': 55, 'aaa_neck_status': 'friendly_ifu', 'hb': 125, 'plt': 230, 'inr': 1.0, 'creat': 68, 'ecg': 'normal'}),
        ('fix_11_healthy_asa1_evla', PreopPatient.OpSubtype.EVLA, 'L', 'F', 34,
         {'filled': True, 'ven_ceap': 'C2', 'mets_ok': True},
         {'hb': 136, 'plt': 245, 'bp_sys': 118, 'bp_dia': 74, 'hr': 72, 'spo2': 99, 'weight_kg': 64, 'height_cm': 168, 'ven_duplex_status': 'deep_patent_reflux', 'compression_ready': True}),
        ('fix_12_ready_go_evla', PreopPatient.OpSubtype.EVLA, 'R', 'M', 40,
         {'filled': True, 'nurse_verified': True, 'nurse_verified_by': 'ექთანი ტესტი', 'ven_ceap': 'C2', 'mets_ok': True},
         {'person_id': '01000000011', 'hb': 144, 'plt': 250, 'bp_sys': 122, 'bp_dia': 76, 'hr': 68, 'spo2': 99, 'weight_kg': 76, 'height_cm': 178, 'ven_duplex_status': 'deep_patent_reflux', 'compression_ready': True}),
    ]
    out = []
    for i, (sid, sub, side, sex, age, q, s) in enumerate(specs):
        p = _make_unsaved_patient(900 + i, sub, age=age, sex=sex, side=side, q=q, s=s)
        out.append((sid, p, True))
    return out


def build_golden_scenarios():
    """Return deterministic list of `(scenario_id, unsaved_PreopPatient, render_html_flag)`."""
    scenarios = []
    rng_arch = random.Random(2026)
    for idx in list(range(20)) + [32]:
        scenarios.append((f'arch_{idx:02d}', _archetype_patient(rng_arch, idx), True))

    rng_bnd = random.Random(2027)
    for idx in range(26):
        scenarios.append((f'bnd_{idx:02d}', _boundary_patient(rng_bnd, idx), idx < 13))

    rng_strat = random.Random(2026)
    for idx in range(26):
        scenarios.append((f'strat_{idx:02d}', _random_stratified_patient(rng_strat, idx), False))

    scenarios.extend(_clinical_fixtures())
    return scenarios


def _normalize_html(html, is_patient_page=False):
    text = _CSRF_RE.sub('<input type="hidden" name="csrfmiddlewaretoken" value="<CSRF>">', html)
    text = _PREOP_PK_URL_RE.sub('/preop/<PK>/', text)
    text = _PREOP_QUERY_PK_RE.sub(r'\1preop=<PK>', text)
    text = _PREOP_FILL_URL_RE.sub('/preop/fill/<TOKEN>/', text)
    text = _SURGEON_OPT_RE.sub(r'\1<UID>\2', text)
    js_body = None
    if is_patient_page:
        m_in = _INLINE_PATIENT_SCRIPT_RE.search(text)
        if m_in:
            raw_block = m_in.group(0)
            js_body = raw_block[len('<script>'):-len('</script>')].strip()
            text = _INLINE_PATIENT_SCRIPT_RE.sub('<!-- PREOP_PATIENT_JS -->', text, count=1)
        elif _EXTERNAL_PATIENT_SCRIPT_RE.search(text):
            js_body = STATIC_PATIENT_JS.read_text(encoding='utf-8').strip()
            text = _EXTERNAL_PATIENT_SCRIPT_RE.sub('<!-- PREOP_PATIENT_JS -->', text, count=1)
    return text, js_body


def _capture_patient_snapshot(client, sid, unsaved_p, render_html, surgeon):
    ev = evaluate(unsaved_p, today=SIM_TODAY)
    anam = generate_anamnesis(unsaved_p)
    meds = build_med_schedule(unsaved_p, ev['q'], ev['s'], ev['egfr'], crcl=ev['crcl'])
    entry = {
        'evaluate': _json_safe(ev),
        'generate_anamnesis': anam,
        'build_med_schedule': _json_safe(meds),
    }
    if not render_html:
        return entry

    saved = PreopPatient.objects.create(
        patient_name=unsaved_p.patient_name,
        age=unsaved_p.age,
        sex=unsaved_p.sex,
        phone=unsaved_p.phone,
        pathway=unsaved_p.pathway,
        op_subtype=unsaved_p.op_subtype,
        side=unsaved_p.side,
        visit_date=unsaved_p.visit_date,
        started_on=unsaved_p.started_on,
        someday_list=unsaved_p.someday_list,
        surgeon=surgeon,
        cardiologist=unsaved_p.cardiologist,
        cardio_decision=unsaved_p.cardio_decision,
        cardio_note=unsaved_p.cardio_note,
        questionnaire=unsaved_p.questionnaire,
        studies=unsaved_p.studies,
        approved_at=SIM_NOW if unsaved_p.approved_at else None,
        approved_by=surgeon if unsaved_p.approved_at else None,
    )
    pages = {}
    js_captured = None
    for tab in PATIENT_TABS:
        resp = client.get(reverse('preop-patient', args=[saved.pk]) + f'?tab={tab}')
        if resp.status_code != 200:
            raise AssertionError(f'{sid} tab={tab} returned HTTP {resp.status_code}')
        norm, js_b = _normalize_html(resp.content.decode('utf-8'), is_patient_page=True)
        pages[f'patient_{tab}'] = norm
        if js_captured is None:
            js_captured = js_b
    resp_pp = client.get(reverse('preop-passport', args=[saved.pk]))
    if resp_pp.status_code != 200:
        raise AssertionError(f'{sid} passport returned HTTP {resp_pp.status_code}')
    pages['passport'], _ = _normalize_html(resp_pp.content.decode('utf-8'))

    token = make_self_fill_token(saved)
    resp_sf = client.get(reverse('preop-self-fill', args=[token]))
    if resp_sf.status_code != 200:
        raise AssertionError(f'{sid} self_fill returned HTTP {resp_sf.status_code}')
    pages['self_fill'], _ = _normalize_html(resp_sf.content.decode('utf-8'))

    saved.delete()
    entry['pages'] = pages
    entry['patient_js'] = js_captured
    return entry


def capture_full_snapshot(client, surgeon):
    """Run all 85 scenarios with pinned clock and return canonical dict."""
    data = {}
    with mock.patch('django.utils.timezone.localdate', return_value=SIM_TODAY), \
            mock.patch('django.utils.timezone.now', return_value=SIM_NOW):
        for sid, unsaved_p, render_html in build_golden_scenarios():
            data[sid] = _capture_patient_snapshot(client, sid, unsaved_p, render_html, surgeon)
    return data


class PreopGoldenSnapshotTests(TestCase):
    """Byte-identical regression lock across Track C pure refactoring."""

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.surgeon = User.objects.create_user(
            username='golden_doc', password='GoldenTestPassword!1',
            first_name='გიორგი', last_name='ექიმი', is_staff=True,
        )

    def test_golden_snapshot_byte_identical(self):
        self.assertTrue(
            SNAPSHOT_PATH.exists(),
            f'Missing golden snapshot file: {SNAPSHOT_PATH}',
        )
        expected = json.loads(lzma.decompress(SNAPSHOT_PATH.read_bytes()).decode('utf-8'))
        self.client.force_login(self.surgeon)
        actual = capture_full_snapshot(self.client, self.surgeon)

        self.assertEqual(sorted(actual.keys()), sorted(expected.keys()))
        for sid in sorted(expected.keys()):
            exp_item = expected[sid]
            act_item = actual[sid]
            for field in ('evaluate', 'generate_anamnesis', 'build_med_schedule', 'patient_js'):
                if field in exp_item:
                    if field == 'patient_js':
                        self.assertTrue(exp_item[field], f'Expected non-empty patient_js in {sid}')
                    self.assertEqual(
                        act_item.get(field), exp_item[field],
                        f'Golden drift in {sid}.{field}',
                    )
            for page_key, exp_html in (exp_item.get('pages') or {}).items():
                act_html = (act_item.get('pages') or {}).get(page_key, '')
                if act_html != exp_html:
                    diff = ''.join(difflib.unified_diff(
                        exp_html.splitlines(keepends=True),
                        act_html.splitlines(keepends=True),
                        fromfile=f'golden/{sid}/{page_key}',
                        tofile=f'actual/{sid}/{page_key}',
                        n=2,
                    ))
                    self.fail(f'Golden HTML drift in {sid}/{page_key}:\n{diff[:3000]}')
