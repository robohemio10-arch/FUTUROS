"""Network-free, synthetic packets and estimator; real normalization/transform/export."""

from __future__ import annotations

import hashlib
import io
import json
import multiprocessing as mp
import os
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path
from queue import Full
from typing import Any
from urllib.parse import parse_qs, urlparse

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from smartcrypto.qlib_engine import market_features_refresh, predictor
from smartcrypto.qlib_engine.common import QlibEngineConfig
from smartcrypto.qlib_engine.sklearn_compatibility import evaluate_sklearn_compatibility
from smartcrypto.research.pretrade_causal_capture.archive import EvidenceArchive
from smartcrypto.research.pretrade_causal_capture.capture import CaptureSession
from smartcrypto.research.pretrade_causal_capture.process_writer import IsolatedWriter
from smartcrypto.research.pretrade_causal_capture.source_model_proof import (
    ModelCallProof,
    Opportunity,
    SourceModelHarness,
    frame_identity,
    validate_archived_call,
)
from test_paper_pretrade_causal_capture_v1 import (
    BASE,
    ROOT,
    FixtureClock,
    capture,
    decision,
    link_for,
    session,
)


class FixtureEstimator:
    """Deliberately not a trained model. Inputs are independently observed here."""

    def __init__(self, probabilities: tuple[float, ...] = (0.8, 0.51)) -> None:
        self.probabilities = probabilities
        self.seen: pd.DataFrame | None = None
        self.seen_identity: str | None = None
        self.input_object: pd.DataFrame | None = None

    def predict_proba(self, frame: pd.DataFrame) -> np.ndarray:
        self.input_object = frame
        self.seen = frame.copy(deep=True)
        self.seen_identity = frame_identity(frame)
        return np.array([[1 - p, p] for p in self.probabilities], dtype=np.float64)


def config() -> QlibEngineConfig:
    return QlibEngineConfig(
        timeframe="5m",
        target_horizon=1,
        min_rows_for_training=100,
        test_size=0.3,
        random_state=7,
        model_version="fixture-frozen-model-v1",
        prediction_threshold=0.55,
        max_position_usdt=100,
        leverage=1,
        signal_ttl_minutes=5,
        feature_columns=["ret_1", "volume", "market_regime"],
    )


def metadata() -> dict[str, object]:
    return {
        "source_feature_columns": config().feature_columns,
        "numeric_columns": ["ret_1", "volume"],
        "numeric_medians": {"ret_1": 0.0, "volume": 10.0},
        "categorical_columns": ["market_regime"],
        "categorical_levels": {"market_regime": ["trend_up", "trend_down", "range"]},
        "model_feature_columns": [
            "ret_1",
            "volume",
            "market_regime__trend_up",
            "market_regime__trend_down",
            "market_regime__range",
        ],
    }


MODEL_BYTES = b"SYNTHETIC_ESTIMATOR_IDENTITY_ONLY_NOT_A_TRAINED_MODEL"
MODEL_HASH = hashlib.sha256(MODEL_BYTES).hexdigest()


def harness(s: CaptureSession | None = None) -> SourceModelHarness:
    return SourceModelHarness(
        enabled=True,
        session=s or session(),
        config=config(),
        metadata=metadata(),
        model_sha256=MODEL_HASH,
        model_id="qlib_lgbm_ranker",
        feature_contract="unified_feature_contract_v1",
    )


def packet_bytes(symbol: str, *, future: bool = False) -> bytes:
    rows = []
    for i in range(280):
        start = BASE - timedelta(minutes=5 * (280 - i))
        price = (50000 if symbol == "BTCUSDT" else 2500) + i * 0.25 + (i % 7) * 0.1
        rows.append(
            [
                int(start.timestamp() * 1000),
                str(price),
                str(price + 2),
                str(price - 2),
                str(price + 0.5),
                str(10 + i % 5),
                int((start + timedelta(minutes=5)).timestamp() * 1000) - 1,
                "100",
                12,
                "5",
                "50",
                "0",
            ]
        )
    if future:
        rows[-1][6] = int((BASE + timedelta(minutes=10)).timestamp() * 1000)
    return json.dumps(rows, separators=(",", ":")).encode()


def install_synthetic_transport(h: SourceModelHarness, monkeypatch: pytest.MonkeyPatch) -> None:
    class Received(io.BytesIO):
        def __init__(self, symbol: str) -> None:
            super().__init__(packet_bytes(symbol))
            self.symbol = symbol

        def read(self, *args: Any, **kwargs: Any) -> bytes:
            payload = super().read(*args, **kwargs)
            h.receive(self.symbol, payload)
            return payload

    def fixture_transport(request: Any, *, timeout: float) -> Received:
        assert timeout == 30
        url = urlparse(request.full_url)
        assert url.netloc == "fapi.binance.com" and url.path == "/fapi/v1/klines"
        return Received(parse_qs(url.query)["symbol"][0])

    monkeypatch.setattr(market_features_refresh, "urlopen", fixture_transport)
    # No sleep is necessary for the injected public-response fixtures.
    monkeypatch.setattr(market_features_refresh.time, "sleep", lambda _: None)


def export(
    h: SourceModelHarness,
    model: FixtureEstimator,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    suffix: str = "one",
    mutate: str | None = None,
) -> pd.DataFrame:
    install_synthetic_transport(h, monkeypatch)
    path = tmp_path / f"features-{suffix}.parquet"
    refresh = market_features_refresh.refresh_qlib_market_features(
        source_path=tmp_path / "no-raw-source.parquet",
        existing_features_path=tmp_path / "no-existing-features.parquet",
        output_path=path,
        report_path=tmp_path / f"refresh-{suffix}.json",
        symbols=["BTCUSDT", "ETHUSDT"],
        timeframe="5m",
        public_download_enabled=True,
        public_download_lookback_candles=280,
        raw_recent_output_path=tmp_path / f"raw-{suffix}.parquet",
        request_sleep_seconds=0,
        now=BASE + timedelta(minutes=5),
    )
    assert refresh["status"] == "ok" and refresh["public_download"]["rows"] == 560
    features = h.build_features()
    pd.testing.assert_frame_equal(pd.read_parquet(path), features)
    if mutate == "features":
        features.loc[features["symbol"].eq("BTCUSDT"), "volume"] = 999.0
    features.to_parquet(path, index=False)
    model_path = tmp_path / "fixture-model.bin"
    model_path.write_bytes(MODEL_BYTES)
    identities = tuple(
        Opportunity(
            f"opportunity-{suffix}-{symbol}",
            f"candidate-{suffix}-{symbol}",
            f"signal-{suffix}-{symbol}",
            symbol,
            "long",
        )
        for symbol in ("BTCUSDT", "ETHUSDT")
    )
    bound = h.bind_model(model, call_id=f"call-{suffix}", opportunities=identities)
    report = evaluate_sklearn_compatibility(
        artifact_version="fixture-v1", runtime_version="fixture-v1"
    )
    monkeypatch.setattr(
        predictor,
        "load_sklearn_artifact",
        lambda *a, **k: (
            {
                "model": bound,
                "feature_columns": config().feature_columns,
                "feature_metadata": metadata(),
            },
            report,
        ),
    )
    output = tmp_path / f"predictions-{suffix}.parquet"
    result = predictor.export_latest_qlib_predictions(
        market_features_path=path,
        model_path=model_path,
        output_path=output,
        report_path=tmp_path / f"report-{suffix}.json",
        config=config(),
    )
    assert result["status"] == "ok"
    return pd.read_parquet(output)


def decide_for(
    h: SourceModelHarness, symbol: str, *, suffix: str = "one", risk: bool = False
) -> Any:
    probability = h.calls()[0].probabilities[0 if symbol == "BTCUSDT" else 1][1]
    body = dict(
        candidate_id=f"candidate-{suffix}-{symbol}",
        signal_id=f"signal-{suffix}-{symbol}",
        event_id=f"decision-{suffix}-{symbol}",
        symbol=symbol,
        pair=f"{symbol[:-4]}/USDT:USDT",
        model_hash=MODEL_HASH,
        calibrated_probability=probability,
        qlib_score=probability * 2 - 1,
    )
    if risk:
        body.update(
            final_decision="BLOCK",
            risk_decision="REJECTED",
            risk_reasons=["fixture_risk_reject"],
            final_reasons=["fixture_risk_reject"],
            approved_stake_usdt=0.0,
            approved_leverage=0.0,
        )
    return decision(**body)


def test_real_export_binds_source_transform_input_prediction_decision_and_trade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = harness()
    model = FixtureEstimator()
    predictions = export(h, model, tmp_path, monkeypatch)
    call = h.calls()[0]
    assert model.seen is not None and model.seen_identity == call.matrix_sha256
    assert tuple(model.seen.columns) == call.columns
    assert tuple(predictions["prob_up"]) == (0.8, 0.51)
    assert h.session is not None and isinstance(h.session.clock, FixtureClock)
    h.session.clock.at = BASE + timedelta(seconds=6)
    for symbol, selected in [("BTCUSDT", True), ("ETHUSDT", False)]:
        h.observe_selection(f"opportunity-one-{symbol}", selected=selected)
    d = decide_for(h, "BTCUSDT")
    allowed = h.observe_decision("opportunity-one-BTCUSDT", d)
    assert allowed.eligibility == "CAUSALLY_COMPLETE"
    c = h.capture_for("opportunity-one-BTCUSDT")
    link = link_for(d)
    assert h.session.transition(c, decision=d, trade_link=link) is not None
    h.session.clock.at = BASE + timedelta(minutes=10)
    outcome = h.session.transition(
        c,
        decision=d,
        trade_link=link,
        close_time_utc=BASE + timedelta(minutes=5),
        reported_net_pnl=-1.0,
        financial_source_sha256="d" * 64,
    )
    assert outcome is not None and outcome.eligibility == "CAUSALLY_COMPLETE"
    assert h.coverage()["totals"]["outcome_known"] == 1
    assert h.coverage()["totals"]["selector_rejected"] == 1
    assert h.links()[0]["model_call_sha256"] == call.record_sha256
    writer = IsolatedWriter(
        enabled=True, root=tmp_path / "external-archive", forbidden_roots=(ROOT,)
    )
    writer.start()
    try:
        assert writer.submit(call)
        assert all(writer.submit(record) for record in h.session.records())
    finally:
        result = writer.finish()
    assert result.status == "passed" and result.shutdown_complete
    assert result.child_pid != os.getpid() and result.persisted == len(h.session.records()) + 1
    assert result.model_calls_persisted == 1
    archived = EvidenceArchive(tmp_path / "external-archive", forbidden_roots=(ROOT,)).read()
    assert {r.record_sha256 for r in archived} == {r.record_sha256 for r in h.session.records()}
    proof_file = next((tmp_path / "external-archive").glob("model-call-*.json"))
    assert ModelCallProof.model_validate_json(proof_file.read_bytes()) == call
    assert validate_archived_call(call, archived) == ()
    assert validate_archived_call(call, ()) == ("archived_capture_missing",)
    print(
        json.dumps(
            {"synthetic_only": True, "model_input_proof": h.report(), "writer": asdict(result)},
            sort_keys=True,
        )
    )


def test_all_pre_filter_rows_survive_risk_rejection_allow_and_missing_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = harness()
    export(h, FixtureEstimator((0.8, 0.85)), tmp_path, monkeypatch)
    assert h.session is not None and isinstance(h.session.clock, FixtureClock)
    h.session.clock.at = BASE + timedelta(seconds=6)
    for symbol, risk in [("BTCUSDT", True), ("ETHUSDT", False)]:
        key = f"opportunity-one-{symbol}"
        h.observe_selection(key, selected=True)
        assert h.observe_decision(key, decide_for(h, symbol, risk=risk)) is not None
    totals = h.coverage()["totals"]
    assert totals["opportunities"] == 2 and totals["risk_rejected"] == totals["allow"] == 1
    assert totals["outcome_unknown"] == 2 and h.report()["selected"] == 2


def test_tampered_feature_version_does_not_reach_estimator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = harness()
    model = FixtureEstimator()
    with pytest.raises(ValueError, match="actual_transformed_matrix_mismatch"):
        export(h, model, tmp_path, monkeypatch, mutate="features")
    assert model.seen is None and h.report()["actual_model_input_binding"] == "BLOCKED"


def test_no_independent_clock_proof_quarantines_real_looking_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = session()
    s.proof = None
    h = harness(s)
    export(h, FixtureEstimator(), tmp_path, monkeypatch)
    assert h.report()["clock_custody"] == "UNPROVEN" and h.report()["quarantined_captures"] == 2
    assert h.coverage()["totals"].get("causally_complete", 0) == 0


@pytest.mark.parametrize("bad", ["partial", "duplicate", "bad_schema", "nonfinite", "invalid_json"])
def test_invalid_source_is_blocked_or_quarantined(bad: str) -> None:
    h = harness()
    raw = packet_bytes("BTCUSDT", future=bad == "partial")
    rows = json.loads(raw)
    if bad == "duplicate":
        rows[-1][0] = rows[-2][0]
    elif bad == "bad_schema":
        rows[-1].pop()
    elif bad == "nonfinite":
        rows[-1][2] = "NaN"
    raw = b"invalid" if bad == "invalid_json" else json.dumps(rows).encode()
    if bad == "partial":
        h.receive("BTCUSDT", raw)
        features = h.build_features()
        model = h.bind_model(
            FixtureEstimator((0.8,)),
            call_id="partial",
            opportunities=(Opportunity("partial", "partial", "partial", "BTCUSDT", "long"),),
        )
        latest = features.groupby("symbol").tail(1)
        model.predict_proba(predictor._transform_features(latest, metadata()))
        assert "CANDLE_NOT_CLOSED" in h.capture_for("partial").quarantine_reasons
    else:
        with pytest.raises((ValueError, TypeError)):
            h.receive("BTCUSDT", raw)


def test_sealed_model_call_rejects_tampering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = harness()
    export(h, FixtureEstimator(), tmp_path, monkeypatch)
    body = h.calls()[0].model_dump(mode="json")
    body["probabilities"][0] = [0.01, 0.99]
    with pytest.raises(ValidationError, match="model_call_seal_mismatch"):
        ModelCallProof.model_validate(body)


def test_changed_prediction_or_selection_cannot_attach_a_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = harness()
    export(h, FixtureEstimator(), tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="selected_opportunity_required"):
        h.observe_decision("opportunity-one-BTCUSDT", decide_for(h, "BTCUSDT"))
    h.observe_selection("opportunity-one-BTCUSDT", selected=True)
    with pytest.raises(ValueError, match="prediction_decision_value_mismatch"):
        h.observe_decision("opportunity-one-BTCUSDT", decision())


def test_default_harness_and_writer_have_no_side_effects(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("unexpected effect")

    monkeypatch.setattr(mp, "get_context", forbidden)
    monkeypatch.setattr(Path, "mkdir", forbidden)
    h = SourceModelHarness()
    assert h.receive("BTCUSDT", b"invalid") is None
    writer = IsolatedWriter(root=tmp_path / "external", forbidden_roots=(ROOT,))
    writer.start()
    assert writer.finish().status == "disabled" and not list(tmp_path.iterdir())


def test_writer_reports_real_child_io_failure_without_leaking_details(tmp_path: Path) -> None:
    target = tmp_path / "not-a-directory"
    target.write_bytes(b"fixture")
    writer = IsolatedWriter(enabled=True, root=target, forbidden_roots=(ROOT,))
    writer.start()
    try:
        assert writer.submit(capture(session()))
    finally:
        result = writer.finish()
    assert result.status == "blocked" and result.reason == "archive_persistence_failed"
    assert result.persisted == 0 and result.shutdown_complete and target.read_bytes() == b"fixture"


def test_writer_backpressure_and_budget_are_explicit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer = IsolatedWriter(enabled=True, root=tmp_path / "archive", forbidden_roots=(ROOT,))
    writer.start()

    def saturated(raw: bytes) -> None:
        raise Full

    try:
        assert writer._queue is not None
        monkeypatch.setattr(writer._queue, "put_nowait", saturated)
        assert not writer.submit(capture(session()))
    finally:
        result = writer.finish()
    assert (
        result.status == "blocked"
        and result.dropped == 1
        and result.reason == "queue_backpressure_loss"
    )
    assert result.shutdown_complete


def test_writer_unexpected_child_exit_cannot_claim_completion(tmp_path: Path) -> None:
    writer = IsolatedWriter(enabled=True, root=tmp_path / "archive", forbidden_roots=(ROOT,))
    writer.start()
    assert writer._process is not None
    writer._process.terminate()
    writer._process.join(timeout=3)
    assert not writer.submit(capture(session()))
    result = writer.finish(timeout_seconds=0.1)
    assert result.status == "blocked" and result.shutdown_complete


def test_harness_never_installs_runtime_hooks() -> None:
    for file in (
        "smartcrypto/qlib_engine/predictor.py",
        "smartcrypto/qlib_engine/market_features_refresh.py",
        "smartcrypto/execution/signal_producer.py",
    ):
        assert "source_model_proof" not in (ROOT / file).read_text(encoding="utf-8")


def test_late_model_result_never_attaches_to_earlier_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = harness()

    class SlowFixture(FixtureEstimator):
        def predict_proba(self, frame: pd.DataFrame) -> np.ndarray:
            assert h.session is not None and isinstance(h.session.clock, FixtureClock)
            h.session.clock.at = BASE + timedelta(seconds=10)
            return super().predict_proba(frame)

    export(h, SlowFixture(), tmp_path, monkeypatch)
    h.observe_selection("opportunity-one-BTCUSDT", selected=True)
    with pytest.raises(ValueError, match="prediction_received_after_decision"):
        h.observe_decision("opportunity-one-BTCUSDT", decide_for(h, "BTCUSDT"))
    assert not h.links() and h.coverage()["totals"].get("allow", 0) == 0


@pytest.mark.parametrize(
    "mode",
    ["mutate_input", "bad_shape", "nonfinite", "out_of_range", "bad_sum", "clock_regression"],
)
def test_invalid_model_observation_never_becomes_a_binding(
    mode: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = harness()

    class BadFixture(FixtureEstimator):
        def predict_proba(self, frame: pd.DataFrame) -> np.ndarray:
            value = super().predict_proba(frame)
            if mode == "mutate_input":
                frame.iloc[0, 0] = 0.99
            elif mode == "bad_shape":
                return value[:, :1]
            elif mode == "nonfinite":
                value[0, 0] = np.nan
            elif mode == "out_of_range":
                value[0] = [-1.0, 2.0]
            elif mode == "bad_sum":
                value[0] = [0.5, 0.6]
            else:
                assert h.session is not None and isinstance(h.session.clock, FixtureClock)
                h.session.clock.at -= timedelta(seconds=1)
            return value

    with pytest.raises((ValueError, ValidationError)):
        export(h, BadFixture(), tmp_path, monkeypatch)
    assert not h.calls() and not h.links()


def test_configuration_and_source_are_immutable_after_receipt() -> None:
    h = harness()
    payload = packet_bytes("BTCUSDT")
    h.receive("BTCUSDT", payload)
    with pytest.raises(ValueError, match="source_immutable"):
        h.receive("BTCUSDT", payload)
    features = h.build_features()
    assert h.config is not None
    h.config.feature_columns.append("close")
    with pytest.raises(ValueError, match="configuration_changed"):
        h.bind_model(
            FixtureEstimator((0.8,)),
            call_id="changed",
            opportunities=(Opportunity("x", "x", "x", "BTCUSDT", "long"),),
        ).predict_proba(
            predictor._transform_features(features.groupby("symbol").tail(1), metadata())
        )


def test_source_budget_and_absent_receipts_are_not_replaced_by_parquet_hash() -> None:
    h = harness()
    with pytest.raises(ValueError, match="source_receipts_required"):
        h.build_features()
    with pytest.raises(ValueError, match="budget"):
        h.receive("BTCUSDT", b"x" * 65537)
    with pytest.raises(ValueError, match="explicit_synthetic_session_required"):
        SourceModelHarness(enabled=True, session=session(synthetic=False), config=config())


def test_writer_incomplete_shutdown_is_blocked_and_fixture_child_is_cleaned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer = IsolatedWriter(enabled=True, root=tmp_path / "archive", forbidden_roots=(ROOT,))
    writer.start()
    assert writer._process is not None and writer._result_queue is not None
    child = writer._process
    original_terminate = child.terminate
    original_alive = child.is_alive
    original_join = child.join

    def denied() -> None:
        raise PermissionError("fixture_only")

    try:
        with monkeypatch.context() as patch:
            # Deterministic unconfirmed termination, without sleeping or an actual orphan.
            patch.setattr(child, "is_alive", lambda: True)
            patch.setattr(child, "terminate", denied)
            patch.setattr(child, "kill", denied)
            result = writer.finish(timeout_seconds=0.1)
        assert result.status == "blocked" and result.reason == "shutdown_incomplete"
        assert result.shutdown_complete is False
    finally:
        if original_alive():
            original_terminate()
        original_join(timeout=3)
        assert not original_alive()


def test_writer_refuses_non_synthetic_and_record_budget(tmp_path: Path) -> None:
    writer = IsolatedWriter(
        enabled=True, root=tmp_path / "archive", forbidden_roots=(ROOT,), max_records=1
    )
    writer.start()
    try:
        record = capture(session())
        assert writer.submit(record)
        assert not writer.submit(record)
    finally:
        result = writer.finish()
    assert result.status == "blocked" and result.reason == "writer_message_budget_exceeded"
    assert result.shutdown_complete


def test_model_call_archive_is_idempotent_and_refuses_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from smartcrypto.research.pretrade_causal_capture.process_writer import _persist_model_call
    from smartcrypto.research.pretrade_causal_capture.contracts import digest

    h = harness()
    export(h, FixtureEstimator(), tmp_path, monkeypatch)
    call = h.calls()[0]
    archive = EvidenceArchive(tmp_path / "archive", authorized=True, forbidden_roots=(ROOT,))
    _persist_model_call(archive, call)
    target = next(archive.root.glob("model-call-*.json"))
    original = target.read_bytes()
    _persist_model_call(archive, call)
    assert target.read_bytes() == original
    body = call.model_dump(mode="json", exclude={"record_sha256"})
    body["probabilities"][0] = [0.3, 0.7]
    different = ModelCallProof.model_validate({**body, "record_sha256": digest(body)})
    with pytest.raises(ValueError, match="model_call_identity_conflict"):
        _persist_model_call(archive, different)
    assert target.read_bytes() == original


def test_real_bounded_mailbox_refuses_when_all_slots_reserved(tmp_path: Path) -> None:
    writer = IsolatedWriter(
        enabled=True, root=tmp_path / "archive", forbidden_roots=(ROOT,), queue_capacity=1
    )
    writer.start()
    assert writer._queue is not None
    # Reserve the actual IPC semaphore without relying on consumer scheduling.
    slots = getattr(writer._queue, "_sem")
    assert slots.acquire(block=False)
    try:
        assert writer.submit(capture(session())) is False
    finally:
        slots.release()
        result = writer.finish()
    assert result.status == "blocked" and result.dropped == 1 and result.shutdown_complete
