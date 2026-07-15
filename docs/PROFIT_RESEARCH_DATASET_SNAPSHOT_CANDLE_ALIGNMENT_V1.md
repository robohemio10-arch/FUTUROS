# Profit Research Dataset Snapshot and Candle Alignment V1

## Purpose

This package builds deterministic research evidence from closed paper trades and local
market candles. It measures economic behavior and tests the BTCUSDT blocking hypothesis
without creating a strategy, signal, model, risk rule or execution permission.

## Safety boundary

- Runtime reads are disabled unless `--allow-runtime-read` is explicit.
- SQLite sources are queried from a temporary copy with `query_only` enabled.
- The Trader Master is not consumed by this dataset builder.
- Entry features use only completed candles whose availability timestamp is less than or
  equal to the trade open timestamp.
- Intratrade path fields are diagnostic outcome fields and are never entry features.
- Missing candles and insufficient lookback remain null; no candle is imputed.
- No exchange, Freqtrade strategy, RiskManager, Qlib runtime or IA Shadow runtime is called.

## Package layout

- `contracts.py`: dataset, feature availability and safety contracts.
- `source_inventory.py`: deterministic hashes and source metadata.
- `trade_snapshot.py`: read-only paper SQLite snapshot.
- `candle_alignment.py`: exact/tolerant alignment and intratrade windows.
- `entry_features.py`: point-in-time features.
- `path_features.py`: MFE, MAE, retracement and outcome diagnostics.
- `economic_segments.py`: segment metrics and BTC hypothesis.
- `dataset_builder.py`: orchestration and atomic output control.
- `report.py`: JSON and Markdown rendering.

## Commands

Default fail-closed probe:

```powershell
python .\scripts\build_profit_research_dataset_snapshot_v1.py --project-root . --json
```

Read-only runtime probe:

```powershell
python .\scripts\build_profit_research_dataset_snapshot_v1.py `
  --project-root . `
  --allow-runtime-read `
  --timeframe 5m `
  --json
```

Explicit materialization writes only ignored research/report artifacts:

```powershell
python .\scripts\build_profit_research_dataset_snapshot_v1.py `
  --project-root . `
  --allow-runtime-read `
  --write-report `
  --write-dataset `
  --output-root data `
  --json
```

## Output contract

Reports are written under `data/reports`. The Parquet dataset and manifest, schema,
coverage and rejection sidecars are written under `data/research`. Writes are atomic and
must remain ignored by Git. `generated_at_utc` is audit metadata and is excluded from the
deterministic in-memory dataset hash.

## Economic interpretation

The BTC block hypothesis is classified as `supported`, `weak`, `unstable` or `rejected`.
That classification is descriptive research evidence only. It cannot block a paper or live
trade and has no operational authority.

The default timeframe is `5m`, matching the locally available coverage for the current
paper period. A `1m` request remains valid but returns a controlled warning when its local
history does not cover the trades; the builder never substitutes another timeframe silently.
