from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pandas as pd

from smartcrypto.learning.feature_contracts import build_dataset_manifest, build_feature_contract
from smartcrypto.learning.qlib_backend_gate import build_qlib_research_backend_gate_report
from smartcrypto.learning.qlib_backend_gate.backend_probe import REQUIRED_MODULES
from smartcrypto.learning.qlib_trainer import build_qlib_institutional_ranking_trainer_report
from smartcrypto.learning.target_store import build_financial_label_target_store_report
from smartcrypto.learning.walkforward import build_walkforward_anti_leakage_report


def fake_probe(status: str) -> dict[str, Any]:
    module_results = {
        module: {
            "module": module,
            "importable": status in {"available", "partial"} and not (status == "partial" and module.endswith(".model")),
            "origin": f"/fake/site-packages/{module.replace('.', '/')}.py",
            "reason": "module_spec_found",
        }
        for module in REQUIRED_MODULES
    }
    importable = status in {"available", "partial"}
    version = "1.0.0" if status == "available" else None
    unsupported: list[str] = []
    if status == "partial":
        unsupported.append("qlib_version_not_detected")
        unsupported.append("missing_required_modules:qlib.contrib.model")
    return {
        "qlib_backend_status": status,
        "qlib_importable": importable,
        "qlib_version": version,
        "qlib_package_path": "/fake/site-packages/qlib/__init__.py" if importable else None,
        "required_modules": list(REQUIRED_MODULES),
        "module_probe_results": module_results,
        "unsupported_reasons": unsupported,
    }


def write_project_inputs(root: Path, rows: int = 36) -> Path:
    dataset_path = root / "data" / "feedback" / "training_microbatches" / "2026-07-01.parquet"
    dataset_path.parent.mkdir(parents=True, exist_ok=True)
    start = pd.Timestamp("2026-07-01T00:00:00Z")
    payload: list[dict[str, Any]] = []
    for index in range(rows):
        opened = start + pd.Timedelta(days=index)
        is_win = index % 3 == 0
        net_pnl = 1.0 if is_win else -0.5
        payload.append(
            {
                "event_id": f"e{index}",
                "order_id": f"o{index}",
                "trade_id": f"t{index}",
                "symbol_norm": "BTCUSDT" if index % 2 == 0 else "ETHUSDT",
                "side": "long" if index % 2 == 0 else "short",
                "open_time_utc": opened.isoformat(),
                "close_time_utc": (opened + pd.Timedelta(minutes=30)).isoformat(),
                "duration_seconds": 1800,
                "is_closed": True,
                "label_win_loss": "win" if is_win else "loss",
                "label_sign": 1 if is_win else -1,
                "net_pnl": net_pnl,
                "gross_pnl": net_pnl,
                "profit_ratio": 0.01 if is_win else -0.005,
                "exit_price": 101.0 if is_win else 99.0,
                "exit_reason": "roi" if is_win else "stoploss",
                "roi_hit": is_win,
                "stoploss_hit": not is_win,
                "feature_side_long": 1 if index % 2 == 0 else 0,
                "feature_side_short": 0 if index % 2 == 0 else 1,
                "feature_symbol_btcusdt": 1 if index % 2 == 0 else 0,
                "feature_symbol_ethusdt": 0 if index % 2 == 0 else 1,
                "feature_entry_price": 100.0 + index,
                "feature_quantity": 0.1,
                "feature_leverage": 2.0,
            }
        )
    pd.DataFrame(payload).to_parquet(dataset_path, index=False)
    frame = pd.read_parquet(dataset_path)
    contract = build_feature_contract(frame, source_datasets=[str(dataset_path)])
    manifest = build_dataset_manifest(
        frame,
        selected_dataset_path=dataset_path,
        source_paths=[dataset_path],
        feature_contract_hash=contract["contract_hash"],
        label_columns=contract["label_columns"],
    )
    report_dir = root / "data" / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "ai_unified_feature_contract_v1.json").write_text(json.dumps(contract), encoding="utf-8")
    (report_dir / "ai_unified_dataset_manifest_v1.json").write_text(json.dumps(manifest), encoding="utf-8")
    build_financial_label_target_store_report(project_root=root, write=True)
    build_walkforward_anti_leakage_report(project_root=root, write=True)
    return dataset_path


def write_gate_report(root: Path, status: str) -> Path:
    report = build_qlib_research_backend_gate_report(project_root=root, probe_func=lambda _modules: fake_probe(status))
    path = root / "data" / "reports" / "qlib_research_backend_gate_v1.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report), encoding="utf-8")
    return path


def test_default_no_write(tmp_path: Path) -> None:
    report = build_qlib_research_backend_gate_report(project_root=tmp_path, probe_func=lambda _modules: fake_probe("unavailable"))

    assert report["status"] == "ok"
    assert report["reason"] == "qlib_backend_unavailable"
    assert report["write_requested"] is False
    assert report["write_performed"] is False
    assert not (tmp_path / "data" / "reports" / "qlib_research_backend_gate_v1.json").exists()


def test_write_outputs_only_reports(tmp_path: Path) -> None:
    report = build_qlib_research_backend_gate_report(project_root=tmp_path, write=True, probe_func=lambda _modules: fake_probe("available"))

    assert report["write_performed"] is True
    assert (tmp_path / "data" / "reports" / "qlib_research_backend_gate_v1.json").exists()
    assert (tmp_path / "data" / "reports" / "qlib_research_backend_gate_v1.md").exists()
    assert not (tmp_path / "data" / "models").exists()


def test_backend_unavailable_when_import_fails(tmp_path: Path) -> None:
    report = build_qlib_research_backend_gate_report(project_root=tmp_path, probe_func=lambda _modules: fake_probe("unavailable"))

    assert report["qlib_backend_status"] == "unavailable"
    assert report["qlib_importable"] is False


def test_backend_available_when_probe_succeeds(tmp_path: Path) -> None:
    report = build_qlib_research_backend_gate_report(project_root=tmp_path, probe_func=lambda _modules: fake_probe("available"))

    assert report["qlib_backend_status"] == "available"
    assert report["qlib_importable"] is True
    assert report["qlib_version"] == "1.0.0"


def test_backend_partial_when_required_module_missing(tmp_path: Path) -> None:
    report = build_qlib_research_backend_gate_report(project_root=tmp_path, probe_func=lambda _modules: fake_probe("partial"))

    assert report["qlib_backend_status"] == "partial"
    assert any("missing_required_modules" in reason for reason in report["unsupported_reasons"])


def test_backend_blocked_on_side_effect_detection(tmp_path: Path) -> None:
    marker = str(tmp_path / "mutated_sys_path")

    def mutating_probe(_modules: list[str] | None) -> dict[str, Any]:
        sys.path.append(marker)
        return fake_probe("available")

    try:
        report = build_qlib_research_backend_gate_report(project_root=tmp_path, probe_func=mutating_probe)
    finally:
        while marker in sys.path:
            sys.path.remove(marker)

    assert report["status"] == "blocked"
    assert report["qlib_backend_status"] == "blocked"
    assert report["runtime_isolation_status"] == "blocked"
    assert "sys_path_changed" in report["validation_errors"]


def test_dependency_contract_hash_is_deterministic(tmp_path: Path) -> None:
    first = build_qlib_research_backend_gate_report(project_root=tmp_path, probe_func=lambda _modules: fake_probe("available"))
    second = build_qlib_research_backend_gate_report(project_root=tmp_path, probe_func=lambda _modules: fake_probe("available"))

    assert first["dependency_contract_hash"] == second["dependency_contract_hash"]


def test_environment_audit_redacts_sensitive_env_values(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("PYTHONPATH", "secret-pythonpath-value")
    report = build_qlib_research_backend_gate_report(project_root=tmp_path, probe_func=lambda _modules: fake_probe("unavailable"))
    encoded = json.dumps(report)

    assert report["environment_audit"]["relevant_env_vars_present"]["PYTHONPATH"] is True
    assert "secret-pythonpath-value" not in encoded


def test_runtime_isolation_does_not_update_runtime(tmp_path: Path) -> None:
    report = build_qlib_research_backend_gate_report(project_root=tmp_path, probe_func=lambda _modules: fake_probe("available"))

    assert report["qlib_runtime_updated"] is False
    assert report["writes_runtime"] is False
    assert report["writes_sqlite"] is False
    assert report["writes_parquet"] is False


def test_gate_safety_flags_preserved(tmp_path: Path) -> None:
    report = build_qlib_research_backend_gate_report(project_root=tmp_path, probe_func=lambda _modules: fake_probe("available"))

    assert report["paper_only"] is True
    assert report["shadow_only"] is True
    assert report["live_release_allowed"] is False
    assert report["order_submission_enabled"] is False
    assert report["real_order_submission_enabled"] is False
    assert report["sends_orders"] is False
    assert report["exchange_private_access"] is False
    assert report["changes_risk"] is False


def test_trainer_blocks_train_when_gate_unavailable(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    gate_path = write_gate_report(tmp_path, "unavailable")

    report = build_qlib_institutional_ranking_trainer_report(
        project_root=tmp_path,
        train=True,
        backend_gate_report_path=gate_path,
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "qlib_backend_unavailable"
    assert report["candidate_decision"] == "BLOCKED_BACKEND_UNAVAILABLE"
    assert report["qlib_challenger_training_performed"] is False


def test_trainer_blocks_train_when_gate_partial(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    gate_path = write_gate_report(tmp_path, "partial")

    report = build_qlib_institutional_ranking_trainer_report(
        project_root=tmp_path,
        train=True,
        backend_gate_report_path=gate_path,
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "qlib_backend_partial"
    assert report["candidate_decision"] == "BLOCKED_BACKEND_UNAVAILABLE"


def test_trainer_blocks_train_when_gate_blocked(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    gate_path = write_gate_report(tmp_path, "available")
    payload = json.loads(gate_path.read_text(encoding="utf-8"))
    payload["qlib_backend_status"] = "blocked"
    gate_path.write_text(json.dumps(payload), encoding="utf-8")

    report = build_qlib_institutional_ranking_trainer_report(
        project_root=tmp_path,
        train=True,
        backend_gate_report_path=gate_path,
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "qlib_backend_blocked"
    assert report["qlib_challenger_training_performed"] is False


def test_trainer_allows_explicit_train_when_gate_available_without_promotion(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    gate_path = write_gate_report(tmp_path, "available")

    report = build_qlib_institutional_ranking_trainer_report(
        project_root=tmp_path,
        train=True,
        backend_gate_report_path=gate_path,
    )

    assert report["status"] == "ok"
    assert report["qlib_backend_status"] == "available"
    assert report["qlib_challenger_training_performed"] is True
    assert report["model_promotion_performed"] is False
    assert report["registry_write_performed"] is False


def test_cli_json_executes(tmp_path: Path) -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/audit_qlib_research_backend_gate_v1.py", "--project-root", str(tmp_path), "--json"],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["schema_version"] == "qlib_research_backend_runtime_dependency_gate_v1"
    assert payload["write_performed"] is False
    assert payload["training_requested"] is False
