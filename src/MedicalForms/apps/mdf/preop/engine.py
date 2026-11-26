"""Short ordered orchestrator for the deterministic preoperative assessment engine."""
from ..models import PreopPatient
from .rules import (
    assemble_eval_result,
    eval_aaa,
    eval_abf,
    eval_anaesthesia_and_triage,
    eval_briefings,
    eval_cardiac_and_tasks,
    eval_carotid_gastro,
    eval_cea,
    eval_cross_pathway_and_urgency,
    eval_glycemia_endocrine,
    eval_hematology_coag,
    eval_infection_stale_labs,
    eval_meds_rules,
    eval_pad_bypass,
    eval_pta,
    eval_pulmonary,
    eval_renal_electrolytes,
    eval_unknown_state_failsafe,
    eval_venous,
    eval_vitals_bp,
    init_eval_context,
)


def evaluate(patient, today=None):
    """Evaluate thresholds, Autonomous Nurse Reflex Orders, Stage 2 tasks, Cardiology Audit, and 2D Triage."""
    ctx = init_eval_context(patient, today=today)

    # 1. Pathway-specific vascular / venous rules & preparation clocks
    if ctx.pathway == PreopPatient.Pathway.AAA_INFRA:
        eval_aaa(ctx)
    elif ctx.pathway == PreopPatient.Pathway.CEA:
        eval_cea(ctx)
    elif ctx.pathway == PreopPatient.Pathway.ABF:
        eval_abf(ctx)
    elif ctx.is_pad_bypass:
        eval_pad_bypass(ctx)
    elif ctx.is_pta:
        eval_pta(ctx)
    elif ctx.is_venous:
        eval_venous(ctx)

    eval_cross_pathway_and_urgency(ctx)

    # 2-8. Vitals, hematology/ASRA, renal/contrast, glycemia/thyroid, infection, pulmonary & GI/carotid
    eval_vitals_bp(ctx)
    eval_hematology_coag(ctx)
    eval_renal_electrolytes(ctx)
    eval_glycemia_endocrine(ctx)
    eval_infection_stale_labs(ctx)
    eval_pulmonary(ctx)
    eval_carotid_gastro(ctx)

    # 9-13. Medications, dual-track cardiology/tasks, anesthesiology/DoS/2D triage, fail-safe unknown-state, briefings & result
    eval_meds_rules(ctx)
    eval_cardiac_and_tasks(ctx)
    eval_anaesthesia_and_triage(ctx)
    eval_unknown_state_failsafe(ctx)
    eval_briefings(ctx)
    return assemble_eval_result(ctx)
