# AI Shadow Online Feedback Learning Loop V1

## Objetivo

Esta camada consolida evidencias ja produzidas pelas pesquisas supervisionadas,
pelo registro shadow e pelas auditorias da IA Shadow. Ela materializa um relatorio
e uma trilha de eventos para analise posterior, sem executar treinamento e sem
alterar qualquer componente operacional.

O nome *learning loop* descreve o ciclo de observacao e registro. Nesta versao,
`learning_action` e sempre `record_only`, `training_allowed` e falso e
`promotion_status` permanece `blocked`.

## Fronteira operacional

O agregador e offline e read-only em relacao as fontes. Ele:

- nao importa nem chama `train_ai_shadow_incremental_model.py`;
- nao importa modulos `smartcrypto/ml/ai_shadow_*`;
- nao le nem escreve SQLite;
- nao registra, promove ou substitui modelos;
- nao altera Qlib, Freqtrade, RiskManager, sinais ou datasets;
- nao acessa exchange publica ou privada;
- nao envia ordens;
- nao executa subprocessos.

As unicas escritas permitidas, quando `--write` e informado, sao:

- `data/reports/ai_shadow_online_feedback_learning_loop_report.json`;
- `data/reports/ai_shadow_online_feedback_learning_loop_events.jsonl`.

Esses arquivos sao artefatos runtime ignorados pelo Git.

## Fontes

Por padrao, o agregador procura:

| Evidencia | Caminho |
| --- | --- |
| Branch 04, treino supervisionado | `data/reports/qlib_ocr_v11_supervised_training_summary.json` |
| Branch 05, pack executivo | `data/reports/training_reports/smart_futuros_training_executive_pack.json` |
| Branch 06, relatorio do candidato | `data/reports/qlib_ocr_v11_shadow_model_candidate_registry_report.json` |
| Branch 06, registry research | `data/models/qlib_ocr_v11/research/qlib_ocr_v11_shadow_candidate_registry.json` |
| Attribution de outcomes | `data/reports/ai_shadow_outcome_attribution_report.json` |
| Threshold financeiro | `data/reports/ai_shadow_financial_threshold_evaluation_report.json` |
| Readiness de threshold | `data/reports/ai_shadow_threshold_readiness_report.json` |
| Drift monitor | `data/reports/ai_shadow_drift_monitor_report.json` |
| Decision logger | `data/reports/ai_shadow_model_decision_logger_report.json` |
| Outcome tracker | `data/reports/ai_shadow_outcome_tracker_report.json` |
| Trainer incremental | `data/reports/ai_shadow_incremental_trainer_report.json` |

Todos os caminhos podem ser sobrescritos por argumentos CLI. Fontes ausentes ou
invalidas sao registradas em `missing_sources`, `warnings` e `load_errors`; elas
nunca sao convertidas silenciosamente em evidencia valida.

## Gate conservador

O gate registra bloqueios observados nas fontes, incluindo:

- decisoes anteriores de manter a pesquisa;
- resultado selecionado que nao supera o baseline de todos os testes;
- candidato sem elegibilidade ou com promocao bloqueada;
- trainer incremental ainda pendente;
- evidencias de readiness, logging ou tracking ausentes;
- qualquer safety flag incompatível;
- proibicao estrutural de treinamento nesta camada.

Com as evidencias correntes, o contrato esperado e:

```text
status=warning
loop_status=research_feedback_only
learning_action=record_only
decision=MANTER_EM_RESEARCH
promotion_status=blocked
reason=feedback_recorded_without_training
```

`--strict` torna o status principal `blocked`, mas nao muda nem executa acoes.
Uma fonte critica ausente tambem bloqueia o status principal.

## Eventos

Cada evidencia disponivel gera um evento `record_only`. O ID usa hash SHA-256 do
tipo, fonte e conteudo canonico da evidencia; portanto, repetir a mesma execucao
nao duplica eventos. Uma mudanca real na evidencia cria um novo evento.

A escrita do JSON e do JSONL e atomica. Um JSONL existente e validado antes de
ser regravado; estrutura invalida bloqueia o CLI com erro controlado.

## Uso

Validacao em memoria, default seguro:

```powershell
python .\scripts\build_ai_shadow_online_feedback_learning_loop.py --project-root . --no-write --json
```

Materializacao dos dois artefatos runtime:

```powershell
python .\scripts\build_ai_shadow_online_feedback_learning_loop.py --project-root . --write --json
```

Gate estrito, ainda sem escrita:

```powershell
python .\scripts\build_ai_shadow_online_feedback_learning_loop.py --project-root . --strict --no-write --json
```

## Garantias de seguranca

O relatorio preserva explicitamente `paper_only=true`, `shadow_only=true` e
mantem live, canary, envio de ordens, acesso privado, mudanca de risco, mudanca de
modelo, treinamento, registro de modelo e alteracoes de runtime como `false`.

Este relatorio e evidencia consultiva de pesquisa. Ele nao autoriza promocao,
nao remove blockers e nao substitui readiness gates operacionais.

## Validacao

```powershell
python -m compileall scripts smartcrypto tests
python -m pytest .\tests\test_ai_shadow_online_feedback_learning_loop_v1.py -q
python .\scripts\generate_project_manifest.py --check
python .\scripts\scan_versioned_secrets.py --project-root . --json
```
