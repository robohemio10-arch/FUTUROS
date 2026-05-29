# Finance-Grade Sidecar Filter

Este fluxo e somente offline/research. Ele nao habilita live trading, nao envia ordens e nao altera o caminho critico do paper 24h.

## Por que o sidecar anterior ficava BLOCKED

O sidecar normalizado consumia todas as linhas de `trade_final_financial_quality_resolved.parquet`, incluindo registros com qualidade final `BLOCKED`. No estado validado, os bloqueios restantes eram:

- `leverage_missing`: leverage ausente ou sem evidencia suficiente.
- `price_return_extreme`: retorno por preco ainda impossivel ou anomalo.
- `net_return_extreme`: retorno alavancado liquido fora do limite institucional.

Essas linhas nao devem entrar na avaliacao financeira porque contaminam metricas agregadas e podem transformar dados ruins em conclusoes aparentemente validas.

## Reparar vs filtrar

Reparar significa corrigir um campo quando ha evidencia forte e auditavel, como escala de preco contra referencia de mercado ou leverage negativo numerico convertido para valor absoluto.

Filtrar significa separar registros que ja passaram pelos reparos conservadores. O filtro finance-grade nao mascara dados ruins: ele escreve um arquivo aceito e outro rejeitado, preservando a rastreabilidade das exclusoes.

## Linhas aceitas

Por padrao, uma linha e aceita somente quando:

- `final_quality_status == OK`;
- `final_quality_flags` nao contem flags bloqueantes;
- as colunas exigidas para o sidecar normalizado estao presentes.

As colunas aceitas incluem `trade_id`, `symbol`, `open_1m_ts`, `target_win`, precos reparados, side reparado, volume reparado, `leverage_resolved`, `raw_return_resolved`, `pnl_resolved`, `price_return_pct`, `leveraged_price_return_pct`, `final_quality_status` e `final_quality_flags`.

## Linhas rejeitadas

Todas as linhas que nao passam no filtro sao gravadas em `trade_finance_grade_rejected.parquet`. Em especial, sao rejeitadas linhas com:

- `final_quality_status` diferente de `OK`;
- `leverage_missing`;
- `price_return_extreme`;
- `net_return_extreme`;
- qualquer outra flag critica de campo financeiro essencial.

## Acceptance Ratio

`acceptance_ratio = rows_accepted / rows_input`.

Um ratio alto indica que a maior parte dos registros esta apta para avaliacao financeira offline. Um ratio baixo gera `WARNING`, e zero linhas aceitas gera `BLOCKED`.

## Sequencia recomendada

```powershell
python scripts/resolve_final_financial_quality_blocks.py `
  --input data/features/trade_financial_consistency_repaired.parquet `
  --output data/features/trade_final_financial_quality_resolved.parquet `
  --report data/reports/final_financial_quality_resolution_report.json `
  --sample-rows 50

python scripts/build_finance_grade_sidecar_input.py `
  --input data/features/trade_final_financial_quality_resolved.parquet `
  --output data/features/trade_finance_grade_sidecar_input.parquet `
  --rejected-output data/features/trade_finance_grade_rejected.parquet `
  --report data/reports/finance_grade_sidecar_input_report.json `
  --sample-rows 50

python scripts/build_normalized_return_sidecar.py `
  --input data/features/trade_finance_grade_sidecar_input.parquet `
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
  --leverage-column leverage_resolved `
  --raw-return-column raw_return_resolved `
  --pnl-column pnl_resolved `
  --fee-bps 8 `
  --slippage-bps 5 `
  --spread-bps 3 `
  --max-abs-net-return-pct 100 `
  --sample-outliers 30

python scripts/run_normalized_financial_evaluation.py `
  --features data/features/training_dataset_open_decision_clean.parquet `
  --sidecar data/features/training_normalized_return_sidecar.parquet `
  --sidecar-report data/reports/normalized_return_sidecar_report.json `
  --output-report data/reports/normalized_financial_evaluation_report.json `
  --id-column trade_id `
  --target-column target_win `
  --return-column net_return_pct `
  --folds 5 `
  --embargo-minutes 60 `
  --seed 42
```

Quando o sidecar normalizado for construido a partir do arquivo finance-grade, ele tera menos linhas do que o dataset de features original. A avaliacao normalizada reconhece esse caso somente quando o report do sidecar esta `OK` ou `WARNING` e declara o mesmo numero de linhas do sidecar. Nesse modo, ela calcula metricas apenas sobre a intersecao auditavel e registra `finance_grade_excluded_rows` e a limitacao `features_filtered_to_finance_grade_sidecar`.

## Interpretacao

- `OK`: ha linhas finance-grade e nenhuma linha aceita contem flags bloqueantes.
- `WARNING`: ha linhas aceitas, mas a taxa de aceitacao e baixa.
- `BLOCKED`: nao ha linhas aceitas ou alguma linha aceita ainda contem flag bloqueante.

Mesmo com `OK`, o resultado continua restrito a research/shadow. Ele nao libera live trading, nao autoriza aumento de risco e nao cria qualquer permissao de envio real de ordens.
