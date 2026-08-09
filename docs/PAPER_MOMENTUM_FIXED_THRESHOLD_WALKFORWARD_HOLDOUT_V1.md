# PAPER MOMENTUM FIXED-THRESHOLD WALK-FORWARD HOLDOUT V1

## Objetivo

Validar, em research-only e read-only, somente os dois filtros de momentum já congelados:

- `entry_return_12 >= 0.004890587971048965`;
- `entry_return_12 >= 0.004890587971048965` AND `entry_return_1 >= 0.0013730468839541765`.

O controle é o conjunto completo de trades paper financeiramente elegíveis. Esta branch não pesquisa novos thresholds e não usa profit-protection.

## Fonte e timeframe

A validação reutiliza `profit_research_dataset` em `5m`, o mesmo domínio temporal em que os thresholds foram descobertos. O dataset é construído com `write_report=False` e `write_dataset=False`.

Leitura direta do runtime só ocorre quando o operador usa explicitamente `--allow-runtime-read`; continua sem escrita de runtime.

## Exclusão financeira

A elegibilidade é herdada de `prepare_profit_dataset`. Amostras conhecidas como financeiramente corrompidas continuam excluídas apenas da análise, sem alteração do dado original.

## Particionamento temporal

1. ordenação cronológica por fechamento e identidade estável;
2. últimos 20% reservados como `replay_holdout`;
3. primeiros 80% formam `development`;
4. development usa três folds walk-forward com histórico expansivo;
5. candidatos são ranqueados somente pelos folds de development;
6. apenas um campeão congelado pode ser avaliado no replay holdout.

O replay holdout nunca participa do ranking.

## Limitação metodológica obrigatória

Os thresholds foram descobertos em uma análise anterior usando o histórico então disponível. Portanto, embora o último 20% fique isolado dentro desta branch, ele não pode ser descrito como holdout historicamente virgem em relação à descoberta dos thresholds.

O relatório registra explicitamente:

- `isolated_inside_this_validation=true`;
- `historically_unseen_during_threshold_discovery=false`;
- `historically_pristine_holdout=false`.

Consequentemente, mesmo que o replay holdout passe, `ready_for_paper_wiring=false`. O resultado pode tornar o candidato elegível para um futuro A/B paper forward, que produzirá evidência realmente posterior ao congelamento dos thresholds.

## Gate de development

Cada candidato precisa:

- selecionar pelo menos cinco trades por fold;
- produzir `net_pnl > 0`;
- produzir `expectancy > 0`;
- produzir `profit_factor > 1` quando houver losses;
- superar o PnL do controle no mesmo intervalo temporal.

Para congelar candidato antes do holdout:

- pelo menos 2 de 3 folds precisam passar;
- agregado walk-forward deve ter PnL, expectancy e PF positivos;
- delta agregado contra controle deve ser positivo;
- mínimo agregado de 15 trades selecionados.

## Gate do replay holdout

O campeão congelado precisa:

- selecionar pelo menos cinco trades;
- `net_pnl > 0`;
- `expectancy > 0`;
- `profit_factor > 1` quando definido;
- delta de PnL contra o controle maior que zero.

O holdout não pode trocar o campeão.

## Métricas

São reportadas para controle e filtros:

- trade count;
- net PnL;
- expectancy;
- profit factor;
- win rate;
- average win;
- average loss;
- maximum drawdown;
- selection ratio;
- positive PnL retention ratio;
- retained positive PnL;
- rejected positive PnL;
- delta de PnL contra controle.

## Segurança

A branch é estritamente:

- research-only;
- read-only;
- paper-only;
- sem profit-protection;
- sem busca de novos thresholds;
- sem Freqtrade/runtime/RiskManager/ROI/stoploss;
- sem alteração de modelo;
- sem containers;
- sem exchange privada;
- sem ordens;
- sem PR/merge/deploy.

## CLI

```powershell
python scripts/run_paper_momentum_fixed_threshold_walkforward_holdout_v1.py `
    --project-root E:\FUTUROS `
    --allow-runtime-read `
    --json
```

Sem `--allow-runtime-read`, o runner permanece snapshot-first/fail-closed.

## Interpretação

- `frozen_champion=null`: nenhum filtro fixo passou o walk-forward; não abrir replay holdout.
- `replay_holdout_passed=false`: campeão development não sustentou o resultado no bloco final.
- `replay_holdout_passed=true`: evidência cronológica favorável, mas historicamente exposta à descoberta anterior.
- `ready_for_forward_paper_ab=true`: candidato pode ser considerado para uma etapa futura de A/B paper forward, mediante nova autorização nominal.
- `ready_for_paper_wiring=false`: esta branch nunca concede autoridade operacional.
