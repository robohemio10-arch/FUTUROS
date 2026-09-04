from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def report_to_lock(payload: dict) -> str:
    packages: dict[str, tuple[str, str]] = {}
    for item in payload.get("install", []):
        metadata = item.get("metadata", {})
        name = str(metadata.get("name", "")).strip()
        version = str(metadata.get("version", "")).strip()
        if name and version:
            packages[normalize(name)] = (name, version)
    if not packages:
        raise ValueError("resolver_report_has_no_packages")
    lines = ["# Exact transitive lock generated from pip --report."]
    lines.extend(f"{name}=={version}" for _, (name, version) in sorted(packages.items()))
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("report")
    parser.add_argument("output")
    args = parser.parse_args()
    payload = json.loads(Path(args.report).read_text(encoding="utf-8"))
    Path(args.output).write_text(report_to_lock(payload), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
