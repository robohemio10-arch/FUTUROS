from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

SAFE_FLAGS = {
    "paper_only": True,
    "shadow_only": True,
    "live_trading_enabled": False,
    "live_release_allowed": False,
    "canary_release_allowed": False,
    "order_submission_enabled": False,
    "real_order_submission_enabled": False,
    "exchange_private_access": False,
    "sends_orders": False,
    "changes_risk": False,
}

DEFAULT_UID = 10001
DEFAULT_GID = 10001


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bootstrap runtime permissions and drop privileges before running a SmartCrypto command."
    )
    parser.add_argument("--uid", type=int, default=DEFAULT_UID)
    parser.add_argument("--gid", type=int, default=DEFAULT_GID)
    parser.add_argument(
        "--path",
        action="append",
        default=[],
        help="Runtime path to create and chown. Can be supplied multiple times.",
    )
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)

    if not args.command:
        parser.error("missing command after --")

    if args.command and args.command[0] == "--":
        args.command = args.command[1:]

    if not args.command:
        parser.error("missing command after --")

    return args


def ensure_runtime_path(path: str, *, uid: int, gid: int) -> None:
    target = Path(path)
    target.mkdir(parents=True, exist_ok=True)

    for root, directories, files in os.walk(target):
        os.chown(root, uid, gid)
        os.chmod(root, 0o775)

        for directory in directories:
            item = Path(root) / directory
            os.chown(item, uid, gid)
            os.chmod(item, 0o775)

        for filename in files:
            item = Path(root) / filename
            os.chown(item, uid, gid)
            os.chmod(item, 0o664)


def drop_privileges(*, uid: int, gid: int) -> None:
    if os.geteuid() != 0:
        return

    os.setgid(gid)
    os.setuid(uid)
    os.environ["HOME"] = "/app"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(argv if argv is not None else sys.argv[1:]))

    for path in args.path:
        ensure_runtime_path(path, uid=args.uid, gid=args.gid)

    drop_privileges(uid=args.uid, gid=args.gid)

    os.execvp(args.command[0], args.command)
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
