from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}


class OCRDependencyError(RuntimeError):
    pass


def load_ocr_dependencies() -> tuple[Any, Any, Any, Any]:
    try:
        import cv2  # type: ignore
        import pytesseract  # type: ignore
        from PIL import Image, ImageOps  # type: ignore
    except Exception as exc:
        raise OCRDependencyError(
            "ocr_engine_unavailable: install optional OCR dependencies "
            "(opencv-python, pillow, pytesseract and local Tesseract binary) "
            "or run with --dry-run for review-only discovery"
        ) from exc
    return cv2, pytesseract, Image, ImageOps


def configure_tesseract(pytesseract_module: Any) -> None:
    candidates = [
        Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
        Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
    ]

    for candidate in candidates:
        if candidate.exists():
            pytesseract_module.pytesseract.tesseract_cmd = str(candidate)
            return


def json_safe(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [json_safe(v) for v in obj]
    if isinstance(obj, tuple):
        return [json_safe(v) for v in obj]
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        if np.isnan(obj) or np.isinf(obj):
            return None
        return float(obj)
    return obj


def list_images(input_dir: Path) -> list[Path]:
    input_dir = input_dir.resolve()

    if not input_dir.exists():
        raise FileNotFoundError(f"Pasta de imagens não encontrada: {input_dir}")

    files = [
        p.resolve()
        for p in input_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
    ]

    return sorted(files, key=lambda p: p.name)


def load_image_cv2_or_pil(path: Path, cv2_module: Any, image_module: Any, image_ops_module: Any) -> np.ndarray:
    path = path.resolve()

    if not path.exists():
        raise FileNotFoundError(f"Imagem não encontrada: {path}")

    image = cv2_module.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2_module.IMREAD_COLOR)

    if image is not None:
        return image

    try:
        pil = image_module.open(path)
        pil = image_ops_module.exif_transpose(pil).convert("RGB")
        arr = np.array(pil)
        return cv2_module.cvtColor(arr, cv2_module.COLOR_RGB2BGR)
    except Exception as exc:
        raise RuntimeError(f"Falha ao abrir imagem via cv2 e PIL: {path} | {exc}") from exc


def preprocess_variants(image: np.ndarray, cv2_module: Any) -> dict[str, np.ndarray]:
    variants: dict[str, np.ndarray] = {}

    variants["original"] = image

    upscaled = cv2_module.resize(
        image,
        None,
        fx=2.0,
        fy=2.0,
        interpolation=cv2_module.INTER_CUBIC,
    )
    variants["upscaled"] = upscaled

    gray = cv2_module.cvtColor(upscaled, cv2_module.COLOR_BGR2GRAY)
    variants["gray_upscaled"] = gray

    denoised = cv2_module.fastNlMeansDenoising(gray, None, 10, 7, 21)
    variants["gray_denoised"] = denoised

    _, otsu = cv2_module.threshold(
        denoised,
        0,
        255,
        cv2_module.THRESH_BINARY + cv2_module.THRESH_OTSU,
    )
    variants["otsu"] = otsu

    adaptive = cv2_module.adaptiveThreshold(
        denoised,
        255,
        cv2_module.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2_module.THRESH_BINARY,
        31,
        11,
    )
    variants["adaptive"] = adaptive

    sharp_kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])
    sharp = cv2_module.filter2D(denoised, -1, sharp_kernel)
    variants["sharp"] = sharp

    return variants


def to_pil(arr: np.ndarray, cv2_module: Any, image_module: Any) -> Any:
    if len(arr.shape) == 2:
        return image_module.fromarray(arr)

    rgb = cv2_module.cvtColor(arr, cv2_module.COLOR_BGR2RGB)
    return image_module.fromarray(rgb)


def ocr_variant(
    arr: np.ndarray,
    lang: str,
    psm: int,
    cv2_module: Any,
    image_module: Any,
    pytesseract_module: Any,
) -> tuple[str, float]:
    pil_img = to_pil(arr, cv2_module=cv2_module, image_module=image_module)

    config = f"--oem 3 --psm {psm}"

    data = pytesseract_module.image_to_data(
        pil_img,
        lang=lang,
        config=config,
        output_type=pytesseract_module.Output.DICT,
    )

    words = []
    confs = []

    for text, conf in zip(data.get("text", []), data.get("conf", [])):
        text = str(text).strip()

        if not text:
            continue

        words.append(text)

        try:
            c = float(conf)
            if c >= 0:
                confs.append(c)
        except Exception:
            pass

    raw_text = " ".join(words)
    mean_conf = float(np.mean(confs)) if confs else 0.0

    return raw_text, mean_conf


def score_ocr_text(text: str, confidence: float) -> float:
    upper = text.upper()

    tokens = [
        "BTC",
        "ETH",
        "USDT",
        "PNL",
        "P&L",
        "LONG",
        "SHORT",
        "BUY",
        "SELL",
        "ENTRADA",
        "SAÍDA",
        "SAIDA",
        "PREÇO",
        "PRECO",
        "ENTRY",
        "EXIT",
        "CLOSE",
        "OPEN",
        "FUTURES",
        "MARGIN",
        "CLOSED",
    ]

    token_score = sum(1 for token in tokens if token in upper)
    numeric_score = len(re.findall(r"[-+]?\d+[.,]?\d*", text))

    return confidence + token_score * 15.0 + numeric_score * 1.5 + min(len(text), 2500) * 0.01


def best_ocr(
    image: np.ndarray,
    lang: str,
    cv2_module: Any,
    image_module: Any,
    pytesseract_module: Any,
) -> dict[str, Any]:
    variants = preprocess_variants(image, cv2_module=cv2_module)
    candidates = []

    for name, arr in variants.items():
        for psm in [6, 11, 12]:
            try:
                text, conf = ocr_variant(
                    arr,
                    lang=lang,
                    psm=psm,
                    cv2_module=cv2_module,
                    image_module=image_module,
                    pytesseract_module=pytesseract_module,
                )
                score = score_ocr_text(text, conf)

                candidates.append({
                    "variant": name,
                    "psm": psm,
                    "text": text,
                    "confidence": conf,
                    "score": score,
                })
            except Exception as exc:
                candidates.append({
                    "variant": name,
                    "psm": psm,
                    "text": "",
                    "confidence": 0.0,
                    "score": -1.0,
                    "error": str(exc),
                })

    if not candidates:
        return {
            "variant": None,
            "psm": None,
            "text": "",
            "confidence": 0.0,
            "score": 0.0,
        }

    return max(candidates, key=lambda x: x["score"])


def normalize_number(value: str) -> float | None:
    if value is None:
        return None

    s = str(value).strip().replace(" ", "")

    if not s:
        return None

    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")

    try:
        return float(s)
    except Exception:
        return None


def parse_symbol(text: str) -> str | None:
    upper = text.upper().replace("/", "").replace("_", "").replace("-", "").replace(" ", "")

    if "BTCUSDT" in upper:
        return "BTCUSDT"

    if "ETHUSDT" in upper:
        return "ETHUSDT"

    if "BTC" in upper and "USDT" in upper:
        return "BTCUSDT"

    if "ETH" in upper and "USDT" in upper:
        return "ETHUSDT"

    return None


def parse_side(text: str) -> str | None:
    upper = text.upper()

    if any(token in upper for token in ["LONG", "COMPRA", "BUY"]):
        return "LONG"

    if any(token in upper for token in ["SHORT", "VENDA", "SELL"]):
        return "SHORT"

    return None


def parse_datetime_candidates(text: str) -> list[str]:
    patterns = [
        r"\b\d{4}[-/]\d{2}[-/]\d{2}[ T]\d{2}:\d{2}(?::\d{2})?\b",
        r"\b\d{2}[-/]\d{2}[-/]\d{4}[ T]\d{2}:\d{2}(?::\d{2})?\b",
        r"\b\d{2}[-/]\d{2}[-/]\d{2}[ T]\d{2}:\d{2}(?::\d{2})?\b",
    ]

    out = []

    for pattern in patterns:
        out.extend(re.findall(pattern, text))

    return list(dict.fromkeys(out))


def extract_labeled_number(text: str, labels: list[str]) -> float | None:
    escaped = [re.escape(label) for label in labels]

    pattern = (
        r"(?i)\b("
        + "|".join(escaped)
        + r")\b[^-+0-9]{0,40}([-+]?\d{1,9}(?:[.,]\d{1,8})?)"
    )

    match = re.search(pattern, text)

    if not match:
        return None

    return normalize_number(match.group(2))


def extract_all_numbers(text: str) -> list[float]:
    raw = re.findall(r"[-+]?\d{1,9}(?:[.,]\d{1,8})?", text)
    nums = []

    for item in raw:
        value = normalize_number(item)

        if value is not None:
            nums.append(value)

    return nums


def parse_fields(text: str) -> dict[str, Any]:
    symbol = parse_symbol(text)
    side = parse_side(text)
    datetimes = parse_datetime_candidates(text)

    pnl = extract_labeled_number(
        text,
        ["PNL", "P&L", "Lucro", "Resultado", "Profit", "Realized PnL", "Realizado"],
    )

    entry_price = extract_labeled_number(
        text,
        ["Entrada", "Entry", "Open", "Preço Entrada", "Preco Entrada", "Avg Entry"],
    )

    exit_price = extract_labeled_number(
        text,
        ["Saida", "Saída", "Exit", "Close", "Preço Saída", "Preco Saida", "Avg Exit"],
    )

    all_numbers = extract_all_numbers(text)

    return {
        "parsed_symbol": symbol,
        "parsed_side": side,
        "datetime_candidates": " | ".join(datetimes),
        "first_datetime_candidate": datetimes[0] if datetimes else None,
        "parsed_pnl_candidate": pnl,
        "parsed_entry_price_candidate": entry_price,
        "parsed_exit_price_candidate": exit_price,
        "numeric_candidates": " | ".join(str(x) for x in all_numbers[:60]),
        "numeric_candidate_count": len(all_numbers),
    }


def build_discovery_row(idx: int, image_path: Path, status: str, error: str | None = None) -> dict[str, Any]:
    return {
        "image_index": idx,
        "file_name": image_path.name,
        "image_path": str(image_path),
        "raw_text_path": None,
        "ocr_variant": None,
        "ocr_psm": None,
        "ocr_confidence": 0.0,
        "ocr_score": 0.0,
        "ocr_text": "",
        "review_status": status,
        "error": error,
        "parsed_symbol": None,
        "parsed_side": None,
        "datetime_candidates": None,
        "first_datetime_candidate": None,
        "parsed_pnl_candidate": None,
        "parsed_entry_price_candidate": None,
        "parsed_exit_price_candidate": None,
        "numeric_candidates": None,
        "numeric_candidate_count": 0,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=str, required=True)
    parser.add_argument("--output-dir", type=str, required=True)
    parser.add_argument("--report", type=str, default=None)
    parser.add_argument("--report-dir", type=str, default=None)
    parser.add_argument("--lang", type=str, default="eng")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-xlsx", action="store_true")
    args = parser.parse_args(argv)

    ocr_dependency_error: str | None = None
    cv2_module = pytesseract_module = image_module = image_ops_module = None
    if not args.dry_run:
        try:
            cv2_module, pytesseract_module, image_module, image_ops_module = load_ocr_dependencies()
            configure_tesseract(pytesseract_module)
        except OCRDependencyError as exc:
            ocr_dependency_error = str(exc)

    input_dir = Path(args.input_dir).resolve()
    output_dir = Path(args.output_dir).resolve()
    report_path = (
        Path(args.report).resolve()
        if args.report
        else (Path(args.report_dir).resolve() if args.report_dir else output_dir) / "bitradex_ocr_summary.json"
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    images = list_images(input_dir)

    rows = []

    raw_text_dir = output_dir / "raw_text"
    raw_text_dir.mkdir(parents=True, exist_ok=True)

    for idx, image_path in enumerate(images, start=1):
        if args.dry_run:
            rows.append(build_discovery_row(idx, image_path, "DISCOVERED_DRY_RUN"))
            continue
        if ocr_dependency_error:
            rows.append(build_discovery_row(idx, image_path, "OCR_ENGINE_UNAVAILABLE", ocr_dependency_error))
            continue
        try:
            image = load_image_cv2_or_pil(image_path, cv2_module, image_module, image_ops_module)
            best = best_ocr(image, lang=args.lang, cv2_module=cv2_module, image_module=image_module, pytesseract_module=pytesseract_module)
            text = best.get("text", "") or ""

            parsed = parse_fields(text)

            raw_txt_path = raw_text_dir / f"{image_path.stem}.txt"
            raw_txt_path.write_text(text, encoding="utf-8")

            row = {
                "image_index": idx,
                "file_name": image_path.name,
                "image_path": str(image_path),
                "raw_text_path": str(raw_txt_path),
                "ocr_variant": best.get("variant"),
                "ocr_psm": best.get("psm"),
                "ocr_confidence": best.get("confidence"),
                "ocr_score": best.get("score"),
                "ocr_text": text,
                "review_status": "REVIEW_REQUIRED",
                **parsed,
            }

        except Exception as exc:
            row = {
                "image_index": idx,
                "file_name": image_path.name,
                "image_path": str(image_path),
                "raw_text_path": None,
                "ocr_variant": None,
                "ocr_psm": None,
                "ocr_confidence": 0.0,
                "ocr_score": 0.0,
                "ocr_text": "",
                "review_status": "OCR_ERROR",
                "error": str(exc),
                "parsed_symbol": None,
                "parsed_side": None,
                "datetime_candidates": None,
                "first_datetime_candidate": None,
                "parsed_pnl_candidate": None,
                "parsed_entry_price_candidate": None,
                "parsed_exit_price_candidate": None,
                "numeric_candidates": None,
                "numeric_candidate_count": 0,
            }

        rows.append(row)

    df = pd.DataFrame(rows)

    raw_csv = output_dir / "bitradex_ocr_raw.csv"
    review_xlsx = output_dir / "bitradex_ocr_review.xlsx"
    review_csv = output_dir / "bitradex_ocr_review.csv"

    df.to_csv(raw_csv, index=False, encoding="utf-8-sig")
    df.to_csv(review_csv, index=False, encoding="utf-8-sig")

    xlsx_written = False
    xlsx_error = None
    if not args.no_xlsx:
        try:
            with pd.ExcelWriter(review_xlsx, engine="openpyxl") as writer:
                df.to_excel(writer, sheet_name="OCR_REVIEW", index=False)
            xlsx_written = True
        except Exception as exc:
            xlsx_error = str(exc)

    summary = {
        "status": "ok",
        "mode": "bitradex_image_ocr_to_review_v2",
        "review_only": True,
        "safety": {
            "writes_trades_master": False,
            "changes_training_dataset": False,
            "sends_orders": False,
            "live_trading": False,
            "changes_docker": False,
            "manual_review_required": True,
        },
        "dry_run": bool(args.dry_run),
        "ocr_dependency_error": ocr_dependency_error,
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "images_found": int(len(images)),
        "rows": int(len(df)),
        "review_status_counts": df["review_status"].value_counts().to_dict() if len(df) else {},
        "parsed_symbols": df["parsed_symbol"].value_counts(dropna=False).astype(int).to_dict() if len(df) else {},
        "parsed_sides": df["parsed_side"].value_counts(dropna=False).astype(int).to_dict() if len(df) else {},
        "outputs": {
            "raw_csv": str(raw_csv),
            "review_csv": str(review_csv),
            "review_xlsx": str(review_xlsx) if xlsx_written else None,
            "review_xlsx_error": xlsx_error,
            "summary_json": str(report_path),
            "raw_text_dir": str(raw_text_dir),
        },
    }

    report_path.write_text(
        json.dumps(json_safe(summary), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(json.dumps(json_safe(summary), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
