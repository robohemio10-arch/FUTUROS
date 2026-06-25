# Daily Candidate Shadow Rule Registry V1

## Objetivo

Esta branch cria um registry research-only de candidate shadow rules do SMART
FUTUROS. A camada converte patterns descritivos em memoria em candidatos
catalogados, mantendo todos bloqueados para uso operacional.

O registry criado aqui e payload auditavel de pesquisa. Ele nao e registry
operacional, nao aplica regras, nao altera IA Shadow runtime e nao libera live,
canary, risco, modelo ou execucao.

## Relacao Com Branches 01-08

- Branch 01 consolidou o closeout Paper/Master.
- Branch 02 definiu contratos e source map.
- Branch 03 criou loaders read-only.
- Branch 04 criou KPI pack diario.
- Branch 05 criou alinhamento Paper/Master.
- Branch 06 criou coverage e entry features.
- Branch 07 criou o catalogo diario de mistakes e winners.
- Branch 08 criou pattern mining research.
- Esta Branch 09 cataloga patterns elegiveis como candidate shadow rules de
  pesquisa.

## Pattern Mining vs Candidate Registry

Pattern mining descreve concentracoes estatisticas encontradas em memoria.
Candidate registry transforma alguns desses patterns em candidatos rastreaveis
para revisao futura.

O candidate registry desta branch nao aplica nada. Ele apenas preserva:

- origem do pattern;
- target;
- condicoes;
- suporte;
- confianca;
- lift;
- blockers de promocao;
- status de aplicacao `not_applied`.

## Research Candidate vs Regra Operacional

Um `research_candidate` e uma hipotese catalogada. Ele nao tem autoridade de
execucao.

Uma regra operacional exigiria branch futura, validacao fora da amostra,
review manual, contrato runtime explicito e soak gap-free. Nada disso acontece
nesta branch.

## Formato Esperado De Patterns Em Memoria

Campos aceitos:

- `pattern_id`
- `pattern_type`
- `target`
- `conditions`
- `support_count`
- `target_count`
- `non_target_count`
- `confidence`
- `baseline_rate`
- `lift`
- `coverage_pct`
- `examples_sample`
- `research_interpretation`
- `creates_candidate_rule=false`
- `operational_action_allowed=false`
- `requires_oos_validation=true`
- `promotion_allowed=false`

## Filtros Minimos

Um pattern so vira candidate shadow rule de pesquisa se:

- `support_count >= min_support_count`;
- `confidence >= min_confidence`;
- `lift >= min_lift`;
- target pertence aos alvos permitidos;
- o pattern ja bloqueia acao operacional;
- o pattern ja bloqueia promocao.

Defaults:

- `min_support_count=2`
- `min_confidence=0.5`
- `min_lift=1.0`

## Conversao Pattern Para Candidate Shadow Rule

Targets de perda:

- `mistake`
- `stop_loss_loss`
- `fast_loss_under_30m`

viram `rule_kind=block_candidate`.

Targets de ganho:

- `winner`
- `profitable_trade`

viram `rule_kind=allow_candidate`.

Qualquer alvo nao previsto ficaria fora dos filtros ou seria observado somente.

## Blockers Obrigatorios

Todo candidato carrega:

- `research_only_candidate`
- `not_oos_validated`
- `not_reviewed_by_operator`
- `not_gap_free_soak_validated`
- `not_bound_to_runtime_contract`
- `not_approved_for_ai_shadow_runtime`
- `not_approved_for_freqtrade`
- `not_approved_for_risk_manager`
- `live_canary_blocked`

Esses blockers sao intencionais. Eles impedem promocao silenciosa.

## Por Que Nao Le Dados Reais Por Padrao

Esta branch deve ser deterministica em teste unitario e desacoplada do ambiente
local. Ela nao abre bases reais, nao carrega artefatos runtime e nao depende de
estado externo. O CLI roda sem inputs reais e retorna
`input_mode=no_runtime_rows_loaded`.

## Por Que Nao Libera Operacao

Candidate rules aqui sao apenas pesquisa catalogada. O relatorio sempre retorna:

- `status=blocked`
- `decision=MANTER_EM_RESEARCH`
- `operational_authority=false`
- `application_status=not_applied`
- `promotion_status=blocked`

## Por Que Validacao Fora Da Amostra Fica Para Branch Futura

Um pattern elegivel ainda pode ser fragil, instavel ou especifico demais da
amostra. A validacao fora da amostra precisa de branch propria, contrato proprio
e evidencias separadas. Esta branch apenas prepara candidatos bloqueados.

## IA Shadow Runtime Nao E Alterada

O payload inclui:

- `applies_to_ai_shadow_runtime=false`
- `updates_ai_shadow_runtime=false`
- `applies_shadow_rules=false`
- `promotes_shadow_rules=false`

Nao ha escrita em runtime, nao ha aplicacao de filtro e nao ha integracao com o
produtor de sinais.

## Safety Flags

Flags preservadas:

- `research_only=true`
- `paper_only=true`
- `shadow_only=true`
- `read_only=true`
- `operational_authority=false`
- `can_apply_to_freqtrade=false`
- `can_apply_to_risk_manager=false`
- `can_promote_rules=false`
- `can_promote_model=false`
- `live_trading_enabled=false`
- `canary_release_allowed=false`
- `live_release_allowed=false`
- `order_submission_enabled=false`
- `real_order_submission_enabled=false`
- `exchange_private_access=false`
- `sends_orders=false`
- `changes_risk=false`
- `changes_model=false`
- `updates_freqtrade=false`
- `updates_risk_manager=false`
- `updates_qlib_runtime=false`
- `updates_ai_shadow_runtime=false`
- `writes_runtime=false`
- `writes_data=false`
- `writes_sqlite=false`
- `writes_parquet=false`
- `runs_training=false`
- `runs_ocr=false`
- `runs_ai_shadow_incremental=false`
- `runs_oos_validation=false`
- `applies_shadow_rules=false`
- `promotes_shadow_rules=false`

## Proximos Passos Permitidos

- Criar validacao fora da amostra em branch futura.
- Criar AI Shadow feedback bridge em branch futura.
- Criar Qlib research dataset em branch futura.
- Criar daily learning orchestrator em branch futura.
- Criar dashboard daily learning command center em branch futura.

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
- Usar candidate registry para liberar operacao.
- Promover regra candidata.
- Aplicar candidate rule.
- Registrar em registry operacional.
- Rodar validacao fora da amostra nesta branch.
- Gerar codigo operacional de veto.

## CLI

Execucao sem escrita:

```powershell
python .\scripts\build_daily_candidate_shadow_rule_registry_v1.py --project-root . --no-write --json
```

Escrita explicita somente fora de escopos runtime/dados:

```powershell
python .\scripts\build_daily_candidate_shadow_rule_registry_v1.py --project-root . --output .\tmp\daily_candidate_shadow_rule_registry_v1.json --json
```

Parametros:

- `--project-root`
- `--json`
- `--no-write`
- `--output`
- `--min-support-count`
- `--min-confidence`
- `--min-lift`

## Validacao

```powershell
python -m compileall scripts smartcrypto tests
python -m pytest .\tests\test_daily_candidate_shadow_rule_registry_v1.py -q
python .\scripts\build_daily_candidate_shadow_rule_registry_v1.py --project-root . --no-write --json
python .\scripts\generate_project_manifest.py
python .\scripts\generate_project_manifest.py --check
python .\scripts\scan_versioned_secrets.py --project-root . --json
python -m pip_audit -r ".\requirements-dev.lock" --progress-spinner off
git diff --check
git status -sb
git status --short
```
