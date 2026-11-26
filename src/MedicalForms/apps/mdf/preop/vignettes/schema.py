"""Schema and deterministic evaluator for the 40 synthetic Track A4 clinical vignettes."""
import datetime
from dataclasses import dataclass

from ...models import PreopPatient
from ..engine import evaluate

VIGNETTE_TODAY = datetime.date(2026, 11, 25)
VIGNETTE_OP_DATE = '2026-12-10'

CATEGORY_LABELS = {
    'aaa': 'აორტა (AAA / EVAR)',
    'cea': 'საძილე არტერიები (CEA)',
    'pad': 'ABF / პერიფერიული შუნტირება / PTA',
    'venous': 'ვენური ქირურგია (EVLA / ფლებექტომია)',
    'pharm': 'ფარმაკოლოგია, ანესთეზია და პოლიმორბიდობა',
}


@dataclass(frozen=True)
class ClinicalVignette:
    """One synthetic, physician-reviewable clinical scenario with explicit expected assertions."""

    id: str
    category: str
    title_ka: str
    summary_ka: str
    expected_summary_ka: str
    source_ref: str
    op_subtype: str
    age: int
    sex: str
    side: str = ''
    q: tuple = ()
    s: tuple = ()
    expected_seal: str = ''
    expect_hard_stop: object = None
    must_red: tuple = ()
    must_not_red: tuple = ()
    must_amber: tuple = ()
    must_reflex: tuple = ()
    must_dos_keys: tuple = ()
    must_not_dos_keys: tuple = ()
    must_med_terms: tuple = ()
    expect_needs_spirometry: object = None
    expect_needs_tsh: object = None
    expect_asa: str = ''
    min_rcri: object = None
    expect_gl_mmol: object = None


def build_vignette_patient(v, today=VIGNETTE_TODAY):
    """Instantiate an unsaved synthetic PreopPatient from a ClinicalVignette definition."""
    pw = PreopPatient.SUBTYPE_PATHWAY[v.op_subtype]
    return PreopPatient(
        patient_name=f'ვინიეტა {v.id}',
        age=v.age,
        sex=v.sex,
        side=v.side,
        pathway=pw,
        op_subtype=v.op_subtype,
        visit_date=today,
        started_on=today,
        questionnaire=dict(v.q),
        studies=dict(v.s),
    )


def _check_vignette_expectations(v, ev):
    checks = []
    red_blob = ' | '.join(ev['red_flags'])
    amber_blob = ' | '.join(ev['amber_flags'])
    reflex_keys = {r['key'] for r in ev['reflex_orders']}
    dos_keys = {c['key'] for c in ev['dos_checks']}
    med_blob = ' | '.join(f"{r['drug']} {r['date_plan']} {r['rule']}" for r in ev['med_schedule'])

    def rec(label, ok, actual=''):
        checks.append({'label': label, 'ok': bool(ok), 'actual': str(actual)})

    if v.expected_seal:
        rec(f'დაშვების ბეჭედი = {v.expected_seal}', ev['clearance_seal']['code'] == v.expected_seal, ev['clearance_seal']['code'])
    if v.expect_hard_stop is not None:
        rec(f'უპირობო STOP = {v.expect_hard_stop}', ev['has_hard_contraindication'] is v.expect_hard_stop, ev['has_hard_contraindication'])
    for needle in v.must_red:
        rec(f'წითელი გაფრთხილება შეიცავს: „{needle}“', needle in red_blob, 'ნაპოვნია' if needle in red_blob else red_blob[:120])
    for needle in v.must_not_red:
        rec(f'წითელი გაფრთხილება არ შეიცავს: „{needle}“', needle not in red_blob, 'სუფთაა' if needle not in red_blob else 'ნაპოვნია!')
    for needle in v.must_amber:
        rec(f'ყვითელი სიგნალი შეიცავს: „{needle}“', needle in amber_blob, 'ნაპოვნია' if needle in amber_blob else amber_blob[:120])
    for rk in v.must_reflex:
        rec(f'რეფლექს-ორდერი აქტიურია: {rk}', rk in reflex_keys, sorted(reflex_keys))
    for dk in v.must_dos_keys:
        rec(f'WHO Sign-In პუნქტი ჩართულია: {dk}', dk in dos_keys, sorted(dos_keys))
    for dk in v.must_not_dos_keys:
        rec(f'WHO Sign-In პუნქტი გამორთულია: {dk}', dk not in dos_keys, sorted(dos_keys))
    for needle in v.must_med_terms:
        rec(f'მედიკამენტების კალენდარი შეიცავს: „{needle}“', needle in med_blob, 'ნაპოვნია' if needle in med_blob else med_blob[:120])
    if v.expect_needs_spirometry is not None:
        rec(f'სპირომეტრია საჭიროა = {v.expect_needs_spirometry}', ev['needs_spirometry'] is v.expect_needs_spirometry, ev['needs_spirometry'])
    if v.expect_needs_tsh is not None:
        rec(f'TSH საჭიროა = {v.expect_needs_tsh}', ev['needs_tsh'] is v.expect_needs_tsh, ev['needs_tsh'])
    if v.expect_asa:
        rec(f'ASA = {v.expect_asa}', ev['auto_asa'] == v.expect_asa, ev['auto_asa'])
    if v.min_rcri is not None:
        rec(f'RCRI >= {v.min_rcri}', ev['rcri']['score'] >= v.min_rcri, ev['rcri']['score'])
    if v.expect_gl_mmol is not None:
        rec(f'გლუკოზა = {v.expect_gl_mmol} მმოლ/ლ', ev['gl_mmol'] == v.expect_gl_mmol, ev['gl_mmol'])
    return checks


def evaluate_vignette(v, today=VIGNETTE_TODAY):
    """Run evaluate() on one synthetic vignette and check every clinical expectation."""
    patient = build_vignette_patient(v, today=today)
    ev = evaluate(patient, today=today)
    checks = _check_vignette_expectations(v, ev)
    failures = [c for c in checks if not c['ok']]
    return {
        'vignette': v,
        'patient': patient,
        'ev': ev,
        'checks': checks,
        'failures': failures,
        'passed': not failures,
    }
