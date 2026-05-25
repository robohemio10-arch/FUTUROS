from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path('.')
DEFAULT_INPUTS = [
    ROOT / 'data' / 'features' / 'training_dataset_v13_candle_structure_real.parquet',
    ROOT / 'data' / 'features' / 'training_dataset_model_ready_v7_compat.parquet',
    ROOT / 'data' / 'features' / 'training_dataset_model_ready.parquet',
]
STATE_PATH = ROOT / 'data' / 'runtime' / 'paper_risk_controller_state.json'
DAILY_SUMMARY_JSON = ROOT / 'data' / 'reports' / 'paper_risk_controller_daily_summary.json'
DAILY_TRADES_CSV = ROOT / 'data' / 'reports' / 'paper_risk_controller_daily_trades.csv'
EQUITY_CSV = ROOT / 'data' / 'reports' / 'paper_risk_controller_equity.csv'
POLICY = {
    'name': 'btc_075_eth_100',
    'multipliers': {'BTCUSDT': 0.75, 'ETHUSDT': 1.00},
    'daily_emergency_stop_usdt': -25.0,
    'cooldown_enabled': False,
    'order_submission_enabled': False,
    'real_order_submission_enabled': False,
}

def pick_input(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit)
        if not path.exists(): raise FileNotFoundError(path)
        return path
    for p in DEFAULT_INPUTS:
        if p.exists(): return p
    raise FileNotFoundError('Nenhum dataset encontrado em data/features.')

def normalize_symbol(x: object) -> str:
    return str(x).upper().replace('/', '').strip()

def normalize_side(x: object) -> str:
    s = str(x).upper().strip()
    if s in {'LONG', 'BUY', 'COMPRA'}: return 'LONG'
    if s in {'SHORT', 'SELL', 'VENDA'}: return 'SHORT'
    return 'UNKNOWN'

def max_drawdown(series: pd.Series) -> float:
    return float((series - series.cummax()).min()) if len(series) else 0.0

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', default=None)
    parser.add_argument('--since', default=None, help='Optional ISO date/time filter for new trades.')
    args = parser.parse_args()
    input_path = pick_input(args.input)
    df = pd.read_parquet(input_path).copy()
    required = ['open_time_utc', 'reported_pnl_usdt', 'symbol', 'side']
    missing = [c for c in required if c not in df.columns]
    if missing: raise RuntimeError(f'Colunas obrigatórias ausentes: {missing}')
    df['open_time_utc'] = pd.to_datetime(df['open_time_utc'], errors='coerce', utc=True)
    df['raw_pnl_usdt'] = pd.to_numeric(df['reported_pnl_usdt'], errors='coerce')
    df['symbol_norm'] = df['symbol'].map(normalize_symbol)
    df['side_norm'] = df['side'].map(normalize_side)
    df = df[df['open_time_utc'].notna() & df['raw_pnl_usdt'].notna()].sort_values('open_time_utc').reset_index(drop=True)
    if args.since:
        since = pd.to_datetime(args.since, utc=True)
        df = df[df['open_time_utc'] >= since].copy()
    df['risk_multiplier'] = df['symbol_norm'].map(POLICY['multipliers']).fillna(1.0).astype(float)
    df['paper_pnl_usdt'] = df['raw_pnl_usdt'] * df['risk_multiplier']
    df['raw_equity'] = df['raw_pnl_usdt'].cumsum()
    df['paper_equity'] = df['paper_pnl_usdt'].cumsum()
    df['trade_day'] = df['open_time_utc'].dt.date.astype(str)
    daily = df.groupby('trade_day', dropna=False).agg(
        trades=('paper_pnl_usdt', 'size'),
        raw_pnl_usdt=('raw_pnl_usdt', 'sum'),
        paper_pnl_usdt=('paper_pnl_usdt', 'sum'),
        btc_trades=('symbol_norm', lambda s: int((s == 'BTCUSDT').sum())),
        eth_trades=('symbol_norm', lambda s: int((s == 'ETHUSDT').sum())),
    ).reset_index()
    report = {
        'status': 'ok',
        'mode': 'paper_risk_controller_shadow',
        'input': str(input_path),
        'rows_used': int(len(df)),
        'policy': POLICY,
        'raw_net_pnl_usdt': float(df['raw_pnl_usdt'].sum()) if len(df) else 0.0,
        'paper_net_pnl_usdt': float(df['paper_pnl_usdt'].sum()) if len(df) else 0.0,
        'raw_max_drawdown_usdt': max_drawdown(df['raw_equity']),
        'paper_max_drawdown_usdt': max_drawdown(df['paper_equity']),
        'symbol_counts': df['symbol_norm'].value_counts(dropna=False).to_dict(),
        'side_counts': df['side_norm'].value_counts(dropna=False).to_dict(),
        'outputs': {'daily_summary_json': str(DAILY_SUMMARY_JSON), 'daily_trades_csv': str(DAILY_TRADES_CSV), 'equity_csv': str(EQUITY_CSV), 'state_json': str(STATE_PATH)},
    }
    DAILY_SUMMARY_JSON.parent.mkdir(parents=True, exist_ok=True); STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(DAILY_TRADES_CSV, index=False, encoding='utf-8-sig')
    df[['open_time_utc', 'symbol_norm', 'side_norm', 'raw_pnl_usdt', 'paper_pnl_usdt', 'raw_equity', 'paper_equity']].to_csv(EQUITY_CSV, index=False, encoding='utf-8-sig')
    DAILY_SUMMARY_JSON.write_text(json.dumps({**report, 'daily': daily.to_dict(orient='records')}, indent=2, ensure_ascii=False, default=str), encoding='utf-8')
    STATE_PATH.write_text(json.dumps({'last_run_status': 'ok', 'last_input': str(input_path), 'rows_used': int(len(df)), 'policy': POLICY}, indent=2, ensure_ascii=False, default=str), encoding='utf-8')
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
if __name__ == '__main__': main()
