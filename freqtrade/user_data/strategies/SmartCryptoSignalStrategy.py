from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

import pandas as pd
from freqtrade.constants import CANCEL_REASON
from freqtrade.strategy import IStrategy

if TYPE_CHECKING:
    from freqtrade.persistence import Trade


class _PaperExitGuardDecision(NamedTuple):
    allowed: bool
    reason: str


class SmartCryptoSignalStrategy(IStrategy):
    timeframe = "5m"
    can_short = True
    minimal_roi = {"0": 0.02}
    stoploss = -0.015
    process_only_new_candles = True
    startup_candle_count = 50
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False
    trailing_stop = False

    _paper_exit_retry_reason = "paper_exit_retry_latched"
    _paper_exit_retry_source_tags = frozenset(
        {
            "roi",
            "exit_signal",
            "phase15_controlled_paper_exit",
            "paper_exit_retry_latched",
        }
    )
    _paper_exit_retry_cancel_states = frozenset(
        {"cancelled", "canceled", "expired", "closed"}
    )
    _paper_exit_retry_timeout_reason = CANCEL_REASON["TIMEOUT"]
    _paper_exit_guard_critical_reasons = frozenset(
        {
            "stop_loss",
            "stoploss_on_exchange",
            "trailing_stop_loss",
            "liquidation",
            "emergency_exit",
            "force_exit",
            "partial_exit",
            "sold_on_exchange",
        }
    )

    _signal_paths = [
        Path("/freqtrade/user_data/data/runtime/active_freqtrade_signals.json"),
        Path("/freqtrade/user_data/data/freqtrade_signals.json"),
        Path("/freqtrade/user_data/freqtrade_signals.json"),
        Path("data/runtime/active_freqtrade_signals.json"),
        Path("data/freqtrade_signals.json"),
    ]

    _exit_control_paths = [
        Path("/freqtrade/user_data/data/runtime/paper_exit_control.json"),
        Path("/freqtrade/user_data/data/paper_exit_control.json"),
        Path("/freqtrade/user_data/paper_exit_control.json"),
        Path("data/runtime/paper_exit_control.json"),
    ]

    _decision_log_paths = [
        Path("/freqtrade/user_data/data/runtime/freqtrade_signal_decisions.jsonl"),
        Path("data/runtime/freqtrade_signal_decisions.jsonl"),
    ]

    def populate_indicators(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        pair = metadata.get("pair", "")
        payload = self._find_signal_for_pair(pair)
        exit_payload = self._find_exit_control_for_pair(pair)

        dataframe["smartcrypto_signal_side"] = None
        dataframe["smartcrypto_signal_confidence"] = 0.0
        dataframe["smartcrypto_decision_event_id"] = None
        dataframe["smartcrypto_signal_id"] = None
        dataframe["smartcrypto_correlation_id"] = None
        dataframe["smartcrypto_exit_requested"] = False

        if len(dataframe.index) == 0:
            return dataframe

        last_index = dataframe.index[-1]

        if payload["accepted"]:
            dataframe.at[last_index, "smartcrypto_signal_side"] = payload["side"]
            dataframe.at[last_index, "smartcrypto_signal_confidence"] = payload["confidence"]
            dataframe.at[last_index, "smartcrypto_decision_event_id"] = payload.get(
                "decision_event_id"
            )
            dataframe.at[last_index, "smartcrypto_signal_id"] = payload.get("signal_id")
            dataframe.at[last_index, "smartcrypto_correlation_id"] = payload.get(
                "correlation_id"
            )

        if exit_payload["accepted"]:
            dataframe.at[last_index, "smartcrypto_exit_requested"] = True

        self._write_decision(
            {
                "event": "populate_indicators",
                "pair": pair,
                "accepted": bool(payload["accepted"]),
                "side": payload.get("side"),
                "confidence": payload.get("confidence"),
                "reason": payload.get("reason"),
                "lookup": payload.get("lookup"),
                "decision_event_id": payload.get("decision_event_id"),
                "signal_id": payload.get("signal_id"),
                "correlation_id": payload.get("correlation_id"),
                "exit_requested": bool(exit_payload["accepted"]),
                "exit_reason": exit_payload.get("reason"),
                "ts": self._now_iso(),
            }
        )

        return dataframe

    def populate_entry_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        dataframe["enter_long"] = 0
        dataframe["enter_short"] = 0
        dataframe["enter_tag"] = None

        if len(dataframe.index) == 0:
            return dataframe

        pair = metadata.get("pair", "")
        last_index = dataframe.index[-1]
        side = dataframe.at[last_index, "smartcrypto_signal_side"] if "smartcrypto_signal_side" in dataframe else None
        decision_event_id = (
            dataframe.at[last_index, "smartcrypto_decision_event_id"]
            if "smartcrypto_decision_event_id" in dataframe
            else None
        )
        signal_id = (
            dataframe.at[last_index, "smartcrypto_signal_id"]
            if "smartcrypto_signal_id" in dataframe
            else None
        )
        correlation_id = (
            dataframe.at[last_index, "smartcrypto_correlation_id"]
            if "smartcrypto_correlation_id" in dataframe
            else None
        )

        if side == "long":
            dataframe.at[last_index, "enter_long"] = 1
            dataframe.at[last_index, "enter_tag"] = self._entry_tag(
                "long", decision_event_id
            )
            self._write_decision(
                {
                    "event": "populate_entry_trend",
                    "pair": pair,
                    "accepted": True,
                    "side": "long",
                    "reason": "entry_signal_set",
                    "decision_event_id": decision_event_id,
                    "signal_id": signal_id,
                    "correlation_id": correlation_id,
                    "ts": self._now_iso(),
                }
            )

        if side == "short":
            dataframe.at[last_index, "enter_short"] = 1
            dataframe.at[last_index, "enter_tag"] = self._entry_tag(
                "short", decision_event_id
            )
            self._write_decision(
                {
                    "event": "populate_entry_trend",
                    "pair": pair,
                    "accepted": True,
                    "side": "short",
                    "reason": "entry_signal_set",
                    "decision_event_id": decision_event_id,
                    "signal_id": signal_id,
                    "correlation_id": correlation_id,
                    "ts": self._now_iso(),
                }
            )

        return dataframe

    def populate_exit_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        dataframe["exit_long"] = 0
        dataframe["exit_short"] = 0
        dataframe["exit_tag"] = None

        if len(dataframe.index) == 0:
            return dataframe

        pair = metadata.get("pair", "")
        last_index = dataframe.index[-1]
        exit_requested = bool(dataframe.at[last_index, "smartcrypto_exit_requested"]) if "smartcrypto_exit_requested" in dataframe else False

        if exit_requested:
            dataframe.at[last_index, "exit_long"] = 1
            dataframe.at[last_index, "exit_short"] = 1
            dataframe.at[last_index, "exit_tag"] = "phase15_controlled_paper_exit"
            self._write_decision(
                {
                    "event": "populate_exit_trend",
                    "pair": pair,
                    "accepted": True,
                    "side": "all",
                    "reason": "phase15_controlled_paper_exit",
                    "ts": self._now_iso(),
                }
            )

        return dataframe

    def leverage(
        self,
        pair: str,
        current_time: datetime,
        current_rate: float,
        proposed_leverage: float,
        max_leverage: float,
        entry_tag: str | None,
        side: str,
        **kwargs: Any,
    ) -> float:
        return min(2.0, max_leverage)

    def custom_exit(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        **kwargs: Any,
    ) -> str | None:
        """Re-emit a proven, unfilled full-exit intent after cancellation."""
        decision = self._evaluate_paper_exit_retry(trade)
        return self._paper_exit_retry_reason if decision.allowed else None

    def confirm_trade_exit(
        self,
        pair: str,
        trade: Trade,
        order_type: str,
        amount: float,
        rate: float,
        time_in_force: str,
        exit_reason: str,
        current_time: datetime,
        **kwargs: Any,
    ) -> bool:
        """Deny duplicate regular full exits from the in-memory trade state."""
        decision = self._evaluate_paper_full_exit_guard(
            trade=trade,
            requested_amount=amount,
            exit_reason=exit_reason,
        )
        return decision.allowed

    def _evaluate_paper_full_exit_guard(
        self,
        *,
        trade: Trade,
        requested_amount: Any,
        exit_reason: Any,
    ) -> _PaperExitGuardDecision:
        if not isinstance(exit_reason, str) or not exit_reason:
            return _PaperExitGuardDecision(False, "invalid_exit_reason")

        if exit_reason in self._paper_exit_guard_critical_reasons:
            return _PaperExitGuardDecision(True, "critical_exit_semantics_preserved")

        if getattr(trade, "is_open", None) is not True:
            return _PaperExitGuardDecision(False, "trade_not_proven_open")

        exit_side = getattr(trade, "exit_side", None)
        trade_amount = self._positive_finite_float(getattr(trade, "amount", None))
        confirmed_amount = self._positive_finite_float(requested_amount)
        orders = getattr(trade, "orders", None)
        if not isinstance(exit_side, str) or exit_side not in {"buy", "sell"}:
            return _PaperExitGuardDecision(False, "invalid_exit_side")
        if trade_amount is None or confirmed_amount is None:
            return _PaperExitGuardDecision(False, "invalid_full_exit_amount")
        if not math.isclose(
            confirmed_amount,
            trade_amount,
            rel_tol=1e-9,
            abs_tol=1e-12,
        ):
            return _PaperExitGuardDecision(False, "requested_amount_not_full_exit")
        if not isinstance(orders, (list, tuple)):
            return _PaperExitGuardDecision(False, "orders_not_available")

        exit_orders = [
            order
            for order in orders
            if getattr(order, "ft_order_side", None) == exit_side
        ]
        if not exit_orders:
            return _PaperExitGuardDecision(True, "first_regular_full_exit")

        for order in exit_orders:
            is_open = getattr(order, "ft_is_open", None)
            if is_open is True:
                return _PaperExitGuardDecision(False, "prior_exit_still_open")
            if is_open is not False:
                return _PaperExitGuardDecision(False, "prior_exit_open_state_unknown")

            status = getattr(order, "status", None)
            if not isinstance(status, str) or not status.strip():
                return _PaperExitGuardDecision(False, "prior_exit_status_unknown")
            if status.strip().lower() in {"open", "new"}:
                return _PaperExitGuardDecision(
                    False,
                    "prior_exit_status_open_inconsistent",
                )

            raw_filled_amount = self._nonnegative_finite_float(
                getattr(order, "filled", None)
            )
            safe_filled_amount = self._nonnegative_finite_float(
                getattr(order, "safe_filled", None)
            )
            if raw_filled_amount is None or safe_filled_amount is None:
                return _PaperExitGuardDecision(False, "prior_exit_fill_unknown")
            if raw_filled_amount != safe_filled_amount:
                return _PaperExitGuardDecision(False, "prior_exit_fill_inconsistent")
            if raw_filled_amount > 0.0:
                return _PaperExitGuardDecision(False, "prior_exit_has_fill")

        if exit_reason != self._paper_exit_retry_reason:
            return _PaperExitGuardDecision(
                False,
                "prior_exit_requires_controlled_retry",
            )

        return self._evaluate_paper_exit_retry(trade)

    def _evaluate_paper_exit_retry(
        self,
        trade: Trade,
    ) -> _PaperExitGuardDecision:
        if getattr(trade, "is_open", None) is not True:
            return _PaperExitGuardDecision(False, "trade_not_proven_open")

        exit_side = getattr(trade, "exit_side", None)
        trade_amount = self._positive_finite_float(getattr(trade, "amount", None))
        orders = getattr(trade, "orders", None)
        if not isinstance(exit_side, str) or exit_side not in {"buy", "sell"}:
            return _PaperExitGuardDecision(False, "invalid_exit_side")
        if trade_amount is None:
            return _PaperExitGuardDecision(False, "invalid_trade_amount")
        if not isinstance(orders, (list, tuple)):
            return _PaperExitGuardDecision(False, "orders_not_available")

        exit_orders = [
            order
            for order in orders
            if getattr(order, "ft_order_side", None) == exit_side
        ]
        for order in exit_orders:
            is_open = getattr(order, "ft_is_open", None)
            if is_open is True:
                return _PaperExitGuardDecision(False, "exit_still_open")
            if is_open is not False:
                return _PaperExitGuardDecision(False, "exit_open_state_unknown")

        for order in reversed(exit_orders):
            status = getattr(order, "status", None)
            if (
                not isinstance(status, str)
                or status not in self._paper_exit_retry_cancel_states
            ):
                return _PaperExitGuardDecision(False, "ineligible_cancel_state")

            tag = getattr(order, "ft_order_tag", None)
            if (
                not isinstance(tag, str)
                or tag not in self._paper_exit_retry_source_tags
            ):
                return _PaperExitGuardDecision(False, "ineligible_exit_tag")

            cancel_reason = getattr(order, "ft_cancel_reason", None)
            if cancel_reason != self._paper_exit_retry_timeout_reason:
                return _PaperExitGuardDecision(False, "cancel_reason_not_timeout")

            order_amount = self._positive_finite_float(
                getattr(order, "safe_amount", None)
            )
            raw_filled_amount = self._nonnegative_finite_float(
                getattr(order, "filled", None)
            )
            safe_filled_amount = self._nonnegative_finite_float(
                getattr(order, "safe_filled", None)
            )
            if (
                order_amount is None
                or raw_filled_amount is None
                or safe_filled_amount is None
            ):
                return _PaperExitGuardDecision(False, "retry_evidence_incomplete")
            if not math.isclose(
                raw_filled_amount,
                0.0,
                rel_tol=0.0,
                abs_tol=1e-12,
            ):
                return _PaperExitGuardDecision(False, "raw_fill_not_zero")
            if not math.isclose(
                safe_filled_amount,
                0.0,
                rel_tol=0.0,
                abs_tol=1e-12,
            ):
                return _PaperExitGuardDecision(False, "safe_fill_not_zero")
            if not math.isclose(
                order_amount,
                trade_amount,
                rel_tol=1e-9,
                abs_tol=1e-12,
            ):
                return _PaperExitGuardDecision(False, "retry_amount_not_full_exit")
            return _PaperExitGuardDecision(True, "timeout_zero_fill_full_exit")

        return _PaperExitGuardDecision(False, "no_eligible_exit_to_retry")

    @staticmethod
    def _positive_finite_float(value: Any) -> float | None:
        if isinstance(value, bool):
            return None
        try:
            parsed = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if not math.isfinite(parsed) or parsed <= 0.0:
            return None
        return parsed

    @staticmethod
    def _nonnegative_finite_float(value: Any) -> float | None:
        if isinstance(value, bool):
            return None
        try:
            parsed = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if not math.isfinite(parsed) or parsed < 0.0:
            return None
        return parsed

    def _find_signal_for_pair(self, pair: str) -> dict[str, Any]:
        symbol = self._symbol_from_pair(pair)
        payload, source_path, source_reason = self._read_first_active_signal_file()

        if payload is None:
            return {
                "accepted": False,
                "reason": source_reason,
                "lookup": {
                    "source_path": str(source_path) if source_path else None,
                    "source_reason": source_reason,
                    "wanted_pair": pair,
                    "wanted_symbol": symbol,
                    "available_pairs": [],
                    "active_count": 0,
                },
            }

        signals = self._extract_signals(payload)
        active_signals = [signal for signal in signals if self._is_signal_active(signal)]
        matching_signal = self._match_signal(active_signals, pair, symbol)

        if matching_signal is None:
            return {
                "accepted": False,
                "reason": "no_signal_for_pair",
                "lookup": {
                    "source_path": str(source_path),
                    "source_reason": "active_signals_found",
                    "wanted_pair": pair,
                    "wanted_symbol": symbol,
                    "available_pairs": [signal.get("pair") for signal in active_signals],
                    "active_count": len(active_signals),
                },
            }

        side = str(matching_signal.get("side", "")).lower()
        confidence = self._safe_float(matching_signal.get("confidence", matching_signal.get("prob_up", matching_signal.get("score", 0.0))))
        # risk_approved must be the exact boolean True. Absent, null, "true"
        # (string), 1, or any other truthy-but-not-True value is treated as
        # NOT approved. This is deliberately stricter than a plain truthy
        # check: the only acceptable source of "approved" is RiskManager
        # (see smartcrypto/execution/signal_risk_gate.py), and a missing or
        # malformed field must fail closed, never open.
        risk_approved = matching_signal.get("risk_approved") is True
        decision_ledger = matching_signal.get("decision_ledger")
        decision_ledger = decision_ledger if isinstance(decision_ledger, dict) else {}

        if side not in {"long", "short"}:
            return {"accepted": False, "reason": "invalid_side", "side": side, "confidence": confidence}

        if not risk_approved:
            return {"accepted": False, "reason": "risk_not_approved", "side": side, "confidence": confidence}

        return {
            "accepted": True,
            "reason": "signal_payload_found",
            "side": side,
            "confidence": confidence,
            "decision_event_id": decision_ledger.get("decision_event_id"),
            "signal_id": decision_ledger.get("signal_id"),
            "correlation_id": decision_ledger.get("correlation_id"),
            "lookup": {
                "source_path": str(source_path),
                "source_reason": "active_signals_found",
                "active_count": len(active_signals),
            },
        }

    def _find_exit_control_for_pair(self, pair: str) -> dict[str, Any]:
        symbol = self._symbol_from_pair(pair)

        for path in self._exit_control_paths:
            payload = self._read_json(path)
            if payload is None:
                continue

            if not bool(payload.get("force_exit_enabled", False)):
                continue

            if str(payload.get("runtime_mode", "paper")).lower() != "paper":
                continue

            if not self._valid_until_active(payload.get("valid_until")):
                continue

            pairs = payload.get("pairs", payload.get("exit_pairs", []))
            normalized_pairs = {str(item) for item in pairs}
            normalized_symbols = {self._symbol_from_pair(str(item)) for item in pairs}

            if "all" in normalized_pairs or pair in normalized_pairs or symbol in normalized_symbols:
                return {
                    "accepted": True,
                    "reason": payload.get("reason", "phase15_controlled_paper_exit"),
                    "source_path": str(path),
                }

        return {"accepted": False, "reason": "no_active_exit_control"}

    def _read_first_active_signal_file(self) -> tuple[dict[str, Any] | None, Path | None, str]:
        # Walks every known signal path in priority order. A file that
        # exists but has no risk-approved, fresh signal for anyone (e.g. it
        # is empty, stale, or every signal in it was rejected) no longer
        # stops the search: the strategy keeps trying the next path. This
        # is what makes the RiskManager-gated file usable as a real
        # fallback instead of being permanently shadowed by whichever file
        # happens to exist first. The fallback itself never grants
        # approval: _is_signal_active() still requires risk_approved is
        # True for every candidate, in every file, with no exception.
        last_path: Path | None = None
        last_reason = "no_signal_payload"
        for path in self._signal_paths:
            payload = self._read_json(path)
            if payload is None:
                continue
            last_path = path
            active_signals = [signal for signal in self._extract_signals(payload) if self._is_signal_active(signal)]
            if active_signals:
                return payload, path, "active_signals_found"
            last_reason = "no_active_signals_in_file"
        return None, last_path, last_reason

    def _read_json(self, path: Path) -> dict[str, Any] | None:
        try:
            if not path.exists():
                return None
            with path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
            if isinstance(payload, dict):
                return payload
            return None
        except Exception:
            return None

    def _extract_signals(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        value = payload.get("signals", [])
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        return []

    def _match_signal(self, signals: list[dict[str, Any]], pair: str, symbol: str) -> dict[str, Any] | None:
        for signal in signals:
            signal_pair = str(signal.get("pair", ""))
            signal_symbol = str(signal.get("symbol", ""))
            if signal_pair == pair or signal_symbol == symbol:
                return signal
        return None

    def _is_signal_active(self, signal: dict[str, Any]) -> bool:
        # "Active" requires both freshness AND explicit RiskManager
        # approval. A signal missing risk_approved, or with risk_approved
        # not equal to True, is never active - regardless of which signal
        # file it came from.
        if signal.get("risk_approved") is not True:
            return False
        return self._valid_until_active(signal.get("valid_until"))

    def _valid_until_active(self, value: Any) -> bool:
        if value is None:
            return True
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed >= datetime.now(timezone.utc)
        except Exception:
            return False

    def _symbol_from_pair(self, pair: str) -> str:
        return pair.replace("/", "").replace(":USDT", "").replace(":USD", "").replace("-", "").upper()

    def _safe_float(self, value: Any) -> float:
        try:
            return float(value)
        except Exception:
            return 0.0

    def _entry_tag(self, side: str, decision_event_id: Any) -> str:
        base = f"smartcrypto_{side}"
        if not isinstance(decision_event_id, str) or not decision_event_id:
            return base
        return f"{base}|decision_event_id={decision_event_id}"

    def _write_decision(self, payload: dict[str, Any]) -> None:
        for path in self._decision_log_paths:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
                return
            except Exception:
                continue

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()
