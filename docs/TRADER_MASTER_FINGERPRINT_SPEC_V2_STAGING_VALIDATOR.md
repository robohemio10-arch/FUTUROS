# Trader Master Fingerprint Spec V2 e Staging Validator

## Objetivo

Esta entrega define uma identidade deterministica e versionada para trades em staging e um
validador read-only. Ela nao importa registros, nao substitui writers existentes e nao altera
`trades_master.xlsx`, Parquet, CSV, SQLite, feedback store, runtime, risco ou modelos.

## Inventario e decisao arquitetural

O repositorio ja possui loaders e deduplicadores em `smartcrypto.data.trades_importer`, no
contrato read-only de trades paper e no feedback store. Esses contratos sao V1 e atendem seus
fluxos locais, mas nao formam uma identidade financeira namespaced completa. O V2 reutiliza
somente `smartcrypto.data.trades_importer.read_trade_file` para leitura tabular. Os writers
existentes permanecem intocados e fora do runner V2.

Writers de Trader Master identificados no inventario:

- `smartcrypto.data.trades_importer.write_master`;
- `smartcrypto.learning.paper_autolearning.master_consolidation`;
- `scripts/apply_bitradex_ocr_orderid_synthetic_v5_to_trades_master.py`;
- `scripts/sync_ocr_master_v11_phase5_sidecars.py`.

Nenhum deles e chamado por esta entrega.

## Fingerprint Spec V2

`fingerprint_spec_version=trader_master_fingerprint_spec_v2` integra o dominio do SHA-256.
A serializacao usa UTF-8 e JSON compacto em ordem fixa. `null` e representado por JSON null.
Nao ha dependencia de `hash()`, ordem de `dict`, locale, timezone local ou hash seed Python.

Ordem dos campos:

1. `venue`
2. `market_type`
3. `contract_type`
4. `settlement_currency`
5. `quantity_unit`
6. `contract_size`
7. `account_scope_hash`
8. `order_id_namespace`
9. `source_trade_id`
10. `order_id`
11. `source`
12. `symbol`
13. `side`
14. `open_time`
15. `close_time`
16. `entry_price`
17. `exit_price`
18. `quantity`
19. `gross_pnl`
20. `trading_fee`
21. `funding_fee`
22. `net_pnl`
23. `epsilon_abs_fonte`

Strings recebem trim e compactacao de whitespace. Casefold ocorre apenas nos campos declarados
no contrato: venue, market type, contract type, settlement currency, quantity unit,
account scope hash, order ID namespace, source, symbol e side. Timestamps sao convertidos para
UTC com microssegundos; timestamps sem offset sao interpretados explicitamente como UTC.

Todos os campos decimais usam `Decimal`, `ROUND_HALF_EVEN` e quantum `0.00000001`. Um `float`
eventualmente entregue pelo loader e convertido por sua representacao decimal textual, nunca
por `Decimal.from_float` nem por serializacao binaria.

## Identidade nativa e canonical_trade_id

`order_id` e `source_trade_id` sao opcionais e nunca sao inventados. Se qualquer identificador
nativo existir, `order_id_namespace` e obrigatorio. A `primary_identity` e:

`venue + account_scope_hash + order_id_namespace + (source_trade_id ou order_id)`.

O `canonical_trade_id` incorpora o namespace `smart_futuros.trader_master.trade`, a versao da
spec e o namespace da fonte. Quando nao ha identificador nativo, o fallback usa
`row_fingerprint`. Marcadores de identificador sintetico/gerado bloqueiam a linha.

## Identidade contabil

Para cada linha:

```text
abs(net_pnl - (gross_pnl - trading_fee - funding_fee))
<= max(epsilon_abs_fonte, 0.0005 * abs(gross_pnl))
```

`trading_fee` nao pode ser negativa. `funding_fee > 0` e custo; `funding_fee < 0` e receita.
Nao ha reparo silencioso. Qualquer violacao coloca a linha em quarentena e bloqueia o staging.

## Duplicata, conflito e colisao

- Duplicata exata: mesma serializacao canonica, mesmo fingerprint e mesmo canonical trade ID.
  E excluida da contagem aceita, sem ser classificada como colisao.
- Conflito de identidade: mesmo canonical trade ID nativo com conteudos financeiros distintos.
  Todas as linhas do grupo vao para quarentena.
- Colisao SHA-256 observada: serializacoes canonicas distintas com o mesmo row fingerprint.
  O gate bloqueia fail-closed.

O relatorio separa `raw_row_count`, `staging_duplicate_count`,
`duplicate_canonical_trade_id_count`, `duplicate_fingerprint_count` e
`observed_fingerprint_collision_count`.

## Linhagem e quarentena

Cada resultado registra `source_file`, `source_sha256`, `ingestion_run_id`,
`source_row_index`, `normalizer_version`, `fingerprint_spec_version`,
`canonical_trade_id` e `row_fingerprint`. Linhas em quarentena jamais sao promovidas e o runner
nao possui caminho de importacao.

## Kill-switch

O runner verifica `data/KILL_SWITCH` no boot e durante batches com intervalo maximo de 60
segundos. A deteccao gera `status=blocked`, `partial_artifact_status=aborted` e escrita atomica
apenas do relatorio final, quando solicitada. Arquivos temporarios nunca viram relatorio valido.

## Comandos

Read-only, default:

```powershell
python .\scripts\validate_trader_master_staging_v2.py `
  --project-root . `
  --staging-file .\data\staging\trader_master\trades_staging.csv `
  --no-write --json
```

Relatorios opcionais, somente em `data/reports`:

```powershell
python .\scripts\validate_trader_master_staging_v2.py `
  --project-root . `
  --staging-file .\data\staging\trader_master\trades_staging.csv `
  --write-report --json
```

## Matriz de seguranca

| Capacidade | Estado |
| --- | --- |
| Paper/shadow/research only | habilitado |
| Escrita JSON/Markdown em `data/reports` | somente com `--write-report` |
| Escrita no Trader Master | bloqueada |
| Simulacao/envio de ordem | bloqueado |
| Exchange privada | bloqueada |
| Mudanca de risco | bloqueada |
| Treino/promocao/modelo ativo | bloqueado |
| Escrita em runtime/SQLite/Parquet | bloqueada |

## Limitacoes

Esta entrega nao converte masters legados para V2, nao corrige dados, nao infere identidade de
conta, nao importa staging e nao executa baseline financeiro, candle alignment, Strategy
Factory, backtest, Monte Carlo, Qlib, IA Shadow, RiskManager ou Freqtrade. A integracao com um
writer oficial exige outra branch, outro gate e autorizacao explicita.
