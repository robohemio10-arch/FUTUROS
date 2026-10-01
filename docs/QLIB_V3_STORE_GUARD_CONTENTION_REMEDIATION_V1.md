# V3 prospective store guard contention

This branch changes only admission-side serialization for the V3 research
evidence store. It does not deploy code or retry an observation, shadow
resolution, financial publication, or publisher linkage.

## Acquisition

`store.exclusive()` still uses atomic `O_CREAT | O_EXCL`. It waits at most
3 seconds by a monotonic clock, polling every 50 ms. Callers may supply an
explicit bounded timeout for tests; validation rejects non-finite values,
timeouts above 30 seconds, and polling above one second or above the timeout.
The bounded wait is only around guard acquisition, before read/merge/write.
The holder's guard is never changed while waiting. An invalid, partial, or
legacy guard remains blocking.

Immediately after acquisition, the owner writes and fsyncs JSON metadata
before entering the critical section: `schema_version`, `lock_id`, `owner`,
`pid`, `hostname`, `acquired_at_utc`, `store_path`, and `invocation_id`.
The receipt exposes `lock_id`, `owner`, `waited`, `wait_seconds`,
`contention_count`, and `acquired_at_utc`. The natural observer's report and
diagnostic probe distinguish `no_contention`, `contention_recovered`, and
`contention_timeout`. The orchestrator reports the same classification.

Timeout fails closed. For compatibility, the outward reason remains
`store_busy_or_stale_guard_requires_review`; the structured reason code is
`store_guard_acquire_timeout`. No stale-lock TTL or automatic removal exists.
Metadata is observational and never authorizes breaking another holder's lock.

## Release and boundaries

Release checks the original file identity and exact metadata bytes before
unlinking. A missing, replaced, or modified guard is not removed; release
raises `store_guard_release_ownership_lost`. If the protected operation has
already failed, that original exception remains primary and the release
failure is logged without sensitive payloads. This protects cooperative
writers; an external actor racing a path replacement at the unlink boundary
remains outside the portable compare-and-delete guarantee.

The store schema, identity, `store_sha256`, atomic JSON replacement,
`signal_id`/`trade_id` deduplication, and fail-closed admission are unchanged.
The probe remains research-only and best-effort. No replay or backfill is
performed, including for the historical event
`decision-event:de3402dc3371f0b84a641cf23d5c6272b092ceeb`.

`AUTO_STALE_GUARD_DELETE=false`
`UNBOUNDED_RETRY=false`
`REPLAY=false`
`BACKFILL=false`
`LIVE_CHANGE=false`
`ORDER_CHANGE=false`
`RISK_CHANGE=false`
