"""Synthetic-only input proof; no hooks, network, model loading or runtime activation."""

from __future__ import annotations

import hashlib
import inspect
import json
import math
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal, Protocol, TypedDict

import numpy as np
import pandas as pd
from numpy.typing import NDArray
from pydantic import Field, field_validator, model_validator

from smartcrypto.data.feature_builder import build_market_feature_frame
from smartcrypto.execution.decision_ledger_v4_2.contracts import (
    DecisionRecordV42,
    require_utc_timestamp,
)
from smartcrypto.qlib_engine import predictor
from smartcrypto.qlib_engine.common import QlibEngineConfig
from smartcrypto.qlib_engine.market_features_refresh import _normalize_public_klines

from .archive import CoverageReport, coverage
from .capture import CaptureSession
from .contracts import (
    CaptureRecord,
    Evidence,
    Frozen,
    Identifier,
    LifecycleRecord,
    Sha256Hex,
    SourceReceipt,
    digest,
)


class Estimator(Protocol):
    def predict_proba(self, frame: pd.DataFrame) -> NDArray[np.float64]:
        """Consume the exact transformed input provided by the installed predictor."""


@dataclass(frozen=True)
class Opportunity:
    opportunity_id: str
    candidate_id: str
    signal_id: str
    symbol: str
    side: Literal["long", "short"]


class ModelCallProof(Frozen):
    schema_version: Literal["paper_pit_model_call_harness_v1"] = "paper_pit_model_call_harness_v1"
    call_id: Identifier
    epoch_id: Identifier
    configuration_sha256: Sha256Hex
    context_json: Annotated[str, Field(max_length=65536)]
    model_sha256: Sha256Hex
    metadata_sha256: Sha256Hex
    code_sha256: Sha256Hex
    feature_frame_sha256: Sha256Hex
    matrix_sha256: Sha256Hex
    columns: tuple[str, ...]
    dtypes: tuple[str, ...]
    row_index: tuple[str, ...]
    opportunities: tuple[str, ...]
    capture_sha256: tuple[Sha256Hex, ...]
    receipt_ids: tuple[Sha256Hex, ...]
    probabilities: tuple[tuple[float, float], ...]
    model_input_at_utc: datetime
    model_input_monotonic_ns: Annotated[int, Field(ge=0)]
    prediction_available_at_utc: datetime
    prediction_monotonic_ns: Annotated[int, Field(ge=0)]
    record_sha256: Sha256Hex
    synthetic: Literal[True] = True

    @field_validator("model_input_at_utc", "prediction_available_at_utc")
    @classmethod
    def utc_timestamp(cls, value: datetime) -> datetime:
        return require_utc_timestamp(value)

    @model_validator(mode="after")
    def verify_seal(self) -> ModelCallProof:
        context = json.loads(self.context_json)
        if (
            not isinstance(context, dict)
            or digest(context) != self.configuration_sha256
            or digest(context.get("metadata")) != self.metadata_sha256
            or context.get("code_sha256") != self.code_sha256
            or context.get("matrix_sha256") != self.matrix_sha256
            or context.get("feature_frame_sha256") != self.feature_frame_sha256
            or tuple(context.get("receipts", ())) != self.receipt_ids
        ):
            raise ValueError("model_call_context_identity_mismatch")
        if (
            self.model_input_at_utc > self.prediction_available_at_utc
            or self.model_input_monotonic_ns >= self.prediction_monotonic_ns
        ):
            raise ValueError("model_call_clock_regression")
        if (
            not len(self.opportunities)
            == len(self.capture_sha256)
            == len(self.probabilities)
            == len(self.row_index)
        ):
            raise ValueError("model_call_row_count_mismatch")
        if len(self.dtypes) != len(self.columns) or any(d != "float64" for d in self.dtypes):
            raise ValueError("model_call_float64_contract_required")
        if any(
            not (0 <= a <= 1 and 0 <= b <= 1) or abs(a + b - 1) > 1e-12
            for a, b in self.probabilities
        ):
            raise ValueError("model_call_probability_invalid")
        if self.record_sha256 != digest(self.model_dump(mode="json", exclude={"record_sha256"})):
            raise ValueError("model_call_seal_mismatch")
        return self


class DecisionInputLink(TypedDict):
    opportunity_id: str
    model_call_sha256: str
    capture_sha256: str
    decision_event_id: str
    decision_payload_sha256: str
    record_sha256: str


class HarnessReport(TypedDict):
    source_receipt_boundary: Literal["PARTIAL"]
    actual_model_input_binding: Literal["PROVEN_IN_HARNESS", "BLOCKED"]
    clock_custody: Literal["UNPROVEN"]
    pre_filter_opportunity_coverage: Literal["HARNESS_ONLY", "BLOCKED"]
    synthetic: Literal[True]
    opportunities: int
    selected: int
    selector_rejected: int
    selection_pending: int
    capture_overhead_ns: tuple[int, ...]
    hard_real_time_guarantee: Literal[False]
    runtime_integration_allowed: Literal[False]
    paper_modified: Literal[False]
    operational_authority: Literal[False]
    real_prospective_coverage: Literal["NOT_COLLECTED"]
    write_performed: Literal[False]
    quarantined_captures: int


def frame_identity(frame: pd.DataFrame) -> str:
    """Preserve float bits, order, index and dtypes; no decimal formatting/rounding."""
    if len(frame) > 4096 or len(frame.columns) > 128:
        raise ValueError("frame_budget_exceeded")

    def cell(value: object) -> tuple[str, str]:
        if isinstance(value, (float, np.floating)):
            return "float", float(value).hex()
        if isinstance(value, (int, np.integer)):
            return "int", str(value)
        if isinstance(value, pd.Timestamp):
            return "timestamp", value.isoformat()
        if isinstance(value, str):
            return "text", value
        raise ValueError("unsupported_frame_value")

    return digest(
        {
            "columns": list(frame.columns),
            "dtypes": [str(d) for d in frame.dtypes],
            "index": [str(i) for i in frame.index],
            "rows": [[cell(v) for v in row] for row in frame.itertuples(index=False, name=None)],
        }
    )


class SourceModelHarness:
    """Explicit synthetic boundary probe. A local receipt is NOT source authentication."""

    def __init__(
        self,
        *,
        enabled: bool = False,
        session: CaptureSession | None = None,
        config: QlibEngineConfig | None = None,
        metadata: dict[str, object] | None = None,
        model_sha256: str = "",
        model_id: str = "",
        feature_contract: str = "",
    ) -> None:
        if enabled and (
            session is None
            or not session.config.enabled
            or not session.config.synthetic
            or config is None
        ):
            raise ValueError("explicit_synthetic_session_required")
        self.enabled = enabled
        self.session = session
        self.config = config
        self._metadata_json = json.dumps(metadata or {}, sort_keys=True, allow_nan=False)
        if len(self._metadata_json.encode()) > 65536 or (
            config is not None and len(config.feature_columns) > 128
        ):
            raise ValueError("configuration_budget_exceeded")
        self._configuration_sha256 = digest(asdict(config)) if config is not None else None
        self.model_sha256 = model_sha256
        self.model_id = model_id
        self.feature_contract = feature_contract
        self._packets: dict[str, tuple[bytes, SourceReceipt]] = {}
        self._features: pd.DataFrame | None = None
        self._captures: dict[str, CaptureRecord] = {}
        self._probabilities: dict[str, float] = {}
        self._selection: dict[str, bool] = {}
        self._calls: list[ModelCallProof] = []
        self._links: list[DecisionInputLink] = []
        self._latencies_ns: list[int] = []
        self._source_rows = 0
        self.failures: list[str] = []

    def receive(self, symbol: str, payload: bytes) -> SourceReceipt | None:
        if not self.enabled:
            return None
        if self.session is None or self.config is None:
            raise ValueError("harness_configuration_missing")
        if (
            self._features is not None
            or symbol in self._packets
            or len(self._packets) >= 8
            or len(payload) > 65536
        ):
            raise ValueError("source_immutable_or_budget_exceeded")
        rows = json.loads(payload)
        if (
            not isinstance(rows, list)
            or not rows
            or len(rows) > 1500
            or self._source_rows + len(rows) > 2048
        ):
            raise ValueError("kline_shape_invalid")
        previous = -1
        close_ms = -1
        for row in rows:
            if (
                not isinstance(row, list)
                or len(row) != 12
                or type(row[0]) is not int
                or type(row[6]) is not int
            ):
                raise ValueError("kline_schema_invalid")
            if row[0] <= previous or row[6] < row[0]:
                raise ValueError("kline_time_identity_invalid")
            if any(not math.isfinite(float(v)) for v in row[1:6]):
                raise ValueError("kline_value_invalid")
            previous, close_ms = row[0], max(close_ms, row[6])
        close = datetime.fromtimestamp(close_ms / 1000, tz=timezone.utc)
        receipt = self.session.receive_source(
            source_id=f"binance-usdm-klines-{symbol}",
            payload=payload,
            event_time_utc=close,
            is_candle=True,
            candle_closed_at_utc=close,
        )
        if receipt is None:
            raise ValueError("source_receipt_rejected")
        self._packets[symbol] = payload, receipt
        self._source_rows += len(rows)
        return receipt

    def build_features(self) -> pd.DataFrame:
        if not self.enabled or self.config is None or not self._packets:
            raise ValueError("source_receipts_required")
        if self._features is not None:
            return self._features.copy(deep=True)
        frames = [
            _normalize_public_klines(json.loads(raw), symbol, self.config.timeframe)
            for symbol, (raw, _) in sorted(self._packets.items())
        ]
        self._features = build_market_feature_frame(pd.concat(frames, ignore_index=True))
        frame_identity(self._features)
        return self._features.copy(deep=True)

    def bind_model(
        self, model: Estimator, *, call_id: str, opportunities: tuple[Opportunity, ...]
    ) -> BoundModel:
        return BoundModel(self, model, call_id, opportunities)

    def _consume(
        self,
        model: Estimator,
        frame: pd.DataFrame,
        call_id: str,
        opportunities: tuple[Opportunity, ...],
    ) -> NDArray[np.float64]:
        if not self.enabled:
            return model.predict_proba(frame)
        if self.session is None or self.config is None or self._features is None:
            raise ValueError("source_features_not_bound")
        if digest(asdict(self.config)) != self._configuration_sha256:
            raise ValueError("configuration_changed_after_receive")
        if self._calls or len(opportunities) > 8:
            raise ValueError("one_immutable_invocation_per_harness")
        started = time.perf_counter_ns()
        metadata = json.loads(self._metadata_json)
        # Reproduce the existing selection of latest rows, not a new feature pipeline.
        raw = self._features.loc[self._features["tf"].eq(self.config.timeframe)].copy()
        raw = predictor._drop_rows_without_required_data(
            raw, feature_columns=self.config.feature_columns
        )
        latest = raw.sort_values(["symbol", "ts"]).groupby("symbol", as_index=False).tail(1)
        expected = predictor._transform_features(latest, metadata)
        if (
            frame_identity(frame) != frame_identity(expected)
            or not np.isfinite(frame.to_numpy(dtype=float)).all()
        ):
            raise ValueError("actual_transformed_matrix_mismatch")
        symbols = tuple(latest["symbol"].astype(str))
        if tuple(o.symbol for o in opportunities) != symbols or len(
            {o.opportunity_id for o in opportunities}
        ) != len(symbols):
            raise ValueError("pre_filter_opportunity_identity_mismatch")
        code_sha = digest(
            {
                f.__name__: hashlib.sha256(inspect.getsource(f).encode()).hexdigest()
                for f in (
                    predictor.export_latest_qlib_predictions,
                    predictor._transform_features,
                    build_market_feature_frame,
                    _normalize_public_klines,
                )
            }
        )
        context = {
            "config": asdict(self.config),
            "metadata": metadata,
            "code_sha256": code_sha,
            "feature_frame_sha256": frame_identity(self._features),
            "matrix_sha256": frame_identity(frame),
            "receipts": [r.receipt_id for _, r in self._packets.values()],
            "numpy": np.__version__,
            "pandas": pd.__version__,
        }
        config_sha = digest(context)
        for i, opportunity in enumerate(opportunities):
            receipt = self._packets[opportunity.symbol][1]
            record = self.session.capture(
                opportunity_id=opportunity.opportunity_id,
                candidate_id=opportunity.candidate_id,
                signal_id=opportunity.signal_id,
                symbol=opportunity.symbol,
                side=opportunity.side,
                feature_names=tuple(str(c) for c in frame.columns),
                feature_values=tuple(float(v) for v in frame.iloc[i]),
                feature_sources=tuple((receipt.source_id,) for _ in frame.columns),
                sources=(receipt,),
                feature_contract_version=self.feature_contract,
                model_id=self.model_id,
                model_sha256=self.model_sha256,
                configuration_sha256=config_sha,
            )
            if record is None:
                raise ValueError("model_input_capture_failed")
            self._captures[opportunity.opportunity_id] = record
        before = frame_identity(frame)
        capture_ns = time.perf_counter_ns() - started
        input_tick = self.session._tick()
        output = model.predict_proba(frame)
        prediction_tick = self.session._tick()
        if before != frame_identity(frame):
            self.failures.append("model_mutated_input")
            raise ValueError("model_mutated_input")
        if (
            not isinstance(output, np.ndarray)
            or output.shape != (len(frame), 2)
            or not np.isfinite(output).all()
        ):
            self.failures.append("invalid_model_probability")
            raise ValueError("invalid_model_probability")
        if (
            (output < 0).any()
            or (output > 1).any()
            or not np.allclose(output.sum(axis=1), 1, rtol=0, atol=1e-12)
        ):
            raise ValueError("invalid_model_probability")
        body = {
            "call_id": call_id,
            "epoch_id": self.session.config.epoch_id,
            "configuration_sha256": config_sha,
            "context_json": json.dumps(
                context, sort_keys=True, separators=(",", ":"), allow_nan=False
            ),
            "model_sha256": self.model_sha256,
            "metadata_sha256": digest(metadata),
            "code_sha256": code_sha,
            "feature_frame_sha256": context["feature_frame_sha256"],
            "matrix_sha256": before,
            "columns": tuple(str(c) for c in frame.columns),
            "dtypes": tuple(str(d) for d in frame.dtypes),
            "row_index": tuple(str(i) for i in frame.index),
            "opportunities": tuple(o.opportunity_id for o in opportunities),
            "capture_sha256": tuple(
                self._captures[o.opportunity_id].record_sha256 for o in opportunities
            ),
            "receipt_ids": tuple(r.receipt_id for _, r in self._packets.values()),
            "probabilities": tuple(tuple(float(v) for v in row) for row in output),
            "model_input_at_utc": input_tick.utc,
            "model_input_monotonic_ns": input_tick.monotonic_ns,
            "prediction_available_at_utc": prediction_tick.utc,
            "prediction_monotonic_ns": prediction_tick.monotonic_ns,
            "synthetic": True,
            "schema_version": "paper_pit_model_call_harness_v1",
        }
        # Pydantic emits UTC as Z; seal exactly that canonical JSON representation.
        rendered = {
            k: v.isoformat().replace("+00:00", "Z") if isinstance(v, datetime) else v
            for k, v in body.items()
        }
        self._calls.append(
            ModelCallProof.model_validate({**rendered, "record_sha256": digest(rendered)})
        )
        self._probabilities.update(
            (o.opportunity_id, float(output[i, 1])) for i, o in enumerate(opportunities)
        )
        self._latencies_ns.append(capture_ns)
        return output

    def observe_selection(self, opportunity_id: str, *, selected: bool) -> LifecycleRecord | None:
        if opportunity_id not in self._probabilities or self.session is None:
            raise ValueError("prediction_parent_required")
        if opportunity_id in self._selection:
            raise ValueError("selection_already_recorded")
        self._selection[opportunity_id] = selected
        return (
            None
            if selected
            else self.session.transition(self._captures[opportunity_id], selector_rejected=True)
        )

    def observe_decision(self, opportunity_id: str, decision: DecisionRecordV42) -> LifecycleRecord:
        if not self._selection.get(opportunity_id) or self.session is None:
            raise ValueError("selected_opportunity_required")
        bound = self.session.proof.error_bound_seconds if self.session.proof is not None else 0
        if (
            self._calls[0].prediction_available_at_utc + timedelta(seconds=bound)
            > decision.decision_timestamp
        ):
            self.failures.append("prediction_received_after_decision")
            raise ValueError("prediction_received_after_decision")
        probability = self._probabilities[opportunity_id]
        if (
            decision.calibrated_probability != probability
            or decision.qlib_score != probability * 2 - 1
        ):
            raise ValueError("prediction_decision_value_mismatch")
        result = self.session.transition(self._captures[opportunity_id], decision=decision)
        if result is None:
            raise ValueError("decision_binding_failed")
        body = {
            "opportunity_id": opportunity_id,
            "model_call_sha256": self._calls[0].record_sha256,
            "capture_sha256": self._captures[opportunity_id].record_sha256,
            "decision_event_id": decision.event_id,
            "decision_payload_sha256": decision.payload_sha256,
        }
        self._links.append(
            {
                "opportunity_id": opportunity_id,
                "model_call_sha256": self._calls[0].record_sha256,
                "capture_sha256": self._captures[opportunity_id].record_sha256,
                "decision_event_id": decision.event_id,
                "decision_payload_sha256": decision.payload_sha256,
                "record_sha256": digest(body),
            }
        )
        return result

    def capture_for(self, opportunity_id: str) -> CaptureRecord:
        return self._captures[opportunity_id]

    def calls(self) -> tuple[ModelCallProof, ...]:
        return tuple(self._calls)

    def links(self) -> tuple[DecisionInputLink, ...]:
        return tuple(self._links)

    def coverage(self) -> CoverageReport:
        return coverage(self.session.records() if self.session else (), include_synthetic=True)

    def report(self) -> HarnessReport:
        return {
            "source_receipt_boundary": "PARTIAL",
            "actual_model_input_binding": "PROVEN_IN_HARNESS"
            if self._calls and not self.failures
            else "BLOCKED",
            "clock_custody": "UNPROVEN",
            "pre_filter_opportunity_coverage": "HARNESS_ONLY" if self._calls else "BLOCKED",
            "synthetic": True,
            "opportunities": len(self._captures),
            "selected": sum(self._selection.values()),
            "selector_rejected": sum(not v for v in self._selection.values()),
            "selection_pending": len(self._captures) - len(self._selection),
            "capture_overhead_ns": tuple(self._latencies_ns),
            "hard_real_time_guarantee": False,
            "runtime_integration_allowed": False,
            "paper_modified": False,
            "operational_authority": False,
            "real_prospective_coverage": "NOT_COLLECTED",
            "write_performed": False,
            "quarantined_captures": sum(
                c.eligibility == "QUARANTINED" for c in self._captures.values()
            ),
        }


class BoundModel:
    def __init__(
        self,
        harness: SourceModelHarness,
        model: Estimator,
        call_id: str,
        opportunities: tuple[Opportunity, ...],
    ) -> None:
        self.harness, self.model = harness, model
        self.call_id, self.opportunities = call_id, opportunities

    def predict_proba(self, frame: pd.DataFrame) -> NDArray[np.float64]:
        return self.harness._consume(self.model, frame, self.call_id, self.opportunities)


def validate_archived_call(proof: ModelCallProof, records: tuple[Evidence, ...]) -> tuple[str, ...]:
    """Structural archive linkage, never source authentication or runtime approval."""
    proof = ModelCallProof.model_validate(proof.model_dump(mode="json"))
    captures = {r.record_sha256: r for r in records if isinstance(r, CaptureRecord)}
    selected: list[CaptureRecord] = []
    for opportunity, sha in zip(proof.opportunities, proof.capture_sha256, strict=True):
        record = captures.get(sha)
        if record is None:
            return ("archived_capture_missing",)
        if (
            record.opportunity_id != opportunity
            or record.epoch_id != proof.epoch_id
            or record.model_sha256 != proof.model_sha256
            or record.configuration_sha256 != proof.configuration_sha256
            or record.feature_names != proof.columns
            or record.synthetic is not True
            or record.captured_at_utc > proof.model_input_at_utc
            or record.captured_monotonic_ns > proof.model_input_monotonic_ns
        ):
            return ("archived_capture_identity_mismatch",)
        if record.quarantine_reasons:
            return ("archived_capture_quarantined",)
        selected.append(record)
    if {r.receipt_id for c in selected for r in c.sources} != set(proof.receipt_ids):
        return ("archived_receipt_identity_mismatch",)
    matrix = pd.DataFrame(
        [c.feature_values for c in selected],
        columns=proof.columns,
        index=proof.row_index,
        dtype="float64",
    )
    if frame_identity(matrix) != proof.matrix_sha256:
        return ("archived_matrix_identity_mismatch",)
    related = tuple(
        r
        for r in records
        if isinstance(r, LifecycleRecord) and r.capture_sha256 in proof.capture_sha256
    )
    if coverage(tuple(selected) + related, include_synthetic=True)["status"] == "blocked":
        return ("archived_lifecycle_quarantined",)
    for capture, probability in zip(selected, proof.probabilities, strict=True):
        for record in records:
            if (
                isinstance(record, LifecycleRecord)
                and record.capture_sha256 == capture.record_sha256
                and record.decision is not None
            ):
                d = record.decision
                if (
                    record.quarantine_reasons
                    or d.calibrated_probability != probability[1]
                    or d.qlib_score != probability[1] * 2 - 1
                    or d.decision_timestamp < proof.prediction_available_at_utc
                ):
                    return ("archived_prediction_decision_mismatch",)
    return ()
