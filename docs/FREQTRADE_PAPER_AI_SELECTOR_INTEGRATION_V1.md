# Freqtrade Paper AI Selector Integration V1

## Objetivo

Esta camada responde como o candidato de IA seria classificado diante da
superficie Freqtrade paper, sem conceder autoridade operacional. A integracao e
um gate de observabilidade: le contratos existentes, consolida blockers e gera
recomendacoes `record_only`.

O resultado esperado enquanto as Branches 04 a 07 mantiverem o candidato em
pesquisa e:

```text
status=warning
selector_status=observe_only_blocked
selector_authority=none
decision=MANTER_EM_RESEARCH
freqtrade_integration_status=paper_observability_only
```

## Inputs

Por padrao, o modulo le:

- `freqtrade/user_data/config.paper.json`;
- `freqtrade/user_data/strategies/SmartCryptoSignalStrategy.py`;
- `data/reports/qlib_ocr_v11_supervised_training_summary.json`;
- `data/reports/training_reports/smart_futuros_training_executive_pack.json`;
- `data/reports/qlib_ocr_v11_shadow_model_candidate_registry_report.json`;
- `data/reports/ai_shadow_online_feedback_learning_loop_report.json`.

A configuracao Freqtrade e convertida em snapshot sanitizado. Chaves, secrets,
senhas e tokens nao sao reproduzidos. A strategy e analisada estaticamente por
AST e hash; ela nao e importada nem executada.

## Outputs runtime

Somente `--write` pode materializar:

- `data/reports/freqtrade_paper_ai_selector_integration_report.json`;
- `data/reports/freqtrade_paper_ai_selector_observations.jsonl`.

Os dois caminhos pertencem a `data/`, sao ignorados pelo Git e nao devem ser
versionados. O JSONL usa `observation_id` estavel e nao duplica uma observacao
quando as evidencias permanecem iguais.

## Limites operacionais

Esta branch nao altera `SmartCryptoSignalStrategy.py`, `config.paper.json`,
RiskManager, OrderManager, sinais, Qlib, modelos ou registries. Tambem nao:

- importa Freqtrade, CCXT, joblib, pickle ou modulos operacionais SmartCrypto;
- usa subprocesso, rede, exchange ou SQLite;
- envia ordens;
- altera risco;
- treina ou promove modelo;
- habilita live ou canary.

O RiskManager permanece a autoridade final existente. Este relatorio nao cria
um caminho alternativo de autorizacao.

## Blockers esperados

No estado atual, o gate registra:

- `branch04_kept_in_research`;
- `branch04_selected_not_above_all_test`;
- `branch05_kept_in_research`;
- `branch06_promotion_blocked`;
- `branch06_not_promotion_eligible`;
- `branch07_record_only_feedback`;
- `branch07_training_not_allowed`;
- `branch07_promotion_not_allowed`;
- `paper_ai_selector_scope_forbids_operational_authority`.

Qualquer fonte que declare live, ordens, mudanca de risco, atualizacao de
Freqtrade/Qlib/AI Shadow runtime ou registro de modelo adiciona um blocker de
seguranca e torna o status principal `blocked`.

Arquivos Freqtrade ausentes geram warning no modo normal e bloqueio em
`--strict`. Fontes de pesquisa ausentes sempre bloqueiam. Em todos os casos,
`selector_authority=none` permanece inalterado.

## Observacoes

Os tipos minimos sao:

- `freqtrade_paper_config_observed`;
- `freqtrade_strategy_contract_observed`;
- `branch04_ai_selector_result_observed`;
- `branch05_executive_pack_gate_observed`;
- `branch06_shadow_registry_gate_observed`;
- `branch07_feedback_loop_gate_observed`;
- `paper_ai_selector_gate_blocked`;
- `recommended_operator_next_actions_recorded`.

Cada observacao declara `action_taken=record_only`, safety flags fail-closed e
nenhuma mutacao de runtime.

## CLI

Default seguro, sem escrita:

```powershell
python .\scripts\build_freqtrade_paper_ai_selector_integration.py --project-root . --no-write --json
```

Materializacao explicita dos dois outputs runtime:

```powershell
python .\scripts\build_freqtrade_paper_ai_selector_integration.py --project-root . --write --json
```

Auditoria estrita:

```powershell
python .\scripts\build_freqtrade_paper_ai_selector_integration.py --project-root . --strict --no-write --json
```

## Safety flags

O relatorio fixa `paper_only=true`, `shadow_only=true` e mantem como `false`:
live, canary, envio de ordens, acesso privado, mudanca de risco/modelo,
treinamento, registro/promocao de modelo, atualizacao Freqtrade/Qlib/AI Shadow e
mutacao de sinais paper. `selector_authority` e sempre `none`.

## Validacao

```powershell
python -m compileall scripts smartcrypto tests
python -m pytest .\tests\test_freqtrade_paper_ai_selector_integration_v1.py -q
python .\scripts\generate_project_manifest.py --check
python .\scripts\scan_versioned_secrets.py --project-root . --json
```

Esta evidencia e consultiva. Ela nao remove blockers, nao altera readiness e nao
autoriza uso operacional do seletor.
