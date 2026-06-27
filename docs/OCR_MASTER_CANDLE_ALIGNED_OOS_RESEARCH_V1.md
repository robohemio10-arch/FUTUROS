# OCR Master Candle Aligned OOS Research V1

## Objetivo

Alinhar o `trades_master.xlsx` oficial OCR/Bitradex às candles reais BTC/ETH em modo estritamente research-only/read-only.

Esta revisão corrige o contrato real do master OCR, que usa colunas em português:

- `11_moeda`
- `12_fechar_long_short`
- `1_pnl_fechado`
- `3_preco_de_abertura`
- `4_preco_de_fechamento`
- `7_horario_de_abertura`
- `8_horario_de_fechamento`

A rotina normaliza símbolos, lado, PnL, preços e timestamps antes do alinhamento.

## Fonte de candles priorizada

Quando disponíveis, as fontes canônicas 1m são priorizadas para evitar mistura com backups, datasets derivados e candles 15s:

- `data/raw/binance_futures_klines/BTCUSDT_1m_20251230_20261208.csv`
- `data/raw/binance_futures_klines/ETHUSDT_1m_20251230_20261208.csv`

Se essas fontes não existirem, o loader recua para os arquivos 1m canônicos/parquet ou para descoberta restrita.

## Alinhamento

O alinhamento usa:

- `symbol` normalizado para `BTCUSDT`/`ETHUSDT`
- `open_time` normalizado em UTC
- candle de entrada nearest-prior `timestamp <= open_time`
- tolerância máxima padrão de 300 segundos
- retornos lookback de 5, 10 e 30 minutos usando close anterior

## Hipóteses medidas

- H1: duração rápida (`<=30m`) ou stop-like exit reason
- H2: cluster `ETHUSDT` + `long`
- H6: regra candidata shadow-only

```text
lb_10m_ret_close <= -0.0038501215827868 AND lb_30m_ret_close <= -0.0060685748963285
```

## Safety

A branch não altera Freqtrade, RiskManager, Qlib runtime, IA Shadow runtime, registry, modelos, datasets oficiais ou qualquer superfície de execução.

Flags permanentes:

- `research_only=true`
- `read_only=true`
- `paper_only=true`
- `shadow_only=true`
- `operational_authority=false`
- `can_promote_rules=false`
- `can_promote_model=false`
- `sends_orders=false`
- `live_release_allowed=false`
- `canary_release_allowed=false`

## Resultado esperado em runtime real

Com o master atual, espera-se:

- `trades_master_rows=3058`
- `normalized_trade_rows=2826`
- `aligned_rows > 0`
- `master_candle_alignment_computed=true`
- `slice_count > 0`

As 232 linhas `legacy_janeiro_sem_abertura` permanecem sem alinhamento por entrada porque não possuem horário de abertura no master OCR.
