# Qlib Fresh Prediction Runner

O runner `scripts/run_qlib_fresh_predictions.py` gera `data/predictions/latest_qlib_predictions.parquet` com `generated_at` atual antes da renovação de sinais pinned. Ele trabalha junto com o freshness guard da Fase 13: primeiro atualiza as predições Qlib, depois o produtor de sinais pode renovar sinais paper/shadow sem reaproveitar parquet antigo.

## Segurança

Este fluxo é paper/shadow only. Ele não envia ordens, não acessa API privada de exchange, não altera `.env`, Docker, `START_PAPER_24H` ou strategy. Os outputs em `data/` e `data/reports/` são runtime e não devem ser versionados.

## Entradas e Saídas

Defaults:

- features: `data/features/market_features_60d.parquet`
- modelo: `data/models/qlib_market_model.joblib`
- predições: `data/predictions/latest_qlib_predictions.parquet`
- relatório: `data/reports/qlib_fresh_prediction_runner_report.json`
- config: `config/qlib_model.yml`

O parquet preserva o schema esperado pelo `signal_producer`:

- `date`
- `generated_at`
- `symbol`
- `pair`
- `tf`
- `prob_up`
- `score`
- `predicted_direction`
- `model_version`
- `model_backend`

O `generated_at` indica quando o arquivo de predições foi emitido. A coluna
`date` representa o timestamp do candle/dado de mercado usado na inferência e
é validada separadamente como `input_data_timestamp`.

## Execução Recomendada

```powershell
$env:PYTHONPATH = "E:\FUTUROS"
python .\scripts\run_qlib_fresh_predictions.py
python .\scripts\phase13_generate_active_signals.py --force-from-predictions --validity-minutes 45
```

Se as predições e o dado de entrada forem frescos, a Fase 13 não deve bloquear.
Se a geração falhar, o relatório retorna `status=blocked` com motivo explícito,
como `market_features_missing`, `model_missing` ou `missing_prediction_columns`.
Se o arquivo for gerado agora, mas o candle/dataset usado for antigo, o bloqueio
esperado é `qlib_input_data_stale`.

## Interpretação

- `status=ok`: parquet gerado e freshness validada.
- `status=blocked`: predição não foi gerada ou não ficou fresca.
- `rows`: quantidade de linhas de predição geradas.
- `pairs`/`symbols`: pares disponíveis para o produtor de sinais.
- `prediction_generated_at`: timestamp de emissão do parquet.
- `input_data_timestamp`: timestamp do candle/dataset usado para inferência.
- `input_data_age_minutes`: idade do dado de entrada.
- `input_data_status`: `input_data_fresh`, `input_data_stale`, `missing` ou `invalid`.
- `prediction_freshness`: diagnóstico do freshness guard.

Gerar predições frescas não libera live trading. O projeto continua em paper/research/shadow, com Freqtrade em dry-run e sem envio real de ordens.
