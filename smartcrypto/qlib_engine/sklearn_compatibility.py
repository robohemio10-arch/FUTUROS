from __future__ import annotations

import warnings
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import joblib

try:
    import sklearn
    from sklearn.exceptions import InconsistentVersionWarning
except Exception:  # pragma: no cover - sklearn is expected in this project.
    sklearn = None  # type: ignore[assignment]
    InconsistentVersionWarning = Warning  # type: ignore[assignment,misc]


VERSION_KEYS = (
    "sklearn_artifact_version",
    "sklearn_version",
    "training_sklearn_version",
    "model_sklearn_version",
    "_sklearn_version",
)


@dataclass(frozen=True)
class SklearnCompatibilityReport:
    status: str
    reason: str | None
    sklearn_runtime_version: str | None
    sklearn_artifact_version: str | None
    warning_count: int
    warnings: list[str]
    strict: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def runtime_sklearn_version() -> str | None:
    return getattr(sklearn, "__version__", None) if sklearn is not None else None


def detect_artifact_sklearn_version(payload: Any) -> str | None:
    if isinstance(payload, dict):
        for key in VERSION_KEYS:
            value = payload.get(key)
            if value:
                return str(value)
        metadata = payload.get("metadata")
        nested = detect_artifact_sklearn_version(metadata)
        if nested:
            return nested

    value = getattr(payload, "_sklearn_version", None)
    if value:
        return str(value)
    return None


def evaluate_sklearn_compatibility(
    *,
    artifact_version: str | None,
    warning_messages: list[Any] | None = None,
    strict: bool = False,
    runtime_version: str | None = None,
) -> SklearnCompatibilityReport:
    current = runtime_version if runtime_version is not None else runtime_sklearn_version()
    warning_messages = warning_messages or []
    warnings_text = [str(message) for message in warning_messages]
    warning_artifact_versions = [
        str(getattr(message, "original_sklearn_version"))
        for message in warning_messages
        if getattr(message, "original_sklearn_version", None)
    ]
    detected_artifact = artifact_version or (warning_artifact_versions[0] if warning_artifact_versions else None)

    mismatch = bool(current and detected_artifact and current != detected_artifact)
    has_inconsistent_warning = bool(warning_messages)
    if mismatch or has_inconsistent_warning:
        status = "incompatible" if strict else "warning"
        reason = "sklearn_version_mismatch"
    elif current and detected_artifact and current == detected_artifact:
        status = "ok"
        reason = None
    else:
        status = "unknown"
        reason = "sklearn_artifact_version_unknown"

    return SklearnCompatibilityReport(
        status=status,
        reason=reason,
        sklearn_runtime_version=current,
        sklearn_artifact_version=detected_artifact,
        warning_count=len(warning_messages),
        warnings=warnings_text,
        strict=bool(strict),
    )


def load_sklearn_artifact(
    path: str | Path,
    *,
    strict: bool = False,
) -> tuple[Any, SklearnCompatibilityReport]:
    caught_messages: list[Any] = []
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", InconsistentVersionWarning)
        payload = joblib.load(Path(path))
    for item in caught:
        if isinstance(item.message, InconsistentVersionWarning):
            caught_messages.append(item.message)

    report = evaluate_sklearn_compatibility(
        artifact_version=detect_artifact_sklearn_version(payload),
        warning_messages=caught_messages,
        strict=strict,
    )
    return payload, report
