"""Dependency-free safe HTML tables."""

from html import escape
from typing import Any

from .status import normalize_status, status_to_label


def render_html_table(
    rows: list[dict[str, Any]] | None,
    columns: list[str] | None = None,
    status_columns: list[str] | None = None,
    empty_message: str = "Sem dados disponíveis",
) -> str:
    """Return a dense safe HTML table."""

    safe_rows = rows or []
    if not safe_rows:
        return f'<div class="sfc-table-empty">{escape(empty_message)}</div>'
    selected_columns = columns or list(safe_rows[0])
    statuses = set(status_columns or [])
    header = "".join(f"<th>{escape(str(column))}</th>" for column in selected_columns)
    body_rows = []
    for row in safe_rows:
        cells = []
        for column in selected_columns:
            value = row.get(column, "")
            safe_value = escape(str(value))
            if column in statuses:
                normalized = normalize_status(value)
                safe_value = (
                    f'<span class="sfc-status-pill sfc-status-{normalized}">'
                    f"{status_to_label(normalized)}</span>"
                )
            cells.append(f"<td>{safe_value}</td>")
        body_rows.append(f"<tr>{''.join(cells)}</tr>")
    return (
        '<div class="sfc-table-wrap"><table class="sfc-table">'
        f"<thead><tr>{header}</tr></thead><tbody>{''.join(body_rows)}</tbody>"
        "</table></div>"
    )
