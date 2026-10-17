"""Read-only source provenance preflight, never an authorization to execute F04."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.data.trader_master_fingerprint_v2.source_authority_preflight import (  # noqa: E402
    audit_paper_financial_source_authority,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True, help="Absolute snapshot read boundary.")
    parser.add_argument("--sqlite-path", required=True, help="Explicit existing snapshot; no discovery.")
    parser.add_argument("--source-profile", required=True, help="Existing F03 financial profile.")
    parser.add_argument("--source-authorization", required=True, help="Existing F04 declaration, not trust.")
    parser.add_argument("--origin-evidence", required=True, help="Independently reviewed origin evidence.")
    parser.add_argument("--effective-config", required=True, help="Existing observed effective config.")
    parser.add_argument("--export-receipt", required=True, help="Existing immutable snapshot custody receipt.")
    args = parser.parse_args(argv)
    report = audit_paper_financial_source_authority(
        project_root=args.project_root, sqlite_path=args.sqlite_path,
        source_profile_path=args.source_profile, authorization_path=args.source_authorization,
        origin_path=args.origin_evidence, effective_config_path=args.effective_config,
        export_receipt_path=args.export_receipt,
    )
    print(json.dumps(report, sort_keys=True, allow_nan=False))
    return 0 if report["status"] == "VALIDATED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
