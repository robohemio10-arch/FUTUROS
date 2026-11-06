from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest

from smartcrypto.learning.qlib_v3_prospective.contracts import CANONICAL, digest
from smartcrypto.research.aibot_parity import economic_phase_final_forward_proof as b17
from tests.v2_contract_fixtures import context_for_sources, validate_synthetic


@pytest.fixture
def v2_context(tmp_path, monkeypatch):
    state = {"schema_version": "qlib_v3_prospective_evidence_store_v1",
             "identity": CANONICAL.mapping(), "signals": [], "outcomes": []}
    state["store_sha256"] = digest(state)
    context = context_for_sources(tmp_path, {"qlib_v3_store": state})
    validate_synthetic(monkeypatch, context)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("V2 preactivation attempted runtime collection")

    for name in ("collect_runtime", "container_read", "container_json", "container_optional_json", "_git"):
        monkeypatch.setattr(b17.governance, name, forbidden)
    return context


def kwargs(context):
    return {
        "descriptor": context[0], "predecessor_registration": context[1],
        "predecessor_raw_sha256": context[2], "known_fingerprints": context[3],
    }


def test_v2_descriptor_recognized_without_activating_or_certifying(v2_context):
    result = b17.build_economic_phase_final_forward_proof_v2(**kwargs(v2_context))
    assert result["status"] == "blocked"
    assert result["decision"] == "BLOCKED_FORWARD_EVIDENCE"
    assert result["certification_attempted"] is False
    assert result["formal_activation_utc"] is None
    assert "v2_cohort_unregistered_no_t0" in result["blockers"]
    assert result["source_contract"]["descriptor_sha256"] == v2_context[0]["descriptor_sha256"]
    assert result["evidence_status"] == "NOT_COLLECTED"
    assert all(gate["value"] is None for gate in result["sample_gates"].values())
    assert all(value is None for value in result["causal_population"].values())


def test_existing_entrypoint_explicit_v2_never_falls_back(v2_context, tmp_path):
    result = b17.build_economic_phase_final_forward_proof_v1(
        project_root=tmp_path, runtime_root=tmp_path,
        **{"v2_" + key: value for key, value in kwargs(v2_context).items()},
    )
    assert result["evidence_mode"] == "explicit_v2_offline"
    assert result["certification_attempted"] is False
    assert "v2_cohort_unregistered_no_t0" in result["blockers"]


def test_incomplete_v2_context_is_blocked_not_legacy(v2_context, tmp_path):
    result = b17.build_economic_phase_final_forward_proof_v1(
        project_root=tmp_path, runtime_root=tmp_path, v2_descriptor=v2_context[0]
    )
    assert result["blockers"] == ["v2_explicit_context_incomplete"]
    assert result["causal_population"]["eligible_decisions"] is None


def test_unapproved_t0_and_epoch_cannot_enter_b17(v2_context):
    bad = copy.deepcopy(v2_context)
    bad[0]["proposal"]["successor"]["formal_activation_utc"] = "2020-01-01T00:00:00Z"
    bad[0]["proposal"]["successor"]["epoch_id"] = "paper-b-epoch-3"
    result = b17.build_economic_phase_final_forward_proof_v2(**kwargs(bad))
    assert result["status"] == "blocked" and result["certification_attempted"] is False
    assert result["formal_activation_utc"] is None


def test_invalid_as_of_is_structured_block(v2_context):
    result = b17.build_economic_phase_final_forward_proof_v2(
        **kwargs(v2_context), as_of_utc="not-a-time"
    )
    assert result["status"] == "blocked"
    assert result["evidence_status"] == "NOT_COLLECTED"


def test_financial_gates_and_legacy_parameters_are_preserved(v2_context):
    assert (b17.MIN_OBSERVATION_DAYS, b17.MIN_RESOLVED_DECISIONS, b17.MIN_SELECTED_TRADES) == (45, 200, 50)
    assert b17.MIN_SCORER_COVERAGE == 0.99
    assert b17.MIN_TREATMENT_PROFIT_FACTOR == 1.10
    assert b17.CANONICAL_ADDITIONAL_EXECUTION_STRESS_BPS == 5.0
    assert (b17.BOOTSTRAP_RESAMPLES, b17.BOOTSTRAP_SEED) == (5000, 42)
    result = b17.build_economic_phase_final_forward_proof_v2(**kwargs(v2_context))
    assert "economic_dod_no_unexplained_gap" in result["economic_gates"]
    assert all(gate["passed"] is False for gate in result["economic_gates"].values())


def test_v2_cli_incomplete_context_cannot_start_legacy_runtime(v2_context, capsys):
    path = Path(__file__).resolve().parents[1] / "scripts/build_economic_phase_final_forward_proof_v1.py"
    spec = importlib.util.spec_from_file_location("b17_v2_cli_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with pytest.raises(SystemExit) as exc:
        module.main(["--v2-descriptor", "missing.json", "--json"])
    assert exc.value.code == 2
    assert "V2 requires" in capsys.readouterr().err
