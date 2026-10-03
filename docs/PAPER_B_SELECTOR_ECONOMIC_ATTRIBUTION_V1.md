# Paper-B selector economic attribution V1

Research-only attribution uses the immutable Branch-01 PRE_FIX population,
the natural POST_FIX population, and Branch-02 exact coverage and funnel joins.
Historical misses remain unresolved even if observed later. Soak A/B states
are reported by Branch 02 and are not overridden by this analysis.

Only a closed Control trade opened after the exact V3 observation and before
`valid_until` can label ABSTAIN as good (negative Control PnL) or false
(positive Control PnL). Only a closed Treatment trade can label ALLOW as good
(positive Treatment PnL) or bad (negative Treatment PnL). Open, unlinked,
out-of-window, and zero-PnL outcomes receive no GOOD/BAD label. A closed
zero-PnL Treatment opportunity counts toward the selected-closed sample gate.
No Control result is substituted for missing Treatment execution.

`net_selector_value_usdt` is the sum of the four labeled contributions; it
is not the paired experiment delta. Control/Treatment net PnL, expectancy,
profit factor, and delta use only classified opportunities with an exact,
closed Control comparator. Profit factor is unavailable without gross losses.
The source is Freqtrade `close_profit_abs`, without invented cost adjustments.
Score buckets use only sign (`NEGATIVE`, `ZERO`, `POSITIVE`); an absent score
or `score_margin` is `UNAVAILABLE`. LONG and SHORT are measured, not authorized.

The CLI is no-write by default. `--write-report` writes only its own JSON under
`data/reports/canonical_treatment`. Example no-write invocation:

```powershell
python -B scripts/build_paper_b_selector_economic_attribution_v1.py --project-root E:\FUTUROS_BR05 --runtime-root E:\FUTUROS --json
```

Runtime parity, causal manifest, Branch-01 baseline, V3 crosswalk, Branch-02
coverage, exact trade identity, and before/after runtime identity are mandatory.
If any gate blocks, economic KPIs are unavailable rather than inferred.
Checkpoints at 10, 25, and 50 selected-closed opportunities are `PENDING_SAMPLE`
until naturally attained; this state is not a software failure.
