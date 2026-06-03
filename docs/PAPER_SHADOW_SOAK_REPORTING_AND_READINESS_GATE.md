# Paper Shadow Soak Reporting And Readiness Gate

Esta frente cria uma camada institucional para consolidar evidencias de soak paper/shadow e decidir se o runtime esta pronto para continuar evoluindo sem promover nada automaticamente.

## Objetivo

O relatorio `paper_soak_report.json` agrega sinais, decisoes shadow, eventos operacionais, alertas criticos, saude de mercado, reconciliacao de estado, ledger de intents, auditoria anti-leakage, Monte Carlo, backtest e qualidade de dados. O `readiness_gate_report.json` consome esse relatorio e as evidencias criticas para produzir uma decisao unica: aprovado ou bloqueado.

Essa decisao e apenas operacional e read-only. Ela nao habilita live, nao envia ordens, nao acessa exchange privada e nao promove modelo.

## Fontes

Fontes padrao do soak:

- `data/reports/financial_event_log.jsonl`
- `data/reports/critical_alerting_report.json`
- `data/reports/risk_recovery_mode_audit_report.json`
- `data/reports/market_data_health_audit_report.json`
- `data/reports/state_reconciliation_audit_report.json`
- `data/reports/order_intent_capital_ledger_audit_report.json`
- `data/reports/ai_governance_dashboard_sources_report.json`
- `data/reports/risk_readiness_soak_dashboard_sources_report.json`
- `data/reports/ai_shadow_drift_monitor_report.json`
- `data/reports/ai_shadow_financial_threshold_evaluation_report.json`
- `data/reports/phase23_anti_leakage_report.json`
- `data/reports/monte_carlo_risk_simulation_report.json`
- `data/reports/event_driven_backtest_report.json`
- `data/reports/data_quality_report.json`
- `data/reports/dataset_manifest.json`

Fonte adicional do readiness gate:

- `data/reports/runtime_safety_config_validation_report.json`

## Metricas

O soak report calcula:

- inicio, fim, dias observados, dias requeridos e dias restantes;
- eventos paper e shadow;
- decisoes totais e decisoes shadow;
- intents simuladas, duplicidade por idempotency key e client order id;
- dispatch unknown;
- divergencia de estado e reconciliacao requerida;
- stale data, bloqueios por spread, liquidez e latencia;
- alertas P0/P1/P2, backup/restore e drills;
- PnL paper/shadow, drawdown e status de auditorias criticas.

## Bloqueios

O soak report bloqueia quando encontra:

- flags inseguras de live, order submission, real order submission ou private exchange;
- soak insuficiente em modo `--strict`;
- incidentes P0/P1;
- duplicidade de order intent ou client order id;
- dispatch unknown;
- reconciliacao requerida ou divergencia de estado;
- market health bloqueado, stale data, spread, liquidez ou latencia;
- risk recovery em `PANIC` ou `RECONCILING`;
- drift, backup ou restore com falha;
- auditorias criticas bloqueadas.

O readiness gate aprova somente quando todos os gates criticos estao `ok`, sem evidencias obrigatorias ausentes, sem bloqueios e com soak minimo cumprido.

## Comandos

Gerar o relatorio de soak:

```powershell
python scripts/build_paper_shadow_soak_report.py --required-soak-days 7
```

Rodar o readiness gate:

```powershell
python scripts/run_readiness_gate_audit.py --required-soak-days 7
```

Modo estrito:

```powershell
python scripts/build_paper_shadow_soak_report.py --required-soak-days 7 --strict
python scripts/run_readiness_gate_audit.py --required-soak-days 7 --strict
```

## Interpretacao

`status=ok` significa que as evidencias locais estao completas e sem bloqueadores. `status=missing_data` indica que o soak foi gerado com alguma fonte ausente, sem promover para aprovacao final. `status=insufficient_soak` indica que o unico bloqueio e tempo de soak abaixo do minimo. `status=blocked` indica risco operacional, evidencia critica ausente em modo estrito ou flag insegura.

`readiness_approved=true` nunca habilita live trading. Ele apenas documenta que, sob as regras paper/shadow, as evidencias atuais nao apresentam bloqueio conhecido.

## Garantias

- Paper/shadow only.
- `LIVE_ENABLED`, order submission real e acesso privado permanecem bloqueados.
- Nenhum script envia ordens.
- Nenhum script acessa exchange privada.
- Nenhum script toca no DB operacional do Freqtrade.
- Nenhum script altera `trades_master` ou `training_dataset.parquet`.
- Nenhum script altera registry, modelos, signal producer, Docker ou `.env`.
- Os artefatos gerados em `data/reports/` sao runtime e nao devem ser versionados.
