# Paper pre-trade causal capture V1

Status: `BLOCKED_FOR_RUNTIME_INTEGRATION`. No producer hook or collector is
installed. This is a disabled contract and isolated harness, not collected
prospective evidence or an operational permission.

## Source boundary and limitations

The actual vector exists in `smartcrypto/qlib_engine/predictor.py`, in the
`latest` / `_transform_features` input passed to `model.predict_proba`.
The predictions consumed by `smartcrypto/execution/signal_producer.py` do not
preserve that vector together with trusted per-source availability receipts.
The existing lineage fallback timestamp/hash is not proof of market-data
receipt or of the original model vector. The late V2 observer cannot supply it.

No changes are made to these modules, RiskManager, Freqtrade, or V4.2. The
7,531 historical decisions remain **0/7531 complete PIT observations**. No
historical reconstruction, model training, financial audit, or runtime reads
are performed by this implementation.

## Evidence contract

`CaptureSession.receive_source` samples UTC and monotonic time at the caller's
actual delivery boundary, hashes bounded source bytes, and returns a sealed
`LOCAL_BOUNDARY_RECEIPT`. It does not accept a caller-supplied `available_at`.
Source event time and candle close are separate, mandatory causal checks;
neither is substituted for receipt time. Raw source bytes are not retained.

`capture` copies ordered float64 feature values without rounding, binding
their `float.hex()` identity (including negative zero), names, per-feature
source receipts, epoch, opportunity/candidate/signal, model, configuration,
boundary, clock evidence, and quarantine reasons into an immutable seal.
Only a receipt issued in the same session is trusted by the harness.
Naive timestamps, non-finite values, sensitive/outcome feature names and
conflicting identities are rejected. Missing sources, partial candles, future
inputs, expired/excessive clock bounds, incomplete vectors and late captures
are explicitly unusable for research.

The receipt proves only delivery to this API. It does **not** authenticate a
remote source, prove that the real model consumed the submitted values, or
establish independent host-clock custody. SHA-256 is an integrity seal, not
authorization. `ClockProof` requires an explicit measurement reference,
validity interval and error bound; this module never manufactures an NTP or
host attestation. These are remaining integration blockers.

Complementary lifecycle records retain the original sealed V4.2 decision and
trade link unchanged. Exact candidate/signal, model, feature contract, side,
symbol, event and trade identities must agree. Missing IDs are not inferred
from opportunity IDs. The existing V4.2 feature hash is not reinterpreted as
the new vector hash. Selector rejection without a decision is separate from
an observed RiskManager rejection. Outcomes require a prior exact trade link,
closed timestamp and source hash; reported PnL is never financially certified
or aggregated here. Unknown outcomes are not zero-PnL outcomes.

## Isolation and explicit harness persistence

`CaptureSession()` is disabled: no clock sampling, file I/O, worker, network,
signal publication or decision mutation. There is no environment-variable
activation or runtime hook. An enabled session requires an explicit epoch;
synthetic evidence is the default and is excluded from real coverage.

The harness has one producer owner. Defaults: queue 64 (maximum 256), 1,024
opportunities (maximum 4,096), 128 receipts (maximum 512), 128 features and
64 KiB per source/record. Receipts are retained for the bounded session;
budget exhaustion is explicit, not an automatic session/epoch rotation.

Only the separately invoked `EvidenceArchive` consumer performs writes, with
explicit `authorized=True`, absolute external root and explicit forbidden
project/runtime roots. It reuses the existing L1 path validator and
institutional interprocess lock/atomic writer. Git roots, symlinks and reparse
points are rejected. Exact repeats are idempotent; identity conflicts never
overwrite an archived record. Default archive limits are 4,096 records and
32 MiB. Reads revalidate schema, seals, paths, bounds and lifecycle links.

`drain` defaults to eight records and a 100 ms cooperative deadline, with
50 ms lock timeouts. Deadline checks occur between/after I/O calls: an OS
write/fsync is **not preemptible** by this API. It must never run on the trading
thread. No background writer or hard real-time guarantee is claimed.
Queue loss, I/O failure and exceeded deadlines invalidate session evidence
and emit sanitized reason codes without source bytes or exception payloads.
The original trading decision is untouched. On failure, records already
written remain immutable; there is no automatic retry or recovery.

Archive coverage alone cannot establish durability/completeness of a lost
session or count opportunities never captured. It reports structural quality,
not runtime authorization. A future authorized integration must establish
independent receipts/clocks, actual model-input binding, writer isolation,
durability/loss accounting and measured producer overhead first.

## No-write coverage

```powershell
python scripts/audit_paper_pretrade_causal_capture_v1.py --json
python scripts/audit_paper_pretrade_causal_capture_v1.py --archive-root <EXPLICIT_EXTERNAL_ARCHIVE> --json
```

Without an explicit archive, the CLI returns blocked and reads no sources.
It has no capture, network or write option. Coverage is grouped by epoch,
boundary, symbol and side, with generated/pending, ALLOW, selector/RiskManager
rejection, quarantine and exact outcome counts. Reordered duplicate receipts
do not inflate counts; conflicting identities block causal eligibility.
There is no claim about pre-boundary rejections, all opportunities in the
trading system, real prospective collection, financial certification, P08,
OOS edge, model promotion or operational readiness.

Validation uses synthetic packets, clocks, outcomes and temporary archives
only, plus existing Ledger/collector/crosswalk regressions.
