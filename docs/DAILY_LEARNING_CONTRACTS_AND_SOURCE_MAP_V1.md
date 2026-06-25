# Daily Learning Contracts and Source Map V1

## Objetivo

Esta branch define os contratos canônicos e o source map da esteira diária de
aprendizado Paper/Master. Ela não implementa loaders, não abre arquivos reais,
não calcula KPIs e não materializa relatórios operacionais.

O estado institucional permanece:

- `status=blocked`
- `decision=MANTER_EM_RESEARCH`
- `research_only=true`
- `operational_authority=false`

## Relação com a Branch 01

A Branch 01 consolidou o closeout research-only da divergência Paper vs
`trades_master` e bloqueou qualquer promoção operacional. Esta Branch 02 usa
essa decisão como fonte versionada estática no source map, sem reler dados
operacionais.

## Por que esta branch não lê fontes reais

O objetivo é estabilizar contratos antes de implementar loaders. Ler arquivos
reais nesta etapa misturaria desenho de schema com execução operacional,
prejudicando auditoria e criando risco de efeitos colaterais em `data/`.

Esta branch apenas define:

- IDs canônicos de fontes;
- paths esperados;
- obrigatoriedade;
- política de freshness;
- flags de leitura/escrita da branch atual;
- permissão para loaders em branches futuras.

## Fontes obrigatórias

| Source ID | Categoria | Path esperado | Freshness |
|---|---|---|---|
| `freqtrade_paper_trades_db` | `paper_execution` | `freqtrade/user_data/tradesv3.dryrun.sqlite` | `daily` |
| `trades_master_xlsx` | `master_reference` | `data/processed/trades_master.xlsx` | `daily_or_on_new_ocr_batch` |
| `btc_15s_candles` | `market_data` | `data/raw/binance_futures_klines_15s/BTCUSDT` | `daily` |
| `eth_15s_candles` | `market_data` | `data/raw/binance_futures_klines_15s/ETHUSDT` | `daily` |
| `paper_master_divergence_research_closeout` | `research_closeout` | `docs/PAPER_MASTER_DIVERGENCE_RESEARCH_CLOSEOUT_V1.md` | `versioned_static` |

## Fontes opcionais

| Source ID | Categoria | Path esperado | Freshness |
|---|---|---|---|
| `ai_shadow_decision_logger_report` | `ai_shadow` | `data/reports/ai_shadow_decision_logger_report.json` | `daily` |
| `ai_shadow_outcome_tracker_report` | `ai_shadow` | `data/reports/ai_shadow_outcome_tracker_report.json` | `daily` |
| `ai_selector_observations` | `ai_selector` | `data/reports/freqtrade_paper_ai_selector_observations.jsonl` | `daily` |
| `market_data_health_audit_report` | `market_health` | `data/reports/market_data_health_audit_report.json` | `daily` |
| `runtime_evidence_pack` | `runtime_evidence` | `data/reports/runtime_evidence_pack_v2.json` | `daily` |
| `readiness_snapshot` | `readiness` | `data/reports/readiness_snapshot_v2.json` | `daily` |
| `paper_shadow_soak_gap_accounting` | `soak_gap_accounting` | `data/reports/paper_shadow_soak_gap_accounting_report.json` | `daily` |

Todas as fontes declaram:

- `current_branch_reads_source=false`
- `current_branch_writes_source=false`

## Freshness policy

`daily` indica que uma branch futura deve validar evidência atual no ciclo diário.
`daily_or_on_new_ocr_batch` indica atualização diária ou quando houver novo lote
OCR autorizado. `versioned_static` indica evidência versionada e auditável.

Nenhuma dessas políticas libera readiness, canary ou live.

## Safety flags

O payload preserva:

- `paper_only=true`
- `shadow_only=true`
- `read_only=true`
- `live_trading_enabled=false`
- `canary_release_allowed=false`
- `live_release_allowed=false`
- `order_submission_enabled=false`
- `real_order_submission_enabled=false`
- `exchange_private_access=false`
- `sends_orders=false`
- `changes_risk=false`
- `changes_model=false`

Também permanecem falsos os flags de atualização de Freqtrade, RiskManager,
Qlib runtime, IA Shadow runtime, SQLite, Parquet, OCR e treinamento.

## Semântica blocked-by-default

O contrato retorna `status=blocked` porque ainda não existem loaders, KPI pack,
comparação diária, validação de coverage, catálogo de erros/acertos, registry de
regras ou validação fora da amostra. O source map é desenho de contrato, não
evidência operacional.

## Próximos passos

Branches futuras previstas:

1. `codex/daily-learning-readonly-loaders-v1`
2. `codex/daily-paper-master-kpi-pack-v1`
3. `codex/daily-paper-master-divergence-and-alignment-v1`
4. `codex/daily-candle-coverage-and-entry-features-v1`
5. `codex/daily-mistake-and-winner-catalog-v1`
6. `codex/daily-pattern-mining-research-v1`
7. `codex/daily-candidate-shadow-rule-registry-v1`
8. `codex/daily-shadow-rule-oos-validation-v1`
9. `codex/daily-learning-ai-shadow-feedback-bridge-v1`
10. `codex/daily-learning-qlib-research-dataset-v1`
11. `codex/daily-paper-master-learning-loop-orchestrator-v1`
12. `codex/daily-learning-scheduler-paper-v1`
13. `codex/dashboard-daily-learning-command-center-v1`
14. `codex/daily-learning-evidence-readiness-integration-v1`
15. `codex/daily-learning-loop-closeout-handover-v1`

## Ações proibidas nesta branch

- ler fontes reais;
- calcular KPIs;
- alterar Freqtrade;
- alterar RiskManager;
- alterar Qlib runtime;
- alterar IA Shadow runtime;
- alterar modelos;
- alterar datasets;
- criar scheduler;
- criar dashboard;
- habilitar live ou canary;
- enviar ordem real;
- usar exchange privada;
- escrever artefatos em `data/`, `runtime/`, `reports/`, `logs/` ou `freqtrade/`.

## Execução

No-write por padrão:

```powershell
python .\scripts\build_daily_learning_source_map_v1.py --project-root . --no-write --json
```

Escrita explícita somente para path fora de diretórios runtime:

```powershell
python .\scripts\build_daily_learning_source_map_v1.py --project-root . --output "$env:TEMP\daily_learning_contract.json" --json
```

## Validação

```powershell
python -m compileall scripts smartcrypto tests
python -m pytest .\tests\test_daily_learning_contracts_and_source_map_v1.py -q
python .\scripts\build_daily_learning_source_map_v1.py --project-root . --no-write --json
python .\scripts\generate_project_manifest.py --check
python .\scripts\scan_versioned_secrets.py --project-root . --json
python -m pip_audit -r ".\requirements-dev.lock" --progress-spinner off
git diff --check
```
