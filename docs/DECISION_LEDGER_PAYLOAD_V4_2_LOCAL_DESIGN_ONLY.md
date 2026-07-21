# Decision Ledger Payload 4.2 — Local Design-Only

## Scope

This package defines an immutable, versioned, append-only decision ledger
contract for SMART FUTUROS. It is intentionally not wired into Freqtrade,
RiskManager, Qlib runtime, AI Shadow runtime, active signals, SQLite, private
exchange access, or order submission.

## Record model

Two record types are separated:

1. `decision`: complete feature, model, AI Shadow, RiskManager, allocation and
   final-decision lineage before execution.
2. `trade_link`: append-only correlation between a sealed decision and a
   positive Freqtrade paper `trade_id` after execution is observed.

A trade link never mutates a prior decision. It references both the parent
`event_id` and the sealed decision SHA-256.

## Fail-closed invariants

- Unknown fields are rejected.
- Models are frozen.
- All timestamps are timezone-aware UTC with offset zero.
- Decision time cannot precede feature time.
- Execution time cannot precede decision time.
- `final_decision=ALLOW` requires RiskManager approval, positive stake and
  positive leverage.
- An AI Shadow block cannot become a final allow.
- Payload hashes use deterministic canonical JSON and exclude only the
  `payload_sha256` field itself.
- Design-only mode denies paths containing `data/runtime`.
- Writer exceptions are raised, not swallowed.
- Writer health is persisted atomically with monotonic failure counters.

## Runtime authority

The payload carries explicit false values for:

- `operational_authority`
- `runtime_integration`
- `sends_orders`
- `exchange_private_access`

RiskManager remains the final risk authority. This package does not authorize
paper restart, branch creation, commit, push, pull request, live, or canary.
