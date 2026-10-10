"""Read-only deployment audit; code readiness is not host authorization."""

from __future__ import annotations

import ast
import hashlib
import os
import platform
import re
import shutil
import socket
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from smartcrypto.execution.decision_ledger_paper_runtime_writer_v1.preflight import (
    inspect_current_identity,
)
from smartcrypto.execution.decision_ledger_v4_2.contracts import (
    DecisionRecordV42,
    parse_payload_record,
)
from smartcrypto.research.execution_intelligence.evidence_readiness import (
    MAX_INPUT_BYTES,
    _read_stable,
    contract_schema_sha256,
)

from .archive import validate_external_root
from .contracts import (
    SOURCE_URL,
    CollectorConfig,
    RawBookTicker,
    build_quote,
    canonical_sha256,
    require_utc,
    schema_sha256,
)
from .transport import FetchError, PublicClient, PublicHTTPClient
from .kill_switch_authority import AuthorityDenied, LEDGER_RELATIVE, inspect_authority

PROJECT_ROOT = Path(__file__).resolve().parents[3]
MAX_LEDGER_RECORDS = 50000
MAX_LINE_BYTES = 65536
WINDOWS_HOST = os.name == "nt"
CODE_PATHS = (
    "smartcrypto/ops/execution_decision_l1/collector.py",
    "smartcrypto/ops/execution_decision_l1/contracts.py",
    "smartcrypto/ops/execution_decision_l1/transport.py",
    "smartcrypto/ops/execution_decision_l1/archive.py",
    "smartcrypto/ops/execution_decision_l1/kill_switch_authority.py",
    "scripts/collect_execution_decision_l1_prospective_v1.py",
)
Status = Literal["PROVEN", "UNPROVEN", "FAILED"]
Gate = Literal[
    "PREFLIGHT_READY_FOR_MANUAL_OPT_IN", "BLOCKED_HOST_UNVERIFIED", "BLOCKED_PREFLIGHT_FAILURE"
]


class AuditFailure(ValueError):
    """Stable auditor-authored reason, without an external exception message."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Check:
    check_id: str
    status: Status
    reason: str
    evidence_scope: Literal["CODE", "HOST", "NOT_OBSERVED"]


REQUIRED_CHECKS = frozenset(
    {
        "code_contracts",
        "opt_in_default",
        "collector_stop_contract",
        "external_kill_switch",
        "effective_identity",
        "auditor_environment",
        "decision_ledger",
        "paper_kill_switch",
        "archive_path",
        "archive_write_contract",
        "public_transport",
        "clock_alignment",
        "paper_process_binding",
        "host_resource_limits",
    }
)


def decide(checks: list[Check]) -> Gate:
    if any(check.status == "FAILED" for check in checks):
        return "BLOCKED_PREFLIGHT_FAILURE"
    ids = [check.check_id for check in checks]
    if (
        len(ids) != len(set(ids))
        or set(ids) != REQUIRED_CHECKS
        or any(check.status != "PROVEN" for check in checks)
    ):
        return "BLOCKED_HOST_UNVERIFIED"
    if any(
        check.evidence_scope != "HOST"
        for check in checks
        if check.check_id
        not in {
            "code_contracts",
            "opt_in_default",
            "collector_stop_contract",
            "external_kill_switch",
        }
    ):
        return "BLOCKED_HOST_UNVERIFIED"
    return "PREFLIGHT_READY_FOR_MANUAL_OPT_IN"


def _regular_source(path: Path) -> None:
    # Apply the existing path-policy rule to an input, without creating a writer.
    import stat

    for part in (path, *path.parents):
        if part.is_symlink() or (
            part.exists()
            and getattr(part.lstat(), "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024)
        ):
            raise AuditFailure("source_reparse_or_symlink_forbidden")
    if not path.is_file():
        raise AuditFailure("regular_source_file_required")


def inspect_ledger(path: Path, now: datetime) -> dict[str, Any]:
    _regular_source(path)
    raw, metadata = _read_stable(path)
    if raw and not raw.endswith(b"\n"):
        raise AuditFailure("ledger_partial_line_requires_later_audit")
    events: dict[str, str] = {}
    keys: dict[str, str] = {}
    decisions = links = duplicates = timestamp_regressions = 0
    last_append_time: datetime | None = None
    latest: DecisionRecordV42 | None = None
    for count, line in enumerate(raw.splitlines(), 1):
        if count > MAX_LEDGER_RECORDS or len(line) > MAX_LINE_BYTES:
            raise AuditFailure("ledger_audit_budget_exceeded")
        if not line.strip():
            continue
        row = parse_payload_record(line)
        if row.event_id in events:
            if events[row.event_id] != row.payload_sha256:
                raise AuditFailure("ledger_event_identity_collision")
            duplicates += 1
        if row.idempotency_key in keys and keys[row.idempotency_key] != row.event_id:
            raise AuditFailure("ledger_idempotency_collision")
        events[row.event_id] = row.payload_sha256
        keys[row.idempotency_key] = row.event_id
        if isinstance(row, DecisionRecordV42):
            if (
                row.runtime_mode != "paper"
                or row.exchange_private_access
                or row.sends_orders
                or row.operational_authority
            ):
                raise AuditFailure("ledger_not_isolated_paper_evidence")
            if row.decision_timestamp > now:
                raise AuditFailure("decision_timestamp_after_audit_clock")
            if last_append_time and row.decision_timestamp < last_append_time:
                timestamp_regressions += 1
            last_append_time = row.decision_timestamp
            decisions += 1
            if latest is None or row.decision_timestamp > latest.decision_timestamp:
                latest = row
        else:
            links += 1
    if latest is None:
        raise AuditFailure("ledger_has_no_paper_decisions")
    return {
        **metadata,
        "schema": "decision_ledger_payload_v4_2",
        "decision_count": decisions,
        "trade_link_count": links,
        "exact_duplicate_records": duplicates,
        "identity_collisions": 0,
        "decision_timestamp_regression_count": timestamp_regressions,
        "latest_decision": {
            "decision_id": latest.event_id,
            "candidate_id": latest.candidate_id,
            "signal_id": latest.signal_id,
            "symbol": latest.symbol,
            "decision_timestamp_utc": latest.decision_timestamp.isoformat(),
            "payload_sha256": latest.payload_sha256,
        },
        "latest_decision_age_seconds": (now - latest.decision_timestamp).total_seconds(),
        "freshness_classification": "AGE_OBSERVED_CADENCE_NOT_INFERRED",
        "classification": "OBSERVED_DECISIONS_NOT_EXCHANGE_FILLS",
        "used_for_trading_replay": False,
    }


def inspect_code(root: Path) -> tuple[dict[str, Any], list[Check]]:
    if root.resolve() != PROJECT_ROOT:
        raise AuditFailure("project_root_not_loaded_auditor_checkout")
    sources: dict[str, str] = {}
    hashes: dict[str, str] = {}
    trees: dict[str, ast.Module] = {}
    for relative in CODE_PATHS:
        path = root / relative
        _regular_source(path)
        raw, _ = _read_stable(path)
        sources[relative] = raw.decode("utf-8")
        trees[relative] = ast.parse(sources[relative])
        hashes[relative] = hashlib.sha256(raw).hexdigest()
    collector = sources[CODE_PATHS[0]]
    cli_tree = trees[CODE_PATHS[-1]]
    flags: dict[str, object] = {}
    for node in ast.walk(cli_tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_argument"
        ):
            if node.args and isinstance(node.args[0], ast.Constant):
                flags[str(node.args[0].value)] = {
                    key.arg: ast.literal_eval(key.value)
                    for key in node.keywords
                    if key.arg in {"action", "default"}
                }
    opt_in = all(
        isinstance(flags.get(key), dict) and flags[key] == {"action": "store_true"}
        for key in ("--collect", "--write-archive")
    )
    stop = (
        "except KeyboardInterrupt:" in collector
        and "thread.join(" in collector
        and "shutdown_incomplete" in collector
    )
    external_stop = (
        all(
            token in collector
            for token in (
                "AuthorityMonitor(",
                "self.authority.start()",
                "self.authority.require(fresh=True)",
                "self.archive.bind_authority(",
                "self.authority.close(",
            )
        )
        and "self._authorize_write()" in sources[CODE_PATHS[3]]
    )
    bounded = all(
        token in collector
        for token in (
            "Queue(config.queue_capacity)",
            "put_nowait",
            "maxlen=config.history_per_symbol",
            "max_decisions",
            "pre_session_decisions_ignored",
            "quote_queue_drops",
            "notice_queue_drops",
            "archive_queue_drops",
            "decision_queue_drops",
            "RawBookTicker.model_validate_json",
        )
    )
    transport = sources[CODE_PATHS[2]]
    public_only = all(
        token in transport
        for token in (
            'HTTPSConnection("fapi.binance.com"',
            '"/fapi/v1/ticker/bookTicker?symbol="',
            "subprocess.run(",
            "timeout=min(0.05, remaining)",
            '"-I"',
            '"-B"',
        )
    ) and all(token not in transport for token in ("shell=True", "/fapi/v1/order", "os.environ"))
    if not bounded or not public_only:
        raise AuditFailure("collector_contract_or_public_transport_drift")
    checks = [
        Check(
            "code_contracts",
            "PROVEN",
            "current_contracts_bounded_public_source_static_audit",
            "CODE",
        ),
        Check(
            "opt_in_default",
            "PROVEN" if opt_in else "FAILED",
            "explicit_cli_opt_in_no_auto_start" if opt_in else "opt_in_contract_drift",
            "CODE",
        ),
        Check(
            "collector_stop_contract",
            "PROVEN" if stop else "FAILED",
            "ctrl_c_bounded_join_static_only" if stop else "shutdown_contract_drift",
            "CODE",
        ),
        Check(
            "external_kill_switch",
            "UNPROVEN" if external_stop else "FAILED",
            "external_stop_binding_requires_host_proof"
            if external_stop
            else "collector_does_not_consume_canonical_kill_switch",
            "CODE",
        ),
    ]
    return {
        "source_sha256": hashes,
        "binding": "CURRENT_AUDITOR_CHECKOUT_NOT_PAPER_DEPLOYMENT",
        "collector_schema_sha256": schema_sha256(),
        "readiness_schema_sha256": contract_schema_sha256(),
        "paper_or_collector_initialized": False,
    }, checks


def inspect_archive(
    root: Path | None, forbidden: tuple[Path, ...], config: CollectorConfig
) -> tuple[dict[str, Any], Check]:
    if root is None:
        return {"write_permission": "UNPROVEN", "atomic_write_fsync": "UNPROVEN"}, Check(
            "archive_path", "UNPROVEN", "external_archive_root_not_supplied", "NOT_OBSERVED"
        )
    resolved = validate_external_root(root, forbidden)
    if resolved.exists() and not resolved.is_dir():
        raise AuditFailure("archive_root_not_directory")
    ancestor = next((part for part in (resolved, *resolved.parents) if part.exists()), None)
    if ancestor is None:
        raise AuditFailure("archive_existing_ancestor_unavailable")
    usage = shutil.disk_usage(ancestor)
    # Allow headroom for temporary atomic replacements and small metadata.
    needed = 2 * config.max_archive_bytes + 16 * 1024**2
    enough = usage.free >= needed
    return {
        "path": str(resolved),
        "exists": resolved.exists(),
        "existing_ancestor": str(ancestor),
        "free_bytes": usage.free,
        "minimum_free_bytes": needed,
        "segment_byte_limit": config.max_archive_bytes,
        "write_access_hint": os.access(ancestor, os.W_OK),
        "write_permission": "UNPROVEN",
        "atomic_write_fsync": "UNPROVEN",
        "write_test_performed": False,
    }, Check(
        "archive_path",
        "PROVEN" if enough and resolved.is_dir() else "UNPROVEN" if enough else "FAILED",
        "external_path_capacity_observed"
        if enough and resolved.is_dir()
        else "archive_creation_requires_write"
        if enough
        else "insufficient_archive_space",
        "HOST",
    )


def inspect_paper_kill_switch(
    root: Path, symbols: tuple[str, ...] = ("BTCUSDT", "ETHUSDT")
) -> tuple[dict[str, Any], Check]:
    metadata = inspect_authority(root, root / LEDGER_RELATIVE, symbols)
    return {**metadata, "blocks_activation": False, "modified": False}, Check(
        "paper_kill_switch", "PROVEN", "canonical_paper_kill_switch_clear_snapshot_only", "HOST"
    )


def local_clock_diagnostic() -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": "UNPROVEN",
        "network_executed": False,
        "clock_domains": ["HOST", "PAPER_CONTAINER", "EXCHANGE"],
        "cross_domain_offset_bound_seconds": None,
    }
    if not WINDOWS_HOST:
        result["reason"] = "local_time_service_query_unavailable_on_this_platform"
        return result
    command = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/w32tm.exe"
    if not command.is_file():
        result["reason"] = "local_time_service_command_unavailable"
        return result
    try:
        process = subprocess.run(
            [str(command), "/query", "/status", "/verbose"],
            capture_output=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        result["reason"] = f"local_clock_query_{type(exc).__name__}"
        return result
    result["exit_code"] = process.returncode
    result["output_sha256"] = hashlib.sha256(process.stdout + process.stderr).hexdigest()
    # Unknown/localized formats do not turn a successful command into clock proof.
    output = process.stdout.decode("utf-8", errors="replace")
    match = re.search(r"(?:Leap Indicator|Indicador de Salto)\s*:\s*(\d+)", output, re.IGNORECASE)
    leap = int(match[1]) if match else None
    result["leap_indicator"] = leap
    result["status"] = "FAILED" if leap == 3 else "UNPROVEN"
    result["reason"] = (
        "local_clock_unsynchronized" if leap == 3 else "cross_domain_clock_alignment_not_proven"
    )
    return result


def public_diagnostic(
    config: CollectorConfig, *, authorized: bool, client: PublicClient | None = None
) -> dict[str, Any]:
    if not authorized:
        return {
            "status": "UNPROVEN",
            "reason": "explicit_public_connectivity_opt_in_required",
            "network_calls_executed": False,
            "market_messages_archived": False,
        }
    started = time.monotonic()
    transport = client if client is not None else PublicHTTPClient()
    try:
        received = transport.fetch(config.symbols[0], config.request_timeout_seconds)
        RawBookTicker.model_validate_json(received.raw_body)
        available = datetime.now(timezone.utc)
        quote = build_quote(
            received.raw_body, received.receive_time_utc, available, time.monotonic_ns()
        )
        if quote.symbol != config.symbols[0] or quote.event_time_utc is None:
            raise AuditFailure("public_schema_or_event_time_missing")
        age = (available - quote.event_time_utc).total_seconds()
        if not 0 <= age <= config.max_quote_age_seconds:
            raise AuditFailure("public_source_stale_or_future")
        status: Status = "PROVEN"
        reason = "single_public_get_schema_freshness_validated_not_clock_proof"
    except (FetchError, OSError, ValueError, OverflowError) as exc:
        status = "FAILED"
        reason = (
            exc.reason
            if isinstance(exc, AuditFailure)
            else f"public_diagnostic_{type(exc).__name__}"
        )
    return {
        "status": status,
        "reason": reason,
        "source": SOURCE_URL,
        "request_count_limit": 1,
        "duration_seconds": time.monotonic() - started,
        "deadline_seconds": config.request_timeout_seconds,
        "network_calls_executed": True,
        "market_messages_archived": False,
        "collector_initialized": False,
        "raw_message_in_report": False,
    }


def audit_deployment(
    *,
    project_root: Path,
    runtime_root: Path | None,
    archive_root: Path | None,
    diagnose_public_connectivity: bool = False,
    config: CollectorConfig | None = None,
    as_of_utc: datetime | None = None,
) -> dict[str, Any]:
    now = require_utc(as_of_utc or datetime.now(timezone.utc))
    limits = config if config is not None else CollectorConfig()
    checks: list[Check] = []
    sources: dict[str, Any] = {}

    def failure(check_id: str, exc: BaseException) -> None:
        # No raw exception, environment, response body or credential strings.
        reason = (
            exc.reason
            if isinstance(exc, (AuditFailure, AuthorityDenied))
            else f"{check_id}_{type(exc).__name__}"
        )
        checks.append(Check(check_id, "FAILED", reason, "HOST"))

    try:
        sources["code"], static = inspect_code(project_root)
        checks.extend(static)
    except (OSError, ValueError, SyntaxError) as exc:
        failure("code_contracts", exc)
    identity = inspect_current_identity()
    checks.append(
        Check(
            "effective_identity",
            "UNPROVEN" if not identity.verified else "FAILED" if identity.elevated else "PROVEN",
            identity.reason,
            "HOST",
        )
    )
    unsafe = [
        key
        for key in ("LIVE_ENABLED", "ORDER_SUBMISSION_ENABLED", "REAL_ORDER_SUBMISSION_ENABLED")
        if os.environ.get(key, "false").strip().lower() not in {"false", "0", "no", "off"}
    ]
    checks.append(
        Check(
            "auditor_environment",
            "FAILED" if unsafe else "PROVEN",
            "unsafe_auditor_authority_flags"
            if unsafe
            else "auditor_flags_no_operational_authority",
            "HOST",
        )
    )
    sources["auditor_environment"] = {
        "unsafe_flag_names": unsafe,
        "paper_container_environment_audited": False,
    }
    if runtime_root is None:
        checks.extend(
            Check(key, "UNPROVEN", "paper_runtime_root_not_supplied", "NOT_OBSERVED")
            for key in ("decision_ledger", "paper_kill_switch")
        )
    elif not runtime_root.is_absolute():
        failure("decision_ledger", ValueError("absolute_runtime_root_required"))
    else:
        try:
            sources["decision_ledger"] = inspect_ledger(runtime_root / LEDGER_RELATIVE, now)
            checks.append(
                Check(
                    "decision_ledger",
                    "PROVEN",
                    "sealed_exact_paper_identities_snapshot_verified",
                    "HOST",
                )
            )
        except (OSError, ValueError, OverflowError) as exc:
            failure("decision_ledger", exc)
        try:
            sources["paper_kill_switch"], check = inspect_paper_kill_switch(
                runtime_root, limits.symbols
            )
            checks.append(check)
        except (OSError, ValueError, RuntimeError) as exc:
            failure("paper_kill_switch", exc)
    forbidden = (project_root, runtime_root) if runtime_root is not None else (project_root,)
    try:
        sources["archive"], check = inspect_archive(archive_root, forbidden, limits)
        checks.append(check)
    except (OSError, ValueError) as exc:
        failure("archive_path", exc)
    checks.append(
        Check(
            "archive_write_contract",
            "UNPROVEN",
            "create_append_fsync_replace_delete_not_tested_read_only",
            "NOT_OBSERVED",
        )
    )
    try:
        if diagnose_public_connectivity:
            inspect_authority(
                runtime_root, (runtime_root or project_root) / LEDGER_RELATIVE, limits.symbols
            )
        sources["transport"] = public_diagnostic(limits, authorized=diagnose_public_connectivity)
    except AuthorityDenied as exc:
        sources["transport"] = {
            "status": "FAILED",
            "reason": exc.reason,
            "network_calls_executed": False,
            "market_messages_archived": False,
        }
    checks.append(
        Check(
            "public_transport",
            sources["transport"]["status"],
            sources["transport"]["reason"],
            "HOST" if diagnose_public_connectivity else "NOT_OBSERVED",
        )
    )
    sources["clocks"] = local_clock_diagnostic()
    checks.append(
        Check("clock_alignment", sources["clocks"]["status"], sources["clocks"]["reason"], "HOST")
    )
    checks.extend(
        [
            Check(
                "paper_process_binding",
                "UNPROVEN",
                "running_paper_process_mounts_and_clocks_not_observed",
                "NOT_OBSERVED",
            ),
            Check(
                "host_resource_limits",
                "UNPROVEN",
                "cpu_rss_limits_and_stop_trial_not_proven_without_activation",
                "NOT_OBSERVED",
            ),
        ]
    )
    observed_ids = {check.check_id for check in checks}
    checks.extend(
        Check(key, "UNPROVEN", "upstream_dependency_not_verified", "NOT_OBSERVED")
        for key in REQUIRED_CHECKS - observed_ids
    )
    checks.sort(key=lambda item: item.check_id)
    gate = decide(checks)
    report: dict[str, Any] = {
        "schema_version": "execution_decision_l1_deployment_preflight_v1",
        "observed_at_utc": now.isoformat(),
        "status": "ready" if gate == "PREFLIGHT_READY_FOR_MANUAL_OPT_IN" else "blocked",
        "decision": gate,
        "reason": next(
            (check.reason for check in checks if check.status == "FAILED"),
            "mandatory_host_evidence_unverified"
            if gate == "BLOCKED_HOST_UNVERIFIED"
            else "all_mandatory_host_requirements_proven",
        ),
        "checks": [asdict(check) for check in checks],
        "failures": sorted(check.reason for check in checks if check.status == "FAILED"),
        "unverified": sorted(check.reason for check in checks if check.status == "UNPROVEN"),
        "host": {
            "host_id_sha256": canonical_sha256(
                {
                    "hostname": socket.gethostname(),
                    "os": platform.system(),
                    "machine": platform.machine(),
                }
            ),
            "platform": platform.system(),
            "architecture": platform.machine(),
            "python": platform.python_version(),
            "auditor_pid": os.getpid(),
            "effective_identity": identity.model_dump(mode="json"),
            "target_binding": "LOCAL_FILES_ONLY_NOT_RUNNING_PAPER_PROCESS",
            "cpu_count": os.cpu_count(),
        },
        "sources": sources,
        "limits": limits.model_dump(mode="json"),
        "resource_contract": {
            "queues": 4,
            "queue_capacity_each": limits.queue_capacity,
            "quote_history_per_symbol": limits.history_per_symbol,
            "decision_registry_limit": limits.max_decisions,
            "ledger_audit_byte_limit": MAX_INPUT_BYTES,
            "ledger_audit_record_limit": MAX_LEDGER_RECORDS,
            "hard_cpu_limit": "UNPROVEN",
            "hard_rss_limit": "UNPROVEN",
        },
        "EXECUTION_READINESS": "BLOCKED_MISSING_EXECUTION_EVIDENCE",
        "manual_activation_allowed": gate == "PREFLIGHT_READY_FOR_MANUAL_OPT_IN",
        "network_calls_executed": sources["transport"]["network_calls_executed"],
        "write_performed": False,
        "collector_initialized": False,
        "collection_started": False,
        "safety": {
            "research_only": True,
            "read_only": True,
            "sends_orders": False,
            "exchange_private_access": False,
            "changes_freqtrade": False,
            "changes_risk": False,
            "changes_paper_treatment": False,
            "changes_pnl": False,
            "runtime_activation_performed": False,
            "observed_fills_provided": False,
            "economic_uplift_claim_allowed": False,
        },
    }
    report["report_sha256"] = canonical_sha256(report)
    return report
