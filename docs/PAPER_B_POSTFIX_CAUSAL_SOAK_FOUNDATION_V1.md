# Paper B Post-fix Causal Soak Foundation V1

## Purpose

The foundation freezes the causal boundary of the Paper-B scheduler remediation without changing the economic experiment.

It distinguishes immutable historical causal debt from natural observations generated after the remediation.

## Authoritative T_FIX

The runtime deployment boundary is the start time of the corrected canonical Qlib refresh supervisor container.

Authoritative Docker timestamp:

`2026-09-24T22:44:17.720829866Z`

Canonical contract timestamp, truncated to microsecond precision:

`2026-09-24T22:44:17.720829Z`

The deployment is causally bracketed by:

- last historical missing decision before deployment: `2026-09-24T22:40:14.153522Z`;
- first natural scored decision after deployment: `2026-09-24T22:45:37.073879Z`.

No timestamp is inferred from the verification directory name or from report-generation time.

## Deployment evidence

The external runtime evidence is:

`E:\FUTUROS_RUNTIME_OVERRIDES\br17-pit-schedule-20260924T174817Z\deployment_evidence.json`

The evidence records:

- raw Docker nanosecond start timestamp;
- canonical microsecond T_FIX;
- container/service/image identity;
- zero restart count after creation;
- exact causal bracketing;
- SHA256 of the original verification artifact;
- project and canonical-observer supervisor source hashes;
- explicit safety and no-economic-change semantics;
- canonical evidence SHA256 seal.

This evidence is external runtime evidence and is not versioned in Git.

## Verification snapshot semantics

The previously observed values:

- V3 signals: 903;
- V3 outcomes: 29;

belong to the post-deployment verification snapshot.

They are not claimed to be the counts that existed at the exact microsecond of T_FIX.

The baseline therefore stores them under `verification_snapshot`, separate from the store counts observed when baseline registration occurs.

## Historical causal debt

The baseline preserves the exact pre-fix missing `decision_event_id` set.

Current count-level classification:

- scheduler drift: 7;
- DNS: 2;
- unresolved event-level cause: 2;
- total historical misses: 11.

The root-cause classification is count-level unless event-specific evidence proves otherwise.

No cause is invented for an individual event.

Late scoring after T_FIX never erases historical debt.

## Pre/post-fix rule

Pre-fix:

`decision_timestamp < fix_deployed_at_utc`

Post-fix:

`decision_timestamp >= fix_deployed_at_utc`

A pre-fix event is considered scored at T_FIX only if both its exact V3 signal timestamp and Treatment first-observed timestamp precede T_FIX.

## Create-once baseline

Canonical runtime artifact:

`data/research/canonical_treatment/postfix_soak_baseline_v1.json`

The baseline is create-once and SHA256 sealed.

It records:

- T_FIX;
- formal B17 activation;
- canonical Qlib V3 identity;
- causal manifest hash;
- runtime parity audit hash;
- Git HEAD and working-tree fingerprint;
- project/canonical-observer supervisor source identity;
- external deployment evidence identity;
- verification snapshot identity;
- exact historical eligible/scored/missing event sets;
- historical root-cause summary;
- safety invariants.

A different T_FIX cannot replace an existing valid baseline.

A tampered baseline fails closed.

## Integrity rules

The foundation blocks on:

- historical population added after registration;
- historical population removed after registration;
- baseline scored event regression;
- causal activation change;
- Qlib V3 identity change;
- source mismatch between project and canonical observer;
- source identity change after T_FIX;
- invalid deployment evidence;
- invalid baseline schema/hash/safety;
- fuzzy/nearest timestamp reconstruction.

## Safety

The branch does not change:

- Qlib V3 model;
- frozen threshold;
- ALLOW/ABSTAIN policy;
- RiskManager;
- strategy;
- stake;
- leverage;
- ROI;
- stop-loss;
- active model;
- live/canary authority;
- order submission.

Historical backfill, fuzzy identity and nearest-timestamp matching remain forbidden.

## Branch boundary

Branch 01 implements only:

- authoritative T_FIX;
- deployment evidence;
- immutable historical debt;
- create-once baseline;
- source fingerprints;
- exact pre/post-fix partition;
- eligible/scored/miss counters.

Detailed latency distributions and Soak A/B certification remain Branch 02 work.
