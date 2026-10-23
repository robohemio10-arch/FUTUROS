# Freqtrade Image Pin/Digest Policy V1

## Machine-Readable Policy

The following compatibility block is consumed by the existing static auditor.
It retains the historical, conditional exception classification; it is not a
statement that the current versioned references are unpinned, nor proof of the
image or configuration used by a running process. Changing its markers requires
review of the auditor and its tests. This closeout does not change that contract.

```text
policy_status: temporary_exception
temporary_exception_allowed: true
follow_up_branch: codex/freqtrade-image-digest-resolution-v1
paper_only: true
shadow_only: true
live_trading_enabled: false
order_submission_enabled: false
real_order_submission_enabled: false
exchange_private_access: false
sends_orders: false
changes_risk: false
```

## Current Decision

At baseline `c089208126d76699981f1010d53724dbd07751fc`, the versioned
`docker-compose.paper.yml` reference is:

```text
freqtradeorg/freqtrade:stable@sha256:7031bca43ed7668ebf421725dd5016acade6ef88b0771db3e08c96e6d19a42db
```

The pin has been versioned since September 2026. H04 identified its installed
release as Freqtrade 2026.8. The `stable` tag does not make this digest-qualified
reference mutable. The static audit at this baseline reports four digest-pinned
references, zero unpinned references, and `status=ok`. This is a repository-policy
result, not runtime parity or effective-configuration certification.

The earlier unpinned `freqtradeorg/freqtrade:stable` state was a historical
temporary exception, not the current versioned state. For an unpinned reference
covered by that compatibility contract, the auditor returns `warning`, never
`ok`. Inventing or guessing a digest remains prohibited; no digest is resolved
or replaced by this documentation change.

## Previously Observed Runtime

H04 observed Freqtrade 2026.6 using the following image, distinct from the
versioned pin:

```text
freqtradeorg/freqtrade@sha256:87aa5c6d65359b34e9d99a0bb260a38c0efe0315253811e6f48c2afe8f278a6a
```

H04-R verified different index, Linux/AMD64 manifest and config descriptors;
eight of nine layer descriptors differ. Therefore
`IMAGE_MANIFEST_PARITY=DIFFERENT`. This does not itself prove functional
incompatibility. H04-D established that container creation preceded the pin,
but not why the old image remained in use. These are prior observations, not a
fresh inspection or authorization to migrate the operational container.

The consolidated evidence, limitations and migration conditions are recorded in
[the H04/H05 technical closeout](FREQTRADE_H04_H05_CONSOLIDATED_TECHNICAL_CLOSEOUT.md).

## Risk

A mutable `stable` tag can resolve to different image bytes over time. That weakens reproducibility, makes rollback evidence ambiguous, and allows an upstream tag change to alter the paper runtime without a source change in this repository.

`latest`, invalid digests, placeholder digests, and mutable Freqtrade images without this policy are blocked by the auditor.

## Temporary Exception Conditions

The retained exception applies only to an encountered unpinned reference under
the existing auditor contract, while all of these conditions hold. Its presence
does not authorize removing a current pin or accepting runtime drift:

- runtime remains paper/shadow only;
- live, canary, order submission, real order submission, and private exchange access remain disabled;
- the Compose command, volumes, environment, network, healthcheck, users, and entrypoint are not changed as part of this exception;
- the auditor continues to report `warning`, never `ok`, for the unpinned `stable` references;
- `codex/freqtrade-image-digest-resolution-v1` remains the historical follow-up identifier consumed by the auditor, not a new work item created by this closeout.

## Future Image Changes

The current pin is already resolved; do not repeat H04-R or change the running
image merely to close this investigation. A future image change needs separate
operator authorization, a concrete operational need, verified registry and
platform identity, engine-level compatibility evidence, preservation of financial
evidence, rollback preparation, and causal/runtime revalidation. Fixture tests
alone do not approve migration. F03 quarantine and F05 authority remain intact.

Only in a separately authorized future change, choose and verify the intended
release tag in an environment with trusted registry access:

```powershell
docker buildx imagetools inspect freqtradeorg/freqtrade:<validated-release-tag>
```

Record whether the digest identifies a multi-platform index or a platform
manifest and verify their relationship. Do not conflate either with an image
config ID. A real image reference must use the verified 64-hex SHA256 digest;
explanatory placeholders are not valid image values.

After updating, validate:

```powershell
docker compose -f docker-compose.paper.yml config
python scripts/audit_freqtrade_image_pin_digest_policy.py --project-root . --json
```

The auditor must return `ok`, with `digest_pinned_count` covering every Freqtrade reference and `unpinned_count=0`.

## Local Audit

The policy audit is static and needs neither Docker nor network access:

```powershell
python scripts/audit_freqtrade_image_pin_digest_policy.py --project-root . --json
```

It scans only versioned Compose files, Dockerfiles, workflows, and the Makefile. It does not import trading modules, access an exchange, send notifications, write runtime data, or execute Docker.

## Scope Boundary

This documentation changes no image, Compose file, trading logic, Freqtrade
strategy, RiskManager, Qlib, AI Shadow, OCR, dataset, active signal, readiness,
canary or live behavior. Effective configuration and financial source authority
remain unproven; no migration, financial audit or operational authorization is
granted by an `ok` static pin audit.
