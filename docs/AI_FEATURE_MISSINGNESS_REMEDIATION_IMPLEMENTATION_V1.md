# AI Feature Missingness Remediation Implementation V1

## Escopo e natureza

Esta branch é **research-only** e **read-only**. Ela implementa, de fato, a remediação determinística
desenhada na Branch 55.1 (`ai_feature_missingness_remediation_design_v1`) para `feature_notional` e
`feature_quantity`, e prova numericamente a redução de missingness a partir do mesmo dataset carregado em
memória.

Este artefato **não autoriza**:

- live trading;
- canary release;
- envio de ordens reais;
- acesso a exchange privada;
- promoção de modelo;
- promoção de regra;
- alteração de risco;
- alteração de contrato de features ativo;
- alteração de dataset manifest ativo;
- alteração de Freqtrade, RiskManager, Qlib runtime, IA Shadow runtime, registry, SQLite, modelos ou
  configuração operacional (YAML).

`decision` é sempre `MANTER_EM_RESEARCH`, independentemente do `status` calculado.

## O que esta branch faz

1. Lê três evidências obrigatórias, sempre em modo leitura:
   - `data/reports/ai_unified_feature_contract_v1.json`
   - `data/reports/ai_unified_dataset_manifest_v1.json`
   - `data/reports/ai_feature_missingness_remediation_design_v1.json` (evidência da Branch 55.1)
2. Resolve o único dataset apontado por `ai_unified_dataset_manifest_v1.json` →
   `selected_training_dataset`, e carrega esse arquivo em memória (JSON, CSV, XLSX ou Parquet).
3. Para cada linha desse dataset, deriva `feature_quantity` e `feature_notional` **somente a partir de
   campos permitidos presentes na mesma linha**, na ordem fixa documentada abaixo.
4. Calcula, a partir do dataset carregado em memória (não apenas dos `null_counts` do manifest ativo):
   - `row_count_before` / `row_count_after` (sempre iguais — nenhuma linha é descartada);
   - `before_null_count` / `before_null_rate`;
   - `after_null_count` / `after_null_rate`;
   - `null_count_delta` / `null_rate_delta`.
5. Reporta o resultado em `status` ∈ {`ok`, `warning`, `blocked`} e nunca escreve de volta no dataset,
   no contrato ou no manifest ativos.

## Sem join com outcome/feedback/label/PnL

Esta implementação **não faz join com `outcome_events.parquet`** nem com nenhuma outra fonte de
outcome, feedback, PnL ou label. A derivação de `feature_notional` e `feature_quantity` acontece
**estritamente linha a linha, dentro de um único arquivo** — o `selected_training_dataset` apontado
pelo dataset manifest ativo. Nenhum outro `source_paths` do manifest é lido ou combinado.

Isso é uma decisão de escopo deliberada: unir arquivos por chave (`order_id`, `event_id`, `trade_id`)
introduziria lógica de matching não coberta pelo desenho aprovado na Branch 55.1, e essa branch existe
apenas para implementar exatamente o que foi desenhado — remediação por linha, com fontes brutas
permitidas.

## Nenhuma fonte de outcome/target/label é lida para derivação

Antes de qualquer derivação, cada linha é sanitizada: qualquer coluna cujo nome normalizado contenha um
dos padrões abaixo é removida da linha e nunca chega às funções de derivação:

```
target, outcome, pnl, label, result, exit_reason, close_reason, future_ret, win, loss, profit
```

Colunas proibidas efetivamente presentes na fonte são listadas em `forbidden_fields_present`
(informativo). O campo `forbidden_fields_used` é **sempre `[]`** — é uma invariante estrutural do
código: a sanitização acontece antes de qualquer leitura de valor para derivação, não depois.

Para CSV/XLSX/Parquet, a leitura já é seletiva (`usecols`/`columns` restritos à whitelist de aliases
permitidos), evitando carregar colunas proibidas do disco. Para JSON, a leitura é completa e a
sanitização remove as colunas proibidas linha a linha antes da derivação.

## Regras de derivação determinística

`feature_quantity` = primeiro valor numérico válido, nesta ordem:

```
feature_quantity, quantity, qty, amount, trade_amount, volume, volume_posicao, volume_fechado
```

`entry_price` (uso interno, não é uma das duas features remediadas) = primeiro valor numérico válido,
nesta ordem:

```
feature_entry_price, entry_price, open_rate, avg_entry_price, preco_abertura, price
```

Se `price` for o campo usado, o relatório emite `warning=ambiguous_price_alias_used`, pois é um nome
genérico que pode não se referir a preço de entrada.

`feature_notional` = primeiro valor numérico válido entre `feature_notional`, `notional`,
`raw_notional`, `trade_notional`; `notiional` é tolerado apenas como alias legado de dado com erro de
digitação, nunca como nome oficial. Se nenhum notional bruto existir, deriva por
`abs(feature_quantity * entry_price)` quando ambos estiverem disponíveis na mesma linha; caso
contrário, permanece nulo.

Nenhum valor é inventado: quando os campos permitidos não existem na linha, o resultado é nulo e o
recurso é reportado como `blocked_reason=insufficient_source_fields`.

## Resultado esperado sobre o dataset real do repositório

O dataset atualmente selecionado pelo manifest ativo
(`data/feedback/training_microbatches/2026-06-01.parquet`, 498 linhas) **não contém, na mesma linha**,
nenhum dos aliases brutos de quantidade ou notional (`quantity`, `qty`, `amount`, `notional`, etc.) —
apenas as colunas já nulas `feature_notional`/`feature_quantity` e `feature_entry_price` (populada).
Esses campos brutos existem em **outros** arquivos do dataset manifest (ex.: `outcome_events.parquet`),
mas esta branch **não faz join** com eles (ver seção acima).

Por isso, é esperado e correto que rodar esta implementação contra o repositório real hoje produza:

- `status=blocked`;
- `insufficient_source_fields:feature_quantity` e `insufficient_source_fields:feature_notional` em
  `blockers`;
- `decision=MANTER_EM_RESEARCH`.

Isso não é uma falha da implementação — é a prova honesta de que a remediação linha-a-linha, sem join,
não tem dado suficiente no arquivo selecionado hoje. Uma branch futura teria que decidir, explicitamente
e com o mesmo rigor de anti-leakage, se popula essas colunas brutas no builder canônico do dataset antes
do dataset manifest, ou se autoriza (fora do escopo desta branch) uma estratégia de join read-only.

Os testes automatizados (`tests/test_ai_feature_missingness_remediation_implementation_v1.py`) provam o
mecanismo de remediação e redução de missingness usando fixtures determinísticas onde os campos brutos
permitidos existem na mesma linha — não dependem do dataset real do repositório.

## Outputs permitidos

Por padrão (`--no-write`, que é o comportamento default), nenhum arquivo é escrito.

Quando `--write-report` é passado (e `--no-write` não é passado — `--no-write` sempre vence), os únicos
dois arquivos que podem ser escritos são:

- `data/reports/ai_feature_missingness_remediation_implementation_v1.json`
- `data/reports/ai_feature_missingness_remediation_implementation_v1.md`

Nenhum dataset derivado é escrito em disco. Nenhum arquivo de runtime, SQLite, parquet, modelo,
registry ou `PROJECT_MANIFEST_CLEAN.json` é tocado por este script.

## Safety flags

Todo relatório inclui, no nível superior e em `safety_flags`:

```
paper_only=true
shadow_only=true
research_only=true
read_only=true
operational_authority=false
readiness_release_authority=false
live_trading_enabled=false
release_allowed=false
live_release_allowed=false
canary_release_allowed=false
order_submission_enabled=false
real_order_submission_enabled=false
sends_orders=false
exchange_private_access=false
changes_risk=false
changes_model=false
changes_feature_contract=false
changes_dataset_manifest=false
can_apply_to_freqtrade=false
can_apply_to_risk_manager=false
can_promote_rules=false
can_promote_model=false
model_promotion_performed=false
registry_write_performed=false
active_model_changed=false
qlib_runtime_updated=false
ai_shadow_runtime_updated=false
updates_freqtrade=false
updates_risk_manager=false
runs_training=false
writes_runtime=false
writes_sqlite=false
writes_parquet=false
```

`forbidden_fields_used` é sempre `[]` e `no_join_sources_used` é sempre `true`.

## Como executar

Modo padrão, sem escrita:

```powershell
python .\scripts\build_ai_feature_missingness_remediation_implementation_v1.py --project-root . --json
```

Gerar evidência JSON/Markdown em `data/reports`:

```powershell
python .\scripts\build_ai_feature_missingness_remediation_implementation_v1.py --project-root . --write-report --json
```

`--no-write` prevalece sobre `--write-report`:

```powershell
python .\scripts\build_ai_feature_missingness_remediation_implementation_v1.py --project-root . --write-report --no-write --json
```

## Comandos de validação

```powershell
python -m compileall scripts smartcrypto tests
python -m pytest tests/test_ai_feature_missingness_remediation_implementation_v1.py -q
python -m pytest tests/test_ai_feature_missingness_remediation_design_v1.py -q
python .\scripts\build_ai_feature_missingness_remediation_implementation_v1.py --project-root . --json
python .\scripts\build_ai_feature_missingness_remediation_implementation_v1.py --project-root . --write-report --json
python scripts\generate_project_manifest.py
python scripts\generate_project_manifest.py --check
python scripts\scan_versioned_secrets.py --project-root . --json
git diff --check
git status -sb
git status --short
```

## Por que esta branch continua research-only

Esta branch prova um mecanismo determinístico de remediação e mede seu efeito em memória, mas não:

- atualiza `ai_unified_feature_contract_v1.json` ativo;
- atualiza `ai_unified_dataset_manifest_v1.json` ativo;
- treina, promove ou registra modelo;
- atualiza runtime do Qlib ou da IA Shadow;
- altera Freqtrade ou RiskManager;
- envia ordens ou acessa exchange privada.

Uma branch futura, com escopo próprio e aprovação explícita, seria necessária para levar esta
remediação ao builder canônico do dataset de treino e, só então, reconstruir contrato e manifest ativos.
