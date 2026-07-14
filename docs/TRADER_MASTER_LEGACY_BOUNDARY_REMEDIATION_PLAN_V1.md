# Trader Master Legacy Boundary Remediation Plan V1

## Objective

This component converts the structured findings from the Trader Master Legacy
Research-Only Boundary V1 into a deterministic remediation plan for human
review. It never applies the plan. The current boundary remains fail-closed and
the legacy Master remains `research_only_legacy_non_v2`.

The planner invokes the institutional 1B.3 auditor in no-write mode and consumes
only its structured report. It does not duplicate Git discovery, AST scanning,
Parquet reading, artifact hashing, schema checks, writer detection, or
operational namespace detection.

## Closed Actions

Each relevant finding receives exactly one action:

- `REMOVE_LEGACY_WRITER_CALLSITE` for active writer/import callsites;
- `ISOLATE_OPERATIONAL_REFERENCE` for operational namespaces and configuration;
- `RESOLVE_DYNAMIC_REFERENCE` when the source auditor cannot resolve a
  non-operational construction;
- `REFACTOR_TO_INSTITUTIONAL_READONLY_ADAPTER` for legitimate historical reads
  that do not yet use `read_trader_master_readonly`;
- `REGISTER_AS_READONLY_RESEARCH_CONSUMER` only when the finding itself proves
  use of the institutional adapter;
- `KEEP_QUARANTINED_LEGACY_IMPLEMENTATION` only for the inventoried legacy
  writer implementation, with no execution authority;
- `FALSE_POSITIVE_WITH_STRUCTURAL_PROOF` only with reproducible structural
  proof;
- `BLOCKED_REQUIRES_MANUAL_REVIEW` when structured evidence is insufficient.

A filename, namespace, or desired outcome is not evidence. Writer/import and
operational precedence cannot be downgraded to registration or a false positive.

## Determinism And Accounting

Planner finding IDs are SHA-256 hashes over canonical classification, relative
path, line, symbol, and a sanitized evidence token. Timestamps are excluded.
Exact duplicates are deduplicated; a repeated source ID with divergent payload
makes the plan incomplete.

Findings, actions, branch packages, dependencies, paths, tests, and blockers are
sorted. Accounting requires:

```text
relevant_finding_count = classified_finding_count + unclassified_finding_count
multiply_classified_finding_count = 0
```

No `max()` or fallback can hide inconsistent accounting.

## Future Branch Sequence

The plan may propose, but never creates, these branches:

1. `trader-master-boundary-writer-callsite-removal-v1`
2. `trader-master-boundary-operational-reference-isolation-v1`
3. `trader-master-boundary-dynamic-reference-resolution-v1`
4. `trader-master-boundary-readonly-adapter-migration-v1`
5. `trader-master-boundary-readonly-consumer-registration-v1`
6. `trader-master-boundary-reverification-closeout-v1`

Prerequisite packages are included when a later action is needed. Registration
cannot precede writer removal, operational isolation, dynamic resolution, and
adapter migration. Closeout depends on every applicable predecessor.

Branch 1 cannot change policy. Branch 2 cannot register consumers. Branch 3
cannot suppress unresolved findings. Branch 4 must use the institutional reader.
Branch 5 may propose a new policy version only after prior gates are green.
Branch 6 may propose final evidence/governance updates and must rerun the 1B.3
auditor.

The closeout gate requires all of the following; this document does not claim
they have already been achieved:

```text
high_count=0
critical_count=0
dynamic_reference_unresolved_count=0
consumer_inventory_complete=true
decision=LEGACY_MASTER_SEGREGATED_RESEARCH_ONLY
segregation_enforced=true
```

## CLI

No-write is the default:

```powershell
python scripts/plan_trader_master_legacy_boundary_remediation_v1.py `
  --project-root . `
  --policy config/trader_master_legacy_research_only_policy_v1.json `
  --taxonomy config/trader_master_legacy_boundary_remediation_taxonomy_v1.json `
  --trader-master data/trades/trades_master.parquet `
  --no-write `
  --json
```

Optional report materialization is restricted to `data/reports`:

```powershell
python scripts/plan_trader_master_legacy_boundary_remediation_v1.py `
  --project-root . `
  --write-report `
  --json
```

There is no apply, fix, force, branch creation, consumer registration, writer
removal, policy update, operational override, or training flag.

## Decision Semantics

- `LEGACY_BOUNDARY_REMEDIATION_PLAN_READY`: all relevant findings have exactly
  one action and no manual review remains.
- `LEGACY_BOUNDARY_REMEDIATION_PLAN_REQUIRES_MANUAL_REVIEW`: accounting is
  complete but at least one item lacks sufficient structured evidence.
- `LEGACY_BOUNDARY_REMEDIATION_PLAN_INCOMPLETE`: classification, duplicate ID,
  branch mapping, dependency, or accounting is structurally incomplete.
- `LEGACY_BOUNDARY_REMEDIATION_NOT_REQUIRED`: the source auditor already proves
  complete segregation with zero high, critical, and dynamic findings.
- `LEGACY_BOUNDARY_SOURCE_AUDIT_BLOCKED`: the source audit or required contract
  cannot conclude safely.

An `ok` planning status is not implementation authority. Every package retains
`implementation_authorized=false` and `branch_created=false`.

## Safety Boundary

The plan does not modify the 1B.3 policy, consumers, `trades_importer.py`,
Fingerprint V2, `data/trades`, Freqtrade, RiskManager, runtime, learning, Qlib,
IA Shadow, dashboard, models, signals, or orders. It does not generate a bridge,
Fingerprint V2, imports, training, promotion, risk decisions, paper/live
selection, or execution. Its optional JSON and Markdown are review evidence,
not operational authorization.
