from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smartcrypto.data.dataset_manifest import (  # noqa: E402
    DEFAULT_MANIFEST_PATH,
    build_dataset_manifest,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a deterministic dataset manifest for SmartCrypto artifacts."
    )
    parser.add_argument("--inputs", nargs="+", required=True)
    parser.add_argument("--output", default=str(DEFAULT_MANIFEST_PATH))
    parser.add_argument("--dataset-role", default="dataset")
    parser.add_argument("--timestamp-column")
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    manifest = build_dataset_manifest(
        inputs=args.inputs,
        output_path=args.output,
        dataset_role=args.dataset_role,
        timestamp_column=args.timestamp_column,
        strict=args.strict,
    )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 1 if manifest.get("status") in {"blocked", "missing_input"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
