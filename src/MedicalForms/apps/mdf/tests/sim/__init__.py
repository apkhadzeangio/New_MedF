"""Pre-Pilot Large-Scale Synthetic Simulation & Clinical Objectivity Engine package.

Runs 100% in-memory against unsaved ``PreopPatient`` instances — ZERO writes to ``db.sqlite3``.
"""
from .archetypes import _archetype_patient
from .cohort import (
    ALL_SUBTYPES,
    ASA_ORDER,
    SIM_TODAY,
    STATUS_SEVERITY_ORDER,
    _boundary_patient,
    _fuzz_patient,
    _make_unsaved_patient,
    _random_stratified_patient,
    generate_synthetic_cohort,
)
from .invariants import (
    _safe_list,
    check_clinical_invariants,
    run_metamorphic_monotonicity,
)
from .oracle import _Confusion, _record_guideline_oracle
from .runner import main, run_simulation

__all__ = [
    'ALL_SUBTYPES',
    'ASA_ORDER',
    'SIM_TODAY',
    'STATUS_SEVERITY_ORDER',
    '_Confusion',
    '_archetype_patient',
    '_boundary_patient',
    '_fuzz_patient',
    '_make_unsaved_patient',
    '_random_stratified_patient',
    '_record_guideline_oracle',
    '_safe_list',
    'check_clinical_invariants',
    'generate_synthetic_cohort',
    'main',
    'run_metamorphic_monotonicity',
    'run_simulation',
]
