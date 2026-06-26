# Paper/Master Divergence OOS Causal Attribution V1

## Objetivo

Esta branch transforma a remediação research-only da divergência Paper/Master em um plano de atribuição causal fora da amostra para as hipóteses H1/H2/H6.

O objetivo não é aplicar regra, treinar modelo, ajustar Freqtrade, alterar RiskManager ou liberar operação. O objetivo é definir o contrato mínimo de validação causal antes de qualquer hipótese sair do laboratório.

## Escopo

Hipóteses avaliadas:

- H1: stop-loss rápido está destruindo expectancy líquido do paper.
- H2: ETH long pode ser cluster estruturalmente negativo.
- H6: filtros atuais podem preservar losers ou remover winners.

## Evidência canônica consolidada

- Paper não replica o edge do master.
- `paper_minus_master_net_pnl=-164.52110752`
- `paper_minus_master_profit_factor=-1.269242`
- `paper_minus_master_win_rate_points=-30.1961`
- `paper_profit_factor=0.803331`
- `master_profit_factor=2.072573`
- `stop_loss_net_pnl=-108.58254837`
- `roi_net_pnl=87.22777285`
- `remove_stop_loss_under_30m_delta=34.9161`
- Candidate shadow rule research-only:
  `lb_10m_ret_close <= -0.0038501215827868 AND lb_30m_ret_close <= -0.0060685748963285`
- Candidate rule precision: `0.65625`
- Candidate rule recall: `0.41176`
- Simulated removed PnL delta: `8.9745`

## Contrato OOS obrigatório

Toda hipótese deve ser validada por:

- dia;
- símbolo;
- lado;
- `exit_reason`;
- bucket de duração;
- covered vs uncovered;
- winner retention;
- false positives;
- false negatives;
- impacto líquido em PnL/PF/DD.

## Safety

A branch preserva:

- `research_only=true`
- `read_only=true`
- `paper_only=true`
- `shadow_only=true`
- `operational_authority=false`
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

## Comandos

```powershell
python -m compileall scripts smartcrypto tests

python -m pytest tests\test_paper_master_divergence_oos_causal_attribution_v1.py -q

python .\scripts\build_paper_master_divergence_oos_causal_attribution_v1.py `
  --project-root . `
  --no-write `
  --json
```

## Resultado esperado

- `status=blocked`
- `decision=MANTER_EM_RESEARCH`
- `causal_attribution_scope=["H1","H2","H6"]`
- `oos_validation_required=true`
- `oos_validated=false`
- `ready_for_candidate_registry=false`
- `remediation_application_allowed=false`
- `gate_summary.critical_failed_gate_ids=[]`
