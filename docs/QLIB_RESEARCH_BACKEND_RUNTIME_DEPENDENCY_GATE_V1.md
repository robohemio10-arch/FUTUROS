# Qlib Research Backend Runtime Dependency Gate V1

## Objetivo

Este gate audita a disponibilidade do backend Qlib para pesquisa institucional sem iniciar runtime Qlib, sem treinar modelo por padrão e sem alterar artefatos operacionais.

O problema tratado é separar claramente quatro estados:

- `available`: Qlib e módulos mínimos de pesquisa estão disponíveis.
- `unavailable`: Qlib não é importável no ambiente atual.
- `partial`: Qlib é importável, mas versão ou módulos mínimos estão ausentes.
- `blocked`: a auditoria detectou mutação de runtime, path inseguro ou violação de isolamento.

## Escopo

O gate é research/paper-only. Ele apenas inspeciona dependências locais e gera evidência em `data/reports` quando `--write` é informado.

Ele não:

- instala pacotes;
- baixa dependências;
- chama `qlib.init`;
- treina modelo por padrão;
- atualiza runtime Qlib;
- altera registry;
- promove champion/challenger;
- acessa exchange privada;
- envia ordens;
- escreve SQLite, Parquet operacional ou sinais.

## Comandos

Dry-run padrão:

```powershell
python .\scripts\audit_qlib_research_backend_gate_v1.py --project-root . --json
```

Escrita explícita de relatório:

```powershell
python .\scripts\audit_qlib_research_backend_gate_v1.py --project-root . --write --json
```

Uso pelo trainer:

```powershell
python .\scripts\train_qlib_institutional_ranking_challenger_v1.py --project-root . --backend-gate-report .\data\reports\qlib_research_backend_gate_v1.json --json
```

Treino research-only continua exigindo `--train`. Se o gate reportar `unavailable`, `partial` ou `blocked`, `--train` bloqueia com razão controlada, salvo fallback de pesquisa explicitamente permitido para os estados sem violação de isolamento.

## Saídas

Com `--write`, o gate gera:

- `data/reports/qlib_research_backend_gate_v1.json`
- `data/reports/qlib_research_backend_gate_v1.md`

Esses arquivos são artefatos runtime e não devem ser versionados.

## Contrato JSON

O relatório expõe:

- `qlib_backend_status`
- `qlib_importable`
- `qlib_version`
- `qlib_package_path`
- `required_modules`
- `module_probe_results`
- `dependency_contract_hash`
- `runtime_isolation_status`
- `environment_audit_status`
- `backend_capabilities`
- `unsupported_reasons`
- `recommended_installation_notes`
- safety flags paper/shadow-only

## Segurança

O probe usa inspeção estática via `importlib.util.find_spec` e metadata de pacote. A auditoria captura estado de `sys.path`, `cwd` e chaves de ambiente antes/depois para detectar side effects.

Valores de variáveis de ambiente não são serializados; apenas a presença de variáveis relevantes é reportada.

## Integração com o trainer

O trainer institucional Qlib continua compatível quando não há relatório do gate. Se um gate report existir ou for passado por `--backend-gate-report`, ele passa a ser a fonte explícita da disponibilidade do backend.

Nenhum resultado do gate concede autoridade operacional. O gate não altera live/canary, RiskManager, Freqtrade, IA Shadow, sinais ativos, modelos ativos ou registry.
