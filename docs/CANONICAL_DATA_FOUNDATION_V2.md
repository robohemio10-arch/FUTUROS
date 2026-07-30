# Canonical Data Foundation V2

## Purpose

Canonical Data Foundation V2 closes the research lineage boundary for the immutable
Trader Master, the historical candle gaps, and three institutionally separate dataset
classes. It is paper-only, shadow-only, research-only, and has no operational authority.

The foundation does not repair evidence by assumption. A row is `VERIFIED` only when all
required identity and financial fields are backed by authoritative evidence and the
accounting identity is reconciled. Otherwise, after the registered primary and secondary
sources are recorded and exhausted, the row ends as `PERMANENT_QUARANTINE`.

## Source Map

| Source | Contract | Reader | Writer | Dataset | Manifest |
| --- | --- | --- | --- | --- | --- |
| Immutable Trader Master | `trader_master_financial_lineage_v2` | `read_trader_master_readonly` | none | `HistoricalResearchDataset` | `canonical_execution_manifest_v2` |
| Closed reconciled paper trades | `paper_outcome_dataset_v2` | `paper_outcome_reader_v2` | `paper_outcome_writer_v2` | `PaperOutcomeDataset` | `canonical_execution_manifest_v2` |
| Validated public market data | `operational_feature_dataset_v2` | `operational_feature_reader_v2` | `operational_feature_writer_v2` | `OperationalFeatureDataset` | `canonical_execution_manifest_v2` |

The existing Trader Master Parquet is read through a temporary copy by
`smartcrypto.data.trader_master_fingerprint_v2.master_adapter.read_trader_master_readonly`.
Its hash and size are checked before and after the read. The XLSX and Parquet sources are
never modified by B02.

The lineage domain, no-write orchestrator, and CLI are registered by exact path in
`trader_master_legacy_research_only_policy_v1.json`. Their registration grants only
read-row, read-schema, hash, and diagnostic capabilities. It does not grant fingerprint,
deduplication, import, write, training, signal, risk, or execution authority.

## Financial Lineage

Every source row receives a versioned `source_record_reference` built from:

1. the lineage schema version;
2. the immutable source file hash;
3. the canonicalized original source row;
4. a duplicate occurrence discriminator.

The discriminator is not a trade ID and is never exposed as an order, entry, exit, or
exchange identity. B02 never fabricates any ID.

Each required field contains:

- `value`;
- `source_type`;
- `source_reference`;
- `source_hash`;
- `confidence_class`;
- `verification_status`;
- `reason_code`.

An observed legacy value is not automatically authoritative. In particular, missing
`account_scope`, namespace, native trade/order IDs, gross PnL, trading fees, funding fees,
contract size, margin mode, or leverage are not filled from filenames, row positions,
net PnL, or zero defaults.

The accounting contract is:

```text
net_pnl = gross_pnl - trading_fee - funding_fee
```

All four components must be independently authoritative. A missing fee or funding value
never becomes zero.

## Candle Recovery

The offline recovery pipeline uses the project's registered public archives:

1. primary: canonical Binance USD-M Futures 1m archive;
2. secondary: independent Bitradex public futures 5m archive.

Every artifact is read-only, path checked, hashed, schema checked, UTC checked, and
validated for monotonicity, duplicate timestamps, OHLC consistency, non-negative volume,
and incomplete-candle markers. Coverage is point-in-time:

```text
candle_available_at = candle_timestamp + timeframe
candle_available_at <= decision_or_observation_time
```

No forward fill exists. Missing intervals remain explicit with `gap_start_utc`,
`gap_end_utc`, and `missing_interval_count`. A secondary archive is attempted only after
a structured primary failure. When comparable primary and secondary archives overlap,
an OHLC divergence above the configured tolerance blocks recovery.

The HTTP request contract is transport-injected and therefore fully offline in tests. It
enforces HTTPS host allowlisting, timeout, bounded retry, incremental backoff, minimum
request interval, an identifiable user agent, sanitized URLs, and a SHA-256 response
hash. The default B02 command does not contact the network.

## Dataset Boundaries

### HistoricalResearchDataset

Authority is limited to historical research, OCR evidence, simulation, and
counterfactual analysis. It cannot write paper outcomes, operational features, or active
signals.

### PaperOutcomeDataset

Authority is limited to closed, financially reconciled paper outcomes. An open trade or
an outcome without `reconciliation_status=VERIFIED` is rejected. It cannot import the
legacy Master automatically or promote a model.

### OperationalFeatureDataset

Authority is limited to validated public market data available at or before the decision
time. It rejects `target_*`, `future_ret_*`, labels, outcomes, PnL, fees, close times,
exit prices, and exit reasons.

Each dataset has a unique root, writer ID, reader ID, authority, schema version, schema
hash, primary key, deduplication contract, timezone contract, point-in-time contract, and
immutable dataset-manifest hash.

## Execution Manifest

`canonical_execution_manifest_v2` supports:

- dataset build;
- feature build;
- target build;
- split;
- backtest;
- training;
- quantitative evaluation.

The canonical payload contains commit, environment, dependency lock, dataset, feature,
target, split, cost, config, schema and source hashes, seed, sanitized command arguments,
row count, status, blockers, warnings, and safety flags.

`execution_id` and timestamps remain in a volatile envelope and do not affect the content
hash. Equal inputs, config, seed, and commit therefore produce the same SHA-256.

Materialization is content-addressed and append-only under `data/reports` and uses the B01
writer `integrity_traceability_v2.atomic_writer`. JSON uses UTF-8, sorted keys, a final
newline, and rejects NaN. Local runs declare `container.status=not_containerized`; no
container digest is invented. A dirty worktree or unresolved commit blocks release
eligibility without granting this research package promotion authority.

## Commands

No-write diagnostic:

```powershell
python .\scripts\build_canonical_data_foundation_v2.py `
  --project-root . `
  --json
```

Explicit research report:

```powershell
python .\scripts\build_canonical_data_foundation_v2.py `
  --project-root . `
  --write-report `
  --json
```

Only the explicit write mode may create JSON, Markdown, and content-addressed manifests
under `data/reports`. These are ignored runtime artifacts and must not be versioned.

## Safety Boundary

The package never:

- changes the Trader Master, sidecars, runtime datasets, models, registries, Qlib, IA
  Shadow, RiskManager, Freqtrade, signals, or configuration;
- calls private exchange or account endpoints;
- submits orders;
- treats historical conclusions as risk, signal, or execution authority;
- promotes a dataset or model.

The mandatory safety state remains:

```text
paper_only=true
shadow_only=true
research_only=true
live_trading_enabled=false
live_release_allowed=false
canary_release_allowed=false
order_submission_enabled=false
real_order_submission_enabled=false
exchange_private_access=false
sends_orders=false
changes_risk=false
changes_model=false
automatic_promotion_allowed=false
operational_authority=false
```
