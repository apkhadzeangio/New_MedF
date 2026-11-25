"""AST-based size guard enforcing Track C modular boundaries.

Rules:
- Every new Python file under `apps/mdf/` (excluding `migrations/`) MUST be <= 800 lines.
- Every new function or method MUST be <= 100 lines.
- Pre-existing oversized files and functions are listed with their exact current sizes
  and may ONLY shrink — never grow.
"""
import ast
import pathlib
import unittest


APP_ROOT = pathlib.Path(__file__).resolve().parent
MAX_FILE_LINES = 800
MAX_FUNC_LINES = 100

# Pre-existing oversized files at the start of Track C (may ONLY shrink).
OVERSIZED_FILES_BASELINE = {
    'artery_map.py': 825,
    'cases.py': 811,
    'models.py': 990,
    'preop.py': 7991,
    'staging.py': 2031,
    'test_preop_sim.py': 1492,
    'tests.py': 7061,
}

# Pre-existing oversized functions/methods at the start of Track C (may ONLY shrink).
OVERSIZED_FUNCTIONS_BASELINE = {
    ('artery_map.py', '_build_interventions'): 180,
    ('artery_map.py', 'build_foot_context'): 122,
    ('artery_map.py', 'build_map_context'): 187,
    ('preop.py', 'PreopAuditView.get_context_data'): 160,
    ('preop.py', 'PreopPatientView._save_stage1'): 106,
    ('preop.py', 'PreopPatientView._save_stage2'): 163,
    ('preop.py', 'PreopWeekView.get_context_data'): 149,
    ('preop.py', '_admit_preop_to_board_locked'): 258,
    ('preop.py', 'apply_questionnaire_post'): 141,
    ('preop.py', 'apply_self_fill_post'): 127,
    ('preop.py', 'build_med_schedule'): 406,
    ('preop.py', 'calc_caprini'): 110,
    ('preop.py', 'evaluate'): 3259,
    ('preop.py', 'generate_anamnesis'): 386,
    ('preop.py', 'import_case_to_preop'): 101,
    ('staging.py', '_limb_body_lines_ka'): 114,
    ('staging.py', 'suggest_from_map'): 269,
    ('test_preop_sim.py', '_archetype_patient'): 143,
    ('test_preop_sim.py', '_random_stratified_patient'): 318,
    ('test_preop_sim.py', '_record_guideline_oracle'): 160,
    ('test_preop_sim.py', 'check_clinical_invariants'): 299,
    ('test_preop_sim.py', 'run_simulation'): 125,
    ('tests.py', 'PreopBoundaryMutationTests.test_sprint6_boundary_and_combinator_invariants'): 270,
    ('tests.py', 'PreopBoundaryMutationTests.test_sprint6_evaluate_pathway_and_threshold_matrix'): 340,
    ('tests.py', 'PreopCeaAndAbfTests.test_abf_esvs_2024_clti_wet_gangrene_groin_outflow_spirometry_and_pathway_filters'): 128,
    ('tests.py', 'PreopCeaAndAbfTests.test_cea_esvs_2023_symptomatic_flow_laryngoscopy_antiplatelet_and_14day_window'): 123,
    ('tests.py', 'PreopClaudePeerReviewFixesTests.test_sprint1_antithrombotic_stop_lock_mech_valve_freetext_and_renal_hepatic_pharmacology'): 127,
    ('tests.py', 'PreopClaudePeerReviewFixesTests.test_sprint2_asra_neuraxial_cied_vitals_lab_units_and_passport_visibility'): 103,
    ('tests.py', 'PreopClaudePeerReviewFixesTests.test_sprint3_esvs_never_events_fast_track_pause_and_metabolic'): 111,
    ('tests.py', 'PreopPrePilotRefinementAndSimulationTests.test_pre_pilot_2_academic_two_page_dossier_risk_indices_and_specialist_briefings'): 103,
    ('tests.py', 'PreopReflexZeroTextAndPeripheralTests.test_standing_reflex_orders_zero_text_chips_med_schedule_passport_and_consent'): 151,
    ('tests.py', 'PreopStageP0SafetyTests.test_p0_1_pharmacology_asra_neuraxial_crcl_cea_clopidogrel_glp1_hit_and_cied'): 137,
    ('tests.py', 'PreopStageP0SafetyTests.test_p0_4_unit_guards_live_dates_non_compressible_abi_tbi_and_case_auto_import'): 157,
    ('tests.py', 'PreopStageP1RationalAndBridgeTests.test_p1_1_rational_testing_fast_track_cea_bnp_ppi_asb_spiro_evla_and_claudication_mets_audit'): 166,
    ('tests.py', 'PreopStageP2AnesthDosAndVqiTests.test_p2_1_identifiers_dob_auto_age_ecosystem_prefills_and_exact_calendar_med_schedule'): 108,
    ('tests.py', 'PreopStageP3ReferenceHardeningTests.test_p3_3_continuity_case_plan_same_name_board_isolation_archive_vqi_hold_pause_and_sticky_chips'): 126,
    ('tests.py', 'PreopTests.test_aaa_thresholds_two_stage_flow_anamnesis_and_cardiology_audit'): 131,
}


def _iter_python_files():
    for fp in sorted(APP_ROOT.rglob('*.py')):
        if 'migrations' in fp.parts or '__pycache__' in fp.parts:
            continue
        yield fp, fp.relative_to(APP_ROOT).as_posix()


def _collect_functions(tree):
    found = []

    def _walk(node, prefix=''):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                _walk(child, f'{prefix}{child.name}.')
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qname = f'{prefix}{child.name}'
                found.append((qname, child.lineno, child.end_lineno - child.lineno + 1))
                _walk(child, f'{qname}.')

    _walk(tree)
    return found


class CodeSizeGuardTests(unittest.TestCase):
    """Fail if any new file exceeds 800 lines, any new function exceeds 100 lines, or any legacy item grows."""

    def test_file_line_limits(self):
        violations = []
        for fp, rel in _iter_python_files():
            line_count = len(fp.read_text(encoding='utf-8').splitlines())
            limit = OVERSIZED_FILES_BASELINE.get(rel, MAX_FILE_LINES)
            if line_count > limit:
                violations.append(f'{rel}: {line_count} lines (limit {limit})')
        self.assertEqual(violations, [], 'Oversized Python files detected:\n' + '\n'.join(violations))

    def test_function_line_limits(self):
        violations = []
        for fp, rel in _iter_python_files():
            tree = ast.parse(fp.read_text(encoding='utf-8'), filename=rel)
            for qname, lineno, length in _collect_functions(tree):
                limit = OVERSIZED_FUNCTIONS_BASELINE.get((rel, qname), MAX_FUNC_LINES)
                if length > limit:
                    violations.append(f'{rel}:{lineno} {qname} = {length} lines (limit {limit})')
        self.assertEqual(violations, [], 'Oversized functions/methods detected:\n' + '\n'.join(violations))
