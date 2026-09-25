from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from smartcrypto.learning.qlib_v3_prospective.contracts import digest
from smartcrypto.research.canonical_treatment.postfix_causal_soak_foundation import (
    PostfixSoakFoundationError,
    SAFETY_FLAGS,
    Snapshot,
    build_baseline,
    build_report,
    partition_snapshot,
    register_baseline,
    validate_baseline,
)


def _dt(
    minutes: int,
) -> datetime:
    return (
        datetime(
            2026,
            9,
            24,
            17,
            0,
            tzinfo=UTC,
        )
        + timedelta(
            minutes=minutes
        )
    )


def _snapshot(
    *,
    add_historical: bool = False,
) -> Snapshot:
    fix = _dt(
        60
    )

    eligible = {
        f"event-{index:02d}": _dt(
            index
        )
        for index in range(
            13
        )
    }

    eligible |= {
        "post-01": (
            fix
            + timedelta(
                minutes=5
            )
        ),
        "post-02": (
            fix
            + timedelta(
                minutes=10
            )
        ),
    }

    if add_historical:
        eligible[
            "event-late-historical"
        ] = (
            fix
            - timedelta(
                seconds=1
            )
        )

    scored = {
        "event-00": {
            "first_observed_at_utc": (
                _dt(1).isoformat()
            ),
            "signal_timestamp_utc": (
                _dt(1).isoformat()
            ),
        },
        "event-01": {
            "first_observed_at_utc": (
                _dt(2).isoformat()
            ),
            "signal_timestamp_utc": (
                _dt(2).isoformat()
            ),
        },
        "post-01": {
            "first_observed_at_utc": (
                fix
                + timedelta(
                    minutes=6
                )
            ).isoformat(),
            "signal_timestamp_utc": (
                fix
                + timedelta(
                    minutes=6
                )
            ).isoformat(),
        },
        "post-02": {
            "first_observed_at_utc": (
                fix
                + timedelta(
                    minutes=11
                )
            ).isoformat(),
            "signal_timestamp_utc": (
                fix
                + timedelta(
                    minutes=11
                )
            ).isoformat(),
        },
    }

    missing = sorted(
        set(eligible)
        - set(scored)
    )

    return Snapshot(
        generated_at_utc=(
            fix
            + timedelta(
                hours=1
            )
        ).isoformat(),
        formal_activation_utc=(
            _dt(-120).isoformat()
        ),
        causal_manifest_sha256=(
            "a" * 64
        ),
        parity_audit_sha256=(
            "b" * 64
        ),
        eligible_event_times=(
            eligible
        ),
        scored_rows=scored,
        current_missing_event_ids=(
            tuple(missing)
        ),
        decision_ledger_row_count=20,
        v3_signal_count=910,
        v3_outcome_count=31,
    )


def _deployment_evidence(
    tmp_path,
):
    path = (
        tmp_path
        / "deployment_evidence.json"
    )

    body = {
        "schema_version": (
            "paper_b_postfix_fix_"
            "deployment_evidence_v1"
        ),
        "evidence_role": (
            "authoritative_runtime_"
            "fix_boundary"
        ),
        "fix_deployed_at_utc": (
            _dt(60)
            .isoformat()
            .replace(
                "+00:00",
                "Z",
            )
        ),
        "container": {
            "name": "qlib",
            "compose_service": (
                "canonical-qlib-refresh-supervisor-paper"
            ),
            "started_at_raw": (
                _dt(60)
                .isoformat()
                .replace(
                    "+00:00",
                    "Z",
                )
            ),
            "status": "running",
            "restart_count": 0,
        },
        "causal_window": {
            "last_miss_lt_fix": True,
            "fix_lt_first_natural": True,
            "fix_inside_proven_window": True,
        },
        "verification": {
            "path": "verification.json",
            "sha256": "1" * 64,
            "recorded_at_utc": (
                _dt(70)
                .isoformat()
            ),
            "predeploy_missing_decision_count": 11,
            "postfix_natural_scored_row_count": 4,
        },
        "semantics": {
            "docker_started_at_is_runtime_source_of_truth": True,
            "contract_timestamp_truncates_nanoseconds_to_microseconds": True,
            "historical_backfill_performed": False,
            "threshold_changed": False,
            "model_changed": False,
            "strategy_changed": False,
            "risk_changed": False,
        },
    }

    body[
        "evidence_sha256"
    ] = digest(
        body
    )

    path.write_text(
        json.dumps(
            body,
            sort_keys=True,
            allow_nan=False,
        ),
        encoding="utf-8",
    )

    return path


def _baseline(
    tmp_path,
):
    return build_baseline(
        snapshot=_snapshot(),
        fix_deployed_at_utc=(
            _dt(60).isoformat()
        ),
        git_identity={
            "head": "c" * 40,
            "changes": [],
            "working_tree_fingerprint": (
                "d" * 64
            ),
        },
        source_identity={
            "project": {
                "a": "e" * 64
            },
            "canonical_observer": {
                "a": "e" * 64
            },
            "source_identity_sha256": (
                "f" * 64
            ),
        },
        deployment_evidence_path=(
            _deployment_evidence(
                tmp_path
            )
        ),
        verification_v3_signal_count=903,
        verification_v3_outcome_count=29,
        scheduler_misses=7,
        dns_misses=2,
        unresolved_misses=2,
    )


def test_partition_separates_pre_and_post_fix_without_backfill():
    result = partition_snapshot(
        _snapshot(),
        _dt(60),
    )

    assert (
        result["pre_fix"]["eligible"]
        == 13
    )

    assert (
        result["pre_fix"]["scored"]
        == 2
    )

    assert (
        result["pre_fix"]["misses"]
        == 11
    )

    assert (
        result["post_fix"]["eligible"]
        == 2
    )

    assert (
        result["post_fix"]["scored"]
        == 2
    )

    assert (
        result["post_fix"]["misses"]
        == 0
    )

    assert (
        result["post_fix"]["coverage"]
        == 1.0
    )


def test_build_baseline_reconciles_historical_misses(
    tmp_path,
):
    baseline = _baseline(
        tmp_path
    )

    assert (
        baseline[
            "baseline_population"
        ]["misses"]
        == 11
    )

    causes = baseline[
        "historical_miss_root_cause_summary"
    ]

    assert causes[
        "scheduler_drift"
    ] == 7

    assert causes["dns"] == 2

    assert causes[
        "unresolved"
    ] == 2

    assert (
        baseline["safety"]
        == SAFETY_FLAGS
    )

    validate_baseline(
        baseline
    )


def test_verification_counts_are_not_claimed_as_exact_fix_counts(
    tmp_path,
):
    baseline = _baseline(
        tmp_path
    )

    snapshot = baseline[
        "verification_snapshot"
    ]

    assert (
        snapshot[
            "v3_signal_count"
        ]
        == 903
    )

    assert (
        snapshot[
            "v3_outcome_count"
        ]
        == 29
    )

    assert (
        "not_claimed_as_counts_at_exact_t_fix"
        in snapshot[
            "temporal_semantics"
        ]
    )

    observed = baseline[
        "baseline_observed_store"
    ]

    assert (
        "v3_signal_count_at_fix_from_verification"
        not in observed
    )

    assert (
        "v3_outcome_count_at_fix_from_verification"
        not in observed
    )


def test_build_baseline_fails_when_classified_total_differs(
    tmp_path,
):
    with pytest.raises(
        PostfixSoakFoundationError,
        match=(
            "historical_miss_count_mismatch"
        ),
    ):
        build_baseline(
            snapshot=_snapshot(),
            fix_deployed_at_utc=(
                _dt(60).isoformat()
            ),
            git_identity={
                "head": "x"
            },
            source_identity={
                "source_identity_sha256": (
                    "y"
                )
            },
            deployment_evidence_path=(
                _deployment_evidence(
                    tmp_path
                )
            ),
            verification_v3_signal_count=903,
            verification_v3_outcome_count=29,
            scheduler_misses=7,
            dns_misses=2,
            unresolved_misses=1,
        )


def test_deployment_evidence_fix_mismatch_is_blocked(
    tmp_path,
):
    with pytest.raises(
        PostfixSoakFoundationError,
        match=(
            "deployment_evidence_fix_mismatch"
        ),
    ):
        build_baseline(
            snapshot=_snapshot(),
            fix_deployed_at_utc=(
                _dt(61).isoformat()
            ),
            git_identity={
                "head": "x"
            },
            source_identity={
                "source_identity_sha256": (
                    "y"
                )
            },
            deployment_evidence_path=(
                _deployment_evidence(
                    tmp_path
                )
            ),
            verification_v3_signal_count=903,
            verification_v3_outcome_count=29,
            scheduler_misses=7,
            dns_misses=2,
            unresolved_misses=2,
        )


def test_baseline_hash_is_immutable(
    tmp_path,
):
    baseline = _baseline(
        tmp_path
    )

    baseline[
        "baseline_population"
    ]["misses"] = 10

    with pytest.raises(
        PostfixSoakFoundationError,
        match="baseline_hash_invalid",
    ):
        validate_baseline(
            baseline
        )


def test_register_baseline_is_create_once_and_idempotent(
    tmp_path,
):
    path = (
        tmp_path
        / "baseline.json"
    )

    baseline = _baseline(
        tmp_path
    )

    first, wrote_first = (
        register_baseline(
            path,
            baseline,
        )
    )

    second, wrote_second = (
        register_baseline(
            path,
            baseline,
        )
    )

    assert wrote_first is True
    assert wrote_second is False

    assert (
        first["baseline_sha256"]
        == second["baseline_sha256"]
    )


def test_register_baseline_rejects_different_fix_timestamp(
    tmp_path,
):
    path = (
        tmp_path
        / "baseline.json"
    )

    baseline = _baseline(
        tmp_path
    )

    register_baseline(
        path,
        baseline,
    )

    changed = dict(
        baseline
    )

    changed[
        "fix_deployed_at_utc"
    ] = (
        _dt(61)
        .isoformat()
        .replace(
            "+00:00",
            "Z",
        )
    )

    changed[
        "deployment_evidence"
    ] = dict(
        changed[
            "deployment_evidence"
        ]
    )

    changed[
        "deployment_evidence"
    ][
        "fix_deployed_at_utc"
    ] = changed[
        "fix_deployed_at_utc"
    ]

    changed.pop(
        "baseline_sha256"
    )

    changed[
        "baseline_sha256"
    ] = digest(
        changed
    )

    with pytest.raises(
        PostfixSoakFoundationError,
        match=(
            "different_fix_timestamp"
        ),
    ):
        register_baseline(
            path,
            changed,
        )


def test_report_preserves_historical_debt_and_counts_postfix(
    tmp_path,
):
    report = build_report(
        _baseline(
            tmp_path
        ),
        _snapshot(),
    )

    assert (
        report["status"]
        == "ok"
    )

    assert (
        report[
            "historical_debt"
        ]["misses_at_fix"]
        == 11
    )

    assert (
        report[
            "post_fix_counters"
        ]["eligible"]
        == 2
    )

    assert (
        report[
            "post_fix_counters"
        ]["scored"]
        == 2
    )

    assert (
        report[
            "post_fix_counters"
        ]["misses"]
        == 0
    )

    assert (
        report[
            "integrity"
        ]["backfill_detected"]
        is False
    )


def test_report_blocks_new_pre_fix_population_after_registration(
    tmp_path,
):
    report = build_report(
        _baseline(
            tmp_path
        ),
        _snapshot(
            add_historical=True
        ),
    )

    assert (
        report["status"]
        == "blocked"
    )

    assert (
        "historical_population_added_after_fix"
        in report["blockers"]
    )

    assert (
        report[
            "integrity"
        ]["backfill_detected"]
        is True
    )
