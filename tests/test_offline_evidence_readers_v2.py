from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from smartcrypto.learning.qlib_v3_prospective.contracts import CANONICAL, digest
from smartcrypto.research.canonical_treatment import offline_evidence_readers_v2 as readers
from tests import test_economic_control_treatment_scorecard_v1 as scorecard_fixtures
from tests import test_portfolio_of_alphas_oos_economic_selection_v1 as portfolio_fixtures
from tests import test_research_council_alpha_uplift_ab_v1 as council_fixtures
from tests.v2_contract_fixtures import encode_source, materialized_b10_sources


def _install_sources(
    monkeypatch: pytest.MonkeyPatch,
    sources: Mapping[str, object],
    *,
    blocked: Mapping[str, str] | None = None,
) -> list[str]:
    calls: list[str] = []

    def resolve(_descriptor: object, **kwargs: Any) -> SimpleNamespace:
        logical = kwargs["logical_source"]
        assert kwargs["access_role"] == "research_read"
        calls.append(logical)
        reason = (blocked or {}).get(logical)
        value = sources.get(logical)
        raw = encode_source(value)
        return SimpleNamespace(
            status="blocked" if reason or logical not in sources else "ok",
            reason=reason or ("source_missing" if logical not in sources else "source_validated"),
            internal_path=f"/declared/{logical}",
            source_identity=f"fixture:{logical}",
            epoch_namespace="historical-fixture",
            mount_type="bind",
            access="RO",
            expected_sha256=hashlib.sha256(raw).hexdigest(),
            raw=None if reason or logical not in sources else raw,
        )

    monkeypatch.setattr(readers, "resolve_source", resolve)
    return calls


def _read(reader: str) -> dict[str, Any]:
    # Resolver validation is tested end-to-end in the core contract/resolver suite.
    # This boundary fixture isolates adapter routing and the existing pure builders.
    return readers.build_offline_evidence_reader_v2(
        descriptor=object(),  # type: ignore[arg-type]
        reader=reader,
        known_fingerprints={},
        observations=[],
    )


def _v3_store() -> dict[str, Any]:
    state = {
        "schema_version": "qlib_v3_prospective_evidence_store_v1",
        "identity": CANONICAL.mapping(), "signals": [], "outcomes": [],
    }
    state["store_sha256"] = digest(state)
    return state


def test_b09_uses_verified_snapshot_without_reopening(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_sources(monkeypatch, {"qlib_v3_store": _v3_store()})

    def reopen(_path: Path, *_args: Any, **_kwargs: Any) -> bytes:
        raise AssertionError("adapter reopened a source after snapshot validation")

    monkeypatch.setattr(Path, "read_bytes", reopen)
    result = _read("B09")
    assert calls == ["qlib_v3_store"]
    assert result["status"] == "waiting"
    assert result["result"]["reason"] == "no_natural_v3_shadow_signals"
    assert result["result"]["identity"] == CANONICAL.mapping()
    assert result["operational_authority"] is False
    assert result["new_cohort_certified"] is False


def test_b09_internal_seal_mismatch_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _v3_store()
    state["store_sha256"] = "0" * 64
    _install_sources(monkeypatch, {"qlib_v3_store": state})
    result = _read("B09")
    assert result["status"] == "blocked"
    assert result["reason"] == "v3_store_hash_mismatch"
    assert result["result"] is None


def test_b09_wrong_schema_not_accepted_as_empty_store(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _v3_store()
    state["schema_version"] = "unsupported"
    state["store_sha256"] = digest({key: value for key, value in state.items() if key != "store_sha256"})
    _install_sources(monkeypatch, {"qlib_v3_store": state})
    assert _read("B09")["reason"] == "store_not_v3"


def test_missing_or_ambiguous_source_never_becomes_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_sources(monkeypatch, {}, blocked={"qlib_v3_store": "source_ambiguous"})
    result = _read("B09")
    assert result["status"] == "blocked"
    assert result["reason"] == "source_blocked:source_ambiguous"
    assert result["result"] is None


def _b14_sources() -> dict[str, object]:
    from smartcrypto.research.aibot_parity.component_economic_attribution_ledger import (
        _canonical_hash,
    )

    sources = {
        "branch09_report": scorecard_fixtures._branch09(),
        **materialized_b10_sources(),
        "branch11_report": scorecard_fixtures._branch11(),
        "branch12_report": scorecard_fixtures._branch12(),
        "branch13_report": scorecard_fixtures._ledger(),
    }
    # Legacy routing fixtures deliberately omit semantic admission. V2 requires
    # the real identity and producer seal, without modifying those V1 fixtures.
    sources["branch09_report"]["identity"] = CANONICAL.mapping()
    ledger = sources["branch13_report"]
    ledger["ledger_evidence_sha256"] = _canonical_hash({
        key: value for key, value in ledger.items() if key != "ledger_evidence_sha256"
    })
    return sources


def _sealed_scorecard(report: dict[str, Any]) -> dict[str, Any]:
    from smartcrypto.research.aibot_parity.economic_control_treatment_scorecard import _hash

    report["scorecard_evidence_sha256"] = _hash({
        key: value for key, value in report.items() if key != "scorecard_evidence_sha256"
    })
    return report


def test_b14_routes_explicit_reports_to_existing_pure_builder(monkeypatch: pytest.MonkeyPatch) -> None:
    sources = _b14_sources()
    expected = scorecard_fixtures.build_economic_control_treatment_scorecard_from_reports(
        branch09_report=sources["branch09_report"],
        branch10_report=sources["branch10_report"],
        branch11_report=sources["branch11_report"],
        branch12_report=sources["branch12_report"],
        ledger_report=sources["branch13_report"],
    )
    calls = _install_sources(monkeypatch, sources)
    result = _read("B14")
    assert calls == list(_b14_sources())
    assert result["status"] == "ok"
    assert result["result"] == expected
    assert result["writes_runtime"] is False
    assert result["writes_data"] is False


def test_b10_invalid_input_keeps_b14_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    sources = _b14_sources()
    sources["branch10_report"] = {"schema_version": "controlled_failure", "status": "blocked"}
    _install_sources(monkeypatch, sources)
    result = _read("B14")
    assert result["status"] == "blocked"
    assert result["reason"] == "branch10_schema_mismatch"
    assert result["result"] is None


def test_mixed_explicit_payload_epoch_blocks_before_pure_builder(monkeypatch: pytest.MonkeyPatch) -> None:
    values = _b14_sources()
    values["branch10_report"]["epoch_id"] = "paper-b-epoch-1"
    _install_sources(monkeypatch, values)
    result = _read("B14")
    assert result["status"] == "blocked"
    assert result["reason"] == "source_payload_epoch_mismatch"


def test_b15_pure_builder_preserves_research_only_result(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_sources(monkeypatch, {
        "branch14_report": _sealed_scorecard(portfolio_fixtures._scorecard(candidate_ids={"trend-exp"})),
        "sleeve_evidence": portfolio_fixtures._evidence(
            portfolio_fixtures._sleeve("trend", "trend-exp", [4.0, -1.0, 5.0, 2.0])
        ),
    })
    result = _read("B15")
    assert result["status"] == "ok"
    assert result["result"]["operational_authority"] is False


def test_b15_does_not_invent_missing_sleeve_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_sources(monkeypatch, {"branch14_report": _sealed_scorecard(portfolio_fixtures._scorecard())})
    result = _read("B15")
    assert result["status"] == "blocked"
    assert result["reason"] == "source_blocked:source_missing"
    assert result["result"] is None


def test_b16_reuses_existing_evidence_validator(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        council_fixtures._row(i, period_id=f"period-{i}", pnl=2.0, action="ALLOW")
        for i in range(1, 5)
    ]
    scorecard = _sealed_scorecard(council_fixtures._scorecard())
    evidence = council_fixtures._evidence(rows)
    evidence["source_scorecard_evidence_sha256"] = scorecard["scorecard_evidence_sha256"]
    _install_sources(monkeypatch, {"branch14_report": scorecard, "council_ab_evidence": evidence})
    result = _read("B16")
    assert result["status"] == "ok"
    assert result["result"]["operational_authority"] is False


def test_b10_reads_only_materialized_report_without_fit(monkeypatch: pytest.MonkeyPatch) -> None:
    from smartcrypto.research.aibot_parity import market_intelligence_pnl_ablation as ablation

    def forbidden(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("B10 training path invoked")

    monkeypatch.setattr(ablation, "_fit_predict", forbidden)
    monkeypatch.setattr(ablation, "build_market_intelligence_pnl_ablation_v1", forbidden)
    calls = _install_sources(monkeypatch, materialized_b10_sources())
    result = _read("B10")
    assert result["status"] == "ok"
    assert calls == list(materialized_b10_sources())
    assert result["training_executed_by_adapter"] is False
    assert result["evaluation_scope"] == "existing_materialized_report_and_explicit_source_bindings"


def test_b10_mismatched_frozen_source_hash_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    sources = materialized_b10_sources()
    sources["branch10_report"]["source"]["dataset_sha256"] = "0" * 64
    _install_sources(monkeypatch, sources)
    result = _read("B10")
    assert result["status"] == "blocked"
    assert result["reason"] == "branch10_explicit_source_hash_mismatch"


def test_b10_blocked_report_not_promoted(monkeypatch: pytest.MonkeyPatch) -> None:
    sources = materialized_b10_sources()
    sources["branch10_report"]["status"] = "blocked"
    _install_sources(monkeypatch, sources)
    result = _read("B10")
    assert result["status"] == "blocked"
    assert result["reason"] == "branch10_status_invalid:blocked"
    assert result["result"] is None


@pytest.mark.parametrize(("reader", "logical", "reason"), [
    ("economic_monitor", "monitor_report", "monitor_generation_requires_registered_cohort_and_authorized_financial_inputs"),
    ("parity_collector", "parity_report", "runtime_parity_collection_requires_versioned_deployment_attestation"),
])
def test_operational_collectors_are_explicitly_blocked(
    monkeypatch: pytest.MonkeyPatch, reader: str, logical: str, reason: str
) -> None:
    calls = _install_sources(monkeypatch, {logical: {"status": "ok"}})
    result = _read(reader)
    assert calls == [logical]
    assert result["status"] == "blocked"
    assert result["reason"] == reason
    assert result["result"] is None


def test_unknown_reader_does_not_resolve_or_read(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_sources(monkeypatch, {})
    result = _read("runtime_trading")
    assert calls == []
    assert result["status"] == "blocked"
    assert result["reason"] == "reader_not_supported"
