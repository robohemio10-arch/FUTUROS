from __future__ import annotations

import ast
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from smartcrypto.dashboard.components.runtime_freshness_producer_entrypoint_static_safety import (
    entrypoint_static_safety_finding_rows,
    entrypoint_static_safety_rows,
    entrypoint_static_safety_summary_row,
    runtime_freshness_producer_entrypoint_static_safety_view,
)
from smartcrypto.ops.dashboard_snapshots.build_context import create_dashboard_build_context
from smartcrypto.ops.dashboard_snapshots.builder_registry import build_all_dashboard_snapshots
from smartcrypto.ops.dashboard_snapshots.runtime_freshness_producer_entrypoint_static_safety import (
    audit_runtime_freshness_producer_entrypoint_static_safety,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    ROOT
    / "scripts"
    / "audit_runtime_freshness_producer_entrypoint_static_safety_v1.py"
)
NOW = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)


def test_real_entrypoints_are_audited_and_safe() -> None:
    payload = audit_runtime_freshness_producer_entrypoint_static_safety(
        project_root=ROOT,
        now_utc=NOW,
    )

    assert payload["status"] == "ok"
    assert payload["entrypoints_total"] == 3
    assert payload["entrypoints_ok_total"] == 3
    assert [row["producer_id"] for row in payload["entrypoint_rows"]] == [
        "market_data_health_audit",
        "kill_switch_state_refresh",
        "runtime_safety_config_validation",
    ]


def test_canonical_fallback_works_when_runtime_contract_report_absent(
    tmp_path: Path,
) -> None:
    _write_all_entrypoints(tmp_path)

    payload = audit_runtime_freshness_producer_entrypoint_static_safety(
        project_root=tmp_path,
        now_utc=NOW,
    )

    assert payload["status"] == "warning"
    assert payload["reason"] == "canonical_contract_fallback_used"
    assert payload["entrypoints_ok_total"] == 3


def test_missing_entrypoint_blocks(tmp_path: Path) -> None:
    _write_all_entrypoints(tmp_path)
    (tmp_path / "scripts/set_kill_switch.py").unlink()

    payload = _audit_temp(tmp_path)

    row = _row(payload, "kill_switch_state_refresh")
    assert payload["status"] == "blocked"
    assert row["exists"] is False
    assert row["status"] == "blocked"
    assert "scripts/set_kill_switch.py" in payload["missing_entrypoints"]


def test_invalid_python_blocks(tmp_path: Path) -> None:
    _write_all_entrypoints(tmp_path)
    (tmp_path / "scripts/run_market_data_health_audit.py").write_text(
        "def broken(:\n",
        encoding="utf-8",
    )

    payload = _audit_temp(tmp_path)

    row = _row(payload, "market_data_health_audit")
    assert payload["status"] == "blocked"
    assert row["parseable_python"] is False
    assert any("invalid_python" in finding for finding in row["static_findings"])


def test_ccxt_import_blocks_private_exchange(tmp_path: Path) -> None:
    _write_all_entrypoints(
        tmp_path,
        market_extra="import ccxt\n",
    )

    payload = _audit_temp(tmp_path)

    row = _row(payload, "market_data_health_audit")
    assert payload["status"] == "blocked"
    assert row["private_exchange_usage_detected"] is True
    assert payload["private_exchange_findings"]


def test_shell_true_blocks_subprocess(tmp_path: Path) -> None:
    _write_all_entrypoints(
        tmp_path,
        market_extra="subprocess.run(['python', '--version'], shell=True)\n",
        market_imports="import subprocess\n",
    )

    payload = _audit_temp(tmp_path)

    row = _row(payload, "market_data_health_audit")
    assert payload["status"] == "blocked"
    assert row["subprocess_usage_detected"] is True
    assert payload["subprocess_findings"]


def test_order_submission_blocks(tmp_path: Path) -> None:
    _write_all_entrypoints(
        tmp_path,
        market_extra="exchange.create_order('BTC/USDT', 'market', 'buy', 1)\n",
    )

    payload = _audit_temp(tmp_path)

    row = _row(payload, "market_data_health_audit")
    assert payload["status"] == "blocked"
    assert row["order_submission_detected"] is True


def test_unsafe_live_canary_or_order_flags_block(tmp_path: Path) -> None:
    _write_all_entrypoints(
        tmp_path,
        safety_extra="live_trading_enabled = True\n",
    )

    payload = _audit_temp(tmp_path)

    row = _row(payload, "runtime_safety_config_validation")
    assert payload["status"] == "blocked"
    assert row["live_or_canary_enable_detected"] is True


def test_output_outside_allowed_prefix_blocks(tmp_path: Path) -> None:
    _write_all_entrypoints(tmp_path)
    contracts = _contracts_payload(
        safety_output="tmp/runtime_safety_audit_config.json",
        safety_hint=(
            "python scripts/validate_runtime_safety_config.py "
            "--config config/paper.example.yml --environment paper "
            "--report tmp/runtime_safety_audit_config.json --strict"
        ),
    )

    payload = audit_runtime_freshness_producer_entrypoint_static_safety(
        project_root=tmp_path,
        now_utc=NOW,
        producer_contracts=contracts,
    )

    row = _row(payload, "runtime_safety_config_validation")
    assert payload["status"] == "blocked"
    assert row["output_path_supported"] is False
    assert row["unsafe_write_detected"] is True


def test_cli_flags_expected_are_validated(tmp_path: Path) -> None:
    _write_all_entrypoints(tmp_path, market_flags=("--report", "--strict"))

    payload = _audit_temp(tmp_path)

    row = _row(payload, "market_data_health_audit")
    assert payload["status"] == "blocked"
    assert row["cli_compatible"] is False
    assert "--runtime-candles" in row["missing_cli_flags"]


def test_kill_switch_refresh_contract_requires_enabled_true(tmp_path: Path) -> None:
    _write_all_entrypoints(tmp_path)
    contracts = _contracts_payload(
        kill_hint=(
            "python scripts/set_kill_switch.py --enabled false "
            "--reason unsafe --path data/runtime/kill_switch.json"
        )
    )

    payload = audit_runtime_freshness_producer_entrypoint_static_safety(
        project_root=tmp_path,
        now_utc=NOW,
        producer_contracts=contracts,
    )

    row = _row(payload, "kill_switch_state_refresh")
    assert payload["status"] == "blocked"
    assert row["kill_switch_disable_detected"] is True


def test_cli_no_write_and_write_report_only(tmp_path: Path) -> None:
    _write_all_entrypoints(tmp_path)
    before = {path.relative_to(tmp_path) for path in tmp_path.rglob("*") if path.is_file()}

    no_write = _run_cli(tmp_path)
    assert no_write.returncode == 0
    assert json.loads(no_write.stdout)["status"] == "warning"
    assert {path.relative_to(tmp_path) for path in tmp_path.rglob("*") if path.is_file()} == before

    write = _run_cli(tmp_path, "--write-report")
    assert write.returncode == 0
    output = (
        tmp_path
        / "data/reports/runtime_freshness_producer_entrypoint_static_safety_audit_v1.json"
    )
    assert output.is_file()
    after = {path.relative_to(tmp_path) for path in tmp_path.rglob("*") if path.is_file()}
    assert after - before == {
        Path("data/reports/runtime_freshness_producer_entrypoint_static_safety_audit_v1.json")
    }
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "warning"


def test_snapshot_integration_does_not_change_authoritative_blockers() -> None:
    context = create_dashboard_build_context(
        ROOT,
        output_dir=ROOT / "data/reports",
        now_utc=NOW,
        runtime_mode="paper",
        strict=False,
        allow_writes_to_output_dir=False,
    )
    result = build_all_dashboard_snapshots(context)
    summary = result["summary"]
    global_snapshot = result["snapshots"]["dashboard_global_status_snapshot.json"]
    infrastructure = result["snapshots"]["dashboard_infrastructure_snapshot.json"]
    active_controls = result["snapshots"]["dashboard_active_controls_snapshot.json"]

    assert global_snapshot["global_blocking_reasons"] == summary["global_blocking_reasons"]
    assert global_snapshot["combined_blocking_reasons"] == summary[
        "combined_blocking_reasons"
    ]
    payload = summary["runtime_freshness_producer_entrypoint_static_safety"]
    assert global_snapshot["runtime_freshness_producer_entrypoint_static_safety"] == payload
    assert infrastructure["runtime_freshness_producer_entrypoint_static_safety"] == payload
    assert active_controls["runtime_freshness_producer_entrypoint_static_safety"] == payload
    assert "runtime_freshness_producer_entrypoint_static_safety" in infrastructure[
        "sections"
    ]
    assert "runtime_freshness_producer_entrypoint_static_safety" in active_controls[
        "sections"
    ]


def test_component_reads_only_static_safety_payload() -> None:
    payload = audit_runtime_freshness_producer_entrypoint_static_safety(
        project_root=ROOT,
        now_utc=NOW,
    )
    snapshot = {
        "sections": {
            "runtime_freshness_producer_entrypoint_static_safety": {"data": payload}
        }
    }

    assert runtime_freshness_producer_entrypoint_static_safety_view(snapshot) == payload
    assert entrypoint_static_safety_summary_row(payload)["manual_execution_only"] is True
    assert len(entrypoint_static_safety_rows(payload)) == 3
    assert entrypoint_static_safety_finding_rows(payload) == []


def test_static_safety_has_no_forbidden_imports_or_calls_in_dashboard_layer() -> None:
    paths = (
        ROOT
        / "smartcrypto/ops/dashboard_snapshots/runtime_freshness_producer_entrypoint_static_safety.py",
        ROOT
        / "smartcrypto/dashboard/components/runtime_freshness_producer_entrypoint_static_safety.py",
        ROOT / "scripts/audit_runtime_freshness_producer_entrypoint_static_safety_v1.py",
    )
    forbidden_imports = {"ccxt", "requests", "httpx", "aiohttp", "subprocess", "yaml"}
    forbidden_calls = {
        "create_order",
        "cancel_order",
        "fetch_balance",
        "fetch_open_orders",
        "send_message",
        "post",
        "popen",
    }

    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = {alias.name.split(".")[0] for alias in node.names}
                assert not roots & forbidden_imports
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".")[0] not in forbidden_imports
            elif isinstance(node, ast.Call):
                assert _call_name(node.func) not in forbidden_calls


def _audit_temp(root: Path) -> dict[str, object]:
    return audit_runtime_freshness_producer_entrypoint_static_safety(
        project_root=root,
        now_utc=NOW,
        producer_contracts=_contracts_payload(),
    )


def _write_all_entrypoints(
    root: Path,
    *,
    market_flags: tuple[str, ...] = (
        "--runtime-candles",
        "--ticker",
        "--order-book",
        "--trades",
        "--rest-snapshot",
        "--ws-heartbeat",
        "--report",
        "--strict",
    ),
    market_imports: str = "",
    market_extra: str = "",
    safety_extra: str = "",
) -> None:
    scripts = root / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "run_market_data_health_audit.py").write_text(
        _entrypoint_source(market_flags, market_imports, market_extra),
        encoding="utf-8",
    )
    (scripts / "set_kill_switch.py").write_text(
        _entrypoint_source(
            ("--enabled", "--reason", "--path"),
            "",
            "target = 'data/runtime/kill_switch.json'\n",
        ),
        encoding="utf-8",
    )
    (scripts / "validate_runtime_safety_config.py").write_text(
        _entrypoint_source(
            ("--config", "--environment", "--report", "--strict"),
            "",
            "target = 'data/runtime/runtime_safety_audit_config.json'\n"
            + safety_extra,
        ),
        encoding="utf-8",
    )


def _entrypoint_source(
    flags: tuple[str, ...],
    imports: str,
    extra: str,
) -> str:
    add_args = "\n".join(f"    parser.add_argument('{flag}')" for flag in flags)
    indented_extra = "".join(
        f"    {line}\n" for line in extra.splitlines() if line.strip()
    )
    return (
        "from __future__ import annotations\n"
        "import argparse\n"
        f"{imports}"
        "\n"
        "def parse_args(argv=None):\n"
        "    parser = argparse.ArgumentParser()\n"
        f"{add_args}\n"
        "    return parser.parse_args(argv)\n"
        "\n"
        "def main(argv=None):\n"
        "    args = parse_args(argv)\n"
        f"{indented_extra}"
        "    return 0\n"
    )


def _contracts_payload(
    *,
    kill_hint: str = (
        "python scripts/set_kill_switch.py --enabled true "
        "--reason manual_runtime_safety_freshness_refresh "
        "--path data/runtime/kill_switch.json"
    ),
    safety_output: str = "data/runtime/runtime_safety_audit_config.json",
    safety_hint: str = (
        "python scripts/validate_runtime_safety_config.py "
        "--config config/paper.example.yml --environment paper "
        "--report data/runtime/runtime_safety_audit_config.json --strict"
    ),
) -> dict[str, object]:
    return {
        "schema_version": "runtime_freshness_producer_contracts_audit_v1",
        "status": "warning",
        "generated_at_utc": "2026-06-15T12:00:00Z",
        "producer_contracts": [
            {
                "contract_id": "market_data_health_manual_refresh_v1",
                "producer_id": "market_data_health_audit",
                "domain": "market_data",
                "expected_artifact_path": "data/reports/market_data_health_audit_report.json",
                "target_canonical_path": "data/reports/market_data_health_audit_report.json",
                "manual_execution_hint": (
                    "python scripts/run_market_data_health_audit.py "
                    "--runtime-candles data/runtime/market_health/candles.json "
                    "--ticker data/runtime/market_health/ticker.json "
                    "--order-book data/runtime/market_health/order_book.json "
                    "--trades data/runtime/market_health/trades.jsonl "
                    "--rest-snapshot data/runtime/market_health/rest_snapshot.json "
                    "--ws-heartbeat data/runtime/market_health/ws_heartbeat.json "
                    "--report data/reports/market_data_health_audit_report.json --strict"
                ),
            },
            {
                "contract_id": "kill_switch_runtime_manual_refresh_v1",
                "producer_id": "kill_switch_state_refresh",
                "domain": "portfolio_risk",
                "expected_artifact_path": "data/runtime/kill_switch.json",
                "target_canonical_path": "data/runtime/kill_switch.json",
                "manual_execution_hint": kill_hint,
            },
            {
                "contract_id": "runtime_safety_config_manual_validation_v1",
                "producer_id": "runtime_safety_config_validation",
                "domain": "active_controls",
                "expected_artifact_path": safety_output,
                "target_canonical_path": safety_output,
                "manual_execution_hint": safety_hint,
            },
        ],
    }


def _row(payload: dict[str, object], producer_id: str) -> dict[str, object]:
    rows = payload["entrypoint_rows"]
    assert isinstance(rows, list)
    return next(row for row in rows if row["producer_id"] == producer_id)


def _run_cli(project_root: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--project-root",
            str(project_root),
            "--json",
            *extra,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _call_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id.lower()
    if isinstance(node, ast.Attribute):
        return node.attr.lower()
    return ""
