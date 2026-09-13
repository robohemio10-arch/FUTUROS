"""Observe explicit V3 sources once; default no-write and no operational authority."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--activation", type=Path, required=True)
    parser.add_argument("--freeze", type=Path, required=True)
    parser.add_argument("--signals", type=Path)
    parser.add_argument("--outcomes", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write-prospective-evidence", action="store_true")
    mode.add_argument("--no-write", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from smartcrypto.learning.qlib_v3_prospective import run_cycle

    def absolute(path: Path | None) -> Path | None:
        return args.project_root / path if path is not None and not path.is_absolute() else path

    report = run_cycle(project_root=args.project_root.absolute(),
                       activation_path=args.project_root / args.activation,
                       freeze_path=args.project_root / args.freeze,
                       signals_path=absolute(args.signals), outcomes_path=absolute(args.outcomes),
                       write=args.write_prospective_evidence)
    print(json.dumps(report, sort_keys=True, allow_nan=False))
    return 1 if report["status"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
