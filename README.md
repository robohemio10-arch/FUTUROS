# SmartCrypto Starter

Projeto base para IA preditiva em Binance Futures usando Docker, Freqtrade, Qlib/ML, RiskManager e Dashboard.

## Princípio central

```text
Qlib/ML decide.
RiskManager autoriza.
Freqtrade executa.
Dashboard observa e comanda.
Docker isola.
SQLite, logs e métricas auditam.
```

## Modo padrão

Este starter vem em modo paper por padrão.

- `LIVE_ENABLED=false`
- `ORDER_SUBMISSION_ENABLED=false`
- `REAL_ORDER_SUBMISSION_ENABLED=false`
- `dry_run=true` no Freqtrade
- sem chaves reais
- sem envio direto de ordens pelo SmartCrypto

## Primeiros comandos

```bash
cp .env.example .env
docker compose -f docker-compose.paper.yml up --build -d
docker compose -f docker-compose.paper.yml ps
docker compose -f docker-compose.paper.yml logs -f --tail=300
```

## Pipeline de dados

```bash
python scripts/download_market_data.py
python scripts/build_market_features.py
python scripts/build_sqlite_db.py
python scripts/build_trade_enriched.py
python scripts/train_model.py
python scripts/export_freqtrade_signals.py
```

## Estrutura

```text
smartcrypto/
  data/              Dados, features, SQLite, sinais e evidências
  config/            Configurações de runtime, risco, universo e modelo
  docker/            Dockerfiles
  docs/              Arquitetura, contratos e roadmap
  freqtrade/         Config e strategy do Freqtrade
  scripts/           Entrypoints operacionais
  smartcrypto/       Código Python principal
  tests/             Testes iniciais
```

## Aviso operacional

Este projeto é um esqueleto inicial. Antes de qualquer live real, rode paper/soak, reconciliação, auditoria de sinais, kill switch e validação walk-forward.
