from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


SAFETY_FLAGS: dict[str, bool] = {
    "paper_only": True,
    "shadow_only": True,
    "live_trading_enabled": False,
    "order_submission_enabled": False,
    "real_order_submission_enabled": False,
    "exchange_private_access": False,
    "sends_orders": False,
    "changes_risk": False,
    "changes_model": False,
    "runs_ocr": False,
    "imports_ocr": False,
    "promotes_quality_gated": False,
    "runs_ai_shadow_incremental": False,
    "cleans_sqlite": False,
    "runs_training": False,
    "updates_freqtrade": False,
    "updates_qlib_runtime": False,
    "auto_promote": False,
}


@dataclass(frozen=True)
class WalkForwardMonteCarloPaths:
    project_root: Path
    trade_outcomes_path: Path
    walkforward_output_path: Path
    monte_carlo_output_path: Path
    report_path: Path
    executive_report_path: Path
    summary_path: Path


@dataclass(frozen=True)
class WalkForwardMonteCarloConfig:
    min_train_rows: int = 600
    test_rows: int = 200
    embargo_rows: int = 10
    max_folds: int = 12
    monte_carlo_iterations: int = 2000
    seed: int = 42
    block_size: int = 20
    ruin_level_usdt: float = 0.0
    max_allowed_risk_of_ruin: float = 0.05
    workers: int = 10
    max_ram_gb: float = 16.0


@dataclass(frozen=True)
class WalkForwardMonteCarloResult:
    walkforward: pd.DataFrame
    monte_carlo: pd.DataFrame
    report: dict[str, Any]


def configured_workers() -> int:
    raw = os.getenv("SMARTCRYPTO_TRAINING_WORKERS", "10")
    try:
        value = int(raw)
    except ValueError:
        return 10
    return max(1, value)


def configured_max_ram_gb() -> float:
    raw = os.getenv("SMARTCRYPTO_TRAINING_MAX_RAM_GB", "16")
    try:
        value = float(raw)
    except ValueError:
        return 16.0
    return max(1.0, value)


def resolve_paths(
    project_root: str | Path,
    *,
    trade_outcomes_path: str | Path | None = None,
    walkforward_output_path: str | Path | None = None,
    monte_carlo_output_path: str | Path | None = None,
    report_path: str | Path | None = None,
    executive_report_path: str | Path | None = None,
    summary_path: str | Path | None = None,
) -> WalkForwardMonteCarloPaths:
    root = Path(project_root).resolve()

    def resolved(value: str | Path | None, default: Path) -> Path:
        path = Path(value) if value is not None else default
        if not path.is_absolute():
            path = root / path
        return path.resolve()

    return WalkForwardMonteCarloPaths(
        project_root=root,
        trade_outcomes_path=resolved(
            trade_outcomes_path,
            Path("data/research/ocr_v11_trade_outcome_simulation.parquet"),
        ),
        walkforward_output_path=resolved(
            walkforward_output_path,
            Path("data/research/ocr_v11_walkforward_results.parquet"),
        ),
        monte_carlo_output_path=resolved(
            monte_carlo_output_path,
            Path("data/research/ocr_v11_monte_carlo_distribution.parquet"),
        ),
        report_path=resolved(
            report_path,
            Path("data/reports/ocr_v11_walkforward_montecarlo_summary.json"),
        ),
        executive_report_path=resolved(
            executive_report_path,
            Path("data/reports/training_reports/ocr_v11_walkforward_montecarlo_executive.md"),
        ),
        summary_path=resolved(
            summary_path,
            Path("data/reports/training_reports/ocr_v11_walkforward_montecarlo_summary.json"),
        ),
    )


def validate_config(config: WalkForwardMonteCarloConfig) -> list[str]:
    errors: list[str] = []
    if config.min_train_rows <= 0:
        errors.append("invalid_min_train_rows")
    if config.test_rows <= 0:
        errors.append("invalid_test_rows")
    if config.embargo_rows < 0:
        errors.append("invalid_embargo_rows")
    if config.max_folds <= 0:
        errors.append("invalid_max_folds")
    if config.monte_carlo_iterations <= 0:
        errors.append("invalid_monte_carlo_iterations")
    if config.block_size <= 0:
        errors.append("invalid_block_size")
    if config.max_allowed_risk_of_ruin < 0 or config.max_allowed_risk_of_ruin > 1:
        errors.append("invalid_max_allowed_risk_of_ruin")
    if config.workers <= 0:
        errors.append("invalid_workers")
    if config.max_ram_gb <= 0:
        errors.append("invalid_max_ram_gb")
    return errors


def read_table(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)
    if path.suffix.lower() in {".csv", ".txt"}:
        return pd.read_csv(path)
    if path.suffix.lower() in {".xlsx", ".xls"}:
        return pd.read_excel(path)
    raise ValueError(f"unsupported_table_format:{path.suffix}")


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_payload = json_safe(payload)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(safe_payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")
        temp_name = handle.name
    Path(temp_name).replace(path)


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(content)
        temp_name = handle.name
    Path(temp_name).replace(path)


def atomic_write_parquet(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".parquet", dir=path.parent, delete=False) as handle:
        temp_path = Path(handle.name)
    frame.to_parquet(temp_path, index=False)
    temp_path.replace(path)


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [json_safe(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        as_float = float(value)
        if math.isnan(as_float) or math.isinf(as_float):
            return None
        return as_float
    if isinstance(value, np.ndarray):
        return json_safe(value.tolist())
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if pd.isna(value):
        return None
    return value


def normalize_outcomes(frame: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    required = {
        "trade_id",
        "symbol",
        "side",
        "open_time",
        "close_time",
        "original_net_pnl",
        "simulation_status",
        "simulated_net_pnl",
    }
    errors = [f"missing_column:{column}" for column in sorted(required - set(frame.columns))]
    if errors:
        return pd.DataFrame(), errors

    data = frame.copy()
    data["open_time"] = pd.to_datetime(data["open_time"], utc=True, errors="coerce")
    data["close_time"] = pd.to_datetime(data["close_time"], utc=True, errors="coerce")
    data["original_net_pnl"] = pd.to_numeric(data["original_net_pnl"], errors="coerce")
    data["simulated_net_pnl"] = pd.to_numeric(data["simulated_net_pnl"], errors="coerce")
    data["simulation_status"] = data["simulation_status"].astype(str)
    data["trade_id"] = data["trade_id"].astype(str)
    data["symbol"] = data["symbol"].astype(str)
    data["side"] = data["side"].astype(str)

    invalid_time_rows = int(data["open_time"].isna().sum() + data["close_time"].isna().sum())
    invalid_pnl_rows = int(data["original_net_pnl"].isna().sum() + data["simulated_net_pnl"].isna().sum())
    warnings: list[str] = []
    if invalid_time_rows:
        warnings.append(f"invalid_time_rows:{invalid_time_rows}")
    if invalid_pnl_rows:
        warnings.append(f"invalid_pnl_rows:{invalid_pnl_rows}")

    data = data.sort_values(["open_time", "trade_id"], kind="mergesort").reset_index(drop=True)
    return data, warnings


def eligible_outcomes(frame: pd.DataFrame) -> pd.DataFrame:
    mask = (
        frame["open_time"].notna()
        & frame["close_time"].notna()
        & frame["original_net_pnl"].notna()
        & frame["simulated_net_pnl"].notna()
        & (frame["simulation_status"] == "ok")
    )
    return frame.loc[mask].copy().reset_index(drop=True)


def max_drawdown_abs(values: list[float] | np.ndarray) -> float:
    pnl = np.asarray(values, dtype=float)
    if pnl.size == 0:
        return 0.0
    equity = np.cumsum(pnl)
    peak = np.maximum.accumulate(equity)
    drawdowns = peak - equity
    return float(np.max(drawdowns))


def max_consecutive_losses(values: list[float] | np.ndarray) -> int:
    max_run = 0
    current = 0
    for value in values:
        if float(value) < 0:
            current += 1
            max_run = max(max_run, current)
        else:
            current = 0
    return max_run


def financial_metrics(values: list[float] | np.ndarray) -> dict[str, float | int | None]:
    pnl = np.asarray(values, dtype=float)
    pnl = pnl[np.isfinite(pnl)]
    if pnl.size == 0:
        return {
            "trades": 0,
            "net_pnl": 0.0,
            "gross_profit": 0.0,
            "gross_loss": 0.0,
            "profit_factor": None,
            "win_rate": 0.0,
            "loss_rate": 0.0,
            "expectancy": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "payoff_ratio": None,
            "max_drawdown": 0.0,
            "max_consecutive_losses": 0,
        }

    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]
    gross_profit = float(wins.sum())
    gross_loss = float(abs(losses.sum()))
    avg_win = float(wins.mean()) if wins.size else 0.0
    avg_loss = float(abs(losses.mean())) if losses.size else 0.0

    return {
        "trades": int(pnl.size),
        "net_pnl": float(pnl.sum()),
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else None,
        "win_rate": float(wins.size / pnl.size),
        "loss_rate": float(losses.size / pnl.size),
        "expectancy": float(pnl.mean()),
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "payoff_ratio": avg_win / avg_loss if avg_loss > 0 else None,
        "max_drawdown": max_drawdown_abs(pnl),
        "max_consecutive_losses": max_consecutive_losses(pnl),
    }


def build_walkforward_results(
    outcomes: pd.DataFrame,
    config: WalkForwardMonteCarloConfig,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    total_rows = len(outcomes)

    for fold_id in range(config.max_folds):
        train_end = config.min_train_rows + fold_id * config.test_rows
        test_start = train_end + config.embargo_rows
        test_end = test_start + config.test_rows

        if test_start >= total_rows:
            break

        test_end = min(test_end, total_rows)
        if train_end <= 0 or test_end <= test_start:
            continue

        train = outcomes.iloc[:train_end]
        embargo = outcomes.iloc[train_end:test_start]
        test = outcomes.iloc[test_start:test_end]

        original_train = financial_metrics(train["original_net_pnl"].to_numpy(dtype=float))
        simulated_train = financial_metrics(train["simulated_net_pnl"].to_numpy(dtype=float))
        original_test = financial_metrics(test["original_net_pnl"].to_numpy(dtype=float))
        simulated_test = financial_metrics(test["simulated_net_pnl"].to_numpy(dtype=float))

        rows.append(
            {
                "fold_id": fold_id + 1,
                "train_start": train["open_time"].min(),
                "train_end": train["open_time"].max(),
                "test_start": test["open_time"].min(),
                "test_end": test["open_time"].max(),
                "train_rows": int(len(train)),
                "embargo_rows": int(len(embargo)),
                "test_rows": int(len(test)),
                "purged_rows": int(len(embargo)),
                "original_train_net_pnl": original_train["net_pnl"],
                "simulated_train_net_pnl": simulated_train["net_pnl"],
                "original_test_net_pnl": original_test["net_pnl"],
                "simulated_test_net_pnl": simulated_test["net_pnl"],
                "test_net_pnl_delta_vs_original": float(simulated_test["net_pnl"] or 0.0)
                - float(original_test["net_pnl"] or 0.0),
                "original_test_profit_factor": original_test["profit_factor"],
                "simulated_test_profit_factor": simulated_test["profit_factor"],
                "original_test_win_rate": original_test["win_rate"],
                "simulated_test_win_rate": simulated_test["win_rate"],
                "original_test_max_drawdown": original_test["max_drawdown"],
                "simulated_test_max_drawdown": simulated_test["max_drawdown"],
                "original_test_expectancy": original_test["expectancy"],
                "simulated_test_expectancy": simulated_test["expectancy"],
            }
        )

    return pd.DataFrame(rows)


def _sample_block_bootstrap(
    pnl: np.ndarray,
    *,
    rng: np.random.Generator,
    block_size: int,
) -> np.ndarray:
    if pnl.size == 0:
        return pnl.copy()

    values: list[float] = []
    while len(values) < pnl.size:
        start = int(rng.integers(0, pnl.size))
        end = min(start + block_size, pnl.size)
        values.extend(float(item) for item in pnl[start:end])

    return np.asarray(values[: pnl.size], dtype=float)


def run_monte_carlo_distribution(
    pnl_values: np.ndarray,
    *,
    config: WalkForwardMonteCarloConfig,
) -> pd.DataFrame:
    pnl = np.asarray(pnl_values, dtype=float)
    pnl = pnl[np.isfinite(pnl)]

    rows: list[dict[str, Any]] = []
    rng = np.random.default_rng(config.seed)

    for method in ("shuffle", "bootstrap", "block_bootstrap"):
        for iteration in range(config.monte_carlo_iterations):
            if method == "shuffle":
                sampled = rng.permutation(pnl)
            elif method == "bootstrap":
                sampled = rng.choice(pnl, size=pnl.size, replace=True)
            else:
                sampled = _sample_block_bootstrap(pnl, rng=rng, block_size=config.block_size)

            final_net_pnl = float(sampled.sum())
            rows.append(
                {
                    "method": method,
                    "iteration": iteration + 1,
                    "final_net_pnl": final_net_pnl,
                    "max_drawdown": max_drawdown_abs(sampled),
                    "max_consecutive_losses": max_consecutive_losses(sampled),
                    "risk_of_ruin_hit": bool(final_net_pnl <= config.ruin_level_usdt),
                }
            )

    return pd.DataFrame(rows)


def monte_carlo_summary(distribution: pd.DataFrame) -> dict[str, Any]:
    if distribution.empty:
        return {
            "iterations": 0,
            "methods": [],
            "final_net_pnl_p05": None,
            "final_net_pnl_p50": None,
            "final_net_pnl_p95": None,
            "max_drawdown_p95": None,
            "worst_max_drawdown": None,
            "risk_of_ruin": None,
        }

    final = pd.to_numeric(distribution["final_net_pnl"], errors="coerce").dropna()
    drawdown = pd.to_numeric(distribution["max_drawdown"], errors="coerce").dropna()
    ruin = distribution["risk_of_ruin_hit"].astype(bool)

    return {
        "iterations": int(len(distribution)),
        "methods": sorted(str(item) for item in distribution["method"].dropna().unique()),
        "final_net_pnl_p05": float(final.quantile(0.05)) if not final.empty else None,
        "final_net_pnl_p50": float(final.quantile(0.50)) if not final.empty else None,
        "final_net_pnl_p95": float(final.quantile(0.95)) if not final.empty else None,
        "max_drawdown_p95": float(drawdown.quantile(0.95)) if not drawdown.empty else None,
        "worst_max_drawdown": float(drawdown.max()) if not drawdown.empty else None,
        "risk_of_ruin": float(ruin.mean()) if len(ruin) else None,
    }


def build_executive_summary(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": "Relatório Executivo — Walk-forward e Monte Carlo OCR V1.1",
        "analysis_date_utc": report["analysis_date_utc"],
        "status": report["status"],
        "reason": report["reason"],
        "trade_outcomes_rows": report["trade_outcomes_rows"],
        "eligible_rows": report["eligible_rows"],
        "walkforward_folds": report["walkforward_folds"],
        "original_walkforward_net_pnl": report["original_walkforward_net_pnl"],
        "candidate_walkforward_net_pnl": report["candidate_walkforward_net_pnl"],
        "walkforward_delta_vs_original": report["walkforward_delta_vs_original"],
        "monte_carlo_final_net_pnl_p05": report["monte_carlo"]["final_net_pnl_p05"],
        "monte_carlo_final_net_pnl_p50": report["monte_carlo"]["final_net_pnl_p50"],
        "monte_carlo_final_net_pnl_p95": report["monte_carlo"]["final_net_pnl_p95"],
        "monte_carlo_risk_of_ruin": report["monte_carlo"]["risk_of_ruin"],
        "recommendation": report["recommendation"],
        "decision": report["decision"],
    }


def render_executive_markdown(summary: dict[str, Any]) -> str:
    return f"""# {summary["title"]}

Data UTC: `{summary["analysis_date_utc"]}`

## 1. Veredito

**Status:** `{summary["status"]}`  
**Motivo:** `{summary["reason"]}`  
**Decisão:** `{summary["decision"]}`

## 2. Base analisada

| Item | Valor |
|---|---:|
| Outcomes analisados | {summary["trade_outcomes_rows"]} |
| Linhas elegíveis | {summary["eligible_rows"]} |
| Folds walk-forward | {summary["walkforward_folds"]} |

## 3. Comparação fora da amostra

| Métrica | Valor |
|---|---:|
| PnL original walk-forward | {summary["original_walkforward_net_pnl"]:.6f} |
| PnL candidato walk-forward | {summary["candidate_walkforward_net_pnl"]:.6f} |
| Delta candidato vs original | {summary["walkforward_delta_vs_original"]:.6f} |

## 4. Monte Carlo

| Métrica | Valor |
|---|---:|
| P05 final net PnL | {summary["monte_carlo_final_net_pnl_p05"]} |
| P50 final net PnL | {summary["monte_carlo_final_net_pnl_p50"]} |
| P95 final net PnL | {summary["monte_carlo_final_net_pnl_p95"]} |
| Risco de ruína | {summary["monte_carlo_risk_of_ruin"]} |

## 5. Conclusão executiva

{summary["recommendation"]}

Observação: esta análise é `research-only`. Ela não treina Qlib, não altera Freqtrade, não altera RiskManager, não roda IA Shadow incremental e não promove modelo ou estratégia.
"""


def base_report(
    paths: WalkForwardMonteCarloPaths,
    config: WalkForwardMonteCarloConfig,
    *,
    write: bool,
    analysis_date_utc: str,
) -> dict[str, Any]:
    return {
        "status": "blocked",
        "reason": "not_run",
        "analysis_date_utc": analysis_date_utc,
        "trade_outcomes_path": str(paths.trade_outcomes_path),
        "walkforward_output_path": str(paths.walkforward_output_path),
        "monte_carlo_output_path": str(paths.monte_carlo_output_path),
        "report_path": str(paths.report_path),
        "executive_report_path": str(paths.executive_report_path),
        "summary_path": str(paths.summary_path),
        "write_requested": bool(write),
        "write_performed": False,
        "configured_workers": int(config.workers),
        "configured_max_ram_gb": float(config.max_ram_gb),
        "min_train_rows": int(config.min_train_rows),
        "test_rows": int(config.test_rows),
        "embargo_rows": int(config.embargo_rows),
        "max_folds": int(config.max_folds),
        "monte_carlo_iterations": int(config.monte_carlo_iterations),
        "seed": int(config.seed),
        "block_size": int(config.block_size),
        "ruin_level_usdt": float(config.ruin_level_usdt),
        "max_allowed_risk_of_ruin": float(config.max_allowed_risk_of_ruin),
        "validation_errors": [],
        "warnings": [],
        **SAFETY_FLAGS,
    }


def run_walkforward_montecarlo(
    paths: WalkForwardMonteCarloPaths,
    config: WalkForwardMonteCarloConfig,
    *,
    write: bool = False,
    analysis_date_utc: str | None = None,
) -> WalkForwardMonteCarloResult:
    analysis_date = analysis_date_utc or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    report = base_report(paths, config, write=write, analysis_date_utc=analysis_date)

    config_errors = validate_config(config)
    if config_errors:
        report["status"] = "blocked"
        report["reason"] = "invalid_config"
        report["validation_errors"] = config_errors
        return WalkForwardMonteCarloResult(pd.DataFrame(), pd.DataFrame(), report)

    if not paths.trade_outcomes_path.exists():
        report["status"] = "blocked"
        report["reason"] = "missing_trade_outcomes"
        report["validation_errors"] = ["missing_trade_outcomes"]
        return WalkForwardMonteCarloResult(pd.DataFrame(), pd.DataFrame(), report)

    raw = read_table(paths.trade_outcomes_path)
    outcomes, normalize_warnings = normalize_outcomes(raw)
    report["warnings"] = normalize_warnings
    report["trade_outcomes_rows"] = int(len(raw))

    if outcomes.empty:
        report["status"] = "blocked"
        report["reason"] = "invalid_trade_outcomes_schema"
        report["validation_errors"] = normalize_warnings
        return WalkForwardMonteCarloResult(pd.DataFrame(), pd.DataFrame(), report)

    eligible = eligible_outcomes(outcomes)
    report["eligible_rows"] = int(len(eligible))
    report["blocked_rows"] = int(len(outcomes) - len(eligible))

    minimum_required = config.min_train_rows + config.embargo_rows + config.test_rows
    if len(eligible) < minimum_required:
        report["status"] = "blocked"
        report["reason"] = "insufficient_eligible_rows"
        report["validation_errors"] = [f"minimum_required_rows:{minimum_required}"]
        return WalkForwardMonteCarloResult(pd.DataFrame(), pd.DataFrame(), report)

    walkforward = build_walkforward_results(eligible, config)
    if walkforward.empty:
        report["status"] = "blocked"
        report["reason"] = "no_walkforward_folds"
        report["validation_errors"] = ["no_walkforward_folds"]
        return WalkForwardMonteCarloResult(walkforward, pd.DataFrame(), report)

    monte_carlo = run_monte_carlo_distribution(
        eligible["simulated_net_pnl"].to_numpy(dtype=float),
        config=config,
    )

    original_wf_net = float(pd.to_numeric(walkforward["original_test_net_pnl"], errors="coerce").sum())
    candidate_wf_net = float(pd.to_numeric(walkforward["simulated_test_net_pnl"], errors="coerce").sum())
    delta = candidate_wf_net - original_wf_net
    mc_summary = monte_carlo_summary(monte_carlo)
    risk_of_ruin = mc_summary["risk_of_ruin"]

    if candidate_wf_net <= original_wf_net:
        status = "blocked"
        reason = "candidate_does_not_beat_original_walkforward"
        decision = "DESCARTAR_CANDIDATO"
        recommendation = (
            "O candidato não superou o resultado original fora da amostra. "
            "A estratégia deve permanecer bloqueada para promoção e o próximo passo deve ser testar outras famílias de estratégia."
        )
    elif risk_of_ruin is not None and risk_of_ruin > config.max_allowed_risk_of_ruin:
        status = "blocked"
        reason = "monte_carlo_risk_of_ruin_above_limit"
        decision = "MANTER_EM_SHADOW"
        recommendation = (
            "O candidato superou o original no walk-forward, mas o risco de ruína excedeu o limite definido. "
            "Manter em shadow e revisar risco, custos e sizing."
        )
    else:
        status = "ok"
        reason = "walkforward_montecarlo_candidate_passed_research_gate"
        decision = "APTO_PARA_SHADOW"
        recommendation = (
            "O candidato passou no gate de pesquisa. A próxima etapa é validação shadow/champion-challenger, "
            "sem promoção automática e sem alteração operacional."
        )

    report.update(
        {
            "status": status,
            "reason": reason,
            "decision": decision,
            "recommendation": recommendation,
            "walkforward_folds": int(len(walkforward)),
            "original_walkforward_net_pnl": original_wf_net,
            "candidate_walkforward_net_pnl": candidate_wf_net,
            "walkforward_delta_vs_original": delta,
            "walkforward_min_test_start": walkforward["test_start"].min(),
            "walkforward_max_test_end": walkforward["test_end"].max(),
            "monte_carlo": mc_summary,
        }
    )

    executive_summary = build_executive_summary(report)
    executive_markdown = render_executive_markdown(executive_summary)

    if write:
        atomic_write_parquet(paths.walkforward_output_path, walkforward)
        atomic_write_parquet(paths.monte_carlo_output_path, monte_carlo)
        atomic_write_json(paths.report_path, report)
        atomic_write_json(paths.summary_path, executive_summary)
        atomic_write_text(paths.executive_report_path, executive_markdown)
        report["write_performed"] = True
        atomic_write_json(paths.report_path, report)

    return WalkForwardMonteCarloResult(walkforward, monte_carlo, report)
