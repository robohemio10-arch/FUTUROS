"""Certified Qlib V3 economic shadow decisions for prospective Paper evidence.

Research/shadow-only. This module never changes the operational Paper decision,
RiskManager result, strategy, stake, leverage, orders, private exchange access,
model registry, or active model. It only creates exact sealed V3 decisions for
prospective evidence admission.
"""

from __future__ import annotations

import hashlib
import math
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd

from smartcrypto.execution.decision_ledger_v4_2.contracts import (
    AIShadowDecision,
    Alignment,
    DecisionRecordV42,
    FinalDecision,
    RiskDecision,
    seal_decision_record,
)
from smartcrypto.execution.freqtrade_contract import internal_symbol
from smartcrypto.learning.paper_autolearning import (
    qlib_market_context_economic_challenger as market_context,
)
from smartcrypto.qlib_engine.market_features_refresh import (
    get_canonical_rebuilt_feature_snapshot,
)

from .activation import Activation, read_object, safe_path
from .contracts import EvidenceError, digest

SCHEMA_VERSION = "qlib_v3_prospective_economic_shadow_decision_producer_v1"
FEATURE_CONTRACT_VERSION = "qlib_v3_reproducible_freeze_epoch_v1"
MODEL_ID = "qlib_v3_economic_shadow"
CONFIG_ENV = "QLIB_V3_NATURAL_EVIDENCE_CONFIG"
DEFAULT_MARKET_PATH = Path("data/features/market_features_60d.parquet")
ConfigSource = str | Path | Mapping[str, object] | None


@dataclass(frozen=True)
class ShadowDecisionReport:
    status: Literal["ok", "blocked"]
    reason: str
    decision_source: str
    candidate_count: int = 0
    scored_count: int = 0
    selected_count: int = 0
    control_count: int = 0
    market_source: str = "not_evaluated"

@dataclass(frozen=True)
class ShadowDecisionBatch:
    signals: tuple[dict[str, Any], ...]
    decisions: tuple[DecisionRecordV42, ...]
    report: ShadowDecisionReport


@dataclass(frozen=True)
class _Assets:
    feature_columns: tuple[str, ...]
    medians: tuple[float, ...]
    threshold: float
    selection_side: str
    booster: Any


@dataclass(frozen=True)
class _Context:
    signal: dict[str, Any]
    feature_timestamp: datetime
    feature_hash: str
    regime: str
    vector: tuple[float, ...]
    market_source: str

def resolve_shadow_decision_batch(
    *,
    project_root: Path,
    signals: Sequence[Mapping[str, Any]],
    existing_decisions: Sequence[DecisionRecordV42],
    decision_timestamp_utc: datetime,
    activation: Activation,
    config_source: ConfigSource,
) -> ShadowDecisionBatch:
    """Resolve exact V3 decisions without changing operational Paper signals."""

    baseline = tuple(dict(item) for item in signals)
    try:
        if not baseline:
            return ShadowDecisionBatch(
                (),
                (),
                ShadowDecisionReport(
                    "ok",
                    "no_risk_approved_signals",
                    "empty",
                ),
            )

        existing = _existing_certified_batch(
            baseline,
            existing_decisions,
            activation,
        )
        if existing is not None:
            return existing

        decision_timestamp = _require_utc(decision_timestamp_utc)
        if decision_timestamp < activation.boundary:
            raise EvidenceError("shadow_decision_before_prospective_boundary")
        if any(item.get("risk_approved") is not True for item in baseline):
            raise EvidenceError("shadow_decision_requires_risk_approved_signals")

        freeze_path = _resolve_freeze_path(project_root, config_source)
        assets = _load_assets(freeze_path, activation)
        contexts = _build_contexts(
            project_root=project_root,
            signals=baseline,
            decision_timestamp=decision_timestamp,
            assets=assets,
        )
        scores = _predict_scores(assets, contexts)

        evidence_signals: list[dict[str, Any]] = []
        decisions: list[DecisionRecordV42] = []
        selected_count = 0

        for context, score in zip(contexts, scores, strict=True):
            record, selected = _decision_from_context(
                context=context,
                score=score,
                decision_timestamp=decision_timestamp,
                activation=activation,
                assets=assets,
            )
            evidence_signal = dict(context.signal)
            evidence_signal["decision_ledger"] = {
                "decision_event_id": record.event_id,
                "decision_payload_sha256": record.payload_sha256,
            }
            evidence_signals.append(evidence_signal)
            decisions.append(record)
            selected_count += int(selected)

        return ShadowDecisionBatch(
            tuple(evidence_signals),
            tuple(decisions),
            ShadowDecisionReport(
                "ok",
                "certified_v3_shadow_decisions_projected",
                "certified_v3_shadow_booster",
                candidate_count=len(baseline),
                scored_count=len(decisions),
                selected_count=selected_count,
                control_count=len(decisions) - selected_count,
                market_source=(
                    contexts[0].market_source
                    if contexts
                    else "none"
                ),
            ),
        )
    except Exception as exc:
        return _blocked_batch(baseline, exc)


def _existing_certified_batch(
    signals: Sequence[Mapping[str, Any]],
    decisions: Sequence[DecisionRecordV42],
    activation: Activation,
) -> ShadowDecisionBatch | None:
    if not decisions or len(decisions) != len(signals):
        return None

    records = {record.event_id: record for record in decisions}
    if len(records) != len(decisions):
        return None

    ordered: list[DecisionRecordV42] = []
    selected_count = 0
    for signal in signals:
        envelope = signal.get("decision_ledger")
        if not isinstance(envelope, Mapping):
            return None
        event_id = str(envelope.get("decision_event_id") or "")
        record = records.get(event_id)
        if record is None:
            return None
        if record.model_hash != activation.identity.model_artifact_sha256:
            return None
        if record.final_decision is not FinalDecision.ALLOW:
            return None
        if signal.get("risk_approved") is not True:
            return None
        if envelope.get("decision_payload_sha256") != record.payload_sha256:
            return None
        if not (
            signal.get("signal_id") == record.signal_id
            and signal.get("candidate_id") == record.candidate_id
            and signal.get("correlation_id") == record.correlation_id
            and signal.get("pair") == record.pair
            and signal.get("symbol") == record.symbol
            and str(signal.get("side") or "").lower() == record.side.value
        ):
            return None
        ordered.append(record)
        selected_count += int(
            record.ai_shadow_decision is AIShadowDecision.ALLOW
        )

    return ShadowDecisionBatch(
        tuple(dict(item) for item in signals),
        tuple(ordered),
        ShadowDecisionReport(
            "ok",
            "existing_certified_v3_decisions_reused",
            "existing_certified_v3_decisions",
            candidate_count=len(signals),
            selected_count=selected_count,
            control_count=len(signals) - selected_count,
            market_source="existing_certified_decision",
        ),
    )


def _resolve_freeze_path(
    project_root: Path,
    source: ConfigSource,
) -> Path:
    resolved: object = source
    if resolved is None:
        resolved = os.environ.get(CONFIG_ENV)
    if resolved is None:
        raise EvidenceError("producer_config_missing")

    if isinstance(resolved, Mapping):
        config = dict(resolved)
    elif isinstance(resolved, (str, Path)):
        config = read_object(project_root / resolved)
    else:
        raise EvidenceError("producer_config_invalid")

    freeze = config.get("freeze")
    if not isinstance(freeze, str) or not freeze:
        raise EvidenceError("producer_certified_paths_required")
    return safe_path(project_root / freeze)


def _sha256_file(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def _require_artifact(
    epoch_root: Path,
    inventory: Mapping[str, Any],
    relative: str,
) -> Path:
    raw = inventory.get(relative)
    if not isinstance(raw, Mapping):
        raise EvidenceError(f"freeze_artifact_not_declared:{relative}")
    path = safe_path(epoch_root / relative)
    if not path.is_file():
        raise EvidenceError(f"freeze_artifact_missing:{relative}")
    expected = raw.get("sha256")
    if not isinstance(expected, str) or _sha256_file(path) != expected:
        raise EvidenceError(f"freeze_artifact_hash_mismatch:{relative}")
    return path


def _load_assets(
    freeze_path: Path,
    activation: Activation,
) -> _Assets:
    stat = freeze_path.stat()
    return _load_assets_cached(
        str(freeze_path),
        stat.st_mtime_ns,
        stat.st_size,
        activation.identity.freeze_v3_sha256,
        activation.identity.model_artifact_sha256,
        activation.identity.model_semantic_fingerprint,
        activation.identity.dataset_fingerprint,
    )


@lru_cache(maxsize=4)
def _load_assets_cached(
    freeze_path_text: str,
    freeze_mtime_ns: int,
    freeze_size: int,
    freeze_v3_sha256: str,
    model_artifact_sha256: str,
    model_semantic_fingerprint: str,
    dataset_fingerprint: str,
) -> _Assets:
    """Load the immutable V3 scoring surface.

    The semantic feature contract is the ordered ``feature_columns`` list in
    the certified freeze plus its matching imputation vector. The serialized
    LightGBM Booster uses generic internal names (``Column_0`` ...), so only
    feature width is asserted against the Booster. Exact model bytes are still
    pinned by SHA-256 before loading.
    """

    del freeze_mtime_ns, freeze_size

    freeze_path = safe_path(Path(freeze_path_text))
    freeze = read_object(freeze_path)
    contract = freeze.get("contract")
    if not isinstance(contract, Mapping):
        raise EvidenceError("freeze_contract_missing")

    checks = (
        (
            freeze.get("freeze_v3_sha256"),
            freeze_v3_sha256,
            "shadow_freeze_identity_mismatch",
        ),
        (
            contract.get("model_artifact_sha256"),
            model_artifact_sha256,
            "shadow_model_identity_mismatch",
        ),
        (
            contract.get("model_semantic_fingerprint"),
            model_semantic_fingerprint,
            "shadow_model_semantic_identity_mismatch",
        ),
        (
            contract.get("dataset_fingerprint"),
            dataset_fingerprint,
            "shadow_dataset_identity_mismatch",
        ),
    )
    for actual, expected, reason in checks:
        if actual != expected:
            raise EvidenceError(reason)

    raw_columns = contract.get("feature_columns")
    if (
        not isinstance(raw_columns, list)
        or not all(
            isinstance(item, str) and item
            for item in raw_columns
        )
        or contract.get("feature_count") != len(raw_columns)
    ):
        raise EvidenceError("shadow_feature_contract_invalid")
    feature_columns = tuple(raw_columns)

    inventory = contract.get("artifacts")
    if not isinstance(inventory, Mapping):
        raise EvidenceError("freeze_artifact_inventory_missing")

    epoch_root = freeze_path.parent
    imputation_path = _require_artifact(
        epoch_root,
        inventory,
        "dataset/imputation.json",
    )
    model_path = _require_artifact(
        epoch_root,
        inventory,
        "model/qlib_lgb_booster.txt",
    )
    metadata_path = _require_artifact(
        epoch_root,
        inventory,
        "model/model_metadata.json",
    )
    calibration_path = _require_artifact(
        epoch_root,
        inventory,
        "calibration/calibration.json",
    )
    policy_path = _require_artifact(
        epoch_root,
        inventory,
        "policy/policy.json",
    )

    if _sha256_file(model_path) != model_artifact_sha256:
        raise EvidenceError(
            "shadow_model_artifact_hash_mismatch"
        )

    metadata = read_object(metadata_path)
    if (
        metadata.get("serialized_model_sha256")
        != model_artifact_sha256
    ):
        raise EvidenceError(
            "shadow_model_metadata_hash_mismatch"
        )
    if (
        metadata.get("model_semantic_fingerprint")
        != model_semantic_fingerprint
    ):
        raise EvidenceError(
            "shadow_model_metadata_semantic_mismatch"
        )

    imputation = read_object(imputation_path)
    if imputation.get("feature_order") != list(feature_columns):
        raise EvidenceError(
            "shadow_imputation_feature_order_mismatch"
        )

    median_rows = imputation.get("ordered_medians")
    if (
        not isinstance(median_rows, list)
        or len(median_rows) != len(feature_columns)
    ):
        raise EvidenceError(
            "shadow_imputation_vector_invalid"
        )

    medians: list[float] = []
    for index, raw in enumerate(median_rows):
        if (
            not isinstance(raw, Mapping)
            or raw.get("feature")
            != feature_columns[index]
        ):
            raise EvidenceError(
                "shadow_imputation_vector_order_mismatch"
            )

        encoded = raw.get("median_float64")
        if not isinstance(encoded, str):
            raise EvidenceError(
                "shadow_imputation_median_invalid"
            )

        value = float.fromhex(encoded)
        if not math.isfinite(value):
            raise EvidenceError(
                "shadow_imputation_median_non_finite"
            )
        medians.append(value)

    calibration = read_object(calibration_path)
    policy = read_object(policy_path)

    threshold_hex = contract.get("threshold_float64")
    if (
        not isinstance(threshold_hex, str)
        or threshold_hex
        != policy.get("threshold_float64")
        or threshold_hex
        != calibration.get("threshold_float64")
    ):
        raise EvidenceError(
            "shadow_threshold_contract_mismatch"
        )

    threshold = float.fromhex(threshold_hex)
    if not math.isfinite(threshold):
        raise EvidenceError(
            "shadow_threshold_non_finite"
        )

    if policy.get("target") != "absolute_stressed_net_pnl":
        raise EvidenceError(
            "shadow_target_contract_mismatch"
        )
    if policy.get("selection_side") != "long":
        raise EvidenceError(
            "shadow_selection_side_contract_mismatch"
        )
    if (
        policy.get("short_rows_eligible_for_selection")
        is not False
    ):
        raise EvidenceError(
            "shadow_short_selection_contract_mismatch"
        )
    if (
        float(
            policy.get(
                "additional_execution_stress_bps"
            )
        )
        != 5.0
    ):
        raise EvidenceError(
            "shadow_execution_stress_contract_mismatch"
        )

    try:
        import lightgbm
    except ImportError as exc:
        raise EvidenceError(
            "shadow_lightgbm_unavailable"
        ) from exc

    try:
        booster = lightgbm.Booster(
            model_file=str(model_path)
        )
    except Exception as exc:
        raise EvidenceError(
            "shadow_certified_booster_load_failed:"
            f"{type(exc).__name__}"
        ) from exc

    try:
        booster_feature_count = int(
            booster.num_feature()
        )
    except Exception as exc:
        raise EvidenceError(
            "shadow_booster_feature_count_unavailable:"
            f"{type(exc).__name__}"
        ) from exc

    if booster_feature_count != len(feature_columns):
        raise EvidenceError(
            "shadow_booster_feature_count_mismatch"
        )

    return _Assets(
        feature_columns=feature_columns,
        medians=tuple(medians),
        threshold=threshold,
        selection_side="long",
        booster=booster,
    )

def _prepare_canonical_refresh_snapshot(
    snapshot: pd.DataFrame,
) -> pd.DataFrame:
    """Normalize an already-canonical refresh rebuild for PIT alignment.

    The snapshot comes directly from the same full-history
    ``build_market_feature_frame`` result produced by the Paper refresh. No
    operational merged feature values are consumed here and no indicator is
    recomputed on the signal-publication path.
    """

    if snapshot is None or getattr(snapshot, "empty", True):
        raise EvidenceError(
            "shadow_canonical_refresh_snapshot_empty"
        )

    required = {
        "symbol",
        "tf",
        "ts",
        *market_context.MARKET_SOURCE_COLUMNS,
    }
    missing = sorted(
        required.difference(snapshot.columns)
    )
    if missing:
        raise EvidenceError(
            "shadow_canonical_refresh_snapshot_schema_invalid:"
            + ",".join(missing)
        )

    features = snapshot.copy()
    features["tf"] = (
        features["tf"]
        .astype(str)
        .str.casefold()
    )
    features = features.loc[
        features["tf"].eq(
            market_context.TIMEFRAME
        )
    ].copy()
    if features.empty:
        raise EvidenceError(
            "shadow_canonical_refresh_snapshot_no_5m_rows"
        )

    features["symbol"] = features["symbol"].map(
        internal_symbol
    )
    features["ts"] = pd.to_datetime(
        features["ts"],
        utc=True,
        errors="coerce",
    )
    features["available_at_utc"] = (
        features["ts"]
        + pd.Timedelta(
            seconds=market_context.TIMEFRAME_SECONDS
        )
    )
    return (
        features
        .dropna(
            subset=[
                "symbol",
                "ts",
                "available_at_utc",
            ]
        )
        .sort_values(
            [
                "symbol",
                "available_at_utc",
            ],
            kind="mergesort",
        )
    )


def _canonical_market_snapshot(
    market_path: Path,
    *,
    required_symbols: set[str],
) -> tuple[pd.DataFrame, str]:
    """Return only the exact refresh-time canonical rebuild.

    There is intentionally no synchronous fallback that rematerializes 60 days
    of indicators here. If the refresh-time in-process snapshot is unavailable
    or stale, V3 evidence blocks while operational Paper publication continues.
    """

    snapshot = get_canonical_rebuilt_feature_snapshot(
        market_path
    )
    if snapshot is None:
        raise EvidenceError(
            "shadow_canonical_refresh_snapshot_unavailable"
        )

    prepared = _prepare_canonical_refresh_snapshot(
        snapshot
    )
    available_symbols = set(
        prepared["symbol"].astype(str).unique()
    )
    if not required_symbols.issubset(
        available_symbols
    ):
        missing = sorted(
            required_symbols.difference(
                available_symbols
            )
        )
        raise EvidenceError(
            "shadow_canonical_refresh_snapshot_symbol_missing:"
            + ",".join(missing)
        )

    return (
        prepared,
        "refresh_full_history_canonical_cache",
    )


def _build_contexts(
    *,
    project_root: Path,
    signals: Sequence[Mapping[str, Any]],
    decision_timestamp: datetime,
    assets: _Assets,
) -> tuple[_Context, ...]:
    market_path = safe_path(
        project_root / DEFAULT_MARKET_PATH
    )
    if not market_path.is_file():
        raise EvidenceError(
            "shadow_market_source_missing"
        )

    candidates: list[dict[str, Any]] = []
    required_symbols: set[str] = set()

    for index, signal in enumerate(signals):
        symbol = internal_symbol(
            str(
                signal.get("symbol")
                or signal.get("pair")
                or ""
            )
        )
        if not symbol:
            raise EvidenceError(
                "shadow_symbol_missing"
            )

        side = str(
            signal.get("side")
            or ""
        ).lower()
        if side not in {"long", "short"}:
            raise EvidenceError(
                "shadow_side_invalid"
            )

        required_symbols.add(symbol)
        candidates.append(
            {
                "__shadow_index": index,
                "__open_time": decision_timestamp,
                "__symbol": symbol,
                "side": side,
            }
        )

    market, market_source = _canonical_market_snapshot(
        market_path,
        required_symbols=required_symbols,
    )

    aligned, alignment_report = (
        market_context
        ._align_point_in_time_market_features(
            candidates,
            market,
        )
    )
    if len(aligned) != len(candidates):
        raise EvidenceError(
            "shadow_pit_context_incomplete:"
            f"{len(aligned)}:"
            f"{len(candidates)}:"
            f"{alignment_report.get('coverage')}"
        )

    by_index = {
        int(item["__shadow_index"]): item
        for item in aligned
    }
    if len(by_index) != len(candidates):
        raise EvidenceError(
            "shadow_pit_context_identity_conflict"
        )

    contexts: list[_Context] = []
    for index, signal in enumerate(signals):
        row = by_index.get(index)
        if row is None:
            raise EvidenceError(
                "shadow_pit_context_missing"
            )

        raw_features = market_context._model_feature_row(
            row
        )
        values: list[float] = []
        for feature_index, feature in enumerate(
            assets.feature_columns
        ):
            numeric = _finite_or_none(
                raw_features.get(feature)
            )
            values.append(
                assets.medians[feature_index]
                if numeric is None
                else numeric
            )

        feature_timestamp = row.get(
            "__market_feature_available_at"
        )
        if not isinstance(
            feature_timestamp,
            datetime,
        ):
            raise EvidenceError(
                "shadow_feature_timestamp_missing"
            )
        feature_timestamp = _require_utc(
            feature_timestamp
        )
        if feature_timestamp > decision_timestamp:
            raise EvidenceError(
                "shadow_feature_timestamp_after_decision"
            )

        market_values = row.get("__market")
        regime = "unknown"
        if isinstance(market_values, Mapping):
            candidate_regime = str(
                market_values.get("market_regime")
                or ""
            ).strip().lower()
            if (
                candidate_regime
                and candidate_regime != "nan"
            ):
                regime = candidate_regime

        feature_hash = digest(
            {
                "feature_contract_version": (
                    FEATURE_CONTRACT_VERSION
                ),
                "feature_timestamp": (
                    feature_timestamp.isoformat()
                ),
                "feature_order": list(
                    assets.feature_columns
                ),
                "feature_values_float64": [
                    value.hex()
                    for value in values
                ],
            }
        )
        contexts.append(
            _Context(
                signal=dict(signal),
                feature_timestamp=feature_timestamp,
                feature_hash=feature_hash,
                regime=regime,
                vector=tuple(values),
                market_source=market_source,
            )
        )

    return tuple(contexts)

def _predict_scores(
    assets: _Assets,
    contexts: Sequence[_Context],
) -> tuple[float, ...]:
    """Score strictly by the certified semantic feature position.

    The freeze owns feature semantics and ordering. The exact serialized
    LightGBM artifact is hash-pinned but internally names its inputs
    ``Column_0`` ... ``Column_36``. Passing an ndarray avoids incorrectly
    treating those generic labels as the semantic FeatureContract.
    """

    if not contexts:
        raise EvidenceError(
            "shadow_feature_matrix_empty"
        )

    matrix = np.asarray(
        [
            list(context.vector)
            for context in contexts
        ],
        dtype=np.float64,
    )

    expected_shape = (
        len(contexts),
        len(assets.feature_columns),
    )
    if matrix.shape != expected_shape:
        raise EvidenceError(
            "shadow_feature_matrix_width_mismatch"
        )

    if not np.isfinite(matrix).all():
        raise EvidenceError(
            "shadow_feature_matrix_non_finite"
        )

    try:
        raw_scores = assets.booster.predict(matrix)
    except Exception as exc:
        raise EvidenceError(
            "shadow_booster_predict_failed:"
            f"{type(exc).__name__}"
        ) from exc

    scores = np.asarray(
        raw_scores,
        dtype=np.float64,
    ).reshape(-1)

    if (
        len(scores) != len(contexts)
        or not np.isfinite(scores).all()
    ):
        raise EvidenceError(
            "shadow_score_vector_invalid"
        )

    return tuple(
        float(value)
        for value in scores
    )

def _decision_from_context(
    *,
    context: _Context,
    score: float,
    decision_timestamp: datetime,
    activation: Activation,
    assets: _Assets,
) -> tuple[DecisionRecordV42, bool]:
    signal = context.signal
    side = str(signal.get("side") or "").lower()
    selected = bool(
        side == assets.selection_side
        and score >= assets.threshold
    )

    if selected:
        ai_decision = AIShadowDecision.ALLOW
        ai_reasons = ("v3_score_gte_frozen_threshold",)
    elif side != assets.selection_side:
        ai_decision = AIShadowDecision.ABSTAIN
        ai_reasons = ("v3_selection_side_ineligible",)
    else:
        ai_decision = AIShadowDecision.ABSTAIN
        ai_reasons = ("v3_score_lt_frozen_threshold",)

    token = digest(
        {
            "epoch_id": activation.identity.epoch_id,
            "signal_id": str(signal.get("signal_id") or ""),
            "candidate_id": str(signal.get("candidate_id") or ""),
            "feature_hash": context.feature_hash,
            "model_hash": activation.identity.model_artifact_sha256,
        }
    )
    event_id = f"v3-shadow-decision-{token[:40]}"

    record = seal_decision_record(
        {
            "event_id": event_id,
            "signal_id": signal.get("signal_id"),
            "candidate_id": signal.get("candidate_id"),
            "correlation_id": signal.get("correlation_id"),
            "idempotency_key": event_id,
            "runtime_mode": "paper",
            "pair": signal.get("pair"),
            "symbol": signal.get("symbol"),
            "side": side,
            "feature_timestamp": context.feature_timestamp,
            "decision_timestamp": decision_timestamp,
            "feature_contract_version": FEATURE_CONTRACT_VERSION,
            "feature_hash": context.feature_hash,
            "model_id": MODEL_ID,
            "model_version": activation.identity.epoch_id,
            "model_hash": activation.identity.model_artifact_sha256,
            "qlib_score": score,
            "calibrated_probability": None,
            # The frozen Booster score is kept in qlib_score. Its serialized
            # training/calibration contract does not certify this value as a
            # directly spendable USDT expectation, so the monetary field stays
            # intentionally unset.
            "expected_net_pnl": None,
            "fast_stop_probability": None,
            "regime": context.regime,
            "alignment": Alignment.UNKNOWN,
            "ai_shadow_decision": ai_decision,
            "ai_shadow_reasons": ai_reasons,
            "risk_decision": RiskDecision.APPROVED,
            "risk_reasons": _reason_tuple(
                signal.get("risk_reasons"),
                fallback="baseline_risk_manager_approved",
            ),
            "approved_stake_usdt": _approved_positive(
                signal,
                primary="approved_stake_usdt",
                fallback="max_position_usdt",
            ),
            "approved_leverage": _approved_positive(
                signal,
                primary="approved_leverage",
                fallback="leverage",
            ),
            "final_decision": FinalDecision.ALLOW,
            "final_reasons": (
                "baseline_paper_risk_approved",
                "v3_shadow_non_authoritative",
            ),
            "operational_authority": False,
            "runtime_integration": False,
            "sends_orders": False,
            "exchange_private_access": False,
        }
    )
    return record, selected


def _approved_positive(
    signal: Mapping[str, Any],
    *,
    primary: str,
    fallback: str,
) -> float:
    for field in (primary, fallback):
        value = _finite_or_none(signal.get(field))
        if value is not None and value > 0.0:
            return value
    raise EvidenceError(f"shadow_positive_value_required:{primary}")


def _reason_tuple(
    value: object,
    *,
    fallback: str,
) -> tuple[str, ...]:
    if isinstance(value, str):
        text = value.strip()
        if text:
            return (text[:512],)
    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        output = tuple(
            str(item).strip()[:512]
            for item in value
            if str(item).strip()
        )
        if output:
            return output
    return (fallback,)


def _finite_or_none(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise EvidenceError("shadow_timestamp_requires_timezone")
    if value.utcoffset().total_seconds() != 0:
        raise EvidenceError("shadow_timestamp_requires_utc")
    return value.astimezone(UTC)


def _blocked_batch(
    baseline: Sequence[Mapping[str, Any]],
    exc: Exception,
) -> ShadowDecisionBatch:
    reason = (
        str(exc)
        if isinstance(exc, EvidenceError)
        else f"shadow_decision_boundary_failed:{type(exc).__name__}"
    )
    return ShadowDecisionBatch(
        (),
        (),
        ShadowDecisionReport(
            "blocked",
            reason,
            "blocked",
            candidate_count=len(baseline),
            market_source="blocked",
        ),
    )
