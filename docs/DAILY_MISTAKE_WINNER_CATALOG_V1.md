# Daily Mistake/Winner Catalog V1

## Objetivo

Esta branch cria o catalogo diario de mistakes e winners do SMART FUTUROS em modo
estritamente research-only. O catalogo classifica trades recebidos em memoria como
`winner`, `mistake`, `neutral` ou `insufficient_evidence`, usando `net_pnl` apenas
como label de resultado.

O objetivo e preparar uma camada auditavel para analise futura. Esta branch nao
minera padroes, nao registra regras candidatas, nao valida fora da amostra e nao
altera qualquer componente operacional.

## Contrato de entrada

O modulo `smartcrypto.research.daily_mistake_winner_catalog` trabalha com listas
em memoria. O CLI `scripts/build_daily_mistake_winner_catalog_v1.py` nao le fontes
reais por padrao.

Campos reconhecidos para classificacao:

- `trade_id`
- `symbol`
- `side`
- `net_pnl`
- `duration_minutes`
- `exit_reason`

Campos opcionais de contexto de entrada:

- `rsi_14`
- `lb_5m_ret_close`
- `lb_10m_ret_close`
- `lb_30m_ret_close`

## Classificacoes

- `winner`: `net_pnl > 0`.
- `mistake`: `net_pnl < 0`.
- `neutral`: `net_pnl == 0`.
- `insufficient_evidence`: `net_pnl` ausente ou invalido.

Subclasses atuais:

- `profitable_trade`
- `stop_loss_loss`
- `fast_loss`
- `unclassified_loss`
- `flat_trade`
- `missing_pnl`

## PnL Como Label

`net_pnl` e usado apenas para classificar o resultado observado. Ele nao e usado
como feature de decisao, nao cria regra candidata e nao autoriza mudanca de
estrategia. O contrato explicita:

- `uses_net_pnl_as_label=true`
- `uses_net_pnl_as_feature=false`
- `uses_future_data=false`
- `creates_candidate_rule=false`
- `operational_action_allowed=false`

## Saida

O relatorio produzido pelo modulo contem:

- `schema_version`
- `status=blocked`
- `decision=MANTER_EM_RESEARCH`
- safety flags paper/shadow
- `catalog`
- `catalog_summary`
- `trade_kpis`
- `catalog_scope`
- `readiness_policy`
- `operator_decision`
- `validation_errors`

O status bloqueado e intencional. O catalogo nao e evidencia de readiness e nao
libera live, canary, alteracao de risco, alteracao de modelo ou alteracao de
execucao.

## CLI

Execucao sem escrita:

```powershell
python .\scripts\build_daily_mistake_winner_catalog_v1.py --project-root . --no-write --json
```

Escrita explicita e segura fora de `data`, `runtime`, `reports`, `logs` e
`freqtrade`:

```powershell
python .\scripts\build_daily_mistake_winner_catalog_v1.py --project-root . --output .\tmp\daily_mistake_winner_catalog_v1.json --json
```

O CLI bloqueia escrita em escopos runtime ou de dados do projeto.

## Fora de Escopo

Esta branch nao:

- le fontes reais;
- grava artefatos em `data/`;
- altera Freqtrade;
- altera RiskManager;
- altera Qlib runtime;
- altera IA Shadow runtime;
- altera modelos;
- altera datasets oficiais;
- cria scheduler;
- cria dashboard;
- minera padroes;
- registra regras candidatas;
- executa validacao fora da amostra;
- habilita live ou canary;
- envia ordens;
- acessa exchange privada.

## Proximos Passos Permitidos

- Criar pattern mining research em branch futura.
- Criar candidate shadow rule registry em branch futura.
- Criar validacao fora da amostra em branch futura.
- Criar AI Shadow feedback bridge em branch futura.
- Criar Qlib research dataset em branch futura.

## Validacao

Comandos recomendados:

```powershell
python -m compileall scripts smartcrypto tests
python -m pytest .\tests\test_daily_mistake_winner_catalog_v1.py -q
python .\scripts\build_daily_mistake_winner_catalog_v1.py --project-root . --no-write --json
python .\scripts\generate_project_manifest.py --check
python .\scripts\scan_versioned_secrets.py --project-root "." --json
```

## Garantias De Seguranca

Todas as safety flags permanecem conservadoras:

- `research_only=true`
- `paper_only=true`
- `shadow_only=true`
- `read_only=true`
- `operational_authority=false`
- `live_trading_enabled=false`
- `order_submission_enabled=false`
- `real_order_submission_enabled=false`
- `exchange_private_access=false`
- `sends_orders=false`
- `changes_risk=false`
- `changes_model=false`
- `writes_runtime=false`
- `writes_data=false`
- `runs_training=false`
