from __future__ import annotations

import ast
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from smartcrypto.ops.dashboard_snapshots.builder_common import iso_utc, json_safe
from smartcrypto.ops.dashboard_snapshots.runtime_blockers_remediation import SAFETY_FLAGS


SCHEMA_VERSION = "runtime_freshness_producer_entrypoint_static_safety_audit_v1"
CONTRACTS_REPORT = Path("data/reports/runtime_freshness_producer_contracts_audit_v1.json")

FORBIDDEN_ACTIONS = [
    "Do not execute audited producers from the dashboard or this static audit CLI.",
    "Do not dynamically import audited producer entrypoints during this audit.",
    "Do not disable the kill switch to refresh runtime freshness evidence.",
    "Do not enable live, canary, private exchange access, or order submission.",
    "Do not change risk, models, datasets, signals, YAML configuration, or notifications.",
    "Do not infer operational release from a passing static safety audit.",
]

NETWORK_IMPORTS = {"requests", "httpx", "aiohttp", "websocket", "websockets"}
SUBPROCESS_IMPORTS = {"subprocess"}
SUBPROCESS_CALLS = {"popen", "run", "call", "check_call", "check_output", "system"}
ORDER_CALLS = {
    "create_order",
    "create_market_order",
    "create_limit_order",
    "place_order",
    "submit_order",
    "cancel_order",
}
PRIVATE_EXCHANGE_NAMES = {
    "fetch_balance",
    "fetch_open_orders",
    "private",
    "private_get",
    "private_post",
}
UNSAFE_TRUE_NAMES = {
    "live_trading_enabled",
    "live_release_allowed",
    "canary_release_allowed",
    "order_submission_enabled",
    "real_order_submission_enabled",
    "exchange_private_access",
    "sends_orders",
    "uses_private_exchange",
}
UNSAFE_PATH_PATTERNS = {
    ".env": "secret_env_mutation",
    "config/": "yaml_config_mutation",
    "config\\": "yaml_config_mutation",
    ".yml": "yaml_config_mutation",
    ".yaml": "yaml_config_mutation",
    "data/models": "model_mutation",
    "data\\models": "model_mutation",
    "data/datasets": "dataset_mutation",
    "data\\datasets": "dataset_mutation",
    "data/features": "dataset_mutation",
    "data\\features": "dataset_mutation",
    "data/signals": "active_signal_mutation",
    "data\\signals": "active_signal_mutation",
    "active_signals": "active_signal_mutation",
}
ALLOWED_WRITE_PREFIXES = ("data/reports/", "data/runtime/")


@dataclass(frozen=True)
class EntrypointStaticSafetyContract:
    contract_id: str
    producer_id: str
    domain: str
    entrypoint_path: str
    expected_output_path: str
    expected_cli_flags: tuple[str, ...]
    required_safe_literals: tuple[str, ...]
    forbidden_cli_patterns: tuple[str, ...]
    allowed_write_prefixes: tuple[str, ...]
    requires_manual_operator: bool = True


CANONICAL_ENTRYPOINT_CONTRACTS: dict[str, EntrypointStaticSafetyContract] = {
    "market_data_health_audit": EntrypointStaticSafetyContract(
        contract_id="market_data_health_manual_refresh_v1",
        producer_id="market_data_health_audit",
        domain="market_data",
        entrypoint_path="scripts/run_market_data_health_audit.py",
        expected_output_path="data/reports/market_data_health_audit_report.json",
        expected_cli_flags=(
            "--runtime-candles",
            "--ticker",
            "--order-book",
            "--trades",
            "--rest-snapshot",
            "--ws-heartbeat",
            "--report",
            "--strict",
        ),
        required_safe_literals=(
            "data/reports/market_data_health_audit_report.json",
            "--report data/reports/market_data_health_audit_report.json",
        ),
        forbidden_cli_patterns=(),
        allowed_write_prefixes=("data/reports/",),
    ),
    "kill_switch_state_refresh": EntrypointStaticSafetyContract(
        contract_id="kill_switch_runtime_manual_refresh_v1",
        producer_id="kill_switch_state_refresh",
        domain="portfolio_risk",
        entrypoint_path="scripts/set_kill_switch.py",
        expected_output_path="data/runtime/kill_switch.json",
        expected_cli_flags=("--enabled", "--reason", "--path"),
        required_safe_literals=(
            "--enabled true",
            "--path data/runtime/kill_switch.json",
            "data/runtime/kill_switch.json",
        ),
        forbidden_cli_patterns=(
            "--enabled false",
            "--enabled=false",
            "enabled=false",
            '"enabled": false',
            "'enabled': false",
        ),
        allowed_write_prefixes=("data/runtime/",),
    ),
    "runtime_safety_config_validation": EntrypointStaticSafetyContract(
        contract_id="runtime_safety_config_manual_validation_v1",
        producer_id="runtime_safety_config_validation",
        domain="active_controls",
        entrypoint_path="scripts/validate_runtime_safety_config.py",
        expected_output_path="data/runtime/runtime_safety_audit_config.json",
        expected_cli_flags=("--config", "--environment", "--report", "--strict"),
        required_safe_literals=(
            "--environment paper",
            "--report data/runtime/runtime_safety_audit_config.json",
            "data/runtime/runtime_safety_audit_config.json",
        ),
        forbidden_cli_patterns=(
            "--environment live",
            "--environment canary",
            "live_trading_enabled=true",
            "order_submission_enabled=true",
        ),
        allowed_write_prefixes=("data/runtime/",),
    ),
}


def audit_runtime_freshness_producer_entrypoint_static_safety(
    *,
    project_root: Path,
    now_utc: datetime,
    producer_contracts: Mapping[str, Any] | None = None,
    input_errors: Sequence[str] = (),
) -> dict[str, Any]:
    current = _ensure_utc(now_utc)
    loaded_contracts = dict(producer_contracts or {})
    fallback_used = False
    normalized_input_errors = sorted({str(error) for error in input_errors if error})
    if not _mapping_rows(loaded_contracts.get("producer_contracts")):
        loaded_contracts = _load_mapping(project_root / CONTRACTS_REPORT)
    if not _mapping_rows(loaded_contracts.get("producer_contracts")):
        fallback_used = True
        loaded_contracts = _canonical_contract_payload(current)

    contracts = _entrypoint_contracts_from_payload(loaded_contracts)
    rows = [_audit_entrypoint(project_root, contract, loaded_contracts) for contract in contracts]
    missing_entrypoints = [
        str(row["entrypoint_path"]) for row in rows if row["exists"] is not True
    ]
    missing_cli_flags = sorted(
        {
            f"{row['producer_id']}:{flag}"
            for row in rows
            for flag in _string_list(row.get("missing_cli_flags"))
        }
    )
    forbidden_findings = _findings_with_prefix(rows, "forbidden")
    unsafe_write_findings = _findings_with_prefix(rows, "unsafe_write")
    network_findings = _findings_with_prefix(rows, "network")
    subprocess_findings = _findings_with_prefix(rows, "subprocess")
    private_exchange_findings = _findings_with_prefix(rows, "private_exchange")
    blocked_total = sum(1 for row in rows if row["status"] == "blocked")
    warning_total = sum(1 for row in rows if row["status"] == "warning")
    ok_total = sum(1 for row in rows if row["status"] == "ok")

    if blocked_total or normalized_input_errors:
        status = "blocked"
        reason = "entrypoint_static_safety_blocked"
    elif fallback_used:
        status = "warning"
        reason = "canonical_contract_fallback_used"
    elif warning_total:
        status = "warning"
        reason = "entrypoint_static_safety_warning"
    else:
        status = "ok"
        reason = "entrypoint_static_safety_ok"

    payload = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "reason": reason,
        "generated_at_utc": iso_utc(current),
        "contracts_source": (
            "canonical_internal_fallback"
            if fallback_used
            else "runtime_freshness_producer_contracts"
        ),
        "entrypoints_total": len(rows),
        "entrypoints_ok_total": ok_total,
        "entrypoints_warning_total": warning_total,
        "entrypoints_blocked_total": blocked_total,
        "entrypoint_rows": rows,
        "missing_entrypoints": sorted(missing_entrypoints),
        "missing_cli_flags": missing_cli_flags,
        "forbidden_findings": forbidden_findings,
        "unsafe_write_findings": unsafe_write_findings,
        "network_findings": network_findings,
        "subprocess_findings": subprocess_findings,
        "private_exchange_findings": private_exchange_findings,
        "input_errors": normalized_input_errors,
        "manual_execution_only": True,
        "forbidden_actions": list(FORBIDDEN_ACTIONS),
        "safety_flags": _static_safety_flags(),
    }
    return json_safe(payload)


def load_runtime_freshness_producer_entrypoint_static_safety_inputs(
    project_root: Path,
) -> dict[str, Any]:
    reports = project_root / "data/reports"
    summary = _load_mapping(reports / "dashboard_snapshot_build_summary.json")
    global_snapshot = _load_mapping(reports / "dashboard_global_status_snapshot.json")
    contracts_report = _load_mapping(project_root / CONTRACTS_REPORT)
    embedded = _embedded_payload(
        global_snapshot,
        summary,
        "runtime_freshness_producer_contracts",
    )
    contracts = _latest_payload(contracts_report, embedded)
    return {"producer_contracts": contracts, "input_errors": []}


def _audit_entrypoint(
    project_root: Path,
    contract: EntrypointStaticSafetyContract,
    contracts_payload: Mapping[str, Any],
) -> dict[str, Any]:
    path = project_root / contract.entrypoint_path
    exists = path.is_file()
    source = ""
    tree: ast.AST | None = None
    parseable = False
    static_findings: list[str] = []
    if exists:
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=contract.entrypoint_path)
            parseable = True
        except (OSError, UnicodeError, SyntaxError) as exc:
            static_findings.append(f"forbidden:invalid_python:{type(exc).__name__}")

    contract_text = _contract_text(contracts_payload, contract)
    combined_text = f"{contract_text}\n{source}"
    expected_cli_flags_present = [
        flag for flag in contract.expected_cli_flags if flag in source
    ]
    missing_cli_flags = [
        flag for flag in contract.expected_cli_flags if flag not in source
    ]
    required_missing = [
        literal
        for literal in contract.required_safe_literals
        if literal.lower() not in combined_text.lower()
    ]
    forbidden_cli = _forbidden_contract_patterns(contract, contract_text)
    expected_output_allowed = _path_allowed(
        contract.expected_output_path, contract.allowed_write_prefixes
    )
    output_path_supported = (
        expected_output_allowed
        and contract.expected_output_path.lower() in combined_text.lower()
    )

    detections = _static_detections(tree, source) if tree is not None else {}
    detection_findings = _detection_findings(detections)
    static_findings.extend(detection_findings)
    static_findings.extend(
        f"forbidden:missing_required_safe_literal:{literal}"
        for literal in required_missing
    )
    static_findings.extend(f"forbidden:contract_cli_pattern:{item}" for item in forbidden_cli)
    if not expected_output_allowed:
        static_findings.append(
            f"unsafe_write:expected_output_outside_allowed_prefix:{contract.expected_output_path}"
        )
    if exists is False:
        static_findings.append(f"forbidden:missing_entrypoint:{contract.entrypoint_path}")
    if missing_cli_flags:
        static_findings.extend(f"forbidden:missing_cli_flag:{flag}" for flag in missing_cli_flags)
    if not output_path_supported:
        static_findings.append(
            f"forbidden:expected_output_path_not_supported:{contract.expected_output_path}"
        )

    critical = bool(static_findings)
    if critical:
        status = "blocked"
        reason = "static_entrypoint_contract_or_safety_violation"
    else:
        status = "ok"
        reason = "static_entrypoint_contract_compatible"

    return {
        "entrypoint_id": f"entrypoint_static_safety:{contract.producer_id}",
        "contract_id": contract.contract_id,
        "producer_id": contract.producer_id,
        "domain": contract.domain,
        "entrypoint_path": contract.entrypoint_path,
        "exists": exists,
        "parseable_python": parseable,
        "cli_compatible": exists and parseable and not missing_cli_flags,
        "expected_cli_flags_present": expected_cli_flags_present,
        "missing_cli_flags": missing_cli_flags,
        "expected_output_path": contract.expected_output_path,
        "output_path_supported": output_path_supported,
        "unsafe_write_detected": _has_finding(static_findings, "unsafe_write"),
        "network_usage_detected": bool(detections.get("network_usage_detected")),
        "subprocess_usage_detected": bool(detections.get("subprocess_usage_detected")),
        "private_exchange_usage_detected": bool(
            detections.get("private_exchange_usage_detected")
        ),
        "order_submission_detected": bool(detections.get("order_submission_detected")),
        "risk_mutation_detected": bool(detections.get("risk_mutation_detected")),
        "model_mutation_detected": bool(detections.get("model_mutation_detected")),
        "dataset_mutation_detected": bool(detections.get("dataset_mutation_detected")),
        "yaml_config_mutation_detected": bool(
            detections.get("yaml_config_mutation_detected")
        ),
        "live_or_canary_enable_detected": bool(
            detections.get("live_or_canary_enable_detected")
        ),
        "kill_switch_disable_detected": bool(
            contract.producer_id == "kill_switch_state_refresh" and forbidden_cli
        )
        or bool(detections.get("kill_switch_disable_detected")),
        "static_findings": sorted(set(static_findings)),
        "status": status,
        "reason": reason,
        "requires_manual_operator": contract.requires_manual_operator,
        "execution_allowed": False,
        "safe_to_execute_from_dashboard": False,
        "changes_runtime": False,
        "changes_risk": False,
        "changes_model": False,
        "sends_orders": False,
        "sends_notifications": False,
    }


def _entrypoint_contracts_from_payload(
    payload: Mapping[str, Any],
) -> list[EntrypointStaticSafetyContract]:
    rows = _mapping_rows(payload.get("producer_contracts"))
    row_by_producer = {str(row.get("producer_id", "")): row for row in rows}
    contracts: list[EntrypointStaticSafetyContract] = []
    for producer_id, canonical in CANONICAL_ENTRYPOINT_CONTRACTS.items():
        row = row_by_producer.get(producer_id, {})
        expected_output = str(
            row.get("expected_artifact_path")
            or row.get("target_canonical_path")
            or canonical.expected_output_path
        )
        contracts.append(
            EntrypointStaticSafetyContract(
                contract_id=str(row.get("contract_id") or canonical.contract_id),
                producer_id=canonical.producer_id,
                domain=str(row.get("domain") or canonical.domain),
                entrypoint_path=canonical.entrypoint_path,
                expected_output_path=expected_output,
                expected_cli_flags=canonical.expected_cli_flags,
                required_safe_literals=canonical.required_safe_literals,
                forbidden_cli_patterns=canonical.forbidden_cli_patterns,
                allowed_write_prefixes=canonical.allowed_write_prefixes,
            )
        )
    return contracts


def _static_detections(tree: ast.AST, source: str) -> dict[str, bool]:
    detections = {
        "network_usage_detected": False,
        "subprocess_usage_detected": False,
        "private_exchange_usage_detected": False,
        "order_submission_detected": False,
        "risk_mutation_detected": False,
        "model_mutation_detected": False,
        "dataset_mutation_detected": False,
        "yaml_config_mutation_detected": False,
        "live_or_canary_enable_detected": False,
        "kill_switch_disable_detected": False,
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots = {alias.name.split(".", maxsplit=1)[0] for alias in node.names}
            detections["network_usage_detected"] |= bool(roots & NETWORK_IMPORTS)
            detections["subprocess_usage_detected"] |= bool(roots & SUBPROCESS_IMPORTS)
            detections["private_exchange_usage_detected"] |= "ccxt" in roots
        elif isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.split(".", maxsplit=1)[0]
            detections["network_usage_detected"] |= root in NETWORK_IMPORTS
            detections["subprocess_usage_detected"] |= root in SUBPROCESS_IMPORTS
            detections["private_exchange_usage_detected"] |= root == "ccxt"
        elif isinstance(node, ast.Call):
            call_name = _call_name(node.func)
            detections["subprocess_usage_detected"] |= call_name in SUBPROCESS_CALLS
            detections["subprocess_usage_detected"] |= any(
                keyword.arg == "shell" and _literal_bool(keyword.value) is True
                for keyword in node.keywords
            )
            detections["order_submission_detected"] |= call_name in ORDER_CALLS
            detections["private_exchange_usage_detected"] |= call_name in PRIVATE_EXCHANGE_NAMES
            if call_name == "set_kill_switch" and _first_call_arg_false(node):
                detections["kill_switch_disable_detected"] = True
            for path in _string_constants(node):
                _apply_path_detection(detections, path, writing=_call_writes(node))
        elif isinstance(node, ast.Assign | ast.AnnAssign):
            names = _assigned_names(node)
            value = _assignment_value(node)
            if names & UNSAFE_TRUE_NAMES and _literal_bool(value) is True:
                detections["live_or_canary_enable_detected"] = True
            if "enabled" in names and _literal_bool(value) is False:
                detections["kill_switch_disable_detected"] = True
    lower = source.lower()
    detections["live_or_canary_enable_detected"] |= any(
        re.search(rf"\b{name}\b\s*[:=]\s*true", lower)
        for name in UNSAFE_TRUE_NAMES
    )
    detections["order_submission_detected"] |= any(
        f".{name}(" in lower or f"{name}(" in lower for name in ORDER_CALLS
    )
    return detections


def _apply_path_detection(
    detections: dict[str, bool], path_literal: str, *, writing: bool
) -> None:
    if not writing:
        return
    normalized = path_literal.replace("\\", "/").lower()
    if not normalized:
        return
    category = next(
        (value for pattern, value in UNSAFE_PATH_PATTERNS.items() if pattern in normalized),
        "",
    )
    if category == "yaml_config_mutation":
        detections["yaml_config_mutation_detected"] = True
    elif category == "model_mutation":
        detections["model_mutation_detected"] = True
    elif category == "dataset_mutation":
        detections["dataset_mutation_detected"] = True
    elif category == "active_signal_mutation":
        detections["risk_mutation_detected"] = True


def _detection_findings(detections: Mapping[str, bool]) -> list[str]:
    mapping = {
        "network_usage_detected": "network:usage_detected",
        "subprocess_usage_detected": "subprocess:usage_detected",
        "private_exchange_usage_detected": "private_exchange:usage_detected",
        "order_submission_detected": "forbidden:order_submission_detected",
        "risk_mutation_detected": "unsafe_write:risk_mutation_detected",
        "model_mutation_detected": "unsafe_write:model_mutation_detected",
        "dataset_mutation_detected": "unsafe_write:dataset_mutation_detected",
        "yaml_config_mutation_detected": "unsafe_write:yaml_config_mutation_detected",
        "live_or_canary_enable_detected": "forbidden:live_canary_or_order_flag_true",
        "kill_switch_disable_detected": "forbidden:kill_switch_disable_detected",
    }
    return [finding for key, finding in mapping.items() if bool(detections.get(key))]


def _contract_text(
    payload: Mapping[str, Any], contract: EntrypointStaticSafetyContract
) -> str:
    rows = _mapping_rows(payload.get("producer_contracts"))
    row = next(
        (
            candidate
            for candidate in rows
            if str(candidate.get("producer_id", "")) == contract.producer_id
        ),
        {},
    )
    values: list[str] = []
    for key in (
        "manual_execution_hint",
        "expected_artifact_path",
        "target_canonical_path",
        "verification_command",
    ):
        value = row.get(key)
        if value not in (None, ""):
            values.append(str(value))
    verification_commands = row.get("verification_commands")
    if isinstance(verification_commands, list):
        values.extend(str(item) for item in verification_commands if item)
    return "\n".join(values)


def _forbidden_contract_patterns(
    contract: EntrypointStaticSafetyContract,
    contract_text: str,
) -> list[str]:
    normalized = " ".join(contract_text.lower().split())
    findings = [
        pattern
        for pattern in contract.forbidden_cli_patterns
        if pattern.lower() in normalized
    ]
    if contract.producer_id == "kill_switch_state_refresh":
        enabled_true = "--enabled true" in normalized or "--enabled=true" in normalized
        if not enabled_true:
            findings.append("missing_required_enabled_true")
    return sorted(set(findings))


def _canonical_contract_payload(now_utc: datetime) -> dict[str, Any]:
    return {
        "schema_version": "canonical_internal_runtime_freshness_producer_contracts",
        "status": "warning",
        "reason": "runtime_contract_report_absent_canonical_fallback",
        "generated_at_utc": iso_utc(now_utc),
        "producer_contracts": [
            {
                "contract_id": contract.contract_id,
                "producer_id": contract.producer_id,
                "domain": contract.domain,
                "target_canonical_path": contract.expected_output_path,
                "expected_artifact_path": contract.expected_output_path,
                "manual_execution_hint": "python "
                + contract.entrypoint_path
                + " "
                + " ".join(contract.required_safe_literals),
                "requires_manual_operator": True,
                "execution_allowed": False,
                "safe_to_execute_from_dashboard": False,
            }
            for contract in CANONICAL_ENTRYPOINT_CONTRACTS.values()
        ],
    }


def _static_safety_flags() -> dict[str, bool]:
    flags = dict(SAFETY_FLAGS)
    flags.update(
        {
            "dashboard_readonly": True,
            "live_release_allowed": False,
            "canary_release_allowed": False,
            "exchange_private_access": False,
            "uses_network": False,
            "changes_runtime": False,
            "sends_notifications": False,
        }
    )
    return flags


def _embedded_payload(
    global_snapshot: Mapping[str, Any], summary: Mapping[str, Any], key: str
) -> dict[str, Any]:
    for payload in (global_snapshot, summary):
        direct = payload.get(key)
        if isinstance(direct, Mapping):
            return dict(direct)
        sections = payload.get("sections")
        if isinstance(sections, Mapping):
            section = sections.get(key)
            if isinstance(section, Mapping) and isinstance(section.get("data"), Mapping):
                return dict(section["data"])
    return {}


def _latest_payload(*payloads: Mapping[str, Any]) -> dict[str, Any]:
    available = [dict(payload) for payload in payloads if payload]
    if not available:
        return {}
    return max(available, key=_payload_timestamp)


def _payload_timestamp(payload: Mapping[str, Any]) -> datetime:
    value = payload.get("generated_at_utc")
    if value in (None, ""):
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    return _ensure_utc(parsed)


def _load_mapping(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return dict(payload) if isinstance(payload, Mapping) else {}


def _mapping_rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(row) for row in value if isinstance(row, Mapping)]


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item]


def _findings_with_prefix(rows: Sequence[Mapping[str, Any]], prefix: str) -> list[str]:
    findings: list[str] = []
    for row in rows:
        producer_id = str(row.get("producer_id", "unknown"))
        for finding in _string_list(row.get("static_findings")):
            if finding.startswith(prefix):
                findings.append(f"{producer_id}:{finding}")
    return sorted(set(findings))


def _has_finding(findings: Sequence[str], prefix: str) -> bool:
    return any(finding.startswith(prefix) for finding in findings)


def _path_allowed(path: str, allowed_prefixes: Sequence[str]) -> bool:
    normalized = path.replace("\\", "/").lstrip("./")
    return any(normalized.startswith(prefix) for prefix in allowed_prefixes)


def _call_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id.lower()
    if isinstance(node, ast.Attribute):
        return node.attr.lower()
    return ""


def _call_writes(node: ast.Call) -> bool:
    call_name = _call_name(node.func)
    if call_name in {"write_text", "write_bytes", "dump", "dumps", "to_csv", "to_json"}:
        return True
    if call_name == "open":
        mode_values = [
            value
            for value in (_string_constant(arg) for arg in node.args[1:2])
            if value is not None
        ]
        mode_values.extend(
            value
            for keyword in node.keywords
            if keyword.arg == "mode"
            for value in [_string_constant(keyword.value)]
            if value is not None
        )
        return any(any(character in mode for character in ("w", "a", "+")) for mode in mode_values)
    return False


def _first_call_arg_false(node: ast.Call) -> bool:
    return bool(node.args) and _literal_bool(node.args[0]) is False


def _literal_bool(node: ast.AST | None) -> bool | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, bool):
        return node.value
    return None


def _assignment_value(node: ast.Assign | ast.AnnAssign) -> ast.AST | None:
    return node.value if isinstance(node, ast.AnnAssign) else node.value


def _assigned_names(node: ast.Assign | ast.AnnAssign) -> set[str]:
    targets = [node.target] if isinstance(node, ast.AnnAssign) else list(node.targets)
    names: set[str] = set()
    for target in targets:
        if isinstance(target, ast.Name):
            names.add(target.id.lower())
        elif isinstance(target, ast.Attribute):
            names.add(target.attr.lower())
        elif isinstance(target, ast.Subscript):
            key = _string_constant(target.slice)
            if key:
                names.add(key.lower())
    return names


def _string_constants(node: ast.AST) -> list[str]:
    values: list[str] = []
    for child in ast.walk(node):
        value = _string_constant(child)
        if value is not None:
            values.append(value)
    return values


def _string_constant(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
