# Futures Execution Realism Engine V2

## Purpose

The B03 engine is a deterministic, event-driven research layer for futures
execution studies. It models order-book depth, queue position, partial fills,
latency, execution costs, funding, margin, liquidation, and portfolio exposure.
It does not replace Freqtrade, RiskManager, an exchange gateway, or any
operational executor.

The default command runs a small sanitized synthetic fixture:

```powershell
python scripts/build_futures_execution_realism_engine_v2.py `
  --project-root . `
  --json
```

This command is no-write. The result is always marked `fixture_only=true` and
`authoritative_result=false`.

## Event Contract

Every market or lifecycle event contains:

- `event_id`
- `event_type`
- `symbol`
- `event_time_utc`
- `receive_time_utc`
- `sequence`
- `source`
- `source_hash`
- `schema_version`
- typed event payload

The canonical ordering is:

```text
(event_time_utc, sequence, receive_time_utc, event_id)
```

Conflicting event IDs, conflicting duplicate sequences, and regressive source
sequences fail closed. Exact replay is idempotent. A decision can only observe
events whose event and receive timestamps are available at the simulated
decision time. The simulation clock is injected and never sleeps.

Supported event types include book snapshots and deltas, trades, mark prices,
funding, signal intent, order submission and acknowledgement, rejection,
partial and complete fills, cancel, reprice, timeout, stop trigger, margin
update, liquidation, and position close.

## Order Book And Orders

The L2 book requires:

- positive prices and quantities;
- descending bids and ascending asks;
- unique price levels;
- `best_bid < best_ask`;
- monotonic deltas;
- a non-stale snapshot at simulated order arrival.

Market orders consume actual levels in price priority. Missing depth remains
explicitly unfilled. Limit orders never execute beyond their price. IOC cancels
the residual, while FOK validates full visible depth before execution.
Post-only orders are rejected by default when they cross; explicit policy can
reprice them to the safe side of the spread.

Simulation IDs use only the `simulation_only` namespace. Repricing creates a
linked child order and does not mutate the parent identity.

## Queue And Latency

Queue models:

- `pessimistic_queue` (default)
- `proportional_queue`
- `deterministic_front`
- `deterministic_back`

Trade volume consumes queue ahead before it fills a resting order. Book changes
never create retroactive fills.

Latency components are independent:

- signal to submit
- client to exchange
- exchange acknowledgement
- market data
- cancel
- reprice
- jitter

Allowed distributions are constant, deterministic empirical fixtures, seeded
lognormal, and seeded gamma. Negative latency is invalid. API timeout yields
`UNKNOWN` and requires reconciliation; it is not silently converted into
rejection or cancellation.

## Costs And Funding

The versioned cost model calculates maker/taker fees per fill. Missing fees,
unknown liquidity role, or missing funding rate fail closed.

Cost reconciliation follows:

```text
net_pnl =
    realized_price_pnl
    - trading_fees
    - funding_fees
    - spread_cost
    - slippage_cost
    - market_impact_cost
    - liquidation_penalty
    - retry_reprice_costs
    - other_supported_costs
```

Observed spread and book walk are separate from modeled fixed-bps or
square-root components. The conservative hybrid is the default. Modeled
assumptions make the execution attribution non-authoritative and are listed in
the report. Funding is applied only while a matching position exists at the
funding event and is signed by LONG/SHORT direction.

## Margin And Liquidation

The margin engine supports isolated and cross modes with explicit maintenance
tiers, contract size, leverage, funding, closing fees, and liquidation penalty.
Missing leverage, contract size, margin mode, or mark price fails closed.

`MarkPriceEvent` is the liquidation authority. Last trade never silently
replaces mark price. Reports expose initial margin, maintenance margin,
position margin, unrealized PnL, funding, liquidation buffer, and margin ratio.

Partial liquidation is attempted under an explicit deterministic fraction and
is retained only when it restores a positive margin state. Otherwise the engine
performs full liquidation and reports bankruptcy shortfall. When stop and
liquidation are both reachable without intrabar ordering evidence, the policy
is `liquidation_or_worst_valid_outcome_first`.

## Portfolio Exposure

Research metrics include gross, net, long, short, concentration, cross-margin
dependency, correlated exposure, and a liquidation-cascade proxy. Missing
cross-symbol correlations block the correlated metric. No result is sent to
RiskManager and no operational limits are published.

## B02 Authority Boundary

The B02 boundary is preserved:

- quarantined Trader Master or candle inputs never become verified;
- legacy quarantined inputs may be inspected only when explicitly classified
  `legacy_research_non_authoritative`;
- such runs return `warning`, `reason=input_not_authoritative`, and
  `authoritative_result=false`;
- synthetic fixtures remain non-authoritative.

No fill, fee, funding, leverage, candle, depth, or mark price is fabricated.

## Reports And B01/B02 Integration

Writing requires explicit `--write-report`:

```powershell
python scripts/build_futures_execution_realism_engine_v2.py `
  --project-root . `
  --write-report `
  --json
```

Only these ignored research paths are allowed:

- `data/reports/futures_execution_realism_engine_v2.json`
- `data/reports/futures_execution_realism_engine_v2.md`
- `data/reports/futures_execution_realism_engine_v2/manifests/**`

JSON and Markdown use the certified B01 atomic writer. The immutable execution
manifest uses B02 and records dataset hash, dataset manifest hash, engine
config hash, cost model hash, schema hash, dependency lock hash, seed, commit,
branch, and safety flags. Reusing the same manifest identity is rejected; no
previous execution manifest is overwritten.

## Safety Boundary

The package does not import or invoke Freqtrade, CCXT, exchange clients,
RiskManager, Qlib runtime, IA Shadow runtime, signal publication, models,
registries, Docker, or private account APIs. It cannot send orders or alter
stake, leverage, ROI, stoploss, risk, model, runtime, or active signals.

Safety flags remain:

```text
paper_only=true
shadow_only=true
research_only=true
operational_authority=false
live_trading_enabled=false
order_submission_enabled=false
real_order_submission_enabled=false
exchange_private_access=false
sends_orders=false
changes_risk=false
changes_model=false
```

## Validation

```powershell
python -m compileall -q scripts smartcrypto tests
python -m pytest tests/test_futures_execution_realism_engine_v2.py -q
python -m ruff check scripts/build_futures_execution_realism_engine_v2.py `
  smartcrypto/research/futures_execution_realism_v2 `
  tests/test_futures_execution_realism_engine_v2.py
python -m mypy smartcrypto/research/futures_execution_realism_v2 `
  scripts/build_futures_execution_realism_engine_v2.py `
  --ignore-missing-imports
python -m bandit -q -r smartcrypto/research/futures_execution_realism_v2 `
  scripts/build_futures_execution_realism_engine_v2.py `
  --severity-level medium --confidence-level medium
```

## Objective Audit

| Risk | Mitigation | Unchanged boundary |
| --- | --- | --- |
| Future market data affects an earlier order | Event and receive timestamps plus market-data latency gate availability | No runtime market-data source is connected |
| Missing depth or cost input creates optimistic execution | Missing liquidity remains unfilled; fees and funding fail closed | No values are inferred from PnL |
| Research output appears operational | B02 authority classification and closed safety flags remain in every report | Freqtrade, RiskManager, exchange, models, and signals are untouched |

Residual risk is model risk: queue, latency, and modeled impact assumptions may
not match a venue. They are versioned, explicit, reproducible, and cannot
produce an authoritative result when based on assumptions.
