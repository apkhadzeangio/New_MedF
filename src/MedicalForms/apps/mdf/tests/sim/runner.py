"""Simulation runner orchestrating cohort evaluation, invariant sweeps, oracles, and CLI entry."""
import argparse
import datetime
import json
import time
from collections import Counter

from django.template.loader import render_to_string

from ...duty import MONTHS
from ...preop import _monday_of, _week_title, evaluate, generate_anamnesis
from .cohort import SIM_TODAY, generate_synthetic_cohort
from .invariants import check_clinical_invariants, run_metamorphic_monotonicity
from .oracle import _Confusion, _record_guideline_oracle


def _render_sim_passport(patient, ev, anam):
    return render_to_string('mdf/preop/passport.html', {
        'p': patient,
        'ev': ev,
        'q': ev['q'],
        's': ev['s'],
        'shown_anamnesis': anam,
        'surgeon_name': 'ნ. აფხაძე',
        'approved_by_name': 'ნ. აფხაძე' if patient.approved_at else '',
        'printed_on': '25.11.2026 12:00',
    })


def _check_calendar_headers(invariant_counts, exceptions):
    # Full-year calendar header sweep across leap year (2024) + standard year (2026) including all December weeks
    for yr in (2024, 2026):
        d_cur = datetime.date(yr, 1, 1)
        while d_cur.year == yr:
            mon = _monday_of(d_cur)
            sun = mon + datetime.timedelta(days=6)
            try:
                w_txt = _week_title(mon)
                if mon.month == sun.month and not w_txt.startswith(f'{MONTHS[mon.month - 1]} {mon.year}'):
                    invariant_counts['INV-26_CALENDAR_WEEK_TITLE_WRONG_MONTH'] += 1
            except Exception as exc:
                exceptions.append({'idx': -1, 'tier': 'calendar', 'op_subtype': str(mon), 'error': f'{type(exc).__name__}: {exc}'})
            d_cur += datetime.timedelta(days=1)


def _summarize_latencies(latencies_ms):
    lat_sorted = sorted(latencies_ms)
    n_lat = len(lat_sorted)
    return {
        'mean_ms': round(sum(lat_sorted) / n_lat, 4) if n_lat else 0.0,
        'p50_ms': round(lat_sorted[int(0.50 * (n_lat - 1))], 4) if n_lat else 0.0,
        'p95_ms': round(lat_sorted[int(0.95 * (n_lat - 1))], 4) if n_lat else 0.0,
        'p99_ms': round(lat_sorted[int(0.99 * (n_lat - 1))], 4) if n_lat else 0.0,
        'max_ms': round(lat_sorted[-1], 4) if n_lat else 0.0,
    }


def run_simulation(n=50000, n_mono=6000, passport_sample=2000, seed=2026):
    """Execute the full Pre-Pilot Synthetic Simulation & Clinical Objectivity Suite."""
    t0 = time.perf_counter()
    cohort = generate_synthetic_cohort(n=n, seed=seed)
    gen_sec = time.perf_counter() - t0

    latencies_ms, exceptions = [], []
    invariant_counts, pathway_counts, tier_counts = Counter(), Counter(), Counter()
    status_counts, seal_counts = Counter(), Counter()
    invariant_examples = {}
    passport_rendered_count = 0

    oracles = {
        k: _Confusion() for k in (
            'hard_contraindication', 'critical_instability_recall', 'esc2022_cardiology_gate',
            'nice_ng45_cxr_gate', 'idsa2019_uti_gate', 'esc2022_gastro_egd_gate',
            'esaic_pbm_anemia_gate', 'esc2022_ppi_gate',
        )
    }
    passport_stride = max(1, n // passport_sample) if passport_sample > 0 else (n + 1)

    t_eval_start = time.perf_counter()
    for idx, (cohort_tier, patient) in enumerate(cohort):
        tier_counts[cohort_tier] += 1
        t1 = time.perf_counter()
        try:
            ev = evaluate(patient, today=SIM_TODAY)
            anam = generate_anamnesis(patient, ev)
            rendered_html = None
            if passport_sample > 0 and (idx % passport_stride == 0 or cohort_tier == 'fuzz' and idx % 8 == 0):
                rendered_html = _render_sim_passport(patient, ev, anam)
                passport_rendered_count += 1
        except Exception as exc:  # pragma: no cover - surfaces bugs during simulation
            exceptions.append({
                'idx': idx, 'tier': cohort_tier,
                'op_subtype': patient.op_subtype, 'error': f'{type(exc).__name__}: {exc}',
            })
            continue
        latencies_ms.append((time.perf_counter() - t1) * 1000.0)
        pathway_counts[ev['pathway']] += 1
        status_counts[ev['status']] += 1
        seal_counts[ev['clearance_seal']['code']] += 1

        for v in check_clinical_invariants(patient, ev, anam, rendered_passport=rendered_html):
            invariant_counts[v] += 1
            if v not in invariant_examples:
                invariant_examples[v] = {
                    'idx': idx, 'tier': cohort_tier,
                    'op_subtype': patient.op_subtype, 'side': patient.side, 'status': ev['status'],
                }
        _record_guideline_oracle(oracles, patient, ev)

    eval_sec = time.perf_counter() - t_eval_start
    mono_res = run_metamorphic_monotonicity(n_pairs=n_mono, seed=seed)
    _check_calendar_headers(invariant_counts, exceptions)

    return {
        'total_scenarios': n,
        'metamorphic_pairs': mono_res['tested_pairs'],
        'total_evaluations': n + mono_res['tested_pairs'] * 2,
        'passports_rendered': passport_rendered_count,
        'generation_sec': round(gen_sec, 2),
        'evaluation_sec': round(eval_sec, 2),
        'cohort_tiers': dict(tier_counts),
        'pathway_distribution': dict(pathway_counts),
        'status_distribution': dict(status_counts),
        'seal_distribution': dict(seal_counts),
        'exceptions_count': len(exceptions),
        'exceptions_sample': exceptions[:10],
        'invariant_violations_total': sum(invariant_counts.values()),
        'invariant_violations': dict(invariant_counts),
        'invariant_examples': invariant_examples,
        'metamorphic_violations': mono_res['violations'],
        'guideline_oracles': {k: v.as_dict() for k, v in oracles.items()},
        'latency_ms': _summarize_latencies(latencies_ms),
    }


def main():
    parser = argparse.ArgumentParser(description='Run Pre-Pilot 50,000+ Synthetic Clinical Safety Simulation.')
    parser.add_argument('--n', type=int, default=50000)
    parser.add_argument('--mono', type=int, default=6000)
    parser.add_argument('--passports', type=int, default=2000)
    parser.add_argument('--seed', type=int, default=2026)
    args = parser.parse_args()

    import django
    django.setup()
    report = run_simulation(n=args.n, n_mono=args.mono, passport_sample=args.passports, seed=args.seed)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
