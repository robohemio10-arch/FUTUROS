# PAPER PROFIT MOMENTUM PROTECTION A/B V1

## Objetivo

Comparar, em pesquisa paper-only/read-only, três braços fixos derivados da evidência financeira real de 632 trades elegíveis:

1. `control_all_eligible` — comportamento histórico elegível sem filtro adicional;
2. `momentum_ret12` — `entry_return_12 >= 0.004890587971048965`;
3. `momentum_ret12_ret1` — `entry_return_12 >= 0.004890587971048965` e `entry_return_1 >= 0.0013730468839541765`.

A branch também pesquisa proteção de lucro/breakeven porque a análise anterior classificou os 387 losers elegíveis como `winner_to_loser`: o trade apresentou MFE positivo antes de terminar com PnL negativo.

O único objetivo desta branch é aumentar PnL líquido, expectancy e profit factor e reduzir gross loss e maximum drawdown. Não há deploy nem alteração operacional.

## Fonte de dados

A implementação reutiliza `profit_research_dataset_snapshot_v1`:

- trades paper fechados;
- leitura por snapshot ou cópia temporária query-only do SQLite paper quando explicitamente autorizada por `--allow-runtime-read`;
- candles causais alinhados;
- features de entrada point-in-time;
- MFE/MAE e retracement pós-MFE;
- fees e funding observados;
- exclusões financeiras já aplicadas pela engine profit-first.

Nenhum dataset, SQLite, relatório runtime ou sinal ativo é escrito por esta branch.

## Split temporal

A validação usa o mesmo princípio temporal da pesquisa profit-first:

- universo: todos os trades `profit_optimization_eligible` ordenados no tempo;
- train: primeiros 70%;
- OOS: últimos 30%;
- o filtro de cada braço é aplicado depois da definição do corte temporal.

Isso evita redefinir o OOS para favorecer um braço mais seletivo.

## Grid de proteção

Triggers de MFE em movimento de preço:

- 10 bps (`0.10%`);
- 25 bps (`0.25%`);
- 50 bps (`0.50%`);
- 75 bps (`0.75%`);
- 100 bps (`1.00%`).

Pisos simulados:

- net breakeven;
- retenção de 25% do MFE bruto;
- retenção de 50% do MFE bruto;
- retenção de 75% do MFE bruto.

O piso considera `fees + funding` observados. Para net breakeven, o piso bruto necessário cobre os custos observados. Para retenção de MFE, o piso bruto é `max(custos, MFE * retention_fraction)`.

## Dois limites para não superestimar lucro

### Optimistic bound

O piso só é aplicado quando o PnL final observado é inferior ao piso protegido. Nesse caso, depois de o MFE atingir o trigger, o fechamento final prova que o caminho terminou abaixo daquele piso.

Esse limite mede o máximo recuperável sem penalizar winners que possam ter tocado o piso intratrade e depois recuperado.

### Pessimistic bound

O piso é aplicado sempre que `retracement_after_mfe_absolute` indica que o caminho pós-MFE alcançou a distância entre MFE e piso. Isso inclui encerramentos antecipados de winners e contabiliza explicitamente:

- `pessimistic_harmed_winner_count`;
- `pessimistic_winner_pnl_sacrificed`;
- `pessimistic_saved_loser_count`;
- `pessimistic_recovered_winner_to_loser_count`;
- `pessimistic_recovered_winner_to_loser_pnl`.

A decisão usa exclusivamente o **pessimistic bound**.

## Gate de candidato

Uma política de proteção só recebe `PROMOVER_PARA_PAPER_AB` quando, no bound pessimista:

- há pelo menos 8 trades no braço;
- há pelo menos 5 trades OOS;
- full-period net PnL > 0;
- full-period expectancy > 0;
- full-period PF > 1 quando definido;
- OOS net PnL > 0;
- OOS expectancy > 0;
- OOS PF > 1 quando definido;
- PnL melhora contra o mesmo braço sem proteção;
- PnL OOS melhora contra o mesmo braço sem proteção.

Os braços momentum sem proteção também são avaliados contra o baseline global.

## Ranking

A ordem prioriza:

1. candidato aprovado;
2. maior delta OOS robusto contra o baseline global;
3. maior PnL OOS robusto;
4. maior PnL full-period robusto;
5. menor maximum drawdown;
6. menor quantidade de winners prejudicados.

O ranking não usa win rate como objetivo primário.

## Segurança

Invariantes desta branch:

- `research_only=true`;
- `read_only=true`;
- `paper_only=true`;
- `operational_authority=false`;
- `sends_orders=false`;
- `exchange_private_access=false`;
- `changes_risk=false`;
- `changes_roi=false`;
- `changes_stoploss=false`;
- `writes_runtime=false`;
- `updates_freqtrade=false`;
- `model_promotion_performed=false`;
- `deploy_performed=false`.

A branch não altera Freqtrade, RiskManager, ROI, stoploss, Qlib runtime, IA Shadow runtime, containers, canary, live ou ordens.

## CLI

Execução read-only com fonte paper explicitamente permitida:

```powershell
python scripts/run_paper_profit_momentum_protection_ab_v1.py `
  --project-root E:\FUTUROS `
  --allow-runtime-read `
  --json
```

O CLI não possui opção de escrita.

## Critério para próximo passo

O próximo wiring paper só deve ser considerado após existir candidato robusto com PnL/expectancy/PF positivos no full-period e OOS, usando o bound pessimista. Mesmo assim, esta branch não ativa o candidato; ela apenas produz a evidência quantitativa para a decisão seguinte.
