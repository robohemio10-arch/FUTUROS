# OCR Shadow Research Evidence Closeout V1

## Objetivo

Este closeout consolida o ciclo research-only de OCR Shadow Research:

```text
OOS Validation
-> Shadow Observation Design
-> Shadow Observation Replay
-> Paper Closed Trades Attribution
-> Readiness Gate
-> Closeout tecnico bloqueado
```

O objetivo e produzir handover tecnico com evidencias disponiveis, evidencias ausentes, blockers, decisao final e proximos caminhos seguros.

## Decisao final

O closeout preserva:

```text
decision=MANTER_EM_RESEARCH
closeout_decision=MANTER_EM_RESEARCH
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

Mesmo quando todas as evidencias estao presentes, o ciclo fica encerrado como `research_closed_blocked`. Isso nao libera paper observer, regra, runtime ou modelo.

## Entradas

Por padrao, o CLI nao le runtime real. Sem `--allow-runtime-read`, retorna closeout estruturado com:

```text
input_mode=no_runtime_rows_loaded
```

Com leitura explicita, aceita somente relatorios JSON locais:

- `--oos-validation-report`
- `--shadow-observation-design-report`
- `--shadow-observation-replay-report`
- `--paper-closed-trades-attribution-report`
- `--readiness-gate-report`

Fontes ausentes ou invalidas retornam blockers estruturados, sem exception crua.

## Saidas

Sem `--write`, nada e escrito.

Com `--write`, a escrita e restrita a:

```text
data/reports/ocr_shadow_research_evidence_closeout_v1.json
data/reports/ocr_shadow_research_evidence_closeout_v1.md
```

O closeout nunca escreve `data/runtime`, SQLite, Parquet operacional, modelos, registry, sinais, configs ou estrategias.

## Conteudo do JSON

O relatorio expoe:

- `closeout_status`
- `closeout_decision`
- `cycle_name`
- `evidence_sources_required`
- `evidence_sources_present`
- `evidence_sources_missing`
- `evidence_summary`
- `blocker_summary`
- `readiness_snapshot`
- `recommended_next_action`
- `forbidden_next_actions`
- `gate_summary`
- `safety_flags`

## Proximos caminhos seguros

O campo `recommended_next_action` pode recomendar:

- materializar evidencias explicitas e reexecutar o closeout;
- manter o ciclo encerrado bloqueado e corrigir blockers apenas em research;
- preparar handover para revisao humana.

Ele nunca recomenda liberar observer, promover regra, alterar runtime ou enviar ordens.

## Acoes proibidas

`forbidden_next_actions` inclui:

- ativar paper observer;
- promover regra;
- alterar runtime;
- alterar RiskManager;
- alterar Freqtrade;
- alterar Qlib runtime;
- alterar IA Shadow runtime;
- alterar modelos;
- enviar ordens;
- acessar exchange privada.

## Uso

Default seguro:

```powershell
python .\scripts\build_ocr_shadow_research_evidence_closeout_v1.py --project-root . --no-write --json
```

Leitura explicita sem escrita:

```powershell
python .\scripts\build_ocr_shadow_research_evidence_closeout_v1.py `
  --project-root . `
  --allow-runtime-read `
  --oos-validation-report .\data\reports\ocr_master_candle_positive_rule_oos_validation_v1.json `
  --shadow-observation-design-report .\data\reports\ocr_master_candle_shadow_observation_design_v1.json `
  --shadow-observation-replay-report .\data\reports\ocr_master_candle_shadow_observation_replay_v1.json `
  --paper-closed-trades-attribution-report .\data\reports\paper_closed_trades_shadow_rule_attribution_v1.json `
  --readiness-gate-report .\data\reports\paper_shadow_observation_readiness_gate_v1.json `
  --no-write `
  --json
```

Escrita explicita de handover research-only:

```powershell
python .\scripts\build_ocr_shadow_research_evidence_closeout_v1.py `
  --project-root . `
  --allow-runtime-read `
  --oos-validation-report .\data\reports\ocr_master_candle_positive_rule_oos_validation_v1.json `
  --shadow-observation-design-report .\data\reports\ocr_master_candle_shadow_observation_design_v1.json `
  --shadow-observation-replay-report .\data\reports\ocr_master_candle_shadow_observation_replay_v1.json `
  --paper-closed-trades-attribution-report .\data\reports\paper_closed_trades_shadow_rule_attribution_v1.json `
  --readiness-gate-report .\data\reports\paper_shadow_observation_readiness_gate_v1.json `
  --write `
  --json
```

## Garantias de seguranca

- Paper/shadow only.
- Sem live trading.
- Sem ordens.
- Sem acesso a exchange privada.
- Sem alteracao de Freqtrade, RiskManager, Qlib runtime ou IA Shadow runtime.
- Sem alteracao de modelos, registry, sinais, datasets oficiais, SQLite ou configs.
- Escrita opcional restrita a JSON/Markdown em `data/reports`.

## Validacao

```powershell
python -m compileall scripts smartcrypto tests
python -m pytest tests\test_ocr_shadow_research_evidence_closeout_v1.py -q
python .\scripts\build_ocr_shadow_research_evidence_closeout_v1.py --project-root . --no-write --json
python .\scripts\generate_project_manifest.py --check
python .\scripts\scan_versioned_secrets.py --project-root . --json
python -m pip_audit -r ".\requirements-dev.lock" --progress-spinner off
git diff --check
git status --short
```
