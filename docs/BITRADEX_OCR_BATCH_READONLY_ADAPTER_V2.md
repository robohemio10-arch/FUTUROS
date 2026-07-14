# Bitradex OCR Batch Read-only Adapter V2

## Objetivo

Este adapter audita o lote OCR Bitradex `20260714_151816` contra o contrato de
identidade financeira Trader Master V2. Ele opera somente em memoria, aplica as
duas exclusoes reais registradas no pacote V5 e nao possui autoridade de
importacao.

O fluxo reutiliza diretamente:

- `fingerprint_spec.py` para normalizacao, row fingerprint e
  `canonical_trade_id`;
- `staging_validator.py` para schema e identidade contabil;
- `master_adapter.py` para ler o Trader Master por copia temporaria;
- `master_reconciliation.py` para confronto fail-closed.

O novo adapter e o CLI nao chamam `pandas.read_parquet`. Toda leitura do Master
passa por `read_trader_master_readonly`.

## Fontes

O profile versionado
`config/bitradex_ocr_locked_candidates_source_profile_v2.json` aponta para:

- as 506 linhas do CSV canonico V4;
- as duas exclusoes V5;
- os quatro membros dos dois grupos duplicados;
- o mapping V5 com 504 linhas retidas.

As exclusoes sao aplicadas por `source_file_name + source_sha256`. O
`synthetic_order_id` nunca participa de `order_id`, `source_trade_id`, row
fingerprint ou canonical trade ID. O order ID OCR original aparece somente como
`raw_ocr_order_id` na linhagem.

## Contrato financeiro

O profile descreve separadamente:

```text
taxa_total     = taxa_1, custo negativo
taxa_execucao = taxa_2, custo negativo
trading_fee   = abs(taxa_total) + abs(taxa_execucao)
```

O PnL bruto e reconstruido apenas por preco, quantidade, contract size e lado:

```text
long  = (exit - entry) * quantity * contract_size
short = (entry - exit) * quantity * contract_size
```

O lote V4 nao contem uma coluna autoritativa de funding. O profile real registra
`funding_contract_approved=false` e
`funding_source_rule=authoritative_funding_unavailable_block`. Portanto, ele nao
substitui funding por zero, nao calcula funding como residual e classifica as
linhas como `ACCOUNTING_CONTRACT_BLOCKED` ate existir evidencia aprovada.

Quando um profile futuro fornecer funding direto e aprovado, o validator exige:

```text
abs(net_pnl - (gross_pnl - trading_fee - funding_fee)) <= epsilon_abs_fonte
```

## Estados

- `VERIFIED_NOVEL`: ausencia comprovada contra Master completamente verificavel;
- `VERIFIED_EXISTING`: fingerprint V2 ja presente;
- `PRIMARY_IDENTITY_CONFLICT`: identidade primaria com payload divergente;
- `LEGACY_OVERLAP_AMBIGUOUS`: linhas legadas impedem provar ausencia;
- `MASTER_COMPARISON_UNAVAILABLE`: Master ausente, ilegivel ou reconciliacao sem resultado;
- `ACCOUNTING_CONTRACT_BLOCKED`: componentes financeiros sem evidencia aprovada.

Nenhum estado torna uma linha importavel. `import_eligible` permanece `false`.

## Uso

No-write e o comportamento padrao. O account scope deve ser fornecido como SHA-256
hexadecimal e nunca e derivado de nome de arquivo:

```powershell
python .\scripts\audit_bitradex_ocr_batch_readonly_adapter_v2.py `
  --project-root . `
  --account-scope-hash '<sha256-sanitizado>' `
  --json
```

Sem `--account-scope-hash`, o resultado e bloqueado. Para materializar somente o
relatorio institucional:

```powershell
python .\scripts\audit_bitradex_ocr_batch_readonly_adapter_v2.py `
  --project-root . `
  --account-scope-hash '<sha256-sanitizado>' `
  --write-report `
  --json
```

As unicas escritas permitidas sao o JSON e o Markdown configurados sob
`data/reports`. O adapter nunca escreve CSV, XLSX, Parquet, SQLite, Master,
dataset, runtime, modelo, risco ou ordem.

## Limites operacionais

Este componente nao executa preview de importacao, finalizer, sidecar rebuild,
Qlib, IA Shadow ou Strategy Factory. Ele nao acessa exchange privada e nao envia
ordens. O resultado permanece evidencia read-only sem autoridade operacional.
