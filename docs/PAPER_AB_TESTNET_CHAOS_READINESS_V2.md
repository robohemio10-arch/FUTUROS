# B06 — Paper A/B, Testnet, Chaos, Capacity and Readiness V2

## Objective

Implement the final research-only engineering gate before a 30-day paper/shadow
soak. The component consolidates evidence for:

1. paper A/B comparison between one champion and one or more challengers;
2. isolated testnet end-to-end execution evidence;
3. deterministic chaos and recovery evidence;
4. capacity and market-impact analysis;
5. unresolved P0/P1 incident control;
6. advisory readiness for the 30-day soak.

The component evaluates evidence. It does not execute testnet or real orders,
change risk, restart containers, modify Freqtrade, update Qlib/IA Shadow runtime,
train models, write active registries or promote a challenger.

## Canonical command

Default no-write probe:

```powershell
python scripts/build_paper_ab_testnet_chaos_readiness_v2.py `
  --project-root . `
  --evidence data/reports/paper_ab_testnet_chaos_evidence_v2.json `
  --json
```

Optional advisory report write, restricted to `data/reports` through the B01
atomic writer:

```powershell
python scripts/build_paper_ab_testnet_chaos_readiness_v2.py `
  --project-root . `
  --evidence data/reports/paper_ab_testnet_chaos_evidence_v2.json `
  --write-report `
  --json
```

`--fail-on-blocked` returns exit code `2` when any B06 gate is blocked.

## Evidence contract

Top-level schema version:

```text
paper_ab_testnet_chaos_evidence_v2
```

Required sections:

```json
{
  "schema_version": "paper_ab_testnet_chaos_evidence_v2",
  "prerequisites": {"g00_status": "PASS"},
  "paper_ab": {
    "champion": {
      "strategy_id": "champion-id",
      "evaluation_window_id": "shared-window-id",
      "trades": []
    },
    "challengers": []
  },
  "testnet_e2e": {"runs": []},
  "chaos": {"scenarios": []},
  "capacity": {"observations": []},
  "incidents": []
}
```

## Paper A/B gate

Each strategy must use the same `evaluation_window_id` and provide the minimum
configured trade count. Every trade requires:

- `trade_id`;
- `symbol` (`BTCUSDT` or `ETHUSDT`);
- `side` (`long` or `short`);
- `close_time_utc`;
- `net_pnl`;
- `notional`;
- `fees`;
- `funding`.

The evaluator calculates:

- trade count;
- win/loss/breakeven counts;
- net PnL;
- gross profit and loss;
- profit factor;
- expectancy;
- win rate;
- average win and loss;
- payoff;
- maximum drawdown;
- turnover;
- total cost and cost in basis points.

A challenger may be recommended for quarantine/soak when it meets the configured
expectancy, profit factor, drawdown and cost criteria. The recommendation is
advisory only:

```text
automatic_promotion=false
model_promotion_performed=false
operational_authority=false
```

The A/B gate can pass while the champion remains selected. A/B completion is not
automatic promotion.

## Testnet E2E gate

The evaluator requires at least three isolated testnet evidence runs. Each run
must explicitly prove:

```text
signal_created
risk_approved
order_submitted_testnet
partial_fill_observed
cancel_observed
reconciliation_complete
restart_recovery_complete
```

Every run must state:

```text
environment=testnet
endpoint_class=testnet
real_order=false
active_runtime_touched=false
```

Production, live or mainnet endpoint evidence is blocked.

The B06 evaluator does not call an exchange. Testnet execution must be produced
by an isolated harness and supplied as evidence.

## Chaos and recovery gate

All mandatory scenarios must pass:

```text
open_trade_restart
qlib_unavailable
signal_missing
sqlite_locked
disk_full
clock_skew
public_api_unavailable
corrupted_report
restart_loop
reconciliation_recovery
```

Each scenario requires:

```text
status=pass
data_loss=false
duplicate_orders=false
active_runtime_touched=false
recovery_seconds<=configured_limit
```

The scenarios must run in an isolated harness. The evaluator itself does not
restart or mutate active containers.

## Capacity and market-impact gate

Minimum evidence is required independently for BTCUSDT and ETHUSDT. Every
observation includes:

- `observation_id`;
- `symbol`;
- `notional`;
- `depth_usdt`;
- `leverage`;
- `participation_ratio`;
- `spread_bps`;
- `slippage_bps`;
- `market_impact_bps`;
- `liquidation_buffer_pct`.

The gate blocks observations that exceed configured limits for total execution
cost, participation, leverage or liquidation buffer. It calculates a
conservative advisory safe notional per symbol:

```text
minimum observed depth × maximum participation ratio
```

This value is not written to RiskManager or Freqtrade.

## Incident gate

No unresolved P0 or P1 incident is permitted. `resolved` and `closed` are the
only terminal statuses accepted for those severities.

## Final readiness decision

The decision is:

```text
READY_FOR_30_DAY_SOAK
```

only when all six gates pass:

1. G00 prerequisite;
2. paper A/B;
3. testnet E2E;
4. chaos/recovery;
5. capacity/market impact;
6. incident control.

Otherwise:

```text
BLOCKED_BEFORE_SOAK
```

This decision authorizes no live/canary release and does not start the soak by
itself. It is an advisory readiness artifact.

## Safety invariants

The following values remain fixed:

```text
research_only=true
paper_only=true
shadow_only=true
live_trading_enabled=false
live_release_allowed=false
canary_release_allowed=false
order_submission_enabled=false
real_order_submission_enabled=false
exchange_private_access=false
sends_orders=false
changes_risk=false
writes_runtime=false
restarts_containers=false
runs_training=false
promotes_model=false
automatic_promotion=false
model_promotion_performed=false
active_model_changed=false
writes_active_registry=false
writes_active_signals=false
updates_freqtrade=false
updates_risk_manager=false
updates_qlib_runtime=false
updates_ai_shadow_runtime=false
```

## Operational status after implementation

The B06 software gate is implemented when this package, CLI, configuration and
tests are merged. B06 operational readiness remains blocked until real,
isolated evidence is supplied for paper A/B, testnet, chaos and capacity.
