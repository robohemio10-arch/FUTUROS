"""JSON Schema generation for P0.4B sandbox mapping profile."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from .contracts import RuntimeProjectionRecordV1

SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
SCHEMA_ID = "urn:smart-futuros:decision-ledger-runtime-profile:v1"


def build_runtime_profile_schema() -> dict[str, Any]:
    schema = TypeAdapter(RuntimeProjectionRecordV1).json_schema(
        union_format="any_of",
    )
    schema["$schema"] = SCHEMA_DIALECT
    schema["$id"] = SCHEMA_ID
    schema["title"] = "SMART FUTUROS Decision Ledger Runtime Profile V1"
    schema["x-profile-version"] = (
        "decision_ledger_runtime_observability_profile_v1"
    )
    schema["x-activation-state"] = "sandbox_mapping_only"
    schema["x-runtime-integration-allowed"] = False
    return schema


def write_runtime_profile_schema(path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        build_runtime_profile_schema(),
        indent=2,
        sort_keys=True,
        ensure_ascii=False,
    ) + "\n"
    target.write_text(payload, encoding="utf-8")
    return target
