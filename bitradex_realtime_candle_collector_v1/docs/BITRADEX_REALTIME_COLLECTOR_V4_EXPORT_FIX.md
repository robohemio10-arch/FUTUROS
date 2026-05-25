# Bitradex Realtime Candle Collector V4 — Export Fix

## Correção principal

A V3 capturava candles e gravava no SQLite, mas a exportação periódica podia falhar com:

```text
vars() argument must have __dict__ attribute
```

A causa era o uso de `vars()` sobre dataclasses com `slots=True` no catálogo de endpoints descobertos.
A V4 troca `vars(item)` por `dataclasses.asdict(item)`.

## Correção secundária

O probe direto agora inclui a rota realmente observada pelo Playwright:

```text
/v1/future-u/market/public/q/kline
```

Também testa chamadas sem janela temporal e com `endTime`, compatíveis com os endpoints vistos no gráfico.

## Uso imediato

Após aplicar o hotfix, rode primeiro:

```powershell
.\scripts\RUN_COLLECTOR_EXPORT_ONLY.ps1
```

Isso exporta o SQLite já preenchido para CSV/Parquet sem precisar recapturar tudo.
