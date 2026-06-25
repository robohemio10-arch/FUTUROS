# Daily Paper Master Divergence Alignment V1

## Objetivo

Esta branch adiciona a camada diária de divergência agregada e temporal alignment
Paper/Master em modo research-only e read-only. O cálculo aceita somente listas
de trades já fornecidas em memória por testes ou chamadas futuras controladas.

O relatório permanece:

- `status=blocked`
- `decision=MANTER_EM_RESEARCH`
- `research_only=true`
- `read_only=true`
- `operational_authority=false`

## Relação com Branches 01 a 04

Branch 01 fechou a investigação Paper vs `trades_master` como research-only.
Branch 02 definiu contratos e source map. Branch 03 criou loaders metadata-only.
Branch 04 criou o KPI pack agregado. Esta Branch 05 reutiliza o KPI pack e
adiciona matching temporal em memória.

## KPI Pack vs Divergence/Alignment

O KPI pack mede deltas agregados: PnL, win rate, Profit Factor, expectancy,
contagem de trades e duração média.

Divergence/Alignment adiciona uma visão temporal: para cada trade Paper, tenta
encontrar o trade Master mais próximo dentro de uma janela configurada, sem
reutilizar o mesmo Master na mesma janela.

## Matching temporal

Janelas default:

- 15 minutos;
- 30 minutos;
- 60 minutos.

Um match ocorre quando:

- Paper e Master têm timestamp de entrada válido;
- símbolos são iguais quando ambos existem;
- diferença absoluta entre entradas fica dentro da janela;
- o Master mais próximo é selecionado;
- o Master selecionado não é reutilizado na mesma janela.

Sides são normalizados para `long` e `short`. `buy` vira `long`; `sell` vira
`short`.

## Categorias de mismatch

O relatório calcula:

- matched;
- unmatched Paper;
- unmatched Master;
- same-side;
- opposite-side;
- Paper stop after Master win;
- Paper entry after Master exit;
- Master winner missed;
- Paper loser without Master match.

Essas categorias são evidência de pesquisa. Elas não geram regra, veto, feature
ou autorização operacional.

## Por que a branch não lê dados reais por padrão

Esta etapa valida o contrato e a lógica determinística de matching. Ler fontes
reais exigiria loaders de conteúdo e governança adicional. O CLI desta branch
não aceita flags para carregar dados reais.

## Por que alignment não libera operação

Alignment temporal é diagnóstico. Ele pode orientar branches futuras de coverage,
catalogação e pesquisa, mas não aprova live, canary, alteração de risco, mudança
em estratégia, promoção de modelo ou regra candidata.

## Semântica blocked-by-default

Mesmo com alignment válido, o payload continua `blocked`. A branch não concede
autoridade operacional.

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

- criar candle coverage/entry features em branch futura;
- criar mistake/winner catalog em branch futura;
- criar pattern mining research em branch futura;
- criar candidate shadow rule registry em branch futura;
- criar OOS validation em branch futura.

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
- usar alignment para liberar operação;
- promover regra candidata;
- promover modelo;
- criar regras candidatas nesta branch.

## Execução

No-write por padrão:

```powershell
python .\scripts\build_daily_paper_master_divergence_alignment_v1.py --project-root . --no-write --json
```

Escrita explícita somente para path fora de diretórios runtime:

```powershell
python .\scripts\build_daily_paper_master_divergence_alignment_v1.py --project-root . --output "$env:TEMP\daily_paper_master_alignment.json" --json
```

## Validação

```powershell
python -m compileall scripts smartcrypto tests
python -m pytest .\tests\test_daily_paper_master_divergence_alignment_v1.py -q
python .\scripts\build_daily_paper_master_divergence_alignment_v1.py --project-root . --no-write --json
python .\scripts\generate_project_manifest.py --check
python .\scripts\scan_versioned_secrets.py --project-root . --json
python -m pip_audit -r ".\requirements-dev.lock" --progress-spinner off
git diff --check
```
