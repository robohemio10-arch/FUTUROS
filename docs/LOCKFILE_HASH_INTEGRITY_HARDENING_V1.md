# Lockfile Hash Integrity Hardening V1

## Machine-Readable Policy

```text
policy_status: temporary_exception
temporary_exception_allowed: true
follow_up_branch: codex/lockfile-full-hash-resolution-v1
paper_only: true
shadow_only: true
live_trading_enabled: false
order_submission_enabled: false
real_order_submission_enabled: false
exchange_private_access: false
sends_orders: false
changes_risk: false
```

## Current Sources Of Truth

- `requirements-runtime.lock` is the transitive runtime lock used by SmartCrypto and dashboard images.
- `requirements-dev.lock` is the transitive development and CI lock.
- `constraints.txt` pins additional shared constraints.
- `docker/qlib/requirements.txt` contains the Qlib worker's direct pins and is installed with both runtime lock and constraints.
- `bitradex_realtime_candle_collector_v1/requirements.txt` is an auxiliary collector requirements file with bounded ranges, not a transitive lock. Its Dockerfile installs it directly.
- `pyproject.toml` defines package metadata and compatible ranges; it is not the Docker or CI resolution source.

The requirements locks and constraints currently pin versions with `==`, but they do not yet include complete artifact hashes. The SmartCrypto Dockerfiles also upgrade the packaging toolchain before installing the runtime lock. The auxiliary Bitradex collector currently has bounded, unpinned requirements and upgrades pip before installing them without a lock. These are explicit temporary integrity exceptions and remain visible as `warning` findings.

## Version Pin Versus Hermetic Hash

A version pin selects a package version. It does not prove which wheel or source archive was downloaded. A `sha256` hash binds installation to exact artifacts and protects against artifact replacement or an unexpected platform distribution.

Pip's `--require-hashes` mode strengthens this further by requiring hashes for every resolved requirement. It is valuable only when the complete transitive set and all supported platform artifacts have been generated and reviewed together.

## Why This Branch Does Not Enable Require-Hashes

Enabling `--require-hashes` against the current unhashed locks would immediately break local installation, CI, and Docker builds. Adding guessed hashes or hashing only part of the graph would create false assurance. This branch therefore audits and documents the gap without changing package managers, install commands, CI behavior, or runtime images.

Complete hash generation belongs to `codex/lockfile-full-hash-resolution-v1` in a controlled resolver environment with the project's supported Python and platform matrix.

## Policy

- Invalid hashes, obvious fake hashes, and unhashed remote URL dependencies are blocked.
- Unpinned entries in a lockfile are blocked.
- Fully pinned requirements without hashes are temporarily MEDIUM while this policy and follow-up remain present.
- Runtime Docker installation without lock/constraints is blocked unless covered by the temporary policy.
- Unconstrained packaging-tool upgrades in Docker remain MEDIUM under this temporary policy.
- The Bitradex collector's bounded requirements and direct Docker installation remain MEDIUM; the follow-up must resolve a collector-specific transitive lock and hashes rather than silently treating those ranges as locked.
- No auditor or unit test may run pip, Docker, a registry, or a dependency resolver.

## Controlled Hash Resolution

In the follow-up branch, use a trusted, isolated resolver environment to generate hashes for the complete dependency graph. Review package names, versions, supported platforms, wheel/source choices, and transitive dependencies before committing the generated lock.

Only resolver-produced 64-character hexadecimal `sha256` values may be accepted. Do not type, infer, or copy hashes from untrusted output. After complete coverage is available, update installation commands to use `--require-hashes` and validate CI and all Docker builds together.

## Static Validation

The audit is offline and read-only:

```powershell
python scripts/audit_lockfile_hash_integrity.py --project-root . --json
```

It inventories dependency files, checks pins and hash syntax, detects obvious fake hashes, and inspects Dockerfiles for floating installation behavior. It does not install packages, access the network, invoke Docker, import trading modules, or write runtime artifacts.

The expected current repository status is `warning`, not `ok`, until complete hermetic hash coverage is implemented.

## Safety Boundary

This policy does not alter trading, RiskManager, Freqtrade strategy, Qlib, AI Shadow, OCR, datasets, active signals, readiness, canary, live behavior, Compose runtime, or Freqtrade image policy. Paper/shadow-only controls remain unchanged.
