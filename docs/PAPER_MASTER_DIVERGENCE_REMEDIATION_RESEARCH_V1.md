# Paper/Master Divergence Remediation Research V1

## Objetivo

Transformar a divergência entre Paper e `trades_master` em diagnóstico quantitativo acionável, sem aplicar mudanças operacionais.

Esta branch não corrige estratégia, não altera Freqtrade, não altera RiskManager, não altera Qlib runtime, não altera IA Shadow runtime, não promove regras, não promove modelo e não envia ordens.

## Tese técnica

A evidência consolidada indica que o Paper não replica o edge do `trades_master`:

- Paper 19D: 239 trades, PnL líquido negativo, PF abaixo de 1.
- Master na mesma janela: 243 trades, PnL líquido positivo, PF acima de 2.
- Divergência material: Paper aproximadamente 164.52 USDT abaixo do Master.
- Stop-loss rápido, ETH long e trades com duração curta são clusters críticos.

## Entregáveis

- `smartcrypto/research/paper_master_divergence_remediation/__init__.py`
- `smartcrypto/research/paper_master_divergence_remediation/remediation.py`
- `scripts/build_paper_master_divergence_remediation_research_v1.py`
- `tests/test_paper_master_divergence_remediation_research_v1.py`

## Hipóteses catalogadas

1. `H1`: stop-loss rápido destrói expectancy líquido.
2. `H2`: ETH long é cluster estruturalmente negativo.
3. `H3`: Paper pode entrar tarde em relação ao Master.
4. `H4`: Paper perde winners do Master por desalinhamento temporal ou gating incorreto.
5. `H5`: Paper pode inverter lado em janelas onde o Master capturou edge.
6. `H6`: filtros atuais podem preservar losers e remover winners.
7. `H7`: candle/feature coverage parcial pode distorcer diagnóstico e decisão.
8. `H8`: Qlib/selector pode sinalizar sem penalizar regime, drawdown, slippage e custos.

## Contrato de segurança

O CLI retorna obrigatoriamente:

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
- `write_performed=false`

## Execução

```powershell
python .\scripts\build_paper_master_divergence_remediation_research_v1.py `
  --project-root . `
  --no-write `
  --json
```

## Gates

```powershell
python -m compileall scripts smartcrypto tests
python -m pytest tests\test_paper_master_divergence_remediation_research_v1.py -q
python .\scripts\build_paper_master_divergence_remediation_research_v1.py --project-root . --no-write --json
python .\scripts\generate_project_manifest.py --check
python .\scripts\scan_versioned_secrets.py --project-root . --json
python -m pip_audit -r ".\requirements-dev.lock" --progress-spinner off
git diff --check
git status -sb
git status --short
```

## Decisão

Mesmo com hipóteses catalogadas, a decisão permanece `MANTER_EM_RESEARCH`.

Qualquer transformação de hipótese em regra candidata requer validação OOS posterior, controle de falso positivo/falso negativo e prova de que ROI winners não são degradados.
