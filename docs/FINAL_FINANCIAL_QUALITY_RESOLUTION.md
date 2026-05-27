# Final Financial Quality Resolution

Esta etapa e uma auditoria/reparo offline para os bloqueios finais do sidecar
normalizado. Ela preserva o modo paper/research/shadow, nao chama exchange,
nao le conta privada, nao envia ordens e nao altera o caminho critico do
`START_PAPER_24H`.

## Por Que Ainda Havia Bloqueios

Depois dos reparos anteriores, o sidecar normalizado ainda bloqueava por:

- `leverage_invalid_defaulted_to_1`: registros sem alavancagem confiavel.
- `net_return_extreme`: registros cujo retorno alavancado ainda era impossivel
  ou fora do limite configurado.

Esses casos nao devem ser resolvidos com defaults agressivos. A regra central e
conservadora: so reparar quando houver evidencia explicita no proprio dataset.

## Leverage Negativo

Algumas fontes OCR/importadas podem representar alavancagem como numero
negativo, por exemplo `-20`. Quando esse valor e numerico, diferente de zero e
`abs(leverage) <= max_leverage`, a etapa final pode usar `abs(leverage)` como
`leverage_resolved`.

Esse reparo sempre fica marcado como `WARNING` com:

- `leverage_negative_abs_resolved`

Se a alavancagem estiver realmente ausente, zero ou acima do limite, o registro
continua `BLOCKED`. O sistema nunca converte alavancagem ausente para `1`.

## Retorno Extremo

O retorno final e sempre reconstruido por preco, side e alavancagem:

- `price_return_pct`: retorno bruto por entry/exit.
- `leveraged_price_return_pct`: `price_return_pct * leverage_resolved`.
- `raw_return_resolved`: valor reconstruido de `leveraged_price_return_pct`
  quando os campos criticos sao validos.

Se o retorno extremo vier apenas de `raw_return` contaminado, o raw return e
recalculado e marcado com `raw_return_recalculated_from_price`.

Se o preco ainda for anomalo, ou se o retorno por preco/alavancado continuar
fora dos limites, o registro fica `BLOCKED` com `price_return_extreme` e/ou
`net_return_extreme`.

## Status

- `OK`: campos criticos validos, leverage resolvido e retorno plausivel.
- `WARNING`: houve reparo conservador ou recalculo auditavel.
- `BLOCKED`: leverage sem evidencia, campo critico invalido ou retorno extremo.

## Sequencia Recomendada

```bash
python scripts/resolve_final_financial_quality_blocks.py \
  --input data/features/trade_financial_consistency_repaired.parquet \
  --output data/features/trade_final_financial_quality_resolved.parquet \
  --report data/reports/final_financial_quality_resolution_report.json \
  --sample-rows 50
```

```bash
python scripts/build_normalized_return_sidecar.py \
  --input data/features/trade_final_financial_quality_resolved.parquet \
  --output data/features/training_normalized_return_sidecar.parquet \
  --report data/reports/normalized_return_sidecar_report.json \
  --id-column trade_id \
  --symbol-column symbol \
  --target-column target_win \
  --time-column open_1m_ts \
  --entry-price-column entry_price_repaired \
  --exit-price-column exit_price_repaired \
  --side-column side_repaired \
  --volume-column volume_repaired \
  --leverage-column leverage_resolved \
  --raw-return-column raw_return_resolved \
  --pnl-column pnl_resolved \
  --fee-bps 8 \
  --slippage-bps 5 \
  --spread-bps 3 \
  --max-abs-net-return-pct 100 \
  --sample-outliers 30
```

```bash
python scripts/run_normalized_financial_evaluation.py \
  --features data/features/training_dataset_open_decision_clean.parquet \
  --sidecar data/features/training_normalized_return_sidecar.parquet \
  --sidecar-report data/reports/normalized_return_sidecar_report.json \
  --output-report data/reports/normalized_financial_evaluation_report.json \
  --id-column trade_id \
  --target-column target_win \
  --return-column net_return_pct \
  --folds 5 \
  --embargo-minutes 60 \
  --seed 42
```

Se ainda houver `BLOCKED`, a avaliacao financeira deve continuar `BLOCKED`.
Isso e esperado e seguro. Esta camada nao libera live trading.
