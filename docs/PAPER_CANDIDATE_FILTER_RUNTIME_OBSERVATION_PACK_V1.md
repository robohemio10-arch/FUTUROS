# Paper Candidate Filter Runtime Observation Pack V1

## Objetivo

Este pack consolida evidencias locais do paper candidate filtrado depois do wiring do
`PaperOnlyCandidateFilterAdapter`. Ele compara o baseline pre-wiring do AB test com
eventos reais ou locais de decisao do runtime `paper_candidate`.

O resultado e uma medida de observacao. Nao cria novo filtro, nao altera regra de
decisao, nao promove estrategia e nao muda runtime operacional.

## Fontes

Entradas padrao, todas locais e read-only:

- `data/reports/paper_only_candidate_strategy_ab_test_v1.json`
- `data/reports/paper_shadow_observation_daily_impact_report_v1.json`
- `data/reports/paper_closed_trades_readonly_source_contract_v1.json`
- eventos de decisao paper candidate, quando existirem, em reports locais como:
  - `data/reports/paper_candidate_filter_runtime_wiring_v1.json`
  - `data/reports/phase13_signal_producer_report.json`

Sem `--allow-runtime-read`, nenhuma fonte runtime e carregada.

## Saidas

Somente com `--write` explicito:

- `data/reports/paper_candidate_filter_runtime_observation_pack_v1.json`
- `data/reports/paper_candidate_filter_runtime_observation_pack_v1.md`

Esses arquivos sao artefatos runtime e nao devem ser versionados.

## Uso

Modo seguro padrao, sem leitura runtime:

```powershell
python .\scripts\build_paper_candidate_filter_runtime_observation_pack_v1.py --project-root . --no-write --json
```

Modo de observacao com leitura local explicita:

```powershell
python .\scripts\build_paper_candidate_filter_runtime_observation_pack_v1.py --project-root . --allow-runtime-read --write --json
```

Com fonte de eventos explicita:

```powershell
python .\scripts\build_paper_candidate_filter_runtime_observation_pack_v1.py `
  --project-root . `
  --allow-runtime-read `
  --decision-events data\reports\paper_candidate_filter_runtime_wiring_v1.json `
  --write `
  --json
```

## Interpretacao

Se ainda nao houver eventos reais pos-wiring, o pack retorna:

- `status=blocked`
- `reason=no_post_wiring_runtime_observation_events_found`
- `observation_status=waiting_for_runtime_evidence`
- `recommended_next_action=rodar_paper_candidate_filtrado_e_reexecutar_observation_pack`

Isso nao e falha de codigo. Significa que o bot ainda precisa rodar em
`runtime_mode=paper_candidate` para materializar eventos observaveis.

Quando eventos existirem, o gate esperado e:

- `decision_events_loaded=true`
- `paper_candidate_filter_called=true`
- `block_event_count > 0`
- `allow_event_count > 0`
- `ethusdt_long_block_event_count >= 1`
- `ethusdt_short_block_event_count >= 1`

## Garantias de seguranca

O pack preserva:

- `paper_only=true`
- `live_behavior_changed=false`
- `canary_behavior_changed=false`
- `order_submission_enabled=false`
- `real_order_submission_enabled=false`
- `sends_orders=false`
- `exchange_private_access=false`
- `changes_risk=false`
- `updates_freqtrade=false`
- `updates_risk_manager=false`
- `updates_qlib_runtime=false`
- `updates_ai_shadow_runtime=false`
- `changes_model=false`
- `writes_runtime=false`
- `writes_sqlite=false`
- `writes_parquet=false`

Fora de escopo:

- alterar `PaperOnlyCandidateDecisionFilter`;
- alterar `PaperOnlyCandidateFilterAdapter`;
- alterar `signal_producer.py`;
- alterar live/canary/orders;
- alterar RiskManager, Qlib runtime, IA Shadow runtime, modelos, Docker, `.env`,
  SQLite, Parquet operacional ou `data/runtime`.
