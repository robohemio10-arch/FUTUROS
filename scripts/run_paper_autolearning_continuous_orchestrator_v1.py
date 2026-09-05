from __future__ import annotations

import argparse
import json
from pathlib import Path

from smartcrypto.learning.paper_autolearning.continuous_orchestrator import (
    run_paper_autolearning_continuous_orchestrator_v1,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one idempotent Paper auto-learning feedback-to-quarantine cycle."
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--paper-db", default=None)
    parser.add_argument("--write-feedback", action="store_true")
    parser.add_argument("--train-challenger", action="store_true")
    parser.add_argument("--write-quarantine-artifacts", action="store_true")
    parser.add_argument("--write-reports", action="store_true")
    parser.add_argument("--no-evaluate-candidates", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = run_paper_autolearning_continuous_orchestrator_v1(
        project_root=Path(args.project_root),
        explicit_paper_db_path=args.paper_db,
        write_feedback=args.write_feedback,
        train_challenger=args.train_challenger,
        write_quarantine_artifacts=args.write_quarantine_artifacts,
        write_reports=args.write_reports,
        evaluate_candidates=not args.no_evaluate_candidates,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, default=str))
    else:
        print(
            f"status={report['status']} reason={report['reason']} "
            f"new_outcomes={report['new_outcome_event_count']} "
            f"microbatch_rows={report['microbatch_rows']} "
            f"quarantine_candidates={report['quarantine_candidate_count']}"
        )
    return 1 if report["status"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
