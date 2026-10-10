# Execution Decision L1 Prospective Collector V1

Independent opt-in research process. Nothing starts on import, no runtime wiring,
service, scheduler, Docker or Paper configuration is changed. No real collector
needs to run to approve the engineering gate; tests use public-schema fixtures only.

## Source And Clocks

Only `GET https://fapi.binance.com/fapi/v1/ticker/bookTicker?symbol=BTCUSDT|ETHUSDT`.
TLS certificate validation, fixed hostname/path, no redirects, proxy discovery,
credentials, keys, private requests or order routes. A disposable isolated Python
child provides a whole-request deadline including DNS; timed-out children are killed.
Poll minimum 1s, default 2s for two symbols. Backoff is interruptible, bounded and
honors numeric Retry-After. Each recovery is counted, not hidden.

Official public schema: symbol, bidPrice/bidQty, askPrice/askQty, optional transaction
`time` (milliseconds). Source reference:
https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data#symbol-order-book-ticker

Event time comes only from `time`; missing event time is UNAVAILABLE and blocks
association. Receive UTC is recorded after the complete HTTP body. Available UTC
is observed in the collector consumer after public-payload validation, including
queue/process delay; it is never replaced with exchange time or decision time.
Host UTC synchronization with Paper/exchange clocks is UNPROVEN. Regressions and
exchange clocks ahead of receive fail closed. No claim of clock accuracy is made.

REST polling is sampled L1, not a continuous order book. RPI orders are excluded by
the source. There is no depth, queue position or contiguous update sequence proof;
unknown missed market-message counts remain null. Repeated unchanged snapshots do
not imply lost events. Polling gaps, interruptions, invalid data and queue losses
are counted and archived when the archive queue has capacity.

## Decision Contract And PIT

Reuse the sealed DecisionRecordV42 parser and identifiers. Tail begins at EOF, never
reads historical decisions or resumes an old session. Only complete new lines whose
decision timestamp is >= this observer session start are eligible. An initial partial
line is skipped; rotation/truncation/invalid seals stop the collector without touching
Paper. Duplicate identical decisions are ignored; divergent identities fail closed.

Association contains exact decision_id, candidate_id, signal_id, payload seal,
symbol, decision UTC, observation UTC and the selected observed L1 record. Selection
requires both event time and available-at <= decision time; default maximum event
and local snapshot age 5s inclusive. A quote received or validated after the decision
is not backdated, even if its exchange transaction time is earlier. No nearest/fuzzy
join, backfill, retrospective public fetch, synthetic decision or outcome use.

Missing/stale/evicted quotes produce explicit UNAVAILABLE, never zeros. A known
interruption invalidates older quotes until a new observed snapshot. Snapshot history,
identity registry, all producer/archive queues, response bytes, line bytes, session
duration and archive bytes are bounded. Producer enqueue is nonblocking; drops are
counted separately for quotes, decisions, notices and archive records. Paper never
waits on a collector or archive queue. Losses prohibit any complete-coverage claim.

## Archive And Opt-In

Default CLI is preflight, no network/no writes. `--collect` permits public polling
without persistence. Persistence needs all three flags below, explicitly supplied.
No daemonization or activation occurs. Ctrl+C requests controlled shutdown; bounded
joins drain accepted work, and any live worker/error is reported as blocked.

```powershell
python -B scripts/collect_execution_decision_l1_prospective_v1.py --runtime-root <runtime-root> --json
python -B scripts/collect_execution_decision_l1_prospective_v1.py --runtime-root <runtime-root> --collect --duration-seconds 60 --json
python -B scripts/collect_execution_decision_l1_prospective_v1.py --runtime-root <runtime-root> --collect --write-archive --output-root <absolute-external-root> --duration-seconds 60 --json
```

External root must be absolute, outside Git/project/runtime, without symlinks or
Windows reparse points. Each session is create-once with a UUID. A single bounded
writer emits immutable JSON segments and an atomic manifest using the existing
restricted AtomicWritePolicy/atomic_write_json, fsync and same-directory replace.
Segments have schema SHA256, raw response SHA256, canonical quote/segment hashes,
previous-segment hash and exact-byte file hashes in the manifest. No runtime ledger
or data directory is an output. Schema is available via `--schema` without collection.
An interrupted session without final manifest is incomplete, not silently certified.
Hashes establish integrity, not independently authenticated exchange provenance.
The manifest records drained archive segments, not a successful join of its own writer.
Its shutdown validation is PROCESS_REPORT_REQUIRED; only the final process report can
confirm shutdown_complete. A session/archive write attempt is reported even on I/O failure.
The archive byte budget bounds segment payload bytes; small session/manifest metadata
and atomic-write temporary space are additional. They contain no raw environment data.

## Canonical Kill-Switch Enforcement

An explicit absolute runtime root and its canonical ledger are required, even with
`--ledger-path`. The sole authority is runtime-root/data/runtime/kill_switch.json,
using the existing KillSwitchGuard pure normalization, never evaluate/load_state or
its event logger. No authority file, CLEAR state or bypass is created. Only explicit
schema-v1 Paper state with boolean entries, UTC metadata and non-default clearance
can authorize this observer. Missing, corrupt, conflicting, inaccessible, changing,
symlink/reparse or indeterminate state blocks; global or ANY requested-symbol block
stops the complete session. Legacy/default-filled state is not proof of clearance.

Authorization precedes public network, archive construction, producer startup and
each filesystem write (mkdir, session, segment, final manifest). One read-only monitor
polls at most four times/second (250ms minimum interval), reading at most 64KiB.
Every request/write boundary waits for a fresh read; critical paths do not perform
filesystem reads themselves. The read/authorization deadline is 750ms; expired proof
or monitor failure latches denial, with no automatic recovery or restart.

Revocation stops producers, discards pending archive records without flushing or
writing a final manifest, and retains the cause in the in-memory process report.
Public child cancellation checks every 50ms, with a 500ms termination wait. All workers,
including the authority reader, share the existing shutdown budget (default 15s,
maximum 30s). A stuck reader, producer, writer or unconfirmed child termination means
shutdown_incomplete, never PASS. Previously committed archive records are not rewritten.

This is bounded sampled authorization, not an instantaneous filesystem transaction
with the external authority writer. A state change immediately after a successful
read is detected on the next read; an atomic write already started cannot be undone.
No subsequent write is started after observed denial, and delayed/stuck reads cannot
renew permission. Local-path binding is not proof of the running Paper process or
clocks. COLLECTOR_KILLSWITCH_ENFORCEMENT is an engineering/session gate only; full
deployment readiness still requires independent host, clocks, durability and isolation
evidence. No operational kill-switch was modified or real collection performed.

## Evidence Readiness Boundary

Engineering gate: COLLECTOR_READY_FOR_OPT_IN after focused validation. This captures
decision L1 only, not submit/ack/fills, actual fees, maker/taker, L2, queue or economic
uplift. Evidence Readiness V1 stays BLOCKED_MISSING_EXECUTION_EVIDENCE. Its MarketSlice
also requires last-trade fields not supplied by bookTicker: do not invent them or
forge a complete EvidencePacket. Existing readiness contracts/gates are unchanged.
Research/shadow/Paper-only; no strategy, RiskManager, Freqtrade, PnL, orders, active
signals, model, Paper Treatment or operational authority changes.

## Validation

Focused tests use temporary ledgers, sealed fixture decisions and fake public clients.
They cover strict PIT/freshness, hash/schema validation, exact identities, EOF-only
tailing, rotation/truncation, bounded queues/loss barriers, source/clock gaps,
deadline errors, backoff/recovery, Ctrl+C, shutdown timeout, external path restrictions,
hash-chain archives, disk/permission errors, and no-write CLI defaults. Regressions
include Evidence Readiness V1, Decision Ledger V4.2 and execution cost gates.
No public collection, runtime activation or real prospective coverage is proved by
these tests. Opt-in collection is a subsequent operator-authorized action.
