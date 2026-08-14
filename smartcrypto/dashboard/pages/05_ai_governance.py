from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from smartcrypto.dashboard.components.ai_training_research_command_center import (
    render_ai_training_research_command_center,
)
from smartcrypto.dashboard.components.read_only import get_streamlit, render_snapshot_page
from smartcrypto.dashboard.services.page_snapshot_loader import load_page_snapshot
from smartcrypto.dashboard.ui import (
    inject_smart_futuros_command_center_css,
    render_compact_kpi,
    render_footer_audit_bar,
    render_global_topbar,
    render_html_table,
    render_page_title,
    render_readonly_banner,
    render_section_panel,
    render_sidebar,
    status_to_label,
)
from smartcrypto.ops.dashboard_snapshots.contracts import DashboardPageId


PAGE_TITLE = "05. IA / Qlib Governance"
PAGE_NUMBER = "05"
PAGE_NAME = "IA / Qlib Governance"
PAGE_SUBTITLE = (
    "MLOps e governança snapshot-first: Qlib ranking, IA Shadow, drift e pesquisa "
    "sem autoridade operacional."
)
ACTIVE_PAGE = "05_ai_governance"
SNAPSHOT_PATH = "data/reports/dashboard_ai_governance_snapshot.json"
EXPECTED_SCHEMA_VERSION = "dashboard_ai_governance_snapshot_v1"
REQUIRED_SECTIONS = (
    "model_state",
    "qlib_ranking",
    "shadow_veto",
    "decision_governance",
    "drift_regime",
    "shadow_classification_metrics",
    "reward_research",
    "model_governance",
    "ai_training_research_command_center",
    "audit",
)

_RANKING_COLUMNS = (
    "Ativo",
    "Score / ETV",
    "Retorno esperado",
    "Probabilidade",
    "Status",
)
_RESEARCH_COLUMNS = (
    "Branch",
    "Evidência",
    "Status",
    "Decisão",
    "Métrica",
    "Valor",
    "Advisory only",
)


def render_page(snapshot: dict[str, Any], *, ui: Any | None = None) -> None:
    """Render Aba 05 strictly from the canonical read-only AI governance snapshot."""

    target_ui = ui or get_streamlit()
    inject_smart_futuros_command_center_css(ui=target_ui)
    render_global_topbar(
        last_updated=_text_or_none(snapshot.get("last_updated_utc")),
        ui=target_ui,
    )
    render_sidebar(
        ACTIVE_PAGE,
        {"environment": "shadow", "snapshot": SNAPSHOT_PATH},
        ui=target_ui,
    )
    render_page_title(PAGE_NUMBER, PAGE_NAME, PAGE_SUBTITLE, ui=target_ui)
    render_readonly_banner(ui=target_ui)

    sections = _sections(snapshot)
    model_state = _mapping(sections.get("model_state"))
    qlib_ranking = _mapping(sections.get("qlib_ranking"))
    shadow_veto = _mapping(sections.get("shadow_veto"))
    decision_governance = _mapping(sections.get("decision_governance"))
    drift_regime = _mapping(sections.get("drift_regime"))
    classification = _mapping(sections.get("shadow_classification_metrics"))
    reward_research = _mapping(sections.get("reward_research"))
    model_governance = _mapping(sections.get("model_governance"))
    research = _mapping(sections.get("ai_training_research_command_center"))
    audit = _mapping(sections.get("audit"))

    ranking_rows = _rows(qlib_ranking.get("ranking"))
    branch_cards = _rows(research.get("branch_cards"))

    _render_primary_kpi_grid(
        _primary_kpi_cards(
            model_state=model_state,
            shadow_veto=shadow_veto,
            drift_regime=drift_regime,
            research=research,
            model_governance=model_governance,
            decision_governance=decision_governance,
        ),
        ui=target_ui,
    )

    target_ui.markdown(
        render_section_panel(
            "Qlib Ranking",
            render_html_table(
                _ranking_table_rows(ranking_rows, qlib_ranking),
                columns=list(_RANKING_COLUMNS),
                status_columns=["Status"],
                empty_message=(
                    "Ranking Qlib não materializado no snapshot canônico · UNKNOWN"
                ),
            ),
            subtitle=(
                "Ranking observacional somente quando materializado; nenhuma previsão "
                "ausente é sintetizada e nenhum item possui autoridade de execução."
            ),
            status=_ranking_status(qlib_ranking, ranking_rows),
        ),
        unsafe_allow_html=True,
    )

    shadow_column, quality_column = target_ui.columns(2)
    shadow_column.markdown(
        render_section_panel(
            "IA Shadow · Veto & Decisions",
            render_html_table(
                _shadow_veto_rows(shadow_veto),
                columns=["Métrica", "Valor"],
                empty_message="Decisões IA Shadow não materializadas · UNKNOWN",
            ),
            subtitle=(
                "AI_ACCEPT / AI_REJECT são observacionais; a IA não aumenta risco "
                "e não substitui o RiskManager."
            ),
            status=_section_status(shadow_veto),
        ),
        unsafe_allow_html=True,
    )
    quality_column.markdown(
        render_section_panel(
            "IA Shadow · Classification Quality",
            render_html_table(
                _classification_rows(classification),
                columns=["Métrica", "Valor"],
                empty_message="Métricas de classificação não materializadas · UNKNOWN",
            ),
            subtitle="Precision, recall, F1, accuracy e Brier somente quando materializados.",
            status=_section_status(classification),
        ),
        unsafe_allow_html=True,
    )

    drift_column, reward_column = target_ui.columns(2)
    drift_column.markdown(
        render_section_panel(
            "Drift & Regime",
            render_html_table(
                _drift_rows(drift_regime),
                columns=["Métrica", "Valor"],
                empty_message="Drift/regime não materializado · UNKNOWN",
            ),
            subtitle="PSI e classificação de drift derivados do snapshot canônico.",
            status=_drift_status(drift_regime),
        ),
        unsafe_allow_html=True,
    )
    reward_column.markdown(
        render_section_panel(
            "Reward Research",
            render_html_table(
                _reward_rows(reward_research),
                columns=["Métrica", "Valor"],
                empty_message="Reward research não materializado · UNKNOWN",
            ),
            subtitle=(
                "Pesquisa financeira permanece consultiva; ausência de ETV nunca é "
                "convertida em zero."
            ),
            status=_section_status(reward_research),
        ),
        unsafe_allow_html=True,
    )

    decision_column, governance_column = target_ui.columns(2)
    decision_column.markdown(
        render_section_panel(
            "Decision Governance",
            render_html_table(
                _decision_rows(decision_governance),
                columns=["Controle", "Valor"],
                empty_message="Governança de decisão indisponível · UNKNOWN",
            ),
            subtitle=(
                "RiskManager mantém autoridade final; IA Shadow não pode aumentar risco."
            ),
            status=_section_status(decision_governance),
        ),
        unsafe_allow_html=True,
    )
    governance_column.markdown(
        render_section_panel(
            "Model Governance · Promotion Boundary",
            render_html_table(
                _model_governance_rows(model_governance),
                columns=["Controle", "Valor"],
                empty_message="Governança de promoção indisponível · UNKNOWN",
            ),
            subtitle=(
                "Auto-promotion, live promotion e promoção pelo dashboard permanecem "
                "bloqueadas."
            ),
            status=_promotion_status(model_governance),
        ),
        unsafe_allow_html=True,
    )

    target_ui.markdown(
        render_section_panel(
            "AI Training Research Command Center",
            _research_command_center_body(research, branch_cards),
            subtitle=(
                "Oito trilhas de pesquisa consolidadas como evidência advisory-only; "
                "blockers de pesquisa não concedem autoridade operacional."
            ),
            status=_section_status(research),
        ),
        unsafe_allow_html=True,
    )

    target_ui.markdown(
        render_section_panel(
            "Diagnóstico & Safety Contract",
            (
                render_html_table(
                    _diagnostic_rows(snapshot, audit),
                    columns=["Campo", "Valor"],
                    empty_message="Diagnóstico do snapshot indisponível · UNKNOWN",
                )
                + render_html_table(
                    _safety_rows(snapshot, research, decision_governance, model_governance),
                    columns=["Controle", "Valor"],
                    empty_message="Safety contract indisponível · UNKNOWN",
                )
            ),
            subtitle=(
                "Contrato snapshot-first/read-only e invariantes paper/shadow da Aba 05."
            ),
            status=_snapshot_status(snapshot),
        ),
        unsafe_allow_html=True,
    )

    _render_canonical_details(snapshot, ui=target_ui)
    render_footer_audit_bar(
        SNAPSHOT_PATH,
        ["Auto-promotion disabled", "RiskManager final authority"],
        ui=target_ui,
    )


def _render_canonical_details(snapshot: Mapping[str, Any], *, ui: Any) -> None:
    """Preserve canonical generic and research renderers behind collapsed details."""

    with ui.expander(
        "Detalhamento canônico da Aba 05 · snapshot-first/read-only",
        expanded=False,
    ):
        ui.title(PAGE_TITLE)
        render_snapshot_page(
            title=PAGE_TITLE,
            snapshot_path=SNAPSHOT_PATH,
            snapshot=snapshot,
            section_order=REQUIRED_SECTIONS,
            ui=ui,
            render_chrome=False,
        )
        render_ai_training_research_command_center(snapshot, ui=ui)


def render_missing_snapshot(reason: str, *, ui: Any | None = None) -> None:
    (ui or get_streamlit()).info(f"UNKNOWN: {reason}")


def main(project_root: str | Path = ".") -> None:
    ui = get_streamlit()
    ui.set_page_config(page_title=PAGE_TITLE, layout="wide")
    render_page(
        load_page_snapshot(
            DashboardPageId.ai_governance,
            project_root=project_root,
        ),
        ui=ui,
    )


def _sections(snapshot: Mapping[str, Any]) -> Mapping[str, Any]:
    return _mapping(snapshot.get("sections"))


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [dict(row) for row in value if isinstance(row, Mapping)]


def _section_status(section: Mapping[str, Any]) -> str:
    return str(section.get("status") or "UNKNOWN")


def _snapshot_status(snapshot: Mapping[str, Any]) -> str:
    summary = _mapping(snapshot.get("status_summary"))
    return str(summary.get("status") or snapshot.get("status") or "UNKNOWN")


def _text_or_none(value: Any) -> str | None:
    return None if value in (None, "") else str(value)


def _render_primary_kpi_grid(cards: Sequence[str], *, ui: Any) -> None:
    """Render six KPI cards as two balanced three-column desktop rows."""

    materialized = list(cards)
    for offset in range(0, len(materialized), 3):
        columns = ui.columns(3)
        for column, card in zip(columns, materialized[offset : offset + 3]):
            column.markdown(card, unsafe_allow_html=True)


def _primary_kpi_cards(
    *,
    model_state: Mapping[str, Any],
    shadow_veto: Mapping[str, Any],
    drift_regime: Mapping[str, Any],
    research: Mapping[str, Any],
    model_governance: Mapping[str, Any],
    decision_governance: Mapping[str, Any],
) -> list[str]:
    return [
        render_compact_kpi(
            "Model / Registry",
            _model_state_value(model_state),
            helper="Registry / active model materializado",
            status=_section_status(model_state),
        ),
        render_compact_kpi(
            "IA Shadow Decisions",
            _shadow_decision_value(shadow_veto),
            helper="AI_ACCEPT / AI_REJECT",
            status=_section_status(shadow_veto),
        ),
        render_compact_kpi(
            "Drift / PSI",
            _drift_value(drift_regime),
            helper="Status de drift canônico",
            status=_drift_status(drift_regime),
        ),
        render_compact_kpi(
            "Research Gate",
            _display(research.get("research_gate_status")),
            helper="Advisory-only",
            status=_research_gate_visual_status(research),
        ),
        render_compact_kpi(
            "Model Promotion",
            status_to_label(_promotion_status(model_governance)),
            helper="Auto / live / dashboard",
            status=_promotion_status(model_governance),
        ),
        render_compact_kpi(
            "RiskManager Authority",
            _display(decision_governance.get("riskmanager_authority")),
            helper="Autoridade final de risco",
            status=_riskmanager_status(decision_governance),
        ),
    ]


def _model_state_value(section: Mapping[str, Any]) -> str:
    active_model = _mapping(section.get("active_model"))
    registry = _mapping(section.get("registry"))
    for source in (active_model, registry):
        for key in ("model_name", "name", "model_id", "candidate_id", "version"):
            value = source.get(key)
            if value not in (None, ""):
                return str(value)
    if active_model or registry:
        return "MATERIALIZED"
    return "UNKNOWN"


def _shadow_decision_value(section: Mapping[str, Any]) -> str:
    accepts = _non_negative_int(section.get("ai_accept_count"))
    rejects = _non_negative_int(section.get("ai_reject_count"))
    if accepts is None and rejects is None:
        return "UNKNOWN"
    return f"A:{accepts if accepts is not None else 'UNKNOWN'} / R:{rejects if rejects is not None else 'UNKNOWN'}"


def _drift_value(section: Mapping[str, Any]) -> str:
    psi = _finite_float(section.get("psi"))
    if psi is None:
        return _display(section.get("drift_status"))
    return f"{psi:.4f}"


def _drift_status(section: Mapping[str, Any]) -> str:
    return str(section.get("drift_status") or section.get("status") or "UNKNOWN")


def _research_gate_visual_status(section: Mapping[str, Any]) -> str:
    gate = str(section.get("research_gate_status") or "UNKNOWN").upper()
    if gate in {"BLOCKED", "HARD_BLOCKED"}:
        return gate
    return _section_status(section)


def _promotion_status(section: Mapping[str, Any]) -> str:
    return str(section.get("promotion_status") or section.get("status") or "UNKNOWN")


def _riskmanager_status(section: Mapping[str, Any]) -> str:
    if "riskmanager_authority" not in section:
        return "UNKNOWN"
    return "OK" if section.get("riskmanager_authority") is True else "BLOCKED"


def _ranking_status(
    section: Mapping[str, Any],
    rows: list[dict[str, Any]],
) -> str:
    if rows:
        return _section_status(section)
    if "ranking" not in section:
        return "UNKNOWN"
    if _section_status(section).upper() in {
        "UNKNOWN",
        "MISSING",
        "MISSING_OPTIONAL",
        "UNAVAILABLE",
    }:
        return "UNKNOWN"
    return _section_status(section)


def _ranking_table_rows(
    rows: list[dict[str, Any]],
    section: Mapping[str, Any],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in rows[:20]:
        output.append(
            {
                "Ativo": _first_display(
                    row,
                    ("symbol", "pair", "asset", "instrument", "ticker"),
                ),
                "Score / ETV": _first_number_display(
                    row,
                    ("expected_trade_value", "score", "rank_score"),
                ),
                "Retorno esperado": _first_number_display(
                    row,
                    (
                        "qlib_expected_return_net",
                        "expected_return_net",
                        "expected_return",
                    ),
                ),
                "Probabilidade": _first_number_display(
                    row,
                    ("probability", "confidence", "probability_quality"),
                ),
                "Status": str(row.get("status") or _section_status(section)),
            }
        )
    return output


def _shadow_veto_rows(section: Mapping[str, Any]) -> list[dict[str, Any]]:
    fields = (
        ("AI_ACCEPT count", "ai_accept_count", "count"),
        ("AI_REJECT count", "ai_reject_count", "count"),
        ("AI_ACCEPT rate", "ai_accept_rate_pct", "pct"),
        ("AI_REJECT rate", "ai_reject_rate_pct", "pct"),
    )
    return _metric_rows(section, fields)


def _classification_rows(section: Mapping[str, Any]) -> list[dict[str, Any]]:
    fields = (
        ("Precision", "precision", "ratio"),
        ("Recall", "recall", "ratio"),
        ("F1 score", "f1_score", "ratio"),
        ("Accuracy", "accuracy", "ratio"),
        ("Brier score", "brier_score", "ratio"),
    )
    return _metric_rows(section, fields)


def _drift_rows(section: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if "psi" in section:
        rows.append({"Métrica": "PSI", "Valor": _format_number(section.get("psi"), 6)})
    if "drift_status" in section:
        rows.append(
            {"Métrica": "Drift status", "Valor": _display(section.get("drift_status"))}
        )
    return rows


def _reward_rows(section: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if "research_only" in section:
        rows.append(
            {"Métrica": "Research only", "Valor": _display(section.get("research_only"))}
        )
    if "expected_trade_value" in section:
        rows.append(
            {
                "Métrica": "Expected Trade Value",
                "Valor": _format_number(section.get("expected_trade_value"), 6),
            }
        )
    if not rows and section:
        rows.append({"Métrica": "Status", "Valor": _section_status(section)})
    return rows


def _decision_rows(section: Mapping[str, Any]) -> list[dict[str, Any]]:
    fields = (
        ("Final action", "final_action"),
        ("RiskManager authority", "riskmanager_authority"),
        ("AI can increase risk", "ai_can_increase_risk"),
    )
    return [
        {"Controle": label, "Valor": _display(section.get(key))}
        for label, key in fields
        if key in section
    ]


def _model_governance_rows(section: Mapping[str, Any]) -> list[dict[str, Any]]:
    fields = (
        ("Promotion status", "promotion_status"),
        ("Auto promotion allowed", "auto_promotion_allowed"),
        ("Live model promotion allowed", "live_model_promotion_allowed"),
        (
            "Model promotion allowed from dashboard",
            "model_promotion_allowed_from_dashboard",
        ),
        ("Accuracy is primary metric", "accuracy_is_primary_metric"),
    )
    return [
        {
            "Controle": label,
            "Valor": _governance_display(key, section.get(key)),
        }
        for label, key in fields
        if key in section
    ]


def _governance_display(key: str, value: Any) -> str:
    """Preserve governance values literally; never reinterpret False as OK."""

    return _display(value)


def _research_command_center_body(
    section: Mapping[str, Any],
    branch_cards: list[dict[str, Any]],
) -> str:
    summary = _mapping(section.get("summary"))
    summary_rows = [
        {"Campo": "Research Gate", "Valor": _display(section.get("research_gate_status"))},
        {"Campo": "Decisão", "Valor": _display(section.get("decision"))},
        {"Campo": "Autoridade", "Valor": _display(section.get("authority"))},
        {
            "Campo": "Fontes",
            "Valor": _source_coverage_value(summary),
        },
    ]

    branch_table = render_html_table(
        _research_rows(branch_cards),
        columns=list(_RESEARCH_COLUMNS),
        status_columns=["Status"],
        empty_message="Research branch cards não materializados · MISSING_OPTIONAL",
    )

    blocker_table = render_html_table(
        _list_rows(section.get("blockers"), "Research blocker"),
        columns=["Research blocker"],
        empty_message="Nenhum blocker de pesquisa materializado",
    )
    missing_table = render_html_table(
        _list_rows(section.get("missing_optional_sources"), "Fonte opcional ausente"),
        columns=["Fonte opcional ausente"],
        empty_message="Nenhuma fonte opcional ausente reportada",
    )

    return (
        render_html_table(summary_rows, columns=["Campo", "Valor"])
        + branch_table
        + blocker_table
        + missing_table
    )


def _research_rows(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for card in cards[:8]:
        headline = _mapping(card.get("headline_metric"))
        rows.append(
            {
                "Branch": _display(card.get("branch_id")),
                "Evidência": _display(card.get("title")),
                "Status": _display(card.get("status")),
                "Decisão": _display(card.get("decision")),
                "Métrica": _display(headline.get("label")),
                "Valor": _display(headline.get("value")),
                "Advisory only": _display(card.get("advisory_only")),
            }
        )
    return rows


def _source_coverage_value(summary: Mapping[str, Any]) -> str:
    available = _non_negative_int(summary.get("available_source_count"))
    total = _non_negative_int(summary.get("source_count"))
    if available is None or total is None:
        return "UNKNOWN"
    return f"{available}/{total}"


def _list_rows(value: Any, column: str) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [{column: str(item)} for item in value]


def _diagnostic_rows(
    snapshot: Mapping[str, Any],
    audit: Mapping[str, Any],
) -> list[dict[str, Any]]:
    rows = [
        {"Campo": "schema_version", "Valor": _display(snapshot.get("schema_version"))},
        {"Campo": "runtime_mode", "Valor": _display(snapshot.get("runtime_mode"))},
        {"Campo": "snapshot_status", "Valor": _snapshot_status(snapshot)},
        {"Campo": "last_updated_utc", "Valor": _display(snapshot.get("last_updated_utc"))},
    ]
    if "reason" in audit:
        rows.append({"Campo": "audit_reason", "Valor": _display(audit.get("reason"))})
    if "status" in audit:
        rows.append({"Campo": "audit_status", "Valor": _display(audit.get("status"))})
    return rows


def _safety_rows(
    snapshot: Mapping[str, Any],
    research: Mapping[str, Any],
    decision_governance: Mapping[str, Any],
    model_governance: Mapping[str, Any],
) -> list[dict[str, Any]]:
    controls: list[tuple[str, Any]] = [
        ("dashboard_readonly", snapshot.get("dashboard_readonly")),
        ("paper_only", snapshot.get("paper_only")),
        ("shadow_only", snapshot.get("shadow_only")),
        ("live_locked", snapshot.get("live_locked")),
        ("order_submission_enabled", snapshot.get("order_submission_enabled")),
        (
            "real_order_submission_enabled",
            snapshot.get("real_order_submission_enabled"),
        ),
        (
            "riskmanager_authority",
            decision_governance.get("riskmanager_authority"),
        ),
        (
            "ai_can_increase_risk",
            decision_governance.get("ai_can_increase_risk"),
        ),
        (
            "auto_promotion_allowed",
            model_governance.get("auto_promotion_allowed"),
        ),
        (
            "live_model_promotion_allowed",
            model_governance.get("live_model_promotion_allowed"),
        ),
    ]

    research_safety = _mapping(research.get("safety_flags"))
    for key in (
        "operational_authority",
        "updates_freqtrade",
        "updates_qlib_runtime",
        "updates_risk_manager",
        "updates_ai_shadow_runtime",
        "sends_orders",
        "changes_risk",
        "changes_model",
        "registers_model",
        "production_enabled",
        "exchange_private_access",
    ):
        controls.append((f"research.{key}", research_safety.get(key)))

    return [
        {"Controle": key, "Valor": _display(value)}
        for key, value in controls
        if value is not None
    ]


def _metric_rows(
    section: Mapping[str, Any],
    fields: Sequence[tuple[str, str, str]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for label, key, mode in fields:
        if key not in section:
            continue
        value = section.get(key)
        if mode == "pct":
            display = _format_percent(value)
        elif mode == "ratio":
            display = _format_number(value, 4)
        elif mode == "count":
            count = _non_negative_int(value)
            display = "UNKNOWN" if count is None else str(count)
        else:
            display = _display(value)
        rows.append({"Métrica": label, "Valor": display})
    return rows


def _first_display(row: Mapping[str, Any], keys: Sequence[str]) -> str:
    for key in keys:
        if key in row and row.get(key) not in (None, ""):
            return _display(row.get(key))
    return "UNKNOWN"


def _first_number_display(row: Mapping[str, Any], keys: Sequence[str]) -> str:
    for key in keys:
        if key in row:
            return _format_number(row.get(key), 6)
    return "UNKNOWN"


def _display(value: Any) -> str:
    if value is None or value == "":
        return "UNKNOWN"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _format_number(value: Any, digits: int) -> str:
    number = _finite_float(value)
    return "UNKNOWN" if number is None else f"{number:.{digits}f}"


def _format_percent(value: Any) -> str:
    number = _finite_float(value)
    return "UNKNOWN" if number is None else f"{number:.2f}%"


def _finite_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


if __name__ == "__main__":
    main()
