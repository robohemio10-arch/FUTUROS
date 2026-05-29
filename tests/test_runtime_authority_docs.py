from pathlib import Path


DOCS = [
    Path("docs/FUTUROS_CONSOLIDATED_STATE_2026-05-28.md"),
    Path("docs/RUNTIME_AUTHORITY_MATRIX.md"),
    Path("docs/NO_DUPLICATE_EXECUTION_POLICY.md"),
]

CRITICAL_TERMS = [
    r"E:\FUTUROS",
    "Freqtrade",
    "Qlib",
    "IA Shadow",
    "Fase 14",
    "Fase 5",
    "active_freqtrade_signals.json",
    "trades_master",
    "2864",
    "2631",
    "paper/shadow",
    "live trading bloqueado",
]


def test_runtime_authority_docs_exist() -> None:
    for path in DOCS:
        assert path.exists(), f"missing required runtime authority doc: {path}"


def test_runtime_authority_docs_contain_critical_terms() -> None:
    combined = "\n".join(path.read_text(encoding="utf-8") for path in DOCS)
    for term in CRITICAL_TERMS:
        assert term in combined, f"missing critical term in runtime authority docs: {term}"
