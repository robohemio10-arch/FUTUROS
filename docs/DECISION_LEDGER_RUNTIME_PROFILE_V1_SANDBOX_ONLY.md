# Decision Ledger Runtime Profile V1 — P0.4B

## Status

This package is a sandbox-only mapping specification. It does not integrate the
decision ledger with Freqtrade, RiskManager, Qlib, AI Shadow, Redis, SQLite,
Docker, exchange APIs or order submission.

## Architectural decision

The certified P0.3B payload remains immutable. It explicitly records:

- `runtime_integration=false`
- `operational_authority=false`
- `sends_orders=false`
- `exchange_private_access=false`

P0.4B therefore introduces a separately versioned projection envelope:

`decision_ledger_runtime_observability_profile_v1`

The envelope preserves operational lineage that is not present in payload 4.2,
including:

- `risk_checked_at_utc`
- `risk_policy_id`
- `risk_config_hash`
- `source_signal_sha256`
- authoritative DB fingerprint for `trade_link`
- field-source registry hash
- mapping-input hash

## Canonical mapping boundaries

1. Candidate, feature and model lineage are assembled by the future signal
   orchestration adapter.
2. AI Shadow remains observational and cannot grant operational authority.
3. RiskManager remains final authority.
4. The final decision is projected before active signal publication.
5. Freqtrade remains a consumer of correlated approved signals.
6. `trade_link` is projected only after authoritative `trade_id` observation.
7. No writer is invoked in P0.4B.

## Fail-closed rules

- Unknown fields are rejected.
- Missing identifiers are rejected.
- `risk_approved` is strict boolean.
- Rejected risk cannot produce `ALLOW`.
- Blocked decisions require zero stake and zero leverage.
- Symbol must match normalized pair.
- All timestamps must use UTC offset zero.
- Decision and trade-link identifiers are deterministic and idempotent.
- Runtime integration, branch creation and paper restart remain blocked.
