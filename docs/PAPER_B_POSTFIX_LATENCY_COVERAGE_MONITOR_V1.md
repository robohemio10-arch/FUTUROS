# Paper-B post-fix latency and coverage monitor V1

This is a read-only research monitor. It reuses the immutable Branch01 baseline,
the certified operational V4.2 population, exact V3 crosswalk, treatment ledger,
and treatment paper SQLite read-only query. It does not alter the causal manifest,
baseline, V3 evidence, paper execution, risk, strategy or model. The CLI writes
only its own ignored `data/reports/canonical_treatment` JSON with explicit
`--write-report`.

Run no-write:

```powershell
python scripts/build_paper_b_postfix_latency_coverage_monitor_v1.py --project-root E:\FUTUROS_BR05 --runtime-root E:\FUTUROS --json
```

`baseline_historical_debt` is never reduced by late resolution. Post-fix and
cumulative counts are separate. Missing events have exact IDs and a conservative
classification; a missing publisher row remains unexplained. The funnel counts
distinct decisions, not trades, joined only by `decision_event_id` in `enter_tag`.

Publisher latency is `first_observed_at_utc - signal_timestamp_utc`; end-to-end
latency is `first_observed_at_utc - operational decision_timestamp`. Only validated
scored rows contribute. Scheduler due/run, feature build start/end and score
start/end are not separately persisted, so their latencies are unavailable.
The eligible-to-observed distribution is also split into chronological post-fix
halves for a descriptive Soak B comparison, without a fabricated stability limit.
No file mtime is used as a causal timestamp. A later container `started_at`
proves a lifecycle event but does not establish the outage boundaries or
observation continuity. The report does not erase historical misses or infer
zero misses during unknown gaps. Events between the two known container starts
are counted separately as an inter-start window, never as proven downtime.

Soak A requires at least 50 post-fix eligible decisions, complete coverage,
zero current misses, and measured causal latency. Soak B requires at least 100,
99% coverage, no unexplained lineage miss, and measured latency. These are
research gates only. No numeric latency threshold is invented. A PASS is not
authorization for an economic edge claim, release, live/canary or orders.
The immutable Branch01 baseline seals 109/98/11 with baseline SHA-256
`bd0d44440b6b7f8f2db6f3fc200ee32d74d3612a813f792e40e2c8a69b75eabc`.
A separate T0 claim of
98/97/1 is not silently merged into that baseline; it needs its own sealed
artifact before it can be independently attested by this monitor.
An `unmatched` publisher row proves only its persisted status, not why its
V3 crosswalk was absent. Complete coverage of generated decisions also cannot
prove that a scheduler generated every due decision; Soak A remains
`INSUFFICIENT_EVIDENCE` when that independent evidence is unavailable.
