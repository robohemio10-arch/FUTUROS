"""Canonical frozen WQ5 Monte Carlo and risk-stress research engine.

This module evaluates the official post-OCR master under the frozen WQ5 V2
methodology plus the V3 corrective incremental execution-cost semantics.

PAPER / SHADOW / RESEARCH ONLY.
No operational authority, no order submission, no RiskManager mutation.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, NoReturn, TypeAlias, cast

import numpy as np
import pandas as pd
from numpy.typing import NDArray

from smartcrypto.research.execution_costs import (
    infer_notional_details,
)

from .contracts import OFFICIAL_MASTER_SHA256
from .loader import (
    OfficialMasterData,
    sha256_file,
)
from .regime_concentration import (
    build_labeled_oos_regime_frame,
)
from .risk_of_ruin import (
    BUFFER_MULTIPLES,
    calculate_path_metrics,
    historical_max_drawdown,
    primary_ruin_probability,
    summarize_path_metrics,
)
from .segment_persistence import OOS_PERIODS


FloatArray: TypeAlias = NDArray[np.float64]
IntArray: TypeAlias = NDArray[np.int64]
StringArray: TypeAlias = NDArray[np.str_]
Sampler: TypeAlias = Callable[
    [np.random.Generator],
    FloatArray,
]


SCHEMA_VERSION = "official_trades_master_monte_carlo_risk_v1"

WQ5_METHOD_V2_HASH = "e837eda4f95adbf4bc725ac7113b56a7562917eb912cc98442b2cbebf96dd6a0"

WQ5_METHOD_V3_HASH = "ddd83887fae177d63f7b7e826f49bc402585d616417bba161a478b34ed60700a"

METHOD_FREEZE_V2_FILE_SHA256 = "62bf92f8eb66f35e43844bb81a9430099d30e121ca89c22b189646a4d3aaf0ec"

METHOD_FREEZE_V3_FILE_SHA256 = "98a8f799c94ac1d918e3a3ec5be18d608debeba96ef06fc702803a4aef7f7e32"

EXECUTION_COST_SOURCE_SHA256 = "f8f56cdb05631ead69acd01707cb997533f3713288f9e00c0481c38630fcc89a"

BASE_SEED = 42
SIMULATION_COUNT = 10_000
BATCH_SIZE = 128

BLOCK_SIZES: tuple[int, ...] = (
    5,
    20,
    60,
)

INCREMENTAL_STRESS_BPS = 9.0

COST_MULTIPLIERS: tuple[float, ...] = (
    0.0,
    0.5,
    1.0,
    2.0,
    4.0,
)

TAIL_WEIGHT_MULTIPLIERS: tuple[float, ...] = (
    1.0,
    2.0,
    3.0,
)

SAFETY_FLAGS: dict[str, bool] = {
    "paper_only": True,
    "shadow_only": True,
    "research_only": True,
    "operational_authority": False,
    "changes_risk": False,
    "changes_model": False,
    "sends_orders": False,
    "exchange_private_access": False,
    "writes_master": False,
    "writes_sqlite": False,
}


class OfficialMonteCarloRiskValidationError(ValueError):
    """Fail-closed validation error for canonical WQ5 research."""

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
) -> NoReturn:
    raise OfficialMonteCarloRiskValidationError(
        code,
        **details,
    )


def _float_array(
    value: object,
) -> FloatArray:
    return cast(
        FloatArray,
        np.asarray(
            value,
            dtype=np.float64,
        ),
    )


def _int_array(
    value: object,
) -> IntArray:
    return cast(
        IntArray,
        np.asarray(
            value,
            dtype=np.int64,
        ),
    )


def _string_array(
    value: object,
) -> StringArray:
    return cast(
        StringArray,
        np.asarray(
            value,
            dtype=str,
        ),
    )


def _stable_hash(
    payload: Any,
) -> str:
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")

    return hashlib.sha256(raw).hexdigest()


def _transport_stripped(
    payload: Mapping[
        str,
        Any,
    ],
) -> dict[str, Any]:
    ignored = {
        "report_content_sha256",
        "report_paths",
        "write_requested",
        "write_performed",
    }

    return {key: value for key, value in payload.items() if key not in ignored}


def report_content_sha256(
    payload: Mapping[
        str,
        Any,
    ],
) -> str:
    """Hash semantic report content excluding transport-only fields."""

    return _stable_hash(_transport_stripped(payload))


def _mapping(
    value: object,
    *,
    error_code: str,
) -> Mapping[str, Any]:
    if not isinstance(
        value,
        Mapping,
    ):
        _fail(
            error_code,
        )

    return cast(
        Mapping[
            str,
            Any,
        ],
        value,
    )


def _validate_freezes(
    *,
    project_root: Path,
    method_freeze_v2_path: Path,
    method_freeze_v3_path: Path,
) -> tuple[
    Mapping[str, Any],
    Mapping[str, Any],
]:
    if not method_freeze_v2_path.is_file():
        _fail(
            "method_freeze_v2_missing",
            path=str(method_freeze_v2_path),
        )

    if not method_freeze_v3_path.is_file():
        _fail(
            "method_freeze_v3_missing",
            path=str(method_freeze_v3_path),
        )

    v2_sha = sha256_file(method_freeze_v2_path)

    v3_sha = sha256_file(method_freeze_v3_path)

    if v2_sha != METHOD_FREEZE_V2_FILE_SHA256:
        _fail(
            "method_freeze_v2_file_sha_mismatch",
            expected=METHOD_FREEZE_V2_FILE_SHA256,
            actual=v2_sha,
        )

    if v3_sha != METHOD_FREEZE_V3_FILE_SHA256:
        _fail(
            "method_freeze_v3_file_sha_mismatch",
            expected=METHOD_FREEZE_V3_FILE_SHA256,
            actual=v3_sha,
        )

    v2_raw = json.loads(method_freeze_v2_path.read_text(encoding="utf-8"))

    v3_raw = json.loads(method_freeze_v3_path.read_text(encoding="utf-8"))

    v2 = _mapping(
        v2_raw,
        error_code=("method_freeze_v2_root_invalid"),
    )

    v3 = _mapping(
        v3_raw,
        error_code=("method_freeze_v3_root_invalid"),
    )

    if v2.get("method_hash") != WQ5_METHOD_V2_HASH:
        _fail(
            "method_freeze_v2_hash_mismatch",
            actual=v2.get("method_hash"),
        )

    if v3.get("method_hash") != WQ5_METHOD_V3_HASH:
        _fail(
            "method_freeze_v3_hash_mismatch",
            actual=v3.get("method_hash"),
        )

    parent = _mapping(
        v3.get("parent_freeze"),
        error_code=("method_freeze_v3_parent_missing"),
    )

    if (
        parent.get("file_sha256") != METHOD_FREEZE_V2_FILE_SHA256
        or parent.get("method_hash") != WQ5_METHOD_V2_HASH
    ):
        _fail(
            "method_freeze_v3_parent_mismatch",
        )

    stress = _mapping(
        v3.get("incremental_execution_stress"),
        error_code=("v3_incremental_stress_missing"),
    )

    if (
        float(
            stress.get(
                "incremental_stress_bps",
                -1.0,
            )
        )
        != INCREMENTAL_STRESS_BPS
    ):
        _fail(
            "v3_incremental_stress_bps_mismatch",
        )

    fee = _mapping(
        stress.get("fee_rate_bps"),
        error_code=("v3_fee_contract_missing"),
    )

    if fee.get("included_in_v3_overlay") is not False:
        _fail(
            "v3_fee_exclusion_mismatch",
        )

    frozen_cost_multipliers = tuple(
        float(value)
        for value in cast(
            list[Any],
            stress.get(
                "cost_multipliers",
                [],
            ),
        )
    )

    if frozen_cost_multipliers != COST_MULTIPLIERS:
        _fail(
            "v3_cost_multiplier_mismatch",
        )

    source_contract = stress.get("source_contract")

    if not isinstance(
        source_contract,
        str,
    ):
        _fail(
            "execution_cost_source_contract_invalid",
        )

    cost_source = project_root / source_contract

    if not cost_source.is_file():
        _fail(
            "execution_cost_source_missing",
            path=str(cost_source),
        )

    cost_source_sha = sha256_file(cost_source)

    if cost_source_sha != EXECUTION_COST_SOURCE_SHA256 or cost_source_sha != stress.get(
        "source_sha256"
    ):
        _fail(
            "execution_cost_source_sha_mismatch",
            actual=cost_source_sha,
        )

    semantics = _mapping(
        v2.get("wq5_semantics"),
        error_code=("wq5_semantics_missing"),
    )

    randomness = _mapping(
        semantics.get("randomness"),
        error_code=("wq5_randomness_missing"),
    )

    if (
        int(
            randomness.get(
                "base_seed",
                -1,
            )
        )
        != BASE_SEED
        or int(
            randomness.get(
                "simulation_count",
                -1,
            )
        )
        != SIMULATION_COUNT
    ):
        _fail(
            "wq5_randomness_mismatch",
        )

    baseline = _mapping(
        semantics.get("baseline_resampling"),
        error_code=("baseline_resampling_missing"),
    )

    block = _mapping(
        baseline.get("moving_block_bootstrap"),
        error_code=("moving_block_bootstrap_missing"),
    )

    frozen_block_sizes = tuple(
        int(value)
        for value in cast(
            list[Any],
            block.get(
                "block_sizes",
                [],
            ),
        )
    )

    if (
        frozen_block_sizes != BLOCK_SIZES
        or block.get("circular") is not False
        or int(
            block.get(
                "primary_block_size",
                -1,
            )
        )
        != 20
    ):
        _fail(
            "moving_block_bootstrap_contract_mismatch",
        )

    tail = _mapping(
        semantics.get("tail_stress"),
        error_code=("tail_stress_missing"),
    )

    if tail.get("negative_cluster_sensitivity") != "baseline_block_b60":
        _fail(
            "negative_cluster_sensitivity_mismatch",
        )

    regime = _mapping(
        semantics.get("regime_conditional_bootstrap"),
        error_code=("regime_conditional_bootstrap_missing"),
    )

    if (
        regime.get("required_for_engineering_completion") is not True
        or regime.get("scenario_id") != "regime_conditional_iid"
        or regime.get("used_as_primary_acceptance_gate") is not False
    ):
        _fail(
            "regime_conditional_contract_mismatch",
        )

    return (
        v2,
        v3,
    )


def _primary_oos(
    master: OfficialMasterData,
) -> pd.DataFrame:
    if master.audit.status != "ok":
        _fail(
            "master_audit_not_ok",
            status=master.audit.status,
        )

    if master.audit.master_sha256 != OFFICIAL_MASTER_SHA256:
        _fail(
            "master_sha256_mismatch",
            actual=(master.audit.master_sha256),
        )

    if len(master.frame) != 3991:
        _fail(
            "master_row_count_mismatch",
            expected=3991,
            actual=len(master.frame),
        )

    required = {
        "trade_sequence",
        "order_id_raw",
        "symbol",
        "side",
        "horario_abertura",
        "horario_fechamento",
        "preco_abertura",
        "preco_fechamento",
        "volume_posicao",
        "volume_fechado",
        "economic_net_pnl",
        "economic_fee_adjustment",
        "fee_semantics_regime",
    }

    missing = sorted(required - set(master.frame.columns))

    if missing:
        _fail(
            "wq5_required_columns_missing",
            columns=missing,
        )

    frame = master.frame.copy()

    raw_open = frame["horario_abertura"].astype("string").str.strip()

    open_time = pd.to_datetime(
        raw_open.mask(raw_open.eq("LEGACY")),
        format="%Y-%m-%d %H:%M:%S",
        errors="coerce",
        utc=True,
    )

    frame["__open_time"] = open_time

    frame["__period"] = open_time.dt.strftime("%Y-%m")

    primary = (
        frame.loc[frame["__period"].isin(OOS_PERIODS)]
        .copy()
        .sort_values(
            [
                "__open_time",
                "trade_sequence",
            ],
            kind="mergesort",
        )
        .reset_index(drop=True)
    )

    if len(primary) != 3066:
        _fail(
            "primary_oos_row_count_mismatch",
            expected=3066,
            actual=len(primary),
        )

    if not (primary["fee_semantics_regime"].astype("string").str.strip().eq("STANDARD").all()):
        _fail(
            "primary_oos_not_all_standard",
        )

    adjustment = _float_array(
        pd.to_numeric(
            primary["economic_fee_adjustment"],
            errors="coerce",
        ).to_numpy()
    )

    if not np.isfinite(adjustment).all() or not np.allclose(
        adjustment,
        0.0,
        rtol=0.0,
        atol=0.0,
    ):
        _fail(
            "primary_oos_fee_adjustment_not_zero",
        )

    return primary


def _align_regime_labels(
    primary: pd.DataFrame,
    labeled: pd.DataFrame,
) -> StringArray:
    if len(labeled) != len(primary):
        _fail(
            "regime_label_row_count_mismatch",
            primary_rows=len(primary),
            labeled_rows=len(labeled),
        )

    for column in (
        "trade_sequence",
        "order_id_raw",
        "composite_regime",
    ):
        if column not in labeled.columns:
            _fail(
                "regime_label_column_missing",
                column=column,
            )

    primary_sequence = _int_array(
        pd.to_numeric(
            primary["trade_sequence"],
            errors="raise",
        ).to_numpy()
    )

    labeled_sequence = _int_array(
        pd.to_numeric(
            labeled["trade_sequence"],
            errors="raise",
        ).to_numpy()
    )

    if not np.array_equal(
        primary_sequence,
        labeled_sequence,
    ):
        _fail(
            "regime_trade_sequence_alignment_failed",
        )

    primary_order = _string_array(primary["order_id_raw"].astype(str).to_numpy())

    labeled_order = _string_array(labeled["order_id_raw"].astype(str).to_numpy())

    if not np.array_equal(
        primary_order,
        labeled_order,
    ):
        _fail(
            "regime_order_id_alignment_failed",
        )

    labels = _string_array(labeled["composite_regime"].astype(str).to_numpy())

    if any(not value for value in labels.tolist()):
        _fail(
            "regime_label_empty",
        )

    return labels


def _notional_from_v3_contract(
    primary: pd.DataFrame,
    freeze_v3: Mapping[
        str,
        Any,
    ],
) -> tuple[
    FloatArray,
    dict[str, Any],
]:
    contract = _mapping(
        freeze_v3.get("notional_contract"),
        error_code=("v3_notional_contract_missing"),
    )

    cap = float(
        contract.get(
            "max_trade_notional_usdt",
            0.0,
        )
    )

    if cap != 100_000.0:
        _fail(
            "v3_notional_cap_mismatch",
            value=cap,
        )

    inferred = infer_notional_details(
        primary,
        max_trade_notional_usdt=cap,
    )

    notional = _float_array(inferred.values.to_numpy())

    invalid = _int_array(np.flatnonzero((~np.isfinite(notional)) | (notional <= 0.0)))

    fallback = _mapping(
        contract.get("fallback_policy"),
        error_code=("v3_notional_fallback_missing"),
    )

    exception_count = int(
        fallback.get(
            "exception_count",
            -1,
        )
    )

    if exception_count != 1 or invalid.size != exception_count:
        _fail(
            "v3_notional_exception_count_mismatch",
            expected=exception_count,
            actual=int(invalid.size),
        )

    exception_contract = _mapping(
        fallback.get("exception"),
        error_code=("v3_notional_exception_missing"),
    )

    position = int(invalid[0])

    row = primary.iloc[position]

    if int(row["trade_sequence"]) != int(exception_contract["trade_sequence"]) or str(
        row["order_id_raw"]
    ) != str(exception_contract["order_id_raw"]):
        _fail(
            "v3_notional_exception_identity_mismatch",
        )

    fallback_notional = abs(float(row["preco_fechamento"]) * float(row["volume_fechado"]))

    expected_fallback = float(exception_contract["fallback_notional_usdt"])

    if not math.isclose(
        fallback_notional,
        expected_fallback,
        rel_tol=0.0,
        abs_tol=1e-9,
    ):
        _fail(
            "v3_notional_fallback_value_mismatch",
            expected=expected_fallback,
            actual=fallback_notional,
        )

    notional[position] = fallback_notional

    if not (np.isfinite(notional).all() and (notional > 0.0).all() and (notional <= cap).all()):
        _fail(
            "v3_notional_coverage_failed",
        )

    evidence = {
        "source": (inferred.source),
        "price_column": (inferred.price_column),
        "size_column": (inferred.size_column),
        "price_adjusted_rows": int(inferred.price_adjusted_rows),
        "size_fallback_rows": int(inferred.size_fallback_rows),
        "initial_invalid_rows": int(inferred.invalid_rows),
        "v3_exact_fallback_rows": 1,
        "coverage_rows": int(np.count_nonzero(notional > 0.0)),
        "max_trade_notional_usdt": (cap),
    }

    return (
        notional,
        evidence,
    )


def per_path_seed(
    scenario_id: str,
    simulation_index: int,
) -> int:
    digest = hashlib.sha256(
        (f"{BASE_SEED}|{scenario_id}|{simulation_index}").encode("utf-8")
    ).digest()

    return int.from_bytes(
        digest[:8],
        byteorder="big",
        signed=False,
    )


def _generator(
    scenario_id: str,
    simulation_index: int,
) -> np.random.Generator:
    return np.random.Generator(
        np.random.PCG64(
            per_path_seed(
                scenario_id,
                simulation_index,
            )
        )
    )


def moving_block_sample(
    values: FloatArray,
    *,
    block_size: int,
    rng: np.random.Generator,
) -> FloatArray:
    data = _float_array(values)

    count = int(data.size)

    if count < 1 or block_size < 1 or block_size > count:
        _fail(
            "moving_block_arguments_invalid",
            rows=count,
            block_size=block_size,
        )

    number_of_blocks = math.ceil(count / block_size)

    starts = _int_array(
        rng.integers(
            0,
            count - block_size + 1,
            size=number_of_blocks,
        )
    )

    offsets = _int_array(
        np.arange(
            block_size,
            dtype=np.int64,
        )
    )

    indexes = _int_array(
        (
            starts[
                :,
                None,
            ]
            + offsets[
                None,
                :,
            ]
        ).reshape(-1)[:count]
    )

    return _float_array(data[indexes])


def _regime_conditional_sample(
    pnl: FloatArray,
    regime_labels: StringArray,
    *,
    rng: np.random.Generator,
) -> FloatArray:
    values = _float_array(pnl)

    labels = _string_array(regime_labels)

    if values.shape != labels.shape:
        _fail(
            "regime_conditional_shape_mismatch",
        )

    result = _float_array(
        np.empty(
            values.size,
            dtype=np.float64,
        )
    )

    unique_labels = sorted(set(labels.tolist()))

    for label in unique_labels:
        positions = _int_array(np.flatnonzero(labels == label))

        if positions.size == 0:
            _fail(
                "regime_conditional_empty_positions",
                regime=label,
            )

        pool = _float_array(values[positions])

        if pool.size == 0:
            _fail(
                "regime_conditional_empty_pool",
                regime=label,
            )

        sampled = _float_array(
            rng.choice(
                pool,
                size=int(positions.size),
                replace=True,
            )
        )

        result[positions] = sampled

    return result


def _shuffle_sampler(
    values: FloatArray,
) -> Sampler:
    frozen = _float_array(values.copy())

    def sample(
        rng: np.random.Generator,
    ) -> FloatArray:
        return _float_array(rng.permutation(frozen))

    return sample


def _iid_sampler(
    values: FloatArray,
) -> Sampler:
    frozen = _float_array(values.copy())

    def sample(
        rng: np.random.Generator,
    ) -> FloatArray:
        return _float_array(
            rng.choice(
                frozen,
                size=frozen.size,
                replace=True,
            )
        )

    return sample


def _block_sampler(
    values: FloatArray,
    *,
    block_size: int,
) -> Sampler:
    frozen = _float_array(values.copy())

    def sample(
        rng: np.random.Generator,
    ) -> FloatArray:
        return moving_block_sample(
            frozen,
            block_size=block_size,
            rng=rng,
        )

    return sample


def _weighted_iid_sampler(
    values: FloatArray,
    probabilities: FloatArray,
) -> Sampler:
    frozen = _float_array(values.copy())

    probability = _float_array(probabilities.copy())

    def sample(
        rng: np.random.Generator,
    ) -> FloatArray:
        return _float_array(
            rng.choice(
                frozen,
                size=frozen.size,
                replace=True,
                p=probability,
            )
        )

    return sample


def _regime_sampler(
    values: FloatArray,
    labels: StringArray,
) -> Sampler:
    frozen_values = _float_array(values.copy())

    frozen_labels = _string_array(labels.copy())

    def sample(
        rng: np.random.Generator,
    ) -> FloatArray:
        return _regime_conditional_sample(
            frozen_values,
            frozen_labels,
            rng=rng,
        )

    return sample


def _run_scenario(
    *,
    scenario_id: str,
    horizon: int,
    sampler: Sampler,
    buffer_anchor: float,
    simulation_count: int = SIMULATION_COUNT,
    batch_size: int = BATCH_SIZE,
) -> dict[str, Any]:
    if horizon < 1 or simulation_count < 1 or batch_size < 1:
        _fail(
            "scenario_iteration_config_invalid",
            horizon=horizon,
            simulation_count=simulation_count,
            batch_size=batch_size,
        )

    stores: dict[
        str,
        list[FloatArray],
    ] = {
        "terminal": [],
        "max_drawdown": [],
        "losing_streak": [],
        "time_under_water": [],
    }

    for multiple in BUFFER_MULTIPLES:
        stores[f"ruin_{multiple:g}"] = []

    for batch_start in range(
        0,
        simulation_count,
        batch_size,
    ):
        batch_end = min(
            batch_start + batch_size,
            simulation_count,
        )

        batch_count = batch_end - batch_start

        paths = _float_array(
            np.empty(
                (
                    batch_count,
                    horizon,
                ),
                dtype=np.float64,
            )
        )

        for (
            local_index,
            simulation_index,
        ) in enumerate(
            range(
                batch_start,
                batch_end,
            )
        ):
            rng = _generator(
                scenario_id,
                simulation_index,
            )

            sample = _float_array(sampler(rng))

            if sample.shape != (horizon,):
                _fail(
                    "scenario_sample_shape_invalid",
                    scenario=scenario_id,
                    expected=(horizon,),
                    actual=sample.shape,
                )

            if not np.isfinite(sample).all():
                _fail(
                    "scenario_sample_nonfinite",
                    scenario=scenario_id,
                )

            paths[
                local_index,
                :,
            ] = sample

        calculated = calculate_path_metrics(
            paths,
            buffer_anchor=buffer_anchor,
        )

        for (
            key,
            vector,
        ) in calculated.items():
            stores[key].append(_float_array(vector))

    merged: dict[
        str,
        FloatArray,
    ] = {key: _float_array(np.concatenate(vectors)) for key, vectors in stores.items()}

    return summarize_path_metrics(merged)


def _tail_probabilities(
    pnl: FloatArray,
    trade_sequence: IntArray,
    *,
    multiplier: float,
) -> FloatArray:
    values = _float_array(pnl)

    sequence = _int_array(trade_sequence)

    if values.shape != sequence.shape:
        _fail(
            "tail_identification_shape_mismatch",
        )

    count = int(values.size)

    tail_size = max(
        1,
        int(math.ceil(count * 0.10)),
    )

    ordered = _int_array(
        np.lexsort(
            (
                sequence,
                values,
            )
        )
    )

    tail = _int_array(ordered[:tail_size])

    weights = _float_array(
        np.ones(
            count,
            dtype=np.float64,
        )
    )

    weights[tail] = float(multiplier)

    total = float(weights.sum())

    if not np.isfinite(total) or total <= 0.0:
        _fail(
            "tail_probability_weight_sum_invalid",
        )

    return _float_array(weights / total)


def _acceptance(
    scenarios: Mapping[
        str,
        Mapping[
            str,
            Any,
        ],
    ],
) -> tuple[
    dict[str, bool],
    float,
    bool,
    str,
]:
    required = {
        "baseline_iid",
        "baseline_block_b20",
        "cost_block20_m10",
        "tail_iid_w20",
    }

    missing = sorted(required - set(scenarios))

    if missing:
        _fail(
            "primary_acceptance_scenarios_missing",
            scenarios=missing,
        )

    iid = scenarios["baseline_iid"]

    block20 = scenarios["baseline_block_b20"]

    cost10 = scenarios["cost_block20_m10"]

    tail20 = scenarios["tail_iid_w20"]

    ruin_2x = primary_ruin_probability(
        scenarios,
        multiple="2",
    )

    gates = {
        "baseline_iid_positive_probability": (float(iid["p_final_net_pnl_gt_zero"]) >= 0.95),
        "baseline_iid_p05_positive": (float(iid["terminal_p05"]) > 0.0),
        "baseline_block20_positive_probability": (
            float(block20["p_final_net_pnl_gt_zero"]) >= 0.95
        ),
        "baseline_block20_p05_positive": (float(block20["terminal_p05"]) > 0.0),
        "cost_block20_m10_median_positive": (float(cost10["terminal_p50"]) > 0.0),
        "tail_iid_w20_median_positive": (float(tail20["terminal_p50"]) > 0.0),
        "conditional_ruin_2x_lte_005": (ruin_2x <= 0.05),
    }

    passed = all(gates.values())

    classification = (
        "ROBUST_UNDER_FROZEN_STRESS_RESEARCH_ONLY"
        if passed
        else "DISCARD_OR_RECALIBRATE_RESEARCH_ONLY"
    )

    return (
        gates,
        ruin_2x,
        passed,
        classification,
    )


def build_monte_carlo_risk_report(
    master: OfficialMasterData,
    *,
    project_root: str | Path,
    method_freeze_v2_path: str | Path,
    method_freeze_v3_path: str | Path,
    market_paths: Mapping[
        str,
        str | Path,
    ],
) -> dict[str, Any]:
    """Execute the complete canonical frozen WQ5 V3 research evaluation."""

    root = Path(project_root).resolve()

    freeze_v2_path = Path(method_freeze_v2_path).resolve()

    freeze_v3_path = Path(method_freeze_v3_path).resolve()

    _, freeze_v3 = _validate_freezes(
        project_root=root,
        method_freeze_v2_path=(freeze_v2_path),
        method_freeze_v3_path=(freeze_v3_path),
    )

    primary = _primary_oos(master)

    (
        labeled,
        pit_evidence,
        regime_lineage,
    ) = build_labeled_oos_regime_frame(
        master,
        project_root=root,
        method_freeze_v2_path=(freeze_v2_path),
        market_paths=market_paths,
    )

    regime_labels = _align_regime_labels(
        primary,
        labeled,
    )

    pnl = _float_array(
        pd.to_numeric(
            primary["economic_net_pnl"],
            errors="raise",
        ).to_numpy()
    )

    sequence = _int_array(
        pd.to_numeric(
            primary["trade_sequence"],
            errors="raise",
        ).to_numpy()
    )

    if pnl.size != 3066 or not np.isfinite(pnl).all():
        _fail(
            "primary_pnl_invalid",
        )

    (
        notional,
        notional_evidence,
    ) = _notional_from_v3_contract(
        primary,
        freeze_v3,
    )

    incremental_cost = _float_array(notional * (INCREMENTAL_STRESS_BPS / 10_000.0))

    historical_mdd = historical_max_drawdown(pnl)

    if not math.isclose(
        historical_mdd,
        68.46778799999993,
        rel_tol=0.0,
        abs_tol=1e-9,
    ):
        _fail(
            "historical_max_drawdown_drift",
            actual=historical_mdd,
        )

    scenarios: dict[
        str,
        dict[
            str,
            Any,
        ],
    ] = {}

    scenarios["baseline_shuffle"] = _run_scenario(
        scenario_id=("baseline_shuffle"),
        horizon=int(pnl.size),
        sampler=_shuffle_sampler(pnl),
        buffer_anchor=historical_mdd,
    )

    scenarios["baseline_iid"] = _run_scenario(
        scenario_id="baseline_iid",
        horizon=int(pnl.size),
        sampler=_iid_sampler(pnl),
        buffer_anchor=historical_mdd,
    )

    for block_size in BLOCK_SIZES:
        scenario_id = f"baseline_block_b{block_size:02d}"

        scenarios[scenario_id] = _run_scenario(
            scenario_id=scenario_id,
            horizon=int(pnl.size),
            sampler=_block_sampler(
                pnl,
                block_size=block_size,
            ),
            buffer_anchor=historical_mdd,
        )

    scenarios["regime_conditional_iid"] = _run_scenario(
        scenario_id=("regime_conditional_iid"),
        horizon=int(pnl.size),
        sampler=_regime_sampler(
            pnl,
            regime_labels,
        ),
        buffer_anchor=historical_mdd,
    )

    cost_scenarios = (
        (
            0.0,
            "m00",
        ),
        (
            0.5,
            "m05",
        ),
        (
            1.0,
            "m10",
        ),
        (
            2.0,
            "m20",
        ),
        (
            4.0,
            "m40",
        ),
    )

    for (
        multiplier,
        suffix,
    ) in cost_scenarios:
        scenario_id = "cost_block20_" + suffix

        stressed = _float_array(pnl - (multiplier * incremental_cost))

        scenarios[scenario_id] = _run_scenario(
            scenario_id=scenario_id,
            horizon=int(pnl.size),
            sampler=_block_sampler(
                stressed,
                block_size=20,
            ),
            buffer_anchor=historical_mdd,
        )

    for multiplier in TAIL_WEIGHT_MULTIPLIERS:
        scenario_id = f"tail_iid_w{int(multiplier * 10):02d}"

        probabilities = _tail_probabilities(
            pnl,
            sequence,
            multiplier=multiplier,
        )

        scenarios[scenario_id] = _run_scenario(
            scenario_id=scenario_id,
            horizon=int(pnl.size),
            sampler=(
                _weighted_iid_sampler(
                    pnl,
                    probabilities,
                )
            ),
            buffer_anchor=historical_mdd,
        )

    (
        primary_gates,
        primary_ruin_2x,
        all_primary_gates_pass,
        classification,
    ) = _acceptance(scenarios)

    scenario_order = [
        "baseline_shuffle",
        "baseline_iid",
        "baseline_block_b05",
        "baseline_block_b20",
        "baseline_block_b60",
        "regime_conditional_iid",
        "cost_block20_m00",
        "cost_block20_m05",
        "cost_block20_m10",
        "cost_block20_m20",
        "cost_block20_m40",
        "tail_iid_w10",
        "tail_iid_w20",
        "tail_iid_w30",
    ]

    if set(scenarios) != set(scenario_order):
        _fail(
            "scenario_inventory_mismatch",
            actual=sorted(scenarios),
        )

    return {
        "schema_version": (SCHEMA_VERSION),
        "status": "ok",
        "engineering_status": ("PASS"),
        "wq5_status": ("RISK_STRESS_READY"),
        "decision": ("WQ5_RISK_STRESS_EVALUATED"),
        "quant_classification": (classification),
        "primary_acceptance_pass": (all_primary_gates_pass),
        "master_sha256": (master.audit.master_sha256),
        "method_v2_hash": (WQ5_METHOD_V2_HASH),
        "method_v2_file_sha256": (METHOD_FREEZE_V2_FILE_SHA256),
        "method_v3_hash": (WQ5_METHOD_V3_HASH),
        "method_v3_file_sha256": (METHOD_FREEZE_V3_FILE_SHA256),
        "primary_population": {
            "rows": int(pnl.size),
            "historical_net_pnl": float(pnl.sum()),
            "historical_max_drawdown": (historical_mdd),
            "notional_sum_usdt": float(notional.sum()),
            "notional_mean_usdt": float(notional.mean()),
            "incremental_stress_bps": (INCREMENTAL_STRESS_BPS),
            "incremental_cost_total_m10_usdt": float(incremental_cost.sum()),
            "incremental_cost_mean_m10_usdt": float(incremental_cost.mean()),
            "composite_regime_count": int(len(set(regime_labels.tolist()))),
        },
        "notional_evidence": (notional_evidence),
        "pit_evidence": (pit_evidence),
        "randomness": {
            "base_seed": BASE_SEED,
            "simulation_count": (SIMULATION_COUNT),
            "generator": ("numpy.random.PCG64"),
            "per_path_seed": (
                "first_8_bytes_big_endian_of_SHA256('42|'+scenario_id+'|'+simulation_index)"
            ),
            "worker_count": 1,
            "maximum_workers": 10,
            "worker_count_must_not_affect_results": True,
        },
        "scenario_order": (scenario_order),
        "scenarios": scenarios,
        "negative_cluster_sensitivity": {
            "scenario_id": ("baseline_block_b60"),
            "result": scenarios["baseline_block_b60"],
        },
        "regime_conditional_engineering": {
            "required": True,
            "used_as_primary_acceptance_gate": False,
            "scenario_id": ("regime_conditional_iid"),
            "result": scenarios["regime_conditional_iid"],
        },
        "primary_gates": (primary_gates),
        "primary_ruin_2x": (primary_ruin_2x),
        "all_primary_gates_pass": (all_primary_gates_pass),
        "v2_cost_stress_valid": False,
        "v3_cost_semantics_applied": True,
        "source_lineage": {
            "wq4_regime_lineage": (regime_lineage),
            "execution_cost_source_sha256": (EXECUTION_COST_SOURCE_SHA256),
        },
        **SAFETY_FLAGS,
        "safety": dict(SAFETY_FLAGS),
    }


__all__ = [
    "BASE_SEED",
    "BLOCK_SIZES",
    "COST_MULTIPLIERS",
    "INCREMENTAL_STRESS_BPS",
    "METHOD_FREEZE_V2_FILE_SHA256",
    "METHOD_FREEZE_V3_FILE_SHA256",
    "OfficialMonteCarloRiskValidationError",
    "SCHEMA_VERSION",
    "SIMULATION_COUNT",
    "TAIL_WEIGHT_MULTIPLIERS",
    "WQ5_METHOD_V2_HASH",
    "WQ5_METHOD_V3_HASH",
    "build_monte_carlo_risk_report",
    "moving_block_sample",
    "per_path_seed",
    "report_content_sha256",
]
