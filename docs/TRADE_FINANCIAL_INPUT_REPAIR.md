# Trade Financial Input Repair

Esta fase é uma auditoria/reparo offline para os campos financeiros usados pelo modelo de retorno normalizado. Ela não altera o dataset original, não executa ordens, não lê exchange e não libera live trading.

## Por Que Existe

O sidecar normalizado bloqueou a avaliação porque encontrou muitos campos financeiros críticos inválidos:

- `volume_invalid`: volume nulo, zero, negativo ou não numérico.
- `raw_return_discrepant`: retorno bruto incompatível com o retorno recalculado por preço.
- `net_return_extreme`: retorno líquido acima do limite plausível configurado.
- `leverage_invalid_defaulted_to_1`: alavancagem ausente ou inválida.

Esses problemas tornam métricas como `profit_factor`, retorno total e retorno médio não confiáveis. A resposta correta é separar registros reparáveis dos bloqueados, não maquiar os dados.

## Correções Permitidas

O reparador aplica somente normalizações conservadoras:

- Converter alavancagem como `20x`, `20X` ou `20` para número.
- Converter volume com vírgula decimal, como `2,5`.
- Normalizar side/direction quando há evidência textual clara.
- Recalcular `price_return_pct` por `entry_price`, `exit_price` e direção.
- Usar campo original de retorno como fallback auditável quando o retorno bruto principal está ausente.

## Correções Proibidas

O reparador não inventa dados:

- Não inventa volume.
- Não inventa alavancagem.
- Não corrige preço com OCR de forma agressiva.
- Não altera o parquet de entrada.
- Não remove registros silenciosamente.
- Não transforma registro bloqueado em válido sem evidência.

## Status

- `OK`: campos críticos coerentes.
- `WARNING`: linha utilizável apenas para diagnóstico/research, com flags leves.
- `BLOCKED`: campo crítico ausente, inválido ou inconsistente sem reparo confiável.

O relatório geral fica:

- `OK` se não houver bloqueios nem warnings relevantes.
- `WARNING` se houver registros bloqueados em minoria ou registros reparáveis com ressalvas.
- `BLOCKED` se a maioria dos registros críticos continuar não reparável.

## Execução

```powershell
python scripts/repair_trade_financial_inputs.py `
  --input data/features/trade_enriched.parquet `
  --output data/features/trade_financial_inputs_repaired.parquet `
  --report data/reports/trade_financial_input_repair_report.json `
  --id-column trade_id `
  --symbol-column symbol `
  --target-column target_win `
  --time-column open_1m_ts `
  --entry-price-column entry_price `
  --exit-price-column exit_price `
  --side-column fechar_side `
  --volume-column volume_posicao `
  --leverage-column leverage `
  --raw-return-column return_pct `
  --pnl-column pnl `
  --original-pnl-column pnl_fechado `
  --original-return-column taxa_lucros_perdas_fechados_pct `
  --max-abs-price-return-pct 20 `
  --max-leverage 125 `
  --sample-rows 50
```

Depois, o sidecar normalizado pode consumir as colunas reparadas:

```powershell
python scripts/build_normalized_return_sidecar.py `
  --input data/features/trade_financial_inputs_repaired.parquet `
  --output data/features/training_normalized_return_sidecar.parquet `
  --report data/reports/normalized_return_sidecar_report.json `
  --id-column trade_id `
  --symbol-column symbol `
  --target-column target_win `
  --time-column open_1m_ts `
  --entry-price-column entry_price_repaired `
  --exit-price-column exit_price_repaired `
  --side-column side_repaired `
  --volume-column volume_repaired `
  --leverage-column leverage_repaired `
  --raw-return-column raw_return_repaired `
  --pnl-column pnl_repaired `
  --fee-bps 8 `
  --slippage-bps 5 `
  --spread-bps 3 `
  --max-abs-net-return-pct 100 `
  --sample-outliers 30
```

Se os dados continuarem ruins, o sidecar e a avaliação financeira devem continuar `BLOCKED`. Isso é esperado e seguro.

## Segurança Operacional

Esta fase é somente offline/research. Ela não altera `.env`, não habilita live trading, não chama API privada, não envia ordem real e não entra no caminho crítico do paper 24h.
