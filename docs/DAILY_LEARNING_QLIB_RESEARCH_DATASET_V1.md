# DAILY LEARNING QLIB RESEARCH DATASET V1

## Objetivo

Esta branch cria o contrato de dataset research-only para Qlib a partir da esteira Daily Learning. O artefato é produzido somente em memória por padrão e serve para organizar linhas, features pré-entrada, labels e metadados de pesquisa.

O resultado permanece bloqueado para operação: `status=blocked`, `decision=MANTER_EM_RESEARCH`, `research_only=true`, `read_only=true` e `operational_authority=false`.

## Relação com as branches anteriores

A branch consome conceitualmente os artefatos das Branches 01 a 11:

- divergência Paper/Master;
- contratos e loaders read-only;
- KPI pack;
- alinhamento temporal;
- candle coverage e entry features;
- catálogo de mistakes/winners;
- pattern mining research;
- registry research-only de candidate shadow rules;
- validação OOS research-only;
- feedback bridge research-only para IA Shadow.

Nada disso é carregado de fonte real por padrão nesta branch. Os testes usam entradas em memória.

## Separação entre features e labels

O módulo materializa colunas com prefixos explícitos:

- `feature_*`: somente atributos pré-entrada quando fornecidos;
- `label_*`: classificações de pesquisa derivadas do catálogo;
- metadados: `row_id`, `trade_id`, `symbol`, `side`, `entry_time`, status e flags.

O campo de resultado financeiro não é usado como feature. Labels podem representar classificação de pesquisa, mas não são misturados com `feature_columns`.

## Escopo permitido

- construir dataset em memória;
- separar features, labels e metadados;
- gerar sumário de linhas, símbolos, lados e cobertura de features;
- incluir contexto agregado de OOS, feedback e candidate rules somente como resumo de pesquisa;
- executar CLI no-write por padrão.

## Ações proibidas

- alterar Freqtrade;
- alterar RiskManager;
- alterar Qlib runtime;
- alterar IA Shadow runtime;
- alterar modelos;
- alterar datasets operacionais;
- treinar modelo nesta branch;
- promover modelo;
- promover regra candidata;
- usar outcome como feature;
- habilitar live ou canary;
- enviar ordem real;
- usar exchange privada;
- escrever artefatos em `data/`, `runtime/`, `reports/`, `logs/` ou `freqtrade/`.

## CLI

```powershell
python .\scripts\build_daily_learning_qlib_research_dataset_v1.py --project-root . --no-write --json
```

A CLI não possui flags para carregar fonte real. Se `--output` for usado sem `--no-write`, a escrita é permitida somente fora das áreas bloqueadas do projeto.

## Validação

```powershell
python -m compileall scripts smartcrypto tests
python -m pytest tests\test_daily_learning_qlib_research_dataset_v1.py -q
python .\scripts\build_daily_learning_qlib_research_dataset_v1.py --project-root . --no-write --json
python .\scripts\generate_project_manifest.py
python .\scripts\generate_project_manifest.py --check
python .\scripts\scan_versioned_secrets.py --project-root . --json
python -m pip_audit -r ".\requirements-dev.lock" --progress-spinner off
git diff --check
git status -sb
git status --short
```

## Próximos passos

- daily learning orchestrator;
- scheduler paper;
- dashboard daily learning command center;
- evidence readiness integration;
- treinamento Qlib research-only em branch futura.
