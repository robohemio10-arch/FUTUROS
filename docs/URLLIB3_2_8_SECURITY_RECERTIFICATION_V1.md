# Security graph recertification: tornado 6.5.9

The first urllib3 2.8.0 candidate was not CI-certified. CI #1516 found
`tornado==6.5.8` in the dev, runtime, and Qlib security locks with
GHSA-chx6-46f5-w4vp, GHSA-c2m8-h5v5-343r, and GHSA-3hv7-mjh2-fv65.
Each finding listed 6.5.9 as the fix. This candidate changes only the tornado
pin and its official PyPI artifact hashes in those three locks. No other
dependency version changes.

The Qlib lock contains 190 exact, hashed pins. The Linux/amd64 Python 3.12
full-graph audit evaluates 183 effective dependencies after platform markers;
the hash-enforced resolver dry-run covers all 190 lock entries. Current
candidate audit and resolver reports are outside Git under
`E:/FUTUROS_LOCAL_CHECKPOINTS/TORNADO_6_5_9_RECERTIFICATION_V1`.
The policy and evidence record their SHA-256 identities.

Local dev and runtime full-graph audits and the Qlib Linux target audit found
zero known vulnerabilities. This is candidate evidence, not CI certification:
`p08_functional_regression.status=pending_ci_certification` and
`p08_allowed=false`. The CI Linux full suite, Docker builds, and Qlib
functional regression remain mandatory. No Paper, V3, trading, or runtime
configuration was changed.
