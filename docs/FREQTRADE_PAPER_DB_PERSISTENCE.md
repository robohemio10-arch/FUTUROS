# Freqtrade Paper DB Persistence

## Objetivo

O SQLite operacional do Freqtrade paper deixou de ficar em bind mount Windows. A persistência ativa agora usa um Docker named volume Linux, evitando divergência entre log runtime e arquivo SQLite persistido no host.

## Arquitetura

O serviço `freqtrade-paper` grava em:

```text
/freqtrade/user_data/db/tradesv3.paper.sqlite
```

Esse caminho é montado pelo volume Docker:

```text
futuros_freqtrade_paper_db
```

Configs, strategy, logs e dados continuam em bind mounts separados:

- `freqtrade/user_data/config.paper.json` como read-only;
- `freqtrade/user_data/strategies/` como read-only;
- `freqtrade/user_data/logs/` para logs paper;
- `data/` para sinais e dados usados pela strategy.

## Snapshot Read-Only

Fase 14 e dashboard não devem ler o DB operacional diretamente. Eles consomem o snapshot:

```text
data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite
```

Gere ou atualize o snapshot com:

```powershell
python .\scripts\export_freqtrade_paper_db_snapshot.py
```

O exportador monta o named volume como read-only em um container auxiliar e usa `sqlite3.backup` para criar um snapshot consistente. Ele não reinicia o Freqtrade, não envia ordens e não altera o SQLite operacional.

## Auditoria

O auditor compara o snapshot com os logs paper:

```powershell
python .\scripts\audit_freqtrade_paper_db_persistence.py
```

Se o log observar `trade_id` maior que `max(id)` do SQLite lido, o status será:

```text
log_sqlite_divergence
```

Isso indica snapshot ausente/desatualizado ou problema de persistência a investigar antes de usar feedback paper.

## Segurança

- paper/shadow only;
- `LIVE_ENABLED=false`;
- `ORDER_SUBMISSION_ENABLED=false`;
- `REAL_ORDER_SUBMISSION_ENABLED=false`;
- sem acesso privado à exchange;
- sem envio de ordens reais;
- sem alteração em `.env`;
- sem alteração em datasets ou SQLite operacional.

## Operação Recomendada

1. Mantenha o Freqtrade paper rodando com `docker-compose.paper.yml`.
2. Gere snapshot antes da Fase 14/dashboard consumir trades.
3. Rode o auditor.
4. Só use feedback se o auditor estiver `ok` ou se a divergência for explicada por snapshot antigo e corrigida com novo export.
