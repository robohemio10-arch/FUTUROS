# SMART FUTUROS — Dashboard Real Paper Data Sources V1

## Objetivo

Criar uma camada read-only para consolidar dados reais do ambiente paper em snapshot intermediário do dashboard.

Snapshot gerado em runtime:

data/reports/dashboard_real_paper_sources_snapshot.json

## Fontes lidas

data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite
data/runtime/trade_event_notifications.sqlite
data/reports/phase14_open_positions_report.json
data/reports/freqtrade_paper_db_snapshot_export.json
data/reports/phase14_output_summary.json
data/reports/trade_event_notifications_report.json
data/runtime/active_freqtrade_signals.json
data/reports/qlib_fresh_prediction_runner_report.json
data/reports/phase13_signal_producer_report.json

## Garantias

- Dashboard permanece read-only.
- SQLite é aberto em modo ro.
- Não há acesso a exchange privada.
- Não há submissão real de ordens.
- Não há envio real de mensagens pelo dashboard.
- Não há alteração de risco, modelo, configuração ou sinais ativos.
- O builder escreve somente o snapshot de saída quando --write true.

## Uso

python scripts\build_dashboard_real_paper_sources_v1.py --project-root . --json

Modo sem escrita:

python scripts\build_dashboard_real_paper_sources_v1.py --project-root . --write false --json

## Métricas reais capturadas

- total de trades paper;
- trades abertos e fechados;
- ordens dry-run/paper;
- PnL realizado absoluto;
- exposição aberta;
- win rate;
- drawdown aproximado;
- últimas operações;
- eventos de mensageria;
- status Qlib;
- sinais ativos;
- status Phase14.

## Uso futuro na Aba 01

A Aba 01 deve consumir dashboard_real_paper_sources_snapshot.json como fonte primária para reduzir UNKNOWN e renderizar dados paper reais no wallboard institucional.
