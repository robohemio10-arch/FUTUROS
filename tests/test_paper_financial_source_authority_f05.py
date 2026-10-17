from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

import smartcrypto.data.trader_master_fingerprint_v2.authoritative_sqlite as reader
import smartcrypto.data.trader_master_fingerprint_v2.source_authority_preflight as f05
from tests.test_paper_financial_population_evidence_f04 import artifacts, fixture_source, reseal, sha


NOW = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
SCRIPT = Path(__file__).resolve().parents[1] / "scripts/audit_paper_financial_source_authority_f05.py"


@dataclass
class Fixture:
    root: Path
    source: tuple[Path, Path, Path, dict[str, Any]]
    origin: dict[str, Any]
    config: dict[str, Any]
    receipt: dict[str, Any]

    def path(self, name: str) -> Path:
        return self.root / f"{name}.json"

    def seal(self, monkeypatch: pytest.MonkeyPatch, *, trusted: bool = True) -> None:
        for name in ("config", "receipt"):
            self.path(name).write_text(json.dumps(getattr(self, name), sort_keys=True), encoding="utf-8")
        self.origin.update(
            effective_config_sha256=sha(self.path("config")),
            export_receipt_sha256=sha(self.path("receipt")),
            source_profile_sha256=sha(self.source[0]),
            f04_authorization_sha256=sha(self.source[2]),
        )
        self.path("origin").write_text(json.dumps(self.origin, sort_keys=True), encoding="utf-8")
        reviews = (f05.InstitutionalReview(
            origin_sha256=sha(self.path("origin")), issuer_id="independent-observer",
            producer_id="fixture-freqtrade", reviewer_id="institutional-reviewer",
            review_reference="fixture-reviewed-custody-001", valid_until_utc="2026-10-17T13:00:00Z",
        ),) if trusted else ()
        monkeypatch.setattr(f05, "INSTITUTIONAL_REVIEWS", reviews)

    def audit(self, **overrides: Any) -> dict[str, Any]:
        kwargs = dict(
            project_root=self.root, sqlite_path=self.source[1], source_profile_path=self.source[0],
            authorization_path=self.source[2], origin_path=self.path("origin"),
            effective_config_path=self.path("config"), export_receipt_path=self.path("receipt"),
        )
        kwargs.update(overrides)
        return f05.audit_paper_financial_source_authority(**kwargs)


def fixture_chain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, opened: bool = False) -> Fixture:
    source = fixture_source(tmp_path, opened=opened)
    scope = {key: source[3][key] for key in f05.f04.SCOPE_FIELDS}
    config = {"dry_run": True, "db_url": "sqlite:////paper-db/fixture-paper.sqlite"}
    receipt = {
        "schema_version": f05.EXPORT_SCHEMA, "producer_id": "fixture-freqtrade",
        "runtime_instance_id": "fixture-instance-001", "source_db_url": config["db_url"],
        "captured_at_utc": "2026-10-17T11:58:00Z", "export_method": "SQLITE_BACKUP",
        "source_query_only": True, "consistent_export": True, "scope": scope.copy(),
        "snapshot_path": str(source[1]),
        "source_artifact_sha256_before": artifacts(source[1]),
        "source_artifact_sha256_after": artifacts(source[1]),
        "snapshot_artifact_sha256": artifacts(source[1]),
        "closed_trade_ids": source[3]["closed_trade_ids"].copy(),
        "population_total_trade_count": 3 if opened else 2,
        "population_open_trade_count": 1 if opened else 0,
    }
    origin = {
        "schema_version": f05.ORIGIN_SCHEMA, "issuer_id": "independent-observer",
        "requester_id": "fixture-requester", "producer_id": "fixture-freqtrade",
        "runtime_instance_id": "fixture-instance-001", "runtime_image_sha256": "1" * 64,
        "observed_at_utc": "2026-10-17T11:59:00Z", "expires_at_utc": "2026-10-17T12:30:00Z",
        "config_resolution_complete": True, "scope": scope.copy(),
    }
    result = Fixture(tmp_path, source, origin, config, receipt)
    monkeypatch.setattr(f05, "_utc_now", lambda: NOW)
    result.seal(monkeypatch)
    return result


def bytes_in(root: Path) -> dict[str, bytes]:
    return {str(path): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def blocked(result: dict[str, Any], reason: str | None = None) -> None:
    assert result["status"] == "BLOCKED"
    assert result["preflight_validated"] is False
    assert result["execution_authorized"] is False
    assert result["f04_executed"] is False
    assert result["pnl_certified"] is False
    assert result["runtime_parity_certified"] is False
    assert result["write_performed"] is False
    assert result["blockers"] == [result["reason"]]
    if reason:
        assert result["reason"] == reason


@pytest.mark.parametrize("opened", [False, True])
def test_reviewed_chain_validates_preflight_only_and_is_immutable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, opened: bool,
) -> None:
    fixture = fixture_chain(tmp_path, monkeypatch, opened=opened)

    def forbidden(**_: object) -> None:
        raise AssertionError("Preflight must never execute F04")

    monkeypatch.setattr(f05.f04, "audit_paper_financial_population", forbidden)
    before = bytes_in(tmp_path)
    result = fixture.audit()
    assert result == fixture.audit()
    assert result["status"] == "VALIDATED", result
    assert result["preflight_validated"] is True
    assert result["execution_authorized"] is False
    assert "read_authorized" not in result
    assert result["f04_executed"] is False
    assert result["economic_readiness"] == "NOT_ASSESSED"
    assert result["runtime_parity_certified"] is False
    assert result["pnl_certified"] is False
    assert result["write_performed"] is False
    assert result["evidence"]["snapshot_query_only"] is True
    assert result["evidence"]["closed_trade_ids"] == [676, 677]
    assert bytes_in(tmp_path) == before
    digest = result.pop("evidence_sha256")
    assert hashlib.sha256(json.dumps(result, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest() == digest


def test_self_declared_hashes_never_establish_trust_or_open_financial_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = fixture_chain(tmp_path, monkeypatch)
    fixture.seal(monkeypatch, trusted=False)

    def forbidden(*_: object, **__: object) -> None:
        raise AssertionError("Untrusted origin must block before source access")

    monkeypatch.setattr(f05, "snapshot_artifact_hashes", forbidden)
    monkeypatch.setattr(f05, "read_authoritative_closed_trades", forbidden)
    monkeypatch.setattr(f05, "load_source_profile", forbidden)
    blocked(fixture.audit(), "INDEPENDENT_INSTITUTIONAL_REVIEW_MISSING_OR_AMBIGUOUS")


def test_treatment_uses_one_exact_epoch_without_aggregating_control(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = fixture_chain(tmp_path, monkeypatch)
    for scope in (fixture.origin["scope"], fixture.receipt["scope"], fixture.source[3]):
        scope.update(paper_scope="TREATMENT_B", epoch_id="fixture-epoch-2")
    reseal(fixture.source)
    fixture.seal(monkeypatch)
    result = fixture.audit()
    assert result["status"] == "VALIDATED"
    assert result["evidence"]["scope"]["paper_scope"] == "TREATMENT_B"
    assert result["evidence"]["scope"]["epoch_id"] == "fixture-epoch-2"
    assert result["evidence"]["expires_at_utc"] == "2026-10-17T12:30:00Z"
    assert result["execution_authorized"] is False


def test_f04_read_permission_is_required_but_never_granted_by_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = fixture_chain(tmp_path, monkeypatch)
    fixture.source[3]["read_authorized"] = False
    reseal(fixture.source)
    fixture.seal(monkeypatch)

    def forbidden(**_: object) -> None:
        raise AssertionError("Preflight must not repair or replace a denied F04 authorization")

    monkeypatch.setattr(f05, "read_authoritative_closed_trades", forbidden)
    before = bytes_in(tmp_path)
    blocked(fixture.audit(), "SOURCE_READ_NOT_AUTHORIZED")
    assert bytes_in(tmp_path) == before


@pytest.mark.parametrize(("field", "value", "reason"), [
    ("issuer_id", "fixture-requester", "INDEPENDENT_ORIGIN_IDENTITIES_UNPROVEN"),
    ("requester_id", "independent-observer", "INDEPENDENT_ORIGIN_IDENTITIES_UNPROVEN"),
    ("producer_id", "another-producer", "INDEPENDENT_ORIGIN_IDENTITIES_UNPROVEN"),
    ("requester_id", "institutional-reviewer", "INDEPENDENT_ORIGIN_IDENTITIES_UNPROVEN"),
    ("runtime_instance_id", "", "PROVENANCE_IDENTITY_INVALID"),
    ("runtime_image_sha256", "invalid", "PROVENANCE_HASH_INVALID"),
    ("config_resolution_complete", False, "EFFECTIVE_CONFIG_RESOLUTION_UNPROVEN"),
    ("expires_at_utc", "2026-10-17T12:00:00Z", "ORIGIN_OR_REVIEW_STALE_OR_FUTURE"),
    ("observed_at_utc", "2026-10-17T12:01:00Z", "ORIGIN_OR_REVIEW_STALE_OR_FUTURE"),
    ("observed_at_utc", "2026-10-17T09:00:00Z", "ORIGIN_EVIDENCE_TOO_OLD"),
    ("observed_at_utc", "2026-10-17T11:59:00", "PROVENANCE_UTC_TIMESTAMP_INVALID"),
])
def test_review_cannot_override_invalid_origin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str, value: object, reason: str,
) -> None:
    fixture = fixture_chain(tmp_path, monkeypatch)
    fixture.origin[field] = value
    fixture.seal(monkeypatch)
    blocked(fixture.audit(), reason)


@pytest.mark.parametrize("location", ["origin", "receipt"])
@pytest.mark.parametrize("field", list(f05.f04.SCOPE_FIELDS))
def test_no_control_treatment_epoch_cohort_or_account_mixing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, location: str, field: str,
) -> None:
    fixture = fixture_chain(tmp_path, monkeypatch)
    getattr(fixture, location)["scope"][field] = "different-scope"
    fixture.seal(monkeypatch)
    blocked(fixture.audit(), "ORIGIN_F04_SCOPE_MISMATCH" if location == "origin" else "EXPORT_SCOPE_MISMATCH")


@pytest.mark.parametrize(("field", "value", "reason"), [
    ("producer_id", "other", "EXPORT_PRODUCER_IDENTITY_MISMATCH"),
    ("runtime_instance_id", "other", "EXPORT_PRODUCER_IDENTITY_MISMATCH"),
    ("source_db_url", "sqlite:////other/source.sqlite", "EFFECTIVE_PAPER_DB_URL_UNPROVEN"),
    ("source_query_only", False, "CONSISTENT_READ_ONLY_EXPORT_UNPROVEN"),
    ("consistent_export", False, "CONSISTENT_READ_ONLY_EXPORT_UNPROVEN"),
    ("export_method", "unverified-copy", "CONSISTENT_READ_ONLY_EXPORT_UNPROVEN"),
    ("snapshot_path", "relative.sqlite", "EXPORT_SNAPSHOT_IDENTITY_MISMATCH"),
    ("closed_trade_ids", [676], "EXPORT_F04_MEMBERSHIP_MISMATCH"),
    ("closed_trade_ids", [676, 677, 677], "EXPORT_F04_MEMBERSHIP_MISMATCH"),
    ("closed_trade_ids", [True, 677], "EXPORT_F04_MEMBERSHIP_MISMATCH"),
    ("population_total_trade_count", 3, "EXPORT_POPULATION_MISMATCH"),
    ("population_open_trade_count", True, "EXPORT_POPULATION_COUNTS_INVALID"),
    ("captured_at_utc", "2026-10-17T10:00:00Z", "SNAPSHOT_CUSTODY_STALE_OR_FUTURE"),
    ("captured_at_utc", "2026-10-17T12:00:01Z", "SNAPSHOT_CUSTODY_STALE_OR_FUTURE"),
])
def test_export_chain_contradictions_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str, value: object, reason: str,
) -> None:
    fixture = fixture_chain(tmp_path, monkeypatch)
    fixture.receipt[field] = value
    fixture.seal(monkeypatch)
    blocked(fixture.audit(), reason)


@pytest.mark.parametrize("role", ["db", "wal", "shm"])
@pytest.mark.parametrize("stage", ["source_artifact_sha256_after", "snapshot_artifact_sha256"])
def test_artifact_sets_must_match_across_custody_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, role: str, stage: str,
) -> None:
    fixture = fixture_chain(tmp_path, monkeypatch)
    fixture.receipt[stage][role] = "0" * 64
    fixture.seal(monkeypatch)
    blocked(fixture.audit(), "EXPORT_SOURCE_ARTIFACT_DRIFT" if stage.startswith("source_")
            else "EXPORT_F04_ARTIFACT_MISMATCH")


def test_receipt_must_describe_all_artifact_roles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = fixture_chain(tmp_path, monkeypatch)
    fixture.receipt["source_artifact_sha256_before"].pop("shm")
    fixture.seal(monkeypatch)
    blocked(fixture.audit(), "PROVENANCE_ARTIFACT_SET_INCOMPLETE")


def test_snapshot_hash_is_checked_before_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = fixture_chain(tmp_path, monkeypatch)
    fixture.source[1].write_bytes(fixture.source[1].read_bytes() + b"\n")

    def forbidden(**_: object) -> None:
        raise AssertionError("Mismatched snapshot must not be queried")

    monkeypatch.setattr(f05, "read_authoritative_closed_trades", forbidden)
    blocked(fixture.audit(), "SNAPSHOT_ARTIFACT_CHAIN_MISMATCH")


def test_hardlink_document_alias_is_blocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = fixture_chain(tmp_path, monkeypatch)
    alias = tmp_path / "config-alias.json"
    alias.hardlink_to(fixture.source[0])
    blocked(fixture.audit(effective_config_path=alias), "PROVENANCE_INPUT_IDENTITIES_OVERLAP")


@pytest.mark.parametrize("name", ["origin", "db", "wal"])
def test_linked_inputs_are_rejected_before_source_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str,
) -> None:
    fixture = fixture_chain(tmp_path, monkeypatch)
    linked = {"origin": fixture.path("origin"), "db": fixture.source[1],
              "wal": Path(f"{fixture.source[1]}-wal")}[name]
    original = Path.is_symlink

    def is_link(path: Path) -> bool:
        return path == linked or original(path)

    monkeypatch.setattr(Path, "is_symlink", is_link)
    blocked(fixture.audit(), "SOURCE_LINK_FORBIDDEN")


@pytest.mark.parametrize("defect", ["missing", "relative", "memory", "network", "live", "future_override"])
def test_effective_config_requires_complete_paper_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, defect: str,
) -> None:
    fixture = fixture_chain(tmp_path, monkeypatch)
    if defect == "missing":
        fixture.config.pop("db_url")
    elif defect == "live":
        fixture.config["dry_run"] = False
    elif defect == "future_override":
        fixture.origin["config_resolution_complete"] = False
    else:
        fixture.config["db_url"] = {
            "relative": "sqlite:///relative.sqlite", "memory": "sqlite:///:memory:",
            "network": "postgresql://user:private-fixture-password@host/db",
        }[defect]
        fixture.receipt["source_db_url"] = fixture.config["db_url"]
    fixture.seal(monkeypatch)
    result = fixture.audit()
    blocked(result)
    assert "private-fixture-password" not in json.dumps(result)


@pytest.mark.parametrize("name", ["origin", "config", "receipt", "profile", "authorization"])
def test_missing_or_tampered_evidence_blocks_without_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str,
) -> None:
    fixture = fixture_chain(tmp_path, monkeypatch)
    path = {"profile": fixture.source[0], "authorization": fixture.source[2]}.get(name, fixture.path(name))
    path.write_bytes(path.read_bytes() + b"\n")
    blocked(fixture.audit())
    path.unlink()
    blocked(fixture.audit())


@pytest.mark.parametrize("defect", ["membership", "mixed_epoch", "duplicate_order", "orphan", "missing_orders", "corrupt"])
def test_review_does_not_mask_snapshot_membership_or_schema_defects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, defect: str,
) -> None:
    fixture = fixture_chain(tmp_path, monkeypatch)
    if defect == "corrupt":
        fixture.source[1].write_bytes(b"invalid fixture sqlite")
    else:
        with sqlite3.connect(fixture.source[1]) as connection:
            if defect == "membership":
                connection.execute("UPDATE trades SET id=999 WHERE id=676")
                connection.execute("UPDATE orders SET ft_trade_id=999 WHERE ft_trade_id=676")
            elif defect == "mixed_epoch":
                connection.execute("ALTER TABLE trades ADD COLUMN epoch_id TEXT")
                connection.execute("UPDATE trades SET epoch_id='mixed-epoch'")
            elif defect == "duplicate_order":
                connection.execute("INSERT INTO orders SELECT * FROM orders WHERE id=6760")
            elif defect == "orphan":
                connection.execute("UPDATE orders SET ft_trade_id=999 WHERE id=6760")
            else:
                connection.execute("DROP TABLE orders")
    reseal(fixture.source)
    fixture.receipt["snapshot_artifact_sha256"] = artifacts(fixture.source[1])
    fixture.seal(monkeypatch)
    blocked(fixture.audit())


@pytest.mark.parametrize(("case", "valid"), [
    ("mismatched_pair", False), ("missing_order_pair", False), ("blank_order_pair", False),
    ("missing_trade_pair", False), ("blank_trade_pair", False),
    ("same_external_different_pairs", True), ("same_external_same_pair", False),
    ("duplicate_local_different_pairs", False), ("absent_parent", False),
    ("missing_parent", False), ("missing_external", False), ("missing_local", False),
    ("missing_order_evidence", False),
])
def test_order_pair_and_external_namespace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str, valid: bool,
) -> None:
    fixture = fixture_chain(tmp_path, monkeypatch)
    with sqlite3.connect(fixture.source[1]) as connection:
        if case in ("same_external_different_pairs", "duplicate_local_different_pairs"):
            connection.execute("UPDATE trades SET pair='ETH/USDT:USDT' WHERE id=677")
            connection.execute("UPDATE orders SET ft_pair='ETH/USDT:USDT' WHERE ft_trade_id=677")
        statements = {
            "mismatched_pair": "UPDATE orders SET ft_pair='ETH/USDT:USDT' WHERE id=6760",
            "missing_order_pair": "UPDATE orders SET ft_pair=NULL WHERE id=6760",
            "blank_order_pair": "UPDATE orders SET ft_pair=' ' WHERE id=6760",
            "missing_trade_pair": "UPDATE trades SET pair=NULL WHERE id=676",
            "blank_trade_pair": "UPDATE trades SET pair=' ' WHERE id=676",
            "same_external_different_pairs": "UPDATE orders SET order_id='shared-external' WHERE id IN (6760,6770)",
            "same_external_same_pair": "UPDATE orders SET order_id='shared-external' WHERE id IN (6760,6770)",
            "duplicate_local_different_pairs": "UPDATE orders SET id=6760 WHERE id=6770",
            "absent_parent": "UPDATE orders SET ft_trade_id=999 WHERE id=6760",
            "missing_parent": "UPDATE orders SET ft_trade_id=NULL WHERE id=6760",
            "missing_external": "UPDATE orders SET order_id=NULL WHERE id=6760",
            "missing_local": "UPDATE orders SET id=NULL WHERE id=6760",
            "missing_order_evidence": "DELETE FROM orders WHERE ft_trade_id=676",
        }
        connection.execute(statements[case])
    reseal(fixture.source)
    fixture.receipt["snapshot_artifact_sha256"] = artifacts(fixture.source[1])
    fixture.seal(monkeypatch)
    before = bytes_in(tmp_path)
    result = fixture.audit()
    if valid:
        assert result["status"] == "VALIDATED", result
        assert result["preflight_validated"] is True
    else:
        blocked(result)
    assert result["execution_authorized"] is False
    assert result["f04_executed"] is False
    assert bytes_in(tmp_path) == before


@pytest.mark.parametrize("duplicate_pair", ["BTC/USDT:USDT", "ETH/USDT:USDT"])
def test_ambiguous_trade_identity_cannot_be_overwritten(duplicate_pair: str) -> None:
    trades = [{"id": 676, "pair": "BTC/USDT:USDT"}, {"id": 676, "pair": duplicate_pair}]
    order = {"id": 6760, "ft_trade_id": 676, "ft_pair": duplicate_pair, "order_id": "fixture-order"}
    with pytest.raises(f05.f04.EvidenceError, match="^SOURCE_TRADE_IDENTITIES_AMBIGUOUS$"):
        f05._order_identities([order], trades)


@pytest.mark.parametrize("name", ["origin", "config", "receipt", "profile", "authorization", "db"])
def test_drift_during_read_blocks_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str,
) -> None:
    fixture = fixture_chain(tmp_path, monkeypatch)
    original = f05.read_authoritative_closed_trades

    def changed(**kwargs: Any) -> Any:
        result = original(**kwargs)
        path = {"profile": fixture.source[0], "authorization": fixture.source[2],
                "db": fixture.source[1]}.get(name, fixture.path(name))
        path.write_bytes(path.read_bytes() + b"\n")
        return result

    monkeypatch.setattr(f05, "read_authoritative_closed_trades", changed)
    blocked(fixture.audit())


def test_expiration_during_read_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = fixture_chain(tmp_path, monkeypatch)
    original = f05.read_authoritative_closed_trades

    def expired(**kwargs: Any) -> Any:
        result = original(**kwargs)
        monkeypatch.setattr(f05, "_utc_now", lambda: datetime(2026, 10, 17, 13, 0, tzinfo=timezone.utc))
        return result

    monkeypatch.setattr(f05, "read_authoritative_closed_trades", expired)
    blocked(fixture.audit(), "ORIGIN_OR_REVIEW_STALE_OR_FUTURE")


def test_live_wal_shm_fixture_is_immutable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = fixture_chain(tmp_path, monkeypatch)
    connection = sqlite3.connect(fixture.source[1])
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA wal_autocheckpoint=0")
        connection.execute("UPDATE trades SET close_profit_abs=7 WHERE id=676")
        connection.commit()
        reseal(fixture.source)
        fixture.receipt["snapshot_artifact_sha256"] = artifacts(fixture.source[1])
        fixture.seal(monkeypatch)
        before = bytes_in(tmp_path)
        assert fixture.receipt["snapshot_artifact_sha256"]["wal"] is not None
        assert fixture.audit()["status"] == "VALIDATED"
        assert bytes_in(tmp_path) == before
    finally:
        connection.close()


def test_torn_temporary_copy_blocks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = fixture_chain(tmp_path, monkeypatch)
    original = reader._copy_snapshot_artifacts

    def changed(source: Path, temporary: Path) -> Path:
        copied = original(source, temporary)
        copied.write_bytes(copied.read_bytes() + b"\n")
        return copied

    monkeypatch.setattr(reader, "_copy_snapshot_artifacts", changed)
    before = bytes_in(tmp_path)
    blocked(fixture.audit(), "SOURCE_QUERY_ONLY_EVIDENCE_UNAVAILABLE")
    assert bytes_in(tmp_path) == before


def test_io_failure_is_explicit_and_sanitized(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = fixture_chain(tmp_path, monkeypatch)

    def denied(**_: object) -> None:
        raise PermissionError("private-fixture-message")

    monkeypatch.setattr(f05, "read_authoritative_closed_trades", denied)
    result = fixture.audit()
    blocked(result, "PROVENANCE_INVALID_OR_UNREADABLE")
    assert result["error_type"] == "PermissionError"
    assert "private-fixture-message" not in json.dumps(result)


@pytest.mark.parametrize("defect", ["duplicate_key", "oversized", "unknown_field", "duplicate_review"])
def test_malformed_or_ambiguous_trust_input_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, defect: str,
) -> None:
    fixture = fixture_chain(tmp_path, monkeypatch)
    if defect == "duplicate_key":
        raw = fixture.path("origin").read_text(encoding="utf-8")
        fixture.path("origin").write_text(raw[:-1] + ',"issuer_id":"duplicate"}', encoding="utf-8")
    elif defect == "oversized":
        fixture.path("origin").write_bytes(b" " * 1_048_577)
    elif defect == "unknown_field":
        fixture.origin["trust_me"] = True
        fixture.seal(monkeypatch)
    else:
        monkeypatch.setattr(f05, "INSTITUTIONAL_REVIEWS", f05.INSTITUTIONAL_REVIEWS * 2)
    blocked(fixture.audit())


def test_cli_never_imports_fixture_trust_or_authorizes_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = fixture_chain(tmp_path, monkeypatch)
    before = bytes_in(tmp_path)
    command = [sys.executable, "-B", str(SCRIPT), "--project-root", str(tmp_path),
               "--sqlite-path", str(fixture.source[1]), "--source-profile", str(fixture.source[0]),
               "--source-authorization", str(fixture.source[2]), "--origin-evidence", str(fixture.path("origin")),
               "--effective-config", str(fixture.path("config")), "--export-receipt", str(fixture.path("receipt"))]
    completed = subprocess.run(command, capture_output=True, text=True, check=False, timeout=30)
    assert completed.returncode == 2
    assert completed.stderr == ""
    blocked(json.loads(completed.stdout), "INDEPENDENT_INSTITUTIONAL_REVIEW_MISSING_OR_AMBIGUOUS")
    assert bytes_in(tmp_path) == before
    help_result = subprocess.run([sys.executable, "-B", str(SCRIPT), "--help"], capture_output=True,
                                 text=True, check=False, timeout=30)
    assert help_result.returncode == 0
    assert "--trust" not in help_result.stdout
    assert "--write" not in help_result.stdout
    assert "--execute" not in help_result.stdout
    missing = subprocess.run([sys.executable, "-B", str(SCRIPT)], capture_output=True,
                             text=True, check=False, timeout=30)
    assert missing.returncode == 2
