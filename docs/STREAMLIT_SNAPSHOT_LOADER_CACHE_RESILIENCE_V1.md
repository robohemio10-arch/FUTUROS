# Streamlit Snapshot Loader Cache Resilience v1

## Objetivo

Esta frente padroniza a leitura dos snapshots JSON do SMART FUTUROS Command
Center. A otimização fica exclusivamente nos services/loaders; páginas Streamlit
continuam sendo uma camada read-only de apresentação, sem regra financeira ou
estado operacional em `session_state`.

## Cache transparente

O loader usa `st.cache_data` quando Streamlit está disponível, com TTL de dois
segundos e sem spinner. O item cacheado são apenas os bytes do arquivo JSON.

A chave inclui:

- path absoluto;
- `mtime_ns`;
- tamanho em bytes.

Assim, uma atualização do snapshot invalida o cache imediatamente pela assinatura
do arquivo, mesmo dentro do TTL. O payload é interpretado novamente e seus campos
`status`, `source_status`, `freshness_status` e `last_updated_utc` são preservados.
O cache nunca promove `STALE`, `BLOCKED`, `DEGRADED` ou `UNKNOWN` para `OK`.

Quando Streamlit não está instalado, não expõe `cache_data` ou não consegue criar
o wrapper, o mesmo leitor funciona sem cache. Os testes não exigem sessão
Streamlit, Docker, rede, exchange ou secrets.

## Falhas controladas

O contrato de leitura diferencia:

- `MISSING`: arquivo ausente;
- `INVALID_EMPTY`: arquivo vazio;
- `INVALID_JSON`: encoding ou JSON inválido;
- `INVALID_SCHEMA`: raiz não-objeto ou contrato incompleto;
- `BLOCKED`: path fora do ProjectRoot;
- `IO_ERROR`: falha previsível de leitura;
- `OK`: objeto JSON válido para validação de schema.

O snapshot adaptado para UI permanece fail-closed com `status=UNKNOWN` quando a
fonte não pode ser usada. `source_status` e `reason` preservam a causa. Mensagens
de parser não incluem o conteúdo do arquivo e não expõem tokens ou secrets.
Nesses fallbacks, `last_updated_utc` fica vazio e `loader_checked_at_utc` registra
somente o instante da tentativa, evitando apresentar erro recente como fonte fresca.

## Limites do cache

O cache é usado somente para snapshots JSON read-only em `data/reports`. Ele não
consulta exchange, saldo, ordens abertas, posições, partial fills, reconciliação
ou kill switch diretamente. Esses estados continuam sendo produzidos pelos
pipelines externos e apresentados sem reclassificação pelo dashboard.

Nenhuma página usa `st.cache_data`; todas continuam consumindo
`load_page_snapshot`.

## Segurança

Fallbacks mantêm:

- `paper_only=true`
- `shadow_only=true`
- `live_trading_enabled=false`
- `order_submission_enabled=false`
- `real_order_submission_enabled=false`
- `exchange_private_access=false`
- `sends_orders=false`
- `changes_risk=false`
- `canary_release_allowed=false`
- `live_release_allowed=false`

Não há `ccxt`, `OrderManager`, `NotificationDispatcher`, HTTP externo ou escrita
de estado financeiro nos loaders.

## Validação

```powershell
python -m pytest tests/test_streamlit_snapshot_loader_cache_resilience_v1.py -q
python scripts/build_dashboard_snapshots.py --project-root . --once --strict false --output-dir data/reports --json
python scripts/generate_project_manifest.py --check
python scripts/scan_versioned_secrets.py --json
```
