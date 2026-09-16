"""Frozen WQ2 temporal walk-forward evaluation for the official post-OCR master."""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from smartcrypto.analysis.paper_financial_performance import (
    compute_financial_metrics,
    json_safe,
)

from .contracts import (
    LEGACY_OPENING_SENTINEL,
    OFFICIAL_MASTER_SHA256,
)
from .loader import OfficialMasterData


WQ2_METHOD_HASH = (
    "604ee399fcb0e57657150eb94207513ae"
    "607d3bd3e7d6755cb7664559b3b1220"
)

WQ2_PERIODS = (
    "2026-01",
    "2026-02",
    "2026-03",
    "2026-04",
    "2026-05",
    "2026-06",
    "2026-07",
    "2026-08",
)

EXPANDING_TEST_PERIODS = WQ2_PERIODS[2:]
ROLLING_TEST_PERIODS = WQ2_PERIODS[3:]

REPORT_JSON = (
    "OFFICIAL_TRADES_MASTER_WALKFORWARD_V1.json"
)
REPORT_MD = (
    "OFFICIAL_TRADES_MASTER_WALKFORWARD_V1.md"
)
FOLDS_CSV = (
    "OFFICIAL_TRADES_MASTER_WALKFORWARD_FOLDS_V1.csv"
)
LOPO_CSV = (
    "OFFICIAL_TRADES_MASTER_WALKFORWARD_LOPO_V1.csv"
)
MANIFEST_CSV = (
    "OFFICIAL_TRADES_MASTER_WALKFORWARD_"
    "SHA256_MANIFEST_V1.csv"
)


class OfficialWalkForwardValidationError(ValueError):
    """Fail-closed WQ2 validation error."""

    def __init__(
        self,
        code: str,
        **details: Any,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.details = details


def _fail(
    code: str,
    **details: Any,
) -> None:
    raise OfficialWalkForwardValidationError(
        code,
        **details,
    )


def _stable_hash(
    payload: Any,
) -> str:
    text = json.dumps(
        json_safe(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )

    return hashlib.sha256(
        text.encode("utf-8")
    ).hexdigest()


def frozen_method() -> dict[str, Any]:
    periods = list(WQ2_PERIODS)

    method: dict[str, Any] = {
        "schema_version": (
            "official_trades_master_walkforward_method_v1"
        ),
        "master_sha256": OFFICIAL_MASTER_SHA256,
        "temporal_population": {
            "master_rows": 3991,
            "eligible_rows": 3760,
            "excluded_rows": 231,
            "eligibility_rule": (
                "horario_abertura parseable and "
                "horario_fechamento parseable"
            ),
            "excluded_classification": (
                "LEGACY_TEMPORAL_INELIGIBLE_"
                "MISSING_OPEN_TIME"
            ),
            "opening_time_synthesis_allowed": False,
        },
        "ordering": {
            "primary": "open_time_utc",
            "secondary": "close_time_utc",
            "tie_breaker": "trade_sequence",
            "stable_sort": "mergesort",
        },
        "period_definition": {
            "unit": "calendar_month",
            "assignment_timestamp": "open_time_utc",
            "observed_periods": periods,
        },
        "primary_walkforward": {
            "mode": "expanding",
            "minimum_train_periods": 2,
            "minimum_train_rows_after_purge": 300,
            "minimum_test_rows": 30,
            "test_periods": periods[2:],
        },
        "sensitivity_walkforward": {
            "mode": "rolling",
            "rolling_train_periods": 3,
            "minimum_train_rows_after_purge": 300,
            "minimum_test_rows": 30,
            "test_periods": periods[3:],
        },
        "purge_policy": {
            "enabled": True,
            "interval_semantics": (
                "[open_time_utc, close_time_utc]"
            ),
            "rule": (
                "remove train row when "
                "close_time_utc >= test_start_utc"
            ),
            "reason": (
                "remove trades whose realized outcome "
                "was not complete before the OOS test "
                "period began"
            ),
        },
        "embargo_policy": {
            "enabled": False,
            "embargo_seconds": 0,
            "reason": (
                "WQ2 is descriptive realized-outcome "
                "walk-forward with no feature lookback, "
                "fitting, calibration or label horizon; "
                "actual trade interval purging is sufficient. "
                "Feature/model embargo is deferred to "
                "PIT/Qlib research."
            ),
        },
        "oos_metrics": [
            "trades",
            "net_pnl",
            "profit_factor",
            "win_rate",
            "expectancy_mean",
            "expectancy_median",
            "payoff_ratio",
            "max_drawdown_signed",
            "top_5_percent_share_of_net",
            "symbol_side_mix",
            "duration_coverage",
        ],
        "stability_outputs": [
            "fold_table",
            "combined_oos_metrics",
            "positive_fold_rate",
            "worst_fold",
            "mature_prefix_stability",
            "rolling_stability",
            "leave_one_period_out",
            "purge_accounting",
        ],
        "research_classification_defaults": {
            "persistent_candidate_requires": {
                "combined_oos_net_pnl_gt": 0.0,
                "combined_oos_profit_factor_gt": 1.0,
                "minimum_independent_positive_oos_periods": 2,
            },
            "operational_authority": False,
        },
        "random_split_allowed": False,
        "rule_selection_inside_wq2": False,
        "model_training": False,
        "feature_selection": False,
        "uses_pnl_to_define_folds": False,
        "paper_only": True,
        "shadow_only": True,
        "research_only": True,
        "live": False,
        "canary": False,
        "sends_orders": False,
        "changes_risk": False,
        "operational_authority": False,
    }

    actual = _stable_hash(
        method
    )

    if actual != WQ2_METHOD_HASH:
        _fail(
            "frozen_method_hash_mismatch",
            expected=WQ2_METHOD_HASH,
            actual=actual,
        )

    return {
        **method,
        "method_hash": actual,
    }


def _population(
    master: OfficialMasterData,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    if master.audit.status != "ok":
        _fail(
            "source_audit_not_ok",
            status=master.audit.status,
        )

    if (
        master.audit.master_sha256
        != OFFICIAL_MASTER_SHA256
    ):
        _fail(
            "master_sha256_not_frozen",
            actual=master.audit.master_sha256,
        )

    required = {
        "trade_sequence",
        "order_id_raw",
        "symbol",
        "side",
        "horario_abertura",
        "horario_fechamento",
        "fee_semantics_regime",
        "economic_net_pnl",
    }

    missing = sorted(
        required - set(master.frame.columns)
    )

    if missing:
        _fail(
            "walkforward_required_columns_missing",
            columns=missing,
        )

    if len(master.frame) != 3991:
        _fail(
            "master_row_count_not_frozen",
            actual=len(master.frame),
        )

    frame = master.frame

    raw_open = (
        frame["horario_abertura"]
        .astype("string")
        .fillna("")
        .str.strip()
    )

    sentinel = raw_open.eq(
        LEGACY_OPENING_SENTINEL
    ).fillna(False)

    open_ts = pd.to_datetime(
        raw_open.mask(
            sentinel,
            pd.NA,
        ),
        format="%Y-%m-%d %H:%M:%S",
        errors="coerce",
        utc=True,
    )

    close_ts = pd.to_datetime(
        frame["horario_fechamento"],
        format="%Y-%m-%d %H:%M:%S",
        errors="coerce",
        utc=True,
    )

    unexpected = (
        ~sentinel
        & open_ts.isna()
    )

    if unexpected.any():
        _fail(
            "unexpected_invalid_open_time",
            count=int(
                unexpected.sum()
            ),
        )

    if close_ts.isna().any():
        _fail(
            "invalid_close_time",
            count=int(
                close_ts.isna().sum()
            ),
        )

    regimes = (
        frame["fee_semantics_regime"]
        .astype("string")
        .str.strip()
    )

    eligible = open_ts.notna()

    if (
        int(eligible.sum()) != 3760
        or int((~eligible).sum()) != 231
    ):
        _fail(
            "temporal_population_count_mismatch"
        )

    if not regimes.loc[
        eligible
    ].eq("STANDARD").all():
        _fail(
            "temporal_eligible_not_standard"
        )

    if not regimes.loc[
        ~eligible
    ].eq("LEGACY").all():
        _fail(
            "temporal_excluded_not_legacy"
        )

    pnl = pd.to_numeric(
        frame.loc[
            eligible,
            "economic_net_pnl",
        ],
        errors="coerce",
    )

    sequence = pd.to_numeric(
        frame.loc[
            eligible,
            "trade_sequence",
        ],
        errors="coerce",
    )

    if (
        pnl.isna().any()
        or not np.isfinite(
            pnl.to_numpy(
                dtype=float
            )
        ).all()
    ):
        _fail(
            "eligible_economic_pnl_invalid"
        )

    if sequence.isna().any():
        _fail(
            "eligible_trade_sequence_invalid"
        )

    working = pd.DataFrame(
        {
            "trade_sequence": (
                sequence
                .astype(int)
                .to_numpy()
            ),
            "order_id_raw": (
                frame.loc[
                    eligible,
                    "order_id_raw",
                ]
                .astype("string")
                .to_numpy()
            ),
            "symbol": (
                frame.loc[
                    eligible,
                    "symbol",
                ]
                .astype("string")
                .to_numpy()
            ),
            "side": (
                frame.loc[
                    eligible,
                    "side",
                ]
                .astype("string")
                .to_numpy()
            ),
            "open_time_utc": (
                open_ts.loc[
                    eligible
                ].to_numpy()
            ),
            "close_time_utc": (
                close_ts.loc[
                    eligible
                ].to_numpy()
            ),
            "economic_net_pnl": (
                pnl.to_numpy(
                    dtype=float
                )
            ),
        }
    )

    working = working.sort_values(
        [
            "open_time_utc",
            "close_time_utc",
            "trade_sequence",
        ],
        kind="mergesort",
    ).reset_index(
        drop=True
    )

    working["duration_seconds"] = (
        working["close_time_utc"]
        - working["open_time_utc"]
    ).dt.total_seconds()

    if (
        working[
            "duration_seconds"
        ] < 0
    ).any():
        _fail(
            "negative_trade_duration"
        )

    working["period"] = (
        working["open_time_utc"]
        .dt.strftime("%Y-%m")
    )

    periods = tuple(
        sorted(
            working["period"]
            .unique()
            .tolist()
        )
    )

    if periods != WQ2_PERIODS:
        _fail(
            "observed_periods_mismatch",
            expected=list(
                WQ2_PERIODS
            ),
            actual=list(
                periods
            ),
        )

    inventory = {
        "eligible_rows": len(
            working
        ),
        "excluded_legacy_rows": 231,
        "observed_periods": list(
            periods
        ),
        "period_counts": {
            str(key): int(value)
            for key, value in (
                working["period"]
                .value_counts()
                .sort_index()
                .items()
            )
        },
        "first_open_utc": (
            working[
                "open_time_utc"
            ].min().isoformat()
        ),
        "last_open_utc": (
            working[
                "open_time_utc"
            ].max().isoformat()
        ),
        "last_close_utc": (
            working[
                "close_time_utc"
            ].max().isoformat()
        ),
    }

    return (
        working,
        inventory,
    )


def _period_start(
    period: str,
) -> pd.Timestamp:
    return pd.Timestamp(
        f"{period}-01T00:00:00Z"
    )


def build_fold_plans(
    frame: pd.DataFrame,
    *,
    mode: str,
) -> list[dict[str, Any]]:
    test_periods: tuple[str, ...]
    rolling_months: int | None

    if mode == "expanding":
        test_periods = EXPANDING_TEST_PERIODS
        rolling_months = None

    elif mode == "rolling":
        test_periods = ROLLING_TEST_PERIODS
        rolling_months = 3

    else:
        _fail(
            "unsupported_walkforward_mode",
            mode=mode,
        )

    plans: list[
        dict[str, Any]
    ] = []

    for fold, period in enumerate(
        test_periods,
        1,
    ):
        start = _period_start(
            period
        )

        end = (
            start
            + pd.offsets.MonthBegin(1)
        )

        test_mask = (
            (
                frame[
                    "open_time_utc"
                ] >= start
            )
            & (
                frame[
                    "open_time_utc"
                ] < end
            )
        )

        if rolling_months is None:
            train_mask = (
                frame[
                    "open_time_utc"
                ] < start
            )

            train_period_start = (
                frame.loc[
                    train_mask,
                    "open_time_utc",
                ].min()
            )

        else:
            rolling_start = (
                start
                - pd.DateOffset(
                    months=rolling_months
                )
            )

            train_mask = (
                (
                    frame[
                        "open_time_utc"
                    ] >= rolling_start
                )
                & (
                    frame[
                        "open_time_utc"
                    ] < start
                )
            )

            train_period_start = (
                rolling_start
            )

        candidate = tuple(
            int(index)
            for index in frame.index[
                train_mask
            ]
        )

        test = tuple(
            int(index)
            for index in frame.index[
                test_mask
            ]
        )

        purged = tuple(
            index
            for index in candidate
            if pd.Timestamp(
                frame.loc[
                    index,
                    "close_time_utc",
                ]
            )
            >= start
        )

        purged_set = set(
            purged
        )

        train = tuple(
            index
            for index in candidate
            if index not in purged_set
        )

        if len(train) < 300:
            _fail(
                "train_rows_below_frozen_minimum",
                mode=mode,
                period=period,
                actual=len(train),
            )

        if len(test) < 30:
            _fail(
                "test_rows_below_frozen_minimum",
                mode=mode,
                period=period,
                actual=len(test),
            )

        train_close_max = (
            pd.Timestamp(
                frame.loc[
                    list(train),
                    "close_time_utc",
                ].max()
            )
        )

        test_open_min = (
            pd.Timestamp(
                frame.loc[
                    list(test),
                    "open_time_utc",
                ].min()
            )
        )

        if (
            train_close_max
            >= test_open_min
        ):
            _fail(
                "temporal_overlap_after_purge",
                mode=mode,
                period=period,
            )

        plans.append(
            {
                "mode": mode,
                "fold": fold,
                "test_period": period,
                "train_indices": train,
                "test_indices": test,
                "purged_indices": purged,
                "train_period_start_utc": (
                    None
                    if pd.isna(
                        train_period_start
                    )
                    else pd.Timestamp(
                        train_period_start
                    ).isoformat()
                ),
                "train_end_boundary_utc": (
                    start.isoformat()
                ),
                "test_start_utc": (
                    start.isoformat()
                ),
                "test_end_exclusive_utc": (
                    end.isoformat()
                ),
                "train_close_max_utc": (
                    train_close_max.isoformat()
                ),
                "test_open_min_utc": (
                    test_open_min.isoformat()
                ),
            }
        )

    return plans


def _metrics(
    frame: pd.DataFrame,
) -> dict[str, Any]:
    if frame.empty:
        _fail(
            "metrics_frame_empty"
        )

    pnl = pd.to_numeric(
        frame[
            "economic_net_pnl"
        ],
        errors="coerce",
    )

    base = (
        compute_financial_metrics(
            pd.DataFrame(
                {
                    "__pnl": pnl,
                }
            )
        )
    )

    cumulative = pnl.cumsum()

    signed_dd = (
        cumulative
        - cumulative.cummax()
    )

    top_count = max(
        1,
        int(
            math.ceil(
                len(pnl) * 0.05
            )
        ),
    )

    top_pnl = float(
        pnl.nlargest(
            top_count
        ).sum()
    )

    net = float(
        base["total_pnl"]
    )

    mix: list[
        dict[str, Any]
    ] = []

    for (
        symbol,
        side,
    ), group in frame.groupby(
        [
            "symbol",
            "side",
        ],
        dropna=False,
        sort=True,
    ):
        mix.append(
            {
                "symbol": (
                    "UNKNOWN"
                    if pd.isna(symbol)
                    else str(symbol)
                ),
                "side": (
                    "UNKNOWN"
                    if pd.isna(side)
                    else str(side)
                ),
                "trades": len(
                    group
                ),
                "net_pnl": float(
                    pd.to_numeric(
                        group[
                            "economic_net_pnl"
                        ],
                        errors="raise",
                    ).sum()
                ),
            }
        )

    duration = pd.to_numeric(
        frame[
            "duration_seconds"
        ],
        errors="coerce",
    )

    return {
        "trades": int(
            base["trades"]
        ),
        "wins": int(
            base["wins"]
        ),
        "losses": int(
            base["losses"]
        ),
        "net_pnl": net,
        "profit_factor": (
            base[
                "profit_factor"
            ]
        ),
        "profit_factor_status": (
            base[
                "profit_factor_status"
            ]
        ),
        "win_rate": (
            base[
                "win_rate"
            ]
        ),
        "expectancy_mean": (
            base[
                "expectancy"
            ]
        ),
        "expectancy_median": (
            base[
                "median_return"
            ]
        ),
        "payoff_ratio": (
            base[
                "payoff_ratio"
            ]
        ),
        "max_drawdown_signed": float(
            signed_dd.min()
        ),
        "max_drawdown_magnitude": (
            base[
                "max_drawdown"
            ]
        ),
        "top_5_percent_trade_count": (
            top_count
        ),
        "top_5_percent_pnl": (
            top_pnl
        ),
        "top_5_percent_share_of_net": (
            None
            if net == 0
            else top_pnl / net
        ),
        "symbol_side_mix": mix,
        "duration_coverage": {
            "available": int(
                duration.notna().sum()
            ),
            "unavailable": int(
                duration.isna().sum()
            ),
            "coverage_rate": float(
                duration.notna().sum()
                / len(frame)
            ),
            "median_seconds": float(
                duration.median()
            ),
            "p95_seconds": float(
                duration.quantile(
                    0.95
                )
            ),
        },
    }


def _public_plan(
    plan: dict[str, Any],
) -> dict[str, Any]:
    public = {
        key: value
        for key, value in (
            plan.items()
        )
        if key not in {
            "train_indices",
            "test_indices",
            "purged_indices",
        }
    }

    public.update(
        {
            "train_candidate_rows": (
                len(
                    plan[
                        "train_indices"
                    ]
                )
                + len(
                    plan[
                        "purged_indices"
                    ]
                )
            ),
            "purged_rows": len(
                plan[
                    "purged_indices"
                ]
            ),
            "train_rows_after_purge": len(
                plan[
                    "train_indices"
                ]
            ),
            "test_rows": len(
                plan[
                    "test_indices"
                ]
            ),
        }
    )

    return public


def _evaluate(
    frame: pd.DataFrame,
    plans: list[
        dict[str, Any]
    ],
) -> list[dict[str, Any]]:
    return [
        {
            **_public_plan(
                plan
            ),
            "metrics": (
                _metrics(
                    frame.loc[
                        list(
                            plan[
                                "test_indices"
                            ]
                        )
                    ]
                )
            ),
        }
        for plan in plans
    ]


def _combined(
    frame: pd.DataFrame,
    plans: list[
        dict[str, Any]
    ],
) -> pd.DataFrame:
    indices = [
        index
        for plan in plans
        for index in plan[
            "test_indices"
        ]
    ]

    if (
        len(indices)
        != len(set(indices))
    ):
        _fail(
            "oos_test_indices_overlap"
        )

    return frame.loc[
        indices
    ].sort_values(
        [
            "open_time_utc",
            "close_time_utc",
            "trade_sequence",
        ],
        kind="mergesort",
    )


def _positive_fold_rate(
    rows: list[
        dict[str, Any]
    ],
) -> dict[str, Any]:
    positive = [
        row
        for row in rows
        if float(
            row[
                "metrics"
            ][
                "net_pnl"
            ]
        )
        > 0.0
    ]

    return {
        "criterion": (
            "fold_net_pnl_gt_0"
        ),
        "positive_fold_count": (
            len(positive)
        ),
        "fold_count": len(rows),
        "positive_fold_rate": (
            len(positive)
            / len(rows)
        ),
        "positive_periods": [
            row[
                "test_period"
            ]
            for row in positive
        ],
    }


def _lopo(
    frame: pd.DataFrame,
    plans: list[
        dict[str, Any]
    ],
) -> list[dict[str, Any]]:
    rows: list[
        dict[str, Any]
    ] = []

    for excluded in plans:
        kept = [
            plan
            for plan in plans
            if plan[
                "test_period"
            ]
            != excluded[
                "test_period"
            ]
        ]

        rows.append(
            {
                "excluded_period": (
                    excluded[
                        "test_period"
                    ]
                ),
                "retained_period_count": (
                    len(kept)
                ),
                "metrics": (
                    _metrics(
                        _combined(
                            frame,
                            kept,
                        )
                    )
                ),
            }
        )

    return rows


def build_walkforward_report(
    master: OfficialMasterData,
) -> dict[str, Any]:
    method = frozen_method()

    frame, inventory = (
        _population(
            master
        )
    )

    expanding = (
        build_fold_plans(
            frame,
            mode="expanding",
        )
    )

    rolling = (
        build_fold_plans(
            frame,
            mode="rolling",
        )
    )

    expanding_rows = (
        _evaluate(
            frame,
            expanding,
        )
    )

    rolling_rows = (
        _evaluate(
            frame,
            rolling,
        )
    )

    combined = _metrics(
        _combined(
            frame,
            expanding,
        )
    )

    positive = (
        _positive_fold_rate(
            expanding_rows
        )
    )

    worst = min(
        expanding_rows,
        key=lambda row: float(
            row[
                "metrics"
            ][
                "net_pnl"
            ]
        ),
    )

    prefixes = [
        {
            "prefix_fold_count": size,
            "through_period": (
                expanding[
                    size - 1
                ][
                    "test_period"
                ]
            ),
            "metrics": (
                _metrics(
                    _combined(
                        frame,
                        expanding[:size],
                    )
                )
            ),
        }
        for size in range(
            2,
            len(expanding) + 1,
        )
    ]

    rolling_net = np.asarray(
        [
            float(
                row[
                    "metrics"
                ][
                    "net_pnl"
                ]
            )
            for row in rolling_rows
        ],
        dtype=float,
    )

    lopo = _lopo(
        frame,
        expanding,
    )

    profit_factor = (
        combined[
            "profit_factor"
        ]
    )

    criteria = {
        "combined_oos_net_pnl_gt_0": (
            float(
                combined[
                    "net_pnl"
                ]
            )
            > 0.0
        ),
        "combined_oos_profit_factor_gt_1": (
            profit_factor is not None
            and float(
                profit_factor
            )
            > 1.0
        ),
        "minimum_independent_positive_oos_periods_2": (
            int(
                positive[
                    "positive_fold_count"
                ]
            )
            >= 2
        ),
    }

    persistent = all(
        criteria.values()
    )

    classification = (
        "PERSISTENT_RESEARCH_CANDIDATE"
        if persistent
        else "NOT_PERSISTENT_RESEARCH_CANDIDATE"
    )

    return json_safe(
        {
            "schema_version": (
                "official_trades_master_walkforward_v1"
            ),
            "engineering_status": "PASS",
            "wq0_status": "CLOSED",
            "wq1_status": "CLOSED",
            "wq2_status": "PASS",
            "quant_edge_status": (
                classification
            ),
            "decision": (
                "WQ2_TEMPORAL_PERSISTENCE_EVALUATED"
            ),
            "master_sha256": (
                master.audit.master_sha256
            ),
            "method": method,
            "method_hash": (
                method[
                    "method_hash"
                ]
            ),
            "temporal_inventory": (
                inventory
            ),
            "no_cherry_pick_manifest": {
                "all_frozen_expanding_test_periods_evaluated": True,
                "all_frozen_rolling_test_periods_evaluated": True,
                "expanding_test_periods": (
                    list(
                        EXPANDING_TEST_PERIODS
                    )
                ),
                "rolling_test_periods": (
                    list(
                        ROLLING_TEST_PERIODS
                    )
                ),
                "skipped_expanding_periods": [],
                "skipped_rolling_periods": [],
                "random_split_used": False,
                "pnl_used_to_define_folds": False,
                "rule_selection_performed": False,
                "model_training_performed": False,
            },
            "expanding": {
                "fold_count": (
                    len(
                        expanding_rows
                    )
                ),
                "folds": (
                    expanding_rows
                ),
                "combined_oos_metrics": (
                    combined
                ),
                "positive_fold_rate": (
                    positive
                ),
                "worst_fold": {
                    "fold": (
                        worst[
                            "fold"
                        ]
                    ),
                    "test_period": (
                        worst[
                            "test_period"
                        ]
                    ),
                    "net_pnl": (
                        worst[
                            "metrics"
                        ][
                            "net_pnl"
                        ]
                    ),
                    "profit_factor": (
                        worst[
                            "metrics"
                        ][
                            "profit_factor"
                        ]
                    ),
                    "expectancy_mean": (
                        worst[
                            "metrics"
                        ][
                            "expectancy_mean"
                        ]
                    ),
                    "max_drawdown_signed": (
                        worst[
                            "metrics"
                        ][
                            "max_drawdown_signed"
                        ]
                    ),
                },
                "mature_prefix_stability": (
                    prefixes
                ),
                "leave_one_period_out": (
                    lopo
                ),
            },
            "rolling": {
                "fold_count": (
                    len(
                        rolling_rows
                    )
                ),
                "folds": (
                    rolling_rows
                ),
                "positive_fold_rate": (
                    _positive_fold_rate(
                        rolling_rows
                    )
                ),
                "net_pnl_mean": float(
                    rolling_net.mean()
                ),
                "net_pnl_median": float(
                    np.median(
                        rolling_net
                    )
                ),
                "net_pnl_min": float(
                    rolling_net.min()
                ),
                "net_pnl_max": float(
                    rolling_net.max()
                ),
                "net_pnl_std_population": float(
                    rolling_net.std(
                        ddof=0
                    )
                ),
            },
            "purge_accounting": {
                "purge_enabled": True,
                "embargo_enabled": False,
                "embargo_seconds": 0,
                "expanding_purged_rows_total": sum(
                    len(
                        plan[
                            "purged_indices"
                        ]
                    )
                    for plan in expanding
                ),
                "rolling_purged_rows_total": sum(
                    len(
                        plan[
                            "purged_indices"
                        ]
                    )
                    for plan in rolling
                ),
            },
            "research_classification": {
                "classification": (
                    classification
                ),
                "criteria": criteria,
                "all_frozen_criteria_pass": (
                    persistent
                ),
                "promotion_allowed": False,
                "operational_authority": False,
            },
            "read_only_master": True,
            "writes_master": False,
            "writes_runtime_trading_state": False,
            "paper_only": True,
            "shadow_only": True,
            "research_only": True,
            "live": False,
            "canary": False,
            "sends_orders": False,
            "exchange_private_access": False,
            "changes_risk": False,
            "changes_model": False,
            "operational_authority": False,
        }
    )


def report_content_sha256(
    payload: dict[str, Any],
) -> str:
    return _stable_hash(
        payload
    )


def _atomic_write(
    path: Path,
    content: bytes,
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    descriptor, temp_name = (
        tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=str(
                path.parent
            ),
        )
    )

    try:
        with os.fdopen(
            descriptor,
            "wb",
        ) as handle:
            handle.write(
                content
            )
            handle.flush()
            os.fsync(
                handle.fileno()
            )

        os.replace(
            temp_name,
            path,
        )

    except Exception:
        try:
            os.unlink(
                temp_name
            )
        except FileNotFoundError:
            pass
        raise


def _csv(
    rows: list[
        dict[str, Any]
    ],
) -> bytes:
    return (
        pd.DataFrame(
            rows
        )
        .to_csv(
            index=False,
            lineterminator="\n",
        )
        .encode("utf-8")
    )


def persist_walkforward_reports(
    payload: dict[str, Any],
    *,
    output_dir: str | Path,
) -> dict[str, str]:
    target = Path(
        output_dir
    )

    fold_rows = []

    for row in [
        *payload[
            "expanding"
        ][
            "folds"
        ],
        *payload[
            "rolling"
        ][
            "folds"
        ],
    ]:
        metrics = row[
            "metrics"
        ]

        fold_rows.append(
            {
                "mode": row[
                    "mode"
                ],
                "fold": row[
                    "fold"
                ],
                "test_period": (
                    row[
                        "test_period"
                    ]
                ),
                "train_candidate_rows": (
                    row[
                        "train_candidate_rows"
                    ]
                ),
                "purged_rows": (
                    row[
                        "purged_rows"
                    ]
                ),
                "train_rows_after_purge": (
                    row[
                        "train_rows_after_purge"
                    ]
                ),
                "test_rows": (
                    row[
                        "test_rows"
                    ]
                ),
                "net_pnl": (
                    metrics[
                        "net_pnl"
                    ]
                ),
                "profit_factor": (
                    metrics[
                        "profit_factor"
                    ]
                ),
                "win_rate": (
                    metrics[
                        "win_rate"
                    ]
                ),
                "expectancy_mean": (
                    metrics[
                        "expectancy_mean"
                    ]
                ),
                "max_drawdown_signed": (
                    metrics[
                        "max_drawdown_signed"
                    ]
                ),
            }
        )

    lopo_rows = []

    for row in payload[
        "expanding"
    ][
        "leave_one_period_out"
    ]:
        metrics = row[
            "metrics"
        ]

        lopo_rows.append(
            {
                "excluded_period": (
                    row[
                        "excluded_period"
                    ]
                ),
                "retained_period_count": (
                    row[
                        "retained_period_count"
                    ]
                ),
                "trades": (
                    metrics[
                        "trades"
                    ]
                ),
                "net_pnl": (
                    metrics[
                        "net_pnl"
                    ]
                ),
                "profit_factor": (
                    metrics[
                        "profit_factor"
                    ]
                ),
                "win_rate": (
                    metrics[
                        "win_rate"
                    ]
                ),
                "expectancy_mean": (
                    metrics[
                        "expectancy_mean"
                    ]
                ),
                "max_drawdown_signed": (
                    metrics[
                        "max_drawdown_signed"
                    ]
                ),
            }
        )

    combined = payload[
        "expanding"
    ][
        "combined_oos_metrics"
    ]

    positive = payload[
        "expanding"
    ][
        "positive_fold_rate"
    ]

    markdown = "\n".join(
        [
            "# Official Trades Master Walk-Forward V1",
            "",
            (
                "- WQ2 status: "
                f"`{payload['wq2_status']}`"
            ),
            (
                "- Quant edge status: "
                f"`{payload['quant_edge_status']}`"
            ),
            (
                "- Method hash: "
                f"`{payload['method_hash']}`"
            ),
            (
                "- OOS trades: "
                f"`{combined['trades']}`"
            ),
            (
                "- OOS Net PnL: "
                f"`{combined['net_pnl']:.6f}` USDT"
            ),
            (
                "- OOS Profit Factor: "
                f"`{combined['profit_factor']}`"
            ),
            (
                "- OOS expectancy: "
                f"`{combined['expectancy_mean']}` USDT/trade"
            ),
            (
                "- Positive fold rate: "
                f"`{positive['positive_fold_rate']}`"
            ),
            "",
            (
                "PAPER / SHADOW / RESEARCH ONLY. "
                "No promotion or operational authority."
            ),
            "",
        ]
    )

    files = {
        REPORT_JSON: (
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8"),
        REPORT_MD: (
            markdown.encode(
                "utf-8"
            )
        ),
        FOLDS_CSV: _csv(
            fold_rows
        ),
        LOPO_CSV: _csv(
            lopo_rows
        ),
    }

    written: dict[
        str,
        str,
    ] = {}

    for (
        name,
        content,
    ) in files.items():
        path = target / name

        _atomic_write(
            path,
            content,
        )

        written[
            name
        ] = str(path)

    manifest = [
        {
            "file": name,
            "sha256": hashlib.sha256(
                (
                    target
                    / name
                ).read_bytes()
            ).hexdigest(),
            "size_bytes": (
                target
                / name
            ).stat().st_size,
        }
        for name in sorted(
            files
        )
    ]

    manifest_path = (
        target
        / MANIFEST_CSV
    )

    _atomic_write(
        manifest_path,
        _csv(
            manifest
        ),
    )

    written[
        MANIFEST_CSV
    ] = str(
        manifest_path
    )

    return written


__all__ = [
    "EXPANDING_TEST_PERIODS",
    "FOLDS_CSV",
    "LOPO_CSV",
    "MANIFEST_CSV",
    "OfficialWalkForwardValidationError",
    "REPORT_JSON",
    "REPORT_MD",
    "ROLLING_TEST_PERIODS",
    "WQ2_METHOD_HASH",
    "WQ2_PERIODS",
    "build_fold_plans",
    "build_walkforward_report",
    "frozen_method",
    "persist_walkforward_reports",
    "report_content_sha256",
]
