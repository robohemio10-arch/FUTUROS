PYTHON ?= python
PIP ?= $(PYTHON) -m pip
LINT_TARGETS ?= smartcrypto/runtime smartcrypto/ops/backup_restore.py scripts/generate_project_manifest.py scripts/scan_versioned_secrets.py tests/test_complete_ci_security_typecheck_runtime_readiness.py smartcrypto/execution/signal_risk_gate.py smartcrypto/execution/signal_producer.py smartcrypto/execution/signal_contract_guard.py smartcrypto/qlib_engine/signal_exporter.py smartcrypto/ops/paper_signal_riskmanager_runtime_wiring_audit scripts/audit_paper_signal_riskmanager_runtime_wiring_v1.py tests/test_paper_signal_riskmanager_runtime_wiring_v1.py smartcrypto/research/aibot_parity_paper_ab_prospective_collector scripts/run_aibot_parity_paper_ab_prospective_collector_v1.py scripts/run_aibot_parity_prospective_runtime_cycle_v1.py scripts/check_aibot_parity_prospective_runtime_health_v1.py scripts/audit_aibot_parity_prospective_runtime_activation_foundation_v1.py tests/test_aibot_parity_paper_ab_prospective_collector_v1.py tests/test_aibot_parity_prospective_runtime_activation_foundation_v1.py
TYPECHECK_TARGETS ?= smartcrypto/runtime smartcrypto/config/runtime_safety_config.py scripts/generate_project_manifest.py scripts/scan_versioned_secrets.py smartcrypto/execution/signal_risk_gate.py smartcrypto/execution/signal_producer.py smartcrypto/execution/signal_contract_guard.py smartcrypto/qlib_engine/signal_exporter.py smartcrypto/ops/paper_signal_riskmanager_runtime_wiring_audit scripts/audit_paper_signal_riskmanager_runtime_wiring_v1.py smartcrypto/research/aibot_parity_paper_ab_prospective_collector scripts/run_aibot_parity_paper_ab_prospective_collector_v1.py scripts/run_aibot_parity_prospective_runtime_cycle_v1.py scripts/check_aibot_parity_prospective_runtime_health_v1.py scripts/audit_aibot_parity_prospective_runtime_activation_foundation_v1.py
BANDIT_TARGETS ?= smartcrypto/runtime smartcrypto/ops/backup_restore.py smartcrypto/ops/system_healthcheck.py scripts/generate_project_manifest.py scripts/scan_versioned_secrets.py smartcrypto/execution/signal_risk_gate.py smartcrypto/execution/signal_producer.py smartcrypto/execution/signal_contract_guard.py smartcrypto/qlib_engine/signal_exporter.py smartcrypto/ops/paper_signal_riskmanager_runtime_wiring_audit scripts/audit_paper_signal_riskmanager_runtime_wiring_v1.py smartcrypto/research/aibot_parity_paper_ab_prospective_collector scripts/run_aibot_parity_paper_ab_prospective_collector_v1.py scripts/run_aibot_parity_prospective_runtime_cycle_v1.py scripts/check_aibot_parity_prospective_runtime_health_v1.py scripts/audit_aibot_parity_prospective_runtime_activation_foundation_v1.py

.PHONY: install test test-fast compile lint typecheck security audit paper-check clean-cache

install:
	$(PIP) install --upgrade pip setuptools wheel
	$(PIP) install -r requirements-dev.lock
	$(PIP) install --no-deps -e .

compile:
	$(PYTHON) -m compileall scripts smartcrypto tests

test:
	$(PYTHON) -m pytest -q

test-fast:
	$(PYTHON) -m pytest tests/test_reproducible_dev_environment_ci_makefile.py tests/test_paper_shadow_soak_reporting_readiness_gate.py tests/test_final_technical_audit_20_pillar_reclassification.py -q

lint:
	$(PYTHON) -m ruff check $(LINT_TARGETS)

typecheck:
	$(PYTHON) -m mypy $(TYPECHECK_TARGETS) --ignore-missing-imports --follow-imports=skip

security:
	$(PYTHON) -m pytest tests/test_reproducible_dev_environment_ci_makefile.py -q
	$(PYTHON) -m bandit -q -r $(BANDIT_TARGETS) --severity-level medium --confidence-level medium
	$(PYTHON) -m pip_audit -r requirements-dev.lock --progress-spinner off
	$(PYTHON) scripts/scan_versioned_secrets.py --json

audit: compile lint typecheck security test-fast

paper-check:
	LIVE_ENABLED=false ORDER_SUBMISSION_ENABLED=false REAL_ORDER_SUBMISSION_ENABLED=false SMARTCRYPTO_EXCHANGE_PRIVATE_ACCESS=false $(PYTHON) -m smartcrypto.runtime.container_healthcheck --required-path smartcrypto --required-import smartcrypto --quiet

clean-cache:
	$(PYTHON) -c "import pathlib, shutil; [shutil.rmtree(path, ignore_errors=True) for path in [pathlib.Path('.pytest_cache'), pathlib.Path('.ruff_cache'), pathlib.Path('.mypy_cache')]]; [shutil.rmtree(path, ignore_errors=True) for path in pathlib.Path('.').rglob('__pycache__')]"
