# Trader Master Legacy Research-Only Boundary V1

## Objective

This boundary classifies `data/trades/trades_master.parquet` as
`research_only_legacy_non_v2`. The classification is logical and versioned: the
physical Parquet remains unchanged at its current path, while the policy denies
identity, deduplication, financial decomposition, import, training, signal,
risk, and execution authority.

The boundary does not assert that recovery is permanently impossible. The
current evidence inventory covered only safely inspected artifacts, so absence
of authoritative evidence has not been proven across the complete candidate
inventory.

## Evidence Basis

The policy records the following closed findings:

- reconciliation: `BLOCKED_BY_UNVERIFIABLE_MASTER_ROWS`;
- 3,058 legacy rows and zero valid Fingerprint V2 rows;
- lineage: `EXTERNAL_AUTHORITATIVE_EVIDENCE_REQUIRED`;
- evidence inventory: `NO_AUTHORITATIVE_EVIDENCE_FOUND`;
- evidence scope: `safely_inspected_artifacts_only`;
- inventory coverage incomplete and blocked artifacts still unassessed;
- bridge design preconditions not satisfied.

These statements do not mean `irrecoverable`, `permanently_unverifiable`, or
that a bridge is impossible forever.

## Pinned Artifact

The policy pins the currently approved evidence:

| Field | Value |
| --- | --- |
| Path | `data/trades/trades_master.parquet` |
| SHA-256 | `24e049b3ca7a72dbde071a056548035fed87651d48959cd0cf4c6c8b0dac7295` |
| Size | 491,355 bytes |
| Rows | 3,058 |
| Columns | 25, in the exact order declared by the policy |

The auditor validates the real artifact through the existing temporary-copy
reader. It rejects paths outside the project, symlinks, non-Parquet files,
hash/size changes during the read, and any hash, size, row-count, or schema
drift. Drift does not update the policy automatically.

## Allowed Access

Only explicitly registered consumers may request `read_only` access for one of
these purposes:

- `historical_readonly_research`;
- `lineage_diagnostics`;
- `evidence_inventory`;
- `non_operational_strategy_research`;
- `read_only_data_quality_analysis`.

Every allowed evaluation still returns no operational authority, no import
eligibility, no Fingerprint V2 generation, and no permission to write the
Master. Registration is exact by repository-relative consumer path; there is
no wildcard authorization. The governance domain is itself explicitly
registered because it invokes the institutional read-only adapter; this is not
an implicit self-authorization.

`smartcrypto/data/trades_importer.py` is recorded separately as a quarantined
legacy writer implementation. Inventorying it neither imports nor executes the
module and does not make it an approved consumer.

## Static Audit

The auditor discovers versioned files with `git ls-files -z` using a fixed
argument list, `shell=False`, and a bounded timeout. Python files are inspected
with AST. A lexical fallback reports unresolved dynamic constructions but does
not treat comments or docstrings as executable consumers. Versioned YAML, JSON,
and TOML are inspected for configuration references.

The scanner identifies direct Master literals and `Path` calls, reader and
writer calls, `trades_importer` aliases, incremental import calls, direct
Parquet reads/writes, filesystem mutations, Fingerprint V2 misuse, dynamic
references, and operational configuration references. It never imports or
executes scanned modules.

High or critical findings produce `LEGACY_MASTER_BOUNDARY_VIOLATED`; unresolved
dynamic references produce `LEGACY_MASTER_CONSUMER_INVENTORY_INCOMPLETE` when
no stronger violation exists. An audit can finish with `status=ok` while its
institutional decision remains blocking.

## CLI

No-write is the default:

```powershell
python scripts/audit_trader_master_legacy_research_only_boundary_v1.py `
  --project-root . `
  --policy config/trader_master_legacy_research_only_policy_v1.json `
  --trader-master data/trades/trades_master.parquet `
  --no-write `
  --json
```

Optional reports require an explicit flag and remain under `data/reports`:

```powershell
python scripts/audit_trader_master_legacy_research_only_boundary_v1.py `
  --project-root . `
  --write-report `
  --json
```

There is no apply, force, bridge, import, policy-update, consumer-registration,
training, or operational override flag.

## Reassessment

Reassessment is permitted only after one of the six triggers in the policy,
including new authoritative joinable evidence or recovery of authoritative
account, instrument, or financial-decomposition evidence. It requires a new
policy version, a new audit, explicit authorization, reviewed hashes, and the
complete test matrix. A trigger never changes this policy automatically.

## Safety Boundary

This component does not write the Trader Master or any CSV, XLSX, Parquet,
SQLite, runtime, model, signal, or registry artifact. It does not change
Fingerprint V2, Freqtrade, RiskManager, Qlib, IA Shadow, feedback, autotrain,
Strategy Factory, paper selection, live selection, risk decisions, or order
execution. The optional JSON and Markdown outputs are audit evidence only.
