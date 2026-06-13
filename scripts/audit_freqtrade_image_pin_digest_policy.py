from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any


POLICY_PATH = "docs/FREQTRADE_IMAGE_PIN_DIGEST_POLICY_V1.md"
FOLLOW_UP_BRANCH = "codex/freqtrade-image-digest-resolution-v1"
FREQTRADE_IMAGE_PATTERN = re.compile(
    r"freqtradeorg/freqtrade(?::(?P<tag>[^@\s\"'}]+))?(?:@sha256:(?P<digest>[^\s\"'}]+))?",
    re.IGNORECASE,
)
VALID_DIGEST_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
VARIABLE_PATTERN = re.compile(r"\$\{[^}]*FREQTRADE[^}]*\}|\$[A-Za-z_]*FREQTRADE[A-Za-z0-9_]*", re.IGNORECASE)
OBVIOUS_FAKE_DIGESTS = {
    "0" * 64,
    "1" * 64,
    "a" * 64,
    "f" * 64,
    "deadbeef" * 8,
    "abcdef0123456789" * 4,
}
SAFETY_FLAGS = {
    "paper_only": True,
    "shadow_only": True,
    "sends_orders": False,
    "changes_risk": False,
    "exchange_private_access": False,
    "live_trading_enabled": False,
    "order_submission_enabled": False,
    "real_order_submission_enabled": False,
    "canary_release_allowed": False,
    "live_release_allowed": False,
}


def load_versioned_file_discovery() -> ModuleType:
    module_name = "smartcrypto.ops.versioned_file_discovery"
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name not in {"smartcrypto", "smartcrypto.ops", module_name}:
            raise

    discovery_path = Path(__file__).resolve().parents[1] / "smartcrypto" / "ops" / "versioned_file_discovery.py"
    spec = importlib.util.spec_from_file_location(module_name, discovery_path)
    if spec is None or spec.loader is None:
        raise ModuleNotFoundError(f"cannot_load_standalone_module:{discovery_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        if sys.modules.get(module_name) is module:
            del sys.modules[module_name]
        raise
    return module


_DISCOVERY_MODULE = load_versioned_file_discovery()
discover_versioned_files = _DISCOVERY_MODULE.discover_versioned_files


def is_relevant_file(relative_path: str) -> bool:
    normalized = relative_path.replace("\\", "/")
    name = normalized.rsplit("/", maxsplit=1)[-1]
    if name == "Makefile":
        return True
    if normalized.startswith(".github/workflows/") and normalized.endswith((".yml", ".yaml")):
        return True
    if name.startswith("docker-compose") and normalized.endswith((".yml", ".yaml")):
        return True
    return name == "Dockerfile" or name.startswith("Dockerfile.") or name.endswith(".Dockerfile")


def policy_state(project_root: Path) -> tuple[bool, bool]:
    path = project_root / POLICY_PATH
    if not path.is_file():
        return False, False
    try:
        text = path.read_text(encoding="utf-8-sig").lower()
    except OSError:
        return False, False
    required_markers = (
        "temporary_exception_allowed: true",
        f"follow_up_branch: {FOLLOW_UP_BRANCH}",
        "paper_only: true",
        "shadow_only: true",
        "live_trading_enabled: false",
        "order_submission_enabled: false",
        "real_order_submission_enabled: false",
        "exchange_private_access: false",
        "sends_orders: false",
        "changes_risk: false",
    )
    documented = all(marker in text for marker in required_markers)
    return documented, documented and "policy_status: temporary_exception" in text


def digest_is_valid(digest: str | None) -> bool:
    if digest is None or not VALID_DIGEST_PATTERN.fullmatch(digest):
        return False
    return digest.lower() not in OBVIOUS_FAKE_DIGESTS


def image_expression(line: str) -> str | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if stripped.lower().startswith("from "):
        return stripped.split(maxsplit=2)[1]
    image_match = re.match(r"(?:-\s*)?image\s*:\s*(.+)$", stripped, re.IGNORECASE)
    if image_match:
        return image_match.group(1).split(" #", maxsplit=1)[0].strip()
    if "freqtradeorg/freqtrade" in stripped.lower() or VARIABLE_PATTERN.search(stripped):
        return stripped.split("#", maxsplit=1)[0].strip()
    return None


def yaml_context(lines: list[str], line_index: int, relative_path: str) -> str:
    for index in range(line_index - 1, -1, -1):
        line = lines[index]
        stripped = line.strip()
        indentation = len(line) - len(line.lstrip())
        if stripped.endswith(":") and indentation in {0, 2, 4}:
            return stripped[:-1]
    return relative_path.rsplit("/", maxsplit=1)[-1]


def classify_reference(
    *,
    expression: str,
    match: re.Match[str] | None,
    policy_documented: bool,
    temporary_exception_allowed: bool,
) -> dict[str, Any]:
    variable_image = bool(VARIABLE_PATTERN.search(expression))
    tag = match.group("tag") if match else None
    digest = match.group("digest") if match else None
    digest_present = "@sha256:" in expression.lower()
    digest_valid = digest_is_valid(digest)
    normalized_tag = (tag or ("variable" if variable_image else "latest")).lower()
    latest = normalized_tag == "latest" or (tag is None and not variable_image and not digest_present)
    stable = normalized_tag == "stable"
    mutable_tag = stable or latest or variable_image

    if digest_present and not digest_valid:
        severity = "high"
        recommendation = "Replace the invalid or placeholder digest with a registry-verified sha256 digest."
    elif latest:
        severity = "high"
        recommendation = "Replace latest with a validated version tag and immutable sha256 digest."
    elif digest_valid:
        severity = "ok"
        recommendation = "Keep the validated tag and digest; re-audit when intentionally upgrading Freqtrade."
    elif variable_image:
        severity = "medium" if policy_documented and temporary_exception_allowed else "high"
        recommendation = "Resolve and validate the variable to a versioned Freqtrade image with an immutable digest."
    elif stable:
        severity = "medium" if policy_documented and temporary_exception_allowed else "high"
        recommendation = "Resolve stable to a validated version tag and immutable digest in the follow-up branch."
    else:
        severity = "low" if policy_documented and temporary_exception_allowed else "high"
        recommendation = "Add the registry-verified immutable digest for this versioned Freqtrade tag."

    return {
        "tag": normalized_tag,
        "digest_present": digest_present,
        "digest_valid": digest_valid,
        "mutable_tag": mutable_tag,
        "variable_image": variable_image,
        "severity": severity,
        "recommendation": recommendation,
    }


def scan_file(
    path: Path,
    relative_path: str,
    *,
    policy_documented: bool,
    temporary_exception_allowed: bool,
) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    references: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, start=1):
        expression = image_expression(line)
        if expression is None:
            continue
        match = FREQTRADE_IMAGE_PATTERN.search(expression)
        if match is None and not VARIABLE_PATTERN.search(expression):
            continue
        classification = classify_reference(
            expression=expression,
            match=match,
            policy_documented=policy_documented,
            temporary_exception_allowed=temporary_exception_allowed,
        )
        references.append(
            {
                "file": relative_path,
                "line": line_number,
                "service_or_context": yaml_context(lines, line_number - 1, relative_path),
                "image": expression.strip("\"'"),
                **classification,
            }
        )
    return references


def audit_project(project_root: Path) -> dict[str, Any]:
    root = project_root.resolve()
    discovery = discover_versioned_files(root)
    relevant_files = sorted(path for path in discovery.files if is_relevant_file(path) and (root / path).is_file())
    policy_documented, temporary_exception_allowed = policy_state(root)
    references: list[dict[str, Any]] = []
    for relative_path in relevant_files:
        references.extend(
            scan_file(
                root / relative_path,
                relative_path,
                policy_documented=policy_documented,
                temporary_exception_allowed=temporary_exception_allowed,
            )
        )
    references.sort(key=lambda item: (item["file"], item["line"], item["image"]))

    invalid_digest_count = sum(item["digest_present"] and not item["digest_valid"] for item in references)
    latest_tag_count = sum(item["tag"] == "latest" for item in references)
    stable_tag_count = sum(item["tag"] == "stable" for item in references)
    digest_pinned_count = sum(item["digest_valid"] for item in references)
    unpinned_count = sum(not item["digest_valid"] for item in references)
    high_count = sum(item["severity"] == "high" for item in references)

    if invalid_digest_count:
        status, reason = "blocked", "invalid_or_placeholder_freqtrade_digest_detected"
    elif latest_tag_count:
        status, reason = "blocked", "latest_freqtrade_tag_detected"
    elif high_count:
        status, reason = "blocked", "mutable_freqtrade_image_without_documented_policy"
    elif unpinned_count:
        status, reason = "warning", "temporary_freqtrade_digest_exception_documented"
    else:
        status, reason = "ok", "all_freqtrade_images_digest_pinned" if references else "no_freqtrade_image_reference"

    return {
        "status": status,
        "reason": reason,
        "scanned_files": len(relevant_files),
        "file_discovery_mode": discovery.mode,
        "file_discovery_source": discovery.source,
        "freqtrade_image_references": references,
        "unpinned_count": unpinned_count,
        "stable_tag_count": stable_tag_count,
        "latest_tag_count": latest_tag_count,
        "digest_pinned_count": digest_pinned_count,
        "invalid_digest_count": invalid_digest_count,
        "policy_documented": policy_documented,
        "temporary_exception_allowed": temporary_exception_allowed,
        "follow_up_branch": FOLLOW_UP_BRANCH,
        **SAFETY_FLAGS,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit immutable pin/digest policy for versioned Freqtrade images.")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = audit_project(Path(args.project_root))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        print(f"{report['status']}: {report['reason']} ({report['unpinned_count']} unpinned references)")
    return 1 if report["status"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
