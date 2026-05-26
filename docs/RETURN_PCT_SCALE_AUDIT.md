# Return Pct Scale Audit

As metricas financeiras atuais parecem absurdas porque `return_pct` pode estar
em escala diferente da interpretada pelo avaliador. Um valor `1.0` pode
significar `1%`, `100%`, retorno alavancado, retorno bruto antes de custos ou
ate um campo corrompido por OCR/importacao.

## Conceitos

- Retorno bruto: variacao antes de custos, slippage e spread.
- Retorno percentual: retorno em pontos percentuais, como `1.5` para `1.5%`.
- Retorno decimal: retorno fracionario, como `0.015` para `1.5%`.
- PnL: resultado monetario, dependente de tamanho de posicao.
- Retorno alavancado: retorno multiplicado por leverage.
- Retorno liquido: retorno depois de taxas, spread e slippage.

Se essas semanticas forem misturadas, `profit_factor`, `total_return_pct` e
`average_return_pct` ficam inflados.

## O Que A Auditoria Faz

- Calcula estatisticas robustas de `return_pct` e `pnl`.
- Agrupa por `symbol`, `target_win` e mes quando ha coluna temporal.
- Detecta outliers de retorno, PnL, preco de entrada/saida, volume e leverage.
- Recalcula retorno aproximado com `entry_price` e `exit_price` quando possivel.
- Compara `return_pct` informado contra retorno recomputado.
- Procura campos candidatos como `taxa_lucros_perdas_fechados_pct`.

## Interpretacao

- `OK`: escala plausivel e sem outliers criticos.
- `WARNING`: escala possivelmente ajustavel ou incompleta, ainda research only.
- `BLOCKED`: escala inconsistente, outliers criticos ou divergencia forte contra
  preco/PnL.

## Execucao

```bash
python scripts/audit_return_pct_scale.py \
  --input data/features/training_dataset.parquet \
  --sidecar data/features/training_outcome_sidecar.parquet \
  --report data/reports/return_pct_scale_audit_report.json \
  --id-column trade_id \
  --return-column return_pct \
  --pnl-column pnl \
  --entry-price-column entry_price \
  --exit-price-column exit_price \
  --volume-column volume_posicao \
  --leverage-column leverage \
  --target-column target_win \
  --symbol-column symbol \
  --sample-outliers 30
```

## Politica Operacional

Esta auditoria e offline/research only. Ela nao chama exchange, nao le conta
privada, nao envia ordens, nao altera datasets originais, nao altera risco, nao
habilita live trading e nao entra no caminho critico do `START_PAPER_24H`.
