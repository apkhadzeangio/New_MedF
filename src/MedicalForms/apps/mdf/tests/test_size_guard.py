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

APP_ROOT = pathlib.Path(__file__).resolve().parent.parent
MAX_FILE_LINES = 800
MAX_FUNC_LINES = 100

# Zero grandfathered exceptions — every Python file under apps/mdf/ is <=800 lines
# and every function/method is <=100 lines.
OVERSIZED_FILES_BASELINE = {}
OVERSIZED_FUNCTIONS_BASELINE = {}


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
