from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from smartcrypto.dashboard.ui.tables import render_html_table


SUMMARY_COLUMNS = [
    "status",
    "gate_allowed",
    "gate_rows_total",
    "gate_pass_total",
    "gate_warning_total",
    "gate_blocked_total",
]

GATE_COLUMNS = [
    "gate_id",
    "contract_id",
    "producer_id",
    "domain",
    "target_canonical_path",
    "artifact_exists",
    "artifact_status",
    "expected_timestamp_field",
    "effective_timestamp_utc",
    "age_seconds",
    "max_acceptable_age_seconds_after_refresh",
    "freshness_passed",
    "health_passed",
    "schema_passed",
    "safety_passed",
    "current_global_blocker_present",
    "gate_state",
    "gate_reason",
]


def render_runtime_freshness_post_refresh_evidence_gate(
    snapshot: Mapping[str, Any],
    *,
    ui: Any,
) -> None:
    payload = runtime_freshness_post_refresh_evidence_gate_view(snapshot)
    ui.subheader("Runtime Freshness: Post-Refresh Evidence Gate")
    if not payload:
        ui.info("UNKNOWN: gate pós-refresh ausente no snapshot.")
        return

    ui.warning(
        "Painel estritamente read-only. Este gate aceita ou bloqueia evidência "
        "pós-refresh; ele não executa producer nem libera live/canary/ordens."
    )
    _render_table(
        ui,
        [post_refresh_gate_summary_row(payload)],
        SUMMARY_COLUMNS,
        ["status"],
        "Resumo do gate ausente.",
    )
    ui.markdown("#### Linhas por artefato")
    _render_table(
        ui,
        post_refresh_gate_rows(payload),
        GATE_COLUMNS,
        ["gate_state", "artifact_status"],
        "Nenhuma linha de gate materializada.",
    )
    _render_text_list(
        ui, "Blockers remanescentes", payload.get("remaining_freshness_blockers")
    )
    _render_text_list(ui, "Bypass indicators", payload.get("bypass_indicators"))
    _render_text_list(
        ui, "Artefatos stale/invalid", payload.get("stale_or_invalid_artifacts")
    )
    decision = payload.get("manual_closeout_decision")
    if isinstance(decision, Mapping):
        ui.markdown("#### Decisao manual de closeout")
        ui.markdown(
            f"- allowed: `{decision.get('allowed', False)}`\n"
            f"- reason: `{decision.get('reason', 'unknown')}`"
        )
    _render_text_list(ui, "Acoes proibidas", payload.get("forbidden_actions"))


def runtime_freshness_post_refresh_evidence_gate_view(
    snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    direct = snapshot.get("runtime_freshness_post_refresh_evidence_gate")
    if isinstance(direct, Mapping):
        return dict(direct)
    sections = snapshot.get("sections", {})
    if not isinstance(sections, Mapping):
        return {}
    section = sections.get("runtime_freshness_post_refresh_evidence_gate", {})
    if isinstance(section, Mapping) and isinstance(section.get("data"), Mapping):
        return dict(section["data"])
    return {}


def post_refresh_gate_summary_row(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "status": str(payload.get("status", "blocked")).upper(),
        "gate_allowed": payload.get("gate_allowed", False),
        "gate_rows_total": payload.get("gate_rows_total", 0),
        "gate_pass_total": payload.get("gate_pass_total", 0),
        "gate_warning_total": payload.get("gate_warning_total", 0),
        "gate_blocked_total": payload.get("gate_blocked_total", 0),
    }


def post_refresh_gate_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    value = payload.get("post_refresh_gate_rows")
    if not isinstance(value, list):
        return []
    return [dict(row) for row in value if isinstance(row, Mapping)]


def _render_text_list(ui: Any, title: str, value: Any) -> None:
    if not isinstance(value, list) or not value:
        return
    ui.markdown(f"#### {title}")
    ui.markdown("\n".join(f"- {item}" for item in value))


def _render_table(
    ui: Any,
    rows: list[dict[str, Any]],
    columns: list[str],
    status_columns: list[str],
    empty_message: str,
) -> None:
    ui.markdown(
        render_html_table(
            rows,
            columns=columns,
            status_columns=status_columns,
            empty_message=empty_message,
        ),
        unsafe_allow_html=True,
    )
