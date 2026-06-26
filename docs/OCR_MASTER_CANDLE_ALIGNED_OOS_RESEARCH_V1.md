# OCR Master Candle Aligned OOS Research V1

## Objetivo

Esta branch pivota a trilha de pesquisa para o caminho correto: `trades_master.xlsx` + candles reais. Ela não depende de fonte paper/Freqtrade para medir edge histórico, regimes ou hipóteses H1/H2/H6.

## Escopo

- Carregar `trades_master` em modo read-only.
- Descobrir e carregar candles reais em roots explícitos.
- Alinhar cada trade ao contexto de candle pré-entrada.
- Calcular features de lookback: 5m, 10m e 30m.
- Produzir métricas por slices: symbol, side, day, hour, duration_bucket e regime_bucket.
- Medir H1, H2 e H6 em modo research-only.

## Hipóteses

- H1: fast stop / duração <= 30m / saída rápida negativa.
- H2: ETH long como cluster estruturalmente negativo ou dependente de regime.
- H6: candidate shadow rule baseada em retornos de 10m e 30m: `lb_10m_ret_close <= -0.0038501215827868 AND lb_30m_ret_close <= -0.0060685748963285`.

## Segurança

Esta branch não altera Freqtrade, RiskManager, Qlib runtime, IA Shadow runtime, SQLite operacional, dashboard runtime, registry, modelos, regras operacionais, live, canary ou orders.

`allow_runtime_read=false` é o padrão. Escrita só ocorre com `--write` e `--output-path` explícitos.

## Comandos

```powershell
python .\scripts\build_ocr_master_candle_aligned_oos_research_v1.py `
  --project-root . `
  --no-write `
  --json
```

```powershell
python .\scripts\build_ocr_master_candle_aligned_oos_research_v1.py `
  --project-root . `
  --allow-runtime-read `
  --trades-master ".\data\trades\trades_master.xlsx" `
  --candle-root ".\data" `
  --candle-root ".\freqtrade\user_data\data" `
  --candle-root ".\user_data\data" `
  --no-write `
  --json
```
