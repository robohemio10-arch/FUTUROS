# Paper Profit Maximization — Trader Master + IA Shadow + Qlib V1

## Objetivo

Maximizar lucro líquido em paper usando evidência já existente de trades fechados,
Trader Master, candles/features, IA Shadow e Qlib. A branch não altera runtime,
ROI, stoploss, RiskManager, modelos ativos ou ordens.

## Métricas prioritárias

A engine prioriza, nesta ordem prática:

- `net_pnl`;
- `expectancy`;
- `profit_factor`;
- `average_win`;
- `winner_capture_ratio`;
- redução de `average_loss` e `maximum_drawdown`.

Win rate é informativo, não objetivo primário.

## Integridade financeira

Os trades paper historicamente confirmados como double-full-exit são excluídos apenas
dos cálculos de otimização:

- 141;
- 258;
- 561;
- 653.

Os dados originais não são apagados nem reescritos. Trades marcados pelo dataset como
`accounting_unreconciled` também não participam da otimização financeira.

## Captura dos winners

Para trades positivos com caminho candle disponível:

```text
winner_capture_ratio = realized_net_pnl / mfe_absolute
profit_left_on_table = max(mfe_absolute - realized_net_pnl, 0)
winner_giveback_ratio = profit_left_on_table / mfe_absolute
```

A engine reporta média, mediana, p25/p75, lucro positivo realizado, MFE observado e
quantidade de winners com captura inferior a 50%.

## Diagnóstico dos losers

Os losers são classificados em:

- `winner_to_loser`: houve MFE positivo, mas o resultado final foi negativo;
- `immediate_adverse`: o movimento adverso ocorreu rapidamente antes de recuperação útil;
- `persistent_loss`: demais perdas sem evidência de MFE positivo.

Isso separa problemas de entrada de problemas de proteção de lucro/saída.

## IA Shadow e Qlib

A engine lê, quando disponíveis, os score sources existentes:

```text
data/reports/financial_label_target_store_v1.json
data/reports/ai_shadow_quality_veto_trainer_v1.json
data/reports/qlib_shadow_ensemble_threshold_calibration_v1.json
data/features/incremental_training_microbatch.parquet
```

Aliases aceitos para Qlib incluem `qlib_score`, `qlib_probability`,
`prediction_score` e `model_score`. Para IA Shadow: `ai_shadow_probability`,
`probability_quality`, `ai_shadow_score` e `shadow_score`.

Scores Qlib são convertidos para ranking percentual somente no universo financeiramente
elegível. O ensemble é a média do rank Qlib e da probabilidade IA Shadow. Duplicatas
conflitantes de score são descartadas para evitar inflação artificial de performance.

## Busca de candidatos

São testados:

1. thresholds quantílicos de features de entrada observáveis no momento da entrada;
2. categorias como símbolo, side, regime, hora e dia;
3. thresholds Qlib, IA Shadow e ensemble;
4. interseções entre os melhores filtros simples;
5. políticas de saída já simuladas pela infraestrutura `profit_research` existente.

As políticas de saída também são calculadas somente sobre trades financeiramente elegíveis.
A validação usa split temporal 70/30. Um candidato só recebe
`PROMOVER_PARA_PAPER_AB` quando apresenta simultaneamente:

```text
candidate_net_pnl > 0
out_of_sample_net_pnl > 0
candidate_expectancy > 0
out_of_sample_expectancy > 0
delta_pnl > 0
out_of_sample_delta_pnl > 0
profit_factor > 1 quando definido
```

Nenhuma regra é aplicada ao Freqtrade nesta branch.

## Execução

```powershell
python scripts/run_paper_profit_maximization_trader_master_qlib_v1.py `
  --project-root E:\FUTUROS `
  --json
```

A execução padrão usa o snapshot paper autoritativo e não lê o SQLite runtime. Para um
ambiente onde somente o SQLite runtime esteja disponível, a leitura continua exigindo
`--allow-runtime-read` e passa pela cópia read-only já implementada no projeto.

## Saída principal

O JSON contém:

```text
baseline_paper_metrics
trader_master_metrics
winner_capture
loser_analysis
score_enrichment
ranked_candidates
best_candidate
positive_historical_candidate_found
```

A saída é somente analítica. Não há deploy, promoção de modelo, alteração de stake,
RiskManager, ROI, stoploss, canary, live ou envio de ordens.
