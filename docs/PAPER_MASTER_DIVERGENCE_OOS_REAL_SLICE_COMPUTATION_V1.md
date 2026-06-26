# Paper/Master Divergence OOS Real Slice Computation V1

## Objetivo

Esta branch move a trilha de divergência Paper/Master para a primeira camada
quantitativa real: computação read-only de métricas OOS por fatia quando fontes
reais forem explicitamente fornecidas.

A branch continua bloqueada para operação. Ela não aplica regra, não cria
registry operacional, não altera Freqtrade, RiskManager, Qlib runtime ou IA
Shadow runtime e não envia ordens.

## Hipóteses no escopo

- H1: fast stop-loss destrói expectancy.
- H2: ETH long pode ser cluster estruturalmente negativo.
- H6: candidate rule/filtro pode remover losers, mas precisa provar que não
  remove winners materialmente.

## Dimensões OOS

- day
- symbol
- side
- exit_reason
- duration_bucket
- covered_vs_uncovered

## Métricas mínimas

- trade_count
- net_pnl
- profit_factor
- win_rate
- max_drawdown
- winner_retention_rate
- winner_pnl_removed
- loser_pnl_removed
- false_positive_count
- false_negative_count
- precision
- recall
- coverage_ratio
- simulated_removed_pnl_delta

## Comportamento por padrão

Sem `--allow-runtime-read`, o CLI não carrega fontes reais e retorna:

- `status=blocked`
- `decision=MANTER_EM_RESEARCH`
- `input_mode=no_runtime_rows_loaded`
- `real_slice_metrics_computed=false`
- `oos_validation_required=true`
- `oos_validated=false`
- `ready_for_candidate_registry=false`

## Execução sem leitura runtime

```powershell
python .\scripts\build_paper_master_divergence_oos_real_slice_computation_v1.py `
  --project-root . `
  --no-write `
  --json
```

## Execução com fontes explícitas read-only

```powershell
python .\scripts\build_paper_master_divergence_oos_real_slice_computation_v1.py `
  --project-root . `
  --allow-runtime-read `
  --paper-source "E:\FUTUROS\data\reports\paper_trades_for_research.csv" `
  --master-source "E:\FUTUROS\data\trades\trades_master.xlsx" `
  --no-write `
  --json
```

## Safety

A saída mantém:

- `research_only=true`
- `read_only=true`
- `paper_only=true`
- `shadow_only=true`
- `operational_authority=false`
- `can_apply_to_freqtrade=false`
- `can_apply_to_risk_manager=false`
- `can_promote_rules=false`
- `can_promote_model=false`
- `updates_freqtrade=false`
- `updates_risk_manager=false`
- `updates_qlib_runtime=false`
- `updates_ai_shadow_runtime=false`
- `sends_orders=false`
- `live_release_allowed=false`
- `canary_release_allowed=false`

## Próxima etapa

Após esta branch, o próximo passo é gerar um relatório de decisão OOS real
sobre H1/H2/H6, com ranking das fatias que explicam o gap Paper/Master e com
bloqueadores explícitos para remoção de ROI winners, concentração em único dia
e viés covered/uncovered.
