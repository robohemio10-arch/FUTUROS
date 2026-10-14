# Kill-Switch Schema Interoperability Fail-Closed V1

## Authority and Scope

The canonical authority remains `data/runtime/kill_switch.json`. No runtime
state is migrated, repaired, refreshed, or cleared by a safety read. No network,
collector activation, orders, or operational rollout is part of this change.

`smartcrypto.risk.kill_switch_guard.read_kill_switch_snapshot` owns bounded,
consistent file reads and schema interpretation. RiskManager's file-based
`load_kill_switch` / `evaluate_risk`, KillSwitchGuard, and L1 reuse it. L1 retains
its explicit runtime/ledger binding, bounded monitor, revocation latch, and
fresh authorization checks. The configuration-only `RiskLimits` signal gate
does not gain a new implicit filesystem dependency.

The shared operational `apply_risk_manager_gate` also requires the canonical
authority. Phase13 passes `paths.kill_switch` explicitly, defaulting to
`DEFAULT_KILL_SWITCH_PATH`; the other existing writers use that same default.
YAML limits and an injected RiskManager cannot override a canonical halt or
authorize from missing, legacy CLEAR, invalid, or unreadable state. Reads occur
before risk evaluation and again before returning approved signals, so a halt
or read failure during evaluation discards prior approvals. Symbol and pair
identities must agree; spot/futures pair aliases cannot bypass a symbol halt.
The pure RiskManager API remains filesystem-independent and is not by itself
an operational authorization. No persisted state is created by this gate.
The Compose Paper bot's `run_once` also uses this shared gate instead of calling
`approve_many` directly, and a blocked gate exits before creating its exporter
or publishing a signals file. OrderManager retains its separate canonical
execution guard. No deployment or running-container mutation is performed.

The operator CLI still calls `risk_manager.set_kill_switch`. Guard mutation
methods remain explicit operator APIs. OrderManager and DashboardReadonlyCommandBus
use the same canonical default path instead of `kill_switch_guard.json`.
Dashboard classification and Paper session snapshots remain advisory, not
authorization sources.

## Compatibility Matrix

| Persisted evidence | RiskManager / Guard inspection | L1 |
| --- | --- | --- |
| Legacy `enabled=true`, explicit safe runtime mode | GLOBAL_BLOCKED | GLOBAL_BLOCKED; collection denied |
| Legacy `enabled=false` | CORRUPTED / blocked, legacy clear is unproven | Denied; no metadata is synthesized |
| Complete structured V1, global halt | GLOBAL_BLOCKED | GLOBAL_BLOCKED |
| Complete structured V1, requested-symbol halt | SYMBOL_BLOCKED | SYMBOL_BLOCKED |
| Complete structured V1, explicit valid CLEAR | CLEAR for the requested scope | Existing authorization checks still apply |
| Both flat `enabled` and structured authority | CORRUPTED / blocked, even if equal | Denied |
| Missing, partial, corrupt, unsupported, inconsistent state | CORRUPTED / blocked | Denied |
| Invalid identity, UTC clock, actor, reason, or default-clear metadata | CORRUPTED / blocked | Denied |

An unscoped RiskManager read blocks if any symbol is blocked. A scoped read
respects that symbol; global halt always wins. Legacy active metadata may be
unavailable: it is reported as unavailable, never fabricated. This is evidence
to deny, not evidence to authorize. A minimal real-equivalent legacy fixture
therefore proves `legacy_enabled=true -> GLOBAL_BLOCKED` without a rewrite.

## Read and Write Contracts

Safety reads reject duplicate JSON keys, non-standard constants, oversized
state, symlinks/reparse points, nonregular/network paths, and changes between
path stat, open descriptor stat, read, and final stat. Read elapsed time is
bounded by the existing L1 monitor/deadline contract; slow reads cannot grant
authorization. Parent and entry UTC timestamps must be valid, consistent, not
future, and ordered chronologically.

The shared reader checks its 750 ms deadline both after I/O and after parsing,
normalization, hashing, and snapshot construction. An elapsed time equal to
the deadline is rejected; this is a fail-closed return contract, not a promise
to interrupt an OS read in progress. A valid snapshot is not permanent
authorization: downstream execution still enforces its own canonical guard.

`KillSwitchGuard.inspect()` is read-only and emits no audit log. Existing
`evaluate()` still records blocked/corrupt results in its configured event log.
`load_state()` never initializes default-clear on a safety read.
RiskManager's returned `enabled` is a derived compatibility projection, not a
second persisted authority and not input to an operator write.

Explicit operators preserve existing legacy or structured format. Structured
global writes preserve symbol blocks and never add flat `enabled`. Legacy
global writes remain flat; clearing legacy does not authorize L1. Symbol writes
against legacy require an explicit migration and are rejected here. Invalid or
mixed existing state is not automatically repaired by an operator API.

For a missing file, `set_kill_switch` preserves legacy initialization
compatibility. Guard initialization is explicit and globally blocked; only an
explicit global clear can change that. Writes use unique same-directory temp
files, flush/fsync, and atomic replace, with cleanup on failure. Operator
signatures remain compatible; returned dictionaries describe the persisted
format. A future schema migration must be a separate explicit, tested action.

## Preflight and Shutdown

Preflight retains the snapshot of a recognized active legacy halt, reports
`authority_global_blocked`, and stays `BLOCKED_PREFLIGHT_FAILURE`. It cannot
declare manual activation ready from that evidence. Public diagnostics still
require the ordinary enforcing authority check, not this reporting path.

The collector report is unchanged: proven incomplete public-child/worker
shutdown remains the primary `shutdown_incomplete` reason; the original
authority cause stays in `canonical_kill_switch`. No incomplete shutdown is
reported as PASS. Execution readiness remains blocked for missing execution
evidence.

## Validation

The dedicated cross-consumer matrix uses temporary fixtures only, asserts
byte-for-byte absence of safety-read mutations, and covers operator atomic
failure, legacy format preservation, symbol blocks, invalid metadata, read
races, permission denial, deadlines, symlink/reparse rejection, and no public
request from blocked startup. Direct risk, guard, preflight, execution, L1
revocation, and incomplete-shutdown regressions must pass before publication.
Phase13 regression fixtures bind their working directory to `tmp_path` and
assert that the checkout's lineage probe is byte-for-byte unchanged. They do
not suppress the producer or change V3 logic.

Positive signal-wiring and CLI audit probes run with explicit structured CLEAR
fixtures in isolated temporary working directories. That evidence certifies
fixture behavior only, not authorization or readiness of the deployed Paper
host. Negative probes cover active legacy/structured halts, absent or invalid
authority, scoped blocks, I/O denial, revocation, and post-parse deadline expiry.

Dashboard Command Bus and Reconciliation Guard regressions explicitly create
structured V1 CLEAR authority fixtures with UTC timestamps, reason, and actor
inside `tmp_path`, then inject those paths. The allowlist, blocklist, unsafe
runtime flags, kill-switch evaluation, and rejection events remain enforced.
Read-only commands are not exempt from a missing, blocked, or invalid authority.

Authorized paper intents exercise the real OrderManager/StateRepository update
and capital reservation path. Tests verify the persisted intent and reservation,
including directory creation, before injecting reconciliation divergences.
Reconciliation does not fabricate intents or repair financial state. Missing
authority rejects an intent before financial state creation. No alternative
loader, workflow change, or automatic CLEAR is needed to make these tests pass.
