# Paper Shadow Survivor Remediation Research V1

## Objetivo

Esta etapa cria uma análise research-only para remediar survivors ruins já
identificados pelo relatório diário de impacto paper shadow. O foco é medir,
de forma descritiva, o efeito de descartar survivors com recomendação
`DISCARD_RESEARCH_ONLY` antes de qualquer observação contínua paper-only.

Esta branch não ativa observer, não aplica veto, não promove regra e não altera
runtime.

## Fontes

Entradas locais esperadas:

- `data/reports/paper_shadow_observation_daily_impact_report_v1.json`
- `data/reports/paper_closed_trades_shadow_rule_attribution_v1.json`
- `data/reports/ocr_master_candle_shadow_observation_replay_v1.json`
- `data/reports/paper_closed_trades_readonly_source_contract_v1.json`, opcional

Por padrão, a CLI não lê fontes runtime. A leitura local precisa de
`--allow-runtime-read`.

## Cenários

O relatório calcula:

- baseline atual vindo do impact report;
- `discard_all_negative_survivors`, simulando descarte research-only de
  survivors com `DISCARD_RESEARCH_ONLY`;
- `keep_only_positive_pnl_subsets`, bloqueado quando não há feature não-outcome
  suficiente para subfiltro;
- `threshold_candidate_review`, apenas como diagnóstico quando campos
  numéricos já existem nas linhas de replay/attribution.

Nenhum cenário cria regra operacional.

## Métricas

O JSON expõe:

- `baseline_summary`
- `remediation_summary`
- `false_positive_reduction`
- `allowed_net_pnl_delta`
- `missed_opportunity_delta`
- `discarded_survivor_count`
- `retained_survivor_count`
- `candidate_subfilters`
- `survivor_remediation_plan`
- `remediation_recommendations`

## Interpretação

`DISCARD_SURVIVOR_RESEARCH_ONLY` significa que descartar o survivor teria
melhorado a métrica pesquisada no histórico fechado. Não significa que o
survivor possa ser bloqueado no runtime, nem que o sistema possa aplicar veto.

`NO_ROBUST_REMEDIATION_FOUND` significa que não há subfiltro defensável com os
campos disponíveis. Nesse caso a evidência deve continuar em pesquisa.

## Comandos

Modo seguro default:

```powershell
python .\scripts\build_paper_shadow_survivor_remediation_research_v1.py --project-root . --no-write --json
```

Modo com leitura local explícita e escrita apenas em `data/reports`:

```powershell
python .\scripts\build_paper_shadow_survivor_remediation_research_v1.py `
  --project-root . `
  --allow-runtime-read `
  --impact-report data/reports/paper_shadow_observation_daily_impact_report_v1.json `
  --paper-attribution-report data/reports/paper_closed_trades_shadow_rule_attribution_v1.json `
  --shadow-replay-report data/reports/ocr_master_candle_shadow_observation_replay_v1.json `
  --write `
  --json
```

Validação focada:

```powershell
python -m compileall scripts smartcrypto tests
python -m pytest tests\test_paper_shadow_survivor_remediation_research_v1.py -q
python .\scripts\generate_project_manifest.py --check
python .\scripts\scan_versioned_secrets.py --project-root . --json
```

## Garantias

O relatório mantém:

- `decision=MANTER_EM_RESEARCH`
- `operational_authority=false`
- `paper_observation_allowed=false`
- `ready_for_shadow_observation=false`
- `can_apply_to_freqtrade=false`
- `can_apply_to_risk_manager=false`
- `can_promote_rules=false`
- `sends_orders=false`
- `changes_risk=false`
- `writes_runtime=false`
- `writes_sqlite=false`
- `writes_parquet=false`

Os únicos outputs permitidos com `--write` são JSON e Markdown research-only em
`data/reports`, que são artefatos runtime ignorados pelo Git.
