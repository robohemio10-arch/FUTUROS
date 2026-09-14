# Qlib V3 prospective evidence orchestrator

Research/paper/shadow only. No runtime integration, scoring, training, activation,
promotion, scheduler or orders. The immutable activation is input, never output.
The CLI runs one observation cycle; it does not poll or restart services.

## V2 port inventory

| Previous component | Classification | V3 treatment |
| --- | --- | --- |
| Sealed Decision Ledger parser | A: reusable | Existing V4.2 parser verifies decision and trade-link hashes |
| Explicit signal/decision/trade identity matching | B: refactor | Require complete V3 identity and microsecond boundary |
| Atomic JSON/fsync | A: reusable | Existing restricted atomic writer, no V2 imports |
| V2 merge/idempotency pattern | B: refactor | V3/epoch/activation store, outer exclusive guard covers whole transaction |
| V2 freeze reconstruction/scoring/calibration | C: prohibited | Not imported or invoked |
| V2 ledger, accepted/rejected counts, historical observations | C: prohibited | Never read as evidence; counters start at zero |
| V2 boundary, model and policy pins | D: obsolete | Validated V3 activation/freeze replace these assumptions |
| Unattended service/retry wiring | D: out of this scope | Explicit one-cycle CLI only |

## Inputs and causal proof

Mandatory `--activation` and `--freeze` point to certified JSON files. Original
JSON values are canonically hashed before timestamps are interpreted. Pins include
epoch, freeze, activation, source commit/tree, model artifact/semantic and dataset.
The boundary is loaded and verified, not supplied by a timestamp-only CLI override.
ISO timestamps must be aware; UTC normalization preserves every microsecond.
Regional, naive and sub-microsecond inputs are rejected rather than truncated.

Optional `--signals` and `--outcomes` accept JSON arrays, JSON objects containing
`signals`/`outcomes` arrays, JSONL or read-only Parquet. Limits are 16 MiB compressed,
50,000 rows, and 64 MiB decoded Parquet; Parquet is consumed in 1,000-row batches
from a single in-memory byte snapshot. The reported SHA hashes those exact bytes.
Legacy rows without full V3 lineage are rejected, never converted to V3.
Paths are explicit: no latest search, fallback, inferred matching or V2 migration.
Each envelope requires `epoch_version=v3`, full `identity`,
`origin=natural_paper_runtime`, and explicit false `synthetic/replayed/backfilled`.
These are provenance assertions from the supplied local source, not signatures
proving that an arbitrary file was naturally generated. Operators must provide
authoritative runtime exports; the orchestrator never manufactures those exports.

Signals contain `signal_id`, `decision_event_id`, `signal_timestamp_utc` and a
sealed V4.2 `decision`. Decision model hash must match the certified V3 artifact.
Both decision and signal timestamps must be at/after the boundary and not future.
Outcome fields are forbidden in signal envelopes.

Outcomes require an eligible V3 parent signal, exact sealed `trade_link`,
`trade_id`, `open_time_utc`, `close_time_utc`, `is_closed=true`, finite `net_pnl`.
Candidate, correlation, signal, decision hash, symbol, pair, side and execution
time must agree exactly. A post-boundary close cannot legitimize an earlier signal.
This branch consumes explicit lineage only; it does not query operational SQLite
or reconstruct missing links from nearby timestamps. Existing producers lacking
V3 pins remain ineligible. No producer changes are made here.

## Persistence and failure handling

Default is no-write, including no lockfile creation. `--write-prospective-evidence`
allows only `data/research/qlib_v3/prospective_evidence/<epoch>/<activation>/evidence.json`
and its adjacent lock/temporary artifacts. No arbitrary output path is accepted.
The atomic snapshot is logically append-only: duplicate causal identities are
no-ops; changed content under the same identity blocks the transaction.
Signals deduplicate by signal ID with a one-to-one decision mapping; outcomes by
exact trade ID. Every retained row is revalidated. V2/corrupt stores block.

An exclusive `.guard` serializes read/merge/write across processes. A crashed
writer leaves a fail-closed guard requiring operator review, never automatic lock
stealing. Atomic replacement and fsync use the existing writer. Symlink/junction
components are rejected. The filesystem must be operator-controlled; this is not
a defense against a privileged concurrent actor replacing ancestor directories.

Missing natural inputs return waiting with explicit source status and zero
counters. Invalid activation/store returns blocked. Rejected source rows include
index and sanitized reason; no rejected row increments an eligible counter.
Reports are stdout-only; all source JSON/JSONL hashes are included for audit.
No outcome financial result is recalculated and no existing V2 worktree is changed.

## Usage

```powershell
python scripts/run_qlib_v3_prospective_evidence_orchestrator_v1.py --project-root . --activation <certified-activation.json> --freeze <freeze_v3.json> --signals <natural-v3-signals.json> --outcomes <natural-v3-outcomes.jsonl> --no-write --json
python -m pytest tests/test_qlib_v3_prospective_evidence_orchestrator_v1.py -q
```

No evidence is a valid infrastructure outcome: `AWAITING_NATURAL_V3_EVIDENCE`.
It grants neither model promotion nor operational authority.

The manifest census for review includes the explicitly named new source files,
without staging them. Standalone verification uses that complete manifest baseline;
a Git-index-only check before staging cannot see new untracked paths. Re-run the
ordinary Git-backed check when staging is separately authorized.
