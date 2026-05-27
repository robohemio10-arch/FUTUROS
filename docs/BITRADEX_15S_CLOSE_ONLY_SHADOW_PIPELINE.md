# Bitradex 15s Close-Only Shadow Pipeline

Este pipeline e exclusivamente offline/shadow. Ele nao chama exchange privada, nao envia ordens, nao altera risco e nao modifica o dataset oficial da IA.

## Por que Bitradex 15s foi reprovado como OHLC

A captura 15s da Bitradex nao deve ser tratada como candle institucional completo. Campos como high, low, range e wick podem representar artefatos de captura, amostragem parcial ou reconstrucao incompleta, e por isso nao sao fonte de verdade OHLC.

## Por que close-only shadow e permitido

O fechamento observado em 15s pode ser usado como sinal auxiliar de microestrutura, desde que:

- seja auditado contra Binance 1m;
- seja usado apenas em research/shadow;
- seja unido de forma causal ao dataset oficial;
- nao substitua o OHLC oficial;
- nao execute trade nem altere risco.

## Features permitidas

Permitidas somente como shadow:

- `micro15s_close`
- retornos de close: `micro15s_ret_*`, `micro15s_logret_*`
- volatilidade derivada de retornos de close: `micro15s_vol_ret_*`
- soma de retornos absolutos: `micro15s_absret_sum_*`
- EMAs e distancias calculadas a partir do close
- direcao/momentum derivados de close
- metadados de causalidade: `join_time_utc`, `usable_from_utc`, `feature_policy`

## Features proibidas

Proibidas como fonte de verdade:

- `micro15s_high`
- `micro15s_low`
- `micro15s_range`
- wicks
- padroes OHLC/candle
- qualquer uso para live execution

## Como rodar auditoria V5

```powershell
python scripts/audit_bitradex_15s_close_only_v5.py `
  --v4-dir data/reports/binance_bitradex_15s_complete_minutes_v4 `
  --out-dir data/reports/binance_bitradex_15s_close_only_v5 `
  --symbols BTCUSDT ETHUSDT
```

A auditoria gera comparacoes de close e um `summary.json` com permissao close-only shadow quando os thresholds forem atendidos.

## Como gerar features 15s shadow

```powershell
python scripts/build_15s_microstructure_shadow_features.py `
  --v4-dir data/reports/binance_bitradex_15s_complete_minutes_v4 `
  --v5-summary data/reports/binance_bitradex_15s_close_only_v5/summary.json `
  --feature-dir data/features `
  --report-dir data/reports
```

O builder cria features causais com `join_time_utc = feature_minute_utc + 1m`, evitando usar informacao ainda nao disponivel.

## Como fazer join com dataset oficial

```powershell
python scripts/join_training_dataset_with_15s_shadow_features_v2.py `
  --base data/features/training_dataset_quality_gated_binance_1m.parquet `
  --shadow data/features/bitradex_15s_microstructure_shadow_features.parquet `
  --time-col open_1m_ts `
  --symbol-col symbol
```

O join usa `usable_from_utc` e `merge_asof` por simbolo. O dataset oficial nao e sobrescrito; o resultado e um artefato shadow separado.

## Matched igual a zero

`matched=0` nao e erro quando nao ha sobreposicao temporal entre dataset base e features 15s, ou quando as features estao fora da janela `max_feature_age_minutes`. O relatorio deve registrar a ausencia de match de forma auditavel.

## O que isso nao libera

Mesmo aprovado como close-only shadow, este pipeline nao libera live trading, nao habilita ordens reais, nao altera `START_PAPER_24H`, nao substitui Binance/Freqtrade como referencia operacional e nao promove modelos para producao.
