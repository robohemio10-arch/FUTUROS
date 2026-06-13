# Freqtrade Image Pin/Digest Policy V1

## Machine-Readable Policy

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

The versioned Compose files currently reference `freqtradeorg/freqtrade:stable` without an immutable digest. This is a temporary, explicit supply-chain exception, not a compliant final pin.

The branch does not replace the image because no registry-verified digest is available in the repository and inventing or guessing a digest would create false security. The static auditor therefore returns `warning` while this documented exception exists.

## Risk

A mutable `stable` tag can resolve to different image bytes over time. That weakens reproducibility, makes rollback evidence ambiguous, and allows an upstream tag change to alter the paper runtime without a source change in this repository.

`latest`, invalid digests, placeholder digests, and mutable Freqtrade images without this policy are blocked by the auditor.

## Temporary Exception Conditions

The exception is accepted only while all of these conditions hold:

- runtime remains paper/shadow only;
- live, canary, order submission, real order submission, and private exchange access remain disabled;
- the Compose command, volumes, environment, network, healthcheck, users, and entrypoint are not changed as part of this exception;
- the auditor continues to report `warning`, never `ok`, for the unpinned `stable` references;
- digest resolution is handled by `codex/freqtrade-image-digest-resolution-v1` in a connected, controlled environment.

## Resolving the Digest

In an environment with trusted Docker registry access, first choose and verify the intended Freqtrade release tag. Inspect its registry metadata without copying a digest from an untrusted source:

```powershell
docker buildx imagetools inspect freqtradeorg/freqtrade:<validated-release-tag>
```

Record the platform-appropriate registry digest returned by Docker. Update each image reference by combining the validated release tag with the exact `sha256` value printed by that command. A real change must contain exactly 64 hexadecimal digest characters obtained from the registry inspection; explanatory placeholders are not valid image values.

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

This policy does not alter trading logic, the Freqtrade strategy, RiskManager, Qlib, AI Shadow, OCR, datasets, active signals, readiness, canary, or live behavior. It does not change the current Compose image reference until a real digest can be verified safely.
