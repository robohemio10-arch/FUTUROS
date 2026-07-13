# Freqtrade Paper Closed Trades Source Profile + Read-Only Adapter V2

## Escopo

O Bloco 1A.1 enriquece, em memoria, o CSV de trades paper fechados com a evidencia
financeira do snapshot SQLite autoritativo. O resultado canonico e entregue diretamente ao
validator Trader Master V2. Nao existe writer, importacao, backfill ou comparacao com o Master.

As garantias permanecem:

- `write_to_master_performed=false`;
- `write_performed=false` no modo default;
- `research_pipeline_writes_runtime=false`;
- `sends_exchange_orders=false`;
- `exchange_private_access=false`.

## Fontes e autoridade

| Papel | Caminho | Politica |
| --- | --- | --- |
| CSV primario | `data/trades/inbox/freqtrade_paper_closed_trades.csv` | Fonte tabular do Phase14 |
| Replica | `data/trades/freqtrade_paper_closed_smartcrypto.csv` | Mesmo lote quando SHA-256 for identico |
| Financeiro autoritativo | `data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite` | Somente copia temporaria e `query_only` |
| Nao autoritativo | `freqtrade/user_data/tradesv3.paper.sqlite` | Rejeitado explicitamente |

No inventario de 13 de julho de 2026, os dois CSVs tinham 558 linhas e o mesmo SHA-256.
Eles representam um lote logico, nao dois lotes. O snapshot continha 558 trades fechados e
dois trades abertos, que nao participam do join.

O adapter calcula SHA-256 do DB, WAL e SHM antes e depois. O arquivo original nunca e aberto
pelo SQLite: DB e sidecars existentes sao copiados para um diretorio temporario, a copia e
aberta com `mode=ro`, e `PRAGMA query_only=ON` e verificado antes do `SELECT`.

## Produtor e origem das colunas CSV

O produtor e `smartcrypto.data.paper_trade_lifecycle.collect_closed_feedback`, usando
`normalize_closed_trades` sobre o snapshot Freqtrade paper.

| CSV | Origem Freqtrade | Semantica |
| --- | --- | --- |
| `moeda` | `pair` | Par normalizado para `BTCUSDT`/`ETHUSDT` |
| `fechar_side` | `is_short` | `long` quando falso, `short` quando verdadeiro |
| `leverage` | `leverage` | Alavancagem registrada no trade |
| `order_id` | `trades.id` | `freqtrade-paper-{id}`; nao e order ID da exchange |
| `pnl_fechado` | `close_profit_abs`, fallback produtor `realized_profit` | PnL reportado do trade fechado |
| `taxa_lucros_perdas_fechados_pct` | `close_profit` | Retorno percentual reportado |
| `preco_abertura` | `open_rate` | Preco medio de entrada |
| `preco_fechamento` | `close_rate` | Preco de fechamento; pode estar ausente |
| `volume_posicao`, `volume_fechado` | `amount` | Quantidade em ativo base |
| `horario_abertura`, `horario_fechamento` | `open_date`, `close_date` | Timestamp do trade |
| `taxa_1` | `fee_open_cost` | Fee raw de entrada; o produtor aplicava `fillna(0)` |
| `taxa_2` | `fee_close_cost` | Fee raw de saida; o produtor aplicava `fillna(0)` |
| `preco_transacao` | `open_rate` | Campo legado de transacao |
| `volume_transacao` | `amount` | Campo legado de quantidade |
| `direcao_liquidez` | `enter_tag` | Tag de entrada, nao prova de maker/taker |
| `horario_transacao` | `close_date` | Timestamp legado de transacao |

## Contrato SQLite

O source profile exige explicitamente as colunas:

`id`, `exchange`, `pair`, `is_open`, `is_short`, `open_rate`, `close_rate`, `amount`,
`contract_size`, `leverage`, `fee_open_cost`, `fee_close_cost`, `fee_open_currency`,
`fee_close_currency`, `funding_fees`, `close_profit_abs`, `realized_profit`, `open_date` e
`close_date`.

O join e fixo:

```text
CSV order_id = freqtrade-paper-{id}
SQLite key   = trades.id
```

O batch inteiro e bloqueado se houver ID malformado, duplicado, somente no CSV ou somente no
conjunto SQLite de trades fechados. Linhas pareadas sao quarentenadas individualmente quando
simbolo, lado, timestamp, PnL, preco, quantidade ou leverage divergem.

`taxa_1` e `taxa_2` permanecem documentadas como linhagem, mas nao substituem as fees do
snapshot e nao participam do gate de divergencia: o objetivo deste bloco e justamente
substituir a ambiguidade do export pela evidencia financeira SQLite autoritativa.

## Identidade financeira

O mercado e Binance USDT-M Futures, contrato linear perpetuo, settlement USDT, quantidade em
ativo base e `contract_size` obtido por trade no SQLite.

As normalizacoes obrigatorias sao:

```text
effective_open_fee  = fee_open_cost * leverage
effective_close_fee = fee_close_cost
trading_fee          = effective_open_fee + effective_close_fee
funding_fee          = -funding_fees

long gross_pnl  = (close_rate - open_rate) * amount * contract_size
short gross_pnl = (open_rate - close_rate) * amount * contract_size

reconstructed_net_pnl = gross_pnl - trading_fee - funding_fee
accounting_residual    = abs(reconstructed_net_pnl - close_profit_abs)
```

O `gross_pnl` nunca e derivado de `close_profit_abs`, `realized_profit` ou do CSV. Zero de fee
ou funding somente e aceito quando lido da coluna autoritativa. Campo ausente nao vira zero.

O epsilon da fonte e `0.00000001`. Acima dele, a linha recebe
`financial_accounting_identity_violation` antes de chegar ao validator V2.

## Resultado real observado

O probe read-only encontrou join exato para os 558 IDs, sem IDs exclusivos ou duplicados.
Duas linhas, `freqtrade-paper-221` e `freqtrade-paper-234`, nao possuem `close_rate` e
permanecem em quarentena.

Tres linhas adicionais, `freqtrade-paper-141`, `freqtrade-paper-258` e
`freqtrade-paper-561`, nao fecham a formula prescrita contra `close_profit_abs`. Elas sao
quarentenadas, sem ajuste de epsilon ou derivacao circular. Assim, o estado real pode diferir
do gate indicativo de 556 linhas aceitas; a evidencia observada prevalece.

Resultado observado: 553 linhas aceitas, cinco quarentenadas, 553 formulas reconciliadas e
tres divergencias contabeis. O residual maximo foi `1.28547708` USDT e o residual mediano foi
`0.00000000068550000053` USDT.

## CLI

Execucao default sem escrita:

```powershell
python .\scripts\validate_trader_master_staging_v2.py `
  --project-root . `
  --source-profile .\config\freqtrade_paper_closed_trades_source_profile_v2.json `
  --account-scope-hash <sha256-sanitizado-da-conta-paper> `
  --no-write `
  --json
```

Override auditavel do snapshot:

```powershell
python .\scripts\validate_trader_master_staging_v2.py `
  --project-root . `
  --source-profile .\config\freqtrade_paper_closed_trades_source_profile_v2.json `
  --account-scope-hash <sha256-sanitizado-da-conta-paper> `
  --authoritative-sqlite .\data\snapshots\freqtrade-paper\tradesv3.paper.snapshot.sqlite `
  --no-write `
  --json
```

O `account_scope_hash` deve ser SHA-256 hexadecimal fornecido explicitamente por configuracao
segura. O adapter nao persiste o identificador original e nunca deriva esse hash do filename.

## Fora de escopo

- comparacao com `trades_master.parquet` (Bloco 1B);
- escrita em CSV, Parquet, XLSX, SQLite ou Master;
- importacao, backup ou backfill;
- alteracao de Freqtrade, RiskManager, Qlib, IA Shadow ou Strategy Factory;
- acesso privado a exchange ou envio de ordens.
