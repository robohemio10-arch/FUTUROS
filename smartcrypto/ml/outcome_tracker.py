from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from smartcrypto.ml.model_decision_logger import normalize_side, normalize_symbol, read_jsonl, stable_hash


DEFAULT_DECISIONS_PATH = Path("data/reports/ai_shadow_model_decisions.jsonl")
DEFAULT_FEEDBACK_PATH = Path("data/feedback/paper_closed_trades_incremental.parquet")
DEFAULT_MICROBATCH_PATH = Path("data/features/incremental_training_microbatch.parquet")
DEFAULT_OUTPUT_PATH = Path("data/reports/ai_shadow_model_outcomes.jsonl")
DEFAULT_REPORT_PATH = Path("data/reports/ai_shadow_model_outcomes_report.json")
MATCH_WINDOW_MINUTES = 120


class OutcomeTrackerError(ValueError):
    pass


@dataclass(frozen=True)
class OutcomeRecord:
    decision_id: str
    trade_id: str | None
    target_win: bool
    return_pct: float
    pnl: float | None
    resolved_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class OutcomeTracker:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def record_outcome(
        self,
        *,
        decision_id: str,
        target_win: bool,
        return_pct: float,
        trade_id: str | None = None,
        pnl: float | None = None,
        resolved_at: str | None = None,
    ) -> OutcomeRecord:
        record = OutcomeRecord(
            decision_id=require_text(decision_id, "decision_id"),
            trade_id=normalize_optional_text(trade_id),
            target_win=bool(target_win),
            return_pct=float(return_pct),
            pnl=None if pnl is None else float(pnl),
            resolved_at=resolved_at or utc_timestamp(),
        )
        payload = self._load()
        payload["outcomes"].append(record.to_dict())
        self._save(payload)
        return record

    def metrics(self) -> dict[str, Any]:
        outcomes = self._load()["outcomes"]
        total = len(outcomes)
        resolved = [item for item in outcomes if item.get("resolved_at")]
        win_count = sum(1 for item in resolved if item.get("target_win") is True)
        returns = [float(item.get("return_pct", 0.0)) for item in resolved]
        return {
            "total_decisions": total,
            "resolved_decisions": len(resolved),
            "win_rate": win_count / len(resolved) if resolved else 0.0,
            "average_return_pct": sum(returns) / len(returns) if returns else 0.0,
        }

    def list_outcomes(self) -> list[OutcomeRecord]:
        return [OutcomeRecord(**item) for item in self._load()["outcomes"]]

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema_version": 1, "outcomes": []}
        payload = json.loads(self.path.read_text(encoding="utf-8") or "{}")
        if not isinstance(payload, dict):
            raise OutcomeTrackerError("outcome_tracker_root_must_be_mapping")
        payload.setdefault("schema_version", 1)
        payload.setdefault("outcomes", [])
        if not isinstance(payload["outcomes"], list):
            raise OutcomeTrackerError("outcomes_must_be_list")
        return payload

    def _save(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(self.path.suffix + f".{uuid.uuid4().hex}.tmp")
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temp_path.replace(self.path)


def track_ai_shadow_outcomes(
    *,
    decisions_path: str | Path = DEFAULT_DECISIONS_PATH,
    feedback_path: str | Path | None = DEFAULT_FEEDBACK_PATH,
    microbatch_path: str | Path | None = DEFAULT_MICROBATCH_PATH,
    output_path: str | Path = DEFAULT_OUTPUT_PATH,
    report_path: str | Path = DEFAULT_REPORT_PATH,
    strict: bool = False,
    match_window_minutes: int = MATCH_WINDOW_MINUTES,
) -> dict[str, Any]:
    decisions_file = Path(decisions_path)
    output = Path(output_path)
    report_file = Path(report_path)
    if not decisions_file.exists():
        return blocked_report(
            reason="missing_decisions",
            decisions_path=decisions_file,
            output_path=output,
            report_path=report_file,
            errors=[f"missing_decisions:{decisions_file}"],
        )
    decisions = read_jsonl(decisions_file)
    safety_errors = [error for decision in decisions for error in hard_safety_violations(decision)]
    if strict and safety_errors:
        return blocked_report(
            reason="unsafe_decision_log",
            decisions_path=decisions_file,
            output_path=output,
            report_path=report_file,
            errors=sorted(set(safety_errors)),
        )

    outcomes_source = load_outcome_source(feedback_path=feedback_path, microbatch_path=microbatch_path)
    if outcomes_source.empty:
        return blocked_report(
            reason="missing_outcome_source",
            decisions_path=decisions_file,
            output_path=output,
            report_path=report_file,
            errors=["missing_outcome_source"],
            status="no_matches",
        )

    tracked = []
    for decision in decisions:
        match = match_outcome(decision, outcomes_source, match_window_minutes=match_window_minutes)
        tracked.append(build_outcome_record(decision, match))

    matched_count = sum(1 for item in tracked if item["matched"])
    status = "ok" if matched_count else "no_matches"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("a", encoding="utf-8", newline="\n") as handle:
        for item in tracked:
            handle.write(json.dumps(item, ensure_ascii=False, sort_keys=True, default=str))
            handle.write("\n")

    report = {
        "status": status,
        "reason": "ok" if matched_count else "no_matches",
        "decisions_path": str(decisions_file),
        "feedback_path": str(feedback_path) if feedback_path else None,
        "microbatch_path": str(microbatch_path) if microbatch_path else None,
        "output_path": str(output),
        "report_path": str(report_file),
        "decisions_rows": int(len(decisions)),
        "outcome_source_rows": int(len(outcomes_source)),
        "outcome_rows_written": int(len(tracked)),
        "matched_rows": int(matched_count),
        "unmatched_rows": int(len(tracked) - matched_count),
        "append_only": True,
        "strict": bool(strict),
        "tracked_at_utc": utc_timestamp(),
        **safety_payload(),
    }
    write_json(report_file, report)
    return report


def load_outcome_source(*, feedback_path: str | Path | None, microbatch_path: str | Path | None) -> pd.DataFrame:
    frames = []
    for path_value in (feedback_path, microbatch_path):
        if not path_value:
            continue
        path = Path(path_value)
        if path.exists():
            frame = read_frame(path)
            frame["_source_path"] = str(path)
            frames.append(normalize_outcome_frame(frame))
    if not frames:
        return pd.DataFrame()
    combined = pd.concat(frames, ignore_index=True, sort=False)
    if "order_id" in combined.columns:
        combined = combined.drop_duplicates(subset=["order_id", "symbol", "open_time_utc"], keep="last")
    return combined.reset_index(drop=True)


def normalize_outcome_frame(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["order_id"] = result.get("order_id", pd.Series([None] * len(result))).map(normalize_optional_text)
    symbol_source = result["symbol"] if "symbol" in result.columns else result.get("moeda", pd.Series([""] * len(result)))
    side_source = result["side"] if "side" in result.columns else result.get("fechar_side", pd.Series([""] * len(result)))
    result["symbol"] = symbol_source.map(normalize_symbol)
    result["side"] = side_source.map(normalize_side)
    result["open_time_utc"] = pd.to_datetime(first_existing_series(result, ("open_time_utc", "horario_abertura")), utc=True, errors="coerce")
    result["close_time_utc"] = pd.to_datetime(first_existing_series(result, ("close_time_utc", "horario_fechamento")), utc=True, errors="coerce")
    result["pnl_fechado"] = pd.to_numeric(first_existing_series(result, ("pnl_fechado", "pnl", "reported_pnl_usdt")), errors="coerce")
    result["target_return"] = pd.to_numeric(first_existing_series(result, ("target_return", "taxa_lucros_perdas_fechados_pct", "return_pct")), errors="coerce")
    if "target_profitable" in result.columns:
        result["target_profitable"] = pd.to_numeric(result["target_profitable"], errors="coerce").fillna(0).astype(int)
    else:
        result["target_profitable"] = (result["pnl_fechado"] > 0).astype(int)
    return result


def match_outcome(decision: dict[str, Any], outcomes: pd.DataFrame, *, match_window_minutes: int) -> pd.Series | None:
    order_id = normalize_optional_text(decision.get("order_id") or decision.get("correlation_id"))
    if order_id and "order_id" in outcomes.columns:
        matches = outcomes.loc[outcomes["order_id"].astype(str).eq(order_id)]
        if not matches.empty:
            return matches.iloc[0]

    symbol = normalize_symbol(decision.get("symbol"))
    side = normalize_side(decision.get("side"))
    decided_at = pd.to_datetime(decision.get("open_time_utc") or decision.get("decided_at_utc"), utc=True, errors="coerce")
    candidates = outcomes.loc[outcomes["symbol"].eq(symbol) & outcomes["side"].eq(side)].copy()
    if pd.isna(decided_at) or candidates.empty:
        return None
    candidates["_delta_seconds"] = (candidates["open_time_utc"] - decided_at).abs().dt.total_seconds()
    candidates = candidates.loc[candidates["_delta_seconds"] <= int(match_window_minutes) * 60]
    if candidates.empty:
        return None
    return candidates.sort_values("_delta_seconds").iloc[0]


def build_outcome_record(decision: dict[str, Any], match: pd.Series | None) -> dict[str, Any]:
    tracked_at = utc_timestamp()
    matched = match is not None
    return {
        "decision_id": decision.get("decision_id"),
        "correlation_id": decision.get("correlation_id"),
        "model_id": decision.get("model_id"),
        "model_version": decision.get("model_version"),
        "symbol": normalize_symbol(decision.get("symbol")),
        "side": normalize_side(decision.get("side")),
        "action_shadow": decision.get("action_shadow"),
        "matched_order_id": normalize_optional_text(match.get("order_id")) if matched else None,
        "matched": bool(matched),
        "pnl_fechado": float(match.get("pnl_fechado")) if matched and pd.notna(match.get("pnl_fechado")) else None,
        "target_return": float(match.get("target_return")) if matched and pd.notna(match.get("target_return")) else None,
        "target_profitable": int(match.get("target_profitable")) if matched and pd.notna(match.get("target_profitable")) else None,
        "open_time_utc": stringify_timestamp(match.get("open_time_utc")) if matched else None,
        "close_time_utc": stringify_timestamp(match.get("close_time_utc")) if matched else None,
        "outcome_status": "matched" if matched else "unmatched",
        "outcome_reason": "matched_by_order_or_time_window" if matched else "no_matching_closed_trade",
        "tracked_at_utc": tracked_at,
        "record_hash": stable_hash({"decision": decision, "matched": matched, "tracked_at": tracked_at}),
        **safety_payload(),
    }


def read_frame(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        return pd.read_parquet(path)
    if suffix == ".csv":
        return pd.read_csv(path)
    if suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8") or "[]")
        return pd.DataFrame(payload if isinstance(payload, list) else payload.get("rows", []))
    raise OutcomeTrackerError(f"unsupported_outcome_source:{path.suffix}")


def first_existing_series(frame: pd.DataFrame, candidates: tuple[str, ...]) -> pd.Series:
    for column in candidates:
        if column in frame.columns:
            return frame[column]
    return pd.Series([None] * len(frame), index=frame.index)


def blocked_report(
    *,
    reason: str,
    decisions_path: Path,
    output_path: Path,
    report_path: Path,
    errors: list[str],
    status: str = "blocked",
) -> dict[str, Any]:
    report = {
        "status": status,
        "reason": reason,
        "decisions_path": str(decisions_path),
        "output_path": str(output_path),
        "report_path": str(report_path),
        "decisions_rows": 0,
        "outcome_rows_written": 0,
        "matched_rows": 0,
        "unmatched_rows": 0,
        "blocking_errors": errors,
        "append_only": True,
        "tracked_at_utc": utc_timestamp(),
        **safety_payload(),
    }
    write_json(report_path, report)
    return report


def hard_safety_violations(decision: dict[str, Any]) -> list[str]:
    violations = []
    for key in ("live_trading_enabled", "order_submission_enabled", "real_order_submission_enabled", "exchange_private_access", "sends_orders", "changes_risk"):
        if decision.get(key) is True:
            violations.append(f"unsafe_flag:{key}=true")
    return violations


def require_text(value: str, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise OutcomeTrackerError(f"{field_name}_required")
    return text


def normalize_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    text = str(value).strip()
    return text or None


def stringify_timestamp(value: Any) -> str | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    parsed = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(parsed):
        return str(value)
    return parsed.isoformat()


def safety_payload() -> dict[str, Any]:
    return {
        "paper_only": True,
        "shadow_only": True,
        "runtime_mode": "paper",
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
    }


def write_json(path: str | Path, payload: dict[str, Any]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
