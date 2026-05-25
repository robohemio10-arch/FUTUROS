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
