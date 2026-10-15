"""F02 boundary tests; Linux DAC/ACL cases require an isolated root test process."""

from __future__ import annotations

import ast
import ctypes
import ctypes.util
import errno
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

from scripts import canonical_treatment_report_healthcheck_v1 as healthcheck
from smartcrypto.research.canonical_treatment import publisher

ROOT = Path(__file__).resolve().parents[1]


def _paths(root: Path) -> dict[str, Path]:
    return {
        "control_signals_path": root / "sources" / "control.json",
        "v3_evidence_path": root / "sources" / "evidence.json",
        "output_path": root / "outputs" / "active.json",
        "report_path": root / "reports" / "report.json",
        "ledger_path": root / "ledger" / "ledger.json",
    }


def _fixture(root: Path) -> dict[str, Path]:
    paths = _paths(root)
    for path in paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)
    crosswalk = {
        "schema_version": publisher.CROSSWALK_SCHEMA_VERSION,
        "signal_id": "signal:f02",
        "operational_decision_event_id": "decision-event:f02",
        "operational_decision_payload_sha256": "a" * 64,
        "v3_decision_event_id": "v3:f02",
        "v3_decision_payload_sha256": "b" * 64,
    }
    signal = {
        "signal_id": "signal:f02",
        "pair": "BTC/USDT:USDT",
        "symbol": "BTCUSDT",
        "side": "long",
        "risk_approved": True,
        "valid_until": (datetime.now(UTC) + timedelta(minutes=30)).isoformat(),
        "decision_ledger": {
            "decision_event_id": "decision-event:f02",
            "decision_payload_sha256": "a" * 64,
        },
    }
    decision = {
        "event_id": "v3:f02",
        "signal_id": "signal:f02",
        "payload_sha256": "b" * 64,
        "ai_shadow_decision": "ALLOW",
        "pair": signal["pair"],
        "symbol": signal["symbol"],
        "side": signal["side"],
    }
    evidence = {
        "signals": [
            {
                "signal_id": "signal:f02",
                "decision_event_id": "v3:f02",
                "decision": decision,
                "operational_crosswalk": {
                    **crosswalk,
                    "crosswalk_sha256": publisher._hash(crosswalk),
                },
            }
        ]
    }
    paths["control_signals_path"].write_text(json.dumps({"signals": [signal]}))
    paths["v3_evidence_path"].write_text(json.dumps(evidence))
    return paths


def _consumer(path: Path) -> Any:
    """Execute the existing consumer's exact file/expiry methods, without Freqtrade imports."""
    source = ROOT / "freqtrade/user_data/strategies/SmartCryptoSignalStrategy.py"
    strategy = next(
        node
        for node in ast.parse(source.read_text()).body
        if isinstance(node, ast.ClassDef) and node.name == "SmartCryptoSignalStrategy"
    )
    methods = {
        "_read_first_active_signal_file",
        "_read_json",
        "_extract_signals",
        "_is_signal_active",
        "_valid_until_active",
    }
    selected = [
        node for node in strategy.body if isinstance(node, ast.FunctionDef) and node.name in methods
    ]
    assert {node.name for node in selected} == methods
    tree = ast.Module(
        body=[
            ast.ClassDef(
                name="Consumer",
                bases=[],
                keywords=[],
                body=[node for node in selected],
                decorator_list=[],
            )
        ],
        type_ignores=[],
    )
    namespace: dict[str, Any] = {
        "Any": Any,
        "Path": Path,
        "datetime": datetime,
        "timezone": timezone,
        "json": json,
    }
    exec(compile(ast.fix_missing_locations(tree), str(source), "exec"), namespace)
    consumer = namespace["Consumer"]()
    consumer._signal_paths = [path]
    return consumer


class PublisherBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="f02-unit-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.paths = _fixture(self.root)

    def publish(self) -> dict[str, Any]:
        return publisher.build_treatment_artifact(
            control_signals_path=self.paths["control_signals_path"],
            v3_evidence_path=self.paths["v3_evidence_path"],
            output_path=self.paths["output_path"],
            report_path=self.paths["report_path"],
            ledger_path=self.paths["ledger_path"],
        )

    def denied_writer(self, denied: set[Path]) -> Any:
        original = publisher._write_json

        def write(path: Path, payload: Any) -> None:
            if path in denied:
                raise PermissionError(errno.EACCES, "private-payload-must-not-leak")
            original(path, payload)

        return patch.object(publisher, "_write_json", side_effect=write)

    def test_success_preserves_exact_control_object(self) -> None:
        self.assertEqual(self.publish()["status"], "ok")
        selected = json.loads(self.paths["output_path"].read_text())["signals"]
        control = json.loads(self.paths["control_signals_path"].read_text())["signals"]
        self.assertEqual(selected, control)

    def test_read_failure_revokes_previous_artifact(self) -> None:
        self.publish()
        self.paths["control_signals_path"].write_text("invalid-json")
        with self.assertLogs(publisher.__name__, level="ERROR"):
            report = self.publish()
        self.assertEqual(report["status"], "blocked")
        self.assertTrue(report["prior_output_invalidation_confirmed"])
        self.assertTrue(report["blocked_report_persisted"])
        self.assertIsNone(_consumer(self.paths["output_path"])._read_first_active_signal_file()[0])

    def test_source_permission_error_preserves_sanitized_cause(self) -> None:
        original = Path.read_text

        def read(path: Path, *args: Any, **kwargs: Any) -> str:
            if path == self.paths["control_signals_path"]:
                raise PermissionError(errno.EACCES, "private-payload-must-not-leak")
            return original(path, *args, **kwargs)

        with (
            patch.object(Path, "read_text", read),
            self.assertLogs(publisher.__name__, level="ERROR") as logs,
        ):
            report = self.publish()
        self.assertEqual(report["failure"]["stage"], "read_control")
        self.assertEqual(report["failure"]["io_error_type"], "PermissionError")
        self.assertEqual(report["failure"]["errno"], errno.EACCES)
        self.assertNotIn("private-payload", json.dumps(report) + str(logs.output))
        self.assertNotIn(str(self.root), json.dumps(report) + str(logs.output))

    def test_output_denied_still_persists_blocked_report_and_exposes_unfenced_consumer(
        self,
    ) -> None:
        self.publish()
        previous = self.paths["output_path"].read_bytes()
        with (
            self.denied_writer({self.paths["output_path"]}),
            self.assertLogs(publisher.__name__, level="ERROR"),
        ):
            report = self.publish()
        self.assertEqual(report["status"], "blocked")
        self.assertFalse(report["prior_output_invalidation_confirmed"])
        self.assertTrue(report["blocked_report_persisted"])
        self.assertFalse(report["continuous_consumer_fence_proven"])
        self.assertEqual(self.paths["output_path"].read_bytes(), previous)
        self.assertEqual(json.loads(self.paths["report_path"].read_text())["status"], "blocked")
        # This is a deployment blocker, not a claim that healthcheck revokes consumption.
        self.assertIsNotNone(
            _consumer(self.paths["output_path"])._read_first_active_signal_file()[0]
        )
        self.assertEqual(healthcheck.main(["--report", str(self.paths["report_path"])]), 2)

    def test_both_recovery_writes_denied_are_explicit(self) -> None:
        self.publish()
        with (
            self.denied_writer({self.paths["output_path"], self.paths["report_path"]}),
            self.assertLogs(publisher.__name__, level="ERROR") as logs,
        ):
            report = self.publish()
        self.assertEqual(report["status"], "blocked")
        self.assertFalse(report["prior_output_invalidation_confirmed"])
        self.assertFalse(report["blocked_report_persisted"])
        self.assertEqual(
            [row["stage"] for row in report["recovery_errors"]],
            ["invalidate_output", "persist_blocked_report"],
        )
        self.assertTrue(all(row["errno"] == errno.EACCES for row in report["recovery_errors"]))
        self.assertNotIn("private-payload", str(logs.output))
        self.assertEqual(report["epoch2_runtime_parity"], "not_evaluated")

    def test_invalidation_readback_mismatch_is_not_claimed_as_revocation(self) -> None:
        self.paths["control_signals_path"].write_text("invalid-json")
        original = publisher._read_json

        def read(path: Path, required: bool = True) -> dict[str, Any]:
            if path == self.paths["output_path"]:
                return {"signals": ["concurrent-replacement"]}
            return original(path, required)

        with patch.object(publisher, "_read_json", side_effect=read), self.assertLogs(
            publisher.__name__, level="ERROR"
        ):
            report = self.publish()
        self.assertEqual(report["status"], "blocked")
        self.assertFalse(report["prior_output_invalidation_confirmed"])
        self.assertEqual(report["recovery_errors"][0]["stage"], "invalidate_output")
        self.assertTrue(report["blocked_report_persisted"])

    def test_disk_full_in_both_recovery_actions_is_not_silent(self) -> None:
        with patch.object(
            publisher, "_write_json", side_effect=OSError(errno.ENOSPC, "private-payload")
        ), self.assertLogs(publisher.__name__, level="ERROR") as logs:
            report = self.publish()
        self.assertEqual(report["status"], "blocked")
        self.assertFalse(report["prior_output_invalidation_confirmed"])
        self.assertFalse(report["blocked_report_persisted"])
        self.assertTrue(all(row["errno"] == errno.ENOSPC for row in report["recovery_errors"]))
        self.assertNotIn("private-payload", str(logs.output))

    def test_ledger_or_report_write_failure_invalidates_signals(self) -> None:
        for name in ("ledger_path", "report_path"):
            with (
                self.subTest(name=name),
                self.denied_writer({self.paths[name]}),
                self.assertLogs(publisher.__name__, level="ERROR"),
            ):
                report = self.publish()
            self.assertEqual(report["status"], "blocked")
            self.assertTrue(report["prior_output_invalidation_confirmed"])
            self.assertEqual(json.loads(self.paths["output_path"].read_text())["signals"], [])

    def test_exit_mirror_failure_cannot_publish_ok_report(self) -> None:
        with (
            patch.object(
                publisher, "_mirror_exit_control", side_effect=PermissionError(13, "denied")
            ),
            self.assertLogs(publisher.__name__, level="ERROR"),
        ):
            report = self.publish()
        self.assertEqual(report["failure"]["stage"], "mirror_exit_control")
        self.assertEqual(json.loads(self.paths["report_path"].read_text())["status"], "blocked")

    def test_loop_exits_nonzero_on_blocked_cycle_without_sleep(self) -> None:
        with (
            patch.object(publisher, "build_treatment_artifact", return_value={"status": "blocked"}),
            patch.object(publisher.time, "sleep") as sleep,
            redirect_stdout(io.StringIO()),
        ):
            code = publisher.main(
                ["--v3-evidence", str(self.paths["v3_evidence_path"]), "--interval-seconds", "5"]
            )
        self.assertEqual(code, 2)
        sleep.assert_not_called()

    def test_healthy_once_remains_successful(self) -> None:
        with redirect_stdout(io.StringIO()):
            code = publisher.main(
                [
                    "--v3-evidence",
                    str(self.paths["v3_evidence_path"]),
                    "--control-signals",
                    str(self.paths["control_signals_path"]),
                    "--output",
                    str(self.paths["output_path"]),
                    "--report",
                    str(self.paths["report_path"]),
                    "--ledger",
                    str(self.paths["ledger_path"]),
                    "--control-exit-control",
                    str(self.root / "absent-exit.json"),
                ]
            )
        self.assertEqual(code, 0)

    def test_healthcheck_rejects_invalid_blocked_stale_and_future_reports(self) -> None:
        report = self.publish()
        cases = [
            ({}, 0),
            ({"status": "blocked"}, 2),
            ({"generated_at_utc": (datetime.now(UTC) - timedelta(minutes=1)).isoformat()}, 3),
            ({"generated_at_utc": (datetime.now(UTC) + timedelta(minutes=1)).isoformat()}, 3),
            ({"generated_at_utc": "2026-10-15T00:00:00"}, 4),
            ({"schema_version": "incorrect"}, 4),
        ]
        for changes, expected in cases:
            with self.subTest(changes=changes):
                self.paths["report_path"].write_text(json.dumps(report | changes))
                self.assertEqual(
                    healthcheck.main(["--report", str(self.paths["report_path"])]), expected
                )
        for text in ("{", "[]", "null"):
            self.paths["report_path"].write_text(text)
            self.assertEqual(healthcheck.main(["--report", str(self.paths["report_path"])]), 4)

    def test_healthcheck_unreadable_missing_and_invalid_deadlines(self) -> None:
        self.publish()
        with patch.object(Path, "read_text", side_effect=PermissionError(13, "denied")):
            self.assertEqual(healthcheck.main(["--report", str(self.paths["report_path"])]), 4)
        with patch.object(Path, "is_file", side_effect=PermissionError(13, "denied")):
            self.assertEqual(healthcheck.main(["--report", str(self.paths["report_path"])]), 4)
        self.assertEqual(healthcheck.main(["--report", str(self.root / "missing")]), 1)
        for duration in ("nan", "inf", "-1", "0"):
            self.assertEqual(
                healthcheck.main(
                    ["--report", str(self.paths["report_path"]), "--max-age-seconds", duration]
                ),
                4,
            )


@unittest.skipUnless(
    sys.platform == "linux" and os.geteuid() == 0,
    "requires isolated Linux root for fixture-only UID/ACL setup",
)
class LinuxPermissionBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="f02-posix-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.paths = _fixture(self.root)
        self.root.chmod(0o711)
        for name in ("sources", "outputs", "unrelated"):
            directory = self.root / name
            directory.mkdir(exist_ok=True)
            os.chown(directory, 1000, 1000)
            directory.chmod(0o2770 if name == "outputs" else 0o700)
            observed = directory.stat()
            self.assertEqual((observed.st_uid, observed.st_gid), (1000, 1000))
            self.assertEqual(stat.S_IMODE(observed.st_mode), 0o2770 if name == "outputs" else 0o700)
        for name in ("reports", "ledger"):
            directory = self.root / name
            os.chown(directory, 10001, 10001)
            directory.chmod(0o700)
        for name in ("control_signals_path", "v3_evidence_path"):
            os.chown(self.paths[name], 1000, 1000)
            self.paths[name].chmod(0o600)
        library = ctypes.util.find_library("acl")
        self.assertIsNotNone(library, "Linux permission gate requires libacl")
        self.acl = ctypes.CDLL(str(library), use_errno=True)
        self.acl.acl_from_text.argtypes = [ctypes.c_char_p]
        self.acl.acl_from_text.restype = ctypes.c_void_p
        self.acl.acl_valid.argtypes = [ctypes.c_void_p]
        self.acl.acl_set_file.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_void_p]
        self.acl.acl_free.argtypes = [ctypes.c_void_p]

    def grant(self, path: Path, text: str, *, default: bool = False) -> None:
        acl = self.acl.acl_from_text(text.encode("ascii"))
        self.assertTrue(acl, "valid POSIX ACL required")
        try:
            self.assertEqual(self.acl.acl_valid(acl), 0)
            result = self.acl.acl_set_file(os.fsencode(path), 0x4000 if default else 0x8000, acl)
            self.assertEqual(result, 0, f"ACL unsupported/denied errno={ctypes.get_errno()}")
        finally:
            self.acl.acl_free(acl)

    def grant_sources(self) -> None:
        self.grant(self.root / "sources", "u::rwx,u:10001:--x,g::---,m::--x,o::---")
        for name in ("control_signals_path", "v3_evidence_path"):
            self.grant(self.paths[name], "u::rw-,u:10001:r--,g::---,m::r--,o::---")

    def grant_outputs(self) -> None:
        self.grant(self.root / "outputs", "u::rwx,u:10001:rwx,g::rwx,m::rwx,o::---")
        self.grant(self.root / "outputs", "u::rwx,u:1000:r-x,g::---,m::r-x,o::---", default=True)

    def run_as(self, code: str, *, uid: int = 10001) -> subprocess.CompletedProcess[str]:
        prelude = (
            "import json, os, sys; from pathlib import Path; "
            "from tests.test_canonical_treatment_publisher_permission_boundary_v1 "
            "import _paths, _consumer; "
            "from smartcrypto.research.canonical_treatment.publisher import build_treatment_artifact; "
            "root=Path(sys.argv[1]); paths=_paths(root); "
            "assert os.geteuid()==int(sys.argv[2]); "
            "assert os.getegid()==int(sys.argv[2]); assert os.getgroups()==[]; "
        )
        return subprocess.run(
            [sys.executable, "-B", "-c", prelude + code, str(self.root), str(uid)],
            cwd="/tmp",
            env={**os.environ, "PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1"},
            user=uid,
            group=uid,
            extra_groups=[],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )

    def publish(self) -> tuple[dict[str, Any], str]:
        child = self.run_as("print(json.dumps(build_treatment_artifact(**paths)))")
        self.assertEqual(child.returncode, 0, child.stderr)
        return json.loads(child.stdout), child.stderr

    def test_uid10001_without_acl_fails_explicitly(self) -> None:
        report, stderr = self.publish()
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["effective_uid"], 10001)
        self.assertEqual(report["effective_gid"], 10001)
        self.assertEqual(report["supplementary_gids"], [])
        self.assertEqual(report["failure"]["stage"], "read_control")
        self.assertEqual(report["failure"]["errno"], errno.EACCES)
        self.assertFalse(report["prior_output_invalidation_confirmed"])
        self.assertTrue(report["blocked_report_persisted"])
        self.assertIn("PermissionError", stderr)

    def test_narrow_acl_allows_publication_but_not_source_writes_or_unrelated_access(self) -> None:
        self.grant_sources()
        self.grant_outputs()
        report, stderr = self.publish()
        self.assertEqual(report["status"], "ok", stderr)
        self.assertEqual(report["selected_signal_count"], 1)
        child = self.run_as("paths['control_signals_path'].write_text('forbidden')")
        self.assertNotEqual(child.returncode, 0)
        self.assertIn("PermissionError", child.stderr)
        child = self.run_as("list((root/'sources').iterdir())")
        self.assertNotEqual(child.returncode, 0)
        child = self.run_as("list((root/'unrelated').iterdir())")
        self.assertNotEqual(child.returncode, 0)
        child = self.run_as("print(paths['output_path'].read_text())", uid=1000)
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertEqual(len(json.loads(child.stdout)["signals"]), 1)
        child = self.run_as(
            "from scripts.canonical_treatment_report_healthcheck_v1 import main; "
            "raise SystemExit(main(['--report',str(paths['report_path'])]))"
        )
        self.assertEqual(child.returncode, 0, child.stderr)

    def test_missing_ancestor_traverse_blocks_despite_readable_source(self) -> None:
        self.grant_sources()
        self.grant_outputs()
        (self.root / "sources").chmod(0o700)
        report, _ = self.publish()
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["failure"]["stage"], "read_control")
        self.assertTrue(report["prior_output_invalidation_confirmed"])

    def test_output_acl_revocation_keeps_consumer_unsafe_and_is_not_hidden(self) -> None:
        self.grant_sources()
        self.grant_outputs()
        self.assertEqual(self.publish()[0]["status"], "ok")
        self.grant(self.root / "outputs", "u::rwx,u:10001:r-x,g::rwx,m::rwx,o::---")
        report, stderr = self.publish()
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["failure"]["stage"], "publish_output")
        self.assertFalse(report["prior_output_invalidation_confirmed"])
        self.assertTrue(report["blocked_report_persisted"])
        self.assertFalse(report["continuous_consumer_fence_proven"])
        self.assertIn("invalidate_output", stderr)
        child = self.run_as(
            "assert _consumer(paths['output_path'])._read_first_active_signal_file()[0] is not None",
            uid=1000,
        )
        self.assertEqual(child.returncode, 0, child.stderr)

    def test_no_output_or_report_access_is_explicit_and_nonzero_in_cli_loop(self) -> None:
        self.grant_sources()
        os.chown(self.root / "reports", 1000, 1000)
        child = self.run_as(
            "from smartcrypto.research.canonical_treatment.publisher import main; "
            "args=['--interval-seconds','5']; "
            "args += ['--control-signals',str(paths['control_signals_path']),"
            "'--v3-evidence',str(paths['v3_evidence_path']),"
            "'--output',str(paths['output_path']),'--report',str(paths['report_path']),"
            "'--ledger',str(paths['ledger_path'])]; raise SystemExit(main(args))"
        )
        self.assertEqual(child.returncode, 2, child.stderr)
        report = json.loads(child.stdout)
        self.assertEqual(report["status"], "blocked")
        self.assertFalse(report["blocked_report_persisted"])
        self.assertIn("persist_blocked_report", child.stderr)

    def test_healthcheck_denies_unreadable_report_and_symlink(self) -> None:
        self.grant_sources()
        self.grant_outputs()
        self.publish()
        path = self.paths["report_path"]
        os.chown(path, 1000, 1000)
        path.chmod(0o600)
        code = (
            "from scripts.canonical_treatment_report_healthcheck_v1 import main; "
            "raise SystemExit(main(['--report',str(paths['report_path'])]))"
        )
        self.assertEqual(self.run_as(code).returncode, 4)
        path.unlink()
        path.symlink_to(self.paths["output_path"])
        self.assertNotEqual(self.run_as(code).returncode, 0)


if __name__ == "__main__":
    unittest.main()
