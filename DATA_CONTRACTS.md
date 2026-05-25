# Contratos de Dados

## `market_features`

| Campo | Tipo | Descrição |
|---|---|---|
| symbol | string | Símbolo interno, exemplo `BTCUSDT` |
| pair | string | Par Freqtrade, exemplo `BTC/USDT:USDT` |
| tf | string | Timeframe |
| ts | datetime UTC | Timestamp do candle |
| open | float | Abertura |
| high | float | Máxima |
| low | float | Mínima |
| close | float | Fechamento |
| volume | float | Volume |
| ret_1 | float | Retorno 1 candle |
| ret_5 | float | Retorno 5 candles |
| ret_15 | float | Retorno 15 candles |
| ema_20 | float | EMA 20 |
| ema_50 | float | EMA 50 |
| ema_200 | float | EMA 200 |
| rsi_14 | float | RSI 14 |
| atr_14 | float | ATR 14 |
| atr_pct_14 | float | ATR percentual |
| vol_30 | float | Volatilidade rolling |
| vol_120 | float | Volatilidade rolling longa |
| vol_rel_30 | float | Volume relativo |

## `trades_excel`

| Campo | Tipo |
|---|---|
| moeda | string |
| fechar_side | string |
| leverage | float |
| order_id | string |
| pnl_fechado | float |
| taxa_lucros_perdas_fechados_pct | float |
| preco_abertura | float |
| preco_fechamento | float |
| volume_posicao | float |
| volume_fechado | float |
| horario_abertura | datetime |
| horario_fechamento | datetime |

## `trade_enriched`

| Campo | Tipo |
|---|---|
| trade_id | string |
| symbol | string |
| pair | string |
| side | string |
| open_ts | datetime UTC |
| close_ts | datetime UTC |
| entry_price | float |
| exit_price | float |
| pnl | float |
| pnl_pct | float |
| duration_seconds | int |
| mfe | float |
| mae | float |
| max_drawdown | float |
| features_at_entry_* | float |
| features_at_exit_* | float |

## `freqtrade_signals.json`

```json
{
  "generated_at": "2026-05-13T17:00:00Z",
  "model_version": "baseline_v1",
  "runtime_mode": "paper",
  "signals": [
    {
      "pair": "BTC/USDT:USDT",
      "side": "long",
      "score": 0.72,
      "confidence": 0.66,
      "timeframe": "5m",
      "valid_until": "2026-05-13T17:05:00Z",
      "risk_approved": true,
      "max_position_usdt": 50,
      "leverage": 2,
      "reason": "score_above_threshold"
    }
  ]
}
```
