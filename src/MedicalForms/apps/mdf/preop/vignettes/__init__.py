"""40 synthetic clinical vignettes for preoperative reliability validation (Track A4)."""
from .cases_pharm import PHARM_VIGNETTES
from .cases_vascular import VASCULAR_VIGNETTES
from .schema import (
    CATEGORY_LABELS,
    VIGNETTE_OP_DATE,
    VIGNETTE_TODAY,
    ClinicalVignette,
    build_vignette_patient,
    evaluate_vignette,
)

VIGNETTES = VASCULAR_VIGNETTES + PHARM_VIGNETTES


def _build_index(items):
    idx = {}
    for v in items:
        if v.id in idx:
            raise ValueError(f'Duplicate clinical vignette id: {v.id}')
        idx[v.id] = v
    return idx


VIGNETTES_BY_ID = _build_index(VIGNETTES)


def evaluate_all_vignettes(today=VIGNETTE_TODAY):
    """Evaluate all 40 synthetic clinical vignettes and return results + summary."""
    results = [evaluate_vignette(v, today=today) for v in VIGNETTES]
    passed_count = sum(1 for r in results if r['passed'])
    return {
        'results': results,
        'total': len(results),
        'passed_count': passed_count,
        'failed_count': len(results) - passed_count,
    }


__all__ = [
    'CATEGORY_LABELS',
    'ClinicalVignette',
    'VIGNETTES',
    'VIGNETTES_BY_ID',
    'VIGNETTE_OP_DATE',
    'VIGNETTE_TODAY',
    'build_vignette_patient',
    'evaluate_all_vignettes',
    'evaluate_vignette',
]
