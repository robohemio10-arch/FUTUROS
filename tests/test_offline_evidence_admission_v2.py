"""V2 semantic admission through the real resolver, using isolated source files."""
from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import pytest

from smartcrypto.learning.qlib_v3_prospective.contracts import CANONICAL
from smartcrypto.research.aibot_parity.component_economic_attribution_ledger import (
    _canonical_hash as ledger_hash,
)
from smartcrypto.research.aibot_parity.economic_control_treatment_scorecard import (
    _hash as scorecard_hash,
)
from smartcrypto.research.canonical_treatment.offline_evidence_readers_v2 import (
    build_offline_evidence_reader_v2,
)
from tests import test_economic_control_treatment_scorecard_v1 as scorecard_fixtures
from tests import test_portfolio_of_alphas_oos_economic_selection_v1 as portfolio_fixtures
from tests import test_research_council_alpha_uplift_ab_v1 as council_fixtures
from tests.v2_contract_fixtures import (
    context_for_sources,
    encode_source,
    materialized_b10_sources,
    reseal,
    validate_synthetic,
)

Report = dict[str, object]


def _historical(report: Report) -> Report:
    report["epoch_id"] = "paper-b-epoch-2"
    report["experiment_id"] = "canonical-treatment-paper-v1"
    return report


def _b14_sources() -> dict[str, object]:
    b10_sources = materialized_b10_sources()
    _historical(b10_sources["branch10_report"])
    sources = {
        "branch09_report": _historical(scorecard_fixtures._branch09()),
        "branch11_report": _historical(scorecard_fixtures._branch11()),
        "branch12_report": _historical(scorecard_fixtures._branch12()),
        "branch13_report": _historical(scorecard_fixtures._ledger()),
    }
    sources["branch09_report"]["identity"] = CANONICAL.mapping()
    ledger = sources["branch13_report"]
    ledger["ledger_evidence_sha256"] = ledger_hash({
        key: value for key, value in ledger.items() if key != "ledger_evidence_sha256"
    })
    return {**sources, **b10_sources}


def _read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reader: str,
    sources: Mapping[str, object],
) -> Report:
    context = context_for_sources(tmp_path, sources)
    # Only the hermetic predecessor header uses the existing explicit unit substitute.
    # Descriptor validation, physical source hashes and source resolution remain real.
    validated = validate_synthetic(monkeypatch, context)
    return build_offline_evidence_reader_v2(
        descriptor=validated, reader=reader,
        known_fingerprints=context[3], observations=context[4],
    )


def _assert_blocked(result: Report) -> None:
    assert result["status"] == "blocked"
    assert result["result"] is None
    assert result["operational_authority"] is False
    assert result["new_cohort_certified"] is False


def test_b14_public_reader_accepts_canonical_identity_and_valid_ledger_seal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _read(tmp_path, monkeypatch, "B14", _b14_sources())
    assert result["status"] == "ok"
    assert cast(Report, result["result"])["decision"] == "SCORECARD_READY_RESEARCH_ONLY"
    assert result["research_only"] is True
    assert result["operational_authority"] is False
    assert result["new_cohort_certified"] is False


@pytest.mark.parametrize(("change", "reason"), [
    ("no_source", "branch10_source_identity_missing"),
    ("physical_link", "branch10_explicit_source_hash_mismatch"),
    ("semantic_link", "branch10_frozen_bundle_identity_mismatch"),
    ("invalid_manifest", "branch10_frozen_bundle_not_validated"),
    ("leaking_splits", "branch10_frozen_split_leakage_blocked"),
    ("missing_artifact", "source_blocked:source_binding_missing_or_ambiguous"),
    ("other_epoch", "source_blocked:historical_source_epoch_mismatch"),
])
def test_b14_rejects_the_same_inadmissible_b10_bundle_as_b10(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str, reason: str,
) -> None:
    sources = _b14_sources()
    branch10 = cast(Report, sources["branch10_report"])
    source = cast(Report, branch10["source"])
    if change == "no_source":
        del branch10["source"]
    elif change == "physical_link":
        source["dataset_sha256"] = "0" * 64
    elif change == "semantic_link":
        source["split_manifest_hash"] = "0" * 64
    elif change == "invalid_manifest":
        manifest = cast(Report, sources["wq6_dataset_manifest"])
        manifest["validation_status"] = "blocked"
        source["dataset_manifest_sha256"] = hashlib.sha256(encode_source(manifest)).hexdigest()
    elif change == "leaking_splits":
        splits = cast(Report, sources["wq6_split_manifest"])
        splits["leakage_status"] = "blocked"
        source["split_manifest_sha256"] = hashlib.sha256(encode_source(splits)).hexdigest()
    elif change == "missing_artifact":
        del sources["wq6_feature_contract"]
    else:
        branch10["epoch_id"] = "paper-b-epoch-1"

    context = context_for_sources(tmp_path, sources)
    validated = validate_synthetic(monkeypatch, context)
    for reader in ("B10", "B14"):
        result = build_offline_evidence_reader_v2(
            descriptor=validated, reader=reader,
            known_fingerprints=context[3], observations=context[4],
        )
        _assert_blocked(result)
        assert result["reason"] == reason


@pytest.mark.parametrize(("change", "reason"), [
    ("artifact_tampered", "source_blocked:source_artifact_hash_mismatch"),
    ("b14_not_authorized", "source_blocked:component_not_authorized_for_source"),
])
def test_b14_requires_the_frozen_source_to_be_intact_and_authorized_for_b14(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str, reason: str,
) -> None:
    context = context_for_sources(tmp_path, _b14_sources())
    descriptor, _, _, fingerprints, observations = context
    if change == "artifact_tampered":
        (tmp_path / "wq6_dataset.parquet").write_bytes(b"changed after sealing the binding")
    else:
        for bindings in (descriptor["source_bindings"], observations):
            for binding in bindings:
                if binding["logical_source"] == "wq6_dataset":
                    binding["components"] = ["B10"]
        reseal(descriptor, fingerprints)
    validated = validate_synthetic(monkeypatch, context)
    result = build_offline_evidence_reader_v2(
        descriptor=validated, reader="B14",
        known_fingerprints=fingerprints, observations=observations,
    )
    _assert_blocked(result)
    assert result["reason"] == reason


@pytest.mark.parametrize("change", ["missing_identity", "missing_model", "wrong_model", "wrong_v3_epoch"])
def test_b14_rejects_noncanonical_v3_identity_even_with_known_physical_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    sources = _b14_sources()
    branch09 = cast(Report, sources["branch09_report"])
    identity = cast(Report, branch09["identity"])
    if change == "missing_identity":
        del branch09["identity"]
    elif change == "missing_model":
        del identity["model_artifact_sha256"]
    elif change == "wrong_model":
        identity["model_artifact_sha256"] = "0" * 64
    else:
        identity["epoch_id"] = "qlib-v3-other"
    _assert_blocked(_read(tmp_path, monkeypatch, "B14", sources))


@pytest.mark.parametrize("change", ["missing", "invalid", "content_changed", "source_after_seal"])
def test_b14_rejects_invalid_ledger_semantics_after_physical_hash_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    sources = _b14_sources()
    ledger = cast(Report, sources["branch13_report"])
    if change == "missing":
        del ledger["ledger_evidence_sha256"]
    elif change == "invalid":
        ledger["ledger_evidence_sha256"] = "0" * 64
    elif change == "content_changed":
        summary = cast(Report, ledger["evidence_summary"])
        summary["market_intelligence_full_model_net_pnl_delta"] = 999.0
    else:
        ledger["source"] = {"producer": "appended-after-seal"}
    _assert_blocked(_read(tmp_path, monkeypatch, "B14", sources))


def test_b13_source_metadata_is_admissible_only_when_inside_its_semantic_seal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    sources = _b14_sources()
    ledger = cast(Report, sources["branch13_report"])
    ledger["source"] = {"producer": "included-before-seal"}
    ledger["ledger_evidence_sha256"] = ledger_hash({
        key: value for key, value in ledger.items() if key != "ledger_evidence_sha256"
    })
    assert _read(tmp_path, monkeypatch, "B14", sources)["status"] == "ok"


def _downstream_sources(reader: str, *, with_source: bool = False) -> dict[str, Report]:
    if reader == "B15":
        scorecard = _historical(portfolio_fixtures._scorecard(candidate_ids={"trend-exp"}))
    else:
        scorecard = _historical(council_fixtures._scorecard())
    if with_source:
        scorecard["source"] = {"producer": "included-before-seal"}
    scorecard["scorecard_evidence_sha256"] = scorecard_hash({
        key: value for key, value in scorecard.items() if key != "scorecard_evidence_sha256"
    })
    if reader == "B15":
        evidence = _historical(portfolio_fixtures._evidence(
            portfolio_fixtures._sleeve("trend", "trend-exp", [4.0, -1.0, 5.0, 2.0]),
        ))
        return {"branch14_report": scorecard, "sleeve_evidence": evidence}
    rows: list[Report] = []
    for period in ("p1", "p2", "p3"):
        for index in range(10):
            pnl = -2.0 if index < 4 else 1.0
            rows.append(council_fixtures._row(
                len(rows), period_id=period, pnl=pnl, action="ABSTAIN" if pnl < 0 else "ALLOW",
            ))
    evidence = _historical(council_fixtures._evidence(rows))
    evidence["source_scorecard_evidence_sha256"] = scorecard["scorecard_evidence_sha256"]
    return {"branch14_report": scorecard, "council_ab_evidence": evidence}


@pytest.mark.parametrize(("reader", "source", "decision"), [
    ("B15", "sleeve_evidence", "ECONOMICALLY_DEFENSIBLE_SLEEVES_SELECTED_RESEARCH_ONLY"),
    ("B16", "council_ab_evidence", "COUNCIL_UPLIFT_RESEARCH_CANDIDATE"),
])
@pytest.mark.parametrize("with_source", [False, True])
@pytest.mark.parametrize("evidence_status", [None, "ok"], ids=["status-absent", "status-ok"])
def test_downstream_readers_accept_scorecards_with_the_official_semantic_seal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reader: str, source: str,
    decision: str, with_source: bool, evidence_status: str | None,
) -> None:
    sources = _downstream_sources(reader, with_source=with_source)
    if evidence_status is not None:
        sources[source]["status"] = evidence_status
    result = _read(tmp_path, monkeypatch, reader, sources)
    assert result["status"] == "ok"
    assert cast(Report, result["result"])["decision"] == decision
    assert result["research_only"] is True
    assert result["operational_authority"] is False
    assert result["new_cohort_certified"] is False


@pytest.mark.parametrize(("reader", "source", "builder"), [
    (
        "B15",
        "sleeve_evidence",
        "smartcrypto.research.portfolio_of_alphas.oos_economic_selection."
        "build_portfolio_of_alphas_oos_economic_selection_from_scorecard",
    ),
    (
        "B16",
        "council_ab_evidence",
        "smartcrypto.research.research_council.alpha_uplift_ab."
        "build_research_council_alpha_uplift_ab_from_scorecard",
    ),
])
@pytest.mark.parametrize("status", ["blocked", "waiting", "invalid", None, True, []])
def test_downstream_readers_block_explicit_nonapproved_status_before_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reader: str, source: str,
    builder: str, status: object,
) -> None:
    sources = _downstream_sources(reader)
    sources[source]["status"] = status

    def reject_evaluator_call(**_kwargs: object) -> None:
        pytest.fail("a pure evaluator received evidence with a nonapproved status")

    monkeypatch.setattr(builder, reject_evaluator_call)
    result = _read(tmp_path, monkeypatch, reader, sources)
    _assert_blocked(result)
    assert result["reason"] == f"source_status_not_admissible:{source}"
    resolutions = cast(dict[str, Report], result["resolutions"])
    assert resolutions[source]["status"] == "ok"


@pytest.mark.parametrize("reader", ["B15", "B16"])
@pytest.mark.parametrize("change", ["missing", "invalid", "content_changed", "source_after_seal"])
def test_downstream_readers_reject_unsealed_scorecard_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reader: str, change: str,
) -> None:
    sources = _downstream_sources(reader)
    scorecard = sources["branch14_report"]
    if change == "missing":
        del scorecard["scorecard_evidence_sha256"]
    elif change == "invalid":
        scorecard["scorecard_evidence_sha256"] = "0" * 64
    elif change == "content_changed":
        scorecard["decision"] = "UNSEALED_CHANGE"
    else:
        scorecard["source"] = {"producer": "appended-after-seal"}
    _assert_blocked(_read(tmp_path, monkeypatch, reader, sources))
