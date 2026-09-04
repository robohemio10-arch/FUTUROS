from __future__ import annotations

import argparse
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

PIN_RE = re.compile(r"^([A-Za-z0-9_.-]+(?:\[[^]]+\])?)==([^;\s]+)$")


def active_pins(text: str) -> list[tuple[str, str, str]]:
    pins: list[tuple[str, str, str]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = PIN_RE.fullmatch(line)
        if not match:
            raise ValueError(f"non_exact_pin:{line}")
        display, version = match.groups()
        package = display.split("[", 1)[0]
        pins.append((display, package, version))
    return pins


def pypi_hashes(package: str, version: str) -> list[str]:
    quoted_name = urllib.parse.quote(package, safe="")
    quoted_version = urllib.parse.quote(version, safe="")
    request = urllib.request.Request(
        f"https://pypi.org/pypi/{quoted_name}/{quoted_version}/json",
        headers={"User-Agent": "smartcrypto-hermetic-lock-v1"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    hashes = sorted({str(item.get("digests", {}).get("sha256", "")) for item in payload.get("urls", []) if item.get("digests", {}).get("sha256")})
    if not hashes:
        raise ValueError(f"no_pypi_sha256:{package}=={version}")
    return hashes


def render_hashed_lock(pins: list[tuple[str, str, str]]) -> str:
    lines = ["# Hermetic lock generated from exact pins. Install with --require-hashes."]
    for display, package, version in pins:
        hashes = pypi_hashes(package, version)
        if len(hashes) == 1:
            lines.append(f"{display}=={version} --hash=sha256:{hashes[0]}")
            continue
        lines.append(f"{display}=={version} \\")
        for index, digest in enumerate(hashes):
            suffix = " \\" if index < len(hashes) - 1 else ""
            lines.append(f"    --hash=sha256:{digest}{suffix}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input")
    parser.add_argument("output")
    args = parser.parse_args()
    source = Path(args.input)
    destination = Path(args.output)
    destination.write_text(render_hashed_lock(active_pins(source.read_text(encoding="utf-8-sig"))), encoding="utf-8")
    print(json.dumps({"status": "ok", "input": str(source), "output": str(destination)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
