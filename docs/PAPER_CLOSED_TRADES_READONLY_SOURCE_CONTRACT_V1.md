# Paper Closed Trades Read-Only Source Contract V1

## Objetivo

Esta branch cria um contrato research-only/read-only para localizar, validar e normalizar uma fonte local de trades paper fechados. O objetivo é remover o gargalo metodológico que deixou replay e attribution sem linhas:

- `replay_trade_count=0`
- `closed_trade_count=0`
- `attributed_trade_count=0`

O contrato serve como base de entrada para replay/attribution em pesquisa. Ele não ativa observer, não aplica regras, não promove survivors e não altera runtime.

## Comportamento Seguro

Por padrão, a CLI não lê fontes runtime reais e retorna bloqueio seguro:

```powershell
python .\scripts\build_paper_closed_trades_readonly_source_contract_v1.py --project-root . --no-write --json
```

Para ler fontes locais explicitamente:

```powershell
python .\scripts\build_paper_closed_trades_readonly_source_contract_v1.py --project-root . --allow-runtime-read --closed-trades-source data\trades\inbox\freqtrade_paper_closed_trades.csv --no-write --json
```

Com `--write`, a ferramenta grava apenas relatórios research-only em `data/reports`:

```powershell
python .\scripts\build_paper_closed_trades_readonly_source_contract_v1.py --project-root . --allow-runtime-read --closed-trades-source data\trades\inbox\freqtrade_paper_closed_trades.csv --write --json
```

## Fontes Candidatas

Quando `--allow-runtime-read` é usado sem fonte explícita, o contrato verifica caminhos locais conhecidos, sempre em modo read-only:

- `data/trades/inbox/freqtrade_paper_closed_trades.csv`
- `data/reports/freqtrade_paper_closed_trades.csv`
- `data/reports/freqtrade_paper_closed_trades.json`
- `data/reports/paper_closed_trades.json`
- `data/reports/phase14_closed_trades.json`
- `data/reports/phase14_paper_closed_trades.json`
- `data/feedback/paper_closed_trades_incremental.parquet`
- `data/runtime/freqtrade_paper_closed_trades.csv`
- `data/runtime/phase14/freqtrade_paper_closed_trades.csv`

Também é possível passar caminhos locais explicitamente com `--closed-trades-source`.

## Contrato Canônico

Cada trade fechado normalizado expõe:

- `trade_id`, ou fallback determinístico;
- `order_id`, quando disponível;
- `internal_order_id`, quando disponível;
- `symbol`;
- `side`;
- `open_time`;
- `close_time`;
- `entry_price`;
- `exit_price`;
- `amount`, quando disponível;
- `stake_amount`, quando disponível;
- `pnl`;
- `profit_ratio`, quando disponível;
- `fee`, quando disponível;
- `source_path`;
- `source_sha256`;
- `row_fingerprint`.

Campos obrigatórios para contrato completo:

- `trade_id`
- `symbol`
- `side`
- `open_time`
- `close_time`
- `entry_price`
- `exit_price`
- `pnl`

## Join Key

O relatório avalia candidatos de join em ordem conservadora:

1. `order_id`
2. `internal_order_id`
3. `trade_id`
4. `row_fingerprint`

O `recommended_join_key` só é definido quando a chave cobre todas as linhas normalizadas e não contém duplicatas.

## Saída JSON

O relatório expõe:

- fontes verificadas, presentes e ausentes;
- resumo de schema por fonte;
- mapeamento canônico de campos;
- campos obrigatórios ausentes;
- contagem de linhas normalizadas;
- contagem de linhas rejeitadas;
- duplicidade de join key;
- candidatos de join;
- `recommended_join_key`;
- `replay_ready`;
- `attribution_ready`;
- root causes;
- próxima ação recomendada;
- safety flags.

Mesmo quando o contrato está completo, o status top-level permanece `blocked` e a decisão permanece `MANTER_EM_RESEARCH`, porque este artefato não possui autoridade operacional.

## Garantias de Segurança

A saída preserva:

- `research_only=true`
- `read_only=true`
- `paper_only=true`
- `shadow_only=true`
- `operational_authority=false`
- `paper_observation_allowed=false`
- `ready_for_shadow_observation=false`
- `can_apply_to_freqtrade=false`
- `can_apply_to_risk_manager=false`
- `can_promote_rules=false`
- `can_promote_model=false`
- `sends_orders=false`
- `changes_risk=false`
- `writes_runtime=false`
- `writes_sqlite=false`
- `writes_parquet=false`

## Fora de Escopo

Esta branch não altera:

- docker-compose;
- Dockerfiles;
- Freqtrade;
- RiskManager;
- Qlib runtime;
- IA Shadow runtime;
- model registry;
- active signals;
- `data/trades` oficiais;
- `data/features`;
- `data/runtime`;
- config;
- `.env`;
- YAML;
- lógica live/canary/order.
