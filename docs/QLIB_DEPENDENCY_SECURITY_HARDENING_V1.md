# Qlib Dependency Security Hardening V1

## Status

- `status=blocked`
- `reason=upstream_constraint_blocked`
- `decision=MANTER_EM_RESEARCH`
- `approved_security_clean_resolution_found=false`
- `qlib_security_gate_passed=false`

This document records a fail-closed research-only dependency-security boundary for
the Qlib stack. It does not authorize runtime activation, model promotion, live
trading, canary release, private exchange access, order submission, or changes to
RiskManager.

## Certified resolver evidence — 2026-08-20

Environment observed during the controlled audit:

- Python `3.12.10`
- pip `26.1.2`
- direct repository contract: `pyqlib==0.9.7`

### Natural resolution

The unbounded resolver completed successfully with 193 packages and selected:

- `pyqlib==0.9.7`
- `mlflow==3.15.1`
- `mlflow-skinny==3.15.1`
- `mlflow-tracing==3.15.1`
- `cryptography==49.0.0`
- `pyarrow==25.0.1`

`pip-audit` returned exit code `1` with one known vulnerability:

- `cryptography==49.0.0`
- `PYSEC-2026-3552`
- fix version reported: `50.0.0`

Therefore resolver success does not imply security-gate success.

### Forced cryptography 50 resolution

When `cryptography>=50.0.0` was required, pip backtracked to a materially older
MLflow/PyArrow stack:

- `pyqlib==0.9.7`
- `mlflow==3.2.0`
- `mlflow-skinny==3.2.0`
- `mlflow-tracing==3.2.0`
- `cryptography==50.0.0`
- `pyarrow==21.0.0`

The resolver completed with 181 packages, but `pip-audit` returned exit code `1`
with 26 known vulnerabilities in two packages:

- 25 findings in `mlflow==3.2.0`
- 1 finding in `pyarrow==21.0.0`
- PyArrow finding: `PYSEC-2026-113`, fix version `23.0.1`

Therefore fixing one package does not imply that the dependency graph is secure.

### Modern MLflow with cryptography 50

The explicit combination:

- `pyqlib==0.9.7`
- `mlflow==3.15.0`
- `cryptography==50.0.0`
- `pyarrow==25.0.0`

returned `ResolutionImpossible` because MLflow 3.15.0 requires
`cryptography>=43.0.0,<50`.

## Institutional decision

No approved security-clean resolution was found for the current Qlib dependency
contract. The correct state is therefore:

- `status=blocked`
- `reason=upstream_constraint_blocked`
- `decision=MANTER_EM_RESEARCH`

This is an explicit blocker, not a warning and not an implicit exception.

## Semantic invariants

The auditor enforces these rules:

1. Resolver success is not security-gate success.
2. Fixing one vulnerable package is not proof that the whole graph is secure.
3. Missing evidence is blocking.
4. Unknown vulnerability findings are blocking.
5. A dependency downgrade caused by a security constraint requires a complete
   graph audit.
6. No approved security-clean resolution means the Qlib security gate remains
   blocked.

## Runtime and authority boundary

The audit is static and offline by default. It does not call pip, PyPI, GitHub,
Docker, CCXT, exchanges, Freqtrade, Qlib runtime, or external HTTP services.

Default execution performs no writes. Optional report persistence is restricted
to `data/reports` and uses same-filesystem atomic replacement.

Safety invariants:

- `paper_only=true`
- `shadow_only=true`
- `research_only=true`
- `operational_authority=false`
- `runtime_updated=false`
- `models_changed=false`
- `model_promotion_performed=false`
- `changes_risk=false`
- `sends_orders=false`
- `exchange_private_access=false`
- `live_trading_enabled=false`
- `order_submission_enabled=false`
- `real_order_submission_enabled=false`
- `canary_release_allowed=false`
- `live_release_allowed=false`

## Out of scope

This hardening does not modify:

- `requirements-qlib.lock`
- `requirements-dev.lock`
- `requirements-runtime.lock`
- `pyproject.toml`
- Docker or Compose
- Freqtrade
- RiskManager
- active Qlib models or runtime
- IA Shadow runtime
- strategy, stake, leverage, ROI, stoploss or max-open settings
- active signals
- model registry
- operational data

The branch must not use `--no-deps`, metadata overrides, patched local wheels,
silent forks, `pip-audit --ignore-vuln`, or any mechanism that hides the
dependency-security failure.
