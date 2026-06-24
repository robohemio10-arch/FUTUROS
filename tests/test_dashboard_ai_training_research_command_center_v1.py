from __future__ import annotations

import ast
import json
from pathlib import Path

from smartcrypto.dashboard.components.ai_training_research_command_center import (
    extract_ai_training_research_command_center,
    render_ai_training_research_command_center,
)
from smartcrypto.ops.dashboard_semantic_audit.catalog import OFFICIAL_PAGE_CONTRACTS
from smartcrypto.ops.dashboard_snapshots.ai_governance_snapshot_builder import (
    build_ai_governance_snapshot,
)
from smartcrypto.ops.dashboard_snapshots.ai_training_research_command_center import (
    RESEARCH_SOURCE_PATHS,
    SAFETY_FLAGS,
    normalize_ai_training_research_command_center,
)
from smartcrypto.ops.dashboard_snapshots.build_context import (
    create_dashboard_build_context,
)
from smartcrypto.ops.dashboard_snapshots.builder_registry import (
    build_all_dashboard_snapshots,
)
from smartcrypto.ops.dashboard_snapshots.contracts import DashboardPageId, SourceKind
from smartcrypto.ops.dashboard_snapshots.source_catalog import sources_for_page
from tests.dashboard_builder_test_support import context, write_json
from tests.dashboard_page_test_support import FakeUi, load_page_module, valid_snapshot


ROOT = Path(__file__).resolve().parents[1]
NORMALIZER = (
    ROOT
    / "smartcrypto"
    / "ops"
    / "dashboard_snapshots"
    / "ai_training_research_command_center.py"
)
COMPONENT = (
    ROOT
    / "smartcrypto"
    / "dashboard"
    / "components"
    / "ai_training_research_command_center.py"
)
PAGE = ROOT / "smartcrypto" / "dashboard" / "pages" / "05_ai_governance.py"


def research_payloads() -> dict[str, list[dict[str, object]]]:
    return {
        "ocr_v11_research_dataset_audit": [
            {
                "status": "ok",
                "reason": "research_dataset_ready",
                "research_dataset_rows": 3058,
                "eligible_rows": 2392,
                "blocked_rows": 666,
                "candles_rows": 459024,
                "paper_only": True,
                "shadow_only": True,
            }
        ],
        "ocr_v11_tp_sl_grid_summary": [
            {
                "status": "ok",
                "reason": "simulation_ready",
                "grid_rows": 167,
                "best_strategy_id": "fixed_tp_20_sl_200",
                "best_net_pnl": -14687.15,
                "original_net_pnl": 549.77,
                "paper_only": True,
                "shadow_only": True,
            }
        ],
        "ocr_v11_walkforward_montecarlo_summary": [
            {
                "status": "blocked",
                "reason": "candidate_does_not_beat_original_walkforward",
                "decision": "DESCARTAR_CANDIDATO",
                "candidate_walkforward_net_pnl": -12270.55,
                "original_walkforward_net_pnl": 268.69,
                "monte_carlo": {"risk_of_ruin": 1.0},
                "paper_only": True,
                "shadow_only": True,
            }
        ],
        "qlib_ocr_v11_supervised_training_summary": [
            {
                "status": "warning",
                "reason": "selector_does_not_beat_all_test_baseline",
                "decision": "MANTER_EM_RESEARCH",
                "aggregate_metrics": {
                    "selected_net_pnl": 227.07,
                    "all_test_net_pnl": 503.16,
                    "mean_roc_auc": 0.5227,
                    "mean_f1": 0.7058,
                },
                "paper_only": True,
                "shadow_only": True,
            }
        ],
        "smart_futuros_training_executive_pack": [
            {
                "status": "warning",
                "reason": "evidence_consolidated_no_promotion",
                "decision": "MANTER_EM_RESEARCH",
                "consolidated_kpis": {
                    "eligible_rows": 2392,
                    "blocked_rows": 666,
                    "original_net_pnl": 549.77,
                },
                "paper_only": True,
                "shadow_only": True,
            }
        ],
        "qlib_ocr_v11_shadow_model_candidate_registry_report": [
            {
                "status": "warning",
                "reason": "research_candidate_registered_without_promotion",
                "decision": "MANTER_EM_RESEARCH",
                "candidate_registry_status": "registered_research_only",
                "promotion_status": "blocked",
                "promotion_eligible": False,
                "registers_model": False,
                "paper_only": True,
                "shadow_only": True,
            }
        ],
        "ai_shadow_online_feedback_learning_loop_report": [
            {
                "status": "warning",
                "reason": "feedback_recorded_without_training",
                "decision": "MANTER_EM_RESEARCH",
                "learning_action": "record_only",
                "training_allowed": False,
                "promotion_allowed": False,
                "event_count": 9,
                "paper_only": True,
                "shadow_only": True,
            }
        ],
        "freqtrade_paper_ai_selector_integration_report": [
            {
                "status": "warning",
                "reason": "selector_observed_without_operational_authority",
                "decision": "MANTER_EM_RESEARCH",
                "selector_status": "observe_only_blocked",
                "selector_authority": "none",
                "observation_count": 8,
                "paper_signal_mutation_allowed": False,
                "paper_only": True,
                "shadow_only": True,
            }
        ],
    }


def write_research_sources(root: Path) -> None:
    for source_key, relative_path in RESEARCH_SOURCE_PATHS.items():
        write_json(root, relative_path, research_payloads()[source_key][0])


def test_normalizer_keeps_research_gate_blocked_and_advisory() -> None:
    result = normalize_ai_training_research_command_center(research_payloads())
    assert result["research_gate_status"] == "BLOCKED"
    assert result["section_status"] == "WARNING"
    assert result["decision"] == "MANTER_EM_RESEARCH"
    assert result["authority"] == "advisory_only"
    assert result["operational_authority"] is False


def test_normalizer_builds_all_eight_branch_cards() -> None:
    cards = normalize_ai_training_research_command_center(research_payloads())[
        "branch_cards"
    ]
    assert [card["branch_id"] for card in cards] == [
        "branch01_dataset_alignment",
        "branch02_tp_sl_grid",
        "branch03_walkforward_montecarlo",
        "branch04_qlib_training",
        "branch05_executive_report",
        "branch06_candidate_registry",
        "branch07_feedback_loop",
        "branch08_freqtrade_selector",
    ]
    assert all(card["advisory_only"] is True for card in cards)


def test_branch01_and_branch02_use_canonical_paths() -> None:
    assert (
        RESEARCH_SOURCE_PATHS["ocr_v11_research_dataset_audit"]
        == "data/reports/ocr_v11_research_dataset_audit.json"
    )
    assert (
        RESEARCH_SOURCE_PATHS["ocr_v11_tp_sl_grid_summary"]
        == "data/reports/ocr_v11_tp_sl_grid_summary.json"
    )
    all_paths = set(RESEARCH_SOURCE_PATHS.values())
    assert "data/reports/ocr_v11_research_dataset_summary.json" not in all_paths
    assert "data/reports/ocr_v11_tp_sl_grid_simulator_summary.json" not in all_paths


def test_missing_sources_are_optional_not_errors() -> None:
    result = normalize_ai_training_research_command_center({})
    assert result["section_status"] == "MISSING_OPTIONAL"
    assert result["research_gate_status"] == "BLOCKED"
    assert len(result["missing_optional_sources"]) == 8
    assert {card["status"] for card in result["branch_cards"]} == {
        "MISSING_OPTIONAL"
    }


def test_research_inputs_are_json_only_without_models_or_parquet() -> None:
    assert all(path.endswith(".json") for path in RESEARCH_SOURCE_PATHS.values())
    serialized = json.dumps(RESEARCH_SOURCE_PATHS).lower()
    assert ".parquet" not in serialized
    assert ".joblib" not in serialized
    assert ".sqlite" not in serialized


def test_safety_flags_are_fail_closed() -> None:
    result = normalize_ai_training_research_command_center(research_payloads())
    assert result["safety_flags"] == SAFETY_FLAGS
    assert result["safety_flags"]["paper_only"] is True
    assert result["safety_flags"]["shadow_only"] is True
    assert all(
        value is False
        for key, value in result["safety_flags"].items()
        if key not in {"paper_only", "shadow_only"}
    )


def test_builder_includes_advisory_section_without_changing_authoritative_status(
    tmp_path: Path,
) -> None:
    baseline = build_ai_governance_snapshot(context(tmp_path))
    write_research_sources(tmp_path)
    snapshot = build_ai_governance_snapshot(context(tmp_path))
    section = snapshot["sections"]["ai_training_research_command_center"]
    assert section["research_gate_status"] == "BLOCKED"
    assert section["authority"] == "advisory_only"
    assert snapshot["status_summary"] == baseline["status_summary"]


def test_research_blockers_never_enter_global_blocker_lists(tmp_path: Path) -> None:
    write_research_sources(tmp_path)
    result = build_all_dashboard_snapshots(context(tmp_path))
    ai_snapshot = result["snapshots"]["dashboard_ai_governance_snapshot.json"]
    blockers = set(
        ai_snapshot["sections"]["ai_training_research_command_center"]["blockers"]
    )
    summary = result["summary"]
    for key in (
        "global_blocking_reasons",
        "runtime_evidence_blocking_reasons",
        "combined_blocking_reasons",
    ):
        assert blockers.isdisjoint(set(summary[key]))


def test_source_catalog_marks_all_research_sources_optional() -> None:
    contracts = {
        source.path: source
        for source in sources_for_page(DashboardPageId.ai_governance)
    }
    for path in RESEARCH_SOURCE_PATHS.values():
        assert contracts[path].source_kind is SourceKind.OPTIONAL_EXISTING_SOURCE


def test_component_renders_only_snapshot_payload() -> None:
    section = normalize_ai_training_research_command_center(research_payloads())
    snapshot = {"sections": {"ai_training_research_command_center": section}}
    ui = FakeUi()
    render_ai_training_research_command_center(snapshot, ui=ui)
    assert extract_ai_training_research_command_center(snapshot) == section
    assert any(name == "subheader" for name, _value in ui.events)
    assert any(name == "dataframe" for name, _value in ui.events)
    assert any(
        name == "caption" and "sem autoridade operacional" in str(value).lower()
        for name, value in ui.events
    )


def test_component_has_no_direct_file_reads() -> None:
    tree = ast.parse(COMPONENT.read_text(encoding="utf-8"))
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
    assert "pathlib" not in imports
    text = COMPONENT.read_text(encoding="utf-8")
    assert "read_text(" not in text
    assert "read_bytes(" not in text
    assert "open(" not in text


def test_page05_remains_importable_and_renders_command_center() -> None:
    module = load_page_module(PAGE)
    assert callable(module.main)
    assert callable(module.render_page)
    assert "ai_training_research_command_center" in module.REQUIRED_SECTIONS
    section = normalize_ai_training_research_command_center(research_payloads())
    snapshot = valid_snapshot(module.EXPECTED_SCHEMA_VERSION, module.REQUIRED_SECTIONS)
    snapshot["sections"]["ai_training_research_command_center"] = {
        "status": section["section_status"],
        "reason": "research_evidence_advisory_only",
        **section,
    }
    ui = FakeUi()
    module.render_page(snapshot, ui=ui)
    assert any(
        name == "subheader" and value == "AI Training Research Command Center"
        for name, value in ui.events
    )


def test_semantic_page_count_remains_exactly_eight() -> None:
    assert len(DashboardPageId) == 8
    assert len(OFFICIAL_PAGE_CONTRACTS) == 8
    assert len(list((ROOT / "smartcrypto/dashboard/pages").glob("[0-9][0-9]_*.py"))) == 8


def test_snapshot_builder_custom_output_does_not_touch_runtime_reports(
    tmp_path: Path,
) -> None:
    write_research_sources(tmp_path)
    source_bytes = {
        path: (tmp_path / path).read_bytes() for path in RESEARCH_SOURCE_PATHS.values()
    }
    output = tmp_path / "temporary_snapshots"
    build_context = create_dashboard_build_context(
        tmp_path,
        output_dir=output,
        runtime_mode="paper",
        strict=False,
        allow_writes_to_output_dir=True,
    )
    result = build_all_dashboard_snapshots(build_context)
    assert result["snapshots"]
    assert output.is_dir()
    assert source_bytes == {
        path: (tmp_path / path).read_bytes() for path in RESEARCH_SOURCE_PATHS.values()
    }
    assert not list((tmp_path / "data/reports").glob("dashboard_*_snapshot.json"))


def test_new_modules_have_no_forbidden_operational_imports() -> None:
    forbidden = {
        "ccxt",
        "joblib",
        "pickle",
        "sqlite3",
        "subprocess",
        "requests",
        "httpx",
    }
    for source in (NORMALIZER, COMPONENT):
        tree = ast.parse(source.read_text(encoding="utf-8"))
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
