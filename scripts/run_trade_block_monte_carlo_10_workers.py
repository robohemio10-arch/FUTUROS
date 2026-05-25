from __future__ import annotations

import argparse
import json
import math
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

ROOT=Path('.')
REPORT_DIR=ROOT/'data'/'reports'
OUT_JSON=REPORT_DIR/'monte_carlo_block_trade_pnl_summary.json'
OUT_CSV=REPORT_DIR/'monte_carlo_block_trade_pnl_segments.csv'
INPUT_CANDIDATES=[ROOT/'data'/'features'/'training_dataset_v13_candle_structure_real.parquet', ROOT/'data'/'features'/'training_dataset_v12b_side_session_real.parquet', ROOT/'data'/'features'/'training_dataset_model_ready_v7_compat.parquet', ROOT/'data'/'features'/'training_dataset_model_ready.parquet']
PNL_CANDIDATES=['reported_pnl_usdt','pnl_usdt','pnl','pnl_fechado']
TIME_CANDIDATES=['open_time_utc','open_ts','open_1m_ts','horario_abertura']
SYMBOL_CANDIDATES=['symbol','moeda']
SIDE_CANDIDATES=['side','trade_side','position_side','direcao']

def pick_existing_file():
    for p in INPUT_CANDIDATES:
        if p.exists(): return p
    raise FileNotFoundError('Nenhum dataset encontrado em data/features.')

def pick_column(df,candidates,required=True):
    for c in candidates:
        if c in df.columns: return c
    if required: raise RuntimeError(f'Nenhuma coluna encontrada entre: {candidates}')
    return None

def normalize_side(series, rows):
    if series is None: return pd.Series(['UNKNOWN']*rows)
    s=series.astype(str).str.upper().str.strip().replace({'BUY':'LONG','COMPRA':'LONG','LONG':'LONG','SELL':'SHORT','VENDA':'SHORT','SHORT':'SHORT'})
    return s.where(s.isin(['LONG','SHORT']),'UNKNOWN')

def profit_factor(pnl):
    gp=float(pnl[pnl>0].sum()); gl=float(pnl[pnl<0].sum())
    return None if gl==0 else gp/abs(gl)

def max_drawdown(equity):
    return float((equity-np.maximum.accumulate(equity)).min())

def summarize_real_sequence(segment,pnl):
    equity=np.cumsum(pnl)
    return {'segment':segment,'trades':int(len(pnl)),'net_pnl_usdt':float(pnl.sum()),'mean_pnl_usdt':float(pnl.mean()),'median_pnl_usdt':float(np.median(pnl)),'win_rate':float((pnl>0).mean()),'profit_factor':profit_factor(pnl),'max_drawdown_usdt':max_drawdown(equity),'best_trade_usdt':float(pnl.max()),'worst_trade_usdt':float(pnl.min()),'gross_profit_usdt':float(pnl[pnl>0].sum()),'gross_loss_usdt':float(pnl[pnl<0].sum())}

def _block_worker(payload):
    pnl_list, block_size, iterations, seed=payload
    pnl=np.asarray(pnl_list,dtype=float); n=len(pnl); block_size=int(block_size)
    rng=np.random.default_rng(seed); blocks_needed=math.ceil(n/block_size)
    starts=rng.integers(0,n,size=(iterations,blocks_needed),endpoint=False); offsets=np.arange(block_size)
    idx=(starts[:,:,None]+offsets[None,None,:])%n; idx=idx.reshape(iterations,blocks_needed*block_size)[:,:n]
    equity=np.cumsum(pnl[idx],axis=1); final_pnl=equity[:,-1]
    max_dd=(equity-np.maximum.accumulate(equity,axis=1)).min(axis=1)
    return final_pnl,max_dd

def run_block_monte_carlo(segment,pnl,block_size,iterations,workers,seed):
    workers=max(1,int(workers)); iterations=int(iterations); chunk_size=math.ceil(iterations/workers)
    chunks=[]; rem=iterations
    for wid in range(workers):
        c=min(chunk_size,rem)
        if c<=0: break
        chunks.append((pnl.tolist(),block_size,c,seed+wid*1009)); rem-=c
    fps=[]; dds=[]
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for fut in as_completed([ex.submit(_block_worker,c) for c in chunks]):
            f,d=fut.result(); fps.append(f); dds.append(d)
    final_pnl=np.concatenate(fps); max_dd=np.concatenate(dds)
    ruin_levels=[-25,-50,-100,-250,-500]
    ruin={f'prob_drawdown_below_{abs(level)}usdt':float((max_dd<=level).mean()) for level in ruin_levels}
    return {'segment':segment,'block_size':int(block_size),'iterations':int(len(final_pnl)),'workers':int(workers),'trades_per_simulation':int(len(pnl)),'final_pnl_p01':float(np.quantile(final_pnl,0.01)),'final_pnl_p05':float(np.quantile(final_pnl,0.05)),'final_pnl_p10':float(np.quantile(final_pnl,0.10)),'final_pnl_p50':float(np.quantile(final_pnl,0.50)),'final_pnl_p90':float(np.quantile(final_pnl,0.90)),'final_pnl_p95':float(np.quantile(final_pnl,0.95)),'final_pnl_p99':float(np.quantile(final_pnl,0.99)),'prob_final_pnl_negative':float((final_pnl<0).mean()),'max_drawdown_p05':float(np.quantile(max_dd,0.05)),'max_drawdown_p10':float(np.quantile(max_dd,0.10)),'max_drawdown_p50':float(np.quantile(max_dd,0.50)),'max_drawdown_worst':float(max_dd.min()),**ruin}

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--workers',type=int,default=10); parser.add_argument('--iterations',type=int,default=20000); parser.add_argument('--seed',type=int,default=20260525); parser.add_argument('--block-sizes',type=int,nargs='+',default=[5,10,20,50]); args=parser.parse_args()
    input_path=pick_existing_file(); df=pd.read_parquet(input_path); pnl_col=pick_column(df,PNL_CANDIDATES); time_col=pick_column(df,TIME_CANDIDATES,False); symbol_col=pick_column(df,SYMBOL_CANDIDATES,False); side_col=pick_column(df,SIDE_CANDIDATES,False)
    df=df.copy(); df['mc_pnl']=pd.to_numeric(df[pnl_col],errors='coerce'); df=df[df['mc_pnl'].notna()].copy()
    if time_col: df['mc_time']=pd.to_datetime(df[time_col],errors='coerce',utc=True); df=df.sort_values('mc_time')
    df['mc_symbol']=df[symbol_col].astype(str).str.upper().str.replace('/','',regex=False) if symbol_col else 'UNKNOWN'; df['mc_side']=normalize_side(df[side_col],len(df)) if side_col else 'UNKNOWN'; df['mc_segment']=df['mc_symbol']+'_'+df['mc_side']
    REPORT_DIR.mkdir(parents=True,exist_ok=True); segment_names=['GLOBAL']+sorted([s for s in df['mc_segment'].dropna().unique().tolist() if 'UNKNOWN' not in s])
    rows=[]; json_segments={}
    for sidx,seg in enumerate(segment_names):
        sub=df if seg=='GLOBAL' else df[df['mc_segment']==seg]
        if len(sub)<100: continue
        pnl=sub['mc_pnl'].to_numpy(dtype=float); real=summarize_real_sequence(seg,pnl)
        for bidx,bs in enumerate(args.block_sizes):
            mc=run_block_monte_carlo(seg,pnl,bs,args.iterations,args.workers,args.seed+sidx*100000+bidx*10000)
            combined={**real,**{f'block_mc_{k}':v for k,v in mc.items() if k!='segment'}}
            rows.append(combined); json_segments[f'{seg}_BLOCK_{bs}']=combined
    pd.DataFrame(rows).to_csv(OUT_CSV,index=False,encoding='utf-8-sig')
    report={'status':'ok','purpose':'Block Monte Carlo bootstrap over realized trade PnL, preserving local sequences.','input':str(input_path),'pnl_column':pnl_col,'time_column':time_col,'symbol_column':symbol_col,'side_column':side_col,'rows_used':int(len(df)),'iterations':int(args.iterations),'workers':int(args.workers),'block_sizes':[int(x) for x in args.block_sizes],'seed':int(args.seed),'outputs':{'summary_json':str(OUT_JSON),'segments_csv':str(OUT_CSV)},'segments':json_segments}
    OUT_JSON.write_text(json.dumps(report,indent=2,ensure_ascii=False,default=str),encoding='utf-8'); print(json.dumps(report,indent=2,ensure_ascii=False,default=str))
if __name__=='__main__': main()
