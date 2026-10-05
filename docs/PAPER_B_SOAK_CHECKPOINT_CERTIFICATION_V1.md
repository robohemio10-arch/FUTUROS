# Paper B Soak Checkpoint Certification V1

## Purpose

BR04 certifies whether natural post-fix Paper-B evidence has accumulated enough
sample and time to move to a separate economic-decision gate.

It does not certify economic edge, promote a selector, change a model, alter
risk, or modify the experiment.

BR04 consumes the BR03 economic attribution read-only. BR03 already carries
the exact BR01 baseline identity and the BR02 post-fix causal coverage funnel.

## Post-fix cohort is mandatory

BR04 never lets PRE_FIX history satisfy a forward soak gate.

The resolved and selected-closed counts are taken only from:

`attribution.by.epoch.POST_FIX`

The coverage funnel is already post-fix by construction.

The BR04 observation clock starts at the scheduler-fix deployment boundary
`fix_deployed_at_utc`, not at an earlier formal activation timestamp. This
prevents pre-remediation elapsed time from satisfying the post-fix soak gate.

## Canonical sample gates

The existing project prospective evidence gate defines:

- minimum observation period: 45 days;
- minimum resolved decisions: 200;
- minimum selected closed outcomes: 50;
- minimum scorer coverage: 0.99.

BR04 also requires at least 100 post-fix eligible decisions.

Intermediate post-fix milestones are reported at:

- eligible: 25, 50 and 100;
- resolved: 50, 100 and 200;
- selected closed: 10, 25 and 50.

A pending milestone is normal and is reported as `PENDING_SAMPLE`.

## Decision semantics

If the sample/time gates are incomplete:

`CONTINUE_NATURAL_COLLECTION`

When all post-fix sample gates pass:

`READY_FOR_SEPARATE_ECONOMIC_DECISION`

This is not an economic approval. BR04 always keeps:

- `economic_gate.evaluated=false`;
- `economic_edge_certified=false`;
- `promotion_allowed=false`;
- `operational_authority=false`.

Cost-stressed economics, bootstrap confidence and any promotion decision remain
a separate gate.

## Integrity

BR04 fails closed when:

- BR03 attribution is blocked;
- BR03 report hash is invalid;
- BR01 baseline identity differs;
- BR03 safety flags are unsafe;
- POST_FIX attribution is absent;
- causal-funnel counts are inconsistent;
- resolved count exceeds post-fix eligible count;
- selected-closed count exceeds selected count;
- scorer coverage disagrees with scored / eligible;
- report time predates the fix deployment boundary.

No fuzzy identity, nearest timestamp or historical backfill is introduced.

## Runtime and writes

The CLI is no-write by default:

```powershell
python -B scripts/build_paper_b_soak_checkpoint_certification_v1.py `
  --project-root E:\FUTUROS_PAPER_B_BR04 `
  --runtime-root E:\FUTUROS `
  --json
```

Optional `--write-report` may write only:

`data/reports/canonical_treatment/paper_b_soak_checkpoint_certification_v1.json`

The branch never writes SQLite, runtime state, models, registries, signals,
orders or risk configuration.

## Operational invariants

- paper only;
- shadow only;
- research only;
- read only;
- no live or canary authority;
- no private exchange access;
- no order submission;
- no RiskManager change;
- no strategy change;
- no model change;
- no threshold change;
- no stake or leverage change;
- no training;
- no automatic promotion.

RiskManager remains final authority and Freqtrade remains outside BR04's
decision authority.
