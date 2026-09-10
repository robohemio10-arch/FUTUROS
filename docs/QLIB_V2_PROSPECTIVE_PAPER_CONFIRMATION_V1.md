# Qlib V2 Prospective Paper Confirmation — Decision Ledger Authority V2

## Objetivo

Validar prospectivamente o challenger econômico Qlib V2 em Paper sem usar outcomes futuros,
sem alterar execução, risco, stake, leverage, estratégia ou modelo ativo. O objetivo econômico
continua sendo Net PnL stressed, expectancy, Profit Factor e delta contra o baseline Paper.

A evidência prospectiva só é válida quando a decisão Qlib V2 é calculada e registrada antes da
abertura do trade e quando a identidade do sinal é provada pelo Decision Ledger V4.2 selado.
Nenhum matching por proximidade temporal, símbolo/lado, posição em lista, alias de `trade_id`
ou backfill retrospectivo é aceito.

## Epoch V1 preservada e abortada

A primeira freeze certificada foi materializada em:

```text
certified_implementation_commit = ab4e4d7ef2fa7ead90bd4e5da4fecf3127fdaabc
certified_ci_run_id              = 34357355122
certified_ci_completed_at_utc    = 2026-09-09T13:44:03+00:00
prospective_start_utc            = 2026-09-09T13:52:11.830168+00:00
policy_sha256                    = 06d4a60a5b83f3dc5c2a20dae757e743bb8e691237fe65e2cd58297d303bf53d
```

Essa epoch não produziu observações prospectivas válidas. Dois sinais Paper pós-boundary foram
identificados com `candidate_id`, `signal_id`, `correlation_id` e `decision_event_id` corretos,
mas o observer V1 exigia indevidamente `lineage_attestation` de research/registry. Como nenhum
ledger prospectivo válido foi escrito, a epoch V1 permanece imutável e é classificada como
`ABORTED_LINEAGE_CONTRACT_VIOLATION`. Não existe backfill desses sinais.

O arquivo histórico não deve ser sobrescrito:

```text
data/reports/qlib_v2/qlib_v2_prospective_freeze_spec_v1.json
```

## Autoridade de identidade V2

A V2 usa como autoridade o registro `decision` selado do Decision Ledger V4.2:

```text
active signal
  -> candidate_id
  -> signal_id
  -> correlation_id
  -> decision_ledger.decision_event_id
  -> DecisionRecordV42 validado por payload_sha256
  -> exact enter_tag decision_event_id
  -> paper_trade_id
  -> outcome_events.trade_id
```

O registro Decision Ledger precisa satisfazer integralmente:

```text
schema_version = decision_ledger_payload_v4_2
record_type    = decision
runtime_mode   = paper
risk_decision  = APPROVED
final_decision = ALLOW
operational_authority = false
runtime_integration   = false
sends_orders          = false
exchange_private_access = false
```

`candidate_id`, `signal_id`, `correlation_id`, `pair`, `symbol`, `side`,
`decision_timestamp` e `decision_payload_sha256` precisam coincidir exatamente entre o sinal
ativo e o registro selado. Se `lineage_attestation` existir, ele continua sendo validado; sua
ausência não invalida a identidade quando o Decision Ledger V4.2 já a prova.

## Freeze sucessora V2

A nova freeze só pode ser materializada depois de um novo commit contendo esta implementação e
de um CI integralmente verde para esse SHA. O caminho sucessor é separado da V1:

```text
data/reports/qlib_v2/qlib_v2_prospective_freeze_spec_decision_ledger_v2.json
```

Na primeira materialização V2, `prospective_start_utc` deve ser gerado automaticamente no mesmo
instante de `freeze_materialized_at_utc`; não deve ser informado manualmente.

O freeze mantém o contrato econômico do challenger:

- target `absolute_stressed_net_pnl`;
- Qlib `LGBModel(loss="mse")`;
- stress adicional de execução de 5 bps por padrão;
- treino e calibração somente com informação causalmente disponível antes da boundary;
- somente longs elegíveis para KEEP;
- threshold calculado somente na calibração pré-boundary;
- feature columns, feature medians, dataset fingerprint e calibration-score fingerprint imutáveis;
- nenhuma atualização de Qlib runtime, modelo ativo, registry, Freqtrade ou RiskManager.

## Evidência ex-ante

Cada observação V2 precisa obedecer a ordem temporal estrita:

```text
decision_timestamp_utc
    <= observed_at_utc
    <= score_completed_at_utc
    <= ledger_recorded_at_utc
    <= paper_trade_open_time_utc
```

`observed_at_utc` marca o início do observer. `score_completed_at_utc` é capturado somente depois
da reconstrução do modelo congelado, verificação do calibration fingerprint e cálculo do score.
`ledger_recorded_at_utc` é adicionado no momento em que a evidência é materializada no ledger de
research. Se score ou gravação ocorrerem depois da abertura do trade, o trade é excluído da
amostra prospectiva. Se a gravação ocorrer depois do `signal_valid_until_utc`, a escrita falha.

O observer não grava `trade_id`, `paper_trade_id`, PnL, close time ou qualquer outcome. Esses
campos só aparecem na etapa posterior de resolução, após o trade fechar.

Ledger prospectivo V2:

```text
data/research/qlib_v2/qlib_v2_prospective_signal_observations_v2.json
schema_version = paper_autolearning_qlib_v2_prospective_signal_ledger_v2
identity_authority = sealed_decision_ledger_v4_2
```

## Resolução econômica

O resolver usa somente a cadeia de IDs explícitos. Símbolo, side e timestamps são checks de
consistência depois que a identidade já foi resolvida.

Gates econômicos pré-registrados:

```text
resolved decisions             >= 200
observation window             >= 45 dias
selected KEEP trades           >= 50
scorer coverage                >= 99%
KEEP stressed Net PnL          > 0
KEEP stressed expectancy       > 0
KEEP stressed Profit Factor    >= 1.10
delta stressed Net PnL         > 0
paired bootstrap samples       = 5000
bootstrap lower 95% CI         > 0
```

Mesmo se todos os gates passarem:

```text
promotion_allowed = false
operational_authority = false
```

A decisão permanece research-only e exige revisão manual posterior.

## Boundary temporal do snapshot Freqtrade

O snapshot SQLite Paper observado persiste `trades.open_date` e
`trades.close_date` como texto UTC sem offset. O resolver interpreta essa
representação timezone-naive como UTC somente no adapter da fonte Freqtrade,
antes de aplicar os contratos prospectivos estritos.

O normalizador dedicado aceita timestamps naive do snapshot, `Z` e `+00:00`.
Offsets explícitos diferentes de UTC, valores vazios e strings inválidas são
bloqueados. A transformação não consulta timezone local, locale ou relógio do
host. Rows injetadas em testes representam semanticamente o mesmo datasource
Freqtrade e atravessam o mesmo boundary.

Todos os demais timestamps continuam UTC-aware obrigatórios, incluindo freeze,
observer, Decision Ledger, sinais, `score_completed_at_utc`,
`ledger_recorded_at_utc` e outcomes. A correção não adiciona matching fuzzy,
nearest timestamp, inferência por símbolo/lado ou backfill.

Esta adaptação ocorre depois do treatment assignment. Ela não altera modelo,
features, target, threshold, policy SHA, `prospective_start_utc`, identidade do
Decision Ledger ou observações registradas. Assim, a freeze V2 e o ledger V2
permanecem válidos; nenhuma Freeze V3 é necessária e promoção continua
proibida.

## Invariantes de segurança

```text
paper_only=true
shadow_only=true
research_only=true
operational_authority=false
promotion_allowed=false
model_promotion_performed=false
active_model_changed=false
changes_risk=false
changes_strategy=false
changes_stake=false
changes_leverage=false
sends_orders=false
exchange_private_access=false
writes_runtime=false
writes_sqlite=false
historical_backfill_allowed=false
fuzzy_identity_matching_allowed=false
timestamp_only_identity_matching_allowed=false
symbol_side_identity_inference_allowed=false
trade_id_as_candidate_id_allowed=false
```

As únicas escritas autorizadas são artefatos JSON de research em `data/research/qlib_v2/` e a
freeze certificada em `data/reports/qlib_v2/`. O snapshot Freqtrade usado pelo resolver é
read-only.
