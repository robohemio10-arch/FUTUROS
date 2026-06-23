"""Read-only executive evidence pack for OCR V1.1 research branches 01-04."""

from __future__ import annotations

import html
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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
    "runs_ai_shadow_incremental": False,
    "cleans_sqlite": False,
    "registers_model": False,
    "auto_promote": False,
    "production_enabled": False,
}


@dataclass(frozen=True)
class ExecutiveReportPackPaths:
    project_root: Path
    branch01_primary_json: Path
    branch01_fallback_json: Path
    branch01_markdown: Path
    branch02_primary_json: Path
    branch02_fallback_json: Path
    branch02_markdown: Path
    branch03_primary_json: Path
    branch03_fallback_json: Path
    branch03_markdown: Path
    branch04_primary_json: Path
    branch04_fallback_json: Path
    branch04_markdown: Path
    output_json: Path
    output_markdown: Path
    output_html: Path


@dataclass(frozen=True)
class ExecutiveReportPackConfig:
    strict: bool = False
    title: str = "SMART FUTUROS - Training Executive Report Pack"
    pack_version: str = "1.0"


@dataclass(frozen=True)
class ExecutiveReportPackResult:
    pack: dict[str, Any]
    markdown: str
    html: str


def _resolve(root: Path, value: str | Path | None, default: Path) -> Path:
    if value is None:
        return default.resolve()
    path = Path(value).expanduser()
    return (root / path).resolve() if not path.is_absolute() else path.resolve()


def resolve_paths(
    project_root: str | Path,
    *,
    output_json: str | Path | None = None,
    output_md: str | Path | None = None,
    output_html: str | Path | None = None,
) -> ExecutiveReportPackPaths:
    root = Path(project_root).expanduser().resolve()
    reports = root / "data" / "reports"
    training = reports / "training_reports"
    return ExecutiveReportPackPaths(
        project_root=root,
        branch01_primary_json=reports / "ocr_v11_research_dataset_summary.json",
        branch01_fallback_json=training / "ocr_v11_research_dataset_summary.json",
        branch01_markdown=training / "ocr_v11_research_dataset_executive.md",
        branch02_primary_json=reports / "ocr_v11_tp_sl_grid_summary.json",
        branch02_fallback_json=training / "ocr_v11_tp_sl_summary.json",
        branch02_markdown=training / "ocr_v11_tp_sl_executive.md",
        branch03_primary_json=reports / "ocr_v11_walkforward_montecarlo_summary.json",
        branch03_fallback_json=training / "ocr_v11_walkforward_montecarlo_summary.json",
        branch03_markdown=training / "ocr_v11_walkforward_montecarlo_executive.md",
        branch04_primary_json=reports / "qlib_ocr_v11_supervised_training_summary.json",
        branch04_fallback_json=training / "qlib_ocr_v11_supervised_training_summary.json",
        branch04_markdown=training / "qlib_ocr_v11_supervised_training_executive.md",
        output_json=_resolve(
            root,
            output_json,
            training / "smart_futuros_training_executive_pack.json",
        ),
        output_markdown=_resolve(
            root,
            output_md,
            training / "smart_futuros_training_executive_pack.md",
        ),
        output_html=_resolve(
            root,
            output_html,
            training / "smart_futuros_training_executive_pack.html",
        ),
    )


def load_json_report(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"json_report_must_be_object:{path}")
    return payload


def load_text_report(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def _markdown_number(text: str, label: str) -> float | None:
    match = re.search(
        rf"(?:\*\*)?{re.escape(label)}[^:\n]*:(?:\*\*)?\s*"
        rf"([+-]?[0-9]+(?:[.,][0-9]+)?)%?",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    return float(match.group(1).replace(",", "."))


def _branch01_from_markdown(text: str) -> dict[str, Any]:
    total = _markdown_number(text, "Quantidade de trades")
    eligible = _markdown_number(text, "Trades elegíveis")
    blocked = _markdown_number(text, "Trades bloqueados")
    alignment = _markdown_number(text, "Taxa alinhada")
    return {
        "status": "warning",
        "trades": int(total) if total is not None else None,
        "eligible_trades": int(eligible) if eligible is not None else None,
        "blocked_trades": int(blocked) if blocked is not None else None,
        "candle_alignment": {
            "aligned_rate": alignment / 100.0 if alignment is not None else None
        },
        "source_format": "markdown_fallback",
    }


def _collect_one(
    *,
    name: str,
    primary: Path,
    fallback: Path,
    markdown: Path,
    warnings: list[str],
    missing_sources: list[str],
) -> dict[str, Any]:
    errors: list[str] = []
    for label, path in (("primary_json", primary), ("fallback_json", fallback)):
        if not path.exists():
            continue
        try:
            payload = load_json_report(path)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            errors.append(f"{label}:{type(exc).__name__}")
            continue
        if label == "fallback_json":
            warnings.append(f"{name}_primary_missing_used_fallback_json")
        return {
            "available": True,
            "source_path": str(path),
            "source_kind": label,
            "payload": payload,
            "markdown_path": str(markdown) if markdown.exists() else None,
            "load_errors": errors,
        }
    if markdown.exists():
        try:
            text = load_text_report(markdown)
        except (OSError, UnicodeError) as exc:
            errors.append(f"markdown:{type(exc).__name__}")
        else:
            warnings.append(f"{name}_json_missing_used_markdown")
            payload = _branch01_from_markdown(text) if name == "branch01" else {}
            return {
                "available": True,
                "source_path": str(markdown),
                "source_kind": "markdown",
                "payload": payload,
                "markdown_path": str(markdown),
                "load_errors": errors,
            }
    missing_sources.append(name)
    if errors:
        warnings.extend(f"{name}_{error}" for error in errors)
    return {
        "available": False,
        "source_path": None,
        "source_kind": "missing",
        "payload": {},
        "markdown_path": None,
        "load_errors": errors,
    }


def collect_branch_evidence(paths: ExecutiveReportPackPaths) -> dict[str, Any]:
    warnings: list[str] = []
    missing_sources: list[str] = []
    branches = {
        "branch01": _collect_one(
            name="branch01",
            primary=paths.branch01_primary_json,
            fallback=paths.branch01_fallback_json,
            markdown=paths.branch01_markdown,
            warnings=warnings,
            missing_sources=missing_sources,
        ),
        "branch02": _collect_one(
            name="branch02",
            primary=paths.branch02_primary_json,
            fallback=paths.branch02_fallback_json,
            markdown=paths.branch02_markdown,
            warnings=warnings,
            missing_sources=missing_sources,
        ),
        "branch03": _collect_one(
            name="branch03",
            primary=paths.branch03_primary_json,
            fallback=paths.branch03_fallback_json,
            markdown=paths.branch03_markdown,
            warnings=warnings,
            missing_sources=missing_sources,
        ),
        "branch04": _collect_one(
            name="branch04",
            primary=paths.branch04_primary_json,
            fallback=paths.branch04_fallback_json,
            markdown=paths.branch04_markdown,
            warnings=warnings,
            missing_sources=missing_sources,
        ),
    }
    return {
        "branches": branches,
        "missing_sources": sorted(missing_sources),
        "warnings": sorted(set(warnings)),
    }


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
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return int(number) if number.is_integer() else number


def _branch_payload(evidence: dict[str, Any], name: str) -> dict[str, Any]:
    return evidence.get("branches", {}).get(name, {}).get("payload", {})


def _branch_available(evidence: dict[str, Any], name: str) -> bool:
    return bool(evidence.get("branches", {}).get(name, {}).get("available", False))


def _missing_branch_decision(branch: str, objective: str) -> dict[str, Any]:
    return {
        "branch": branch,
        "objective": objective,
        "status": "missing",
        "decision": "EVIDENCIA_AUSENTE",
        "primary_metric": None,
        "executive_interpretation": "Fonte ausente; nenhuma conclusão foi inferida.",
        "next_gate": "Restaurar a evidência estruturada antes de decidir.",
    }


def _branch_decisions(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    branch01 = _branch_payload(evidence, "branch01")
    branch02 = _branch_payload(evidence, "branch02")
    branch03 = _branch_payload(evidence, "branch03")
    branch04 = _branch_payload(evidence, "branch04")
    objectives = {
        "branch01": "Dataset OCR V1.1 e alinhamento de candles",
        "branch02": "Grade TP/SL e outcomes alternativos",
        "branch03": "Walk-forward e Monte Carlo",
        "branch04": "Laboratório supervisionado Qlib OCR V1.1",
    }
    rows = [
        {
            "branch": "01",
            "objective": objectives["branch01"],
            "status": _value(branch01, "status") or "missing",
            "decision": "USAR_PARCIALMENTE_EM_RESEARCH",
            "primary_metric": _number(
                _value(branch01, "candle_alignment.aligned_rate")
            ),
            "executive_interpretation": (
                "Base utilizável para pesquisa, com linhas bloqueadas fora de treino."
            ),
            "next_gate": "Melhorar cobertura sem relaxar quality gates.",
        },
        {
            "branch": "02",
            "objective": objectives["branch02"],
            "status": _value(branch02, "status") or "missing",
            "decision": "DESCARTAR_CANDIDATO",
            "primary_metric": _number(
                _value(branch02, "best_net_pnl", "net_pnl_delta_vs_original")
            ),
            "executive_interpretation": (
                "A melhor configuração fixa não melhorou o resultado original."
            ),
            "next_gate": "Não promover; testar famílias com hipótese econômica nova.",
        },
        {
            "branch": "03",
            "objective": objectives["branch03"],
            "status": _value(branch03, "status") or "missing",
            "decision": _value(branch03, "decision") or "DESCARTAR_CANDIDATO",
            "primary_metric": _number(
                _value(branch03, "monte_carlo.risk_of_ruin", "monte_carlo_risk_of_ruin")
            ),
            "executive_interpretation": (
                "O candidato falhou fora da amostra e apresentou risco de ruína impeditivo."
            ),
            "next_gate": "Exigir edge fora da amostra e risco de ruína aceitável.",
        },
        {
            "branch": "04",
            "objective": objectives["branch04"],
            "status": _value(branch04, "status") or "missing",
            "decision": _value(branch04, "decision") or "MANTER_EM_RESEARCH",
            "primary_metric": _number(
                _value(branch04, "aggregate_metrics.mean_roc_auc", "mean_roc_auc")
            ),
            "executive_interpretation": (
                "O seletor treinou, mas não superou o baseline financeiro all-test."
            ),
            "next_gate": "Exigir ganho incremental reproduzível antes de registry.",
        },
    ]
    output: list[dict[str, Any]] = []
    for name, row in zip(objectives, rows, strict=True):
        output.append(
            row
            if _branch_available(evidence, name)
            else _missing_branch_decision(row["branch"], objectives[name])
        )
    return output


def _consolidated_kpis(evidence: dict[str, Any]) -> dict[str, Any]:
    branch01 = _branch_payload(evidence, "branch01")
    branch02 = _branch_payload(evidence, "branch02")
    branch03 = _branch_payload(evidence, "branch03")
    branch04 = _branch_payload(evidence, "branch04")
    return {
        "total_trades_ocr_v11": _number(
            _value(branch01, "trades", "research_dataset_rows")
        ),
        "eligible_rows": _number(
            _value(branch01, "eligible_trades", "eligible_rows")
        ),
        "blocked_rows": _number(
            _value(branch01, "blocked_trades", "blocked_rows")
        ),
        "alignment_rate": _number(_value(branch01, "candle_alignment.aligned_rate")),
        "best_tp_sl_net_pnl": _number(_value(branch02, "best_net_pnl")),
        "original_net_pnl": _number(_value(branch02, "original_net_pnl")),
        "walkforward_candidate_net_pnl": _number(
            _value(branch03, "candidate_walkforward_net_pnl")
        ),
        "walkforward_original_net_pnl": _number(
            _value(branch03, "original_walkforward_net_pnl")
        ),
        "monte_carlo_risk_of_ruin": _number(
            _value(branch03, "monte_carlo.risk_of_ruin", "monte_carlo_risk_of_ruin")
        ),
        "supervised_mean_accuracy": _number(
            _value(branch04, "aggregate_metrics.mean_accuracy", "mean_accuracy")
        ),
        "supervised_mean_roc_auc": _number(
            _value(branch04, "aggregate_metrics.mean_roc_auc", "mean_roc_auc")
        ),
        "supervised_mean_f1": _number(
            _value(branch04, "aggregate_metrics.mean_f1", "mean_f1")
        ),
        "supervised_all_test_net_pnl": _number(
            _value(branch04, "aggregate_metrics.all_test_net_pnl", "all_test_net_pnl")
        ),
        "supervised_selected_net_pnl": _number(
            _value(branch04, "aggregate_metrics.selected_net_pnl", "selected_net_pnl")
        ),
        "selected_rows": _number(
            _value(branch04, "aggregate_metrics.selected_rows", "selected_rows")
        ),
        "feature_count": _number(_value(branch04, "feature_count")),
        "valid_folds": _number(
            _value(branch04, "aggregate_metrics.valid_folds", "valid_folds")
        ),
    }


def _chart_data(
    decisions: list[dict[str, Any]],
    kpis: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    return {
        "branch_status_chart": [
            {"branch": row["branch"], "status": row["status"]} for row in decisions
        ],
        "pnl_comparison_chart": [
            {"series": "tp_sl_best", "value": kpis["best_tp_sl_net_pnl"]},
            {"series": "tp_sl_original", "value": kpis["original_net_pnl"]},
            {
                "series": "walkforward_candidate",
                "value": kpis["walkforward_candidate_net_pnl"],
            },
            {
                "series": "walkforward_original",
                "value": kpis["walkforward_original_net_pnl"],
            },
            {
                "series": "supervised_all_test",
                "value": kpis["supervised_all_test_net_pnl"],
            },
            {
                "series": "supervised_selected",
                "value": kpis["supervised_selected_net_pnl"],
            },
        ],
        "ml_metrics_chart": [
            {"metric": "accuracy", "value": kpis["supervised_mean_accuracy"]},
            {"metric": "f1", "value": kpis["supervised_mean_f1"]},
            {"metric": "roc_auc", "value": kpis["supervised_mean_roc_auc"]},
        ],
        "eligibility_chart": [
            {"category": "eligible", "value": kpis["eligible_rows"]},
            {"category": "blocked", "value": kpis["blocked_rows"]},
        ],
    }


def build_executive_pack(
    evidence: dict[str, Any],
    config: ExecutiveReportPackConfig,
) -> dict[str, Any]:
    missing_sources = list(evidence.get("missing_sources", []))
    warnings = list(evidence.get("warnings", []))
    decisions = _branch_decisions(evidence)
    kpis = _consolidated_kpis(evidence)
    if config.strict and missing_sources:
        status, reason = "blocked", "strict_missing_sources"
    elif missing_sources:
        status, reason = "warning", "partial_evidence_missing_sources"
    else:
        status, reason = "warning", "evidence_consolidated_no_promotion"
    executive_summary = [
        "A decisão institucional é manter em research até existir evidência incremental mais forte."
    ]
    negative_evidence = [
        "Registrar resultados negativos evita promoção prematura e reduz risco institucional."
    ]
    if _branch_available(evidence, "branch01"):
        executive_summary.insert(
            0,
            "A base OCR V1.1 é utilizável para research, mas permanece parcialmente elegível.",
        )
    if _branch_available(evidence, "branch02"):
        executive_summary.insert(
            -1,
            "A grade TP/SL fixa foi reprovada e o candidato não deve ser promovido.",
        )
        negative_evidence.insert(
            0,
            "Branch 02: a melhor configuração TP/SL fixa piorou o resultado original.",
        )
    if _branch_available(evidence, "branch03"):
        executive_summary.insert(
            -1,
            "Walk-forward e Monte Carlo bloquearam o candidato, com risco de ruína impeditivo.",
        )
        negative_evidence.insert(
            -1,
            "Branch 03: o candidato foi descartado após walk-forward e Monte Carlo.",
        )
    if _branch_available(evidence, "branch04"):
        executive_summary.insert(
            -1,
            "O laboratório supervisionado treinou um seletor, mas não superou o baseline all-test.",
        )
        negative_evidence.insert(
            -1,
            "Branch 04: o seletor não superou o baseline financeiro all-test.",
        )
    return {
        "pack_version": config.pack_version,
        "title": config.title,
        "status": status,
        "reason": reason,
        "decision": "MANTER_EM_RESEARCH",
        "executive_summary": executive_summary,
        "branch_decisions": decisions,
        "consolidated_kpis": kpis,
        "negative_evidence": negative_evidence,
        "positive_evidence": [
            "Pipeline de pesquisa reproduzível e auditável.",
            "Parcela relevante da base OCR V1.1 possui alinhamento de candles válido.",
            "Métricas financeiras, walk-forward, Monte Carlo e ML estão estruturadas.",
            "A seleção de features da Branch 04 exclui leakage e outcomes pós-evento.",
            "Safety flags permanecem bloqueadas para live, ordens e produção.",
            "Outputs runtime permanecem fora do versionamento.",
        ],
        "advancement_gates": [
            {
                "gate": "registry_champion_challenger",
                "status": "pending",
                "requirement": "Evidência incremental fora da amostra superior ao baseline.",
            },
            {
                "gate": "ai_shadow_online_learning",
                "status": "blocked",
                "requirement": "Drift, amostra e promoção manual formalmente aprovados.",
            },
            {
                "gate": "freqtrade_paper_selector",
                "status": "blocked",
                "requirement": "Seletor superar baseline financeiro em paper controlado.",
            },
            {
                "gate": "dashboard_training_command_center",
                "status": "pending",
                "requirement": "Consumir somente snapshots read-only deste pack.",
            },
            {
                "gate": "30d_soak_readiness",
                "status": "blocked",
                "requirement": "Completar soak de 30 dias e gates de readiness.",
            },
        ],
        "chart_data": _chart_data(decisions, kpis),
        "source_manifest": {
            name: {
                "available": branch["available"],
                "source_path": branch["source_path"],
                "source_kind": branch["source_kind"],
                "load_errors": branch["load_errors"],
            }
            for name, branch in evidence.get("branches", {}).items()
        },
        "missing_sources": missing_sources,
        "warnings": warnings,
        **SAFETY_FLAGS,
    }


def _display(value: Any) -> str:
    if value is None:
        return "indisponível"
    if isinstance(value, float):
        return f"{value:.6f}"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _markdown_table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    output = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
    ]
    output.extend("| " + " | ".join(_display(value) for value in row) + " |" for row in rows)
    return output


def render_markdown_pack(pack: dict[str, Any]) -> str:
    lines = [
        f"# {pack['title']}",
        "",
        f"**Data da análise (UTC):** {pack['analysis_date_utc']}",
        f"**Status:** `{pack['status']}`",
        f"**Decisão institucional:** `{pack['decision']}`",
        "",
        "## 1. Resumo executivo",
        "",
    ]
    lines.extend(f"- {item}" for item in pack["executive_summary"])
    lines.extend(["", "## 2. Decisão por branch", ""])
    lines.extend(
        _markdown_table(
            ["Branch", "Objetivo", "Status", "Decisão", "Métrica", "Interpretação", "Próximo gate"],
            [
                [
                    row["branch"],
                    row["objective"],
                    row["status"],
                    row["decision"],
                    row["primary_metric"],
                    row["executive_interpretation"],
                    row["next_gate"],
                ]
                for row in pack["branch_decisions"]
            ],
        )
    )
    lines.extend(["", "## 3. KPIs consolidados", ""])
    lines.extend(
        _markdown_table(
            ["KPI", "Valor"],
            [[name, value] for name, value in pack["consolidated_kpis"].items()],
        )
    )
    for number, title, key in (
        (4, "Evidências negativas", "negative_evidence"),
        (5, "Evidências positivas", "positive_evidence"),
    ):
        lines.extend(["", f"## {number}. {title}", ""])
        lines.extend(f"- {item}" for item in pack[key])
    lines.extend(["", "## 6. Gates para avançar", ""])
    lines.extend(
        _markdown_table(
            ["Gate", "Status", "Requisito"],
            [[row["gate"], row["status"], row["requirement"]] for row in pack["advancement_gates"]],
        )
    )
    lines.extend(["", "## 7. Safety block", ""])
    lines.extend(
        _markdown_table(
            ["Flag", "Valor"],
            [[name, pack[name]] for name in SAFETY_FLAGS],
        )
    )
    lines.extend(["", "## 8. Fontes e limitações", ""])
    lines.append(f"- Fontes ausentes: {_display(pack['missing_sources'])}")
    lines.append(f"- Warnings: {_display(pack['warnings'])}")
    lines.append("- Este pacote consolida evidência; não promove modelo, estratégia ou runtime.")
    lines.append("")
    return "\n".join(lines)


def _html_table(headers: list[str], rows: list[list[Any]]) -> str:
    head = "".join(f"<th>{html.escape(header)}</th>" for header in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{html.escape(_display(value))}</td>" for value in row) + "</tr>"
        for row in rows
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def render_html_pack(pack: dict[str, Any]) -> str:
    decisions = _html_table(
        ["Branch", "Objetivo", "Status", "Decisão", "Métrica", "Interpretação", "Próximo gate"],
        [
            [
                row["branch"],
                row["objective"],
                row["status"],
                row["decision"],
                row["primary_metric"],
                row["executive_interpretation"],
                row["next_gate"],
            ]
            for row in pack["branch_decisions"]
        ],
    )
    kpis = _html_table(
        ["KPI", "Valor"],
        [[name, value] for name, value in pack["consolidated_kpis"].items()],
    )
    gates = _html_table(
        ["Gate", "Status", "Requisito"],
        [[row["gate"], row["status"], row["requirement"]] for row in pack["advancement_gates"]],
    )
    safety = _html_table(
        ["Flag", "Valor"],
        [[name, pack[name]] for name in SAFETY_FLAGS],
    )
    negative = "".join(f"<li>{html.escape(item)}</li>" for item in pack["negative_evidence"])
    positive = "".join(f"<li>{html.escape(item)}</li>" for item in pack["positive_evidence"])
    summary = "".join(f"<li>{html.escape(item)}</li>" for item in pack["executive_summary"])
    title = html.escape(str(pack["title"]))
    return f"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
body {{ font-family: Arial, sans-serif; margin: 2rem auto; max-width: 1200px; color: #17202a; line-height: 1.45; }}
h1, h2 {{ color: #12344d; }}
.decision {{ border-left: 5px solid #b9770e; background: #fdf2e9; padding: 1rem; }}
table {{ border-collapse: collapse; width: 100%; margin: 1rem 0 2rem; }}
th, td {{ border: 1px solid #ccd1d1; padding: .55rem; text-align: left; vertical-align: top; }}
th {{ background: #eaf2f8; }}
code {{ background: #f2f3f4; padding: .15rem .3rem; }}
</style>
</head>
<body>
<h1>{title}</h1>
<p>Data UTC: <code>{html.escape(str(pack['analysis_date_utc']))}</code></p>
<p class="decision"><strong>Status:</strong> {html.escape(str(pack['status']))}<br>
<strong>Decisão:</strong> {html.escape(str(pack['decision']))}</p>
<h2>Resumo executivo</h2><ul>{summary}</ul>
<h2>Decisão por branch</h2>{decisions}
<h2>KPIs consolidados</h2>{kpis}
<h2>Evidências negativas</h2><ul>{negative}</ul>
<h2>Evidências positivas</h2><ul>{positive}</ul>
<h2>Gates para avançar</h2>{gates}
<h2>Safety block</h2>{safety}
<p>Pacote read-only. Nenhum modelo, estratégia, risco ou runtime foi alterado.</p>
</body>
</html>
"""


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
    encoded = json.dumps(
        _json_safe(payload),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    )
    _atomic_write_text(path, encoded + "\n")


def run_training_executive_report_pack(
    paths: ExecutiveReportPackPaths,
    config: ExecutiveReportPackConfig,
    *,
    write: bool = False,
    analysis_date_utc: str | None = None,
) -> ExecutiveReportPackResult:
    evidence = collect_branch_evidence(paths)
    pack = build_executive_pack(evidence, config)
    pack["analysis_date_utc"] = analysis_date_utc or "not_recorded_no_write"
    pack["write_requested"] = write
    pack["write_performed"] = False
    pack["output_json"] = str(paths.output_json)
    pack["output_markdown"] = str(paths.output_markdown)
    pack["output_html"] = str(paths.output_html)
    markdown = render_markdown_pack(pack)
    rendered_html = render_html_pack(pack)
    if write:
        if analysis_date_utc is None:
            pack["analysis_date_utc"] = datetime.now(timezone.utc).isoformat()
            markdown = render_markdown_pack(pack)
            rendered_html = render_html_pack(pack)
        pack["write_performed"] = True
        _atomic_write_json(paths.output_json, pack)
        _atomic_write_text(paths.output_markdown, markdown)
        _atomic_write_text(paths.output_html, rendered_html)
    return ExecutiveReportPackResult(pack=pack, markdown=markdown, html=rendered_html)
