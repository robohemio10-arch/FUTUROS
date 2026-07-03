# Qlib Research Backend Environment Lock V1

## Objective

This branch adds a static, research-only environment audit for the Qlib backend. The goal is to make the current Qlib dependency state explicit and reproducible enough for research planning without enabling runtime operation.

The audit does not install packages, train models, update Qlib runtime, promote models, write registries, access exchange, or send orders.

## What The Auditor Checks

The CLI reads local project files and static Python import metadata:

- `pyproject.toml`
- `requirements*.lock`
- `requirements*.txt`
- import specs for the required Qlib modules used by the existing backend gate

It reports:

- whether a Qlib dependency is declared;
- whether the declaration is pinned or hash locked;
- whether Qlib is importable in the current Python environment;
- detected Qlib version and package path;
- status for required modules;
- Python version and platform;
- compatibility and recommended next action;
- safety flags proving no operational side effects.

## Current Dependency Contract

`pyproject.toml` declares a research-only optional dependency group:

```toml
[project.optional-dependencies]
qlib = [
  "pyqlib>=0.9,<1"
]
```

This is an explicit research dependency declaration. It is not a hermetic lock because the version is a range. A fully pinned or hash-locked Qlib research environment can be added later in a dependency-management branch after resolver validation.

## Commands

No-write audit:

```powershell
python .\scripts\audit_qlib_research_backend_environment_lock_v1.py --project-root . --json
```

Optional report write under ignored runtime reports:

```powershell
python .\scripts\audit_qlib_research_backend_environment_lock_v1.py --project-root . --write --json
```

Existing backend gate:

```powershell
python .\scripts\audit_qlib_research_backend_gate_v1.py --project-root . --json
```

Trainer dry-run remains no-train by default:

```powershell
python .\scripts\train_qlib_institutional_ranking_challenger_v1.py --project-root . --json
```

## Interpreting Status

- `status=ok`: Qlib is declared and the backend is importable.
- `status=warning`, `reason=qlib_backend_unavailable`: Qlib is declared but unavailable in the current environment.
- `status=warning`, `reason=qlib_backend_partial`: Qlib is partially importable but required modules or metadata are incomplete.
- `status=blocked`, `reason=qlib_dependency_not_declared`: the project has no Qlib research dependency declaration.
- `status=blocked`, `reason=qlib_backend_blocked`: the probe was blocked by environment or isolation validation.

`qlib_dependency_pinned=false` is not an operational failure for this branch. It is a reproducibility signal: the dependency is declared, but not fully locked.

## Safety Boundaries

This audit is paper/shadow-only and research-only:

- no model promotion;
- no active registry write;
- no Qlib runtime update;
- no IA Shadow runtime update;
- no Freqtrade, RiskManager, or signal producer changes;
- no orders;
- no private exchange access;
- no SQLite, parquet, runtime, or model writes.

If Qlib becomes available, the existing backend gate can report `qlib_backend_status=available`. The Qlib trainer still does not train by default; training requires explicit `--train` and remains challenger/research-only.
