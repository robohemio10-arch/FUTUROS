from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "smartcrypto" / "research" / "research_council"
CLI = ROOT / "scripts" / "run_research_council_shadow_v1.py"
CONFIG = ROOT / "config" / "research" / "research_council.yaml"

DENIED_CALLS = {
    "create_order",
    "cancel_order",
    "submit_order",
    "send_order",
    "promote_model",
    "write_active_registry",
    "write_active_signals",
}
DENIED_IMPORT_PREFIXES = (
    "ccxt",
    "freqtrade",
    "requests",
    "httpx",
    "urllib",
    "smartcrypto.risk",
    "smartcrypto.execution.signal_producer",
)


def _w2_python_files() -> list[Path]:
    return sorted([*PACKAGE.glob("*.py"), CLI])


def _call_leaf(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return ""


def test_static_safety_has_no_order_network_or_mutation_calls() -> None:
    findings: list[str] = []
    for path in _w2_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _call_leaf(node) in DENIED_CALLS:
                findings.append(f"{path.name}:{node.lineno}:call:{_call_leaf(node)}")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(DENIED_IMPORT_PREFIXES):
                        findings.append(f"{path.name}:{node.lineno}:import:{alias.name}")
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.startswith(DENIED_IMPORT_PREFIXES):
                    findings.append(f"{path.name}:{node.lineno}:import:{node.module}")
    assert findings == []


def test_package_does_not_import_external_provider_sdks() -> None:
    provider_names = ("openai", "anthropic", "google.generativeai", "ollama", "openrouter")
    source = "\n".join(path.read_text(encoding="utf-8-sig") for path in _w2_python_files())
    tree = ast.parse(source)
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imports.update(
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    )
    assert not any(name.startswith(provider_names) for name in imports)


def test_config_is_research_only_and_contains_no_secret_keys() -> None:
    payload = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    assert payload["mode"] == "research"
    assert payload["paper_only"] is True
    assert payload["shadow_only"] is True
    assert payload["research_only"] is True
    for key in (
        "operational_authority",
        "sends_orders",
        "exchange_private_access",
        "changes_risk",
        "changes_model",
        "live_release_allowed",
        "canary_release_allowed",
        "writes_active_signals",
    ):
        assert payload[key] is False
    lowered = {str(key).casefold() for key in payload}
    assert not any("token" in key or "secret" in key or "api_key" in key for key in lowered)


def test_cli_no_write_executes_offline_without_creating_data(tmp_path: Path, capsys) -> None:
    project = tmp_path / "project"
    (project / "config" / "research").mkdir(parents=True)
    (project / "config" / "research" / "research_council.yaml").write_text(
        CONFIG.read_text(encoding="utf-8"), encoding="utf-8"
    )
    fixture = project / "fixture.json"
    fixture.write_text(
        json.dumps(
            {
                "request_id": "cli-fixture",
                "symbol": "BTCUSDT",
                "decision_time_utc": "2026-08-28T12:00:00+00:00",
                "evidence": [
                    {
                        "event_id": "market-event",
                        "context_type": "market",
                        "symbol": "BTCUSDT",
                        "event_time_utc": "2026-08-28T11:55:00+00:00",
                        "available_at_utc": "2026-08-28T11:59:00+00:00",
                        "source_id": "offline-fixture",
                        "source_hash": "b" * 64,
                        "payload": {
                            "trend_strength": 0.4,
                            "momentum_score": 0.3,
                            "volatility_state": "normal",
                            "support_pressure": 0.6,
                            "resistance_pressure": 0.4,
                            "uncertainty": 0.2,
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    spec = importlib.util.spec_from_file_location("research_council_cli", CLI)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    code = module.main(
        [
            "--project-root",
            str(project),
            "--input-json",
            str(fixture),
            "--no-write",
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["status"] == "PARTIAL"
    assert payload["write_performed"] is False
    assert payload["network_calls_executed"] is False
    assert not (project / "data").exists()


def test_w2_paths_do_not_reference_active_runtime_artifacts() -> None:
    denied_fragments = (
        "active_freqtrade_signals.json",
        "data/runtime",
        "data/registries/active",
        "tradesv3.sqlite",
    )
    for path in _w2_python_files():
        source = path.read_text(encoding="utf-8-sig").replace("\\", "/").casefold()
        assert not any(fragment in source for fragment in denied_fragments)


def test_all_snapshot_safety_flags_are_fail_closed() -> None:
    from smartcrypto.research.research_council import ResearchCouncilConfig

    config = ResearchCouncilConfig()
    assert config.paper_only is True
    assert config.shadow_only is True
    assert config.research_only is True
    assert config.operational_authority is False
    assert config.sends_orders is False
    assert config.exchange_private_access is False
    assert config.changes_risk is False
    assert config.changes_model is False
    assert config.writes_active_signals is False

