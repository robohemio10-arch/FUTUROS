from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

HIGH_PRIORITY_SCRIPTS = (
    "scripts/build_freqtrade_paper_ai_selector_integration.py",
    "scripts/collect_freqtrade_paper_history.py",
    "scripts/export_freqtrade_signals.py",
    "scripts/export_market_freqtrade_signals.py",
    "scripts/export_qlib_freqtrade_signals.py",
    "scripts/inspect_phase11_freqtrade_db.py",
)

DASHBOARD_COMMAND_STUB_ADAPTER = (
    "smartcrypto/dashboard/controls/command_stub_adapter.py"
)


def _read_project_file(relative_path: str) -> str:
    return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")


def test_high_priority_scripts_have_explicit_project_root_bootstrap() -> None:
    for relative_path in HIGH_PRIORITY_SCRIPTS:
        text = _read_project_file(relative_path)
        assert "Path(__file__).resolve().parents[1]" in text, relative_path
        assert "sys.path.insert(0, str(PROJECT_ROOT))" in text, relative_path


def test_bootstrap_appears_before_first_smartcrypto_import() -> None:
    for relative_path in HIGH_PRIORITY_SCRIPTS:
        text = _read_project_file(relative_path)
        bootstrap_index = text.index("sys.path.insert(0, str(PROJECT_ROOT))")
        smartcrypto_match = re.search(
            r"^\s*(?:from\s+smartcrypto(?:\.|\s+import)|import\s+smartcrypto(?:\.|\s|$))",
            text,
            flags=re.MULTILINE,
        )
        assert smartcrypto_match is not None, relative_path
        assert bootstrap_index < smartcrypto_match.start(), relative_path


def test_high_priority_scripts_import_without_pythonpath_in_subprocess() -> None:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    for relative_path in HIGH_PRIORITY_SCRIPTS:
        script_path = PROJECT_ROOT / relative_path
        command = [
            sys.executable,
            "-c",
            (
                "import runpy; "
                f"runpy.run_path({str(script_path)!r}, run_name='__not_main__')"
            ),
        ]
        result = subprocess.run(
            command,
            cwd=Path(os.environ.get("TMP", os.environ.get("TEMP", "."))),
            env=env,
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
        )
        assert result.returncode == 0, (
            relative_path,
            result.stdout,
            result.stderr,
        )


def test_dashboard_command_stub_adapter_has_no_dry_run_false_token() -> None:
    text = _read_project_file(DASHBOARD_COMMAND_STUB_ADAPTER)
    assert "dry_run=False" not in text
    assert "dry_run = False" not in text


def test_dashboard_hard_blocked_path_remains_dry_run_and_non_executing() -> None:
    text = _read_project_file(DASHBOARD_COMMAND_STUB_ADAPTER)
    hard_blocked_index = text.index("if policy.hard_blocked:")
    hard_blocked_block = text[hard_blocked_index : text.index("valid, reason", hard_blocked_index)]
    assert "executed=False" in hard_blocked_block
    assert "dry_run=True" in hard_blocked_block
    assert "hard_blocked=True" in hard_blocked_block
    assert "blocked_by_policy_no_external_side_effect" in hard_blocked_block


def test_dashboard_adapter_contains_no_external_notification_client_tokens() -> None:
    text = _read_project_file(DASHBOARD_COMMAND_STUB_ADAPTER)
    forbidden_tokens = (
        "requests.post",
        "httpx.post",
        "aiohttp.ClientSession",
        "urllib.request.urlopen",
        "TELEGRAM",
        "telegram_token",
        "NTFY",
        "ntfy_url",
        "send_real",
        "real_notification",
    )
    for token in forbidden_tokens:
        assert token not in text
