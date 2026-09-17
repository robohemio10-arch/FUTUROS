from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any, NoReturn, cast


PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_OUTPUT_DIR = Path("data/reports/post_ocr_quant")

REPORT_JSON = "OFFICIAL_TRADES_MASTER_MONTE_CARLO_RISK_V1.json"

REPORT_MD = "OFFICIAL_TRADES_MASTER_MONTE_CARLO_RISK_V1.md"

SCENARIOS_CSV = "OFFICIAL_TRADES_MASTER_MONTE_CARLO_SCENARIOS_V1.csv"

RUIN_CSV = "OFFICIAL_TRADES_MASTER_MONTE_CARLO_RUIN_GRID_V1.csv"

MANIFEST_CSV = "OFFICIAL_TRADES_MASTER_MONTE_CARLO_RISK_SHA256_MANIFEST_V1.csv"


class WQ5ReportPersistenceError(ValueError):
    """Fail-closed validation error for WQ5 report persistence."""

    def __init__(
        self,
        code: str,
        **details: Any,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.details = details


def _fail(
    code: str,
    **details: Any,
) -> NoReturn:
    raise WQ5ReportPersistenceError(
        code,
        **details,
    )


def _ensure_project_root() -> None:
    value = str(PROJECT_ROOT)

    if value not in sys.path:
        sys.path.insert(
            0,
            value,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run canonical frozen WQ5 Monte Carlo / risk stress "
            "on the official post-OCR trades master."
        )
    )

    parser.add_argument(
        "--master",
        required=True,
        metavar="PATH",
    )

    parser.add_argument(
        "--method-freeze-v2",
        required=True,
        metavar="PATH",
    )

    parser.add_argument(
        "--method-freeze-v3",
        required=True,
        metavar="PATH",
    )

    parser.add_argument(
        "--btc-existing",
        required=True,
        metavar="PATH",
    )

    parser.add_argument(
        "--eth-existing",
        required=True,
        metavar="PATH",
    )

    parser.add_argument(
        "--btc-backfill",
        required=True,
        metavar="PATH",
    )

    parser.add_argument(
        "--eth-backfill",
        required=True,
        metavar="PATH",
    )

    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
    )

    parser.add_argument(
        "--write-report",
        action="store_true",
    )

    parser.add_argument(
        "--json",
        action="store_true",
    )

    return parser


def _mapping(
    value: object,
    *,
    code: str,
) -> Mapping[str, Any]:
    if not isinstance(
        value,
        Mapping,
    ):
        _fail(
            code,
        )

    return cast(
        Mapping[
            str,
            Any,
        ],
        value,
    )


def _blocked(
    *,
    reason: str,
    details: Mapping[
        str,
        Any,
    ],
    master_path: str,
) -> dict[str, Any]:
    return {
        "schema_version": ("official_trades_master_monte_carlo_risk_v1"),
        "status": "blocked",
        "engineering_status": "BLOCKED",
        "wq5_status": "BLOCKED",
        "decision": "NOT_EVALUATED",
        "quant_classification": "NOT_EVALUATED",
        "primary_acceptance_pass": False,
        "reason": reason,
        "details": dict(details),
        "master_path": master_path,
        "paper_only": True,
        "shadow_only": True,
        "research_only": True,
        "operational_authority": False,
        "changes_risk": False,
        "changes_model": False,
        "sends_orders": False,
        "exchange_private_access": False,
        "writes_master": False,
        "writes_sqlite": False,
    }


def _sha256_file(
    path: Path,
) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)

            if not chunk:
                break

            digest.update(chunk)

    return digest.hexdigest()


def _atomic_write_bytes(
    path: Path,
    content: bytes,
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )

    try:
        with os.fdopen(
            descriptor,
            "wb",
        ) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())

        os.replace(
            temporary,
            path,
        )

    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass

        raise


def _csv_bytes(
    rows: list[dict[str, Any]],
) -> bytes:
    if not rows:
        _fail(
            "csv_rows_empty",
        )

    fieldnames = list(rows[0])

    expected = set(fieldnames)

    for index, row in enumerate(rows):
        if set(row) != expected:
            _fail(
                "csv_schema_drift",
                row_index=index,
            )

    buffer = io.StringIO(newline="")

    writer = csv.DictWriter(
        buffer,
        fieldnames=fieldnames,
        lineterminator="\n",
    )

    writer.writeheader()
    writer.writerows(rows)

    return buffer.getvalue().encode("utf-8")


def _scenario_rows(
    payload: Mapping[
        str,
        Any,
    ],
) -> list[dict[str, Any]]:
    order_raw = payload.get("scenario_order")

    if not isinstance(
        order_raw,
        list,
    ):
        _fail(
            "scenario_order_missing",
        )

    scenarios = _mapping(
        payload.get("scenarios"),
        code="scenarios_missing",
    )

    rows: list[dict[str, Any]] = []

    for scenario_raw in order_raw:
        scenario_id = str(scenario_raw)

        scenario = _mapping(
            scenarios.get(scenario_id),
            code=("scenario_result_missing"),
        )

        ruin = _mapping(
            scenario.get("risk_of_ruin"),
            code=("scenario_ruin_grid_missing"),
        )

        rows.append(
            {
                "scenario_id": (scenario_id),
                "simulation_count": int(scenario["simulation_count"]),
                "terminal_p01": float(scenario["terminal_p01"]),
                "terminal_p05": float(scenario["terminal_p05"]),
                "terminal_p50": float(scenario["terminal_p50"]),
                "terminal_p95": float(scenario["terminal_p95"]),
                "terminal_p99": float(scenario["terminal_p99"]),
                "p_final_net_pnl_gt_zero": float(scenario["p_final_net_pnl_gt_zero"]),
                "max_drawdown_p50": float(scenario["max_drawdown_p50"]),
                "max_drawdown_p90": float(scenario["max_drawdown_p90"]),
                "max_drawdown_p95": float(scenario["max_drawdown_p95"]),
                "max_drawdown_p99": float(scenario["max_drawdown_p99"]),
                "terminal_loss_cvar_95": float(scenario["terminal_loss_cvar_95"]),
                "terminal_loss_cvar_99": float(scenario["terminal_loss_cvar_99"]),
                "losing_streak_p95": float(scenario["losing_streak_p95"]),
                "time_under_water_p95_trade_steps": float(
                    scenario["time_under_water_p95_trade_steps"]
                ),
                "risk_of_ruin_1x": float(ruin["1"]),
                "risk_of_ruin_1_5x": float(ruin["1.5"]),
                "risk_of_ruin_2x": float(ruin["2"]),
                "risk_of_ruin_3x": float(ruin["3"]),
                "risk_of_ruin_5x": float(ruin["5"]),
            }
        )

    return rows


def _ruin_rows(
    payload: Mapping[
        str,
        Any,
    ],
) -> list[dict[str, Any]]:
    scenario_rows = _scenario_rows(payload)

    rows: list[dict[str, Any]] = []

    columns = (
        (
            "1",
            "risk_of_ruin_1x",
        ),
        (
            "1.5",
            "risk_of_ruin_1_5x",
        ),
        (
            "2",
            "risk_of_ruin_2x",
        ),
        (
            "3",
            "risk_of_ruin_3x",
        ),
        (
            "5",
            "risk_of_ruin_5x",
        ),
    )

    for scenario in scenario_rows:
        for (
            multiple,
            column,
        ) in columns:
            rows.append(
                {
                    "scenario_id": (scenario["scenario_id"]),
                    "buffer_multiple": (multiple),
                    "risk_of_ruin": float(scenario[column]),
                }
            )

    return rows


def render_markdown(
    payload: Mapping[
        str,
        Any,
    ],
) -> str:
    population = _mapping(
        payload.get("primary_population"),
        code=("primary_population_missing"),
    )

    scenarios = _mapping(
        payload.get("scenarios"),
        code="scenarios_missing",
    )

    gates = _mapping(
        payload.get("primary_gates"),
        code=("primary_gates_missing"),
    )

    key_scenarios = (
        "baseline_iid",
        "baseline_block_b20",
        "baseline_block_b60",
        "regime_conditional_iid",
        "cost_block20_m10",
        "tail_iid_w20",
        "tail_iid_w30",
    )

    lines = [
        "# Official Trades Master Monte Carlo Risk V1",
        "",
        (f"- Engineering status: `{payload.get('engineering_status')}`"),
        (f"- WQ5 status: `{payload.get('wq5_status')}`"),
        (f"- Quant classification: `{payload.get('quant_classification')}`"),
        (f"- Primary acceptance pass: `{payload.get('primary_acceptance_pass')}`"),
        (f"- Master SHA256: `{payload.get('master_sha256')}`"),
        (f"- V3 method hash: `{payload.get('method_v3_hash')}`"),
        "",
        "## Population contract",
        "",
        "- Primary: `WQ2_EXPANDING_OOS_3066`",
        "- Secondary reference: `FULL_MASTER_3991`",
        "- Acceptance and simulated horizon use the primary population.",
        "",
        "## Primary population",
        "",
        (f"- Rows: `{population.get('rows')}`"),
        (f"- Historical Net PnL: `{float(population.get('historical_net_pnl', 0.0)):.6f}` USDT"),
        (
            "- Historical Max DD: "
            f"`{float(population.get('historical_max_drawdown', 0.0)):.6f}` USDT"
        ),
        (f"- Notional total: `{float(population.get('notional_sum_usdt', 0.0)):.6f}` USDT"),
        (
            "- Incremental execution stress: "
            f"`{float(population.get('incremental_stress_bps', 0.0)):.2f}` bps"
        ),
        (
            "- Incremental m1.0 cost total: "
            f"`{float(population.get('incremental_cost_total_m10_usdt', 0.0)):.6f}` USDT"
        ),
        "",
        "## Primary gates",
        "",
    ]

    for key in sorted(gates):
        lines.append(f"- {key}: `{bool(gates[key])}`")

    lines.extend(
        [
            "",
            "## Key scenarios",
            "",
            ("| Scenario | P(final>0) | Terminal p05 | Terminal p50 | MDD p95 | Ruin 2x |"),
            ("| --- | ---: | ---: | ---: | ---: | ---: |"),
        ]
    )

    for scenario_id in key_scenarios:
        scenario = _mapping(
            scenarios.get(scenario_id),
            code=("markdown_key_scenario_missing"),
        )

        ruin = _mapping(
            scenario.get("risk_of_ruin"),
            code=("markdown_ruin_grid_missing"),
        )

        lines.append(
            (
                f"| {scenario_id} "
                f"| {float(scenario['p_final_net_pnl_gt_zero']):.6f} "
                f"| {float(scenario['terminal_p05']):.6f} "
                f"| {float(scenario['terminal_p50']):.6f} "
                f"| {float(scenario['max_drawdown_p95']):.6f} "
                f"| {float(ruin['2']):.6f} |"
            )
        )

    lines.extend(
        [
            "",
            "## Interpretation boundary",
            "",
            (
                "- V3 cost stress is a hypothetical incremental adverse "
                "execution-friction overlay, not a reconstruction of "
                "historical realized exchange costs."
            ),
            ("- `baseline_block_b60` is the frozen negative-cluster sensitivity."),
            (
                "- `regime_conditional_iid` is required for engineering "
                "completion but is not a primary acceptance gate."
            ),
            "",
            "## Safety",
            "",
            "- PAPER / SHADOW / RESEARCH ONLY",
            "- Operational authority: `false`",
            "- Sends orders: `false`",
            "- Changes risk: `false`",
            "- Changes model: `false`",
            "",
        ]
    )

    return "\n".join(lines)


def persist_wq5_reports(
    payload: Mapping[
        str,
        Any,
    ],
    *,
    output_dir: str | Path,
) -> dict[str, str]:
    target = Path(output_dir)

    files: dict[
        str,
        bytes,
    ] = {
        REPORT_JSON: (
            json.dumps(
                dict(payload),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8"),
        REPORT_MD: (render_markdown(payload) + "\n").encode("utf-8"),
        SCENARIOS_CSV: (_csv_bytes(_scenario_rows(payload))),
        RUIN_CSV: (_csv_bytes(_ruin_rows(payload))),
    }

    written: dict[
        str,
        str,
    ] = {}

    for name in sorted(files):
        path = target / name

        _atomic_write_bytes(
            path,
            files[name],
        )

        written[name] = str(path)

    manifest_rows: list[dict[str, Any]] = []

    for name in sorted(files):
        path = target / name

        manifest_rows.append(
            {
                "file": name,
                "sha256": (_sha256_file(path)),
                "size_bytes": (path.stat().st_size),
            }
        )

    manifest_path = target / MANIFEST_CSV

    _atomic_write_bytes(
        manifest_path,
        _csv_bytes(manifest_rows),
    )

    written[MANIFEST_CSV] = str(manifest_path)

    return written


def main(
    argv: list[str] | None = None,
) -> int:
    _ensure_project_root()

    from smartcrypto.research.trades_master_official.loader import (
        OfficialMasterValidationError,
        load_official_trades_master,
    )
    from smartcrypto.research.trades_master_official.monte_carlo import (
        OfficialMonteCarloRiskValidationError,
        build_monte_carlo_risk_report,
        report_content_sha256,
    )
    from smartcrypto.research.trades_master_official.regime_concentration import (
        OfficialRegimeConcentrationError,
    )
    from smartcrypto.research.trades_master_official.risk_of_ruin import (
        OfficialRiskOfRuinValidationError,
    )

    args = build_parser().parse_args(argv)

    try:
        master = load_official_trades_master(args.master)

        payload = build_monte_carlo_risk_report(
            master,
            project_root=(PROJECT_ROOT),
            method_freeze_v2_path=(args.method_freeze_v2),
            method_freeze_v3_path=(args.method_freeze_v3),
            market_paths={
                "BTCUSDT_existing": (args.btc_existing),
                "ETHUSDT_existing": (args.eth_existing),
                "BTCUSDT_backfill": (args.btc_backfill),
                "ETHUSDT_backfill": (args.eth_backfill),
            },
        )

        payload["report_content_sha256"] = report_content_sha256(payload)

        payload["write_requested"] = bool(args.write_report)

        payload["write_performed"] = False

        if args.write_report:
            persisted = {
                **payload,
                "write_performed": True,
            }

            payload["report_paths"] = persist_wq5_reports(
                persisted,
                output_dir=(args.output_dir),
            )

            payload["write_performed"] = True

    except (
        OfficialMasterValidationError,
        OfficialMonteCarloRiskValidationError,
        OfficialRegimeConcentrationError,
        OfficialRiskOfRuinValidationError,
    ) as exc:
        payload = _blocked(
            reason=exc.code,
            details=exc.details,
            master_path=str(args.master),
        )

    if args.json:
        print(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                allow_nan=False,
            )
        )

    else:
        for (
            key,
            value,
        ) in payload.items():
            print(f"{key}={value}")

    return 0 if payload.get("status") == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
