# Developer Guide

Quick-reference guide for setting up, editing, testing, and deploying **MedicalForms** (`New_MedF`).

---

## 1. Local Setup

Requires **Python 3.12+** (LibreOffice `soffice` is needed for `.docx → PDF` print tests).

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements/prod.txt
# Optional (browser smoke tests & Playwright):
pip install -r requirements/dev.txt
```

Initialize local SQLite database and run the dev server:

```bash
make migrate
make run
```

All local management commands use `DJANGO_SETTINGS_MODULE=MedicalForms.settings.dev`
(SQLite database at `src/MedicalForms/db.sqlite3`, which is gitignored).

---

## 2. Daily Commands (`Makefile`)

| Command | Purpose |
| --- | --- |
| `make help` | List all available targets |
| `make check` | Django system checks (`manage.py check`) |
| `make migrations-check` | Fail if models diverge from committed migrations |
| `make size-guard` | Enforce `<= 800` lines/file and `<= 100` lines/function |
| `make golden` | Run Pre-op Golden Snapshot byte-identity guard |
| `make lint` | Run Ruff (`pyproject.toml`) across `apps/mdf` |
| `make test-fast` | Fast structural + schema + size-guard gate (`< 5s`) |
| `make test-preop` | Run registry, reference, vignettes, and golden pre-op tests |
| `make test` | Full Django test suite (must pass 100% before every push) |

To run a single test class or method directly:

```bash
DJANGO_SETTINGS_MODULE=MedicalForms.settings.dev \
  python src/MedicalForms/manage.py test apps.mdf.<module>.<TestClass>.<test_method>
```

---

## 3. Code Organization & Modularity Rules

1. **File & Function Size Limits (`test_size_guard.py`)**
   - Every Python file under `apps/mdf/` (excluding `migrations/`) must stay **`<= 800` lines**.
   - Every function or method must stay **`<= 100` lines**.
   - Prefer splitting large domains into focused subpackages (`models/`, `cases/`, `artery_map/`, `staging/`, `preop/`, `tests/`) with clean `__init__.py` re-exports so external imports stay stable.

2. **Clinical Rule Thresholds (`apps/mdf/preop/registry/`)**
   - Never hardcode clinical thresholds or drug-hold intervals inside `preop/rules/*.py`.
   - Register the rule with its primary guideline citation in `preop/registry/` and read it via `rule_value('RULE_ID')`.
   - Bump `PROTOCOL_VERSION` / `PROTOCOL_CHANGELOG` in `preop/registry/__init__.py` when clinical logic changes.

3. **Safe Database Migrations**
   - Production runs live clinical data on MySQL (`ramaz.ge`).
   - Migrations should **only add** tables or fields (with sensible defaults or `null=True`/`blank=True`); never destructively drop or rename columns that hold clinical data without a staged data-migration plan.
   - Always verify `make migrations-check` before committing.

4. **Golden Snapshot Policy**
   - Pure structural refactors must keep the Golden Snapshot **byte-identical** — never regenerate snapshots just to silence a refactor failure.
   - Only update snapshots in an intentional clinical/UI commit where the diff is explicitly reviewed and explained.

5. **Static Asset Cache Busting**
   - `preop.css` and `preop-patient.js` include content-hash `?v=` parameters in templates via template tags. Modifying static files changes rendered HTML hashes (and therefore Golden Snapshots).

6. **Encoding & Privacy**
   - Keep all production source files within Unicode BMP (`ord(ch) <= 0xFFFF`).
   - Never commit real patient names, personal IDs, phone numbers, photos, or SQL dumps.

---

## 4. CI & Deployment

- **GitHub Actions** (`.github/workflows/tests.yml`) runs on every push and PR on `ubuntu-24.04` with Python 3.12 and LibreOffice:
  1. `manage.py check`
  2. `manage.py makemigrations --check --dry-run`
  3. Fast structural guard (`test_size_guard`)
  4. Full `manage.py test`
- **Production Deployment**: Pushes to the deployed branch auto-deploy to `ramaz.ge` (see `deploy/contabo/README.md`). Never push with a failing local test.
