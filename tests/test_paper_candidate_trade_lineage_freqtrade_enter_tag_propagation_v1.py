from __future__ import annotations

import ast
import re
from pathlib import Path


STRATEGY_PATH = Path(
    "freqtrade/user_data/strategies/SmartCryptoSignalStrategy.py"
)
TRADE_LINK_PATH = Path(
    "smartcrypto/execution/"
    "decision_ledger_paper_observability_wiring_v1/"
    "trade_link.py"
)
STRICT_DECISION_PATH = Path(
    "smartcrypto/execution/"
    "paper_candidate_trade_lineage_propagation_v1/"
    "decision_projection.py"
)


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _tree(path: Path) -> ast.Module:
    return ast.parse(_source(path), filename=str(path))


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"function_not_found:{name}")


def _is_mapping_get(
    node: ast.AST,
    mapping_name: str,
    key_name: str,
) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == mapping_name
        and node.func.attr == "get"
        and len(node.args) >= 1
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == key_name
    )


def _has_dict_mapping_get(
    node: ast.AST,
    *,
    dict_key: str,
    mapping_name: str,
    mapping_key: str,
) -> bool:
    for child in ast.walk(node):
        if not isinstance(child, ast.Dict):
            continue
        for key, value in zip(child.keys, child.values, strict=True):
            if (
                isinstance(key, ast.Constant)
                and key.value == dict_key
                and _is_mapping_get(value, mapping_name, mapping_key)
            ):
                return True
    return False


def _subscript_literals(node: ast.Subscript) -> set[str]:
    return {
        child.value
        for child in ast.walk(node.slice)
        if isinstance(child, ast.Constant)
        and isinstance(child.value, str)
    }


def _has_assignment_from_mapping_get(
    function: ast.FunctionDef,
    *,
    target_literal: str,
    mapping_name: str,
    mapping_key: str,
) -> bool:
    for node in ast.walk(function):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = (
            node.targets
            if isinstance(node, ast.Assign)
            else [node.target]
        )
        value = node.value
        if value is None or not _is_mapping_get(
            value,
            mapping_name,
            mapping_key,
        ):
            continue
        for target in targets:
            if (
                isinstance(target, ast.Subscript)
                and target_literal in _subscript_literals(target)
            ):
                return True
    return False


def _compiled_event_tag_pattern() -> str:
    tree = _tree(TRADE_LINK_PATH)
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name)
            and target.id == "_EVENT_TAG"
            for target in node.targets
        ):
            continue
        value = node.value
        assert isinstance(value, ast.Call)
        assert isinstance(value.func, ast.Attribute)
        assert isinstance(value.func.value, ast.Name)
        assert value.func.value.id == "re"
        assert value.func.attr == "compile"
        assert value.args
        pattern = value.args[0]
        assert isinstance(pattern, ast.Constant)
        assert isinstance(pattern.value, str)
        return pattern.value
    raise AssertionError("_EVENT_TAG_not_found")


def test_strategy_reads_ids_from_decision_ledger_envelope() -> None:
    tree = _tree(STRATEGY_PATH)
    function = _function(tree, "_find_signal_for_pair")

    for field in (
        "decision_event_id",
        "signal_id",
        "correlation_id",
    ):
        assert _has_dict_mapping_get(
            function,
            dict_key=field,
            mapping_name="decision_ledger",
            mapping_key=field,
        ), f"nested_decision_ledger_mapping_missing:{field}"


def test_strategy_populate_indicators_carries_lineage_columns() -> None:
    tree = _tree(STRATEGY_PATH)
    function = _function(tree, "populate_indicators")

    for column, key in (
        ("smartcrypto_decision_event_id", "decision_event_id"),
        ("smartcrypto_signal_id", "signal_id"),
        ("smartcrypto_correlation_id", "correlation_id"),
    ):
        assert _has_assignment_from_mapping_get(
            function,
            target_literal=column,
            mapping_name="payload",
            mapping_key=key,
        ), f"indicator_lineage_assignment_missing:{column}"


def test_strategy_entry_path_passes_exact_decision_id_to_entry_tag() -> None:
    tree = _tree(STRATEGY_PATH)
    function = _function(tree, "populate_entry_trend")

    calls: list[ast.Call] = []
    for node in ast.walk(function):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute):
            continue
        if not isinstance(node.func.value, ast.Name):
            continue
        if node.func.value.id != "self" or node.func.attr != "_entry_tag":
            continue
        calls.append(node)

    assert len(calls) == 2
    sides = set()
    for call in calls:
        assert len(call.args) == 2
        assert isinstance(call.args[0], ast.Constant)
        assert call.args[0].value in {"long", "short"}
        sides.add(call.args[0].value)
        assert isinstance(call.args[1], ast.Name)
        assert call.args[1].id == "decision_event_id"

    assert sides == {"long", "short"}


def test_entry_tag_contract_is_explicit_and_has_no_identity_fallback() -> None:
    source = _source(STRATEGY_PATH)
    tree = ast.parse(source, filename=str(STRATEGY_PATH))
    function = _function(tree, "_entry_tag")
    segment = ast.get_source_segment(source, function)
    assert segment is not None

    assert "smartcrypto_" in segment
    assert "decision_event_id=" in segment
    assert "decision_event_id" in segment
    assert "datetime" not in segment
    assert "timestamp" not in segment
    assert "uuid" not in segment.lower()
    assert "random" not in segment.lower()


def test_existing_trade_link_parser_accepts_strategy_enter_tag_exactly() -> None:
    pattern = re.compile(_compiled_event_tag_pattern())
    decision_event_id = "decision-event:abc123"
    tag = f"smartcrypto_long|decision_event_id={decision_event_id}"

    match = pattern.search(tag)

    assert match is not None
    assert match.group(1) == decision_event_id


def test_trade_link_parser_requires_explicit_decision_event_token() -> None:
    pattern = re.compile(_compiled_event_tag_pattern())

    assert pattern.search("smartcrypto_long") is None
    assert pattern.search("smartcrypto_short|signal_id=signal:abc") is None
    assert pattern.search("smartcrypto_long|candidate_id=candidate-1") is None


def test_trade_link_adapter_has_no_timestamp_nearest_matching() -> None:
    source = _source(TRADE_LINK_PATH).lower()

    forbidden = (
        "timestamp_nearest",
        "nearest_timestamp",
        "nearest_time",
        "time_distance",
        "timedelta",
    )
    for token in forbidden:
        assert token not in source

    assert "decision_event_id" in source
    assert "enter_tag" in source


def test_strict_decision_projection_emits_in_memory_decision_event() -> None:
    source = _source(STRICT_DECISION_PATH)
    tree = ast.parse(source, filename=str(STRICT_DECISION_PATH))

    decision_event_assignment = False
    writer_false = False
    runtime_false = False

    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        pairs = list(zip(node.keys, node.values, strict=True))
        for key, value in pairs:
            if not isinstance(key, ast.Constant):
                continue
            if key.value == "decision_event_id":
                if (
                    isinstance(value, ast.Attribute)
                    and isinstance(value.value, ast.Name)
                    and value.value.id == "target"
                    and value.attr == "event_id"
                ):
                    decision_event_assignment = True
            elif key.value == "writer_invoked":
                if isinstance(value, ast.Constant) and value.value is False:
                    writer_false = True
            elif key.value == "writes_runtime":
                if isinstance(value, ast.Constant) and value.value is False:
                    runtime_false = True

    assert decision_event_assignment is True
    assert writer_false is True
    assert runtime_false is True
