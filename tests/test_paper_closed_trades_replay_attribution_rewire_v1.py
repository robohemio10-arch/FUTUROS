from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

from smartcrypto.research.ocr_master_candle_shadow_observation_replay import (
    build_shadow_observation_replay_report,
)
from smartcrypto.research.paper_closed_trades_readonly_source_contract import (
    build_paper_closed_trades_readonly_source_contract_report,
)
from smartcrypto.research.paper_closed_trades_shadow_rule_attribution import (
    build_paper_closed_trades_shadow_rule_attribution_report,
)


def _closed_rows() -> list[dict[str, object]]:
    return [
        {
            "trade_id": "t1",
            "order_id": "ord-1",
            "symbol": "BTCUSDT",
            "side": "long",
            "open_time_utc": "2026-06-01T10:00:00Z",
            "close_time_utc": "2026-06-01T10:10:00Z",
            "open_rate": 100.0,
            "close_rate": 110.0,
            "profit_abs": 10.0,
        },
        {
            "trade_id": "t2",
            "order_id": "ord-2",
            "symbol": "ETHUSDT",
            "side": "short",
            "open_time_utc": "2026-06-01T11:00:00Z",
            "close_time_utc": "2026-06-01T11:10:00Z",
            "open_rate": 200.0,
            "close_rate": 210.0,
            "profit_abs": -5.0,
        },
    ]


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _write_observation_design(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": "blocked",
        "decision": "MANTER_EM_RESEARCH",
        "observation_records": [
            {
                "survivor_rule_id": "survivor_btc_long",
                "survivor_expression": "symbol_norm == 'BTCUSDT' AND side_norm == 'long'",
                "dimensions": ["symbol_norm", "side_norm"],
                "values": ["BTCUSDT", "long"],
                "expected_value_delta": 0.2,
                "operational_authority": False,
            }
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _build_contract(project: Path, *, partial_sample: bool = False) -> Path:
    closed_path = project / "data" / "trades" / "inbox" / "freqtrade_paper_closed_trades.csv"
    _write_csv(closed_path, _closed_rows())
    contract_path = project / "data" / "reports" / "paper_closed_trades_readonly_source_contract_v1.json"
    report = build_paper_closed_trades_readonly_source_contract_report(
        project_root=project,
        allow_runtime_read=True,
        source_paths=["data/trades/inbox/freqtrade_paper_closed_trades.csv"],
        write=True,
        no_write=False,
        output_report=contract_path,
    )
    assert report["source_contract_status"] == "ok"
    assert report["recommended_join_key"] == "order_id"
    if partial_sample:
        payload = json.loads(contract_path.read_text(encoding="utf-8"))
        payload["normalized_rows_sample"] = payload["normalized_rows_sample"][:1]
        payload["normalized_closed_trade_count"] = 2
        contract_path.write_text(json.dumps(payload), encoding="utf-8")
    return contract_path


def _build_replay(project: Path, contract_path: Path) -> Path:
    design_path = project / "data" / "reports" / "ocr_master_candle_shadow_observation_design_v1.json"
    replay_path = project / "data" / "reports" / "ocr_master_candle_shadow_observation_replay_v1.json"
    _write_observation_design(design_path)
    replay = build_shadow_observation_replay_report(
        project_root=project,
        allow_runtime_read=True,
        observation_design_report=design_path,
        closed_trades_source_contract=contract_path,
        write=True,
        no_write=False,
        output_report=replay_path,
    )
    assert replay["replay_metrics"]["replay_trade_count"] == 2
    return replay_path


def test_replay_consumes_closed_trades_source_contract(tmp_path: Path) -> None:
    contract_path = _build_contract(tmp_path, partial_sample=True)
    design_path = tmp_path / "data" / "reports" / "ocr_master_candle_shadow_observation_design_v1.json"
    _write_observation_design(design_path)

    report = build_shadow_observation_replay_report(
        project_root=tmp_path,
        allow_runtime_read=True,
        observation_design_report=design_path,
        closed_trades_source_contract=contract_path,
    )

    assert report["closed_trade_count"] == 2
    assert report["closed_trades_source_contract_path"] == "data/reports/paper_closed_trades_readonly_source_contract_v1.json"
    assert report["closed_trades_contract_join_key"] == "order_id"
    assert report["trades_source_path"] == "data/trades/inbox/freqtrade_paper_closed_trades.csv"


def test_replay_produces_trade_count_from_closed_trades_contract_fixture(tmp_path: Path) -> None:
    contract_path = _build_contract(tmp_path)
    design_path = tmp_path / "data" / "reports" / "ocr_master_candle_shadow_observation_design_v1.json"
    _write_observation_design(design_path)

    report = build_shadow_observation_replay_report(
        project_root=tmp_path,
        allow_runtime_read=True,
        observation_design_report=design_path,
        closed_trades_source_contract=contract_path,
    )

    assert report["replay_metrics"]["replay_trade_count"] == 2
    assert report["replay_metrics"]["would_allow_count"] == 1
    assert report["replay_metrics"]["would_block_count"] == 1
    assert report["replay_metrics"]["replay_rows"][0]["order_id"] == "ord-1"


def test_replay_preserves_research_only_safety_flags(tmp_path: Path) -> None:
    contract_path = _build_contract(tmp_path)
    design_path = tmp_path / "data" / "reports" / "ocr_master_candle_shadow_observation_design_v1.json"
    _write_observation_design(design_path)

    report = build_shadow_observation_replay_report(
        project_root=tmp_path,
        allow_runtime_read=True,
        observation_design_report=design_path,
        closed_trades_source_contract=contract_path,
    )

    assert report["decision"] == "MANTER_EM_RESEARCH"
    assert report["operational_authority"] is False
    assert report["paper_observation_allowed"] is False
    assert report["ready_for_shadow_observation"] is False
    assert report["sends_orders"] is False
    assert report["changes_risk"] is False
    assert report["writes_runtime"] is False


def test_attribution_consumes_replay_and_closed_trades_contract(tmp_path: Path) -> None:
    contract_path = _build_contract(tmp_path)
    replay_path = _build_replay(tmp_path, contract_path)

    report = build_paper_closed_trades_shadow_rule_attribution_report(
        project_root=tmp_path,
        allow_runtime_read=True,
        shadow_replay_report=replay_path,
        closed_trades_source_contract=contract_path,
    )

    assert report["closed_trade_count"] == 2
    assert report["replay_row_count"] == 2
    assert report["attributed_trade_count"] == 2
    assert report["unattributed_trade_count"] == 0


def test_attribution_produces_attributed_trade_count_from_fixture(tmp_path: Path) -> None:
    contract_path = _build_contract(tmp_path)
    replay_path = _build_replay(tmp_path, contract_path)

    report = build_paper_closed_trades_shadow_rule_attribution_report(
        project_root=tmp_path,
        allow_runtime_read=True,
        shadow_replay_report=replay_path,
        closed_trades_source_contract=contract_path,
    )

    assert report["attributed_trade_count"] > 0
    assert report["attribution_table_sample"][0]["attribution_method"] == "shadow_replay_trade_id"


def test_attribution_uses_recommended_join_key_order_id(tmp_path: Path) -> None:
    contract_path = _build_contract(tmp_path)
    replay_path = _build_replay(tmp_path, contract_path)

    report = build_paper_closed_trades_shadow_rule_attribution_report(
        project_root=tmp_path,
        allow_runtime_read=True,
        shadow_replay_report=replay_path,
        closed_trades_source_contract=contract_path,
    )

    assert report["recommended_join_key"] == "order_id"
    assert report["join_key_used"] == "order_id"
    assert {row["join_value"] for row in report["attribution_table_sample"]} == {"ord-1", "ord-2"}


def test_rewire_keeps_paper_observation_blocked(tmp_path: Path) -> None:
    contract_path = _build_contract(tmp_path)
    replay_path = _build_replay(tmp_path, contract_path)
    report = build_paper_closed_trades_shadow_rule_attribution_report(
        project_root=tmp_path,
        allow_runtime_read=True,
        shadow_replay_report=replay_path,
        closed_trades_source_contract=contract_path,
    )

    assert report["decision"] == "MANTER_EM_RESEARCH"
    assert report["paper_observation_allowed"] is False
    assert report["ready_for_shadow_observation"] is False
    assert report["operational_authority"] is False
    assert report["can_promote_rules"] is False
    assert report["can_apply_to_freqtrade"] is False
    assert report["can_apply_to_risk_manager"] is False


def test_rewire_does_not_write_runtime_sqlite_or_parquet(tmp_path: Path) -> None:
    contract_path = _build_contract(tmp_path)
    replay_path = _build_replay(tmp_path, contract_path)
    build_paper_closed_trades_shadow_rule_attribution_report(
        project_root=tmp_path,
        allow_runtime_read=True,
        shadow_replay_report=replay_path,
        closed_trades_source_contract=contract_path,
        write=True,
        no_write=False,
    )

    assert not (tmp_path / "data" / "runtime").exists()
    assert not list(tmp_path.rglob("*.sqlite"))
    assert not list(tmp_path.rglob("*.db"))
    assert not list(tmp_path.rglob("*.parquet"))


def test_cli_replay_with_source_contract_json_executes(tmp_path: Path) -> None:
    contract_path = _build_contract(tmp_path)
    design_path = tmp_path / "data" / "reports" / "ocr_master_candle_shadow_observation_design_v1.json"
    _write_observation_design(design_path)
    script = Path("scripts/build_ocr_master_candle_shadow_observation_replay_v1.py").resolve()

    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--project-root",
            str(tmp_path),
            "--allow-runtime-read",
            "--observation-design-report",
            str(design_path),
            "--closed-trades-source-contract",
            str(contract_path),
            "--no-write",
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["replay_metrics"]["replay_trade_count"] == 2
    assert payload["decision"] == "MANTER_EM_RESEARCH"
    assert payload["paper_observation_allowed"] is False


def test_cli_attribution_with_source_contract_json_executes(tmp_path: Path) -> None:
    contract_path = _build_contract(tmp_path)
    replay_path = _build_replay(tmp_path, contract_path)
    script = Path("scripts/build_paper_closed_trades_shadow_rule_attribution_v1.py").resolve()

    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--project-root",
            str(tmp_path),
            "--allow-runtime-read",
            "--shadow-replay-report",
            str(replay_path),
            "--closed-trades-source-contract",
            str(contract_path),
            "--no-write",
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["attributed_trade_count"] == 2
    assert payload["join_key_used"] == "order_id"
    assert payload["decision"] == "MANTER_EM_RESEARCH"
    assert payload["paper_observation_allowed"] is False
