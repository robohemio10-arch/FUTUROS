# Trader Master Read-only Reconciliation V2

## Objetivo

Este bloco compara, em memoria, o lote paper validado pelo adapter V2 com o
`data/trades/trades_master.parquet`. Ele produz evidencia para revisao de uma
eventual importacao futura, mas nao possui autoridade para importar, alterar o
Master ou promover registros.

O lote paper e derivado a cada execucao com recuperacao forense autoritativa
habilitada. Nenhuma contagem historica e fixada no codigo. Registros que ainda
estiverem em quarentena permanecem fora da reconciliacao.

## Fontes e integridade

- O CSV paper, sua replica e o snapshot SQLite/WAL/SHM sao hasheados antes e
  depois da reconciliacao.
- O Trader Master aceito e exclusivamente o Parquet informado, por default
  `data/trades/trades_master.parquet`.
- O Master deve estar dentro do projeto, ser arquivo regular, nao ser symlink e
  possuir extensao `.parquet`.
- A leitura ocorre por copia em `TemporaryDirectory`; hash e tamanho da fonte
  sao verificados antes e depois.
- Nao existe fallback para XLSX, inbox ou arquivo de compatibilidade.

## Identidade V2

A reconciliacao reutiliza exclusivamente `normalize_trade_row`,
`canonical_json`, `row_fingerprint_for`, `primary_identity_for` e
`canonical_trade_id_for`. O schema legado e inventariado antes da adaptacao.
Valores ausentes de venue, account scope, namespace, fees, funding, gross PnL
ou contract size nao sao fabricados.

Uma linha legada que nao satisfaz o contrato permanece no relatorio como
`master_row_unverifiable`. Coincidencia de `order_id` legado e apenas evidencia
de `ambiguous_legacy_identity_match`; ela nunca decide duplicidade.

Se uma linha incoming valida nao tiver duplicidade, conflito ou ambiguidade
comprovados, mas o Master ainda contiver qualquer linha nao verificavel, ela e
classificada como `incoming_blocked_by_unverifiable_master`. Ausencia de prova
nao e tratada como prova de ausencia. Essa linha recebe `import_eligible=false`
e nunca entra na contagem de novos candidatos.

## Decisoes

- `READY_FOR_CONTROLLED_IMPORT_REVIEW`: ha candidatos novos, o Master e
  completamente verificavel e nao existe blocker.
- `NO_NEW_TRADES`: todas as linhas sao duplicatas exatas e nao ha blockers.
- `BLOCKED_BY_MASTER_IDENTITY_CONFLICTS`: identidade duplicada ou conflito
  financeiro no Master.
- `BLOCKED_BY_UNVERIFIABLE_MASTER_ROWS`: o schema legado impede prova completa.
- `BLOCKED_BY_FINGERPRINT_COLLISION`: mesmo hash aponta para payload diferente.

Mesmo `READY_FOR_CONTROLLED_IMPORT_REVIEW` e somente evidencia. Nenhuma decisao
executa importacao.

Quando a decisao for bloqueada por linhas nao verificaveis,
`new_trade_candidate_count=0`,
`projected_master_row_count_after_hypothetical_import=null` e
`projected_master_row_count_calculable=false`. O reconciliador nao soma o lote
paper ao Master legado sem antes provar sua ausencia.

## Uso

Preview sem escrita:

```powershell
python scripts/reconcile_trader_master_preview_v2.py `
  --project-root . `
  --source-profile config/freqtrade_paper_closed_trades_source_profile_v2.json `
  --account-scope-hash "<SHA256_VALIDADO>" `
  --authoritative-sqlite data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite `
  --trader-master data/trades/trades_master.parquet `
  --no-write --json
```

`--write-report` pode materializar somente JSON e Markdown sob `data/reports`.
Nao existem flags de importacao, apply, force, backup ou alteracao de epsilon.

## Limites de seguranca

O reconciliador nao chama writers legados, nao usa `build_dedup_key` como
autoridade, nao acessa exchange privada, nao envia ordens e nao altera risco,
modelo, runtime, CSV, XLSX, Parquet ou SQLite operacional.
