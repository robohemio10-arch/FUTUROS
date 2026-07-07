# AI Feature Source Fields Enrichment Contract V1

## Objetivo

Esta branch cria um contrato research-only/read-only para auditar, classificar e normalizar campos-fonte contemporâneos que poderão ser usados em uma branch futura para derivar com segurança:

- `feature_notional`
- `feature_quantity`

Esta branch não deriva valores, não escreve dataset ativo, não altera feature contract ativo, não altera dataset manifest ativo, não treina modelo, não promove modelo e não escreve registry.

## Escopo Do Contrato

O contrato classifica campos em quatro grupos:

- `available_fields`: campos observados nas evidências disponíveis.
- `allowed_source_fields`: campos contemporâneos aceitos para futura derivação.
- `forbidden_fields_present`: campos de leakage encontrados, mas nunca usados.
- `ambiguous_fields_requires_review`: campos que exigem revisão humana antes de qualquer uso.

O campo `forbidden_fields_used` deve permanecer sempre `[]`.

## Campos Proibidos

Campos contendo os seguintes termos são tratados como leakage ou outcome e não podem ser usados como fonte:

- `label`
- `target`
- `outcome`
- `pnl`
- `profit`
- `win_loss`
- `future`
- `roi_hit`
- `stoploss_hit`
- `time_exit`
- `expected_value_proxy`

Esses campos podem ser reportados em `forbidden_fields_present`, mas nunca entram em `allowed_source_fields`.

## Campos Permitidos Se Contemporâneos

Campos de preço:

- `price`
- `rate`
- `open`
- `close`
- `entry_price`

Campos de quantidade:

- `amount`
- `quantity`
- `base_amount`
- `contracts`

Campos de notional:

- `stake_amount`
- `notional`
- `cost`
- `quote_amount`

Campos de contexto:

- `timestamp`
- `open_date`
- `symbol`
- `pair`
- `side`

## Campos Ambíguos

Campos genéricos como `volume`, `value`, `size`, `total`, `balance`, `margin`, `position`, `filled` ou `fee` entram em `ambiguous_fields_requires_review`. Eles não são usados automaticamente e não são classificados como fonte permitida.

## Readiness De Derivação

O relatório expõe:

- `can_derive_feature_quantity`
- `can_derive_feature_notional`
- `missing_required_source_fields`

Para `feature_quantity`, é necessário haver pelo menos um campo de quantidade permitido.

Para `feature_notional`, é necessário haver um campo direto de notional ou a combinação segura de quantidade e preço contemporâneo.

## Execução

No-write é o padrão:

```powershell
python .\scripts\build_ai_feature_source_fields_enrichment_contract_v1.py --project-root . --json
```

Para materializar relatório research-only:

```powershell
python .\scripts\build_ai_feature_source_fields_enrichment_contract_v1.py --project-root . --write --json
```

Saídas com `--write`:

- `data/reports/ai_feature_source_fields_enrichment_contract_v1.json`
- `data/reports/ai_feature_source_fields_enrichment_contract_v1.md`

Esses arquivos são artefatos runtime e não devem ser versionados.

## Garantias De Segurança

Invariantes:

- `decision=MANTER_EM_RESEARCH`
- `research_only=true`
- `read_only=true`
- `release_allowed=false`
- `active_contract_changed=false`
- `active_dataset_manifest_changed=false`
- `changes_feature_contract=false`
- `changes_dataset_manifest=false`
- `runs_training=false`
- `writes_registry=false`
- `writes_runtime=false`
- `writes_sqlite=false`
- `writes_parquet=false`
- `sends_orders=false`
- `exchange_private_access=false`

Esta branch não altera Freqtrade, RiskManager, Qlib runtime, IA Shadow runtime, modelos, registry, datasets oficiais ou qualquer lógica de live/canary/order.
