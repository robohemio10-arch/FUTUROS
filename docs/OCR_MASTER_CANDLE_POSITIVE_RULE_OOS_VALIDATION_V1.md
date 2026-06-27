# OCR Master Candle Positive Rule OOS Validation V1

## Objetivo

Validar, em modo **research-only/read-only**, os slices positivos descobertos na etapa `OCR Master Candle Positive EV Slice Mining V1` por janelas temporais out-of-sample/walk-forward.

A branch não promove regras, não registra candidatos, não altera IA Shadow, Qlib, Freqtrade, RiskManager, runtime, modelo, stake, stop, entry, exit ou ordens.

## Entradas

Leitura real somente com `--allow-runtime-read`:

- `data/trades/trades_master.xlsx`
- candles canônicas BTC/ETH 1m em `data/raw/binance_futures_klines/`

Sem `--allow-runtime-read`, o comando retorna estado bloqueado sem carregar runtime/data.

## Método

1. Normaliza o `trades_master` OCR/Bitradex.
2. Carrega candles canônicas BTCUSDT/ETHUSDT 1m.
3. Alinha trades por candle nearest-prior.
4. Recria os candidatos positivos da mineração anterior.
5. Cria folds mensais walk-forward.
6. Avalia cada candidato nos períodos OOS.
7. Produz shortlist research-only de sobreviventes, se houver.

## Gates mínimos

- `min_trade_count`: 30 por candidato in-sample.
- `min_oos_trade_count`: 8 por avaliação OOS agregada.
- `min_oos_pass_ratio`: 0.60.
- `min_oos_folds`: 3.
- `max_day_concentration`: 0.35.

## Contrato de segurança

Sempre preservado:

- `research_only=true`
- `read_only=true`
- `paper_only=true`
- `shadow_only=true`
- `operational_authority=false`
- `paper_observation_allowed=false`
- `ready_for_candidate_registry=false`
- `can_promote_rules=false`
- `updates_freqtrade=false`
- `updates_risk_manager=false`
- `updates_qlib_runtime=false`
- `updates_ai_shadow_runtime=false`
- `sends_orders=false`

## Resultado esperado

A branch pode encontrar sobreviventes OOS, mas eles permanecem bloqueados para uso operacional. Uma branch posterior deverá transformar sobreviventes estáveis em observação shadow/paper, ainda sem autoridade operacional.
