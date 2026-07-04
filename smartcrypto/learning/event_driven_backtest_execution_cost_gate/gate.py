"""Research-only event-driven backtest gate with execution costs.

The gate reads existing research evidence and estimates whether reported gross
expected value survives conservative execution costs. It never trains, promotes,
updates runtime, touches Freqtrade/RiskManager, writes parquet/SQLite/model
artifacts, or sends orders.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "event_driven_backtest_execution_cost_gate_v1"
DECISION_RESEARCH = "MANTER_EM_RESEARCH"
EXECUTION_COST_MODEL_VERSION = "conservative_bps_v1"

DEFAULT_REPORT_JSON = Path("data/reports/event_driven_backtest_execution_cost_gate_v1.json")
DEFAULT_REPORT_MD = Path("data/reports/event_driven_backtest_execution_cost_gate_v1.md")

INPUT_SOURCES: tuple[tuple[str, Path, bool], ...] = (
    ("qlib_trainer", Path("data/reports/qlib_institutional_ranking_trainer_v1.json"), True),
    ("ai_shadow_quality_veto", Path("data/reports/ai_shadow_quality_veto_trainer_v1.json"), True),
    ("walkforward", Path("data/reports/walkforward_anti_leakage_split_engine_v1.json"), True),
    ("walkforward_baseline", Path("data/reports/walkforward_baseline_summary_v1.json"), True),
    ("target_store", Path("data/reports/financial_label_target_store_v1.json"), True),
    ("drift_monitor", Path("data/reports/ai_qlib_drift_regime_monitor_v1.json"), True),
    ("paper_autotrain_feedback_loop", Path("data/reports/paper_autotrain_feedback_loop_v1.json"), True),
)


@dataclass(frozen=True)
class CostModel:
    maker_fee_bps: float = 2.0
    taker_fee_bps: float = 4.0
    slippage_bps: float = 2.0
    spread_bps: float = 1.0
    funding_bps_per_position: float | None = None
    cost_drag_block_threshold: float = 0.5

    @property
    def funding_unavailable(self) -> bool:
        return self.funding_bps_per_position is None

    @property
    def funding_bps(self) -> float:
        return 0.0 if self.funding_bps_per_position is None else self.funding_bps_per_position

    @property
    def round_trip_cost_bps(self) -> float:
        return round(
            self.maker_fee_bps
            + self.taker_fee_bps
            + (2.0 * self.slippage_bps)
            + self.spread_bps
            + self.funding_bps,
            10,
        )

    def as_report_dict(self) -> dict[str, Any]:
        return {
            "execution_cost_model_version": EXECUTION_COST_MODEL_VERSION,
            "maker_fee_bps": self.maker_fee_bps,
            "taker_fee_bps": self.taker_fee_bps,
            "slippage_bps": self.slippage_bps,
            "spread_bps": self.spread_bps,
            "funding_bps_per_position": self.funding_bps_per_position,
            "funding_unavailable": self.funding_unavailable,
            "round_trip_cost_bps": self.round_trip_cost_bps,
            "cost_drag_block_threshold": self.cost_drag_block_threshold,
        }


@dataclass(frozen=True)
class SourceRecord:
    source_id: str
    relative_path: str
    path: Path
    required: bool
    exists: bool
    sha256: str | None
    load_error: str | None
    payload: dict[str, Any]

    def public_record(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "relative_path": self.relative_path,
            "path": str(self.path),
            "required": self.required,
            "exists": self.exists,
            "sha256": self.sha256,
            "load_error": self.load_error,
        }


def build_event_driven_backtest_execution_cost_gate_v1(
    *,
    project_root: str | Path,
    write_report: bool = False,
    report_json_path: str | Path | None = None,
    report_markdown_path: str | Path | None = None,
    maker_fee_bps: float = 2.0,
    taker_fee_bps: float = 4.0,
    slippage_bps: float = 2.0,
    spread_bps: float = 1.0,
    funding_bps_per_position: float | None = None,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    """Build the event-driven backtest execution cost gate report."""

    root = Path(project_root).resolve()
    generated_at = generated_at_utc or datetime.now(UTC).isoformat()
    cost_model = CostModel(
        maker_fee_bps=float(maker_fee_bps),
        taker_fee_bps=float(taker_fee_bps),
        slippage_bps=float(slippage_bps),
        spread_bps=float(spread_bps),
        funding_bps_per_position=funding_bps_per_position,
    )
    sources = load_sources(root)
    payloads = {source.source_id: source.payload for source in sources if source.payload}
    missing_required = [
        f"missing_required_source:{source.relative_path}"
        for source in sources
        if source.required and (not source.exists or source.load_error is not None)
    ]
    warnings: list[str] = []
    if cost_model.funding_unavailable:
        warnings.append("funding_cost_source_unavailable_using_zero_funding_with_warning")

    split_results = build_split_results(payloads, cost_model)
    baseline_results = build_baseline_results(payloads, cost_model)
    symbol_results = build_group_results(payloads, cost_model, "symbol")
    side_results = build_group_results(payloads, cost_model, "side")
    blockers = sorted(set(missing_required + cost_blockers(split_results, baseline_results)))
    warnings = sorted(set(warnings + source_status_warnings(payloads)))
    status, reason = decide_status(blockers, warnings)
    safety = safety_flags()
    report_json = resolve(root, report_json_path, DEFAULT_REPORT_JSON)
    report_md = resolve(root, report_markdown_path, DEFAULT_REPORT_MD)

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "reason": reason,
        "decision": DECISION_RESEARCH,
        "generated_at_utc": generated_at,
        "project_root": str(root),
        "input_sources": [source.public_record() for source in sources],
        "execution_cost_model": cost_model.as_report_dict(),
        "split_cost_gate_results": split_results,
        "symbol_cost_gate_results": symbol_results,
        "side_cost_gate_results": side_results,
        "baseline_comparison": baseline_results,
        "gate_summary": build_gate_summary(split_results, baseline_results, blockers),
        "research_candidate_cost_gate_passed": bool(split_results) and not any(
            result["cost_gate_passed"] is False for result in split_results
        ),
        "blockers": blockers,
        "warnings": warnings,
        "lineage_hashes": build_lineage_hashes(payloads),
        "write_requested": bool(write_report),
        "write_performed": False,
        "output_paths": {"json": str(report_json), "markdown": str(report_md)},
        **safety,
        "safety_flags": safety,
    }
    if write_report:
        write_reports(report, report_json, report_md)
        report["write_performed"] = True
        write_json(report_json, report)
    return report


def load_sources(project_root: Path) -> list[SourceRecord]:
    records: list[SourceRecord] = []
    for source_id, relative_path, required in INPUT_SOURCES:
        path = project_root / relative_path
        exists = path.is_file()
        payload: dict[str, Any] = {}
        load_error: str | None = None
        if exists:
            try:
                parsed = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError) as exc:
                load_error = f"invalid_json:{exc.__class__.__name__}"
            else:
                if isinstance(parsed, dict):
                    payload = parsed
                else:
                    load_error = "json_root_not_object"
        records.append(
            SourceRecord(
                source_id=source_id,
                relative_path=relative_path.as_posix(),
                path=path.resolve(),
                required=required,
                exists=exists,
                sha256=file_sha256(path) if exists else None,
                load_error=load_error,
                payload=payload,
            )
        )
    return records


def build_split_results(payloads: Mapping[str, Mapping[str, Any]], cost_model: CostModel) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    results.extend(
        split_result(
            source_id="qlib_trainer",
            split_id=str(item.get("split_id", f"qlib_split_{index + 1:03d}")),
            gross_expected_value=to_float(item.get("selected_top_k_expected_value"), default=0.0),
            event_count=to_int(item.get("test_row_count"), default=0),
            cost_model=cost_model,
        )
        for index, item in enumerate(list_of_mappings(payloads.get("qlib_trainer", {}).get("metrics_by_split")))
    )
    results.extend(
        split_result(
            source_id="ai_shadow_quality_veto",
            split_id=str(item.get("split_id", f"ai_shadow_split_{index + 1:03d}")),
            gross_expected_value=to_float(item.get("net_ev_delta_if_applied_research_only"), default=0.0),
            event_count=to_int(item.get("test_row_count"), default=0),
            cost_model=cost_model,
        )
        for index, item in enumerate(
            list_of_mappings(payloads.get("ai_shadow_quality_veto", {}).get("metrics_by_split"))
        )
    )
    return results


def split_result(
    *,
    source_id: str,
    split_id: str,
    gross_expected_value: float,
    event_count: int,
    cost_model: CostModel,
) -> dict[str, Any]:
    cost = execution_cost(event_count, cost_model)
    net = round(gross_expected_value - cost, 10)
    delta = round(net - gross_expected_value, 10)
    drag = cost_drag_ratio(cost, gross_expected_value)
    passed = net > 0 and (drag is None or drag <= cost_model.cost_drag_block_threshold)
    return {
        "source_id": source_id,
        "split_id": split_id,
        "event_count": event_count,
        "gross_expected_value": round(gross_expected_value, 10),
        "estimated_execution_cost": cost,
        "net_expected_value": net,
        "net_expected_value_delta": delta,
        "cost_drag_ratio": drag,
        "cost_gate_passed": passed,
        "failure_reasons": failure_reasons(net, drag, cost_model),
    }


def build_baseline_results(payloads: Mapping[str, Mapping[str, Any]], cost_model: CostModel) -> list[dict[str, Any]]:
    baseline = dict(payloads.get("walkforward_baseline", {}))
    if not baseline:
        baseline = dict(payloads.get("walkforward", {}).get("baseline_summary", {}))
    row_count = to_int(baseline.get("baseline_row_count"), default=0)
    specs = (
        ("no_trade", baseline.get("no_trade_expected_value"), 0),
        ("random", baseline.get("random_deterministic_expected_value"), max(row_count // 2, 0)),
        ("always_long", baseline.get("always_long_expected_value"), row_count),
        ("always_short", baseline.get("always_short_expected_value"), row_count),
    )
    return [
        split_result(
            source_id="walkforward_baseline",
            split_id=name,
            gross_expected_value=to_float(gross_value, default=0.0),
            event_count=event_count,
            cost_model=cost_model,
        )
        for name, gross_value, event_count in specs
    ]


def build_group_results(
    payloads: Mapping[str, Mapping[str, Any]],
    cost_model: CostModel,
    group_key: str,
) -> list[dict[str, Any]]:
    rows = list_of_mappings(payloads.get("target_store", {}).get("target_records"))
    if not rows:
        return []
    groups: dict[str, list[float]] = {}
    for row in rows:
        if group_key == "symbol":
            key = str(row.get("symbol_norm") or row.get("symbol") or "unknown").upper()
        else:
            key = str(row.get("side") or "unknown").lower()
        groups.setdefault(key, []).append(to_float(row.get("target_expected_value_component") or row.get("target_net_pnl"), default=0.0))
    output = []
    for key, values in sorted(groups.items()):
        gross = round(sum(values), 10)
        result = split_result(
            source_id=f"target_store_by_{group_key}",
            split_id=key,
            gross_expected_value=gross,
            event_count=len(values),
            cost_model=cost_model,
        )
        result[group_key] = key
        output.append(result)
    return output


def build_gate_summary(
    split_results: Sequence[Mapping[str, Any]],
    baseline_results: Sequence[Mapping[str, Any]],
    blockers: Sequence[str],
) -> dict[str, Any]:
    net_values = [to_float(item.get("net_expected_value"), default=0.0) for item in split_results]
    gross_values = [to_float(item.get("gross_expected_value"), default=0.0) for item in split_results]
    return {
        "evaluated_split_count": len(split_results),
        "passed_split_count": sum(1 for item in split_results if item.get("cost_gate_passed") is True),
        "failed_split_count": sum(1 for item in split_results if item.get("cost_gate_passed") is False),
        "gross_expected_value_total": round(sum(gross_values), 10),
        "net_expected_value_total": round(sum(net_values), 10),
        "baseline_count": len(baseline_results),
        "blocker_count": len(blockers),
    }


def cost_blockers(
    split_results: Sequence[Mapping[str, Any]],
    baseline_results: Sequence[Mapping[str, Any]],
) -> list[str]:
    blockers: list[str] = []
    if not split_results:
        blockers.append("no_split_metrics_available_for_cost_gate")
    for result in split_results:
        for reason in list_of_strings(result.get("failure_reasons")):
            blockers.append(f"{result.get('source_id')}:{result.get('split_id')}:{reason}")
    no_trade = next((item for item in baseline_results if item.get("split_id") == "no_trade"), None)
    if no_trade is not None:
        no_trade_net = to_float(no_trade.get("net_expected_value"), default=0.0)
        best_split_net = max((to_float(item.get("net_expected_value"), default=0.0) for item in split_results), default=0.0)
        if best_split_net <= no_trade_net:
            blockers.append("best_net_expected_value_not_above_no_trade")
    return blockers


def source_status_warnings(payloads: Mapping[str, Mapping[str, Any]]) -> list[str]:
    warnings: list[str] = []
    drift = payloads.get("drift_monitor", {})
    if drift.get("status") == "blocked":
        warnings.append("source_drift_monitor_blocked_research_context")
    paper_loop = payloads.get("paper_autotrain_feedback_loop", {})
    for warning in list_of_strings(paper_loop.get("warnings")):
        warnings.append(f"paper_autotrain_feedback_loop:{warning}")
    return warnings


def execution_cost(event_count: int, cost_model: CostModel) -> float:
    return round(max(event_count, 0) * cost_model.round_trip_cost_bps / 10_000.0, 10)


def cost_drag_ratio(cost: float, gross_expected_value: float) -> float | None:
    if gross_expected_value == 0:
        return None
    return round(cost / abs(gross_expected_value), 10)


def failure_reasons(net_expected_value: float, drag: float | None, cost_model: CostModel) -> list[str]:
    reasons: list[str] = []
    if net_expected_value <= 0:
        reasons.append("net_expected_value_non_positive")
    if drag is not None and drag > cost_model.cost_drag_block_threshold:
        reasons.append("cost_drag_ratio_exceeds_threshold")
    return reasons


def decide_status(blockers: Sequence[str], warnings: Sequence[str]) -> tuple[str, str]:
    if blockers:
        return "blocked", "execution_cost_gate_blocked"
    if warnings:
        return "warning", "execution_cost_gate_warnings_research_only"
    return "ok", "execution_cost_gate_passed_research_only"


def build_lineage_hashes(payloads: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for payload in payloads.values():
        for key in (
            "dataset_hash",
            "feature_contract_hash",
            "target_store_hash",
            "split_engine_hash",
            "walkforward_split_engine_hash",
            "dependency_contract_hash",
        ):
            if payload.get(key):
                output[key] = payload[key]
        nested = payload.get("lineage_hashes")
        if isinstance(nested, dict):
            output.update({str(key): value for key, value in nested.items() if value})
    return output


def render_markdown(report: Mapping[str, Any]) -> str:
    summary = mapping_or_empty(report.get("gate_summary"))
    cost = mapping_or_empty(report.get("execution_cost_model"))
    return "\n".join(
        [
            "# Event-Driven Backtest Execution Cost Gate V1",
            "",
            "## Executive Summary",
            "",
            f"- Status: `{report.get('status')}`",
            f"- Reason: `{report.get('reason')}`",
            f"- Decision: `{report.get('decision')}`",
            f"- Release allowed: `{report.get('release_allowed')}`",
            f"- Evaluated splits: `{summary.get('evaluated_split_count')}`",
            f"- Failed splits: `{summary.get('failed_split_count')}`",
            f"- Net EV total after costs: `{summary.get('net_expected_value_total')}`",
            "",
            "## Execution Cost Model",
            "",
            f"- Version: `{cost.get('execution_cost_model_version')}`",
            f"- Maker fee bps: `{cost.get('maker_fee_bps')}`",
            f"- Taker fee bps: `{cost.get('taker_fee_bps')}`",
            f"- Slippage bps: `{cost.get('slippage_bps')}`",
            f"- Spread bps: `{cost.get('spread_bps')}`",
            f"- Round-trip cost bps: `{cost.get('round_trip_cost_bps')}`",
            f"- Funding unavailable: `{cost.get('funding_unavailable')}`",
            "",
            "## Split Results",
            "",
            *markdown_rows(report.get("split_cost_gate_results", [])),
            "",
            "## Baseline Comparison",
            "",
            *markdown_rows(report.get("baseline_comparison", [])),
            "",
            "## Safety Invariants",
            "",
            "- `operational_authority=false`",
            "- `readiness_release_authority=false`",
            "- `release_allowed=false`",
            "- `sends_orders=false`",
            "- `exchange_private_access=false`",
            "- `updates_freqtrade=false`",
            "- `updates_risk_manager=false`",
            "- `writes_runtime=false`",
            "- `writes_sqlite=false`",
            "- `writes_parquet=false`",
            "",
            "This report is research evidence only. It does not change models, rules, runtime, risk, or trading behavior.",
            "",
        ]
    )


def markdown_rows(rows: Any) -> list[str]:
    records = list_of_mappings(rows)
    if not records:
        return ["- No rows available."]
    return [
        (
            f"- `{item.get('source_id')}/{item.get('split_id')}`: gross=`{item.get('gross_expected_value')}`, "
            f"cost=`{item.get('estimated_execution_cost')}`, net=`{item.get('net_expected_value')}`, "
            f"passed=`{item.get('cost_gate_passed')}`"
        )
        for item in records
    ]


def write_reports(report: Mapping[str, Any], report_json: Path, report_md: Path) -> None:
    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_md.parent.mkdir(parents=True, exist_ok=True)
    write_json(report_json, report)
    report_md.write_text(render_markdown(report), encoding="utf-8")


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=json_safe) + "\n",
        encoding="utf-8",
    )


def safety_flags() -> dict[str, bool]:
    return {
        "paper_only": True,
        "shadow_only": True,
        "research_only": True,
        "read_only": True,
        "operational_authority": False,
        "readiness_release_authority": False,
        "release_allowed": False,
        "live_release_allowed": False,
        "canary_release_allowed": False,
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "sends_orders": False,
        "changes_risk": False,
        "changes_model": False,
        "can_promote_model": False,
        "can_promote_rules": False,
        "model_promotion_performed": False,
        "registry_write_performed": False,
        "qlib_runtime_updated": False,
        "ai_shadow_runtime_updated": False,
        "updates_freqtrade": False,
        "updates_risk_manager": False,
        "writes_runtime": False,
        "writes_sqlite": False,
        "writes_parquet": False,
        "runs_training": False,
    }


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve(root: Path, value: str | Path | None, default: Path) -> Path:
    path = Path(value) if value is not None else default
    return path if path.is_absolute() else root / path


def mapping_or_empty(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def list_of_mappings(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def list_of_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None]


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def json_safe(value: Any) -> Any:
    if hasattr(value, "item"):
        try:
            return value.item()
        except (TypeError, ValueError):
            pass
    return value
