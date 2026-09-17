from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from smartcrypto.research.trades_master_official.contracts import (
    LEGACY_OPENING_SENTINEL,
    OFFICIAL_MASTER_SHA256,
)
from smartcrypto.research.trades_master_official.loader import (
    OfficialMasterData,
    SourceAudit,
)
from smartcrypto.research.trades_master_official.segment_persistence import (
    FAMILIES_CSV,
    MANIFEST_CSV,
    REPORT_JSON,
    REPORT_MD,
    SEGMENTS_CSV,
    SEGMENT_FAMILIES,
    WQ3_METHOD_HASH,
    build_segment_persistence_report,
    frozen_method,
    persist_segment_persistence_reports,
)
from smartcrypto.research.trades_master_official.walkforward import (
    build_walkforward_report,
)


PERIOD_COUNTS = {
    "2026-01": 246,
    "2026-02": 448,
    "2026-03": 566,
    "2026-04": 599,
    "2026-05": 616,
    "2026-06": 331,
    "2026-07": 404,
    "2026-08": 550,
}


def _master() -> OfficialMasterData:
    rows: list[
        dict[str, object]
    ] = []

    sequence = 1

    for (
        period,
        count,
    ) in PERIOD_COUNTS.items():
        start = pd.Timestamp(
            f"{period}-01T00:00:00Z"
        )

        for offset in range(
            count
        ):
            opened = (
                start
                + pd.Timedelta(
                    minutes=30
                    * offset
                )
            )

            pnl = (
                2.0
                if sequence % 3
                else -1.0
            )

            symbol = (
                "BTC_USDT"
                if sequence % 2
                else "ETH_USDT"
            )

            side = (
                "LONG"
                if (
                    sequence // 2
                )
                % 2
                else "SHORT"
            )

            duration_minutes = (
                10
                if sequence % 4
                else 20
            )

            rows.append(
                {
                    "trade_sequence": sequence,
                    "order_id_raw": (
                        f"{sequence:024x}"
                    ),
                    "symbol": symbol,
                    "side": side,
                    "horario_abertura": (
                        opened.strftime(
                            "%Y-%m-%d %H:%M:%S"
                        )
                    ),
                    "horario_fechamento": (
                        (
                            opened
                            + pd.Timedelta(
                                minutes=(
                                    duration_minutes
                                )
                            )
                        ).strftime(
                            "%Y-%m-%d %H:%M:%S"
                        )
                    ),
                    "fee_semantics_regime": (
                        "STANDARD"
                    ),
                    "economic_net_pnl": pnl,
                }
            )

            sequence += 1

    for _ in range(
        231
    ):
        rows.append(
            {
                "trade_sequence": sequence,
                "order_id_raw": (
                    f"{sequence:024x}"
                ),
                "symbol": "BTC_USDT",
                "side": "LONG",
                "horario_abertura": (
                    LEGACY_OPENING_SENTINEL
                ),
                "horario_fechamento": (
                    "2026-01-01 00:00:00"
                ),
                "fee_semantics_regime": (
                    "LEGACY"
                ),
                "economic_net_pnl": 1.0,
            }
        )

        sequence += 1

    frame = pd.DataFrame(
        rows
    )

    audit = SourceAudit(
        status="ok",
        master_path="fixture.xlsx",
        master_sha256=OFFICIAL_MASTER_SHA256,
        source_contract_sha256="b" * 64,
        rows=3991,
        columns=41,
        sheets=(
            "TRADES",
            "METADATA",
            "AUDIT",
        ),
        trades_dimension="A1:AO3992",
        formula_count=0,
        column_names=tuple(),
        economic_identity_max_abs_error=0.0,
        fee_regime_counts={
            "LEGACY": 231,
            "STANDARD": 3760,
        },
        invalid_order_id_count=0,
        trade_sequence_contiguous=True,
        hash_unchanged_after_read=True,
        read_only=True,
        writes_master=False,
        sends_orders=False,
        changes_risk=False,
        operational_authority=False,
    )

    return OfficialMasterData(
        path=Path(
            "fixture.xlsx"
        ),
        frame=frame,
        audit=audit,
    )


@pytest.fixture(scope="module")
def report() -> dict:
    master = _master()

    parent = (
        build_walkforward_report(
            master
        )
    )

    parent[
        "status"
    ] = "ok"

    return (
        build_segment_persistence_report(
            master,
            parent_wq2_report=parent,
        )
    )


def test_frozen_method_hash_is_exact() -> None:
    method = frozen_method()

    assert (
        method[
            "method_hash"
        ]
        == WQ3_METHOD_HASH
    )

    assert (
        method[
            "parent_wq2_method_hash"
        ]
        == (
            "604ee399fcb0e57657150eb94207513ae"
            "607d3bd3e7d6755cb7664559b3b1220"
        )
    )

    assert (
        method[
            "uses_segment_pnl_to_define_method"
        ]
        is False
    )

    assert (
        method[
            "hypothesis_policy"
        ][
            "automatic_filter_activation"
        ]
        is False
    )


def test_report_uses_exact_oos_population(
    report: dict,
) -> None:
    assert report[
        "wq3_status"
    ] == "PASS"

    assert report[
        "oos_rows"
    ] == 3066

    assert report[
        "segment_family_count"
    ] == len(
        SEGMENT_FAMILIES
    )

    assert report[
        "operational_authority"
    ] is False

    assert report[
        "sends_orders"
    ] is False


def test_high_confidence_gate_blocks_small_hour_crosses(
    report: dict,
) -> None:
    family = next(
        row
        for row in report[
            "family_summaries"
        ]
        if row[
            "family_key"
        ]
        == (
            "symbol+side+"
            "open_hour_utc"
        )
    )

    assert family[
        "high_confidence_n_ge_100"
    ] == 0

    candidates = [
        row
        for row in report[
            "segments"
        ]
        if row[
            "family_key"
        ]
        == (
            "symbol+side+"
            "open_hour_utc"
        )
        and row[
            "classification"
        ]
        == "PERSISTENT_CANDIDATE"
    ]

    assert candidates == []


def test_duration_segments_are_diagnostic_only(
    report: dict,
) -> None:
    duration_rows = [
        row
        for row in report[
            "segments"
        ]
        if "duration_bucket"
        in row[
            "family"
        ]
    ]

    assert duration_rows

    assert all(
        row[
            "duration_bucket_is_outcome_diagnostic_only"
        ]
        is True
        for row in duration_rows
    )

    assert all(
        row[
            "operational_feature_allowed"
        ]
        is False
        for row in duration_rows
    )


def test_persistent_segments_remain_research_only(
    report: dict,
) -> None:
    persistent = [
        row
        for row in report[
            "segments"
        ]
        if row[
            "classification"
        ]
        == "PERSISTENT_CANDIDATE"
    ]

    assert persistent

    assert all(
        row[
            "automatic_activation_allowed"
        ]
        is False
        for row in persistent
    )

    assert report[
        "candidate_policy"
    ][
        "promotion_allowed"
    ] is False


def test_report_persistence_is_deterministic(
    report: dict,
    tmp_path: Path,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"

    persist_segment_persistence_reports(
        report,
        output_dir=first,
    )

    persist_segment_persistence_reports(
        report,
        output_dir=second,
    )

    expected = {
        REPORT_JSON,
        REPORT_MD,
        SEGMENTS_CSV,
        FAMILIES_CSV,
        MANIFEST_CSV,
    }

    assert {
        path.name
        for path in first.iterdir()
    } == expected

    assert {
        path.name
        for path in second.iterdir()
    } == expected

    for filename in expected:
        assert (
            (first / filename).read_bytes()
            == (second / filename).read_bytes()
        )
