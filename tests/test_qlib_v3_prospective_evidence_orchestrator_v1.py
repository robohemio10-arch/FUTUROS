from __future__ import annotations

import ast
import json
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from smartcrypto.execution.decision_ledger_v4_2 import seal_decision_record, seal_trade_link_record
from smartcrypto.learning.qlib_v3_prospective import admission, store
from smartcrypto.learning.qlib_v3_prospective.activation import load_activation
from smartcrypto.learning.qlib_v3_prospective.contracts import (
    CANONICAL, SAFETY, EvidenceError, digest, utc,
)
from smartcrypto.learning.qlib_v3_prospective.orchestrator import run_cycle

BOUNDARY = utc(CANONICAL.prospective_start_utc)
NOW = BOUNDARY + timedelta(hours=1)


def put(path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.fixture
def fixture(tmp_path):
    contract = {k: v for k, v in CANONICAL.mapping().items() if k in (
        "git_commit_sha", "git_tree_sha", "model_artifact_sha256",
        "model_semantic_fingerprint", "dataset_fingerprint")}
    contract["training_cutoff_utc"] = (BOUNDARY-timedelta(hours=1)).isoformat()
    freeze = {**SAFETY, "epoch_id": CANONICAL.epoch_id, "contract": contract,
              "epoch_version": "v3", "schema_version": "qlib_v3_complete_freeze_manifest_v2"}
    freeze_sha = digest(freeze)
    freeze["freeze_v3_sha256"] = freeze_sha
    activation = {**SAFETY, "schema_version": "qlib_v3_prospective_activation_v1",
        "epoch_id": CANONICAL.epoch_id, "freeze_v3_sha256": freeze_sha, "epoch_version": "v3",
        "activated_at_utc": (BOUNDARY-timedelta(seconds=60)).isoformat(),
        "prospective_start_utc": BOUNDARY.isoformat(), "activation_delay_seconds": 60,
        "backfill_allowed": False}
    activation["activation_sha256"] = digest(activation)
    identity = replace(CANONICAL, freeze_v3_sha256=freeze_sha,
                       activation_sha256=activation["activation_sha256"])
    return {"project_root": tmp_path, "activation_path": put(tmp_path/"activation.json", activation),
            "freeze_path": put(tmp_path/"freeze.json", freeze), "expected": identity, "now": NOW}


def signal(identity, instant=BOUNDARY, name="s1"):
    body = {"event_id": "d"+name, "signal_id": name, "candidate_id": "c"+name,
        "correlation_id": "r"+name, "idempotency_key": "key"+name,
        "pair": "BTC/USDT:USDT", "symbol": "BTCUSDT", "side": "long",
        "feature_timestamp": instant-timedelta(minutes=5), "decision_timestamp": instant,
        "feature_contract_version": "v1", "feature_hash": "a"*64,
        "model_id": "qlib", "model_version": "v3", "model_hash": identity.model_artifact_sha256,
        "qlib_score": 0.1, "calibrated_probability": None, "expected_net_pnl": None,
        "fast_stop_probability": None, "regime": "range", "alignment": "aligned",
        "ai_shadow_decision": "NOT_EVALUATED", "ai_shadow_reasons": [],
        "risk_decision": "APPROVED", "risk_reasons": [], "approved_stake_usdt": 10,
        "approved_leverage": 1, "final_decision": "ALLOW", "final_reasons": ["paper"]}
    return {"epoch_version": "v3", "identity": identity.mapping(),
            "origin": "natural_paper_runtime", "replayed": False, "backfilled": False,
            "synthetic": False, "signal_id": name, "decision_event_id": "d"+name,
            "signal_timestamp_utc": instant.isoformat(),
            "decision": seal_decision_record(body).model_dump(mode="json")}


def outcome(parent):
    d = parent["decision"]
    opened = utc(parent["signal_timestamp_utc"])+timedelta(seconds=1)
    link = seal_trade_link_record({"event_id": "link1", "parent_event_id": d["event_id"],
        "signal_id": d["signal_id"], "candidate_id": d["candidate_id"], "trade_id": 1,
        "correlation_id": d["correlation_id"], "idempotency_key": "linkkey1",
        "pair": d["pair"], "symbol": d["symbol"], "side": d["side"],
        "decision_timestamp": utc(d["decision_timestamp"]), "execution_timestamp": opened,
        "decision_payload_sha256": d["payload_sha256"], "link_reason": "exact_paper_link"})
    return {"epoch_version": "v3", "identity": parent["identity"],
            "origin": "natural_paper_runtime", "replayed": False, "backfilled": False,
            "synthetic": False, "signal_id": d["signal_id"], "decision_event_id": d["event_id"],
            "trade_id": 1, "trade_link": link.model_dump(mode="json"),
            "open_time_utc": opened.isoformat(), "close_time_utc": (opened+timedelta(minutes=2)).isoformat(),
            "is_closed": True, "net_pnl": 1.25}


def activation(fixture):
    return load_activation(fixture["activation_path"], fixture["freeze_path"], fixture["expected"])


def test_valid_activation(fixture):
    assert activation(fixture).boundary == BOUNDARY


@pytest.mark.parametrize("field", ["epoch_id", "freeze_v3_sha256", "activation_sha256",
    "prospective_start_utc", "historical_backfill_allowed", "backfill_allowed"])
def test_activation_tampering_blocks(fixture, field):
    path = fixture["activation_path"]
    raw = json.loads(path.read_text())
    raw[field] = "wrong"
    put(path, raw)
    assert run_cycle(**fixture)["status"] == "blocked"


@pytest.mark.parametrize("delta,accepted", [(-1, False), (0, True), (1, True)])
def test_microsecond_boundary(fixture, delta, accepted):
    row = signal(fixture["expected"], BOUNDARY+timedelta(microseconds=delta))
    if accepted:
        assert admission.signal(row, activation(fixture), NOW)["signal_id"] == "s1"
    else:
        with pytest.raises(EvidenceError):
            admission.signal(row, activation(fixture), NOW)


@pytest.mark.parametrize("field", list(CANONICAL.mapping()))
def test_all_v3_identity_fields_required(fixture, field):
    row = signal(fixture["expected"])
    row["identity"][field] = "wrong"
    with pytest.raises(EvidenceError):
        admission.signal(row, activation(fixture), NOW)


def test_pre_boundary_signal_with_late_close_rejected(fixture, tmp_path):
    row = signal(fixture["expected"], BOUNDARY-timedelta(microseconds=1))
    report = run_cycle(**fixture, signals_path=put(tmp_path/"signals.json", [row]),
                       outcomes_path=put(tmp_path/"outcomes.json", [outcome(row)]))
    assert report["eligible_signal_count"] == report["eligible_outcome_count"] == 0
    assert len(report["rejections"]) == 2


def test_post_boundary_natural_outcome(fixture, tmp_path):
    row = signal(fixture["expected"])
    report = run_cycle(**fixture, signals_path=put(tmp_path/"signals.json", [row]),
                       outcomes_path=put(tmp_path/"outcomes.json", [outcome(row)]))
    assert report["eligible_signal_count"] == report["eligible_outcome_count"] == 1
    assert report["write_performed"] is False


def test_outcome_without_lineage_is_not_matched(fixture, tmp_path):
    row = signal(fixture["expected"])
    report = run_cycle(**fixture, outcomes_path=put(tmp_path/"outcomes.json", [outcome(row)]))
    assert report["eligible_outcome_count"] == 0
    assert report["rejections"][0]["reason"] == "outcome_without_eligible_v3_signal"
    assert report["nearest_timestamp_matching_allowed"] is False
    assert report["fuzzy_identity_matching_allowed"] is False


@pytest.mark.parametrize("field,value", [("epoch_version", "v2"), ("replayed", True),
    ("backfilled", True), ("synthetic", True), ("origin", "historical")])
def test_v2_and_replay_forbidden(fixture, field, value):
    row = signal(fixture["expected"])
    row[field] = value
    with pytest.raises(EvidenceError):
        admission.signal(row, activation(fixture), NOW)


def test_default_no_write_and_zero_counters(fixture, tmp_path):
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    report = run_cycle(**fixture)
    assert report["status"] == "waiting"
    assert report["decision"] == "AWAITING_NATURAL_V3_EVIDENCE"
    assert report["signal_count"] == report["outcome_count"] == 0
    assert report["prospective_counter_base_signal"] == report["prospective_counter_base_outcome"] == 0
    assert report["write_performed"] is False
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    for flag, expected in SAFETY.items():
        assert report[flag] is expected


def test_atomic_store_idempotency_and_v2_untouched(fixture, tmp_path):
    v2 = tmp_path/"old_v2"
    v2.mkdir()
    legacy = put(v2/"ledger.json", {"signal_count": 950})
    original = legacy.read_bytes()
    row = signal(fixture["expected"])
    paths = {"signals_path": put(tmp_path/"signals.json", [row]),
             "outcomes_path": put(tmp_path/"outcomes.json", [outcome(row)])}
    first = run_cycle(**fixture, **paths, write=True)
    assert first["status"] == "ok", first
    path = store.location(tmp_path, fixture["expected"])
    before = path.read_bytes()
    second = run_cycle(**fixture, **paths, write=True)
    assert second["status"] == "ok", second
    assert second["signal_count"] == second["outcome_count"] == 1
    assert second["new_signal_count"] == second["new_outcome_count"] == 0
    assert second["write_performed"] is False
    assert path.read_bytes() == before
    assert legacy.read_bytes() == original


def test_old_worktree_not_modified_or_imported(fixture, tmp_path):
    old = tmp_path/"FUTUROS_BR02_PROSPECTIVE"
    old.mkdir()
    code = put(old/"historical.json", {"epoch_version": "v2"})
    before = code.read_bytes()
    run_cycle(**fixture)
    assert code.read_bytes() == before
    import smartcrypto.learning.qlib_v3_prospective as module
    for path in Path(module.__file__).parent.glob("*.py"):
        tree = ast.parse(path.read_text())
        imports = [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        assert not any("qlib_v2" in name or "continuous_orchestrator" in name for name in imports)


@pytest.mark.parametrize("text", ["2026-09-12T13:17:28.798985Z", "2026-09-12T10:17:28.798985-03:00"])
def test_timezone_independence(text):
    assert utc(text) == BOUNDARY


@pytest.mark.parametrize("text", ["12/09/2026 13:17:28", "2026-09-12T13:17:28",
                                  "2026-09-12T13:17:28.7989851Z"])
def test_no_regional_naive_or_truncated_time(text):
    with pytest.raises(EvidenceError):
        utc(text)


def test_identity_conflict_never_overwrites(fixture, tmp_path):
    one = signal(fixture["expected"])
    two = signal(fixture["expected"], BOUNDARY+timedelta(seconds=1))
    report = run_cycle(**fixture, signals_path=put(tmp_path/"signals.json", [one, two]), write=True)
    assert report["status"] == "blocked"
    assert report["reason"] == "causal_identity_content_conflict"
    assert not store.location(tmp_path, fixture["expected"]).exists()


def test_store_lock_contention_blocks(fixture, tmp_path):
    path = store.location(tmp_path, fixture["expected"])
    with store.exclusive(path):
        result = run_cycle(**fixture, write=True)
    assert result["status"] == "blocked"
    assert "store_busy" in result["reason"]


def test_missing_activation_blocks(fixture):
    fixture["activation_path"].unlink()
    assert run_cycle(**fixture)["status"] == "blocked"


def test_bad_seal_not_accepted(fixture, tmp_path):
    row = signal(fixture["expected"])
    row["decision"]["model_hash"] = "f"*64
    report = run_cycle(**fixture, signals_path=put(tmp_path/"signals.json", [row]))
    assert report["eligible_signal_count"] == 0


def test_decision_before_boundary_blocks_even_when_signal_is_later(fixture):
    row = signal(fixture["expected"], BOUNDARY-timedelta(microseconds=1))
    row["signal_timestamp_utc"] = (BOUNDARY+timedelta(seconds=1)).isoformat()
    with pytest.raises(EvidenceError):
        admission.signal(row, activation(fixture), NOW)


def test_store_path_traversal_blocked(tmp_path):
    with pytest.raises(EvidenceError, match="unsafe_store_identity"):
        store.location(tmp_path, replace(CANONICAL, epoch_id="../../qlib_v2"))


def test_symlink_source_blocked(fixture, tmp_path):
    target = put(tmp_path / "rows.json", [])
    link = tmp_path / "link.json"
    link.symlink_to(target)
    report = run_cycle(**fixture, signals_path=link)
    assert report["status"] == "blocked"
    assert report["reason"] == "symlink_or_junction_forbidden"


def test_corrupt_existing_store_blocks(fixture, tmp_path):
    path = store.location(tmp_path, fixture["expected"])
    path.parent.mkdir(parents=True)
    put(path, {"schema_version": store.SCHEMA, "identity": fixture["expected"].mapping(),
               "signals": [], "outcomes": [], "store_sha256": "0" * 64})
    before = path.read_bytes()
    assert run_cycle(**fixture)["status"] == "blocked"
    assert path.read_bytes() == before


def test_current_legacy_parquet_is_not_converted_to_v3(fixture, tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq

    path = tmp_path / "outcomes.parquet"
    pq.write_table(pa.table({"trade_id": [111], "net_pnl": [1.0]}), path)
    before = path.read_bytes()
    report = run_cycle(**fixture, outcomes_path=path)
    assert report["status"] == "waiting"
    assert report["eligible_outcome_count"] == 0
    assert report["rejections"][0]["reason"] == "not_natural_v3_evidence"
    assert path.read_bytes() == before


def test_duplicate_json_keys_fail_closed(fixture, tmp_path):
    path = tmp_path / "bad.json"
    path.write_text('{"signals":[],"signals":[]}', encoding="utf-8")
    assert run_cycle(**fixture, signals_path=path)["reason"] == "duplicate_json_key"


def test_future_signal_rejected(fixture):
    with pytest.raises(EvidenceError):
        admission.signal(signal(fixture["expected"], NOW+timedelta(microseconds=1)),
                         activation(fixture), NOW)
