# Trader Master Legacy Lineage Profile V2

## Objective

This block profiles the 25-column legacy Trader Master against `trader_master_fingerprint_spec_v2` without adapting, migrating, fingerprinting, or importing legacy rows. It separates evidence present in the Parquet from assumptions that would fabricate native identity or financial decomposition.

The exclusive Master source is `data/trades/trades_master.parquet`. The file is validated, hashed, copied to a temporary directory, read from that copy, and hashed again. There is no XLSX fallback.

## Institutional boundary

The profile is research-only and has no operational authority. It never:

- writes the Trader Master, CSV, XLSX, Parquet, SQLite, or runtime artifacts;
- changes `fingerprint_spec_v2`;
- fills missing venue, account scope, namespace, contract size, fees, or funding;
- reconstructs gross PnL from net PnL;
- classifies a paper row as a confirmed duplicate or new trade;
- runs Freqtrade, Qlib, AI Shadow, Strategy Factory, training, or order submission.

Optional output is limited to JSON and Markdown under `data/reports` and requires `--write-report`.

## Field lineage contract

Every Fingerprint V2 field receives exactly one closed classification:

- `direct_authoritative_column`
- `direct_column_null`
- `direct_column_invalid`
- `deterministic_derivation_available`
- `versioned_source_contract_required`
- `external_authoritative_evidence_required`
- `mathematically_underdetermined`
- `conflicting_source_evidence`
- `unavailable`

Normalization uses the existing Fingerprint V2 functions. Values with embedded units that the active normalizer cannot parse are reported as invalid; units are not silently stripped. Missing costs are not replaced by zero.

Samples for native identifiers and account scope are irreversibly masked. Order ID format distribution is descriptive and never defines a namespace.

## Financial profile

The profiler evaluates the direct availability of entry, exit, quantity, contract size, gross PnL, trading fee, funding fee, net PnL, and source epsilon.

Gross PnL is only marked reconstructable when side, entry price, exit price, quantity, and authoritative contract size are all present and valid. Full verification additionally requires direct gross PnL, trading fee, funding fee, net PnL, and source epsilon. The identities are checked independently:

```text
long gross  = (exit_price - entry_price) * quantity * contract_size
short gross = (entry_price - exit_price) * quantity * contract_size
net_pnl     = gross_pnl - trading_fee - funding_fee
```

The profile never solves multiple unknowns from net PnL.

## Source cohorts

Cohorts use only exact source columns that exist in the Master, including explicit null cohorts. A filename can remain descriptive evidence but cannot define venue, market, account scope, or order ID namespace.

Each cohort reports row count, null profile, schema coverage, order ID format distribution, time range, symbol/side distributions, financial coverage, fields that may need a versioned source contract, and fields that still need external authoritative evidence.

## Legacy observation key

`legacy_observation_key_v1` is a separate diagnostic contract over these eight directly comparable fields:

```text
symbol, side, open_time, close_time,
entry_price, exit_price, quantity, net_pnl
```

The key is generated only when all fields normalize successfully. Hash matches are confirmed by canonical payload comparison, so a digest collision cannot become a silent overlap.

Interpretation is deliberately conservative:

- unique overlap is not a confirmed duplicate;
- multiple overlap is ambiguous;
- no overlap is not proof of a new trade;
- every paper row remains `import_eligible=false`.

## Decisions

- `LEGACY_MASTER_ALREADY_V2_VERIFIABLE`
- `VERSIONED_SOURCE_CONTRACT_DESIGN_FEASIBLE`
- `EXTERNAL_AUTHORITATIVE_EVIDENCE_REQUIRED`
- `LEGACY_FINANCIAL_DECOMPOSITION_UNDERDETERMINED`
- `LEGACY_MASTER_IRREDUCIBLY_UNVERIFIABLE`

No decision authorizes a bridge, migration, or import.

## Usage

No-write is the default:

```powershell
python scripts/profile_trader_master_legacy_lineage_v2.py `
  --project-root . `
  --trader-master data/trades/trades_master.parquet `
  --source-profile config/freqtrade_paper_closed_trades_source_profile_v2.json `
  --account-scope-hash "<SHA256-SANITIZADO-JA-VALIDADO>" `
  --authoritative-sqlite data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite `
  --no-write `
  --json
```

Optional reports:

```powershell
python scripts/profile_trader_master_legacy_lineage_v2.py `
  --project-root . `
  --account-scope-hash "<SHA256-SANITIZADO-JA-VALIDADO>" `
  --write-report `
  --json
```

Only these runtime outputs are permitted:

- `data/reports/trader_master_legacy_lineage_profile_v2.json`
- `data/reports/trader_master_legacy_lineage_profile_v2.md`

They remain ignored and must not be committed.
