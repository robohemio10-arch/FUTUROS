# AI Feature Missingness Remediation Design V1

## Objetivo

Esta branch cria uma evidência research-only/read-only para diagnosticar e desenhar a remediação do blocker
`feature_missingness_critical` detectado no monitor IA/Qlib.

O foco é `feature_notional` e `feature_quantity`, que aparecem no contrato unificado de features, mas estão com
missingness crítico no dataset manifest atual.

## Blocker tratado

O relatório `data/reports/ai_qlib_drift_regime_monitor_v1.json` reporta:

- `feature_missingness_critical`
- `feature_notional` com `null_rate=1.0`
- `feature_quantity` com `null_rate=1.0`

Esta branch não mascara esse blocker. Quando a evidência atual confirma missingness crítico, o relatório permanece:

- `status=blocked`
- `decision=MANTER_EM_RESEARCH`
- `release_allowed=false`
- `operational_authority=false`
- `readiness_release_authority=false`

## Fontes analisadas

O builder lê apenas evidências existentes:

- `data/reports/ai_unified_feature_contract_v1.json`
- `data/reports/ai_unified_dataset_manifest_v1.json`
- `data/reports/financial_label_target_store_v1.json`
- `data/reports/ai_qlib_drift_regime_monitor_v1.json`
- `data/reports/daily_evidence_readiness_executive_pack_v1.json`

Quando o dataset manifest lista fontes de dados, o builder tenta inspecionar o schema dessas fontes para descobrir se
existem campos brutos como `quantity`, `qty`, `amount`, `notional` ou `entry_price`. Essa inspeção é read-only.

## Causa provável

A causa provável é uma lacuna de mapeamento entre campos brutos disponíveis nas fontes e as colunas canônicas
`feature_notional` e `feature_quantity` no dataset de treino.

Quando campos brutos existem, mas a feature final permanece nula, a conclusão é:

- os campos existem na fonte;
- ainda não são populados pelo builder canônico;
- a remediação deve ocorrer no estágio de construção de features, antes do dataset manifest.

Quando os campos brutos não existem ou não são legíveis, a conclusão é:

- a remediação deve bloquear por `insufficient_source_fields`;
- não se deve inventar quantidade, notional ou preço.

## Desenho de remediação

Regra para `feature_quantity`:

- preferir campo bruto `quantity`, `qty`, `amount` ou `trade_amount`;
- se nenhum existir, bloquear como `insufficient_source_fields`.

Regra para `feature_notional`:

- preferir campo bruto `notional`, `raw_notional` ou `trade_notional`;
- se não existir, derivar por `abs(quantity * entry_price)` somente quando ambos existirem;
- se `quantity` ou `entry_price` estiver ausente, bloquear como `insufficient_source_fields`.

## Anti-leakage

Os seguintes campos nunca podem ser usados como fonte de feature:

- `target`
- `outcome`
- `pnl`
- `net_pnl`
- `label`
- `result`
- `close_reason`

Esses campos podem existir em arquivos de feedback ou label store, mas não podem participar da derivação de
`feature_notional` ou `feature_quantity`.

## Validações futuras necessárias

Uma branch futura de implementação deve provar:

- `feature_quantity` é derivada apenas de `quantity`, `qty`, `amount` ou `trade_amount`;
- `feature_notional` usa notional bruto ou `abs(quantity * entry_price)`;
- nenhum campo de target/outcome/pnl/label é usado como feature;
- o dataset manifest reconstruído reduz o missingness abaixo do limite crítico;
- o drift monitor deixa de reportar `feature_missingness_critical` somente com evidência real;
- Qlib/IA Shadow continuam sem promoção automática.

## Por que é design-only

Esta branch não altera contratos ativos nem corrige datasets. Ela existe para deixar uma trilha auditável antes da
remediação real.

Não faz parte do escopo:

- alterar `ai_unified_feature_contract_v1.json`;
- alterar `ai_unified_dataset_manifest_v1.json`;
- treinar modelo;
- promover modelo;
- escrever registry;
- atualizar Qlib runtime;
- atualizar IA Shadow runtime;
- alterar Freqtrade;
- alterar RiskManager;
- enviar ordens;
- acessar exchange privada;
- escrever SQLite, parquet, modelo ou runtime artifact.

## Como executar

Modo padrão, sem escrita:

```powershell
python .\scripts\build_ai_feature_missingness_remediation_design_v1.py --project-root . --json
```

Gerar evidência JSON/Markdown em `data/reports`:

```powershell
python .\scripts\build_ai_feature_missingness_remediation_design_v1.py --project-root . --write-report --json
```

`--no-write` prevalece sobre `--write-report`.
