# Fase 23 - Walk-Forward Anti-Leakage Audit

Esta fase existe para investigar metricas perfeitas ou quase perfeitas em
experimentos de IA. Em dados financeiros, esse resultado costuma indicar algum
tipo de vazamento: alvo usado como feature, informacao futura no input,
fechamento de trade usado para prever abertura ou split temporal incorreto.

## Colunas Proibidas

Como feature, a auditoria bloqueia colunas como:

- `future_ret_*`
- `target_*`
- `pnl`, `pnl_pct`, `realized_pnl`, `closed_pnl`
- `return_pct`, exceto quando usado apenas como resultado/label fora das features
- `mfe_pct` e `mae_pct`
- `close_*` quando `decision_mode=open`
- prefixos ou sufixos que indiquem futuro, proximo candle, pos-evento ou outcome

`target_win` pode ser usado como alvo, desde que esteja fora de
`feature_columns`.

## Target, Metadata E Feature

- Target: coluna que o modelo tenta prever, por padrao `target_win`.
- Metadata: identificadores e tempo, como `trade_id`, `symbol`, `open_ts`.
- Feature: informacao disponivel no momento da decisao paper/shadow.

Qualquer target ou resultado realizado dentro das features torna o relatorio
`BLOCKED`.

## Split Temporal

O utilitario de walk-forward ordena os dados por tempo, cria folds com treino
sempre anterior ao teste e falha quando ha timestamps nulos ou impossiveis de
interpretar. O metadado de cada fold registra janela de treino, janela de teste,
linhas e quantidade removida por purging.

## Embargo E Purging

Embargo cria um intervalo entre fim do treino e inicio do teste. Purging remove
eventos de treino cujo `event_end` se sobrepoe ao inicio do teste. Isso reduz o
risco de o modelo aprender informacao que ainda estaria aberta durante a janela
de teste.

## Interpretacao

- `OK`: nao ha vazamento detectado e os checks basicos passaram.
- `WARNING`: ha colunas suspeitas, mas nao bloqueantes.
- `BLOCKED`: ha vazamento explicito ou uso indevido de target/resultado.

`BLOCKED` bloqueia a avaliacao de IA, nao libera live trading e nao muda risco.

## Nao Libera Live

Esta fase e offline/research only. Ela nao chama exchange, nao le conta privada,
nao envia ordens e nao deve ser adicionada ao `START_PAPER_24H`.

## Execucao Recomendada

```bash
python scripts/run_phase23_anti_leakage_audit.py \
  --dataset data/features/training_dataset.parquet \
  --target-column target_win \
  --time-column open_ts \
  --decision-mode open \
  --folds 5 \
  --embargo-minutes 60
```

Relatorios runtime esperados:

- `data/reports/phase23_anti_leakage_report.json`
- `data/reports/phase23_feature_audit.json`
- `data/reports/phase23_walkforward_clean_report.json`

Esses arquivos permanecem ignorados pelo git.
