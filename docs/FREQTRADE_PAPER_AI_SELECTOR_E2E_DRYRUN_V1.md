# Freqtrade Paper AI Selector E2E Dry-Run V1

## Objetivo

Esta branch cria uma validação end-to-end de dry-run para o caminho:

```text
paper_ai_signal_candidate_producer_v1.json
-> selector dry-run adapter
-> simulated selector decision report
-> safety/readiness blocked
```

O pacote materializa apenas evidência de pesquisa. Ele não conecta o seletor ao
bot paper em execução, não escreve sinais ativos e não altera Freqtrade,
RiskManager, Qlib runtime, IA Shadow runtime, modelos, registry ou configs.

## Dependências

O dry-run depende da Branch 59 e da Branch 60:

- Branch 59: `paper_model_candidate_registry_gate_v1`
- Branch 60: `paper_ai_signal_candidate_producer_v1`

No estado atual esperado, a Branch 60 permanece bloqueada porque o registry gate
não possui candidatos elegíveis. Consequentemente este dry-run também deve
permanecer bloqueado com `reason=no_actionable_signal_candidates`.

## Diferença entre dry-run e sinal operacional

`selector_decision` é uma simulação auditável. Ela descreve se um candidato
seria rejeitado ou apenas observado em dry-run. Ela não é um payload consumível
pelo Freqtrade e nunca deve ser tratada como autorização para trade.

O relatório preserva:

- `decision=MANTER_EM_RESEARCH`
- `paper_selector_runtime_enabled=false`
- `freqtrade_strategy_changed=false`
- `freqtrade_config_changed=false`
- `active_signal_file_written=false`
- `writes_active_freqtrade_signals=false`
- `writes_signal_file=false`
- `sends_orders=false`
- `exchange_private_access=false`

## Fontes lidas

Fonte obrigatória:

- `data/reports/paper_ai_signal_candidate_producer_v1.json`

Fontes opcionais:

- `data/reports/paper_model_candidate_registry_gate_v1.json`
- `data/reports/freqtrade_paper_ai_selector_integration_v1.json`
- `data/reports/runtime_safety_audit_config.json`
- `data/reports/readiness_snapshot_v2.json`

Fontes opcionais ausentes viram warnings estruturados. A fonte principal ausente
gera `status=blocked` e `reason=missing_signal_candidate_report`.

## Escrita

O default da CLI é no-write. Com `--write`, a CLI escreve somente:

- `data/reports/freqtrade_paper_ai_selector_e2e_dryrun_v1.json`
- `data/reports/freqtrade_paper_ai_selector_e2e_dryrun_v1.md`

Nenhum arquivo operacional é escrito. Em particular, a branch não cria
`active_freqtrade_signals.json`, não toca `freqtrade/user_data`, não escreve
SQLite, Parquet, modelo, registry ou runtime.

## Execução

```powershell
python .\scripts\build_freqtrade_paper_ai_selector_e2e_dryrun_v1.py --project-root . --json
python .\scripts\build_freqtrade_paper_ai_selector_e2e_dryrun_v1.py --project-root . --write --json
```

## Resultado esperado atual

Enquanto a Branch 60 tiver `actionable_signal_candidate_count=0`, o resultado
esperado é:

```text
status=blocked
reason=no_actionable_signal_candidates
decision=MANTER_EM_RESEARCH
selector_dryrun_status=blocked_no_actionable_candidates
selected_signal_count=0
active_signal_file_written=false
sends_orders=false
```

## Validações

```powershell
python -m compileall -q scripts smartcrypto tests
python -m pytest .\tests\test_freqtrade_paper_ai_selector_e2e_dryrun_v1.py -q
python .\scripts\build_freqtrade_paper_ai_selector_e2e_dryrun_v1.py --project-root . --json
python .\scripts\build_freqtrade_paper_ai_selector_e2e_dryrun_v1.py --project-root . --write --json
python .\scripts\audit_state_execution_ledger_boundary.py --project-root . --json
python .\scripts\audit_operational_exception_swallowing.py --project-root . --json
python .\scripts\generate_project_manifest.py --check
python .\scripts\scan_versioned_secrets.py --project-root . --json
python -m pip_audit -r .\requirements-dev.lock --progress-spinner off
python -m pip_audit -r .\requirements-qlib.lock --progress-spinner off
```

## Próximo passo

A Branch 62 pode usar esta evidência para decidir se existe base suficiente para
um adapter paper selector separado. Esta branch não autoriza live, canary,
paper runtime real, ordens, alteração de risco ou promoção de modelo.
