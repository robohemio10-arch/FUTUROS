# Branch 17: causal Paper A/B forward proof

The evaluator is research-only. It does not authorize orders, live/canary,
promotion, training, strategy or risk changes. Production sources and SQLite
databases are read in the active containers without starting the application,
calling exchange endpoints, restarting containers or rewriting evidence.

## Registration and parity

Run from the isolated implementation checkout:

```powershell
python scripts/build_canonical_treatment_causal_governance_v1.py --project-root . --runtime-root E:\FUTUROS --json
```

This materializes `data/reports/canonical_treatment/runtime_parity_audit_v1.json`
in the implementation checkout. Protected runtime trees cannot be output roots.
The audit inspects active container identities, process environment, commands,
mounts, database identities, config, strategies, support code and healthchecks.
It records active source Git identities and compose hashes, and validates the
certified V3 freeze and activation through their existing validator. Secrets and
raw configuration/environment values are not exported.

Economic parity compares only Control and Treatment Freqtrade: active image ID,
the mounted `SmartCryptoSignalStrategy.py`, economic arguments, safety flags and
normalized economic config. Literal strategy defaults resolve omitted settings;
remaining defaults are tied to the required identical active image. Pair sets
are normalized; unknown config differences and Freqtrade environment overrides
fail closed. Service-specific environment and publisher/monitor images are
instrumentation identities, not financial equality gates.

Allowed config differences are bot name, DB/log paths, validated Treatment
signal source/output paths, and API-server settings only when disabled or absent
in both arms. The strategy's actual `_signal_paths`, primary signals file,
readonly Control mount and isolated Treatment source are verified. Compose
sources come from active labels, including overrides. Missing files are read from
an exact Git HEAD blob when available, without restoring files or changing the
source checkout. Recovery identifies that blob, not necessarily the original
compose used at creation. Unrecoverable files remain explicit provenance warnings
(`compose_source_recovered=false`); a complete canonical live container spec is
required in their place. Private inspect values are hashed, not exported.
Warnings and spec hashes are preserved in the create-once manifest.
Active image IDs must match, or both verified local image records must share an
immutable RepoDigest and platform; tags never establish equivalence.
Reports separate economic parity, safety parity, runtime
identity, allowed differences, instrumentation differences and blockers.
Instrumentation still requires safe, healthy runtimes and a fresh publisher
report. PID1 environment is read directly; for the observer bootstrap only,
unreadable PID1 environment is explicitly attested by Docker inspect instead.

Only a PASS permits the same command with `--activate`. It publishes
`data/research/canonical_treatment/causal_activation_manifest_v1.json` in this
checkout using the existing exclusive V3 guard, fsync and an atomic no-clobber
hard link. A stale guard requires review. Existing manifests are validated and
never rewritten, including invalid manifests. The formal activation is the
actual current UTC time; the originating parity audit is sealed in the manifest.
All earlier data is excluded from the causal cohort. Runtime drift requires a
new governance decision, not editing an existing registration.
The manifest requires `pre_activation_rows_classification=smoke_pre_registration`
and `exact_join_required=true`. B17 is run only after successful registration.

## Evidence and decisions

```powershell
python scripts/build_economic_phase_final_forward_proof_v1.py --project-root . --runtime-root E:\FUTUROS --json
```

The CLI writes only stdout. It consumes the saved audit, rechecks the active
runtimes, and reads the operational Decision Ledger V4.2, canonical V3 store,
Treatment ledger and both actual Freqtrade databases. SQLite is opened with
`mode=ro` and `query_only`. No historical rows are migrated or reconstructed.

The population is sealed Phase13 operational ALLOW decisions at or after formal
activation, irrespective of whether Treatment observed them. Crosswalk event IDs
and payload hashes, sealed V3 decisions, signal/candidate/correlation identities,
Treatment selection and trade `enter_tag` must match exactly. Treatment selection
and V3 observation must precede trade opening. A causal operational event missing
from Treatment counts as a coverage miss. No nearest-time or approximate join
exists. Duplicate identical operational records are idempotent; conflicting
identities and duplicate financial rows block evaluation.

Financial evidence uses separate Control and Treatment `close_profit_abs` values.
An additional 5 bps stress is charged once to leveraged stake for each closed
trade in both arms. A paired decision resolves only after signal expiry and all
linked trades close, with a Control trade and, when selected, a Treatment trade.
Open or unexecuted decisions remain unresolved. No hypothetical selected-Control
PnL is substituted for Treatment results.

Required gates remain 45 days, 200 resolved decisions, 50 selected closed trades,
99% scorer coverage, positive stressed Treatment net PnL and expectancy, profit
factor at least 1.10, positive paired delta and paired bootstrap CI95 lower bound,
and no unexplained coverage gaps. Bootstrap uses 5,000 resamples and seed 42.
With no losing trades profit factor remains undefined; it is not a numeric PASS.
Materialized B15/B16 reports and their seals are required; missing evidence remains
explicitly waiting, and corrupted or blocked upstream evidence blocks evaluation.

Insufficient time/sample/upstream evidence returns `waiting`,
`WAITING_FORWARD_EVIDENCE`, `certification_attempted=false`. Integrity failures
return `blocked`. Before registration there is no causal population to measure;
zero counters do not describe the historical smoke population. Successful
certification returns `FORWARD_PROOF_RESEARCH_ONLY` without any financial authority.

## Validation and limits

Focused tests cover no-clobber registration, lock/I/O failure, drift, stale audits,
unsafe configuration, historical exclusion, exact lineage, coverage denominator,
independent actual PnL, thresholds, pending upstreams and idempotence.

```powershell
python -m pytest tests/test_economic_phase_final_forward_proof_v1.py tests/test_canonical_treatment_causal_governance_v1.py tests/test_canonical_treatment_signal_publisher_v1.py -q
python scripts/generate_project_manifest.py
python scripts/generate_project_manifest.py --check
python scripts/scan_versioned_secrets.py --json
git diff --check
```

Runtime audits attest available bytes, process configuration and container
identities; they do not inspect Python heap objects. Source changes after process
start fail closed. A publisher without normal completed cycles cannot prove a
late-crosswalk race. Historical gaps remain unchanged, and a real publisher fix
requires a separate demonstrated reproduction before modifying its code.
