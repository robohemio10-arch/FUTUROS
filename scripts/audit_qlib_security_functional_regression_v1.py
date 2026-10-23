from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata
import json
import math
import os
import re
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smartcrypto.learning.qlib_dependency_security_hardening.audit import (  # noqa: E402
    PolicyError,
    REQUIRED_ADVISORIES,
    _validate_policy,
    audit_project,
)

SCHEMA_VERSION = "qlib_security_functional_regression_v1"
POLICY_PATH = Path("config/qlib_dependency_security_policy_v1.json")
SECURITY_LOCK_PATH = Path("requirements-qlib-security.lock")
HASH_RE = re.compile(r"--hash=sha256:[0-9a-fA-F]{64}")
PIN_RE = re.compile(r"^([A-Za-z0-9_.-]+)(?:\[[^]]+\])?==([^;\s]+)$")


class FunctionalRegressionError(RuntimeError):
    """Raised when the Qlib security or functional contract is not satisfied."""


def _canonical_text_bytes(path: Path) -> bytes:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise FunctionalRegressionError(f"unreadable_utf8_file:{path}") from exc
    return text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(_canonical_text_bytes(path).decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise FunctionalRegressionError(f"invalid_json:{path}") from exc
    if not isinstance(payload, dict):
        raise FunctionalRegressionError(f"json_root_must_be_object:{path}")
    return payload


def _logical_requirement_lines(text: str) -> list[str]:
    rows: list[str] = []
    buffer: list[str] = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not buffer and (not stripped or stripped.startswith("#")):
            continue
        buffer.append(stripped.rstrip("\\").strip())
        if stripped.endswith("\\"):
            continue
        combined = " ".join(part for part in buffer if part).split(" #", 1)[0].strip()
        if combined:
            rows.append(combined)
        buffer = []
    if buffer:
        rows.append(" ".join(buffer).strip())
    return rows


def parse_security_lock(path: Path) -> dict[str, Any]:
    canonical = _canonical_text_bytes(path)
    rows = _logical_requirement_lines(canonical.decode("utf-8"))
    packages: dict[str, str] = {}
    for row in rows:
        marker = row.find(" --hash=")
        spec = (row[:marker] if marker >= 0 else row).strip()
        match = PIN_RE.fullmatch(spec)
        if match is None:
            raise FunctionalRegressionError(f"security_lock_non_exact_pin:{spec}")
        if HASH_RE.search(row) is None:
            raise FunctionalRegressionError(f"security_lock_missing_hash:{spec}")
        package_name = re.sub(r"[-_.]+", "-", match.group(1)).lower()
        if package_name in packages:
            raise FunctionalRegressionError(f"security_lock_duplicate_package:{package_name}")
        packages[package_name] = match.group(2)
    if packages.get("pyqlib") != "0.9.7" or "streamlit" in packages:
        raise FunctionalRegressionError("unexpected_qlib_graph_contract")
    return {
        "sha256": _sha256(canonical),
        "package_count": len(rows),
        "packages": packages,
        "hashes_required": True,
    }


def validate_policy_contract(
    policy: Mapping[str, Any], lock_summary: Mapping[str, Any]
) -> dict[str, Any]:
    try:
        _validate_policy(policy)
    except PolicyError as exc:
        raise FunctionalRegressionError(str(exc)) from exc
    lock_sha = str(lock_summary.get("sha256", ""))
    if policy.get("expected_security_lock_sha256") != lock_sha:
        raise FunctionalRegressionError("policy_lock_sha_mismatch")
    if policy["expected_security_lock_package_count"] != lock_summary["package_count"]:
        raise FunctionalRegressionError("policy_lock_package_count_mismatch")
    if policy["expected_packages"] != lock_summary["packages"]:
        raise FunctionalRegressionError("policy_lock_packages_mismatch")
    return {
        "policy_version": str(policy.get("policy_version", "")),
        "policy_state": policy["policy_status"],
        "functional_status": policy["p08_functional_regression"]["status"],
        "p08_allowed": policy["p08_allowed"],
        "lock_sha256": lock_sha,
        "expected_packages": dict(policy["expected_packages"]),
        "advisories_explicitly_enforced": sorted(REQUIRED_ADVISORIES),
    }


def validate_static_contract(project_root: Path) -> dict[str, Any]:
    root = project_root.resolve()
    lock_summary = parse_security_lock(root / SECURITY_LOCK_PATH)
    policy = _read_json(root / POLICY_PATH)
    result = validate_policy_contract(policy, lock_summary)
    audit = audit_project(root)
    result["candidate_evidence_valid"] = audit.get("candidate_evidence_valid", False)
    result["p08_allowed"] = audit.get("p08_allowed", False)
    return result


def _installed_versions(expected_packages: Mapping[str, str]) -> dict[str, str]:
    versions, _extras = _installed_inventory(expected_packages)
    return versions


def _installed_inventory(
    expected_packages: Mapping[str, str],
) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    """Check every locked distribution and identify the two explicit image extras."""
    installed: dict[str, str] = {}
    for distribution in importlib.metadata.distributions():
        try:
            raw_name = distribution.metadata["Name"]
        except KeyError as exc:
            raise FunctionalRegressionError("installed_package_name_missing") from exc
        if not isinstance(raw_name, str) or not raw_name.strip():
            raise FunctionalRegressionError("installed_package_name_missing")
        name = re.sub(r"[-_.]+", "-", raw_name).lower()
        if name in installed:
            raise FunctionalRegressionError(f"installed_package_duplicate:{name}")
        installed[name] = distribution.version
    extras: dict[str, dict[str, str]] = {}
    for name in sorted(set(installed) - set(expected_packages)):
        if name == "pip":
            extras[name] = {
                "version": installed[name],
                "provenance": "bootstrap_tool_from_digest_pinned_python_image",
                "qlib_lock_audit_coverage": "not_included",
            }
        elif name == "smartcrypto" and installed[name] == "0.2.1":
            extras[name] = {
                "version": installed[name],
                "provenance": "local_repository_package_declared_in_pyproject",
                "qlib_lock_audit_coverage": "not_included",
            }
        else:
            raise FunctionalRegressionError(f"installed_package_unexpected:{name}:{installed[name]}")
    versions: dict[str, str] = {}
    for package_name, expected_version in expected_packages.items():
        if package_name not in installed:
            raise FunctionalRegressionError(f"installed_package_missing:{package_name}")
        actual_version = installed[package_name]
        if actual_version != expected_version:
            raise FunctionalRegressionError(
                "installed_package_version_mismatch:"
                f"{package_name}:{actual_version}:{expected_version}"
            )
        versions[package_name] = actual_version
    return versions, extras


def _build_frame(pd: Any, np: Any, *, start_day: int, rows: int) -> Any:
    dates = pd.date_range("2026-01-01", periods=start_day + rows, freq="D", tz="UTC")[
        start_day:
    ]
    index = pd.MultiIndex.from_arrays(
        [dates, ["SYNTHETIC"] * rows], names=["datetime", "instrument"]
    )
    position = np.arange(start_day + 1, start_day + rows + 1, dtype=float)
    f1 = position / 10.0
    f2 = ((position % 7.0) - 3.0) / 5.0
    f3 = np.sin(position / 3.0)
    label = 0.75 * f1 - 0.35 * f2 + 0.20 * f3
    columns = pd.MultiIndex.from_tuples(
        [
            ("feature", "f1"),
            ("feature", "f2"),
            ("feature", "f3"),
            ("label", "LABEL0"),
        ]
    )
    return pd.DataFrame(np.column_stack([f1, f2, f3, label]), index=index, columns=columns)


def _prediction_digest(predictions: Any) -> str:
    values = [float(value) for value in predictions.to_numpy()]
    canonical = "\n".join(f"{value:.12e}" for value in values).encode("ascii")
    return _sha256(canonical)


def run_functional_regression(project_root: Path) -> dict[str, Any]:
    static = validate_static_contract(project_root)
    if static["policy_state"] == "BLOCKED":
        raise FunctionalRegressionError("qlib_security_blocked")
    if not static["candidate_evidence_valid"]:
        raise FunctionalRegressionError("current_graph_evidence_invalid")
    if os.environ.get("SMARTCRYPTO_QLIB_NETWORK_MODE") != "none":
        raise FunctionalRegressionError("network_mode_not_declared_none")
    if os.environ.get("SMARTCRYPTO_QLIB_REPOSITORY_MOUNT") != "readonly":
        raise FunctionalRegressionError("repository_mount_not_declared_readonly")
    if os.environ.get("MLFLOW_ALLOW_PICKLE_DESERIALIZATION", "").lower() != "false":
        raise FunctionalRegressionError("mlflow_pickle_deserialization_control_not_false")

    installed_versions, installation_extras = _installed_inventory(static["expected_packages"])
    import_smoke = (
        "git", "mako", "mlflow", "certifi", "PIL", "aiohttp", "tornado",
        "cryptography", "pyarrow", "jupyterlab", "notebook",
    )
    try:
        for module_name in import_smoke:
            importlib.import_module(module_name)
        np = importlib.import_module("numpy")
        pd = importlib.import_module("pandas")
        qlib = importlib.import_module("qlib")
        linear_module = importlib.import_module("qlib.contrib.model.linear")
    except Exception as exc:
        raise FunctionalRegressionError(f"qlib_import_failed:{type(exc).__name__}:{exc}") from exc

    if str(getattr(qlib, "__version__", "")) != "0.9.7":
        raise FunctionalRegressionError("qlib_module_version_mismatch")
    linear_model_class = getattr(linear_module, "LinearModel", None)
    if linear_model_class is None:
        raise FunctionalRegressionError("qlib_contrib_linear_model_missing")
    if not str(getattr(linear_model_class, "__module__", "")).startswith("qlib.contrib."):
        raise FunctionalRegressionError("model_not_from_qlib_contrib")

    class InMemoryQlibDataset:
        def __init__(self, frames: Mapping[str, Any]) -> None:
            self._frames = dict(frames)

        def prepare(self, segment: Any, col_set: Any = None, data_key: Any = None) -> Any:
            del data_key
            if isinstance(segment, (list, tuple)):
                return [self.prepare(item, col_set=col_set) for item in segment]
            key = str(segment)
            if key not in self._frames:
                raise KeyError(key)
            frame = self._frames[key].copy()
            if col_set is None:
                return frame
            if isinstance(col_set, str):
                return frame[col_set]
            requested = set(col_set)
            mask = [column[0] in requested for column in frame.columns]
            return frame.loc[:, mask]

    with tempfile.TemporaryDirectory(prefix="futuros-qlib-functional-") as temp_dir:
        temp_root = Path(temp_dir)
        provider_dir = temp_root / "provider"
        provider_dir.mkdir(parents=True, exist_ok=True)
        mlflow_dir = temp_root / "mlruns"
        mlflow_dir.mkdir(parents=True, exist_ok=True)
        os.environ["MLFLOW_TRACKING_URI"] = mlflow_dir.as_uri()

        try:
            qlib.init(provider_uri=str(provider_dir), region="us", clear_mem_cache=True)
        except Exception as exc:
            raise FunctionalRegressionError(
                f"qlib_init_failed:{type(exc).__name__}:{exc}"
            ) from exc

        dataset = InMemoryQlibDataset(
            {
                "train": _build_frame(pd, np, start_day=0, rows=36),
                "valid": _build_frame(pd, np, start_day=36, rows=12),
                "test": _build_frame(pd, np, start_day=48, rows=12),
            }
        )
        try:
            model_a = linear_model_class(estimator="ols", fit_intercept=False)
            model_a.fit(dataset)
            predictions_a = model_a.predict(dataset, segment="test")
            model_b = linear_model_class(estimator="ols", fit_intercept=False)
            model_b.fit(dataset)
            predictions_b = model_b.predict(dataset, segment="test")
        except Exception as exc:
            raise FunctionalRegressionError(
                f"qlib_fit_predict_failed:{type(exc).__name__}:{exc}"
            ) from exc

    values_a = [float(value) for value in predictions_a.to_numpy()]
    values_b = [float(value) for value in predictions_b.to_numpy()]
    if not values_a:
        raise FunctionalRegressionError("empty_predictions")
    if not all(math.isfinite(value) for value in values_a + values_b):
        raise FunctionalRegressionError("non_finite_predictions")
    deterministic = bool(np.allclose(values_a, values_b, rtol=1e-12, atol=1e-12))
    if not deterministic:
        raise FunctionalRegressionError("non_deterministic_predictions")

    prediction_digest = _prediction_digest(predictions_a)
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ok",
        "reason": "qlib_locked_graph_functional_regression_passed",
        "decision": (
            "P08_SECURITY_GATE_CERTIFIED"
            if static["p08_allowed"]
            else "FUNCTIONAL_REGRESSION_PASSED_PENDING_CI_CERTIFICATION"
        ),
        "policy_version": static["policy_version"],
        "lock_sha256": static["lock_sha256"],
        "installed_versions": installed_versions,
        "installation_extras": installation_extras,
        "audited_lock_package_count": len(installed_versions),
        "import_smoke": {name: "PASS" for name in import_smoke},
        "qlib": {
            "version": str(getattr(qlib, "__version__", "")),
            "init_status": "ok",
            "provider_mode": "local_temporary_offline",
        },
        "model": {
            "module": str(getattr(linear_model_class, "__module__", "")),
            "class": str(getattr(linear_model_class, "__name__", "")),
            "estimator": "ols",
            "fallback_used": False,
            "train_rows": 36,
            "valid_rows": 12,
            "test_rows": 12,
        },
        "predictions": {
            "count": len(values_a),
            "all_finite": True,
            "deterministic_repeat": deterministic,
            "sha256": prediction_digest,
        },
        "security": {
            "advisories_explicitly_enforced": static["advisories_explicitly_enforced"],
            "mlflow_pickle_deserialization_control": "false",
            "network_mode": "none",
            "repository_mount": "readonly",
            "exact_locked_graph": True,
            "exact_certified_graph": bool(static["p08_allowed"]),
            "current_graph_ci_certified": bool(static["p08_allowed"]),
            "qlib_security_gate_bypassed": False,
        },
        "safety": {
            "research_only": True,
            "shadow_only": True,
            "paper_only": True,
            "operational_authority": False,
            "sends_orders": False,
            "exchange_private_access": False,
            "changes_risk": False,
            "models_changed": False,
            "runtime_updated": False,
        },
        "p08_allowed": bool(static["p08_allowed"]),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Fail-closed functional Qlib regression on the exact hash-locked security graph."
        )
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def _failure_report(exc: Exception) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "blocked",
        "reason": str(exc),
        "decision": "MANTER_EM_RESEARCH",
        "qlib_security_gate_bypassed": False,
        "p08_allowed": False,
        "safety": {
            "research_only": True,
            "operational_authority": False,
            "sends_orders": False,
            "exchange_private_access": False,
            "changes_risk": False,
            "models_changed": False,
            "runtime_updated": False,
        },
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = run_functional_regression(Path(args.project_root))
    except Exception as exc:
        report = _failure_report(exc)
        exit_code = 1
    else:
        exit_code = 0

    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        print(
            f"status={report['status']} reason={report['reason']} "
            f"decision={report['decision']}"
        )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
