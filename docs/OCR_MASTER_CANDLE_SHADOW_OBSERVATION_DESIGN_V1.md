# OCR Master Candle Shadow Observation Design V1

## Decisão institucional

`codex/ocr-master-candle-shadow-observation-design-v1` cria apenas um contrato de observação shadow research-only para os survivors OOS da branch anterior.

A entrega não autoriza paper observation, não registra regras, não aplica filtros, não altera sinais, não altera modelos e não toca o runtime operacional.

Estado obrigatório:

```text
MANTER_EM_RESEARCH
paper_only=true
shadow_only=true
research_only=true
read_only=true
operational_authority=false
can_apply_to_freqtrade=false
can_apply_to_risk_manager=false
can_promote_rules=false
can_promote_model=false
paper_observation_allowed=false
```

## Fonte esperada

A fonte primária opcional é o relatório local:

```text
data/reports/ocr_master_candle_positive_rule_oos_validation_v1.json
```

O script não lê essa fonte por padrão. A leitura local precisa ser explicitada com `--allow-runtime-read`. Em testes, a camada também aceita survivors em memória para manter a lógica determinística sem depender de runtime.

## Contrato observacional

Cada survivor OOS é transformado em um registro descritivo com os campos:

```text
survivor_rule_id
survivor_expression
dimensions
values
would_allow
would_block
opportunity_score
expected_value_delta
shadow_observation_reason
operational_authority
can_apply_to_freqtrade
can_apply_to_risk_manager
can_promote_rules
```

### `would_allow`

Booleano hipotético indicando que uma regra sobrevivente permitiria uma linha de observação shadow caso a mesma condição de slice fosse satisfeita. Não é permissão de ordem, não é seletor paper e não é sinal live.

### `would_block`

Booleano hipotético indicando que o contrato bloquearia uma linha da coorte de observação survivor. Para survivors OOS, permanece `false`; não matches ficam simplesmente fora da coorte.

### `opportunity_score`

Score determinístico normalizado em `[0, 1]`, calculado a partir de evidência OOS research-only: pass ratio, delta esperado positivo, profit factor e suporte amostral.

O score não tem autoridade operacional.

### `expected_value_delta`

Delta research-only comparando média OOS do survivor contra média baseline dos folds. É evidência descritiva, não parâmetro operacional.

## Proibições preservadas

A branch não pode:

- alterar Freqtrade;
- alterar RiskManager;
- alterar Qlib runtime;
- alterar IA Shadow runtime;
- registrar shadow rule;
- aplicar shadow rule;
- promover modelo;
- habilitar paper observation;
- habilitar live/canary;
- enviar ordem;
- acessar exchange privada;
- escrever runtime, sinais ativos, registry, modelos ou configuração operacional.

## CLI

No modo padrão, sem leitura runtime:

```powershell
python .\scripts\build_ocr_master_candle_shadow_observation_design_v1.py --project-root . --no-write --json
```

Resultado esperado: `status=blocked`, `input_mode=no_runtime_rows_loaded`, `decision=MANTER_EM_RESEARCH`, `write_performed=false`.

Com leitura explícita do relatório OOS local:

```powershell
python .\scripts\build_ocr_master_candle_shadow_observation_design_v1.py --project-root . --allow-runtime-read --no-write --json
```

Com escrita explícita de relatório research-only:

```powershell
python .\scripts\build_ocr_master_candle_shadow_observation_design_v1.py --project-root . --allow-runtime-read --write --json
```

Saída permitida somente:

```text
data/reports/ocr_master_candle_shadow_observation_design_v1.json
```

## Validações

```powershell
python -m compileall scripts smartcrypto tests
python -m pytest tests\test_ocr_master_candle_shadow_observation_design_v1.py -q
python .\scripts\build_ocr_master_candle_shadow_observation_design_v1.py --project-root . --no-write --json
python .\scripts\generate_project_manifest.py --check
python .\scripts\scan_versioned_secrets.py --project-root . --json
python -m pip_audit -r ".\requirements-dev.lock" --progress-spinner off
git diff --check
git status --short
```
