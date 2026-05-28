# Qlib Prediction Freshness Guard

O Qlib Prediction Freshness Guard impede que o produtor de sinais da Fase 13 gere `primary_signals` ou `pinned_signals` a partir de `latest_qlib_predictions.parquet` antigo.

## Problema

O dashboard mostrou predições Qlib com `date`/`generated_at` de `2026-05-14`, enquanto sinais pinned eram renovados em `2026-05-28`. Isso indica reaproveitamento de predições antigas, o que pode criar sinais paper desconectados do mercado atual.

## Como o guard funciona

Antes de carregar as predições para gerar sinais, `build_active_signals` inspeciona:

- `generated_at`
- `date`

Quando ambos existem, os dois precisam estar dentro da janela permitida. A janela padrão é:

```yaml
policy:
  max_prediction_age_minutes: 90
```

Status possíveis:

- `fresh`: arquivo existe, timestamps válidos e dentro da janela.
- `stale`: arquivo existe, mas `generated_at` ou `date` excede a idade máxima.
- `missing`: arquivo não existe.
- `invalid`: arquivo vazio, corrompido ou timestamps inválidos.

## Comportamento quando stale

Se o arquivo estiver `stale`, `missing` ou `invalid`:

- não grava novos sinais primary;
- não grava novos sinais pinned;
- retorna `status: blocked`;
- retorna `reason: qlib_predictions_stale`, `qlib_predictions_missing` ou `qlib_predictions_timestamp_invalid`;
- escreve apenas o report da Fase 13 com a causa do bloqueio.

Isso evita renovar `active_freqtrade_signals.json` com sinal antigo.

## Dashboard

Na aba `Qlib / Predições`, o dashboard mostra:

- `prediction_generated_at`;
- `prediction_date`;
- `prediction_age_minutes`;
- `max_allowed_age_minutes`;
- `freshness_status`;
- `source_file`;
- motivo do bloqueio quando stale/missing/invalid.

## Segurança

Esta mudança é paper/shadow only. Ela não habilita live trading, não envia ordens, não altera `.env`, não altera Docker, não altera `START_PAPER_24H`, não usa exchange private API e não muda strategy.
