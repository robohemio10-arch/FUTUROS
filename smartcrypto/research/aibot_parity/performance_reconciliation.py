"""Trade-level and future account-level AIBOT reconciliation contract."""

from __future__ import annotations

from typing import Any

from .contracts import safety_flags


ACCOUNT_CASHFLOW_REQUIRED_FIELDS = (
    "capital_initial",
    "capital_timeline",
    "deposits",
    "withdrawals",
    "credits",
    "fund_allocation",
    "compounding_policy",
)


def build_performance_reconciliation(
    *,
    source_investment_id: str,
    source_batch_id: str,
    behavior_fingerprint: dict[str, Any],
    account_cashflow_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metrics = dict(behavior_fingerprint.get("global", {}))
    trade_level_available = int(metrics.get("trade_count", 0)) > 0
    supplied = account_cashflow_data or {}
    missing_account_fields = [
        field for field in ACCOUNT_CASHFLOW_REQUIRED_FIELDS if supplied.get(field) is None
    ]
    account_level_available = not missing_account_fields
    return {
        "status": "ok" if trade_level_available else "blocked",
        "reason": (
            "trade_level_performance_reconciled_account_level_pending"
            if trade_level_available
            else "trade_level_performance_unavailable"
        ),
        "source_investment_id": source_investment_id,
        "source_batch_id": source_batch_id,
        "benchmark_snapshot_status": "CURRENT_SNAPSHOT_NOT_FINAL",
        "financial_closeout_status": "PENDING_TRADER_MASTER_REFRESH",
        "trade_level_performance_status": "AVAILABLE" if trade_level_available else "UNAVAILABLE",
        "trade_level_performance": metrics,
        "account_level_reconciliation_status": (
            "AVAILABLE_FOR_FUTURE_RECONCILIATION"
            if account_level_available
            else "INSUFFICIENT_ACCOUNT_CASHFLOW_DATA"
        ),
        "account_level_return_pct": None,
        "account_level_return_claimed": False,
        "missing_account_cashflow_fields": missing_account_fields,
        "account_cashflow_contract": list(ACCOUNT_CASHFLOW_REQUIRED_FIELDS),
        "isolated_trade_pnl_is_not_account_return": True,
        "safety_flags": safety_flags(),
    }
