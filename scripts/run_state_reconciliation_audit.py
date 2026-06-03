from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smartcrypto.state.reconciliation_guard import (  # noqa: E402
    DEFAULT_AUDIT_REPORT_PATH,
    run_state_reconciliation_audit,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audita StateRepository/ReconciliationGuard paper/shadow sem alterar estado."
    )
    parser.add_argument("--repository", required=True, help="Caminho do StateRepository local JSON/SQLite.")
    parser.add_argument("--snapshot", help="Snapshot externo local opcional em json/csv/parquet/jsonl.")
    parser.add_argument("--report", default=str(DEFAULT_AUDIT_REPORT_PATH), help="Relatório JSON de saída.")
    parser.add_argument("--strict", action="store_true", help="Converte warnings em bloqueio.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = run_state_reconciliation_audit(
        repository_path=args.repository,
        snapshot_path=args.snapshot,
        report_path=args.report,
        strict=args.strict,
        runtime_mode="paper",
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 1 if report.get("status") in {"blocked", "missing_data"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
