"""Frozen WQ3 segment persistence analysis for the official post-OCR master."""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from smartcrypto.analysis.paper_financial_performance import (
    compute_financial_metrics,
    json_safe,
)

from .contracts import (
    DURATION_BUCKET_EDGES_SECONDS,
    DURATION_BUCKET_LABELS,
    LEGACY_OPENING_SENTINEL,
    OFFICIAL_MASTER_SHA256,
)
from .loader import OfficialMasterData
from .walkforward import WQ2_METHOD_HASH


WQ3_METHOD_HASH = (
    "b6429be5a76b210f90bc1cd3d5ada678"
    "5ae4693a0a66e980ffd2bdf2444ce6a2"
)

OOS_PERIODS: tuple[str, ...] = (
    "2026-03",
    "2026-04",
    "2026-05",
    "2026-06",
    "2026-07",
    "2026-08",
)

SEGMENT_FAMILIES: tuple[tuple[str, ...], ...] = (
    ("symbol",),
    ("side",),
    ("open_hour_utc",),
    ("duration_bucket",),
    ("symbol", "side"),
    ("symbol", "side", "open_hour_utc"),
    ("symbol", "side", "duration_bucket"),
)

REPORT_JSON = (
    "OFFICIAL_TRADES_MASTER_SEGMENT_PERSISTENCE_V1.json"
)
REPORT_MD = (
    "OFFICIAL_TRADES_MASTER_SEGMENT_PERSISTENCE_V1.md"
)
SEGMENTS_CSV = (
    "OFFICIAL_TRADES_MASTER_SEGMENT_PERSISTENCE_SEGMENTS_V1.csv"
)
FAMILIES_CSV = (
    "OFFICIAL_TRADES_MASTER_SEGMENT_PERSISTENCE_FAMILIES_V1.csv"
)
MANIFEST_CSV = (
    "OFFICIAL_TRADES_MASTER_SEGMENT_PERSISTENCE_"
    "SHA256_MANIFEST_V1.csv"
)


class OfficialSegmentPersistenceError(ValueError):
    """Fail-closed WQ3 validation error."""

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
    raise OfficialSegmentPersistenceError(
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
    method: dict[str, Any] = {
        "schema_version": (
            "official_trades_master_segment_persistence_method_v1"
        ),
        "parent_wq2_method_hash": WQ2_METHOD_HASH,
        "classification_population": {
            "source": "WQ2 expanding OOS test folds only",
            "periods": list(OOS_PERIODS),
            "expected_oos_rows": 3066,
            "training_periods_excluded_from_classification": [
                "2026-01",
                "2026-02",
            ],
        },
        "dimensions": {
            "symbol": {
                "role": "entry_context",
            },
            "side": {
                "role": "entry_context",
            },
            "open_hour_utc": {
                "role": "entry_context",
                "timestamp": "horario_abertura",
                "timezone": "UTC",
                "range": list(range(24)),
            },
            "duration_bucket": {
                "role": "outcome_diagnostic_only",
                "operational_feature_allowed": False,
                "edges_seconds": [
                    "-inf",
                    900,
                    1800,
                    3600,
                    10800,
                    21600,
                    "+inf",
                ],
                "labels": list(
                    DURATION_BUCKET_LABELS
                ),
                "right_closed": False,
            },
        },
        "segment_families": [
            list(family)
            for family in SEGMENT_FAMILIES
        ],
        "sample_policy": {
            "minimum_total_n_for_report": 30,
            "minimum_total_n_for_high_confidence_label": 100,
            "minimum_fold_n_for_supported_fold": 10,
            "minimum_supported_folds_for_inference": 2,
            "minimum_positive_supported_folds_for_persistent": 2,
            "minimum_positive_fold_rate_for_persistent": (
                2.0 / 3.0
            ),
        },
        "bootstrap_policy": {
            "enabled": True,
            "method": "iid_percentile_descriptive_only",
            "replicates": 2000,
            "seed": 42,
            "confidence_level": 0.95,
            "metrics": [
                "expectancy_mean",
                "win_rate",
            ],
            "time_series_robustness_deferred_to": "WQ5",
        },
        "outlier_policy": {
            "top_fraction_to_remove": 0.05,
            "recompute_metrics_after_removal": True,
            "persistent_candidate_requires_removed_net_pnl_gt": 0.0,
            "persistent_candidate_requires_removed_profit_factor_gt": 1.0,
        },
        "temporal_dependency_policy": {
            "leave_best_period_out": True,
            "persistent_candidate_requires_lbo_net_pnl_gt": 0.0,
            "persistent_candidate_requires_lbo_profit_factor_gt": 1.0,
            "worst_fold_must_be_reported": True,
            "negative_folds_must_not_be_hidden": True,
        },
        "classification_order": [
            "LOW_SAMPLE",
            "PERSISTENT_CANDIDATE",
            "NEGATIVE",
            "REGIME_DEPENDENT",
            "WEAK_POSITIVE",
        ],
        "classification_rules": {
            "LOW_SAMPLE": {
                "condition": (
                    "oos_n < 30 OR supported_fold_count < 2"
                ),
            },
            "PERSISTENT_CANDIDATE": {
                "requires": [
                    "oos_n >= 100",
                    "combined_oos_net_pnl > 0",
                    "combined_oos_profit_factor > 1",
                    "supported_positive_fold_count >= 2",
                    "supported_positive_fold_rate >= 2/3",
                    "top_5_percent_removed_net_pnl > 0",
                    "top_5_percent_removed_profit_factor > 1",
                    "leave_best_period_out_net_pnl > 0",
                    "leave_best_period_out_profit_factor > 1",
                ],
            },
            "NEGATIVE": {
                "requires": [
                    "combined_oos_expectancy < 0",
                    "combined_oos_profit_factor < 1",
                    "supported_negative_fold_count >= 2",
                ],
            },
            "REGIME_DEPENDENT": {
                "requires": [
                    "combined_oos_net_pnl > 0",
                    "combined_oos_profit_factor > 1",
                    (
                        "leave_best_period_out fails positive/PF gate "
                        "OR supported_positive_fold_count < 2"
                    ),
                ],
            },
            "WEAK_POSITIVE": {
                "condition": (
                    "positive aggregate evidence exists but persistent "
                    "high-confidence gates are not all satisfied"
                ),
            },
        },
        "independence_policy": {
            "overlapping_segment_families_are_not_independent_edges": True,
            "candidate_counts_reported_per_family": True,
            "cross_family_edge_counts_must_not_be_summed": True,
        },
        "hypothesis_policy": {
            "outputs_are_research_hypotheses_only": True,
            "automatic_filter_activation": False,
            "automatic_weighting_activation": False,
            "freqtrade_change": False,
            "risk_manager_change": False,
            "model_change": False,
        },
        "uses_segment_pnl_to_define_method": False,
        "paper_only": True,
        "shadow_only": True,
        "research_only": True,
        "live": False,
        "canary": False,
        "sends_orders": False,
        "changes_risk": False,
        "changes_model": False,
        "operational_authority": False,
    }

    actual = _stable_hash(
        method
    )

    if actual != WQ3_METHOD_HASH:
        _fail(
            "wq3_frozen_method_hash_mismatch",
            expected=WQ3_METHOD_HASH,
            actual=actual,
        )

    return {
        **method,
        "method_hash": actual,
    }


def _validate_parent(
    parent: Mapping[str, Any],
) -> None:
    if parent.get("status") != "ok":
        _fail(
            "parent_wq2_status_not_ok",
            status=parent.get("status"),
        )

    if parent.get("wq2_status") != "PASS":
        _fail(
            "parent_wq2_gate_not_pass",
            status=parent.get("wq2_status"),
        )

    if (
        parent.get("method_hash")
        != WQ2_METHOD_HASH
    ):
        _fail(
            "parent_wq2_method_hash_mismatch",
            expected=WQ2_METHOD_HASH,
            actual=parent.get("method_hash"),
        )

    if (
        parent.get("master_sha256")
        != OFFICIAL_MASTER_SHA256
    ):
        _fail(
            "parent_wq2_master_hash_mismatch",
            actual=parent.get("master_sha256"),
        )

    no_cherry = parent.get(
        "no_cherry_pick_manifest"
    )

    if isinstance(
        no_cherry,
        Mapping,
    ):
        if (
            no_cherry.get(
                "random_split_used"
            )
            is not False
        ):
            _fail(
                "parent_wq2_random_split_not_false"
            )

        if (
            no_cherry.get(
                "pnl_used_to_define_folds"
            )
            is not False
        ):
            _fail(
                "parent_wq2_pnl_defined_folds"
            )
    else:
        _fail(
            "parent_wq2_no_cherry_pick_manifest_missing"
        )

    expanding = parent.get(
        "expanding"
    )

    if isinstance(
        expanding,
        Mapping,
    ):
        folds = expanding.get(
            "folds"
        )

        if isinstance(
            folds,
            list,
        ):
            periods = tuple(
                str(
                    fold.get(
                        "test_period"
                    )
                )
                for fold in folds
                if isinstance(
                    fold,
                    Mapping,
                )
            )

            if periods != OOS_PERIODS:
                _fail(
                    "parent_wq2_periods_mismatch",
                    expected=list(
                        OOS_PERIODS
                    ),
                    actual=list(
                        periods
                    ),
                )
        else:
            _fail(
                "parent_wq2_folds_missing"
            )
    else:
        _fail(
            "parent_wq2_expanding_missing"
        )


def _prepare_oos_population(
    master: OfficialMasterData,
) -> pd.DataFrame:
    if master.audit.status != "ok":
        _fail(
            "source_audit_not_ok"
        )

    if (
        master.audit.master_sha256
        != OFFICIAL_MASTER_SHA256
    ):
        _fail(
            "master_hash_not_frozen"
        )

    frame = master.frame

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
        required
        - set(frame.columns)
    )

    if missing:
        _fail(
            "wq3_required_columns_missing",
            columns=missing,
        )

    raw_open = (
        frame[
            "horario_abertura"
        ]
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
        frame[
            "horario_fechamento"
        ],
        format="%Y-%m-%d %H:%M:%S",
        errors="coerce",
        utc=True,
    )

    invalid_open = (
        ~sentinel
        & open_ts.isna()
    )

    if invalid_open.any():
        _fail(
            "unexpected_invalid_open_time",
            count=int(
                invalid_open.sum()
            ),
        )

    if close_ts.isna().any():
        _fail(
            "invalid_close_time",
            count=int(
                close_ts.isna().sum()
            ),
        )

    eligible = open_ts.notna()

    if (
        int(
            eligible.sum()
        )
        != 3760
    ):
        _fail(
            "wq3_eligible_population_drift",
            actual=int(
                eligible.sum()
            ),
        )

    regimes = (
        frame[
            "fee_semantics_regime"
        ]
        .astype("string")
        .str.strip()
    )

    if not regimes.loc[
        eligible
    ].eq(
        "STANDARD"
    ).all():
        _fail(
            "wq3_eligible_not_standard"
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
            "wq3_pnl_invalid"
        )

    if sequence.isna().any():
        _fail(
            "wq3_trade_sequence_invalid"
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
                .str.strip()
                .to_numpy()
            ),
            "side": (
                frame.loc[
                    eligible,
                    "side",
                ]
                .astype("string")
                .str.strip()
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

    working[
        "period"
    ] = (
        working[
            "open_time_utc"
        ]
        .dt.strftime(
            "%Y-%m"
        )
    )

    working[
        "open_hour_utc"
    ] = (
        working[
            "open_time_utc"
        ]
        .dt.hour
        .astype(int)
    )

    working[
        "duration_seconds"
    ] = (
        working[
            "close_time_utc"
        ]
        - working[
            "open_time_utc"
        ]
    ).dt.total_seconds()

    if (
        working[
            "duration_seconds"
        ] < 0
    ).any():
        _fail(
            "wq3_negative_duration"
        )

    working[
        "duration_bucket"
    ] = pd.cut(
        working[
            "duration_seconds"
        ],
        bins=list(
            DURATION_BUCKET_EDGES_SECONDS
        ),
        labels=list(
            DURATION_BUCKET_LABELS
        ),
        right=False,
    )

    oos = working.loc[
        working[
            "period"
        ].isin(
            OOS_PERIODS
        )
    ].copy()

    if len(oos) != 3066:
        _fail(
            "wq3_oos_row_count_mismatch",
            expected=3066,
            actual=len(oos),
        )

    periods = tuple(
        sorted(
            oos[
                "period"
            ]
            .unique()
            .tolist()
        )
    )

    if periods != OOS_PERIODS:
        _fail(
            "wq3_oos_periods_mismatch",
            expected=list(
                OOS_PERIODS
            ),
            actual=list(
                periods
            ),
        )

    return oos.reset_index(
        drop=True
    )


def _metrics(
    frame: pd.DataFrame,
) -> dict[str, Any]:
    if frame.empty:
        return {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "net_pnl": 0.0,
            "gross_profit": 0.0,
            "gross_loss": 0.0,
            "profit_factor": None,
            "profit_factor_status": "no_trades",
            "win_rate": None,
            "expectancy_mean": None,
            "expectancy_median": None,
            "payoff_ratio": None,
            "max_drawdown_signed": None,
            "max_drawdown_magnitude": None,
        }

    pnl = pd.to_numeric(
        frame[
            "economic_net_pnl"
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
            "segment_pnl_invalid"
        )

    metrics = (
        compute_financial_metrics(
            pd.DataFrame(
                {
                    "__pnl": pnl,
                }
            )
        )
    )

    cumulative = pnl.cumsum()

    drawdown = (
        cumulative
        - cumulative.cummax()
    )

    return {
        "trades": int(
            metrics[
                "trades"
            ]
        ),
        "wins": int(
            metrics[
                "wins"
            ]
        ),
        "losses": int(
            metrics[
                "losses"
            ]
        ),
        "net_pnl": float(
            metrics[
                "total_pnl"
            ]
        ),
        "gross_profit": float(
            metrics[
                "gross_profit"
            ]
        ),
        "gross_loss": float(
            metrics[
                "gross_loss"
            ]
        ),
        "profit_factor": (
            metrics[
                "profit_factor"
            ]
        ),
        "profit_factor_status": (
            metrics[
                "profit_factor_status"
            ]
        ),
        "win_rate": (
            metrics[
                "win_rate"
            ]
        ),
        "expectancy_mean": (
            metrics[
                "expectancy"
            ]
        ),
        "expectancy_median": (
            metrics[
                "median_return"
            ]
        ),
        "payoff_ratio": (
            metrics[
                "payoff_ratio"
            ]
        ),
        "max_drawdown_signed": float(
            drawdown.min()
        ),
        "max_drawdown_magnitude": (
            metrics[
                "max_drawdown"
            ]
        ),
    }


def _profit_factor_gt_one(
    metrics: Mapping[str, Any],
) -> bool:
    value = metrics.get(
        "profit_factor"
    )

    if value is not None:
        return float(
            value
        ) > 1.0

    return (
        metrics.get(
            "profit_factor_status"
        )
        == "no_losses"
        and float(
            metrics.get(
                "net_pnl",
                0.0,
            )
        )
        > 0.0
    )


def _profit_factor_lt_one(
    metrics: Mapping[str, Any],
) -> bool:
    value = metrics.get(
        "profit_factor"
    )

    if value is None:
        return False

    return float(
        value
    ) < 1.0


def _segment_id(
    family: Sequence[str],
    values: Mapping[str, Any],
) -> str:
    payload = {
        "family": list(
            family
        ),
        "values": {
            key: values[
                key
            ]
            for key in family
        },
    }

    return (
        "segment_"
        + _stable_hash(
            payload
        )[:24]
    )


def _bootstrap_ci(
    pnl: np.ndarray,
    *,
    segment_id: str,
) -> dict[str, Any]:
    if pnl.size == 0:
        return {
            "replicates": 0,
            "confidence_level": 0.95,
            "expectancy_mean_ci95": [
                None,
                None,
            ],
            "win_rate_ci95": [
                None,
                None,
            ],
        }

    segment_seed = (
        42
        + (
            int(
                hashlib.sha256(
                    segment_id.encode(
                        "utf-8"
                    )
                ).hexdigest()[:8],
                16,
            )
            % 1_000_000_000
        )
    )

    rng = np.random.default_rng(
        segment_seed
    )

    replicates = 2000
    batch_size = 200

    means: list[
        np.ndarray
    ] = []

    win_rates: list[
        np.ndarray
    ] = []

    completed = 0

    while completed < replicates:
        current = min(
            batch_size,
            replicates - completed,
        )

        indices = rng.integers(
            0,
            pnl.size,
            size=(
                current,
                pnl.size,
            ),
        )

        samples = pnl[
            indices
        ]

        means.append(
            samples.mean(
                axis=1
            )
        )

        win_rates.append(
            (
                samples > 0.0
            ).mean(
                axis=1
            )
        )

        completed += current

    mean_values = np.concatenate(
        means
    )

    win_values = np.concatenate(
        win_rates
    )

    return {
        "method": (
            "iid_percentile_descriptive_only"
        ),
        "replicates": replicates,
        "seed_base": 42,
        "segment_seed": segment_seed,
        "confidence_level": 0.95,
        "expectancy_mean_ci95": [
            float(
                np.quantile(
                    mean_values,
                    0.025,
                )
            ),
            float(
                np.quantile(
                    mean_values,
                    0.975,
                )
            ),
        ],
        "win_rate_ci95": [
            float(
                np.quantile(
                    win_values,
                    0.025,
                )
            ),
            float(
                np.quantile(
                    win_values,
                    0.975,
                )
            ),
        ],
    }


def _remove_top_fraction(
    frame: pd.DataFrame,
) -> tuple[pd.DataFrame, int]:
    if frame.empty:
        return (
            frame.copy(),
            0,
        )

    remove_count = max(
        1,
        int(
            math.ceil(
                len(frame)
                * 0.05
            )
        ),
    )

    ordered = frame.sort_values(
        [
            "economic_net_pnl",
            "trade_sequence",
        ],
        ascending=[
            False,
            True,
        ],
        kind="mergesort",
    )

    removed_indices = set(
        ordered.head(
            remove_count
        ).index.tolist()
    )

    retained = frame.loc[
        ~frame.index.isin(
            removed_indices
        )
    ].copy()

    return (
        retained,
        remove_count,
    )


def _period_rows(
    frame: pd.DataFrame,
) -> list[dict[str, Any]]:
    rows: list[
        dict[str, Any]
    ] = []

    for period in OOS_PERIODS:
        subset = frame.loc[
            frame[
                "period"
            ]
            == period
        ]

        rows.append(
            {
                "period": period,
                "n": int(
                    len(
                        subset
                    )
                ),
                "supported": (
                    len(
                        subset
                    )
                    >= 10
                ),
                "metrics": (
                    _metrics(
                        subset
                    )
                ),
            }
        )

    return rows


def _leave_best_period_out(
    frame: pd.DataFrame,
    period_rows: list[
        dict[str, Any]
    ],
) -> dict[str, Any]:
    observed = [
        row
        for row in period_rows
        if row[
            "n"
        ]
        > 0
    ]

    if len(
        observed
    ) <= 1:
        return {
            "best_period": None,
            "remaining_period_count": 0,
            "metrics": _metrics(
                frame.iloc[
                    0:0
                ]
            ),
        }

    best = max(
        observed,
        key=lambda row: float(
            row[
                "metrics"
            ][
                "net_pnl"
            ]
        ),
    )

    best_period = str(
        best[
            "period"
        ]
    )

    retained = frame.loc[
        frame[
            "period"
        ]
        != best_period
    ]

    return {
        "best_period": (
            best_period
        ),
        "best_period_net_pnl": (
            best[
                "metrics"
            ][
                "net_pnl"
            ]
        ),
        "remaining_period_count": int(
            retained[
                "period"
            ].nunique()
        ),
        "metrics": (
            _metrics(
                retained
            )
        ),
    }


def _classify(
    *,
    n: int,
    metrics: Mapping[str, Any],
    supported_fold_count: int,
    positive_supported_fold_count: int,
    negative_supported_fold_count: int,
    positive_supported_fold_rate: float | None,
    removed_metrics: Mapping[str, Any],
    leave_best_metrics: Mapping[str, Any],
) -> tuple[str, dict[str, bool]]:
    high_confidence_n = (
        n >= 100
    )

    combined_positive = (
        float(
            metrics[
                "net_pnl"
            ]
        )
        > 0.0
    )

    combined_pf_gt_one = (
        _profit_factor_gt_one(
            metrics
        )
    )

    removed_positive = (
        float(
            removed_metrics[
                "net_pnl"
            ]
        )
        > 0.0
    )

    removed_pf_gt_one = (
        _profit_factor_gt_one(
            removed_metrics
        )
    )

    lbo_positive = (
        float(
            leave_best_metrics[
                "net_pnl"
            ]
        )
        > 0.0
    )

    lbo_pf_gt_one = (
        _profit_factor_gt_one(
            leave_best_metrics
        )
    )

    persistent_gates = {
        "oos_n_ge_100": (
            high_confidence_n
        ),
        "combined_oos_net_pnl_gt_0": (
            combined_positive
        ),
        "combined_oos_profit_factor_gt_1": (
            combined_pf_gt_one
        ),
        "supported_positive_fold_count_ge_2": (
            positive_supported_fold_count
            >= 2
        ),
        "supported_positive_fold_rate_ge_2_over_3": (
            positive_supported_fold_rate
            is not None
            and positive_supported_fold_rate
            >= (
                2.0
                / 3.0
            )
        ),
        "top_5_percent_removed_net_pnl_gt_0": (
            removed_positive
        ),
        "top_5_percent_removed_profit_factor_gt_1": (
            removed_pf_gt_one
        ),
        "leave_best_period_out_net_pnl_gt_0": (
            lbo_positive
        ),
        "leave_best_period_out_profit_factor_gt_1": (
            lbo_pf_gt_one
        ),
    }

    if (
        n < 30
        or supported_fold_count < 2
    ):
        return (
            "LOW_SAMPLE",
            persistent_gates,
        )

    if all(
        persistent_gates.values()
    ):
        return (
            "PERSISTENT_CANDIDATE",
            persistent_gates,
        )

    expectancy = metrics.get(
        "expectancy_mean"
    )

    negative_rule = (
        expectancy is not None
        and float(
            expectancy
        )
        < 0.0
        and _profit_factor_lt_one(
            metrics
        )
        and negative_supported_fold_count
        >= 2
    )

    if negative_rule:
        return (
            "NEGATIVE",
            persistent_gates,
        )

    regime_dependent = (
        combined_positive
        and combined_pf_gt_one
        and (
            not (
                lbo_positive
                and lbo_pf_gt_one
            )
            or positive_supported_fold_count
            < 2
        )
    )

    if regime_dependent:
        return (
            "REGIME_DEPENDENT",
            persistent_gates,
        )

    positive_evidence = (
        combined_positive
        or (
            expectancy is not None
            and float(
                expectancy
            )
            > 0.0
        )
        or combined_pf_gt_one
    )

    if positive_evidence:
        return (
            "WEAK_POSITIVE",
            persistent_gates,
        )

    return (
        "UNCLASSIFIED_FROZEN_RULE_GAP",
        persistent_gates,
    )


def _segment_report(
    frame: pd.DataFrame,
    *,
    family: tuple[str, ...],
    values: Mapping[str, Any],
) -> dict[str, Any]:
    identifier = _segment_id(
        family,
        values,
    )

    metrics = _metrics(
        frame
    )

    periods = _period_rows(
        frame
    )

    supported = [
        row
        for row in periods
        if row[
            "supported"
        ]
    ]

    positive_supported = [
        row
        for row in supported
        if float(
            row[
                "metrics"
            ][
                "net_pnl"
            ]
        )
        > 0.0
    ]

    negative_supported = [
        row
        for row in supported
        if float(
            row[
                "metrics"
            ][
                "net_pnl"
            ]
        )
        < 0.0
    ]

    positive_rate = (
        len(
            positive_supported
        )
        / len(
            supported
        )
        if supported
        else None
    )

    worst = (
        min(
            supported,
            key=lambda row: float(
                row[
                    "metrics"
                ][
                    "net_pnl"
                ]
            ),
        )
        if supported
        else None
    )

    retained, removed_count = (
        _remove_top_fraction(
            frame
        )
    )

    removed_metrics = (
        _metrics(
            retained
        )
    )

    lbo = (
        _leave_best_period_out(
            frame,
            periods,
        )
    )

    lbo_metrics = lbo[
        "metrics"
    ]

    pnl_array = pd.to_numeric(
        frame[
            "economic_net_pnl"
        ],
        errors="raise",
    ).to_numpy(
        dtype=float
    )

    bootstrap = (
        _bootstrap_ci(
            pnl_array,
            segment_id=identifier,
        )
    )

    classification, gates = (
        _classify(
            n=len(
                frame
            ),
            metrics=metrics,
            supported_fold_count=len(
                supported
            ),
            positive_supported_fold_count=len(
                positive_supported
            ),
            negative_supported_fold_count=len(
                negative_supported
            ),
            positive_supported_fold_rate=(
                positive_rate
            ),
            removed_metrics=(
                removed_metrics
            ),
            leave_best_metrics=(
                lbo_metrics
            ),
        )
    )

    duration_diagnostic = (
        "duration_bucket"
        in family
    )

    return {
        "segment_id": (
            identifier
        ),
        "family": list(
            family
        ),
        "family_key": "+".join(
            family
        ),
        "values": dict(
            values
        ),
        "oos_n": int(
            len(
                frame
            )
        ),
        "reportable_n_ge_30": (
            len(
                frame
            )
            >= 30
        ),
        "high_confidence_n_ge_100": (
            len(
                frame
            )
            >= 100
        ),
        "classification": (
            classification
        ),
        "metrics": metrics,
        "period_metrics": (
            periods
        ),
        "supported_fold_count": (
            len(
                supported
            )
        ),
        "supported_positive_fold_count": (
            len(
                positive_supported
            )
        ),
        "supported_negative_fold_count": (
            len(
                negative_supported
            )
        ),
        "supported_positive_fold_rate": (
            positive_rate
        ),
        "worst_supported_fold": (
            None
            if worst is None
            else {
                "period": (
                    worst[
                        "period"
                    ]
                ),
                "n": (
                    worst[
                        "n"
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
            }
        ),
        "top_5_percent_removed": {
            "removed_trade_count": (
                removed_count
            ),
            "retained_trade_count": (
                len(
                    retained
                )
            ),
            "metrics": (
                removed_metrics
            ),
        },
        "leave_best_period_out": (
            lbo
        ),
        "bootstrap": (
            bootstrap
        ),
        "persistent_gate_results": (
            gates
        ),
        "duration_bucket_is_outcome_diagnostic_only": (
            duration_diagnostic
        ),
        "operational_feature_allowed": (
            not duration_diagnostic
        ),
        "automatic_activation_allowed": False,
        "operational_authority": False,
    }


def _normalize_group_key(
    key: Any,
    family: tuple[str, ...],
) -> dict[str, Any]:
    if len(
        family
    ) == 1:
        values = (
            key,
        )

    elif isinstance(
        key,
        tuple,
    ):
        values = key

    else:
        _fail(
            "unexpected_group_key_shape",
            family=list(
                family
            ),
        )

    normalized: dict[
        str,
        Any,
    ] = {}

    for (
        column,
        value,
    ) in zip(
        family,
        values,
        strict=True,
    ):
        if pd.isna(
            value
        ):
            normalized[
                column
            ] = "UNKNOWN"

        elif hasattr(
            value,
            "item",
        ):
            try:
                normalized[
                    column
                ] = value.item()

            except (
                TypeError,
                ValueError,
            ):
                normalized[
                    column
                ] = str(
                    value
                )

        else:
            normalized[
                column
            ] = str(
                value
            )

    return normalized


def build_segment_persistence_report(
    master: OfficialMasterData,
    *,
    parent_wq2_report: Mapping[str, Any],
) -> dict[str, Any]:
    method = frozen_method()

    _validate_parent(
        parent_wq2_report
    )

    oos = (
        _prepare_oos_population(
            master
        )
    )

    segments: list[
        dict[str, Any]
    ] = []

    family_summaries: list[
        dict[str, Any]
    ] = []

    for family in SEGMENT_FAMILIES:
        family_segments: list[
            dict[str, Any]
        ] = []

        grouped = oos.groupby(
            list(
                family
            ),
            observed=True,
            dropna=False,
            sort=True,
        )

        for (
            key,
            group,
        ) in grouped:
            values = (
                _normalize_group_key(
                    key,
                    family,
                )
            )

            row = (
                _segment_report(
                    group.copy(),
                    family=family,
                    values=values,
                )
            )

            family_segments.append(
                row
            )

            segments.append(
                row
            )

        classification_counts: dict[
            str,
            int,
        ] = {}

        for row in family_segments:
            classification = str(
                row[
                    "classification"
                ]
            )

            classification_counts[
                classification
            ] = (
                classification_counts.get(
                    classification,
                    0,
                )
                + 1
            )

        family_summaries.append(
            {
                "family": list(
                    family
                ),
                "family_key": (
                    "+".join(
                        family
                    )
                ),
                "segment_count": len(
                    family_segments
                ),
                "reportable_n_ge_30": sum(
                    1
                    for row in family_segments
                    if row[
                        "reportable_n_ge_30"
                    ]
                ),
                "high_confidence_n_ge_100": sum(
                    1
                    for row in family_segments
                    if row[
                        "high_confidence_n_ge_100"
                    ]
                ),
                "persistent_candidate_count": sum(
                    1
                    for row in family_segments
                    if row[
                        "classification"
                    ]
                    == "PERSISTENT_CANDIDATE"
                ),
                "classification_counts": (
                    classification_counts
                ),
            }
        )

    candidate_segments = [
        row
        for row in segments
        if row[
            "classification"
        ]
        == "PERSISTENT_CANDIDATE"
    ]

    entry_context_candidates = [
        row
        for row in candidate_segments
        if not row[
            "duration_bucket_is_outcome_diagnostic_only"
        ]
    ]

    duration_diagnostic_candidates = [
        row
        for row in candidate_segments
        if row[
            "duration_bucket_is_outcome_diagnostic_only"
        ]
    ]

    return json_safe(
        {
            "schema_version": (
                "official_trades_master_segment_persistence_v1"
            ),
            "engineering_status": "PASS",
            "wq0_status": "CLOSED",
            "wq1_status": "CLOSED",
            "wq2_status": "PASS",
            "wq3_status": "PASS",
            "decision": (
                "WQ3_SEGMENT_PERSISTENCE_EVALUATED"
            ),
            "master_sha256": (
                master.audit.master_sha256
            ),
            "parent_wq2_method_hash": (
                WQ2_METHOD_HASH
            ),
            "method": (
                method
            ),
            "method_hash": (
                method[
                    "method_hash"
                ]
            ),
            "oos_rows": int(
                len(
                    oos
                )
            ),
            "oos_periods": list(
                OOS_PERIODS
            ),
            "segment_family_count": (
                len(
                    SEGMENT_FAMILIES
                )
            ),
            "segment_count": len(
                segments
            ),
            "reportable_segment_count": sum(
                1
                for row in segments
                if row[
                    "reportable_n_ge_30"
                ]
            ),
            "high_confidence_segment_count": sum(
                1
                for row in segments
                if row[
                    "high_confidence_n_ge_100"
                ]
            ),
            "persistent_candidate_count": (
                len(
                    candidate_segments
                )
            ),
            "entry_context_persistent_candidate_count": (
                len(
                    entry_context_candidates
                )
            ),
            "duration_diagnostic_persistent_candidate_count": (
                len(
                    duration_diagnostic_candidates
                )
            ),
            "family_summaries": (
                family_summaries
            ),
            "segments": (
                segments
            ),
            "candidate_policy": {
                "research_hypotheses_only": True,
                "cross_family_counts_are_not_independent_edges": True,
                "duration_bucket_candidates_are_not_pretrade_features": True,
                "automatic_filter_activation": False,
                "automatic_weighting_activation": False,
                "promotion_allowed": False,
                "operational_authority": False,
            },
            "paper_only": True,
            "shadow_only": True,
            "research_only": True,
            "live": False,
            "canary": False,
            "sends_orders": False,
            "changes_risk": False,
            "changes_model": False,
            "writes_master": False,
            "writes_runtime_trading_state": False,
            "operational_authority": False,
        }
    )


def report_content_sha256(
    payload: Mapping[str, Any],
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


def _csv_bytes(
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
        .encode(
            "utf-8"
        )
    )


def persist_segment_persistence_reports(
    payload: Mapping[str, Any],
    *,
    output_dir: str | Path,
) -> dict[str, str]:
    target = Path(
        output_dir
    )

    segment_rows: list[
        dict[str, Any]
    ] = []

    raw_segments = payload.get(
        "segments",
        [],
    )

    if not isinstance(
        raw_segments,
        list,
    ):
        _fail(
            "segments_payload_invalid"
        )

    for row in raw_segments:
        if not isinstance(
            row,
            Mapping,
        ):
            continue

        metrics = row.get(
            "metrics",
            {},
        )

        removed = row.get(
            "top_5_percent_removed",
            {},
        )

        removed_metrics = (
            removed.get(
                "metrics",
                {},
            )
            if isinstance(
                removed,
                Mapping,
            )
            else {}
        )

        lbo = row.get(
            "leave_best_period_out",
            {},
        )

        lbo_metrics = (
            lbo.get(
                "metrics",
                {},
            )
            if isinstance(
                lbo,
                Mapping,
            )
            else {}
        )

        segment_rows.append(
            {
                "segment_id": row.get(
                    "segment_id"
                ),
                "family_key": row.get(
                    "family_key"
                ),
                "values_json": json.dumps(
                    row.get(
                        "values",
                        {},
                    ),
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                "oos_n": row.get(
                    "oos_n"
                ),
                "reportable_n_ge_30": row.get(
                    "reportable_n_ge_30"
                ),
                "high_confidence_n_ge_100": row.get(
                    "high_confidence_n_ge_100"
                ),
                "classification": row.get(
                    "classification"
                ),
                "net_pnl": (
                    metrics.get(
                        "net_pnl"
                    )
                    if isinstance(
                        metrics,
                        Mapping,
                    )
                    else None
                ),
                "profit_factor": (
                    metrics.get(
                        "profit_factor"
                    )
                    if isinstance(
                        metrics,
                        Mapping,
                    )
                    else None
                ),
                "win_rate": (
                    metrics.get(
                        "win_rate"
                    )
                    if isinstance(
                        metrics,
                        Mapping,
                    )
                    else None
                ),
                "expectancy_mean": (
                    metrics.get(
                        "expectancy_mean"
                    )
                    if isinstance(
                        metrics,
                        Mapping,
                    )
                    else None
                ),
                "supported_fold_count": row.get(
                    "supported_fold_count"
                ),
                "supported_positive_fold_count": row.get(
                    "supported_positive_fold_count"
                ),
                "supported_negative_fold_count": row.get(
                    "supported_negative_fold_count"
                ),
                "supported_positive_fold_rate": row.get(
                    "supported_positive_fold_rate"
                ),
                "top5_removed_net_pnl": (
                    removed_metrics.get(
                        "net_pnl"
                    )
                    if isinstance(
                        removed_metrics,
                        Mapping,
                    )
                    else None
                ),
                "top5_removed_profit_factor": (
                    removed_metrics.get(
                        "profit_factor"
                    )
                    if isinstance(
                        removed_metrics,
                        Mapping,
                    )
                    else None
                ),
                "leave_best_period": (
                    lbo.get(
                        "best_period"
                    )
                    if isinstance(
                        lbo,
                        Mapping,
                    )
                    else None
                ),
                "leave_best_net_pnl": (
                    lbo_metrics.get(
                        "net_pnl"
                    )
                    if isinstance(
                        lbo_metrics,
                        Mapping,
                    )
                    else None
                ),
                "leave_best_profit_factor": (
                    lbo_metrics.get(
                        "profit_factor"
                    )
                    if isinstance(
                        lbo_metrics,
                        Mapping,
                    )
                    else None
                ),
                "duration_outcome_only": row.get(
                    "duration_bucket_is_outcome_diagnostic_only"
                ),
                "automatic_activation_allowed": False,
            }
        )

    family_rows: list[
        dict[str, Any]
    ] = []

    raw_families = payload.get(
        "family_summaries",
        [],
    )

    if not isinstance(
        raw_families,
        list,
    ):
        _fail(
            "family_summaries_payload_invalid"
        )

    for row in raw_families:
        if not isinstance(
            row,
            Mapping,
        ):
            continue

        family_rows.append(
            {
                "family_key": row.get(
                    "family_key"
                ),
                "segment_count": row.get(
                    "segment_count"
                ),
                "reportable_n_ge_30": row.get(
                    "reportable_n_ge_30"
                ),
                "high_confidence_n_ge_100": row.get(
                    "high_confidence_n_ge_100"
                ),
                "persistent_candidate_count": row.get(
                    "persistent_candidate_count"
                ),
                "classification_counts_json": json.dumps(
                    row.get(
                        "classification_counts",
                        {},
                    ),
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            }
        )

    markdown_lines = [
        "# Official Trades Master Segment Persistence V1",
        "",
        (
            "- WQ3 status: "
            f"`{payload.get('wq3_status')}`"
        ),
        (
            "- Method hash: "
            f"`{payload.get('method_hash')}`"
        ),
        (
            "- OOS rows: "
            f"`{payload.get('oos_rows')}`"
        ),
        (
            "- Reportable segments: "
            f"`{payload.get('reportable_segment_count')}`"
        ),
        (
            "- High-confidence segments: "
            f"`{payload.get('high_confidence_segment_count')}`"
        ),
        (
            "- Persistent research candidates: "
            f"`{payload.get('persistent_candidate_count')}`"
        ),
        "",
        (
            "All candidates are research hypotheses only. "
            "No automatic filter, weighting, promotion, "
            "RiskManager or Freqtrade authority."
        ),
        "",
    ]

    files: dict[
        str,
        bytes,
    ] = {
        REPORT_JSON: (
            json.dumps(
                json_safe(
                    dict(
                        payload
                    )
                ),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n"
        ).encode(
            "utf-8"
        ),
        REPORT_MD: (
            "\n".join(
                markdown_lines
            )
            .encode(
                "utf-8"
            )
        ),
        SEGMENTS_CSV: (
            _csv_bytes(
                segment_rows
            )
        ),
        FAMILIES_CSV: (
            _csv_bytes(
                family_rows
            )
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
        path = (
            target
            / name
        )

        _atomic_write(
            path,
            content,
        )

        written[
            name
        ] = str(
            path
        )

    manifest_rows = [
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
        _csv_bytes(
            manifest_rows
        ),
    )

    written[
        MANIFEST_CSV
    ] = str(
        manifest_path
    )

    return written


__all__ = [
    "FAMILIES_CSV",
    "MANIFEST_CSV",
    "OOS_PERIODS",
    "OfficialSegmentPersistenceError",
    "REPORT_JSON",
    "REPORT_MD",
    "SEGMENTS_CSV",
    "SEGMENT_FAMILIES",
    "WQ3_METHOD_HASH",
    "build_segment_persistence_report",
    "frozen_method",
    "persist_segment_persistence_reports",
    "report_content_sha256",
]
