# Post-P07 Independent Readiness Gap Audit V1

## Purpose

This audit classifies the remaining remote branches after P07 and the Shadow Opportunity Ledger hardening against the certified `dev` baseline.

Canonical baseline:

```text
DEV_SHA=7bf0e2dcf010f2b78caf2a675047870f13f86946
P07=ENCERRADO
SHADOW_LEDGER_HARDENING=ENCERRADO
QLIB_SECURITY_GATE_REMAINS_BLOCKED=true
QLIB_SECURITY_GATE_BYPASSED=false
P08_ALLOWED=false
```

The audit is governance/read-only. It does not alter an active model, model registry, Freqtrade strategy, RiskManager, stake, leverage, stoploss, ROI, runtime execution wiring, order submission or private exchange access.

## Classification vocabulary

- `ALREADY_MERGED_STALE_BRANCH`: branch has no commits ahead of current `dev`; retain only as historical evidence until explicit cleanup.
- `DUPLICATE_CAPABILITY_PRESENT`: old branch is ahead in Git history but its capability already exists in an evolved form on `dev`; do not revive it.
- `STALE_DO_NOT_REVIVE`: branch has unique commits but is too stale, misleadingly named, or mixes scopes; extract evidence only if needed.
- `EXCLUDED_EXECUTION_SCOPE`: unique work touches execution/Freqtrade and is outside the present independent-front boundary.
- `BLOCKED_EXTERNAL`: work is gated by the unresolved upstream Qlib dependency-security condition.
- `ACTIVE_GAP_CANDIDATE`: capability is not present on current `dev`, is independent of Qlib when the optional Qlib path stays disabled/fail-closed, and can remain research-only without active-model/risk/execution authority.

## Branch classification

| Branch | Ahead / behind vs `dev` | Classification | Decision |
| --- | ---: | --- | --- |
| `codex/market-data-health-cli-native-shutdown-hardening-v1` | 0 / 64 | `ALREADY_MERGED_STALE_BRANCH` | Do not revive. |
| `codex/execution-intelligence-v1` | 0 / 24 | `ALREADY_MERGED_STALE_BRANCH` | Do not revive. |
| `codex/opportunity-book-portfolio-allocator-v2` | 0 / 30 | `ALREADY_MERGED_STALE_BRANCH` | Do not revive. |
| `codex/portfolio-of-alphas-fleet-research-v1` | 0 / 28 | `ALREADY_MERGED_STALE_BRANCH` | Do not revive. |
| `codex/relative-value-market-neutral-research-v1` | 0 / 26 | `ALREADY_MERGED_STALE_BRANCH` | Do not revive. |
| `codex/ai-feature-missingness-remediation-implementation-v1` | 0 / 318 | `ALREADY_MERGED_STALE_BRANCH` | Do not revive. |
| `codex/ci-gitpython-3-1-59-security-lock-v1` | 0 / 13 | `ALREADY_MERGED_STALE_BRANCH` | Security fix already incorporated. |
| `codex/paper-shadow-survivor-remediation-research-v1` | 0 / 377 | `ALREADY_MERGED_STALE_BRANCH` | Do not revive. |
| `codex/freqtrade-paper-exit-lifecycle-hardening-v1` | 0 / 61 | `ALREADY_MERGED_STALE_BRANCH` | No new work; execution scope remains excluded. |
| `codex/freqtrade-paper-signal-permission-contract-hotfix-v1` | 0 / 169 | `ALREADY_MERGED_STALE_BRANCH` | No new work; execution scope remains excluded. |
| `codex/qlib-security-clean-dependency-resolution-v1` | 0 / 21 | `BLOCKED_EXTERNAL` | Keep fail-closed; no branch delta currently exists. |
| `codex/decision-ledger-foundation-and-sandbox-harness-v1` | 4 / 205 | `EXCLUDED_EXECUTION_SCOPE` | Do not revive in this front. |
| `codex/decision-ledger-foundation-and-sandbox-harness-v1-dev-rebase` | 1 / 205 | `EXCLUDED_EXECUTION_SCOPE` | Do not revive in this front. |
| `codex/freqtrade-paper-exit-idempotency-guard-v1` | 6 / 92 | `EXCLUDED_EXECUTION_SCOPE` | Unique work modifies `SmartCryptoSignalStrategy.py`; outside boundary. |
| `codex/paper-momentum-fixed-threshold-walkforward-holdout-v1` | 65 / 92 | `STALE_DO_NOT_REVIVE` | Large stale mixed branch; includes Freqtrade execution changes and Qlib-era work. |
| `codex/paper-momentum-forward-oos-observer-v1` | 75 / 92 | `STALE_DO_NOT_REVIVE` | Large stale mixed branch; includes Freqtrade execution changes and Qlib-era work. |
| `codex/paper-financial-performance-metrics` | 1 / 827 | `DUPLICATE_CAPABILITY_PRESENT` | Current `dev` already contains an evolved `smartcrypto/analysis/paper_financial_performance.py`; do not revive. |
| `codex/paper-trade-ntfy-telegram-notifications` | 3 / 827 | `STALE_DO_NOT_REVIVE` | Name does not match the remaining effective old delta; do not revive. |
| `codex/market-features-rematerialization-and-first-training-runs-v1` | 3 / 205 | `ACTIVE_GAP_CANDIDATE` | Reimplement selectively from current `dev`; never merge/cherry-pick the stale branch wholesale. |

## Active gap candidate

The strongest independent candidate is a clean V2 of market-feature rematerialization and ephemeral research challenger evaluation.

The stale V1 contract is useful as design evidence because it already establishes the required safety boundary:

```text
paper/shadow/research-only
no serialized/promoted model
no active registry update
no runtime update
no risk change
no orders
no private exchange access
Qlib optional and fail-closed
```

Current `dev` does not contain `smartcrypto/research/market_features_first_training_runs/pipeline.py`, confirming that the V1 package itself is not present at the current baseline.

The next implementation must therefore be created from current `dev`, not by merging or rebasing the stale V1 branch.

## Recommended next branch

```text
codex/market-features-rematerialization-research-v2
```

Allowed scope:

```text
smartcrypto/research/market_features_rematerialization_v2/
scripts/run_market_features_rematerialization_research_v2.py
tests/test_market_features_rematerialization_research_v2.py
docs/MARKET_FEATURES_REMATERIALIZATION_RESEARCH_V2.md
PROJECT_MANIFEST_CLEAN.json
```

The first V2 branch should stop at point-in-time feature rematerialization, lineage, leakage checks, cohort/drift diagnostics and an ephemeral sklearn smoke challenger. Qlib execution must remain disabled; no model artifact may be persisted or promoted.

Forbidden scope:

```text
freqtrade/user_data/strategies/
smartcrypto/execution/
RiskManager
active model registry
active model artifacts
stake/leverage
stoploss/ROI
runtime signal wiring
orders
private exchange access
Qlib dependency changes
P08 implementation
```

## Gate for starting the V2 implementation

```text
QLIB_SECURITY_GATE_REMAINS_BLOCKED=true
QLIB_SECURITY_GATE_BYPASSED=false
P08_ALLOWED=false

research_only=true
operational_authority=false
model_promotion_performed=false
active_model_changed=false
runtime_updated=false
sends_orders=false
changes_risk=false
exchange_private_access=false
```

## Cleanup policy

This audit does not delete any remote or local branch. Cleanup is a separate explicit action after classification is accepted. Branches with `ahead_by=0` are cleanup candidates, not automatically deleted branches.
