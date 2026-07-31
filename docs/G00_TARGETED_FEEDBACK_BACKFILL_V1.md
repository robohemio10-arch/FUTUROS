# G00 Targeted Feedback Backfill V1

## Objective

Materialize only the missing paper-feedback events associated with Freqtrade
trades `599` and `600`, while excluding the other pending events from the
global remediation plan.

The component reuses the existing transactional writer. It does not create a
second feedback store, writer implementation, microbatch pipeline, watermark
updater, trainer, model registry or runtime integration.

## Default behavior

The default execution is no-write and fail-closed:

```text
status=blocked
reason=explicit_targeted_backfill_authorization_required
decision=NO_TARGETED_BACKFILL_WITHOUT_EXPLICIT_AUTHORIZATION
write_performed=false
```

It recomputes:

- feedback-gap diagnostics;
- remediation plan;
- global dry-run;
- source fingerprint;
- target batch for trades `599` and `600`;
- schema and identity validation for both target events.

## Target isolation

The executor accepts only these identities:

```text
trade_close:599
order_close:freqtrade-paper-599
trade_close:600
order_close:freqtrade-paper-600
```

Events outside this set remain excluded even when present in the global
remediation plan.

## Authorization contract

Mutation requires all fields in one invocation:

```text
--execute-targeted-backfill
--expected-plan-hash <sha256>
--expected-dryrun-hash <sha256>
--expected-target-batch-hash <sha256>
--expected-source-fingerprint-hash <sha256>
--authorization-reference <reference>
--confirmation-text "EXECUTAR BACKFILL TARGETED G00 TRADES 599 E 600"
```

Any missing or mismatched value blocks before lock acquisition or write.

## Transactional guarantees

The implementation delegates mutation to
`paper_feedback_autotrain_e2e_closeout.controlled_backfill` and preserves:

- exclusive filesystem lock;
- source fingerprint validation before and immediately before write;
- byte-exact backup with SHA-256 verification;
- same-directory temporary file;
- atomic replacement;
- post-write identity and count audit;
- rollback on post-write validation failure;
- idempotency.

The post-write validator proves that:

- all pre-existing event identities remain present with the same counts;
- target identities are present exactly once;
- no unrelated identity was added;
- the final row count equals the pre-write count plus missing targets.

## Explicit exclusions

This component does not:

- close or reopen trades;
- call Telegram or NTFY;
- access an exchange;
- submit orders;
- create a microbatch;
- advance a watermark;
- run Qlib or IA Shadow training;
- promote a model;
- update Freqtrade, RiskManager or runtime configuration;
- authorize B06.

A successful targeted feedback write only restores the missing feedback
evidence. Microbatch creation, watermark advancement and G00 recertification
remain separate, explicit stages.

## CLI

No-write probe:

```powershell
python scripts/run_g00_targeted_feedback_backfill_v1.py `
  --project-root . `
  --allow-paper-db-read `
  --json
```

The mutation command must only be assembled after a separate explicit
authorization based on a fresh no-write packet.


## Deterministic authorization identity

The targeted executor recomputes diagnostics in memory. Authorization hashes
must not change only because a report was generated at a different clock time
or with different report-output metadata.

The diagnostics identity therefore excludes, recursively, only:

- `generated_at_utc`;
- `output_paths`;
- `write_report_requested`;
- `write_performed`;
- `safety_flags`.

Trade identities, timestamps, financial values, source authority, validation
results, writer inventory, warnings, conflicts and missing-record content
remain bound to `diagnostics_identity_hash`.

After implementation is materialized, two consecutive no-write probes must
produce identical values for:

- `diagnostics_identity_hash`;
- `plan_hash`;
- `dryrun_hash`;
- `source_fingerprint_hash`;
- `target_batch_hash`.

Any meaningful source or evidence change invalidates the packet and requires a
fresh no-write authorization packet.


## Sanitized authorization hash exposure

`diagnostics_identity_hash` is part of the operator-facing sanitized report.
It contains only a SHA-256 digest of deterministic evidence identity; it does
not expose raw diagnostics, credentials, private exchange data or feedback
payloads.

The field must remain present in no-write probes because it is one of the
authorization-bound values compared across consecutive deterministic runs.
