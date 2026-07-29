from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

import pytest

from scripts import freqtrade_paper_healthcheck as healthcheck
from smartcrypto.qlib_engine import paper_refresh_supervisor as supervisor
from smartcrypto.runtime import shared_freqtrade_signal_artifact as shared


def _signal_path(tmp_path: Path) -> Path:
    runtime = tmp_path / "data" / "runtime"
    runtime.mkdir(parents=True)
    path = runtime / shared.SHARED_SIGNAL_FILENAME
    path.write_text(
        json.dumps(
            {
                "generated_at": "2026-07-29T11:00:00Z",
                "signals": [
                    {
                        "pair": "BTC/USDT:USDT",
                        "side": "short",
                        "risk_approved": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def _market_ok(**_kwargs: Any) -> dict[str, Any]:
    return {
        "status": "ok",
        "operational_feature_schema_ok": True,
    }


def _prediction_ok(**_kwargs: Any) -> dict[str, Any]:
    return {
        "status": "ok",
        "input_data_status": "input_data_fresh",
    }


def _phase13_ok(**_kwargs: Any) -> dict[str, Any]:
    return {
        "status": "ok",
        "written_pinned": True,
        "signals_after": 1,
    }


def _freshness_ok(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    return {
        "freshness_status": "fresh",
        "stale": False,
        "input_data_status": "input_data_fresh",
    }


def _signal_inspect_ok(_path: str | os.PathLike[str]) -> dict[str, Any]:
    return {
        "exists": True,
        "signal_count": 1,
        "active_signal_count": 1,
    }


def test_contract_rejects_path_outside_data_runtime(tmp_path: Path) -> None:
    path = tmp_path / shared.SHARED_SIGNAL_FILENAME
    path.write_text('{"signals": []}', encoding="utf-8")

    with pytest.raises(
        shared.SharedFreqtradeSignalArtifactError,
        match="shared_signal_path_outside_data_runtime",
    ):
        shared.publish_shared_freqtrade_signal_artifact(path)


def test_missing_optional_artifact_is_nonblocking(tmp_path: Path) -> None:
    path = (
        tmp_path
        / "data"
        / "runtime"
        / shared.SHARED_SIGNAL_FILENAME
    )

    report = shared.publish_shared_freqtrade_signal_artifact(
        path,
        required=False,
    )

    assert report["status"] == "not_present"
    assert report["consumer_readable"] is False
    assert report["permission_changed"] is False
    assert report["paper_only"] is True
    assert report["live_trading_enabled"] is False
    assert report["sends_orders"] is False


def test_missing_required_artifact_is_fail_closed(tmp_path: Path) -> None:
    path = (
        tmp_path
        / "data"
        / "runtime"
        / shared.SHARED_SIGNAL_FILENAME
    )

    with pytest.raises(
        shared.SharedFreqtradeSignalArtifactError,
        match="shared_signal_required_file_missing",
    ):
        shared.publish_shared_freqtrade_signal_artifact(
            path,
            required=True,
        )


@pytest.mark.skipif(
    os.name == "nt",
    reason="POSIX permission verification runs in the Linux runtime/CI.",
)
def test_contract_publishes_exact_posix_modes(tmp_path: Path) -> None:
    path = _signal_path(tmp_path)
    os.chmod(path.parent, 0o700)
    os.chmod(path, 0o600)

    report = shared.publish_shared_freqtrade_signal_artifact(
        path,
        required=True,
    )

    assert report["status"] == "ok"
    assert report["reason"] == (
        "shared_signal_permission_contract_established"
    )
    assert report["directory_mode"] == "0o755"
    assert report["file_mode"] == "0o644"
    assert report["signal_count"] == 1
    assert report["consumer_readable"] is True
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o755
    assert stat.S_IMODE(path.stat().st_mode) == 0o644


def test_supervisor_invokes_permission_contract_after_phase13(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, bool]] = []

    def permission_contract(
        path: str | os.PathLike[str],
        *,
        required: bool,
    ) -> dict[str, Any]:
        calls.append((str(path), required))
        return {
            "status": "ok",
            "reason": "shared_signal_permission_contract_established",
            "path": str(path),
            "consumer_readable": True,
        }

    cfg = supervisor.PaperRefreshSupervisorConfig(
        report_path=tmp_path / "report.json",
        pinned_signals_path=(
            tmp_path
            / "data"
            / "runtime"
            / shared.SHARED_SIGNAL_FILENAME
        ),
    )

    report = supervisor.run_paper_refresh_supervisor(
        cfg,
        market_refresh_fn=_market_ok,
        prediction_refresh_fn=_prediction_ok,
        phase13_fn=_phase13_ok,
        freshness_fn=_freshness_ok,
        signal_inspect_fn=_signal_inspect_ok,
        signal_permission_fn=permission_contract,
        write_report=False,
    )

    assert report["status"] == "ok"
    assert calls == [(str(cfg.pinned_signals_path), True)]
    assert report["signal_permission_contract"][
        "consumer_readable"
    ] is True


def test_supervisor_blocks_when_permission_contract_fails(
    tmp_path: Path,
) -> None:
    def permission_contract(
        _path: str | os.PathLike[str],
        *,
        required: bool,
    ) -> dict[str, Any]:
        assert required is True
        raise shared.SharedFreqtradeSignalArtifactError(
            "shared_signal_chmod_failed"
        )

    cfg = supervisor.PaperRefreshSupervisorConfig(
        report_path=tmp_path / "report.json",
        pinned_signals_path=(
            tmp_path
            / "data"
            / "runtime"
            / shared.SHARED_SIGNAL_FILENAME
        ),
    )

    report = supervisor.run_paper_refresh_supervisor(
        cfg,
        market_refresh_fn=_market_ok,
        prediction_refresh_fn=_prediction_ok,
        phase13_fn=_phase13_ok,
        freshness_fn=_freshness_ok,
        signal_inspect_fn=_signal_inspect_ok,
        signal_permission_fn=permission_contract,
        write_report=False,
    )

    assert report["status"] == "phase13_failed"
    assert report["reason"] == (
        "shared_signal_permission_contract_failed:"
        "shared_signal_chmod_failed"
    )
    assert report["signal_permission_contract"]["status"] == "blocked"
    assert report["signal_permission_contract"][
        "consumer_readable"
    ] is False
    assert report["signal_permission_contract"]["sends_orders"] is False


def test_healthcheck_blocks_permission_denied_signal_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _signal_path(tmp_path)
    original_stat = Path.stat

    def denied_stat(
        self: Path,
        *args: Any,
        **kwargs: Any,
    ) -> os.stat_result:
        if self == path:
            raise PermissionError("synthetic-eacces")
        return original_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", denied_stat)

    findings, exists, readable, signal_count = (
        healthcheck._signal_file_findings(path)
    )

    assert findings == ["signal_file_permission_denied"]
    assert exists is False
    assert readable is False
    assert signal_count is None


def test_healthcheck_accepts_readable_signal_payload(
    tmp_path: Path,
) -> None:
    path = _signal_path(tmp_path)

    findings, exists, readable, signal_count = (
        healthcheck._signal_file_findings(path)
    )

    assert findings == []
    assert exists is True
    assert readable is True
    assert signal_count == 1


def test_contract_never_enables_live_or_order_submission() -> None:
    assert shared.SAFE_FLAGS == {
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
        "changes_model": False,
    }
