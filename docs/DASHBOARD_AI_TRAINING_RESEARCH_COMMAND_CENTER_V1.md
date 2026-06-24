# Dashboard AI Training Research Command Center V1

## Objetivo

O AI Training Research Command Center consolida, em uma unica secao read-only,
as evidencias de pesquisa produzidas pelas Branches 01 a 08. Ele torna visivel o
estado do dataset OCR, simulacoes quantitativas, walk-forward, treinamento Qlib,
governanca de candidato, feedback IA Shadow e observabilidade Freqtrade paper.

O Command Center nao executa nenhuma dessas etapas. Ele apenas normaliza JSONs
existentes durante a construcao do snapshot do dashboard.

## Localizacao

A secao fica na pagina existente `05_ai_governance.py` e no snapshot
`dashboard_ai_governance_snapshot.json`.

Nao foi criada uma nona pagina. O contrato semantico institucional continua com
exatamente oito paginas Streamlit.

## Fontes JSON

As oito fontes sao opcionais e read-only:

1. `data/reports/ocr_v11_research_dataset_audit.json`;
2. `data/reports/ocr_v11_tp_sl_grid_summary.json`;
3. `data/reports/ocr_v11_walkforward_montecarlo_summary.json`;
4. `data/reports/qlib_ocr_v11_supervised_training_summary.json`;
5. `data/reports/training_reports/smart_futuros_training_executive_pack.json`;
6. `data/reports/qlib_ocr_v11_shadow_model_candidate_registry_report.json`;
7. `data/reports/ai_shadow_online_feedback_learning_loop_report.json`;
8. `data/reports/freqtrade_paper_ai_selector_integration_report.json`.

Os paths corretos das Branches 01 e 02 sao, respectivamente,
`ocr_v11_research_dataset_audit.json` e `ocr_v11_tp_sl_grid_summary.json`. Os
nomes `ocr_v11_research_dataset_summary.json` e
`ocr_v11_tp_sl_grid_simulator_summary.json` nao fazem parte do contrato.

Parquets, XLSX, SQLite e artefatos `.joblib` nao sao fontes desta secao. Nenhum
modelo e desserializado.

## Contrato snapshot-first

O normalizador recebe o mapping de payloads ja carregado pelo snapshot builder.
Ele acessa cada fonte por `source_key` especifica e nunca faz merge generico dos
schemas das oito branches.

O componente Streamlit recebe somente o snapshot materializado. Ele nao abre
arquivos, nao consulta banco e nao chama scripts.

Fontes ausentes produzem `MISSING_OPTIONAL`; JSON invalido permanece ausente na
secao e e tratado pela camada de carregamento existente. Ausencia nunca e
convertida em sucesso.

## Advisory-only

A estrutura normalizada fixa:

```text
research_gate_status=BLOCKED
decision=MANTER_EM_RESEARCH
authority=advisory_only
operational_authority=false
section_status=WARNING ou MISSING_OPTIONAL
```

O status autoritativo do snapshot e calculado antes da secao advisory ser
anexada. Assim, o estado de pesquisa nao altera:

- `global_blocking_reasons`;
- `runtime_evidence_blocking_reasons`;
- `combined_blocking_reasons`;
- readiness;
- permissoes de live/canary;
- autoridade do RiskManager.

Os blockers permanecem visiveis dentro da secao para interpretacao humana, mas
nao se tornam controles operacionais.

## Branch cards

A secao apresenta oito cards:

- dataset alignment;
- TP/SL grid;
- walk-forward e Monte Carlo;
- treinamento supervisionado Qlib;
- pack executivo;
- candidate registry;
- feedback loop;
- Freqtrade paper selector.

Cada card preserva status, decisao, metrica principal, metricas de apoio, razao,
source key/path e `advisory_only=true`.

## Safety flags

O payload mantem `paper_only=true` e `shadow_only=true`. Permanecem falsos:

- autoridade operacional;
- live e envio de ordens;
- acesso privado a exchange;
- mudanca de risco ou modelo;
- atualizacao Freqtrade, Qlib, RiskManager ou IA Shadow runtime;
- registro/promocao de modelo;
- production enablement.

## Riscos e mitigacoes

1. **Blocker research contaminar readiness:** a secao e anexada depois do
   calculo autoritativo e nao participa das listas globais.
2. **Colisao entre campos de oito schemas:** cada payload e acessado por source
   key e normalizado isoladamente.
3. **Carga ou execucao de artefatos:** somente JSON e consumido; nao ha Parquet,
   joblib, SQLite, subprocesso, rede ou import de runtime operacional.

## Validacao

```powershell
python -m compileall scripts smartcrypto tests
python -m pytest .\tests\test_dashboard_ai_training_research_command_center_v1.py -q
python -m pytest .\tests\test_dashboard_*.py -q
python .\scripts\audit_dashboard_semantic_coverage_v2.py --project-root . --json
python .\scripts\generate_project_manifest.py --check
python .\scripts\scan_versioned_secrets.py --project-root . --json
python -m pip_audit -r .\requirements-dev.lock --progress-spinner off
```

Para validar o snapshot builder sem alterar `data/reports`, use sempre um
`--output-dir` temporario e remova-o ao final.
