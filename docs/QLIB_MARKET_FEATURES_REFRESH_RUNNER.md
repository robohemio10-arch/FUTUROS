# Qlib Market Features Refresh Runner

Este runner atualiza `data/features/market_features_60d.parquet` antes da geração de predições Qlib. Ele existe para fechar o ciclo:

1. market features recentes;
2. predição Qlib com `generated_at` recente;
3. `phase13_generate_active_signals.py` aceitando apenas sinais com dado de entrada fresco.

## Segurança

O fluxo é paper/shadow only. Ele não habilita live trading, não envia ordem, não lê conta privada e não usa API privada de exchange. A fonte online opcional usa apenas candles públicos de mercado e grava somente artefatos runtime ignorados pelo Git.

## Como Funciona

Por padrão o runner:

- lê o parquet existente `data/features/market_features_60d.parquet`;
- baixa candles públicos recentes de BTCUSDT e ETHUSDT em `5m`;
- reconstrói features recentes com o builder institucional;
- concatena com o histórico existente;
- remove duplicatas por `symbol`, `tf` e `ts`;
- valida schema mínimo esperado pelo Qlib;
- bloqueia se o `market_features_max_timestamp` continuar stale.

O relatório expõe:

- `market_features_rows`;
- `market_features_max_timestamp`;
- `market_features_age_minutes`;
- `max_source_age_minutes`;
- `public_download`;
- `status`: `ok` ou `blocked`;
- `reason`: `missing_source`, `stale_source` ou `invalid_schema`.

## Execução

```powershell
$env:PYTHONPATH = "E:\FUTUROS"
python .\scripts\run_qlib_market_features_refresh.py
python .\scripts\run_qlib_fresh_predictions.py
python .\scripts\phase13_generate_active_signals.py --force-from-predictions --validity-minutes 45
```

Resultado esperado quando o mercado está acessível e recente:

- `market_features_60d.parquet` com timestamp recente;
- `run_qlib_fresh_predictions.py` com `status=ok`;
- Phase 13 com `status=ok`, `written_pinned=true` e sinais ativos.

## Dashboard

A aba `Qlib / Predições` mostra também:

- `market_features_path`;
- `market_features_rows`;
- `market_features_max_timestamp`;
- `market_features_age_minutes`;
- `market_features_status`.

Isso ajuda a distinguir predição stale por arquivo antigo de predição gerada agora com dado de entrada velho.

## Não Libera Live

Este runner apenas atualiza dados de pesquisa/paper e alimenta guards já existentes. Ele não muda strategy, Docker, `.env`, `START_PAPER_24H` nem qualquer permissão de execução real.
