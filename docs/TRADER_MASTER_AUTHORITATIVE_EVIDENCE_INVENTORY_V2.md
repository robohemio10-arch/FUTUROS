# Trader Master Authoritative Evidence Inventory V2

## Objetivo

O Bloco 1B.2 inventaria, em modo estritamente read-only, evidências que possam explicar os gaps de linhagem das coortes legadas `full_ocr_3141` e `manual_queue_resolved`. O inventário não preenche campos, não cria Fingerprint V2, não desenha ou aplica bridge e não altera o Trader Master.

Os doze campos prioritários são:

- `account_scope_hash`
- `order_id_namespace`
- `source_trade_id`
- `market_type`
- `contract_type`
- `settlement_currency`
- `quantity_unit`
- `contract_size`
- `gross_pnl`
- `trading_fee`
- `funding_fee`
- `epsilon_abs_fonte`

## Fronteira operacional

O domínio lê o Master pelo adapter institucional baseado em cópia temporária. Cada evidência lida é validada dentro do `ProjectRoot`, rejeita symlink, recebe SHA-256 e tamanho antes e depois e é inspecionada somente em cópia temporária. SQLite inclui DB, WAL e SHM na cópia e é aberto com URI `mode=ro` e `PRAGMA query_only=ON`.

Arquivos `.env`, nomes associados a credenciais, caches e caminhos externos são ignorados ou bloqueados. Findings de conteúdo secreto tornam o artefato inelegível e nenhum conteúdo bruto é incluído no relatório. ZIPs não são extraídos. Imagens e PDFs recebem apenas inventário de metadata; nenhum OCR é executado.

O modo padrão não escreve. `--write-report` pode materializar somente JSON e Markdown em `data/reports`. Não existem flags de apply, bridge, import, preenchimento, extração ou OCR.

## Autoridade

Encontrar uma coluna ou um nome de arquivo não comprova autoridade. Uma declaração sanitizada precisa usar `schema_version=trader_master_authoritative_evidence_v2`, identificar produtor e cohort, documentar semântica por campo e declarar `provenance_classification=authoritative`.

Um join somente é aceito quando o contrato declara uma destas classes:

- `exact_native_id`
- `exact_source_row_provenance`
- `versioned_deterministic_composite_key`

Além disso, `deterministic_per_row`, `uniqueness_verified` devem ser verdadeiros e `fuzzy_matching` deve ser falso. `cohort_level_only` e `not_joinable` nunca sustentam bridge.

Campos de instrumento exigem escopo versionado com símbolos, mercado e intervalo temporal. `account_scope_hash` exige atestação sanitizada, provenance e igualdade com o hash fornecido explicitamente, sem persistir o identificador original. Campos financeiros exigem colunas-fonte e fórmulas versionadas; ausência nunca é substituída por zero e `net_pnl` nunca é decomposto em componentes desconhecidos.

## Classificações

Cada par cohort/campo recebe exatamente uma classificação:

- `authoritative_and_joinable`
- `authoritative_but_not_joinable`
- `informational_only`
- `conflicting`
- `missing`

Fontes autoritativas joináveis com digests incompatíveis são classificadas como conflito. Nenhuma fonte é selecionada automaticamente nesse cenário.

## Decisões

- `AUTHORITATIVE_EVIDENCE_COMPLETE_AND_JOINABLE`: todos os campos dos dois cohorts têm evidência autoritativa e join determinístico. A única ação permitida é desenhar um contrato de bridge futuro.
- `PARTIAL_AUTHORITATIVE_EVIDENCE_FOUND`: existe evidência joinável, mas a cobertura permanece incompleta.
- `AUTHORITATIVE_EVIDENCE_NOT_JOINABLE`: há autoridade documentada sem vínculo determinístico por linha.
- `CONFLICTING_AUTHORITATIVE_EVIDENCE`: fontes equivalentes divergem.
- `NO_AUTHORITATIVE_EVIDENCE_FOUND`: não foi encontrada evidência autoritativa utilizável.

Nenhuma decisão torna uma linha importável. `fingerprint_generation_allowed`, `bridge_applied`, `import_performed`, writers operacionais, ordens e acesso privado permanecem falsos.

## Execução

No-write com roots padrão:

```powershell
python .\scripts\inventory_trader_master_authoritative_evidence_v2.py `
  --project-root . `
  --trader-master data/trades/trades_master.parquet `
  --source-profile config/freqtrade_paper_closed_trades_source_profile_v2.json `
  --account-scope-hash "<SHA256_SANITIZADO>" `
  --authoritative-sqlite data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite `
  --no-write `
  --json
```

Roots explícitos podem ser repetidos:

```powershell
python .\scripts\inventory_trader_master_authoritative_evidence_v2.py `
  --project-root . `
  --account-scope-hash "<SHA256_SANITIZADO>" `
  --evidence-root data `
  --evidence-root backups `
  --evidence-root docs `
  --evidence-root config `
  --no-write `
  --json
```

Relatório opcional:

```powershell
python .\scripts\inventory_trader_master_authoritative_evidence_v2.py `
  --project-root . `
  --account-scope-hash "<SHA256_SANITIZADO>" `
  --write-report `
  --json
```

As saídas são `data/reports/trader_master_authoritative_evidence_inventory_v2.json` e `.md`, ignoradas pelo Git.

## Validação

```powershell
python -m compileall scripts smartcrypto tests
python -m pytest tests/test_trader_master_authoritative_evidence_inventory_v2.py -q
python -m ruff check smartcrypto/data/trader_master_fingerprint_v2 scripts/inventory_trader_master_authoritative_evidence_v2.py tests/test_trader_master_authoritative_evidence_inventory_v2.py
python -m mypy smartcrypto/data/trader_master_fingerprint_v2 scripts/inventory_trader_master_authoritative_evidence_v2.py
python -m bandit -q -r smartcrypto/data/trader_master_fingerprint_v2 scripts/inventory_trader_master_authoritative_evidence_v2.py
```

O probe real deve começar sem `--write-report`. Quantidade de arquivos nunca é prova de autoridade.
