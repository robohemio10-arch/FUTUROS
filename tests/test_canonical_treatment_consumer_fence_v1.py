"""No exchange/network: pure contracts and isolated Linux UID/Unix-socket integration."""

from __future__ import annotations

import importlib.util
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import types
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

from scripts import canonical_treatment_report_healthcheck_v1 as healthcheck
from smartcrypto.research.canonical_treatment import publisher
from tests import test_canonical_treatment_publisher_permission_boundary_v1 as fixtures

ROOT = Path(__file__).resolve().parents[1]
PAIR = "BTC/USDT:USDT"
TAG = "smartcrypto_long|decision_event_id=decision-event:f02"


def _strategy_class() -> Any:
    names = (
        "freqtrade",
        "freqtrade.constants",
        "freqtrade.strategy",
        "SmartCryptoSignalStrategy",
        "SmartCryptoCanonicalTreatmentStrategy",
    )
    previous = {name: sys.modules.get(name) for name in names}
    previous_path = list(sys.path)
    try:
        source_root = os.environ.get("F02_FREQTRADE_SOURCE_ROOT")
        if source_root:
            sys.path.insert(0, source_root)
        try:
            from freqtrade.strategy import IStrategy  # noqa: F401
        except ImportError:
            if os.environ.get("F02_REQUIRE_REAL_FREQTRADE") == "1":
                raise

            class StrategyFixture:
                def __init__(self, config: dict[str, Any]) -> None:
                    self.config = config

            ft = types.ModuleType("freqtrade")
            constants = types.ModuleType("freqtrade.constants")
            strategy = types.ModuleType("freqtrade.strategy")
            constants.CANCEL_REASON = {"TIMEOUT": "cancelled due to timeout"}  # type: ignore[attr-defined]
            strategy.IStrategy = StrategyFixture  # type: ignore[attr-defined]
            sys.modules.update(
                {"freqtrade": ft, "freqtrade.constants": constants, "freqtrade.strategy": strategy}
            )
        for name in names[-2:]:
            spec = importlib.util.spec_from_file_location(
                name, ROOT / "freqtrade/user_data/strategies" / f"{name}.py"
            )
            if spec is None or spec.loader is None:
                raise RuntimeError("strategy_fixture_module_unavailable")
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
        return sys.modules[names[-1]].SmartCryptoCanonicalTreatmentStrategy
    finally:
        sys.path[:] = previous_path
        for name, value in previous.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value


def _make_strategy(root: Path) -> Any:
    strategy = _strategy_class()(config={"dry_run": True})
    readonly_root = os.environ.get("F02_TEST_READONLY_ROOT")
    source_root = Path(readonly_root) / root.name if readonly_root else root
    strategy._treatment_signal_path = source_root / "outputs" / "active.json"
    strategy._publisher_report_path = source_root / "reports" / "report.json"
    strategy._publisher_socket_path = source_root / "reports" / "authority.sock"
    strategy._decision_log_paths = [root / "decisions.jsonl"]
    strategy._exit_control_paths = [source_root / "outputs" / "paper_exit_control.json"]
    return strategy


def _confirm(strategy: Any, tag: str | None = TAG) -> bool:
    return strategy.confirm_trade_entry(
        PAIR, "limit", 1.0, 100.0, "GTC", datetime.now(UTC), tag, "long"
    )


def _request(paths: dict[str, Path]) -> dict[str, Any]:
    output = json.loads(paths["output_path"].read_text())
    report = json.loads(paths["report_path"].read_text())
    return {
        "schema_version": publisher.AUTHORIZATION_SCHEMA,
        "nonce": uuid.uuid4().hex,
        "publication_id": output["publication_id"],
        "payload_sha256": publisher._hash(output),
        "report_sha256": publisher._hash(report),
        "decision_event_id": "decision-event:f02",
        "decision_payload_sha256": "a" * 64,
        "signal_id": "signal:f02",
        "pair": PAIR,
        "side": "long",
    }


class ConsumerContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="f02-contract-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.paths = fixtures._fixture(self.root)
        self.authority = publisher.PublicationAuthority(self.root / "reports" / "authority.sock")
        self.assertEqual(
            publisher.build_treatment_artifact(**self.paths, authority=self.authority)["status"],
            "ok",
        )
        self.strategy = _make_strategy(self.root)

    def test_no_socket_cannot_authorize_from_fresh_json_alone(self) -> None:
        self.strategy._publication()
        self.assertFalse(_confirm(self.strategy))

    def test_stale_and_future_publications_are_rejected_even_with_consistent_hash(self) -> None:
        for offset in (-60, 60):
            payload = json.loads(self.paths["output_path"].read_text())
            report = json.loads(self.paths["report_path"].read_text())
            generated = (datetime.now(UTC) + timedelta(seconds=offset)).isoformat()
            payload["generated_at"] = generated
            report["generated_at_utc"] = generated
            report["published_payload_sha256"] = publisher._hash(payload)
            self.paths["output_path"].write_text(json.dumps(payload))
            self.paths["report_path"].write_text(json.dumps(report))
            with self.assertRaisesRegex(ValueError, "publication_stale_or_future"):
                self.strategy._publication()

    def test_policy_rejects_missing_null_invalid_or_expired_deadline(self) -> None:
        for value in (None, "invalid", "2026-01-01T00:00:00", "2000-01-01T00:00:00Z"):
            with self.subTest(value=value):
                control = json.loads(self.paths["control_signals_path"].read_text())
                control["signals"][0]["valid_until"] = value
                self.paths["control_signals_path"].write_text(json.dumps(control))
                publisher.build_treatment_artifact(**self.paths, authority=self.authority)
                self.assertFalse(self.authority.authorize(_request(self.paths))["allowed"])
                self.assertFalse(_confirm(self.strategy))

    def test_authority_rejects_identity_hash_nonce_and_generation_mismatch(self) -> None:
        request = _request(self.paths)
        self.assertTrue(self.authority.authorize(request)["allowed"])
        for changes in (
            {"decision_event_id": "decision-event:other"},
            {"signal_id": "signal:other"},
            {"payload_sha256": "0" * 64},
            {"report_sha256": "0" * 64},
            {"nonce": "invalid"},
            {"publication_id": "0" * 32},
            {"side": "short"},
        ):
            with self.subTest(changes=changes):
                self.assertFalse(self.authority.authorize(request | changes)["allowed"])

    def test_disk_change_or_source_failure_revokes_in_memory_approval(self) -> None:
        request = _request(self.paths)
        self.paths["v3_evidence_path"].write_text("{}")
        with self.assertLogs(publisher.__name__, level="ERROR"):
            self.assertFalse(self.authority.authorize(request)["allowed"])
        self.assertFalse(self.authority.authorize(request)["allowed"])

    def test_revocation_during_synchronous_read_is_not_reissued(self) -> None:
        original = publisher._read_json

        def revoke(path: Path, required: bool = True) -> dict[str, Any]:
            result = original(path, required)
            if path == self.paths["control_signals_path"]:
                self.authority.invalidate()
            return result

        request = _request(self.paths)
        with patch.object(publisher, "_read_json", side_effect=revoke):
            result = self.authority.authorize(request)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "authorization_revoked_during_validation")

    def test_fallback_list_cannot_supply_approval(self) -> None:
        self.strategy._signal_paths = [self.paths["control_signals_path"]]
        self.paths["output_path"].unlink()
        self.assertFalse(_confirm(self.strategy))

    def test_control_and_protective_callbacks_are_unchanged(self) -> None:
        base = type(self.strategy).__bases__[0]
        for name in (
            "confirm_trade_exit",
            "custom_exit",
            "populate_exit_trend",
            "_valid_until_active",
        ):
            self.assertIs(getattr(type(self.strategy), name), getattr(base, name))
        self.assertTrue(self.strategy._valid_until_active(None))
        self.assertFalse(
            self.strategy._is_signal_active({"risk_approved": True, "valid_until": None})
        )
        self.assertTrue(
            fixtures._consumer(self.paths["output_path"])._is_signal_active({"risk_approved": True})
        )

    def test_callback_catches_failures_instead_of_triggering_freqtrade_default_allow(self) -> None:
        with patch.object(self.strategy, "_publication", side_effect=PermissionError("sensitive")):
            self.assertFalse(_confirm(self.strategy))
        self.assertEqual(
            self.strategy._last_treatment_gate_reason, "consumer_boundary_failed:PermissionError"
        )
        with patch.object(self.strategy, "_decision", side_effect=RuntimeError("sensitive")):
            self.assertFalse(_confirm(self.strategy))
        self.assertEqual(
            self.strategy._last_treatment_gate_reason, "entry_confirmation_failed:RuntimeError"
        )

    def test_healthcheck_keeps_economic_monitor_schema_compatible(self) -> None:
        report = {
            "schema_version": "canonical_treatment_paper_economic_monitor_v1",
            "status": "ok",
            "generated_at_utc": datetime.now(UTC).isoformat(),
        }
        self.paths["report_path"].write_text(json.dumps(report))
        self.assertEqual(healthcheck.main(["--report", str(self.paths["report_path"])]), 0)

    def test_strategy_is_only_used_by_treatment_compose(self) -> None:
        compose = (ROOT / "docker-compose.canonical-treatment.yml").read_text()
        self.assertIn("--strategy SmartCryptoCanonicalTreatmentStrategy", compose)
        self.assertIn("/run/canonical-treatment-output:ro", compose)
        self.assertIn("/run/canonical-treatment-report:ro", compose)
        self.assertNotIn("group_add", compose)


@unittest.skipUnless(
    sys.platform == "linux" and os.geteuid() == 0,
    "fixture UID and ACL setup requires isolated Linux root",
)
class LinuxConsumerIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fs = fixtures.LinuxPermissionBoundaryTests(
            "test_uid10001_without_acl_fails_explicitly"
        )
        self.fs.setUp()
        self.addCleanup(self.fs.doCleanups)
        self.root = self.fs.root
        self.paths = self.fs.paths
        self.fs.grant_sources()
        self.fs.grant_outputs()
        self.fs.grant(self.root / "reports", "u::rwx,u:1000:--x,g::---,m::--x,o::---")
        self.fs.grant(self.root / "reports", "u::rwx,u:1000:rw-,g::---,m::rw-,o::---", default=True)
        log = self.root / "decisions.jsonl"
        log.touch()
        os.chown(log, 1000, 1000)
        log.chmod(0o600)
        script = (
            "import json,sys,threading; from pathlib import Path; "
            "from tests.test_canonical_treatment_publisher_permission_boundary_v1 import _paths; "
            "from smartcrypto.research.canonical_treatment.publisher import PublicationAuthority,build_treatment_artifact; "
            "root=Path(sys.argv[1]); paths=_paths(root); authority=PublicationAuthority(root/'reports'/'authority.sock'); "
            "authority.start(); release=threading.Event(); original_authorize=authority.authorize; "
            "print(json.dumps(build_treatment_artifact(**paths,authority=authority)),flush=True)\n"
            "def stalled(request):\n"
            " release.wait(timeout=5); return original_authorize(request)\n"
            "try:\n"
            " for line in sys.stdin:\n"
            "  command=line.strip()\n"
            "  if command=='stop': break\n"
            "  if command=='invalidate': authority.invalidate(); result={'status':'revoked'}\n"
            "  elif command=='publish': result=build_treatment_artifact(**paths,authority=authority)\n"
            "  elif command=='stall': authority.authorize=stalled; result={'status':'stall_armed'}\n"
            "  elif command=='release': release.set(); result={'status':'released'}\n"
            "  else: raise ValueError('unknown_fixture_command')\n"
            "  print(json.dumps(result),flush=True)\n"
            "finally:\n"
            " if not authority.stop(): raise RuntimeError('fixture_shutdown_incomplete')\n"
        )
        self.child = subprocess.Popen(
            [sys.executable, "-B", "-c", script, str(self.root)],
            cwd="/tmp",
            env={**os.environ, "PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1"},
            user=10001,
            group=10001,
            extra_groups=[],
            text=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.addCleanup(self.shutdown)
        self.responses: queue.Queue[str] = queue.Queue(maxsize=20)

        def read() -> None:
            if self.child.stdout is not None:
                for line in self.child.stdout:
                    self.responses.put(line, timeout=5)

        threading.Thread(target=read, daemon=True).start()
        self.assertEqual(self.receive()["status"], "ok")

    def shutdown(self) -> None:
        if getattr(self, "_closed", False):
            return
        if self.child.poll() is None:
            # Restore only this disposable fixture so its own socket can be cleaned up.
            self.fs.grant(self.root / "reports", "u::rwx,u:1000:--x,g::---,m::--x,o::---")
            if self.child.stdin is not None:
                self.child.stdin.write("stop\n")
                self.child.stdin.flush()
            try:
                self.child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.child.kill()
                self.child.wait(timeout=5)
                self.fail("isolated authority fixture shutdown incomplete")
        if self.child.stderr is not None:
            errors = self.child.stderr.read()
            self.child.stderr.close()
            self.assertEqual(self.child.returncode, 0, errors)
        if self.child.stdin is not None:
            self.child.stdin.close()
        if self.child.stdout is not None:
            self.child.stdout.close()
        self._closed = True

    def receive(self) -> dict[str, Any]:
        return json.loads(self.responses.get(timeout=10))

    def command(self, text: str) -> dict[str, Any]:
        channel = self.child.stdin
        if channel is None:
            raise AssertionError("fixture input channel missing")
        channel.write(text + "\n")
        channel.flush()
        return self.receive()

    def consume(
        self,
        code: str = "print(json.dumps({'accepted':_confirm(strategy),'reason':strategy._last_treatment_gate_reason}))",
    ) -> dict[str, Any]:
        child = self.fs.run_as(
            "from tests.test_canonical_treatment_consumer_fence_v1 import _make_strategy,_confirm; "
            "strategy=_make_strategy(root); " + code,
            uid=1000,
        )
        self.assertEqual(child.returncode, 0, child.stderr)
        return json.loads(child.stdout)

    def test_valid_bound_publication_authorizes_before_entry(self) -> None:
        self.assertTrue(self.consume()["accepted"])

    def test_original_negative_case_both_writes_denied_rejects_readable_old_json(self) -> None:
        self.assertTrue(self.consume()["accepted"])
        old_output = self.paths["output_path"].read_bytes()
        old_report = self.paths["report_path"].read_bytes()
        self.fs.grant(self.root / "outputs", "u::rwx,u:10001:r-x,g::rwx,m::rwx,o::---")
        self.fs.grant(self.root / "reports", "u::r-x,u:1000:--x,g::---,m::--x,o::---")
        result = self.command("publish")
        self.assertFalse(result["prior_output_invalidation_confirmed"])
        self.assertFalse(result["blocked_report_persisted"])
        self.assertEqual(self.paths["output_path"].read_bytes(), old_output)
        self.assertEqual(self.paths["report_path"].read_bytes(), old_report)
        result = self.consume(
            "strategy._publication(); assert strategy._read_first_active_signal_file()[0] is None; "
            "print(json.dumps({'accepted':_confirm(strategy),'reason':strategy._last_treatment_gate_reason}))"
        )
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"], "authority_denied_or_unbound")

    def test_source_permission_revocation_denies_before_next_publish_cycle(self) -> None:
        self.assertTrue(self.consume()["accepted"])
        (self.root / "sources").chmod(0o700)
        result = self.consume()
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"], "authority_denied_or_unbound")

    def test_populate_approval_does_not_bypass_later_confirmation(self) -> None:
        script = (
            "import sys,json,pandas as pd; from pathlib import Path; "
            "from tests.test_canonical_treatment_consumer_fence_v1 import _make_strategy,_confirm; "
            "strategy=_make_strategy(Path(sys.argv[1])); frame=pd.DataFrame({'close':[100.0]}); "
            "frame=strategy.populate_indicators(frame,{'pair':'BTC/USDT:USDT'}); "
            "assert frame.iloc[-1]['smartcrypto_signal_side']=='long'; "
            "print('populate_approved',flush=True); sys.stdin.readline(); "
            "print(json.dumps({'accepted':_confirm(strategy)}),flush=True)"
        )
        client = subprocess.Popen(
            [sys.executable, "-B", "-c", script, str(self.root)],
            cwd="/tmp",
            env={**os.environ, "PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1"},
            user=1000,
            group=1000,
            extra_groups=[],
            text=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        ready: queue.Queue[str] = queue.Queue(maxsize=1)

        def first_line() -> None:
            if client.stdout is not None:
                ready.put(client.stdout.readline(), timeout=5)

        threading.Thread(target=first_line, daemon=True).start()
        try:
            self.assertEqual(ready.get(timeout=20).strip(), "populate_approved")
            self.command("invalidate")
            stdout, stderr = client.communicate(input="confirm\n", timeout=20)
            self.assertEqual(client.returncode, 0, stderr)
            result = json.loads(stdout)
        finally:
            if client.poll() is None:
                client.kill()
                client.communicate(timeout=5)
        self.assertFalse(result["accepted"])

    def test_authority_timeout_is_denied_without_using_cached_json(self) -> None:
        self.command("stall")
        try:
            result = self.consume()
            self.assertFalse(result["accepted"])
            self.assertEqual(result["reason"], "consumer_boundary_failed:TimeoutError")
        finally:
            self.command("release")

    def test_peer_uid_cannot_be_forged_by_chowning_a_socket_node(self) -> None:
        import socket

        path = self.root / "reports" / "fake.sock"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as fake:
            fake.bind(str(path))
            fake.listen(1)
            os.chown(path, 10001, 10001)
            path.chmod(0o660)
            result = self.consume(
                "strategy._publisher_socket_path=root/'reports'/'fake.sock'; "
                "print(json.dumps({'accepted':_confirm(strategy),'reason':strategy._last_treatment_gate_reason}))"
            )
        path.unlink()
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"], "authority_peer_uid_invalid")

    def test_memory_revocation_and_publisher_shutdown_reject_fresh_reports(self) -> None:
        self.command("invalidate")
        self.assertFalse(self.consume()["accepted"])
        self.command("publish")
        self.assertTrue(self.consume()["accepted"])
        self.shutdown()
        self.assertFalse(self.consume()["accepted"])

    def test_pending_entry_revocation_cancels_only_unfilled_entry(self) -> None:
        self.command("invalidate")
        result = self.consume(
            "from types import SimpleNamespace as N; from datetime import datetime,UTC; "
            "trade=N(is_short=False,entry_side='buy',exit_side='sell',enter_tag='smartcrypto_long|decision_event_id=decision-event:f02'); "
            "order=N(ft_order_side='buy',ft_order_tag=trade.enter_tag,filled=0.5); "
            "cancel=strategy.check_entry_timeout('BTC/USDT:USDT',trade,order,datetime.now(UTC)); "
            "replacement=strategy.adjust_entry_price(trade,order,'BTC/USDT:USDT',datetime.now(UTC),101.0,100.0,trade.enter_tag,'long'); "
            "order.ft_order_side='sell'; "
            "exit_cancel=strategy.check_entry_timeout('BTC/USDT:USDT',trade,order,datetime.now(UTC)); "
            "print(json.dumps({'cancel_entry':cancel,'replacement':replacement,'cancel_exit':exit_cancel,'filled':order.filled}))"
        )
        self.assertTrue(result["cancel_entry"])
        self.assertIsNone(result["replacement"])
        self.assertFalse(result["cancel_exit"])
        self.assertEqual(result["filled"], 0.5)

    def test_report_tamper_or_missing_file_is_rejected(self) -> None:
        self.paths["report_path"].chmod(0o600)
        self.assertFalse(self.consume()["accepted"])
        self.paths["report_path"].chmod(0o660)
        report = json.loads(self.paths["report_path"].read_text())
        report["status"] = "blocked"
        self.paths["report_path"].write_text(json.dumps(report))
        self.assertFalse(self.consume()["accepted"])
        self.paths["report_path"].unlink()
        self.assertFalse(self.consume()["accepted"])

    def test_wrong_entry_tag_cannot_borrow_another_decision_approval(self) -> None:
        result = self.consume(
            "print(json.dumps({'accepted':_confirm(strategy,'smartcrypto_long|decision_event_id=decision-event:other')}))"
        )
        self.assertFalse(result["accepted"])

    def test_actual_readonly_mount_permits_socket_query_but_no_report_write(self) -> None:
        if not os.environ.get("F02_TEST_READONLY_ROOT"):
            self.skipTest("requires the explicit disposable read-only volume alias")
        result = self.consume(
            "assert _confirm(strategy); "
            "print(json.dumps({'accepted':True,'report':str(strategy._publisher_report_path)}))"
        )
        self.assertTrue(result["accepted"])
        child = self.fs.run_as(
            "from tests.test_canonical_treatment_consumer_fence_v1 import _make_strategy; "
            "_make_strategy(root)._publisher_report_path.write_text('forbidden')",
            uid=1000,
        )
        self.assertNotEqual(child.returncode, 0)
        self.assertIn("Read-only file system", child.stderr)


if __name__ == "__main__":
    unittest.main()
