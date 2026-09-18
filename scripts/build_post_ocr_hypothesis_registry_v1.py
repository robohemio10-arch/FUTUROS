"""Build the frozen WQ7 post-OCR hypothesis registry.

Research/paper/shadow only. The CLI consumes the exact frozen WQ7 method
artifact and candidate queue V2. It performs no training, candidate evaluation,
threshold search, model selection, runtime wiring, risk mutation, order
submission, or private exchange access.

No-write is the default. Registry persistence requires both --write and
--output. Repository runtime/data scopes are rejected for registry output.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _ensure_project_root() -> None:
    root = str(PROJECT_ROOT)

    if root not in sys.path:
        sys.path.insert(
            0,
            root,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
    )

    parser.add_argument(
        "--method-freeze",
        required=True,
        help=(
            "Exact WQ7 method-freeze V1 JSON."
        ),
    )

    parser.add_argument(
        "--queue-v2",
        required=True,
        help=(
            "Exact WQ7 candidate queue V2 JSON."
        ),
    )

    parser.add_argument(
        "--output",
        help=(
            "Explicit safe output path for the registry JSON. "
            "Ignored unless --write is supplied."
        ),
    )

    parser.add_argument(
        "--write",
        action="store_true",
        help=(
            "Persist the validated registry to --output."
        ),
    )

    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit a machine-readable CLI envelope."
        ),
    )

    return parser


def _resolve_output_path(
    raw: str,
) -> Path:
    candidate = Path(
        raw
    ).expanduser()

    if candidate.is_absolute():
        return candidate.resolve()

    return (
        PROJECT_ROOT
        / candidate
    ).resolve()


def _validate_output_path(
    output: Path,
) -> str | None:
    restricted = (
        PROJECT_ROOT / "data",
        PROJECT_ROOT / "runtime",
        PROJECT_ROOT / "reports",
        PROJECT_ROOT / "logs",
        PROJECT_ROOT / "freqtrade",
    )

    try:
        output.relative_to(
            PROJECT_ROOT
        )

    except ValueError:
        return None

    for directory in restricted:
        try:
            output.relative_to(
                directory.resolve()
            )

        except ValueError:
            continue

        return (
            "output_path_in_runtime_or_data_scope"
        )

    return None


def _blocked_payload(
    *,
    reason: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": (
            "post_ocr_hypothesis_registry_cli_v1"
        ),
        "status": "blocked",
        "decision": (
            "WQ7_REGISTRY_BLOCKED"
        ),
        "reason": reason,
        "details": (
            {}
            if details is None
            else details
        ),
        "registry": None,
        "challenger_consumption": {
            "consumed_registry": False,
            "hypothesis_count": 0,
            "hypothesis_ids": [],
            "states": {},
            "training_performed": False,
            "candidate_evaluation_performed": False,
            "operational_authority": False,
        },
        "write_requested": False,
        "write_performed": False,
        "output_path": None,
        "paper_only": True,
        "shadow_only": True,
        "research_only": True,
        "operational_authority": False,
        "sends_orders": False,
        "changes_risk": False,
        "changes_model": False,
        "exchange_private_access": False,
    }


def main(
    argv: list[str] | None = None,
) -> int:
    _ensure_project_root()

    from smartcrypto.research.trades_master_official.hypothesis_registry import (
        OfficialHypothesisRegistryError,
        build_post_ocr_hypothesis_registry,
        list_registered_hypotheses,
        load_candidate_queue_v2,
        load_method_freeze,
        persist_post_ocr_hypothesis_registry,
    )

    args = build_parser().parse_args(
        argv
    )

    try:
        method = load_method_freeze(
            args.method_freeze
        )

        queue = load_candidate_queue_v2(
            args.queue_v2
        )

        registry = (
            build_post_ocr_hypothesis_registry(
                method,
                queue,
                method_freeze_path=(
                    args.method_freeze
                ),
                queue_v2_path=(
                    args.queue_v2
                ),
            )
        )

        challengers = (
            list_registered_hypotheses(
                registry
            )
        )

        state_counts: dict[
            str,
            int,
        ] = {}

        for challenger in challengers:
            state = str(
                challenger[
                    "state"
                ]
            )

            state_counts[
                state
            ] = (
                state_counts.get(
                    state,
                    0,
                )
                + 1
            )

        envelope: dict[
            str,
            Any,
        ] = {
            "schema_version": (
                "post_ocr_hypothesis_registry_cli_v1"
            ),
            "status": "ok",
            "decision": (
                registry[
                    "decision"
                ]
            ),
            "registry": registry,
            "challenger_consumption": {
                "consumed_registry": True,
                "hypothesis_count": len(
                    challengers
                ),
                "hypothesis_ids": [
                    challenger[
                        "hypothesis_id"
                    ]
                    for challenger in challengers
                ],
                "states": dict(
                    sorted(
                        state_counts.items()
                    )
                ),
                "training_performed": False,
                "candidate_evaluation_performed": False,
                "operational_authority": False,
            },
            "write_requested": bool(
                args.write
            ),
            "write_performed": False,
            "output_path": None,
            "paper_only": True,
            "shadow_only": True,
            "research_only": True,
            "operational_authority": False,
            "sends_orders": False,
            "changes_risk": False,
            "changes_model": False,
            "exchange_private_access": False,
        }

        if args.write:
            if not args.output:
                envelope = _blocked_payload(
                    reason=(
                        "write_requires_explicit_output"
                    )
                )

            else:
                output = (
                    _resolve_output_path(
                        args.output
                    )
                )

                output_error = (
                    _validate_output_path(
                        output
                    )
                )

                if (
                    output_error
                    is not None
                ):
                    envelope = (
                        _blocked_payload(
                            reason=(
                                output_error
                            ),
                            details={
                                "output_path": (
                                    str(
                                        output
                                    )
                                )
                            },
                        )
                    )

                else:
                    (
                        persist_post_ocr_hypothesis_registry(
                            registry,
                            output_path=output,
                        )
                    )

                    envelope[
                        "write_performed"
                    ] = True

                    envelope[
                        "output_path"
                    ] = str(
                        output
                    )

    except (
        OfficialHypothesisRegistryError
    ) as exc:
        envelope = _blocked_payload(
            reason=exc.code,
            details=dict(
                exc.details
            ),
        )

    if args.json_output:
        print(
            json.dumps(
                envelope,
                ensure_ascii=False,
                sort_keys=True,
                allow_nan=False,
            )
        )

    else:
        print(
            "WQ7_HYPOTHESIS_REGISTRY_STATUS="
            f"{envelope['status']}"
        )

        print(
            "WQ7_HYPOTHESIS_REGISTRY_DECISION="
            f"{envelope['decision']}"
        )

        registry = envelope.get(
            "registry"
        )

        if isinstance(
            registry,
            dict,
        ):
            print(
                "WQ7_REGISTRY_HASH="
                f"{registry.get('registry_hash')}"
            )

            print(
                "WQ7_HYPOTHESIS_COUNT="
                f"{registry.get('hypothesis_count')}"
            )

            print(
                "WQ7_STATE_COUNTS="
                f"{registry.get('state_counts')}"
            )

        print(
            "WQ7_WRITE_PERFORMED="
            f"{envelope['write_performed']}"
        )

    return (
        0
        if envelope[
            "status"
        ]
        == "ok"
        else 2
    )


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
