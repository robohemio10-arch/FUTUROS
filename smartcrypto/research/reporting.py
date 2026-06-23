"""Pure executive reporting helpers for SmartCrypto research datasets."""

from __future__ import annotations

from typing import Any

import pandas as pd


def build_group_summary(dataset: pd.DataFrame, column: str) -> list[dict[str, Any]]:
    if column not in dataset.columns or dataset.empty:
        return []
    counts = dataset[column].fillna("unknown").astype(str).value_counts(dropna=False).sort_index()
    return [{column: str(value), "trades": int(count)} for value, count in counts.items()]


def build_alignment_summary(dataset: pd.DataFrame) -> dict[str, Any]:
    total = int(len(dataset))
    aligned = (
        int(dataset["candle_alignment_status"].eq("aligned").sum())
        if "candle_alignment_status" in dataset.columns
        else 0
    )
    partial = total - aligned
    return {
        "total_trades": total,
        "aligned_trades": aligned,
        "missing_or_partial_trades": partial,
        "aligned_rate": aligned / total if total else 0.0,
    }


def prepare_chart_data(dataset: pd.DataFrame) -> dict[str, list[dict[str, Any]]]:
    eligible = dataset.copy()
    if "is_research_eligible" in eligible.columns:
        eligible["eligibility"] = eligible["is_research_eligible"].map(
            {True: "eligible", False: "blocked"}
        ).fillna("blocked")
    else:
        eligible["eligibility"] = "blocked"
    return {
        "trades_by_symbol": build_group_summary(dataset, "symbol"),
        "trades_by_side": build_group_summary(dataset, "side"),
        "trades_by_alignment": build_group_summary(dataset, "candle_alignment_status"),
        "trades_by_eligibility": build_group_summary(eligible, "eligibility"),
    }


def build_executive_summary(
    dataset: pd.DataFrame,
    technical_report: dict[str, Any],
    *,
    analysis_date_utc: str,
) -> dict[str, Any]:
    alignment = build_alignment_summary(dataset)
    eligible = (
        int(dataset["is_research_eligible"].eq(True).sum())
        if "is_research_eligible" in dataset.columns
        else 0
    )
    total = int(len(dataset))
    symbols = (
        sorted(dataset["symbol"].dropna().astype(str).unique().tolist())
        if "symbol" in dataset.columns
        else []
    )
    min_open = technical_report.get("min_open_time")
    max_close = technical_report.get("max_close_time")
    if total == 0:
        conclusion = "Nenhum trade foi materializado para análise."
    elif eligible == total:
        conclusion = "Todos os trades possuem qualidade suficiente para pesquisa posterior."
    elif eligible == 0:
        conclusion = "Nenhum trade atende integralmente aos gates atuais de pesquisa."
    else:
        conclusion = (
            "A base é parcialmente elegível; linhas bloqueadas devem permanecer fora de treino "
            "até correção das fontes ou do alinhamento."
        )
    return {
        "status": technical_report.get("status", "blocked"),
        "analysis_date_utc": analysis_date_utc,
        "source_analyzed": technical_report.get("source_master_path"),
        "trades": total,
        "eligible_trades": eligible,
        "blocked_trades": total - eligible,
        "symbols": symbols,
        "period": {"min_open_time": min_open, "max_close_time": max_close},
        "candle_alignment": alignment,
        "tables": {
            "symbols": build_group_summary(dataset, "symbol"),
            "sides": build_group_summary(dataset, "side"),
            "alignment": build_group_summary(dataset, "candle_alignment_status"),
        },
        "chart_data": prepare_chart_data(dataset),
        "conclusion": conclusion,
    }


def render_executive_markdown(summary: dict[str, Any]) -> str:
    alignment = summary["candle_alignment"]
    symbols = ", ".join(summary["symbols"]) or "Nenhum"
    period = summary["period"]
    return "\n".join(
        [
            "# OCR V1.1 Research Dataset - Relatório Executivo",
            "",
            f"**Data da análise (UTC):** {summary['analysis_date_utc']}",
            f"**Fonte analisada:** `{summary['source_analyzed']}`",
            f"**Quantidade de trades:** {summary['trades']}",
            f"**Trades elegíveis:** {summary['eligible_trades']}",
            f"**Trades bloqueados:** {summary['blocked_trades']}",
            f"**Símbolos analisados:** {symbols}",
            (
                "**Período analisado:** "
                f"{period.get('min_open_time') or 'indisponível'} a "
                f"{period.get('max_close_time') or 'indisponível'}"
            ),
            "",
            "## Qualidade do candle alignment",
            "",
            f"- Alinhados: {alignment['aligned_trades']}",
            f"- Ausentes ou parciais: {alignment['missing_or_partial_trades']}",
            f"- Taxa alinhada: {alignment['aligned_rate']:.2%}",
            "",
            "## Conclusão",
            "",
            str(summary["conclusion"]),
            "",
        ]
    )
