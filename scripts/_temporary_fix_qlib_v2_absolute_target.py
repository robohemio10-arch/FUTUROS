from __future__ import annotations

from pathlib import Path
from textwrap import dedent


def _replace_once(text: str, old: str, new: str, reason: str) -> str:
    if text.count(old) != 1:
        raise RuntimeError(reason)
    return text.replace(old, new, 1)


def main() -> None:
    base_path = Path(
        "smartcrypto/learning/paper_autolearning/qlib_market_context_economic_challenger.py"
    )
    base_text = base_path.read_text(encoding="utf-8")
    base_text = _replace_once(
        base_text,
        "from typing import Any, Iterator, Protocol",
        "from typing import Any, Callable, Iterator, Protocol",
        "unexpected_typing_import_shape",
    )
    base_text = _replace_once(
        base_text,
        dedent(
            """\
            def _prepare_fold(
                *,
                prior_rows: Sequence[Mapping[str, Any]],
                test_rows: Sequence[Mapping[str, Any]],
                stress_bps: float,
            ) -> tuple[PreparedFold | None, list[str]]:
            """
        ),
        dedent(
            """\
            def _prepare_fold(
                *,
                prior_rows: Sequence[Mapping[str, Any]],
                test_rows: Sequence[Mapping[str, Any]],
                stress_bps: float,
                target_builder: Callable[[Mapping[str, Any], float], float] | None = None,
                target_error_reason: str = "non_finite_training_matrix",
            ) -> tuple[PreparedFold | None, list[str]]:
            """
        ),
        "unexpected_prepare_fold_signature",
    )
    base_text = _replace_once(
        base_text,
        dedent(
            """\
                target = np.asarray(
                    [_stressed_return_bps(row, stress_bps) for row in fit_rows],
                    dtype=float,
                )
            """
        ),
        dedent(
            """\
                build_target = target_builder or _stressed_return_bps
                target = np.asarray(
                    [build_target(row, stress_bps) for row in fit_rows],
                    dtype=float,
                )
            """
        ),
        "unexpected_target_builder_block",
    )
    base_text = _replace_once(
        base_text,
        dedent(
            """\
                if not np.isfinite(train_x.to_numpy()).all() or not np.isfinite(target).all():
                    return None, ["non_finite_training_matrix"]
                if not np.isfinite(calibration_x.to_numpy()).all():
            """
        ),
        dedent(
            """\
                if not np.isfinite(train_x.to_numpy()).all():
                    return None, ["non_finite_training_matrix"]
                if not np.isfinite(target).all():
                    return None, [target_error_reason]
                if not np.isfinite(calibration_x.to_numpy()).all():
            """
        ),
        "unexpected_non_finite_guard_block",
    )
    base_path.write_text(base_text, encoding="utf-8")

    v2_path = Path(
        "smartcrypto/learning/paper_autolearning/qlib_long_economic_policy_challenger_v2.py"
    )
    v2_text = v2_path.read_text(encoding="utf-8")
    start = v2_text.index("\ndef _prepare_fold(")
    end = v2_text.index("\ndef _select_calibration_threshold(", start)
    replacement = dedent(
        '''\

        def _prepare_fold(
            *,
            prior_rows: Sequence[Mapping[str, Any]],
            test_rows: Sequence[Mapping[str, Any]],
            stress_bps: float,
        ) -> tuple[base.PreparedFold | None, list[str]]:
            """Prepare V2 folds without constructing the obsolete per-notional target."""

            prepared, blockers = base._prepare_fold(
                prior_rows=prior_rows,
                test_rows=test_rows,
                stress_bps=stress_bps,
                target_builder=base._stressed_pnl,
                target_error_reason="non_finite_absolute_stressed_pnl_target",
            )
            if prepared is None:
                return None, blockers
            return prepared, []

        '''
    )
    v2_text = v2_text[:start] + replacement + v2_text[end + 1 :]
    v2_path.write_text(v2_text, encoding="utf-8")

    test_path = Path("tests/test_qlib_long_economic_policy_challenger_v2.py")
    test_text = test_path.read_text(encoding="utf-8")
    marker = (
        "def test_v2_missing_notional_within_allowed_coverage_uses_absolute_target_without_crash"
    )
    if marker not in test_text:
        test_text += dedent(
            '''\


            def test_v2_missing_notional_within_allowed_coverage_uses_absolute_target_without_crash() -> None:
                market, rows = _market_and_rows()
                for row in rows[::30]:
                    row.pop("notional", None)
                    row.pop("quantity", None)

                observed_targets: list[np.ndarray] = []

                def predictor(
                    train_x: pd.DataFrame,
                    train_y: pd.Series,
                    calibration_x: pd.DataFrame,
                    test_x: pd.DataFrame,
                    *,
                    fold_id: str,
                ) -> tuple[np.ndarray, np.ndarray]:
                    del train_x, fold_id
                    observed_targets.append(train_y.to_numpy(dtype=float).copy())
                    return _long_signal_with_short_boost(
                        pd.DataFrame(),
                        pd.Series(dtype=float),
                        calibration_x,
                        test_x,
                        fold_id="ignored",
                    )

                report = build_qlib_long_economic_policy_challenger_v2(
                    project_root=Path("."),
                    rows=rows,
                    market_rows=market,
                    additional_execution_stress_bps=0.0,
                    predictor=predictor,
                )

                assert report["target"] == TARGET_NAME
                assert report["predictor_mode"] == "injected_test_double"
                assert observed_targets
                assert all(np.isfinite(target).all() for target in observed_targets)
                assert report["reason"] != "native_qlib_lgb_unavailable_or_failed"
            '''
        )
    test_path.write_text(test_text, encoding="utf-8")


if __name__ == "__main__":
    main()
