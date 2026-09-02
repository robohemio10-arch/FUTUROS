"""Read-only discovery of configured Freqtrade services from Compose files."""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

import yaml

from .contracts import FleetDiscoveredService, require_utc


def discover_freqtrade_services_from_compose(
    compose_path: Path,
    *,
    project_root: Path,
    decision_time_utc: datetime,
) -> tuple[FleetDiscoveredService, ...]:
    """Return configured Freqtrade services without contacting Docker or any network."""

    decision = require_utc(decision_time_utc)
    root = project_root.resolve()
    path = compose_path.resolve()
    if not path.is_file() or path.is_symlink():
        raise ValueError("compose_path_missing_or_invalid")
    raw = path.read_bytes()
    payload = yaml.safe_load(raw.decode("utf-8-sig"))
    if not isinstance(payload, Mapping):
        raise ValueError("compose_root_must_be_mapping")
    services = payload.get("services")
    if not isinstance(services, Mapping):
        raise ValueError("compose_services_missing")
    source_hash = hashlib.sha256(raw).hexdigest()
    try:
        display_path = path.relative_to(root).as_posix()
    except ValueError:
        display_path = path.as_posix()

    discovered: list[FleetDiscoveredService] = []
    for service_name_raw, config_raw in services.items():
        service_name = str(service_name_raw).strip()
        config: Mapping[str, Any] = config_raw if isinstance(config_raw, Mapping) else {}
        image_raw = config.get("image")
        image = None if image_raw is None else str(image_raw).strip()
        is_freqtrade = "freqtrade" in service_name.lower() or (
            image is not None and "freqtrade" in image.lower()
        )
        if not is_freqtrade:
            continue
        discovered.append(
            FleetDiscoveredService(
                service_name=service_name,
                compose_path=display_path,
                image=image,
                discovered_at_utc=decision,
                available_at_utc=decision,
                source_hash=source_hash,
            )
        )
    return tuple(sorted(discovered, key=lambda item: item.service_name))
