# Freqtrade Paper Duplicate Full Exit Race Guard V1

## Status e escopo

Este documento descreve um guard paper/shadow-only para impedir uma segunda
full exit regular enquanto o estado em memoria do trade ainda contem uma exit
anterior aberta, ambigua, parcialmente preenchida ou integralmente preenchida.

O guard nao altera ROI, stoploss, leverage, sizing, RiskManager, Qlib, IA
Shadow, modelos, datasets, Docker ou o core do Freqtrade. O rollout paper nao
esta autorizado nesta branch.

## Incidentes confirmados

### Trade 653

O trade ETH short tinha posicao de `0.051`. A primeira exit ROI, order 1336,
foi preenchida integralmente e registrada como `closed`, com
`ft_cancel_reason="cancelled on exchange"`. A order 1337 foi criada cerca de
41 ms depois e tambem preencheu `0.051`. O total contabilizado em exits foi
`0.102`, duas vezes a posicao.

### Trade 669

O trade BTC short tinha posicao de `0.001`. A primeira exit ROI, order 1376,
foi preenchida integralmente e registrada como `closed`, com
`ft_cancel_reason="cancelled on exchange"`. A order 1377 foi criada cerca de
53 ms depois e tambem preencheu `0.001`. O total contabilizado em exits foi
`0.002`, duas vezes a posicao.

Nos dois casos, o PnL reportado fecha quando as duas exits sao consideradas. O
finding de identidade contabil e consequencia da dupla saida, nao a causa
primaria. CSV, replica e snapshot SQLite sao consistentes; nao ha source drift.

### Diferenca para o trade 676

O incidente 676 era uma exit legitimamente cancelada por TIMEOUT, com
`filled` raw conhecido e igual a zero, `safe_filled` igual a zero e amount
integral. O lifecycle hardening anterior criou o motivo controlado
`paper_exit_retry_latched`. Esse contrato permanece estrito e nao foi
convertido em retry generico.

## Contrato observado no Freqtrade 2026.6

A API publica instalada foi inspecionada em modo read-only. A assinatura e:

```text
confirm_trade_exit(
  pair, trade, order_type, amount, rate, time_in_force,
  exit_reason, current_time, **kwargs
) -> bool
```

O call graph relevante e:

```text
exit_positions
  -> handle_trade
  -> _check_and_execute_exit
  -> execute_trade_exit
       -> confirm_trade_exit
       -> trade.has_open_orders
       -> handle_similar_open_order
            -> cancel_open_orders_of_trade(REPLACE)
            -> handle_cancel_exit
                 -> update_trade_state
                 -> Trade.update_trade
       -> exchange.create_order
```

`confirm_trade_exit` roda antes de `handle_similar_open_order`. No cancel, uma
order que ja chegou a estado nao-open pode receber
`CANCELLED_ON_EXCHANGE`; `update_trade_state` reconcilia o fill e
`Trade.update_trade` pode fechar integralmente o trade. Sem nova verificacao,
`execute_trade_exit` ainda pode alcancar `create_order`.

## Boundary escolhido

O boundary e o callback oficial `IStrategy.confirm_trade_exit`. Ele opera
somente sobre `Trade` e `Order` ja carregados em memoria e nao executa I/O,
rede, sleep, subprocess, acesso a exchange, banco ou filesystem.

O core nao foi patchado porque:

1. o callback publico existe exatamente antes do trecho vulneravel;
2. a protecao e especifica da politica paper SMART FUTUROS;
3. manter o core oficial reduz divergencia de upgrade;
4. o guard pode ser testado deterministicamente sem exchange.

## Regras fail-closed

Para full exits regulares:

- primeira exit sem historico no `trade.exit_side`: permitida somente com trade
  aberto, side valido e amount integral conhecido e finito;
- exit anterior aberta: bloqueia;
- `ft_is_open` ausente ou ambiguo: bloqueia;
- status ausente ou inconsistente com open state: bloqueia;
- `filled` raw ausente, NaN ou infinito: bloqueia;
- `safe_filled` ausente, nao finito ou divergente do raw: bloqueia;
- qualquer fill positivo, inclusive partial ou full: bloqueia;
- historico zero-fill nao autoriza ROI ou exit signal generico;
- somente `paper_exit_retry_latched` pode continuar, e apenas quando o helper
  compartilhado prova TIMEOUT, zero fill raw/safe, amount integral, tag
  permitida e ausencia de exit aberta.

Entry orders e orders com `ft_order_side="stoploss"` nao sao confundidas com
regular exits.

## Exits criticas

O Freqtrade 2026.6 pula `confirm_trade_exit` para liquidation. Para preservar
semantica e nao bloquear mecanismos de protecao, o callback aceita
explicitamente:

- `stop_loss`;
- `stoploss_on_exchange`;
- `trailing_stop_loss`;
- `liquidation`;
- `emergency_exit`;
- `force_exit`;
- `partial_exit`;
- `sold_on_exchange`.

O guard desta branch atua somente na familia regular de full exit. Ele nao
cria opposite-signal exit, close-and-reverse ou nova politica de risco.

## Limites

- O guard usa apenas o snapshot em memoria fornecido pelo Freqtrade.
- Situacoes ambiguas no caminho regular sao negadas, nao inferidas.
- Nao ha reparo retroativo dos trades 653/669.
- Nao ha alteracao de accounting, recovery allowlist ou source profile.
- Nao ha ativacao, restart ou recriacao de container nesta branch.

## Safety flags

```text
paper_only=true
shadow_only=true
live_trading_enabled=false
live_release_allowed=false
canary_release_allowed=false
real_order_submission_enabled=false
exchange_private_access=false
changes_risk=false
changes_model=false
runtime_rollout_authorized=false
```

## Validacao

```powershell
python -m compileall -q scripts smartcrypto tests
python -m pytest tests/test_freqtrade_paper_duplicate_full_exit_race_guard_v1.py tests/test_freqtrade_paper_exit_lifecycle_hardening_v1.py -q
python -m ruff check freqtrade/user_data/strategies/SmartCryptoSignalStrategy.py tests/test_freqtrade_paper_duplicate_full_exit_race_guard_v1.py
python scripts/generate_project_manifest.py
python scripts/generate_project_manifest.py --check
python scripts/scan_versioned_secrets.py --project-root . --json
python scripts/audit_state_execution_ledger_boundary.py --project-root . --json
python scripts/audit_operational_exception_swallowing.py --project-root . --json
git diff --check
```
