from __future__ import annotations

import json
import os
import runpy
import subprocess
import sys
from pathlib import Path

from smartcrypto.research.paper_master_divergence_oos_causal_attribution import (
    SCHEMA_VERSION,
    build_oos_causal_attribution_report,
    run_oos_causal_attribution_research,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "build_paper_master_divergence_oos_causal_attribution_v1.py"


def test_report_preserves_research_only_safety_contract() -> None:
    report = build_oos_causal_attribution_report(PROJECT_ROOT)

    assert report["schema_version"] == SCHEMA_VERSION
    assert report["status"] == "blocked"
    assert report["decision"] == "MANTER_EM_RESEARCH"
    assert report["research_only"] is True
    assert report["read_only"] is True
    assert report["paper_only"] is True
    assert report["shadow_only"] is True
    assert report["operational_authority"] is False
    assert report["can_apply_to_freqtrade"] is False
    assert report["can_apply_to_risk_manager"] is False
    assert report["can_promote_rules"] is False
    assert report["can_promote_model"] is False
    assert report["updates_freqtrade"] is False
    assert report["updates_risk_manager"] is False
    assert report["updates_qlib_runtime"] is False
    assert report["updates_ai_shadow_runtime"] is False
    assert report["sends_orders"] is False
    assert report["live_release_allowed"] is False
    assert report["canary_release_allowed"] is False


def test_report_scopes_causal_attribution_to_h1_h2_h6() -> None:
    report = build_oos_causal_attribution_report(PROJECT_ROOT)

    assert report["causal_attribution_created"] is True
    assert report["causal_attribution_scope"] == ["H1", "H2", "H6"]
    assert report["causal_attribution_hypothesis_count"] == 3
    assert [item["hypothesis_id"] for item in report["causal_hypothesis_attributions"]] == [
        "H1",
        "H2",
        "H6",
    ]
    assert all(item["oos_required"] is True for item in report["causal_hypothesis_attributions"])
    assert all(item["oos_passed"] is False for item in report["causal_hypothesis_attributions"])
    assert report["oos_validation_required"] is True
    assert report["oos_validated"] is False
    assert report["ready_for_candidate_registry"] is False


def test_report_keeps_known_divergence_and_cluster_evidence_explicit() -> None:
    report = build_oos_causal_attribution_report(PROJECT_ROOT)

    assert report["divergence_confirmed"] is True
    assert report["paper_replicates_master_edge"] is False
    assert report["divergence_metrics"]["paper_minus_master_net_pnl"] == -164.52110752
    assert report["divergence_metrics"]["paper_minus_master_profit_factor"] == -1.269242
    assert report["divergence_metrics"]["paper_minus_master_win_rate_points"] == -30.1961
    assert report["canonical_cluster_evidence"]["remove_stop_loss_under_30m_delta"] == 34.9161
    assert report["canonical_cluster_evidence"]["candidate_shadow_rule_precision"] == 0.65625
    assert report["canonical_cluster_evidence"]["candidate_shadow_rule_recall"] == 0.41176


def test_gate_summary_has_no_failed_or_critical_failed_gates() -> None:
    report = build_oos_causal_attribution_report(PROJECT_ROOT)

    assert report["gate_summary"]["gate_count"] == 6
    assert report["gate_summary"]["failed_gate_count"] == 0
    assert report["gate_summary"]["failed_gate_ids"] == []
    assert report["gate_summary"]["critical_failed_gate_ids"] == []


def test_no_write_default_does_not_create_output(tmp_path: Path) -> None:
    output_path = tmp_path / "should_not_exist.json"

    report = run_oos_causal_attribution_research(
        PROJECT_ROOT,
        write=False,
        output_path=output_path,
    )

    assert report["write_requested"] is False
    assert report["write_performed"] is False
    assert report["writes_reports"] is False
    assert not output_path.exists()


def test_write_mode_is_explicit_and_research_only(tmp_path: Path) -> None:
    output_path = tmp_path / "report.json"

    report = run_oos_causal_attribution_research(
        PROJECT_ROOT,
        write=True,
        output_path=output_path,
    )

    assert output_path.exists()
    assert report["write_requested"] is True
    assert report["write_performed"] is True
    assert report["writes_reports"] is True
    assert report["operational_authority"] is False
    assert report["sends_orders"] is False
    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written["schema_version"] == SCHEMA_VERSION


def test_cli_outputs_valid_no_write_json() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--project-root",
            str(PROJECT_ROOT),
            "--no-write",
            "--json",
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )

    assert result.returncode == 0, (result.stdout, result.stderr)
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["status"] == "blocked"
    assert payload["decision"] == "MANTER_EM_RESEARCH"
    assert payload["write_performed"] is False
    assert payload["causal_attribution_scope"] == ["H1", "H2", "H6"]


def test_cli_is_standalone_without_pythonpath() -> None:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--project-root",
            str(PROJECT_ROOT),
            "--no-write",
            "--json",
        ],
        cwd=Path(os.environ.get("TMP", os.environ.get("TEMP", "."))),
        env=env,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )

    assert result.returncode == 0, (result.stdout, result.stderr)
    payload = json.loads(result.stdout)
    assert payload["research_only"] is True
    assert payload["operational_authority"] is False


def test_script_import_via_runpy_does_not_execute_main() -> None:
    namespace = runpy.run_path(str(SCRIPT_PATH), run_name="__not_main__")

    assert "main" in namespace
    assert "parse_args" in namespace
