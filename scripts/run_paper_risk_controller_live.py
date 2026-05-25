from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smartcrypto.risk.paper_risk_controller import (
    InputDataError,
    PaperRiskControllerError,
    SafetyViolation,
    run_paper_risk_controller,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Executa o paper risk controller institucional em modo shadow. "
            "Nao envia ordens, nao usa exchange e bloqueia flags live/real."
        )
    )
    parser.add_argument(
        "--config",
        default="config/paper_risk_controller.example.yml",
        help="Arquivo YAML seguro do paper risk controller.",
    )
    parser.add_argument(
        "--input",
        default=None,
        help="Dataset local de trades paper/importados (.csv, .json ou .parquet).",
    )
    parser.add_argument(
        "--since",
        default=None,
        help="Filtro ISO opcional para processar trades a partir de uma data/hora.",
    )
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="Executa a simulacao sem gravar artefatos em data/reports ou data/runtime.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    try:
        result = run_paper_risk_controller(
            config_path=args.config,
            input_path=args.input,
            since=args.since,
            write_outputs=not args.no_write,
        )
    except SafetyViolation as exc:
        raise SystemExit(f"SAFETY_BLOCKED: {exc}") from exc
    except (InputDataError, FileNotFoundError) as exc:
        raise SystemExit(f"INPUT_ERROR: {exc}") from exc
    except PaperRiskControllerError as exc:
        raise SystemExit(f"PAPER_RISK_CONTROLLER_ERROR: {exc}") from exc

    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
