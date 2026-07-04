"""Single, shared RiskManager gate for every writer of active paper signals.

Branch: codex/paper-signal-riskmanager-runtime-wiring-audit-v1

Background
----------
An external audit of the SMART FUTUROS handover found that the file Freqtrade
actually prioritizes reading (``data/runtime/active_freqtrade_signals.json``)
was written by a path that never called ``RiskManager.approve()`` /
``RiskManager.approve_many()``. Instead it stamped every candidate signal with
a hardcoded ``risk_approved`` value.

A second, independent writer (``smartcrypto/qlib_engine/signal_exporter.py``)
and a third (``smartcrypto/execution/signal_contract_guard.py``) had the exact
same defect, each with its own copy of the same unsafe pattern.

This module exists so there is exactly one implementation of "turn candidate
signals into RiskManager-approved signals" instead of three drifting copies.

``smartcrypto/execution/signal_producer.py``,
``smartcrypto/qlib_engine/signal_exporter.py`` and
``smartcrypto/execution/signal_contract_guard.py`` all call
:func:`apply_risk_manager_gate` instead of setting their own ``risk_approved``
value.

Fail-closed contract
--------------------
- If ``RiskManager`` cannot be constructed, every candidate signal is rejected.
- If ``RiskManager.approve_many()`` raises, every candidate signal is rejected.
- No exception raised anywhere in this module, or by ``RiskManager`` itself,
  ever results in ``risk_approved=True``.
- Only signals RiskManager actually approved are returned in
  ``RiskGateResult.approved_signals``.
- Rejected signals are never included in ``approved_signals``; callers must not
  write them as active signals.
- This module never writes runtime, SQLite or Parquet artifacts.
- This module does not send orders, does not access a private exchange
  connection, does not change risk limits, and does not alter model/registry
  state.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from smartcrypto.risk.risk_manager import RiskManager


_LOGGER = logging.getLogger(__name__)

SCHEMA_VERSION = "signal_risk_gate_v1"
RISK_MANAGER_SOURCE = "smartcrypto.risk.risk_manager.RiskManager"
DEFAULT_RISK_LIMITS_PATH = Path("config/risk_limits.yml")


# Safety flags this module always upholds. They describe what this module
# itself does; they are not a discovery about the wider runtime.
SAFETY_FLAGS: dict[str, bool] = {
    "paper_only": True,
    "shadow_only": True,
    "research_only": True,
    "sends_orders": False,
    "exchange_private_access": False,
    "order_submission_enabled": False,
    "real_order_submission_enabled": False,
    "changes_risk": False,
    "changes_model": False,
    "registry_write_performed": False,
    "writes_runtime": False,
    "writes_sqlite": False,
    "writes_parquet": False,
}


@dataclass(frozen=True)
class RiskGateResult:
    """Outcome of running candidate signals through the RiskManager gate."""

    status: str
    reason: str | None
    risk_manager_available: bool
    risk_manager_source: str
    risk_limits_path: str
    risk_config_hash: str | None
    signals_submitted: int
    signals_approved: int
    signals_rejected: int
    approved_signals: list[dict[str, Any]] = field(default_factory=list)
    rejected_signals: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": self.status,
            "reason": self.reason,
            "risk_manager_available": self.risk_manager_available,
            "risk_manager_source": self.risk_manager_source,
            "risk_limits_path": self.risk_limits_path,
            "risk_config_hash": self.risk_config_hash,
            "signals_submitted": self.signals_submitted,
            "signals_approved": self.signals_approved,
            "signals_rejected": self.signals_rejected,
            "rejected_signal_reasons": [
                {
                    "pair": item.get("pair"),
                    "symbol": item.get("symbol"),
                    "side": item.get("side"),
                    "risk_reasons": item.get("risk_reasons"),
                }
                for item in self.rejected_signals
            ],
            "error": self.error,
            "safety_flags": dict(SAFETY_FLAGS),
        }


def apply_risk_manager_gate(
    candidate_signals: Sequence[Mapping[str, Any]],
    *,
    risk_limits_path: str | Path = DEFAULT_RISK_LIMITS_PATH,
    risk_manager: RiskManager | None = None,
) -> RiskGateResult:
    """Gate candidate signals through RiskManager.

    This function is intentionally fail-closed. Any inability to construct or
    execute RiskManager returns ``status="blocked"`` and rejects every submitted
    signal.

    The broad exception handlers below are deliberate operational boundaries:
    they log a sanitized diagnostic and then return a controlled blocked result.
    """

    resolved_path = Path(risk_limits_path)
    config_hash = sha256_file(resolved_path)
    submitted = list(candidate_signals)

    manager = risk_manager
    if manager is None:
        try:
            manager = RiskManager.from_yaml(resolved_path)
        except Exception as exc:  # noqa: BLE001 - explicit fail-closed boundary.
            failure_status = "blocked"
            failure_reason = f"risk_manager_unavailable:{type(exc).__name__}"
            failure_error = str(exc)

            _LOGGER.error(
                "RiskManager unavailable; rejecting all candidate signals fail-closed. "
                "error_type=%s risk_limits_path=%s submitted_signals=%d",
                type(exc).__name__,
                str(resolved_path),
                len(submitted),
                exc_info=True,
            )

            return RiskGateResult(
                status=failure_status,
                reason=failure_reason,
                risk_manager_available=False,
                risk_manager_source=RISK_MANAGER_SOURCE,
                risk_limits_path=str(resolved_path),
                risk_config_hash=config_hash,
                signals_submitted=len(submitted),
                signals_approved=0,
                signals_rejected=len(submitted),
                approved_signals=[],
                rejected_signals=[
                    _stamp_rejected(signal, reasons=["risk_manager_unavailable"])
                    for signal in submitted
                ],
                error=failure_error,
            )

    try:
        decisions = manager.approve_many(submitted)
    except Exception as exc:  # noqa: BLE001 - explicit fail-closed boundary.
        failure_status = "blocked"
        failure_reason = f"risk_manager_evaluation_failed:{type(exc).__name__}"
        failure_error = str(exc)

        _LOGGER.error(
            "RiskManager evaluation failed; rejecting all candidate signals fail-closed. "
            "error_type=%s risk_limits_path=%s submitted_signals=%d",
            type(exc).__name__,
            str(resolved_path),
            len(submitted),
            exc_info=True,
        )

        return RiskGateResult(
            status=failure_status,
            reason=failure_reason,
            risk_manager_available=True,
            risk_manager_source=RISK_MANAGER_SOURCE,
            risk_limits_path=str(resolved_path),
            risk_config_hash=config_hash,
            signals_submitted=len(submitted),
            signals_approved=0,
            signals_rejected=len(submitted),
            approved_signals=[],
            rejected_signals=[
                _stamp_rejected(signal, reasons=["risk_manager_evaluation_failed"])
                for signal in submitted
            ],
            error=failure_error,
        )

    checked_at = datetime.now(timezone.utc).isoformat()
    risk_policy_id = f"risk_limits_yaml:{config_hash[:16]}" if config_hash else "risk_limits_yaml:unavailable"

    approved: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    for decision in decisions:
        if decision.approved:
            approved.append(
                _stamp_approved(
                    decision.signal,
                    risk_reasons=list(decision.reasons),
                    checked_at=checked_at,
                    risk_policy_id=risk_policy_id,
                    risk_config_hash=config_hash,
                )
            )
        else:
            rejected.append(
                _stamp_rejected(
                    decision.signal,
                    reasons=list(decision.reasons) or ["risk_not_approved"],
                    checked_at=checked_at,
                    risk_policy_id=risk_policy_id,
                    risk_config_hash=config_hash,
                )
            )

    return RiskGateResult(
        status="ok",
        reason=None,
        risk_manager_available=True,
        risk_manager_source=RISK_MANAGER_SOURCE,
        risk_limits_path=str(resolved_path),
        risk_config_hash=config_hash,
        signals_submitted=len(submitted),
        signals_approved=len(approved),
        signals_rejected=len(rejected),
        approved_signals=approved,
        rejected_signals=rejected,
        error=None,
    )


def _stamp_approved(
    signal: Mapping[str, Any],
    *,
    risk_reasons: list[str],
    checked_at: str,
    risk_policy_id: str,
    risk_config_hash: str | None,
) -> dict[str, Any]:
    stamped = dict(signal)
    stamped["risk_approved"] = True
    stamped["risk_reasons"] = risk_reasons
    stamped["risk_checked_at_utc"] = checked_at
    stamped["risk_manager_source"] = RISK_MANAGER_SOURCE
    stamped["risk_policy_id"] = risk_policy_id
    stamped["risk_config_hash"] = risk_config_hash
    return stamped


def _stamp_rejected(
    signal: Mapping[str, Any],
    *,
    reasons: list[str],
    checked_at: str | None = None,
    risk_policy_id: str | None = None,
    risk_config_hash: str | None = None,
) -> dict[str, Any]:
    stamped = dict(signal)
    stamped["risk_approved"] = False
    stamped["risk_reasons"] = list(reasons)
    stamped["risk_checked_at_utc"] = checked_at or datetime.now(timezone.utc).isoformat()
    stamped["risk_manager_source"] = RISK_MANAGER_SOURCE

    if risk_policy_id is not None:
        stamped["risk_policy_id"] = risk_policy_id

    if risk_config_hash is not None:
        stamped["risk_config_hash"] = risk_config_hash

    return stamped


def sha256_file(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None

    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)

    return hasher.hexdigest()