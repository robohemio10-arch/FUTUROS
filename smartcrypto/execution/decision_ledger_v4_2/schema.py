"""JSON Schema Draft 2020-12 support for payload 4.2."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

from .contracts import PAYLOAD_ADAPTER, SCHEMA_VERSION

SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
SCHEMA_ID = "https://smart-futuros.local/schemas/decision-ledger-payload-v4-2.json"
BUNDLED_SCHEMA_PATH = Path(__file__).with_name(
    "decision_ledger_payload_v4_2.schema.json"
)


def build_payload_json_schema() -> dict[str, Any]:
    schema = PAYLOAD_ADAPTER.json_schema()
    schema["$schema"] = SCHEMA_DIALECT
    schema["$id"] = SCHEMA_ID
    schema["title"] = "SMART FUTUROS Decision Ledger Payload 4.2"
    schema["x-schema-version"] = SCHEMA_VERSION
    return schema


def load_bundled_payload_json_schema() -> dict[str, Any]:
    return json.loads(BUNDLED_SCHEMA_PATH.read_text(encoding="utf-8"))


def write_payload_json_schema(path: str | Path) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(
        f".{destination.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    payload = json.dumps(
        build_payload_json_schema(),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    ) + "\n"
    temporary.write_text(payload, encoding="utf-8")
    os.replace(temporary, destination)
    return destination
