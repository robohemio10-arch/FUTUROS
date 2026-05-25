from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT=Path('.')
INPUT=ROOT/'data'/'features'/'training_dataset_v13_candle_structure_real.parquet'
OUT_JSON=ROOT/'data'/'reports'/'paper_risk_sizing_simulation_summary.json'
OUT_CSV=ROOT/'data'/'reports'/'paper_risk_sizing_simulation_results.csv'
OUT_TRADES_CSV=ROOT/'data'/'reports'/'paper_risk_sizing_simulation_best_trades.csv'

def profit_factor(pnl: pd.Series):
    gp=pnl[pnl>0].sum(); gl=pnl[pnl<0].sum()
    return None if gl==0 else float(gp/abs(gl))

def max_drawdown(equity: pd.Series) -> float:
    return float((equity-equity.cummax()).min())

def normalize_symbol(x): return str(x).upper().replace('/','').strip()
def normalize_side(x):
    s=str(x).upper().strip()
    if s in {'LONG','BUY','COMPRA'}: return 'LONG'
    if s in {'SHORT','SELL','VENDA'}: return 'SHORT'
    return 'UNKNOWN'

def simulate_policy(df: pd.DataFrame, policy: dict):
    rows=[]; daily_pnl={}; cooldown_remaining=0; loss_streak=0
    for _,row in df.iterrows():
        symbol=row['paper_symbol']; side=row['paper_side']; day=row['paper_day']; raw_pnl=float(row['paper_raw_pnl'])
        multiplier=float(policy['btc_multiplier']) if symbol=='BTCUSDT' else float(policy['eth_multiplier']) if symbol=='ETHUSDT' else 1.0
        reason='accepted'; accepted=True
        if cooldown_remaining>0:
            accepted=False; reason='cooldown'; cooldown_remaining-=1
        current_day_pnl=daily_pnl.get(day,0.0); daily_stop=policy['daily_stop_loss_usdt']
        if accepted and daily_stop is not None and current_day_pnl <= -abs(float(daily_stop)):
            accepted=False; reason='daily_stop'
        if accepted:
            paper_pnl=raw_pnl*multiplier; daily_pnl[day]=daily_pnl.get(day,0.0)+paper_pnl
            if paper_pnl<0: loss_streak+=1
            else: loss_streak=0
            if loss_streak >= int(policy['loss_streak_limit']):
                cooldown_remaining=int(policy['cooldown_trades']); loss_streak=0
        else:
            paper_pnl=0.0
        rows.append({'open_time_utc':row['open_time_utc'],'symbol':symbol,'side':side,'raw_pnl_usdt':raw_pnl,'paper_pnl_usdt':paper_pnl,'accepted':accepted,'reason':reason,'multiplier':multiplier,'policy':policy['name']})
    out=pd.DataFrame(rows); out['equity']=out['paper_pnl_usdt'].cumsum(); acc=out[out['accepted']].copy(); rej=out[~out['accepted']].copy(); acc_pnl=acc['paper_pnl_usdt']
    daily=out.groupby(out['open_time_utc'].dt.date)['paper_pnl_usdt'].sum()
    return {'policy':policy['name'],'total_trades':int(len(out)),'accepted_trades':int(len(acc)),'skipped_trades':int(len(rej)),'acceptance_rate':float(len(acc)/len(out)) if len(out) else 0.0,'net_pnl_usdt':float(out['paper_pnl_usdt'].sum()),'raw_net_pnl_usdt':float(out['raw_pnl_usdt'].sum()),'win_rate':float((acc_pnl>0).mean()) if len(acc) else None,'profit_factor':profit_factor(acc_pnl) if len(acc) else None,'mean_pnl_usdt':float(acc_pnl.mean()) if len(acc) else None,'median_pnl_usdt':float(acc_pnl.median()) if len(acc) else None,'max_drawdown_usdt':max_drawdown(out['equity']),'worst_day_usdt':float(daily.min()) if len(daily) else 0.0,'best_day_usdt':float(daily.max()) if len(daily) else 0.0,'active_days':int(len(daily)),'btc_multiplier':float(policy['btc_multiplier']),'eth_multiplier':float(policy['eth_multiplier']),'daily_stop_loss_usdt':policy['daily_stop_loss_usdt'],'loss_streak_limit':int(policy['loss_streak_limit']),'cooldown_trades':int(policy['cooldown_trades']),'skip_by_reason':rej['reason'].value_counts().to_dict()}, out

def main():
    if not INPUT.exists(): raise FileNotFoundError(f'Arquivo não encontrado: {INPUT}')
    df=pd.read_parquet(INPUT).copy(); required=['open_time_utc','reported_pnl_usdt','symbol','side']; missing=[c for c in required if c not in df.columns]
    if missing: raise RuntimeError(f'Colunas obrigatórias ausentes: {missing}')
    df['open_time_utc']=pd.to_datetime(df['open_time_utc'],errors='coerce',utc=True); df['paper_raw_pnl']=pd.to_numeric(df['reported_pnl_usdt'],errors='coerce'); df['paper_symbol']=df['symbol'].map(normalize_symbol); df['paper_side']=df['side'].map(normalize_side); df['paper_day']=df['open_time_utc'].dt.date
    df=df[df['open_time_utc'].notna() & df['paper_raw_pnl'].notna()].sort_values('open_time_utc').reset_index(drop=True)
    policies=[{'name':'baseline_all_trades','btc_multiplier':1.0,'eth_multiplier':1.0,'daily_stop_loss_usdt':None,'loss_streak_limit':999,'cooldown_trades':0},{'name':'btc_075_eth_100','btc_multiplier':0.75,'eth_multiplier':1.0,'daily_stop_loss_usdt':None,'loss_streak_limit':999,'cooldown_trades':0},{'name':'btc_050_eth_100','btc_multiplier':0.5,'eth_multiplier':1.0,'daily_stop_loss_usdt':None,'loss_streak_limit':999,'cooldown_trades':0},{'name':'daily_stop_25','btc_multiplier':1.0,'eth_multiplier':1.0,'daily_stop_loss_usdt':25,'loss_streak_limit':999,'cooldown_trades':0},{'name':'daily_stop_50','btc_multiplier':1.0,'eth_multiplier':1.0,'daily_stop_loss_usdt':50,'loss_streak_limit':999,'cooldown_trades':0},{'name':'loss_streak_3_cooldown_5','btc_multiplier':1.0,'eth_multiplier':1.0,'daily_stop_loss_usdt':None,'loss_streak_limit':3,'cooldown_trades':5},{'name':'loss_streak_4_cooldown_10','btc_multiplier':1.0,'eth_multiplier':1.0,'daily_stop_loss_usdt':None,'loss_streak_limit':4,'cooldown_trades':10},{'name':'combined_conservative','btc_multiplier':0.5,'eth_multiplier':1.0,'daily_stop_loss_usdt':25,'loss_streak_limit':3,'cooldown_trades':5},{'name':'combined_balanced','btc_multiplier':0.75,'eth_multiplier':1.0,'daily_stop_loss_usdt':50,'loss_streak_limit':4,'cooldown_trades':5}]
    results=[]; trade_outputs={}
    for p in policies:
        r,t=simulate_policy(df,p); results.append(r); trade_outputs[p['name']]=t
    res=pd.DataFrame(results); res['score_return_over_dd']=res['net_pnl_usdt']/res['max_drawdown_usdt'].abs().replace(0,np.nan); res=res.sort_values(['score_return_over_dd','net_pnl_usdt'],ascending=[False,False]).reset_index(drop=True)
    best_policy=str(res.iloc[0]['policy']); OUT_JSON.parent.mkdir(parents=True,exist_ok=True); res.to_csv(OUT_CSV,index=False,encoding='utf-8-sig'); trade_outputs[best_policy].to_csv(OUT_TRADES_CSV,index=False,encoding='utf-8-sig')
    report={'status':'ok','input':str(INPUT),'rows_used':int(len(df)),'best_policy':best_policy,'outputs':{'summary_json':str(OUT_JSON),'results_csv':str(OUT_CSV),'best_policy_trades_csv':str(OUT_TRADES_CSV)},'results':res.to_dict(orient='records')}
    OUT_JSON.write_text(json.dumps(report,indent=2,ensure_ascii=False,default=str),encoding='utf-8'); print(json.dumps(report,indent=2,ensure_ascii=False,default=str))
if __name__=='__main__': main()
