# Source and actual model-input proof: synthetic harness

No production hook, collector, model training, external market request,
operational data access or trading action. The proof remains research-only:

```text
SOURCE_RECEIPT_BOUNDARY=PARTIAL
ACTUAL_MODEL_INPUT_BINDING=PROVEN_IN_HARNESS
CLOCK_CUSTODY=UNPROVEN
PRE_FILTER_OPPORTUNITY_COVERAGE=HARNESS_ONLY
WRITER_ISOLATION=PASS
HISTORICAL_PIT_COVERAGE=0/7531
REAL_PROSPECTIVE_COVERAGE=NOT_COLLECTED
RUNTIME_INTEGRATION_ALLOWED=false
OPERATIONAL_AUTHORITY=false
```

`WRITER_ISOLATION=PASS` describes only the synthetic harness process and
fixture persistence; it does not certify resource isolation on a runtime host.

## Static boundary findings

- `market_features_refresh._fetch_klines` receives HTTP bytes in
  `response.read()` before JSON decoding. This is a possible local-receive
  instrumentation boundary, not authentication or independent clock custody.
- `_normalize_public_klines` drops exchange `close_time`. Parquet paths,
  candle timestamps, refresh time and the rebuilt-frame cache/mtime cannot
  replace receipt proof. The paginator's closed-range intent is not proof of
  every payload row's actual close or availability.
- Refresh may merge `existing_feature_ohlcv`, `configured_raw_source` and
  `recent_refresh_raw`. Existing history/fallback has no complete per-input
  receipt chain. The harness does not repair or authorize it.
- `predictor.export_latest_qlib_predictions` transforms latest rows and calls
  `model.predict_proba`. Exported predictions omit original vectors and source
  receipts; a later signal observer cannot supply missing receipt evidence.

## What the harness verifies

The network-free tests substitute HTTP transport with explicitly synthetic
Binance-format bytes, observing their receipt before the real downloader
decodes them. Real refresh, normalization, operational feature builder,
transformer and prediction export execute on disposable files. No joblib
artifact is deserialized: the loader supplies a frozen-metadata estimator
fixture, not a trained or active Qlib model.

`BoundModel` observes the exact DataFrame delivered by the real export path
and passes the same object to the estimator fixture. Ordered columns, dtypes,
row index and float hexadecimal values are sealed before consumption and
verified afterwards. The proof binds source receipts, feature-frame version,
transformation/configuration JSON, code hashes, model identity, input and
prediction UTC/monotonic samples, original row capture seals and probabilities.
Tampering, input mutation, config drift, invalid outputs or a prediction
available after the sealed decision block the research linkage.

All latest rows are captured before selection. Selected, selector-rejected,
RiskManager-rejected, ALLOW, pending decision and missing outcome remain
distinct. Existing V4.2 decisions/trade links and the V1 capture archive are
reused unchanged; outcomes require exact parents, never approximate joins.
`validate_archived_call` reconstructs the submitted matrix from archived
capture values and validates its hash and exact prediction/decision linkage.

## Writer and limits

`IsolatedWriter` defaults disabled and starts only through explicit harness
calls. Only synthetic records are accepted. A spawned process uses the V1
archive and institutional atomic writer/locks; auxiliary model-call proofs
are create-once and conflict rejecting. The successful E2E persists a model
call plus six lifecycle records, then reopens and checks them independently.

Defaults: eight IPC slots (maximum 32), 64 messages (maximum 256), 64 KiB per
message, 32 MiB V1 evidence and 4 MiB/64 auxiliary proofs. Source bounds are
eight packets, 64 KiB each, 1,500 rows/packet, 2,048 rows total; transformed
frames are capped at 4,096 rows/128 columns. These bound serialized data and
counts, **not total process RSS or CPU consumption**. No hard RSS limit is
enforced; Python/NumPy/pandas/sklearn overhead still requires host validation.

Enqueue is nonblocking. Backpressure/loss, child I/O failure, missing completion,
record-count mismatch and unconfirmed termination are blocked, never PASS.
Startup wait is bounded at 15 seconds; shutdown defaults to five seconds
(maximum 15), with bounded terminate/kill waits. OS I/O is not preemptible;
forced/unconfirmed shutdown invalidates completion. Writes are confined to
explicit external fixture roots. No process is installed in the Paper runtime.

Harness overhead is measured with `perf_counter_ns` before the model call,
including checks, fingerprints and capture. A single wall-clock sample is not
a production p99, CPU benchmark or hard real-time guarantee. The previously
reported 37.27 ms for two vectors is one harness observation, not a latency
bound or evidence of operational CPU, RSS or I/O isolation.

## Reuse, validation and remaining evidence

The original implementation started at clean `origin/dev@c089208`. This PR
is stacked on the head of PR #445,
`d3afb11bf562ab82e655212ccb7425800aaf2255`, on
`codex/paper-pretrade-causal-feature-capture-v1`. Its seven non-manifest V1
files are inherited byte-for-byte at their canonical paths and excluded from
the incremental diff. The incremental change contains only this proof's two
modules, tests, documentation and canonical manifest update. PR #445 and the
branches of #443/#444 remain unchanged; no lock or runtime changes are included.

Run the harness with:
`python -m pytest -q -s tests/test_paper_pit_source_model_input_proof_v1.py`.
Other validation covers V1, immutable Ledger/crosswalk contracts and the
existing refresh/continuity fixtures, plus compile, Ruff, MyPy, Bandit,
canonical manifest, secret scan and diff checks.

The implementation validation reported 178 passing tests. Closing this PR
preserves all Python and test bytes; only this documentation and its generated
manifest entry change. Content/ancestry comparisons, canonical manifest,
versioned-secret scan and diff checks validate this packaging step. Prior
functional, lint, typing and security results are retained as reported rather
than presented as newly executed checks.

The clock proofs and payloads are synthetic. Authentication of real source
receipts, independent clock attestation/custody, complete merged-history
provenance, actual installed-model identity and measured runtime isolation
remain unproven. Authorization of any real prospective capture is separate.
Historical PIT stays `0/7531`; real prospective coverage is `NOT_COLLECTED`.
