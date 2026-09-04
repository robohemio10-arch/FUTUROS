# DEV Security & Governance Hardening Closeout V1

Single-branch closeout for audit findings H-01/H-02/M-01..M-05/L-01.

## Invariants

- Paper/shadow/research only.
- No Treatment, live, canary, real order submission, exchange-private access, risk change, model promotion, or scheduler activation.
- A Qlib dependency-security gate may pass only for the exact certified security-clean graph; this does not grant operational authority.
- `dev` branch protection is an external GitHub administrative control and is not considered closed until GitHub reports `protected=true` with required status checks enforced.

## H-02 and M-01/M-02 evidence

GitHub Actions run `33916166319` on commit `452c6d953cac21634aaf6ce004eef4d392b1a3b2` produced the reviewed `security-resolution-evidence` artifact.

The certified Qlib graph contains 190 exact packages, including `pyqlib==0.9.7`, `mlflow==3.16.0`, `cryptography==50.0.0` and `pyarrow==25.0.1`. `pip-audit` returned exit code 0 and zero known vulnerabilities. The complete graph is committed in `requirements-qlib-security.lock` with PyPI SHA256 hashes.

`requirements-dev.lock`, `requirements-runtime.lock` and the Bitradex requirements are also exact and hash-locked. Runtime Docker builds install with `--require-hashes`; uncontrolled `pip install --upgrade` was removed.

Validated immutable image identities:

- `python:3.11-slim@sha256:9534e5a8e315485d4061ed659af0fd78a284c015f9b73661b41d6bab25604534`
- `python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea`
- `freqtradeorg/freqtrade:stable@sha256:7031bca43ed7668ebf421725dd5016acade6ef88b0771db3e08c96e6d19a42db`

GitHub Actions are pinned to full commit SHAs.

## Review registries

Legacy state/execution-boundary and exception-handling findings are reviewed only when the exact path/line/classification (or function/pattern) and complete source SHA256 match the versioned registry. Any source drift invalidates the review automatically. High and critical exception findings are never waivable.

## H-01 sequencing

Feature branches and pull requests always run the full CI suite. The branch-protection auditor runs diagnostically there. Enforcement with non-zero exit is limited to pushes on `dev`, preventing a governance gap from hiding code/test results while still making an unprotected `dev` fail closed after integration.

H-01 is closed only by GitHub administrative state, not by repository documentation.
