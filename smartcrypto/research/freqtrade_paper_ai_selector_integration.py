"""Read-only observability gate for the paper Freqtrade AI selector surface."""

from __future__ import annotations

import ast
import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


INTEGRATION_VERSION = "1.0"

SAFETY_FLAGS: dict[str, bool] = {
    "paper_only": True,
    "shadow_only": True,
    "live_trading_enabled": False,
    "live_release_allowed": False,
    "canary_release_allowed": False,
    "order_submission_enabled": False,
    "real_order_submission_enabled": False,
    "exchange_private_access": False,
    "sends_orders": False,
    "changes_risk": False,
    "changes_model": False,
    "runs_training": False,
    "updates_freqtrade": False,
    "updates_qlib_runtime": False,
    "updates_risk_manager": False,
    "updates_ai_shadow_runtime": False,
    "runs_ai_shadow_incremental": False,
    "cleans_sqlite": False,
    "writes_sqlite": False,
    "registers_model": False,
    "auto_promote": False,
    "production_enabled": False,
    "paper_signal_mutation_allowed": False,
    "freqtrade_strategy_update_allowed": False,
    "risk_manager_override_allowed": False,
    "order_submission_allowed": False,
}

UNSAFE_TRUE_FLAGS = (
    "updates_freqtrade",
    "updates_qlib_runtime",
    "updates_risk_manager",
    "updates_ai_shadow_runtime",
    "registers_model",
    "production_enabled",
    "sends_orders",
    "changes_risk",
    "exchange_private_access",
    "order_submission_enabled",
    "real_order_submission_enabled",
    "live_trading_enabled",
)

RESEARCH_SOURCE_NAMES = (
    "branch04_training_summary",
    "branch05_executive_pack",
    "branch06_candidate_report",
    "branch07_feedback_loop_report",
)

OBSERVATION_TYPES = {
    "freqtrade_config": "freqtrade_paper_config_observed",
    "freqtrade_strategy": "freqtrade_strategy_contract_observed",
    "branch04_training_summary": "branch04_ai_selector_result_observed",
    "branch05_executive_pack": "branch05_executive_pack_gate_observed",
    "branch06_candidate_report": "branch06_shadow_registry_gate_observed",
    "branch07_feedback_loop_report": "branch07_feedback_loop_gate_observed",
}


@dataclass(frozen=True)
class FreqtradePaperAISelectorPaths:
    project_root: Path
    freqtrade_config_path: Path
    freqtrade_strategy_path: Path
    training_summary_path: Path
    executive_pack_path: Path
    shadow_candidate_report_path: Path
    feedback_loop_report_path: Path
    report_output_path: Path
    observations_output_path: Path


@dataclass(frozen=True)
class FreqtradePaperAISelectorConfig:
    strict: bool = False
    integration_version: str = INTEGRATION_VERSION


@dataclass(frozen=True)
class FreqtradePaperAISelectorResult:
    report: dict[str, Any]
    observations: list[dict[str, Any]]


def _resolve(root: Path, value: str | Path | None, default: Path) -> Path:
    if value is None:
        return default.resolve()
    candidate = Path(value).expanduser()
    return (root / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()


def resolve_paths(
    project_root: str | Path,
    *,
    freqtrade_config: str | Path | None = None,
    freqtrade_strategy: str | Path | None = None,
    training_summary: str | Path | None = None,
    executive_pack: str | Path | None = None,
    shadow_candidate_report: str | Path | None = None,
    feedback_loop_report: str | Path | None = None,
    report_output: str | Path | None = None,
    observations_output: str | Path | None = None,
) -> FreqtradePaperAISelectorPaths:
    root = Path(project_root).expanduser().resolve()
    reports = root / "data" / "reports"
    return FreqtradePaperAISelectorPaths(
        project_root=root,
        freqtrade_config_path=_resolve(
            root,
            freqtrade_config,
            root / "freqtrade" / "user_data" / "config.paper.json",
        ),
        freqtrade_strategy_path=_resolve(
            root,
            freqtrade_strategy,
            root
            / "freqtrade"
            / "user_data"
            / "strategies"
            / "SmartCryptoSignalStrategy.py",
        ),
        training_summary_path=_resolve(
            root,
            training_summary,
            reports / "qlib_ocr_v11_supervised_training_summary.json",
        ),
        executive_pack_path=_resolve(
            root,
            executive_pack,
            reports / "training_reports" / "smart_futuros_training_executive_pack.json",
        ),
        shadow_candidate_report_path=_resolve(
            root,
            shadow_candidate_report,
            reports / "qlib_ocr_v11_shadow_model_candidate_registry_report.json",
        ),
        feedback_loop_report_path=_resolve(
            root,
            feedback_loop_report,
            reports / "ai_shadow_online_feedback_learning_loop_report.json",
        ),
        report_output_path=_resolve(
            root,
            report_output,
            reports / "freqtrade_paper_ai_selector_integration_report.json",
        ),
        observations_output_path=_resolve(
            root,
            observations_output,
            reports / "freqtrade_paper_ai_selector_observations.jsonl",
        ),
    )


def load_json_report(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"json_report_must_be_object:{path}")
    return payload


def load_freqtrade_config_snapshot(path: Path) -> dict[str, Any]:
    """Load a redacted paper configuration snapshot without exposing secrets."""
    payload = load_json_report(path)
    exchange_value = payload.get("exchange")
    api_server_value = payload.get("api_server")
    telegram_value = payload.get("telegram")
    exchange: dict[str, Any] = exchange_value if isinstance(exchange_value, dict) else {}
    api_server: dict[str, Any] = (
        api_server_value if isinstance(api_server_value, dict) else {}
    )
    telegram: dict[str, Any] = (
        telegram_value if isinstance(telegram_value, dict) else {}
    )
    pairs = exchange.get("pair_whitelist", [])
    pair_whitelist = [str(value) for value in pairs] if isinstance(pairs, list) else []
    return {
        "status": "ok" if payload.get("dry_run") is True else "warning",
        "reason": (
            "freqtrade_paper_config_observed"
            if payload.get("dry_run") is True
            else "freqtrade_config_not_confirmed_dry_run"
        ),
        "path": str(path),
        "dry_run": payload.get("dry_run") is True,
        "trading_mode": payload.get("trading_mode"),
        "margin_mode": payload.get("margin_mode"),
        "timeframe": payload.get("timeframe"),
        "stake_currency": payload.get("stake_currency"),
        "max_open_trades": payload.get("max_open_trades"),
        "pair_whitelist": pair_whitelist,
        "pair_count": len(pair_whitelist),
        "exchange_name": exchange.get("name"),
        "exchange_credentials_configured": bool(exchange.get("key") or exchange.get("secret")),
        "api_server_enabled": api_server.get("enabled") is True,
        "telegram_enabled": telegram.get("enabled") is True,
        "force_entry_enabled": payload.get("force_entry_enable") is True,
        "snapshot_redacted": True,
        "source_read_only": True,
    }


def _base_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _base_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def inspect_strategy_file(path: Path) -> dict[str, Any]:
    """Inspect the strategy statically; never import or execute it."""
    content = path.read_text(encoding="utf-8-sig")
    tree = ast.parse(content, filename=str(path))
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
    class_names = [node.name for node in classes]
    strategy_classes = [
        node.name
        for node in classes
        if any(_base_name(base).endswith("IStrategy") for base in node.bases)
    ]
    method_names = sorted(
        {
            node.name
            for class_node in classes
            for node in class_node.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
    )
    normalized = content.lower()
    selector_references = sorted(
        token for token in ("ai_selector", "shadow_candidate", "promotion_status") if token in normalized
    )
    return {
        "status": "ok" if strategy_classes else "warning",
        "reason": (
            "freqtrade_strategy_contract_observed"
            if strategy_classes
            else "freqtrade_strategy_class_not_detected"
        ),
        "path": str(path),
        "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "class_names": class_names,
        "strategy_classes": strategy_classes,
        "method_names": method_names,
        "has_populate_indicators": "populate_indicators" in method_names,
        "has_populate_entry_trend": "populate_entry_trend" in method_names,
        "has_populate_exit_trend": "populate_exit_trend" in method_names,
        "ai_selector_references": selector_references,
        "ai_selector_reference_count": len(selector_references),
        "inspection_mode": "static_ast_read_only",
        "source_read_only": True,
        "strategy_imported": False,
        "strategy_executed": False,
    }


def _source_paths(paths: FreqtradePaperAISelectorPaths) -> dict[str, Path]:
    return {
        "freqtrade_config": paths.freqtrade_config_path,
        "freqtrade_strategy": paths.freqtrade_strategy_path,
        "branch04_training_summary": paths.training_summary_path,
        "branch05_executive_pack": paths.executive_pack_path,
        "branch06_candidate_report": paths.shadow_candidate_report_path,
        "branch07_feedback_loop_report": paths.feedback_loop_report_path,
    }


def collect_selector_evidence(paths: FreqtradePaperAISelectorPaths) -> dict[str, Any]:
    sources: dict[str, dict[str, Any]] = {}
    missing_sources: list[str] = []
    warnings: list[str] = []
    load_errors: list[str] = []
    for name, path in _source_paths(paths).items():
        if not path.exists():
            missing_sources.append(name)
            warnings.append(f"missing_source:{name}")
            sources[name] = {
                "available": False,
                "path": str(path),
                "payload": {},
                "load_error": None,
            }
            continue
        try:
            if name == "freqtrade_config":
                payload = load_freqtrade_config_snapshot(path)
            elif name == "freqtrade_strategy":
                payload = inspect_strategy_file(path)
            else:
                payload = load_json_report(path)
        except (OSError, UnicodeError, json.JSONDecodeError, SyntaxError, ValueError) as exc:
            error = f"{name}:{type(exc).__name__}"
            load_errors.append(error)
            warnings.append(f"invalid_source:{error}")
            sources[name] = {
                "available": False,
                "path": str(path),
                "payload": {},
                "load_error": error,
            }
            continue
        sources[name] = {
            "available": True,
            "path": str(path),
            "payload": payload,
            "load_error": None,
        }
    return {
        "sources": sources,
        "missing_sources": sorted(missing_sources),
        "warnings": sorted(set(warnings)),
        "load_errors": sorted(load_errors),
    }


def _payload(evidence: dict[str, Any], source: str) -> dict[str, Any]:
    return evidence.get("sources", {}).get(source, {}).get("payload", {})


def _value(payload: dict[str, Any], *paths: str) -> Any:
    for path in paths:
        current: Any = payload
        for part in path.split("."):
            if not isinstance(current, dict) or part not in current:
                current = None
                break
            current = current[part]
        if current is not None:
            return current
    return None


def _number(value: Any) -> float | int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return int(number) if number.is_integer() else number


def _unsafe_source_flags(evidence: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    for source, entry in evidence.get("sources", {}).items():
        if not entry.get("available"):
            continue
        payload = entry.get("payload", {})
        for flag in ("paper_only", "shadow_only"):
            if flag in payload and payload.get(flag) is not True:
                blockers.append(f"unsafe_safety_flag:{source}:{flag}={payload.get(flag)!r}")
        for flag in UNSAFE_TRUE_FLAGS:
            if payload.get(flag) is True:
                blockers.append(f"unsafe_safety_flag:{source}:{flag}=true")
    return blockers


def evaluate_selector_gate(
    evidence: dict[str, Any],
    config: FreqtradePaperAISelectorConfig,
) -> dict[str, Any]:
    blockers: list[str] = []
    branch04 = _payload(evidence, "branch04_training_summary")
    branch05 = _payload(evidence, "branch05_executive_pack")
    branch06 = _payload(evidence, "branch06_candidate_report")
    branch07 = _payload(evidence, "branch07_feedback_loop_report")
    if branch04.get("decision") == "MANTER_EM_RESEARCH":
        blockers.append("branch04_kept_in_research")
    selected = _number(
        _value(branch04, "aggregate_metrics.selected_net_pnl", "selected_net_pnl")
    )
    all_test = _number(
        _value(branch04, "aggregate_metrics.all_test_net_pnl", "all_test_net_pnl")
    )
    if selected is not None and all_test is not None and float(selected) <= float(all_test):
        blockers.append("branch04_selected_not_above_all_test")
    if branch05.get("decision") == "MANTER_EM_RESEARCH":
        blockers.append("branch05_kept_in_research")
    if branch06.get("promotion_status") == "blocked":
        blockers.append("branch06_promotion_blocked")
    if branch06.get("promotion_eligible") is False:
        blockers.append("branch06_not_promotion_eligible")
    if branch07.get("learning_action") == "record_only":
        blockers.append("branch07_record_only_feedback")
    if branch07.get("training_allowed") is False:
        blockers.append("branch07_training_not_allowed")
    if branch07.get("promotion_allowed") is False:
        blockers.append("branch07_promotion_not_allowed")

    unavailable = {
        name
        for name, entry in evidence.get("sources", {}).items()
        if not entry.get("available")
    }
    missing_freqtrade_files = sorted(
        name for name in ("freqtrade_config", "freqtrade_strategy") if name in unavailable
    )
    missing_research_sources = sorted(name for name in RESEARCH_SOURCE_NAMES if name in unavailable)
    blockers.extend(
        f"missing_required_selector_source:{source}" for source in missing_research_sources
    )
    unsafe_flags = _unsafe_source_flags(evidence)
    blockers.extend(unsafe_flags)
    blockers.append("paper_ai_selector_scope_forbids_operational_authority")
    return {
        "status": "blocked",
        "selector_authority": "none",
        "paper_signal_mutation_allowed": False,
        "freqtrade_strategy_update_allowed": False,
        "risk_manager_override_allowed": False,
        "order_submission_allowed": False,
        "selector_blockers": list(dict.fromkeys(blockers)),
        "unsafe_source_flags": unsafe_flags,
        "missing_freqtrade_files": missing_freqtrade_files,
        "missing_research_sources": missing_research_sources,
        "strict": config.strict,
        "recommended_operator_next_actions": [
            "keep_freqtrade_strategy_and_risk_manager_unchanged",
            "continue_collecting_paper_shadow_selector_evidence",
            "require_research_and_promotion_gates_before_any_future_integration",
            "review_observation_report_without_granting_ai_authority",
        ],
    }


def _stable_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _observation(
    *,
    observation_type: str,
    source: str,
    status: str,
    decision: str,
    summary: str,
    analysis_date_utc: str,
    identity_payload: dict[str, Any],
) -> dict[str, Any]:
    identity = f"{observation_type}|{source}|{_stable_hash(identity_payload)}"
    return {
        "observation_id": hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32],
        "observation_type": observation_type,
        "analysis_date_utc": analysis_date_utc,
        "source": source,
        "status": status,
        "decision": decision,
        "summary": summary,
        "action_taken": "record_only",
        "paper_only": True,
        "shadow_only": True,
        "sends_orders": False,
        "changes_risk": False,
        "updates_freqtrade": False,
        "updates_qlib_runtime": False,
        "updates_risk_manager": False,
        "updates_ai_shadow_runtime": False,
        "registers_model": False,
        "production_enabled": False,
    }


def build_selector_observations(
    evidence: dict[str, Any],
    gate: dict[str, Any],
    analysis_date_utc: str,
) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for source, observation_type in OBSERVATION_TYPES.items():
        entry = evidence.get("sources", {}).get(source, {})
        if not entry.get("available"):
            continue
        payload = entry.get("payload", {})
        observations.append(
            _observation(
                observation_type=observation_type,
                source=str(entry.get("path") or source),
                status=str(payload.get("status") or "observed"),
                decision=str(
                    payload.get("decision")
                    or payload.get("promotion_status")
                    or ("DRY_RUN" if payload.get("dry_run") is True else "OBSERVED")
                ),
                summary=str(payload.get("reason") or f"{source}_observed"),
                analysis_date_utc=analysis_date_utc,
                identity_payload=payload,
            )
        )
    observations.append(
        _observation(
            observation_type="paper_ai_selector_gate_blocked",
            source="paper_ai_selector_observability_gate",
            status="blocked",
            decision="MANTER_EM_RESEARCH",
            summary=";".join(gate["selector_blockers"]),
            analysis_date_utc=analysis_date_utc,
            identity_payload={"selector_blockers": gate["selector_blockers"]},
        )
    )
    observations.append(
        _observation(
            observation_type="recommended_operator_next_actions_recorded",
            source="paper_ai_selector_observability_gate",
            status="warning",
            decision="RECORD_ONLY",
            summary=";".join(gate["recommended_operator_next_actions"]),
            analysis_date_utc=analysis_date_utc,
            identity_payload={
                "recommended_operator_next_actions": gate[
                    "recommended_operator_next_actions"
                ]
            },
        )
    )
    return sorted(
        observations,
        key=lambda observation: (
            observation["observation_type"],
            observation["observation_id"],
        ),
    )


def build_selector_report(
    evidence: dict[str, Any],
    gate: dict[str, Any],
    observations: list[dict[str, Any]],
    config: FreqtradePaperAISelectorConfig,
    analysis_date_utc: str,
) -> dict[str, Any]:
    hard_block = bool(
        gate.get("missing_research_sources")
        or gate.get("unsafe_source_flags")
        or (config.strict and gate.get("missing_freqtrade_files"))
    )
    return {
        "integration_version": config.integration_version,
        "status": "blocked" if hard_block else "warning",
        "reason": "selector_observed_without_operational_authority",
        "selector_status": "observe_only_blocked",
        "selector_authority": "none",
        "decision": "MANTER_EM_RESEARCH",
        "freqtrade_integration_status": "paper_observability_only",
        "analysis_date_utc": analysis_date_utc,
        "selector_blockers": list(gate["selector_blockers"]),
        "unsafe_source_flags": list(gate["unsafe_source_flags"]),
        "missing_freqtrade_files": list(gate["missing_freqtrade_files"]),
        "missing_research_sources": list(gate["missing_research_sources"]),
        "missing_sources": list(evidence.get("missing_sources", [])),
        "warnings": list(evidence.get("warnings", [])),
        "load_errors": list(evidence.get("load_errors", [])),
        "recommended_operator_next_actions": list(
            gate["recommended_operator_next_actions"]
        ),
        "source_status": {
            name: {
                "available": bool(entry.get("available")),
                "path": entry.get("path"),
                "status": entry.get("payload", {}).get("status"),
                "reason": entry.get("payload", {}).get("reason"),
                "decision": entry.get("payload", {}).get("decision"),
                "load_error": entry.get("load_error"),
            }
            for name, entry in evidence.get("sources", {}).items()
        },
        "freqtrade_config_snapshot": _payload(evidence, "freqtrade_config"),
        "freqtrade_strategy_inspection": _payload(evidence, "freqtrade_strategy"),
        "observation_count": len(observations),
        "observation_types": [
            observation["observation_type"] for observation in observations
        ],
        "strict": config.strict,
        "write_requested": False,
        "write_performed": False,
        "new_observations_written": 0,
        **SAFETY_FLAGS,
    }


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    content = json.dumps(
        _json_safe(payload),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    )
    _atomic_write_text(path, content + "\n")


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    observations: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8-sig").splitlines(), 1
    ):
        if not line.strip():
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict):
            raise ValueError(f"jsonl_row_must_be_object:{path}:{line_number}")
        observations.append(payload)
    return observations


def _atomic_write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    content = "\n".join(
        json.dumps(
            _json_safe(row),
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
        )
        for row in rows
    )
    _atomic_write_text(path, content + ("\n" if content else ""))


def run_freqtrade_paper_ai_selector_integration(
    paths: FreqtradePaperAISelectorPaths,
    config: FreqtradePaperAISelectorConfig,
    *,
    write: bool = False,
    analysis_date_utc: str | None = None,
) -> FreqtradePaperAISelectorResult:
    analysis_date = analysis_date_utc or (
        datetime.now(timezone.utc).isoformat() if write else "not_recorded_no_write"
    )
    evidence = collect_selector_evidence(paths)
    gate = evaluate_selector_gate(evidence, config)
    observations = build_selector_observations(evidence, gate, analysis_date)
    report = build_selector_report(evidence, gate, observations, config, analysis_date)
    report["write_requested"] = write
    report["report_output_path"] = str(paths.report_output_path)
    report["observations_output_path"] = str(paths.observations_output_path)
    existing_observations = _load_jsonl(paths.observations_output_path)
    existing_ids = {
        str(observation.get("observation_id")) for observation in existing_observations
    }
    new_observations = [
        observation
        for observation in observations
        if observation["observation_id"] not in existing_ids
    ]
    report["existing_observation_count"] = len(existing_observations)
    report["new_observations_written"] = len(new_observations) if write else 0
    if write:
        _atomic_write_jsonl(
            paths.observations_output_path,
            [*existing_observations, *new_observations],
        )
        report["write_performed"] = True
        _atomic_write_json(paths.report_output_path, report)
    return FreqtradePaperAISelectorResult(
        report=report,
        observations=observations,
    )
