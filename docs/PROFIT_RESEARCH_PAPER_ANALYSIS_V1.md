# Profit Research Paper Analysis V1

## Objetivo

Esta entrega analisa economicamente os trades paper fechados usando a fonte SQLite
autoritativa e candles point-in-time. O resultado e apenas evidencia de pesquisa:
nao altera Freqtrade, RiskManager, stake, Qlib, IA Shadow, modelo ativo, sinais ou
Trader Master.

O default e `--no-write`. Com `--write`, os unicos outputs permitidos sao:

- `data/research/profit_research_paper_analytical_dataset_v1.parquet`;
- `data/reports/profit_research_paper_analysis_v1.json`;
- `data/reports/profit_research_paper_analysis_v1.md`.

## Fontes e autoridade

| Fonte | Papel |
| --- | --- |
| `data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite` | fonte analitica principal, lida por copia temporaria e `query_only` |
| `freqtrade/user_data/tradesv3.paper.sqlite` | fonte operacional atual incompleta, apenas inventariada em read-only |
| `data/trades/inbox/freqtrade_paper_closed_trades.csv` | replica analitica |
| `data/feedback/paper_closed_trades_incremental.parquet` | feedback auxiliar incompleto |
| `data/features/incremental_training_microbatch.parquet` | evidencia de treino incompleta |
| `data/features/market_features_60d.parquet` | contexto de candles e features anteriores a entrada |
| `data/trades/trades_master.parquet` | referencia protegida, somente tamanho e SHA-256 |

O snapshot SQLite fornece precos, quantidade, `contract_size`, leverage, fees,
funding e PnL liquido. O PnL bruto e reconstruido independentemente:

```text
long_gross  = (close_rate - open_rate) * amount * contract_size
short_gross = (open_rate - close_rate) * amount * contract_size
trading_fee = fee_open_cost * leverage + fee_close_cost
funding     = -funding_fees
net         = gross - trading_fee - funding
```

Linhas que nao satisfazem essa identidade ficam fora da analise economica.

## Contrato temporal

Para cada trade, o loader escolhe o menor timeframe que cobre integralmente o
intervalo entre abertura e fechamento. No estado observado, `1m` termina em
2026-05-29 e `5m` cobre o periodo paper de junho/julho. Portanto, os trades
recentes usam `5m`; nao ha extrapolacao ou candle inventado.

As features de entrada usam apenas `feature_timestamp_utc <= open_time_utc`.
MFE/MAE e simulacoes de saida usam somente candles entre abertura e fechamento.
A antecipacao da entrada em um candle fica bloqueada porque a decisao original
nao era conhecida antes da abertura.

## Probe economico de 2026-07-14

O probe no-write observou:

- 567 trades fechados no snapshot;
- 562 trades financeiramente reconciliados e elegiveis;
- 5 trades bloqueados por campos ou identidade contabil incompleta;
- periodo de 2026-06-01 a 2026-07-14;
- PnL liquido de `-66.85397257` USDT;
- PnL bruto de `-32.76495000` USDT;
- fees de `34.004842639` USDT;
- funding normalizado de `0.084179891` USDT;
- win rate de `38.61%`;
- profit factor de `0.7409`;
- expectancy de `-0.11896` USDT/trade;
- drawdown maximo de `76.0699` USDT;
- maior sequencia de perdas de 15 trades.
- 534 trades com caminho `5m` alinhado e 28 sem cobertura completa;
- 257 trades tiveram MFE positivo, mas terminaram com PnL negativo;
- 63 perdas nao apresentaram recuperacao positiva nos candles observados.

### Segmentos observados

Os segmentos mais lucrativos incluem `exit_reason=roi` (`+191.1921` USDT) e
as horas UTC 02, 12 e 07. `exit_reason` e apenas diagnostico pos-trade e nunca e
convertido em filtro de entrada.

Os segmentos prejudiciais incluem stop loss (`-258.0461` USDT), junho de 2026
(`-72.1391` USDT), BTCUSDT (`-21.1589` USDT), segundas e tercas-feiras e faixas
de stake. As regras candidatas aceitam somente informacao disponivel na entrada.

### Acoes economicas

1. Promover apenas para backtest o bloqueio de `BTCUSDT`: 226 trades afetados,
   delta historico `+21.1589`, reducao de drawdown `18.1619` e delta OOS
   `+10.9119` USDT. Isso nao autoriza bloqueio paper/runtime.
2. Backtestar filtros de terca-feira, segunda-feira, lado short e semana W26,
   todos com efeito positivo nos dois lados do split temporal 70/30. Nao combinar
   filtros antes de medir sobreposicao e estabilidade walk-forward.
3. Nao atrasar entradas: os atrasos de 1, 2, 3 e 5 candles pioraram o PnL total,
   embora alguns deltas OOS isolados tenham sido positivos.
4. Nao promover mudanca de saida: todos os grids testados tiveram delta OOS
   negativo. O time stop de 60 minutos melhorou o full sample em `+19.8907`, mas
   perdeu `-12.4786` no OOS.
5. Manter stake operacional. A reducao de 50% apos duas perdas reduziu drawdown
   para `52.9537` e teve delta OOS `+3.0238`, mas ainda manteve PnL negativo e
   risco historico de ruina; exige backtest independente.

Todas as decisoes permanecem `MANTER_EM_RESEARCH`, exceto regras explicitamente
marcadas apenas como `PROMOVER_PARA_BACKTEST`.

## Lote OCR de 511 imagens

A fonte explicita e `E:\bitradex\Bitradex prints`. Somente os 511 arquivos no
nivel raiz pertencem ao lote atual. Os 3.563 arquivos em subdiretorios historicos
sao inventariados como ignorados para impedir repeticao de OCR.

O contrato obrigatorio e
`E:\Apoio Futuros\Handoff Canônico - Extração OCR.pdf`.
O analyzer registra caminho, tamanho e SHA-256, mas nao executa OCR. A pasta de
imagens nunca entra diretamente no dataset financeiro.

Quando o OCR for executado, deve usar apenas as ROIs dos retangulos pretos,
ignorar o topo vermelho, persistir texto bruto por campo, normalizar por campo e
produzir somente:

- pacote OCR bruto;
- pacote normalizado;
- staging/review;
- relatorio de validacao;
- candidate import-ready;
- preview contra o Master atual;
- snapshot research-only.

A regra atual prevalece sobre os blockers historicos do handoff: `order_id` vazio
nao e erro de extracao e duplicidade isolada de `order_id` nao bloqueia. A
deduplicacao prioriza SHA-256 da imagem e depois timestamps, moeda, lado, precos,
volumes, PnL e taxas. Imagens hash-identicas sao duplicidade real do lote.

Esta entrega nao roda OCR, nao escreve no Master, nao reconstrui Phase 5 e nao
executa IA Shadow incremental.

## CLI

```powershell
python .\scripts\build_profit_research_paper_analysis_v1.py `
  --project-root . `
  --new-trades-source "E:\bitradex\Bitradex prints" `
  --ocr-handoff "E:\Apoio Futuros\Handoff Canônico - Extração OCR.pdf" `
  --no-write `
  --json
```

Escrita research-only exige `--write` explicito.

## Limitacoes

- 28 trades nao possuem caminho completo de candles no artefato atual;
- custos contrafactuais de saida reutilizam fee/funding observados;
- ha apenas um split temporal 70/30, ainda sem walk-forward multiplo;
- filtros podem se sobrepor e nao representam causalidade;
- os 511 prints ainda nao foram convertidos em candidate OCR revisado;
- nenhuma conclusao autoriza mudanca paper, live, stake ou modelo.
