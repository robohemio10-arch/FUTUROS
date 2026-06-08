# Freqtrade Paper DB Authority and Soak Recalculation

## Objetivo

Esta frente institucionaliza qual SQLite paper do Freqtrade deve ser usado para medir o
soak paper/shadow. O problema corrigido era a coexistencia de bancos antigos e snapshots
atuais, causando `paper_soak_report.json` com `observed_soak_days` desatualizado.

O fluxo continua estritamente paper/shadow. Ele le SQLite em modo read-only, nao altera o
DB operacional, nao envia ordens e nao acessa endpoints privados de exchange.

## Fontes Freqtrade paper

As fontes conhecidas avaliadas pelo resolvedor sao:

- `data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite`: snapshot compativel
  com dashboard e Fase 14.
- `freqtrade/user_data/tradesv3.paper.sqlite`: caminho historico que pode ficar stale
  quando o SQLite operacional esta em Docker volume Linux.
- `data/runtime/freqtrade_active_path_snapshot.sqlite`: snapshot runtime auxiliar, tambem
  sujeito a atraso.
- `data/evidence/db_persistence_fault_20260601/tradesv3.paper.sqlite`: evidencia historica
  do incidente de persistencia.

O snapshot atual pode conter mais trades que os bancos antigos. Nessa situacao, os bancos
com menor `total_trades` ou `last_activity_date` antigo aparecem como `stale_candidates`.

## Regra de autoridade

O resolvedor aplica uma regra deterministica:

- caminho explicito valido vence;
- sem caminho explicito, vence o DB com tabela `trades`, maior `total_trades` e
  `last_activity_date` mais recente;
- DB sem tabela `trades` e DB vazio sao rejeitados;
- todos os candidatos sao reportados com contagens, datas e razao de selecao.

Campos principais gerados:

- `freqtrade_paper_db_selected`
- `freqtrade_paper_db_selection_reason`
- `freqtrade_paper_db_candidates`
- `freqtrade_paper_db_stale_candidates`
- `trades_total`, `trades_open`, `trades_closed`
- `first_trade_open_date`, `last_trade_activity_date`
- `observed_soak_days_from_trade_history`
- `remaining_soak_days_from_trade_history`

## Recalculo do soak

Quando um DB autorizado e selecionado, o `paper_soak_report.json` passa a calcular a janela
de observacao usando:

- inicio: `first_open_date` da tabela `trades`;
- fim: `last_activity_date`, preferindo fechamento/atividade mais recente.

No estado observado em junho de 2026, isso move o soak de aproximadamente `1.11` dia para
cerca de `6.67` dias. Ainda assim, com meta de `30` dias, restam aproximadamente `23.33`
dias e o sistema deve permanecer bloqueado.

## Comandos

Usar caminho explicito do snapshot compativel com dashboard:

```powershell
python scripts/build_paper_shadow_soak_report.py `
  --freqtrade-paper-db data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite `
  --freqtrade-paper-db-authority-report data/reports/freqtrade_paper_db_authority_report.json `
  --required-soak-days 30 `
  --strict
```

Usar autodiscovery dos candidatos conhecidos:

```powershell
python scripts/build_paper_shadow_soak_report.py `
  --freqtrade-paper-db-auto-discover `
  --freqtrade-paper-db-authority-report data/reports/freqtrade_paper_db_authority_report.json `
  --required-soak-days 30 `
  --strict
```

## Interpretacao

`status=blocked` continua correto quando:

- `observed_soak_days` esta abaixo de `required_soak_days`;
- a politica Monte Carlo esta em `no_trade`;
- qualquer safety flag indica live/order/private exchange.

O recalculo melhora a auditabilidade do tempo paper observado; ele nao e um release gate
positivo por si so.

## Garantias de seguranca

- `live_trading_enabled=false`
- `order_submission_enabled=false`
- `real_order_submission_enabled=false`
- `sends_orders=false`
- `exchange_private_access=false`
- leitura SQLite em modo `mode=ro`
- nenhum arquivo em `data/`, SQLite, parquet, csv, xlsx, logs ou evidencias deve ser
  versionado
- nenhum modelo, registry, signal producer, stake ou leverage e alterado
