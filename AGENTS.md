# AGENTS.md

## Purpose

Keep Codex work correct, safe, scoped, and context-efficient.

## Read order

Read only what the task requires.

1. This file.
2. Files named in the task.
3. Direct dependencies of those files.
4. Relevant canonical docs only when needed:
   - `ARCHITECTURE.md` — component boundaries and authority.
   - `SECURITY.md` — runtime/security invariants.
   - `DATA_CONTRACTS.md` — schemas/contracts.
   - `README.md` — project overview.
   - task-specific docs under `docs/`.

Do not scan the whole repository by default. Do not read `PROJECT_MANIFEST_CLEAN.json` unless the task requires manifest analysis.

## Operating defaults

- Default environment is paper/research/shadow, not live.
- Preserve:
  - `LIVE_ENABLED=false`
  - `ORDER_SUBMISSION_ENABLED=false`
  - `REAL_ORDER_SUBMISSION_ENABLED=false`
  - no private exchange access unless the task explicitly and validly requires it.
- Never introduce real credentials, secrets, or private keys.
- Do not activate live trading, canary trading, order submission, automatic model promotion, automatic risk changes, or operational authority unless explicitly required by the task and existing project policy allows it.
- Respect the architecture in `ARCHITECTURE.md`: ML/Qlib does not send orders; RiskManager remains the authorization boundary; Freqtrade remains the execution boundary.
- Preserve existing data contracts unless the task explicitly changes them.

## Change policy

- Make the smallest correct change.
- Stay inside the requested scope.
- Reuse existing modules/contracts before creating new abstractions.
- Do not refactor unrelated code.
- Do not rename/move files unless required.
- Do not change public behavior silently.
- Do not add placeholders, TODO implementations, fake outputs, or broad fallbacks.
- Fail closed when safety, schema, freshness, authority, or required evidence is uncertain.
- Keep code typed, testable, deterministic where practical, and explicit about I/O failures and missing/invalid data.

## Context and credit efficiency

- Prefer targeted reads/searches over repository-wide exploration.
- If paths are supplied, inspect those first.
- Follow dependencies only as needed to implement or verify the change.
- Do not restate project history or documentation in code/comments.
- Do not generate long planning documents unless requested.
- Avoid running the full test suite unless:
  1. the task explicitly requires it,
  2. the change is cross-cutting, or
  3. focused validation indicates wider impact.
- Start with the smallest relevant validation set and expand only when justified.
- Do not repeatedly rerun expensive checks that already passed unless subsequent changes can affect them.

## Validation

Use the minimum validation proportional to the change.

Typical order:

1. syntax/compile for changed Python scope;
2. focused tests for changed behavior;
3. directly affected regressions;
4. targeted lint/type/security checks when relevant;
5. broader checks only if impact warrants them.

Repository-wide commands already exist in `Makefile`, including:
- `make compile`
- `make test-fast`
- `make lint`
- `make typecheck`
- `make security`
- `make audit`

For manifest-sensitive changes, use the repository's existing manifest generator/checker. Do not hand-edit generated manifest content unless that is the established project workflow.

Before completion:
- `git diff --check`
- ensure no unintended runtime/data artifacts are versioned;
- ensure no secrets were introduced.

## Git discipline

- Do not modify unrelated user changes.
- Do not discard worktree changes you did not create.
- Do not commit, push, merge, delete branches, or open/close PRs unless explicitly requested.
- Keep generated/runtime artifacts out of version control unless the task explicitly declares them versionable.

## Completion report

Keep the final report short:

- changed files;
- validations run and result;
- blockers or residual risk, if any.

Do not repeat the task description or produce a long narrative unless requested.
