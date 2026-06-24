from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

from smartcrypto.research.freqtrade_paper_ai_selector_integration import (
    SAFETY_FLAGS,
    FreqtradePaperAISelectorConfig,
    build_selector_observations,
    collect_selector_evidence,
    evaluate_selector_gate,
    resolve_paths,
    run_freqtrade_paper_ai_selector_integration,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_freqtrade_paper_ai_selector_integration.py"
MODULE = (
    ROOT / "smartcrypto" / "research" / "freqtrade_paper_ai_selector_integration.py"
)


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def populate_research_reports(project_root: Path) -> None:
    paths = resolve_paths(project_root)
    write_json(
        paths.training_summary_path,
        {
            "status": "warning",
            "reason": "selector_does_not_beat_all_test_baseline",
            "decision": "MANTER_EM_RESEARCH",
            "aggregate_metrics": {
                "selected_net_pnl": 227.07,
                "all_test_net_pnl": 503.16,
            },
            "paper_only": True,
            "shadow_only": True,
            "updates_freqtrade": False,
            "updates_qlib_runtime": False,
            "updates_risk_manager": False,
            "production_enabled": False,
        },
    )
    write_json(
        paths.executive_pack_path,
        {
            "status": "warning",
            "reason": "evidence_consolidated_no_promotion",
            "decision": "MANTER_EM_RESEARCH",
            "paper_only": True,
            "shadow_only": True,
            "updates_freqtrade": False,
            "updates_qlib_runtime": False,
            "updates_risk_manager": False,
            "production_enabled": False,
        },
    )
    write_json(
        paths.shadow_candidate_report_path,
        {
            "status": "warning",
            "reason": "research_candidate_registered_without_promotion",
            "decision": "MANTER_EM_RESEARCH",
            "promotion_status": "blocked",
            "promotion_eligible": False,
            "paper_only": True,
            "shadow_only": True,
            "registers_model": False,
            "production_enabled": False,
        },
    )
    write_json(
        paths.feedback_loop_report_path,
        {
            "status": "warning",
            "reason": "feedback_recorded_without_training",
            "decision": "MANTER_EM_RESEARCH",
            "learning_action": "record_only",
            "training_allowed": False,
            "promotion_allowed": False,
            "paper_only": True,
            "shadow_only": True,
            "updates_freqtrade": False,
            "updates_qlib_runtime": False,
            "updates_risk_manager": False,
            "updates_ai_shadow_runtime": False,
        },
    )


def populate_freqtrade_files(project_root: Path) -> None:
    paths = resolve_paths(project_root)
    write_json(
        paths.freqtrade_config_path,
        {
            "dry_run": True,
            "trading_mode": "futures",
            "margin_mode": "isolated",
            "timeframe": "5m",
            "stake_currency": "USDT",
            "max_open_trades": 2,
            "force_entry_enable": False,
            "exchange": {
                "name": "binance",
                "key": "",
                "secret": "",
                "pair_whitelist": ["BTC/USDT:USDT", "ETH/USDT:USDT"],
            },
            "api_server": {"enabled": False, "password": "must-not-leak"},
            "telegram": {"enabled": False, "token": "must-not-leak"},
        },
    )
    paths.freqtrade_strategy_path.parent.mkdir(parents=True, exist_ok=True)
    paths.freqtrade_strategy_path.write_text(
        "\n".join(
            [
                "from freqtrade.strategy import IStrategy",
                "",
                "class SmartCryptoSignalStrategy(IStrategy):",
                "    def populate_indicators(self, dataframe, metadata):",
                "        return dataframe",
                "",
                "    def populate_entry_trend(self, dataframe, metadata):",
                "        return dataframe",
                "",
                "    def populate_exit_trend(self, dataframe, metadata):",
                "        return dataframe",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def populate_project(project_root: Path, *, freqtrade_files: bool = True) -> None:
    populate_research_reports(project_root)
    if freqtrade_files:
        populate_freqtrade_files(project_root)


def run_integration(
    project_root: Path,
    *,
    write: bool = False,
    strict: bool = False,
):
    populate_project(project_root)
    return run_freqtrade_paper_ai_selector_integration(
        resolve_paths(project_root),
        FreqtradePaperAISelectorConfig(strict=strict),
        write=write,
        analysis_date_utc="2026-06-23T18:00:00Z",
    )


def test_collects_freqtrade_paper_config_and_strategy_snapshot(tmp_path: Path) -> None:
    populate_project(tmp_path)
    evidence = collect_selector_evidence(resolve_paths(tmp_path))
    config = evidence["sources"]["freqtrade_config"]["payload"]
    strategy = evidence["sources"]["freqtrade_strategy"]["payload"]
    assert config["dry_run"] is True
    assert config["pair_count"] == 2
    assert config["snapshot_redacted"] is True
    assert "password" not in json.dumps(config).lower()
    assert "token" not in json.dumps(config).lower()
    assert strategy["strategy_classes"] == ["SmartCryptoSignalStrategy"]
    assert strategy["inspection_mode"] == "static_ast_read_only"
    assert strategy["strategy_imported"] is False
    assert strategy["strategy_executed"] is False


def test_selector_gate_blocks_when_ai_candidate_kept_in_research(tmp_path: Path) -> None:
    populate_project(tmp_path)
    gate = evaluate_selector_gate(
        collect_selector_evidence(resolve_paths(tmp_path)),
        FreqtradePaperAISelectorConfig(),
    )
    assert "branch04_kept_in_research" in gate["selector_blockers"]
    assert "branch04_selected_not_above_all_test" in gate["selector_blockers"]
    assert "branch05_kept_in_research" in gate["selector_blockers"]
    assert gate["selector_authority"] == "none"


def test_selector_gate_blocks_when_shadow_registry_not_promotable(tmp_path: Path) -> None:
    populate_project(tmp_path)
    gate = evaluate_selector_gate(
        collect_selector_evidence(resolve_paths(tmp_path)),
        FreqtradePaperAISelectorConfig(),
    )
    assert "branch06_promotion_blocked" in gate["selector_blockers"]
    assert "branch06_not_promotion_eligible" in gate["selector_blockers"]


def test_selector_gate_blocks_when_feedback_loop_record_only(tmp_path: Path) -> None:
    populate_project(tmp_path)
    gate = evaluate_selector_gate(
        collect_selector_evidence(resolve_paths(tmp_path)),
        FreqtradePaperAISelectorConfig(),
    )
    assert "branch07_record_only_feedback" in gate["selector_blockers"]
    assert "branch07_training_not_allowed" in gate["selector_blockers"]
    assert "branch07_promotion_not_allowed" in gate["selector_blockers"]


def test_unsafe_safety_flags_block_selector_authority(tmp_path: Path) -> None:
    populate_project(tmp_path)
    paths = resolve_paths(tmp_path)
    payload = json.loads(paths.feedback_loop_report_path.read_text(encoding="utf-8"))
    payload["updates_freqtrade"] = True
    payload["sends_orders"] = True
    write_json(paths.feedback_loop_report_path, payload)
    result = run_freqtrade_paper_ai_selector_integration(
        paths,
        FreqtradePaperAISelectorConfig(),
        analysis_date_utc="2026-06-23T18:00:00Z",
    )
    assert result.report["status"] == "blocked"
    assert result.report["selector_authority"] == "none"
    assert (
        "unsafe_safety_flag:branch07_feedback_loop_report:updates_freqtrade=true"
        in result.report["selector_blockers"]
    )
    assert result.report["updates_freqtrade"] is False
    assert result.report["sends_orders"] is False


def test_builds_record_only_selector_observations(tmp_path: Path) -> None:
    populate_project(tmp_path)
    evidence = collect_selector_evidence(resolve_paths(tmp_path))
    gate = evaluate_selector_gate(evidence, FreqtradePaperAISelectorConfig())
    first = build_selector_observations(evidence, gate, "2026-06-23T18:00:00Z")
    second = build_selector_observations(evidence, gate, "2026-06-24T18:00:00Z")
    expected_types = {
        "freqtrade_paper_config_observed",
        "freqtrade_strategy_contract_observed",
        "branch04_ai_selector_result_observed",
        "branch05_executive_pack_gate_observed",
        "branch06_shadow_registry_gate_observed",
        "branch07_feedback_loop_gate_observed",
        "paper_ai_selector_gate_blocked",
        "recommended_operator_next_actions_recorded",
    }
    assert expected_types == {item["observation_type"] for item in first}
    assert [item["observation_id"] for item in first] == [
        item["observation_id"] for item in second
    ]
    assert all(item["action_taken"] == "record_only" for item in first)
    assert all(item["sends_orders"] is False for item in first)
    assert all(item["updates_freqtrade"] is False for item in first)


def test_no_write_does_not_materialize_outputs(tmp_path: Path) -> None:
    paths = resolve_paths(tmp_path)
    result = run_integration(tmp_path)
    assert result.report["status"] == "warning"
    assert result.report["write_requested"] is False
    assert result.report["write_performed"] is False
    assert not paths.report_output_path.exists()
    assert not paths.observations_output_path.exists()


def test_write_materializes_report_and_observations(tmp_path: Path) -> None:
    paths = resolve_paths(tmp_path)
    result = run_integration(tmp_path, write=True)
    report = json.loads(paths.report_output_path.read_text(encoding="utf-8"))
    observations = [
        json.loads(line)
        for line in paths.observations_output_path.read_text(encoding="utf-8").splitlines()
    ]
    assert result.report["write_performed"] is True
    assert report["selector_authority"] == "none"
    assert len(observations) == result.report["observation_count"]


def test_write_is_idempotent_for_observations(tmp_path: Path) -> None:
    paths = resolve_paths(tmp_path)
    first = run_integration(tmp_path, write=True)
    second = run_integration(tmp_path, write=True)
    lines = paths.observations_output_path.read_text(encoding="utf-8").splitlines()
    assert first.report["new_observations_written"] == len(first.observations)
    assert second.report["new_observations_written"] == 0
    assert len(lines) == len(first.observations)


def test_cli_json_no_write(tmp_path: Path) -> None:
    populate_project(tmp_path)
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
    assert completed.returncode == 0
    assert payload["status"] == "warning"
    assert payload["selector_status"] == "observe_only_blocked"
    assert payload["write_performed"] is False


def test_runtime_outputs_are_not_expected_to_be_versioned() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    paths = resolve_paths(ROOT)
    assert "data/" in gitignore
    assert paths.report_output_path.is_relative_to(ROOT / "data")
    assert paths.observations_output_path.is_relative_to(ROOT / "data")


def test_does_not_require_or_modify_freqtrade_strategy(tmp_path: Path) -> None:
    populate_project(tmp_path)
    paths = resolve_paths(tmp_path)
    before = paths.freqtrade_strategy_path.read_bytes()
    run_freqtrade_paper_ai_selector_integration(
        paths,
        FreqtradePaperAISelectorConfig(),
        write=True,
        analysis_date_utc="2026-06-23T18:00:00Z",
    )
    assert paths.freqtrade_strategy_path.read_bytes() == before


def test_missing_freqtrade_files_warn_not_crash_in_non_strict_mode(tmp_path: Path) -> None:
    populate_project(tmp_path, freqtrade_files=False)
    result = run_freqtrade_paper_ai_selector_integration(
        resolve_paths(tmp_path),
        FreqtradePaperAISelectorConfig(),
        analysis_date_utc="2026-06-23T18:00:00Z",
    )
    assert result.report["status"] == "warning"
    assert result.report["missing_freqtrade_files"] == [
        "freqtrade_config",
        "freqtrade_strategy",
    ]
    assert result.report["selector_authority"] == "none"


def test_missing_freqtrade_files_block_in_strict_mode(tmp_path: Path) -> None:
    populate_project(tmp_path, freqtrade_files=False)
    result = run_freqtrade_paper_ai_selector_integration(
        resolve_paths(tmp_path),
        FreqtradePaperAISelectorConfig(strict=True),
        analysis_date_utc="2026-06-23T18:00:00Z",
    )
    assert result.report["status"] == "blocked"
    assert result.report["selector_authority"] == "none"


def test_static_contract_has_no_operational_imports_or_calls() -> None:
    forbidden_imports = (
        "freqtrade",
        "ccxt",
        "joblib",
        "pickle",
        "sqlite3",
        "subprocess",
        "smartcrypto.execution",
        "smartcrypto.risk",
        "smartcrypto.qlib_engine",
        "smartcrypto.ml",
    )
    for source in (MODULE, SCRIPT):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imports.update(
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        )
        assert all(
            not any(name.startswith(fragment) for fragment in forbidden_imports)
            for name in imports
        )


def test_all_safety_flags_remain_fail_closed(tmp_path: Path) -> None:
    report = run_integration(tmp_path).report
    for name, expected in SAFETY_FLAGS.items():
        assert report[name] is expected
    assert report["selector_authority"] == "none"
