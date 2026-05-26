from __future__ import annotations

import numpy as np
import pandas as pd

from smartcrypto.ml.drift_monitor import BLOCKED, OK, WARNING, DriftMonitor


def test_drift_monitor_returns_ok_for_close_distributions() -> None:
    frame = pd.DataFrame({"ret_1": np.linspace(0, 1, 100)})
    monitor = DriftMonitor(warning_threshold=0.10, blocked_threshold=0.25)

    report = monitor.compare(frame, frame.copy(), features=["ret_1"])

    assert report.status == OK
    assert report.to_dict()["safety"]["risk_increase"] is False
    assert report.to_dict()["safety"]["order_submission"] is False


def test_drift_monitor_returns_warning_or_blocked_for_high_psi() -> None:
    baseline = pd.DataFrame({"ret_1": np.linspace(0, 1, 100)})
    current = pd.DataFrame({"ret_1": np.linspace(3, 4, 100)})
    monitor = DriftMonitor(warning_threshold=0.01, blocked_threshold=0.10)

    report = monitor.compare(baseline, current, features=["ret_1"])

    assert report.status in {WARNING, BLOCKED}
    assert report.feature_results[0].psi > 0.01


def test_drift_monitor_blocked_is_ai_only_not_bot_execution() -> None:
    baseline = pd.DataFrame({"ret_1": np.linspace(0, 1, 100)})
    current = pd.DataFrame({"ret_1": np.linspace(10, 11, 100)})
    report = DriftMonitor(warning_threshold=0.01, blocked_threshold=0.02).compare(
        baseline,
        current,
        features=["ret_1"],
    )

    payload = report.to_dict()
    assert report.status == BLOCKED
    assert payload["safety"]["bot_block"] is False
    assert payload["safety"]["risk_increase"] is False
