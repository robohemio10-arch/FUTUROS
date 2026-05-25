from __future__ import annotations

import json

from smartcrypto.qlib_engine.common import load_config
from smartcrypto.qlib_engine.signal_exporter import export_qlib_freqtrade_signals


def main() -> None:
    config = load_config()
    report = export_qlib_freqtrade_signals(
        predictions_path="data/predictions/latest_qlib_predictions.parquet",
        output_path="data/freqtrade_signals.json",
        report_path="data/reports/phase8_qlib_signal_export_report.json",
        config=config,
    )
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
