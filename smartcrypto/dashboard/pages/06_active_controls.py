from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from smartcrypto.dashboard.components.control_stubs import (
    render_command_policy_table,
    render_command_result_stub,
    render_n4_hard_block_panel,
    render_stub_only_banner,
)
from smartcrypto.dashboard.components.read_only import (
    get_streamlit,
    render_disabled_control_stub,
    render_snapshot_page,
)
from smartcrypto.dashboard.components.readiness_gates import render_readiness_gates_snapshot_view
from smartcrypto.dashboard.components.runtime_blockers_closeout_evidence import render_runtime_blockers_closeout_evidence
from smartcrypto.dashboard.components.runtime_blockers_operator_pack import render_runtime_blockers_operator_pack
from smartcrypto.dashboard.components.runtime_blockers_remediation import render_runtime_blockers_remediation
from smartcrypto.dashboard.components.runtime_evidence_freshness_remediation_producers import render_runtime_evidence_freshness_remediation_producers
from smartcrypto.dashboard.components.runtime_evidence_panel import render_runtime_evidence_panel
from smartcrypto.dashboard.components.runtime_freshness_governance_closeout_index import render_runtime_freshness_governance_closeout_index
from smartcrypto.dashboard.components.runtime_freshness_post_refresh_evidence_gate import render_runtime_freshness_post_refresh_evidence_gate
from smartcrypto.dashboard.components.runtime_freshness_producer_contracts import render_runtime_freshness_producer_contracts
from smartcrypto.dashboard.components.runtime_freshness_producer_entrypoint_static_safety import render_runtime_freshness_producer_entrypoint_static_safety
from smartcrypto.dashboard.components.runtime_source_health import render_runtime_source_health
from smartcrypto.dashboard.controls.command_classifier import list_dashboard_command_policies
from smartcrypto.dashboard.controls.command_stub_adapter import evaluate_dashboard_command_intent
from smartcrypto.dashboard.controls.contracts import DashboardCommandIntent
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
    worst_status,
)
from smartcrypto.ops.dashboard_snapshots.contracts import DashboardPageId

PAGE_TITLE = "06. Controles Ativos"
PAGE_NUMBER = "06"
PAGE_NAME = "Controles Ativos"
PAGE_SUBTITLE = (
    "Governança de comandos snapshot-first: N1 local, N2/N3 somente dry-run e "
    "N4 permanentemente HARD_BLOCKED."
)
ACTIVE_PAGE = "06_active_controls"
SNAPSHOT_PATH = "data/reports/dashboard_active_controls_snapshot.json"
EXPECTED_SCHEMA_VERSION = "dashboard_active_controls_snapshot_v1"
REQUIRED_SECTIONS = (
    "active_layer_status",
    "level1_commands",
    "level2_commands",
    "level3_commands",
    "level4_hard_blocks",
    "kill_switch",
    "grid_parameter_change",
    "security_state",
    "readiness_gap_accounting",
    "paper_runtime_health",
    "runtime_evidence_integration",
    "runtime_blockers_remediation",
    "runtime_blockers_operator_pack",
    "runtime_blockers_closeout_evidence",
    "runtime_evidence_freshness_remediation_producers",
    "runtime_freshness_producer_contracts",
    "runtime_freshness_producer_entrypoint_static_safety",
    "runtime_freshness_post_refresh_evidence_gate",
    "runtime_freshness_governance_closeout_index",
    "command_events",
    "runtime_source_health",
    "audit",
)

LEVEL4_ALWAYS_BLOCKED = (
    "LIVE_ORDER",
    "MARKET_SELL_ALL_REAL",
    "SNIPER_REAL",
    "CANCEL_ALL_LIVE_ORDERS",
    "LIQUIDATE_REAL_INVENTORY",
    "CHANGE_LIVE_RISK",
    "ENABLE_LIVE_TRADING",
    "ENABLE_PRIVATE_READ_REAL",
    "PROMOTE_MODEL_TO_PRODUCTION",
    "AUTO_INCREASE_CAPITAL",
    "RELEASE_REAL_SAFETY_ORDER",
)

METRICS = (
    ("Execution Enabled", "active_layer_status", "command_execution_enabled"),
    ("Kill Switch Active", "kill_switch", "global_kill_switch_active"),
    ("RiskManager Authority", "security_state", "riskmanager_authority"),
    ("Live Authority", "security_state", "live_authority"),
    ("Critical Gaps", "readiness_gap_accounting", "critical_gap_count"),
    ("Continuous Soak", "readiness_gap_accounting", "continuous_valid_soak_days"),
)

_RUNTIME_BLOCKER_SECTIONS = (
    ("Remediation", "runtime_blockers_remediation"),
    ("Operator Pack", "runtime_blockers_operator_pack"),
    ("Closeout Evidence", "runtime_blockers_closeout_evidence"),
    ("Freshness Producers", "runtime_evidence_freshness_remediation_producers"),
    ("Producer Contracts", "runtime_freshness_producer_contracts"),
    ("Entrypoint Static Safety", "runtime_freshness_producer_entrypoint_static_safety"),
    ("Post-refresh Gate", "runtime_freshness_post_refresh_evidence_gate"),
    ("Governance Closeout", "runtime_freshness_governance_closeout_index"),
    ("Runtime Source Health", "runtime_source_health"),
)


def render_page(snapshot: dict[str, Any], *, ui: Any | None = None) -> None:
    """Render Aba 06 as a read-only institutional control/governance wallboard."""

    target_ui = ui or get_streamlit()
    inject_smart_futuros_command_center_css(ui=target_ui)
    render_global_topbar(last_updated=_text_or_none(snapshot.get("last_updated_utc")), ui=target_ui)
    render_sidebar(ACTIVE_PAGE, {"environment": "paper", "snapshot": SNAPSHOT_PATH}, ui=target_ui)
    render_page_title(PAGE_NUMBER, PAGE_NAME, PAGE_SUBTITLE, ui=target_ui)
    render_readonly_banner(ui=target_ui)

    sections = _sections(snapshot)
    active_layer = _mapping(sections.get("active_layer_status"))
    kill_switch = _mapping(sections.get("kill_switch"))
    security_state = _mapping(sections.get("security_state"))
    readiness = _mapping(sections.get("readiness_gap_accounting"))
    paper_runtime = _mapping(sections.get("paper_runtime_health"))
    runtime_evidence = _mapping(sections.get("runtime_evidence_integration"))
    audit = _mapping(sections.get("audit"))
    policies = [policy.to_dict() for policy in list_dashboard_command_policies()]

    _render_primary_kpi_grid(
        _primary_kpi_cards(active_layer, kill_switch, security_state, readiness, policies),
        ui=target_ui,
    )

    target_ui.markdown(
        render_section_panel(
            "Control Authority Matrix",
            render_html_table(
                _policy_level_rows(policies),
                columns=["Nível", "Políticas", "Modo", "Risco", "Autoridade", "Status"],
                status_columns=["Status"],
                empty_message="Catálogo de políticas indisponível · UNKNOWN",
            ),
            subtitle=(
                "N1 é local/read-only; N2/N3 são simulações sem execução; "
                "N4 não possui autoridade e permanece HARD_BLOCKED."
            ),
            status=_policy_matrix_status(policies),
        ),
        unsafe_allow_html=True,
    )

    left, right = target_ui.columns(2)
    left.markdown(
        render_section_panel(
            "Safety Boundary",
            render_html_table(
                _safety_boundary_rows(active_layer, kill_switch, security_state),
                columns=["Controle", "Valor"],
                empty_message="Safety boundary indisponível · UNKNOWN",
            ),
            subtitle="Kill switch, reconciliation lock, RiskManager e autoridade live em modo somente leitura.",
            status=_safety_boundary_status(active_layer, kill_switch, security_state),
        ),
        unsafe_allow_html=True,
    )
    right.markdown(
        render_section_panel(
            "Readiness & Soak",
            render_html_table(
                _readiness_rows(readiness),
                columns=["Gate", "Valor"],
                empty_message="Readiness não materializada · UNKNOWN",
            ),
            subtitle="7d é diagnóstico; 30d é readiness. Canary/live nunca são auto-liberados.",
            status=_readiness_status(readiness),
        ),
        unsafe_allow_html=True,
    )

    target_ui.markdown(
        render_section_panel(
            "Paper Runtime Health",
            render_html_table(
                _paper_runtime_rows(paper_runtime),
                columns=["Sinal", "Valor"],
                empty_message="Paper runtime health não materializado · UNKNOWN",
            ),
            subtitle="Saúde/freshness observacional; nenhum container é mutado pela interface.",
            status=_section_status(paper_runtime),
        ),
        unsafe_allow_html=True,
    )
    target_ui.markdown(
        render_section_panel(
            "Governed Dry-run Examples",
            render_html_table(
                _dry_run_rows(),
                columns=["Comando", "Nível", "Resultado", "Aceito", "Executado", "Efeito simulado"],
                empty_message="Exemplos dry-run indisponíveis · UNKNOWN",
            ),
            subtitle=(
                "Adapter de stub em largura total: executed=false, sem efeito externo "
                "e com todas as colunas de auditoria visíveis."
            ),
            status="READONLY",
        ),
        unsafe_allow_html=True,
    )

    left, right = target_ui.columns(2)
    left.markdown(
        render_section_panel(
            "Runtime Evidence",
            render_html_table(
                _runtime_evidence_rows(runtime_evidence),
                columns=["Evidência", "Valor"],
                empty_message="Runtime evidence não materializada · UNKNOWN",
            ),
            subtitle="Readiness, freshness e evidence pack somente leitura.",
            status=_section_status(runtime_evidence),
        ),
        unsafe_allow_html=True,
    )
    right.markdown(
        render_section_panel(
            "Runtime Blockers & Freshness",
            render_html_table(
                _runtime_blocker_rows(sections),
                columns=["Domínio", "Status", "Reason"],
                status_columns=["Status"],
                empty_message="Packs de blockers/freshness não materializados · UNKNOWN",
            ),
            subtitle="Resumo executivo; detalhes canônicos permanecem colapsados abaixo.",
            status=_runtime_blocker_status(sections),
        ),
        unsafe_allow_html=True,
    )

    target_ui.markdown(
        render_section_panel(
            "Diagnóstico & Safety Contract",
            render_html_table(
                _diagnostic_rows(snapshot, audit),
                columns=["Campo", "Valor"],
                empty_message="Diagnóstico do snapshot indisponível · UNKNOWN",
            )
            + render_html_table(
                _safety_contract_rows(snapshot, active_layer, security_state, readiness),
                columns=["Invariante", "Valor"],
                empty_message="Safety contract indisponível · UNKNOWN",
            ),
            subtitle="Contrato snapshot-first/read-only; sem config write, ordens ou autoridade live.",
            status=_snapshot_status(snapshot),
        ),
        unsafe_allow_html=True,
    )

    _render_canonical_details(snapshot, ui=target_ui)
    render_footer_audit_bar(SNAPSHOT_PATH, ["N4 HARD-BLOCKED", "STUB ONLY - NO EXECUTION"], ui=target_ui)


def _render_canonical_details(snapshot: Mapping[str, Any], *, ui: Any) -> None:
    """Preserve all legacy read-only renderers behind collapsed audit details."""

    policies = list_dashboard_command_policies()
    with ui.expander("Detalhamento canônico da Aba 06 · snapshot-first/read-only", expanded=False):
        ui.title(PAGE_TITLE)
        render_snapshot_page(
            title=PAGE_TITLE,
            snapshot_path=SNAPSHOT_PATH,
            snapshot=snapshot,
            section_order=REQUIRED_SECTIONS,
            metric_specs=METRICS,
            ui=ui,
            render_chrome=False,
        )
        ui.subheader("Controles governados")
        render_stub_only_banner(ui=ui)
        render_command_policy_table(policies, ui=ui)
        render_n4_hard_block_panel(policies, ui=ui)
        ui.subheader("Exemplos estáticos de avaliação dry-run")
        for intent in _example_intents():
            render_command_result_stub(evaluate_dashboard_command_intent(intent), ui=ui)
        render_disabled_control_stub("N2", "DRY-RUN/STUB FUTURO", ui=ui)
        render_disabled_control_stub("N3", "DRY-RUN/STUB FUTURO", ui=ui)
        for command in LEVEL4_ALWAYS_BLOCKED:
            render_disabled_control_stub(command, "HARD_BLOCKED", ui=ui)
        render_readiness_gates_snapshot_view(snapshot, ui=ui)
        render_runtime_evidence_panel(snapshot, ui=ui)
        render_runtime_blockers_remediation(snapshot, ui=ui)
        render_runtime_blockers_operator_pack(snapshot, ui=ui)
        render_runtime_blockers_closeout_evidence(snapshot, ui=ui)
        render_runtime_evidence_freshness_remediation_producers(snapshot, ui=ui)
        render_runtime_freshness_producer_contracts(snapshot, ui=ui)
        render_runtime_freshness_producer_entrypoint_static_safety(snapshot, ui=ui)
        render_runtime_freshness_post_refresh_evidence_gate(snapshot, ui=ui)
        render_runtime_freshness_governance_closeout_index(snapshot, ui=ui)
        render_runtime_source_health(snapshot, ui=ui)


def render_missing_snapshot(reason: str, *, ui: Any | None = None) -> None:
    (ui or get_streamlit()).info(f"UNKNOWN: {reason}")


def main(project_root: str | Path = ".") -> None:
    ui = get_streamlit()
    ui.set_page_config(page_title=PAGE_TITLE, layout="wide")
    render_page(load_page_snapshot(DashboardPageId.active_controls, project_root=project_root), ui=ui)


def _example_intents() -> tuple[DashboardCommandIntent, ...]:
    return (
        DashboardCommandIntent(command_id="example-n1", command_name="REFRESH_VIEW"),
        DashboardCommandIntent(
            command_id="example-n2",
            command_name="REQUEST_ALERT_TEST_DRY_RUN",
            payload={"severity": "WARNING", "channel": "TELEGRAM"},
        ),
        DashboardCommandIntent(
            command_id="example-n3",
            command_name="REQUEST_DATASET_AUDIT_DRY_RUN",
            payload={"dataset_scope": "summary", "reason": "dashboard_example"},
        ),
    )


def _sections(snapshot: Mapping[str, Any]) -> Mapping[str, Any]:
    return _mapping(snapshot.get("sections"))


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _section_status(section: Mapping[str, Any]) -> str:
    return str(section.get("status") or "UNKNOWN")


def _snapshot_status(snapshot: Mapping[str, Any]) -> str:
    summary = _mapping(snapshot.get("status_summary"))
    return str(summary.get("status") or snapshot.get("status") or "UNKNOWN")


def _text_or_none(value: Any) -> str | None:
    return None if value in (None, "") else str(value)


def _render_primary_kpi_grid(cards: Sequence[str], *, ui: Any) -> None:
    for offset in range(0, len(cards), 3):
        columns = ui.columns(3)
        for column, card in zip(columns, cards[offset : offset + 3]):
            column.markdown(card, unsafe_allow_html=True)


def _primary_kpi_cards(
    active_layer: Mapping[str, Any],
    kill_switch: Mapping[str, Any],
    security_state: Mapping[str, Any],
    readiness: Mapping[str, Any],
    policies: Sequence[Mapping[str, Any]],
) -> list[str]:
    execution = _bool_or_none(active_layer.get("command_execution_enabled"))
    paper_entry = _bool_or_none(active_layer.get("paper_entry_allowed"))
    kill_active = _bool_or_none(kill_switch.get("global_kill_switch_active"))
    risk_authority = _bool_or_none(security_state.get("riskmanager_authority"))
    readiness_30d = readiness.get("thirty_day_readiness_status")
    n4_count = sum(1 for row in policies if bool(row.get("hard_blocked")))
    return [
        render_compact_kpi(
            "Command Execution",
            "DISABLED" if execution is False else _display(execution),
            helper="Dashboard nunca executa comando real",
            status="DISABLED" if execution is False else "BLOCKED" if execution else "UNKNOWN",
        ),
        render_compact_kpi(
            "Paper Entry Allowed",
            _display(paper_entry),
            helper="Somente estado materializado",
            status=_bool_status(paper_entry, true_status="OK", false_status="BLOCKED"),
        ),
        render_compact_kpi(
            "Kill Switch",
            "ACTIVE" if kill_active is True else "INACTIVE" if kill_active is False else "UNKNOWN",
            helper=f"snapshot={_display(kill_active)}",
            status="BLOCKED" if kill_active is True else "OK" if kill_active is False else "UNKNOWN",
        ),
        render_compact_kpi(
            "RiskManager Authority",
            _display(risk_authority),
            helper="Autoridade final de risco",
            status=_bool_status(risk_authority, true_status="OK", false_status="BLOCKED"),
        ),
        render_compact_kpi(
            "30d Readiness",
            _display(readiness_30d),
            helper="Sem auto-release canary/live",
            status=str(readiness_30d or "UNKNOWN"),
        ),
        render_compact_kpi(
            "N4 Hard Blocks",
            str(n4_count) if policies else "UNKNOWN",
            helper="Catálogo completo de políticas",
            status="HARD_BLOCKED" if n4_count else "UNKNOWN",
        ),
    ]


def _policy_level_rows(policies: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    definitions = (
        ("N1", "N1_LOCAL_INFO", "LOCAL / READ-ONLY", "LOW", "local_ui_only", "READONLY"),
        ("N2", "N2_DRY_RUN_STUB", "DRY-RUN STUB", "MEDIUM", "simulated_only", "INFO"),
        ("N3", "N3_DRY_RUN_STUB_SENSITIVE", "SENSITIVE DRY-RUN", "HIGH", "simulated_sensitive_only", "WARNING"),
        ("N4", "N4_HARD_BLOCKED", "NO EXECUTION", "CRITICAL", "none", "HARD_BLOCKED"),
    )
    return [
        {
            "Nível": label,
            "Políticas": sum(1 for policy in policies if str(policy.get("level")) == level),
            "Modo": mode,
            "Risco": risk,
            "Autoridade": authority,
            "Status": status,
        }
        for label, level, mode, risk, authority, status in definitions
    ]


def _policy_matrix_status(policies: Sequence[Mapping[str, Any]]) -> str:
    if not policies:
        return "UNKNOWN"
    n4 = [row for row in policies if str(row.get("level")) == "N4_HARD_BLOCKED"]
    if not n4:
        return "BLOCKED"
    if any(bool(row.get("enabled")) or bool(row.get("dry_run_only")) or not bool(row.get("hard_blocked")) for row in n4):
        return "BLOCKED"
    return "READONLY"


def _safety_boundary_rows(active: Mapping[str, Any], kill: Mapping[str, Any], security: Mapping[str, Any]) -> list[dict[str, Any]]:
    fields = (
        ("Command execution enabled", active, "command_execution_enabled"),
        ("Paper entry allowed", active, "paper_entry_allowed"),
        ("Global kill switch active", kill, "global_kill_switch_active"),
        ("Kill switch effective", kill, "kill_switch_effective"),
        ("Reconciliation lock active", security, "reconciliation_lock_active"),
        ("RiskManager authority", security, "riskmanager_authority"),
        ("Live authority", security, "live_authority"),
        ("Real order submission enabled", security, "real_order_submission_enabled"),
    )
    return [{"Controle": label, "Valor": _display(source.get(key))} for label, source, key in fields]


def _safety_boundary_status(active: Mapping[str, Any], kill: Mapping[str, Any], security: Mapping[str, Any]) -> str:
    if _bool_or_none(active.get("command_execution_enabled")) is True:
        return "BLOCKED"
    if _bool_or_none(security.get("live_authority")) is True:
        return "BLOCKED"
    if _bool_or_none(security.get("real_order_submission_enabled")) is True:
        return "BLOCKED"
    if _bool_or_none(security.get("riskmanager_authority")) is False:
        return "BLOCKED"
    if _bool_or_none(kill.get("global_kill_switch_active")) is True:
        return "BLOCKED"
    if not active and not kill and not security:
        return "UNKNOWN"
    return worst_status(_section_status(active), _section_status(kill), _section_status(security), default="UNKNOWN")


def _readiness_rows(readiness: Mapping[str, Any]) -> list[dict[str, Any]]:
    fields = (
        ("7d diagnostic", "seven_day_diagnostic_status"),
        ("30d readiness", "thirty_day_readiness_status"),
        ("Continuous valid soak days", "continuous_valid_soak_days"),
        ("Observed calendar days", "observed_calendar_days"),
        ("Critical gaps", "critical_gap_count"),
        ("Warning gaps", "warning_gap_count"),
        ("Max gap minutes", "max_gap_minutes"),
        ("Readiness gap free", "readiness_gap_free"),
        ("Manual go/no-go required", "manual_go_no_go_required"),
        ("Canary release allowed", "canary_release_allowed"),
        ("Live release allowed", "live_release_allowed"),
    )
    return [{"Gate": label, "Valor": _display(readiness.get(key))} for label, key in fields]


def _readiness_status(readiness: Mapping[str, Any]) -> str:
    if not readiness:
        return "UNKNOWN"
    critical = _non_negative_int(readiness.get("critical_gap_count"))
    if critical is not None and critical > 0:
        return "BLOCKED"
    return worst_status(_section_status(readiness), str(readiness.get("thirty_day_readiness_status") or "UNKNOWN"), default="UNKNOWN")


def _paper_runtime_rows(runtime: Mapping[str, Any]) -> list[dict[str, Any]]:
    fields = (
        ("Paper runtime alive", "paper_runtime_alive"),
        ("Paper runtime fresh", "paper_runtime_fresh"),
        ("Critical stale count", "critical_stale_count"),
        ("Warning stale count", "warning_stale_count"),
        ("Container collection requested", "container_collection_requested"),
        ("Container snapshot status", "container_snapshot_status"),
        ("Docker services status", "docker_services_status"),
        ("Freqtrade paper status", "freqtrade_paper_status"),
        ("SmartCrypto bot status", "smartcrypto_bot_status"),
        ("Canary release allowed", "canary_release_allowed"),
        ("Live release allowed", "live_release_allowed"),
    )
    return [{"Sinal": label, "Valor": _display(runtime.get(key))} for label, key in fields]


def _dry_run_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for intent in _example_intents():
        result = evaluate_dashboard_command_intent(intent).to_dict()
        effect = _mapping(result.get("simulated_effect")).get("effect")
        rows.append(
            {
                "Comando": result.get("command_name", "UNKNOWN"),
                "Nível": result.get("level", "UNKNOWN"),
                "Resultado": result.get("status", "UNKNOWN"),
                "Aceito": _display(result.get("accepted")),
                "Executado": _display(result.get("executed")),
                "Efeito simulado": _display(effect),
            }
        )
    return rows


def _runtime_evidence_rows(evidence: Mapping[str, Any]) -> list[dict[str, Any]]:
    fields = (
        ("Runtime evidence status", "runtime_evidence_status"),
        ("Runtime evidence pack", "runtime_evidence_pack_status"),
        ("Readiness status", "readiness_status"),
        ("Paper runtime health", "paper_runtime_health_status"),
        ("Container snapshot", "container_snapshot_status"),
        ("Soak status", "soak_status"),
        ("Gap accounting", "gap_accounting_status"),
        ("Continuous valid soak days", "continuous_valid_soak_days"),
        ("Required soak days", "required_soak_days"),
        ("Critical gaps", "critical_gap_count"),
        ("Evidence sources blocked", "evidence_sources_blocked"),
        ("Evidence sources missing", "evidence_sources_missing"),
        ("Evidence sources stale", "evidence_sources_stale"),
        ("Canary release allowed", "canary_release_allowed"),
        ("Live release allowed", "live_release_allowed"),
    )
    return [{"Evidência": label, "Valor": _display(evidence.get(key))} for label, key in fields]


def _runtime_blocker_rows(sections: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "Domínio": label,
            "Status": _section_status(_mapping(sections.get(name))),
            "Reason": _display(_mapping(sections.get(name)).get("reason")),
        }
        for label, name in _RUNTIME_BLOCKER_SECTIONS
    ]


def _runtime_blocker_status(sections: Mapping[str, Any]) -> str:
    statuses = [_section_status(_mapping(sections.get(name))) for _label, name in _RUNTIME_BLOCKER_SECTIONS]
    if all(status == "UNKNOWN" for status in statuses):
        return "UNKNOWN"
    return worst_status(*statuses, default="UNKNOWN")


def _diagnostic_rows(snapshot: Mapping[str, Any], audit: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {"Campo": "Schema", "Valor": _display(snapshot.get("schema_version"))},
        {"Campo": "Runtime mode", "Valor": _display(snapshot.get("runtime_mode"))},
        {"Campo": "Snapshot status", "Valor": _snapshot_status(snapshot)},
        {"Campo": "Last updated UTC", "Valor": _display(snapshot.get("last_updated_utc"))},
        {"Campo": "Missing required sources", "Valor": _display(snapshot.get("missing_required_sources"))},
        {"Campo": "Missing optional sources", "Valor": _display(snapshot.get("missing_optional_sources"))},
        {"Campo": "Dashboard reads only", "Valor": _display(audit.get("dashboard_reads_only"))},
        {"Campo": "Changes config", "Valor": _display(audit.get("changes_config"))},
    ]


def _safety_contract_rows(snapshot: Mapping[str, Any], active: Mapping[str, Any], security: Mapping[str, Any], readiness: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {"Invariante": "dashboard_readonly", "Valor": _display(snapshot.get("dashboard_readonly"))},
        {"Invariante": "paper_only", "Valor": _display(snapshot.get("paper_only"))},
        {"Invariante": "shadow_only", "Valor": _display(snapshot.get("shadow_only"))},
        {"Invariante": "live_locked", "Valor": _display(snapshot.get("live_locked"))},
        {"Invariante": "order_submission_enabled", "Valor": _display(snapshot.get("order_submission_enabled"))},
        {"Invariante": "real_order_submission_enabled", "Valor": _display(snapshot.get("real_order_submission_enabled"))},
        {"Invariante": "command_execution_enabled", "Valor": _display(active.get("command_execution_enabled"))},
        {"Invariante": "riskmanager_authority", "Valor": _display(security.get("riskmanager_authority"))},
        {"Invariante": "live_authority", "Valor": _display(security.get("live_authority"))},
        {"Invariante": "canary_release_allowed", "Valor": _display(readiness.get("canary_release_allowed"))},
        {"Invariante": "live_release_allowed", "Valor": _display(readiness.get("live_release_allowed"))},
    ]


def _display(value: Any) -> str:
    if value is None:
        return "UNKNOWN"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        if not math.isfinite(value):
            return "UNKNOWN"
        return f"{value:.4f}".rstrip("0").rstrip(".")
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        items = list(value)
        return "—" if not items else ", ".join(str(item) for item in items)
    text = str(value).strip()
    return text or "UNKNOWN"


def _bool_or_none(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    candidate = str(value).strip().lower()
    if candidate in {"true", "1", "yes"}:
        return True
    if candidate in {"false", "0", "no"}:
        return False
    return None


def _bool_status(value: bool | None, *, true_status: str, false_status: str) -> str:
    if value is True:
        return true_status
    if value is False:
        return false_status
    return "UNKNOWN"


def _non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


if __name__ == "__main__":
    main()
