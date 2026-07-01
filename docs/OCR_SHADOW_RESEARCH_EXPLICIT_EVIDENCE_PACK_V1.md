# OCR Shadow Research Explicit Evidence Pack V1

## Objetivo

Este pacote materializa evidencias explicitas do ciclo OCR Shadow Research sem transformar evidência em autorização operacional.

Fluxo consolidado:

```text
OOS Validation
-> Shadow Observation Design
-> Shadow Observation Replay
-> Paper Closed Trades Attribution
-> Paper Shadow Observation Readiness Gate
-> OCR Shadow Research Closeout
-> Evidence Pack consolidado
```

## Decisao institucional

O resultado permanece:

```text
decision=MANTER_EM_RESEARCH
evidence_pack_decision=MANTER_EM_RESEARCH
paper_observation_allowed=false
ready_for_shadow_observation=false
operational_authority=false
can_promote_rules=false
can_apply_to_freqtrade=false
can_apply_to_risk_manager=false
sends_orders=false
changes_risk=false
writes_runtime=false
```

Mesmo quando os builders sao executados e os relatórios sao materializados, o pacote nao libera observer, nao promove survivor e nao altera runtime.

## Comportamento default

Por padrao, o script:

- nao le runtime real;
- nao executa builders;
- nao escreve relatorios;
- retorna `status=blocked`;
- retorna `input_mode=no_runtime_rows_loaded`.

Comando default:

```powershell
python .\scripts\build_ocr_shadow_research_explicit_evidence_pack_v1.py --project-root . --no-write --json
```

## Execucao controlada

Para executar a allowlist fixa de builders, e necessario passar explicitamente:

- `--allow-runtime-read`
- `--execute-builders`
- `--write`

Exemplo:

```powershell
python .\scripts\build_ocr_shadow_research_explicit_evidence_pack_v1.py `
  --project-root . `
  --allow-runtime-read `
  --execute-builders `
  --write `
  --json
```

O script usa `subprocess.run([...], shell=False)` com timeout por etapa. Ele nao aceita script arbitrario; `--stage` permite apenas IDs da allowlist.

## Allowlist de builders

Builders permitidos:

- `scripts/build_ocr_master_candle_positive_rule_oos_validation_v1.py`
- `scripts/build_ocr_master_candle_shadow_observation_design_v1.py`
- `scripts/build_ocr_master_candle_shadow_observation_replay_v1.py`
- `scripts/build_paper_closed_trades_shadow_rule_attribution_v1.py`
- `scripts/build_paper_shadow_observation_readiness_gate_v1.py`
- `scripts/build_ocr_shadow_research_evidence_closeout_v1.py`

Qualquer stage fora da allowlist retorna `blocked` com `reason=unknown_stage_not_in_allowlist`.

## Saidas

Com `--write`, o pack escreve apenas:

```text
data/reports/ocr_shadow_research_explicit_evidence_pack_v1.json
data/reports/ocr_shadow_research_explicit_evidence_pack_v1.md
```

Os builders filhos tambem sao chamados somente pelos comandos fixos permitidos e continuam restritos a relatorios research-only em `data/reports`.

## Campos principais

O JSON expoe:

- `stage_results`
- `evidence_artifacts`
- `closeout_summary`
- `readiness_summary`
- `recommended_next_action`
- `forbidden_next_actions`
- `gate_summary`
- `safety_flags`

Cada stage registra:

- `returncode`
- `status`
- `reason`
- `output_path`
- `sha256`
- `shell=false`
- safety flags research-only.

## Garantias de seguranca

- Paper/shadow only.
- Sem live trading.
- Sem ordens.
- Sem acesso a exchange privada.
- Sem alteracao de Freqtrade, RiskManager, Qlib runtime ou IA Shadow runtime.
- Sem alteracao de modelos, registry, sinais, datasets oficiais, SQLite ou configs.
- Sem `shell=True`.
- Sem script arbitrario.
- Sem escrita em `data/runtime`, SQLite ou Parquet operacional.

## Validacao

```powershell
python -m compileall scripts smartcrypto tests
python -m pytest tests\test_ocr_shadow_research_explicit_evidence_pack_v1.py -q
python .\scripts\build_ocr_shadow_research_explicit_evidence_pack_v1.py --project-root . --no-write --json
python .\scripts\generate_project_manifest.py --check
python .\scripts\scan_versioned_secrets.py --project-root . --json
python -m pip_audit -r ".\requirements-dev.lock" --progress-spinner off
git diff --check
git status --short
```
