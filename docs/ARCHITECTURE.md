# MedicalForms Architecture Guide

This document gives a developer a fast, accurate map of the `New_MedF` (MedicalForms) codebase:
what each module owns, how clinical pipelines flow, where files live, and which safety guards
protect the repository.

---

## 1. Repository Layout

```text
.
├── Makefile                      # One-command dev/test/lint workflows
├── pyproject.toml                # Ruff lint & import-sorting config (Python 3.12)
├── requirements/
│   ├── prod.txt                  # Production dependencies (Django 5.2 LTS, Pillow, docxtpl, whitenoise)
│   └── dev.txt                   # Local dev extras (ipython, playwright)
├── deploy/contabo/               # Production deployment configs & scripts (ramaz.ge)
├── docs/
│   ├── ARCHITECTURE.md           # This file
│   ├── DEVELOPMENT.md            # Setup, testing, migrations, and coding rules
│   └── design/                   # Reference artwork for vector limb maps
├── tools/
│   ├── smoke                     # Playwright headless browser check across main routes
│   └── check_pdfs                # LibreOffice + Poppler visual verification of printable docs
└── src/MedicalForms/
    ├── manage.py
    ├── MedicalForms/
    │   ├── urls.py               # Root URLconf (admin + apps.mdf.urls)
    │   └── settings/
    │       ├── base.py           # Shared settings, .env loader, PRIVATE_MEDIA_ROOT
    │       ├── dev.py            # SQLite dev/test settings (MedicalForms.settings.dev)
    │       └── prod.py           # MySQL / Contabo / PythonAnywhere production settings
    ├── static/                   # CSS, JS, fonts (Noto Sans Georgian), icons, staff avatars
    ├── templates/mdf/            # Django HTML templates (grouped by feature)
    └── apps/mdf/                 # Single core Django application ("mdf")
```

---

## 2. Core Application Modules (`src/MedicalForms/apps/mdf/`)

The app is organized by clinical and operational domain:

| Domain | Module / Package | Responsibility |
| --- | --- | --- |
| **Data Model** | `models` (`models/` package) | All 23 Django ORM models across prescriptions, inpatient board, roster/consent, clinical cases, and pre-op assessment, plus `PrivateStorage`. |
| **Prescriptions (NIV-300-9/a)** | `views.py`, `forms.py`, `analysis.py`, `medicines.py`, `enums.py` | Medication sheets, reusable templates, nosology analysis, duplicate medicine cleanup, `.docx`/PDF export via `django-docxtpl` + LibreOffice. |
| **Inpatient Board & Duty** | `board.py`, `duty.py`, `blood.py`, `reminders.py` | ICU/ward patient board, handover reports, blood order blanks, shift reminders. |
| **Operations & Utility** | `consent.py`, `roster.py`, `quotes.py`, `avatars.py`, `pwa.py`, `backends.py`, `mixins.py`, `utils.py` | Informed consent (`NIV-300-12/a`), surgeon duty roster, PWA manifest/SW, staff auth backend, shared view mixins. |
| **Clinical Cases & Doppler** | `cases` (`cases/` package), `doppler.py`, `doppler_glyphs.py` | Private case photos (EXIF stripped, watermark/thumb generated via Pillow), Doppler PDF parser & glyph decoder, clinical decision log. |
| **Vascular Staging & Maps** | `staging` (`staging/` package), `artery_map` (`artery_map/` package) | GLASS (FP/IP/inflow/TAP), WIfI (W/I/fI), and PLAN clinical staging tables, SVG Bezier limb/foot artery maps, Georgian protocol generation. |
| **Pre-op Assessment (POA)** | `preop/` package | Tweek-style weekly calendar, nurse/surgeon workspace, clinical decision engine, evidence registry, 40 synthetic clinical vignettes, patient self-fill, A4 passport. |
| **URLs & Admin** | `urls.py`, `admin.py`, `templatetags/`, `management/commands/` | Domain-grouped URL routes, Django admin, static cache-buster tags, `setup_staff` & `backup_data` commands. |

---

## 3. Pre-op Module Architecture (`apps/mdf/preop/`)

The preoperative assessment engine follows a clean **pure-function evaluation pipeline** separated
from Django HTTP views and clinical evidence metadata:

```text
PreopPatient (DB model + JSONField: medical_history, meds, labs, vitals, studies)
        │
        ▼
  preop/engine.py :: evaluate(patient)
        │
        ├── 1. calculators.py        (Age, BMI, BSA, CrCl Cockcroft-Gault ABW, eGFR CKD-EPI 2021,
        │                             RCRI, VSG-CRI, ARISCAT, Caprini, Gurm, ABI/TBI unit guards)
        ├── 2. rules/context.py      (EvalContext: normalized flags, units, procedure pathway)
        ├── 3. rules/ (ordered rule modules reading thresholds from registry/)
        │       ├── aaa.py           (ESVS 2024 AAA / EVAR / Open repair)
        │       ├── cea.py           (ESVS 2023 Carotid Endarterectomy / CAS)
        │       ├── abf.py           (ESVS 2024 PAD / CLTI / Aortobifemoral & infrainguinal bypass)
        │       ├── pta.py           (Endovascular PTA / contrast nephropathy / Gurm)
        │       ├── venous.py        (ESVS 2022 EVLA / phlebectomy / Caprini VTE)
        │       ├── labs.py          (NICE NG45 rational testing, Hb/Plt/INR/K+/glucose/renal guards)
        │       ├── cardiac.py       (ESC 2022 cardiac clearance, METs, CIED, echo, valves)
        │       ├── anaesthesia.py   (ASRA 2025 neuraxial safety, OSA/STOP-BANG, fasting, WHO Sign-In)
        │       ├── meds.py          (PAUSE/CHEST/EHRA antithrombotics, SGLT2, GLP-1, ACEi/ARB)
        │       ├── failsafe.py      (Track A5: unknown-state banners, score completeness, GO guard)
        │       └── briefings.py     (Surgeon / Anesthesiologist / Nurse role briefings)
        └── 4. rules/result.py       (Structured dict: status, stops, alerts, orders, scores, briefings)

Supporting Pre-op Modules:
  • preop/registry/        — Single source of truth for all clinical rules & thresholds (v1.1.0,
                             75 rules with exact guideline citations, classes/levels, and version log).
  • preop/vignettes/       — 40 synthetic end-to-end clinical test cases covering every registry rule.
  • preop/meds.py          — Exact calendar-date perioperative drug hold/restart schedule builder.
  • preop/anamnesis.py     — Structured Georgian preoperative anamnesis text generator.
  • preop/case_import.py   — Safe non-destructive prefill from linked clinical Case/Doppler records.
  • preop/views/           — Thin Django CBVs (week, patient, passport, self_fill, files, board,
                             audit, rules_catalog, vignettes).
```

---

## 4. Vascular Staging & Vector Map Architecture (`staging/` + `artery_map/`)

- **`staging/`**: Implements ESVS/SVS **GLASS**, **WIfI**, and **PLAN** scoring for lower-limb PAD/CLTI,
  segment-level schema validation/migration (`v1 → v2`), intervention-to-lumen synchronization,
  automated clinical suggestions (`suggest_from_map`), and Georgian protocol summary lines.
- **`artery_map/`**: Pure geometric SVG builder turning segment stenosis/occlusion/intervention data
  into Bezier artery paths, collateral arches, bypass/stent overlays, and plantar angiosome diagrams
  rendered in Case detail, Presentation, and Print views.

---

## 5. Security, Storage & Privacy Boundaries

- **Private Media (`PRIVATE_MEDIA_ROOT`)**: Patient case photos, Doppler PDFs, and pre-op lab files
  are stored **outside** `STATIC_ROOT` / public `/media/` using `PrivateStorage`, and served strictly
  through authenticated Django views with explicit `Content-Security-Policy` headers.
- **No Real Patient Data in Git**: All tests, fixtures (`apps/mdf/fixtures/`), clinical vignettes,
  and browser scripts use 100% synthetic data. `db.sqlite3` and `.env` are gitignored.
- **BMP-Only Source Encoding**: Production Python, HTML, JS, and CSS files stay within the Unicode
  Basic Multilingual Plane (`ord(ch) <= 0xFFFF`) for MySQL `utf8` / legacy font compatibility.

---

## 6. Automated Quality & Safety Guards

| Guard | Location | What It Enforces |
| --- | --- | --- |
| **Size Guard** | `tests/test_size_guard.py` | Every Python file under `apps/mdf/` (except `migrations/`) `<= 800` lines; every function/method `<= 100` lines (zero grandfathered baselines). |
| **Golden Snapshot** | `tests/test_preop_golden.py` | Byte-identical regression lock on `evaluate()`, `generate_anamnesis()`, `build_med_schedule()`, and rendered pre-op HTML across fixtures & simulation cohorts. |
| **Registry Coverage** | `tests/test_preop_registry.py` | Every clinical rule in code resolves its threshold from `preop/registry/`; zero orphan rules or magic numbers. |
| **Reference Math** | `tests/test_preop_reference.py` | Published-source verification of RCRI, VSG-CRI, ARISCAT, CKD-EPI 2021, Cockcroft-Gault ABW, Caprini, Gurm, PAUSE/ASRA/EHRA. |
| **Clinical Vignettes** | `tests/test_preop_vignettes.py` | All 40 synthetic clinical cases assert expected stops, alerts, holds, and statuses + 100% registry rule firing. |
| **Monte Carlo Sim** | `tests/sim/` | Stratified cohort invariant & guideline-oracle stress testing across vascular pathways. |
