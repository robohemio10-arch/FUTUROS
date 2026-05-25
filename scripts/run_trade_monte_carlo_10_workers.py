from __future__ import annotations

import argparse
import json
import math
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path('.')
REPORT_DIR = ROOT / 'data' / 'reports'
OUT_JSON = REPORT_DIR / 'monte_carlo_trade_pnl_summary.json'
OUT_CSV = REPORT_DIR / 'monte_carlo_trade_pnl_segments.csv'
OUT_EQUITY_CSV = REPORT_DIR / 'monte_carlo_trade_pnl_equity_samples.csv'
INPUT_CANDIDATES = [
    ROOT / 'data' / 'features' / 'training_dataset_v13_candle_structure_real.parquet',
    ROOT / 'data' / 'features' / 'training_dataset_v12b_side_session_real.parquet',
    ROOT / 'data' / 'features' / 'training_dataset_model_ready_v7_compat.parquet',
    ROOT / 'data' / 'features' / 'training_dataset_model_ready.parquet',
]
PNL_CANDIDATES = ['reported_pnl_usdt', 'pnl_usdt', 'pnl', 'pnl_fechado']
TIME_CANDIDATES = ['open_time_utc', 'open_ts', 'open_1m_ts', 'horario_abertura']
SYMBOL_CANDIDATES = ['symbol', 'moeda']
SIDE_CANDIDATES = ['side', 'trade_side', 'position_side', 'direcao']

def pick_existing_file() -> Path:
    for path in INPUT_CANDIDATES:
        if path.exists():
            return path
    raise FileNotFoundError('Nenhum dataset encontrado em data/features.')

def pick_column(df: pd.DataFrame, candidates: list[str], required: bool = True) -> str | None:
    for col in candidates:
        if col in df.columns:
            return col
    if required:
        raise RuntimeError(f'Nenhuma coluna encontrada entre: {candidates}')
    return None

def normalize_side(series: pd.Series | None, rows: int) -> pd.Series:
    if series is None:
        return pd.Series(['UNKNOWN'] * rows)
    s = series.astype(str).str.upper().str.strip()
    s = s.replace({'BUY':'LONG','COMPRA':'LONG','LONG':'LONG','SELL':'SHORT','VENDA':'SHORT','SHORT':'SHORT'})
    return s.where(s.isin(['LONG','SHORT']), 'UNKNOWN')

def profit_factor(pnl: np.ndarray) -> float | None:
    gross_profit = float(pnl[pnl > 0].sum())
    gross_loss = float(pnl[pnl < 0].sum())
    if gross_loss == 0:
        return None
    return gross_profit / abs(gross_loss)

def max_drawdown(equity: np.ndarray) -> float:
    peak = np.maximum.accumulate(equity)
    return float((equity - peak).min())

def summarize_real_sequence(name: str, pnl: np.ndarray) -> dict:
    equity = np.cumsum(pnl)
    return {
        'segment': name,
        'trades': int(len(pnl)),
        'net_pnl_usdt': float(pnl.sum()),
        'mean_pnl_usdt': float(pnl.mean()),
        'median_pnl_usdt': float(np.median(pnl)),
        'win_rate': float((pnl > 0).mean()),
        'profit_factor': profit_factor(pnl),
        'max_drawdown_usdt': max_drawdown(equity),
        'best_trade_usdt': float(pnl.max()),
        'worst_trade_usdt': float(pnl.min()),
        'gross_profit_usdt': float(pnl[pnl > 0].sum()),
        'gross_loss_usdt': float(pnl[pnl < 0].sum()),
    }

def _mc_worker(payload: tuple[list[float], int, int]) -> tuple[np.ndarray, np.ndarray]:
    pnl_list, iterations, seed = payload
    pnl = np.asarray(pnl_list, dtype=float)
    rng = np.random.default_rng(seed)
    sims = rng.choice(pnl, size=(iterations, len(pnl)), replace=True)
    equity = np.cumsum(sims, axis=1)
    final_pnl = equity[:, -1]
    running_peak = np.maximum.accumulate(equity, axis=1)
    max_dd = (equity - running_peak).min(axis=1)
    return final_pnl, max_dd

def run_monte_carlo_parallel(name: str, pnl: np.ndarray, iterations: int, seed: int, workers: int) -> dict:
    workers = max(1, int(workers)); iterations = int(iterations)
    chunk_size = math.ceil(iterations / workers)
    chunks=[]; remaining=iterations
    for worker_id in range(workers):
        this_chunk = min(chunk_size, remaining)
        if this_chunk <= 0: break
        chunks.append((pnl.tolist(), this_chunk, seed + worker_id * 1009)); remaining -= this_chunk
    final_parts=[]; dd_parts=[]
    with ProcessPoolExecutor(max_workers=workers) as executor:
        for future in as_completed([executor.submit(_mc_worker, c) for c in chunks]):
            f, d = future.result(); final_parts.append(f); dd_parts.append(d)
    final_pnl_all = np.concatenate(final_parts); max_dd_all = np.concatenate(dd_parts)
    ruin_levels = [-25,-50,-100,-250,-500]
    ruin_probs = {f'prob_drawdown_below_{abs(level)}usdt': float((max_dd_all <= level).mean()) for level in ruin_levels}
    return {
        'segment': name, 'iterations': int(len(final_pnl_all)), 'workers': int(workers),
        'trades_per_simulation': int(len(pnl)),
        'final_pnl_p01': float(np.quantile(final_pnl_all, 0.01)),
        'final_pnl_p05': float(np.quantile(final_pnl_all, 0.05)),
        'final_pnl_p10': float(np.quantile(final_pnl_all, 0.10)),
        'final_pnl_p50': float(np.quantile(final_pnl_all, 0.50)),
        'final_pnl_p90': float(np.quantile(final_pnl_all, 0.90)),
        'final_pnl_p95': float(np.quantile(final_pnl_all, 0.95)),
        'final_pnl_p99': float(np.quantile(final_pnl_all, 0.99)),
        'prob_final_pnl_negative': float((final_pnl_all < 0).mean()),
        'max_drawdown_p05': float(np.quantile(max_dd_all, 0.05)),
        'max_drawdown_p10': float(np.quantile(max_dd_all, 0.10)),
        'max_drawdown_p50': float(np.quantile(max_dd_all, 0.50)),
        'max_drawdown_worst': float(max_dd_all.min()),
        **ruin_probs,
    }

def main() -> None:
    parser=argparse.ArgumentParser(); parser.add_argument('--workers',type=int,default=10); parser.add_argument('--iterations',type=int,default=20000); parser.add_argument('--seed',type=int,default=20260525); args=parser.parse_args()
    input_path=pick_existing_file(); df=pd.read_parquet(input_path)
    pnl_col=pick_column(df, PNL_CANDIDATES); time_col=pick_column(df, TIME_CANDIDATES, required=False); symbol_col=pick_column(df, SYMBOL_CANDIDATES, required=False); side_col=pick_column(df, SIDE_CANDIDATES, required=False)
    df=df.copy(); df['mc_pnl']=pd.to_numeric(df[pnl_col], errors='coerce'); df=df[df['mc_pnl'].notna()].copy()
    if time_col:
        df['mc_time']=pd.to_datetime(df[time_col], errors='coerce', utc=True); df=df.sort_values('mc_time')
    df['mc_symbol']=df[symbol_col].astype(str).str.upper().str.replace('/','',regex=False) if symbol_col else 'UNKNOWN'
    df['mc_side']=normalize_side(df[side_col], len(df)) if side_col else 'UNKNOWN'; df['mc_segment']=df['mc_symbol']+'_'+df['mc_side']
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    segment_names=['GLOBAL'] + sorted([s for s in df['mc_segment'].dropna().unique().tolist() if 'UNKNOWN' not in s])
    summaries=[]; json_segments={}
    for i, segment in enumerate(segment_names):
        sub=df if segment=='GLOBAL' else df[df['mc_segment']==segment]
        if len(sub)<100: continue
        pnl=sub['mc_pnl'].to_numpy(dtype=float)
        combined={**summarize_real_sequence(segment,pnl), **{f'mc_{k}':v for k,v in run_monte_carlo_parallel(segment,pnl,args.iterations,args.seed+i*100000,args.workers).items() if k!='segment'}}
        summaries.append(combined); json_segments[segment]=combined
    pd.DataFrame(summaries).to_csv(OUT_CSV,index=False,encoding='utf-8-sig')
    global_pnl=df['mc_pnl'].to_numpy(dtype=float); rng=np.random.default_rng(args.seed)
    pd.DataFrame(rng.choice(global_pnl,size=(100,len(global_pnl)),replace=True).cumsum(axis=1).T).to_csv(OUT_EQUITY_CSV,encoding='utf-8-sig',index_label='trade_number')
    report={'status':'ok','purpose':'Monte Carlo bootstrap over realized trade PnL with multiprocessing.','input':str(input_path),'pnl_column':pnl_col,'time_column':time_col,'symbol_column':symbol_col,'side_column':side_col,'rows_used':int(len(df)),'iterations':int(args.iterations),'workers':int(args.workers),'seed':int(args.seed),'outputs':{'summary_json':str(OUT_JSON),'segments_csv':str(OUT_CSV),'equity_samples_csv':str(OUT_EQUITY_CSV)},'segments':json_segments}
    OUT_JSON.write_text(json.dumps(report,indent=2,ensure_ascii=False,default=str),encoding='utf-8'); print(json.dumps(report,indent=2,ensure_ascii=False,default=str))

if __name__ == '__main__': main()
