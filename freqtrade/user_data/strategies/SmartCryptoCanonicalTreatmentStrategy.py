"""Paper B admission fence. Control A keeps SmartCryptoSignalStrategy unchanged."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import socket
import stat
import struct
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from SmartCryptoSignalStrategy import SmartCryptoSignalStrategy


class TreatmentAdmissionDenied(ValueError):
    """A deterministic, non-sensitive admission rejection."""


def _digest(payload: Mapping[str, Any]) -> str:
    raw = (
        json.dumps(
            dict(payload),
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def _utc(value: object) -> datetime:
    if not isinstance(value, str):
        raise TreatmentAdmissionDenied("timestamp_required")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise TreatmentAdmissionDenied("timestamp_timezone_required")
    return parsed.astimezone(UTC)


def _read_object(path: Path) -> dict[str, Any]:
    if not path.is_absolute() or any(parent.is_symlink() for parent in path.parents):
        raise TreatmentAdmissionDenied("source_path_invalid")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as handle:
        before = os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > 1048576:
            raise TreatmentAdmissionDenied("source_type_or_size_invalid")
        raw = handle.read(1048577)
        after = os.fstat(handle.fileno())
    current = path.lstat()

    def fingerprint(s: os.stat_result) -> tuple[int, int, int, int, int]:
        # Windows 3.12 exposes differing creation-time views after atomic rename.
        change_time = s.st_ctime_ns if os.name == "posix" else 0
        return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, change_time

    if fingerprint(before) != fingerprint(after) or fingerprint(after) != fingerprint(current):
        raise TreatmentAdmissionDenied("source_changed_during_read")
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise TreatmentAdmissionDenied("source_object_required")
    return result


class SmartCryptoCanonicalTreatmentStrategy(SmartCryptoSignalStrategy):
    _treatment_signal_path = Path("/run/canonical-treatment-output/active_freqtrade_signals.json")
    _publisher_report_path = Path("/run/canonical-treatment-report/signal_publisher_report_v1.json")
    _publisher_socket_path = Path("/run/canonical-treatment-report/publisher_authorization_v1.sock")
    _signal_paths = [_treatment_signal_path]
    # Exit callbacks and their permissive protective-exit semantics stay inherited.
    _exit_control_paths = [Path("/run/canonical-treatment-output/paper_exit_control.json")]
    position_adjustment_enable = False
    _publication_max_age = 20.0
    _authorization_timeout = 0.5
    _authorization_schema = "canonical_treatment_entry_authorization_v1"

    def _publication(self) -> tuple[dict[str, Any], dict[str, Any]]:
        payload = _read_object(self._treatment_signal_path)
        report = _read_object(self._publisher_report_path)
        if (
            payload.get("schema_version") != "canonical_treatment_signal_publisher_v1"
            or report.get("schema_version") != "canonical_treatment_signal_publisher_report_v1"
            or report.get("status") != "ok"
            or payload.get("runtime_mode") != "paper"
            or payload.get("paper_only") is not True
            or not re.fullmatch(r"[0-9a-f]{32}", str(payload.get("publication_id") or ""))
            or report.get("publication_id") != payload.get("publication_id")
            or report.get("published_payload_sha256") != _digest(payload)
            or payload.get("generated_at") != report.get("generated_at_utc")
        ):
            raise TreatmentAdmissionDenied("publication_binding_invalid")
        age = (datetime.now(UTC) - _utc(report.get("generated_at_utc"))).total_seconds()
        if not 0 <= age <= self._publication_max_age:
            raise TreatmentAdmissionDenied("publication_stale_or_future")
        if not isinstance(payload.get("signals"), list):
            raise TreatmentAdmissionDenied("signals_list_required")
        return payload, report

    def _authorize(self, request: dict[str, Any]) -> float:
        started = datetime.now(UTC)
        deadline = time.monotonic() + self._authorization_timeout
        path = self._publisher_socket_path
        if not path.is_absolute() or any(parent.is_symlink() for parent in path.parents):
            raise TreatmentAdmissionDenied("authority_path_invalid")
        node = path.lstat()
        if not stat.S_ISSOCK(node.st_mode) or node.st_uid != 10001:
            raise TreatmentAdmissionDenied("authority_identity_invalid")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
            channel.settimeout(max(0.001, deadline - time.monotonic()))
            channel.connect(str(path))
            _, uid, _ = struct.unpack(
                "3i",
                channel.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")),
            )
            if uid != 10001:
                raise TreatmentAdmissionDenied("authority_peer_uid_invalid")
            channel.settimeout(max(0.001, deadline - time.monotonic()))
            channel.sendall(json.dumps(request, allow_nan=False).encode() + b"\n")
            raw = b""
            while b"\n" not in raw and len(raw) <= 8192:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TreatmentAdmissionDenied("authority_deadline_exceeded")
                channel.settimeout(remaining)
                chunk = channel.recv(1024)
                if not chunk:
                    raise TreatmentAdmissionDenied("authority_response_incomplete")
                raw += chunk
        if time.monotonic() > deadline or len(raw) > 8192 or not raw.endswith(b"\n"):
            raise TreatmentAdmissionDenied("authority_response_invalid")
        response = json.loads(raw)
        if (
            not isinstance(response, dict)
            or response.get("allowed") is not True
            or response.get("schema_version") != self._authorization_schema
            or response.get("nonce") != request["nonce"]
            or response.get("request_sha256") != _digest(request)
            or not started <= _utc(response.get("checked_at_utc")) <= datetime.now(UTC)
        ):
            raise TreatmentAdmissionDenied("authority_denied_or_unbound")
        return deadline

    def _decision(
        self, pair: str, side: str | None = None, entry_tag: str | None = None
    ) -> dict[str, Any]:
        try:
            if self.config.get("dry_run") is not True:
                raise TreatmentAdmissionDenied("paper_configuration_required")
            payload, report = self._publication()
            identities: set[str] = set()
            signals = payload["signals"]
            for row in signals:
                if not isinstance(row, dict) or not isinstance(row.get("decision_ledger"), dict):
                    raise TreatmentAdmissionDenied("signal_identity_invalid")
                event = row["decision_ledger"].get("decision_event_id")
                if not isinstance(event, str) or not re.fullmatch(
                    r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", event
                ):
                    raise TreatmentAdmissionDenied("decision_identity_invalid")
                if event in identities:
                    raise TreatmentAdmissionDenied("decision_identity_collision")
                identities.add(event)
            matches = [
                row
                for row in signals
                if row.get("pair") == pair and (side is None or row.get("side") == side)
            ]
            if len(matches) != 1:
                raise TreatmentAdmissionDenied("single_exact_signal_required")
            signal = matches[0]
            ledger = signal["decision_ledger"]
            actual_side = signal.get("side")
            signal_id = signal.get("signal_id")
            if (
                signal.get("risk_approved") is not True
                or actual_side not in {"long", "short"}
                or signal.get("symbol") != self._symbol_from_pair(pair)
                or not isinstance(signal_id, str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", signal_id)
                or ledger.get("signal_id", signal_id) != signal_id
                or not re.fullmatch(
                    r"[0-9a-f]{64}", str(ledger.get("decision_payload_sha256") or "")
                )
                or _utc(signal.get("valid_until")) <= datetime.now(UTC)
                or (
                    side is not None
                    and entry_tag != self._entry_tag(side, ledger["decision_event_id"])
                )
            ):
                raise TreatmentAdmissionDenied("signal_approval_identity_or_expiry_invalid")
            request = {
                "schema_version": self._authorization_schema,
                "nonce": uuid.uuid4().hex,
                "publication_id": payload["publication_id"],
                "payload_sha256": _digest(payload),
                "report_sha256": _digest(report),
                "decision_event_id": ledger["decision_event_id"],
                "decision_payload_sha256": ledger["decision_payload_sha256"],
                "signal_id": signal_id,
                "pair": pair,
                "side": actual_side,
            }
            authority_deadline = self._authorize(request)
            current_payload, current_report = self._publication()
            if (
                _digest(current_payload) != request["payload_sha256"]
                or _digest(current_report) != request["report_sha256"]
            ):
                raise TreatmentAdmissionDenied("publication_changed_after_authorization")
            if _utc(signal.get("valid_until")) <= datetime.now(UTC):
                raise TreatmentAdmissionDenied("signal_expired_after_authorization")
            if time.monotonic() > authority_deadline:
                raise TreatmentAdmissionDenied("authority_deadline_exceeded_after_revalidation")
            self._last_treatment_gate_reason = "current_publication_authorized"
            return {
                "accepted": True,
                "reason": self._last_treatment_gate_reason,
                "side": actual_side,
                "confidence": self._safe_float(signal.get("confidence", 0.0)),
                "decision_event_id": ledger["decision_event_id"],
                "signal_id": signal_id,
                "correlation_id": signal.get("correlation_id"),
            }
        except Exception as exc:
            # Freqtrade's safe wrapper defaults confirm_trade_entry exceptions to True.
            # Keep every failure inside this boundary and expose a sanitized in-memory reason.
            self._last_treatment_gate_reason = (
                str(exc)
                if isinstance(exc, TreatmentAdmissionDenied)
                else f"consumer_boundary_failed:{type(exc).__name__}"
            )
            return {"accepted": False, "reason": self._last_treatment_gate_reason}

    def _find_signal_for_pair(self, pair: str) -> dict[str, Any]:
        return self._decision(pair)

    def _is_signal_active(self, signal: dict[str, Any]) -> bool:
        try:
            return signal.get("risk_approved") is True and _utc(
                signal.get("valid_until")
            ) > datetime.now(UTC)
        except Exception as exc:
            self._last_treatment_gate_reason = f"signal_freshness_failed:{type(exc).__name__}"
            return False

    def _read_first_active_signal_file(self) -> tuple[dict[str, Any] | None, Path, str]:
        try:
            payload, _ = self._publication()
            signals = payload["signals"]
            if not signals:
                return None, self._treatment_signal_path, "no_treatment_signals"
            for row in signals:
                if (
                    not isinstance(row, dict)
                    or not self._is_signal_active(row)
                    or not isinstance(row.get("pair"), str)
                    or self._decision(row["pair"])["accepted"] is not True
                ):
                    return None, self._treatment_signal_path, "treatment_signal_not_authorized"
            return payload, self._treatment_signal_path, "current_publication_authorized"
        except Exception as exc:
            self._last_treatment_gate_reason = f"signal_lookup_failed:{type(exc).__name__}"
            return None, self._treatment_signal_path, self._last_treatment_gate_reason

    def confirm_trade_entry(
        self,
        pair: str,
        order_type: str,
        amount: float,
        rate: float,
        time_in_force: str,
        current_time: datetime,
        entry_tag: str | None,
        side: str,
        **kwargs: Any,
    ) -> bool:
        try:
            decision = self._decision(pair, side, entry_tag)
            return isinstance(decision, dict) and decision.get("accepted") is True
        except Exception as exc:
            self._last_treatment_gate_reason = f"entry_confirmation_failed:{type(exc).__name__}"
            return False

    def check_entry_timeout(
        self,
        pair: str,
        trade: Any,
        order: Any,
        current_time: datetime,
        **kwargs: Any,
    ) -> bool:
        try:
            if not isinstance(trade.is_short, bool):
                raise TreatmentAdmissionDenied("pending_entry_side_unknown")
            entry_side, exit_side = ("sell", "buy") if trade.is_short else ("buy", "sell")
            if trade.entry_side != entry_side or trade.exit_side != exit_side:
                raise TreatmentAdmissionDenied("pending_entry_identity_invalid")
            if order.ft_order_side == exit_side:
                return False
            if order.ft_order_side != entry_side:
                raise TreatmentAdmissionDenied("pending_order_side_unknown")
            tag = order.ft_order_tag or trade.enter_tag
            if order.ft_order_tag and trade.enter_tag != order.ft_order_tag:
                raise TreatmentAdmissionDenied("pending_entry_tag_mismatch")
            return (
                self._decision(pair, "short" if trade.is_short else "long", tag)["accepted"]
                is not True
            )
        except Exception as exc:
            self._last_treatment_gate_reason = f"pending_entry_boundary_failed:{type(exc).__name__}"
            return True

    def adjust_entry_price(
        self,
        trade: Any,
        order: Any,
        pair: str,
        current_time: datetime,
        proposed_rate: float,
        current_order_rate: float,
        entry_tag: str | None,
        side: str,
        **kwargs: Any,
    ) -> float | None:
        try:
            if order is None or not math.isfinite(current_order_rate) or current_order_rate <= 0:
                return None
            if self._decision(pair, side, entry_tag)["accepted"] is not True:
                return None
            # Maintain the original order, never request a replacement that bypasses confirmation.
            return current_order_rate
        except Exception as exc:
            self._last_treatment_gate_reason = (
                f"entry_adjustment_boundary_failed:{type(exc).__name__}"
            )
            return None
