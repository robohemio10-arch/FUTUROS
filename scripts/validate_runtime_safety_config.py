from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smartcrypto.config.runtime_safety_config import (  # noqa: E402
    DEFAULT_REPORT_PATH,
    build_runtime_safety_report,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Valida schema/config/runtime safety em modo paper/shadow read-only."
    )
    parser.add_argument("--config", required=True, help="Arquivo YAML/JSON de configuração a validar.")
    parser.add_argument("--environment", required=True, help="Ambiente lógico: paper, shadow, backtest, research ou live.example.")
    parser.add_argument("--report", default=str(DEFAULT_REPORT_PATH), help="Caminho do relatório JSON.")
    parser.add_argument("--strict", action="store_true", help="Converte warnings de segurança em bloqueio.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_runtime_safety_report(
        config_path=args.config,
        environment=args.environment,
        report_path=args.report,
        strict=args.strict,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 1 if report.get("status") in {"blocked", "invalid_schema", "missing_config"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
