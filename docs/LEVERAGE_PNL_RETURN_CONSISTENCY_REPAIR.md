# Leverage, PnL And Return Consistency Repair

Esta fase é uma auditoria/reparo offline para consistência entre alavancagem, PnL e retorno bruto depois do reparo de escala/OCR de preço. Ela não altera o input original, não chama exchange, não envia ordens e não libera live trading.

## Por Que Ainda Há Bloqueios

O reparo de preço reduziu bastante os retornos absurdos, mas o sidecar normalizado ainda pode ficar `BLOCKED` por:

- `leverage_invalid_defaulted_to_1`: alavancagem ausente ou inválida.
- `raw_return_discrepant`: retorno bruto incompatível com o retorno por preço.
- `pnl_incompatible`: PnL informado incompatível com preço e volume. Como o PnL original tem semântica mista nos dados OCR/importados, essa flag é `WARNING` quando preço, side, volume, leverage e retorno plausível estão íntegros.
- `net_return_extreme`: retorno alavancado ainda impossível ou fora do limite.

Esta fase separa registros aproveitáveis de registros que ainda precisam ficar bloqueados.

## Conceitos

- `price_return_pct`: retorno calculado só por preço e direção.
- `leveraged_price_return_pct`: `price_return_pct * leverage`.
- `raw_return`: retorno bruto importado do OCR/origem; pode estar em escala errada ou conter sentinelas como `1999.999999`.
- `pnl`: resultado financeiro absoluto informado. É comparado contra um PnL esperado calculado por preço e volume, sem multiplicar por alavancagem.
- `pnl_consistent`: PnL modelado por preço e volume para linhas `OK`/`WARNING`. O `pnl_original` permanece preservado para auditoria, mas não bloqueia a reconstrução de retorno quando os campos críticos estão válidos.
- `pnl_semantics_guess`, `pnl_error_abs`, `pnl_error_pct`, `pnl_warning_only`: diagnósticos para separar PnL coerente, possivelmente líquido de custos, misto ou não confiável.

Em contratos lineares, a alavancagem altera o ROI/margem, mas não multiplica o PnL absoluto em USDT. Por isso:

- Long: `expected_pnl_from_price = (exit_price - entry_price) * volume`.
- Short: `expected_pnl_from_price = (entry_price - exit_price) * volume`.
- `leveraged_price_return_pct = price_return_pct * leverage`.

Alavancagem inválida não vira `1` silenciosamente. Com `default-leverage-policy=block`, registros sem alavancagem confiável ficam `BLOCKED`.

## Status

- `OK`: preço, side, volume, leverage e PnL são coerentes.
- `WARNING`: retorno bruto ou PnL original está discrepante, mas preço, side, volume e leverage são utilizáveis para diagnóstico/research.
- `BLOCKED`: leverage inválido, PnL incompatível grave, retorno extremo ou campo crítico ausente.

## Sequência Recomendada

1. Reparar escala/OCR de preço.

```powershell
python scripts/repair_price_scale_ocr_anomalies.py `
  --input data/features/trade_enriched.parquet `
  --output data/features/trade_price_scale_repaired.parquet `
  --report data/reports/trade_price_scale_ocr_repair_report.json
```

2. Reparar inputs financeiros.

```powershell
python scripts/repair_trade_financial_inputs.py `
  --input data/features/trade_price_scale_repaired.parquet `
  --output data/features/trade_financial_inputs_repaired.parquet `
  --report data/reports/trade_financial_input_repair_report.json `
  --entry-price-column entry_price_repaired `
  --exit-price-column exit_price_repaired
```

3. Rodar consistência leverage/PnL/return.

```powershell
python scripts/repair_leverage_pnl_return_consistency.py `
  --input data/features/trade_financial_inputs_repaired.parquet `
  --output data/features/trade_financial_consistency_repaired.parquet `
  --report data/reports/leverage_pnl_return_consistency_report.json `
  --default-leverage-policy block `
  --raw-return-discrepancy-threshold 5 `
  --pnl-tolerance-pct 5
```

4. Gerar sidecar normalizado com colunas consistentes.

```powershell
python scripts/build_normalized_return_sidecar.py `
  --input data/features/trade_financial_consistency_repaired.parquet `
  --output data/features/training_normalized_return_sidecar.parquet `
  --report data/reports/normalized_return_sidecar_report.json `
  --entry-price-column entry_price_repaired `
  --exit-price-column exit_price_repaired `
  --side-column side_repaired `
  --volume-column volume_repaired `
  --leverage-column leverage_consistent `
  --raw-return-column raw_return_consistent `
  --pnl-column pnl_consistent
```

5. Rodar avaliação financeira normalizada.

```powershell
python scripts/run_normalized_financial_evaluation.py `
  --features data/features/training_dataset_open_decision_clean.parquet `
  --sidecar data/features/training_normalized_return_sidecar.parquet `
  --sidecar-report data/reports/normalized_return_sidecar_report.json `
  --output-report data/reports/normalized_financial_evaluation_report.json `
  --return-column net_return_pct
```

Se o sidecar continuar `BLOCKED`, a avaliação deve continuar bloqueada. Isso é comportamento esperado e seguro.

## Segurança Operacional

Esta etapa é somente offline/research. Ela não altera `.env`, não habilita live trading, não chama API privada, não envia ordem real e não entra no caminho crítico do paper 24h.
