"""Prospective V3-selected signal publisher for isolated Paper-B treatment."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = "canonical_treatment_signal_publisher_v1"
LEDGER_SCHEMA_VERSION = "canonical_treatment_decision_ledger_v1"
REPORT_SCHEMA_VERSION = "canonical_treatment_signal_publisher_report_v1"
CROSSWALK_SCHEMA_VERSION = "qlib_v3_operational_decision_crosswalk_v1"

SAFE_FLAGS = {
    "paper_only": True,
    "shadow_only": True,
    "research_only": True,
    "operational_authority": False,
    "live_trading_enabled": False,
    "live_release_allowed": False,
    "canary_release_allowed": False,
    "order_submission_enabled": False,
    "real_order_submission_enabled": False,
    "exchange_private_access": False,
    "changes_risk": False,
    "changes_model": False,
    "changes_strategy": False,
    "changes_leverage": False,
    "changes_stake": False,
}

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}")
_SHA256 = re.compile(r"[0-9a-f]{64}")


class CanonicalTreatmentError(RuntimeError):
    pass


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).astimezone(UTC).isoformat()


def _read_json(path: Path, required: bool = True) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        if required:
            raise CanonicalTreatmentError(f"json_source_missing:{path}")
        return {}
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CanonicalTreatmentError(
            f"json_source_invalid:{path}:{type(exc).__name__}"
        ) from exc
    if not isinstance(value, dict):
        raise CanonicalTreatmentError(f"json_object_required:{path}")
    return value


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    raw = json.dumps(
        dict(payload),
        sort_keys=True,
        ensure_ascii=False,
        indent=2,
        allow_nan=False,
    ).encode("utf-8")
    try:
        with temp.open("wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _hash(payload: Mapping[str, Any]) -> str:
    raw = (
        json.dumps(
            dict(payload),
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _id(value: object, field: str) -> str:
    text = str(value or "")
    if not _IDENTIFIER.fullmatch(text):
        raise CanonicalTreatmentError(f"identifier_invalid:{field}")
    return text


def _sha(value: object, field: str) -> str:
    text = str(value or "").lower()
    if not _SHA256.fullmatch(text):
        raise CanonicalTreatmentError(f"sha256_invalid:{field}")
    return text


def _finite(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return out if math.isfinite(out) else None


def _crosswalk_index(evidence: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    signals = evidence.get("signals")
    if not isinstance(signals, list):
        raise CanonicalTreatmentError("v3_signals_list_required")

    result: dict[str, dict[str, Any]] = {}
    for row in signals:
        if not isinstance(row, Mapping):
            raise CanonicalTreatmentError("v3_signal_object_required")

        cw = row.get("operational_crosswalk")
        if cw is None:
            continue
        if not isinstance(cw, Mapping):
            raise CanonicalTreatmentError("operational_crosswalk_not_object")

        required = {
            "schema_version",
            "signal_id",
            "operational_decision_event_id",
            "operational_decision_payload_sha256",
            "v3_decision_event_id",
            "v3_decision_payload_sha256",
            "crosswalk_sha256",
        }
        if set(cw) != required:
            raise CanonicalTreatmentError("operational_crosswalk_fields_invalid")
        if cw.get("schema_version") != CROSSWALK_SCHEMA_VERSION:
            raise CanonicalTreatmentError("operational_crosswalk_schema_invalid")

        body = {
            "schema_version": CROSSWALK_SCHEMA_VERSION,
            "signal_id": _id(cw.get("signal_id"), "crosswalk.signal_id"),
            "operational_decision_event_id": _id(
                cw.get("operational_decision_event_id"),
                "crosswalk.operational_decision_event_id",
            ),
            "operational_decision_payload_sha256": _sha(
                cw.get("operational_decision_payload_sha256"),
                "crosswalk.operational_decision_payload_sha256",
            ),
            "v3_decision_event_id": _id(
                cw.get("v3_decision_event_id"),
                "crosswalk.v3_decision_event_id",
            ),
            "v3_decision_payload_sha256": _sha(
                cw.get("v3_decision_payload_sha256"),
                "crosswalk.v3_decision_payload_sha256",
            ),
        }
        crosswalk_sha = _sha(
            cw.get("crosswalk_sha256"),
            "crosswalk.crosswalk_sha256",
        )
        if _hash(body) != crosswalk_sha:
            raise CanonicalTreatmentError("operational_crosswalk_hash_mismatch")

        decision = row.get("decision")
        if not isinstance(decision, Mapping):
            raise CanonicalTreatmentError("v3_decision_object_required")

        if (
            body["signal_id"] != row.get("signal_id")
            or body["v3_decision_event_id"] != row.get("decision_event_id")
            or body["signal_id"] != decision.get("signal_id")
            or body["v3_decision_event_id"] != decision.get("event_id")
            or body["v3_decision_payload_sha256"] != decision.get("payload_sha256")
        ):
            raise CanonicalTreatmentError("operational_crosswalk_v3_identity_mismatch")

        shadow = str(decision.get("ai_shadow_decision") or "").upper()
        if shadow not in {"ALLOW", "ABSTAIN"}:
            raise CanonicalTreatmentError("v3_ai_shadow_decision_invalid")

        normalized = {
            **body,
            "crosswalk_sha256": crosswalk_sha,
            "ai_shadow_decision": shadow,
            "qlib_score": _finite(decision.get("qlib_score")),
            "pair": str(decision.get("pair") or ""),
            "symbol": str(decision.get("symbol") or ""),
            "side": str(decision.get("side") or "").lower(),
        }

        op_id = body["operational_decision_event_id"]
        prior = result.get(op_id)
        if prior is not None and prior != normalized:
            raise CanonicalTreatmentError("operational_decision_crosswalk_collision")
        result[op_id] = normalized
    return result


def _load_ledger(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "schema_version": LEDGER_SCHEMA_VERSION,
            "experiment_id": "canonical-treatment-paper-v1",
            "started_at_utc": _iso(),
            "rows": [],
            **SAFE_FLAGS,
        }

    value = _read_json(path)
    if value.get("schema_version") != LEDGER_SCHEMA_VERSION:
        raise CanonicalTreatmentError("treatment_ledger_schema_mismatch")
    if not isinstance(value.get("rows"), list):
        raise CanonicalTreatmentError("treatment_ledger_rows_invalid")
    return value


def _merge_ledger(
    ledger: dict[str, Any],
    observations: list[dict[str, Any]],
    now: str,
) -> dict[str, Any]:
    current: dict[str, dict[str, Any]] = {}
    for row in ledger["rows"]:
        if not isinstance(row, Mapping):
            raise CanonicalTreatmentError("treatment_ledger_row_invalid")
        op_id = _id(
            row.get("operational_decision_event_id"),
            "ledger.operational_decision_event_id",
        )
        if op_id in current:
            raise CanonicalTreatmentError("treatment_ledger_duplicate_decision")
        current[op_id] = dict(row)

    for obs in observations:
        op_id = str(obs["operational_decision_event_id"])
        old = current.get(op_id)
        if old is None:
            current[op_id] = obs
            continue

        for field in (
            "operational_decision_payload_sha256",
            "signal_id",
            "pair",
            "symbol",
            "side",
        ):
            if old.get(field) != obs.get(field):
                raise CanonicalTreatmentError(
                    f"treatment_ledger_identity_conflict:{field}"
                )

        if old.get("status") == "scored" and obs.get("status") == "scored":
            for field in (
                "v3_decision_event_id",
                "v3_decision_payload_sha256",
                "crosswalk_sha256",
                "selected",
                "ai_shadow_decision",
                "qlib_score",
            ):
                if old.get(field) != obs.get(field):
                    raise CanonicalTreatmentError(
                        f"treatment_ledger_scored_conflict:{field}"
                    )
        elif old.get("status") != "scored" and obs.get("status") == "scored":
            obs["first_observed_at_utc"] = old.get(
                "first_observed_at_utc",
                now,
            )
            current[op_id] = obs
            continue

        old["last_observed_at_utc"] = now
        if obs.get("valid_until"):
            old["valid_until"] = obs["valid_until"]
        current[op_id] = old

    ledger["rows"] = sorted(
        current.values(),
        key=lambda row: (
            str(row.get("first_observed_at_utc") or ""),
            str(row.get("operational_decision_event_id") or ""),
        ),
    )
    ledger["updated_at_utc"] = now
    ledger.update(SAFE_FLAGS)
    return ledger


def _mirror_exit_control(source: Path | None, output_dir: Path) -> None:
    destination = output_dir / "paper_exit_control.json"
    if source is None or not source.exists():
        destination.unlink(missing_ok=True)
        return
    _write_json(destination, _read_json(source))


def build_treatment_artifact(
    *,
    control_signals_path: Path,
    v3_evidence_path: Path,
    output_path: Path,
    report_path: Path,
    ledger_path: Path,
    control_exit_control_path: Path | None = None,
) -> dict[str, Any]:
    now = _iso()

    try:
        control = _read_json(control_signals_path)
        evidence = _read_json(v3_evidence_path)
        index = _crosswalk_index(evidence)
        signals = control.get("signals")
        if not isinstance(signals, list):
            raise CanonicalTreatmentError("control_signals_list_required")

        selected: list[dict[str, Any]] = []
        observations: list[dict[str, Any]] = []
        scored = 0
        abstained = 0
        unmatched = 0

        for raw in signals:
            if not isinstance(raw, Mapping):
                raise CanonicalTreatmentError("control_signal_object_required")
            signal = dict(raw)
            if signal.get("risk_approved") is not True:
                raise CanonicalTreatmentError("control_signal_not_risk_approved")

            decision_ledger = signal.get("decision_ledger")
            if not isinstance(decision_ledger, Mapping):
                raise CanonicalTreatmentError("control_signal_decision_ledger_missing")

            op_id = _id(
                decision_ledger.get("decision_event_id"),
                "control.decision_event_id",
            )
            op_sha = _sha(
                decision_ledger.get("decision_payload_sha256"),
                "control.decision_payload_sha256",
            )
            signal_id = _id(signal.get("signal_id"), "control.signal_id")
            pair = str(signal.get("pair") or "")
            symbol = str(signal.get("symbol") or "")
            side = str(signal.get("side") or "").lower()
            if not pair or not symbol or side not in {"long", "short"}:
                raise CanonicalTreatmentError("control_signal_identity_invalid")

            v3 = index.get(op_id)
            status = "unmatched"
            selected_flag: bool | None = None

            if v3 is None:
                unmatched += 1
            else:
                if (
                    v3["operational_decision_payload_sha256"] != op_sha
                    or v3["signal_id"] != signal_id
                    or v3["pair"] != pair
                    or v3["symbol"] != symbol
                    or v3["side"] != side
                ):
                    raise CanonicalTreatmentError(
                        "control_to_v3_crosswalk_identity_mismatch"
                    )
                status = "scored"
                scored += 1
                selected_flag = v3["ai_shadow_decision"] == "ALLOW"
                if selected_flag:
                    selected.append(signal)
                else:
                    abstained += 1

            observations.append(
                {
                    "operational_decision_event_id": op_id,
                    "operational_decision_payload_sha256": op_sha,
                    "signal_id": signal_id,
                    "pair": pair,
                    "symbol": symbol,
                    "side": side,
                    "valid_until": signal.get("valid_until"),
                    "status": status,
                    "selected": selected_flag,
                    "ai_shadow_decision": (
                        v3["ai_shadow_decision"] if v3 else None
                    ),
                    "qlib_score": v3["qlib_score"] if v3 else None,
                    "v3_decision_event_id": (
                        v3["v3_decision_event_id"] if v3 else None
                    ),
                    "v3_decision_payload_sha256": (
                        v3["v3_decision_payload_sha256"] if v3 else None
                    ),
                    "crosswalk_sha256": (
                        v3["crosswalk_sha256"] if v3 else None
                    ),
                    "first_observed_at_utc": now,
                    "last_observed_at_utc": now,
                }
            )

        output = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": now,
            "source": "canonical_treatment_v3_shadow_allow_v1",
            "runtime_mode": "paper",
            "control_generated_at": control.get("generated_at"),
            "control_source": control.get("source"),
            "model_version": control.get("model_version"),
            "signals": selected,
            "treatment_policy": {
                "selector": "qlib_v3_ai_shadow_decision",
                "allow_value": "ALLOW",
                "abstain_value": "ABSTAIN",
                "exact_operational_crosswalk_required": True,
                "preserves_control_signal_object": True,
            },
            **SAFE_FLAGS,
        }

        ledger = _merge_ledger(
            _load_ledger(ledger_path),
            observations,
            now,
        )
        rows = ledger["rows"]
        total = len(rows)
        scored_total = sum(row.get("status") == "scored" for row in rows)
        selected_total = sum(
            row.get("status") == "scored" and row.get("selected") is True
            for row in rows
        )

        report = {
            "schema_version": REPORT_SCHEMA_VERSION,
            "status": "ok",
            "reason": (
                "treatment_signals_published"
                if selected
                else "no_v3_selected_signal_current_cycle"
            ),
            "generated_at_utc": now,
            "control_signal_count": len(signals),
            "scored_signal_count": scored,
            "selected_signal_count": len(selected),
            "abstained_signal_count": abstained,
            "unmatched_signal_count": unmatched,
            "cumulative_candidate_decision_count": total,
            "cumulative_scored_decision_count": scored_total,
            "cumulative_selected_decision_count": selected_total,
            "cumulative_scorer_coverage": (
                scored_total / total if total else None
            ),
            "output_path": str(output_path),
            "ledger_path": str(ledger_path),
            "v3_evidence_path": str(v3_evidence_path),
            **SAFE_FLAGS,
        }

        _write_json(output_path, output)
        _write_json(ledger_path, ledger)
        _write_json(report_path, report)
        _mirror_exit_control(control_exit_control_path, output_path.parent)
        return report

    except Exception as exc:
        reason = (
            str(exc)
            if isinstance(exc, CanonicalTreatmentError)
            else f"canonical_treatment_boundary_failed:{type(exc).__name__}"
        )
        blocked = {
            "schema_version": REPORT_SCHEMA_VERSION,
            "status": "blocked",
            "reason": reason,
            "generated_at_utc": now,
            **SAFE_FLAGS,
        }
        empty = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": now,
            "source": "canonical_treatment_v3_shadow_allow_v1",
            "runtime_mode": "paper",
            "signals": [],
            "treatment_policy": {"fail_closed": True},
            **SAFE_FLAGS,
        }
        try:
            _write_json(output_path, empty)
            _write_json(report_path, blocked)
        except OSError:
            pass
        return blocked


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--control-signals",
        default="data/runtime/active_freqtrade_signals.json",
    )
    parser.add_argument("--v3-evidence", required=True)
    parser.add_argument(
        "--output",
        default="data/runtime/canonical_treatment/active_freqtrade_signals.json",
    )
    parser.add_argument(
        "--report",
        default="data/reports/canonical_treatment/signal_publisher_report_v1.json",
    )
    parser.add_argument(
        "--ledger",
        default="data/research/canonical_treatment/decision_ledger_v1.json",
    )
    parser.add_argument(
        "--control-exit-control",
        default="data/runtime/paper_exit_control.json",
    )
    parser.add_argument("--interval-seconds", type=float, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    def once() -> dict[str, Any]:
        return build_treatment_artifact(
            control_signals_path=Path(args.control_signals),
            v3_evidence_path=Path(args.v3_evidence),
            output_path=Path(args.output),
            report_path=Path(args.report),
            ledger_path=Path(args.ledger),
            control_exit_control_path=Path(args.control_exit_control),
        )

    if args.interval_seconds is None:
        report = once()
        print(json.dumps(report, sort_keys=True, ensure_ascii=False))
        return 0 if report.get("status") == "ok" else 2

    interval = max(1.0, float(args.interval_seconds))
    while True:
        report = once()
        print(
            json.dumps(report, sort_keys=True, ensure_ascii=False),
            flush=True,
        )
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
