# Dashboard Runtime Source Closeout V1

## Objetivo

Esta branch fecha a matriz operacional de fontes do SMART FUTUROS Institutional Dashboard. O dashboard continua snapshot-first e read-only: builders externos inspecionam fontes locais, produzem snapshots em `data/reports`, e as oito páginas Streamlit apenas apresentam esses snapshots.

Nenhuma fonte ausente é fabricada. Ausência, staleness, JSON inválido, schema incompatível e erro de leitura permanecem explícitos e nunca são promovidos artificialmente para `OK`.

## Arquitetura

`smartcrypto/ops/dashboard_snapshots/source_catalog.py` permanece o catálogo canônico de página e path. `source_closeout.py` enriquece esse catálogo com:

- identidade e nome de exibição;
- domínio proprietário;
- tipo de fonte;
- nível `REQUIRED`, `OPTIONAL`, `FUTURE_SOURCE_PENDING` ou `GENERATED`;
- política de freshness e schema, quando aplicável;
- páginas e snapshots consumidores;
- comportamento de ausência/staleness;
- orientação operacional e impacto de segurança.

O build central incorpora `source_matrix` e `page_source_matrix` no `dashboard_snapshot_build_summary.json`. Cada snapshot também recebe `runtime_source_health` e `page_source_closeout`. As páginas 01 e 06 exibem a tabela sem ler fontes operacionais diretamente.

## Status

- `OK`: fonte disponível e válida.
- `MISSING_REQUIRED`: fonte obrigatória ausente; bloqueia a visão operacional consumidora.
- `MISSING_OPTIONAL`: fonte opcional ausente; degrada observabilidade sem bloqueio global.
- `FUTURE_SOURCE_PENDING`: fonte planejada, exibida sem falso erro operacional.
- `STALE`: idade excede a política; fonte obrigatória não pode aparecer como OK.
- `INVALID_SCHEMA`: schema versionado incompatível ou ausente.
- `INVALID_JSON`: conteúdo JSON/JSONL inválido.
- `EMPTY`: arquivo ou payload vazio.
- `READ_ERROR`: falha sanitizada de leitura.
- `UNKNOWN`: estado não classificável.

Uma página com fonte obrigatória ausente ou inválida fica `BLOCKED`. Staleness e ausência opcional aparecem como `DEGRADED`, conforme a política da fonte. Readiness bloqueado nunca é representado visualmente como OK.

## Operação

O campo `operator_hint` informa qual produtor documentado deve ser executado e orienta a reconstrução dos snapshots. A UI não contém botão operacional e não executa produtores.

```powershell
python scripts/build_dashboard_snapshots.py --project-root . --output-dir data/reports --strict false --once --json
python scripts/audit_dashboard_semantic_coverage_v2.py --project-root . --json
python -m pytest tests/test_dashboard_*.py -q
```

## Segurança

O closeout não usa `ccxt`, endpoints privados, OrderManager, ExchangeGateway, Telegram ou NTFY. Não executa OCR, importação Bitradex, rebuild de dataset, treinamento/promoção de modelo, alteração de Qlib/IA Shadow/RiskManager/active signals, live trading, canary ou order submission.

Builders podem ler arquivos locais e escrever apenas os snapshots e summaries autorizados quando o script de build é chamado. Streamlit apenas apresenta dados já consolidados. `data/reports` e demais artefatos runtime permanecem fora do Git.

## Fora De Escopo

- criação dos produtores de fontes futuras;
- correção de dados operacionais ausentes ou stale;
- mudança de schemas dos sistemas produtores;
- qualquer ação de trading, risco, modelo, dataset ou notificação real.
