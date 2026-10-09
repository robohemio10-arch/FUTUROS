# Execution Decision L1 Deployment Preflight V1

Independent advisory auditor, not an extension of the collector CLI. Default:
no public network, collector initialization, collection, persistence or runtime mutation.
The existing runtime preflight and KillSwitchGuard.evaluate are NOT called: they can
write event logs. This audit reuses only their pure privilege/state contracts, sealed
Decision Ledger parser, stable Evidence Readiness reads, public transport and path policy.

## Institutional Checklist

Code evidence: exact contract/schema hashes, current checkout source hashes, explicit
collect/write opt-in, fixed public route, bounded transport, queues/history/identity budget,
loss accounting, Ctrl+C and bounded shutdown. Static proof is not successful deployment.

Host evidence: effective non-elevated identity, safe auditor authority flags, canonical
Paper ledger at runtime-root/data/runtime/decision_ledger_paper_v1/decision_ledger_v4_2.jsonl,
complete stable bytes and seals, exact IDs and idempotency, real external archive path and
free space, canonical Paper kill-switch state. Environment findings refer ONLY to the
auditor, never inferred container settings. No secrets/environment dump/raw exceptions.

Ledger read is bounded by the existing 128 MiB stable-read policy plus 50,000 records and
64 KiB per line. A concurrent append/partial line blocks this audit; no retry/backfill,
copy or snapshot write occurs. Historical records are validated only for deployment
integrity, not replayed as new L1 coverage. No Paper trade-link becomes an observed fill.
Exact duplicates are counted; divergent identities fail. Timestamp regressions are
reported, not automatically interpreted as a host clock defect. Last decision age is
observed without inventing a required opportunity cadence.

Archive must be absolute, outside Git/project/runtime, without symlink/reparse ancestors.
Free space must cover twice the configured segment budget plus 16 MiB metadata headroom.
os.access is only a hint, not Windows effective ACL/write proof. No directory/file is
created. Create/append/fsync/atomic-replace/delete permissions remain UNPROVEN read-only.

Optional public diagnostic: exactly one GET, using the existing whole-request timeout,
strict public payload/freshness validation, no retries, no collector, no market payload
in the report or archive. Success does not prove future network reliability or clocks.
The Windows time-service query is local/read-only and timeout-bounded. Unsynchronized
leap indicator 3 is a failure; exit=0 alone never proves host/container/exchange alignment.
Raw query output is hashed, not printed. Unknown/localized formats remain UNPROVEN.

Running Paper process/mount binding, inter-domain clock error bound, effective CPU/RSS
limits and real shutdown trial are UNPROVEN here. Bounded queues do not certify RSS/CPU
isolation. No Docker query, scheduler, process launch, priority or configuration change.

## Gates

FAILED mandatory check => BLOCKED_PREFLIGHT_FAILURE.
Missing/UNPROVEN host check (including fixture-only validation) => BLOCKED_HOST_UNVERIFIED.
PREFLIGHT_READY_FOR_MANUAL_OPT_IN requires the COMPLETE unique checklist with all mandatory
checks PROVEN on the host. No --assume-safe, --force, fake attestation or local-test override.
No ready gate is fabricated from an operator-supplied boolean or a successful command.

Collector V1 supports Ctrl+C/bounded shutdown, but does NOT consume the canonical external
kill-switch. This is a proven activation blocker, not silently reclassified as verified.
An enabled canonical Paper kill-switch also blocks authorization; the auditor never clears
it. Collector changes/host authorization are separate future work, not made in this branch.
This read-only audit cannot complete the required write-contract/activation trials and
therefore must not approve deployment today, even if static tests and public GET pass.

EXECUTION_READINESS always stays BLOCKED_MISSING_EXECUTION_EVIDENCE: observed fills,
actual fees and decision/submit/ack/fill clocks remain absent. No trading/Paper Treatment,
Freqtrade/RiskManager, PnL, operational authority or economic claims change.

## Manual Audit

```powershell
python -B scripts/audit_execution_decision_l1_deployment_preflight_v1.py --runtime-root <absolute-Paper-root> --archive-root <absolute-external-root> --json
```

Explicit diagnostic only (not required or run by default):

```powershell
python -B scripts/audit_execution_decision_l1_deployment_preflight_v1.py --runtime-root <absolute-Paper-root> --archive-root <absolute-external-root> --diagnose-public-connectivity --json
```

JSON is returned to stdout, with a canonical report seal and deterministic sorted blocker
codes. No --write or --collect option exists. Exit 0 requires manual-opt-in readiness;
exit 2 is blocked. Do not redirect the report into Git/runtime. This audit is not a
deployment, restart, repair, clearance of any existing safety guard or collection request.
