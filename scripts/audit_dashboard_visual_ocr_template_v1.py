from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable, Literal, TypedDict

SCHEMA_VERSION = "dashboard_visual_ocr_template_audit_v1"
SUPPORTED_PAGE = "02_portfolio_risk"
SUPPORTED_TEMPLATE = "aba02_portfolio_risk_visual_ocr_template_v1"
OCR_ENGINE_NAME = "tesseract"
OCR_LANGUAGE = "eng"
OCR_TIMEOUT_SECONDS = 60
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"}

Status = Literal["ok", "blocked"]
ValidationStatus = Literal["pass", "fail"]


class AuditError(Exception):
    """Controlled audit failure that can be emitted as JSON."""


@dataclass(frozen=True)
class VisualRegion:
    name: str
    bounds: tuple[float, float, float, float]
    psm: str
    expected_terms: tuple[str, ...]


class RegionReport(TypedDict):
    expected_terms: list[str]
    found_terms: list[str]
    missing_terms: list[str]
    raw_text: str
    normalized_text: str


class ForbiddenUsageGuard(TypedDict):
    uses_numeric_values_as_truth: bool
    changes_runtime_state: bool
    changes_risk: bool
    sends_orders: bool
    runs_inside_streamlit_page: bool
    writes_data_runtime: bool
    exchange_private_access: bool


class SafetyFlags(TypedDict):
    dashboard_readonly: bool
    paper_only: bool
    shadow_only: bool
    live_trading_enabled: bool
    live_release_allowed: bool
    canary_release_allowed: bool
    order_submission_enabled: bool
    real_order_submission_enabled: bool
    exchange_private_access: bool
    sends_orders: bool
    sends_notifications: bool
    changes_risk: bool
    changes_model: bool


OcrRunner = Callable[[Path, VisualRegion], str]


REGIONS: tuple[VisualRegion, ...] = (
    VisualRegion(
        name="topbar",
        bounds=(0.0, 0.0, 1.0, 0.06),
        psm="6",
        expected_terms=(
            "PAPER / SHADOW ONLY",
            "LIVE LOCKED",
            "ORDER SUBMISSION DISABLED",
            "READINESS BLOCKED",
            "RISKMANAGER AUTHORITY",
        ),
    ),
    VisualRegion(
        name="sidebar",
        bounds=(0.0, 0.055, 0.125, 0.965),
        psm="6",
        expected_terms=(
            "01. Infraestrutura",
            "02. Portfólio e Risco",
            "03. Grid Spot Monitor",
            "04. Oportunidades",
            "05. IA / Governance",
            "06. Controles Ativos",
            "07. Relatórios & TCA",
            "08. Alertas & Mensageria",
        ),
    ),
    VisualRegion(
        name="header",
        bounds=(0.125, 0.055, 1.0, 0.12),
        psm="6",
        expected_terms=(
            "02. PORTFÓLIO E RISCO",
            "Visão consolidada de capital, exposição e risco",
        ),
    ),
    VisualRegion(
        name="capital_summary",
        bounds=(0.13, 0.12, 1.0, 0.235),
        psm="6",
        expected_terms=(
            "1. RESUMO DE CAPITAL",
            "Saldo Disponível",
            "Saldo Bloqueado em Ordens",
            "Capital Reservado pelo Ledger",
            "Exposição Total em Cripto",
            "Patrimônio Líquido Estimado",
            "Reconciliação",
        ),
    ),
    VisualRegion(
        name="allocation_exposure",
        bounds=(0.13, 0.235, 0.55, 0.505),
        psm="6",
        expected_terms=(
            "2. ALOCAÇÃO E EXPOSIÇÃO POR ATIVO",
            "Alocação (% do PL)",
            "Exposição por Ativo",
            "Ativo",
            "Notional (USDT)",
            "% do PL",
            "PnL Flutuante",
            "Status Risco",
        ),
    ),
    VisualRegion(
        name="pnl",
        bounds=(0.55, 0.235, 1.0, 0.505),
        psm="6",
        expected_terms=(
            "3. PNL REALIZADO E NÃO REALIZADO",
            "Realizado 24h",
            "Realizado 7d",
            "Realizado 30d",
            "Realizado Total",
            "Não Realizado (Flutuante)",
            "Curva de Patrimônio Líquido",
            "Drawdown (%)",
        ),
    ),
    VisualRegion(
        name="drawdown_controls",
        bounds=(0.13, 0.505, 0.41, 0.77),
        psm="6",
        expected_terms=(
            "4. DRAWDOWN E CONTROLES DE RISCO",
            "Max Drawdown",
            "Drawdown Atual",
            "Drawdown Duration",
            "Recovery Time",
            "Capital Preso",
            "Distância até Break-even",
            "Risk Mode",
            "Safety Orders Bloqueadas",
            "Kill Switch",
            "Reduce-Only Mode",
            "Protection Mode",
            "Limite Diário de Perda",
        ),
    ),
    VisualRegion(
        name="var_cvar_tail_risk",
        bounds=(0.41, 0.505, 0.71, 0.77),
        psm="6",
        expected_terms=(
            "5. VAR / CVAR / RISCO DE CAUDA",
            "Métrica",
            "95%",
            "99%",
            "VaR Paramétrico",
            "VaR Histórico",
            "CVaR / Expected Shortfall",
            "Risk of Ruin",
            "Stress Scenario: Flash Crash",
        ),
    ),
    VisualRegion(
        name="financial_source_of_truth",
        bounds=(0.71, 0.505, 1.0, 0.77),
        psm="6",
        expected_terms=(
            "6. FONTE DE VERDADE FINANCEIRA",
            "StateRepository",
            "CapitalReservationLedger",
            "PositionRepository",
            "OrderRepository",
            "ReconciliationRepository",
            "Status Geral de Reconciliação",
        ),
    ),
    VisualRegion(
        name="risk_events",
        bounds=(0.13, 0.77, 0.71, 0.965),
        psm="6",
        expected_terms=(
            "7. EVENTOS RECENTES DE RISCO",
            "Horário (UTC)",
            "Severidade",
            "Categoria",
            "Evento",
            "Detalhes",
            "Status",
        ),
    ),
    VisualRegion(
        name="reconciliation_integrity",
        bounds=(0.71, 0.77, 1.0, 0.965),
        psm="6",
        expected_terms=(
            "8. RECONCILIAÇÃO E INTEGRIDADE",
            "Divergência Ledger vs Exchange",
            "Divergência Posições vs Exchange",
            "Ordens Desconhecidas",
            "Partial Fills Não Reconhecidos",
            "Dispatch Unknown",
            "Integridade Geral",
        ),
    ),
    VisualRegion(
        name="footer",
        bounds=(0.0, 0.965, 1.0, 1.0),
        psm="6",
        expected_terms=(
            "Dashboard Read-only",
            "Sem ccxt",
            "Sem create_order",
            "Sem OrderManager direto",
            "Sem live trading",
            "Snapshot: dashboard_portfolio_risk_snapshot.json",
        ),
    ),
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit a SMART FUTUROS dashboard visual template with local Tesseract OCR."
    )
    parser.add_argument("--image", required=True, help="Local dashboard screenshot path.")
    parser.add_argument("--page", required=True, help="Canonical dashboard page id.")
    parser.add_argument("--template", required=True, help="Visual OCR template id.")
    parser.add_argument("--output", required=True, help="JSON report output path.")
    parser.add_argument(
        "--no-write-runtime",
        action="store_true",
        help="Block any attempt to write under data/runtime.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON to stdout.")
    return parser.parse_args(argv)


def normalize_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    without_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    upper = without_accents.upper()
    normalized_chars = [ch if ch.isalnum() else " " for ch in upper]
    return " ".join("".join(normalized_chars).split())


def _term_tokens(term: str) -> list[str]:
    return [token for token in normalize_text(term).split() if token]


def expected_terms() -> list[str]:
    terms: list[str] = []
    for region in REGIONS:
        terms.extend(region.expected_terms)
    return terms


def _token_matches(expected: str, observed_tokens: set[str]) -> bool:
    if expected in observed_tokens:
        return True
    if len(expected) < 5:
        return False
    return any(SequenceMatcher(None, expected, observed).ratio() >= 0.8 for observed in observed_tokens)


def _token_coverage(term: str, text: str) -> float:
    term_tokens = _term_tokens(term)
    if not term_tokens:
        return 1.0
    text_tokens = set(_term_tokens(text))
    present = sum(1 for token in term_tokens if _token_matches(token, text_tokens))
    return present / len(term_tokens)


def term_found(term: str, text: str) -> bool:
    normalized_term = normalize_text(term)
    normalized_text = normalize_text(text)
    if not normalized_term:
        return True
    if normalized_term in normalized_text:
        return True
    compact_term = normalized_term.replace(" ", "")
    compact_text = normalized_text.replace(" ", "")
    if compact_term and compact_term in compact_text:
        return True

    term_tokens = _term_tokens(term)
    if len(term_tokens) == 1:
        return _token_matches(term_tokens[0], set(_term_tokens(text)))

    coverage = _token_coverage(term, text)
    if coverage >= 0.75:
        return True

    if len(normalized_term) >= 12:
        ratio = SequenceMatcher(None, normalized_term, normalized_text).quick_ratio()
        return ratio >= 0.86
    return False


def validate_image_path(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        raise AuditError(f"image_missing:{resolved}")
    if not resolved.is_file():
        raise AuditError(f"image_not_file:{resolved}")
    if resolved.suffix.lower() not in IMAGE_SUFFIXES:
        raise AuditError(f"unsupported_image_suffix:{resolved.suffix}")
    return resolved


def _has_data_runtime_part(path: Path) -> bool:
    parts = tuple(part.lower() for part in path.parts)
    for index, part in enumerate(parts[:-1]):
        if part == "data" and parts[index + 1] == "runtime":
            return True
    return False


def validate_output_path(path: Path, *, no_write_runtime: bool) -> Path:
    resolved = path.expanduser().resolve()
    if no_write_runtime and _has_data_runtime_part(resolved):
        raise AuditError(f"runtime_output_forbidden:{resolved}")
    if resolved.exists() and resolved.is_dir():
        raise AuditError(f"output_is_directory:{resolved}")
    return resolved


def validate_contract(page: str, template: str) -> None:
    if page != SUPPORTED_PAGE:
        raise AuditError(f"unsupported_page:{page}")
    if template != SUPPORTED_TEMPLATE:
        raise AuditError(f"unsupported_template:{template}")


def _load_pil_modules() -> tuple[object, object, object] | None:
    try:
        from PIL import Image, ImageOps, ImageStat
    except ModuleNotFoundError:
        return None
    return Image, ImageOps, ImageStat


def _crop_region_to_temp_png(image_path: Path, region: VisualRegion, temp_dir: Path) -> Path | None:
    pil_modules = _load_pil_modules()
    if pil_modules is None:
        return None
    Image, ImageOps, ImageStat = pil_modules
    image = Image.open(image_path)
    width, height = image.size
    left = max(0, min(width, int(width * region.bounds[0])))
    top = max(0, min(height, int(height * region.bounds[1])))
    right = max(left + 1, min(width, int(width * region.bounds[2])))
    bottom = max(top + 1, min(height, int(height * region.bounds[3])))

    crop = image.crop((left, top, right, bottom)).convert("L")
    mean_brightness = ImageStat.Stat(crop).mean[0]
    if mean_brightness < 128:
        crop = ImageOps.invert(crop)
    crop = ImageOps.autocontrast(crop)
    resized = crop.resize((crop.width * 3, crop.height * 3))

    output = temp_dir / f"{region.name}.png"
    resized.save(output)
    return output


def _run_tesseract(image_path: Path, *, psm: str) -> str:
    executable = shutil.which(OCR_ENGINE_NAME)
    if executable is None:
        raise AuditError("tesseract_not_found")
    completed = subprocess.run(
        [executable, str(image_path), "stdout", "-l", OCR_LANGUAGE, "--psm", psm],
        capture_output=True,
        check=False,
        text=True,
        timeout=OCR_TIMEOUT_SECONDS,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip().splitlines()[-1] if completed.stderr.strip() else "unknown"
        raise AuditError(f"tesseract_failed:{detail}")
    return completed.stdout


def default_ocr_runner(image_path: Path, region: VisualRegion) -> str:
    with tempfile.TemporaryDirectory(prefix="smart_futuros_visual_ocr_") as tmp:
        temp_dir = Path(tmp)
        cropped = _crop_region_to_temp_png(image_path, region, temp_dir)
        ocr_input = cropped if cropped is not None else image_path
        return _run_tesseract(ocr_input, psm=region.psm)


def build_region_report(region: VisualRegion, raw_text: str) -> RegionReport:
    found = [term for term in region.expected_terms if term_found(term, raw_text)]
    missing = [term for term in region.expected_terms if term not in found]
    return {
        "expected_terms": list(region.expected_terms),
        "found_terms": found,
        "missing_terms": missing,
        "raw_text": raw_text,
        "normalized_text": normalize_text(raw_text),
    }


def forbidden_usage_guard(*, writes_data_runtime: bool) -> ForbiddenUsageGuard:
    return {
        "uses_numeric_values_as_truth": False,
        "changes_runtime_state": False,
        "changes_risk": False,
        "sends_orders": False,
        "runs_inside_streamlit_page": False,
        "writes_data_runtime": writes_data_runtime,
        "exchange_private_access": False,
    }


def safety_flags() -> SafetyFlags:
    return {
        "dashboard_readonly": True,
        "paper_only": True,
        "shadow_only": True,
        "live_trading_enabled": False,
        "live_release_allowed": False,
        "canary_release_allowed": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "sends_orders": False,
        "sends_notifications": False,
        "changes_risk": False,
        "changes_model": False,
    }


def build_error_report(reason: str, *, page: str, template: str, image: str, output: str) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "template_id": template,
        "page": page,
        "source_image": image,
        "ocr_engine": OCR_ENGINE_NAME,
        "ocr_language": OCR_LANGUAGE,
        "status": "blocked",
        "reason": reason,
        "validation_status": "fail",
        "extracted_regions": {},
        "expected_terms": expected_terms(),
        "found_terms": [],
        "missing_terms": expected_terms(),
        "forbidden_usage_guard": forbidden_usage_guard(writes_data_runtime=False),
        "safety": safety_flags(),
        "output": output,
    }


def audit_visual_template(
    *,
    image: Path,
    page: str,
    template: str,
    output: Path,
    no_write_runtime: bool,
    ocr_runner: OcrRunner | None = None,
) -> dict[str, object]:
    validate_contract(page, template)
    image_path = validate_image_path(image)
    output_path = validate_output_path(output, no_write_runtime=no_write_runtime)
    runner = ocr_runner or default_ocr_runner

    extracted_regions: dict[str, RegionReport] = {}
    found_terms: list[str] = []
    missing_terms: list[str] = []

    for region in REGIONS:
        raw_text = runner(image_path, region)
        region_report = build_region_report(region, raw_text)
        extracted_regions[region.name] = region_report
        found_terms.extend(region_report["found_terms"])
        missing_terms.extend(f"{region.name}:{term}" for term in region_report["missing_terms"])

    validation_status: ValidationStatus = "pass" if not missing_terms else "fail"
    status: Status = "ok" if validation_status == "pass" else "blocked"
    reason = "visual_ocr_template_pass" if status == "ok" else "visual_ocr_template_missing_terms"

    writes_data_runtime = _has_data_runtime_part(output_path)
    return {
        "schema_version": SCHEMA_VERSION,
        "template_id": template,
        "page": page,
        "source_image": str(image_path),
        "ocr_engine": OCR_ENGINE_NAME,
        "ocr_language": OCR_LANGUAGE,
        "status": status,
        "reason": reason,
        "validation_status": validation_status,
        "extracted_regions": extracted_regions,
        "expected_terms": expected_terms(),
        "found_terms": found_terms,
        "missing_terms": missing_terms,
        "forbidden_usage_guard": forbidden_usage_guard(writes_data_runtime=writes_data_runtime),
        "safety": safety_flags(),
        "output": str(output_path),
    }


def write_report(path: Path, report: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = audit_visual_template(
            image=Path(args.image),
            page=args.page,
            template=args.template,
            output=Path(args.output),
            no_write_runtime=args.no_write_runtime,
        )
        write_report(Path(report["output"]), report)
    except (AuditError, OSError, subprocess.SubprocessError) as exc:
        report = build_error_report(
            str(exc), page=args.page, template=args.template, image=args.image, output=args.output
        )
        if not str(exc).startswith("runtime_output_forbidden"):
            try:
                output = validate_output_path(Path(args.output), no_write_runtime=args.no_write_runtime)
                write_report(output, report)
            except (AuditError, OSError):
                pass
        if args.json:
            print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 1

    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
