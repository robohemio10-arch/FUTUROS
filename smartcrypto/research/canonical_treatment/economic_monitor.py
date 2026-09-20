"""Read-only Control x Canonical-Treatment Paper economic monitor."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sqlite3
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

SCHEMA_VERSION = "canonical_treatment_paper_economic_monitor_v1"
_DECISION_TAG = re.compile(r"(?:^|\|)decision_event_id=([^|]+)")

SAFE_FLAGS = {
    "paper_only": True,
    "shadow_only": True,
    "research_only": True,
    "operational_authority": False,
    "live_trading_enabled": False,
    "live_release_allowed": False,
    "canary_release_allowed": False,
    "order_submission_enabled": False,
    "real_order_submission_enabled": False,
    "exchange_private_access": False,
    "changes_risk": False,
    "changes_model": False,
    "changes_strategy": False,
    "writes_sqlite": False,
}


class CanonicalTreatmentMonitorError(RuntimeError):
    pass


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).astimezone(UTC).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CanonicalTreatmentMonitorError(
            f"json_source_invalid:{path}:{type(exc).__name__}"
        ) from exc
    if not isinstance(value, dict):
        raise CanonicalTreatmentMonitorError(f"json_object_required:{path}")
    return value


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    raw = json.dumps(
        dict(payload),
        sort_keys=True,
        ensure_ascii=False,
        indent=2,
        allow_nan=False,
    ).encode("utf-8")
    try:
        with temp.open("wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _time(value: object) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _finite(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _decision_id(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    matches = _DECISION_TAG.findall(value)
    return matches[0] if len(matches) == 1 else None


def _read_trades(path: Path, decision_ids: set[str]) -> list[dict[str, Any]]:
    if not path.is_file():
        raise CanonicalTreatmentMonitorError(f"sqlite_source_missing:{path}")

    connection = sqlite3.connect(
        "file:" + path.as_posix() + "?mode=ro",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    try:
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(trades)").fetchall()
        }
        required = {"id", "is_open", "open_date", "close_date", "enter_tag"}
        missing = sorted(required - columns)
        if missing:
            raise CanonicalTreatmentMonitorError(
                "sqlite_required_columns_missing:" + ",".join(missing)
            )

        pnl = (
            "close_profit_abs"
            if "close_profit_abs" in columns
            else "realized_profit"
            if "realized_profit" in columns
            else None
        )
        if pnl is None:
            raise CanonicalTreatmentMonitorError("sqlite_net_pnl_column_missing")
        stake = "stake_amount" if "stake_amount" in columns else None

        fields = [
            "id",
            "is_open",
            "open_date",
            "close_date",
            "enter_tag",
            f"{pnl} AS net_pnl",
            f"{stake} AS stake_amount" if stake else "NULL AS stake_amount",
        ]
        rows = connection.execute(
            f"SELECT {','.join(fields)} FROM trades ORDER BY id"
        ).fetchall()

        out: list[dict[str, Any]] = []
        for row in rows:
            event_id = _decision_id(row["enter_tag"])
            if event_id is None or event_id not in decision_ids:
                continue
            out.append(
                {
                    "trade_id": int(row["id"]),
                    "decision_event_id": event_id,
                    "is_open": bool(row["is_open"]),
                    "open_date": row["open_date"],
                    "close_date": row["close_date"],
                    "net_pnl": _finite(row["net_pnl"]),
                    "stake_amount": _finite(row["stake_amount"]),
                }
            )
        return out
    finally:
        connection.close()


def _aggregate(rows: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["decision_event_id"])].append(row)

    out: dict[str, dict[str, Any]] = {}
    for event_id, items in grouped.items():
        closed = [
            row
            for row in items
            if not bool(row["is_open"])
            and row.get("close_date") is not None
            and row.get("net_pnl") is not None
        ]
        out[event_id] = {
            "trade_count": len(items),
            "closed_trade_count": len(closed),
            "open_trade_count": sum(bool(row["is_open"]) for row in items),
            "net_pnl": float(sum(float(row["net_pnl"]) for row in closed)),
            "trade_ids": sorted(int(row["trade_id"]) for row in items),
        }
    return out


def _metrics(
    rows: Sequence[Mapping[str, Any]],
    initial_wallet_usdt: float,
) -> dict[str, Any]:
    closed = sorted(
        [
            row
            for row in rows
            if not bool(row["is_open"])
            and row.get("close_date") is not None
            and row.get("net_pnl") is not None
        ],
        key=lambda row: (
            str(row.get("close_date") or ""),
            int(row.get("trade_id") or 0),
        ),
    )
    pnl = [float(row["net_pnl"]) for row in closed]
    gross_profit = sum(value for value in pnl if value > 0.0)
    gross_loss = abs(sum(value for value in pnl if value < 0.0))

    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    capital_hours = 0.0
    for row, value in zip(closed, pnl, strict=True):
        equity += value
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)

        opened = _time(row.get("open_date"))
        closed_at = _time(row.get("close_date"))
        stake = _finite(row.get("stake_amount"))
        if (
            opened is not None
            and closed_at is not None
            and closed_at >= opened
            and stake is not None
            and stake > 0.0
        ):
            capital_hours += stake * (
                closed_at - opened
            ).total_seconds() / 3600.0

    net = float(sum(pnl))
    return {
        "closed_trade_count": len(pnl),
        "net_pnl": net,
        "roi_on_initial_wallet": (
            net / initial_wallet_usdt
            if initial_wallet_usdt > 0.0
            else None
        ),
        "expectancy": net / len(pnl) if pnl else None,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0.0 else None,
        "max_drawdown": float(max_dd),
        "capital_hours": float(capital_hours),
        "net_pnl_per_capital_hour": (
            net / capital_hours if capital_hours > 0.0 else None
        ),
    }


def build_monitor_report(
    *,
    ledger_path: Path,
    control_db: Path,
    treatment_db: Path,
    output_path: Path,
    initial_wallet_usdt: float = 1000.0,
) -> dict[str, Any]:
    now = _now()
    try:
        ledger = _read_json(ledger_path)
        rows = ledger.get("rows")
        if not isinstance(rows, list):
            raise CanonicalTreatmentMonitorError("ledger_rows_invalid")

        decisions = [dict(row) for row in rows if isinstance(row, Mapping)]
        ids = {
            str(row.get("operational_decision_event_id") or "")
            for row in decisions
            if row.get("operational_decision_event_id")
        }

        control_rows = _read_trades(control_db, ids)
        treatment_rows = _read_trades(treatment_db, ids)
        control_by = _aggregate(control_rows)
        treatment_by = _aggregate(treatment_rows)

        paired: list[dict[str, Any]] = []
        for row in decisions:
            event_id = str(row.get("operational_decision_event_id") or "")
            if not event_id:
                continue

            empty = {
                "trade_count": 0,
                "closed_trade_count": 0,
                "open_trade_count": 0,
                "net_pnl": 0.0,
                "trade_ids": [],
            }
            control = control_by.get(event_id, empty)
            treatment = treatment_by.get(event_id, empty)

            valid_until = _time(row.get("valid_until"))
            expired = valid_until is not None and now >= valid_until
            selected = row.get("selected") is True

            resolved = (
                row.get("status") == "scored"
                and expired
                and control["closed_trade_count"] > 0
                and control["open_trade_count"] == 0
                and treatment["open_trade_count"] == 0
            )
            treatment_pnl = float(treatment["net_pnl"]) if selected else 0.0

            paired.append(
                {
                    "operational_decision_event_id": event_id,
                    "signal_id": row.get("signal_id"),
                    "selected": row.get("selected"),
                    "ai_shadow_decision": row.get("ai_shadow_decision"),
                    "qlib_score": row.get("qlib_score"),
                    "valid_until": row.get("valid_until"),
                    "financially_resolved": resolved,
                    "control_trade_count": control["trade_count"],
                    "control_closed_trade_count": control["closed_trade_count"],
                    "control_net_pnl": float(control["net_pnl"]),
                    "treatment_trade_count": treatment["trade_count"],
                    "treatment_closed_trade_count": treatment["closed_trade_count"],
                    "treatment_net_pnl": treatment_pnl,
                    "delta_net_pnl": (
                        treatment_pnl - float(control["net_pnl"])
                        if resolved
                        else None
                    ),
                }
            )

        resolved_rows = [row for row in paired if row["financially_resolved"]]
        deltas = [
            float(row["delta_net_pnl"])
            for row in resolved_rows
            if row.get("delta_net_pnl") is not None
        ]
        candidate_count = len(decisions)
        scored_count = sum(row.get("status") == "scored" for row in decisions)
        selected_count = sum(
            row.get("status") == "scored" and row.get("selected") is True
            for row in decisions
        )

        report = {
            "schema_version": SCHEMA_VERSION,
            "status": "ok",
            "reason": "canonical_treatment_paper_monitor_ready",
            "generated_at_utc": _iso(now),
            "experiment_id": ledger.get(
                "experiment_id",
                "canonical-treatment-paper-v1",
            ),
            "experiment_started_at_utc": ledger.get("started_at_utc"),
            "scorer": {
                "candidate_decision_count": candidate_count,
                "scored_decision_count": scored_count,
                "selected_decision_count": selected_count,
                "coverage": (
                    scored_count / candidate_count
                    if candidate_count
                    else None
                ),
            },
            "sample": {
                "financially_resolved_decisions": len(resolved_rows),
                "selected_closed_trades": sum(
                    not bool(row["is_open"])
                    and row.get("net_pnl") is not None
                    for row in treatment_rows
                ),
                "control_linked_trade_count": len(control_rows),
                "treatment_linked_trade_count": len(treatment_rows),
            },
            "control_metrics": _metrics(
                control_rows,
                initial_wallet_usdt,
            ),
            "treatment_metrics": _metrics(
                treatment_rows,
                initial_wallet_usdt,
            ),
            "paired_uplift": {
                "resolved_decision_count": len(deltas),
                "delta_net_pnl_total": float(sum(deltas)) if deltas else None,
                "delta_net_pnl_mean": (
                    float(sum(deltas) / len(deltas)) if deltas else None
                ),
                "positive_delta_count": sum(value > 0.0 for value in deltas),
                "negative_delta_count": sum(value < 0.0 for value in deltas),
                "zero_delta_count": sum(value == 0.0 for value in deltas),
            },
            "paired_rows": paired[-500:],
            "cost_basis": {
                "control": "freqtrade_close_profit_abs",
                "treatment": "freqtrade_close_profit_abs",
                "runtime_config_parity_asserted": False,
                "runtime_config_parity_reason": "not_yet_verified_against_active_control_runtime_config",
                "initial_wallet_usdt": initial_wallet_usdt,
            },
            **SAFE_FLAGS,
        }
        _write_json(output_path, report)
        return report

    except Exception as exc:
        reason = (
            str(exc)
            if isinstance(exc, CanonicalTreatmentMonitorError)
            else f"canonical_treatment_monitor_failed:{type(exc).__name__}"
        )
        report = {
            "schema_version": SCHEMA_VERSION,
            "status": "blocked",
            "reason": reason,
            "generated_at_utc": _iso(now),
            **SAFE_FLAGS,
        }
        try:
            _write_json(output_path, report)
        except OSError:
            pass
        return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ledger",
        default="data/research/canonical_treatment/decision_ledger_v1.json",
    )
    parser.add_argument("--control-db", required=True)
    parser.add_argument("--treatment-db", required=True)
    parser.add_argument(
        "--output",
        default="data/reports/canonical_treatment/economic_monitor_v1.json",
    )
    parser.add_argument("--initial-wallet-usdt", type=float, default=1000.0)
    parser.add_argument("--interval-seconds", type=float, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    def once() -> dict[str, Any]:
        return build_monitor_report(
            ledger_path=Path(args.ledger),
            control_db=Path(args.control_db),
            treatment_db=Path(args.treatment_db),
            output_path=Path(args.output),
            initial_wallet_usdt=float(args.initial_wallet_usdt),
        )

    if args.interval_seconds is None:
        report = once()
        print(json.dumps(report, sort_keys=True, ensure_ascii=False))
        return 0 if report.get("status") == "ok" else 2

    interval = max(5.0, float(args.interval_seconds))
    while True:
        report = once()
        print(
            json.dumps(report, sort_keys=True, ensure_ascii=False),
            flush=True,
        )
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
