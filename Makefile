# Developer task runner for MedicalForms
# Usage: make help

PYTHON ?= $(shell if [ -x /home/antigravity/.venv/bin/python ]; then echo /home/antigravity/.venv/bin/python; elif [ -x .venv/bin/python ]; then echo .venv/bin/python; else echo python3; fi)
RUFF ?= $(shell command -v ruff 2>/dev/null || echo /opt/pipx/bin/ruff)
MANAGE := src/MedicalForms/manage.py
SETTINGS := MedicalForms.settings.dev
TEST_APP := apps.mdf

.PHONY: help check migrations-check size-guard golden lint test-fast test-preop test run migrate

help: ## Show available developer commands
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z0-9_-]+:.*## / {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

check: ## Run Django system checks
	DJANGO_SETTINGS_MODULE=$(SETTINGS) $(PYTHON) $(MANAGE) check

migrations-check: ## Verify no uncreated model migrations exist
	DJANGO_SETTINGS_MODULE=$(SETTINGS) $(PYTHON) $(MANAGE) makemigrations --check --dry-run

size-guard: ## Enforce <=800 lines/file and <=100 lines/function guard
	@if [ -f src/MedicalForms/apps/mdf/tests/test_size_guard.py ]; then \
		DJANGO_SETTINGS_MODULE=$(SETTINGS) $(PYTHON) $(MANAGE) test $(TEST_APP).tests.test_size_guard --verbosity=2; \
	else \
		DJANGO_SETTINGS_MODULE=$(SETTINGS) $(PYTHON) $(MANAGE) test $(TEST_APP).test_size_guard --verbosity=2; \
	fi

golden: ## Run Pre-op Golden Snapshot regression guard
	@if [ -f src/MedicalForms/apps/mdf/tests/test_preop_golden.py ]; then \
		DJANGO_SETTINGS_MODULE=$(SETTINGS) $(PYTHON) $(MANAGE) test $(TEST_APP).tests.test_preop_golden --verbosity=2; \
	else \
		DJANGO_SETTINGS_MODULE=$(SETTINGS) $(PYTHON) $(MANAGE) test $(TEST_APP).test_preop_golden --verbosity=2; \
	fi

lint: ## Run Ruff static analysis across apps/mdf
	$(RUFF) check src/MedicalForms/apps/mdf

test-fast: check migrations-check size-guard ## Run fast structural and schema checks (<5s)

test-preop: ## Run Pre-op registry, reference, vignettes, and golden tests
	@if [ -f src/MedicalForms/apps/mdf/tests/test_preop_registry.py ]; then \
		DJANGO_SETTINGS_MODULE=$(SETTINGS) $(PYTHON) $(MANAGE) test \
			$(TEST_APP).tests.test_preop_registry \
			$(TEST_APP).tests.test_preop_reference \
			$(TEST_APP).tests.test_preop_vignettes \
			$(TEST_APP).tests.test_preop_golden --verbosity=2; \
	else \
		DJANGO_SETTINGS_MODULE=$(SETTINGS) $(PYTHON) $(MANAGE) test \
			$(TEST_APP).test_preop_registry \
			$(TEST_APP).test_preop_reference \
			$(TEST_APP).test_preop_vignettes \
			$(TEST_APP).test_preop_golden --verbosity=2; \
	fi

test: check migrations-check ## Run the complete Django test suite
	DJANGO_SETTINGS_MODULE=$(SETTINGS) $(PYTHON) $(MANAGE) test $(TEST_APP)

migrate: ## Apply database migrations locally (dev SQLite)
	DJANGO_SETTINGS_MODULE=$(SETTINGS) $(PYTHON) $(MANAGE) migrate

run: ## Start local Django dev server on 127.0.0.1:8000
	DJANGO_SETTINGS_MODULE=$(SETTINGS) $(PYTHON) $(MANAGE) runserver 127.0.0.1:8000
