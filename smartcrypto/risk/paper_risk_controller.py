from __future__ import annotations

import csv
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    import yaml
except Exception:  # pragma: no cover - exercised only in minimal runtimes without PyYAML.
    yaml = None  # type: ignore[assignment]


TRUE_VALUES = {"1", "true", "yes", "y", "on"}
SAFE_RUNTIME_MODES = {"paper", "research", "shadow"}

DEFAULT_INPUTS = (
    "data/features/training_dataset_v13_candle_structure_real.parquet",
    "data/features/training_dataset_model_ready_v7_compat.parquet",
    "data/features/training_dataset_model_ready.parquet",
)

DEFAULT_OUTPUTS = {
    "daily_summary_json": "data/reports/paper_risk_controller_daily_summary.json",
    "daily_trades_csv": "data/reports/paper_risk_controller_daily_trades.csv",
    "equity_csv": "data/reports/paper_risk_controller_equity.csv",
    "state_json": "data/runtime/paper_risk_controller_state.json",
}


class PaperRiskControllerError(RuntimeError):
    """Base error for the paper risk controller."""


class SafetyViolation(PaperRiskControllerError):
    """Raised when a live/real order path is requested."""


class InputDataError(PaperRiskControllerError):
    """Raised when the local paper dataset cannot be read or normalized."""


@dataclass(frozen=True)
class PaperRiskPolicy:
    name: str = "btc_075_eth_100_daily_stop_25"
    multipliers: dict[str, float] = field(
        default_factory=lambda: {"BTCUSDT": 0.75, "ETHUSDT": 1.0}
    )
    default_multiplier: float = 1.0
    daily_emergency_stop_usdt: float = -25.0
    cooldown_enabled: bool = False
    order_submission_enabled: bool = False
    real_order_submission_enabled: bool = False
    runtime_mode: str = "paper"

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "PaperRiskPolicy":
        policy = config.get("policy", {}) if isinstance(config.get("policy"), dict) else {}
        safety = config.get("safety", {}) if isinstance(config.get("safety"), dict) else {}
        runtime_mode = str(config.get("runtime_mode", policy.get("runtime_mode", "paper")))

        multipliers = {
            normalize_symbol(symbol): float(multiplier)
            for symbol, multiplier in dict(
                policy.get("multipliers", {"BTCUSDT": 0.75, "ETHUSDT": 1.0})
            ).items()
        }

        return cls(
            name=str(policy.get("name", "btc_075_eth_100_daily_stop_25")),
            multipliers=multipliers,
            default_multiplier=float(policy.get("default_multiplier", 1.0)),
            daily_emergency_stop_usdt=float(
                policy.get("daily_emergency_stop_usdt", -25.0)
            ),
            cooldown_enabled=bool(policy.get("cooldown_enabled", False)),
            order_submission_enabled=bool(
                safety.get(
                    "order_submission_enabled",
                    policy.get("order_submission_enabled", False),
                )
            ),
            real_order_submission_enabled=bool(
                safety.get(
                    "real_order_submission_enabled",
                    policy.get("real_order_submission_enabled", False),
                )
            ),
            runtime_mode=runtime_mode,
        )

    def validate(self) -> None:
        mode = normalize_mode(self.runtime_mode)
        if mode not in SAFE_RUNTIME_MODES:
            raise SafetyViolation(
                f"runtime_mode inseguro para este controller: {self.runtime_mode}"
            )
        if self.order_submission_enabled:
            raise SafetyViolation("order_submission_enabled deve permanecer false")
        if self.real_order_submission_enabled:
            raise SafetyViolation("real_order_submission_enabled deve permanecer false")
        if self.cooldown_enabled:
            raise SafetyViolation("cooldown_enabled deve permanecer false nesta politica inicial")
        if self.default_multiplier > 1.0:
            raise SafetyViolation("default_multiplier nao pode aumentar risco acima de 1.0")
        risky = {symbol: value for symbol, value in self.multipliers.items() if value > 1.0}
        if risky:
            raise SafetyViolation(f"multipliers nao podem aumentar risco acima de 1.0: {risky}")


@dataclass(frozen=True)
class PaperRiskControllerPaths:
    input_paths: tuple[str, ...] = DEFAULT_INPUTS
    daily_summary_json: str = DEFAULT_OUTPUTS["daily_summary_json"]
    daily_trades_csv: str = DEFAULT_OUTPUTS["daily_trades_csv"]
    equity_csv: str = DEFAULT_OUTPUTS["equity_csv"]
    state_json: str = DEFAULT_OUTPUTS["state_json"]

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "PaperRiskControllerPaths":
        inputs = config.get("inputs", {}) if isinstance(config.get("inputs"), dict) else {}
        outputs = config.get("outputs", {}) if isinstance(config.get("outputs"), dict) else {}

        configured_inputs = inputs.get("default_paths", DEFAULT_INPUTS)
        if isinstance(configured_inputs, (str, Path)):
            configured_inputs = [str(configured_inputs)]

        return cls(
            input_paths=tuple(str(path) for path in configured_inputs),
            daily_summary_json=str(
                outputs.get("daily_summary_json", DEFAULT_OUTPUTS["daily_summary_json"])
            ),
            daily_trades_csv=str(
                outputs.get("daily_trades_csv", DEFAULT_OUTPUTS["daily_trades_csv"])
            ),
            equity_csv=str(outputs.get("equity_csv", DEFAULT_OUTPUTS["equity_csv"])),
            state_json=str(outputs.get("state_json", DEFAULT_OUTPUTS["state_json"])),
        )


@dataclass(frozen=True)
class NormalizedTrade:
    input_index: int
    opened_at: datetime
    symbol: str
    side: str
    raw_pnl_usdt: float


@dataclass(frozen=True)
class PaperTradeResult:
    input_index: int
    opened_at: str
    trade_day: str
    symbol: str
    side: str
    raw_pnl_usdt: float
    risk_multiplier: float
    paper_pnl_usdt: float
    accepted: bool
    reason: str
    daily_raw_pnl_after: float
    daily_paper_pnl_after: float
    raw_equity: float
    paper_equity: float


@dataclass(frozen=True)
class PaperRiskRunResult:
    status: str
    mode: str
    input_path: str
    rows_seen: int
    rows_used: int
    generated_at: str
    policy: dict[str, Any]
    raw_net_pnl_usdt: float
    paper_net_pnl_usdt: float
    raw_max_drawdown_usdt: float
    paper_max_drawdown_usdt: float
    accepted_trades: int
    skipped_trades: int
    emergency_stop_days: list[str]
    daily: list[dict[str, Any]]
    outputs: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat()


def normalize_mode(value: Any) -> str:
    return str(value or "").strip().lower()


def normalize_symbol(value: Any) -> str:
    return str(value or "").upper().replace("/", "").replace(":USDT", "").strip()


def normalize_side(value: Any) -> str:
    side = str(value or "").upper().strip()
    if side in {"LONG", "BUY", "COMPRA"}:
        return "LONG"
    if side in {"SHORT", "SELL", "VENDA"}:
        return "SHORT"
    return "UNKNOWN"


def parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def env_enabled(name: str) -> bool:
    return str(os.getenv(name, "")).strip().lower() in TRUE_VALUES


def assert_environment_safe() -> None:
    if env_enabled("LIVE_ENABLED"):
        raise SafetyViolation("LIVE_ENABLED=true bloqueado para paper risk controller")
    if env_enabled("ORDER_SUBMISSION_ENABLED"):
        raise SafetyViolation("ORDER_SUBMISSION_ENABLED=true bloqueado")
    if env_enabled("REAL_ORDER_SUBMISSION_ENABLED"):
        raise SafetyViolation("REAL_ORDER_SUBMISSION_ENABLED=true bloqueado")


def load_yaml_config(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        return {}
    if yaml is None:
        raise InputDataError("PyYAML nao esta instalado para ler arquivo de config YAML")
    with target.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise InputDataError(f"Config YAML invalida: {target}")
    return data


def pick_input_path(explicit_input: str | Path | None, candidates: Iterable[str | Path]) -> Path:
    if explicit_input:
        target = Path(explicit_input)
        if not target.exists():
            raise FileNotFoundError(f"Dataset nao encontrado: {target}")
        return target

    for candidate in candidates:
        target = Path(candidate)
        if target.exists():
            return target

    joined = ", ".join(str(path) for path in candidates)
    raise FileNotFoundError(f"Nenhum dataset local encontrado. Candidatos: {joined}")


def read_trade_rows(path: str | Path) -> list[dict[str, Any]]:
    target = Path(path)
    suffix = target.suffix.lower()
    try:
        if suffix == ".csv":
            with target.open("r", encoding="utf-8-sig", newline="") as handle:
                return [dict(row) for row in csv.DictReader(handle)]
        if suffix == ".json":
            payload = json.loads(target.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                return [row for row in payload if isinstance(row, dict)]
            if isinstance(payload, dict):
                for key in ("trades", "data", "rows"):
                    rows = payload.get(key)
                    if isinstance(rows, list):
                        return [row for row in rows if isinstance(row, dict)]
            raise InputDataError(f"JSON sem lista de trades reconhecida: {target}")
        if suffix == ".parquet":
            return read_parquet_rows(target)
    except PaperRiskControllerError:
        raise
    except Exception as exc:
        raise InputDataError(f"Falha ao ler dataset {target}: {exc}") from exc

    raise InputDataError(f"Formato nao suportado para trades locais: {target.suffix}")


def read_parquet_rows(path: Path) -> list[dict[str, Any]]:
    try:
        import pandas as pd  # type: ignore
    except Exception as exc:
        raise InputDataError(
            "Leitura Parquet requer pandas/pyarrow instalados no ambiente local"
        ) from exc

    frame = pd.read_parquet(path)
    return frame.to_dict(orient="records")


def first_present(row: dict[str, Any], candidates: tuple[str, ...]) -> Any:
    for candidate in candidates:
        if candidate in row:
            return row[candidate]
    return None


def normalize_trades(rows: list[dict[str, Any]]) -> list[NormalizedTrade]:
    trades: list[NormalizedTrade] = []
    missing_required = 0
    for index, row in enumerate(rows):
        opened_at = parse_datetime(
            first_present(
                row,
                (
                    "open_time_utc",
                    "close_time_utc",
                    "close_date",
                    "timestamp",
                    "datetime",
                    "date",
                    "time",
                ),
            )
        )
        symbol = normalize_symbol(first_present(row, ("symbol", "pair", "ticker", "asset")))
        side = normalize_side(first_present(row, ("side", "direction", "trade_side")))
        raw_pnl = to_float(
            first_present(
                row,
                (
                    "reported_pnl_usdt",
                    "raw_pnl_usdt",
                    "pnl_usdt",
                    "profit_usdt",
                    "profit_abs",
                    "close_profit_abs",
                    "realized_profit",
                ),
            )
        )

        if opened_at is None or not symbol or raw_pnl is None:
            missing_required += 1
            continue

        trades.append(
            NormalizedTrade(
                input_index=index,
                opened_at=opened_at,
                symbol=symbol,
                side=side,
                raw_pnl_usdt=raw_pnl,
            )
        )

    if not trades and rows:
        raise InputDataError(
            "Nenhuma linha valida encontrada; requer timestamp, symbol/pair e PnL em USDT"
        )
    if missing_required and not trades:
        raise InputDataError("Todas as linhas foram descartadas por campos obrigatorios ausentes")

    return sorted(trades, key=lambda trade: (trade.opened_at, trade.input_index))


def max_drawdown(values: list[float]) -> float:
    if not values:
        return 0.0
    peak = 0.0
    worst = 0.0
    for value in values:
        peak = max(peak, value)
        worst = min(worst, value - peak)
    return float(worst)


def simulate_paper_risk(
    trades: list[NormalizedTrade],
    policy: PaperRiskPolicy,
) -> tuple[list[PaperTradeResult], list[dict[str, Any]]]:
    policy.validate()
    threshold = -abs(float(policy.daily_emergency_stop_usdt))
    daily_raw: dict[str, float] = {}
    daily_paper: dict[str, float] = {}
    daily_stop_active: dict[str, bool] = {}
    daily_counts: dict[str, dict[str, Any]] = {}
    raw_equity = 0.0
    paper_equity = 0.0
    results: list[PaperTradeResult] = []

    for trade in trades:
        day = trade.opened_at.date().isoformat()
        daily_raw.setdefault(day, 0.0)
        daily_paper.setdefault(day, 0.0)
        daily_stop_active.setdefault(day, False)
        daily_counts.setdefault(
            day,
            {
                "trade_day": day,
                "trades": 0,
                "accepted_trades": 0,
                "skipped_trades": 0,
                "raw_pnl_usdt": 0.0,
                "paper_pnl_usdt": 0.0,
                "emergency_stop_triggered": False,
            },
        )

        multiplier = float(policy.multipliers.get(trade.symbol, policy.default_multiplier))
        accepted = not daily_stop_active[day]
        reason = "accepted" if accepted else "daily_emergency_stop"
        paper_pnl = trade.raw_pnl_usdt * multiplier if accepted else 0.0

        raw_equity += trade.raw_pnl_usdt
        paper_equity += paper_pnl
        daily_raw[day] += trade.raw_pnl_usdt
        daily_paper[day] += paper_pnl

        if accepted and daily_paper[day] <= threshold:
            daily_stop_active[day] = True
            daily_counts[day]["emergency_stop_triggered"] = True

        counts = daily_counts[day]
        counts["trades"] += 1
        counts["raw_pnl_usdt"] = round(daily_raw[day], 10)
        counts["paper_pnl_usdt"] = round(daily_paper[day], 10)
        counts["accepted_trades"] += int(accepted)
        counts["skipped_trades"] += int(not accepted)

        results.append(
            PaperTradeResult(
                input_index=trade.input_index,
                opened_at=trade.opened_at.isoformat(),
                trade_day=day,
                symbol=trade.symbol,
                side=trade.side,
                raw_pnl_usdt=float(trade.raw_pnl_usdt),
                risk_multiplier=multiplier,
                paper_pnl_usdt=float(paper_pnl),
                accepted=accepted,
                reason=reason,
                daily_raw_pnl_after=float(daily_raw[day]),
                daily_paper_pnl_after=float(daily_paper[day]),
                raw_equity=float(raw_equity),
                paper_equity=float(paper_equity),
            )
        )

    return results, [daily_counts[day] for day in sorted(daily_counts)]


def write_json(path: str | Path, payload: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".tmp")
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    temp.write_text(encoded, encoding="utf-8")
    temp.replace(target)


def write_csv(path: str | Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def build_run_result(
    input_path: Path,
    rows_seen: int,
    trades: list[NormalizedTrade],
    trade_results: list[PaperTradeResult],
    daily: list[dict[str, Any]],
    policy: PaperRiskPolicy,
    outputs: PaperRiskControllerPaths,
) -> PaperRiskRunResult:
    raw_equity_curve = [row.raw_equity for row in trade_results]
    paper_equity_curve = [row.paper_equity for row in trade_results]
    accepted = sum(1 for row in trade_results if row.accepted)
    skipped = len(trade_results) - accepted
    emergency_days = [
        str(row["trade_day"]) for row in daily if row.get("emergency_stop_triggered") is True
    ]

    return PaperRiskRunResult(
        status="ok",
        mode="paper_risk_controller_shadow",
        input_path=str(input_path),
        rows_seen=int(rows_seen),
        rows_used=int(len(trades)),
        generated_at=iso_now(),
        policy=asdict(policy),
        raw_net_pnl_usdt=float(sum(row.raw_pnl_usdt for row in trade_results)),
        paper_net_pnl_usdt=float(sum(row.paper_pnl_usdt for row in trade_results)),
        raw_max_drawdown_usdt=max_drawdown(raw_equity_curve),
        paper_max_drawdown_usdt=max_drawdown(paper_equity_curve),
        accepted_trades=accepted,
        skipped_trades=skipped,
        emergency_stop_days=emergency_days,
        daily=daily,
        outputs={
            "daily_summary_json": outputs.daily_summary_json,
            "daily_trades_csv": outputs.daily_trades_csv,
            "equity_csv": outputs.equity_csv,
            "state_json": outputs.state_json,
        },
    )


def run_paper_risk_controller(
    config_path: str | Path = "config/paper_risk_controller.example.yml",
    input_path: str | Path | None = None,
    since: str | None = None,
    write_outputs: bool = True,
) -> PaperRiskRunResult:
    assert_environment_safe()
    config = load_yaml_config(config_path)
    policy = PaperRiskPolicy.from_config(config)
    paths = PaperRiskControllerPaths.from_config(config)
    policy.validate()

    selected_input = pick_input_path(input_path, paths.input_paths)
    rows = read_trade_rows(selected_input)
    trades = normalize_trades(rows)

    if since:
        since_dt = parse_datetime(since)
        if since_dt is None:
            raise InputDataError(f"--since invalido: {since}")
        trades = [trade for trade in trades if trade.opened_at >= since_dt]

    trade_results, daily = simulate_paper_risk(trades, policy)
    result = build_run_result(
        selected_input, len(rows), trades, trade_results, daily, policy, paths
    )

    if write_outputs:
        trade_rows = [asdict(row) for row in trade_results]
        write_json(paths.daily_summary_json, result.to_dict())
        write_csv(
            paths.daily_trades_csv,
            trade_rows,
            [
                "input_index",
                "opened_at",
                "trade_day",
                "symbol",
                "side",
                "raw_pnl_usdt",
                "risk_multiplier",
                "paper_pnl_usdt",
                "accepted",
                "reason",
                "daily_raw_pnl_after",
                "daily_paper_pnl_after",
                "raw_equity",
                "paper_equity",
            ],
        )
        write_csv(
            paths.equity_csv,
            [
                {
                    "opened_at": row.opened_at,
                    "trade_day": row.trade_day,
                    "symbol": row.symbol,
                    "raw_equity": row.raw_equity,
                    "paper_equity": row.paper_equity,
                }
                for row in trade_results
            ],
            ["opened_at", "trade_day", "symbol", "raw_equity", "paper_equity"],
        )
        write_json(
            paths.state_json,
            {
                "last_run_status": result.status,
                "mode": result.mode,
                "last_input_path": result.input_path,
                "rows_seen": result.rows_seen,
                "rows_used": result.rows_used,
                "accepted_trades": result.accepted_trades,
                "skipped_trades": result.skipped_trades,
                "emergency_stop_days": result.emergency_stop_days,
                "policy": result.policy,
                "updated_at": result.generated_at,
            },
        )

    return result
