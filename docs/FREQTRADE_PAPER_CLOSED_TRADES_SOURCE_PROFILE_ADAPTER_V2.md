# Freqtrade Paper Closed Trades Source Profile e Adapter V2

## Escopo

Este bloco perfila os CSVs de trades paper fechados produzidos pela Fase 14 e os adapta em
memoria ao Trader Master Fingerprint Spec V2. Ele nao compara com o Master, nao importa,
nao executa backfill, nao atualiza feedback, nao treina e nao altera Freqtrade ou runtime.

## Produtor autoritativo

O produtor full-repo e `smartcrypto.data.paper_trade_lifecycle.collect_closed_feedback`.
Ele le uma copia local do SQLite paper, seleciona `is_open=0`, chama
`normalize_closed_trades` e grava o mesmo DataFrame em:

- `data/trades/freqtrade_paper_closed_smartcrypto.csv`;
- `data/trades/inbox/freqtrade_paper_closed_trades.csv`.

No snapshot auditado em 13 de julho de 2026, ambos tinham 558 linhas, 133290 bytes e o mesmo
SHA-256 `F367F1742CB233EFFA35EF07200FCA52C781B75FF2B6EA2A8518CAB64E0BF1FF`.
Esse hash e evidencia do snapshot, nao pin permanente. O adapter recalcula hashes a cada run,
classifica arquivos hash-identicos como replicas e processa apenas um lote logico.

## Origem e semantica das colunas

| CSV | Origem Freqtrade | Semantica |
| --- | --- | --- |
| `moeda` | `pair` | Simbolo normalizado, sem `/USDT:USDT` |
| `fechar_side` | `is_short` | `short` quando 1; caso contrario `long` |
| `leverage` | `leverage` | Informativo; produtor usa 1 se ausente |
| `order_id` | `id` | `freqtrade-paper-{trades.id}` local; nao e exchange order ID |
| `pnl_fechado` | `close_profit_abs`, fallback `realized_profit` | PnL reportado pelo Freqtrade |
| `taxa_lucros_perdas_fechados_pct` | `close_profit` | Razao de lucro reportada |
| `preco_abertura` | `open_rate` | Preco de entrada |
| `preco_fechamento` | `close_rate` | Preco de saida; duas linhas reais estavam nulas |
| `volume_posicao` | `amount` | Quantidade do ativo-base |
| `volume_fechado` | `amount` | Quantidade fechada do ativo-base |
| `horario_abertura` | `open_date` | Timestamp de abertura |
| `horario_fechamento` | `close_date` | Timestamp de fechamento |
| `taxa_1` | `fee_open_cost` | Custo de fee de entrada; produtor aplica `fillna(0)` |
| `preco_transacao` | `open_rate` | Copia do preco de entrada |
| `volume_transacao` | `amount` | Copia da quantidade |
| `direcao_liquidez` | `enter_tag` | Nome legado enganoso; nao prova maker/taker |
| `taxa_2` | `fee_close_cost` | Custo de fee de saida; produtor aplica `fillna(0)` |
| `horario_transacao` | `close_date` | Copia do timestamp de fechamento |

## Identidade financeira

O profile versionado e
`config/freqtrade_paper_closed_trades_source_profile_v2.json`.

- venue: Binance;
- market type: USDT-M Futures;
- contract type: linear perpetual;
- settlement currency: USDT;
- quantity unit: base asset;
- contract size: 1;
- namespace do order ID: `freqtrade:paper:sqlite:trades.id:v1`.

O `order_id` recebido e preservado literalmente. O adapter nao cria `source_trade_id` e nao
deriva IDs nativos. `account_scope_hash` deve ser um SHA-256 hexadecimal fornecido
explicitamente. O identificador de conta original nunca e recebido ou persistido e nenhum hash
e derivado do filename.

## Formula financeira

Para contrato linear:

```text
long gross_pnl  = (exit_price - entry_price) * quantity * contract_size
short gross_pnl = (entry_price - exit_price) * quantity * contract_size
trading_fee     = taxa_1 + taxa_2
net_pnl         = gross_pnl - trading_fee - funding_fee
```

Gross PnL e reconstruido apenas de side, precos, quantidade e contract size, nunca a partir de
`pnl_fechado`. Fees devem estar presentes e ser custos nao negativos. Fee zero e bloqueada
porque o produtor atual usa `fillna(0)` e o CSV nao preserva evidencia para distinguir zero real
de dado ausente.

Funding nao e exportado nesses CSVs. Ele pode estar incorporado no PnL reportado, mas isso nao
pode ser demonstrado pela fonte. Portanto o profile declara `funding_availability=absent` e
todas as linhas reais sao quarentenadas com `funding_fee_unavailable`. Nenhum zero e inventado.

A auditoria do snapshot real encontrou 556 linhas com gross calculavel; nenhuma fechou em
tolerancia absoluta de `1e-8` sem funding e apenas duas fecharam na tolerancia relativa V2.
Isso reforca o bloqueio fail-closed.

## CLI read-only

```powershell
python .\scripts\validate_trader_master_staging_v2.py `
  --project-root . `
  --source-profile .\config\freqtrade_paper_closed_trades_source_profile_v2.json `
  --account-scope-hash <sha256-hex-seguro> `
  --no-write `
  --json
```

`--source-profile` e `--account-scope-hash` ativam o adapter. Sem profile, o CLI preserva o
validador genérico do Bloco 1. `--write-report` continua limitado a JSON/Markdown em
`data/reports`; o adapter nunca escreve CSV, Parquet, XLSX ou SQLite.

## Gates e seguranca

- profile ausente ou invalido: blocked;
- account scope hash ausente/invalido: blocked;
- replica divergente: blocked;
- fees ausentes, negativas ou zero sem proveniencia: quarentena;
- funding ausente ou indeterminado: quarentena;
- identidade contabil divergente: quarentena pelo validator V2;
- `write_to_master_performed=false`;
- `sends_exchange_orders=false`;
- `exchange_private_access=false`;
- `research_pipeline_writes_runtime=false`.

## Fora de escopo

Nao ha comparacao com `trades_master.parquet`; ela pertence ao Bloco 1B. Tambem nao ha writer,
importacao, backfill, feedback, treino, Strategy Factory, Qlib, IA Shadow ou Freqtrade runtime.
