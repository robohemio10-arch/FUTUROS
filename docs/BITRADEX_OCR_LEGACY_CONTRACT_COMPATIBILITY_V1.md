# Bitradex OCR Legacy Contract Compatibility V1

## Purpose

This package records the historical compatibility boundary for Bitradex OCR batch
`20260714_151816`. The batch predates Trader Master financial schema V2. The audit is
read-only and fail-closed: it validates historical source evidence, master hashes,
row counts, schema, and the Preview V4 gates without importing a trade.

An `ok` result means only that the historical append candidate is compatible with the
declared legacy contract. It does not authorize an import, a master write, a sidecar
rebuild, model work, risk changes, or order submission.

## Legacy contract versus financial V2

The historical OCR contract preserves the 25-column Trader Master layout and the PnL
reported by the source. It did not capture authoritative funding evidence. Therefore:

- `funding_fee` is `null`, meaning unknown, not zero;
- funding is never calculated as a residual;
- the reported PnL is retained as source evidence, not decomposed into V2 economics;
- the rows are not eligible for complete V2 financial decomposition;
- `synthetic_order_id` is a legacy deduplication alias and lineage evidence only;
- no synthetic identifier is promoted to native exchange or account identity;
- `account_scope_hash` is not required by this legacy contract and no V2 primary
  identity is asserted.

## Evidence and counts

The versioned contract pins:

- 506 OCR input rows;
- 2 confirmed exact duplicates excluded;
- 504 retained historical append candidates;
- 3,058 master rows before any future authorized append;
- 3,562 expected rows only after a separately authorized append;
- 3,057 automatically reconciled sidecar rows plus one residual-equivalence row;
- exact SHA-256 values for the XLSX and Parquet masters.

The auditor reads the Preview V4 summary and CSV without modifying them. It accepts
the canonical nested V4 evidence paths explicitly. Key matching ignores only casing
and punctuation formatting. Values are never supplied from financial defaults. The
older V4 completion marker is normalized to
`PREVIEW_V4_RECONCILED_IMPORT_NOT_CONFIRMED` only when the summary directly states
that the preview completed, is preview-only, and did not execute the official import.

The Parquet master is read exclusively through
`read_trader_master_readonly`, which uses a temporary copy and verifies the source
hash after reading. The XLSX master is also copied to a temporary directory and opened
with `read_only=True` and `data_only=False`; the original workbook is never saved.

## Commands

No-write is the default:

```powershell
python scripts/audit_bitradex_ocr_legacy_compatibility_v1.py `
  --project-root . `
  --no-write `
  --json
```

Optional reports are restricted to `data/reports`:

```powershell
python scripts/audit_bitradex_ocr_legacy_compatibility_v1.py `
  --project-root . `
  --write-report `
  --json
```

## Authority boundary

This branch performs no import. A future apply branch must receive explicit manual
authorization and independently implement backup, final preview, atomic write, and
post-import audit. It must also revalidate the pinned hashes and counts. Nothing in
this compatibility report grants operational, live, canary, model, risk, exchange,
or order authority.
