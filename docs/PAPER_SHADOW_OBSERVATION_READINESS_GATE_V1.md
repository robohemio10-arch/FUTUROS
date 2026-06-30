# Paper Shadow Observation Readiness Gate V1

## Objetivo

Este gate consolida evidencias research-only das camadas:

```text
OCR OOS survivors
-> shadow observation design
-> shadow observation replay
-> paper closed trades attribution
-> readiness decision bloqueada
```

O objetivo e medir se existe evidencia suficiente para analise futura de observacao paper-shadow. A branch nao libera observacao, nao ativa regra e nao acopla nada ao runtime paper.

## Decisao institucional

O resultado final permanece bloqueado por contrato:

```text
decision=MANTER_EM_RESEARCH
paper_observation_allowed=false
ready_for_shadow_observation=false
operational_authority=false
can_apply_to_freqtrade=false
can_apply_to_risk_manager=false
can_promote_rules=false
sends_orders=false
changes_risk=false
writes_runtime=false
```

Mesmo com todos os relatorios presentes e consistentes, o nivel maximo e `RESEARCH_READY_BLOCKED`. Isso significa apenas que a evidencia research esta completa para discussao tecnica, nao que o sistema esta autorizado a observar paper runtime ou aplicar regra.

## Entradas

Por padrao, o CLI nao le runtime real. Sem `--allow-runtime-read`, retorna:

```text
status=blocked
input_mode=no_runtime_rows_loaded
```

Com leitura explicita, aceita somente relatorios JSON locais:

- `--oos-validation-report`
- `--shadow-observation-design-report`
- `--shadow-observation-replay-report`
- `--paper-closed-trades-attribution-report`

Se qualquer fonte obrigatoria estiver ausente, o retorno e `blocked` estruturado, sem exception crua.

## Saida

Sem `--write`, nada e escrito.

Com `--write`, a escrita e restrita a JSON research-only sob `data/reports`:

```text
data/reports/paper_shadow_observation_readiness_gate_v1.json
```

O gate nunca escreve `data/runtime`, SQLite, Parquet, modelos, registry, sinais, configs ou estrategias.

## Gates avaliados

Para cada relatorio, o gate verifica:

- `decision=MANTER_EM_RESEARCH`
- `operational_authority=false`
- `sends_orders=false`
- `changes_risk=false`
- `can_promote_rules=false`
- `can_apply_to_freqtrade=false`
- `can_apply_to_risk_manager=false`
- `writes_runtime=false`

Tambem exige evidencias minimas:

- survivors OOS presentes;
- contrato de design presente;
- replay com trades;
- attribution com trades atribuidos.

## Campos principais

O JSON expõe:

- `oos_survivor_count`
- `design_contract_status`
- `replay_trade_count`
- `attribution_trade_count`
- `readiness_score`
- `readiness_level`
- `readiness_blockers`
- `readiness_warnings`
- `evidence_matrix`
- `gate_summary`
- `safety_flags`

`readiness_score` e descritivo e research-only. Ele nao vira autorizacao operacional.

## Uso

Default seguro:

```powershell
python .\scripts\build_paper_shadow_observation_readiness_gate_v1.py --project-root . --no-write --json
```

Leitura explicita sem escrita:

```powershell
python .\scripts\build_paper_shadow_observation_readiness_gate_v1.py `
  --project-root . `
  --allow-runtime-read `
  --oos-validation-report .\data\reports\ocr_master_candle_positive_rule_oos_validation_v1.json `
  --shadow-observation-design-report .\data\reports\ocr_master_candle_shadow_observation_design_v1.json `
  --shadow-observation-replay-report .\data\reports\ocr_master_candle_shadow_observation_replay_v1.json `
  --paper-closed-trades-attribution-report .\data\reports\paper_closed_trades_shadow_rule_attribution_v1.json `
  --no-write `
  --json
```

Escrita explicita do relatorio research-only:

```powershell
python .\scripts\build_paper_shadow_observation_readiness_gate_v1.py `
  --project-root . `
  --allow-runtime-read `
  --oos-validation-report .\data\reports\ocr_master_candle_positive_rule_oos_validation_v1.json `
  --shadow-observation-design-report .\data\reports\ocr_master_candle_shadow_observation_design_v1.json `
  --shadow-observation-replay-report .\data\reports\ocr_master_candle_shadow_observation_replay_v1.json `
  --paper-closed-trades-attribution-report .\data\reports\paper_closed_trades_shadow_rule_attribution_v1.json `
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
- Escrita opcional restrita a JSON em `data/reports`.

## Validacao

```powershell
python -m compileall scripts smartcrypto tests
python -m pytest tests\test_paper_shadow_observation_readiness_gate_v1.py -q
python .\scripts\build_paper_shadow_observation_readiness_gate_v1.py --project-root . --no-write --json
python .\scripts\generate_project_manifest.py --check
python .\scripts\scan_versioned_secrets.py --project-root . --json
python -m pip_audit -r ".\requirements-dev.lock" --progress-spinner off
git diff --check
git status --short
```
