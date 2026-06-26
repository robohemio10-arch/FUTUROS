# DAILY_LEARNING_AI_SHADOW_FEEDBACK_BRIDGE_V1

## Objetivo

Esta branch cria a ponte research-only entre Daily Learning e IA Shadow.

A entrega converte resultados de validação OOS e candidate shadow rules em eventos auditáveis de feedback. O payload é exclusivamente `record_only`; nenhum feedback é aplicado em runtime, nenhum threshold é alterado e nenhuma regra é promovida.

## Relação com as branches 01–10

A trilha Daily Learning já construiu, em sequência:

1. closeout Paper/Master;
2. contratos e source map;
3. loaders read-only;
4. KPI pack;
5. divergência e alinhamento;
6. candle coverage e entry features;
7. mistake/winner catalog;
8. pattern mining research-only;
9. candidate shadow rule registry research-only;
10. OOS validation research-only.

Esta branch 11 não substitui nenhuma etapa anterior. Ela apenas empacota os resultados OOS em feedback auditável para pesquisa futura da IA Shadow.

## OOS validation versus feedback bridge

OOS validation mede estabilidade fora da amostra. Feedback bridge traduz essa medição para eventos pesquisáveis.

Um `oos_research_pass` pode gerar um sinal positivo ou negativo de pesquisa, mas continua sem autoridade operacional. O resultado não altera IA Shadow runtime, não muda política, não muda threshold, não aplica candidate rule e não libera live/canary.

## Formato esperado de OOS results

Entrada em memória:

- `candidate_rule_id`
- `target`
- `rule_kind`
- `conditions`
- `oos_status`
- `out_of_sample_confidence`
- métricas in-sample e out-of-sample
- `promotion_status="blocked"`
- `application_status="not_applied"`
- `operational_action_allowed=false`
- `promotion_allowed=false`

## Formato esperado de candidate rules

Entrada em memória:

- `candidate_rule_id`
- `target`
- `conditions`
- `rule_kind`
- `candidate_status="research_candidate"`
- `registry_status="registered_research_only"`
- `promotion_status="blocked"`
- `application_status="not_applied"`

## Tipos de feedback

- `candidate_positive_signal`: OOS passou para `allow_candidate`.
- `candidate_negative_signal`: OOS passou para `block_candidate`.
- `needs_review`: OOS falhou.
- `insufficient_evidence`: suporte OOS insuficiente ou ausência de OOS.
- `observe_only`: caso residual sem autoridade operacional.

Todos os eventos têm:

- `feedback_status="record_only"`
- `feedback_application_status="not_applied"`
- `review_required=true`
- `operational_action_allowed=false`
- `promotion_allowed=false`

## Por que record-only não altera runtime

O payload é criado apenas em memória por padrão. A CLI não lê fontes reais e não escreve outputs de runtime. Mesmo quando `--output` é usado, paths sensíveis são bloqueados.

## Banco local da IA Shadow

Esta branch não escreve no banco local da IA Shadow. O campo `ai_shadow_sqlite_write_allowed=false` é uma trava explícita de contrato.

## Por que a branch não lê dados reais por padrão

A função principal aceita listas em memória. Quando nenhuma entrada é passada, retorna `input_mode="no_runtime_rows_loaded"`. Não há flags de CLI para carregar OOS, candidates, catalog, features ou trades reais.

## Por que feedback bridge não libera operação

O report mantém:

- `status="blocked"`
- `decision="MANTER_EM_RESEARCH"`
- `operational_authority=false`
- `applies_feedback_to_ai_shadow=false`
- `updates_ai_shadow_runtime=false`
- `updates_ai_shadow_thresholds=false`
- `updates_ai_shadow_policy=false`
- `promotes_shadow_rules=false`

## Safety flags

A branch preserva:

- `research_only=true`
- `paper_only=true`
- `shadow_only=true`
- `read_only=true`
- `live_release_allowed=false`
- `canary_release_allowed=false`
- `order_submission_enabled=false`
- `real_order_submission_enabled=false`
- `exchange_private_access=false`
- `changes_risk=false`
- `changes_model=false`

## Próximos passos permitidos

- criar Qlib research dataset em branch futura;
- criar daily learning orchestrator em branch futura;
- criar scheduler paper em branch futura;
- criar dashboard daily learning command center em branch futura;
- criar evidence readiness integration em branch futura.

## Ações proibidas

- alterar Freqtrade;
- alterar RiskManager;
- alterar Qlib runtime;
- alterar IA Shadow runtime;
- alterar modelos;
- alterar datasets;
- habilitar live;
- habilitar canary;
- enviar ordem real;
- usar exchange privada;
- escrever artefatos em diretórios runtime/data/logs/freqtrade;
- usar feedback bridge para liberar operação;
- promover regra candidata;
- aplicar candidate rule;
- registrar em registry operacional;
- alterar IA Shadow runtime com feedback;
- escrever banco local IA Shadow;
- atualizar threshold IA Shadow;
- gerar código operacional de veto.

## Validação

```powershell
cd "E:\FUTUROS"

python -m compileall scripts smartcrypto tests

python -m pytest tests\test_daily_learning_ai_shadow_feedback_bridge_v1.py -q

python .\scripts\build_daily_learning_ai_shadow_feedback_bridge_v1.py --project-root . --no-write --json

python .\scripts\generate_project_manifest.py
python .\scripts\generate_project_manifest.py --check
python .\scripts\scan_versioned_secrets.py --project-root . --json
python -m pip_audit -r ".\requirements-dev.lock" --progress-spinner off

git diff --check
git status -sb
git status --short
```

## Critérios de aceite

- teste focado passando;
- CLI retorna `status=blocked`;
- CLI retorna `decision=MANTER_EM_RESEARCH`;
- CLI retorna `input_mode=no_runtime_rows_loaded`;
- `write_performed=false`;
- nenhum feedback aplicado;
- nenhum threshold alterado;
- nenhuma IA Shadow runtime alterada;
- nenhum safety flag relaxado;
- manifest current;
- secret scan limpo;
- worktree com apenas os arquivos esperados antes do commit.
