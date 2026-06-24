# AI Research Training Track Closeout Handover V1

## Objetivo

Esta entrega encerra de forma auditável a trilha de pesquisa 01-09 da Master OCR
V1.1. Ela consolida evidências técnicas e executivas já existentes, sem executar
OCR, treinamento, Qlib runtime, Freqtrade, IA Shadow incremental ou qualquer
operação de mercado.

O resultado institucional é fixo e conservador:

- `track_status=closed_research_only`
- `decision=MANTER_EM_RESEARCH`
- `research_gate_status=BLOCKED`
- `promotion_status=blocked`
- `operational_authority=false`

## Escopo

O módulo lê somente nove relatórios JSON opcionais e produz uma visão consolidada
das etapas de dataset, TP/SL, walk-forward/Monte Carlo, treinamento Qlib de
pesquisa, pack executivo, registry challenger, feedback offline, observabilidade
do seletor paper e command center do dashboard.

Não são lidos Parquet, modelos Joblib, SQLite, XLSX ou CSV. As fontes são
read-only; ausência ou JSON inválido vira `MISSING_OPTIONAL` e mantém a promoção
bloqueada.

## Fontes canônicas

1. `data/reports/ocr_v11_research_dataset_audit.json`
2. `data/reports/ocr_v11_tp_sl_grid_summary.json`
3. `data/reports/ocr_v11_walkforward_montecarlo_summary.json`
4. `data/reports/qlib_ocr_v11_supervised_training_summary.json`
5. `data/reports/training_reports/smart_futuros_training_executive_pack.json`
6. `data/reports/qlib_ocr_v11_shadow_model_candidate_registry_report.json`
7. `data/reports/ai_shadow_online_feedback_learning_loop_report.json`
8. `data/reports/freqtrade_paper_ai_selector_integration_report.json`
9. `data/reports/dashboard_ai_governance_snapshot.json`

## Decisão e promoção

A trilha fecha como research-only porque walk-forward/Monte Carlo, métricas fora
da amostra e gates de governança não justificam promoção. O relatório não registra
modelo, não modifica registry e não atribui autoridade operacional ao seletor.

A conclusão não altera RiskManager, Freqtrade, Qlib runtime, IA Shadow runtime ou
o dashboard. A evidência da Branch 09 permanece consultiva e separada dos gates
autoritativos.

## Próximos gates

O avanço seguinte depende de evidência operacional atual e completa, readiness
aprovado sem atalhos de pesquisa, soak paper/shadow suficiente, freshness saudável
e revisão institucional após nova evidência fora da amostra. Esses gates não
significam autorização automática para promoção ou live.

## Execução

O default efetivo é no-write:

```powershell
python .\scripts\build_ai_research_training_track_closeout_handover.py --project-root . --no-write --json
```

Materialização explícita:

```powershell
python .\scripts\build_ai_research_training_track_closeout_handover.py --project-root . --write --json
```

Somente `--write` pode criar:

- `data/reports/ai_research_training_track_closeout_handover_summary.json`
- `data/reports/training_reports/ai_research_training_track_closeout_handover.md`

Esses arquivos são artefatos runtime ignorados pelo Git.

## Safety flags

O payload declara `paper_only=true` e `shadow_only=true`. Live, canary, submissão
de ordens, acesso privado à exchange, mudanças de risco/modelo, treinamento, OCR,
rebuild, atualização de Freqtrade/Qlib/IA Shadow, registro e promoção de modelo
permanecem `false`.

## Validação

```powershell
python -m compileall scripts smartcrypto tests
python -m pytest .\tests\test_ai_research_training_track_closeout_handover_v1.py -q
python .\scripts\generate_project_manifest.py --check
python .\scripts\scan_versioned_secrets.py --project-root . --json
python -m pip_audit -r .\requirements-dev.lock --progress-spinner off
git diff --check
```

## Riscos e mitigação

- Fonte ausente ou inválida: classificada como `MISSING_OPTIONAL`; nunca promove
  o estado para sucesso operacional.
- Evidência insegura: flags verdadeiras de live, ordens, risco ou promoção são
  registradas como blockers.
- Escrita concorrente: os dois outputs usam substituição atômica e só existem
  quando `--write` é fornecido explicitamente.
