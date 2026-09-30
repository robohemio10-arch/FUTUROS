from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, BinaryIO, Iterator, Literal, Protocol


SCHEMA_VERSION = "windows_docker_dns_causal_telemetry_v1"
TARGET_HOST = "fapi.binance.com"
DEFAULT_OUTPUT_DIR = Path(r"E:\FUTUROS_LOCAL_CHECKPOINTS\DNS_CAUSAL_TELEMETRY_V1")
CONTAINER_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(token|secret|password|passwd|authorization|api[_-]?key|proxy)\b\s*[:=]\s*[^\s,;]+"
)
URL_CREDENTIALS = re.compile(r"(?i)\b(https?://)[^\s/@]+:[^\s/@]+@")
BEARER_VALUE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+")
ERROR_LIMIT = 500
DNS_SERVER_LITERAL = re.compile(r"[0-9A-Fa-f:.]+(?:%[0-9]+)?\Z")


def sanitize_error(value: str) -> str:
    text = URL_CREDENTIALS.sub(r"\1<redacted>@", value)
    text = BEARER_VALUE.sub("Bearer <redacted>", text)
    text = SECRET_ASSIGNMENT.sub(lambda match: f"{match.group(1)}=<redacted>", text)
    return text[:ERROR_LIMIT]


def _error_result(status: str, reason: str, duration_ms: float | None = None) -> dict[str, Any]:
    return {"status": status, "error": sanitize_error(reason), "duration_ms": duration_ms}


def _safe_dns_server(value: Any) -> str | None:
    try:
        server = str(ipaddress.ip_address(value))
    except (TypeError, ValueError):
        return None
    return server if DNS_SERVER_LITERAL.fullmatch(server) else None


class ProbeProvider(Protocol):
    def host_resolution(self, timeout_seconds: float) -> dict[str, Any]: ...

    def windows_network(self, timeout_seconds: float) -> dict[str, Any]: ...

    def direct_dns(self, server: str, timeout_seconds: float) -> dict[str, Any]: ...

    def docker(self, container_name: str | None, timeout_seconds: float) -> dict[str, Any]: ...

    def windows_events(self, now: datetime, timeout_seconds: float) -> dict[str, Any]: ...


HOST_LOOKUP_SCRIPT = r"""
import json, socket, sys
try:
    values = socket.getaddrinfo(sys.argv[1], 443, type=socket.SOCK_STREAM)
    print(json.dumps({"status": "ok", "addresses": sorted({item[4][0] for item in values})}))
except OSError as exc:
    print(json.dumps({"status": "failed", "error": f"{type(exc).__name__}:{exc}"}))
"""

CONTAINER_LOOKUP_SCRIPT = r"""
import hashlib, json, socket, sys
result = {"resolver_status": "unavailable", "resolution_status": "unavailable"}
try:
    with open("/etc/resolv.conf", "rb") as handle:
        raw = handle.read(65537)
    if len(raw) > 65536:
        result["resolver_error"] = "resolv_conf_too_large"
    else:
        result["resolver_status"] = "ok"
        result["resolver_sha256"] = hashlib.sha256(raw).hexdigest()
        result["resolver_content"] = raw.decode("utf-8", "replace")
except OSError as exc:
    result["resolver_error"] = f"{type(exc).__name__}:{exc}"
try:
    values = socket.getaddrinfo(sys.argv[1], 443, type=socket.SOCK_STREAM)
    result["resolution_status"] = "ok"
    result["addresses"] = sorted({item[4][0] for item in values})
except OSError as exc:
    result["resolution_status"] = "failed"
    result["resolution_error"] = f"{type(exc).__name__}:{exc}"
print(json.dumps(result))
"""

NETWORK_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$items = @()
foreach ($item in @(Get-NetIPConfiguration)) {
    if ($item.NetAdapter.Status -ne 'Up') { continue }
    if (-not $item.IPv4DefaultGateway -and -not $item.IPv6DefaultGateway) { continue }
    $index = [int]$item.InterfaceIndex
    $dns = @(Get-DnsClientServerAddress -InterfaceIndex $index -ErrorAction Stop |
        ForEach-Object { $_.ServerAddresses } | Where-Object { $_ } | Sort-Object -Unique)
    $profile = Get-NetConnectionProfile -InterfaceIndex $index -ErrorAction Stop |
        Select-Object -First 1
    $routes = @(Get-NetRoute -InterfaceIndex $index -ErrorAction Stop |
        Where-Object { $_.DestinationPrefix -in @('0.0.0.0/0', '::/0') } |
        ForEach-Object { [pscustomobject]@{
            destination = $_.DestinationPrefix
            gateway = $_.NextHop
            route_metric = [int]$_.RouteMetric
            interface_metric = [int]$_.InterfaceMetric
        } })
    $items += [pscustomobject]@{
        interface_index = $index
        adapter = [string]$item.InterfaceAlias
        adapter_status = [string]$item.NetAdapter.Status
        profile_id = if ($profile) { [string]$profile.InstanceID } else { $null }
        profile_category = if ($profile) { [string]$profile.NetworkCategory } else { $null }
        ipv4_connectivity = if ($profile) { [string]$profile.IPv4Connectivity } else { $null }
        ipv6_connectivity = if ($profile) { [string]$profile.IPv6Connectivity } else { $null }
        dns_servers = $dns
        default_routes = $routes
    }
}
ConvertTo-Json -InputObject @($items) -Depth 6 -Compress
"""

DIRECT_DNS_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
if (-not (Get-Command Resolve-DnsName -ErrorAction SilentlyContinue)) {
    ConvertTo-Json -InputObject @{status='unavailable'; error='resolve_dns_name_missing'} -Compress
    exit 0
}
try {
    $answers = @(Resolve-DnsName -Name 'fapi.binance.com' -Server '__SERVER__' -Type A -DnsOnly -NoHostsFile -ErrorAction Stop |
        ForEach-Object { $_.IPAddress } | Where-Object { $_ } | Sort-Object -Unique)
    if ($answers.Count -eq 0) {
        ConvertTo-Json -InputObject @{status='failed'; error='no_a_records'; addresses=@()} -Compress
    } else {
        ConvertTo-Json -InputObject @{status='ok'; addresses=$answers} -Compress
    }
} catch {
    ConvertTo-Json -InputObject @{status='failed'; error=($_.Exception.GetType().Name + ':' + $_.Exception.Message)} -Compress
}
"""

EVENTS_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$since = [datetime]'__SINCE__'
$channels = @(
    @{name='Microsoft-Windows-DNS-Client/Operational'; ids=@()},
    @{name='System'; ids=@(1014)},
    @{name='Microsoft-Windows-NetworkProfile/Operational'; ids=@(10000,10001,4004)}
)
$result = @()
foreach ($channel in $channels) {
    try {
        $meta = Get-WinEvent -ListLog $channel.name -ErrorAction Stop
        if (-not $meta.IsEnabled) {
            $result += [pscustomobject]@{channel=$channel.name; status='unavailable'; reason='channel_disabled'; events=@()}
            continue
        }
        $filter = @{LogName=$channel.name; StartTime=$since}
        if ($channel.ids.Count -gt 0) { $filter.Id = $channel.ids }
        try {
            $events = @(Get-WinEvent -FilterHashtable $filter -MaxEvents 50 -ErrorAction Stop |
                ForEach-Object { [pscustomobject]@{
                    timestamp_utc = $_.TimeCreated.ToUniversalTime().ToString('o')
                    provider = $_.ProviderName
                    event_id = [int]$_.Id
                    level = [int]$_.Level
                } })
            $result += [pscustomobject]@{channel=$channel.name; status='ok'; reason=$null; events=$events}
        } catch {
            if ($_.FullyQualifiedErrorId -match 'NoMatchingEventsFound') {
                $result += [pscustomobject]@{channel=$channel.name; status='ok'; reason=$null; events=@()}
            } else {
                $result += [pscustomobject]@{channel=$channel.name; status='unavailable'; reason=$_.Exception.GetType().Name; events=@()}
            }
        }
    } catch {
        $result += [pscustomobject]@{channel=$channel.name; status='unavailable'; reason=$_.Exception.GetType().Name; events=@()}
    }
}
ConvertTo-Json -InputObject @($result) -Depth 5 -Compress
"""


class SystemProbes:
    def _run(self, args: list[str], timeout_seconds: float) -> dict[str, Any]:
        start = time.monotonic()
        try:
            process = subprocess.run(args, capture_output=True, text=True, timeout=timeout_seconds, check=False)
        except (FileNotFoundError, PermissionError) as exc:
            return _error_result("unavailable", f"{type(exc).__name__}:{exc}", _duration(start))
        except subprocess.TimeoutExpired:
            return _error_result("timeout", "probe_timeout", _duration(start))
        except OSError as exc:
            return _error_result("unavailable", f"{type(exc).__name__}:{exc}", _duration(start))
        if process.returncode != 0:
            return _error_result(
                "unavailable", f"exit_{process.returncode}:{process.stderr}", _duration(start)
            )
        if len(process.stdout) > 65536:
            return _error_result("unavailable", "probe_output_too_large", _duration(start))
        return {"status": "ok", "stdout": process.stdout, "duration_ms": _duration(start)}

    def _powershell(self, script: str, timeout_seconds: float) -> dict[str, Any]:
        executable = shutil.which("powershell.exe") or shutil.which("powershell")
        if executable is None:
            return _error_result("unavailable", "powershell_missing")
        return self._run([executable, "-NoProfile", "-NonInteractive", "-Command", script], timeout_seconds)

    def host_resolution(self, timeout_seconds: float) -> dict[str, Any]:
        command = self._run(
            [sys.executable, "-B", "-c", HOST_LOOKUP_SCRIPT, TARGET_HOST], timeout_seconds
        )
        return _resolution_from_command(command)

    def windows_network(self, timeout_seconds: float) -> dict[str, Any]:
        command = self._powershell(NETWORK_SCRIPT, timeout_seconds)
        if command["status"] != "ok":
            return command
        payload = _parse_json(command)
        if not isinstance(payload, list):
            return _error_result("unavailable", "invalid_network_probe_json", command["duration_ms"])
        adapters = [item for item in payload if isinstance(item, dict)]
        servers: list[str] = []
        invalid_server_count = 0
        for adapter in adapters:
            profile_id = adapter.pop("profile_id", None)
            adapter["profile_id_sha256"] = (
                hashlib.sha256(str(profile_id).encode("utf-8")).hexdigest()
                if profile_id else None
            )
            configured = adapter.get("dns_servers", [])
            adapter["dns_config_sha256"] = hashlib.sha256(
                json.dumps(configured, sort_keys=True, default=str).encode("utf-8")
            ).hexdigest()
            if isinstance(configured, str):
                configured = [configured]
            if not isinstance(configured, list):
                adapter["dns_servers"] = ["<invalid>"]
                invalid_server_count += 1
                continue
            safe_configured: list[str] = []
            for value in configured:
                server = _safe_dns_server(value)
                if server is None:
                    safe_configured.append("<invalid>")
                    invalid_server_count += 1
                    continue
                safe_configured.append(server)
                if server not in servers:
                    servers.append(server)
            adapter["dns_servers"] = safe_configured
        canonical = json.dumps(adapters, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return {
            "status": "ok",
            "adapters": adapters,
            "dns_servers": servers,
            "invalid_dns_server_count": invalid_server_count,
            "network_state_sha256": hashlib.sha256(canonical).hexdigest(),
            "duration_ms": command["duration_ms"],
        }

    def direct_dns(self, server: str, timeout_seconds: float) -> dict[str, Any]:
        safe_server = _safe_dns_server(server)
        if safe_server is None:
            return _error_result("unavailable", "invalid_dns_server")
        command = self._powershell(
            DIRECT_DNS_SCRIPT.replace("__SERVER__", safe_server), timeout_seconds
        )
        result = _resolution_from_command(command)
        result["server"] = safe_server
        return result

    def docker(self, container_name: str | None, timeout_seconds: float) -> dict[str, Any]:
        if container_name is None:
            return _error_result("unavailable", "container_not_configured")
        if not CONTAINER_NAME.fullmatch(container_name):
            return _error_result("unavailable", "invalid_container_name")
        executable = shutil.which("docker")
        if executable is None:
            return _error_result("unavailable", "docker_missing")
        deadline = time.monotonic() + timeout_seconds
        engine = self._run(
            [executable, "version", "--format", "{{.Server.Version}}"], timeout_seconds
        )
        if engine["status"] != "ok":
            return {"engine_status": "unavailable", "container_status": "unavailable", **engine}
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return {"engine_status": "ok", "container_status": "unavailable", **_error_result("timeout", "probe_timeout")}
        inspect = self._run(
            [
                executable,
                "inspect",
                "--type",
                "container",
                "--format",
                "{{.Name}}\n{{.State.Running}}\n{{json .NetworkSettings.Networks}}",
                container_name,
            ],
            remaining,
        )
        if inspect["status"] != "ok":
            missing = any(
                marker in inspect.get("error", "").lower()
                for marker in ("no such object", "no such container")
            )
            return {
                "engine_status": "ok",
                "container_status": "missing" if missing else "unavailable",
                **inspect,
            }
        parts = inspect["stdout"].splitlines()
        if len(parts) != 3 or parts[0] != f"/{container_name}":
            return _error_result("unavailable", "container_identity_unverified", inspect["duration_ms"])
        if parts[1] != "true":
            return {"status": "unavailable", "engine_status": "ok", "container_status": "not_running"}
        try:
            networks = json.loads(parts[2])
        except json.JSONDecodeError:
            return _error_result("unavailable", "invalid_docker_network_json", inspect["duration_ms"])
        if not isinstance(networks, dict):
            return _error_result("unavailable", "invalid_docker_network_json", inspect["duration_ms"])
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return {"engine_status": "ok", "container_status": "running", **_error_result("timeout", "probe_timeout")}
        lookup = self._run(
            [executable, "exec", container_name, "python", "-B", "-c", CONTAINER_LOOKUP_SCRIPT, TARGET_HOST],
            remaining,
        )
        if lookup["status"] != "ok":
            return {
                "status": lookup["status"],
                "error": lookup["error"],
                "duration_ms": lookup["duration_ms"],
                "engine_status": "ok",
                "container_status": "running",
                "networks": sorted(networks),
            }
        payload = _parse_json(lookup)
        if not isinstance(payload, dict):
            return _error_result("unavailable", "invalid_container_probe_json", lookup["duration_ms"])
        resolution_status = payload.get("resolution_status")
        if resolution_status not in {"ok", "failed"}:
            return _error_result("unavailable", "invalid_container_resolution_status", lookup["duration_ms"])
        resolver_content = payload.get("resolver_content", "")
        if not isinstance(resolver_content, str):
            resolver_content = ""
        resolver_hash = payload.get("resolver_sha256")
        if not isinstance(resolver_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", resolver_hash):
            resolver_hash = None
        return {
            "status": resolution_status,
            "error": sanitize_error(str(payload.get("resolution_error", ""))) or None,
            "addresses": _public_addresses(payload.get("addresses", [])),
            "duration_ms": lookup["duration_ms"],
            "engine_status": "ok",
            "container_status": "running",
            "container_name": container_name,
            "networks": sorted(networks),
            "resolver_status": payload.get("resolver_status", "unavailable"),
            "resolver_error": sanitize_error(str(payload.get("resolver_error", ""))) or None,
            "resolver_sha256": resolver_hash,
            "resolver_content_sanitized": sanitize_resolver(resolver_content),
        }

    def windows_events(self, now: datetime, timeout_seconds: float) -> dict[str, Any]:
        since = (now - timedelta(minutes=15)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        command = self._powershell(EVENTS_SCRIPT.replace("__SINCE__", since), timeout_seconds)
        if command["status"] != "ok":
            return command
        payload = _parse_json(command)
        if not isinstance(payload, list):
            return _error_result("unavailable", "invalid_event_probe_json", command["duration_ms"])
        channels = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            channels.append(
                {
                    "channel": item.get("channel"),
                    "status": item.get("status"),
                    "reason": sanitize_error(str(item.get("reason"))) if item.get("reason") else None,
                    "events": item.get("events", []),
                }
            )
        return {"status": "ok", "channels": channels, "duration_ms": command["duration_ms"]}


def _duration(start: float) -> float:
    return round((time.monotonic() - start) * 1000, 3)


def _parse_json(command: dict[str, Any]) -> Any:
    try:
        return json.loads(command["stdout"])
    except (KeyError, TypeError, json.JSONDecodeError):
        return None


def _public_addresses(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    addresses = []
    for value in values:
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            continue
        addresses.append(str(address))
    return sorted(set(addresses))


def _resolution_from_command(command: dict[str, Any]) -> dict[str, Any]:
    if command["status"] != "ok":
        return command
    payload = _parse_json(command)
    if not isinstance(payload, dict) or payload.get("status") not in {"ok", "failed", "unavailable"}:
        return _error_result("unavailable", "invalid_resolution_probe_json", command["duration_ms"])
    return {
        "status": payload["status"],
        "addresses": _public_addresses(payload.get("addresses", [])),
        "error": sanitize_error(str(payload.get("error", ""))) or None,
        "duration_ms": command["duration_ms"],
    }


def sanitize_resolver(content: str) -> list[str]:
    lines = []
    for raw in content.splitlines():
        parts = raw.strip().split()
        if not parts or parts[0].startswith("#"):
            continue
        if parts[0] == "nameserver" and len(parts) == 2:
            try:
                lines.append(f"nameserver {ipaddress.ip_address(parts[1])}")
            except ValueError:
                lines.append("nameserver <invalid>")
        elif parts[0] == "options":
            options = [part for part in parts[1:] if re.fullmatch(r"(?:ndots|timeout|attempts):\d+", part)]
            lines.append("options " + " ".join(options))
        elif parts[0] in {"search", "domain"}:
            lines.append(f"{parts[0]} <redacted>")
        else:
            lines.append("<redacted>")
    return lines


Classification = Literal[
    "HOST_AND_DIRECT_DNS_OK",
    "HOST_RESOLUTION_FAILED_DIRECT_DNS_OK",
    "HOST_AND_DIRECT_DNS_FAILED",
    "HOST_OK_CONTAINER_FAILED",
    "HOST_AND_CONTAINER_FAILED",
    "CONTAINER_OK",
    "INSUFFICIENT_EVIDENCE",
]


def classify_sample(
    host: dict[str, Any], direct_dns: list[dict[str, Any]], docker: dict[str, Any]
) -> Classification:
    host_status = host.get("status")
    container_status = docker.get("status")
    direct_statuses = [item.get("status") for item in direct_dns]
    all_direct_ok = bool(direct_statuses) and all(value == "ok" for value in direct_statuses)
    all_direct_failed = bool(direct_statuses) and all(value == "failed" for value in direct_statuses)
    if host_status == "failed" and all_direct_ok:
        return "HOST_RESOLUTION_FAILED_DIRECT_DNS_OK"
    if host_status == "failed" and all_direct_failed:
        return "HOST_AND_DIRECT_DNS_FAILED"
    if host_status == "ok" and container_status == "failed":
        return "HOST_OK_CONTAINER_FAILED"
    if host_status == "failed" and container_status == "failed":
        return "HOST_AND_CONTAINER_FAILED"
    if host_status == "ok" and all_direct_ok:
        return "HOST_AND_DIRECT_DNS_OK"
    if container_status == "ok":
        return "CONTAINER_OK"
    return "INSUFFICIENT_EVIDENCE"


def collect_sample(
    probes: ProbeProvider,
    *,
    container_name: str | None,
    timeout_seconds: float,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not 0 < timeout_seconds <= 30:
        raise ValueError("timeout_seconds_out_of_range")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("now_must_be_timezone_aware")
    host = probes.host_resolution(timeout_seconds)
    network = probes.windows_network(timeout_seconds)
    direct_dns = [
        probes.direct_dns(server, timeout_seconds)
        for server in network.get("dns_servers", [])
    ]
    direct_dns.extend(
        _error_result("unavailable", "invalid_dns_server")
        for _ in range(network.get("invalid_dns_server_count", 0))
    )
    docker = probes.docker(container_name, timeout_seconds)
    events = probes.windows_events(current, timeout_seconds)
    return {
        "schema_version": SCHEMA_VERSION,
        "sampled_at_utc": current.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "target": TARGET_HOST,
        "host_windows": {"resolution": host, "network": network},
        "direct_dns": direct_dns,
        "docker": docker,
        "windows_event_evidence": events,
        "classification": classify_sample(host, direct_dns, docker),
        "classification_is_root_cause": False,
        "source_reads_only": True,
        "writes_runtime": False,
        "paper_behavior_changed": False,
        "runtime_changed": False,
        "sends_orders": False,
        "exchange_private_access": False,
    }


def _validate_output_dir(path: Path) -> Path:
    if not path.is_absolute():
        raise ValueError("output_dir_must_be_absolute")
    resolved = path.resolve()
    for ancestor in (resolved, *resolved.parents):
        if (ancestor / ".git").exists():
            raise ValueError("output_dir_must_be_outside_git_worktree")
    return resolved


@contextmanager
def _file_lock(handle: BinaryIO) -> Iterator[None]:
    handle.seek(0)
    if sys.platform == "win32":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        flock = getattr(fcntl, "flock")
        flock(handle.fileno(), getattr(fcntl, "LOCK_EX") | getattr(fcntl, "LOCK_NB"))
        try:
            yield
        finally:
            flock(handle.fileno(), getattr(fcntl, "LOCK_UN"))


def persist_sample(output_dir: Path, sample: dict[str, Any]) -> tuple[Path, Path]:
    destination = _validate_output_dir(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    jsonl_path = destination / "dns_causal_telemetry_v1.jsonl"
    snapshot_path = destination / "dns_causal_telemetry_v1.latest.json"
    if any(path.is_symlink() for path in (jsonl_path, snapshot_path)):
        raise ValueError("output_file_symlink_not_allowed")
    payload = json.dumps(sample, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    with jsonl_path.open("a+b") as handle:
        with _file_lock(handle):
            handle.seek(0, os.SEEK_END)
            handle.write((payload + "\n").encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
            temp_path: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", newline="\n", dir=destination, delete=False
                ) as snapshot_handle:
                    temp_path = Path(snapshot_handle.name)
                    snapshot_handle.write(
                        json.dumps(sample, sort_keys=True, indent=2, ensure_ascii=True) + "\n"
                    )
                    snapshot_handle.flush()
                    os.fsync(snapshot_handle.fileno())
                os.replace(temp_path, snapshot_path)
            finally:
                if temp_path is not None:
                    temp_path.unlink(missing_ok=True)
    logging.getLogger(__name__).info("dns_telemetry_sample_persisted classification=%s", sample["classification"])
    return jsonl_path, snapshot_path
