# Paper Closed Trades Shadow Rule Attribution V1

## Objetivo

Esta camada atribui trades paper fechados a evidencias de replay observacional shadow para medir, de forma descritiva, como regras survivors teriam classificado trades ja encerrados.

Fluxo conceitual:

```text
closed paper trades
-> shadow survivor/replay attribution
-> would_allow / would_block
-> missed_opportunity / preserved_loss / false_positive_observation
-> EV delta hipotetico por trade
```

## Escopo

O artefato e research-only/read-only. Ele nao executa regra, nao registra survivor, nao altera runtime, nao altera modelos e nao emite sinais. A decisao institucional permanece:

```text
decision=MANTER_EM_RESEARCH
operational_authority=false
ready_for_shadow_observation=false
can_promote_rules=false
can_apply_to_freqtrade=false
can_apply_to_risk_manager=false
sends_orders=false
changes_risk=false
writes_runtime=false
```

## Entradas

Por padrao, o CLI nao le fontes runtime reais. Sem `--allow-runtime-read`, retorna `status=blocked` com `input_mode=no_runtime_rows_loaded`.

Com leitura explicita, aceita:

- `--closed-trades`: JSON ou CSV com trades paper fechados.
- `--shadow-replay-report`: JSON do replay observacional shadow.
- `--observation-design-report` ou `--oos-report`: fontes JSON alternativas com survivor rules, quando aplicavel.

Todas as leituras sao locais e read-only.

## Saida

Sem `--write`, nenhuma escrita e realizada.

Com `--write`, o script grava apenas um JSON research-only em `data/reports/`:

```text
data/reports/paper_closed_trades_shadow_rule_attribution_v1.json
```

O script nao escreve `data/runtime`, SQLite, Parquet operacional, modelos, registry, sinais ou configuracoes.

## Metricas

O relatorio expoe:

- `closed_trade_count`
- `attributed_trade_count`
- `unattributed_trade_count`
- `would_allow_count`
- `would_block_count`
- `missed_opportunity_count`
- `preserved_loss_count`
- `false_positive_observation_count`
- `expected_value_delta_total`
- `expected_value_delta_mean`
- `attribution_table_sample`
- `survivor_attribution_summary`
- `gate_summary`

## Semantica

- `would_allow`: o trade fechado pertenceu ao cohort observacional research-only.
- `would_block`: o trade fechado ficou fora do cohort observacional research-only.
- `missed_opportunity`: `would_block` com PnL positivo.
- `preserved_loss`: `would_block` com PnL negativo.
- `false_positive_observation`: `would_allow` com PnL negativo.
- `expected_value_delta`: delta hipotetico usado apenas para pesquisa.

Nenhum campo pode ser usado como permissao operacional, veto runtime, sinal de entrada, alteracao de risco ou promocao de regra.

## Uso

Default seguro:

```powershell
python .\scripts\build_paper_closed_trades_shadow_rule_attribution_v1.py --project-root . --no-write --json
```

Leitura explicita e sem escrita:

```powershell
python .\scripts\build_paper_closed_trades_shadow_rule_attribution_v1.py `
  --project-root . `
  --allow-runtime-read `
  --closed-trades .\data\trades\paper_closed_trades.json `
  --shadow-replay-report .\data\reports\ocr_master_candle_shadow_observation_replay_v1.json `
  --no-write `
  --json
```

Escrita explicita do relatorio research-only:

```powershell
python .\scripts\build_paper_closed_trades_shadow_rule_attribution_v1.py `
  --project-root . `
  --allow-runtime-read `
  --closed-trades .\data\trades\paper_closed_trades.json `
  --shadow-replay-report .\data\reports\ocr_master_candle_shadow_observation_replay_v1.json `
  --write `
  --json
```

## Garantias de seguranca

- Paper/shadow only.
- Sem live trading.
- Sem ordens.
- Sem acesso a exchange privada.
- Sem alteracao de Freqtrade, RiskManager, Qlib runtime ou IA Shadow runtime.
- Sem alteracao de modelos, registry, sinais, datasets oficiais ou SQLite.
- Escrita opcional restrita a JSON em `data/reports`.

## Validacao

```powershell
python -m compileall scripts smartcrypto tests
python -m pytest tests\test_paper_closed_trades_shadow_rule_attribution_v1.py -q
python .\scripts\build_paper_closed_trades_shadow_rule_attribution_v1.py --project-root . --no-write --json
python .\scripts\generate_project_manifest.py --check
python .\scripts\scan_versioned_secrets.py --project-root . --json
python -m pip_audit -r ".\requirements-dev.lock" --progress-spinner off
git diff --check
git status --short
```
