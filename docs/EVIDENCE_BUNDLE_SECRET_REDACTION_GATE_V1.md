# Evidence Bundle Secret Redaction Gate V1

## Purpose

This gate protects evidence collection paths that are outside Git's tracked-file boundary. It scans explicitly selected files, staging directories, and ZIP archives; redacts supported text secrets deterministically; and permits bundle creation only after the sanitized staging tree and final archive both pass a second scan.

It does not replace `scan_versioned_secrets.py`. That scanner protects versioned source. This package protects explicitly supplied evidence inputs, including ignored files and external bundles, without reading any source by default.

## Default Boundary

The default command is no-write and has no implicit source discovery:

```powershell
python scripts\build_sanitized_evidence_bundle_v1.py --project-root . --json
```

Without `--source`, it returns `BLOCKED_INPUT_NOT_FOUND`. It never walks the project, reads `.env`, invokes Docker, inspects process environments, accesses an exchange, or executes producers.

For a single file, the explicit `--source` path is its allowlist entry. For directories and ZIPs, every included relative path must be repeated explicitly with `--allow-file`:

```powershell
python scripts\build_sanitized_evidence_bundle_v1.py `
  --project-root . `
  --source .\staging `
  --allow-file reports/system_health.json `
  --allow-file compose/docker-compose-no-interpolate.yml `
  --compose-output-mode no-interpolate `
  --json
```

No wildcard or recursive implicit inclusion is supported.

## Secret Handling

Text detection covers sensitive named assignments, GitHub PATs, JWTs, bearer and Authorization tokens, generic API keys, Telegram bot tokens, authenticated URLs, sensitive query parameters, and PEM private keys. JSON, YAML, TOML, ENV-style text, and logs use the same deterministic detector.

Redaction uses:

```text
<REDACTED:category:sha256_prefix>
```

Reports never store the matched value or complete line. A finding contains only its deterministic ID, category, severity, relative path, location, pattern name, redacted marker, complete one-way SHA-256 fingerprint, blocking state, and remediation guidance.

Synthetic test credentials are assembled only at test runtime. No complete synthetic or real credential is versioned.

## Compose Outputs

Interpolated `docker compose config` output is blocked even if its values could be redacted. Evidence collection must use:

```text
docker compose config --no-interpolate
```

The resulting text must still pass the allowlist and secret scanner. Use `--compose-output-mode no-interpolate` to record this provenance. This package never invokes Docker itself.

## Archive Safety

ZIPs are inspected in place and never extracted. The gate blocks path traversal, absolute paths, Windows drive paths, symlinks, duplicate entries, oversized files, forbidden extensions, forbidden directories, and allowlist violations. Binary or non-UTF-8 inputs are rejected because deterministic text redaction cannot be proven.

Forbidden inputs include `.env` variants, private-key containers, credential/auth JSON, `.git`, runtime directories, SQLite, operational Parquet, active registries, active models, process/environment dumps, and unapproved files.

## Report and Bundle Writes

Report-only mode writes one JSON under `data/reports`:

```powershell
python scripts\build_sanitized_evidence_bundle_v1.py `
  --project-root . --source .\safe.txt --write-report --json
```

Bundle mode requires an explicit directory under `data/reports/evidence_bundles`:

```powershell
python scripts\build_sanitized_evidence_bundle_v1.py `
  --project-root . `
  --source .\staging `
  --allow-file report.json `
  --build-sanitized-bundle `
  --output-dir data/reports/evidence_bundles/manual-run `
  --json
```

The build uses a temporary staging directory, rescans sanitized files, writes a deterministic ZIP to a temporary path, rescans the ZIP without extraction, atomically publishes it, calculates SHA-256, and removes staging. A failed build does not publish a partial archive.

## Decisions

- `BUNDLE_SAFE_TO_CREATE`
- `BUNDLE_SAFE_AFTER_REDACTION`
- `BLOCKED_SECRET_FINDINGS`
- `BLOCKED_FORBIDDEN_FILE`
- `BLOCKED_UNSAFE_ARCHIVE_ENTRY`
- `BLOCKED_COMPOSE_INTERPOLATION`
- `BLOCKED_ALLOWLIST_VIOLATION`
- `BLOCKED_INPUT_NOT_FOUND`
- `BLOCKED_OUTPUT_OUTSIDE_ALLOWED_ROOT`

Safe decisions do not authorize live trading, runtime changes, model promotion, order submission, or private exchange access.

## Validation

```powershell
python -m compileall scripts smartcrypto tests
python -m pytest tests\test_evidence_bundle_secret_redaction_gate_v1.py -q
python -m ruff check scripts/build_sanitized_evidence_bundle_v1.py smartcrypto/security/evidence_bundle_redaction tests/test_evidence_bundle_secret_redaction_gate_v1.py
python scripts\build_sanitized_evidence_bundle_v1.py --project-root . --json
python scripts\generate_project_manifest.py --check
python scripts\scan_versioned_secrets.py --project-root . --json
git diff --check
git diff --cached --check
git status --short
git status --short -- data
```
