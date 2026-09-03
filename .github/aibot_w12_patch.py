from __future__ import annotations

from pathlib import Path

REPORT = "data/reports/aibot_parity/aibot_parity_e2e_snapshot_v1.json"


def patch(path: str, old: str, new: str, *, count: int = 1) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    actual = text.count(old)
    if actual != count:
        raise SystemExit(
            f"{path}: expected {count} occurrences, found {actual}: {old[:100]!r}"
        )
    target.write_text(text.replace(old, new, count), encoding="utf-8")


def main() -> None:
    patch_source_catalog()
    patch_builders()
    patch_page_04()
    patch_page_05()
    patch_page_07()


def patch_source_catalog() -> None:
    path = "smartcrypto/ops/dashboard_snapshots/source_catalog.py"
    for page in ("opportunity_scanner", "ai_governance", "quantitative_reports"):
        old = f"        _generated(DashboardPageId.{page}),"
        new = (
            f'        _source(DashboardPageId.{page}, "{REPORT}", '
            "SourceKind.FUTURE_SOURCE, "
            '"AIBOT-Parity W13 read-only E2E projection; materialized only by explicit '
            'research/shadow pipeline run."),\n'
            + old
        )
        patch(path, old, new)


def patch_builders() -> None:
    import_anchor = (
        "from smartcrypto.ops.dashboard_snapshots.build_context import DashboardBuildContext\n"
    )
    import_block = (
        "from smartcrypto.ops.dashboard_snapshots.aibot_parity_integration import (\n"
        "    build_aibot_parity_dashboard_section,\n"
        ")\n"
        + import_anchor
    )

    path = "smartcrypto/ops/dashboard_snapshots/opportunity_scanner_snapshot_builder.py"
    patch(path, import_anchor, import_block)
    patch(
        path,
        '    "governance",\n    "audit",',
        '    "governance",\n    "aibot_parity",\n    "audit",',
    )
    patch(
        path,
        '        "governance": section(DashboardSectionStatus.OK, **governance),\n'
        '        "audit": section(DashboardSectionStatus.OK, dashboard_reads_only=True),',
        '        "governance": section(DashboardSectionStatus.OK, **governance),\n'
        '        "aibot_parity": build_aibot_parity_dashboard_section(\n'
        '            sources, "opportunity_scanner"\n'
        '        ),\n'
        '        "audit": section(DashboardSectionStatus.OK, dashboard_reads_only=True),',
    )

    path = "smartcrypto/ops/dashboard_snapshots/ai_governance_snapshot_builder.py"
    patch(path, import_anchor, import_block)
    patch(
        path,
        '    "ai_training_research_command_center",\n    "audit",',
        '    "ai_training_research_command_center",\n    "aibot_parity",\n    "audit",',
    )
    patch(
        path,
        '        "model_governance": section(DashboardSectionStatus.OK, auto_promotion_allowed=False, live_model_promotion_allowed=False, model_promotion_allowed_from_dashboard=False, accuracy_is_primary_metric=False, promotion_status=HardBlockStatus.HARD_BLOCKED.value),\n'
        '        "audit": section(DashboardSectionStatus.OK, dashboard_reads_only=True, trains_model=False, promotes_model=False),',
        '        "model_governance": section(DashboardSectionStatus.OK, auto_promotion_allowed=False, live_model_promotion_allowed=False, model_promotion_allowed_from_dashboard=False, accuracy_is_primary_metric=False, promotion_status=HardBlockStatus.HARD_BLOCKED.value),\n'
        '        "aibot_parity": build_aibot_parity_dashboard_section(\n'
        '            sources, "ai_governance"\n'
        '        ),\n'
        '        "audit": section(DashboardSectionStatus.OK, dashboard_reads_only=True, trains_model=False, promotes_model=False),',
    )

    path = "smartcrypto/ops/dashboard_snapshots/quantitative_reports_snapshot_builder.py"
    patch(path, import_anchor, import_block)
    patch(
        path,
        '    "institutional_score",\n    "audit",',
        '    "institutional_score",\n    "aibot_parity",\n    "audit",',
    )
    patch(
        path,
        '        "institutional_score": section(DashboardSectionStatus.OK, score=institutional, weights={"robustness": 0.25, "risk": 0.25, "tca": 0.20, "recovery": 0.15, "consistency": 0.10, "winrate": 0.05}),\n'
        '        "audit": section(DashboardSectionStatus.OK, dashboard_reads_only=True),',
        '        "institutional_score": section(DashboardSectionStatus.OK, score=institutional, weights={"robustness": 0.25, "risk": 0.25, "tca": 0.20, "recovery": 0.15, "consistency": 0.10, "winrate": 0.05}),\n'
        '        "aibot_parity": build_aibot_parity_dashboard_section(\n'
        '            sources, "quantitative_reports"\n'
        '        ),\n'
        '        "audit": section(DashboardSectionStatus.OK, dashboard_reads_only=True),',
    )


def patch_page_04() -> None:
    path = "smartcrypto/dashboard/pages/04_opportunity_scanner.py"
    patch(
        path,
        '    "governance",\n    "audit",',
        '    "governance",\n    "aibot_parity",\n    "audit",',
    )
    patch(
        path,
        '    governance = _mapping(sections.get("governance"))\n'
        '    audit = _mapping(sections.get("audit"))',
        '    governance = _mapping(sections.get("governance"))\n'
        '    aibot_parity = _mapping(sections.get("aibot_parity"))\n'
        '    audit = _mapping(sections.get("audit"))',
    )
    marker = (
        '    target_ui.markdown(\n'
        '        render_section_panel(\n'
        '            "Diagnóstico do Snapshot",'
    )
    panel = (
        '    target_ui.markdown(\n'
        '        render_section_panel(\n'
        '            "AIBOT-Parity E2E · Shadow",\n'
        '            render_html_table(\n'
        '                _aibot_parity_rows(aibot_parity),\n'
        '                columns=["Campo", "Valor"],\n'
        '                empty_message="Pipeline E2E ainda não materializado · UNKNOWN",\n'
        '            ),\n'
        '            subtitle=(\n'
        '                "Projeção somente leitura do W13. WOULD_SIGNAL é contrafactual; "\n'
        '                "writes_active_signals permanece false."\n'
        '            ),\n'
        '            status=_section_status(aibot_parity),\n'
        '        ),\n'
        '        unsafe_allow_html=True,\n'
        '    )\n\n'
    )
    patch(path, marker, panel + marker)
    helper_marker = (
        "def _diagnostic_rows(snapshot: Mapping[str, Any], audit: Mapping[str, Any]) "
        "-> list[dict[str, Any]]:"
    )
    helper = (
        'def _aibot_parity_rows(section: Mapping[str, Any]) -> list[dict[str, Any]]:\n'
        '    if not section:\n'
        '        return []\n'
        '    return [\n'
        '        {"Campo": "cycle_id", "Valor": _display_text(section.get("cycle_id"))},\n'
        '        {"Campo": "pipeline_status", "Valor": _display_text(section.get("pipeline_status"))},\n'
        '        {"Campo": "final_action", "Valor": _display_text(section.get("final_action"))},\n'
        '        {"Campo": "selected_candidate_count", "Valor": _display_text(section.get("selected_candidate_count"))},\n'
        '        {"Campo": "would_signal", "Valor": _format_bool(section.get("would_signal"))},\n'
        '        {"Campo": "writes_active_signals", "Valor": _format_bool(section.get("writes_active_signals"))},\n'
        '        {"Campo": "signal_published", "Valor": _format_bool(section.get("signal_published"))},\n'
        '        {"Campo": "RiskManager final", "Valor": _format_bool(section.get("riskmanager_final_authority"))},\n'
        '    ]\n\n\n'
    )
    patch(path, helper_marker, helper + helper_marker)


def patch_page_05() -> None:
    path = "smartcrypto/dashboard/pages/05_ai_governance.py"
    patch(
        path,
        '    "ai_training_research_command_center",\n    "audit",',
        '    "ai_training_research_command_center",\n    "aibot_parity",\n    "audit",',
    )
    patch(
        path,
        '    research = _mapping(sections.get("ai_training_research_command_center"))\n'
        '    audit = _mapping(sections.get("audit"))',
        '    research = _mapping(sections.get("ai_training_research_command_center"))\n'
        '    aibot_parity = _mapping(sections.get("aibot_parity"))\n'
        '    audit = _mapping(sections.get("audit"))',
    )
    marker = (
        '    target_ui.markdown(\n'
        '        render_section_panel(\n'
        '            "AI Training Research Command Center",'
    )
    panel = (
        '    target_ui.markdown(\n'
        '        render_section_panel(\n'
        '            "AIBOT-Parity E2E · Governance",\n'
        '            render_html_table(\n'
        '                _aibot_parity_rows(aibot_parity),\n'
        '                columns=["Controle", "Valor"],\n'
        '                empty_message="Pipeline E2E ainda não materializado · UNKNOWN",\n'
        '            ),\n'
        '            subtitle=(\n'
        '                "Qlib pode permanecer BLOCKED_EXTERNAL; RiskManager continua final e "\n'
        '                "nenhuma projeção concede promoção ou autoridade operacional."\n'
        '            ),\n'
        '            status=_section_status(aibot_parity),\n'
        '        ),\n'
        '        unsafe_allow_html=True,\n'
        '    )\n\n'
    )
    patch(path, marker, panel + marker)
    helper_marker = "def _model_state_value(section: Mapping[str, Any]) -> str:"
    helper = (
        'def _aibot_parity_rows(section: Mapping[str, Any]) -> list[dict[str, Any]]:\n'
        '    if not section:\n'
        '        return []\n'
        '    return [\n'
        '        {"Controle": "cycle_id", "Valor": _display(section.get("cycle_id"))},\n'
        '        {"Controle": "pipeline_status", "Valor": _display(section.get("pipeline_status"))},\n'
        '        {"Controle": "ensemble_action", "Valor": _display(section.get("ensemble_action"))},\n'
        '        {"Controle": "qlib_status", "Valor": _display(section.get("qlib_status"))},\n'
        '        {"Controle": "riskmanager_shadow_decision", "Valor": _display(section.get("riskmanager_shadow_decision"))},\n'
        '        {"Controle": "would_signal", "Valor": _display(section.get("would_signal"))},\n'
        '        {"Controle": "writes_active_signals", "Valor": _display(section.get("writes_active_signals"))},\n'
        '        {"Controle": "operational_authority", "Valor": _display(section.get("operational_authority"))},\n'
        '        {"Controle": "model_promotion_allowed", "Valor": _display(section.get("model_promotion_allowed"))},\n'
        '    ]\n\n\n'
    )
    patch(path, helper_marker, helper + helper_marker)


def patch_page_07() -> None:
    path = "smartcrypto/dashboard/pages/07_quantitative_reports.py"
    patch(
        path,
        "from __future__ import annotations\n\nfrom pathlib import Path\nfrom typing import Any\n",
        "from __future__ import annotations\n\nfrom collections.abc import Mapping\n"
        "from pathlib import Path\nfrom typing import Any\n",
    )
    patch(
        path,
        "    render_global_topbar,\n    render_page_title,\n    render_sidebar,",
        "    render_global_topbar,\n    render_html_table,\n    render_page_title,\n"
        "    render_section_panel,\n    render_sidebar,",
    )
    patch(
        path,
        '    "regime_comparison", "asset_comparison", "soak_gap_accounting", "exports", '
        '"institutional_score", "audit",',
        '    "regime_comparison", "asset_comparison", "soak_gap_accounting", "exports", '
        '"institutional_score", "aibot_parity", "audit",',
    )
    marker = (
        '    target_ui.markdown(\n'
        '        render_chart_placeholder("Equity Curve / Drawdown", '
        '"Série temporal indisponível no snapshot"),'
    )
    panel = (
        '    sections = snapshot.get("sections", {})\n'
        '    aibot_parity = sections.get("aibot_parity", {}) if isinstance(sections, Mapping) else {}\n'
        '    aibot_parity = aibot_parity if isinstance(aibot_parity, Mapping) else {}\n'
        '    target_ui.markdown(\n'
        '        render_section_panel(\n'
        '            "AIBOT-Parity E2E · Coverage",\n'
        '            render_html_table(\n'
        '                _aibot_parity_rows(aibot_parity),\n'
        '                columns=["Campo", "Valor"],\n'
        '                empty_message="Pipeline E2E ainda não materializado · UNKNOWN",\n'
        '            ),\n'
        '            subtitle=(\n'
        '                "Cobertura PIT e subsistemas W1-W9; apresentação somente leitura, "\n'
        '                "writes_active_signals=false."\n'
        '            ),\n'
        '            status=str(aibot_parity.get("status") or "UNKNOWN"),\n'
        '        ),\n'
        '        unsafe_allow_html=True,\n'
        '    )\n'
    )
    patch(path, marker, panel + marker)
    helper_marker = "def render_missing_snapshot(reason: str, *, ui: Any | None = None) -> None:"
    helper = (
        'def _aibot_parity_rows(section: Mapping[str, Any]) -> list[dict[str, Any]]:\n'
        '    if not section:\n'
        '        return []\n'
        '    keys = (\n'
        '        "cycle_id",\n'
        '        "pipeline_status",\n'
        '        "required_source_count",\n'
        '        "required_sources_present_count",\n'
        '        "point_in_time_valid_required_count",\n'
        '        "execution_intelligence_status",\n'
        '        "risk_budget_status",\n'
        '        "treasury_status",\n'
        '        "qlib_status",\n'
        '        "final_action",\n'
        '        "writes_active_signals",\n'
        '        "operational_authority",\n'
        '    )\n'
        '    return [{"Campo": key, "Valor": str(section.get(key, "UNKNOWN"))} for key in keys]\n\n\n'
    )
    patch(path, helper_marker, helper + helper_marker)


if __name__ == "__main__":
    main()
