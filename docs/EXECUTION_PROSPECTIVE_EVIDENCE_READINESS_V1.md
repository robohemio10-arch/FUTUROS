# Execution Prospective Evidence Readiness V1

Offline research audit, not a collector, execution policy, or economic uplift claim.
No write option exists. No runtime service, private exchange call, or order is activated.

## Sources And Authority

- Sealed Decision Ledger 4.2: OBSERVED decisions, not exchange executions.
  Existing payload validation and trade-link parent seals are reused.
- Existing Paper lifecycle CSV/source profile/SQLite snapshot: MODELLED prices,
  quantities, fees and orders. Existing read-only loaders and query-only temporary
  snapshot inspection are reused; the lifecycle producer is never invoked.
- An optional independently sealed execution archive can provide OBSERVED
  execution exports and observed L1. Its provenance must identify venue, market,
  source, scoped account hash, capture UTC and observed clock basis.
  Hashes prove byte integrity, not exchange authenticity. An institutional operator
  must verify origin outside this offline tool. Relabeling a Paper export is invalid.
- OHLCV, aggTrades, strategy tags, open/close times and simulated fill probabilities
  cannot substitute for arrival quotes, maker/taker, per-fill fees or execution clocks.

## Prospective Contract

The canonical Pydantic schema is emitted with `--schema`; its canonical SHA256 is
required in the archive. Extra fields, non-finite numbers and non-UTC clocks fail.
No local machine path is part of the schema.

Identity: `DecisionRecordV42.event_id` is `decision_id`. Orders reference its exact
payload SHA256, symbol and explicit ENTRY/EXIT intent (exit reverses position side).
An exit does not substitute for a missing entry order. Orders/fills use `(source_id, order_id)` and
`(source_id, fill_id)` keys; source includes account/venue/market namespace. Never
derive exchange IDs from local Paper trade IDs, times, prices or approximate joins.
Duplicate keys, orphan fills and mismatched parents block the gate. Multiple orders
and partial fills are supported; all filled quantities must reconcile.

Clocks: UTC decision <= submit <= ack <= fill <= observation. Cancellations carry
time/reason, follow ack, and cannot precede linked fills. Missing clocks are missing,
not zero latency. Capture freshness is separate from event-time quote freshness.

Prices/quantities: positive fill price and base-asset quantity; requested limit price
is mandatory only for LIMIT. No unit/notional inference. Every fill has actual signed
fee amount, currency, fee-regime identity and explicit maker/taker. An observed rebate
can be negative; an authoritative zero fee is permitted, but a missing fee is not zero.
No implicit conversion of non-USDT fees into USDT costs.

L1: reuse MarketSlice, including event/available-at, bid/ask and displayed quantities.
Decision and submit quote references must have the same venue, market and symbol,
be available no later than that clock, and meet the evidence age policy.
The quote source hash seals its Provenance; the archive hash seals quote bytes.
Default maximum quote age is 5 seconds (inclusive) and capture age 300 seconds
(inclusive). These are research evidence requirements, not trading/PIT thresholds.

L2 and queue are explicitly UNAVAILABLE in V1: no supported archive binding for
depth sequence continuity, gap detection, queue priority or own-order book position.
Even a basic evidence READY cannot certify queue-sensitive fill improvement.
Any future depth binding must reuse futures_execution_realism_v2 OrderBook/contracts,
not introduce another simulator. No L2 is inferred from klines or aggTrades.

## Gate And Reproduction

EXECUTION_EVIDENCE_READY requires a nonempty complete ALLOW cohort with observed
orders, actual fills, clocks, fees, role, causal L1, schema/seal/freshness/integrity and
quantity reconciliation. BLOCK decisions do not require an order. All other cases
return BLOCKED_MISSING_EXECUTION_EVIDENCE with sorted deterministic reasons.
Coverage denominators are explicit; empty coverage is null, never 100 percent.
Paper inventories are reported separately and cannot upgrade observed coverage.

```powershell
python -B scripts/build_execution_prospective_evidence_readiness_v1.py --runtime-root <runtime-root> --as-of-utc <UTC-ISO8601> --json
python -B scripts/build_execution_prospective_evidence_readiness_v1.py --runtime-root <runtime-root> --execution-evidence <sealed-archive.json> --execution-evidence-sha256 <SHA256> --json
```

Exit 0 means basic evidence READY; exit 2 means blocked. This is not operational
authorization or economic certification. All safety flags remain research/paper/shadow;
orders, private access, runtime writes, promotion and uplift claims remain disabled.
The report exposes raw-file hashes, schema hash, sizes, mtime, source profile hash,
read integrity, field coverage and blockers, never credentials or raw account IDs.
Supply a fixed observation UTC for deterministic replay of the audit only; no trading
replay or backfill is performed.
