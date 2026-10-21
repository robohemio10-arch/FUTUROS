# Freqtrade H04/H05 Consolidated Technical Closeout

## Scope and Baseline

Reference: `dev` at `c089208126d76699981f1010d53724dbd07751fc`.
This closes the Docker/Freqtrade investigation as
`TECHNICAL_INVESTIGATION_STATUS=COMPLETED_WITH_LIMITATIONS`, not institutional
certification. Results below consolidate the prior H04/H05 executions; they are
not new runtime observations. No further H04/H05 stage or observer is required
to close this investigation. The only implementation here is documentation and
its deterministic project manifest.

## H04 Image Identity and Chronology

| Evidence | Previously established result |
| --- | --- |
| Operational image | `freqtradeorg/freqtrade@sha256:87aa5c6d65359b34e9d99a0bb260a38c0efe0315253811e6f48c2afe8f278a6a`, Freqtrade 2026.6 |
| Versioned Paper Compose | `freqtradeorg/freqtrade:stable@sha256:7031bca43ed7668ebf421725dd5016acade6ef88b0771db3e08c96e6d19a42db`, Freqtrade 2026.8 |
| H04-R | Raw registry response hashes/digests verified; different indices, Linux/AMD64 manifests and config descriptors; eight of nine layer descriptors differ |
| H04-D | Container created before the September 2026 pin; the reason for retaining the old image was not proved |

`IMAGE_MANIFEST_PARITY=DIFFERENT` is established. Descriptor verification is not
a vendor signature, source-authority review or proof of functional equivalence.
The [pin policy](FREQTRADE_IMAGE_PIN_DIGEST_POLICY_V1.md) now distinguishes its
historical unpinned exception from the current versioned pin and observed drift.

## H04 Engine and Fixture Evidence

| Execution | Result and coverage |
| --- | --- |
| H04-E, installed Freqtrade 2026.6 | One real-engine F03 test passed, no failures/skips |
| H04-E, installed Freqtrade 2026.8 | One real-engine F03 test passed, no failures/skips |
| H04-G, duplicate full exit guard | 36 passed |
| H04-G, exit lifecycle hardening | 57 passed |
| H04-G, financial lifecycle reconciliation | 66 passed |

Both installed engines reproduced `close_rate=NULL` after `handle_cancel_exit`
following a full fill; order fill evidence remained present. F03 rejected the
inconsistent record. PASS means reproduction plus quarantine, not repair of the
engine behavior or certification of the historical financial population.

H04-G collected/passed 159 tests, with zero failures/skips, using Python 3.12.10
and pytest 9.0.3: 82 stub-behavior tests, 11 static-configuration tests and 66
real-reconciler fixture tests. They do not compare both engines functionally;
real-engine comparison remains limited to H04-E. H04-F could not execute its
behavioral pytest suites inside the images because pytest was absent; its
signature inspection did not establish general compatibility.

H04-G functional acceptance remains PASS. Cleanup of its external disposable
runner is still pending at closeout (`H04G_TEMP_CLEANUP=PENDING`); its
administrative closeout remains BLOCKED. No deletion or policy bypass is
authorized here. This does not invalidate the tests or block this technical
investigation's conclusion. A later absence check is required to confirm cleanup.

## H05 Source Boundary Findings

H05-B0 read and parsed installed source in both immutable local images using
isolated non-root disposable containers, without network, Paper mounts or engine
execution. Relevant source hashes were stable on reread. The common static chain:

`main -> Arguments -> start_trading -> Worker._init -> Configuration.get_config`
`-> FreqtradeBot -> Exchange -> Strategy -> validation -> init_db -> bot_start`.

Installed paths below are under `/freqtrade/freqtrade/`; line references apply
to both versions unless separated as 2026.6 / 2026.8:

- `configuration/load_config.py:80`: merges JSON files, later file wins; parent
  values override included files. `configuration/configuration.py:71` applies
  environment overrides, then CLI processing. `--dry-run` enforces true;
  `db_url` has CLI/default handling in methods at lines 161 and 145 respectively.
- `freqtradebot.py:96`: Exchange construction precedes Strategy loading at 100,
  consistency/schema validation at 103 and `init_db(self.config["db_url"])` at 107.
- `exchange/exchange.py:254/255`: early `dry_run` consumption;
  `create_order` at 1448/1464 reads it again for simulated versus real execution.
- `persistence/models.py:48,77`: `init_db` passes its URL argument to
  `create_engine`. This proves static argument flow, not the DB identity or
  configuration of the current operational process.
- Worker, Bot, Exchange, Strategy and Wallets share mutable configuration;
  later transformations and extension access exist. `Worker._reconfigure:214`
  reloads configuration and reconstructs the bot without requiring a new PID.
- `strategy/interface.py:210/217` invokes `bot_start` after relevant components
  were initialized; it is not a sufficient pre-consumption attestation hook.

Resolution/override, validation, Worker initialization, Exchange construction and
DB argument flow are compatible in the proven static scope, not generally
equivalent across the two releases. A loaded config is not necessarily validated,
final or immutable. Neither a static file nor a second config-resolution process
attests to the existing operational process.

```text
H05_BOUNDARY=PARTIAL
DRY_RUN_FINAL_BOUNDARY=UNPROVEN
DB_URL_FINAL_BOUNDARY=PROVEN
DB_URL_BOUNDARY_SCOPE=STATIC_ARGUMENT_TO_INIT_DB
LATE_CONFIG_MUTATIONS=PRESENT
NATIVE_ATTESTATION_HOOK=UNPROVEN
CURRENT_PROCESS_RETROACTIVE_ATTESTATION=UNAVAILABLE
EFFECTIVE_CONFIG_PROVEN=false
H05_OBSERVER_IMPLEMENTED=false
```

## Architecture Decision and Runtime Preservation

Do not implement H05-B, wrappers, monkeypatches, synthetic hooks or an assertion
of immutable configuration. No native hook satisfying the full contract was
proved. Startup instrumentation would require version-specific integration and
independent validation of mutation/reconfiguration behavior; without a concrete
operational need its execution-engine risk is unwarranted. This is a closed
investigation with limitations, not a new mandatory implementation backlog.

Preserve the previously observed 2026.6 runtime temporarily, without treating that
preservation as certification. No image migration, restart, deploy, operational
container inspection, financial-source read or configuration change is part of
this closeout. Trading, protective exits, risk, F03/F04/F05 and Epoch 2 registration
remain unchanged. No financial operation or order is authorized.

## Conditions for a Future Image Change

A separately authorized migration needs a concrete need, immutable registry and
platform identity, sufficient real-engine exit/lifecycle compatibility evidence,
consistent preservation/custody of financial evidence, a controlled rollback plan
and revalidation of effective configuration and causal/runtime parity. Existing
F03 quarantines must remain blocked. Do not infer approval from signatures,
fixture tests, an image pin, startup health or this document. Changing the image
does not itself resolve the reproduced `close_rate` behavior.

## Institutional Authority and Residual Limits

The project has one operator. F05 requires four distinct participants with
verifiable independence; `INSTITUTIONAL_REVIEWS` remains empty. Codex, another AI
agent or an automated process is not an independent institutional reviewer.
No requester assertion, fabricated reviewer or local technical artifact may
replace that authority. F05 remains unchanged; validated preflight would still
not authorize F04 execution. See the
[existing F05 contract](PAPER_FINANCIAL_SOURCE_AUTHORITY_PREFLIGHT_F05.md).

```text
F05_INSTITUTIONAL_REVIEW=BLOCKED
SOURCE_AUTHORITY_NOT_PROVEN
execution_authorized=false
f04_executed=false
PAPER_B_EPOCH2_CERTIFICATION=BLOCKED
```

Residual limits are external runner cleanup, missing independent provenance and
effective configuration, unavailable retroactive attestation, limited real-engine
compatibility coverage and unexecuted migration. Institutional certification,
financial readiness and technical investigation closure are separate outcomes.
No new study, financial audit or operational change is initiated by this closeout.
