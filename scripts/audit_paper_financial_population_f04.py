"""Audit one explicitly authorized Paper financial cohort; JSON stdout only."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.data.trader_master_fingerprint_v2.population_evidence import (  # noqa: E402
    audit_paper_financial_population,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True, help="Absolute read boundary containing the snapshot.")
    parser.add_argument("--sqlite-path", required=True, help="Explicit absolute snapshot path; no discovery.")
    parser.add_argument("--source-profile", required=True, help="Absolute existing F03 profile path.")
    parser.add_argument(
        "--source-authorization", required=True,
        help="Absolute JSON authorization: schema paper_financial_population_source_authorization_f04_v1; "
        "read_authorized=true; sqlite_path; source_profile_sha256; source_id/account_scope_hash/"
        "paper_scope/epoch_id/cohort_id; exact closed_trade_ids; artifact_sha256 with db/wal/shm "
        "hashes (null only for absent sidecars). One CONTROL_A or TREATMENT_B scope only.",
    )
    args = parser.parse_args(argv)
    report = audit_paper_financial_population(
        project_root=args.project_root, sqlite_path=args.sqlite_path,
        source_profile_path=args.source_profile, authorization_path=args.source_authorization,
    )
    print(json.dumps(report, sort_keys=True, allow_nan=False))
    return 0 if report["status"] == "OK" else 2


if __name__ == "__main__":
    raise SystemExit(main())
