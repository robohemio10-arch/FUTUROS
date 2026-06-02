from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.ml.ai_shadow_incremental_trainer import (  # noqa: E402
    DEFAULT_INPUT_PATH,
    DEFAULT_MODEL_DIR,
    DEFAULT_REPORT_PATH,
    train_ai_shadow_incremental_model,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Train paper/shadow-only AI Shadow incremental challenger model.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT_PATH))
    parser.add_argument("--model-dir", default=str(DEFAULT_MODEL_DIR))
    parser.add_argument("--report", default=str(DEFAULT_REPORT_PATH))
    parser.add_argument("--strict", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = train_ai_shadow_incremental_model(
        input_path=Path(args.input),
        model_dir=Path(args.model_dir),
        report_path=Path(args.report),
        strict=bool(args.strict),
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, default=str))
    return 0 if report.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
