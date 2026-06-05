PYTHON ?= python
PIP ?= $(PYTHON) -m pip

.PHONY: install test test-fast compile lint typecheck security audit paper-check clean-cache

install:
	$(PIP) install --upgrade pip setuptools wheel
	$(PIP) install -e ".[dev,test]"

compile:
	$(PYTHON) -m compileall scripts smartcrypto tests

test:
	$(PYTHON) -m pytest -q

test-fast:
	$(PYTHON) -m pytest tests/test_reproducible_dev_environment_ci_makefile.py tests/test_paper_shadow_soak_reporting_readiness_gate.py tests/test_final_technical_audit_20_pillar_reclassification.py -q

lint:
	$(PYTHON) -c "import importlib.util, subprocess, sys; sys.exit(subprocess.call([sys.executable, '-m', 'ruff', 'check', '.']) if importlib.util.find_spec('ruff') else 0)"

typecheck:
	$(PYTHON) -c "import importlib.util, subprocess, sys; tool = 'mypy' if importlib.util.find_spec('mypy') else None; sys.exit(subprocess.call([sys.executable, '-m', tool, 'smartcrypto']) if tool else 0)"

security:
	$(PYTHON) -m pytest tests/test_reproducible_dev_environment_ci_makefile.py -q

audit: compile security test-fast

paper-check: audit
	$(PYTHON) -c "import os, sys; unsafe = [key for key in ('LIVE_ENABLED', 'ORDER_SUBMISSION_ENABLED', 'REAL_ORDER_SUBMISSION_ENABLED') if os.getenv(key, 'false').lower() in {'1','true','yes','on'}]; print('paper/shadow only check:', 'ok' if not unsafe else ','.join(unsafe)); sys.exit(1 if unsafe else 0)"

clean-cache:
	$(PYTHON) -c "import pathlib, shutil; [shutil.rmtree(path, ignore_errors=True) for path in [pathlib.Path('.pytest_cache'), pathlib.Path('.ruff_cache'), pathlib.Path('.mypy_cache')]]; [shutil.rmtree(path, ignore_errors=True) for path in pathlib.Path('.').rglob('__pycache__')]"
