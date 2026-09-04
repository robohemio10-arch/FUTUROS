# DEV Security & Governance Hardening Closeout V1

Single-branch closeout for audit findings H-01/H-02/M-01..M-05/L-01.

## Invariants

- Paper/shadow/research only.
- No Treatment, live, canary, real order submission, exchange-private access, risk change, model promotion, or scheduler activation.
- Qlib remains fail-closed until a full resolver graph plus `pip-audit=0` is committed as evidence.
- `dev` branch protection is an external GitHub administrative control and is not considered closed until GitHub reports `protected=true` with required status checks enforced.

## Review registries

Legacy state/execution-boundary and exception-handling findings are reviewed only when the exact path/line/classification (or function/pattern) and the complete source SHA256 match the versioned registry. Any source drift invalidates the review automatically. High and critical exception findings are never waivable.

## Supply chain

The security-resolution workflow produces full transitive resolution evidence, PyPI artifact hashes and immutable external image digests. Generated artifacts do not grant runtime authority and must be reviewed and committed before the corresponding audit item is closed.
