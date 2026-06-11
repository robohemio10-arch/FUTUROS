# SMART FUTUROS Command Center v2: contratos fundacionais

## 1. Objetivo e identidade

O nome oficial do produto e SMART FUTUROS. A interface e denominada SMART FUTUROS
Command Center, e a denominacao tecnica e SMART FUTUROS Institutional Dashboard.
Este documento define o nucleo de contratos da primeira etapa do dashboard v2.

## 2. Regra operacional

O dashboard opera somente em PAPER / SHADOW ONLY. Live, envio de ordens reais e
leitura de conta privada permanecem HARD_BLOCKED. A autoridade operacional continua
no RiskManager; o dashboard nao assume autoridade sobre risco ou execucao.

Esta branch e apenas fundacional. Ela nao executa ordens, nao altera risco, nao chama
exchange, nao envia notificacoes e nao promove modelos.

## 3. Arquitetura snapshot-first

O fluxo institucional e:

```text
fontes e relatorios read-only
  -> builders backend (Branch 2)
  -> data/reports/dashboard_*_snapshot.json
  -> paginas Streamlit read-only (Branch 3)
```

A UI nunca deve consultar exchange, banco operacional, `.env`, tokens ou fontes
financeiras mutaveis. Builders e UI permanecem separados.

## 4. Oito abas

1. Infraestrutura
2. Portfolio e Risco
3. Grid Spot Monitor
4. Oportunidades
5. IA / Qlib Governance
6. Controles Ativos
7. Relatorios Quantitativos e TCA
8. Alertas e Mensageria

## 5. Snapshots esperados

Os snapshots futuros usam `dashboard_global_status_snapshot.json`, um arquivo por aba
e `dashboard_snapshot_build_summary.json`. A Aba 8 usa exclusivamente
`dashboard_alerts_messaging_snapshot.json`; o nome historico baseado em queue e proibido.
Todos esses arquivos pertencem a `data/reports/` e nunca sao versionados.

## 6. Classificacao de fontes

- `REQUIRED_EXISTING_SOURCE`: ausencia deve bloquear o builder em modo strict.
- `OPTIONAL_EXISTING_SOURCE`: ausencia produz estado opcional/unknown.
- `FUTURE_SOURCE`: planejamento futuro e nao bloqueante nesta etapa.
- `GENERATED_BY_THIS_BRANCH`: artefato runtime da familia de snapshots, nunca fonte versionada.

O catalogo completo por aba vive em `source_catalog.py`.

## 7. Schema versions

As dez constantes oficiais estao em `contracts.py`: status global, oito abas e resumo
de build. Todo snapshot inclui schema, runtime mode, locks de live/order, timestamp,
secoes e auditoria read-only.

## 8. Safe math

`safe_math.py` ignora valores `None`, NaN e infinitos, trata colecoes vazias e evita
divisao por zero. O CVaR usa a cauda inferior:

```text
q = quantile(returns, alpha)
tail = returns <= q
cvar = -mean(tail) * equity
```

O backoff exponencial usa `min(base * (2 ** retry_count), maximum)`.

## 9. File loader e build context

O loader suporta JSON, JSONL e Parquet por import lazy. Erros e arquivos ausentes sao
resultados controlados, sem criar diretorios ou escrever dados. O contexto de build
nasce com `allow_writes_to_output_dir=false`; a escrita de snapshots pertence a branch
de builders.

## 10. Guard read-only

O guard exige auditoria sem exchange privada, sem biblioteca de exchange, sem ordens,
sem alteracao de risco/config/modelo/signals e com `dashboard_reads_only=true`. Os
banners globais tornam visiveis PAPER / SHADOW ONLY, LIVE LOCKED, ordens desabilitadas,
autoridade do RiskManager e dashboard read-only.

## 11. Proibicoes

O pacote dashboard nao pode importar cliente de exchange, criar/cancelar ordens,
consultar saldo ou ordens privadas, escrever YAML, ler tokens do ambiente, enviar HTTP,
disparar tarefas assicronas operacionais, promover modelos ou alterar sinais ativos.
O teste estatico usa AST para diferenciar chamadas reais de strings de auditoria.

## 12. Validacao

```powershell
python -m compileall smartcrypto tests
python -m pytest tests/test_dashboard_snapshot_contracts_v2.py tests/test_dashboard_safe_math_v2.py tests/test_dashboard_source_catalog_v2.py tests/test_dashboard_file_loader_v2.py tests/test_dashboard_command_center_static_safety_v2.py -q
python -m pytest
python scripts/generate_project_manifest.py
python scripts/generate_project_manifest.py --check
python scripts/scan_versioned_secrets.py --json
```

## 13. Limites desta branch e proximas etapas

Esta etapa nao cria builders completos, script de build, paginas Streamlit finais,
CommandAdapter, CommandBus operacional, dispatcher, Telegram/NTFY, execucao paper/live,
alteracao de risco, alteracao de modelos ou escrita em fontes operacionais.

As proximas branches adicionam, nesta ordem: builders de snapshot, paginas Streamlit
read-only, stubs de controles/alertas e o tema visual final do SMART FUTUROS Command Center.
