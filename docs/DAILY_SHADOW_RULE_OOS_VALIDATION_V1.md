# Daily Shadow Rule OOS Validation V1

## Objetivo

Esta branch cria a validacao fora da amostra research-only para candidate shadow
rules do SMART FUTUROS. A camada recebe candidate rules, catalog entries e
feature rows em memoria, separa in-sample e out-of-sample de forma deterministica
e calcula metricas descritivas.

Mesmo quando um candidato passa na pesquisa OOS, a promocao permanece bloqueada.

## Relacao Com Branches 01-09

- Branch 01 consolidou o closeout Paper/Master.
- Branch 02 definiu contratos e source map.
- Branch 03 criou loaders read-only.
- Branch 04 criou KPI pack diario.
- Branch 05 criou alinhamento Paper/Master.
- Branch 06 criou coverage e entry features.
- Branch 07 criou o catalogo diario de mistakes e winners.
- Branch 08 criou pattern mining research.
- Branch 09 criou registry research-only de candidate shadow rules.
- Esta Branch 10 valida candidate rules fora da amostra, ainda sem autoridade
  operacional.

## Candidate Registry vs OOS Validation

O candidate registry cataloga hipoteses. A OOS validation mede se essas hipoteses
mantem suporte, confianca e lift na cauda temporal separada.

Esta branch nao aplica regras, nao atualiza IA Shadow runtime e nao transforma
resultado de pesquisa em autorizacao.

## Split Deterministico

O split ordena entradas por:

1. `entry_time`
2. `open_time`
3. `close_time`
4. `trade_id`
5. indice original

A cauda temporal vira OOS. O `oos_fraction` e limitado entre `0.10` e `0.80`.
Com menos de duas entradas, o split retorna estado insuficiente.

## Entradas Em Memoria

Candidate rule:

- `candidate_rule_id`
- `target`
- `conditions`
- `rule_kind`
- `confidence`
- `lift`
- status e flags research-only

Catalog entry:

- `trade_id`
- `classification`
- `subclassification`
- `severity`
- `evidence`
- `symbol`
- `side`
- tempos opcionais

Feature row:

- `trade_id`
- `symbol`
- `side`
- `rsi_14`
- `dist_sma_20_pct`
- `pre_entry_volatility_20`
- `lb_5m_ret_close`
- `lb_10m_ret_close`
- `lb_30m_ret_close`

O CLI nao carrega fontes reais por padrao.

## Match De Conditions

Para cada entrada, a validacao monta um bucket set com:

- `classification_<classification>`
- `sub_<subclassification>`
- `severity_<severity>`
- `side_<side>`
- `symbol_<symbol>`
- buckets de feature da camada de pattern mining
- evidencias no formato `evidence_<value>`

A candidate rule da match quando todas as `conditions` estao no bucket set.

## Targets

- `mistake`: `classification == mistake`
- `winner`: `classification == winner`
- `stop_loss_loss`: subclassificacao ou evidencia correspondente
- `fast_loss_under_30m`: evidencia correspondente
- `profitable_trade`: winner ou subclassificacao correspondente

## Metricas OOS

Por candidate rule:

- in-sample e out-of-sample counts;
- match counts;
- target match counts;
- confidence;
- baseline rate;
- lift;
- confidence degradation;
- status de suporte;
- `oos_status`.

Status possiveis:

- `no_oos_data`
- `insufficient_oos_support`
- `oos_research_pass`
- `oos_research_fail`

## Por Que OOS Pass Nao Promove

`oos_research_pass` e evidencia de pesquisa. Promocao exigiria AI Shadow feedback
bridge, binding de contrato runtime, revisao manual e soak gap-free. Esta branch
nao faz essas etapas.

Todo resultado mantem:

- `promotion_status=blocked`
- `application_status=not_applied`
- `operational_action_allowed=false`
- `promotion_allowed=false`

## IA Shadow Runtime Nao E Alterada

A branch preserva:

- `applies_to_ai_shadow_runtime=false`
- `updates_ai_shadow_runtime=false`
- `applies_shadow_rules=false`
- `promotes_shadow_rules=false`

Nao ha aplicacao de filtro, veto operacional, registry operacional ou codigo de
execucao.

## Safety Flags

Flags preservadas:

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
- `runs_ocr=false`
- `runs_ai_shadow_incremental=false`
- `runs_oos_validation=true`
- `applies_shadow_rules=false`
- `promotes_shadow_rules=false`

## Proximos Passos Permitidos

- Criar AI Shadow feedback bridge em branch futura.
- Criar Qlib research dataset em branch futura.
- Criar daily learning orchestrator em branch futura.
- Criar dashboard daily learning command center em branch futura.
- Criar evidence readiness integration em branch futura.

## Acoes Proibidas

- Alterar Freqtrade.
- Alterar RiskManager.
- Alterar Qlib runtime.
- Alterar IA Shadow runtime.
- Alterar modelos.
- Alterar datasets.
- Habilitar live.
- Habilitar canary.
- Enviar ordem real.
- Usar exchange privada.
- Escrever artefatos em escopos runtime do projeto.
- Usar OOS validation para liberar operacao.
- Promover regra candidata.
- Aplicar candidate rule.
- Registrar em registry operacional.
- Gerar codigo operacional de veto.

## CLI

Sem escrita:

```powershell
python .\scripts\build_daily_shadow_rule_oos_validation_v1.py --project-root . --no-write --json
```

Escrita explicita somente fora de escopos runtime/dados:

```powershell
python .\scripts\build_daily_shadow_rule_oos_validation_v1.py --project-root . --output .\tmp\daily_shadow_rule_oos_validation_v1.json --json
```

Parametros:

- `--project-root`
- `--json`
- `--no-write`
- `--output`
- `--oos-fraction`
- `--min-oos-support-count`
- `--min-oos-confidence`
- `--max-confidence-degradation`

## Validacao

```powershell
python -m compileall scripts smartcrypto tests
python -m pytest .\tests\test_daily_shadow_rule_oos_validation_v1.py -q
python .\scripts\build_daily_shadow_rule_oos_validation_v1.py --project-root . --no-write --json
python .\scripts\generate_project_manifest.py
python .\scripts\generate_project_manifest.py --check
python .\scripts\scan_versioned_secrets.py --project-root . --json
python -m pip_audit -r ".\requirements-dev.lock" --progress-spinner off
git diff --check
git status -sb
git status --short
```
