# Daily Learning Read-only Loaders V1

## Objetivo

Esta branch adiciona a primeira camada de loaders read-only para a esteira Daily
Paper/Master Learning Loop. O objetivo é inspecionar metadata mínima das fontes
definidas no source map da Branch 02, sem carregar linhas, sem calcular KPIs e
sem gerar autoridade operacional.

O relatório permanece:

- `status=blocked`
- `decision=MANTER_EM_RESEARCH`
- `research_only=true`
- `read_only=true`
- `operational_authority=false`

## Relação com Branch 01 e Branch 02

A Branch 01 consolidou o closeout research-only Paper vs `trades_master`, com a
decisão de manter a investigação fora da operação.

A Branch 02 definiu contratos, IDs canônicos de fontes, paths esperados e
freshness policy. Esta Branch 03 usa esses contratos e apenas verifica metadata
dos paths esperados quando eles existem.

## Source map vs loaders read-only

O source map declara o que existe como contrato. O loader read-only verifica
metadata básica da fonte:

- existência;
- tipo de path;
- tamanho quando é arquivo;
- contagem direta de arquivos quando é diretório;
- extensão;
- `mtime_utc`.

Ele não abre `trades_master`, não abre banco paper, não lê candles em massa e não
carrega linhas de trades.

## Por que não calcular KPIs nesta branch

KPI financeiro, PnL, win rate, divergence, temporal alignment, candle coverage e
features exigem loaders de conteúdo e regras de cálculo. Esses pontos ficam para
branches futuras. Misturar metadata loader com cálculo de KPI criaria risco de
escopo e dificultaria auditoria.

## Tratamento de fontes

Cada fonte retorna um item com:

- `source_id`;
- `expected_path`;
- `required`;
- `freshness_policy`;
- `exists`;
- `status`;
- `reason`;
- `read_attempted=false`;
- `write_attempted=false`;
- metadata;
- `sample_rows_loaded=0`;
- flags de que nenhum KPI, métrica financeira, alignment ou feature foi
  calculado.

Status possíveis:

- `metadata_only`: fonte existe, mas somente metadata foi inspecionada;
- `missing_required`: fonte obrigatória ausente;
- `missing_optional`: fonte opcional ausente;
- `invalid_path`: path inválido;
- `read_error`: falha de metadata;
- `available`: reservado para evolução futura, quando houver loader de conteúdo
  seguro em outra branch.

## Freshness por metadata

Freshness nesta branch usa apenas `mtime_utc`. Isso não prova que a fonte está
semanticamente atualizada, não aprova readiness, não libera canary e não libera
live.

## Semântica blocked-by-default

Mesmo se todas as fontes existirem, o relatório global continua `blocked`.
Existência de fonte não é evidência de edge, não é autorização de risco e não é
permissão para operação.

## Safety flags

O payload preserva:

- `paper_only=true`;
- `shadow_only=true`;
- `read_only=true`;
- `live_trading_enabled=false`;
- `canary_release_allowed=false`;
- `live_release_allowed=false`;
- `order_submission_enabled=false`;
- `real_order_submission_enabled=false`;
- `exchange_private_access=false`;
- `sends_orders=false`;
- `changes_risk=false`;
- `changes_model=false`;
- `updates_freqtrade=false`;
- `updates_risk_manager=false`;
- `updates_qlib_runtime=false`;
- `updates_ai_shadow_runtime=false`;
- `writes_runtime=false`;
- `writes_data=false`;
- `writes_sqlite=false`;
- `writes_parquet=false`;
- `runs_training=false`;
- `runs_ocr=false`;
- `runs_ai_shadow_incremental=false`.

## Próximos passos permitidos

- criar KPI pack diario em branch futura;
- criar divergence/alignment diario em branch futura;
- criar candle coverage/entry features em branch futura;
- criar mistake/winner catalog em branch futura;
- criar pattern mining research em branch futura.

## Ações proibidas

- calcular KPIs nesta branch;
- comparar Paper vs `trades_master` nesta branch;
- carregar linhas de trades nesta branch;
- carregar candles em massa nesta branch;
- alterar Freqtrade;
- alterar RiskManager;
- alterar Qlib runtime;
- alterar IA Shadow runtime;
- alterar modelos;
- alterar datasets;
- habilitar live ou canary;
- enviar ordem real;
- usar exchange privada;
- escrever artefatos em `data/`, `runtime/`, `reports/`, `logs/` ou
  `freqtrade/`.

## Execução

No-write por padrão:

```powershell
python .\scripts\build_daily_learning_readonly_loaders_v1.py --project-root . --no-write --json
```

Escrita explícita somente para path fora de diretórios runtime:

```powershell
python .\scripts\build_daily_learning_readonly_loaders_v1.py --project-root . --output "$env:TEMP\daily_learning_readonly_loaders.json" --json
```

## Validação

```powershell
python -m compileall scripts smartcrypto tests
python -m pytest .\tests\test_daily_learning_readonly_loaders_v1.py -q
python .\scripts\build_daily_learning_readonly_loaders_v1.py --project-root . --no-write --json
python .\scripts\generate_project_manifest.py --check
python .\scripts\scan_versioned_secrets.py --project-root . --json
python -m pip_audit -r ".\requirements-dev.lock" --progress-spinner off
git diff --check
```
