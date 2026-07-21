"""Safe config loader for the disabled paper observability wiring."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import yaml

from .contracts import PaperObservabilityWiringConfigV1

MAX_CONFIG_BYTES = 256 * 1024


def load_observability_config(
    source: str | Path | Mapping[str, Any] | PaperObservabilityWiringConfigV1 | None,
) -> PaperObservabilityWiringConfigV1:
    if isinstance(source, PaperObservabilityWiringConfigV1):
        return source
    if isinstance(source, Mapping):
        return PaperObservabilityWiringConfigV1.model_validate(dict(source))
    if source is None:
        return PaperObservabilityWiringConfigV1()

    path = Path(source).expanduser()
    if not path.exists():
        return PaperObservabilityWiringConfigV1()
    if path.is_symlink():
        raise ValueError("observability_config_symlink_denied")
    if not path.is_file():
        raise ValueError("observability_config_not_regular_file")
    if path.suffix.casefold() not in {".yml", ".yaml"}:
        raise ValueError("observability_config_extension_invalid")
    if path.stat().st_size > MAX_CONFIG_BYTES:
        raise ValueError("observability_config_too_large")
    payload = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
    if not isinstance(payload, dict):
        raise ValueError("observability_config_root_must_be_mapping")
    return PaperObservabilityWiringConfigV1.model_validate(payload)
