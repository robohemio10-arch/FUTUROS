# Operational Exception Swallowing Audit V1

## Objective

This audit identifies broad Python exception handlers that can hide operational failures. It is static and read-only: audited modules are parsed with `ast` and are never imported or executed.

The branch does not change trading, risk, Qlib, AI Shadow, OCR, datasets, active signals, readiness, live/canary controls, Docker runtime, or notification delivery.

## Scope

The auditor scans versioned Python files discovered by the repository's canonical versioned-file discovery utility. This preserves the same behavior in a Git checkout and in a standalone project ZIP.

Detected patterns include:

- broad handlers containing only `pass`;
- broad handlers containing only `continue`;
- broad handlers returning success after an exception;
- broad handlers returning a fail-closed/default value without a diagnostic;
- broad handlers returning another fallback without controlled status.

Handlers that re-raise, emit `logger.exception`/`warning`/`error`, append a structured error, or return a controlled failure with `status` and `reason` are not reported.

## False Positives

The following are intentionally excluded:

- custom exception classes with `pass`;
- Protocol and ABC contracts;
- exception fixtures under `tests/`;
- explicitly documented best-effort or optional fallbacks;
- controlled failure reports.

The report exposes `ignored_false_positive_count` so exclusions remain visible rather than silently disappearing.

## Severity

- `critical`: silence can mask order submission, risk mutation, dataset mutation, readiness, live, or canary behavior.
- `high`: silence masks operational audits, runtime evidence, manifest/secret checks, dashboard safety, notification safety, Qlib refresh, feedback sync, or healthchecks.
- `medium`: reporting, parsing, conversion, UI fallback, or non-critical operational work lacks a diagnostic.
- `low`: local fail-closed fallback is safe but insufficiently observable.

A fail-closed parser returning `None`, `False`, or an empty collection is not treated like an order/risk mutation. It can still be reported as LOW or MEDIUM when its failure reason is invisible.

## Commands

Run the audit:

```powershell
python scripts/audit_operational_exception_swallowing.py --project-root . --json
```

Fail CI on HIGH or CRITICAL findings:

```powershell
python scripts/audit_operational_exception_swallowing.py --project-root . --json --fail-on high
```

Accepted `--fail-on` values are `critical`, `high`, `medium`, `low`, and `none`. The default is `high`.

## Output Contract

The deterministic JSON contains counts by severity, ordered findings, file-discovery provenance, ignored false positives, parse errors, and immutable safety flags. It intentionally has no timestamp so identical source trees produce identical output.

Each finding includes `severity`, `file`, `line`, `function_or_class`, `pattern`, `reason`, and `recommendation`.

## Safety Guarantees

The auditor:

- uses no network, Docker, exchange, ccxt, or notification dispatcher;
- sends no orders and changes no risk;
- writes no runtime artifact;
- logs no environment variables, tokens, or secrets;
- preserves paper/shadow-only flags;
- never converts a caught failure into success.

The initial repository audit is allowed to return `warning` for existing MEDIUM/LOW observability debt. Acceptance requires zero unresolved HIGH/CRITICAL findings; broader cleanup belongs in narrowly scoped follow-up branches.
