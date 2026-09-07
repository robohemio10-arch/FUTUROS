from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata
import json
import math
import os
import re
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "qlib_security_functional_regression_v1"
POLICY_PATH = Path("config/qlib_dependency_security_policy_v1.json")
SECURITY_LOCK_PATH = Path("requirements-qlib-security.lock")
EXPECTED_PACKAGE_COUNT = 190
EXPECTED_PACKAGES = {
    "pyqlib": "0.9.7",
    "mlflow": "3.16.0",
    "mlflow-skinny": "3.16.0",
    "mlflow-tracing": "3.16.0",
    "cryptography": "50.0.0",
    "pyarrow": "25.0.1",
}
EXPECTED_GHSA = {
    "package": "mlflow",
    "published_date": "2026-09-01",
    "severity": "HIGH",
    "cvss": 8.8,
    "affected_spec": ">=2.1.0,<3.15.0",
    "fixed_minimum": "3.15.0",
    "certified_version": "3.16.0",
    "status": "remediated_by_certified_pin",
    "control_impacted": "MLFLOW_ALLOW_PICKLE_DESERIALIZATION=False",
}
EXPECTED_CRYPTOGRAPHY_CVE = {
    "package": "cryptography",
    "affected_spec": ">=44,<50",
    "fixed_minimum": "50.0.0",
    "certified_version": "50.0.0",
    "status": "remediated_by_certified_pin",
}
HASH_RE = re.compile(r"--hash=sha256:[0-9a-fA-F]{64}")
PIN_RE = re.compile(r"^([A-Za-z0-9_.-]+)(?:\[[^]]+\])?==([^;\s]+)$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


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


def _as_mapping(value: Any, *, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise FunctionalRegressionError(f"{name}_must_be_object")
    return value


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
        package_name = match.group(1).lower().replace("_", "-")
        if package_name in packages:
            raise FunctionalRegressionError(f"security_lock_duplicate_package:{package_name}")
        packages[package_name] = match.group(2)
    if len(rows) != EXPECTED_PACKAGE_COUNT:
        raise FunctionalRegressionError(
            f"security_lock_package_count_mismatch:{len(rows)}:{EXPECTED_PACKAGE_COUNT}"
        )
    for package_name, expected_version in EXPECTED_PACKAGES.items():
        actual = packages.get(package_name)
        if actual != expected_version:
            raise FunctionalRegressionError(
                f"security_lock_anchor_mismatch:{package_name}:{actual}:{expected_version}"
            )
    return {
        "sha256": _sha256(canonical),
        "package_count": len(rows),
        "packages": packages,
        "hashes_required": True,
    }


def _validate_advisory(
    advisories: Mapping[str, Any], advisory_id: str, expected: Mapping[str, Any]
) -> None:
    advisory = _as_mapping(advisories.get(advisory_id), name=f"advisory:{advisory_id}")
    for key, expected_value in expected.items():
        if advisory.get(key) != expected_value:
            raise FunctionalRegressionError(f"advisory_contract_mismatch:{advisory_id}:{key}")


def validate_policy_contract(
    policy: Mapping[str, Any], lock_summary: Mapping[str, Any]
) -> dict[str, Any]:
    if policy.get("schema_version") != "qlib_dependency_security_policy_v1":
        raise FunctionalRegressionError("unexpected_policy_schema")
    if policy.get("policy_status") != "active_security_clean":
        raise FunctionalRegressionError("dependency_policy_not_security_clean")
    if policy.get("qlib_security_gate_passed") is not True:
        raise FunctionalRegressionError("dependency_security_gate_not_passed")
    if policy.get("approved_security_clean_resolution_found") is not True:
        raise FunctionalRegressionError("approved_security_clean_resolution_missing")

    lock_sha = str(lock_summary.get("sha256", ""))
    if policy.get("expected_security_lock_sha256") != lock_sha:
        raise FunctionalRegressionError("policy_lock_sha_mismatch")
    if int(policy.get("expected_security_lock_package_count", 0)) != EXPECTED_PACKAGE_COUNT:
        raise FunctionalRegressionError("policy_lock_package_count_mismatch")

    certified = _as_mapping(policy.get("certified_evidence"), name="certified_evidence")
    clean = _as_mapping(
        certified.get("security_clean_resolution"), name="security_clean_resolution"
    )
    if clean.get("security_status") != "clean":
        raise FunctionalRegressionError("certified_security_status_not_clean")
    if int(clean.get("pip_audit_exit_code", -1)) != 0:
        raise FunctionalRegressionError("certified_pip_audit_failed")
    if int(clean.get("known_vulnerability_count", -1)) != 0:
        raise FunctionalRegressionError("certified_vulnerability_count_nonzero")
    if int(clean.get("resolved_package_count", 0)) != EXPECTED_PACKAGE_COUNT:
        raise FunctionalRegressionError("certified_package_count_mismatch")
    if clean.get("hashed_lock_sha256") != lock_sha:
        raise FunctionalRegressionError("certified_lock_sha_mismatch")
    certified_packages = _as_mapping(clean.get("packages"), name="certified_packages")
    for package_name, expected_version in EXPECTED_PACKAGES.items():
        if certified_packages.get(package_name) != expected_version:
            raise FunctionalRegressionError(
                f"certified_anchor_mismatch:{package_name}:{certified_packages.get(package_name)}"
            )

    advisories = _as_mapping(policy.get("security_advisories"), name="security_advisories")
    _validate_advisory(advisories, "GHSA-gqvg-gmmx-x4hm", EXPECTED_GHSA)
    _validate_advisory(advisories, "CVE-2026-69247", EXPECTED_CRYPTOGRAPHY_CVE)

    functional = _as_mapping(
        policy.get("p08_functional_regression"), name="p08_functional_regression"
    )
    expected_functional_values: dict[str, Any] = {
        "required": True,
        "schema_version": SCHEMA_VERSION,
        "exact_certified_graph_required": True,
        "qlib_contrib_model_required": True,
        "real_fit_predict_required": True,
        "fallback_allowed": False,
        "network_mode": "none",
        "repository_mount": "readonly",
    }
    for key, expected_value in expected_functional_values.items():
        if functional.get(key) != expected_value:
            raise FunctionalRegressionError(f"functional_contract_mismatch:{key}")

    functional_status = functional.get("status")
    if functional_status not in {"pending_ci_certification", "ci_certified"}:
        raise FunctionalRegressionError("unexpected_functional_regression_status")
    expected_p08_allowed = functional_status == "ci_certified"
    if policy.get("p08_allowed") is not expected_p08_allowed:
        raise FunctionalRegressionError("p08_allowed_inconsistent_with_functional_status")
    if policy.get("qlib_security_gate_bypassed") is not False:
        raise FunctionalRegressionError("qlib_security_gate_bypassed")
    if policy.get("p08_security_gate_bypassed") is not False:
        raise FunctionalRegressionError("p08_security_gate_bypassed")
    if policy.get("p08_security_gate_remains_blocked") is not (not expected_p08_allowed):
        raise FunctionalRegressionError("p08_blocked_state_inconsistent")

    if functional_status == "ci_certified":
        run_id = functional.get("certified_workflow_run_id")
        proof_sha = functional.get("certified_proof_commit_sha")
        if not isinstance(run_id, int) or run_id <= 0:
            raise FunctionalRegressionError("missing_certified_workflow_run_id")
        if not isinstance(proof_sha, str) or SHA_RE.fullmatch(proof_sha) is None:
            raise FunctionalRegressionError("invalid_certified_proof_commit_sha")

    return {
        "policy_version": str(policy.get("policy_version", "")),
        "functional_status": str(functional_status),
        "p08_allowed": expected_p08_allowed,
        "lock_sha256": lock_sha,
        "advisories_explicitly_enforced": [
            "GHSA-gqvg-gmmx-x4hm",
            "CVE-2026-69247",
        ],
    }


def validate_static_contract(project_root: Path) -> dict[str, Any]:
    root = project_root.resolve()
    lock_summary = parse_security_lock(root / SECURITY_LOCK_PATH)
    policy = _read_json(root / POLICY_PATH)
    return validate_policy_contract(policy, lock_summary)


def _installed_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for package_name, expected_version in EXPECTED_PACKAGES.items():
        try:
            actual_version = importlib.metadata.version(package_name)
        except importlib.metadata.PackageNotFoundError as exc:
            raise FunctionalRegressionError(f"installed_package_missing:{package_name}") from exc
        if actual_version != expected_version:
            raise FunctionalRegressionError(
                "installed_package_version_mismatch:"
                f"{package_name}:{actual_version}:{expected_version}"
            )
        versions[package_name] = actual_version
    return versions


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
    if os.environ.get("SMARTCRYPTO_QLIB_NETWORK_MODE") != "none":
        raise FunctionalRegressionError("network_mode_not_declared_none")
    if os.environ.get("SMARTCRYPTO_QLIB_REPOSITORY_MOUNT") != "readonly":
        raise FunctionalRegressionError("repository_mount_not_declared_readonly")
    if os.environ.get("MLFLOW_ALLOW_PICKLE_DESERIALIZATION", "").lower() != "false":
        raise FunctionalRegressionError("mlflow_pickle_deserialization_control_not_false")

    installed_versions = _installed_versions()
    try:
        np = importlib.import_module("numpy")
        pd = importlib.import_module("pandas")
        qlib = importlib.import_module("qlib")
        linear_module = importlib.import_module("qlib.contrib.model.linear")
    except Exception as exc:
        raise FunctionalRegressionError(f"qlib_import_failed:{type(exc).__name__}:{exc}") from exc

    if str(getattr(qlib, "__version__", "")) != EXPECTED_PACKAGES["pyqlib"]:
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
            "exact_certified_graph": True,
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
