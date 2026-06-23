from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from smartcrypto.research.training_executive_report_pack import (
    SAFETY_FLAGS,
    ExecutiveReportPackConfig,
    build_executive_pack,
    collect_branch_evidence,
    resolve_paths,
    run_training_executive_report_pack,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_training_executive_report_pack.py"


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def branch_payloads() -> dict[str, dict[str, object]]:
    return {
        "branch01": {
            "status": "ok",
            "trades": 3058,
            "eligible_trades": 2392,
            "blocked_trades": 666,
            "candle_alignment": {"aligned_rate": 0.7822},
        },
        "branch02": {
            "status": "ok",
            "reason": "simulation_ready",
            "best_net_pnl": -14687.15,
            "original_net_pnl": 549.77,
        },
        "branch03": {
            "status": "blocked",
            "reason": "candidate_does_not_beat_original_walkforward",
            "decision": "DESCARTAR_CANDIDATO",
            "candidate_walkforward_net_pnl": -12270.55,
            "original_walkforward_net_pnl": 268.70,
            "monte_carlo": {"risk_of_ruin": 1.0},
        },
        "branch04": {
            "status": "warning",
            "reason": "selector_does_not_beat_all_test_baseline",
            "decision": "MANTER_EM_RESEARCH",
            "feature_count": 14,
            "aggregate_metrics": {
                "mean_accuracy": 0.591,
                "mean_f1": 0.706,
                "mean_roc_auc": 0.523,
                "all_test_net_pnl": 503.16,
                "selected_net_pnl": 227.07,
                "selected_rows": 771,
                "valid_folds": 5,
            },
        },
    }


def write_primary_reports(project: Path) -> None:
    reports = project / "data" / "reports"
    payloads = branch_payloads()
    write_json(reports / "ocr_v11_research_dataset_summary.json", payloads["branch01"])
    write_json(reports / "ocr_v11_tp_sl_grid_summary.json", payloads["branch02"])
    write_json(reports / "ocr_v11_walkforward_montecarlo_summary.json", payloads["branch03"])
    write_json(reports / "qlib_ocr_v11_supervised_training_summary.json", payloads["branch04"])


def test_collects_branch_evidence_with_primary_reports(tmp_path: Path) -> None:
    write_primary_reports(tmp_path)
    evidence = collect_branch_evidence(resolve_paths(tmp_path))
    assert evidence["missing_sources"] == []
    assert all(item["available"] for item in evidence["branches"].values())
    assert all(item["source_kind"] == "primary_json" for item in evidence["branches"].values())


def test_branch01_fallback_to_training_reports_summary(tmp_path: Path) -> None:
    payloads = branch_payloads()
    fallback = (
        tmp_path
        / "data"
        / "reports"
        / "training_reports"
        / "ocr_v11_research_dataset_summary.json"
    )
    write_json(fallback, payloads["branch01"])
    paths = resolve_paths(tmp_path)
    evidence = collect_branch_evidence(paths)
    branch = evidence["branches"]["branch01"]
    assert branch["available"] is True
    assert branch["source_kind"] == "fallback_json"
    assert branch["source_path"] == str(fallback)
    assert "branch01_primary_missing_used_fallback_json" in evidence["warnings"]


def test_branch01_fallback_to_markdown_when_json_is_absent(tmp_path: Path) -> None:
    markdown = (
        tmp_path
        / "data"
        / "reports"
        / "training_reports"
        / "ocr_v11_research_dataset_executive.md"
    )
    markdown.parent.mkdir(parents=True)
    markdown.write_text(
        "\n".join(
            [
                "# OCR V1.1",
                "**Quantidade de trades:** 3058",
                "**Trades elegíveis:** 2392",
                "**Trades bloqueados:** 666",
                "- Taxa alinhada: 78.22%",
            ]
        ),
        encoding="utf-8",
    )
    branch = collect_branch_evidence(resolve_paths(tmp_path))["branches"]["branch01"]
    assert branch["source_kind"] == "markdown"
    assert branch["payload"]["trades"] == 3058
    assert branch["payload"]["eligible_trades"] == 2392
    assert branch["payload"]["candle_alignment"]["aligned_rate"] == 0.7822


def test_missing_optional_sources_returns_warning_not_crash(tmp_path: Path) -> None:
    payload = branch_payloads()["branch01"]
    write_json(tmp_path / "data" / "reports" / "ocr_v11_research_dataset_summary.json", payload)
    evidence = collect_branch_evidence(resolve_paths(tmp_path))
    pack = build_executive_pack(evidence, ExecutiveReportPackConfig())
    assert pack["status"] == "warning"
    assert pack["reason"] == "partial_evidence_missing_sources"
    assert pack["missing_sources"] == ["branch02", "branch03", "branch04"]
    missing = [row for row in pack["branch_decisions"] if row["status"] == "missing"]
    assert all(row["decision"] == "EVIDENCIA_AUSENTE" for row in missing)


def test_no_write_does_not_materialize_outputs(tmp_path: Path) -> None:
    write_primary_reports(tmp_path)
    paths = resolve_paths(tmp_path)
    result = run_training_executive_report_pack(
        paths,
        ExecutiveReportPackConfig(),
        write=False,
        analysis_date_utc="2026-06-23T18:00:00Z",
    )
    assert result.pack["write_performed"] is False
    assert not paths.output_json.exists()
    assert not paths.output_markdown.exists()
    assert not paths.output_html.exists()


def test_write_materializes_json_md_html(tmp_path: Path) -> None:
    write_primary_reports(tmp_path)
    paths = resolve_paths(tmp_path)
    result = run_training_executive_report_pack(
        paths,
        ExecutiveReportPackConfig(),
        write=True,
        analysis_date_utc="2026-06-23T18:00:00Z",
    )
    assert result.pack["write_performed"] is True
    assert json.loads(paths.output_json.read_text(encoding="utf-8"))["decision"] == "MANTER_EM_RESEARCH"
    assert "Decisão institucional" in paths.output_markdown.read_text(encoding="utf-8")
    html = paths.output_html.read_text(encoding="utf-8")
    assert "<!doctype html>" in html
    assert "https://" not in html and "<script" not in html


def test_pack_contains_negative_and_positive_evidence(tmp_path: Path) -> None:
    write_primary_reports(tmp_path)
    pack = build_executive_pack(
        collect_branch_evidence(resolve_paths(tmp_path)),
        ExecutiveReportPackConfig(),
    )
    assert any("Branch 02" in item for item in pack["negative_evidence"])
    assert any("Branch 03" in item for item in pack["negative_evidence"])
    assert any("Branch 04" in item for item in pack["negative_evidence"])
    assert any("reproduzível" in item for item in pack["positive_evidence"])
    assert pack["decision"] == "MANTER_EM_RESEARCH"


def test_safety_flags_are_preserved(tmp_path: Path) -> None:
    write_primary_reports(tmp_path)
    result = run_training_executive_report_pack(
        resolve_paths(tmp_path),
        ExecutiveReportPackConfig(),
    )
    for name, expected in SAFETY_FLAGS.items():
        assert result.pack[name] is expected


def test_chart_data_is_present(tmp_path: Path) -> None:
    write_primary_reports(tmp_path)
    pack = build_executive_pack(
        collect_branch_evidence(resolve_paths(tmp_path)),
        ExecutiveReportPackConfig(),
    )
    assert set(pack["chart_data"]) == {
        "branch_status_chart",
        "pnl_comparison_chart",
        "ml_metrics_chart",
        "eligibility_chart",
    }
    assert len(pack["chart_data"]["branch_status_chart"]) == 4
    assert pack["consolidated_kpis"]["supervised_mean_roc_auc"] == 0.523


def test_cli_json_no_write(tmp_path: Path) -> None:
    write_primary_reports(tmp_path)
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--project-root",
            str(tmp_path),
            "--no-write",
            "--json",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    payload = json.loads(completed.stdout)
    assert completed.returncode == 0, completed.stderr
    assert payload["status"] == "warning"
    assert payload["write_performed"] is False
    assert payload["decision"] == "MANTER_EM_RESEARCH"


def test_runtime_outputs_are_not_expected_to_be_versioned(tmp_path: Path) -> None:
    paths = resolve_paths(tmp_path)
    for output in (paths.output_json, paths.output_markdown, paths.output_html):
        assert output.is_relative_to(tmp_path / "data" / "reports" / "training_reports")
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "data/" in gitignore
