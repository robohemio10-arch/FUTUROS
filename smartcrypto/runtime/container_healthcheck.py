from __future__ import annotations

import argparse
import importlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

SAFE_FALSE_FLAGS = (
    "live_trading_enabled",
    "order_submission_enabled",
    "real_order_submission_enabled",
    "exchange_private_access",
    "sends_orders",
    "changes_risk",
)
ENV_FLAG_MAP = {
    "LIVE_ENABLED": "live_trading_enabled",
    "ORDER_SUBMISSION_ENABLED": "order_submission_enabled",
    "REAL_ORDER_SUBMISSION_ENABLED": "real_order_submission_enabled",
    "EXCHANGE_PRIVATE_ACCESS": "exchange_private_access",
    "SMARTCRYPTO_EXCHANGE_PRIVATE_ACCESS": "exchange_private_access",
}
DEFAULT_REQUIRED_PATHS = ("smartcrypto",)
DEFAULT_IMPORTS = ("smartcrypto",)


def run_container_healthcheck(
    *,
    required_paths: list[str | Path] | tuple[str | Path, ...] = DEFAULT_REQUIRED_PATHS,
    required_imports: list[str] | tuple[str, ...] = DEFAULT_IMPORTS,
    env: Mapping[str, str | None] | None = None,
    safety_overrides: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    current_time = ensure_utc(now or datetime.now(timezone.utc))
    active_env = env or os.environ
    runtime_mode = str(active_env.get("SMARTCRYPTO_RUNTIME_MODE") or "paper").strip().lower()
    safety = safety_payload(active_env=active_env, runtime_mode=runtime_mode, overrides=safety_overrides)
    blocking_findings = [f"unsafe_safety_flag:{flag}" for flag in unsafe_safety_flags(safety)]
    checks: dict[str, dict[str, Any]] = {}

    for raw_path in required_paths:
        target = Path(raw_path)
        checks[f"path:{target}"] = {
            "status": "ok" if target.exists() else "blocked",
            "reason": "ok" if target.exists() else "missing_required_path",
            "path": str(target),
        }
    for module_name in required_imports:
        checks[f"import:{module_name}"] = check_import(module_name)

    for name, check in checks.items():
        if check["status"] != "ok":
            blocking_findings.append(f"{name}:{check['reason']}")

    blocking_findings = sorted(set(blocking_findings))
    status = "blocked" if blocking_findings else "ok"
    return {
        "status": status,
        "reason": "container_healthcheck_ok" if status == "ok" else ";".join(blocking_findings),
        "checked_at_utc": iso(current_time),
        "checks": checks,
        "blocking_findings": blocking_findings,
        "public_data_only": True,
        "private_endpoints_used": False,
        "write_performed": False,
        **safety,
    }


def check_import(module_name: str) -> dict[str, Any]:
    try:
        importlib.import_module(module_name)
    except Exception as exc:  # pragma: no cover - defensive reporting for container probes
        return {"status": "blocked", "reason": f"import_failed:{type(exc).__name__}", "module": module_name}
    return {"status": "ok", "reason": "ok", "module": module_name}


def safety_payload(
    *,
    active_env: Mapping[str, str | None],
    runtime_mode: str,
    overrides: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "paper_only": runtime_mode == "paper",
        "shadow_only": True,
        "runtime_mode": runtime_mode,
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "sends_orders": False,
        "changes_risk": False,
    }
    for env_name, flag_name in ENV_FLAG_MAP.items():
        payload[flag_name] = payload[flag_name] or as_bool(active_env.get(env_name))
    if overrides:
        payload.update({key: value for key, value in overrides.items() if key in payload})
    return payload


def unsafe_safety_flags(payload: Mapping[str, Any]) -> list[str]:
    unsafe: list[str] = []
    if payload.get("paper_only") is not True:
        unsafe.append("paper_only")
    if payload.get("shadow_only") is not True:
        unsafe.append("shadow_only")
    if payload.get("runtime_mode") != "paper":
        unsafe.append("runtime_mode")
    for flag in SAFE_FALSE_FLAGS:
        if payload.get(flag) is True:
            unsafe.append(flag)
    return unsafe


def as_bool(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def ensure_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def iso(value: datetime) -> str:
    return ensure_utc(value).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Container healthcheck paper/shadow only.")
    parser.add_argument("--required-path", action="append", dest="required_paths")
    parser.add_argument("--required-import", action="append", dest="required_imports")
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = run_container_healthcheck(
        required_paths=args.required_paths or list(DEFAULT_REQUIRED_PATHS),
        required_imports=args.required_imports or list(DEFAULT_IMPORTS),
    )
    if not args.quiet or report["status"] != "ok":
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 1 if report["status"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
