# Price Scale OCR Repair

Esta fase é uma auditoria/reparo offline para anomalias de escala em `entry_price` e `exit_price`. Ela não altera o input original, não chama exchange, não envia ordens e não libera live trading.

## Por Que Existe

A avaliação financeira normalizada continuou bloqueada porque muitos trades apresentavam retornos por preço impossíveis, como `+/-900%` ou mais. A evidência real sugere erros de escala/OCR:

- BTC com preço 10x maior ou menor que o preço de mercado plausível.
- ETH com preço 10x maior ou menor que o preço de mercado plausível.
- Campos de preço deslocados por casa decimal ou contaminados por OCR.

Sem reparar ou bloquear esses registros, o sidecar normalizado gera `net_return_extreme`, `pnl_incompatible` e métricas financeiras sem validade.

## Como A Correção Funciona

O reparador compara `entry_price` e `exit_price` contra referências de mercado já presentes no próprio dataset:

- Entrada: `open_1m_close` e `open_5m_close`.
- Saída: `close_1m_close` e `close_5m_close`.

Para cada preço, testa fatores conservadores:

```text
0.001, 0.01, 0.1, 1, 10, 100, 1000
```

O candidato escolhido é o preço corrigido mais próximo da referência. A correção só é permitida quando:

- o preço original está longe da referência;
- o candidato fica dentro de `max_reference_distance_pct`;
- a distância cai de forma clara;
- o par corrigido não gera retorno por preço acima de `max_corrected_price_return_pct`.

Se as referências estiverem ausentes, divergirem entre si ou o reparo continuar absurdo, a linha fica `BLOCKED`.

## Status

- `OK`: preço mantido ou corrigido com evidência forte.
- `WARNING`: relatório geral contém alguns bloqueios, mas a maioria das linhas é aproveitável para diagnóstico.
- `BLOCKED`: maioria dos registros não é reparável com segurança.

Correções são auditadas por:

- `entry_price_scale_factor`
- `exit_price_scale_factor`
- distância antes/depois contra referência
- `price_scale_repair_flags`

## Execução

```powershell
python scripts/repair_price_scale_ocr_anomalies.py `
  --input data/features/trade_enriched.parquet `
  --output data/features/trade_price_scale_repaired.parquet `
  --report data/reports/trade_price_scale_ocr_repair_report.json `
  --id-column trade_id `
  --symbol-column symbol `
  --time-column open_1m_ts `
  --entry-price-column entry_price `
  --exit-price-column exit_price `
  --open-reference-column open_1m_close `
  --close-reference-column close_1m_close `
  --alt-open-reference-column open_5m_close `
  --alt-close-reference-column close_5m_close `
  --max-reference-distance-pct 5 `
  --max-corrected-price-return-pct 20 `
  --sample-rows 50
```

## Sequência Recomendada

1. Reparar escala/OCR de preço.

```powershell
python scripts/repair_price_scale_ocr_anomalies.py `
  --input data/features/trade_enriched.parquet `
  --output data/features/trade_price_scale_repaired.parquet `
  --report data/reports/trade_price_scale_ocr_repair_report.json
```

2. Reparar inputs financeiros usando os preços reparados.

```powershell
python scripts/repair_trade_financial_inputs.py `
  --input data/features/trade_price_scale_repaired.parquet `
  --output data/features/trade_financial_inputs_repaired.parquet `
  --report data/reports/trade_financial_input_repair_report.json `
  --entry-price-column entry_price_repaired `
  --exit-price-column exit_price_repaired
```

3. Gerar sidecar normalizado.

```powershell
python scripts/build_normalized_return_sidecar.py `
  --input data/features/trade_financial_inputs_repaired.parquet `
  --output data/features/training_normalized_return_sidecar.parquet `
  --report data/reports/normalized_return_sidecar_report.json `
  --entry-price-column entry_price_repaired `
  --exit-price-column exit_price_repaired `
  --side-column side_repaired `
  --volume-column volume_repaired `
  --leverage-column leverage_repaired `
  --raw-return-column raw_return_repaired `
  --pnl-column pnl_repaired
```

4. Rodar avaliação financeira normalizada.

```powershell
python scripts/run_normalized_financial_evaluation.py `
  --features data/features/training_dataset_open_decision_clean.parquet `
  --sidecar data/features/training_normalized_return_sidecar.parquet `
  --sidecar-report data/reports/normalized_return_sidecar_report.json `
  --output-report data/reports/normalized_financial_evaluation_report.json `
  --return-column net_return_pct
```

Se o sidecar continuar `BLOCKED`, a avaliação deve continuar bloqueada. Isso é comportamento correto.

## Segurança Operacional

Esta fase é somente offline/research. Ela não altera `.env`, não habilita live trading, não chama API privada, não envia ordem real e não entra no caminho crítico do paper 24h.
