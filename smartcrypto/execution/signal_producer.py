from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

import pandas as pd
import yaml

from smartcrypto.execution.paper_candidate_filter_runtime_wiring import (
    apply_paper_candidate_filter_to_signals,
    summarize_runtime_wiring,
)
from smartcrypto.execution.paper_profitability_policy_v1 import (
    build_minimum_decision_ledger_context,
    decide_direction,
    evaluate_candidate_policy,
)
from smartcrypto.execution.paper_candidate_trade_lineage_propagation_v1 import (
    PaperLineagePublicationResultV1,
    materialize_signal_batch_from_explicit_provenance,
    project_strict_decision_envelopes_in_memory,
    select_non_blocking_paper_publication_signals,
)
from smartcrypto.execution.signal_risk_gate import (
    DEFAULT_RISK_LIMITS_PATH,
    apply_risk_manager_gate,
)
from smartcrypto.execution.decision_ledger_paper_observability_wiring_v1 import (
    finalize_after_risk_manager,
    prepare_before_risk_manager,
)
from smartcrypto.qlib_engine.prediction_freshness import inspect_qlib_prediction_freshness
from smartcrypto.runtime.integrity_traceability_v2 import (
    atomic_write_json as institutional_atomic_write_json,
)


DEFAULT_CONFIG_PATH = "config/signal_producer.yml"


@dataclass(frozen=True)
class _NonBlockingObservabilityReport:
    status: str
    reason: str
    publication_blocked: bool
    writer_invoked: bool = False
    writes_runtime: bool = False
    paper_behavior_changed: bool = False
    attribution_evidence_blocked: bool = True
    error_type: str | None = None

    def model_dump(self, *, mode: str = "json") -> dict[str, Any]:
        del mode
        return {
            "status": self.status,
            "reason": self.reason,
            "publication_blocked": self.publication_blocked,
            "writer_invoked": self.writer_invoked,
            "writes_runtime": self.writes_runtime,
            "paper_behavior_changed": self.paper_behavior_changed,
            "attribution_evidence_blocked": self.attribution_evidence_blocked,
            "error_type": self.error_type,
            "lineage_is_operational_authority": False,
            "publication_blocked_by_lineage": False,
            "changes_risk": False,
            "sends_orders": False,
        }


@dataclass(frozen=True)
class _NonBlockingObservabilityOutcome:
    active_signals: tuple[dict[str, Any], ...]
    report: _NonBlockingObservabilityReport


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
                "risk_limits": str(DEFAULT_RISK_LIMITS_PATH),
                "decision_ledger_observability_config": None,
                "lineage_research_candidates": "data/reports/paper_ai_signal_candidate_producer_v1.json",
                "lineage_registry_candidates": "data/reports/paper_model_candidate_registry_gate_v1.json",
            },
            "policy": {
                "profile_id": "paper-profitability-candidate-v1",
                "validity_minutes": 30,
                "long_probability": 0.55,
                "short_probability": 0.45,
                "regime_gate_enabled": True,
                "cooldown_minutes": 0,
                "top_n_can_authorize_trade": False,
                "decision_ledger_enabled": False,
                "min_confidence": 0.0,
                "max_signals": 2,
                "top_n_telemetry": 2,
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
    institutional_atomic_write_json(Path(path), payload, sort_keys=False)


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

        if item.get("risk_approved") is not True:
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
    if "prob_up" not in frame.columns:
        return frame.iloc[0:0].copy()

    frame = frame.copy()

    if "pair" not in frame.columns and "symbol" in frame.columns:
        frame["pair"] = frame["symbol"].map(symbol_to_pair)

    if "symbol" not in frame.columns and "pair" in frame.columns:
        frame["symbol"] = frame["pair"].map(pair_to_symbol)

    frame["prob_up"] = pd.to_numeric(frame["prob_up"], errors="coerce")
    valid_probability = frame["prob_up"].between(0.0, 1.0, inclusive="both")
    frame.loc[~valid_probability, "prob_up"] = float("nan")
    frame["score"] = (2.0 * frame["prob_up"]) - 1.0
    frame["confidence"] = (frame["prob_up"] - 0.5).abs()
    frame["side"] = "no_trade"
    frame["proposed_side"] = "no_trade"
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
    long_probability = float(policy.get("long_probability", 0.55) or 0.55)
    short_probability = float(policy.get("short_probability", 0.45) or 0.45)
    min_confidence = float(policy.get("min_confidence", 0.0) or 0.0)
    max_signals = int(policy.get("max_signals", 2) or 2)

    if frame.empty:
        return frame
    if "prob_up" not in frame.columns:
        return frame.iloc[0:0].copy()
    if bool(policy.get("top_n_can_authorize_trade", False)):
        return frame.iloc[0:0].copy()
    if int(policy.get("cooldown_minutes", 0) or 0) != 0:
        return frame.iloc[0:0].copy()

    clean = frame.copy()
    decisions = [
        decide_direction(
            value,
            long_probability=long_probability,
            short_probability=short_probability,
        )
        for value in clean["prob_up"].tolist()
    ]
    clean["side"] = [decision.proposed_side for decision in decisions]
    clean["proposed_side"] = clean["side"]
    clean["direction_reason"] = [decision.reason for decision in decisions]
    clean["score"] = [decision.score for decision in decisions]
    clean["confidence"] = [decision.confidence for decision in decisions]
    regimes = (
        clean["market_regime"]
        if "market_regime" in clean.columns
        else pd.Series("unknown", index=clean.index)
    )
    regime_statuses = (
        clean["market_regime_status"]
        if "market_regime_status" in clean.columns
        else pd.Series("unknown", index=clean.index)
    )
    regime_gate_enabled = bool(policy.get("regime_gate_enabled", False))
    policy_decisions = [
        evaluate_candidate_policy(
            proposed_side=decision.proposed_side,
            market_regime=regime,
            market_regime_status=regime_status,
            regime_gate_enabled=regime_gate_enabled,
            observed_at=datetime(1970, 1, 1, tzinfo=timezone.utc),
            cooldown_until=None,
        )
        for decision, regime, regime_status in zip(
            decisions,
            regimes.tolist(),
            regime_statuses.tolist(),
            strict=True,
        )
    ]
    clean["market_regime"] = [decision.market_regime for decision in policy_decisions]
    clean["market_regime_status"] = [
        decision.market_regime_status for decision in policy_decisions
    ]
    clean["regime_block"] = [decision.regime_block for decision in policy_decisions]
    clean["regime_block_reason"] = [
        decision.regime_block_reason for decision in policy_decisions
    ]
    clean["cooldown_block"] = [
        decision.cooldown_block for decision in policy_decisions
    ]
    clean["candidate_policy_decision"] = [
        decision.final_decision for decision in policy_decisions
    ]
    clean = clean[clean["proposed_side"].isin(["long", "short"])]
    clean = clean[clean["candidate_policy_decision"].eq("ALLOW_CANDIDATE")]
    clean = clean[(clean.get("pair", "") != "") | (clean.get("symbol", "") != "")]
    filtered = clean[clean["confidence"] >= min_confidence]
    filtered = filtered.sort_values(
        ["confidence", "symbol"],
        ascending=[False, True],
        kind="mergesort",
    ).head(max_signals)
    return filtered.reset_index(drop=True)


def row_to_signal(
    row: Mapping[str, Any],
    config: Mapping[str, Any],
    generated_at: datetime,
    valid_until: datetime,
) -> dict[str, Any]:
    risk = config.get("risk", {})
    model_version = str(config.get("model_version_default") or "qlib_lgbm_v1")

    pair = str(row.get("pair") or symbol_to_pair(row.get("symbol"))).strip()
    symbol = str(row.get("symbol") or pair_to_symbol(pair)).strip()

    policy = config.get("policy", {})
    direction = decide_direction(
        row.get("prob_up"),
        long_probability=float(policy.get("long_probability", 0.55) or 0.55),
        short_probability=float(policy.get("short_probability", 0.45) or 0.45),
    )
    if direction.status != "ok" or direction.proposed_side == "no_trade":
        raise ValueError(f"prediction_not_tradeable:{direction.reason}")
    side = direction.proposed_side
    market_regime = str(row.get("market_regime") or "unknown").strip().lower()
    market_regime_status = str(
        row.get("market_regime_status")
        or ("fresh" if market_regime != "unknown" else "unknown")
    ).strip().lower()

    # NOTE: this candidate signal is intentionally built WITHOUT a
    # "risk_approved" claim. The only place allowed to set that field is
    # smartcrypto.execution.signal_risk_gate.apply_risk_manager_gate, which
    # only stamps risk_approved=True for signals RiskManager itself
    # approved. See build_active_signals() below.
    return {
        "pair": pair,
        "symbol": symbol,
        "side": side,
        "proposed_side": side,
        "score": direction.score,
        "confidence": direction.confidence,
        "prob_up": direction.prob_up,
        "calibrated_probability": direction.prob_up,
        "predicted_direction": int(1 if side == "long" else -1),
        "direction_reason": direction.reason,
        "market_regime": market_regime,
        "market_regime_status": market_regime_status,
        "regime_block": bool(row.get("regime_block", False)),
        "regime_block_reason": row.get("regime_block_reason"),
        "cooldown_block": bool(row.get("cooldown_block", False)),
        "candidate_policy_decision": row.get(
            "candidate_policy_decision", "ALLOW_CANDIDATE"
        ),
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


def _observability_failure(
    stage: str,
    exc: Exception,
) -> _NonBlockingObservabilityOutcome:
    error_type = type(exc).__name__
    return _NonBlockingObservabilityOutcome(
        active_signals=(),
        report=_NonBlockingObservabilityReport(
            status="blocked",
            reason=f"lineage_{stage}_failed:{error_type}",
            publication_blocked=True,
            error_type=error_type,
        ),
    )


def _observability_skipped_by_risk_gate() -> _NonBlockingObservabilityOutcome:
    return _NonBlockingObservabilityOutcome(
        active_signals=(),
        report=_NonBlockingObservabilityReport(
            status="skipped",
            reason="risk_gate_not_ok_observability_not_finalized",
            publication_blocked=False,
            attribution_evidence_blocked=True,
        ),
    )


def _safe_select_paper_lineage_publication(
    *,
    risk_gate: Any,
    observability: Any,
) -> PaperLineagePublicationResultV1:
    try:
        return select_non_blocking_paper_publication_signals(
            risk_gate=risk_gate,
            observability=observability,
        )
    except Exception as exc:
        approved = tuple(
            dict(item)
            for item in getattr(risk_gate, "approved_signals", ())
            if isinstance(item, Mapping)
        )
        return PaperLineagePublicationResultV1(
            active_signals=approved,
            status="baseline_preserved",
            reason=f"lineage_publication_boundary_failed:{type(exc).__name__}",
            baseline_approved_count=len(approved),
            observability_active_count=0,
            published_signal_count=len(approved),
            lineage_envelope_count=0,
            attribution_evidence_blocked=True,
            baseline_execution_preserved=True,
            risk_decision_preserved=True,
        )


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
    risk_limits_path = paths.get("risk_limits", str(DEFAULT_RISK_LIMITS_PATH))
    observability_config_source = paths.get("decision_ledger_observability_config")

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

    selected_rows = selected.to_dict(orient="records")
    candidate_signals = [
        row_to_signal(row, config, generated_at, valid_until)
        for row in selected_rows
    ]

    lineage_research_path = paths.get(
        "lineage_research_candidates",
        "data/reports/paper_ai_signal_candidate_producer_v1.json",
    )
    lineage_registry_path = paths.get(
        "lineage_registry_candidates",
        "data/reports/paper_model_candidate_registry_gate_v1.json",
    )
    lineage_materialization = materialize_signal_batch_from_explicit_provenance(
        signals=candidate_signals,
        source_rows=selected_rows,
        research_report=read_json(lineage_research_path),
        registry_report=read_json(lineage_registry_path),
        producer_id="phase13-signal-producer",
    )
    candidate_signals = list(lineage_materialization.signals)
    lineage_materialization_summary = lineage_materialization.report.to_dict()

    runtime_mode = str(config.get("runtime_mode") or "paper")
    paper_candidate_wiring = apply_paper_candidate_filter_to_signals(candidate_signals, runtime_mode=runtime_mode)
    candidate_signals = list(paper_candidate_wiring["allowed_signals"])
    paper_candidate_wiring_summary = summarize_runtime_wiring(paper_candidate_wiring)

    observability_preparation = prepare_before_risk_manager(
        candidate_signals,
        producer_id="phase13-signal-producer",
        config_source=observability_config_source,
    )
    risk_gate = apply_risk_manager_gate(
        observability_preparation.signals,
        risk_limits_path=risk_limits_path,
    )

    if risk_gate.status == "ok":
        observability = finalize_after_risk_manager(
            observability_preparation,
            risk_gate=risk_gate,
        )
    else:
        observability = _observability_skipped_by_risk_gate()

    strict_decision_projection = project_strict_decision_envelopes_in_memory(
        approved_signals=risk_gate.approved_signals,
        source_rows=selected_rows,
        decision_timestamp_utc=utc_now(),
        producer_id="phase13-signal-producer",
    )

    publication = _safe_select_paper_lineage_publication(
        risk_gate=risk_gate,
        observability=strict_decision_projection,
    )

    ledger_publication_blocked = (
        (
            bool(policy.get("decision_ledger_enabled", False))
            and not observability_preparation.enabled
        )
        or (
            observability_preparation.enabled
            and observability.report.publication_blocked
        )
    )
    if risk_gate.status != "ok" or ledger_publication_blocked:
        report = {
            "status": "blocked",
            "reason": (
                risk_gate.reason
                if risk_gate.status != "ok"
                else (
                    "decision_ledger_required_but_disabled"
                    if not observability_preparation.enabled
                    else observability.report.reason
                )
            ),
            "created_at": generated_at.isoformat(),
            "predictions_path": str(predictions_path),
            "primary_signals_path": str(primary_path),
            "pinned_signals_path": str(pinned_path),
            "signals_before_primary": len(before_primary),
            "signals_before_pinned": len(before_pinned),
            "signals_after": 0,
            "written_primary": False,
            "written_pinned": False,
            "prediction_rows": int(len(frame)),
            "prediction_freshness": freshness,
            "generated_at": generated_at.isoformat(),
            "valid_until_min": None,
            "valid_until_max": None,
            "paper_candidate_filter_runtime_wiring": paper_candidate_wiring_summary,
            "paper_candidate_lineage_materialization": lineage_materialization_summary,
            "risk_manager_gate": risk_gate.to_dict(),
            "decision_ledger_observability": observability.report.model_dump(mode="json"),
            "paper_candidate_strict_decision_projection": strict_decision_projection.report.model_dump(mode="json"),
            "paper_lineage_publication": publication.to_dict(),
        }
        atomic_write_json(report_path, report)
        return report

    signals = list(
        observability.active_signals
        if observability_preparation.enabled
        else publication.active_signals
    )
    for signal in signals:
        signal["decision_ledger_context"] = build_minimum_decision_ledger_context(
            signal,
            final_decision=str(signal.get("final_decision") or "ALLOW"),
            risk_approved=signal.get("risk_approved") is True,
        )
    signal_payload = {
        "generated_at": generated_at.isoformat(),
        "source": "phase13_signal_producer_hardening",
        "model_version": str(config.get("model_version_default") or "qlib_lgbm_v1"),
        "runtime_mode": runtime_mode,
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
        "paper_candidate_filter_runtime_wiring": paper_candidate_wiring_summary,
        "paper_candidate_lineage_materialization": lineage_materialization_summary,
        "risk_manager_gate": risk_gate.to_dict(),
        "decision_ledger_observability": observability.report.model_dump(mode="json"),
        "paper_candidate_strict_decision_projection": strict_decision_projection.report.model_dump(mode="json"),
        "paper_lineage_publication": publication.to_dict(),
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
        "pairs": sorted(
            {
                str(item.get("pair"))
                for item in signals
                if isinstance(item, Mapping) and item.get("pair")
            }
        ),
        "active_pairs": sorted({str(item.get("pair")) for item in active if item.get("pair")}),
        "sides": sorted(
            {
                str(item.get("side"))
                for item in signals
                if isinstance(item, Mapping) and item.get("side")
            }
        ),
    }


def inspect_decision_log(path: str | os.PathLike[str], sample_size: int = 80) -> dict[str, Any]:
    file_path = Path(path)
    result: dict[str, Any] = {
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
