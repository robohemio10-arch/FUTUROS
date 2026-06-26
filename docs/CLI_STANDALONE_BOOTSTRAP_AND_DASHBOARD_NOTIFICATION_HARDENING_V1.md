# CLI Standalone Bootstrap and Dashboard Notification Hardening V1

## Decisão

A Branch 00A confirmou que a Branch 00B é necessária:

- `branch_00b_required=true`
- `branch_00b_reason=cli_standalone_high_priority_findings+dashboard_notification_side_effect_risk`
- seis scripts high-priority sem bootstrap explícito de project root
- um achado high no dashboard por `dry_run=False` em `smartcrypto/dashboard/controls/command_stub_adapter.py`

Esta branch aplica hardening cirúrgico sobre os achados confirmados, sem alterar o comportamento operacional do sistema.

## Escopo

Arquivos de CLI com bootstrap standalone explícito:

- `scripts/build_freqtrade_paper_ai_selector_integration.py`
- `scripts/collect_freqtrade_paper_history.py`
- `scripts/export_freqtrade_signals.py`
- `scripts/export_market_freqtrade_signals.py`
- `scripts/export_qlib_freqtrade_signals.py`
- `scripts/inspect_phase11_freqtrade_db.py`

Arquivo de dashboard neutralizado:

- `smartcrypto/dashboard/controls/command_stub_adapter.py`

## Invariantes de segurança

- `research_only=true`
- `read_only=true` para a validação da branch
- `paper_only=true`
- `shadow_only=true`
- `operational_authority=false`
- `release_authority=false`
- `live_release_allowed=false`
- `canary_release_allowed=false`
- `sends_orders=false`
- `exchange_private_access=false`
- `changes_risk=false`
- `changes_model=false`

## Fora de escopo

Esta branch não altera:

- Freqtrade strategy
- RiskManager
- Qlib runtime
- IA Shadow runtime
- modelos
- datasets operacionais
- live/canary/orders
- runtime/data/reports/logs/freqtrade

## Resultado esperado

Após esta branch, o replay DEV27 deve continuar podendo reportar findings medium em scripts fora do escopo 00B, mas os seis scripts high-priority devem deixar de aparecer como `branch_00b_candidate=true`, e o dashboard não deve conter `dry_run=False`.

O resultado aceitável esperado é:

- `branch_00b_required=false`
- dashboard notification sem high finding por `dry_run=False`
- testes subprocess sem `PYTHONPATH` passando para os seis scripts hardenizados
- dashboard hard-blocked retornando `dry_run=True`
