# Qlib Prediction Freshness Guard

O Qlib Prediction Freshness Guard impede que o produtor de sinais da Fase 13 gere `primary_signals` ou `pinned_signals` a partir de `latest_qlib_predictions.parquet` antigo.

## Problema

O dashboard mostrou predições Qlib com `date`/`generated_at` de `2026-05-14`, enquanto sinais pinned eram renovados em `2026-05-28`. Isso indica reaproveitamento de predições antigas, o que pode criar sinais paper desconectados do mercado atual.

## Como o guard funciona

Antes de carregar as predições para gerar sinais, `build_active_signals` inspeciona duas janelas:

- `generated_at`: recência do arquivo de predição.
- `date`: recência do candle/dataset usado como entrada da predição.

As janelas padrão são:

```yaml
policy:
  max_prediction_age_minutes: 90
  max_input_data_age_minutes: 15
```

Status de predição:

- `fresh`: arquivo existe e `generated_at` é válido e recente.
- `stale`: arquivo existe, mas `generated_at` excede a idade máxima.
- `missing`: arquivo não existe.
- `invalid`: arquivo vazio, corrompido ou `generated_at` inválido.

Status de dado de entrada:

- `input_data_fresh`: `date` é válido e recente.
- `input_data_stale`: `date` excede `max_input_data_age_minutes`.
- `missing`: timestamp do dado de entrada ausente.
- `invalid`: timestamp do dado de entrada inválido.

## Comportamento quando stale

Se o arquivo estiver `stale`, `missing` ou `invalid`, ou se o dado de entrada estiver `input_data_stale`, `missing` ou `invalid`:

- não grava novos sinais primary;
- não grava novos sinais pinned;
- retorna `status: blocked`;
- retorna `reason: qlib_predictions_stale`, `qlib_predictions_missing`, `qlib_predictions_timestamp_invalid`, `qlib_input_data_stale`, `qlib_input_data_missing` ou `qlib_input_data_invalid`;
- escreve apenas o report da Fase 13 com a causa do bloqueio.

Isso evita renovar `active_freqtrade_signals.json` com arquivo recém-gerado a partir de candle antigo.

## Dashboard

Na aba `Qlib / Predições`, o dashboard mostra:

- `prediction_generated_at`;
- `prediction_age_minutes`;
- `input_data_timestamp`;
- `input_data_age_minutes`;
- `max_allowed_age_minutes`;
- `max_input_data_age_minutes`;
- `freshness_status`;
- `input_data_status`;
- `source_file`;
- motivo do bloqueio quando stale/missing/invalid.

## Segurança

Esta mudança é paper/shadow only. Ela não habilita live trading, não envia ordens, não altera `.env`, não altera Docker, não altera `START_PAPER_24H`, não usa exchange private API e não muda strategy.
