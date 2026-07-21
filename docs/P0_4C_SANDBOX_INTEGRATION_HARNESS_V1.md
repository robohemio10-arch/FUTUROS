# P0.4C — Sandbox Integration Harness V1

## Scope

P0.4C implements and tests the integration seam immediately after RiskManager,
but does not connect it to tracked runtime sources. The harness is installed as
untracked files in the detached local sandbox.

## Implemented

- strict source adapter from RiskManager-stamped signals;
- complete P0.4B decision projection;
- immutable correlation envelope for approved active signals;
- fail-closed exclusion of approved signals with incomplete lineage;
- projection of rejected decisions without active publication;
- deterministic decision-to-trade link preview;
- injected sink interface with disabled default;
- in-memory idempotent test sink;
- sandbox file sink using P0.3B writer only under disposable roots;
- lock, append, fsync, permission, idempotency and health tests;
- static migration guard for the legacy strategy writer.

## Explicitly blocked

- modification of tracked signal producer or strategy files;
- data/runtime writes;
- SQLite writes;
- container start or paper restart;
- branch, commit, push or pull request;
- private exchange access or order submission;
- canonical writer activation or legacy writer retirement.
