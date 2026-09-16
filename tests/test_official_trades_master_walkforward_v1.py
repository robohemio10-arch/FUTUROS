from __future__ import annotations

from pathlib import Path

import pandas as pd

from smartcrypto.research.trades_master_official.contracts import (
    LEGACY_OPENING_SENTINEL,
    OFFICIAL_MASTER_SHA256,
)
from smartcrypto.research.trades_master_official.loader import (
    OfficialMasterData,
    SourceAudit,
)
from smartcrypto.research.trades_master_official.walkforward import (
    EXPANDING_TEST_PERIODS,
    FOLDS_CSV,
    LOPO_CSV,
    MANIFEST_CSV,
    REPORT_JSON,
    REPORT_MD,
    WQ2_METHOD_HASH,
    WQ2_PERIODS,
    build_fold_plans,
    build_walkforward_report,
    frozen_method,
    persist_walkforward_reports,
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
                    minutes=30 * offset
                )
            )

            pnl = (
                2.0
                if sequence % 3
                else -1.0
            )

            rows.append(
                {
                    "trade_sequence": sequence,
                    "order_id_raw": (
                        f"{sequence:024x}"
                    ),
                    "symbol": (
                        "BTC_USDT"
                        if sequence % 2
                        else "ETH_USDT"
                    ),
                    "side": (
                        "LONG"
                        if sequence % 2
                        else "SHORT"
                    ),
                    "horario_abertura": (
                        opened.strftime(
                            "%Y-%m-%d %H:%M:%S"
                        )
                    ),
                    "horario_fechamento": (
                        (
                            opened
                            + pd.Timedelta(
                                minutes=10
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

    for _ in range(231):
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
                "economic_net_pnl": 0.5,
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


def test_frozen_method_hash_is_exact() -> None:
    method = frozen_method()

    assert (
        method[
            "method_hash"
        ]
        == WQ2_METHOD_HASH
    )

    assert tuple(
        method[
            "period_definition"
        ][
            "observed_periods"
        ]
    ) == WQ2_PERIODS

    assert tuple(
        method[
            "primary_walkforward"
        ][
            "test_periods"
        ]
    ) == EXPANDING_TEST_PERIODS

    assert (
        method[
            "uses_pnl_to_define_folds"
        ]
        is False
    )

    assert (
        method[
            "random_split_allowed"
        ]
        is False
    )


def test_builds_frozen_geometry() -> None:
    report = build_walkforward_report(
        _master()
    )

    assert report[
        "wq2_status"
    ] == "PASS"

    assert report[
        "expanding"
    ][
        "fold_count"
    ] == 6

    assert report[
        "rolling"
    ][
        "fold_count"
    ] == 5

    assert [
        row[
            "test_rows"
        ]
        for row in report[
            "expanding"
        ][
            "folds"
        ]
    ] == [
        566,
        599,
        616,
        331,
        404,
        550,
    ]


def test_fold_geometry_is_independent_of_pnl() -> None:
    first = _master()
    second = _master()

    second.frame[
        "economic_net_pnl"
    ] = -second.frame[
        "economic_net_pnl"
    ]

    first_report = (
        build_walkforward_report(
            first
        )
    )

    second_report = (
        build_walkforward_report(
            second
        )
    )

    first_geometry = [
        {
            key: value
            for key, value in row.items()
            if key != "metrics"
        }
        for row in first_report[
            "expanding"
        ][
            "folds"
        ]
    ]

    second_geometry = [
        {
            key: value
            for key, value in row.items()
            if key != "metrics"
        }
        for row in second_report[
            "expanding"
        ][
            "folds"
        ]
    ]

    assert (
        first_report[
            "method_hash"
        ]
        == second_report[
            "method_hash"
        ]
        == WQ2_METHOD_HASH
    )

    assert (
        first_geometry
        == second_geometry
    )


def test_purge_removes_boundary_crossing_trade() -> None:
    rows = []

    for period in WQ2_PERIODS:
        start = pd.Timestamp(
            f"{period}-01T00:00:00Z"
        )

        for offset in range(400):
            opened = (
                start
                + pd.Timedelta(
                    minutes=30 * offset
                )
            )

            rows.append(
                {
                    "open_time_utc": opened,
                    "close_time_utc": (
                        opened
                        + pd.Timedelta(
                            minutes=10
                        )
                    ),
                }
            )

    frame = pd.DataFrame(
        rows
    )

    crossing_index = 799

    frame.loc[
        crossing_index,
        "close_time_utc",
    ] = pd.Timestamp(
        "2026-03-01T00:05:00Z"
    )

    plans = build_fold_plans(
        frame,
        mode="expanding",
    )

    march = plans[0]

    assert (
        crossing_index
        in march[
            "purged_indices"
        ]
    )

    assert (
        crossing_index
        not in march[
            "train_indices"
        ]
    )


def test_legacy_rows_are_excluded() -> None:
    report = build_walkforward_report(
        _master()
    )

    assert report[
        "temporal_inventory"
    ][
        "eligible_rows"
    ] == 3760

    assert report[
        "temporal_inventory"
    ][
        "excluded_legacy_rows"
    ] == 231

    assert report[
        "no_cherry_pick_manifest"
    ][
        "skipped_expanding_periods"
    ] == []

    assert report[
        "no_cherry_pick_manifest"
    ][
        "pnl_used_to_define_folds"
    ] is False


def test_classification_has_no_operational_authority() -> None:
    report = build_walkforward_report(
        _master()
    )

    classification = report[
        "research_classification"
    ]

    assert (
        classification[
            "promotion_allowed"
        ]
        is False
    )

    assert (
        classification[
            "operational_authority"
        ]
        is False
    )

    assert (
        report[
            "sends_orders"
        ]
        is False
    )

    assert (
        report[
            "changes_risk"
        ]
        is False
    )

    assert (
        report[
            "changes_model"
        ]
        is False
    )


def test_persistence_is_deterministic(
    tmp_path: Path,
) -> None:
    payload = build_walkforward_report(
        _master()
    )

    first = tmp_path / "first"
    second = tmp_path / "second"

    persist_walkforward_reports(
        payload,
        output_dir=first,
    )

    persist_walkforward_reports(
        payload,
        output_dir=second,
    )

    expected = {
        REPORT_JSON,
        REPORT_MD,
        FOLDS_CSV,
        LOPO_CSV,
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

    for name in expected:
        assert (
            (first / name).read_bytes()
            == (second / name).read_bytes()
        )
