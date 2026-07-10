# Credential Rotation Attestation Gate V1

## Purpose

This security-only gate validates a sanitized operational declaration that every credential category affected by an incident has been revoked, rotated, or formally classified as not applicable. It establishes inventory completeness, dual control, timestamp ordering, freshness, and absence of secret material.

The gate does not rotate or revoke credentials. It does not access provider consoles, admin APIs, secret managers, environment variables, Docker, runtime services, or the internet. `ROTATION_ATTESTATION_COMPLETE` proves only that a sanitized declaration is internally consistent under this contract; it does not technically prove provider state.

## Inputs

Both JSON files must be supplied explicitly:

```powershell
python scripts\validate_credential_rotation_attestation_v1.py `
  --project-root . `
  --required-inventory path\required-inventory.json `
  --attestation path\attestation.json `
  --json
```

Before JSON parsing, each file is checked for `.json` extension, size limit, symlinks and unsafe relative traversal. The gate then calls the existing `smartcrypto.security.evidence_bundle_redaction.scan_source` contract. Any secret finding blocks parsing and no finding details or source lines are copied into the report.

The inventory schema is `credential_rotation_required_inventory_v1`. It identifies stable non-secret credential IDs, categories, providers, sanitized scopes, and the required `revoke_or_rotate` action. Duplicate IDs or categories fail closed.

The attestation schema is `credential_rotation_attestation_v1`. Allowed statuses are `revoked`, `rotated`, `not_applicable`, and `unverified`. Allowed verification methods are limited to the manual methods defined by the contract; the names document what an operator claims to have used and never trigger a provider call.

## Dual Control

`revoked` and `rotated` items require distinct operator and reviewer roles, completion and verification timestamps in UTC, verification at or after completion, an allowed manual verification method, and a sanitized evidence reference.

`not_applicable` requires a reviewer, `documented_not_applicable`, a sanitized reference, and a non-empty sanitized justification. `unverified` always blocks closure.

The default maximum attestation age is 30 days. Invalid, non-UTC, future, stale, or incorrectly ordered timestamps fail closed.

## Forbidden Material

Inputs must not include credential values, token hints, hashes, fingerprints, prefixes, suffixes, usernames, passwords, Authorization headers, authenticated URLs, or reusable secret material. Credential IDs that look like long hexadecimal fingerprints are also rejected. The report omits sanitized notes and every field outside the explicit `credential_results` allowlist.

## Report

Default operation is no-write. With `--write-report`, the only permitted output is:

```text
data/reports/credential_rotation_attestation_gate_v1.json
```

Writing is atomic. The report contains sanitized metadata and validation outcomes only, never raw inventory, raw attestation, complete input lines, screenshots, secret fingerprints, or provider output.

## Decisions

- `ROTATION_ATTESTATION_COMPLETE`
- `BLOCKED_INPUT_NOT_FOUND`
- `BLOCKED_REQUIRED_INVENTORY_INVALID`
- `BLOCKED_ATTESTATION_INVALID`
- `BLOCKED_SECRET_MATERIAL_DETECTED`
- `BLOCKED_REQUIRED_CREDENTIAL_MISSING`
- `BLOCKED_UNKNOWN_CREDENTIAL`
- `BLOCKED_DUPLICATE_CREDENTIAL`
- `BLOCKED_UNVERIFIED_CREDENTIAL`
- `BLOCKED_DUAL_CONTROL_INVALID`
- `BLOCKED_STALE_ATTESTATION`
- `BLOCKED_TIMESTAMP_INVALID`
- `BLOCKED_INCIDENT_REFERENCE_MISMATCH`
- `BLOCKED_UNSAFE_INPUT_PATH`
- `BLOCKED_WRITE_OUTSIDE_ALLOWED_ROOT`

## Operational Closure

Complete incident closure still requires manual provider-side rotation or revocation, this sanitized attestation, an independent reviewer, and zero unverified items. None of those provider actions are implemented by this branch.

## Validation

```powershell
python -m compileall scripts smartcrypto tests
python -m pytest tests\test_credential_rotation_attestation_gate_v1.py -q
python -m ruff check scripts/validate_credential_rotation_attestation_v1.py smartcrypto/security/credential_rotation_attestation tests/test_credential_rotation_attestation_gate_v1.py
python scripts\validate_credential_rotation_attestation_v1.py --project-root . --json
python scripts\generate_project_manifest.py --check
python scripts\scan_versioned_secrets.py --project-root . --json
git diff --check
git diff --cached --check
git status --short
git status --short -- data
```
