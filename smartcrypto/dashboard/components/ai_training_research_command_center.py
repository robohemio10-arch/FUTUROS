"""Read-only renderer for consolidated AI training research evidence."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


SECTION_KEY = "ai_training_research_command_center"


def extract_ai_training_research_command_center(
    snapshot: Mapping[str, Any] | None,
) -> dict[str, Any]:
    source = snapshot if isinstance(snapshot, Mapping) else {}
    sections = source.get("sections")
    if not isinstance(sections, Mapping):
        return {}
    section = sections.get(SECTION_KEY)
    return dict(section) if isinstance(section, Mapping) else {}


def render_ai_training_research_command_center(
    snapshot: Mapping[str, Any],
    *,
    ui: Any,
) -> None:
    section = extract_ai_training_research_command_center(snapshot)
    if not section:
        ui.info("AI Training Research Command Center: MISSING_OPTIONAL")
        return

    ui.subheader("AI Training Research Command Center")
    ui.caption("Evidência consultiva, sem autoridade operacional.")
    columns = ui.columns(4)
    columns[0].metric("Research Gate", section.get("research_gate_status", "UNKNOWN"))
    columns[1].metric("Decisão", section.get("decision", "UNKNOWN"))
    columns[2].metric("Autoridade", section.get("authority", "advisory_only"))
    summary = section.get("summary")
    summary = summary if isinstance(summary, Mapping) else {}
    columns[3].metric(
        "Fontes",
        f"{summary.get('available_source_count', 0)}/{summary.get('source_count', 8)}",
    )

    cards = section.get("branch_cards")
    card_rows = [_card_row(card) for card in cards] if isinstance(cards, list) else []
    card_rows = [row for row in card_rows if row]
    if card_rows:
        ui.dataframe(card_rows, use_container_width=True, hide_index=True)

    blockers = section.get("blockers")
    if isinstance(blockers, list) and blockers:
        ui.warning("Research blockers permanecem ativos; nenhuma liberação operacional.")
        ui.dataframe(
            [{"Research blocker": str(blocker)} for blocker in blockers],
            use_container_width=True,
            hide_index=True,
        )

    missing = section.get("missing_optional_sources")
    if isinstance(missing, list) and missing:
        ui.info("Fontes opcionais ausentes: " + ", ".join(str(item) for item in missing))

    safety = section.get("safety_flags")
    with ui.expander("Safety flags da pesquisa", expanded=False):
        ui.json(dict(safety) if isinstance(safety, Mapping) else {})


def _card_row(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    headline = value.get("headline_metric")
    headline = headline if isinstance(headline, Mapping) else {}
    return {
        "Branch": value.get("branch_id"),
        "Evidência": value.get("title"),
        "Status": value.get("status"),
        "Decisão": value.get("decision"),
        "Métrica": headline.get("label"),
        "Valor": headline.get("value"),
        "Razão": value.get("reason"),
        "Advisory only": value.get("advisory_only") is True,
    }
