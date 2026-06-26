# Paper/Master Divergence — Paper Source Discovery V1

## Objetivo

Localizar candidatos reais de fonte Paper/Freqtrade em modo read-only/version-safe para destravar o cálculo OOS real por fatia da divergência Paper/Master.

Esta branch não seleciona fonte automaticamente e não executa cálculo operacional. Ela apenas descobre candidatos e produz metadados de revisão manual.

## Contrato

- `research_only=true`
- `read_only=true`
- `paper_only=true`
- `shadow_only=true`
- `operational_authority=false`
- `paper_source_selected=false`
- `real_slice_metrics_computed=false`
- `can_apply_to_freqtrade=false`
- `can_apply_to_risk_manager=false`
- `can_promote_rules=false`
- `can_promote_model=false`
- `sends_orders=false`
- `live_release_allowed=false`
- `canary_release_allowed=false`

## Uso padrão

```powershell
python .\scripts\build_paper_master_divergence_paper_source_discovery_v1.py `
  --project-root . `
  --no-write `
  --json
```

Resultado esperado: discovery bloqueada sem leitura runtime.

## Uso com descoberta explícita read-only

```powershell
python .\scripts\build_paper_master_divergence_paper_source_discovery_v1.py `
  --project-root . `
  --allow-runtime-read `
  --discovery-root ".\data\reports" `
  --discovery-root ".\data\runtime" `
  --discovery-root ".\user_data" `
  --no-write `
  --json
```

## Próximo gate

Apenas após revisar manualmente `best_paper_source_candidate`, executar a branch de real slice computation com `--paper-source` explícito e `--master-source .\data\trades\trades_master.xlsx`.
