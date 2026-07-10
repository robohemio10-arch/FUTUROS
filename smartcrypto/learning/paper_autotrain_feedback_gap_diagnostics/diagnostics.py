"""Read-only diagnostics for the paper autotrain feedback gap.

This module explains, with evidence, why closed paper trades present in the
paper DB and in the closed-trades CSV are absent from the autotrain feedback
JSONL (`missing_in_feedback`, per
`paper_autotrain_source_key_reconciliation`). It reuses that module's source
loading and reconciliation-key logic instead of re-implementing it, adds:

1. a static, read-only, full-repo writer search for the two artifact paths
   and the two known writer functions (`write_feedback_outputs`,
   `write_quarantine_outputs`), so "is there another writer nobody knows
   about" is answered by evidence, not by memory of a handful of files;
2. a full (never truncated/sampled) listing of every `missing_in_feedback`
   group, with dedup/source keys and a per-record verdict on whether the
   record would actually be rejected by Stage 1 (`feedback_store.py`) or
   Stage 2 (`paper_autotrain_daily_quarantine_activation`) validation, by
   running the real validation functions against the record instead of
   guessing;
3. an explicit separation between "the gap is explained by pipeline cadence
   (stages did not run recently enough)" and "the gap is explained by
   validation rejection" so the two are never collapsed into one claim.

This module never writes to data/feedback, data/runtime, data/sqlite, or any
parquet file. Its only optional write target is a JSON/Markdown report pair
under data/reports, gated behind an explicit --write-report flag. It never
creates a microbatch, never trains, never promotes, never registers a
scheduler, and has no authority over Freqtrade, RiskManager, Qlib runtime,
IA Shadow runtime, active signals, or orders.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd

from smartcrypto.learning.paper_autolearning.feedback_store import (
    normalize_closed_trade_row,
    validate_event_inputs,
)
from smartcrypto.learning.paper_autolearning.outcome_schema import DEFAULT_FEEDBACK_STORE
from smartcrypto.learning.paper_autotrain_daily_quarantine_activation.activation import (
    DEFAULT_FEEDBACK_EVENTS_PATH,
)
from smartcrypto.learning.paper_autotrain_daily_quarantine_activation.activation import (
    normalize_closed_trades as stage2_normalize_closed_trades,
)
from smartcrypto.learning.paper_autotrain_paper_runtime_source_diagnostics.diagnostics import (
    CLOSED_TRADES_CSV,
    FEEDBACK_EVENTS,
    resolve_paper_db,
)
from smartcrypto.learning.paper_autotrain_source_key_reconciliation.reconciliation import (
    SOURCE_CSV,
    SOURCE_FEEDBACK,
    SOURCE_NAMES,
    SOURCE_PAPER_DB,
    build_reconciliation_groups,
    classify_group,
    first_datetime_from_value,
    has_field_conflict,
    load_csv_source,
    load_feedback_source,
    load_paper_db_source,
    resolve_path,
    source_to_status,
    summarize_reconciliation,
    summarize_sources,
)

SCHEMA_VERSION = "paper_autotrain_feedback_gap_diagnostics_v1"

DEFAULT_OUTPUT_JSON = Path("data/reports/paper_autotrain_feedback_gap_diagnostics_v1.json")
DEFAULT_OUTPUT_MARKDOWN = Path("data/reports/paper_autotrain_feedback_gap_diagnostics_v1.md")
ALLOWED_REPORT_ROOT = Path("data/reports")

# --------------------------------------------------------------------------
# Scope item 1/2: static, read-only, full-repo writer search
# --------------------------------------------------------------------------

PARQUET_TERMS: tuple[str, ...] = (
    "paper_closed_trades_incremental",
    "paper_closed_trades_incremental.parquet",
)
JSONL_TERMS: tuple[str, ...] = (
    "paper_autotrain_daily_quarantine_feedback_events_v1",
    "paper_autotrain_daily_quarantine_feedback_events_v1.jsonl",
)
FUNCTION_TERMS: tuple[str, ...] = (
    "write_feedback_outputs",
    "write_quarantine_outputs",
)
SEARCH_TERMS: tuple[str, ...] = PARQUET_TERMS + JSONL_TERMS + FUNCTION_TERMS

WRITE_CALL_PATTERN = re.compile(r"\.to_parquet\(|\.write_text\(|\.to_csv\(|\.write\(")
EXCLUDED_DIR_NAMES: tuple[str, ...] = (".git", "__pycache__", ".mypy_cache", ".pytest_cache", ".venv", "node_modules")


@dataclass(frozen=True)
class WriterSearchMatch:
    search_term: str
    file: str
    line_number: int
    line_text: str
    is_definition: bool
    in_tests: bool
    looks_like_write_context: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "search_term": self.search_term,
            "file": self.file,
            "line_number": self.line_number,
            "line_text": self.line_text,
            "is_definition": self.is_definition,
            "in_tests": self.in_tests,
            "looks_like_write_context": self.looks_like_write_context,
        }


def _definition_pattern(name: str) -> re.Pattern[str]:
    return re.compile(rf"^\s*def\s+{re.escape(name)}\s*\(", re.MULTILINE)


def _module_constant_literal_map(tree: "ast.Module") -> dict[str, str]:
    """Best-effort, same-file-only resolution of `NAME = Path("literal")` /
    `NAME = "literal"` module-level assignments. Deliberately does not follow
    imports across files: it is a one-hop heuristic, not a data-flow engine.
    """
    literals: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        value = node.value
        if isinstance(value, ast.Call) and value.args and isinstance(value.args[0], ast.Constant):
            if isinstance(value.args[0].value, str):
                literals[target.id] = value.args[0].value
        elif isinstance(value, ast.Constant) and isinstance(value.value, str):
            literals[target.id] = value.value
    return literals


WRITE_METHOD_NAMES: tuple[str, ...] = ("to_parquet", "to_csv", "write_text", "write")
# Methods called *on* the path object itself (path.write_text(...), path.write(...))
# vs. methods called on a dataframe/buffer *with* the path as an argument
# (frame.to_parquet(path, ...), frame.to_csv(path, ...)).
PATH_IS_RECEIVER_METHODS: frozenset[str] = frozenset({"write_text", "write"})


def _terminal_name(node: "ast.AST") -> str | None:
    """Return the last identifier in a Name/Attribute chain, e.g. `paths.feedback_events_path` -> "feedback_events_path"."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _function_param_aliases(node: "ast.FunctionDef | ast.AsyncFunctionDef", alias_names: set[str]) -> set[str]:
    """Parameter names whose default value is one of the given alias names
    (one-hop: `def f(feedback_path: Path = DEFAULT_FEEDBACK_PATH)` -> {"feedback_path"}
    when "DEFAULT_FEEDBACK_PATH" is already a known alias for the target)."""
    params = node.args
    defaults = list(params.defaults)
    positional = params.posonlyargs + params.args
    offset = len(positional) - len(defaults)
    found: set[str] = set()
    for arg, default in zip(positional[offset:], defaults):
        if isinstance(default, ast.Name) and default.id in alias_names:
            found.add(arg.arg)
    for kwarg, kwdefault in zip(params.kwonlyargs, params.kw_defaults):
        if kwdefault is not None and isinstance(kwdefault, ast.Name) and kwdefault.id in alias_names:
            found.add(kwarg.arg)
    return found


def _call_writes_target(call: "ast.Call", target_names: set[str]) -> bool:
    func = call.func
    if not isinstance(func, ast.Attribute) or func.attr not in WRITE_METHOD_NAMES:
        return False
    if func.attr in PATH_IS_RECEIVER_METHODS:
        candidate = _terminal_name(func.value)
    else:
        if not call.args:
            return False
        candidate = _terminal_name(call.args[0])
    return candidate is not None and candidate in target_names


def _file_candidate_writer(*, text: str, terms: Sequence[str]) -> bool:
    """Function-scoped, argument-aware heuristic: a file is a candidate writer
    for one of `terms` if some function contains a write-call
    (`.to_parquet(X)` / `.to_csv(X)` / `X.write_text(...)` / `X.write(...)`)
    whose path-like argument/receiver `X` resolves, by name, to either a
    module-level constant (defined in the same file) whose literal value
    contains one of `terms`, or a same-function parameter whose default is
    such a constant. This intentionally does NOT follow imports across files
    or track re-assignment beyond one hop (see `search_writers` docstring),
    so it misses writers that only receive the path as a parameter default
    imported from another module (those are instead caught by the exact
    `def <name>(` search for `write_feedback_outputs` / `write_quarantine_outputs`)
    and it deliberately requires the *specific write call's argument* to
    resolve to the target, not just any write call anywhere in the function,
    to avoid flagging functions that read the target path and separately
    write an unrelated output.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return False

    constant_map = _module_constant_literal_map(tree)
    module_aliases = {name for name, literal in constant_map.items() if any(term in literal for term in terms)}

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        local_targets = set(module_aliases) | _function_param_aliases(node, module_aliases)
        if not local_targets:
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Call) and _call_writes_target(inner, local_targets):
                return True
    return False


def search_writers(root: Path) -> dict[str, Any]:
    """Static, read-only search for writers of the two feedback-gap artifacts.

    This combines two independent, individually-disclosed techniques, never
    imports or executes repository code, and never writes anything:

    1. An exact, zero-false-positive search for the two writer function
       *definitions* already confirmed by manual source review in prior
       audit rounds (`def write_feedback_outputs(`, `def write_quarantine_outputs(`).
    2. A same-function-scope heuristic (`_file_candidate_writer`) that flags
       a file when some function's body contains both a write-call pattern
       (`.to_parquet(`, `.write_text(`, `.to_csv(`, `.write(`) and either the
       target filename literally or a same-file constant whose value is that
       filename. This is a one-hop, same-file heuristic: it will miss a
       writer that only receives the path as a parameter/default imported
       from a different module (those rely on technique 1 instead), and it
       may occasionally flag a file that reads the path and separately
       writes something unrelated in the same function. Every individual
       text match is reported in `writer_search_matches` so a human can
       confirm any candidate in seconds; nothing here is asserted as an
       exhaustive, zero-false-positive result.
    """
    matches: list[WriterSearchMatch] = []
    file_texts: dict[str, str] = {}
    self_path = Path(__file__).resolve().relative_to(root).as_posix() if _is_under(Path(__file__).resolve(), root) else None

    for path in sorted(root.rglob("*.py")):
        relative_parts = path.relative_to(root).parts
        if any(part in EXCLUDED_DIR_NAMES for part in relative_parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if not any(term in text for term in SEARCH_TERMS):
            continue

        relative = path.relative_to(root).as_posix()
        file_texts[relative] = text
        in_tests = relative_parts[0] == "tests"
        lines = text.splitlines()

        for term in SEARCH_TERMS:
            if term not in text:
                continue
            is_def = _definition_pattern(term) if term in FUNCTION_TERMS else None
            for line_number, line in enumerate(lines, start=1):
                if term not in line:
                    continue
                matches.append(
                    WriterSearchMatch(
                        search_term=term,
                        file=relative,
                        line_number=line_number,
                        line_text=line.strip(),
                        is_definition=bool(is_def.match(line)) if is_def else False,
                        in_tests=in_tests,
                        looks_like_write_context=bool(WRITE_CALL_PATTERN.search(line)),
                    )
                )

    parquet_def_pattern = _definition_pattern("write_feedback_outputs")
    jsonl_def_pattern = _definition_pattern("write_quarantine_outputs")
    candidate_parquet_writers: set[str] = set()
    candidate_jsonl_writers: set[str] = set()
    files_referencing_parquet_filename: set[str] = set()
    files_referencing_jsonl_filename: set[str] = set()

    for relative, text in file_texts.items():
        if relative.split("/")[0] == "tests" or relative == self_path:
            continue
        lines = text.splitlines()

        if any(term in text for term in PARQUET_TERMS):
            files_referencing_parquet_filename.add(relative)
        if any(term in text for term in JSONL_TERMS):
            files_referencing_jsonl_filename.add(relative)

        if parquet_def_pattern.search(text) or _file_candidate_writer(text=text, terms=PARQUET_TERMS):
            candidate_parquet_writers.add(relative)
        if jsonl_def_pattern.search(text) or _file_candidate_writer(text=text, terms=JSONL_TERMS):
            candidate_jsonl_writers.add(relative)

    parquet_writer_count = len(candidate_parquet_writers)
    jsonl_writer_count = len(candidate_jsonl_writers)
    # Baseline expectation going into this diagnostic was exactly one writer
    # per artifact (the two Stage 1 / Stage 2 functions already known from
    # prior audit rounds). Anything beyond that is "unexpected" and must be
    # reviewed by a human (via writer_search_matches) before being treated
    # as benign.
    unexpected_writer_count = max(0, parquet_writer_count - 1) + max(0, jsonl_writer_count - 1)

    return {
        "writer_search_status": "completed",
        "writer_search_scope": {
            "root": str(root),
            "included_glob": "**/*.py",
            "excluded_dirs": sorted(EXCLUDED_DIR_NAMES),
            "method": "exact_def_search_plus_same_function_scope_heuristic_no_code_execution",
            "limitation": (
                "candidate_*_writer_files is a same-file, same-function heuristic, "
                "not a cross-file data-flow analysis: it can miss a writer that only "
                "receives the path as a parameter/default imported from another "
                "module, and can occasionally flag a file that reads the target path "
                "and separately writes something unrelated in the same function. "
                "Every individual text match is listed in writer_search_matches for "
                "manual confirmation; treat the counts as upper-bound candidates, "
                "not as an exhaustively proven result."
            ),
        },
        "writer_search_matches": [match.to_dict() for match in matches],
        "files_referencing_parquet_filename": sorted(files_referencing_parquet_filename),
        "files_referencing_jsonl_filename": sorted(files_referencing_jsonl_filename),
        "candidate_parquet_writer_files": sorted(candidate_parquet_writers),
        "candidate_jsonl_writer_files": sorted(candidate_jsonl_writers),
        "paper_closed_trades_incremental_writer_count": parquet_writer_count,
        "feedback_events_jsonl_writer_count": jsonl_writer_count,
        "unexpected_writer_count": unexpected_writer_count,
    }


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


# --------------------------------------------------------------------------
# Scope item 3/4/5: load, reconcile, and list every missing_in_feedback group
# --------------------------------------------------------------------------

OPEN_TIME_CANDIDATES: tuple[str, ...] = (
    "open_time_utc",
    "open_date",
    "open_time",
    "horario_abertura",
    "opened_at",
    "date_open",
)
PROFIT_RATIO_CANDIDATES: tuple[str, ...] = (
    "profit_ratio",
    "close_profit",
    "roi",
    "taxa_lucros_perdas_fechados_pct",
)


def _first_raw_value(raw: Mapping[str, Any], candidates: Sequence[str]) -> Any:
    for name in candidates:
        value = raw.get(name)
        if value not in (None, ""):
            return value
    return None


def _assess_stage_validation(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Run the real Stage 1 and Stage 2 validation functions against a raw row.

    This executes the same functions the production pipeline uses
    (`normalize_closed_trade_row` + `validate_event_inputs` for Stage 1,
    `normalize_closed_trades` for Stage 2) instead of guessing whether a
    missing record would be rejected. It never writes anything; it only
    calls pure functions against an in-memory copy of the row.
    """
    now = datetime.now(UTC).isoformat()
    try:
        mapped = normalize_closed_trade_row(
            raw,
            source_file="paper_autotrain_feedback_gap_diagnostics_v1",
            source_sha256=None,
            ingestion_run_id="diagnostics",
            source_row_index=0,
            created_at_utc=now,
        )
        stage1_errors = validate_event_inputs(
            symbol_norm=mapped.get("symbol"),
            side=mapped.get("side"),
            close_time_utc=mapped.get("close_time_utc"),
            net_pnl=mapped.get("net_pnl"),
        )
    except Exception as exc:  # noqa: BLE001 - diagnostics must never raise on bad input rows
        stage1_errors = [f"stage1_normalization_raised:{type(exc).__name__}"]

    try:
        one_row = pd.DataFrame([dict(raw)])
        stage2_output = stage2_normalize_closed_trades(one_row)
        stage2_valid = len(stage2_output) == 1
    except Exception as exc:  # noqa: BLE001 - diagnostics must never raise on bad input rows
        stage2_valid = False
        stage1_errors = list(stage1_errors) + [f"stage2_normalization_raised:{type(exc).__name__}"]

    would_pass_both = (not stage1_errors) and stage2_valid
    return {
        "stage1_errors": stage1_errors,
        "stage1_would_pass": not stage1_errors,
        "stage2_would_pass": stage2_valid,
        "would_pass_both_stages": would_pass_both,
    }


@dataclass(frozen=True)
class MissingRecordRow:
    classification: str
    dedup_key: str
    native_key: str | None
    source_keys: Mapping[str, list[str]]
    paper_db_trade_id: str | None
    closed_trades_csv_order_id: str | None
    symbol: str | None
    side: str | None
    open_time_utc: Any
    close_time_utc: str | None
    net_pnl: float | None
    profit_ratio: Any
    source_presence: list[str]
    missing_sources: list[str]
    db_csv_match_status: str
    normalization_status: str
    validation_status: Mapping[str, Any]
    causal_bucket: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "classification": self.classification,
            "dedup_key": self.dedup_key,
            "native_key": self.native_key,
            "source_keys": {source: list(keys) for source, keys in self.source_keys.items()},
            "paper_db_trade_id": self.paper_db_trade_id,
            "closed_trades_csv_order_id": self.closed_trades_csv_order_id,
            "symbol": self.symbol,
            "side": self.side,
            "open_time_utc": self.open_time_utc,
            "close_time_utc": self.close_time_utc,
            "net_pnl": self.net_pnl,
            "profit_ratio": self.profit_ratio,
            "source_presence": list(self.source_presence),
            "missing_sources": list(self.missing_sources),
            "db_csv_match_status": self.db_csv_match_status,
            "normalization_status": self.normalization_status,
            "validation_status": dict(self.validation_status),
            "causal_bucket": self.causal_bucket,
        }


def _csv_feedback_classification(source_records: Mapping[str, Sequence[Any]]) -> str:
    """Classify a reconciliation group for this diagnostic's own purpose.

    `classify_group()` (reused from paper_autotrain_source_key_reconciliation)
    checks `SOURCE_PAPER_DB not in present` before it ever checks feedback
    presence, so whenever the paper DB is absent or not read (the default,
    `--allow-paper-db-read` not passed) every group is classified
    `missing_in_db` and `missing_in_feedback` is never reached -- even when
    `closed_trades_csv` genuinely has a record `feedback_events` lacks. This
    diagnostic's whole purpose is detecting that CSV -> feedback gap, so it
    must not depend on paper_db being present. The paper DB is used only to
    enrich a missing row (trade_id, cross-check for conflicts); it must
    never gate the missing_in_feedback detection itself.
    """
    csv_present = bool(source_records.get(SOURCE_CSV))
    feedback_present = bool(source_records.get(SOURCE_FEEDBACK))
    if csv_present and feedback_present:
        return "conflicting" if has_field_conflict(source_records) else "reconciled_csv_feedback"
    if csv_present and not feedback_present:
        return "missing_in_feedback"
    if feedback_present and not csv_present:
        return "missing_in_csv"
    return "missing_in_both_csv_and_feedback"


def summarize_csv_feedback_classification(
    groups: Mapping[str, Mapping[str, Sequence[Any]]],
) -> dict[str, int]:
    """Group counts using `_csv_feedback_classification`, independent of paper_db.

    This is the authoritative source for `missing_in_feedback_count` and
    `conflicting_group_count` in the report -- `reconciliation_summary`
    (from the reused 3-way reconciliation module) is kept only as auxiliary
    context, because it inherits `classify_group()`'s paper_db-gating bug.
    """
    counts = {
        "reconciled_csv_feedback": 0,
        "missing_in_feedback": 0,
        "missing_in_csv": 0,
        "missing_in_both_csv_and_feedback": 0,
        "conflicting": 0,
    }
    for source_records in groups.values():
        counts[_csv_feedback_classification(source_records)] += 1
    return counts


def build_missing_record_rows(
    groups: Mapping[str, Mapping[str, Sequence[Any]]],
) -> list[MissingRecordRow]:
    """Build one full row per `missing_in_feedback` group, never truncated."""
    rows: list[MissingRecordRow] = []
    for key, source_records in sorted(groups.items()):
        classification = _csv_feedback_classification(source_records)
        if classification != "missing_in_feedback":
            continue

        db_records = source_records.get(SOURCE_PAPER_DB, ())
        csv_records = source_records.get(SOURCE_CSV, ())
        db_record = db_records[0] if db_records else None
        csv_record = csv_records[0] if csv_records else None
        primary = db_record or csv_record

        present = sorted(source for source, records in source_records.items() if records)
        # paper_db is only ever "missing" here if a read was actually
        # attempted for this group (SOURCE_PAPER_DB in source_records); a
        # None/not-requested read is reported as "not_queried", not
        # "missing", so callers cannot misread it as a data gap.
        candidate_missing = set(SOURCE_NAMES) - set(present)
        missing = sorted(
            source
            for source in candidate_missing
            if source != SOURCE_PAPER_DB or SOURCE_PAPER_DB in source_records
        )
        conflict = has_field_conflict(source_records) if db_record is not None else False

        db_raw = dict(db_record.raw) if db_record is not None else {}
        csv_raw = dict(csv_record.raw) if csv_record is not None else {}
        merged_raw = {**csv_raw, **db_raw}
        validation = _assess_stage_validation(merged_raw) if merged_raw else {
            "stage1_errors": ["no_raw_payload_available"],
            "stage1_would_pass": False,
            "stage2_would_pass": False,
            "would_pass_both_stages": False,
        }

        if validation["would_pass_both_stages"]:
            causal_bucket = "cadence_gap_unexplained_by_validation"
        else:
            first_error = (
                validation["stage1_errors"][0]
                if validation["stage1_errors"]
                else "stage2_normalization_rejected"
            )
            causal_bucket = f"validation_rejection:{first_error}"

        rows.append(
            MissingRecordRow(
                classification=classification,
                dedup_key=key,
                native_key=(db_record or csv_record).native_key if primary else None,
                source_keys={
                    source: [record.native_key for record in records]
                    for source, records in sorted(source_records.items())
                    if records
                },
                paper_db_trade_id=db_record.trade_id if db_record is not None else None,
                closed_trades_csv_order_id=csv_record.order_id if csv_record is not None else None,
                symbol=(primary.symbol if primary else None),
                side=(primary.side if primary else None),
                open_time_utc=_first_raw_value(merged_raw, OPEN_TIME_CANDIDATES),
                close_time_utc=(primary.close_time_utc if primary else None),
                net_pnl=(primary.pnl if primary else None),
                profit_ratio=_first_raw_value(merged_raw, PROFIT_RATIO_CANDIDATES),
                source_presence=present,
                missing_sources=missing,
                db_csv_match_status=(
                    "conflicting" if conflict else "match" if db_record is not None else "db_not_available"
                ),
                normalization_status=(
                    "normalized_from_closed_trades_csv_and_paper_db"
                    if db_record is not None
                    else "normalized_from_closed_trades_csv"
                ),
                validation_status=validation,
                causal_bucket=causal_bucket,
            )
        )
    return rows


# --------------------------------------------------------------------------
# Scope item 6: cadence-gap-mechanism vs validation-rejection, kept separate
# --------------------------------------------------------------------------


def _mtime_utc_or_none(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat()
    except OSError:
        return None


def assess_cadence_gap_mechanism(
    *,
    root: Path,
    missing_rows: Sequence[MissingRecordRow],
) -> dict[str, Any]:
    stage1_output_path = root / DEFAULT_FEEDBACK_STORE
    stage2_output_path = root / DEFAULT_FEEDBACK_EVENTS_PATH
    stage1_mtime_utc = _mtime_utc_or_none(stage1_output_path)
    stage2_mtime_utc = _mtime_utc_or_none(stage2_output_path)

    close_times = [
        first_datetime_from_value(row.close_time_utc)
        for row in missing_rows
        if row.close_time_utc is not None
    ]
    close_times = [value for value in close_times if value is not None]

    if stage1_mtime_utc is None or not close_times:
        status = "indeterminate_missing_evidence"
        all_after_stage1 = None
    else:
        stage1_mtime = first_datetime_from_value(stage1_mtime_utc)
        all_after_stage1 = bool(stage1_mtime is not None and all(ts > stage1_mtime for ts in close_times))
        status = "confirmed" if all_after_stage1 else "not_confirmed_by_this_run"

    return {
        "status": status,
        "stage1_output_path": str(stage1_output_path),
        "stage1_output_exists": stage1_output_path.exists(),
        "stage1_last_write_mtime_utc": stage1_mtime_utc,
        "stage2_output_path": str(stage2_output_path),
        "stage2_output_exists": stage2_output_path.exists(),
        "stage2_last_write_mtime_utc": stage2_mtime_utc,
        "missing_record_count_considered": len(close_times),
        "all_missing_records_after_stage1_last_write": all_after_stage1,
    }


def assess_validation_rejection(missing_rows: Sequence[MissingRecordRow]) -> dict[str, Any]:
    rejected = [row for row in missing_rows if not row.validation_status.get("would_pass_both_stages")]
    return {
        "status": "none_rejected" if not rejected else "some_rejected",
        "rejected_count": len(rejected),
        "rejected_dedup_keys": [row.dedup_key for row in rejected],
        "causal_bucket_counts": _count_causal_buckets(missing_rows),
    }


def _count_causal_buckets(missing_rows: Sequence[MissingRecordRow]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in missing_rows:
        counts[row.causal_bucket] = counts.get(row.causal_bucket, 0) + 1
    return counts


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


def _select_paper_db_source_against_csv(
    *,
    resolution: Any,
    closed_trades_csv_max_close_time_utc: str | None,
) -> dict[str, Any]:
    """Independent paper_db freshness verdict, compared directly against the CSV.

    `resolve_paper_db()` only compares candidates against a
    `watermark_close_time` argument; this diagnostic deliberately passes
    `watermark_close_time=None` (it must see every candidate record, not
    just ones after some watermark), which also disables that function's
    own staleness comparison -- so a genuinely stale runtime DB (e.g. data
    ending weeks before the CSV's latest close) gets reported as fresh
    simply because no watermark was given to compare it against.

    This function does not modify `resolve_paper_db()` (shared, out of
    scope). It re-derives freshness by comparing each candidate's own
    `max_close_time_utc` directly against `closed_trades_csv`'s
    `max_close_time_utc`, and prefers a fresh snapshot candidate over a
    stale runtime candidate when both exist. It only affects paper_db
    authority/enrichment reporting -- it never gates `missing_in_feedback`
    detection, which is computed independently in
    `_csv_feedback_classification`.
    """
    csv_dt = (
        first_datetime_from_value(closed_trades_csv_max_close_time_utc)
        if closed_trades_csv_max_close_time_utc
        else None
    )

    def candidate_dt(candidate: Any) -> Any:
        if candidate is None or not getattr(candidate, "max_close_time_utc", None):
            return None
        return first_datetime_from_value(candidate.max_close_time_utc)

    snapshot = getattr(resolution, "snapshot_best", None)
    runtime = getattr(resolution, "runtime_best", None)

    if csv_dt is None:
        return {
            "selected_path": resolution.selected_path,
            "selected_source_kind": resolution.selected_source_kind,
            "status": resolution.authority_status,
            "reason": "closed_trades_csv_max_close_time_utc_unavailable_direct_check_skipped",
            "paper_db_max_close_time_utc": None,
            "closed_trades_csv_max_close_time_utc": None,
        }

    snapshot_dt = candidate_dt(snapshot)
    if snapshot is not None and snapshot_dt is not None and snapshot_dt >= csv_dt:
        return {
            "selected_path": snapshot.path,
            "selected_source_kind": snapshot.source_kind,
            "status": "snapshot_db_fresh_against_csv",
            "reason": "snapshot_max_close_time_utc_not_before_closed_trades_csv_max_close_time_utc",
            "paper_db_max_close_time_utc": snapshot.max_close_time_utc,
            "closed_trades_csv_max_close_time_utc": closed_trades_csv_max_close_time_utc,
        }

    runtime_dt = candidate_dt(runtime)
    if runtime is not None and runtime_dt is not None and runtime_dt >= csv_dt:
        return {
            "selected_path": runtime.path,
            "selected_source_kind": runtime.source_kind,
            "status": "runtime_db_fresh_against_csv",
            "reason": "runtime_max_close_time_utc_not_before_closed_trades_csv_max_close_time_utc",
            "paper_db_max_close_time_utc": runtime.max_close_time_utc,
            "closed_trades_csv_max_close_time_utc": closed_trades_csv_max_close_time_utc,
        }

    stale_candidate = snapshot or runtime
    if stale_candidate is None:
        return {
            "selected_path": resolution.selected_path,
            "selected_source_kind": resolution.selected_source_kind,
            "status": resolution.authority_status,
            "reason": "no_paper_db_candidate_available_direct_check_skipped",
            "paper_db_max_close_time_utc": None,
            "closed_trades_csv_max_close_time_utc": closed_trades_csv_max_close_time_utc,
        }
    return {
        "selected_path": stale_candidate.path,
        "selected_source_kind": stale_candidate.source_kind,
        "status": f"{stale_candidate.source_kind}_stale_against_csv",
        "reason": "selected_candidate_max_close_time_utc_before_closed_trades_csv_max_close_time_utc",
        "paper_db_max_close_time_utc": stale_candidate.max_close_time_utc,
        "closed_trades_csv_max_close_time_utc": closed_trades_csv_max_close_time_utc,
    }


@dataclass(frozen=True)
class DiagnosticsPaths:
    paper_db_path: Path | None
    closed_trades_csv_path: Path
    feedback_events_path: Path
    output_json: Path
    output_markdown: Path


def build_paper_autotrain_feedback_gap_diagnostics_v1(
    *,
    project_root: str | Path,
    paper_db_path: str | Path | None = None,
    allow_paper_db_read: bool = False,
    write_report: bool = False,
    output_json_path: str | Path | None = None,
    output_markdown_path: str | Path | None = None,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    """Build the read-only paper autotrain feedback gap diagnostics report."""

    root = Path(project_root).resolve()
    generated_at = generated_at_utc or datetime.now(UTC).isoformat()

    output_json = resolve_path(root, output_json_path, DEFAULT_OUTPUT_JSON)
    output_markdown = resolve_path(root, output_markdown_path, DEFAULT_OUTPUT_MARKDOWN)
    write_errors = validate_write_request(root, output_json, output_markdown, write_report)

    writer_search = search_writers(root)

    closed_csv = load_csv_source(root / CLOSED_TRADES_CSV, None)
    feedback = load_feedback_source(root / FEEDBACK_EVENTS, None)

    explicit_db = resolve_path(root, paper_db_path, Path("")) if paper_db_path else None
    resolution = resolve_paper_db(
        root=root,
        explicit_db=explicit_db,
        read_requested=allow_paper_db_read,
        watermark_close_time=None,
        closed_csv_new_record_count=0,
        feedback_new_record_count=0,
    )

    if allow_paper_db_read:
        paper_db_freshness = _select_paper_db_source_against_csv(
            resolution=resolution,
            closed_trades_csv_max_close_time_utc=closed_csv.metadata.get("max_close_time_utc"),
        )
    else:
        paper_db_freshness = {
            "selected_path": None,
            "selected_source_kind": None,
            "status": resolution.authority_status,
            "reason": "paper_db_read_not_requested",
            "paper_db_max_close_time_utc": None,
            "closed_trades_csv_max_close_time_utc": closed_csv.metadata.get("max_close_time_utc"),
        }

    paper_db_selected_path = paper_db_freshness["selected_path"] or resolution.selected_path
    paper_db_selected_source_kind = paper_db_freshness["selected_source_kind"] or resolution.selected_source_kind

    paper_db = load_paper_db_source(
        path=paper_db_selected_path,
        read_requested=allow_paper_db_read,
        watermark_close_time=None,
        selected_source_kind=paper_db_selected_source_kind,
    )

    source_loads = {
        SOURCE_PAPER_DB: paper_db,
        SOURCE_CSV: closed_csv,
        SOURCE_FEEDBACK: feedback,
    }
    groups = build_reconciliation_groups(source_loads)
    source_summary = summarize_sources(source_loads)
    reconciliation_summary = summarize_reconciliation(groups)
    csv_feedback_classification_counts = summarize_csv_feedback_classification(groups)

    missing_rows = build_missing_record_rows(groups)
    cadence_gap_mechanism_status = assess_cadence_gap_mechanism(root=root, missing_rows=missing_rows)
    validation_rejection_status = assess_validation_rejection(missing_rows)

    status, reason, blockers, warnings = decide_status(
        allow_paper_db_read=allow_paper_db_read,
        paper_db=paper_db,
        write_errors=write_errors,
    )

    paths = DiagnosticsPaths(
        paper_db_path=paper_db_selected_path,
        closed_trades_csv_path=root / CLOSED_TRADES_CSV,
        feedback_events_path=root / FEEDBACK_EVENTS,
        output_json=output_json,
        output_markdown=output_markdown,
    )

    safety = safety_flags(write_report_requested=write_report, write_report_performed=False)

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": generated_at,
        "project_root": str(root),
        "status": status,
        "reason": reason,
        "paper_db_read_requested": bool(allow_paper_db_read),
        "paper_db_path": str(paths.paper_db_path) if paths.paper_db_path else None,
        "paper_db_authority_status": paper_db_freshness["status"],
        "paper_db_selected_reason": paper_db_freshness["reason"],
        "paper_db_freshness_check": paper_db_freshness,
        "closed_trades_csv_path": str(paths.closed_trades_csv_path),
        "feedback_events_path": str(paths.feedback_events_path),
        "source_status": {name: source_to_status(load) for name, load in source_loads.items()},
        "source_summary": source_summary,
        "reconciliation_summary": reconciliation_summary,
        "paper_db_normalized_record_count": source_summary[SOURCE_PAPER_DB]["normalized_record_count"],
        "closed_trades_csv_normalized_record_count": source_summary[SOURCE_CSV]["normalized_record_count"],
        "feedback_events_normalized_record_count": source_summary[SOURCE_FEEDBACK]["normalized_record_count"],
        "missing_in_feedback_count": csv_feedback_classification_counts["missing_in_feedback"],
        "conflicting_group_count": csv_feedback_classification_counts["conflicting"],
        "csv_feedback_classification_counts": csv_feedback_classification_counts,
        "missing_in_feedback_records": [row.to_dict() for row in missing_rows],
        "cadence_gap_mechanism_status": cadence_gap_mechanism_status,
        "validation_rejection_status": validation_rejection_status,
        **writer_search,
        "blockers": sorted(set(write_errors) | set(blockers)),
        "warnings": sorted(set(warnings)),
        "output_paths": {"json": str(output_json), "markdown": str(output_markdown)},
        **safety,
        "safety_flags": safety,
    }
    return maybe_write_report(report, paths, write_report, write_errors)


def decide_status(
    *,
    allow_paper_db_read: bool,
    paper_db: Any,
    write_errors: Sequence[str],
) -> tuple[str, str, list[str], list[str]]:
    if write_errors:
        return "blocked", "write_boundary_validation_failed", list(write_errors), []
    if not allow_paper_db_read:
        return "warning", "paper_db_read_not_requested", [], ["paper_db_read_not_requested_reconciliation_uses_csv_and_feedback_only"]
    if paper_db.status in {"missing", "unreadable", "invalid_schema"}:
        return "warning", "paper_db_source_missing_or_unreadable", [], ["paper_db_source_missing_or_unreadable"]
    return "ok", "diagnostics_completed", [], []


# --------------------------------------------------------------------------
# Report writing (mirrors house convention: atomic write, gated, safety-flagged)
# --------------------------------------------------------------------------


def maybe_write_report(
    report: dict[str, Any],
    paths: DiagnosticsPaths,
    write_report: bool,
    write_errors: Sequence[str],
) -> dict[str, Any]:
    if not write_report or write_errors:
        return report
    write_json(paths.output_json, report)
    atomic_write_text(paths.output_markdown, render_markdown(report))
    safety = safety_flags(write_report_requested=True, write_report_performed=True)
    report.update(safety)
    report["safety_flags"] = safety
    report["write_performed"] = True
    report["write_report_performed"] = True
    write_json(paths.output_json, report)
    atomic_write_text(paths.output_markdown, render_markdown(report))
    return report


def render_markdown(report: Mapping[str, Any]) -> str:
    return "\n".join(
        [
            "# Paper Autotrain Feedback Gap Diagnostics V1",
            "",
            f"- Status: `{report.get('status')}`",
            f"- Reason: `{report.get('reason')}`",
            f"- paper_db normalized: `{report.get('paper_db_normalized_record_count')}`",
            f"- closed_trades_csv normalized: `{report.get('closed_trades_csv_normalized_record_count')}`",
            f"- feedback_events normalized: `{report.get('feedback_events_normalized_record_count')}`",
            f"- missing_in_feedback: `{report.get('missing_in_feedback_count')}`",
            f"- conflicting: `{report.get('conflicting_group_count')}`",
            f"- paper_db_authority_status: `{report.get('paper_db_authority_status')}`",
            f"- parquet writer count: `{report.get('paper_closed_trades_incremental_writer_count')}`",
            f"- jsonl writer count: `{report.get('feedback_events_jsonl_writer_count')}`",
            f"- unexpected writer count: `{report.get('unexpected_writer_count')}`",
            f"- cadence_gap_mechanism_status: `{(report.get('cadence_gap_mechanism_status') or {}).get('status')}`",
            f"- validation_rejection_status: `{(report.get('validation_rejection_status') or {}).get('status')}`",
            "",
            "## Conclusao",
            "",
            "Este diagnostico apenas explica o gap missing_in_feedback com evidencia.",
            "Ele nao cria microbatch, nao treina, nao promove, nao registra scheduler",
            "e nao altera Freqtrade, RiskManager, Qlib runtime, IA Shadow runtime,",
            "active signals ou watermark.",
            "",
        ]
    )


def validate_write_request(root: Path, output_json: Path, output_markdown: Path, write_report: bool) -> list[str]:
    if not write_report:
        return []
    errors: list[str] = []
    errors.extend(validate_path_under(root, output_json, ALLOWED_REPORT_ROOT, "report_path_outside_data_reports"))
    errors.extend(validate_path_under(root, output_markdown, ALLOWED_REPORT_ROOT, "report_path_outside_data_reports"))
    return sorted(set(errors))


def validate_path_under(root: Path, path: Path, allowed: Path, reason: str) -> list[str]:
    try:
        path.resolve().relative_to((root / allowed).resolve())
    except ValueError:
        return [reason]
    return []


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    import json

    atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=str) + "\n")


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    temp_path.write_text(text, encoding="utf-8")
    temp_path.replace(path)


def safety_flags(*, write_report_requested: bool, write_report_performed: bool) -> dict[str, bool]:
    return {
        "paper_only": True,
        "shadow_only": True,
        "research_only": True,
        "read_only": True,
        "write_report_requested": bool(write_report_requested),
        "write_report_performed": bool(write_report_performed),
        "write_performed": bool(write_report_performed),
        "writes_runtime": False,
        "writes_sqlite": False,
        "writes_parquet": False,
        "writes_feedback": False,
        "writes_microbatch": False,
        "would_create_microbatch": False,
        "would_write_microbatch": False,
        "would_run_training": False,
        "would_evaluate_candidate": False,
        "would_promote_model": False,
        "ready_for_training": False,
        "ready_for_promotion": False,
        "model_promotion_performed": False,
        "active_model_changed": False,
        "sends_orders": False,
        "changes_risk": False,
        "exchange_private_access": False,
        "live_trading_enabled": False,
        "live_release_allowed": False,
        "canary_release_allowed": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "updates_freqtrade": False,
        "updates_freqtrade_config": False,
        "updates_freqtrade_strategy": False,
        "updates_risk_manager": False,
        "updates_qlib_runtime": False,
        "qlib_runtime_updated": False,
        "updates_ai_shadow_thresholds": False,
        "ai_shadow_runtime_updated": False,
        "updates_active_signals": False,
        "alters_watermark": False,
        "scheduler_registered": False,
        "creates_cron": False,
        "creates_systemd_timer": False,
        "creates_windows_task": False,
        "starts_service": False,
    }
