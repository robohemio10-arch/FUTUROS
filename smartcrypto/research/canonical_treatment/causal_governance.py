"""Read-only runtime audit and create-once registration of the Paper A/B cohort.

Docker is used only for inspect and bounded reads. No strategy, exchange client,
publisher or monitor is imported or executed in the active containers.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re

# Bounded Docker/Git reads use argument lists and never invoke a shell.
import subprocess  # nosec B404
import tempfile
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from smartcrypto.learning.qlib_v3_prospective.activation import (
    load_activation,
    parse_json,
    read_object,
    safe_path,
)
from smartcrypto.learning.qlib_v3_prospective.contracts import (
    CANONICAL,
    SAFETY,
    EvidenceError,
    check_identity,
    digest,
    utc,
)
from smartcrypto.learning.qlib_v3_prospective.store import exclusive
from smartcrypto.research.canonical_treatment.publisher import _write_json

AUDIT_SCHEMA = "canonical_treatment_runtime_parity_audit_v1"
MANIFEST_SCHEMA = "canonical_treatment_causal_activation_manifest_v1"
AUDIT_PATH = Path("data/reports/canonical_treatment/runtime_parity_audit_v1.json")
MANIFEST_PATH = Path("data/research/canonical_treatment/causal_activation_manifest_v1.json")
ALLOWED_CONFIG_DIFFERENCES = (
    "bot_name",
    "db_url",
    "logfile",
    "logfile_path",
    "signal_source_path",
    "signal_output_path",
    "api_server_if_disabled",
)
ECONOMIC_SETTINGS = (
    "dry_run",
    "dry_run_wallet",
    "trading_mode",
    "margin_mode",
    "liquidation_buffer",
    "futures_funding_rate",
    "stake_currency",
    "stake_amount",
    "tradable_balance_ratio",
    "max_open_trades",
    "timeframe",
    "exchange.name",
    "exchange.pair_whitelist",
    "exchange.pair_blacklist",
    "pairlists",
    "entry_pricing",
    "exit_pricing",
    "order_types",
    "order_time_in_force",
    "unfilledtimeout",
    "minimal_roi",
    "stoploss",
    "use_exit_signal",
    "exit_profit_only",
    "exit_profit_offset",
    "ignore_roi_if_entry_signal",
    "position_adjustment_enable",
    "force_entry_enable",
    "trailing_stop",
    "trailing_stop_positive",
    "trailing_stop_positive_offset",
    "trailing_only_offset_is_reached",
    "process_only_new_candles",
    "cancel_open_orders_on_exit",
    "fee",
    "leverage",
    "protections",
)
POLICY_VERSION = "economic_projection_live_spec_v3"
SAFETY_ENV = (
    "SMARTCRYPTO_RUNTIME_MODE",
    "LIVE_ENABLED",
    "ORDER_SUBMISSION_ENABLED",
    "REAL_ORDER_SUBMISSION_ENABLED",
    "SMARTCRYPTO_EXCHANGE_PRIVATE_ACCESS",
)
JOIN_CONTRACT = {
    "population": "sealed_phase13_operational_final_ALLOW",
    "join": [
        "operational_event_id_and_payload_sha256",
        "v3_operational_crosswalk",
        "treatment_decision_ledger",
        "sqlite_enter_tag_decision_event_id",
    ],
    "cohort": "decision_timestamp_gte_formal_activation_utc",
    "trade_source": "two_distinct_active_freqtrade_sqlite_databases",
    "coverage_denominator": "all_causal_operational_eligible_decisions",
    "additional_execution_stress_bps": 5.0,
    "prospective_only": True,
    "backfill_allowed": False,
}
CONTAINERS = {
    "control": "futuros-freqtrade-paper-1",
    "treatment": "futuros-canonical-treatment-freqtrade-canonical-treatment-paper-1",
    "publisher": "futuros-canonical-treatment-canonical-treatment-signal-publisher-1",
    "monitor": "futuros-canonical-treatment-canonical-treatment-economic-monitor-1",
}
MAX_AUDIT_AGE_SECONDS = 300
_FALSE_ENV = (
    "LIVE_ENABLED",
    "ORDER_SUBMISSION_ENABLED",
    "REAL_ORDER_SUBMISSION_ENABLED",
    "SMARTCRYPTO_EXCHANGE_PRIVATE_ACCESS",
)


def now_utc() -> datetime:
    return datetime.now(UTC)


def command(args: list[str]) -> bytes:
    """Never propagate subprocess output: it can contain private configuration."""
    try:
        # Callers supply fixed Docker/Git operations and pass paths as separate arguments.
        result = subprocess.run(args, capture_output=True, check=False, timeout=45)  # nosec B603
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EvidenceError("runtime_read_command_unavailable") from exc
    if result.returncode:
        raise EvidenceError("runtime_read_command_failed")
    return result.stdout


# This literal is executed with python -B; it only reads files / SQLite.
_READ_PROGRAM = r"""
import hashlib,json,pathlib,sqlite3,sys
request=json.loads(sys.argv[1]); mode=request['mode']; p=pathlib.Path(request['path'])
def raw(path):
    with path.open('rb') as f:
        data=f.read(128*1024*1024+1)
    if len(data)>128*1024*1024: raise ValueError('source_size_limit')
    return data
def fingerprint(path):
    before=path.stat(); h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''): h.update(chunk)
    after=path.stat()
    if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
        raise ValueError('source_changed_during_read')
    return {'sha256':h.hexdigest(),'mtime':after.st_mtime}
if mode=='optional_file':
    try: p.stat()
    except FileNotFoundError: print('null')
    else: sys.stdout.buffer.write(raw(p))
elif mode=='file':
    sys.stdout.buffer.write(raw(p))
elif mode=='files':
    paths=sorted(x for x in p.rglob('*') if x.is_file() and '__pycache__' not in x.parts and x.suffix!='.pyc') if p.is_dir() else [p]
    print(json.dumps({str(x.relative_to(p)) if p.is_dir() else p.name:fingerprint(x) for x in paths}))
elif mode=='sqlite':
    db=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True)
    db.row_factory=sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    try:
        rows=db.execute('SELECT id,is_open,open_date,close_date,enter_tag,pair,is_short,close_profit_abs,stake_amount,leverage FROM trades ORDER BY id').fetchall()
        print(json.dumps([dict(r) for r in rows],allow_nan=False))
    finally: db.close()
else: raise ValueError('unsupported_read_mode')
"""


def container_read(container: str, path: str, mode: str = "file") -> bytes:
    return command(
        [
            "docker",
            "exec",
            container,
            "python",
            "-B",
            "-c",
            _READ_PROGRAM,
            json.dumps({"mode": mode, "path": path}),
        ]
    )


def container_json(container: str, path: str) -> dict[str, Any]:
    result = parse_json(container_read(container, path))
    if not isinstance(result, dict):
        raise EvidenceError("runtime_json_object_required")
    return result


def container_optional_json(container: str, path: str) -> dict[str, Any] | None:
    result = parse_json(container_read(container, path, "optional_file"))
    if result is not None and not isinstance(result, dict):
        raise EvidenceError("runtime_optional_json_object_required")
    return result


def argument(args: list[str], flag: str) -> str:
    if args.count(flag) != 1 or args.index(flag) + 1 >= len(args):
        raise EvidenceError("runtime_argument_missing_or_ambiguous:" + flag)
    return args[args.index(flag) + 1]


def host_path(value: str) -> Path:
    value = value.replace("\\", "/")
    prefix = "/run/desktop/mnt/host/"
    if value.startswith(prefix):
        value = value[len(prefix)] + ":/" + value[len(prefix) + 2 :]
    return Path(value)


def _mount(info: Mapping[str, Any], destination: str) -> dict[str, Any]:
    matches = [m for m in info["Mounts"] if m["Destination"] == destination]
    if len(matches) != 1:
        raise EvidenceError("required_mount_missing_or_ambiguous:" + destination)
    mount = matches[0]
    return {k: mount.get(k) for k in ("Type", "Name", "Source", "Destination", "RW")}


def _git(root: Path) -> dict[str, str]:
    return {
        name: command(["git", "--no-optional-locks", "-C", str(root), "rev-parse", ref])
        .decode()
        .strip()
        for name, ref in (("commit", "HEAD"), ("tree", "HEAD^{tree}"))
    }


def _flatten(value: object, prefix: str = "") -> dict[str, object]:
    if isinstance(value, dict) and value:
        return {
            path: item
            for key, child in value.items()
            for path, item in _flatten(child, f"{prefix}.{key}" if prefix else key).items()
        }
    return {prefix: value}


def config_differences(control: Mapping[str, Any], treatment: Mapping[str, Any]) -> list[str]:
    a, b = _flatten(dict(control)), _flatten(dict(treatment))
    return sorted(k for k in a.keys() | b.keys() if k not in a or k not in b or a[k] != b[k])


def economic_environment(environment: Mapping[str, str]) -> dict[str, str]:
    """Only Freqtrade overrides and explicit safety flags affect A/B parity.

    Unattested Freqtrade overrides still block collection; unrelated service variables
    are recorded separately and never compared to Control's financial environment.
    """
    return {k: v for k, v in environment.items() if k in SAFETY_ENV or k.startswith("FREQTRADE__")}


def economic_projection(
    config: Mapping[str, Any], strategy_defaults: Mapping[str, Any]
) -> dict[str, Any]:
    projection: dict[str, Any] = {}
    for key in ECONOMIC_SETTINGS:
        value: Any = config
        for part in key.split("."):
            if not isinstance(value, Mapping) or part not in value:
                value = strategy_defaults.get(
                    key, {"source": "freqtrade_image_default", "setting": key}
                )
                break
            value = value[part]
        if key in {"exchange.pair_whitelist", "exchange.pair_blacklist"} and isinstance(
            value, list
        ):
            if not all(isinstance(pair, str) for pair in value) or len(set(value)) != len(value):
                raise EvidenceError("invalid_pair_universe")
            value = sorted(value)
        projection[key] = value
    return projection


def strategy_contract(raw: bytes) -> tuple[dict[str, Any], list[str]]:
    """Read class declarations without importing or executing a strategy."""
    tree = ast.parse(raw)
    classes = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "SmartCryptoSignalStrategy"
    ]
    if len(classes) != 1:
        raise EvidenceError("active_signal_strategy_class_missing_or_ambiguous")
    defaults: dict[str, Any] = {}
    paths: list[str] = []
    for node in classes[0].body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            name, value = node.targets[0].id, node.value
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.value is not None
        ):
            name, value = node.target.id, node.value
        else:
            continue
        if name in ECONOMIC_SETTINGS:
            try:
                defaults[name] = ast.literal_eval(value)
            except (ValueError, TypeError) as exc:
                raise EvidenceError("strategy_economic_attribute_not_literal:" + name) from exc
        if name == "_signal_paths":
            if not isinstance(value, (ast.List, ast.Tuple)):
                raise EvidenceError("strategy_signal_paths_not_literal")
            for item in value.elts:
                if not (
                    isinstance(item, ast.Call)
                    and isinstance(item.func, ast.Name)
                    and item.func.id == "Path"
                    and len(item.args) == 1
                    and not item.keywords
                    and isinstance(item.args[0], ast.Constant)
                    and isinstance(item.args[0].value, str)
                ):
                    raise EvidenceError("strategy_signal_path_not_literal")
                paths.append(item.args[0].value)
    if not paths or paths[0] != "/freqtrade/user_data/data/runtime/active_freqtrade_signals.json":
        raise EvidenceError("strategy_primary_signal_path_unexpected")
    return defaults, paths


def _api_disabled(config: Mapping[str, Any]) -> bool:
    api = config.get("api_server", {})
    return isinstance(api, Mapping) and api.get("enabled", False) is False


def permitted_config_difference(key: str, a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    if key == "api_server" or key.startswith("api_server."):
        return _api_disabled(a) and _api_disabled(b)
    return key in ALLOWED_CONFIG_DIFFERENCES


def compose_sources(info: Mapping[str, Any]) -> tuple[dict[str, str], Path, list[str]]:
    labels = info["Config"]["Labels"]
    names = labels.get("com.docker.compose.project.config_files", "").split(",")
    root_name = labels.get("com.docker.compose.project.working_dir")
    if not root_name or not all(names):
        raise EvidenceError("active_compose_source_labels_missing")
    root = host_path(root_name)
    hashes, issues = {}, []
    for name in names:
        path = host_path(name)
        if not path.is_absolute():
            path = root / path
        try:
            hashes[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        except FileNotFoundError:
            issues.append("compose_label_source_missing:" + str(path))
        except OSError:
            issues.append("compose_label_source_unreadable:" + str(path))
    return hashes, root, issues


def compose_provenance(info: Mapping[str, Any]) -> tuple[dict[str, Any], Path]:
    """Read missing label sources from an immutable Git blob; never restore files."""
    hashes, root, missing = compose_sources(info)
    recovered: dict[str, dict[str, str]] = {}
    warnings: list[str] = []
    for reason in missing:
        prefix, name = reason.split(":", 1)
        path = Path(name)
        if prefix == "compose_label_source_missing" and path.resolve().is_relative_to(
            root.resolve()
        ):
            try:
                relative = path.resolve().relative_to(root.resolve()).as_posix()
                commit = _git(root)["commit"]
                ref = commit + ":" + relative
                raw = command(["git", "--no-optional-locks", "-C", str(root), "show", ref])
                blob = (
                    command(["git", "--no-optional-locks", "-C", str(root), "rev-parse", ref])
                    .decode()
                    .strip()
                )
                hashes[name] = hashlib.sha256(raw).hexdigest()
                recovered[name] = {"commit": commit, "git_blob": blob, "sha256": hashes[name]}
                # HEAD recovery proves the blob, not that it created the live container.
                warnings.append("compose_git_blob_recovered_live_origin_unproven:" + name)
                continue
            except (EvidenceError, OSError):
                warnings.append("compose_source_unrecovered:" + name)
        else:
            warnings.append(reason)
    return {
        "files_sha256": hashes,
        "git_recovery": recovered,
        "compose_source_recovered": len(missing) == len(recovered),
        "warnings": warnings,
        "runtime_authority": "live_container_spec_snapshot",
    }, root


def live_container_spec_snapshot(info: Mapping[str, Any]) -> dict[str, Any]:
    """Canonical inspect projection, with private Config/HostConfig values hashed.

    Raw environment, healthcheck commands, log options and arbitrary labels may
    contain credentials and must never be exported in evidence reports.
    """
    labels = info["Config"]["Labels"]
    required_labels = (
        "project",
        "service",
        "project.working_dir",
        "project.config_files",
        "config-hash",
    )
    names = ["com.docker.compose." + key for key in required_labels]
    if any(not labels.get(key) for key in names):
        raise EvidenceError("live_spec_compose_labels_incomplete")
    if (
        not info.get("Id")
        or not info.get("Image")
        or not info.get("Mounts")
        or not info.get("HostConfig")
    ):
        raise EvidenceError("live_container_spec_incomplete")
    return {
        "schema_version": "canonical_treatment_live_container_spec_v1",
        "container_id": info["Id"],
        "name": info["Name"],
        "image": info["Image"],
        "configured_image": info["Config"]["Image"],
        "configured_user": info["Config"]["User"],
        "config_sha256": digest(info["Config"]),
        "host_config_sha256": digest(info["HostConfig"]),
        "command_sha256": digest({"argv": info["Config"]["Cmd"]}),
        "compose_labels": {key: labels[key] for key in names},
        "mounts": [
            {key: mount.get(key) for key in ("Type", "Name", "Source", "Destination", "RW")}
            for mount in sorted(info["Mounts"], key=lambda m: m["Destination"])
        ],
    }


def image_identity_equivalent(control: Mapping[str, Any], treatment: Mapping[str, Any]) -> bool:
    if control.get("image") and control["image"] == treatment.get("image"):
        return True
    a, b = control.get("image_details", {}), treatment.get("image_details", {})
    digests = set(a.get("repo_digests", [])) & set(b.get("repo_digests", []))
    immutable = any(
        re.fullmatch(r"[A-Za-z0-9._:/-]+@sha256:[0-9a-f]{64}", value) for value in digests
    )
    return bool(
        a.get("id") == control.get("image")
        and b.get("id") == treatment.get("image")
        and a.get("platform")
        and a.get("platform") == b.get("platform")
        and immutable
    )


def _selector_fingerprint() -> dict[str, Any]:
    name = "futuros-canonical-observer-canonical-qlib-refresh-supervisor-paper-1"
    info = parse_json(command(["docker", "inspect", name]))[0]
    if (
        info["State"]["Status"] != "running"
        or info["State"].get("Health", {}).get("Status") != "healthy"
    ):
        raise EvidenceError("v3_selector_not_running_healthy")
    issues: list[str] = []
    environment_evidence_source = "proc_pid1"
    try:
        entries = container_read(name, "/proc/1/environ").decode().split("\0")
    except EvidenceError:
        # PID1 can be a root bootstrap while the observer worker runs non-root.
        # Inspect still identifies the instantiated container's environment.
        entries = info["Config"]["Env"]
        environment_evidence_source = "docker_inspect_config_env"
    env = dict(item.split("=", 1) for item in entries if "=" in item)
    if env.get("QLIB_V3_NATURAL_EVIDENCE_CONFIG") != "v3_certified/config/runtime.json":
        issues.append("v3_selector_active_config_mismatch")
    if any(env.get(k, "").lower() != "false" for k in _FALSE_ENV):
        issues.append("v3_selector_safety_unproven")
    config_raw = container_read(name, "/app/v3_certified/config/runtime.json")
    config = parse_json(config_raw)
    if config != {
        "enabled": True,
        "activation": "v3_certified/activation/activation.json",
        "freeze": "v3_certified/freeze/freeze_v3.json",
    }:
        raise EvidenceError("v3_selector_config_unexpected")
    activation_raw = container_read(name, "/app/" + config["activation"])
    freeze_raw = container_read(name, "/app/" + config["freeze"])
    # Reuse the certified validator on private temporary copies; sources remain read-only.
    with tempfile.TemporaryDirectory(prefix="b17-certified-read-") as directory:
        a, f = Path(directory) / "activation.json", Path(directory) / "freeze.json"
        a.write_bytes(activation_raw)
        f.write_bytes(freeze_raw)
        validated = load_activation(a, f)
    frozen_files = parse_json(container_read(name, "/app/v3_certified/freeze", "files"))
    frozen_hashes = {p: row["sha256"] for p, row in frozen_files.items()}
    for relative, artifact in parse_json(freeze_raw)["contract"]["artifacts"].items():
        if frozen_hashes.get(relative) != artifact["sha256"]:
            raise EvidenceError("v3_frozen_artifact_hash_mismatch")
    sources = parse_json(
        container_read(name, "/app/smartcrypto/learning/qlib_v3_prospective", "files")
    )
    started = datetime.fromisoformat(info["State"]["StartedAt"]).timestamp()
    if any(row["mtime"] > started for row in sources.values()):
        raise EvidenceError("v3_selector_source_changed_after_start")
    after = parse_json(command(["docker", "inspect", name]))[0]
    if (
        any(info[k] != after[k] for k in ("Id", "Image", "RestartCount"))
        or info["State"]["StartedAt"] != after["State"]["StartedAt"]
    ):
        raise EvidenceError("selector_changed_during_audit")
    return {
        "issues": issues,
        "environment_evidence_source": environment_evidence_source,
        "container_id": info["Id"],
        "image": info["Image"],
        "started_at": info["State"]["StartedAt"],
        "config_sha256": hashlib.sha256(config_raw).hexdigest(),
        "activation_file_sha256": validated.activation_file_sha256,
        "freeze_file_sha256": validated.freeze_file_sha256,
        "frozen_artifact_sha256": frozen_hashes,
        "source_sha256": {p: row["sha256"] for p, row in sources.items()},
        "mounts": [
            {k: m.get(k) for k in ("Type", "Name", "Source", "Destination", "RW")}
            for m in sorted(info["Mounts"], key=lambda m: m["Destination"])
        ],
    }


def audit_snapshots(
    snapshots: Mapping[str, dict[str, Any]],
    *,
    git: dict[str, str],
    generated: datetime | None = None,
) -> dict[str, Any]:
    """Pure validator; production snapshots come exclusively from collect_runtime."""
    runtime_issues: list[str] = []
    safety_issues: list[str] = []
    economic_issues: list[str] = []
    provenance_warnings: list[str] = []
    for role in CONTAINERS:
        item = snapshots[role]
        runtime_issues.extend(role + ":" + reason for reason in item.get("issues", []))
        provenance = item.get("compose_provenance", {})
        warnings = provenance.get("warnings", [])
        provenance_warnings.extend(role + ":" + reason for reason in warnings)
        if warnings or provenance.get("compose_source_recovered") is False:
            spec = item.get("live_container_spec_snapshot", {})
            if (
                not spec
                or spec.get("container_id") != item["container_id"]
                or spec.get("image") != item["image"]
                or item.get("live_container_spec_sha256") != digest(spec)
                or not spec.get("compose_labels")
                or not spec.get("mounts")
                or not spec.get("config_sha256")
                or not spec.get("host_config_sha256")
            ):
                runtime_issues.append(role + ":compose_gap_without_complete_live_spec")
        if item["status"] != "running" or item["health"] != "healthy":
            runtime_issues.append(role + ":not_running_healthy")
        env = item["safety_environment"]
        if env.get("SMARTCRYPTO_RUNTIME_MODE") != "paper" or any(
            str(env.get(k, "")).lower() != "false" for k in _FALSE_ENV
        ):
            safety_issues.append(role + ":paper_safety_unproven")
    a, b = snapshots["control"], snapshots["treatment"]
    differences = config_differences(a["config"], b["config"])
    allowed = [k for k in differences if permitted_config_difference(k, a["config"], b["config"])]
    projections = {
        role: economic_projection(
            snapshots[role]["config"], snapshots[role].get("strategy_defaults", {})
        )
        for role in ("control", "treatment")
    }
    economic_differences = config_differences(projections["control"], projections["treatment"])
    economic_issues.extend("economic_config_difference:" + k for k in economic_differences)
    # Unknown settings are not silently exempted. Non-economic exemptions are explicit.
    for key in differences:
        economic_key = any(key == k or key.startswith(k + ".") for k in ECONOMIC_SETTINGS)
        if key not in allowed and not economic_key:
            economic_issues.append("unclassified_config_difference:" + key)
    for role in ("control", "treatment"):
        config = snapshots[role]["config"]
        if config.get("dry_run") is not True or config.get("trading_mode") != "futures":
            safety_issues.append(role + ":dry_run_futures_required")
        if config.get("add_config_files"):
            runtime_issues.append(role + ":layered_configuration_not_attested")
        if any(config.get("exchange", {}).get(k) for k in ("key", "secret", "password", "uid")):
            safety_issues.append(role + ":exchange_credentials_present")
    image_parity = image_identity_equivalent(a, b)
    if not image_parity:
        economic_issues.append("runtime_difference:image")
    for key in ("strategy_sha256", "economic_environment_sha256", "economic_command"):
        if a[key] != b[key]:
            economic_issues.append("runtime_difference:" + key)
    if a["db_identity"] == b["db_identity"]:
        runtime_issues.append("control_treatment_database_not_isolated")
    signal_sources = [item.get("signal_source_identity", {}).get("source_host") for item in (a, b)]
    if not all(signal_sources) or signal_sources[0] == signal_sources[1]:
        runtime_issues.append("control_treatment_signal_source_not_isolated")
    issues = runtime_issues + safety_issues + economic_issues
    # Only sanitized identities and hashes are exported, never raw config/env.
    fingerprints = {
        role: {
            k: v
            for k, v in item.items()
            if k
            not in {
                "config",
                "safety_environment",
                "issues",
                "status",
                "health",
                "strategy_defaults",
            }
        }
        for role, item in snapshots.items()
    }
    result = {
        "schema_version": AUDIT_SCHEMA,
        "generated_at_utc": (generated or now_utc()).isoformat(),
        "status": "blocked" if issues else "ok",
        "parity_status": "BLOCKED" if issues else "PASS",
        "policy_version": POLICY_VERSION,
        "economic_parity": "BLOCKED" if economic_issues else "PASS",
        "image_parity": "PASS" if image_parity else "BLOCKED",
        "safety_parity": "BLOCKED" if safety_issues else "PASS",
        "strategy_hash_parity": "PASS"
        if a["strategy_sha256"] == b["strategy_sha256"]
        else "BLOCKED",
        "runtime_identity": {
            "status": "BLOCKED" if runtime_issues else "PASS",
            "fingerprints_sha256": digest(fingerprints),
        },
        "allowed_differences": allowed
        + ["isolated_treatment_signal_source_output", "isolated_databases"],
        "instrumentation_differences": {
            role: {
                "image": snapshots[role]["image"],
                "compared_to_freqtrade": False,
                "service_environment_sha256": snapshots[role].get("service_environment_sha256"),
            }
            for role in ("publisher", "monitor")
        },
        "economic_projection_sha256": {role: digest(value) for role, value in projections.items()},
        "blocking_differences": sorted(set(issues)),
        "provenance_warnings": sorted(set(provenance_warnings)),
        "issues": sorted(set(issues)),
        "git": git,
        "identity": CANONICAL.mapping(),
        "fingerprints": fingerprints,
        "fingerprints_sha256": digest(fingerprints),
        "allowed_config_differences": list(ALLOWED_CONFIG_DIFFERENCES),
        "observed_config_differences": differences,
        "join_contract": JOIN_CONTRACT,
        **SAFETY,
    }
    result["audit_sha256"] = digest(result)
    return result


def collect_runtime(runtime_root: Path) -> dict[str, dict[str, Any]]:
    """Inspect active process environment, commands, mounts and bytes in place."""
    inspected = parse_json(command(["docker", "inspect", *CONTAINERS.values()]))
    by_name = {item["Name"].lstrip("/"): item for item in inspected}
    snapshots: dict[str, dict[str, Any]] = {}
    for role, name in CONTAINERS.items():
        info = by_name[name]
        live_spec = live_container_spec_snapshot(info)
        image_info = parse_json(command(["docker", "image", "inspect", info["Image"]]))[0]
        if image_info["Id"] != info["Image"]:
            raise EvidenceError("active_image_inspection_mismatch")
        args = info["Config"]["Cmd"]
        issues: list[str] = []
        process_args = container_read(name, "/proc/1/cmdline").decode().rstrip("\0").split("\0")
        if not args or process_args[-len(args) :] != args:
            issues.append("active_process_command_mismatch")
        # Inspect's environment is checked against the running PID1, not trusted alone.
        process_env = container_read(name, "/proc/1/environ").decode().split("\0")
        env = dict(entry.split("=", 1) for entry in process_env if "=" in entry)
        configured = dict(entry.split("=", 1) for entry in info["Config"]["Env"] if "=" in entry)
        if economic_environment(env) != economic_environment(configured):
            issues.append("process_environment_drift")
        if role in {"control", "treatment"} and any(k.startswith("FREQTRADE__") for k in env):
            issues.append("freqtrade_environment_override_not_attested")
        compose, source_root = compose_provenance(info)
        try:
            source_git = _git(source_root)
        except EvidenceError:
            source_git = {}
            compose["warnings"].append("active_source_git_identity_unavailable")
        snapshots[role] = item = {
            "container_id": info["Id"],
            "image": info["Image"],
            "started_at": info["State"]["StartedAt"],
            "status": info["State"]["Status"],
            "health": info["State"].get("Health", {}).get("Status"),
            "git": source_git,
            "compose_sha256": compose["files_sha256"],
            "compose_provenance": compose,
            "live_container_spec_snapshot": live_spec,
            "live_container_spec_sha256": digest(live_spec),
            "image_details": {
                "id": image_info["Id"],
                "repo_digests": sorted(image_info.get("RepoDigests") or []),
                "platform": "/".join(
                    str(image_info.get(k, "")) for k in ("Os", "Architecture", "Variant")
                ),
            },
            "safety_environment": {
                k: env.get(k) for k in (*_FALSE_ENV, "SMARTCRYPTO_RUNTIME_MODE")
            },
            "economic_environment_sha256": digest(economic_environment(env)),
            "service_environment_sha256": digest(
                {k: v for k, v in env.items() if k not in SAFETY_ENV}
            ),
            "mounts": [
                {k: m.get(k) for k in ("Type", "Name", "Source", "Destination", "RW")}
                for m in sorted(info["Mounts"], key=lambda m: m["Destination"])
            ],
            "command_sha256": digest({"argv": args}),
            "issues": issues,
        }
        process_status = container_read(name, "/proc/1/status").decode()
        item["effective_process_identity"] = {
            line.split(":", 1)[0]: [int(value) for value in line.split(":", 1)[1].split()]
            for line in process_status.splitlines()
            if line.startswith(("Uid:", "Gid:", "Groups:"))
        }
        if not all(item["effective_process_identity"].get(key) for key in ("Uid", "Gid")):
            issues.append("process_uid_gid_unavailable")
        if role in {"control", "treatment"}:
            config_path = argument(args, "--config")
            config_raw = container_read(name, config_path)
            item["config"] = parse_json(config_raw)
            item["config_sha256"] = hashlib.sha256(config_raw).hexdigest()
            strategy_path = "/freqtrade/user_data/strategies/SmartCryptoSignalStrategy.py"
            strategy_raw = container_read(name, strategy_path)
            item["strategy_sha256"] = hashlib.sha256(strategy_raw).hexdigest()
            item["strategy_defaults"], item["signal_paths"] = strategy_contract(strategy_raw)
            paths = [
                config_path,
                "/freqtrade/user_data/strategies",
                "/freqtrade/user_data/freqtrade_paper_healthcheck.py",
            ]
            if any(_mount(info, path)["RW"] for path in paths):
                issues.append("financial_source_mount_not_readonly")
            db = _mount(info, "/freqtrade/user_data/db")
            item["db_identity"] = {
                "mount_type": db["Type"],
                "source": db["Name"] or db["Source"],
                "url": argument(args, "--db-url"),
            }
            economic_args = list(args)
            for flag in ("--config", "--logfile", "--db-url"):
                economic_args[economic_args.index(flag) + 1] = flag + "_role_specific_path"
            if economic_args != [
                "trade",
                "--config",
                "--config_role_specific_path",
                "--strategy",
                "SmartCryptoSignalStrategy",
                "--logfile",
                "--logfile_role_specific_path",
                "--db-url",
                "--db-url_role_specific_path",
            ]:
                issues.append("unattested_execution_arguments")
            item["economic_command"] = economic_args
            runtime = _mount(info, "/freqtrade/user_data/data/runtime")
            expected = runtime_root / "data/runtime"
            if role == "treatment":
                expected /= "canonical_treatment"
            if host_path(runtime["Source"]).resolve() != expected.resolve():
                issues.append("runtime_mount_source_mismatch")
            if role == "control" and runtime["RW"]:
                issues.append("control_signal_mount_not_readonly")
            item["signal_source_identity"] = {
                "source_host": str(host_path(runtime["Source"]).resolve()),
                "file": "active_freqtrade_signals.json",
                "mount_readonly": not runtime["RW"],
            }
            signal_path = item["signal_paths"][0]
            try:
                payload = container_optional_json(name, signal_path)
            except EvidenceError:
                issues.append("expected_signal_file_unreadable_or_invalid")
            else:
                if payload is None or not isinstance(payload.get("signals"), list):
                    issues.append("expected_signal_file_missing_or_invalid")
            # Any fallback with actionable signals could bypass the isolated selector.
            for fallback in item["signal_paths"][1:]:
                absolute = (
                    fallback
                    if fallback.startswith("/")
                    else info["Config"]["WorkingDir"].rstrip("/") + "/" + fallback
                )
                if absolute == signal_path:
                    continue
                alternative = container_optional_json(name, absolute)
                if alternative is not None and alternative.get("signals"):
                    issues.append("non_primary_signal_source_not_empty:" + absolute)
            for key in ("signal_source_path", "signal_output_path"):
                if key in item["config"] and item["config"][key] not in {
                    signal_path,
                    str(expected / "active_freqtrade_signals.json"),
                }:
                    issues.append("configured_signal_path_inconsistent:" + key)
        else:
            paths = [
                "/app/smartcrypto/research/canonical_treatment/"
                + ("publisher.py" if role == "publisher" else "economic_monitor.py"),
                "/app/" + args[1],
                "/app/scripts/canonical_treatment_report_healthcheck_v1.py",
            ]
            if (
                host_path(_mount(info, "/app/data")["Source"]).resolve()
                != (runtime_root / "data").resolve()
            ):
                issues.append("runtime_mount_source_mismatch")
        source_hashes = {}
        started = datetime.fromisoformat(info["State"]["StartedAt"]).timestamp()
        for path in paths:
            files = parse_json(container_read(name, path, "files"))
            if not files:
                issues.append("source_files_missing")
            if any(record["mtime"] > started for record in files.values()):
                issues.append("source_modified_after_process_start")
            source_hashes[path] = {k: v["sha256"] for k, v in files.items()}
            if path.endswith("/strategies"):
                item["strategy_hashes"] = source_hashes[path]
        item["source_sha256"] = source_hashes
    publisher = CONTAINERS["publisher"]
    args = by_name[publisher]["Config"]["Cmd"]
    store = container_json(publisher, argument(args, "--v3-evidence"))
    check_identity(store.get("identity"), CANONICAL)
    if store.get("store_sha256") != digest({k: v for k, v in store.items() if k != "store_sha256"}):
        raise EvidenceError("store_hash_mismatch")
    report = container_json(publisher, argument(args, "--report"))
    if (
        report.get("status") != "ok"
        or not 0 <= (now_utc() - utc(report.get("generated_at_utc"))).total_seconds() <= 20
    ):
        snapshots["publisher"]["issues"].append("publisher_report_stale_or_blocked")
    try:
        selector = _selector_fingerprint()
        snapshots["publisher"]["issues"].extend(selector.pop("issues"))
        snapshots["publisher"]["selector"] = selector
    except (EvidenceError, OSError) as exc:
        snapshots["publisher"]["issues"].append(
            "selector:" + str(exc)
            if isinstance(exc, EvidenceError)
            else "selector_source_unavailable"
        )
    # Cross-check that the monitor reads the very same two financial databases.
    monitor = by_name[CONTAINERS["monitor"]]
    for role, destination in (("control", "/control-db"), ("treatment", "/treatment-db")):
        mount = _mount(monitor, destination)
        db = snapshots[role]["db_identity"]
        if mount["RW"] or (mount["Name"] or mount["Source"]) != db["source"]:
            snapshots["monitor"]["issues"].append(role + "_database_mount_mismatch")
    after = parse_json(command(["docker", "inspect", *CONTAINERS.values()]))
    if digest({"inspect": inspected}) != digest({"inspect": after}):
        # Health log timestamps change normally; compare stable runtime fields only.
        for role, old, new in zip(CONTAINERS, inspected, after, strict=True):
            keys = ("Id", "Image", "Config", "RestartCount")
            mounts_changed = sorted(old["Mounts"], key=lambda m: m["Destination"]) != sorted(
                new["Mounts"], key=lambda m: m["Destination"]
            )
            if (
                any(old[k] != new[k] for k in keys)
                or mounts_changed
                or old["State"]["StartedAt"] != new["State"]["StartedAt"]
            ):
                snapshots[role]["issues"].append("runtime_changed_during_audit")
            if (
                new["State"]["Status"] != "running"
                or new["State"].get("Health", {}).get("Status") != "healthy"
            ):
                snapshots[role]["issues"].append("runtime_not_healthy_at_audit_end")
    return snapshots


def validate_audit(report: Mapping[str, Any], now: datetime) -> None:
    if report.get("schema_version") != AUDIT_SCHEMA or report.get("audit_sha256") != digest(
        {k: v for k, v in report.items() if k != "audit_sha256"}
    ):
        raise EvidenceError("parity_audit_hash_or_schema_invalid")
    if (
        report.get("parity_status") != "PASS"
        or report.get("status") != "ok"
        or report.get("issues") != []
    ):
        raise EvidenceError("runtime_parity_not_pass")
    if (
        report.get("policy_version") != POLICY_VERSION
        or any(
            report.get(k) != "PASS"
            for k in ("economic_parity", "safety_parity", "strategy_hash_parity", "image_parity")
        )
        or report.get("runtime_identity", {}).get("status") != "PASS"
        or report.get("blocking_differences") != []
    ):
        raise EvidenceError("runtime_parity_hard_gate_invalid")
    if (
        not 0
        <= (now - utc(report.get("generated_at_utc"))).total_seconds()
        <= MAX_AUDIT_AGE_SECONDS
    ):
        raise EvidenceError("runtime_parity_stale_or_future")
    if report.get("fingerprints_sha256") != digest(report.get("fingerprints", {})):
        raise EvidenceError("runtime_fingerprints_hash_invalid")
    if report.get("join_contract") != JOIN_CONTRACT or report.get(
        "allowed_config_differences"
    ) != list(ALLOWED_CONFIG_DIFFERENCES):
        raise EvidenceError("causal_policy_mismatch")
    check_identity(report.get("identity"), CANONICAL)
    if any(report.get(k) is not v for k, v in SAFETY.items()):
        raise EvidenceError("governance_safety_invalid")


def _docker_start_key(value: object) -> tuple[datetime, int]:
    """Compare Docker RFC3339Nano in UTC without truncating future nanoseconds."""
    match = re.fullmatch(
        r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(?:Z|\+00:00)",
        value if isinstance(value, str) else "",
    )
    if match is None:
        raise EvidenceError("selector_start_timestamp_invalid")
    return utc(match[1] + "Z"), int((match[2] or "").ljust(9, "0"))


def _same_runtime_after_selector_restart(
    registered: Mapping[str, Any], current: Mapping[str, Any],
    activation: datetime, observed: datetime,
) -> bool:
    """Allow only a later start of the same fully attested selector container."""
    if registered == current:
        return True
    old_publisher, new_publisher = registered.get("publisher"), current.get("publisher")
    if not isinstance(old_publisher, dict) or not isinstance(new_publisher, dict):
        return False
    old = old_publisher.get("selector", {})
    new = new_publisher.get("selector", {})
    if not isinstance(old, dict) or not isinstance(new, dict):
        return False
    required = {
        "container_id", "image", "config_sha256", "activation_file_sha256",
        "freeze_file_sha256", "frozen_artifact_sha256", "source_sha256", "mounts",
        "environment_evidence_source", "started_at",
    }
    if any(not selector.get(key) for selector in (old, new) for key in required):
        return False
    activated = (activation.replace(microsecond=0), activation.microsecond * 1000)
    attested = (observed.replace(microsecond=0), observed.microsecond * 1000)
    if not _docker_start_key(old["started_at"]) <= activated < _docker_start_key(new["started_at"]) <= attested:
        return False
    # Keep the raw timestamps in both sealed reports. Only this comparison
    # separates process lifetime from the selector's immutable causal identity.
    normalized = dict(current)
    normalized["publisher"] = dict(
        current["publisher"], selector=dict(new, started_at=old["started_at"])
    )
    return registered == normalized


def validate_manifest(manifest: Mapping[str, Any], audit: Mapping[str, Any], now: datetime) -> None:
    validate_audit(audit, now)
    if manifest.get("schema_version") != MANIFEST_SCHEMA or manifest.get(
        "manifest_sha256"
    ) != digest({k: v for k, v in manifest.items() if k != "manifest_sha256"}):
        raise EvidenceError("causal_manifest_hash_or_schema_invalid")
    if manifest.get("experiment_id") != "canonical-treatment-paper-v1":
        raise EvidenceError("causal_experiment_mismatch")
    if (
        not utc(CANONICAL.prospective_start_utc)
        <= utc(manifest.get("formal_activation_utc"))
        <= now
    ):
        raise EvidenceError("causal_activation_time_invalid")
    registration = manifest.get("registration_audit")
    if not isinstance(registration, dict):
        raise EvidenceError("registration_audit_missing")
    validate_audit(registration, utc(manifest["formal_activation_utc"]))
    if registration["fingerprints_sha256"] != manifest.get("fingerprints_sha256") or registration[
        "audit_sha256"
    ] != manifest.get("activation_audit_sha256") or registration["fingerprints"] != manifest.get(
        "fingerprints"
    ):
        raise EvidenceError("registration_audit_identity_mismatch")
    if not _same_runtime_after_selector_restart(
        manifest["fingerprints"], audit["fingerprints"],
        utc(manifest["formal_activation_utc"]), utc(audit["generated_at_utc"]),
    ):
        raise EvidenceError("causal_runtime_drift")
    if manifest.get("provenance_warnings") != registration.get("provenance_warnings"):
        raise EvidenceError("causal_provenance_warnings_mismatch")
    if (
        manifest.get("join_contract") != JOIN_CONTRACT
        or manifest.get("prospective_only") is not True
        or manifest.get("backfill_allowed") is not False
        or manifest.get("pre_activation_rows_classification") != "smoke_pre_registration"
        or manifest.get("exact_join_required") is not True
    ):
        raise EvidenceError("causal_join_policy_invalid")
    if manifest.get("allowed_config_differences") != list(ALLOWED_CONFIG_DIFFERENCES):
        raise EvidenceError("causal_allowed_differences_invalid")
    check_identity(manifest.get("identity"), CANONICAL)
    if any(manifest.get(k) is not v for k, v in SAFETY.items()):
        raise EvidenceError("causal_safety_invalid")


def activate_once(path: Path, audit: Mapping[str, Any]) -> dict[str, Any]:
    """Atomic no-clobber publication under the existing V3 exclusive guard."""
    safe_path(path)
    with exclusive(path):
        now = now_utc()
        validate_audit(audit, now)
        if path.exists():
            manifest = read_object(path)
            validate_manifest(manifest, audit, now)
            return manifest
        manifest = {
            k: audit[k]
            for k in (
                "git",
                "identity",
                "fingerprints",
                "fingerprints_sha256",
                "allowed_config_differences",
                "join_contract",
                "provenance_warnings",
            )
        }
        manifest.update(
            schema_version=MANIFEST_SCHEMA,
            experiment_id="canonical-treatment-paper-v1",
            formal_activation_utc=now.isoformat(),
            activation_audit_sha256=audit["audit_sha256"],
            registration_audit=dict(audit),
            prospective_only=True,
            backfill_allowed=False,
            pre_activation_rows_classification="smoke_pre_registration",
            exact_join_required=True,
            **SAFETY,
        )
        manifest["manifest_sha256"] = digest(manifest)
        fd, temporary = tempfile.mkstemp(prefix=".activation-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(
                    (
                        json.dumps(manifest, sort_keys=True, indent=2, allow_nan=False) + "\n"
                    ).encode()
                )
                handle.flush()
                os.fsync(handle.fileno())
            os.link(temporary, path)  # atomic; existing destination is never replaced
        finally:
            Path(temporary).unlink(missing_ok=True)
        return manifest


def run_governance(
    project_root: Path, runtime_root: Path, *, activate: bool = False
) -> dict[str, Any]:
    # Protected runtime roots are sources only, including report/manifest paths.
    project_root, runtime_root = project_root.resolve(), runtime_root.resolve()
    protected = (
        runtime_root,
        runtime_root.with_name("FUTUROS_CANONICAL_OBSERVER"),
        runtime_root.with_name("FUTUROS_CANONICAL_TREATMENT"),
    )
    if any(project_root == p or project_root.is_relative_to(p) for p in protected):
        raise EvidenceError("governance_output_must_be_outside_protected_runtime")
    try:
        report = audit_snapshots(collect_runtime(runtime_root), git=_git(project_root))
    except (EvidenceError, OSError, ValueError, KeyError, TypeError) as exc:
        reason = (
            str(exc)
            if isinstance(exc, EvidenceError)
            else "runtime_audit_failed:" + type(exc).__name__
        )
        report = {
            "schema_version": AUDIT_SCHEMA,
            "status": "blocked",
            "parity_status": "BLOCKED",
            "generated_at_utc": now_utc().isoformat(),
            "policy_version": POLICY_VERSION,
            "economic_parity": "BLOCKED",
            "image_parity": "BLOCKED",
            "safety_parity": "BLOCKED",
            "strategy_hash_parity": "BLOCKED",
            "runtime_identity": {"status": "BLOCKED"},
            "allowed_differences": [],
            "instrumentation_differences": {},
            "provenance_warnings": [],
            "blocking_differences": [reason],
            "issues": [reason],
            **SAFETY,
        }
        report["audit_sha256"] = digest(report)
    target = safe_path(project_root / AUDIT_PATH)
    with exclusive(target):
        _write_json(target, report)
    if activate:
        activate_once(project_root / MANIFEST_PATH, report)
    elif (project_root / MANIFEST_PATH).exists() and report["parity_status"] == "PASS":
        validate_manifest(read_object(project_root / MANIFEST_PATH), report, now_utc())
    return report
