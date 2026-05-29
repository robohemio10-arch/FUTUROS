# Normalized Return Cost Model

`return_pct` bruto foi bloqueado pela auditoria de escala porque apresentou
valores extremos e semantica inconsistente. Por isso, a avaliacao financeira
nao deve usar `return_pct` diretamente.

## Campos

- `raw_return_pct`: valor original importado, mantido apenas para auditoria.
- `gross_return_pct`: retorno recalculado por preco de entrada e saida.
- `leveraged_return_pct`: `gross_return_pct * leverage`.
- `net_return_pct`: retorno alavancado descontando custos estimados.

## Conversao De Bps

`1 bps = 0.01%`.

Com os defaults:

- `fee_bps=8` vira `0.08%`
- `slippage_bps=5` vira `0.05%`
- `spread_bps=3` vira `0.03%`
- custo total estimado: `0.16%`

## Regras De Qualidade

O sidecar normalizado registra `quality_flags` quando encontra:

- preco de entrada invalido;
- preco de saida invalido;
- leverage ausente ou invalido;
- volume invalido;
- side desconhecido;
- retorno bruto discrepante;
- `net_return_pct` extremo;
- PnL incompatível quando ha dados suficientes. O PnL absoluto e comparado
  contra o retorno bruto por preco e volume, sem multiplicar por leverage ou
  custos. Leverage e custos afetam ROI/margem, nao o PnL absoluto de contrato
  linear usado nessa checagem.

## Execucao

```bash
python scripts/build_normalized_return_sidecar.py \
  --input data/features/training_dataset.parquet \
  --output data/features/training_normalized_return_sidecar.parquet \
  --report data/reports/normalized_return_sidecar_report.json \
  --id-column trade_id \
  --symbol-column symbol \
  --target-column target_win \
  --time-column open_1m_ts \
  --entry-price-column entry_price \
  --exit-price-column exit_price \
  --side-column fechar_side \
  --volume-column volume_posicao \
  --leverage-column leverage \
  --raw-return-column return_pct \
  --pnl-column pnl \
  --fee-bps 8 \
  --slippage-bps 5 \
  --spread-bps 3
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

## Interpretacao

- `OK`: entradas suficientes e retorno liquido plausivel.
- `WARNING`: dados incompletos ou flags moderadas, apenas pesquisa.
- `BLOCKED`: muitos invalidos, outliers extremos ou retorno impossivel.

A avaliacao financeira normalizada respeita o status do report do sidecar. Se
`normalized_return_sidecar_report.json` estiver `BLOCKED`, a avaliacao tambem
retorna `BLOCKED` e nao calcula metricas como se fossem validas.

`--allow-blocked-sidecar` existe apenas para diagnostico: ele permite calcular
metricas exploratorias mesmo com sidecar bloqueado, mas o relatorio fica
`WARNING` e deve ser tratado como invalido para validacao financeira.

Esta camada nao libera live trading. Ela nao chama exchange, nao le conta
privada, nao envia ordens e nao entra no caminho critico do `START_PAPER_24H`.
