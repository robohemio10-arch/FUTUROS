from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Mapping

import pandas as pd
import yaml

from smartcrypto.qlib_engine.prediction_freshness import inspect_qlib_prediction_freshness


DEFAULT_CONFIG_PATH = "config/signal_producer.yml"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime | None = None) -> str:
    return (value or utc_now()).isoformat()


def parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def load_config(config_path: str | os.PathLike[str] | Mapping[str, Any] | None = None) -> dict[str, Any]:
    if isinstance(config_path, Mapping):
        return dict(config_path)

    path = Path(config_path or DEFAULT_CONFIG_PATH)
    if not path.exists():
        return {
            "runtime_mode": "paper",
            "source": "qlib",
            "model_version_default": "qlib_lgbm_v1",
            "paths": {
                "predictions": "data/predictions/latest_qlib_predictions.parquet",
                "primary_signals": "data/freqtrade_signals.json",
                "pinned_signals": "data/runtime/active_freqtrade_signals.json",
                "report": "data/reports/phase13_signal_producer_report.json",
                "summary": "data/reports/phase13_summary.json",
                "decision_log": "data/runtime/freqtrade_signal_decisions.jsonl",
            },
            "policy": {
                "validity_minutes": 30,
                "min_abs_score": 0.0,
                "min_confidence": 0.0,
                "max_signals": 2,
                "include_top_n_when_threshold_empty": 2,
                "never_overwrite_with_empty": True,
                "require_risk_approved": True,
                "max_prediction_age_minutes": 90,
                "max_input_data_age_minutes": 15,
            },
            "risk": {
                "max_position_usdt": 50.0,
                "leverage": 2.0,
            },
        }

    with path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or {}

    defaults = load_config({})
    return deep_merge(defaults, loaded)


def deep_merge(left: dict[str, Any], right: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(left)
    for key, value in right.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = deep_merge(dict(merged[key]), value)
        else:
            merged[key] = value
    return merged


def ensure_parent(path: str | os.PathLike[str]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def read_json(path: str | os.PathLike[str]) -> dict[str, Any]:
    file_path = Path(path)
    if not file_path.exists():
        return {}
    try:
        with file_path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def atomic_write_json(path: str | os.PathLike[str], payload: Mapping[str, Any]) -> None:
    file_path = Path(path)
    ensure_parent(file_path)
    tmp_path = file_path.with_suffix(file_path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, default=str)
        handle.write("\n")
    tmp_path.replace(file_path)


def active_signals_from_payload(payload: Mapping[str, Any], now: datetime | None = None) -> list[dict[str, Any]]:
    current = now or utc_now()
    signals = payload.get("signals", [])
    if not isinstance(signals, list):
        return []

    active: list[dict[str, Any]] = []
    for item in signals:
        if not isinstance(item, Mapping):
            continue
        valid_until = parse_datetime(item.get("valid_until"))
        if valid_until and valid_until < current:
            continue
        if item.get("risk_approved") is False:
            continue
        pair = str(item.get("pair") or "").strip()
        symbol = str(item.get("symbol") or "").strip()
        side = str(item.get("side") or "").strip().lower()
        if not (pair or symbol):
            continue
        if side not in {"long", "short"}:
            continue
        active.append(dict(item))
    return active


def load_predictions(path: str | os.PathLike[str]) -> pd.DataFrame:
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(str(file_path))

    suffix = file_path.suffix.lower()
    if suffix == ".parquet":
        frame = pd.read_parquet(file_path)
    elif suffix in {".csv", ".txt"}:
        frame = pd.read_csv(file_path)
    elif suffix in {".xlsx", ".xls"}:
        frame = pd.read_excel(file_path)
    else:
        raise ValueError(f"Formato de predições não suportado: {file_path}")

    if frame.empty:
        return frame

    frame = frame.copy()
    if "pair" not in frame.columns and "symbol" in frame.columns:
        frame["pair"] = frame["symbol"].map(symbol_to_pair)
    if "symbol" not in frame.columns and "pair" in frame.columns:
        frame["symbol"] = frame["pair"].map(pair_to_symbol)

    if "score" not in frame.columns:
        for candidate in ("prediction", "pred", "pred_score", "prob_up", "probability", "confidence"):
            if candidate in frame.columns:
                frame["score"] = pd.to_numeric(frame[candidate], errors="coerce")
                if candidate in {"prob_up", "probability"}:
                    frame["score"] = frame["score"] - 0.5
                break

    if "score" not in frame.columns:
        frame["score"] = 0.0

    frame["score"] = pd.to_numeric(frame["score"], errors="coerce").fillna(0.0)

    if "confidence" not in frame.columns:
        if "prob_up" in frame.columns:
            prob = pd.to_numeric(frame["prob_up"], errors="coerce")
            frame["confidence"] = (prob - 0.5).abs().fillna(frame["score"].abs())
        else:
            frame["confidence"] = frame["score"].abs()

    frame["confidence"] = pd.to_numeric(frame["confidence"], errors="coerce").fillna(0.0)

    if "side" not in frame.columns:
        if "predicted_direction" in frame.columns:
            direction = pd.to_numeric(frame["predicted_direction"], errors="coerce").fillna(0)
            frame["side"] = direction.map(lambda value: "long" if value > 0 else "short")
        else:
            frame["side"] = frame["score"].map(lambda value: "long" if value > 0 else "short")

    frame["side"] = frame["side"].astype(str).str.lower().replace({"buy": "long", "sell": "short"})
    return frame


def symbol_to_pair(value: Any) -> str:
    symbol = str(value or "").replace("/", "").replace(":USDT", "").upper()
    if not symbol:
        return ""
    if symbol.endswith("USDT"):
        base = symbol[:-4]
        return f"{base}/USDT:USDT"
    return symbol


def pair_to_symbol(value: Any) -> str:
    pair = str(value or "").upper()
    if not pair:
        return ""
    return pair.replace(":USDT", "").replace("/", "")


def select_prediction_rows(frame: pd.DataFrame, config: Mapping[str, Any]) -> pd.DataFrame:
    policy = config.get("policy", {})
    min_abs_score = float(policy.get("min_abs_score", 0.0) or 0.0)
    min_confidence = float(policy.get("min_confidence", 0.0) or 0.0)
    max_signals = int(policy.get("max_signals", 2) or 2)
    include_top_n = int(policy.get("include_top_n_when_threshold_empty", max_signals) or max_signals)

    if frame.empty:
        return frame

    clean = frame.copy()
    clean = clean[clean["side"].isin(["long", "short"])]
    clean = clean[(clean.get("pair", "") != "") | (clean.get("symbol", "") != "")]
    filtered = clean[(clean["score"].abs() >= min_abs_score) & (clean["confidence"] >= min_confidence)]

    if filtered.empty and include_top_n > 0:
        filtered = clean.reindex(clean["score"].abs().sort_values(ascending=False).index).head(include_top_n)

    filtered = filtered.reindex(filtered["score"].abs().sort_values(ascending=False).index).head(max_signals)
    return filtered.reset_index(drop=True)


def row_to_signal(row: Mapping[str, Any], config: Mapping[str, Any], generated_at: datetime, valid_until: datetime) -> dict[str, Any]:
    risk = config.get("risk", {})
    model_version = str(config.get("model_version_default") or "qlib_lgbm_v1")

    pair = str(row.get("pair") or symbol_to_pair(row.get("symbol"))).strip()
    symbol = str(row.get("symbol") or pair_to_symbol(pair)).strip()
    side = str(row.get("side") or ("long" if float(row.get("score", 0.0)) > 0 else "short")).lower()

    score = safe_float(row.get("score"), 0.0)
    confidence = safe_float(row.get("confidence"), abs(score))

    return {
        "pair": pair,
        "symbol": symbol,
        "side": side,
        "score": score,
        "confidence": confidence,
        "prob_up": safe_float(row.get("prob_up"), None),
        "predicted_direction": int(1 if side == "long" else -1),
        "risk_approved": True,
        "leverage": safe_float(risk.get("leverage"), 2.0),
        "max_position_usdt": safe_float(risk.get("max_position_usdt"), 50.0),
        "model_version": str(row.get("model_version") or model_version),
        "generated_at": generated_at.isoformat(),
        "valid_until": valid_until.isoformat(),
        "source": str(config.get("source") or "qlib"),
    }


def safe_float(value: Any, default: float | None = 0.0) -> float | None:
    try:
        if value is None:
            return default
        number = float(value)
        if math.isnan(number) or math.isinf(number):
            return default
        return number
    except Exception:
        return default


def build_active_signals(
    config_path: str | os.PathLike[str] | Mapping[str, Any] | None = None,
    force_from_predictions: bool = False,
    validity_minutes: int | None = None,
) -> dict[str, Any]:
    config = load_config(config_path)
    paths = config.get("paths", {})
    policy = config.get("policy", {})

    predictions_path = paths.get("predictions", "data/predictions/latest_qlib_predictions.parquet")
    primary_path = paths.get("primary_signals", "data/freqtrade_signals.json")
    pinned_path = paths.get("pinned_signals", "data/runtime/active_freqtrade_signals.json")
    report_path = paths.get("report", "data/reports/phase13_signal_producer_report.json")

    generated_at = utc_now()
    minutes = int(validity_minutes or policy.get("validity_minutes", 30) or 30)
    valid_until = generated_at + timedelta(minutes=minutes)

    before_primary = active_signals_from_payload(read_json(primary_path), generated_at)
    before_pinned = active_signals_from_payload(read_json(pinned_path), generated_at)

    max_prediction_age = int(policy.get("max_prediction_age_minutes", 90) or 90)
    max_input_data_age = int(policy.get("max_input_data_age_minutes", 15) or 15)
    freshness = inspect_qlib_prediction_freshness(
        predictions_path,
        max_allowed_age_minutes=max_prediction_age,
        max_input_data_age_minutes=max_input_data_age,
        now=generated_at,
    )
    if freshness.get("freshness_status") != "fresh":
        report = {
            "status": "blocked",
            "reason": freshness.get("reason") or "qlib_predictions_not_fresh",
            "created_at": generated_at.isoformat(),
            "predictions_path": str(predictions_path),
            "primary_signals_path": str(primary_path),
            "pinned_signals_path": str(pinned_path),
            "signals_before_primary": len(before_primary),
            "signals_before_pinned": len(before_pinned),
            "signals_after": 0,
            "written_primary": False,
            "written_pinned": False,
            "prediction_rows": int(freshness.get("rows") or 0),
            "prediction_freshness": freshness,
            "generated_at": generated_at.isoformat(),
            "valid_until_min": None,
            "valid_until_max": None,
        }
        atomic_write_json(report_path, report)
        return report
    if freshness.get("input_data_status") != "input_data_fresh":
        input_reason_by_status = {
            "input_data_stale": "qlib_input_data_stale",
            "missing": "qlib_input_data_missing",
            "invalid": "qlib_input_data_invalid",
        }
        report = {
            "status": "blocked",
            "reason": input_reason_by_status.get(str(freshness.get("input_data_status")), "qlib_input_data_invalid"),
            "created_at": generated_at.isoformat(),
            "predictions_path": str(predictions_path),
            "primary_signals_path": str(primary_path),
            "pinned_signals_path": str(pinned_path),
            "signals_before_primary": len(before_primary),
            "signals_before_pinned": len(before_pinned),
            "signals_after": 0,
            "written_primary": False,
            "written_pinned": False,
            "prediction_rows": int(freshness.get("rows") or 0),
            "prediction_freshness": freshness,
            "generated_at": generated_at.isoformat(),
            "valid_until_min": None,
            "valid_until_max": None,
        }
        atomic_write_json(report_path, report)
        return report

    frame = load_predictions(predictions_path)
    selected = select_prediction_rows(frame, config)

    signals = [row_to_signal(row, config, generated_at, valid_until) for row in selected.to_dict(orient="records")]
    signal_payload = {
        "generated_at": generated_at.isoformat(),
        "source": "phase13_signal_producer_hardening",
        "model_version": str(config.get("model_version_default") or "qlib_lgbm_v1"),
        "runtime_mode": str(config.get("runtime_mode") or "paper"),
        "signals": signals,
    }

    never_empty = bool(policy.get("never_overwrite_with_empty", True))
    written_primary = False
    written_pinned = False
    reason = None

    if signals or force_from_predictions or not never_empty:
        atomic_write_json(primary_path, signal_payload)
        atomic_write_json(pinned_path, signal_payload)
        written_primary = True
        written_pinned = True
    else:
        reason = "no_signals_generated_and_never_overwrite_with_empty_enabled"

    report = {
        "status": "ok" if signals else "empty",
        "reason": reason,
        "created_at": generated_at.isoformat(),
        "predictions_path": str(predictions_path),
        "primary_signals_path": str(primary_path),
        "pinned_signals_path": str(pinned_path),
        "signals_before_primary": len(before_primary),
        "signals_before_pinned": len(before_pinned),
        "signals_after": len(signals),
        "written_primary": written_primary,
        "written_pinned": written_pinned,
        "prediction_rows": int(len(frame)),
        "prediction_freshness": freshness,
        "pairs": [item.get("pair") for item in signals],
        "sides": [item.get("side") for item in signals],
        "generated_at": generated_at.isoformat(),
        "valid_until_min": min([item["valid_until"] for item in signals], default=None),
        "valid_until_max": max([item["valid_until"] for item in signals], default=None),
    }

    atomic_write_json(report_path, report)
    return report


def inspect_signal_file(path: str | os.PathLike[str]) -> dict[str, Any]:
    payload = read_json(path)
    active = active_signals_from_payload(payload)
    signals = payload.get("signals", [])
    signals = signals if isinstance(signals, list) else []

    return {
        "path": str(path),
        "exists": Path(path).exists(),
        "generated_at": payload.get("generated_at"),
        "source": payload.get("source"),
        "model_version": payload.get("model_version", "unknown"),
        "signal_count": len(signals),
        "active_signal_count": len(active),
        "pairs": sorted({str(item.get("pair")) for item in signals if isinstance(item, Mapping) and item.get("pair")}),
        "active_pairs": sorted({str(item.get("pair")) for item in active if item.get("pair")}),
        "sides": sorted({str(item.get("side")) for item in signals if isinstance(item, Mapping) and item.get("side")}),
    }


def inspect_decision_log(path: str | os.PathLike[str], sample_size: int = 80) -> dict[str, Any]:
    file_path = Path(path)
    result = {
        "path": str(path),
        "exists": file_path.exists(),
        "rows_sampled": 0,
        "accepted_decisions": 0,
        "entry_events": 0,
        "exit_events": 0,
        "recent": [],
    }
    if not file_path.exists():
        return result

    lines = file_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    recent_lines = lines[-sample_size:]
    events: list[dict[str, Any]] = []
    for line in recent_lines:
        try:
            item = json.loads(line)
            if isinstance(item, dict):
                events.append(item)
        except Exception:
            continue

    result["rows_sampled"] = len(events)
    result["accepted_decisions"] = sum(1 for item in events if item.get("accepted") is True)
    result["entry_events"] = sum(1 for item in events if item.get("event") == "populate_entry_trend")
    result["exit_events"] = sum(1 for item in events if item.get("event") == "populate_exit_trend")
    result["recent"] = events
    return result


def inspect_signal_runtime(config_path: str | os.PathLike[str] | Mapping[str, Any] | None = None) -> dict[str, Any]:
    config = load_config(config_path)
    paths = config.get("paths", {})
    primary_path = paths.get("primary_signals", "data/freqtrade_signals.json")
    pinned_path = paths.get("pinned_signals", "data/runtime/active_freqtrade_signals.json")
    report_path = paths.get("report", "data/reports/phase13_signal_producer_report.json")
    decision_log_path = paths.get("decision_log", "data/runtime/freqtrade_signal_decisions.jsonl")

    return {
        "primary_signal": inspect_signal_file(primary_path),
        "pinned_signal": inspect_signal_file(pinned_path),
        "producer_report": read_json(report_path),
        "decision_log": inspect_decision_log(decision_log_path),
        "created_at": iso_utc(),
    }


def write_phase13_summary(config_path: str | os.PathLike[str] | Mapping[str, Any] | None = None) -> dict[str, Any]:
    config = load_config(config_path)
    summary = inspect_signal_runtime(config)
    summary_path = config.get("paths", {}).get("summary", "data/reports/phase13_summary.json")
    atomic_write_json(summary_path, summary)
    return {
        "status": "ok",
        "summary": summary_path,
        "created_at": iso_utc(),
    }


if __name__ == "__main__":
    report = build_active_signals(force_from_predictions=True)
    print(json.dumps(report, ensure_ascii=False, indent=2))
