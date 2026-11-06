"""V2 semantic admission through the real resolver, using isolated source files."""
from __future__ import annotations

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
from tests.v2_contract_fixtures import context_for_sources, validate_synthetic

Report = dict[str, object]


def _historical(report: Report) -> Report:
    report["epoch_id"] = "paper-b-epoch-2"
    report["experiment_id"] = "canonical-treatment-paper-v1"
    return report


def _b14_sources() -> dict[str, Report]:
    sources = {
        "branch09_report": _historical(scorecard_fixtures._branch09()),
        "branch10_report": _historical(scorecard_fixtures._branch10()),
        "branch11_report": _historical(scorecard_fixtures._branch11()),
        "branch12_report": _historical(scorecard_fixtures._branch12()),
        "branch13_report": _historical(scorecard_fixtures._ledger()),
    }
    sources["branch09_report"]["identity"] = CANONICAL.mapping()
    ledger = sources["branch13_report"]
    ledger["ledger_evidence_sha256"] = ledger_hash({
        key: value for key, value in ledger.items() if key != "ledger_evidence_sha256"
    })
    return sources


def _read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reader: str,
    sources: dict[str, Report],
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
    assert result["operational_authority"] is False
    assert result["new_cohort_certified"] is False


@pytest.mark.parametrize("change", ["missing_identity", "missing_model", "wrong_model", "wrong_v3_epoch"])
def test_b14_rejects_noncanonical_v3_identity_even_with_known_physical_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    sources = _b14_sources()
    branch09 = sources["branch09_report"]
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
    ledger = sources["branch13_report"]
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
    ledger = sources["branch13_report"]
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
    evidence = _historical(council_fixtures._evidence([
        council_fixtures._row(index, period_id=f"period-{index}", pnl=2.0, action="ALLOW")
        for index in range(1, 5)
    ]))
    evidence["source_scorecard_evidence_sha256"] = scorecard["scorecard_evidence_sha256"]
    return {"branch14_report": scorecard, "council_ab_evidence": evidence}


@pytest.mark.parametrize("reader", ["B15", "B16"])
@pytest.mark.parametrize("with_source", [False, True])
def test_downstream_readers_accept_scorecards_with_the_official_semantic_seal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reader: str, with_source: bool,
) -> None:
    result = _read(tmp_path, monkeypatch, reader, _downstream_sources(reader, with_source=with_source))
    assert result["status"] == "ok"
    assert result["operational_authority"] is False


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
