"""Tests for the read-only paper autotrain feedback gap diagnostics module."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from smartcrypto.learning.paper_autotrain_feedback_gap_diagnostics import (
    build_paper_autotrain_feedback_gap_diagnostics_v1,
)
from smartcrypto.learning.paper_autotrain_feedback_gap_diagnostics.diagnostics import (
    search_writers,
)

ROOT = Path(__file__).resolve().parents[1]


def _write_paper_db(path: Path, rows: list[tuple]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY, pair TEXT, is_open INTEGER, is_short INTEGER,
            open_date TEXT, close_date TEXT, open_rate REAL, close_rate REAL,
            stake_amount REAL, close_profit_abs REAL, exit_reason TEXT
        )
        """
    )
    conn.executemany("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()


def _write_closed_trades_csv(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = "order_id,moeda,fechar_side,horario_abertura,horario_fechamento,preco_abertura,preco_fechamento,pnl_fechado,is_open\n"
    path.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")


def _write_feedback_jsonl(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(event) for event in events) + ("\n" if events else ""), encoding="utf-8")


def _seed_project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    _write_paper_db(
        root / "paper-db" / "tradesv3.paper.sqlite",
        [
            (1, "BTC/USDT:USDT", 0, 0, "2026-07-01T10:00:00+00:00", "2026-07-01T12:00:00+00:00", 60000.0, 60500.0, 100.0, 5.0, "roi"),
            (2, "ETH/USDT:USDT", 0, 0, "2026-07-02T10:00:00+00:00", "2026-07-02T12:00:00+00:00", 3000.0, 2950.0, 100.0, -1.5, "stop_loss"),
            (3, "BTC/USDT:USDT", 0, 0, "2026-07-05T10:00:00+00:00", "2026-07-05T12:00:00+00:00", 61000.0, 61200.0, 100.0, 2.0, "roi"),
            (4, "BTC/USDT:USDT", 0, 0, "2026-07-06T10:00:00+00:00", "2026-07-06T12:00:00+00:00", 61000.0, 61200.0, 100.0, None, "roi"),
        ],
    )
    _write_closed_trades_csv(
        root / "data" / "trades" / "inbox" / "freqtrade_paper_closed_trades.csv",
        [
            "1,BTC/USDT:USDT,long,2026-07-01T10:00:00+00:00,2026-07-01T12:00:00+00:00,60000.0,60500.0,5.0,0",
            "2,ETH/USDT:USDT,long,2026-07-02T10:00:00+00:00,2026-07-02T12:00:00+00:00,3000.0,2950.0,-1.5,0",
            "3,BTC/USDT:USDT,long,2026-07-05T10:00:00+00:00,2026-07-05T12:00:00+00:00,61000.0,61200.0,2.0,0",
            "4,BTC/USDT:USDT,long,2026-07-06T10:00:00+00:00,2026-07-06T12:00:00+00:00,61000.0,61200.0,,0",
        ],
    )
    _write_feedback_jsonl(
        root / "data" / "feedback" / "paper_autotrain_daily_quarantine_feedback_events_v1.jsonl",
        [
            {
                "order_id": "1",
                "symbol": "BTC/USDT:USDT",
                "side": "long",
                "close_time_utc": "2026-07-01T12:00:00+00:00",
                "net_pnl": 5.0,
            }
        ],
    )
    return root


def test_reconciliation_counts_and_full_missing_list(tmp_path: Path) -> None:
    root = _seed_project(tmp_path)
    db_path = root / "paper-db" / "tradesv3.paper.sqlite"

    report = build_paper_autotrain_feedback_gap_diagnostics_v1(
        project_root=root,
        paper_db_path=db_path,
        allow_paper_db_read=True,
        write_report=False,
    )

    assert report["paper_db_normalized_record_count"] == 4
    assert report["closed_trades_csv_normalized_record_count"] == 4
    assert report["feedback_events_normalized_record_count"] == 1
    assert report["missing_in_feedback_count"] == 3
    assert report["conflicting_group_count"] == 0
    # Full listing, never truncated/sampled.
    assert len(report["missing_in_feedback_records"]) == report["missing_in_feedback_count"]


def test_missing_record_fields_are_complete(tmp_path: Path) -> None:
    root = _seed_project(tmp_path)
    db_path = root / "paper-db" / "tradesv3.paper.sqlite"

    report = build_paper_autotrain_feedback_gap_diagnostics_v1(
        project_root=root, paper_db_path=db_path, allow_paper_db_read=True, write_report=False,
    )

    required_fields = {
        "classification",
        "dedup_key",
        "native_key",
        "source_keys",
        "paper_db_trade_id",
        "closed_trades_csv_order_id",
        "symbol",
        "side",
        "open_time_utc",
        "close_time_utc",
        "net_pnl",
        "profit_ratio",
        "source_presence",
        "missing_sources",
        "db_csv_match_status",
        "normalization_status",
        "validation_status",
        "causal_bucket",
    }
    for row in report["missing_in_feedback_records"]:
        assert required_fields.issubset(row.keys())
        assert row["classification"] == "missing_in_feedback"
        assert row["missing_sources"] == ["feedback_events"]
        assert row["db_csv_match_status"] == "match"


def test_causal_bucket_separates_cadence_from_validation_rejection(tmp_path: Path) -> None:
    root = _seed_project(tmp_path)
    db_path = root / "paper-db" / "tradesv3.paper.sqlite"

    report = build_paper_autotrain_feedback_gap_diagnostics_v1(
        project_root=root, paper_db_path=db_path, allow_paper_db_read=True, write_report=False,
    )

    by_key = {row["dedup_key"]: row for row in report["missing_in_feedback_records"]}
    clean_rows = [row for row in by_key.values() if row["net_pnl"] is not None]
    broken_rows = [row for row in by_key.values() if row["net_pnl"] is None]

    assert clean_rows, "expected at least one well-formed missing record in the fixture"
    for row in clean_rows:
        assert row["validation_status"]["would_pass_both_stages"] is True
        assert row["causal_bucket"] == "cadence_gap_unexplained_by_validation"

    assert broken_rows, "expected the deliberately incomplete fixture record (missing net_pnl)"
    for row in broken_rows:
        assert row["validation_status"]["would_pass_both_stages"] is False
        assert row["causal_bucket"].startswith("validation_rejection:")

    assert report["validation_rejection_status"]["status"] == "some_rejected"
    assert report["validation_rejection_status"]["rejected_count"] == len(broken_rows)
    assert report["cadence_gap_mechanism_status"]["status"] in {
        "confirmed",
        "not_confirmed_by_this_run",
        "indeterminate_missing_evidence",
    }


def test_no_missing_records_when_feedback_matches_sources(tmp_path: Path) -> None:
    root = tmp_path / "project"
    _write_paper_db(
        root / "paper-db" / "tradesv3.paper.sqlite",
        [(1, "BTC/USDT:USDT", 0, 0, "2026-07-01T10:00:00+00:00", "2026-07-01T12:00:00+00:00", 60000.0, 60500.0, 100.0, 5.0, "roi")],
    )
    _write_closed_trades_csv(
        root / "data" / "trades" / "inbox" / "freqtrade_paper_closed_trades.csv",
        ["1,BTC/USDT:USDT,long,2026-07-01T10:00:00+00:00,2026-07-01T12:00:00+00:00,60000.0,60500.0,5.0,0"],
    )
    _write_feedback_jsonl(
        root / "data" / "feedback" / "paper_autotrain_daily_quarantine_feedback_events_v1.jsonl",
        [{"order_id": "1", "symbol": "BTC/USDT:USDT", "side": "long", "close_time_utc": "2026-07-01T12:00:00+00:00", "net_pnl": 5.0}],
    )

    report = build_paper_autotrain_feedback_gap_diagnostics_v1(
        project_root=root,
        paper_db_path=root / "paper-db" / "tradesv3.paper.sqlite",
        allow_paper_db_read=True,
        write_report=False,
    )

    assert report["missing_in_feedback_count"] == 0
    assert report["missing_in_feedback_records"] == []
    assert report["validation_rejection_status"]["status"] == "none_rejected"


def test_safety_flags_default_to_read_only_no_write(tmp_path: Path) -> None:
    root = _seed_project(tmp_path)
    report = build_paper_autotrain_feedback_gap_diagnostics_v1(project_root=root, write_report=False)

    assert report["paper_only"] is True
    assert report["shadow_only"] is True
    assert report["research_only"] is True
    assert report["read_only"] is True
    assert report["write_performed"] is False
    assert report["writes_runtime"] is False
    assert report["writes_sqlite"] is False
    assert report["writes_parquet"] is False
    assert report["writes_feedback"] is False
    assert report["writes_microbatch"] is False
    assert report["would_create_microbatch"] is False
    assert report["would_run_training"] is False
    assert report["would_promote_model"] is False
    assert report["sends_orders"] is False
    assert report["changes_risk"] is False
    assert report["exchange_private_access"] is False
    assert report["scheduler_registered"] is False
    assert report["creates_cron"] is False
    assert report["creates_systemd_timer"] is False
    assert report["creates_windows_task"] is False


def test_write_report_gate_writes_only_under_data_reports(tmp_path: Path) -> None:
    root = _seed_project(tmp_path)

    not_written = build_paper_autotrain_feedback_gap_diagnostics_v1(project_root=root, write_report=False)
    assert not_written["write_performed"] is False
    assert not (root / "data" / "reports" / "paper_autotrain_feedback_gap_diagnostics_v1.json").exists()

    written = build_paper_autotrain_feedback_gap_diagnostics_v1(project_root=root, write_report=True)
    assert written["write_performed"] is True
    output_json = root / "data" / "reports" / "paper_autotrain_feedback_gap_diagnostics_v1.json"
    output_markdown = root / "data" / "reports" / "paper_autotrain_feedback_gap_diagnostics_v1.md"
    assert output_json.exists()
    assert output_markdown.exists()
    payload = json.loads(output_json.read_text(encoding="utf-8"))
    assert payload["write_performed"] is True


def test_write_report_blocked_outside_data_reports(tmp_path: Path) -> None:
    root = _seed_project(tmp_path)
    outside_path = tmp_path / "outside" / "report.json"

    report = build_paper_autotrain_feedback_gap_diagnostics_v1(
        project_root=root,
        write_report=True,
        output_json_path=outside_path,
    )

    assert report["status"] == "blocked"
    assert "report_path_outside_data_reports" in report["blockers"]
    assert report["write_performed"] is False
    assert not outside_path.exists()


def test_writer_search_isolated_synthetic_repo(tmp_path: Path) -> None:
    root = tmp_path / "mini_repo"
    (root / "smartcrypto").mkdir(parents=True)
    (root / "scripts").mkdir(parents=True)
    (root / "tests").mkdir(parents=True)

    (root / "smartcrypto" / "real_writer.py").write_text(
        "from pathlib import Path\n"
        "DEFAULT_TARGET = Path('data/feedback/paper_closed_trades_incremental.parquet')\n"
        "def write_feedback_outputs(frame, feedback_store_path=DEFAULT_TARGET):\n"
        "    frame.to_parquet(feedback_store_path, index=False)\n",
        encoding="utf-8",
    )
    (root / "scripts" / "unrelated_reader_and_writer.py").write_text(
        "from pathlib import Path\n"
        "DEFAULT_FEEDBACK_PATH = Path('data/feedback/paper_closed_trades_incremental.parquet')\n"
        "def build_microbatch(feedback_path=DEFAULT_FEEDBACK_PATH, output_path=Path('data/feedback/other.parquet')):\n"
        "    joined = read_frame(feedback_path)\n"
        "    joined.to_parquet(output_path, index=False)\n",
        encoding="utf-8",
    )
    (root / "tests" / "test_ignored.py").write_text(
        "def test_x():\n"
        "    path = 'paper_closed_trades_incremental.parquet'\n",
        encoding="utf-8",
    )

    result = search_writers(root)
    assert result["writer_search_status"] == "completed"
    assert result["candidate_parquet_writer_files"] == ["smartcrypto/real_writer.py"]
    assert result["paper_closed_trades_incremental_writer_count"] == 1
    assert result["unexpected_writer_count"] == 0
    assert all(not match["in_tests"] or match["file"].startswith("tests/") for match in result["writer_search_matches"])


def test_writer_search_against_real_repo_matches_known_finding() -> None:
    """Regression test for the exact finding from the 2026-07-09 audit round:
    two independent writers of paper_closed_trades_incremental.parquet exist
    (feedback_store.py and the standalone update_paper_feedback_incremental_store.py
    script), and exactly one writer of the feedback jsonl (activation.py). If
    this ever changes, it is a real, reviewable finding, not test noise.
    """
    result = search_writers(ROOT)
    assert result["paper_closed_trades_incremental_writer_count"] == 2
    assert result["feedback_events_jsonl_writer_count"] == 1
    assert result["unexpected_writer_count"] == 1
    assert "smartcrypto/learning/paper_autolearning/feedback_store.py" in result["candidate_parquet_writer_files"]
    assert "scripts/update_paper_feedback_incremental_store.py" in result["candidate_parquet_writer_files"]
    assert (
        "smartcrypto/learning/paper_autotrain_daily_quarantine_activation/activation.py"
        in result["candidate_jsonl_writer_files"]
    )


def test_writer_search_paths_are_posix_normalized(tmp_path: Path) -> None:
    """Report paths must be deterministic POSIX (forward-slash) strings, not
    the OS-native separator. Before the fix, `search_writers()` built
    relative paths with plain `str(path.relative_to(root))`, which emits
    backslashes on Windows -- exactly the 2 pytest failures Rodrigo hit
    running this branch for real on Windows. This test cannot reproduce a
    backslash on this Linux sandbox (str() and .as_posix() coincide on
    POSIX), so it locks in the contract two ways: functionally (no path in
    the report may contain a backslash) and at the source level (the fix
    must use `.as_posix()`, not a bare `str()` of a `relative_to()` result).
    """
    root = tmp_path / "mini_repo"
    nested = root / "smartcrypto" / "learning" / "nested" / "package"
    nested.mkdir(parents=True)
    (nested / "real_writer.py").write_text(
        "from pathlib import Path\n"
        "DEFAULT_TARGET = Path('data/feedback/paper_closed_trades_incremental.parquet')\n"
        "def write_feedback_outputs(frame, feedback_store_path=DEFAULT_TARGET):\n"
        "    frame.to_parquet(feedback_store_path, index=False)\n",
        encoding="utf-8",
    )
    (root / "tests").mkdir(parents=True)

    result = search_writers(root)
    all_paths = [
        *result["candidate_parquet_writer_files"],
        *result["candidate_jsonl_writer_files"],
        *[match["file"] for match in result["writer_search_matches"]],
    ]
    assert all_paths, "expected the synthetic nested writer to be found"
    for path_str in all_paths:
        assert "\\" not in path_str, f"path must be POSIX-normalized, got {path_str!r}"
    assert "smartcrypto/learning/nested/package/real_writer.py" in result["candidate_parquet_writer_files"]

    source_path = (
        Path(__file__).resolve().parents[1]
        / "smartcrypto"
        / "learning"
        / "paper_autotrain_feedback_gap_diagnostics"
        / "diagnostics.py"
    )
    source_text = source_path.read_text(encoding="utf-8")
    assert "str(path.relative_to(root))" not in source_text
    assert "path.relative_to(root).as_posix()" in source_text


def test_missing_in_feedback_independent_of_paper_db(tmp_path: Path) -> None:
    """Regression test for the exact production scenario Rodrigo reported:
    closed_trades_csv has 539 records, feedback_events has 498 (the first
    498 by order_id), and paper_db is never read (allow_paper_db_read
    defaults to False, exactly like the reported run). Before the fix,
    `classify_group()` checked SOURCE_PAPER_DB presence before feedback
    presence, so with paper_db absent every group was misclassified
    `missing_in_db` and `missing_in_feedback_count` was always 0 instead of
    the correct, paper_db-independent answer of 41 (539 - 498).
    """
    root = tmp_path / "project"
    base = datetime(2026, 6, 1, tzinfo=timezone.utc)
    total_csv = 539
    total_feedback = 498

    csv_lines: list[str] = []
    feedback_events: list[dict] = []
    for i in range(1, total_csv + 1):
        close_dt = base + timedelta(minutes=i)
        close_iso = close_dt.isoformat()
        open_iso = (close_dt - timedelta(hours=1)).isoformat()
        symbol = "BTC/USDT:USDT" if i % 2 == 0 else "ETH/USDT:USDT"
        csv_lines.append(f"{i},{symbol},long,{open_iso},{close_iso},100.0,101.0,1.0,0")
        if i <= total_feedback:
            feedback_events.append(
                {
                    "order_id": str(i),
                    "symbol": symbol,
                    "side": "long",
                    "close_time_utc": close_iso,
                    "net_pnl": 1.0,
                }
            )

    _write_closed_trades_csv(root / "data" / "trades" / "inbox" / "freqtrade_paper_closed_trades.csv", csv_lines)
    _write_feedback_jsonl(
        root / "data" / "feedback" / "paper_autotrain_daily_quarantine_feedback_events_v1.jsonl",
        feedback_events,
    )

    report = build_paper_autotrain_feedback_gap_diagnostics_v1(
        project_root=root,
        write_report=False,
        # allow_paper_db_read defaults to False -- exactly like the reported run.
    )

    assert report["closed_trades_csv_normalized_record_count"] == total_csv
    assert report["feedback_events_normalized_record_count"] == total_feedback
    assert report["missing_in_feedback_count"] == total_csv - total_feedback
    assert len(report["missing_in_feedback_records"]) == total_csv - total_feedback

    missing_order_ids = {row["closed_trades_csv_order_id"] for row in report["missing_in_feedback_records"]}
    assert missing_order_ids == {str(i) for i in range(total_feedback + 1, total_csv + 1)}
    for row in report["missing_in_feedback_records"]:
        assert row["db_csv_match_status"] == "db_not_available"
        assert row["missing_sources"] == ["feedback_events"]


def test_paper_db_stale_runtime_not_reported_as_fresh(tmp_path: Path) -> None:
    """Regression test for the exact production scenario Rodrigo reported:
    a runtime paper_db whose latest close (2026-05-29) predates
    closed_trades_csv's latest close (2026-07-08) was reported as
    `runtime_db_fresh`, because `resolve_paper_db()`'s own staleness check
    is disabled when `watermark_close_time=None` (which this diagnostic
    must pass, since it needs to see every candidate record, not just ones
    after a watermark). The diagnostic's own freshness check must compare
    directly against closed_trades_csv and must never call a stale DB
    fresh -- and that staleness must never hide the CSV -> feedback gap.
    """
    root = tmp_path / "project"
    _write_paper_db(
        root / "paper-db" / "tradesv3.paper.sqlite",
        [
            (1, "BTC/USDT:USDT", 0, 0, "2026-05-28T10:00:00+00:00", "2026-05-29T10:00:00+00:00", 60000.0, 60500.0, 100.0, 5.0, "roi"),
        ],
    )
    _write_closed_trades_csv(
        root / "data" / "trades" / "inbox" / "freqtrade_paper_closed_trades.csv",
        [
            "1,BTC/USDT:USDT,long,2026-05-28T10:00:00+00:00,2026-05-29T10:00:00+00:00,60000.0,60500.0,5.0,0",
            "2,ETH/USDT:USDT,long,2026-07-08T10:00:00+00:00,2026-07-08T12:00:00+00:00,3000.0,2950.0,-1.5,0",
        ],
    )
    _write_feedback_jsonl(
        root / "data" / "feedback" / "paper_autotrain_daily_quarantine_feedback_events_v1.jsonl", []
    )

    report = build_paper_autotrain_feedback_gap_diagnostics_v1(
        project_root=root,
        paper_db_path=root / "paper-db" / "tradesv3.paper.sqlite",
        allow_paper_db_read=True,
        write_report=False,
    )

    assert report["paper_db_authority_status"] != "runtime_db_fresh"
    assert report["paper_db_authority_status"] == "runtime_db_stale_against_csv"
    assert report["paper_db_selected_reason"] == "selected_candidate_max_close_time_utc_before_closed_trades_csv_max_close_time_utc"
    freshness = report["paper_db_freshness_check"]
    assert freshness["paper_db_max_close_time_utc"] == "2026-05-29T10:00:00+00:00"
    assert freshness["closed_trades_csv_max_close_time_utc"] == "2026-07-08T12:00:00+00:00"
    # The staleness of paper_db must never hide the CSV -> feedback gap.
    assert report["missing_in_feedback_count"] == 2
