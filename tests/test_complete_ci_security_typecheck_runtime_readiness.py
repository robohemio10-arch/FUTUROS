from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def make_target_body(target: str) -> str:
    text = read("Makefile")
    pattern = rf"^{re.escape(target)}:\n(?P<body>(?:\t.*\n)+)"
    match = re.search(pattern, text, flags=re.MULTILINE)
    assert match, f"missing Makefile target {target}"
    return match.group("body")


def load_manifest_module():
    path = ROOT / "scripts" / "generate_project_manifest.py"
    spec = importlib.util.spec_from_file_location("generate_project_manifest", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_makefile_typecheck_is_not_noop() -> None:
    body = make_target_body("typecheck")

    assert "python -m mypy" in body or "$(PYTHON) -m mypy" in body
    assert "find_spec" not in body
    assert "else 0" not in body


def test_makefile_lint_is_not_noop() -> None:
    body = make_target_body("lint")

    assert "ruff check" in body
    assert "find_spec" not in body
    assert "else 0" not in body


def test_makefile_security_is_not_noop() -> None:
    body = make_target_body("security")

    assert "bandit" in body
    assert "pip_audit" in body
    assert "scan_versioned_secrets.py" in body
    assert "--skip B608,B310" not in body
    assert "find_spec" not in body
    assert "else 0" not in body


def test_requirements_lock_contains_security_lint_and_typecheck_tools() -> None:
    text = read("requirements-dev.lock").lower()

    assert "mypy==" in text
    assert "types-pyyaml==" in text
    assert "ruff==" in text
    assert "bandit==" in text
    assert "pip-audit==" in text
    assert "pyarrow==23.0.1" in text
    assert "pytest==9.0.3" in text
    assert "streamlit==1.54.0" in text


def test_pyproject_exposes_tools_in_test_and_dev_extras() -> None:
    payload = __import__("tomllib").loads(read("pyproject.toml"))
    optional = payload["project"]["optional-dependencies"]
    for extra in ("test", "dev"):
        deps = "\n".join(optional[extra]).lower()
        assert "mypy" in deps
        assert "types-pyyaml" in deps
        assert "ruff" in deps
        assert "bandit" in deps
        assert "pip-audit" in deps
        assert "pyarrow>=23.0.1" in deps


def test_ci_contains_lint_typecheck_security_secret_scan_docker_and_healthcheck() -> None:
    text = read(".github/workflows/ci.yml")

    assert "python -m pip install --require-hashes -r requirements-dev.lock" in text
    assert "make lint" in text
    assert "make typecheck" in text
    assert "make security" in text
    assert "make audit" in text
    assert "make paper-check" in text
    assert "scan_versioned_secrets.py" in read("Makefile")
    assert "docker build -f docker/smartcrypto/Dockerfile" in text
    assert "docker build -f docker/dashboard/Dockerfile" in text
    assert "docker build -f docker/qlib/Dockerfile" in text
    assert "smartcrypto.runtime.container_healthcheck" in text
    assert "scripts/generate_project_manifest.py --check" in text


def test_incremental_lint_typecheck_and_security_scope_is_documented() -> None:
    doc = read("docs/COMPLETE_CI_SECURITY_TYPECHECK_RUNTIME_READINESS.md")

    assert "ruff check ." in doc
    assert "mypy smartcrypto --ignore-missing-imports" in doc
    assert "bandit -q -r smartcrypto scripts" in doc
    assert "smartcrypto/runtime" in doc
    assert "smartcrypto/config/runtime_safety_config.py" in doc
    assert "smartcrypto/ops/backup_restore.py" in doc
    assert "smartcrypto/ops/system_healthcheck.py" in doc
    assert "accepted_legacy_debt" in doc
    assert "High severity dentro do escopo ativo nao e aceito" in doc


def test_security_exception_backlog_has_required_fields() -> None:
    text = read("docs/security_audit_exceptions.md")

    for token in (
        "Date",
        "Classification",
        "Rule/advisory",
        "Package",
        "Reason",
        "Plan",
        "accepted_legacy_debt",
        "B608",
        "B310",
        "2026-06-06",
    ):
        assert token in text

    assert "# nosec" in text
    assert "--skip" in text
    assert "nao sao excecoes ativas do gate atual" in text


def test_ci_does_not_use_secrets_or_enable_live() -> None:
    text = read(".github/workflows/ci.yml")
    forbidden = [
        "secrets.",
        "LIVE_ENABLED: \"true\"",
        "ORDER_SUBMISSION_ENABLED: \"true\"",
        "REAL_ORDER_SUBMISSION_ENABLED: \"true\"",
        "SMARTCRYPTO_EXCHANGE_PRIVATE_ACCESS: \"true\"",
    ]

    assert not any(token in text for token in forbidden)
    assert 'SMARTCRYPTO_RUNTIME_MODE: paper' in text
    assert 'LIVE_ENABLED: "false"' in text
    assert 'ORDER_SUBMISSION_ENABLED: "false"' in text
    assert 'REAL_ORDER_SUBMISSION_ENABLED: "false"' in text


def test_project_manifest_is_coherent_and_deterministic() -> None:
    module = load_manifest_module()
    expected = module.build_manifest(ROOT)
    actual = json.loads(read("PROJECT_MANIFEST_CLEAN.json"))

    assert actual == expected
    assert actual["generated_by_script"] == "scripts/generate_project_manifest.py"
    assert actual["deterministic"] is True
    assert actual["manifest_version"] >= 3
    assert actual["hash_strategy"] == "sha256 over canonical text LF content or raw binary bytes"
    assert actual["byte_count_strategy"] == "canonical content bytes"
    assert actual["runtime_artifacts_not_in_zip"] is True
    assert actual["aggregate_sha256"] == expected["aggregate_sha256"]
    assert actual["counts"]["tracked_files_total"] == len(
        subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True).splitlines()
    )
    assert actual["counts"]["python_files"] > 0
    assert actual["counts"]["test_files"] > 0
    assert actual["counts"]["docs_files"] > 0
    assert actual["counts"]["dockerfiles"] >= 4
    assert actual["counts"]["workflows"] >= 1
    assert all(not path["path"].startswith(("data/", "logs/", "models/", "reports/")) for path in actual["files"])
    assert all("\\" not in path["path"] for path in actual["files"])
    assert {path["hash_mode"] for path in actual["files"]} <= {"text_lf", "binary_raw"}


def test_project_manifest_build_is_stable_across_calls() -> None:
    module = load_manifest_module()

    assert module.build_manifest(ROOT) == module.build_manifest(ROOT)


def test_manifest_text_hash_normalizes_crlf_and_lf(tmp_path: Path) -> None:
    module = load_manifest_module()
    lf_path = tmp_path / "lf.txt"
    crlf_path = tmp_path / "crlf.txt"
    lf_path.write_bytes(b"a\nb\n")
    crlf_path.write_bytes(b"a\r\nb\r\n")

    lf_content, lf_mode = module.canonical_file_content(lf_path)
    crlf_content, crlf_mode = module.canonical_file_content(crlf_path)

    assert lf_content == crlf_content == b"a\nb\n"
    assert lf_mode == crlf_mode == "text_lf"
    assert module.file_sha256(lf_path) == module.file_sha256(crlf_path)


def test_manifest_binary_hash_preserves_raw_bytes(tmp_path: Path) -> None:
    module = load_manifest_module()
    binary_path = tmp_path / "blob.bin"
    binary_path.write_bytes(b"a\r\n\x00b\r\n")

    content, mode = module.canonical_file_content(binary_path)

    assert content == b"a\r\n\x00b\r\n"
    assert mode == "binary_raw"


def test_bitradex_dockerfile_has_non_root_user_and_healthcheck() -> None:
    dockerfile = ROOT / "bitradex_realtime_candle_collector_v1" / "Dockerfile"
    text = dockerfile.read_text(encoding="utf-8")
    users = [line.split(maxsplit=1)[1].strip().lower() for line in text.splitlines() if line.strip().upper().startswith("USER ")]

    assert dockerfile.exists()
    assert users
    assert users[-1] not in {"root", "0"}
    assert users[-1] == "bitradex"
    assert "HEALTHCHECK" in text
    assert "chown -R bitradex:bitradex /app /ms-playwright" in text


def test_operational_python_has_no_hardcoded_windows_project_root() -> None:
    offenders: list[str] = []
    for base in (ROOT / "scripts", ROOT / "smartcrypto"):
        for path in base.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if "E:/FUTUROS" in text or "E:\\FUTUROS" in text:
                offenders.append(str(path.relative_to(ROOT)).replace("\\", "/"))

    assert offenders == []


def test_runtime_safety_flags_remain_blocked_in_composes_and_ci() -> None:
    for compose_path in ("docker-compose.paper.yml", "docker-compose.live.example.yml"):
        payload = yaml.safe_load(read(compose_path))
        for service in payload["services"].values():
            if service.get("image", "").startswith("redis"):
                continue
            env = service.get("environment") or {}
            assert env["SMARTCRYPTO_RUNTIME_MODE"] == "paper"
            assert env["LIVE_ENABLED"] == "false"
            assert env["ORDER_SUBMISSION_ENABLED"] == "false"
            assert env["REAL_ORDER_SUBMISSION_ENABLED"] == "false"

    ci = read(".github/workflows/ci.yml")
    assert "create_order" not in ci
    assert "fetch_balance" not in ci
    assert "private_get" not in ci
