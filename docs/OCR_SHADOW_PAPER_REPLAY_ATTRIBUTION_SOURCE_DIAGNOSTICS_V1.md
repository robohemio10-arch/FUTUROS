# OCR Shadow Paper Replay Attribution Source Diagnostics V1

## Objetivo

Esta etapa diagnostica, de forma research-only e read-only, por que o ciclo de replay observacional OCR Shadow e a attribution de trades paper fechados ficaram bloqueados por ausência de trades ou fontes suficientes no evidence pack explícito.

Os bloqueios investigados são:

- `replay_report_without_trades`
- `paper_attribution_without_attributed_trades`
- `readiness_gate:replay_report_without_trades`
- `readiness_gate:attribution_report_without_trades`

O resultado é evidência descritiva. Ele não libera paper observation, não promove regras, não altera runtime e não pode ser usado como autorização operacional.

## Entradas

Por padrão, a CLI não lê fontes runtime e retorna bloqueio seguro. Para diagnosticar relatórios locais existentes, use `--allow-runtime-read`.

Fontes esperadas:

- `data/reports/ocr_master_candle_positive_rule_oos_validation_v1.json`
- `data/reports/ocr_master_candle_shadow_observation_design_v1.json`
- `data/reports/ocr_master_candle_shadow_observation_replay_v1.json`
- `data/reports/paper_closed_trades_shadow_rule_attribution_v1.json`
- `data/reports/paper_shadow_observation_readiness_gate_v1.json`
- `data/reports/ocr_shadow_research_evidence_closeout_v1.json`
- `data/reports/ocr_shadow_research_explicit_evidence_pack_v1.json`

Também é possível apontar caminhos explícitos pelos argumentos da CLI.

## Saídas

Sem `--write`, nada é gravado.

Com `--write`, a ferramenta materializa apenas relatórios research-only em `data/reports`:

- `data/reports/ocr_shadow_paper_replay_attribution_source_diagnostics_v1.json`
- `data/reports/ocr_shadow_paper_replay_attribution_source_diagnostics_v1.md`

Esses arquivos são artefatos runtime e não devem ser versionados.

## Diagnósticos Gerados

O relatório identifica:

- fontes presentes, ausentes e ilegíveis;
- hashes SHA256 dos relatórios carregados;
- contagens de survivors, observation records, replay rows e attributed trades;
- ausência de fonte de closed trades;
- ausência de fonte de replay;
- campos de join faltantes, como `trade_id`, `order_id` e `fingerprint_operacional`;
- mismatches de contrato entre OOS, design, replay, attribution, readiness gate e closeout;
- root causes prováveis;
- próxima ação recomendada mantendo tudo em research.

## Comandos

Bloqueio seguro padrão, sem leitura runtime:

```powershell
python .\scripts\build_ocr_shadow_paper_replay_attribution_source_diagnostics_v1.py --project-root . --no-write --json
```

Diagnóstico read-only com fontes padrão:

```powershell
python .\scripts\build_ocr_shadow_paper_replay_attribution_source_diagnostics_v1.py --project-root . --allow-runtime-read --no-write --json
```

Escrita explícita de relatório research-only:

```powershell
python .\scripts\build_ocr_shadow_paper_replay_attribution_source_diagnostics_v1.py --project-root . --allow-runtime-read --write --json
```

## Garantias de Segurança

A saída deve manter:

- `decision=MANTER_EM_RESEARCH`
- `research_only=true`
- `read_only=true`
- `paper_only=true`
- `shadow_only=true`
- `operational_authority=false`
- `paper_observation_allowed=false`
- `ready_for_shadow_observation=false`
- `can_promote_rules=false`
- `can_apply_to_freqtrade=false`
- `can_apply_to_risk_manager=false`
- `registers_shadow_rules=false`
- `applies_shadow_rules=false`
- `sends_orders=false`
- `changes_risk=false`
- `writes_runtime=false`
- `writes_sqlite=false`
- `writes_parquet=false`

## Fora de Escopo

Esta branch não:

- altera Freqtrade;
- altera RiskManager;
- altera Qlib runtime;
- altera IA Shadow runtime;
- altera model registry;
- altera active signals;
- altera configs, YAML ou `.env`;
- escreve SQLite;
- escreve Parquet operacional;
- promove regras;
- habilita live/canary;
- envia ordens;
- acessa exchange privada.
