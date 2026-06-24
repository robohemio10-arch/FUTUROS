from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from smartcrypto.research.ai_research_training_track_closeout_handover import (
    DEFAULT_MARKDOWN_PATH,
    DEFAULT_REPORT_PATH,
    SAFETY_FLAGS,
    SOURCE_PATHS,
    build_ai_research_training_track_closeout_handover,
    render_closeout_markdown,
    resolve_paths,
    run_ai_research_training_track_closeout_handover,
)


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "smartcrypto/research/ai_research_training_track_closeout_handover.py"
CLI = ROOT / "scripts/build_ai_research_training_track_closeout_handover.py"


def source_payloads() -> dict[str, dict[str, Any]]:
    return {
        "branch01_research_dataset": {
            "status": "ok",
            "reason": "research_dataset_built",
            "research_dataset_rows": 3058,
            "eligible_rows": 2392,
            "blocked_rows": 666,
        },
        "branch02_tp_sl_grid": {
            "status": "ok",
            "reason": "grid_evaluated",
            "grid_rows": 167,
            "best_strategy_id": "fixed_20_30",
            "best_net_pnl": -12.5,
            "original_net_pnl": -21.0,
        },
        "branch03_walkforward_montecarlo": {
            "status": "blocked",
            "reason": "out_of_sample_gate_blocked",
            "decision": "DESCARTAR_CANDIDATO",
            "candidate_walkforward_net_pnl": -8.0,
            "original_walkforward_net_pnl": -5.0,
            "monte_carlo": {"risk_of_ruin": 0.31},
        },
        "branch04_qlib_training": {
            "status": "warning",
            "reason": "research_only_training",
            "decision": "MANTER_EM_RESEARCH",
            "aggregate_metrics": {
                "selected_net_pnl": -3.0,
                "all_test_net_pnl": -2.0,
                "mean_roc_auc": 0.54,
                "mean_f1": 0.49,
            },
        },
        "branch05_executive_pack": {
            "status": "warning",
            "reason": "executive_pack_research_only",
            "decision": "MANTER_EM_RESEARCH",
            "consolidated_kpis": {"eligible_rows": 2392, "blocked_rows": 666},
        },
        "branch06_candidate_registry": {
            "status": "warning",
            "reason": "promotion_gate_blocked",
            "decision": "MANTER_EM_RESEARCH",
            "promotion_status": "blocked",
            "promotion_eligible": False,
            "candidate_registry_status": "research_only",
        },
        "branch07_feedback_loop": {
            "status": "warning",
            "reason": "record_only",
            "decision": "MANTER_EM_RESEARCH",
            "learning_action": "record_only",
            "training_allowed": False,
            "promotion_allowed": False,
        },
        "branch08_freqtrade_selector": {
            "status": "warning",
            "reason": "selector_observation_only",
            "decision": "MANTER_EM_RESEARCH",
            "selector_status": "observe_only_blocked",
            "selector_authority": "none",
            "paper_signal_mutation_allowed": False,
        },
        "branch09_dashboard_command_center": {
            "status_summary": {"status": "WARNING"},
            "sections": {
                "ai_training_research_command_center": {
                    "status": "WARNING",
                    "section_status": "WARNING",
                    "reason": "research_evidence_advisory_only",
                    "decision": "MANTER_EM_RESEARCH",
                    "research_gate_status": "BLOCKED",
                    "authority": "advisory_only",
                    "operational_authority": False,
                }
            },
        },
    }


def write_sources(root: Path) -> None:
    for source_key, relative_path in SOURCE_PATHS.items():
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(source_payloads()[source_key]), encoding="utf-8"
        )


def test_closeout_contract_is_research_only_and_blocked() -> None:
    report = build_ai_research_training_track_closeout_handover(source_payloads())
    assert report["track_status"] == "closed_research_only"
    assert report["decision"] == "MANTER_EM_RESEARCH"
    assert report["research_gate_status"] == "BLOCKED"
    assert report["promotion_status"] == "blocked"
    assert report["operational_authority"] is False


def test_closeout_builds_nine_branch_cards() -> None:
    report = build_ai_research_training_track_closeout_handover(source_payloads())
    assert len(report["branch_cards"]) == 9
    assert report["branch_cards"][2]["status"] == "BLOCKED"
    assert all(card["advisory_only"] is True for card in report["branch_cards"])


def test_missing_sources_are_optional_and_do_not_crash() -> None:
    report = build_ai_research_training_track_closeout_handover({})
    assert len(report["missing_optional_sources"]) == 9
    assert {card["status"] for card in report["branch_cards"]} == {
        "MISSING_OPTIONAL"
    }
    assert report["promotion_status"] == "blocked"


def test_dashboard_snapshot_without_research_section_is_missing_optional() -> None:
    payloads = source_payloads()
    payloads["branch09_dashboard_command_center"] = {
        "status_summary": {"status": "OK"},
        "sections": {"model_state": {"status": "OK"}},
    }
    report = build_ai_research_training_track_closeout_handover(payloads)
    assert "branch09_dashboard_command_center" in report["missing_optional_sources"]
    assert report["branch_cards"][-1]["status"] == "MISSING_OPTIONAL"


def test_branch01_and_branch02_use_canonical_json_paths() -> None:
    assert SOURCE_PATHS["branch01_research_dataset"] == (
        "data/reports/ocr_v11_research_dataset_audit.json"
    )
    assert SOURCE_PATHS["branch02_tp_sl_grid"] == (
        "data/reports/ocr_v11_tp_sl_grid_summary.json"
    )


def test_sources_are_json_only_without_parquet_joblib_or_sqlite() -> None:
    assert all(path.endswith(".json") for path in SOURCE_PATHS.values())
    serialized = json.dumps(SOURCE_PATHS).lower()
    assert ".parquet" not in serialized
    assert ".joblib" not in serialized
    assert ".sqlite" not in serialized


def test_safety_flags_are_fail_closed() -> None:
    report = build_ai_research_training_track_closeout_handover(source_payloads())
    assert report["safety_flags"] == SAFETY_FLAGS
    assert report["safety_flags"]["paper_only"] is True
    assert report["safety_flags"]["shadow_only"] is True
    assert all(
        value is False
        for key, value in report["safety_flags"].items()
        if key not in {"paper_only", "shadow_only"}
    )


def test_no_write_does_not_materialize_outputs(tmp_path: Path) -> None:
    write_sources(tmp_path)
    paths = resolve_paths(tmp_path)
    result = run_ai_research_training_track_closeout_handover(paths)
    assert result.report["write_requested"] is False
    assert result.report["write_performed"] is False
    assert not (tmp_path / DEFAULT_REPORT_PATH).exists()
    assert not (tmp_path / DEFAULT_MARKDOWN_PATH).exists()


def test_write_materializes_only_two_allowed_outputs(tmp_path: Path) -> None:
    write_sources(tmp_path)
    before = {path for path in tmp_path.rglob("*") if path.is_file()}
    paths = resolve_paths(tmp_path)
    result = run_ai_research_training_track_closeout_handover(
        paths,
        write=True,
        analysis_date_utc="2026-06-24T00:00:00+00:00",
    )
    after = {path for path in tmp_path.rglob("*") if path.is_file()}
    assert after - before == {
        tmp_path / DEFAULT_REPORT_PATH,
        tmp_path / DEFAULT_MARKDOWN_PATH,
    }
    assert result.report["write_performed"] is True


def test_markdown_contains_decision_and_next_gates() -> None:
    report = build_ai_research_training_track_closeout_handover(source_payloads())
    markdown = render_closeout_markdown(report)
    assert "MANTER_EM_RESEARCH" in markdown
    assert "## Próximos gates" in markdown
    assert "readiness" in markdown
    assert "Autoridade operacional: `false`" in markdown


def test_cli_returns_valid_json_without_writing(tmp_path: Path) -> None:
    write_sources(tmp_path)
    completed = subprocess.run(
        [
            sys.executable,
            str(CLI),
            "--project-root",
            str(tmp_path),
            "--no-write",
            "--json",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)
    assert payload["track_status"] == "closed_research_only"
    assert payload["write_performed"] is False
    assert not (tmp_path / DEFAULT_REPORT_PATH).exists()


def test_module_has_no_forbidden_operational_imports() -> None:
    forbidden = {
        "freqtrade",
        "ccxt",
        "joblib",
        "pickle",
        "sqlite3",
        "subprocess",
        "requests",
        "httpx",
    }
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    imports = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imports.update(
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    )
    assert forbidden.isdisjoint(imports)


def test_runtime_outputs_are_not_versioned() -> None:
    tracked = subprocess.run(
        ["git", "ls-files", "--", DEFAULT_REPORT_PATH, DEFAULT_MARKDOWN_PATH],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert tracked.stdout.strip() == ""
