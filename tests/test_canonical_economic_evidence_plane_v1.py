from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from smartcrypto.research.canonical_economic_plane.orchestrator import (
    PlaneConfig,
    run_economic_evidence_cycle,
)


def _write(path: Path, content: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _sources(root: Path) -> PlaneConfig:
    data = root / "data"
    _write(data / "trades/trades_master.xlsx")
    _write(data / "trades/inbox/freqtrade_paper_closed_trades.csv")
    _write(data / "snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite")
    _write(data / "research/qlib/" / "official_trades_master_qlib_dataset_v1.parquet")
    _json(
        data / "research/qlib/" / "official_trades_master_qlib_feature_contract_v1.json",
        {},
    )
    _json(
        data / "research/qlib/" / "official_trades_master_qlib_dataset_manifest_v1.json",
        {},
    )
    _json(
        data / "research/qlib/" / "official_trades_master_qlib_split_manifest_v1.json",
        {},
    )
    _json(
        data / "reports/canonical_treatment/economic_monitor_v1.json",
        {
            "status": "ok",
            "experiment_started_at_utc": "2026-09-20T14:57:19Z",
            "scorer": {
                "candidate_decision_count": 4,
                "scored_decision_count": 4,
                "selected_decision_count": 1,
                "coverage": 1.0,
            },
            "sample": {
                "control_linked_trade_count": 2,
                "treatment_linked_trade_count": 1,
                "financially_resolved_decisions": 1,
                "selected_closed_trades": 1,
            },
            "paired_uplift": {
                "delta_net_pnl_total": 1.25,
                "delta_net_pnl_mean": 1.25,
            },
        },
    )
    return PlaneConfig(
        project_root=root,
        data_root=data,
        output_dir=data / "reports/canonical_economic_plane",
    )


def _builders() -> dict[str, Any]:
    def report(name: str, status: str = "ok") -> dict[str, Any]:
        return {
            "schema_version": f"{name}_v1",
            "status": status,
            "reason": f"{name}_{status}",
            "decision": "RESEARCH_ONLY",
        }

    return {
        "branch08": lambda **_: report("branch08"),
        "branch09": lambda **_: report("branch09", "waiting"),
        "branch10": lambda **_: report("branch10"),
        "branch11": lambda **_: report("branch11"),
        "branch12": lambda **_: report("branch12"),
        "branch13": lambda **_: report("branch13"),
        "branch14": lambda **_: report("branch14"),
        "branch15": lambda **_: report("branch15", "waiting"),
        "branch16": lambda **_: report("branch16", "waiting"),
    }


def test_cycle_writes_all_branch_reports_and_summary(tmp_path: Path) -> None:
    config = _sources(tmp_path)

    report = run_economic_evidence_cycle(
        config,
        write_reports=True,
        builders=_builders(),
        now=datetime(2026, 9, 21, 0, 0, tzinfo=UTC),
    )

    assert report["status"] == "ok"
    assert report["stage_count"] == 9
    assert report["stage_status_counts"] == {
        "blocked": 0,
        "ok": 6,
        "waiting": 3,
    }
    assert report["write_performed"] is True
    assert report["operational_authority"] is False
    assert report["sends_orders"] is False
    assert report["changes_risk"] is False
    assert report["canonical_treatment_runtime_overlay"]["status"] == "ok"
    assert (
        report["canonical_treatment_runtime_overlay"][
            "financially_resolved_decisions"
        ]
        == 1
    )
    assert report["branch17_forward_observation"]["certification_attempted"] is False

    output = config.output_dir
    assert (output / "canonical_economic_evidence_plane_v1.json").is_file()
    branch_files = sorted(output.glob("branch*.json"))
    assert len(branch_files) == 9


def test_missing_required_sources_fail_closed_without_authority(tmp_path: Path) -> None:
    config = PlaneConfig(
        project_root=tmp_path,
        data_root=tmp_path / "data",
        output_dir=tmp_path / "data/reports/canonical_economic_plane",
    )
    config.data_root.mkdir(parents=True)

    report = run_economic_evidence_cycle(
        config,
        write_reports=False,
        builders=_builders(),
        now=datetime(2026, 9, 21, 0, 0, tzinfo=UTC),
    )

    assert report["status"] == "ok"
    assert report["stages"]["branch08"]["status"] == "blocked"
    assert report["stages"]["branch10"]["status"] == "blocked"
    assert report["stages"]["branch11"]["status"] == "blocked"
    assert report["stages"]["branch12"]["status"] == "blocked"
    assert report["write_performed"] is False
    assert report["operational_authority"] is False
    assert report["order_submission_enabled"] is False
    assert report["real_order_submission_enabled"] is False


def test_optional_evidence_absence_is_not_invented(tmp_path: Path) -> None:
    config = _sources(tmp_path)
    seen: dict[str, object] = {}
    builders = _builders()

    def branch15(**kwargs: object) -> dict[str, Any]:
        seen["sleeve"] = kwargs["sleeve_evidence"]
        return {
            "status": "waiting",
            "reason": "existing_sleeve_oos_evidence_not_materialized",
            "decision": "WAITING_FOR_EXISTING_SLEEVE_OOS_EVIDENCE",
        }

    def branch16(**kwargs: object) -> dict[str, Any]:
        seen["council"] = kwargs["council_ab_evidence"]
        return {
            "status": "waiting",
            "reason": "paired_council_ab_evidence_not_materialized",
            "decision": "WAITING_FOR_COUNCIL_AB_EVIDENCE",
        }

    builders["branch15"] = branch15
    builders["branch16"] = branch16

    report = run_economic_evidence_cycle(
        config,
        write_reports=False,
        builders=builders,
        now=datetime(2026, 9, 21, 0, 0, tzinfo=UTC),
    )

    assert seen == {"sleeve": None, "council": None}
    assert report["stages"]["branch15"]["status"] == "waiting"
    assert report["stages"]["branch16"]["status"] == "waiting"


def test_branch14_receives_branch09_to_branch13_reports(tmp_path: Path) -> None:
    config = _sources(tmp_path)
    builders = _builders()
    captured: dict[str, object] = {}

    def branch14(**kwargs: object) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            "status": "ok",
            "reason": "scorecard_ready",
            "decision": "SCORECARD_READY_RESEARCH_ONLY",
        }

    builders["branch14"] = branch14

    run_economic_evidence_cycle(
        config,
        write_reports=False,
        builders=builders,
        now=datetime(2026, 9, 21, 0, 0, tzinfo=UTC),
    )

    assert set(captured) == {
        "branch09_report",
        "branch10_report",
        "branch11_report",
        "branch12_report",
        "ledger_report",
    }
    assert captured["branch09_report"]["status"] == "waiting"
    assert captured["ledger_report"]["status"] == "ok"


ROOT = Path(__file__).resolve().parents[1]


def test_compose_mounts_frozen_wq6_evidence_read_only() -> None:
    source = (ROOT / "docker-compose.canonical-economic-plane.yml").read_text(
        encoding="utf-8"
    )
    assert (
        "${FUTUROS_WQ6_EVIDENCE_ROOT}:/app/data/research/frozen_wq6:ro"
        in source
    )


def test_runtime_scripts_gate_wq6_and_blocked_stages_without_python_c() -> None:
    start = (
        ROOT / "scripts/start_canonical_economic_evidence_plane_v1.ps1"
    ).read_text(encoding="utf-8")
    validate = (
        ROOT / "scripts/validate_canonical_economic_evidence_plane_v1.ps1"
    ).read_text(encoding="utf-8")

    for digest in (
        "70ccb7f88aa281fc90ebbc77920b89340b688477ec29fbe6a373013d15213973",
        "fba5a285dc9ce554c6e0ea8e0be2f3ab1daca38052c4f0355f34f5f0e21550e9",
        "51d39a61893a9be1a70e8a92ef983b8b2366051ec03c6be96cfb5e3e00044f6e",
        "b9d5d4fc82c2dbbb5b7793d6adad357e24d10136dfcdc0c4bae5f96da0d715f6",
    ):
        assert digest in start

    assert "WQ6_EVIDENCE_HASH_MISMATCH" in start
    assert "ECONOMIC_PLANE_PREFLIGHT_BLOCKED_STAGES" in start
    assert "ConvertFrom-Json" in start
    assert "ECONOMIC_PLANE_BLOCKED_STAGES" in validate
    assert "ConvertFrom-Json" in validate
    assert "python -c" not in validate.lower()
