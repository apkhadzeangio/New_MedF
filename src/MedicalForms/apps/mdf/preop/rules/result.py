"""Canonical evaluate() result dictionary assembler."""
from ..constants import CXR_LABELS, ECG_LABELS, PRIORITY_LABELS, STATUS_LABELS
from ..registry import rule_value
from ..utils import _safe_display_str


def _effective_operation_label(ctx):
    eff_op_lbl = ctx.patient.planned_operation_label
    if ctx.pathway == 'cea':
        if ctx.is_symp_cea and 'ასიმპტომური' in eff_op_lbl:
            return eff_op_lbl.replace('ასიმპტომური', 'სიმპტომური')
        if not ctx.is_symp_cea and 'სიმპტომური' in eff_op_lbl and 'ასიმპტომური' not in eff_op_lbl:
            return eff_op_lbl.replace('სიმპტომური', 'ასიმპტომური (>180 დღე)')
    elif ctx.pathway in ('abf', 'pad_bypass', 'pad_pta') and ctx.is_clti_any:
        return eff_op_lbl.replace('ლერიშის სინდრომი', 'კრიტიკული იშემია (CLTI)').replace('კოჭლობა', 'CLTI')
    return eff_op_lbl


def _result_anesth_dos_part(ctx):
    return {
        'auto_asa': ctx.auto_asa,
        'asa_roman': ctx.asa_roman,
        'effective_asa': ctx.effective_asa,
        'mallampati_label': ctx.mallampati_label,
        'difficult_airway_alert': ctx.difficult_airway_alert,
        'stop_bang_auto': ctx.stop_bang_auto,
        'effective_stop_bang': ctx.effective_stop_bang,
        'stop_bang_label': ctx.stop_bang_label,
        'effective_anesth_label': ctx.effective_anesth_label,
        'anesth_lines_txt': ctx.anesth_lines_txt,
        'cied_plan_label': ctx.cied_plan_label,
        'needs_icu_bed': ctx.needs_icu_bed,
        'is_self_filled': getattr(ctx, 'is_self_filled', False),
        'has_glp1_any': getattr(ctx, 'has_glp1_any', False),
        'fasting_instruction': ctx.fasting_instruction,
        'surveillance_plan': ctx.surveillance_plan,
        'dos_checks': ctx.dos_checks,
        'dos_warnings': ctx.dos_warnings,
        'dos_hold_danger': ctx.dos_hold_danger,
        'dos_done_count': ctx.dos_done_count,
        'dos_total_count': ctx.dos_total_count,
        'dos_ready': ctx.dos_ready,
        'dos_gl_mmol': ctx.dos_gl,
    }


def _result_clinical_part(ctx):
    res = {
        'pathway': ctx.pathway,
        'pathway_short': ctx.pw_short,
        'effective_operation_label': _effective_operation_label(ctx),
        'side_badge': ctx.side_badge,
        'rcri': ctx.rcri,
        'vsg_cri': ctx.vsg_cri,
        'ariscat': ctx.ariscat,
        'frailty_notes': ctx.frailty_notes,
        's1_required_fields': ctx.s1_required_fields,
        'missing_items': ctx.missing_items,
        'next_step_hint': ctx.next_step_hint,
        'self_fill_highlights': ctx.self_fill_highlights,
        'clearance_seal': ctx.clearance_seal,
        'unknown_state_flags': ctx.unknown_state_flags,
        'has_blocking_unknown_state': ctx.has_blocking_unknown_state,
        'score_completeness': ctx.score_completeness,
        'surgical_brief': ctx.surgical_brief,
        'anesthetic_brief': ctx.anesthetic_brief,
        'passport_cautions': ctx.passport_cautions,
        'neuraxial_hard_blocked': ctx.neuraxial_hard_blocked,
        'solo_neuraxial_anatomic_mismatch': ctx.solo_neuraxial_anatomic_mismatch,
        'has_explicit_nx_blockers': bool(getattr(ctx, 'explicit_nx_blockers', None)),
        'eff_pregnant': getattr(ctx, 'eff_pregnant', False),
        'is_clti_any': ctx.is_clti_any,
        'is_evla_warfarin_only': getattr(ctx, 'is_evla_warfarin_only', False),
        'hb_unit_ambiguous': getattr(ctx, 'hb_unit_ambiguous', False),
        'eff_allergy_latex': ('latex_chlorhex' in ctx.allergy_chips),
        'eff_allergy_iodine': ctx.eff_allergy_iodine,
        'has_vancomycin_allergy': getattr(ctx, 'has_vancomycin_allergy', False),
        'active_thrombolysis_window': getattr(ctx, 'active_thrombolysis_window', False),
        'has_mech_valve': ctx.has_mech_valve,
        'has_known_dm': getattr(ctx, 'has_known_dm', False),
        'uses_contrast': getattr(ctx, 'uses_contrast', False),
        'creat_display_raw': (
            ctx.s.get('creat')
            if (ctx.s.get('creat') is not None and not (isinstance(ctx.s.get('creat'), (int, float)) and abs(ctx.s.get('creat')) > 1e6))
            else _safe_display_str(getattr(ctx, 'raw_creat', None), max_len=24, default=None)
        ),
        'na_val_sat': getattr(ctx, 'na_val_sat', None),
        'is_acute_emergency': ctx.is_acute_emergency,
        'show_pregnancy_check': ctx.show_pregnancy_check,
        'shock_index': ctx.shock_index,
        'fib_gl': ctx.fib,
        'is_venous': ctx.is_venous,
        'is_phleb': ctx.is_phleb,
        'is_pta': ctx.is_pta,
        'is_pad_bypass': ctx.is_pad_bypass,
        'is_on_hold': ctx.is_on_hold,
        'is_urgent_case': ctx.is_urgent_case,
        'is_symp_cea': ctx.is_symp_cea,
        'open_aortic': ctx.open_aortic,
        'has_synthetic_implant': ctx.has_synthetic_implant,
        'has_full_oac': ctx.has_full_oac,
        'has_ft_full_oac': getattr(ctx, 'has_ft_full_oac', False),
        'has_contra_oac': getattr(ctx, 'has_contra_oac', False),
        'has_contra_full_oac': getattr(ctx, 'has_contra_full_oac', False),
        'active_gi_bleed': bool(getattr(ctx, 'active_gi_bleed', False)),
        'has_elapsed_hold_window': getattr(ctx, 'has_elapsed_hold_window', False),
        'has_neuraxial_plan': ctx.has_neuraxial_plan,
        'has_hard_contraindication': ctx.has_hard_contraindication,
        'cea_high_surg_risk': ctx.cea_high_surg_risk,
        'post_antithrombotic_label': ctx.post_antithrombotic_label,
        'lidocaine_max_mg': ctx.lidocaine_max_mg,
        'max_contrast_ml': ctx.max_contrast_ml,
        'co2_angio_advised': ctx.co2_angio_advised,
        'pbm_iv_iron_indicated': ctx.pbm_iv_iron_indicated,
        'pbm_iron_required': ctx.pbm_iron_required,
        'chf_hyd_flag': ctx.chf_hyd_flag,
        'is_rupture_suspect': ctx.is_rupture_suspect,
        'nt_probnp': ctx.nt_probnp,
        'hs_ctn': ctx.hs_ctn,
        'tsat': ctx.tsat_val,
        'ldl': ctx.ldl_val,
        'person_id': ctx.person_id,
        'birth_date_ka': ctx.birth_date_ka,
        'labs_date_ka': ctx.labs_date_ka,
        'planned_surgery_ka': ctx.planned_surgery_ka,
        'planned_surgery_short': ctx.planned_surgery_short,
        'operated_date_ka': ctx.operated_date_ka,
        'allergy_summary': ctx.allergy_summary,
        'age': ctx.age,
        'stale_labs_days': rule_value('INF_STALE_LABS_DAYS'),
    }
    res.update(_result_anesth_dos_part(ctx))
    return res


def _result_flags_and_needs_part(ctx):
    hr_hi = rule_value('VIT_HR_SPO2_METS')['hr_hi']
    hr_eff = getattr(ctx, 'hr_sat', ctx.hr)
    ecg_display_label = (
        f'მოციმციმე არითმია — ტაქისისტოლია (HR > {hr_hi}!)'
        if (ctx.ecg == 'afib_controlled' and hr_eff is not None and hr_eff > hr_hi)
        else (ECG_LABELS.get(ctx.ecg) or _safe_display_str(ctx.ecg, max_len=60))
    )
    has_bad_med = any(r.get('contraindicated') or r.get('unstructured') for r in (ctx.med_schedule or ()))
    return {
        'status': ctx.status,
        'status_label': STATUS_LABELS[ctx.status],
        'all_tasks_done': ctx.all_tasks_done,
        'has_bad_med': has_bad_med,
        'workup_ready': bool(
            ctx.is_active_case
            and not ctx.patient.archived
            and ctx.q_filled
            and ctx.all_tasks_done
            and not ctx.has_hard_contraindication
            and not ctx.dos_hold_danger
            and not ctx.neuraxial_hard_blocked
            and not ctx.solo_neuraxial_anatomic_mismatch
            and not ctx.has_unacked_red
            and not ctx.has_blocking_unknown_state
            and not getattr(ctx, 'has_elapsed_hold_window', False)
            and not has_bad_med
            and not (ctx.needs_stale_labs and not ctx.s.get('stale_labs_refreshed'))
        ),
        'priority_tier': ctx.priority_tier,
        'priority_label': PRIORITY_LABELS[ctx.priority_tier],
        'days_elapsed': ctx.days,
        'target_days': ctx.t_days,
        'over_target_days': ctx.over_target,
        'over_7_days': ctx.over_target,
        'key_badge': ctx.key_badge,
        'key_badge_urgent': ctx.key_badge_urgent,
        'red_flags': ctx.red_flags,
        'amber_flags': ctx.amber_flags,
        'reflex_orders': ctx.reflex_orders,
        'all_reflex_done': ctx.all_reflex_done,
        'med_schedule': ctx.med_schedule,
        'has_any_date_plan': bool(
            ctx.planned_surgery_ka
            or (ctx.clearance_seal and ctx.clearance_seal.get('code') == 'stop')
            or any(r.get('date_plan') for r in (ctx.med_schedule or ()))
        ),
        'abx_prophylaxis_note': ctx.abx_prophylaxis_note,
        'caprini_score': ctx.caprini_score,
        'caprini_tier': ctx.caprini_tier,
        'caprini_advice': ctx.caprini_advice,
        'bp_arm_diff': ctx.bp_arm_diff,
        'labs_age_days': ctx.labs_age_days,
        'alerts_sig': ctx.current_sig,
        'alerts_acked': ctx.alerts_acked,
        'has_unacked_red': ctx.has_unacked_red,
        'egfr': ctx.egfr,
        'crcl': ctx.crcl,
        'eff_renal': ctx.eff_renal,
        'eff_renal_sat': getattr(ctx, 'eff_renal_sat', None),
        'eff_allergy_chips': sorted(ctx.allergy_chips),
        'compression_contra': ctx.compression_contra,
        'tbi_reduced_comp': ctx.tbi_reduced_comp,
        'creat_unit_ambiguous': ctx.creat_unit_ambiguous,
        'creat_unit_mismatch': getattr(ctx, 'creat_unit_mismatch', False),
        'bmi': ctx.bmi,
        'hb_gl': ctx.hb_gl,
        'wbc': ctx.wbc,
        'plt': ctx.plt,
        'gl_mmol': ctx.gl_mmol,
        'alb_gl': ctx.alb_gl,
        'aaa_mm': ctx.aaa_mm,
        'cta_months': ctx.cta_months,
        'days_since_ev': ctx.days_since_ev,
        'ipsi_car': ctx.ipsi_car,
        'contra_car': ctx.contra_car,
        'min_abi': ctx.min_abi,
        'max_abi': ctx.max_abi,
        'min_tp': ctx.min_tp,
        'min_tbi': ctx.min_tbi,
        'abi_non_compressible': ctx.abi_non_compressible,
        'tbi_measured': ctx.tbi_measured,
        'tbi_comp_safe': ctx.tbi_comp_safe,
        'pad_gsv_mm': ctx.pad_gsv_mm,
        'guideline_justifies_extra_cardio': ctx.guideline_justifies_extra_cardio,
        'cardio_non_compliant': ctx.cardio_non_compliant,
        's1_core_done': ctx.s1_core_done,
        'tasks': ctx.tasks,
        'done_count': ctx.done_count,
        'total_count': len(ctx.tasks),
        'ecg_label': ecg_display_label,
        'cxr_label': CXR_LABELS.get(ctx.cxr) or _safe_display_str(ctx.cxr, max_len=60),
        'q': ctx.q,
        's': ctx.s,
    }


def _result_needs_part(ctx):
    ft_keys = ctx.ft_meds.get('matched_keys') or ()
    return {
        'archived': bool(ctx.patient.archived),
        'case_outcome': ctx.case_outcome,
        'case_outcome_label': ctx.case_outcome_label,
        'vqi_bundle_applicable': ctx.vqi_bundle_applicable,
        'vqi_bundle_done': ctx.vqi_bundle_done,
        'vqi_30d_status': ctx.vqi_30d_status,
        'vqi_30d_label': ctx.vqi_30d_label,
        'implausible_fields': ctx.implausible_fields,
        'severe_htn': ctx.severe_htn,
        'ft_meds_keys': sorted(ft_keys),
        'cyp2c19_ppi_sensitive': bool(
            ctx.q.get('med_clopidogrel')
            or 'cilostazol' in ctx.med_chips
            or 'clopidogrel' in ft_keys
            or 'cilostazol' in ft_keys
        ),
        'needs_anemia_panel': ctx.needs_anemia_panel,
        'needs_hba1c_reflex': ctx.needs_hba1c_reflex,
        'needs_endo_reflex': ctx.needs_endo_reflex,
        'needs_nephro_panel': ctx.needs_nephro_panel,
        'needs_hydration': ctx.needs_hydration,
        'needs_tsh': ctx.needs_tsh,
        'needs_dental': ctx.needs_dental,
        'needs_contrast_premed': ctx.needs_contrast_premed,
        'needs_cied': ctx.needs_cied,
        'needs_tbi_check': ctx.needs_tbi_check,
        'needs_cea_antiplatelet': ctx.needs_cea_antiplatelet,
        'needs_alt_vein': ctx.needs_alt_vein,
        'needs_stale_labs': ctx.needs_stale_labs,
        'needs_spirometry': ctx.needs_spirometry,
        'needs_gastro': ctx.needs_gastro,
        'needs_ppi': ctx.needs_ppi,
        'needs_carotid_duplex': ctx.needs_carotid_duplex,
        'needs_cxr': ctx.needs_cxr,
        'needs_echo_or_bnp': ctx.needs_echo_or_bnp,
        'uti_suspected': ctx.uti_suspected,
        'needs_med_hold': ctx.needs_med_hold,
        'needs_brain_ct': ctx.needs_brain_ct,
        'needs_neuro': ctx.needs_neuro,
        'needs_laryngoscopy': ctx.needs_laryngoscopy,
        'needs_wound_care': ctx.needs_wound_care,
        'needs_abi_check': ctx.needs_abi_check,
        'needs_vte_prophylaxis': ctx.needs_vte_prophylaxis,
        'needs_ecg_venous': ctx.needs_ecg_venous,
        'needs_cardio': ctx.needs_cardio,
        'needs_anesth': ctx.needs_anesth,
        'needs_blood': ctx.open_major,
    }


def assemble_eval_result(ctx):
    """Combine clinical briefings/scores and flags/needs into the canonical evaluate() dict."""
    res = _result_clinical_part(ctx)
    res.update(_result_flags_and_needs_part(ctx))
    res.update(_result_needs_part(ctx))
    return res
