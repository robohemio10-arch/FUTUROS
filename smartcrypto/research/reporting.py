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


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    return frame.astype(object).where(pd.notna(frame), None).to_dict(orient="records")


def _optional_number(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if pd.notna(number) else None


def prepare_tp_sl_chart_data(
    grid: pd.DataFrame,
    trades: pd.DataFrame,
) -> dict[str, list[dict[str, Any]]]:
    """Prepare serializable tables for future charts without rendering them."""
    if grid.empty:
        top_ranking = top_pnl = top_drawdown = heatmap = pd.DataFrame()
    else:
        top_ranking = grid.sort_values(
            ["ranking_score", "strategy_id"], ascending=[False, True]
        ).head(10)
        top_pnl = grid.sort_values(
            ["net_pnl", "strategy_id"], ascending=[False, True]
        ).head(10)
        top_drawdown = grid.sort_values(
            ["max_drawdown", "strategy_id"], ascending=[True, True]
        ).head(10)
        heatmap = grid.loc[
            grid["tp_mode"].eq("fixed_bps") & grid["sl_mode"].eq("fixed_bps"),
            ["tp_value", "sl_value", "net_pnl", "ranking_score"],
        ].sort_values(["tp_value", "sl_value"])
    simulated = trades.loc[
        trades.get("simulation_status", pd.Series(index=trades.index, dtype=str)).eq("ok"),
        ["trade_id", "simulated_net_pnl"],
    ] if not trades.empty else pd.DataFrame()
    comparison_columns = [
        "trade_id",
        "original_net_pnl",
        "simulated_net_pnl",
        "simulated_net_pnl_delta",
    ]
    comparison = (
        trades.loc[trades["simulation_status"].eq("ok"), comparison_columns]
        if not trades.empty
        else pd.DataFrame()
    )
    return {
        "top_10_by_ranking": _records(top_ranking),
        "top_10_by_net_pnl": _records(top_pnl),
        "top_10_by_lowest_drawdown": _records(top_drawdown),
        "fixed_tp_sl_heatmap": _records(heatmap),
        "simulated_pnl_distribution": _records(simulated),
        "original_vs_best_simulation": _records(comparison),
    }


def build_tp_sl_executive_summary(
    grid: pd.DataFrame,
    trades: pd.DataFrame,
    technical_report: dict[str, Any],
    *,
    analysis_date_utc: str,
) -> dict[str, Any]:
    best = grid.loc[grid["is_candidate_best"]].iloc[0] if not grid.empty else None
    evaluated = int(best["evaluated_trades"]) if best is not None else 0
    blocked = int(best["blocked_trades"]) if best is not None else len(trades)
    if best is None:
        conclusion = "A simulação não produziu estratégia candidata válida."
        next_action = "Corrigir os blockers de dados antes de repetir a análise."
    elif float(best["net_pnl_delta_vs_original"]) > 0:
        conclusion = (
            "O melhor cenário simulado superou o resultado original, mas permanece "
            "apenas como evidência de pesquisa."
        )
        next_action = "Validar robustez fora da amostra antes de qualquer decisão posterior."
    else:
        conclusion = (
            "Nenhuma configuração simulada demonstrou melhoria líquida sobre o resultado original."
        )
        next_action = "Revisar custos e estabilidade antes de ampliar a grade de pesquisa."
    return {
        "status": technical_report.get("status", "blocked"),
        "analysis_date_utc": analysis_date_utc,
        "base_analyzed": technical_report.get("research_dataset_path"),
        "evaluated_trades": evaluated,
        "blocked_trades": blocked,
        "best_strategy_id": None if best is None else str(best["strategy_id"]),
        "best_tp_mode": None if best is None else str(best["tp_mode"]),
        "best_tp": None if best is None else _optional_number(best["tp_value"]),
        "best_sl_mode": None if best is None else str(best["sl_mode"]),
        "best_sl": None if best is None else _optional_number(best["sl_value"]),
        "best_trailing_mode": None if best is None else str(best["trailing_mode"]),
        "best_net_pnl": None if best is None else _optional_number(best["net_pnl"]),
        "original_net_pnl": (
            None if best is None else _optional_number(best["original_net_pnl"])
        ),
        "net_pnl_delta_vs_original": (
            None
            if best is None
            else _optional_number(best["net_pnl_delta_vs_original"])
        ),
        "best_profit_factor": (
            None if best is None else _optional_number(best["profit_factor"])
        ),
        "best_win_rate": None if best is None else _optional_number(best["win_rate"]),
        "maximum_observed_drawdown": (
            None if best is None else _optional_number(best["max_drawdown"])
        ),
        "same_candle_policy": "stop_loss_first",
        "auto_promote": False,
        "conclusion": conclusion,
        "next_recommended_action": next_action,
        "chart_data": prepare_tp_sl_chart_data(grid, trades),
    }


def render_tp_sl_executive_markdown(summary: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# OCR V1.1 - Simulação Executiva de TP/SL",
            "",
            f"**Data da análise (UTC):** {summary['analysis_date_utc']}",
            f"**Base analisada:** `{summary['base_analyzed']}`",
            f"**Trades avaliados:** {summary['evaluated_trades']}",
            f"**Trades bloqueados:** {summary['blocked_trades']}",
            (
                "**Melhor TP/SL:** "
                f"{summary['best_strategy_id']} "
                f"(TP {summary['best_tp_mode']}={summary['best_tp']}; "
                f"SL {summary['best_sl_mode']}={summary['best_sl']})"
            ),
            (
                "**Resultado simulado vs. original:** "
                f"{summary['best_net_pnl']} vs. {summary['original_net_pnl']} "
                f"(delta {summary['net_pnl_delta_vs_original']})"
            ),
            f"**Risco máximo observado:** {summary['maximum_observed_drawdown']}",
            "",
            "## Premissa conservadora",
            "",
            "Quando TP e SL são tocados no mesmo candle, o stop loss é aplicado primeiro.",
            "Taxas e slippage são descontados em todas as simulações.",
            "",
            "## Conclusão executiva",
            "",
            str(summary["conclusion"]),
            "",
            "## Próxima ação recomendada",
            "",
            str(summary["next_recommended_action"]),
            "",
        ]
    )
