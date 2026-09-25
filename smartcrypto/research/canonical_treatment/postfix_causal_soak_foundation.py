"""Create-once foundation for Paper-B post-fix causal soak evidence."""

from __future__ import annotations

import hashlib
import json
import subprocess  # nosec B404 - bounded local git reads only
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from smartcrypto.learning.qlib_v3_prospective.activation import (
    parse_json,
    read_object,
)
from smartcrypto.learning.qlib_v3_prospective.contracts import (
    CANONICAL,
    EvidenceError,
    digest,
    utc,
)
from smartcrypto.learning.qlib_v3_prospective.store import exclusive
from smartcrypto.research.aibot_parity.economic_phase_final_forward_proof import (
    _causal_rows,
    _operational_population,
)
from smartcrypto.research.canonical_treatment import (
    causal_governance as governance,
)
from smartcrypto.research.canonical_treatment.publisher import (
    LEDGER_SCHEMA_VERSION,
)
from smartcrypto.runtime.integrity_traceability_v2 import atomic_write_json


SCHEMA_VERSION = "paper_b_postfix_causal_soak_foundation_v1"
BASELINE_SCHEMA_VERSION = "paper_b_postfix_soak_baseline_v1"

DEFAULT_BASELINE_RELATIVE_PATH = Path(
    "data/research/canonical_treatment/postfix_soak_baseline_v1.json"
)

DEFAULT_REPORT_RELATIVE_PATH = Path(
    "data/reports/canonical_treatment/"
    "paper_b_postfix_causal_soak_foundation_v1.json"
)

SUPERVISOR_SOURCE_PATHS = (
    Path("smartcrypto/qlib_engine/paper_refresh_supervisor.py"),
    Path("scripts/run_qlib_paper_refresh_supervisor.py"),
)

SAFETY_FLAGS: dict[str, bool] = {
    "paper_only": True,
    "shadow_only": True,
    "research_only": True,
    "operational_authority": False,
    "historical_backfill_allowed": False,
    "nearest_timestamp_matching_allowed": False,
    "fuzzy_identity_matching_allowed": False,
    "changes_model": False,
    "changes_strategy": False,
    "changes_risk": False,
    "changes_leverage": False,
    "changes_stake": False,
    "changes_threshold": False,
    "sends_orders": False,
    "exchange_private_access": False,
    "writes_sqlite": False,
    "writes_runtime": False,
    "runs_training": False,
}


class PostfixSoakFoundationError(RuntimeError):
    """Stable fail-closed error for the post-fix foundation."""


@dataclass(frozen=True)
class Snapshot:
    generated_at_utc: str
    formal_activation_utc: str
    causal_manifest_sha256: str
    parity_audit_sha256: str
    eligible_event_times: Mapping[str, datetime]
    scored_rows: Mapping[str, Mapping[str, Any]]
    current_missing_event_ids: tuple[str, ...]
    decision_ledger_row_count: int
    v3_signal_count: int
    v3_outcome_count: int


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()

    with path.open("rb") as handle:
        for chunk in iter(
            lambda: handle.read(1024 * 1024),
            b"",
        ):
            hasher.update(chunk)

    return hasher.hexdigest()


def _resolve_under(
    root: Path,
    value: str | Path,
) -> Path:
    runtime_root = root.resolve(
        strict=False
    )

    logical = Path(value)

    candidate = (
        logical.resolve(
            strict=False
        )
        if logical.is_absolute()
        else (
            runtime_root
            / logical
        ).resolve(
            strict=False
        )
    )

    try:
        candidate.relative_to(
            runtime_root
        )
    except ValueError as exc:
        raise PostfixSoakFoundationError(
            "artifact_path_outside_runtime_root"
        ) from exc

    return candidate


def _git(
    root: Path,
    *args: str,
    binary: bool = False,
) -> str | bytes:
    completed = subprocess.run(  # nosec B603
        ["git", *args],
        cwd=root,
        check=False,
        capture_output=True,
        text=not binary,
    )

    if completed.returncode != 0:
        raise PostfixSoakFoundationError(
            "git_read_failed:" + args[0]
        )

    return completed.stdout


def git_worktree_identity(
    project_root: Path,
) -> dict[str, Any]:
    head = str(
        _git(
            project_root,
            "rev-parse",
            "HEAD",
        )
    ).strip()

    raw = _git(
        project_root,
        "status",
        "--porcelain=v1",
        "-z",
        binary=True,
    )

    if not isinstance(raw, bytes):
        raise PostfixSoakFoundationError(
            "git_status_binary_required"
        )

    entries = raw.decode(
        "utf-8",
        errors="strict",
    ).split("\0")

    changes: list[dict[str, Any]] = []

    for entry in entries:

        if not entry:
            continue

        if len(entry) < 4:
            raise PostfixSoakFoundationError(
                "git_status_entry_invalid"
            )

        code = entry[:2]

        if "R" in code or "C" in code:
            raise PostfixSoakFoundationError(
                "git_rename_or_copy_requires_review"
            )

        relative = entry[3:].replace(
            "\\",
            "/",
        )

        path = project_root / relative

        changes.append(
            {
                "status": code,
                "path": relative,
                "sha256": (
                    _sha256_file(path)
                    if path.is_file()
                    else None
                ),
            }
        )

    changes.sort(
        key=lambda item: (
            item["path"],
            item["status"],
        )
    )

    payload = {
        "head": head,
        "changes": changes,
    }

    return {
        **payload,
        "working_tree_fingerprint": digest(
            payload
        ),
    }


def supervisor_source_identity(
    project_root: Path,
    observer_root: Path,
) -> dict[str, Any]:
    project: dict[str, str] = {}
    observer: dict[str, str] = {}

    for relative in SUPERVISOR_SOURCE_PATHS:

        project_path = project_root / relative
        observer_path = observer_root / relative

        if not project_path.is_file():
            raise PostfixSoakFoundationError(
                "project_source_missing:"
                + relative.as_posix()
            )

        if not observer_path.is_file():
            raise PostfixSoakFoundationError(
                "observer_source_missing:"
                + relative.as_posix()
            )

        project_hash = _sha256_file(
            project_path
        )

        observer_hash = _sha256_file(
            observer_path
        )

        project[relative.as_posix()] = (
            project_hash
        )

        observer[relative.as_posix()] = (
            observer_hash
        )

        if project_hash != observer_hash:
            raise PostfixSoakFoundationError(
                "canonical_observer_source_mismatch:"
                + relative.as_posix()
            )

    body = {
        "project": project,
        "canonical_observer": observer,
    }

    return {
        **body,
        "source_identity_sha256": digest(
            body
        ),
    }


def collect_runtime_snapshot(
    project_root: Path,
    runtime_root: Path,
) -> Snapshot:
    now = governance.now_utc()

    parity = read_object(
        project_root / governance.AUDIT_PATH
    )

    governance.validate_audit(
        parity,
        now,
    )

    current = governance.audit_snapshots(
        governance.collect_runtime(
            runtime_root
        ),
        git=parity["git"],
    )

    governance.validate_audit(
        current,
        governance.now_utc(),
    )

    if (
        current["fingerprints_sha256"]
        != parity["fingerprints_sha256"]
    ):
        raise EvidenceError(
            "runtime_changed_since_materialized_audit"
        )

    manifest_path = (
        project_root
        / governance.MANIFEST_PATH
    )

    if not manifest_path.is_file():
        raise EvidenceError(
            "causal_activation_not_registered"
        )

    manifest = read_object(
        manifest_path
    )

    governance.validate_manifest(
        manifest,
        current,
        governance.now_utc(),
    )

    boundary = utc(
        manifest["formal_activation_utc"]
    )

    publisher = governance.CONTAINERS[
        "publisher"
    ]

    raw = governance.container_read(
        publisher,
        "/app/data/runtime/"
        "decision_ledger_paper_v1/"
        "decision_ledger_v4_2.jsonl",
    )

    records = [
        parse_json(line)
        for line in raw.splitlines()
        if line.strip()
    ]

    population = _operational_population(
        records,
        boundary,
        governance.now_utc(),
    )

    evidence = governance.container_json(
        publisher,
        "/app/data/research/"
        "qlib_v3/prospective_evidence/"
        + CANONICAL.epoch_id
        + "/"
        + CANONICAL.activation_sha256
        + "/evidence.json",
    )

    ledger = governance.container_json(
        publisher,
        "/app/data/research/"
        "canonical_treatment/"
        "decision_ledger_v1.json",
    )

    if (
        ledger.get("schema_version")
        != LEDGER_SCHEMA_VERSION
    ):
        raise EvidenceError(
            "treatment_ledger_schema_invalid"
        )

    rows = ledger.get("rows")

    if not isinstance(rows, list):
        raise EvidenceError(
            "treatment_ledger_rows_invalid"
        )

    signals = evidence.get("signals")
    outcomes = evidence.get("outcomes")

    if (
        not isinstance(signals, list)
        or not isinstance(outcomes, list)
    ):
        raise EvidenceError(
            "v3_store_records_invalid"
        )

    scored, misses = _causal_rows(
        population,
        evidence,
        ledger,
        boundary,
        governance.now_utc(),
    )

    if (
        len(scored)
        + len(misses)
        != len(population)
    ):
        raise EvidenceError(
            "causal_population_partition_invalid"
        )

    eligible_times = {
        event_id: record.decision_timestamp
        for event_id, record
        in population.items()
    }

    return Snapshot(
        generated_at_utc=_iso(
            governance.now_utc()
        ),
        formal_activation_utc=_iso(
            boundary
        ),
        causal_manifest_sha256=str(
            manifest["manifest_sha256"]
        ),
        parity_audit_sha256=str(
            current["audit_sha256"]
        ),
        eligible_event_times=eligible_times,
        scored_rows=scored,
        current_missing_event_ids=tuple(
            sorted(misses)
        ),
        decision_ledger_row_count=len(
            rows
        ),
        v3_signal_count=len(
            signals
        ),
        v3_outcome_count=len(
            outcomes
        ),
    )


def partition_snapshot(
    snapshot: Snapshot,
    fix_deployed_at: datetime,
) -> dict[str, Any]:
    fix = fix_deployed_at.astimezone(
        UTC
    )

    pre_eligible = sorted(
        event
        for event, timestamp
        in snapshot.eligible_event_times.items()
        if timestamp < fix
    )

    post_eligible = sorted(
        event
        for event, timestamp
        in snapshot.eligible_event_times.items()
        if timestamp >= fix
    )

    pre_scored_at_fix: list[str] = []

    current_scored = set(
        snapshot.scored_rows
    )

    for event in pre_eligible:

        row = snapshot.scored_rows.get(
            event
        )

        if row is None:
            continue

        observed = utc(
            row.get(
                "first_observed_at_utc"
            )
        )

        signal_at = utc(
            row.get(
                "signal_timestamp_utc"
            )
        )

        if (
            observed < fix
            and signal_at < fix
        ):
            pre_scored_at_fix.append(
                event
            )

    pre_scored_set = set(
        pre_scored_at_fix
    )

    pre_missing_at_fix = sorted(
        set(pre_eligible)
        - pre_scored_set
    )

    post_scored = sorted(
        set(post_eligible)
        & current_scored
    )

    post_missing = sorted(
        set(post_eligible)
        - current_scored
    )

    late_resolved_historical = sorted(
        set(pre_missing_at_fix)
        & current_scored
    )

    return {
        "pre_fix": {
            "eligible": len(
                pre_eligible
            ),
            "scored": len(
                pre_scored_at_fix
            ),
            "misses": len(
                pre_missing_at_fix
            ),
            "eligible_event_ids": (
                pre_eligible
            ),
            "scored_event_ids": sorted(
                pre_scored_at_fix
            ),
            "missing_event_ids": (
                pre_missing_at_fix
            ),
        },
        "post_fix": {
            "eligible": len(
                post_eligible
            ),
            "scored": len(
                post_scored
            ),
            "misses": len(
                post_missing
            ),
            "eligible_event_ids": (
                post_eligible
            ),
            "scored_event_ids": (
                post_scored
            ),
            "missing_event_ids": (
                post_missing
            ),
            "coverage": (
                len(post_scored)
                / len(post_eligible)
                if post_eligible
                else None
            ),
        },
        "late_resolved_historical_event_ids": (
            late_resolved_historical
        ),
        "current_cumulative": {
            "eligible": len(
                snapshot.eligible_event_times
            ),
            "scored": len(
                snapshot.scored_rows
            ),
            "misses": len(
                snapshot.current_missing_event_ids
            ),
            "coverage": (
                len(snapshot.scored_rows)
                / len(
                    snapshot.eligible_event_times
                )
                if snapshot.eligible_event_times
                else None
            ),
        },
    }


def _baseline_body(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        key: value
        for key, value
        in payload.items()
        if key != "baseline_sha256"
    }


def _load_deployment_evidence(
    path: Path,
    expected_fix: datetime,
) -> dict[str, Any]:
    value = read_object(
        path
    )

    if (
        value.get("schema_version")
        != "paper_b_postfix_fix_deployment_evidence_v1"
    ):
        raise PostfixSoakFoundationError(
            "deployment_evidence_schema_invalid"
        )

    seal = value.get(
        "evidence_sha256"
    )

    if not isinstance(
        seal,
        str,
    ):
        raise PostfixSoakFoundationError(
            "deployment_evidence_seal_missing"
        )

    body = {
        key: item
        for key, item
        in value.items()
        if key != "evidence_sha256"
    }

    if digest(body) != seal:
        raise PostfixSoakFoundationError(
            "deployment_evidence_seal_invalid"
        )

    if utc(
        value.get(
            "fix_deployed_at_utc"
        )
    ) != expected_fix:
        raise PostfixSoakFoundationError(
            "deployment_evidence_fix_mismatch"
        )

    container = value.get(
        "container"
    )

    if not isinstance(
        container,
        Mapping,
    ):
        raise PostfixSoakFoundationError(
            "deployment_container_invalid"
        )

    if (
        container.get(
            "compose_service"
        )
        != "canonical-qlib-refresh-supervisor-paper"
        or container.get(
            "status"
        )
        != "running"
        or container.get(
            "restart_count"
        )
        != 0
    ):
        raise PostfixSoakFoundationError(
            "deployment_container_identity_invalid"
        )

    window = value.get(
        "causal_window"
    )

    if not isinstance(
        window,
        Mapping,
    ):
        raise PostfixSoakFoundationError(
            "deployment_causal_window_invalid"
        )

    for field in (
        "last_miss_lt_fix",
        "fix_lt_first_natural",
        "fix_inside_proven_window",
    ):
        if window.get(field) is not True:
            raise PostfixSoakFoundationError(
                "deployment_causal_window_not_proven:"
                + field
            )

    verification = value.get(
        "verification"
    )

    if not isinstance(
        verification,
        Mapping,
    ):
        raise PostfixSoakFoundationError(
            "deployment_verification_invalid"
        )

    if (
        verification.get(
            "predeploy_missing_decision_count"
        )
        != 11
        or (
            verification.get(
                "postfix_natural_scored_row_count"
            )
            or 0
        )
        <= 0
    ):
        raise PostfixSoakFoundationError(
            "deployment_verification_counts_invalid"
        )

    semantics = value.get(
        "semantics"
    )

    if not isinstance(
        semantics,
        Mapping,
    ):
        raise PostfixSoakFoundationError(
            "deployment_semantics_invalid"
        )

    if (
        semantics.get(
            "docker_started_at_is_runtime_source_of_truth"
        )
        is not True
        or semantics.get(
            "contract_timestamp_truncates_nanoseconds_to_microseconds"
        )
        is not True
        or semantics.get(
            "historical_backfill_performed"
        )
        is not False
    ):
        raise PostfixSoakFoundationError(
            "deployment_semantics_unsafe"
        )

    for field in (
        "threshold_changed",
        "model_changed",
        "strategy_changed",
        "risk_changed",
    ):
        if semantics.get(field) is not False:
            raise PostfixSoakFoundationError(
                "deployment_economic_semantics_changed:"
                + field
            )

    return value


def validate_baseline(
    payload: Mapping[str, Any],
) -> None:
    if (
        payload.get("schema_version")
        != BASELINE_SCHEMA_VERSION
    ):
        raise PostfixSoakFoundationError(
            "baseline_schema_invalid"
        )

    expected = digest(
        _baseline_body(
            payload
        )
    )

    if (
        payload.get("baseline_sha256")
        != expected
    ):
        raise PostfixSoakFoundationError(
            "baseline_hash_invalid"
        )

    if (
        payload.get("qlib_v3_identity")
        != CANONICAL.mapping()
    ):
        raise PostfixSoakFoundationError(
            "baseline_qlib_v3_identity_mismatch"
        )

    if (
        payload.get("safety")
        != SAFETY_FLAGS
    ):
        raise PostfixSoakFoundationError(
            "baseline_safety_invalid"
        )

    deployment = payload.get(
        "deployment_evidence"
    )

    if not isinstance(
        deployment,
        Mapping,
    ):
        raise PostfixSoakFoundationError(
            "baseline_deployment_evidence_invalid"
        )

    if (
        deployment.get(
            "fix_deployed_at_utc"
        )
        != payload.get(
            "fix_deployed_at_utc"
        )
    ):
        raise PostfixSoakFoundationError(
            "baseline_deployment_fix_mismatch"
        )

    for field in (
        "file_sha256",
        "evidence_sha256",
    ):
        value = deployment.get(
            field
        )

        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(
                char not in "0123456789abcdef"
                for char in value
            )
        ):
            raise PostfixSoakFoundationError(
                "baseline_deployment_sha_invalid:"
                + field
            )

    verification = payload.get(
        "verification_snapshot"
    )

    if not isinstance(
        verification,
        Mapping,
    ):
        raise PostfixSoakFoundationError(
            "baseline_verification_snapshot_invalid"
        )

    for field in (
        "v3_signal_count",
        "v3_outcome_count",
    ):
        value = verification.get(
            field
        )

        if (
            type(value) is not int
            or value < 0
        ):
            raise PostfixSoakFoundationError(
                "baseline_verification_count_invalid:"
                + field
            )

    pre = payload.get(
        "baseline_population"
    )

    if not isinstance(
        pre,
        Mapping,
    ):
        raise PostfixSoakFoundationError(
            "baseline_population_invalid"
        )

    eligible = pre.get(
        "eligible_event_ids"
    )

    scored = pre.get(
        "scored_event_ids"
    )

    missing = pre.get(
        "missing_event_ids"
    )

    if not all(
        isinstance(value, list)
        for value in (
            eligible,
            scored,
            missing,
        )
    ):
        raise PostfixSoakFoundationError(
            "baseline_event_sets_invalid"
        )

    if (
        len(eligible)
        != pre.get("eligible")
        or len(scored)
        != pre.get("scored")
        or len(missing)
        != pre.get("misses")
    ):
        raise PostfixSoakFoundationError(
            "baseline_counter_mismatch"
        )

    if (
        set(scored)
        | set(missing)
        != set(eligible)
        or set(scored)
        & set(missing)
    ):
        raise PostfixSoakFoundationError(
            "baseline_partition_invalid"
        )

    causes = payload.get(
        "historical_miss_root_cause_summary"
    )

    if not isinstance(
        causes,
        Mapping,
    ):
        raise PostfixSoakFoundationError(
            "historical_root_cause_summary_invalid"
        )

    total_causes = sum(
        int(
            causes.get(
                key,
                -1,
            )
        )
        for key in (
            "scheduler_drift",
            "dns",
            "unresolved",
        )
    )

    if (
        total_causes
        != len(missing)
    ):
        raise PostfixSoakFoundationError(
            "historical_root_cause_total_mismatch"
        )


def build_baseline(
    *,
    snapshot: Snapshot,
    fix_deployed_at_utc: str,
    git_identity: Mapping[str, Any],
    source_identity: Mapping[str, Any],
    deployment_evidence_path: Path,
    verification_v3_signal_count: int,
    verification_v3_outcome_count: int,
    scheduler_misses: int,
    dns_misses: int,
    unresolved_misses: int,
) -> dict[str, Any]:
    fix = utc(
        fix_deployed_at_utc
    )

    boundary = utc(
        snapshot.formal_activation_utc
    )

    now = utc(
        snapshot.generated_at_utc
    )

    if not (
        boundary
        < fix
        <= now
    ):
        raise PostfixSoakFoundationError(
            "fix_timestamp_outside_causal_window"
        )

    if not deployment_evidence_path.is_file():
        raise PostfixSoakFoundationError(
            "deployment_evidence_missing"
        )

    deployment = (
        _load_deployment_evidence(
            deployment_evidence_path,
            fix,
        )
    )

    if min(
        verification_v3_signal_count,
        verification_v3_outcome_count,
    ) < 0:
        raise PostfixSoakFoundationError(
            "verification_v3_counts_invalid"
        )

    if (
        snapshot.v3_signal_count
        < verification_v3_signal_count
    ):
        raise PostfixSoakFoundationError(
            "current_v3_signals_below_verification_snapshot"
        )

    if (
        snapshot.v3_outcome_count
        < verification_v3_outcome_count
    ):
        raise PostfixSoakFoundationError(
            "current_v3_outcomes_below_verification_snapshot"
        )

    if min(
        scheduler_misses,
        dns_misses,
        unresolved_misses,
    ) < 0:
        raise PostfixSoakFoundationError(
            "historical_root_cause_count_invalid"
        )

    partition = partition_snapshot(
        snapshot,
        fix,
    )

    pre = partition[
        "pre_fix"
    ]

    cause_total = (
        scheduler_misses
        + dns_misses
        + unresolved_misses
    )

    if (
        pre["misses"]
        != cause_total
    ):
        raise PostfixSoakFoundationError(
            "historical_miss_count_mismatch:"
            f"computed={pre['misses']}:"
            f"classified={cause_total}"
        )

    deployment_verification = (
        deployment[
            "verification"
        ]
    )

    if (
        deployment_verification[
            "predeploy_missing_decision_count"
        ]
        != pre["misses"]
    ):
        raise PostfixSoakFoundationError(
            "deployment_and_runtime_historical_miss_mismatch"
        )

    body: dict[str, Any] = {
        "schema_version": (
            BASELINE_SCHEMA_VERSION
        ),
        "created_at_utc": (
            snapshot.generated_at_utc
        ),
        "fix_deployed_at_utc": (
            _iso(fix)
        ),
        "formal_activation_utc": (
            snapshot.formal_activation_utc
        ),
        "causal_manifest_sha256": (
            snapshot.causal_manifest_sha256
        ),
        "parity_audit_sha256_at_registration": (
            snapshot.parity_audit_sha256
        ),
        "qlib_v3_identity": (
            CANONICAL.mapping()
        ),
        "git_identity": dict(
            git_identity
        ),
        "source_identity": dict(
            source_identity
        ),
        "deployment_evidence": {
            "path": str(
                deployment_evidence_path
            ),
            "file_sha256": (
                _sha256_file(
                    deployment_evidence_path
                )
            ),
            "evidence_sha256": (
                deployment[
                    "evidence_sha256"
                ]
            ),
            "docker_started_at_raw": (
                deployment[
                    "container"
                ][
                    "started_at_raw"
                ]
            ),
            "fix_deployed_at_utc": (
                deployment[
                    "fix_deployed_at_utc"
                ]
            ),
        },
        "verification_snapshot": {
            "source_path": (
                deployment_verification[
                    "path"
                ]
            ),
            "source_sha256": (
                deployment_verification[
                    "sha256"
                ]
            ),
            "recorded_at_utc": (
                deployment_verification.get(
                    "recorded_at_utc"
                )
            ),
            "v3_signal_count": (
                verification_v3_signal_count
            ),
            "v3_outcome_count": (
                verification_v3_outcome_count
            ),
            "predeploy_missing_decision_count": (
                deployment_verification[
                    "predeploy_missing_decision_count"
                ]
            ),
            "postfix_natural_scored_row_count": (
                deployment_verification[
                    "postfix_natural_scored_row_count"
                ]
            ),
            "temporal_semantics": (
                "counts_observed_in_post_deployment_"
                "verification_snapshot_not_claimed_"
                "as_counts_at_exact_t_fix"
            ),
        },
        "baseline_population": pre,
        "baseline_observed_store": {
            "decision_ledger_row_count_current_at_registration": (
                snapshot.decision_ledger_row_count
            ),
            "v3_signal_count_current_at_registration": (
                snapshot.v3_signal_count
            ),
            "v3_outcome_count_current_at_registration": (
                snapshot.v3_outcome_count
            ),
        },
        "historical_miss_root_cause_summary": {
            "scheduler_drift": (
                scheduler_misses
            ),
            "dns": dns_misses,
            "unresolved": (
                unresolved_misses
            ),
            "event_level_mapping_status": (
                "summary_reconciled_"
                "event_mapping_not_proven"
            ),
        },
        "historical_debt_missing_event_ids": (
            list(
                pre[
                    "missing_event_ids"
                ]
            )
        ),
        "postfix_semantics": {
            "pre_fix": (
                "decision_timestamp_lt_"
                "fix_deployed_at_utc"
            ),
            "post_fix": (
                "decision_timestamp_gte_"
                "fix_deployed_at_utc"
            ),
            "historical_backfill": (
                "forbidden"
            ),
            "historical_miss_debt_is_never_removed_by_late_resolution": (
                True
            ),
        },
        "safety": dict(
            SAFETY_FLAGS
        ),
    }

    body[
        "baseline_sha256"
    ] = digest(
        body
    )

    validate_baseline(
        body
    )

    return body


def register_baseline(
    path: Path,
    proposed: Mapping[str, Any],
) -> tuple[dict[str, Any], bool]:
    from smartcrypto.runtime.integrity_traceability_v2 import (
        AtomicWriteError,
        AtomicWritePolicy,
    )

    validate_baseline(
        proposed
    )

    target = path.resolve(
        strict=False
    )

    target.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    policy = AtomicWritePolicy.restricted(
        [target.parent],
        working_directory=target.parent,
    )

    with exclusive(target):

        if target.exists():

            existing = read_object(
                target
            )

            validate_baseline(
                existing
            )

            if (
                existing.get(
                    "fix_deployed_at_utc"
                )
                != proposed.get(
                    "fix_deployed_at_utc"
                )
            ):
                raise PostfixSoakFoundationError(
                    "baseline_exists_with_"
                    "different_fix_timestamp"
                )

            if (
                existing.get(
                    "baseline_sha256"
                )
                != proposed.get(
                    "baseline_sha256"
                )
            ):
                raise PostfixSoakFoundationError(
                    "baseline_exists_with_"
                    "different_content"
                )

            return existing, False

        try:
            atomic_write_json(
                target,
                dict(proposed),
                policy=policy,
                allow_nan=False,
            )
        except AtomicWriteError as exc:
            raise PostfixSoakFoundationError(
                "baseline_atomic_write_failed:"
                + exc.reason
            ) from exc

    registered = read_object(
        target
    )

    validate_baseline(
        registered
    )

    if (
        registered.get(
            "baseline_sha256"
        )
        != proposed.get(
            "baseline_sha256"
        )
    ):
        raise PostfixSoakFoundationError(
            "baseline_post_write_"
            "identity_mismatch"
        )

    return registered, True


def build_report(
    baseline: Mapping[str, Any],
    snapshot: Snapshot,
) -> dict[str, Any]:
    validate_baseline(
        baseline
    )

    fix = utc(
        baseline[
            "fix_deployed_at_utc"
        ]
    )

    partition = partition_snapshot(
        snapshot,
        fix,
    )

    pre = partition["pre_fix"]

    baseline_pre = baseline[
        "baseline_population"
    ]

    blockers: list[str] = []

    baseline_eligible = set(
        baseline_pre[
            "eligible_event_ids"
        ]
    )

    current_pre_eligible = set(
        pre[
            "eligible_event_ids"
        ]
    )

    new_historical = sorted(
        current_pre_eligible
        - baseline_eligible
    )

    missing_historical = sorted(
        baseline_eligible
        - current_pre_eligible
    )

    baseline_scored = set(
        baseline_pre[
            "scored_event_ids"
        ]
    )

    current_scored = set(
        snapshot.scored_rows
    )

    scored_regressions = sorted(
        baseline_scored
        - current_scored
    )

    if new_historical:
        blockers.append(
            "historical_population_"
            "added_after_fix"
        )

    if missing_historical:
        blockers.append(
            "historical_population_"
            "removed_after_fix"
        )

    if scored_regressions:
        blockers.append(
            "historical_scored_"
            "event_regressed"
        )

    if (
        snapshot.causal_manifest_sha256
        != baseline[
            "causal_manifest_sha256"
        ]
    ):
        blockers.append(
            "causal_manifest_"
            "changed_after_fix"
        )

    if (
        CANONICAL.mapping()
        != baseline[
            "qlib_v3_identity"
        ]
    ):
        blockers.append(
            "qlib_v3_identity_"
            "changed_after_fix"
        )

    post = partition[
        "post_fix"
    ]

    report: dict[str, Any] = {
        "schema_version": (
            SCHEMA_VERSION
        ),
        "generated_at_utc": (
            snapshot.generated_at_utc
        ),
        "status": (
            "blocked"
            if blockers
            else "ok"
        ),
        "reason": (
            blockers[0]
            if blockers
            else (
                "postfix_causal_soak_"
                "foundation_active"
            )
        ),
        "decision": (
            "BLOCKED_FOUNDATION"
            if blockers
            else (
                "SOAK_FOUNDATION_ACTIVE"
            )
        ),
        "baseline_sha256": (
            baseline[
                "baseline_sha256"
            ]
        ),
        "fix_deployed_at_utc": (
            baseline[
                "fix_deployed_at_utc"
            ]
        ),
        "formal_activation_utc": (
            baseline[
                "formal_activation_utc"
            ]
        ),
        "historical_debt": {
            "eligible": (
                baseline_pre["eligible"]
            ),
            "scored_at_fix": (
                baseline_pre["scored"]
            ),
            "misses_at_fix": (
                baseline_pre["misses"]
            ),
            "missing_event_ids": (
                baseline[
                    "historical_debt_missing_event_ids"
                ]
            ),
            "root_cause_summary": (
                baseline[
                    "historical_miss_root_cause_summary"
                ]
            ),
            "late_resolved_after_fix_event_ids": (
                partition[
                    "late_resolved_historical_event_ids"
                ]
            ),
        },
        "post_fix_counters": post,
        "b17_cumulative_counters": (
            partition[
                "current_cumulative"
            ]
        ),
        "integrity": {
            "new_historical_event_ids": (
                new_historical
            ),
            "missing_historical_event_ids": (
                missing_historical
            ),
            "historical_scored_regression_event_ids": (
                scored_regressions
            ),
            "historical_population_immutable": (
                not new_historical
                and not missing_historical
            ),
            "baseline_scored_monotonic": (
                not scored_regressions
            ),
            "backfill_detected": bool(
                new_historical
            ),
        },
        "blockers": blockers,
        "safety": dict(
            SAFETY_FLAGS
        ),
    }

    report[
        "foundation_report_sha256"
    ] = digest(
        report
    )

    return report


def run_foundation(
    *,
    project_root: str | Path,
    runtime_root: str | Path,
    observer_root: str | Path,
    baseline_path: str | Path = (
        DEFAULT_BASELINE_RELATIVE_PATH
    ),
    report_path: str | Path = (
        DEFAULT_REPORT_RELATIVE_PATH
    ),
    create_baseline: bool = False,
    fix_deployed_at_utc: (
        str | None
    ) = None,
    deployment_evidence_path: (
        str | Path | None
    ) = None,
    verification_v3_signal_count: (
        int | None
    ) = None,
    verification_v3_outcome_count: (
        int | None
    ) = None,
    scheduler_misses: int = 7,
    dns_misses: int = 2,
    unresolved_misses: int = 2,
    write_report: bool = False,
) -> dict[str, Any]:
    from smartcrypto.runtime.integrity_traceability_v2 import (
        AtomicWriteError,
        AtomicWritePolicy,
    )

    project = Path(
        project_root
    ).resolve(
        strict=False
    )

    runtime = Path(
        runtime_root
    ).resolve(
        strict=False
    )

    observer = Path(
        observer_root
    ).resolve(
        strict=False
    )

    baseline_file = _resolve_under(
        runtime,
        baseline_path,
    )

    report_file = _resolve_under(
        runtime,
        report_path,
    )

    def write_report_payload(
        payload: Mapping[str, Any],
    ) -> None:
        report_file.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        policy = AtomicWritePolicy.restricted(
            [report_file.parent],
            working_directory=runtime,
        )

        try:
            atomic_write_json(
                report_file,
                dict(payload),
                policy=policy,
                allow_nan=False,
            )
        except AtomicWriteError as exc:
            raise PostfixSoakFoundationError(
                "report_atomic_write_failed:"
                + exc.reason
            ) from exc

    try:
        snapshot = collect_runtime_snapshot(
            project,
            runtime,
        )

        git_identity = (
            git_worktree_identity(
                project
            )
        )

        source_identity = (
            supervisor_source_identity(
                project,
                observer,
            )
        )

        baseline_write_performed = (
            False
        )

        if baseline_file.exists():

            baseline = read_object(
                baseline_file
            )

            validate_baseline(
                baseline
            )

            if create_baseline:

                if (
                    fix_deployed_at_utc
                    is None
                ):
                    raise PostfixSoakFoundationError(
                        "existing_baseline_"
                        "requires_explicit_fix_timestamp"
                    )

                requested_fix = _iso(
                    utc(
                        fix_deployed_at_utc
                    )
                )

                if (
                    requested_fix
                    != baseline[
                        "fix_deployed_at_utc"
                    ]
                ):
                    raise PostfixSoakFoundationError(
                        "existing_baseline_"
                        "fix_timestamp_mismatch"
                    )

        else:

            if not create_baseline:
                raise PostfixSoakFoundationError(
                    "baseline_missing_"
                    "create_once_required"
                )

            if (
                fix_deployed_at_utc
                is None
            ):
                raise PostfixSoakFoundationError(
                    "fix_deployed_at_utc_required"
                )

            if (
                deployment_evidence_path
                is None
            ):
                raise PostfixSoakFoundationError(
                    "deployment_evidence_required"
                )

            if (
                verification_v3_signal_count
                is None
                or verification_v3_outcome_count
                is None
            ):
                raise PostfixSoakFoundationError(
                    "verification_v3_counts_required"
                )

            deployment_path = Path(
                deployment_evidence_path
            ).resolve(
                strict=False
            )

            proposed = build_baseline(
                snapshot=snapshot,
                fix_deployed_at_utc=(
                    fix_deployed_at_utc
                ),
                git_identity=git_identity,
                source_identity=source_identity,
                deployment_evidence_path=(
                    deployment_path
                ),
                verification_v3_signal_count=(
                    verification_v3_signal_count
                ),
                verification_v3_outcome_count=(
                    verification_v3_outcome_count
                ),
                scheduler_misses=(
                    scheduler_misses
                ),
                dns_misses=(
                    dns_misses
                ),
                unresolved_misses=(
                    unresolved_misses
                ),
            )

            (
                baseline,
                baseline_write_performed,
            ) = register_baseline(
                baseline_file,
                proposed,
            )

        result = build_report(
            baseline,
            snapshot,
        )

        if (
            source_identity.get(
                "source_identity_sha256"
            )
            != baseline.get(
                "source_identity",
                {},
            ).get(
                "source_identity_sha256"
            )
        ):
            result["status"] = "blocked"
            result["reason"] = (
                "supervisor_source_identity_"
                "changed_after_fix"
            )
            result["decision"] = (
                "BLOCKED_FOUNDATION"
            )

            blockers = list(
                result.get("blockers")
                or []
            )

            reason = (
                "supervisor_source_identity_"
                "changed_after_fix"
            )

            if reason not in blockers:
                blockers.append(
                    reason
                )

            result["blockers"] = (
                blockers
            )

        result["baseline_path"] = str(
            baseline_file
        )

        result["report_path"] = str(
            report_file
        )

        result[
            "baseline_write_performed"
        ] = baseline_write_performed

        result[
            "git_identity_current"
        ] = git_identity

        result[
            "source_identity_current"
        ] = source_identity

        result[
            "report_write_performed"
        ] = bool(
            write_report
        )

        result.pop(
            "foundation_report_sha256",
            None,
        )

        result[
            "foundation_report_sha256"
        ] = digest(
            result
        )

        if write_report:
            write_report_payload(
                result
            )

        return result

    except (
        EvidenceError,
        PostfixSoakFoundationError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        json.JSONDecodeError,
    ) as exc:

        reason = (
            str(exc)
            if isinstance(
                exc,
                (
                    EvidenceError,
                    PostfixSoakFoundationError,
                ),
            )
            else (
                "postfix_foundation_failed:"
                + type(exc).__name__
            )
        )

        result: dict[str, Any] = {
            "schema_version": (
                SCHEMA_VERSION
            ),
            "generated_at_utc": (
                datetime.now(
                    UTC
                ).isoformat()
            ),
            "status": "blocked",
            "reason": reason,
            "decision": (
                "BLOCKED_FOUNDATION"
            ),
            "baseline_path": str(
                baseline_file
            ),
            "report_path": str(
                report_file
            ),
            "baseline_write_performed": (
                False
            ),
            "report_write_performed": (
                False
            ),
            "safety": dict(
                SAFETY_FLAGS
            ),
        }

        result[
            "foundation_report_sha256"
        ] = digest(
            result
        )

        if write_report:
            try:
                result[
                    "report_write_performed"
                ] = True

                result.pop(
                    "foundation_report_sha256",
                    None,
                )

                result[
                    "foundation_report_sha256"
                ] = digest(
                    result
                )

                write_report_payload(
                    result
                )

            except (
                PostfixSoakFoundationError,
                OSError,
            ) as write_exc:

                result[
                    "report_write_performed"
                ] = False

                result[
                    "report_write_error"
                ] = str(
                    write_exc
                )

                result.pop(
                    "foundation_report_sha256",
                    None,
                )

                result[
                    "foundation_report_sha256"
                ] = digest(
                    result
                )

        return result
