# Fase 14 — Paper Trade Lifecycle + Feedback Sync

A Fase 13 comprovou que a strategy lê sinais e gera eventos de entrada. A Fase 14 cobre o próximo trecho do ciclo:

```text
entrada paper → posição aberta → fechamento → feedback → Fase 5 → dataset
```

## Por que esta fase existe

O projeto já possui:

- Qlib gerando predições;
- sinais persistentes em `data/freqtrade_signals.json`;
- pinned signals em `data/runtime/active_freqtrade_signals.json`;
- strategy aceitando sinais;
- SQLite do Freqtrade com trades abertos.

Agora precisamos transformar trades fechados em feedback de treino.

## Segurança

Esta fase é paper-only. Ela apenas lê SQLite e exporta arquivos. Não executa ordens reais.

## Leitura SQLite via snapshot

O SQLite paper do Freqtrade pode estar em bind mount dentro do container, por exemplo:

```text
/app/freqtrade_user_data/tradesv3.paper.sqlite
```

Em algumas execuções operacionais, o arquivo existe, mas `sqlite3.connect` direto nesse caminho retorna:

```text
sqlite3.OperationalError: unable to open database file
```

Para evitar falha falsa da Fase 14, a leitura agora cria um snapshot local temporário do SQLite antes de abrir o banco. O fluxo é:

```text
SQLite original paper -> cópia temporária em /tmp -> leitura da tabela trades -> limpeza do snapshot
```

Quando existirem arquivos auxiliares do SQLite (`-wal` e `-shm`), eles também são copiados para o snapshot. Isso mantém a Fase 14 read-only em relação ao banco original e evita prender o arquivo ativo do Freqtrade.

Se a criação ou leitura do snapshot falhar, a Fase 14 registra relatório `blocked` com erro explícito, sem mascarar o problema.

Essa mudança não habilita live trading, não altera sinais, não envia ordens, não modifica strategy e não muda o `START_PAPER_24H`.

## Comportamento com posições abertas

Se `max_open_trades = 2` e já existem 2 posições abertas, o Freqtrade pode não abrir novas entradas. Nesse caso, a fase reporta `saturated: true`.

## Feedback fechado

Quando houver trades fechados, a fase gera:

```text
data/trades/freqtrade_paper_trades_raw.parquet
data/trades/freqtrade_paper_closed_smartcrypto.parquet
data/trades/freqtrade_paper_closed_smartcrypto.csv
data/trades/inbox/freqtrade_paper_closed_trades.csv
```

A Fase 5 pode importar o CSV da inbox e reconstruir os datasets.
