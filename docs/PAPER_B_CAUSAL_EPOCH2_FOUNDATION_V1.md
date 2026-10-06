# Paper B Causal Epoch 2 Foundation V1

## Purpose

This branch is a recovery/epoch-rollover foundation, not a fifth stage of the original BR01-BR04 sequence.

The original Paper B cohort is preserved as Epoch 1. Epoch 2 is created only because the registered causal runtime no longer matches the current runtime fingerprint.

## Proven predecessor break

Epoch 1 remains immutable.

The recovery requires all of the following before a new registration can be created:

- the Epoch 1 baseline is valid and SHA-256 sealed;
- the Epoch 1 causal manifest is valid against its own registration audit;
- the Epoch 1 baseline points to that exact causal manifest;
- the current Paper A/B runtime parity audit is `PASS`;
- validating the Epoch 1 manifest against the current runtime fails specifically with `causal_runtime_drift`;
- the exact fingerprint diff contains a selector source change.

For the observed transition, the selector source changed after Epoch 1 registration, including the introduction of `admission_lineage_probe.py` and a changed `natural_producer.py` source hash. Container restarts and the Treatment host-config hash are recorded but are not used to erase or rewrite historical evidence.

## No retrospective repair

The branch never:

- edits or reseals the Epoch 1 causal manifest;
- edits or reseals the Epoch 1 post-fix baseline;
- backdates the Epoch 2 activation;
- carries Epoch 1 population into Epoch 2 counters;
- treats pre-activation rows as Epoch 2 evidence.

The first proven incompatible runtime timestamp is recorded as predecessor closeout evidence. The Epoch 2 formal activation timestamp is the fresh current runtime audit timestamp at create-once registration.

## Create-once registration

The Epoch 2 registration is one atomic research artifact containing:

- predecessor closeout;
- exact fingerprint differences;
- current parity-audited runtime fingerprints;
- Epoch 2 causal manifest;
- opening counters and hashes for operational ledger, Treatment ledger and Qlib V3 store;
- strict no-cross-epoch population policy;
- safety invariants.

Default relative registration path under an explicit external evidence root:

`paper_b_epoch_2/epoch_registration_v1.json`

The evidence root is intentionally explicit and may be placed under `E:\FUTUROS_LOCAL_CHECKPOINTS`. It is not versioned.

If a valid registration already exists, reruns are idempotent only when the current runtime remains causally equivalent to the Epoch 2 registration. A later incompatible change fails closed as `epoch2_runtime_drift`.

## Operational sequence

Before merge, run no-write only:

```powershell
python -B scripts/build_paper_b_causal_epoch2_foundation_v1.py `
  --project-root E:\FUTUROS_PAPER_B_E2_BR01 `
  --runtime-root E:\FUTUROS `
  --epoch1-project-root E:\FUTUROS_BR05 `
  --epoch1-baseline E:\FUTUROS\data\research\canonical_treatment\postfix_soak_baseline_v1.json `
  --evidence-root E:\FUTUROS_LOCAL_CHECKPOINTS\PAPER_B_EPOCH2 `
  --json
```

Expected decision before merge:

`READY_TO_REGISTER_EPOCH2`

Do not use `--register-epoch2` from a feature branch.

After the branch is merged and post-merge CI is green, run the same command from a worktree pinned to canonical `origin/dev`, adding:

`--register-epoch2`

That create-once action starts Epoch 2 prospectively from the registration timestamp. Data between the predecessor drift boundary and the new activation remains inter-epoch/unregistered evidence and is not counted toward Epoch 2 maturity gates.

## Safety

Paper/shadow/research only.

No model, threshold, strategy, RiskManager, stake, leverage, ROI, stop-loss, live/canary authority, exchange-private access or order submission is changed.

The branch does not run training and does not write SQLite or runtime state.

## Next branch

After Epoch 2 is registered, the next required branch is an epoch-aware monitoring integration. It must adapt the existing BR02-BR04 monitors to consume an explicit epoch registration/baseline and must forbid cross-epoch aggregation.
