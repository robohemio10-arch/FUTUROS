# Paper Profitability Core V1

This branch removes directional inference from `score`: `prob_up` is the sole
direction authority, `score = 2 * prob_up - 1`, and RiskManager may only allow
or block the proposed side. The candidate regime and cooldown policies remain
research evidence until a scenario passes every OOS gate and receives human
review.

The approved Paper candidate profile is `0.55/0.45`, countertrend regime gate
enabled, zero-minute cooldown, no top-N authorization, and the existing Paper
Decision Ledger required. It replaces the Paper selection policy; the baseline
and the other thresholds remain offline research comparators only.

The evaluator reads the paper snapshot in SQLite read-only mode, reconstructs
net outcomes from price, quantity, fees and funding, and aligns the frozen model
to 5-minute features available before entry. It evaluates exactly 24 fixed
threshold/regime/cooldown scenarios through purged walk-forward folds. The
counterfactual outcome holds the observed exit timestamp and costs fixed; it is
research evidence, not a claim about executable fill paths.

Default execution writes nothing:

```powershell
python scripts/evaluate_paper_profitability_core_v1.py --project-root . --no-write --json
```

Static profile and BTC/ETH snapshot sanity preflight:

```powershell
python scripts/evaluate_paper_profitability_core_v1.py --project-root . --profile-preflight-only --json
```

`--write-report` may write only below `data/reports` (the default is
`data/reports/paper_profitability_core_v1.json`). It never updates Paper config,
models, registry, financial risk limits, live/canary state, or order submission.

The existing Paper Decision Ledger profile is enabled in preflight-only,
fail-closed mode. The branch does not start or restart Paper runtime. During a
controlled rollout, an invalid writer preflight or persistence failure blocks
signal publication; it never authorizes a trade by losing observability.
