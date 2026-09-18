"""Research-only economic benchmark between AIBOT Trader Master and SMART paper."""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


SCHEMA_VERSION = "smart_aibot_3991_economic_benchmark_v1"
METHOD_HASH = "7485063ebb2a0753ab92c5eb6b1313cfbbb8bd14b192cda877dd4dfdb5a75d62"
EXPECTED_MASTER_SHA256 = "a99c03497bb47e2aee2762c182ed45e38060490914d6991d3730d31de649935f"
MIN_TRADES = 30

DURATION_BINS = (
    float("-inf"),
    15 * 60,
    30 * 60,
    60 * 60,
    3 * 60 * 60,
    6 * 60 * 60,
    float("inf"),
)
DURATION_LABELS = ("<15m", "15-30m", "30-60m", "1-3h", "3-6h", ">6h")
DIMENSIONS = ("symbol", "side", "symbol_side", "entry_hour_utc", "duration_bucket")

UNAVAILABLE_CROSS_SYSTEM_DIMENSIONS = {
    "exit_reason": "AIBOT_OFFICIAL_MASTER_UNAVAILABLE",
    "composite_regime": "SMART_PAPER_WQ4_EQUIVALENT_PIT_LABEL_NOT_YET_MATERIALIZED",
}

EXPECTED_BASELINE = {
    "common_start_utc": "2026-06-01T22:01:49.990000Z",
    "common_end_utc": "2026-08-28T16:52:50Z",
    "aibot_trade_count": 1264,
    "smart_trade_count": 858,
    "matrix_row_count": 38,
    "rankable_matrix_row_count": 25,
    "positive_gap_count": 24,
    "aibot_net_pnl": 2929.4006149999996,
    "aibot_profit_factor": 2.159404199507647,
    "aibot_max_drawdown": 68.46778799999993,
    "aibot_net_pnl_per_capital_hour": 0.007098271979988383,
    "smart_net_pnl": -101.26341792999999,
    "smart_profit_factor": 0.7497360417020967,
    "smart_max_drawdown": 106.58479546999993,
    "smart_net_pnl_per_capital_hour": -0.0007415434369491392,
    "top_dimension": "duration_bucket",
    "top_bucket": "<15m",
    "top_aibot_trade_count": 967,
    "top_smart_trade_count": 135,
    "top_aibot_net_pnl_per_capital_hour": 0.010137362015826552,
    "top_smart_net_pnl_per_capital_hour": -0.07406751609557058,
    "top_smart_loss_gap": 0.08420487811139712,
}


class EconomicBenchmarkError(RuntimeError):
    """Fail-closed validation error for the research benchmark."""


def _numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iso_z(value: pd.Timestamp) -> str:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    return timestamp.isoformat().replace("+00:00", "Z")


def normalize_symbol(value: Any) -> str:
    text = str(value or "").strip().upper()
    text = text.replace("/", "").replace("_", "").replace("-", "").replace(":", "")
    if text.endswith("USDTUSDT"):
        text = text[:-4]
    if not text:
        raise EconomicBenchmarkError("empty_symbol")
    return text


def normalize_side(value: Any) -> str:
    text = str(value or "").strip().lower()
    if "short" in text:
        return "short"
    if "long" in text:
        return "long"
    raise EconomicBenchmarkError(f"unsupported_side:{value}")


def duration_bucket(seconds: Any) -> str | None:
    if seconds is None or pd.isna(seconds):
        return None
    value = float(seconds)
    if not math.isfinite(value):
        return None
    for index, label in enumerate(DURATION_LABELS):
        if DURATION_BINS[index] <= value < DURATION_BINS[index + 1]:
            return label
    return None


def compute_metrics(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {
            "trade_count": 0,
            "net_pnl": 0.0,
            "gross_profit": 0.0,
            "gross_loss_abs": 0.0,
            "profit_factor": None,
            "expectancy": None,
            "win_rate": None,
            "max_drawdown": None,
            "capital_total": 0.0,
            "roi": None,
            "capital_hour_eligible_trade_count": 0,
            "capital_hour_coverage_rate": None,
            "capital_hours_total": 0.0,
            "capital_hour_net_pnl": 0.0,
            "net_pnl_per_capital_hour": None,
        }

    ordered = frame.sort_values(["close_time_utc", "stable_order"], kind="mergesort").copy()
    pnl = ordered["net_pnl"].astype(float)
    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]
    gross_profit = float(wins.sum())
    gross_loss_abs = float(abs(losses.sum()))
    profit_factor = gross_profit / gross_loss_abs if gross_loss_abs > 0 else None

    equity = pnl.cumsum()
    peaks = equity.cummax()
    drawdown = equity - peaks
    max_drawdown = abs(float(drawdown.min()))

    capital = ordered["capital"].astype(float)
    capital_total = float(capital.sum())
    roi = float(pnl.sum()) / capital_total if capital_total > 0 else None

    capital_hours_array = pd.to_numeric(
        ordered["capital_hours"], errors="coerce"
    ).to_numpy(dtype=float, na_value=np.nan)
    eligible = (
        ordered["capital_hours"].notna()
        & np.isfinite(capital_hours_array)
        & ordered["capital_hours"].gt(0)
    )
    eligible_frame = ordered.loc[eligible]
    capital_hours_total = float(eligible_frame["capital_hours"].sum())
    capital_hour_net_pnl = float(eligible_frame["net_pnl"].sum())
    net_pnl_per_capital_hour = (
        capital_hour_net_pnl / capital_hours_total if capital_hours_total > 0 else None
    )

    return {
        "trade_count": int(len(ordered)),
        "net_pnl": float(pnl.sum()),
        "gross_profit": gross_profit,
        "gross_loss_abs": gross_loss_abs,
        "profit_factor": profit_factor,
        "expectancy": float(pnl.mean()),
        "win_rate": float((pnl > 0).mean()),
        "max_drawdown": max_drawdown,
        "capital_total": capital_total,
        "roi": roi,
        "capital_hour_eligible_trade_count": int(len(eligible_frame)),
        "capital_hour_coverage_rate": float(len(eligible_frame) / len(ordered)),
        "capital_hours_total": capital_hours_total,
        "capital_hour_net_pnl": capital_hour_net_pnl,
        "net_pnl_per_capital_hour": net_pnl_per_capital_hour,
    }


def build_aibot_frame(master_path: str | Path) -> pd.DataFrame:
    path = Path(master_path)
    if not path.is_file():
        raise EconomicBenchmarkError(f"master_not_found:{path}")

    raw = pd.read_excel(path, sheet_name="TRADES", dtype=object)
    required = {
        "trade_sequence",
        "symbol",
        "side",
        "horario_abertura",
        "horario_fechamento",
        "reported_pnl",
        "economic_net_pnl",
        "taxa_lucros_perdas_fechados_pct",
    }
    missing = sorted(required - set(raw.columns))
    if missing:
        raise EconomicBenchmarkError("master_missing_columns:" + ",".join(missing))

    reported = _numeric(raw["reported_pnl"])
    net_pnl = _numeric(raw["economic_net_pnl"])
    return_pct = _numeric(raw["taxa_lucros_perdas_fechados_pct"])
    stable_order = _numeric(raw["trade_sequence"])
    if (
        reported.isna().any()
        or net_pnl.isna().any()
        or return_pct.isna().any()
        or return_pct.eq(0).any()
        or stable_order.isna().any()
    ):
        raise EconomicBenchmarkError("master_economic_inputs_invalid")

    open_time = pd.to_datetime(
        raw["horario_abertura"].replace("SOURCE_NOT_AVAILABLE_FROM_PRINT", pd.NA),
        errors="coerce",
        utc=True,
    )
    close_time = pd.to_datetime(raw["horario_fechamento"], errors="coerce", utc=True)
    if close_time.isna().any():
        raise EconomicBenchmarkError("master_close_time_invalid")

    capital = abs(reported / (return_pct / 100.0))
    duration_seconds = (close_time - open_time).dt.total_seconds()

    frame = pd.DataFrame(
        {
            "source": "AIBOT",
            "stable_order": stable_order.astype(int),
            "symbol": raw["symbol"].map(normalize_symbol),
            "side": raw["side"].map(normalize_side),
            "open_time_utc": open_time,
            "close_time_utc": close_time,
            "entry_hour_utc": open_time.dt.hour,
            "duration_seconds": duration_seconds,
            "net_pnl": net_pnl.astype(float),
            "capital": capital.astype(float),
        }
    )
    frame["capital_hours"] = frame["capital"] * frame["duration_seconds"] / 3600.0
    frame["duration_bucket"] = frame["duration_seconds"].map(duration_bucket)
    return frame


def _read_paper_sqlite(sqlite_path: Path) -> pd.DataFrame:
    if not sqlite_path.is_file():
        raise EconomicBenchmarkError(f"paper_sqlite_not_found:{sqlite_path}")

    uri = "file:" + sqlite_path.resolve().as_posix() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=2.0)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        if connection.execute("PRAGMA query_only").fetchone()[0] != 1:
            raise EconomicBenchmarkError("sqlite_query_only_not_enabled")
        rows = connection.execute(
            """
            SELECT
                id AS sqlite_id,
                pair AS sqlite_pair,
                is_short AS sqlite_is_short,
                open_rate AS sqlite_open_rate,
                close_rate AS sqlite_close_rate,
                amount AS sqlite_amount,
                contract_size AS sqlite_contract_size,
                leverage AS sqlite_leverage,
                close_profit_abs AS sqlite_close_profit_abs,
                open_date AS sqlite_open_date,
                close_date AS sqlite_close_date
            FROM trades
            WHERE is_open = 0
            ORDER BY id
            """
        ).fetchall()
    except sqlite3.Error as exc:
        raise EconomicBenchmarkError(f"paper_sqlite_read_failed:{exc}") from exc
    finally:
        connection.close()

    frame = pd.DataFrame([dict(row) for row in rows])
    if frame.empty:
        raise EconomicBenchmarkError("paper_sqlite_no_closed_trades")
    return frame


def build_smart_frame(paper_path: str | Path, sqlite_path: str | Path) -> pd.DataFrame:
    csv_path = Path(paper_path)
    db_path = Path(sqlite_path)
    if not csv_path.is_file():
        raise EconomicBenchmarkError(f"paper_csv_not_found:{csv_path}")

    csv = pd.read_csv(csv_path, dtype=object)
    required_csv = {"order_id", "moeda", "fechar_side", "pnl_fechado"}
    missing_csv = sorted(required_csv - set(csv.columns))
    if missing_csv:
        raise EconomicBenchmarkError("paper_missing_columns:" + ",".join(missing_csv))

    sqlite = _read_paper_sqlite(db_path)
    csv_ids: list[int] = []
    for raw in csv["order_id"]:
        match = re.fullmatch(r"freqtrade-paper-([1-9][0-9]*)", str(raw).strip())
        if match is None:
            raise EconomicBenchmarkError(f"invalid_paper_order_id:{raw}")
        csv_ids.append(int(match.group(1)))

    csv = csv.copy()
    csv["sqlite_id"] = csv_ids
    if csv["sqlite_id"].duplicated().any():
        raise EconomicBenchmarkError("paper_csv_duplicate_trade_id")
    if sqlite["sqlite_id"].duplicated().any():
        raise EconomicBenchmarkError("paper_sqlite_duplicate_trade_id")
    if set(csv["sqlite_id"].astype(int)) != set(sqlite["sqlite_id"].astype(int)):
        raise EconomicBenchmarkError("paper_csv_sqlite_identity_set_mismatch")

    joined = csv.merge(sqlite, on="sqlite_id", how="inner", validate="one_to_one")
    csv_symbol = joined["moeda"].map(normalize_symbol)
    sqlite_symbol = joined["sqlite_pair"].map(normalize_symbol)
    if not csv_symbol.equals(sqlite_symbol):
        raise EconomicBenchmarkError("paper_symbol_divergence")

    csv_side = joined["fechar_side"].map(normalize_side)
    sqlite_side = joined["sqlite_is_short"].map(
        lambda value: "short" if int(value) == 1 else "long"
    )
    if not csv_side.equals(sqlite_side):
        raise EconomicBenchmarkError("paper_side_divergence")

    csv_pnl = _numeric(joined["pnl_fechado"])
    sqlite_pnl = _numeric(joined["sqlite_close_profit_abs"])
    if csv_pnl.isna().any() or sqlite_pnl.isna().any():
        raise EconomicBenchmarkError("paper_pnl_invalid")
    if float((csv_pnl - sqlite_pnl).abs().max()) > 1e-8:
        raise EconomicBenchmarkError("paper_pnl_divergence")

    open_rate = _numeric(joined["sqlite_open_rate"])
    amount = _numeric(joined["sqlite_amount"])
    contract_size = _numeric(joined["sqlite_contract_size"])
    leverage = _numeric(joined["sqlite_leverage"])
    if (
        open_rate.isna().any()
        or amount.isna().any()
        or contract_size.isna().any()
        or leverage.isna().any()
        or leverage.le(0).any()
    ):
        raise EconomicBenchmarkError("paper_capital_inputs_invalid")

    capital = abs(open_rate * amount * contract_size / leverage)
    open_time = pd.to_datetime(joined["sqlite_open_date"], errors="coerce", utc=True)
    close_time = pd.to_datetime(joined["sqlite_close_date"], errors="coerce", utc=True)
    if open_time.isna().any() or close_time.isna().any():
        raise EconomicBenchmarkError("paper_timestamp_invalid")

    duration_seconds = (close_time - open_time).dt.total_seconds()
    if duration_seconds.lt(0).any():
        raise EconomicBenchmarkError("paper_negative_duration")

    frame = pd.DataFrame(
        {
            "source": "SMART",
            "stable_order": joined["sqlite_id"].astype(int),
            "symbol": sqlite_symbol,
            "side": sqlite_side,
            "open_time_utc": open_time,
            "close_time_utc": close_time,
            "entry_hour_utc": open_time.dt.hour,
            "duration_seconds": duration_seconds,
            "net_pnl": sqlite_pnl.astype(float),
            "capital": capital.astype(float),
        }
    )
    frame["capital_hours"] = frame["capital"] * frame["duration_seconds"] / 3600.0
    frame["duration_bucket"] = frame["duration_seconds"].map(duration_bucket)
    return frame


def _segment_key(frame: pd.DataFrame, dimension: str) -> pd.Series:
    if dimension == "symbol":
        return frame["symbol"].astype(str)
    if dimension == "side":
        return frame["side"].astype(str)
    if dimension == "symbol_side":
        return frame["symbol"].astype(str) + "|" + frame["side"].astype(str)
    if dimension == "entry_hour_utc":
        return frame["entry_hour_utc"].map(
            lambda value: "UNAVAILABLE" if pd.isna(value) else str(int(value))
        )
    if dimension == "duration_bucket":
        return frame["duration_bucket"].fillna("UNAVAILABLE").astype(str)
    raise EconomicBenchmarkError(f"unsupported_dimension:{dimension}")


def _build_matrix(
    aibot: pd.DataFrame,
    smart: pd.DataFrame,
    min_trades: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    matrix: list[dict[str, Any]] = []
    for dimension in DIMENSIONS:
        aibot_keys = _segment_key(aibot, dimension)
        smart_keys = _segment_key(smart, dimension)
        buckets = sorted(set(aibot_keys.tolist()) | set(smart_keys.tolist()))

        for bucket in buckets:
            a = compute_metrics(aibot.loc[aibot_keys.eq(bucket)])
            s = compute_metrics(smart.loc[smart_keys.eq(bucket)])
            full_coverage = (
                a["capital_hour_coverage_rate"] == 1.0
                and s["capital_hour_coverage_rate"] == 1.0
            )
            rankable = (
                a["trade_count"] >= min_trades
                and s["trade_count"] >= min_trades
                and full_coverage
                and a["net_pnl_per_capital_hour"] is not None
                and s["net_pnl_per_capital_hour"] is not None
            )
            gap = (
                float(a["net_pnl_per_capital_hour"])
                - float(s["net_pnl_per_capital_hour"])
                if rankable
                else None
            )
            if rankable:
                reason = "rankable"
            elif a["trade_count"] < min_trades or s["trade_count"] < min_trades:
                reason = "minimum_trade_count_not_met"
            elif not full_coverage:
                reason = "capital_hour_coverage_incomplete"
            else:
                reason = "primary_metric_unavailable"

            matrix.append(
                {
                    "dimension": dimension,
                    "bucket": bucket,
                    "rankable": rankable,
                    "rank_reason": reason,
                    "smart_loss_gap": gap,
                    "aibot": a,
                    "smart": s,
                }
            )

    queue = sorted(
        [
            {
                "dimension": row["dimension"],
                "bucket": row["bucket"],
                "smart_loss_gap": row["smart_loss_gap"],
                "aibot_trade_count": row["aibot"]["trade_count"],
                "smart_trade_count": row["smart"]["trade_count"],
                "aibot_net_pnl_per_capital_hour": row["aibot"][
                    "net_pnl_per_capital_hour"
                ],
                "smart_net_pnl_per_capital_hour": row["smart"][
                    "net_pnl_per_capital_hour"
                ],
                "aibot_net_pnl": row["aibot"]["net_pnl"],
                "smart_net_pnl": row["smart"]["net_pnl"],
            }
            for row in matrix
            if row["rankable"]
            and row["smart_loss_gap"] is not None
            and row["smart_loss_gap"] > 0
        ],
        key=lambda row: (-float(row["smart_loss_gap"]), row["dimension"], row["bucket"]),
    )
    return matrix, queue


def _assert_close(actual: Any, expected: float, name: str, tolerance: float) -> None:
    if actual is None or not math.isfinite(float(actual)):
        raise EconomicBenchmarkError(f"baseline_non_finite:{name}:{actual}")
    if abs(float(actual) - expected) > tolerance:
        raise EconomicBenchmarkError(
            f"baseline_mismatch:{name}:actual={actual}:expected={expected}"
        )


def validate_frozen_baseline(
    report: dict[str, Any], *, tolerance: float = 1e-9
) -> None:
    if report.get("method_hash") != METHOD_HASH:
        raise EconomicBenchmarkError("method_hash_mismatch")

    common = report["common_window"]
    if common["start_utc"] != EXPECTED_BASELINE["common_start_utc"]:
        raise EconomicBenchmarkError("baseline_mismatch:common_start_utc")
    if common["end_utc"] != EXPECTED_BASELINE["common_end_utc"]:
        raise EconomicBenchmarkError("baseline_mismatch:common_end_utc")

    aibot = report["global"]["aibot"]
    smart = report["global"]["smart"]
    integer_checks = {
        "aibot_trade_count": aibot["trade_count"],
        "smart_trade_count": smart["trade_count"],
        "matrix_row_count": report["matrix_row_count"],
        "rankable_matrix_row_count": report["rankable_matrix_row_count"],
        "positive_gap_count": report["positive_gap_count"],
    }
    for name, actual in integer_checks.items():
        if int(actual) != int(EXPECTED_BASELINE[name]):
            raise EconomicBenchmarkError(
                f"baseline_mismatch:{name}:actual={actual}:expected={EXPECTED_BASELINE[name]}"
            )

    for prefix, metrics in (("aibot", aibot), ("smart", smart)):
        for metric in ("net_pnl", "profit_factor", "max_drawdown", "net_pnl_per_capital_hour"):
            _assert_close(
                metrics[metric],
                float(EXPECTED_BASELINE[f"{prefix}_{metric}"]),
                f"{prefix}_{metric}",
                tolerance,
            )

    queue = report["priority_queue"]
    if not queue:
        raise EconomicBenchmarkError("baseline_priority_queue_empty")
    top = queue[0]
    if top["dimension"] != EXPECTED_BASELINE["top_dimension"]:
        raise EconomicBenchmarkError("baseline_mismatch:top_dimension")
    if top["bucket"] != EXPECTED_BASELINE["top_bucket"]:
        raise EconomicBenchmarkError("baseline_mismatch:top_bucket")
    for name in ("aibot_trade_count", "smart_trade_count"):
        expected = EXPECTED_BASELINE[f"top_{name}"]
        if int(top[name]) != int(expected):
            raise EconomicBenchmarkError(
                f"baseline_mismatch:top_{name}:actual={top[name]}:expected={expected}"
            )
    for name in (
        "aibot_net_pnl_per_capital_hour",
        "smart_net_pnl_per_capital_hour",
        "smart_loss_gap",
    ):
        _assert_close(
            top[name],
            float(EXPECTED_BASELINE[f"top_{name}"]),
            f"top_{name}",
            tolerance,
        )


def build_economic_benchmark(
    *,
    master_path: str | Path,
    paper_path: str | Path,
    sqlite_path: str | Path,
    min_trades: int = MIN_TRADES,
    enforce_master_hash: bool = True,
    enforce_frozen_baseline: bool = True,
) -> dict[str, Any]:
    if min_trades < 1:
        raise EconomicBenchmarkError("min_trades_must_be_positive")

    master = Path(master_path)
    paper = Path(paper_path)
    sqlite = Path(sqlite_path)
    for label, path in (("master", master), ("paper", paper), ("sqlite", sqlite)):
        if not path.is_file():
            raise EconomicBenchmarkError(f"{label}_source_not_found:{path}")

    master_sha = _sha256(master)
    if enforce_master_hash and master_sha != EXPECTED_MASTER_SHA256:
        raise EconomicBenchmarkError(
            f"master_sha256_mismatch:{master_sha}:{EXPECTED_MASTER_SHA256}"
        )

    aibot = build_aibot_frame(master)
    smart = build_smart_frame(paper, sqlite)

    common_start = max(aibot["close_time_utc"].min(), smart["close_time_utc"].min())
    common_end = min(aibot["close_time_utc"].max(), smart["close_time_utc"].max())
    if common_start > common_end:
        raise EconomicBenchmarkError("no_common_time_window")

    aibot_common = aibot.loc[
        aibot["close_time_utc"].between(common_start, common_end, inclusive="both")
    ].copy()
    smart_common = smart.loc[
        smart["close_time_utc"].between(common_start, common_end, inclusive="both")
    ].copy()
    if aibot_common.empty or smart_common.empty:
        raise EconomicBenchmarkError("empty_common_window_source")

    matrix, queue = _build_matrix(aibot_common, smart_common, min_trades)
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": "ok",
        "decision": "RESEARCH_BENCHMARK_READY",
        "method_hash": METHOD_HASH,
        "minimum_rankable_trades": min_trades,
        "common_window": {
            "start_utc": _iso_z(common_start),
            "end_utc": _iso_z(common_end),
        },
        "source_sha256": {
            "master": master_sha,
            "paper_csv": _sha256(paper),
            "paper_sqlite": _sha256(sqlite),
        },
        "global": {
            "aibot": compute_metrics(aibot_common),
            "smart": compute_metrics(smart_common),
        },
        "matrix_row_count": len(matrix),
        "rankable_matrix_row_count": sum(1 for row in matrix if row["rankable"]),
        "positive_gap_count": len(queue),
        "matrix": matrix,
        "priority_queue": queue,
        "unavailable_cross_system_dimensions": dict(UNAVAILABLE_CROSS_SYSTEM_DIMENSIONS),
        "trade_identity_matching": False,
        "approximate_matching": False,
        "paper_only": True,
        "shadow_only": True,
        "research_only": True,
        "operational_authority": False,
        "sends_orders": False,
        "changes_risk": False,
        "changes_model": False,
        "exchange_private_access": False,
        "writes_runtime": False,
        "writes_data": False,
    }

    if enforce_frozen_baseline:
        validate_frozen_baseline(report)
        report["frozen_baseline_gate"] = "PASS"
    else:
        report["frozen_baseline_gate"] = "NOT_ENFORCED"

    json.dumps(report, sort_keys=True, allow_nan=False)
    return report
