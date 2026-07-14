# Freqtrade Paper Authoritative Batch Closeout V2

## Objetivo

O fechamento V2 reapresenta ao Validator V2 somente os trades paper cuja saída foi
recuperada por evidência autoritativa de ordens preenchidas. O processo é opt-in,
read-only, executado em memória e não altera Trader Master, CSV, Parquet, XLSX,
SQLite, fingerprint spec ou epsilon.

O contrato foi desenhado para o lote congelado de 558 linhas do Bloco 1A.1:

- antes da forense: 553 aceitos e 5 em quarentena;
- após a recuperação: 555 aceitos e 3 em quarentena;
- recuperados: `freqtrade-paper-221` e `freqtrade-paper-234`;
- permanecem bloqueados: `freqtrade-paper-141`, `freqtrade-paper-258` e
  `freqtrade-paper-561`.

As contagens são calculadas a partir da fonte lida. O adapter não trunca lotes nem
força o número 558. Se novos trades fechados forem acrescentados, os totais crescem,
mas o delta forense continua limitado aos dois IDs aprovados.

## Fonte autoritativa

A recuperação utiliza exclusivamente:

`data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite`

O snapshot é lido duas vezes por componentes independentes:

1. adapter financeiro do Bloco 1A.1;
2. forense de ordens do Bloco 1A.2.

Ambas as leituras usam cópia temporária, URI SQLite `mode=ro` e
`PRAGMA query_only`. Os hashes de DB, WAL e SHM precisam coincidir entre os dois
readers. Qualquer divergência bloqueia todas as recuperações.

O caminho `freqtrade/user_data/tradesv3.paper.sqlite` permanece explicitamente
não autoritativo e proibido.

## Gates da recuperação

`quarantine_recovery.py` revalida cada evidência sem confiar apenas no texto
`recovered_authoritatively`. Para entrar no mapa imutável, o registro precisa:

1. pertencer à allowlist fixa `{221, 234}`;
2. ter preços médios de entrada e saída positivos e finitos;
3. reconciliar `filled_entry_quantity`, `filled_exit_quantity` e `trades.amount`;
4. reconciliar a entrada média com `trades.open_rate` no epsilon vigente;
5. ter residual recuperado menor ou igual ao epsilon vigente;
6. não usar `close_rate_requested`;
7. não ter recovery previamente aplicado;
8. não ter blockers remanescentes;
9. reconciliar `realized_profit` e `close_profit_abs`;
10. provar lineage nas tabelas `trades` e `orders`;
11. provar `orders.average` e `orders.filled` em `source_columns`.

Um ID fora da allowlist invalida o mapa. Um candidato permitido que falha em algum
gate permanece em quarentena, com motivos estruturados.

## Aplicação em memória

`apply_authoritative_recoveries` cria cópias dos registros SQLite. Apenas
`close_rate` pode ser substituído. `open_rate`, quantidade, fees, funding, timestamps
e lucro reportado permanecem os valores autoritativos originais.

Antes do override, a função volta a comparar `open_rate` e `amount` da linha SQLite
com a evidência forense. Isso protege contra mudança da fonte entre análise e uso.

O adapter então executa novamente:

1. reconciliação financeira independente;
2. normalização canônica;
3. Validator V2;
4. geração normal de fingerprints pelo `fingerprint_spec_v2` existente.

Nenhuma recuperação é persistida de volta no SQLite ou no arquivo tabular.

## Provenance

Cada linha recuperada registra:

- preço de fechamento original e recuperado;
- fonte `authoritative_orders_average_filled_v1`;
- versão da fórmula;
- residual recuperado;
- tabelas, IDs de evidência e colunas-fonte;
- confirmação de que `close_rate_requested` não foi usado.

As três quarentenas finais registram a decisão forense e os blockers que impediram
a recuperação.

## Operação

O comportamento anterior permanece default:

```powershell
python scripts/validate_trader_master_staging_v2.py `
  --project-root . `
  --source-profile config/freqtrade_paper_closed_trades_source_profile_v2.json `
  --account-scope-hash "<SHA256-SANITIZADO-JA-VALIDADO>" `
  --no-write `
  --json
```

O fechamento autoritativo exige flag explícita:

```powershell
python scripts/validate_trader_master_staging_v2.py `
  --project-root . `
  --source-profile config/freqtrade_paper_closed_trades_source_profile_v2.json `
  --account-scope-hash "<SHA256-SANITIZADO-JA-VALIDADO>" `
  --apply-authoritative-forensic-recovery `
  --no-write `
  --json
```

`--write-report` pode materializar somente os relatórios permitidos pelo staging
runner. `--write-to-master` continua bloqueado.

## Estado de segurança

- `write_to_master_performed=false`
- `recovery_writes_performed=false`
- `research_pipeline_writes_runtime=false`
- `sends_exchange_orders=false`
- `exchange_private_access=false`
- `recovery_changes_fingerprint_spec=false`
- `recovery_changes_epsilon=false`

O status global continua `blocked` enquanto existirem três registros em quarentena.
O fechamento bem-sucedido é expresso separadamente como
`batch_closeout_status=completed_with_quarantine`.
