from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from smartcrypto.learning.feature_source_fields_enrichment_contract.source_contract import (
    build_ai_feature_source_fields_enrichment_contract_v1,
    render_markdown,
)


SCRIPT = Path("scripts/build_ai_feature_source_fields_enrichment_contract_v1.py")


def test_classifies_forbidden_fields_correctly(tmp_path: Path) -> None:
    report = build_ai_feature_source_fields_enrichment_contract_v1(
        project_root=tmp_path,
        available_fields=[
            "quantity",
            "entry_price",
            "target_win",
            "outcome_label",
            "net_pnl",
            "future_ret_1",
            "expected_value_proxy",
            "roi_hit",
            "stoploss_hit",
            "time_exit",
            "profit_ratio",
            "win_loss",
        ],
    )

    assert report["status"] == "warning"
    assert report["forbidden_fields_used"] == []
    for field in (
        "target_win",
        "outcome_label",
        "net_pnl",
        "future_ret_1",
        "expected_value_proxy",
        "roi_hit",
        "stoploss_hit",
        "time_exit",
        "profit_ratio",
        "win_loss",
    ):
        assert field in report["forbidden_fields_present"]


def test_classifies_contemporary_allowed_fields(tmp_path: Path) -> None:
    report = build_ai_feature_source_fields_enrichment_contract_v1(
        project_root=tmp_path,
        available_fields=[
            "timestamp",
            "symbol",
            "pair",
            "side",
            "quantity",
            "base_amount",
            "contracts",
            "entry_price",
            "open",
            "rate",
            "notional",
            "quote_amount",
            "stake_amount",
            "cost",
        ],
    )

    allowed = report["allowed_source_fields"]
    assert report["status"] == "ok"
    assert "quantity" in allowed["feature_quantity"]
    assert "base_amount" in allowed["feature_quantity"]
    assert "notional" in allowed["feature_notional"]
    assert "entry_price" in allowed["feature_notional"]
    assert "timestamp" in allowed["context"]
    assert "symbol" in allowed["context"]
    assert report["can_derive_feature_notional"] is True
    assert report["can_derive_feature_quantity"] is True


def test_ambiguous_fields_require_review_and_are_not_allowed(tmp_path: Path) -> None:
    report = build_ai_feature_source_fields_enrichment_contract_v1(
        project_root=tmp_path,
        available_fields=["quantity", "entry_price", "volume", "position_size", "total_value", "fee_amount"],
    )

    allowed_flat = {
        field
        for fields in report["allowed_source_fields"].values()
        for field in fields
    }
    assert report["status"] == "warning"
    assert "ambiguous_source_fields_require_review" in report["warnings"]
    assert "volume" in report["ambiguous_fields_requires_review"]
    assert "position_size" in report["ambiguous_fields_requires_review"]
    assert "total_value" in report["ambiguous_fields_requires_review"]
    assert "fee_amount" in report["ambiguous_fields_requires_review"]
    assert allowed_flat.isdisjoint({"volume", "position_size", "total_value", "fee_amount"})


def test_returns_blocked_when_required_source_fields_are_missing(tmp_path: Path) -> None:
    report = build_ai_feature_source_fields_enrichment_contract_v1(
        project_root=tmp_path,
        available_fields=["symbol", "side", "timestamp"],
    )

    assert report["status"] == "blocked"
    assert report["can_derive_feature_notional"] is False
    assert report["can_derive_feature_quantity"] is False
    assert "feature_quantity: amount|quantity|base_amount|contracts" in report["missing_required_source_fields"]
    assert "feature_notional: stake_amount|notional|cost|quote_amount OR quantity+price" in report[
        "missing_required_source_fields"
    ]


def test_no_write_default_does_not_materialize_reports(tmp_path: Path) -> None:
    report = build_ai_feature_source_fields_enrichment_contract_v1(
        project_root=tmp_path,
        available_fields=["quantity", "entry_price"],
    )

    assert report["write_requested"] is False
    assert report["write_performed"] is False
    assert not (tmp_path / "data" / "reports" / "ai_feature_source_fields_enrichment_contract_v1.json").exists()
    assert not (tmp_path / "data" / "reports" / "ai_feature_source_fields_enrichment_contract_v1.md").exists()
    assert not list(tmp_path.rglob("*.parquet"))
    assert not list(tmp_path.rglob("*.sqlite"))


def test_write_writes_only_json_and_markdown_under_data_reports(tmp_path: Path) -> None:
    report = build_ai_feature_source_fields_enrichment_contract_v1(
        project_root=tmp_path,
        available_fields=["quantity", "entry_price"],
        write_report=True,
    )
    json_report = tmp_path / "data" / "reports" / "ai_feature_source_fields_enrichment_contract_v1.json"
    markdown_report = tmp_path / "data" / "reports" / "ai_feature_source_fields_enrichment_contract_v1.md"

    assert report["write_requested"] is True
    assert report["write_performed"] is True
    assert json_report.is_file()
    assert markdown_report.is_file()
    assert not (tmp_path / "data" / "runtime").exists()
    assert not (tmp_path / "data" / "models").exists()
    assert not list(tmp_path.rglob("*.parquet"))
    assert not list(tmp_path.rglob("*.sqlite"))


def test_safety_flags_disable_runtime_model_registry_and_orders(tmp_path: Path) -> None:
    report = build_ai_feature_source_fields_enrichment_contract_v1(
        project_root=tmp_path,
        available_fields=["quantity", "entry_price"],
    )

    assert report["decision"] == "MANTER_EM_RESEARCH"
    assert report["research_only"] is True
    assert report["read_only"] is True
    for key, value in report["safety_flags"].items():
        if key in {"paper_only", "shadow_only", "research_only", "read_only"}:
            assert value is True
        else:
            assert value is False
        assert report[key] == value
    assert report["forbidden_fields_used"] == []


def test_json_serializable(tmp_path: Path) -> None:
    report = build_ai_feature_source_fields_enrichment_contract_v1(
        project_root=tmp_path,
        available_fields=["quantity", "entry_price"],
    )

    encoded = json.dumps(report, sort_keys=True)
    assert "ai_feature_source_fields_enrichment_contract_v1" in encoded


def test_markdown_renderable(tmp_path: Path) -> None:
    report = build_ai_feature_source_fields_enrichment_contract_v1(
        project_root=tmp_path,
        available_fields=["quantity", "entry_price"],
    )
    markdown = render_markdown(report)

    assert "# AI Feature Source Fields Enrichment Contract V1" in markdown
    assert "## Allowed Source Fields" in markdown
    assert "## Derivation Readiness" in markdown
    assert "MANTER_EM_RESEARCH" in markdown


def test_cli_json_executes_no_write(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--project-root",
            str(tmp_path),
            "--available-field",
            "quantity",
            "--available-field",
            "entry_price",
            "--json",
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert payload["status"] == "ok"
    assert payload["decision"] == "MANTER_EM_RESEARCH"
    assert payload["write_performed"] is False
    assert payload["forbidden_fields_used"] == []


def test_boundary_auditor_has_no_critical_or_high_findings() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/audit_state_execution_ledger_boundary.py", "--project-root", ".", "--json"],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    findings = payload.get("boundary_findings", [])
    critical_or_high = [
        item
        for item in findings
        if item.get("severity") in {"critical", "high"}
    ]
    assert critical_or_high == []
