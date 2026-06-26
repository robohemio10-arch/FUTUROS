# Paper/Master Divergence OOS Real Source Loader V1

## Objetivo

Esta branch adiciona uma camada **research-only / read-only / version-safe** para localizar, validar e normalizar fontes reais de trades Paper/Master antes do cálculo OOS por fatia.

Ela não computa métricas OOS finais. Ela prepara o contrato de entrada para a próxima etapa, mantendo o sistema bloqueado para qualquer uso operacional.

## Problema

As branches anteriores confirmaram que o paper não replica o edge do `trades_master`:

- `paper_minus_master_net_pnl=-164.52110752`
- `paper_minus_master_profit_factor=-1.269242`
- `paper_minus_master_win_rate_points=-30.1961`
- `paper_replicates_master_edge=false`

A etapa seguinte exige linhas reais para calcular OOS por:

- `day`
- `symbol`
- `side`
- `exit_reason`
- `duration_bucket`
- `covered_vs_uncovered`

Esta branch cria o loader seguro dessas fontes.

## Escopo

Arquivos adicionados:

- `smartcrypto/research/paper_master_divergence_oos_real_source_loader/__init__.py`
- `smartcrypto/research/paper_master_divergence_oos_real_source_loader/real_source_loader.py`
- `scripts/build_paper_master_divergence_oos_real_source_loader_v1.py`
- `tests/test_paper_master_divergence_oos_real_source_loader_v1.py`
- `docs/PAPER_MASTER_DIVERGENCE_OOS_REAL_SOURCE_LOADER_V1.md`

## Contrato de segurança

A saída preserva:

- `research_only=true`
- `read_only=true`
- `paper_only=true`
- `shadow_only=true`
- `operational_authority=false`
- `release_authority=false`
- `can_apply_to_freqtrade=false`
- `can_apply_to_risk_manager=false`
- `can_promote_rules=false`
- `can_promote_model=false`
- `updates_freqtrade=false`
- `updates_risk_manager=false`
- `updates_qlib_runtime=false`
- `updates_ai_shadow_runtime=false`
- `sends_orders=false`
- `live_release_allowed=false`
- `canary_release_allowed=false`

## Leitura real opt-in

Por padrão, nenhuma fonte runtime é carregada.

Mesmo quando `--paper-source` ou `--master-source` são fornecidos, o loader bloqueia a leitura se `--allow-runtime-read` não for explicitamente passado.

Exemplo seguro padrão:

```powershell
python .\scripts\build_paper_master_divergence_oos_real_source_loader_v1.py `
  --project-root . `
  --no-write `
  --json
```

Exemplo com leitura real explícita:

```powershell
python .\scripts\build_paper_master_divergence_oos_real_source_loader_v1.py `
  --project-root . `
  --paper-source "data\runtime\paper_trades.csv" `
  --master-source "data\trades\trades_master.xlsx" `
  --allow-runtime-read `
  --no-write `
  --json
```

Atenção: fontes runtime/data não devem ser versionadas.

## Schema normalizado mínimo

O loader normaliza para:

- `source_role`
- `trade_id`
- `symbol`
- `side`
- `open_time`
- `close_time`
- `day`
- `pnl`
- `exit_reason`
- `duration_minutes`
- `duration_bucket`
- `covered_feature_subset`
- `covered_vs_uncovered`

Formatos suportados:

- `.csv`
- `.json`
- `.jsonl`
- `.xlsx`

## Gate matrix

Gates críticos:

- Research-only contract preserved
- Real source loader is informational and read-only
- Rule/model promotion blocked
- Runtime and execution surfaces unchanged

Gates high:

- Real Paper/Master sources loaded only through explicit opt-in
- Minimum normalized schema declared
- OOS slice dimensions preserved

## Resultado esperado sem fontes reais

```json
{
  "status": "blocked",
  "decision": "MANTER_EM_RESEARCH",
  "input_mode": "no_runtime_rows_loaded",
  "real_source_loader_created": true,
  "real_sources_loaded": false,
  "oos_ready_for_slice_metrics": false,
  "oos_slice_metrics_computed": false,
  "oos_validated": false,
  "operational_authority": false
}
```

## Próxima etapa

Após merge e baseline, a próxima branch deve usar este loader para computar métricas OOS reais por fatia, ainda sem aplicar regras:

`codex/paper-master-divergence-oos-real-slice-computation-v1`
