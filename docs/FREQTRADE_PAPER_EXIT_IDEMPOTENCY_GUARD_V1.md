# Freqtrade Paper Exit Idempotency Guard V1

## Objetivo

Bloquear, exclusivamente no runtime `dry_run`/paper, a criação de uma segunda full exit não protetiva quando o mesmo `Trade` já possui uma exit order aberta/pending no mesmo `exit_side`.

A correção atua no último boundary de estratégia antes da criação da ordem: `confirm_trade_exit()`.

## Incidente reproduzido

A auditoria read-only do snapshot paper encontrou quatro trades com duas full exits para uma única posição:

| trade_id | par | direção | amount | exit total | motivo |
| ---: | --- | --- | ---: | ---: | --- |
| 141 | ETH/USDT:USDT | long | 0.060 | 0.120 | roi |
| 258 | ETH/USDT:USDT | long | 0.060 | 0.120 | roi |
| 561 | ETH/USDT:USDT | long | 0.056 | 0.112 | roi |
| 653 | ETH/USDT:USDT | short | 0.051 | 0.102 | roi |

No trade 653, o log do Freqtrade 2026.6 mostrou:

1. primeira decisão ROI e criação de uma `LIMIT_BUY` de `0.051`;
2. a ordem permaneceu aberta durante ciclos subsequentes;
3. nova decisão ROI ocorreu enquanto a primeira exit ainda estava aberta;
4. a primeira ordem foi preenchida e o trade marcado como fechado;
5. a segunda full exit já disparada foi criada/preenchida em seguida;
6. `cumulative_profit` passou de aproximadamente `1.01493825` para `2.00896232`.

O defeito contamina a magnitude financeira do resultado paper e, por consequência, feedback e targets que consumam `close_profit_abs`/`realized_profit` sem quarentena contábil.

## Contrato do guard

O guard é habilitado somente quando:

```text
strategy.config["dry_run"] is True
```

Para exits não protetivas, uma full exit é rejeitada quando qualquer uma das condições abaixo ocorre:

- `trade.is_open` não é exatamente `True`;
- a quantidade da full exit não pode ser validada de forma finita/positiva;
- a inspeção de `trade.open_orders` é inválida ou ambígua;
- existe uma `Order` aberta/pending cujo lado corresponde a `trade.exit_side`.

Estados de order aceitos como pending por fallback:

```text
open
new
pending
partially_filled
partially-filled
```

Estados terminais reconhecidos:

```text
closed
canceled
cancelled
expired
rejected
```

A fonte primária de estado permanece `Order.ft_is_open` quando ela é booleana.

## Preservação de exits protetivas

`confirm_trade_exit()` pode bloquear stoploss. Por isso o guard não interfere nos seguintes motivos:

```text
stop_loss
stoploss_on_exchange
trailing_stop_loss
emergency_exit
force_exit
```

Esses exits retornam `True` mesmo quando uma regular exit está pendente. Isso impede que a correção de idempotência altere a política de stoploss, emergência ou controle manual.

## Full exit versus partial exit

O guard atua apenas quando `amount` representa toda a quantidade atual do trade, considerando tolerância numérica mínima:

```text
relative_tolerance = 1e-9
absolute_tolerance = 1e-12
```

Uma solicitação parcial não é bloqueada por este guard. `position_adjustment_enable` continua inalterado e permanece fora do escopo.

## Performance

O callback não executa:

- I/O de arquivo;
- banco de dados;
- network/API;
- Qlib/IA;
- RiskManager;
- serialização/log adicional.

A inspeção é local sobre `trade.open_orders`, portanto custo `O(n)` no número de orders abertas do trade, normalmente muito pequeno.

## Arquivos

```text
freqtrade/user_data/strategies/SmartCryptoSignalStrategy.py
tests/test_freqtrade_paper_exit_idempotency_guard_v1.py
docs/FREQTRADE_PAPER_EXIT_IDEMPOTENCY_GUARD_V1.md
PROJECT_MANIFEST_CLEAN.json
```

## Testes determinísticos

A suíte dedicada cobre:

- reprodução explícita dos trades 141, 258, 561 e 653;
- primeira full ROI exit sem ordem pendente permitida;
- pending entry order não bloqueia exit;
- exits protetivas preservadas;
- partial exit fora do guard;
- runtime não-paper não alterado;
- trade já fechado bloqueado para exit não protetiva;
- falha/ambiguidade na inspeção de open orders bloqueada fail-closed;
- fallback por `status` quando `ft_is_open` não está disponível;
- orders terminais não bloqueiam nova exit;
- valores inválidos/NaN/inf bloqueados fail-closed;
- ROI, stoploss, trailing e demais parâmetros existentes permanecem inalterados;
- nenhuma API de position adjustment/live foi introduzida.

## Invariantes de segurança

A branch não autoriza nem implementa:

```text
live trading
canary
ordens reais
exchange privada
deploy
restart de container
alteração de ROI
alteração de stoploss
alteração de leverage
alteração do RiskManager
reconciliação histórica
correção/backfill de trades 141/258/561/653
promoção de modelo
Trader Master
```

## Validação obrigatória antes de PR

```powershell
python -m compileall -q scripts smartcrypto tests freqtrade/user_data/strategies
python -m pytest -q tests/test_freqtrade_paper_exit_idempotency_guard_v1.py
python -m ruff check freqtrade/user_data/strategies/SmartCryptoSignalStrategy.py tests/test_freqtrade_paper_exit_idempotency_guard_v1.py
python -m mypy freqtrade/user_data/strategies/SmartCryptoSignalStrategy.py
python -m bandit -q freqtrade/user_data/strategies/SmartCryptoSignalStrategy.py
python scripts/generate_project_manifest.py --project-root .
python scripts/generate_project_manifest.py --project-root . --check
python scripts/scan_versioned_secrets.py --project-root . --json
python -m pytest -q
```

A suíte completa pode continuar expondo o teste histórico não-hermético `test_real_no_write_probe_closes_only_fixed_recoveries_when_sources_exist` enquanto os dados paper reais contiverem `freqtrade-paper-653` como quarto quarantine. Esse fato deve ser reportado como drift/evidência runtime preexistente, não mascarado alterando a cardinalidade esperada nesta branch.
