"""Research-only closeout for the AI training track Branches 01 through 09."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "ai_research_training_track_closeout_handover_v1"

SOURCE_PATHS: dict[str, str] = {
    "branch01_research_dataset": "data/reports/ocr_v11_research_dataset_audit.json",
    "branch02_tp_sl_grid": "data/reports/ocr_v11_tp_sl_grid_summary.json",
    "branch03_walkforward_montecarlo": (
        "data/reports/ocr_v11_walkforward_montecarlo_summary.json"
    ),
    "branch04_qlib_training": (
        "data/reports/qlib_ocr_v11_supervised_training_summary.json"
    ),
    "branch05_executive_pack": (
        "data/reports/training_reports/smart_futuros_training_executive_pack.json"
    ),
    "branch06_candidate_registry": (
        "data/reports/qlib_ocr_v11_shadow_model_candidate_registry_report.json"
    ),
    "branch07_feedback_loop": (
        "data/reports/ai_shadow_online_feedback_learning_loop_report.json"
    ),
    "branch08_freqtrade_selector": (
        "data/reports/freqtrade_paper_ai_selector_integration_report.json"
    ),
    "branch09_dashboard_command_center": (
        "data/reports/dashboard_ai_governance_snapshot.json"
    ),
}

DEFAULT_REPORT_PATH = (
    "data/reports/ai_research_training_track_closeout_handover_summary.json"
)
DEFAULT_MARKDOWN_PATH = (
    "data/reports/training_reports/ai_research_training_track_closeout_handover.md"
)

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
    "runs_ocr": False,
    "rebuilds_dataset": False,
    "updates_freqtrade": False,
    "updates_qlib_runtime": False,
    "updates_risk_manager": False,
    "updates_ai_shadow_runtime": False,
    "registers_model": False,
    "promotes_model": False,
    "production_enabled": False,
}

UNSAFE_TRUE_FLAGS = tuple(
    key for key, expected in SAFETY_FLAGS.items() if expected is False
)

NEXT_REQUIRED_GATES = (
    "runtime_evidence_current_and_complete",
    "readiness_gate_approved_without_research_shortcuts",
    "paper_shadow_soak_completed",
    "market_and_runtime_freshness_healthy",
    "manual_institutional_review_after_new_out_of_sample_evidence",
)


@dataclass(frozen=True)
class AIResearchTrainingTrackCloseoutPaths:
    project_root: Path
    source_paths: dict[str, Path]
    report_output_path: Path
    markdown_output_path: Path


@dataclass(frozen=True)
class AIResearchTrainingTrackCloseoutResult:
    report: dict[str, Any]
    markdown: str


def resolve_paths(
    project_root: str | Path,
    *,
    report_output: str | Path | None = None,
    markdown_output: str | Path | None = None,
) -> AIResearchTrainingTrackCloseoutPaths:
    root = Path(project_root).expanduser().resolve()
    return AIResearchTrainingTrackCloseoutPaths(
        project_root=root,
        source_paths={key: (root / path).resolve() for key, path in SOURCE_PATHS.items()},
        report_output_path=_resolve(root, report_output, DEFAULT_REPORT_PATH),
        markdown_output_path=_resolve(root, markdown_output, DEFAULT_MARKDOWN_PATH),
    )


def load_json_report(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"json_report_must_be_object:{path}")
    return payload


def collect_closeout_sources(
    paths: AIResearchTrainingTrackCloseoutPaths,
) -> dict[str, Any]:
    payloads: dict[str, dict[str, Any]] = {}
    source_status: dict[str, dict[str, Any]] = {}
    missing_optional_sources: list[str] = []
    warnings: list[str] = []
    for source_key, path in paths.source_paths.items():
        if not path.is_file():
            missing_optional_sources.append(source_key)
            source_status[source_key] = {
                "status": "MISSING_OPTIONAL",
                "available": False,
                "path": str(path),
                "reason": "optional_source_missing",
                "load_error": None,
            }
            payloads[source_key] = {}
            continue
        try:
            payload = load_json_report(path)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            missing_optional_sources.append(source_key)
            error = f"{type(exc).__name__}:{exc}"
            warnings.append(f"invalid_optional_source:{source_key}:{type(exc).__name__}")
            source_status[source_key] = {
                "status": "MISSING_OPTIONAL",
                "available": False,
                "path": str(path),
                "reason": "optional_source_invalid",
                "load_error": error,
            }
            payloads[source_key] = {}
            continue
        payloads[source_key] = payload
        normalized_payload = (
            _dashboard_section(payload)
            if source_key == "branch09_dashboard_command_center"
            else payload
        )
        if not normalized_payload:
            missing_optional_sources.append(source_key)
        source_status[source_key] = {
            "status": str(
                normalized_payload.get("status")
                or normalized_payload.get("section_status")
                or "MISSING_OPTIONAL"
            ),
            "available": True,
            "path": str(path),
            "reason": normalized_payload.get("reason")
            or ("optional_evidence_section_missing" if not normalized_payload else None),
            "decision": normalized_payload.get("decision"),
            "load_error": None,
        }
    return {
        "payloads": payloads,
        "source_status": source_status,
        "missing_optional_sources": sorted(set(missing_optional_sources)),
        "warnings": sorted(set(warnings)),
    }


def build_ai_research_training_track_closeout_handover(
    all_source_payloads: Mapping[str, Any],
    *,
    source_status: Mapping[str, Any] | None = None,
    missing_optional_sources: list[str] | tuple[str, ...] = (),
    warnings: list[str] | tuple[str, ...] = (),
    analysis_date_utc: str = "not_recorded_no_write",
) -> dict[str, Any]:
    """Build a deterministic research-only closeout payload without I/O."""
    payloads = {
        source_key: _source_payload(all_source_payloads, source_key)
        for source_key in SOURCE_PATHS
    }
    derived_missing = [
        key
        for key, payload in payloads.items()
        if not payload
        or (
            key == "branch09_dashboard_command_center"
            and not _dashboard_section(payload)
        )
    ]
    missing = sorted(set(missing_optional_sources) | set(derived_missing))
    cards = _build_branch_cards(payloads)
    blockers = _promotion_blockers(payloads, missing)
    metrics = _consolidated_metrics(payloads)
    normalized_source_status = _source_status(payloads, source_status)
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "warning",
        "reason": "research_track_closed_without_operational_promotion",
        "track_status": "closed_research_only",
        "decision": "MANTER_EM_RESEARCH",
        "research_gate_status": "BLOCKED",
        "promotion_status": "blocked",
        "operational_authority": False,
        "analysis_date_utc": analysis_date_utc,
        "executive_summary": {
            "title": "AI Research Training Track 01-09 Closeout",
            "conclusion": "Trilha encerrada como research-only, sem promoção ou autoridade operacional.",
            "partner_message": (
                "A pesquisa produziu evidências auditáveis, mas os resultados fora da "
                "amostra e os gates de governança não sustentam promoção operacional."
            ),
            "recommended_action": (
                "Preservar o modelo e o seletor em pesquisa e avançar somente nos "
                "gates de runtime, readiness, soak e freshness."
            ),
        },
        "branch_cards": cards,
        "consolidated_metrics": metrics,
        "promotion_blockers": blockers,
        "next_required_gates": list(NEXT_REQUIRED_GATES),
        "safety_flags": dict(SAFETY_FLAGS),
        "source_status": normalized_source_status,
        "missing_optional_sources": missing,
        "warnings": sorted(set(warnings)),
        "write_requested": False,
        "write_performed": False,
        "report_output_path": None,
        "markdown_output_path": None,
    }


def render_closeout_markdown(report: Mapping[str, Any]) -> str:
    """Render an executive Markdown handover from the normalized payload."""
    cards = report.get("branch_cards")
    cards = cards if isinstance(cards, list) else []
    metrics = report.get("consolidated_metrics")
    metrics = metrics if isinstance(metrics, Mapping) else {}
    blockers = report.get("promotion_blockers")
    blockers = blockers if isinstance(blockers, list) else []
    gates = report.get("next_required_gates")
    gates = gates if isinstance(gates, list) else []
    safety = report.get("safety_flags")
    safety = safety if isinstance(safety, Mapping) else {}
    lines = [
        "# AI Research Training Track 01-09 - Closeout Handover",
        "",
        f"**Data da análise:** {_markdown_value(report.get('analysis_date_utc'))}",
        "",
        "## Contexto",
        "",
        "Consolidação executiva e técnica das nove etapas de pesquisa OCR, simulação, validação, treinamento e governança shadow.",
        "",
        "## Decisão final",
        "",
        f"- Track status: `{_markdown_value(report.get('track_status'))}`",
        f"- Decisão: `{_markdown_value(report.get('decision'))}`",
        f"- Research gate: `{_markdown_value(report.get('research_gate_status'))}`",
        f"- Promotion status: `{_markdown_value(report.get('promotion_status'))}`",
        "- Autoridade operacional: `false`",
        "",
        "## Evidências por branch",
        "",
        "| Branch | Evidência | Status | Decisão | Métrica principal | Razão |",
        "|---|---|---|---|---|---|",
    ]
    for card in cards:
        if not isinstance(card, Mapping):
            continue
        headline = card.get("headline_metric")
        headline = headline if isinstance(headline, Mapping) else {}
        metric = f"{headline.get('label', 'N/A')}: {headline.get('value', 'N/A')}"
        lines.append(
            "| "
            + " | ".join(
                _markdown_cell(value)
                for value in (
                    card.get("branch_id"),
                    card.get("title"),
                    card.get("status"),
                    card.get("decision"),
                    metric,
                    card.get("reason"),
                )
            )
            + " |"
        )
    lines.extend(["", "## Métricas consolidadas", ""])
    lines.extend(
        f"- {key}: `{_markdown_value(value)}`" for key, value in metrics.items()
    )
    lines.extend(["", "## Blockers de promoção", ""])
    lines.extend(f"- `{_markdown_value(blocker)}`" for blocker in blockers)
    lines.extend(["", "## Segurança operacional", ""])
    lines.extend(f"- {key}: `{str(value).lower()}`" for key, value in safety.items())
    lines.extend(["", "## Próximos gates", ""])
    lines.extend(f"- `{_markdown_value(gate)}`" for gate in gates)
    lines.extend(
        [
            "",
            "## Mensagem executiva para sócios e parceiros",
            "",
            str(
                _mapping(report.get("executive_summary")).get(
                    "partner_message",
                    "Trilha encerrada como research-only, sem promoção operacional.",
                )
            ),
            "",
            "Não promover modelo, não conceder autoridade ao seletor e não alterar RiskManager ou Freqtrade. O próximo avanço depende de evidências de runtime, readiness, soak e freshness.",
            "",
        ]
    )
    return "\n".join(lines)


def run_ai_research_training_track_closeout_handover(
    paths: AIResearchTrainingTrackCloseoutPaths,
    *,
    write: bool = False,
    analysis_date_utc: str | None = None,
) -> AIResearchTrainingTrackCloseoutResult:
    collected = collect_closeout_sources(paths)
    analysis_date = analysis_date_utc or (
        datetime.now(timezone.utc).isoformat() if write else "not_recorded_no_write"
    )
    report = build_ai_research_training_track_closeout_handover(
        collected["payloads"],
        source_status=collected["source_status"],
        missing_optional_sources=collected["missing_optional_sources"],
        warnings=collected["warnings"],
        analysis_date_utc=analysis_date,
    )
    report["write_requested"] = write
    report["report_output_path"] = str(paths.report_output_path)
    report["markdown_output_path"] = str(paths.markdown_output_path)
    markdown = render_closeout_markdown(report)
    if write:
        _atomic_write_text(paths.markdown_output_path, markdown)
        report["write_performed"] = True
        _atomic_write_json(paths.report_output_path, report)
    return AIResearchTrainingTrackCloseoutResult(report=report, markdown=markdown)


def _resolve(root: Path, value: str | Path | None, default: str) -> Path:
    candidate = Path(value if value is not None else default).expanduser()
    return (root / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()


def _source_payload(sources: Mapping[str, Any], source_key: str) -> dict[str, Any]:
    value = sources.get(source_key)
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, list):
        return dict(value[0]) if value and isinstance(value[0], Mapping) else {}
    return {}


def _dashboard_section(payload: Mapping[str, Any]) -> dict[str, Any]:
    sections = payload.get("sections")
    if not isinstance(sections, Mapping):
        return {}
    section = sections.get("ai_training_research_command_center")
    return dict(section) if isinstance(section, Mapping) else {}


def _build_branch_cards(payloads: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    branch09 = _dashboard_section(payloads["branch09_dashboard_command_center"])
    return [
        _card(
            "branch01_research_dataset",
            "OCR V1.1 Research Dataset & Candle Alignment",
            payloads["branch01_research_dataset"],
            "Research trades",
            payloads["branch01_research_dataset"].get("research_dataset_rows"),
            {
                "eligible_rows": payloads["branch01_research_dataset"].get("eligible_rows"),
                "blocked_rows": payloads["branch01_research_dataset"].get("blocked_rows"),
            },
            "DATASET_ALIGNED_RESEARCH_ONLY",
        ),
        _card(
            "branch02_tp_sl_grid",
            "TP/SL Grid Simulator",
            payloads["branch02_tp_sl_grid"],
            "Grid strategies",
            payloads["branch02_tp_sl_grid"].get("grid_rows"),
            {
                "best_strategy_id": payloads["branch02_tp_sl_grid"].get("best_strategy_id"),
                "best_net_pnl": payloads["branch02_tp_sl_grid"].get("best_net_pnl"),
                "original_net_pnl": payloads["branch02_tp_sl_grid"].get("original_net_pnl"),
            },
            "GRID_EVALUATED_RESEARCH_ONLY",
        ),
        _card(
            "branch03_walkforward_montecarlo",
            "Walk-forward & Monte Carlo",
            payloads["branch03_walkforward_montecarlo"],
            "Candidate walk-forward net PnL",
            payloads["branch03_walkforward_montecarlo"].get(
                "candidate_walkforward_net_pnl"
            ),
            {
                "original_walkforward_net_pnl": payloads[
                    "branch03_walkforward_montecarlo"
                ].get("original_walkforward_net_pnl"),
                "risk_of_ruin": _mapping(
                    payloads["branch03_walkforward_montecarlo"].get("monte_carlo")
                ).get("risk_of_ruin"),
            },
            "MANTER_EM_RESEARCH",
        ),
        _card(
            "branch04_qlib_training",
            "Qlib OCR V1.1 Supervised Training",
            payloads["branch04_qlib_training"],
            "Selected net PnL",
            _mapping(payloads["branch04_qlib_training"].get("aggregate_metrics")).get(
                "selected_net_pnl"
            ),
            {
                "all_test_net_pnl": _mapping(
                    payloads["branch04_qlib_training"].get("aggregate_metrics")
                ).get("all_test_net_pnl"),
                "mean_roc_auc": _mapping(
                    payloads["branch04_qlib_training"].get("aggregate_metrics")
                ).get("mean_roc_auc"),
                "mean_f1": _mapping(
                    payloads["branch04_qlib_training"].get("aggregate_metrics")
                ).get("mean_f1"),
            },
            "MANTER_EM_RESEARCH",
        ),
        _card(
            "branch05_executive_pack",
            "Training Executive Report Pack",
            payloads["branch05_executive_pack"],
            "Executive decision",
            payloads["branch05_executive_pack"].get("decision"),
            {
                "eligible_rows": _mapping(
                    payloads["branch05_executive_pack"].get("consolidated_kpis")
                ).get("eligible_rows"),
                "blocked_rows": _mapping(
                    payloads["branch05_executive_pack"].get("consolidated_kpis")
                ).get("blocked_rows"),
            },
            "MANTER_EM_RESEARCH",
        ),
        _card(
            "branch06_candidate_registry",
            "Qlib OCR V1.1 Shadow Candidate Registry",
            payloads["branch06_candidate_registry"],
            "Promotion status",
            payloads["branch06_candidate_registry"].get("promotion_status"),
            {
                "candidate_registry_status": payloads["branch06_candidate_registry"].get(
                    "candidate_registry_status"
                ),
                "promotion_eligible": payloads["branch06_candidate_registry"].get(
                    "promotion_eligible"
                ),
            },
            "MANTER_EM_RESEARCH",
        ),
        _card(
            "branch07_feedback_loop",
            "AI Shadow Online Feedback Learning Loop",
            payloads["branch07_feedback_loop"],
            "Learning action",
            payloads["branch07_feedback_loop"].get("learning_action"),
            {
                "training_allowed": payloads["branch07_feedback_loop"].get(
                    "training_allowed"
                ),
                "promotion_allowed": payloads["branch07_feedback_loop"].get(
                    "promotion_allowed"
                ),
            },
            "MANTER_EM_RESEARCH",
        ),
        _card(
            "branch08_freqtrade_selector",
            "Freqtrade Paper AI Selector Observability",
            payloads["branch08_freqtrade_selector"],
            "Selector authority",
            payloads["branch08_freqtrade_selector"].get("selector_authority"),
            {
                "selector_status": payloads["branch08_freqtrade_selector"].get(
                    "selector_status"
                ),
                "paper_signal_mutation_allowed": payloads[
                    "branch08_freqtrade_selector"
                ].get("paper_signal_mutation_allowed"),
            },
            "MANTER_EM_RESEARCH",
        ),
        _card(
            "branch09_dashboard_command_center",
            "Dashboard AI Training Research Command Center",
            branch09,
            "Research gate",
            branch09.get("research_gate_status"),
            {
                "authority": branch09.get("authority"),
                "operational_authority": branch09.get("operational_authority"),
                "section_status": branch09.get("section_status"),
            },
            "MANTER_EM_RESEARCH",
        ),
    ]


def _card(
    branch_id: str,
    title: str,
    payload: Mapping[str, Any],
    headline_label: str,
    headline_value: Any,
    supporting_metrics: Mapping[str, Any],
    default_decision: str,
) -> dict[str, Any]:
    if not payload:
        return {
            "branch_id": branch_id,
            "title": title,
            "status": "MISSING_OPTIONAL",
            "decision": "UNKNOWN",
            "headline_metric": {"label": headline_label, "value": None},
            "supporting_metrics": dict(supporting_metrics),
            "reason": "optional_source_missing",
            "source_key": branch_id,
            "source_path": SOURCE_PATHS[branch_id],
            "advisory_only": True,
        }
    return {
        "branch_id": branch_id,
        "title": title,
        "status": str(payload.get("status") or payload.get("section_status") or "UNKNOWN").upper(),
        "decision": str(payload.get("decision") or default_decision),
        "headline_metric": {"label": headline_label, "value": _json_safe(headline_value)},
        "supporting_metrics": _json_safe(dict(supporting_metrics)),
        "reason": str(payload.get("reason") or "research_evidence_observed"),
        "source_key": branch_id,
        "source_path": SOURCE_PATHS[branch_id],
        "advisory_only": True,
    }


def _consolidated_metrics(payloads: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    branch01 = payloads["branch01_research_dataset"]
    branch02 = payloads["branch02_tp_sl_grid"]
    branch03 = payloads["branch03_walkforward_montecarlo"]
    branch04_metrics = _mapping(payloads["branch04_qlib_training"].get("aggregate_metrics"))
    branch06 = payloads["branch06_candidate_registry"]
    branch08 = payloads["branch08_freqtrade_selector"]
    branch09 = _dashboard_section(payloads["branch09_dashboard_command_center"])
    return {
        "research_dataset_rows": _number(branch01.get("research_dataset_rows")),
        "eligible_rows": _number(branch01.get("eligible_rows")),
        "blocked_rows": _number(branch01.get("blocked_rows")),
        "tp_sl_grid_rows": _number(branch02.get("grid_rows")),
        "tp_sl_best_net_pnl": _number(branch02.get("best_net_pnl")),
        "original_net_pnl": _number(branch02.get("original_net_pnl")),
        "candidate_walkforward_net_pnl": _number(
            branch03.get("candidate_walkforward_net_pnl")
        ),
        "original_walkforward_net_pnl": _number(
            branch03.get("original_walkforward_net_pnl")
        ),
        "mean_roc_auc": _number(branch04_metrics.get("mean_roc_auc")),
        "mean_f1": _number(branch04_metrics.get("mean_f1")),
        "selected_net_pnl": _number(branch04_metrics.get("selected_net_pnl")),
        "all_test_net_pnl": _number(branch04_metrics.get("all_test_net_pnl")),
        "candidate_promotion_status": branch06.get("promotion_status"),
        "selector_authority": branch08.get("selector_authority", "none"),
        "dashboard_research_gate_status": branch09.get("research_gate_status"),
        "dashboard_authority": branch09.get("authority"),
    }


def _promotion_blockers(
    payloads: Mapping[str, Mapping[str, Any]],
    missing: list[str],
) -> list[str]:
    blockers = [f"missing_optional_source:{source}" for source in missing]
    branch02 = payloads["branch02_tp_sl_grid"]
    branch03 = payloads["branch03_walkforward_montecarlo"]
    branch04 = payloads["branch04_qlib_training"]
    branch04_metrics = _mapping(branch04.get("aggregate_metrics"))
    branch05 = payloads["branch05_executive_pack"]
    branch06 = payloads["branch06_candidate_registry"]
    branch07 = payloads["branch07_feedback_loop"]
    branch08 = payloads["branch08_freqtrade_selector"]
    branch09 = _dashboard_section(payloads["branch09_dashboard_command_center"])
    if (_number(branch02.get("best_net_pnl")) or 0.0) < 0.0:
        blockers.append("branch02_best_net_pnl_not_positive")
    if _upper(branch03.get("status")) == "BLOCKED" or _upper(
        branch03.get("decision")
    ) == "DESCARTAR_CANDIDATO":
        blockers.append("branch03_candidate_rejected")
    if _upper(branch04.get("decision")) == "MANTER_EM_RESEARCH":
        blockers.append("branch04_kept_in_research")
    selected = _number(branch04_metrics.get("selected_net_pnl"))
    all_test = _number(branch04_metrics.get("all_test_net_pnl"))
    if selected is not None and all_test is not None and selected <= all_test:
        blockers.append("branch04_selected_not_above_all_test")
    if _upper(branch05.get("decision")) == "MANTER_EM_RESEARCH":
        blockers.append("branch05_kept_in_research")
    if _upper(branch06.get("promotion_status")) == "BLOCKED":
        blockers.append("branch06_promotion_blocked")
    if branch06.get("promotion_eligible") is False:
        blockers.append("branch06_not_promotion_eligible")
    if _upper(branch07.get("learning_action")) == "RECORD_ONLY":
        blockers.append("branch07_record_only_feedback")
    if branch07.get("training_allowed") is False:
        blockers.append("branch07_training_not_allowed")
    if branch07.get("promotion_allowed") is False:
        blockers.append("branch07_promotion_not_allowed")
    if branch08.get("selector_authority") is None or _upper(
        branch08.get("selector_authority")
    ) == "NONE":
        blockers.append("branch08_selector_has_no_authority")
    if branch09.get("research_gate_status") is None or _upper(
        branch09.get("research_gate_status")
    ) == "BLOCKED":
        blockers.append("branch09_dashboard_research_gate_blocked")
    if branch09.get("operational_authority") is not True:
        blockers.append("branch09_dashboard_has_no_operational_authority")
    for source_key, payload in payloads.items():
        source = _dashboard_section(payload) if source_key == "branch09_dashboard_command_center" else payload
        for flag in UNSAFE_TRUE_FLAGS:
            if source.get(flag) is True:
                blockers.append(f"unsafe_source_flag:{source_key}:{flag}=true")
    blockers.append("closeout_scope_forbids_operational_authority")
    return list(dict.fromkeys(blockers))


def _source_status(
    payloads: Mapping[str, Mapping[str, Any]],
    supplied: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if supplied is not None:
        return _json_safe(dict(supplied))
    return {
        source_key: {
            "status": str(
                payload.get("status")
                or _dashboard_section(payload).get("status")
                or "MISSING_OPTIONAL"
            ),
            "available": bool(payload),
            "path": SOURCE_PATHS[source_key],
            "reason": payload.get("reason") or _dashboard_section(payload).get("reason"),
        }
        for source_key, payload in payloads.items()
    }


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _upper(value: Any) -> str:
    return str(value or "").strip().upper()


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


def _markdown_value(value: Any) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


def _markdown_cell(value: Any) -> str:
    return _markdown_value(value).replace("|", "\\|").replace("\n", " ")


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, str | int | bool):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return str(value)


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    content = json.dumps(
        _json_safe(payload),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    )
    _atomic_write_text(path, content + "\n")
