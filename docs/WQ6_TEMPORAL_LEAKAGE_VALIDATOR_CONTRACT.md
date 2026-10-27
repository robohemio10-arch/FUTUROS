# Temporal leakage validation contract

This research-only correction hardens validation without changing the canonical
WQ6 dataset, features, labels, split membership or nine membership hashes.
It grants no PIT certification, training permission or operational authority.

## Boundaries and counters

The audit checks train/validation, train/test and validation/test independently.
For each boundary:

- `*_overlap_count` counts shared row indices.
- `*_label_interval_overlap_count` counts intersecting cross-role information
  interval pairs, using inclusive `[open_time_utc, close_time_utc]` endpoints.
- Duplicate authoritative identities across roles block the audit even when
  indices and timestamps differ. `identity_column` defaults to `order_id` for
  WQ6; the financial research consumer explicitly supplies its existing
  `estimate_subject_id`. No identifier is inferred, repaired or synthesized.
  The existing duplicate counter retains its name and counts the configured
  identity; the report exposes `identity_column` to make that scope explicit.

Existing fields remain present. `temporal_overlap_count` aggregates direct row
overlap across all three boundaries. The previously incorrect
`label_interval_overlap_count` now aggregates actual interval pairs, rather than
repeating the row-index counter. New per-boundary fields make this distinction
explicit. Counts may differ from the number of distinct affected rows. Historical
reports are not rewritten; their counters retain the semantics of their producer.

Any nonzero direct, interval, embargo or duplicate-identity count blocks the
audit. Invalid evidence also blocks it and is listed in `validation_errors`.
An empty error list does not override a nonzero contamination counter.

## Embargo and purging

The existing train-vs-evaluation purge algorithm and memberships are preserved.
A contaminated validation/test boundary is rejected, not silently repartitioned.
The embargo predicate is still:

```text
evaluation.close < earlier_role.open <= evaluation.close + embargo_seconds
```

It is checked for train against validation/test and validation against test.
It is not a requirement for a 24-hour gap before validation or test. Its audit
counter counts pairs, whereas the split engine's removal counter counts unique
training rows. A forward-only split can legitimately remove zero embargo rows.

## Invalid evidence

Timestamps must be explicit ISO-8601 strings with offsets or aware datetime
values. They are compared as UTC instants in a copy, without changing source
values. Missing, naive, numeric, ambiguous, unrepresentable and reversed
intervals fail closed. Original raw timestamps are checked before preparation
could interpret naive values as UTC.

Frame indices must be unique integers; membership lists must be nonempty,
contain existing integer indices, and contain no duplicates or booleans.
Partition chronology must follow train, validation, test. Stable identities
must be nonempty exact strings without surrounding whitespace. Missing
identity is a blocker, not a reason to infer another identifier.

## Historical inputs and authority

Shared historical candles in feature lookbacks are not automatically leakage.
These validators examine identity and label intervals; closed feature windows
and observed availability remain distinct checks. A cutoff equal to entry does
not attest receipt time. Clock custody and historical PIT provenance remain
unproven, with zero causally certified training rows.
