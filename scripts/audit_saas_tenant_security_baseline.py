from __future__ import annotations

import argparse
import json
from pathlib import Path

from smartcrypto.ops.saas_tenant_security_baseline import (
    DEFAULT_ACCESS_POLICY_PATH,
    DEFAULT_OUTPUT_PATH,
    DEFAULT_TENANT_REGISTRY_PATH,
    build_saas_tenant_security_baseline,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Gera baseline read-only de segurança SaaS/multi-tenant.")
    parser.add_argument("--project-root", default=".", help="Raiz do projeto FUTUROS.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_PATH))
    parser.add_argument("--tenant-registry-path", default=str(DEFAULT_TENANT_REGISTRY_PATH))
    parser.add_argument("--access-policy-path", default=str(DEFAULT_ACCESS_POLICY_PATH))
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = build_saas_tenant_security_baseline(
        project_root=Path(args.project_root),
        output=Path(args.output),
        tenant_registry_path=Path(args.tenant_registry_path),
        access_policy_path=Path(args.access_policy_path),
        no_write=args.no_write,
    )
    if args.json:
        print(json.dumps(result.report, indent=2, sort_keys=True))
    else:
        print(
            json.dumps(
                {
                    "status": result.report["status"],
                    "output": str(result.output_path),
                    "write_performed": result.write_performed,
                    "paper_only": result.report["paper_only"],
                    "shadow_only": result.report["shadow_only"],
                    "live_release_allowed": result.report["live_release_allowed"],
                    "canary_release_allowed": result.report["canary_release_allowed"],
                    "tenant_runtime_mutation_allowed": result.report["tenant_runtime_mutation_allowed"],
                    "sends_orders": result.report["sends_orders"],
                    "changes_risk": result.report["changes_risk"],
                },
                sort_keys=True,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
