# Paper Candidate → Trade Lineage Propagation V1

## Status

Branch: `codex/paper-candidate-trade-lineage-propagation-v1`.

Stage 1 is closed and validated locally in the canonical repository:

```text
STAGE1_FUNCTIONAL=PASS
STAGE1_REGRESSION=101_PASSED
STAGE1_MANIFEST=PASS
STAGE1_SCOPE=PASS
STAGE1_GIT_HYGIENE=PASS
```

Stage 2 extends the same isolated package with a strict
`research candidate -> concrete signal identity` materialization boundary.
It still does **not** touch the Paper publisher, Freqtrade strategy,
RiskManager, runtime writer, active signal file, model, registry, Qlib runtime,
AI Shadow runtime, or exchange integration.

## Problem proved by read-only diagnostics

The current research layer materializes candidate identities, but no
runtime bridge exists from those candidate identities to closed Paper trades.
The required prospective chain is:

```text
registry candidate_id
    -> research source_candidate_id
    -> research signal_candidate_id
    -> concrete per-signal signal_id / correlation_id
    -> deterministic decision_event_id
    -> explicit Paper correlation metadata
    -> real Paper trade_id
```

Historical reconstruction by fuzzy matching is prohibited.

## Existing producer contract and the Stage 2 boundary

`paper_ai_signal_candidate_producer_v1` currently emits research-only rows with:

```text
source_candidate_id
signal_candidate_id
symbol_scope
side_scope
regime_scope
signal_direction
signal_actionability
```

The research producer derives `source_candidate_id` from the registry
`candidate_id`, but its implementation also contains a legacy fallback
`registry-candidate-{index}` when the registry candidate ID is absent.
That fallback is not authoritative enough for Treatment lineage.

The producer's `signal_candidate_id` is a deterministic research identity. It
is **not** a concrete per-signal runtime identity and must never be silently
reused as `signal_id`.

Stage 2 therefore introduces an explicit proof/materialization boundary:

1. prove `research.source_candidate_id == registry.candidate_id`;
2. reject the legacy `registry-candidate-{index}` fallback pattern;
3. verify the research `signal_candidate_id` against the canonical producer V1
   deterministic-ID formula;
4. require an explicit concrete `signal_instance_id` supplied by the signal
   production boundary;
5. materialize a deterministic `signal_id` and `correlation_id` from only
   pre-execution occurrence fields;
6. propagate the verified registry candidate ID unchanged as `candidate_id`;
7. feed the resulting exact fields into the Stage 1 authoritative identity
   contract.

## Package

```text
smartcrypto/execution/paper_candidate_trade_lineage_propagation_v1/
    __init__.py
    contracts.py
    adapter.py
    mapper.py
```

The package reuses:

```text
decision_ledger_v4_2
decision_ledger_runtime_profile_v1
```

No second Decision/TradeLink schema is introduced.

## Stage 1 strict identity contract

`build_authoritative_signal_identity()` accepts only exact concrete signal
fields:

```text
candidate_id
signal_id
correlation_id
```

It never repairs or generates missing identity and never accepts
`source_candidate_id` / `signal_candidate_id` as silent aliases.

## Stage 2 research candidate proof

`build_research_candidate_reference()` accepts:

```text
research_candidate
registry_candidate
producer_id
```

It requires:

```text
research.source_candidate_id is present
research.signal_candidate_id is present
registry.candidate_id is present
research.source_candidate_id == registry.candidate_id
```

It rejects:

```text
registry-candidate-{index} legacy fallback
candidate mismatch
signal_candidate_id tampering
invalid signal_actionability
post-outcome fields in the research candidate payload
```

The research candidate remains a reference only. No runtime signal identity is
materialized by this function.

## Stage 2 concrete signal occurrence

A static research candidate is insufficient to create a concrete signal event.
The caller must provide a `ConcreteSignalOccurrenceV1` containing:

```text
producer_id
signal_instance_id
signal_timestamp_utc
pair
symbol
side
regime
occurrence_source_sha256
```

`signal_instance_id` is mandatory. It is the explicit event-instance key from
the future signal production boundary. Stage 2 never substitutes current wall
clock time, random UUID, trade ID, timestamp-nearest matching, pair, symbol, or
side for that key.

The occurrence timestamp must be timezone-aware UTC offset zero.

## Deterministic signal identity materialization

`materialize_concrete_signal_identity()` first proves the research-to-registry
candidate binding, then validates that the occurrence is inside the research
candidate's symbol/side/regime scope.

Runtime candidate identity:

```text
candidate_id = verified registry candidate_id
```

This propagation is exact and unchanged.

Concrete signal identity is deterministic over:

```text
producer_id
registry_candidate_id
research_signal_candidate_id
signal_instance_id
signal_timestamp_utc
pair
symbol
side
regime
occurrence_source_sha256
```

Separate domain-separated hashes produce:

```text
signal:<sha256-prefix>
correlation:<sha256-prefix>
```

These are authoritative materializer IDs, not fallback repairs. The same
occurrence produces the same IDs; a different explicit signal occurrence
produces different IDs while preserving the same candidate ID.

## Actionability gate

A research candidate with:

```text
signal_actionability=blocked
```

may be validated as a research reference, but it cannot create a concrete
signal identity.

Only:

```text
signal_actionability=research_observation_only
```

is eligible for isolated identity materialization in Stage 2.

This is not an operational release. The resulting identity remains
non-authoritative for trading behavior and is not published anywhere.

## Scope validation

Stage 2 rejects a concrete occurrence when:

```text
normalized pair != symbol
symbol not in symbol_scope
side not in side_scope
regime outside a non-empty regime_scope
signal_direction conflicts with side
producer_id differs from the materialization boundary
```

No approximate matching is allowed.

## Post-outcome contamination guard

The adapter rejects research candidate keys containing the forbidden patterns:

```text
label
target
outcome
pnl
profit
win_loss
future_return
future_ret
```

No realized outcome field participates in candidate proof or signal identity.
Registry payload may be fingerprinted for audit provenance, but registry
performance fields do not enter the concrete signal ID basis.

## Safety invariants

Both Stage 1 and Stage 2 remain isolated:

```text
prospective_only=true
historical_backfill_allowed=false
fuzzy_linkage_allowed=false
timestamp_only_matching_allowed=false
symbol_side_only_matching_allowed=false
trade_id_as_candidate_id_allowed=false

synthetic_candidate_id_allowed=false
synthetic_signal_id_allowed=false
synthetic_correlation_id_allowed=false
fallback_identity_generation_allowed=false
post_outcome_identity_inputs_allowed=false

registry_candidate_proof_required=true
explicit_signal_instance_key_required=true

deterministic_signal_materialization_allowed=true
publisher_touched=false
writer_invoked=false
writes_runtime=false
writes_sqlite=false
changes_strategy=false
changes_risk=false
changes_stake=false
changes_leverage=false
changes_model=false
sends_orders=false
exchange_private_access=false
live_release_allowed=false
canary_release_allowed=false
```

## Decision and trade-link projection remains Stage 1 behavior

After a concrete signal identity exists, Stage 1 can project it into the
existing Decision Ledger runtime-profile contracts.

`project_strict_decision()` prohibits identity override and generates no trade
ID.

`project_strict_trade_link()` inherits candidate/signal/correlation identity
from the sealed strict decision and accepts only the authoritative Paper trade
observation. The observation cannot override identity.

No publisher or runtime writer is involved in Stage 2.

## Explicit non-goals

- no publisher wiring;
- no runtime writer activation;
- no `enter_tag` modification yet;
- no active signal file write;
- no Decision Ledger runtime write;
- no historical backfill;
- no timestamp-nearest matching;
- no symbol/side heuristic linkage;
- no fuzzy matching;
- no `trade_id` as `candidate_id`;
- no random/UUID/wall-clock identity generation;
- no silent research-alias reinterpretation;
- no Financial AI modification;
- no Qlib output-contract modification;
- no Trader Master modification;
- no RiskManager modification;
- no strategy/stake/leverage modification;
- no live/canary enablement;
- no real order submission.

## Stage 2 acceptance

Focused target:

```powershell
python -m pytest tests/test_paper_candidate_trade_lineage_propagation_v1.py -q
```

Regression target:

```powershell
python -m pytest `
    tests/test_decision_ledger_runtime_profile_v1.py `
    tests/test_decision_ledger_runtime_integration_v1.py `
    tests/test_decision_ledger_paper_observability_wiring_v1.py `
    tests/test_paper_candidate_trade_lineage_propagation_v1.py `
    -q
```

Required Stage 2 properties:

```text
REGISTRY_CANDIDATE_PROOF=true
LEGACY_REGISTRY_FALLBACK_REJECTED=true
SIGNAL_CANDIDATE_ID_INTEGRITY=true
BLOCKED_RESEARCH_CANDIDATE_MATERIALIZATION=false
EXPLICIT_SIGNAL_INSTANCE_KEY_REQUIRED=true
CANDIDATE_ID_PROPAGATED_UNCHANGED=true
SIGNAL_CANDIDATE_ID_REUSED_AS_SIGNAL_ID=false
SIGNAL_ID_DETERMINISTIC=true
CORRELATION_ID_DETERMINISTIC=true
POST_OUTCOME_IDENTITY_INPUTS=false
RANDOM_IDENTITY=false
WALL_CLOCK_IDENTITY=false

PUBLISHER_TOUCHED=false
WRITER_INVOKED=false
WRITES_RUNTIME=false
CHANGES_RISK=false
SENDS_ORDERS=false
```

## Next stage

Only after Stage 2 passes should this branch approach a metadata-only Paper
publication boundary. That later stage must prove that adding explicit decision
correlation metadata does not change RiskManager decisions, stake, leverage,
strategy behavior, signal actionability, or baseline Paper execution when
lineage attribution fails.


## Stage 3A — Non-blocking Paper publication boundary

Stage 3A freezes the execution/evidence asymmetry before any publisher wiring is
changed.

The RiskManager-approved batch is the operational baseline. Decision Ledger
observability is allowed to add only a verified `decision_ledger` envelope.
It is never allowed to change pair, symbol, side, score, confidence, stake,
leverage, `risk_approved`, RiskManager evidence, or any other baseline field.

If observability is disabled, blocked, incomplete, count-mismatched, identity-
mismatched, or lacks the Stage-2 authoritative identity attestation, the result
is:

```text
RiskManager ALLOW
    -> Paper baseline signal preserved unchanged
    -> attribution_evidence_blocked=true
    -> publication_blocked_by_lineage=false
```

If the strict envelope is valid, the published signal is exactly:

```text
RiskManager-approved baseline signal
    + decision_ledger envelope
```

No other observability-generated fields are propagated.

Stage 3A remains pure and isolated. It does not call the publisher, does not
write runtime files, does not invoke a Decision Ledger writer, does not submit
orders, does not alter risk, and does not enable live/canary release.

The actual `signal_producer.py` wiring is deliberately deferred until the
Stage-3A boundary contract and regression suite are validated in the canonical
repository.

## Stage 3B — Publisher safety wiring

Stage 3B integrates the Stage-3A non-blocking publication boundary into
`smartcrypto/execution/signal_producer.py` without materializing new candidate
identity yet.

The execution ordering deliberately preserves the existing coordinator call
order for compatibility, but changes authority semantics:

```text
candidate batch
    -> observability preparation (evidence only, exception-contained)
    -> RiskManager(candidate batch directly)
    -> observability finalization (evidence only, exception-contained)
    -> non-blocking lineage publication boundary
    -> Paper signal files
```

The critical invariant is that `prepare_before_risk_manager()` no longer
supplies the object evaluated by `apply_risk_manager_gate()`. RiskManager
receives the operational `candidate_signals` batch directly.

A preparation exception is converted into blocked attribution evidence and
RiskManager evaluation continues. A finalization exception is also converted
into blocked attribution evidence. A Decision Ledger
`publication_blocked=True` no longer has authority to cancel a RiskManager
ALLOW. The Stage-3A boundary publishes the exact RiskManager-approved baseline
when lineage evidence is missing, invalid or blocked.

RiskManager failure/rejection remains fail-closed. Stage 3B does not weaken
that boundary.

Stage 3B adds `paper_lineage_publication` to the producer report so the
execution state and lineage/evidence state remain separately auditable.

Stage 3B does not:
- create candidate/signal/correlation identity;
- enable Decision Ledger writer configuration;
- change risk limits, stake, leverage or strategy policy;
- enable live or canary;
- submit orders or access private exchange APIs;
- backfill historical lineage.

Concrete identity materialization remains a separate Stage 3C concern.

## Stage 3C — Explicit prospective identity materialization in the producer

Stage 3C wires the Stage-2 materializer into the real signal producer while
preserving the Stage-3B non-blocking execution semantics.

A concrete prediction row is lineage-materializable only when it explicitly
carries:

```text
source_candidate_id
signal_candidate_id
signal_instance_id
signal_timestamp_utc
regime | market_regime
```

No candidate is inferred from symbol, side, score, threshold, list position or
timestamp proximity.

`source_candidate_id` + `signal_candidate_id` must exactly identify one row in
`paper_ai_signal_candidate_producer_v1.json`. `source_candidate_id` must then
exactly identify one authoritative registry candidate in
`paper_model_candidate_registry_gate_v1.json`.

Only after both proofs succeed is identity materialized:

```text
candidate_id   = verified registry candidate_id
signal_id      = deterministic concrete occurrence identity
correlation_id = deterministic concrete occurrence correlation identity
```

The research `signal_candidate_id` is never reused as runtime `signal_id`.

`signal_instance_id` and `signal_timestamp_utc` have no wall-clock fallback.
Missing or invalid provenance blocks lineage attribution for that signal but
leaves its operational payload unchanged for RiskManager evaluation.

The occurrence source hash is built only from explicit pre-execution
provenance and the concrete signal payload. Realized outcome fields are
rejected from lineage inputs.

The producer report adds:

```text
paper_candidate_lineage_materialization
```

Stage 3C does not activate research thresholds, promote candidates/models,
change risk/stake/leverage/strategy, activate a Decision Ledger writer, write
SQLite/Parquet lineage state, submit orders, enable live/canary, or backfill
historical trades.

## Stage 3D — Strict decision_event_id projection in memory

Stage 3D creates the next prospective lineage edge:

```text
candidate_id
    -> signal_id
    -> decision_event_id
```

The decision event is projected only after RiskManager approval and only when
the approved signal carries the Stage-3C authoritative identity attestation.

The projection is built with the existing
`project_strict_decision()` contract and therefore inherits exact
`candidate_id`, `signal_id` and `correlation_id` from the sealed authoritative
signal identity. Caller override of those fields and `trade_id` in the
decision are forbidden by the Stage-1 mapper.

Stage 3D does not use the legacy Decision Ledger writer. It constructs the
sealed decision record entirely in memory and publishes only a minimal
`decision_ledger` envelope:

```text
decision_event_id
decision_payload_sha256
candidate_id
signal_id
correlation_id
decision_timestamp
```

The source prediction row is resolved by exact
`source_candidate_id + signal_candidate_id + signal_instance_id` provenance
from the Stage-3C attestation. No symbol/side/timestamp-nearest discovery is
performed.

The following decision context must be explicit:

```text
feature_timestamp_utc | feature_timestamp
feature_contract_version
feature_hash
model_id
model_hash
alignment
ai_shadow_decision
regime | market_regime
```

Risk evidence is taken only from the RiskManager-approved signal:
`risk_checked_at_utc`, `risk_policy_id`, `risk_config_hash`,
`risk_reasons`, approved stake and leverage.

The runtime decision timestamp is captured after the RiskManager call and is
not an identity input.

If any required decision context is absent, duplicated, inconsistent or
invalid:

```text
strict decision projection = BLOCKED
decision_event_id          = NOT PUBLISHED
RiskManager-approved signal = UNCHANGED
Paper execution             = UNCHANGED
```

Batch publication of decision envelopes is all-or-nothing. Partial lineage is
not published.

The producer report adds:

```text
paper_candidate_strict_decision_projection
```

Stage 3D does not activate a writer, write runtime/SQLite state, change risk,
change models, submit orders, enable live/canary, or backfill history.

## Stage 3E-A — Existing Freqtrade enter_tag propagation proof

Stage 3E-A intentionally does not modify the Freqtrade strategy.

Repository inspection proved that
`freqtrade/user_data/strategies/SmartCryptoSignalStrategy.py` already carries
the Stage-3D decision envelope through the Paper entry path:

```text
active signal decision_ledger.decision_event_id
    -> _find_signal_for_pair()
    -> populate_indicators.smartcrypto_decision_event_id
    -> populate_entry_trend()
    -> _entry_tag(side, decision_event_id)
    -> smartcrypto_<side>|decision_event_id=<exact-id>
    -> Freqtrade trade.enter_tag
```

The strategy also carries `signal_id` and `correlation_id` into its internal
decision logging path. Entry direction remains controlled by the existing
`side == long|short` logic; Stage 3E-A introduces no new entry condition.

The existing read-only trade-link adapter already recognizes explicit
`decision_event_id=...` tokens in `enter_tag` and uses that identifier as the
correlation key. It does not perform time-nearest correlation.

Because the required propagation path is already present, changing the
strategy in this stage would create unnecessary execution risk. Stage 3E-A is
therefore an executable contract/audit only.

The focused contract verifies structurally that:

```text
decision_ledger.decision_event_id
    -> strategy payload
    -> dataframe lineage column
    -> _entry_tag()
    -> existing trade-link parser
```

It also verifies that the generated Stage-3D identifier syntax is compatible
with the existing `_EVENT_TAG` parser.

Stage 3E-A does not:
- change `SmartCryptoSignalStrategy.py`;
- change entry or exit conditions;
- change stake, leverage, stoploss or ROI;
- change RiskManager behavior;
- write the Decision Ledger;
- write SQLite lineage state;
- correlate by timestamp, symbol/side proximity or order similarity;
- enable live/canary or submit real orders.

The remaining Stage 3E-B concern is read-only closed-Paper-trade correlation:
an explicit `paper_trade_id` plus the exact `decision_event_id` recovered from
`enter_tag`, followed by strict trade-link projection only when the complete
strict decision projection is explicitly available.

## Stage 3E-B — Strict closed-Paper-trade projection, read-only

Stage 3E-B closes the software contract from the explicit Freqtrade
`enter_tag` correlation to a typed `paper_trade_id`, without enabling the
Decision Ledger writer.

New pure boundary:

```text
StrictDecisionProjectionV1
        +
closed Paper trade row
        |
        +-- id > 0
        +-- is_open == false
        +-- exact pair
        +-- exact long/short
        +-- UTC open_date
        +-- exactly one decision_event_id in enter_tag
        |
        v
exact decision_event_id equality
        |
        v
RuntimeTradeObservationInputV1
        |
        v
project_strict_trade_link()
        |
        v
StrictTradeLinkProjectionV1
```

The closed trade is never used to reconstruct candidate, signal, correlation
or decision identity. Those fields are inherited exclusively from the supplied
complete strict decision projection.

The module rejects post-execution identity overrides in the trade row:
`candidate_id`, `signal_id`, `correlation_id`, `decision_event_id` and
`parent_event_id`.

The source-row fingerprint is deterministic over only the authoritative
correlation slice consumed by this boundary:

```text
id
pair
is_short
is_open=false
open_date
enter_tag
```

No PnL, exit result, label or future outcome participates in identity.

### Durability boundary remains explicit

Stage 3E-B does **not** make historical trade linkage magically available.

The complete `StrictDecisionProjectionV1` must already be available to the
caller. `enter_tag` contains only the explicit `decision_event_id`; it is not
sufficient by itself to reconstruct the complete decision projection after a
restart or after the active signal has disappeared.

Therefore:

```text
software projection capability = implemented
historical reconstruction = forbidden
Decision Ledger persistence = still disabled
runtime writer activation = still disabled
```

A future durability/persistence decision, if ever authorized, is a separate
governance step and must not be smuggled into this branch.

Stage 3E-B does not:
- read SQLite itself;
- write SQLite;
- invoke the Decision Ledger writer;
- change Freqtrade entry/exit behavior;
- change RiskManager behavior;
- change stake, leverage, model or strategy;
- perform timestamp-nearest or symbol/side-only matching;
- fabricate historical lineage;
- enable live/canary or submit orders.

### Stage 3E-B R1 — delimiter-safe explicit event parser

The initial Stage 3E-B parser used a delimiter-consuming regular expression.
For adjacent tokens such as:

```text
smartcrypto_long
|decision_event_id=<id>
|decision_event_id=<id>
```

the first regex match consumed the shared `|` delimiter and the second token
could be skipped by `findall()`. The focused ambiguity test correctly exposed
this as a fail-open attribution defect.

R1 replaces that parser with deterministic `|` tokenization plus exact
`decision_event_id=` key recognition and identifier validation.

R1 invariants:

```text
zero event tokens      -> BLOCKED / missing
one valid event token  -> accepted
two or more tokens     -> BLOCKED / ambiguous
empty/invalid token    -> BLOCKED / invalid
```

No execution, RiskManager, Freqtrade strategy, persistence or writer logic is
changed by R1.
