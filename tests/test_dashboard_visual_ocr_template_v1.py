from __future__ import annotations

import importlib.util
import ast
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "audit_dashboard_visual_ocr_template_v1.py"


def load_auditor() -> Any:
    spec = importlib.util.spec_from_file_location("dashboard_visual_ocr_template_v1", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_fake_png(path: Path) -> Path:
    path.write_bytes(b"not-a-real-image-but-path-validation-does-not-decode-when-runner-is-injected")
    return path


def all_terms_for_region(region: Any) -> str:
    return "\n".join(region.expected_terms)


def test_template_passes_with_injected_region_ocr(tmp_path: Path) -> None:
    module = load_auditor()
    image = write_fake_png(tmp_path / "aba02.png")
    output = tmp_path / "reports" / "aba02_ocr.json"

    report = module.audit_visual_template(
        image=image,
        page=module.SUPPORTED_PAGE,
        template=module.SUPPORTED_TEMPLATE,
        output=output,
        no_write_runtime=True,
        ocr_runner=lambda _image, region: all_terms_for_region(region),
    )

    assert report["status"] == "ok"
    assert report["validation_status"] == "pass"
    assert report["missing_terms"] == []
    assert report["forbidden_usage_guard"]["uses_numeric_values_as_truth"] is False
    assert report["forbidden_usage_guard"]["writes_data_runtime"] is False
    assert report["safety"]["dashboard_readonly"] is True
    assert report["safety"]["sends_orders"] is False
    assert report["safety"]["changes_risk"] is False


def test_missing_guardrail_blocks_validation(tmp_path: Path) -> None:
    module = load_auditor()
    image = write_fake_png(tmp_path / "aba02.png")
    output = tmp_path / "reports" / "aba02_ocr.json"

    def runner(_image: Path, region: Any) -> str:
        if region.name == "topbar":
            return "PAPER / SHADOW ONLY\nORDER SUBMISSION DISABLED\nREADINESS BLOCKED\nRISKMANAGER AUTHORITY"
        return all_terms_for_region(region)

    report = module.audit_visual_template(
        image=image,
        page=module.SUPPORTED_PAGE,
        template=module.SUPPORTED_TEMPLATE,
        output=output,
        no_write_runtime=True,
        ocr_runner=runner,
    )

    assert report["status"] == "blocked"
    assert report["validation_status"] == "fail"
    assert "topbar:LIVE LOCKED" in report["missing_terms"]


def test_normalization_handles_accents_and_punctuation() -> None:
    module = load_auditor()

    assert module.normalize_text("Portfólio e Risco") == "PORTFOLIO E RISCO"
    assert module.term_found("02. PORTFÓLIO E RISCO", "02 PORTFOLIO E RISCO") is True
    assert module.term_found("Sem create_order", "Sem create order") is True


def test_output_under_data_runtime_is_blocked(tmp_path: Path) -> None:
    module = load_auditor()

    output = tmp_path / "data" / "runtime" / "visual_ocr.json"

    try:
        module.validate_output_path(output, no_write_runtime=True)
    except module.AuditError as exc:
        assert str(exc).startswith("runtime_output_forbidden")
    else:
        raise AssertionError("data/runtime output must be blocked")


def test_unsupported_page_returns_controlled_audit_error(tmp_path: Path) -> None:
    module = load_auditor()
    image = write_fake_png(tmp_path / "aba03.png")

    try:
        module.audit_visual_template(
            image=image,
            page="03_grid_monitor",
            template=module.SUPPORTED_TEMPLATE,
            output=tmp_path / "out.json",
            no_write_runtime=True,
            ocr_runner=lambda _image, region: all_terms_for_region(region),
        )
    except module.AuditError as exc:
        assert str(exc) == "unsupported_page:03_grid_monitor"
    else:
        raise AssertionError("unsupported page must fail with controlled error")


def test_report_write_is_deterministic(tmp_path: Path) -> None:
    module = load_auditor()
    image = write_fake_png(tmp_path / "aba02.png")
    output = tmp_path / "report.json"

    first = module.audit_visual_template(
        image=image,
        page=module.SUPPORTED_PAGE,
        template=module.SUPPORTED_TEMPLATE,
        output=output,
        no_write_runtime=True,
        ocr_runner=lambda _image, region: all_terms_for_region(region),
    )
    second = module.audit_visual_template(
        image=image,
        page=module.SUPPORTED_PAGE,
        template=module.SUPPORTED_TEMPLATE,
        output=output,
        no_write_runtime=True,
        ocr_runner=lambda _image, region: all_terms_for_region(region),
    )

    assert first == second
    module.write_report(output, first)
    rendered_once = output.read_text(encoding="utf-8")
    module.write_report(output, second)
    assert output.read_text(encoding="utf-8") == rendered_once


def test_expected_terms_do_not_use_numeric_financial_values_as_truth() -> None:
    module = load_auditor()
    joined = "\n".join(module.expected_terms())

    forbidden_values = ["3,214.87", "1,247.33", "11,101.18", "-15.21", "+124.37"]
    assert all(value not in joined for value in forbidden_values)


def test_cli_missing_image_emits_controlled_json(tmp_path: Path) -> None:
    output = tmp_path / "report.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--image",
            str(tmp_path / "missing.png"),
            "--page",
            "02_portfolio_risk",
            "--template",
            "aba02_portfolio_risk_visual_ocr_template_v1",
            "--output",
            str(output),
            "--no-write-runtime",
            "--json",
        ],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
    )

    assert completed.returncode == 1
    payload = json.loads(completed.stdout)
    assert payload["status"] == "blocked"
    assert payload["validation_status"] == "fail"
    assert payload["reason"].startswith("image_missing:")
    assert payload["safety"]["sends_orders"] is False


def test_auditor_source_has_no_external_dispatch_or_trading_calls() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(SCRIPT))
    findings: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in {"ccxt", "requests", "httpx", "aiohttp"}:
                    findings.append(f"import:{alias.name}")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in {"ccxt", "requests", "httpx", "aiohttp"}:
                findings.append(f"import:{node.module}")
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in {
                "create_order",
                "cancel_order",
                "fetch_balance",
                "fetch_open_orders",
            }:
                findings.append(f"call:{node.func.id}")
            if isinstance(node.func, ast.Attribute) and node.func.attr in {"post", "create_task"}:
                findings.append(f"attribute_call:{node.func.attr}")

    assert findings == []
    assert "shell=True" not in source
    assert "os.environ" not in source
    assert "st.secrets" not in source
