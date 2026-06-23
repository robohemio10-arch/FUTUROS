# Training Executive Report Pack V1

## Objetivo

Esta branch consolida as evidências das quatro etapas de pesquisa OCR V1.1 em
um pacote único para sócios, investidores e revisão técnica. O pacote resume
resultados positivos e negativos sem alterar modelo, estratégia, risco ou
runtime.

A decisão institucional esperada é `MANTER_EM_RESEARCH`. O documento não é um
gate de promoção e não autoriza registry produtivo, integração com Freqtrade,
live, canary ou envio de ordens.

## Entradas

O coletor procura relatórios estruturados na seguinte ordem:

| Branch | Fonte primária | Fallback |
|---|---|---|
| 01 | `data/reports/ocr_v11_research_dataset_summary.json` | `data/reports/training_reports/ocr_v11_research_dataset_summary.json` e Markdown executivo |
| 02 | `data/reports/ocr_v11_tp_sl_grid_summary.json` | summary e Markdown de TP/SL em `training_reports` |
| 03 | `data/reports/ocr_v11_walkforward_montecarlo_summary.json` | summary e Markdown walk-forward/Monte Carlo |
| 04 | `data/reports/qlib_ocr_v11_supervised_training_summary.json` | summary e Markdown supervisionado |

JSON é carregado como UTF-8 com ou sem BOM e deve possuir objeto no nível raiz.
Uma fonte inválida ou ausente é registrada em `missing_sources`, `warnings` e
`source_manifest`. O pack não inventa métricas para branches sem evidência.

## Saídas runtime

Somente `--write` materializa:

- `data/reports/training_reports/smart_futuros_training_executive_pack.json`
- `data/reports/training_reports/smart_futuros_training_executive_pack.md`
- `data/reports/training_reports/smart_futuros_training_executive_pack.html`

As escritas são atômicas. Todos os caminhos estão sob `data/` e permanecem
ignorados pelo Git.

O HTML é autocontido: usa apenas HTML e CSS inline, sem JavaScript, requests,
assets externos ou dependência adicional.

## Conteúdo executivo

O pacote contém:

1. resumo executivo;
2. decisão, status, métrica e próximo gate por branch;
3. KPIs consolidados de cobertura, PnL, risco e ML;
4. evidências negativas;
5. evidências positivas;
6. gates para registry, IA Shadow, paper selector, dashboard e soak de 30 dias;
7. safety block completo;
8. dados estruturados para gráficos futuros.

As evidências negativas são parte deliberada do relatório. Reprovar TP/SL,
descartar candidato em walk-forward/Monte Carlo e manter um seletor abaixo do
baseline impedem promoção prematura.

## Chart data

O JSON expõe quatro contratos read-only:

- `branch_status_chart`;
- `pnl_comparison_chart`;
- `ml_metrics_chart`;
- `eligibility_chart`.

Esses dados podem ser consumidos posteriormente pelo dashboard. Esta branch não
modifica páginas Streamlit nem introduz regra de negócio na UI.

## CLI

Validação em memória, default seguro:

```powershell
python .\scripts\build_training_executive_report_pack.py `
  --project-root "." `
  --no-write `
  --json
```

Materialização explícita:

```powershell
python .\scripts\build_training_executive_report_pack.py `
  --project-root "." `
  --write `
  --json
```

Os outputs podem ser substituídos com `--output-json`, `--output-md` e
`--output-html`. `--strict` transforma ausência de qualquer branch em
`status=blocked`; isso continua retornando exit code 0 porque é um resultado
controlado de auditoria. Exit code 1 fica reservado a erro estrutural inesperado.

## Interpretação

- `warning`: evidências consolidadas, mas sem base para promoção, ou fontes
  parciais em modo normal.
- `blocked`: fontes incompletas sob `--strict`.
- `MANTER_EM_RESEARCH`: decisão conservadora canônica desta etapa.

O pack é uma camada de comunicação e auditoria. RiskManager, registry, Qlib
runtime, Freqtrade e IA Shadow não consomem este arquivo como autorização.

## Segurança

O JSON, Markdown e HTML registram explicitamente:

- paper/shadow ativos;
- live, canary e release bloqueados;
- order submission e exchange privada bloqueados;
- nenhuma alteração de risco, modelo, Freqtrade, Qlib ou RiskManager;
- nenhum treino, registry, promoção, IA Shadow incremental ou limpeza SQLite.

## Validação

```powershell
python -m compileall scripts smartcrypto tests
python -m pytest .\tests\test_training_executive_report_pack_v1.py -q
python -m pytest .\tests\test_qlib_ocr_v11_supervised_training_lab.py -q
python -m pytest .\tests\test_ocr_v11_walkforward_montecarlo_research.py -q
python -m pytest .\tests\test_ocr_v11_tp_sl_grid_simulator.py -q
python -m pytest .\tests\test_ocr_v11_research_dataset.py -q
python .\scripts\generate_project_manifest.py --check
python .\scripts\scan_versioned_secrets.py --project-root "." --json
python -m pip_audit -r .\requirements-dev.lock --progress-spinner off
```

## Fora de escopo

- treinar ou promover modelo;
- registrar challenger/champion;
- atualizar Qlib runtime;
- alterar estratégia Freqtrade ou RiskManager;
- executar IA Shadow incremental;
- modificar datasets, SQLite ou trades oficiais;
- habilitar live/canary ou enviar ordens.
