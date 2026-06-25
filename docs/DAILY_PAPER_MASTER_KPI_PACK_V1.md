# Daily Paper Master KPI Pack V1

## Objetivo

Esta branch cria o KPI pack agregado da esteira Daily Paper/Master Learning
Loop. O cálculo aceita somente trades normalizados já fornecidos em memória por
testes ou chamadas internas futuras. O CLI padrão não carrega trades reais e não
escreve runtime.

O relatório permanece:

- `status=blocked`
- `decision=MANTER_EM_RESEARCH`
- `research_only=true`
- `read_only=true`
- `operational_authority=false`

## Relação com Branch 01, Branch 02 e Branch 03

A Branch 01 fechou a divergência Paper vs `trades_master` como research-only.
A Branch 02 definiu contratos e source map. A Branch 03 criou loaders
metadata-only. Esta Branch 04 adiciona cálculos agregados em memória sem avançar
para alignment temporal, candle coverage, features, mineração de padrão ou
registro de regra.

## Diferença entre loaders metadata-only e KPI pack

Loaders metadata-only apenas verificam existência, tipo, tamanho e mtime de
fontes esperadas. O KPI pack calcula métricas agregadas somente quando recebe
listas de trades já normalizadas em memória.

O CLI desta branch não lê banco paper, planilha master, candles, modelos ou
artefatos oficiais.

## KPIs agregados calculados

Para cada conjunto de trades em memória:

- `trade_count`
- `win_count`
- `loss_count`
- `flat_count`
- `win_rate_pct`
- `loss_rate_pct`
- `gross_profit`
- `gross_loss_abs`
- `net_pnl`
- `profit_factor`
- `expectancy`
- `avg_win`
- `avg_loss_abs`
- `best_trade`
- `worst_trade`
- `max_drawdown`
- `avg_duration_minutes`
- `exit_reason_counts`
- `symbol_counts`
- `side_counts`

A comparação Paper vs Master calcula apenas deltas agregados. Ela não faz
matching por trade, não compara horários e não estima causa raiz.

## Por que a branch não lê dados reais por padrão

Esta etapa estabiliza o contrato do KPI pack. Ler dados reais nesta branch
misturaria cálculo agregado com implementação de loaders de conteúdo. Esse
trabalho fica para branches futuras e deve continuar read-only.

## Por que os KPIs não liberam operação

KPIs agregados não são evidência suficiente para live, canary, alteração de
risco, promoção de modelo ou mudança de estratégia. O payload declara
explicitamente que o KPI pack não é evidência de readiness.

## Semântica blocked-by-default

Mesmo quando os cálculos são válidos, o relatório global continua `blocked`.
Isso preserva a separação entre pesquisa, evidência operacional e autorização.

## Safety flags

O payload preserva:

- `paper_only=true`
- `shadow_only=true`
- `read_only=true`
- `live_trading_enabled=false`
- `canary_release_allowed=false`
- `live_release_allowed=false`
- `order_submission_enabled=false`
- `real_order_submission_enabled=false`
- `exchange_private_access=false`
- `sends_orders=false`
- `changes_risk=false`
- `changes_model=false`
- `updates_freqtrade=false`
- `updates_risk_manager=false`
- `updates_qlib_runtime=false`
- `updates_ai_shadow_runtime=false`
- `writes_runtime=false`
- `writes_data=false`
- `writes_sqlite=false`
- `writes_parquet=false`
- `runs_training=false`
- `runs_ocr=false`
- `runs_ai_shadow_incremental=false`

## Próximos passos

- criar divergence/alignment diario em branch futura;
- criar candle coverage/entry features em branch futura;
- criar mistake/winner catalog em branch futura;
- criar pattern mining research em branch futura;
- criar candidate shadow rule registry em branch futura.

## Ações proibidas

- alterar Freqtrade;
- alterar RiskManager;
- alterar Qlib runtime;
- alterar IA Shadow runtime;
- alterar modelos;
- alterar datasets;
- habilitar live;
- habilitar canary;
- enviar ordem real;
- usar exchange privada;
- escrever artefatos em `data/`, `runtime/`, `reports/`, `logs/` ou
  `freqtrade/`;
- usar KPIs para liberar operação;
- promover regra candidata;
- promover modelo.

## Execução

No-write por padrão:

```powershell
python .\scripts\build_daily_paper_master_kpi_pack_v1.py --project-root . --no-write --json
```

Escrita explícita somente para path fora de diretórios runtime:

```powershell
python .\scripts\build_daily_paper_master_kpi_pack_v1.py --project-root . --output "$env:TEMP\daily_paper_master_kpi_pack.json" --json
```

## Validação

```powershell
python -m compileall scripts smartcrypto tests
python -m pytest .\tests\test_daily_paper_master_kpi_pack_v1.py -q
python .\scripts\build_daily_paper_master_kpi_pack_v1.py --project-root . --no-write --json
python .\scripts\generate_project_manifest.py --check
python .\scripts\scan_versioned_secrets.py --project-root . --json
python -m pip_audit -r ".\requirements-dev.lock" --progress-spinner off
git diff --check
```
