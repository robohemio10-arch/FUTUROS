"""Validate the disabled paper Decision Ledger writer profile without writing."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from smartcrypto.execution.decision_ledger_paper_runtime_writer_v1 import (
    PaperRuntimeWriterProfileV1,
    inspect_current_identity,
    run_writer_preflight,
)

MAX_PROFILE_BYTES = 256 * 1024


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate paper writer profile without constructing or invoking a writer."
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--profile")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def build_validation_report(
    *, project_root: Path, profile: PaperRuntimeWriterProfileV1
) -> dict[str, Any]:
    identity = inspect_current_identity()
    preflight = run_writer_preflight(
        project_root=project_root,
        profile=profile,
        identity=identity,
    )
    if not profile.enabled:
        status = "ok"
        reason = "profile_valid_disabled_by_default"
        decision = "KEEP_WRITER_DISABLED"
    elif preflight.status == "ready":
        status = "warning"
        reason = "preflight_ready_but_runtime_wiring_not_authorized"
        decision = "REVIEW_ONLY_NO_RUNTIME_WIRING"
    else:
        status = "blocked"
        reason = preflight.reason
        decision = "BLOCK_WRITER_CREATION"

    safety = profile.safety_flags.model_dump(mode="json")
    return {
        "schema_version": "decision_ledger_paper_runtime_writer_profile_validator_v1",
        "status": status,
        "reason": reason,
        "decision": decision,
        "profile": profile.model_dump(mode="json"),
        "preflight": preflight.model_dump(mode="json"),
        "profile_enabled": profile.enabled,
        "writer_creation_allowed": preflight.writer_creation_allowed,
        "writer_factory_invoked": False,
        "writer_created": False,
        "writer_invoked_in_runtime": False,
        "runtime_wiring_performed": False,
        "write_performed": False,
        "writes_runtime": False,
        "writes_sqlite": False,
        "writes_parquet": False,
        "safety_flags": safety,
        **safety,
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        project_root = Path(args.project_root).expanduser().resolve(strict=False)
        profile = _load_profile(Path(args.profile)) if args.profile else PaperRuntimeWriterProfileV1()
        report = build_validation_report(project_root=project_root, profile=profile)
    except (OSError, ValueError, json.JSONDecodeError, ValidationError) as exc:
        report = _blocked_report(exc)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"{report['status']}:{report['reason']}")
    return 1 if report["status"] == "blocked" else 0


def _load_profile(path: Path) -> PaperRuntimeWriterProfileV1:
    candidate = path.expanduser()
    if candidate.is_symlink():
        raise ValueError("profile_symlink_denied")
    if candidate.suffix.casefold() != ".json":
        raise ValueError("profile_extension_must_be_json")
    if not candidate.is_file():
        raise ValueError("profile_file_missing")
    if candidate.stat().st_size > MAX_PROFILE_BYTES:
        raise ValueError("profile_file_too_large")
    payload = json.loads(candidate.read_text(encoding="utf-8-sig"))
    return PaperRuntimeWriterProfileV1.model_validate(payload)


def _blocked_report(error: Exception) -> dict[str, Any]:
    safety = PaperRuntimeWriterProfileV1().safety_flags.model_dump(mode="json")
    error_message_sha256 = hashlib.sha256(str(error).encode("utf-8")).hexdigest()
    return {
        "schema_version": "decision_ledger_paper_runtime_writer_profile_validator_v1",
        "status": "blocked",
        "reason": f"profile_validation_failed:{type(error).__name__}",
        "decision": "BLOCK_WRITER_CREATION",
        "error_type": type(error).__name__,
        "error_message_sha256": error_message_sha256,
        "profile_enabled": False,
        "writer_creation_allowed": False,
        "writer_factory_invoked": False,
        "writer_created": False,
        "writer_invoked_in_runtime": False,
        "runtime_wiring_performed": False,
        "write_performed": False,
        "writes_runtime": False,
        "writes_sqlite": False,
        "writes_parquet": False,
        "safety_flags": safety,
        **safety,
    }


if __name__ == "__main__":
    raise SystemExit(main())
