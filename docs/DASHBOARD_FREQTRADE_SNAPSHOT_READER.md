# Dashboard Freqtrade Snapshot Reader

O dashboard SmartCrypto Paper agora le o SQLite paper do Freqtrade via snapshot temporario, seguindo o mesmo padrao validado na Fase 14.

## Problema

Em ambiente paper, o banco pode existir em:

```text
freqtrade/user_data/tradesv3.paper.sqlite
```

ou dentro do container em:

```text
/app/freqtrade_user_data/tradesv3.paper.sqlite
```

Abrir esse SQLite diretamente pode falhar quando ele esta em bind mount ou em uso pelo Freqtrade. Antes, o dashboard capturava a excecao e devolvia DataFrame vazio, fazendo as abas `Freqtrade`, `Trades paper` e `Performance` mostrarem 0 trades silenciosamente.

## Solucao

O dashboard usa `smartcrypto.data.paper_trade_lifecycle.read_trades(..., use_snapshot=True)`.

Fluxo:

```text
SQLite Freqtrade paper -> snapshot temporario -> leitura da tabela trades -> limpeza do snapshot
```

O painel passa a exibir tambem:

- `db_snapshot_used`
- `db_last_read_at`
- `db_path`
- erro claro quando a leitura falhar

## Abas afetadas

- `Freqtrade`
- `Trades paper`
- `Performance`

Essas abas continuam read-only. Elas mostram contagens, PnL paper, win rate, profit factor e tabelas de trades recentes sem alterar o banco.

## Seguranca

Esta mudanca nao habilita live trading, nao envia ordens, nao altera `.env`, nao altera Docker, nao altera `START_PAPER_24H`, nao chama API privada e nao muda strategy.

Os arquivos runtime em `data/`, logs, SQLite, CSV, Parquet, JSON e evidencias continuam fora do Git.
