# Paper vs trades_master Divergence Research Closeout V1

## Objetivo

Esta branch versiona o closeout determinístico da investigação Paper vs
`trades_master`. O artefato é research-only: consolida evidências já apuradas e
não lê nem escreve `data/`, runtime, Freqtrade, SQLite, Parquet, modelos ou
qualquer fonte operacional.

Decisão institucional:

- `status=blocked`
- `decision=MANTER_EM_RESEARCH`
- `reason=paper_does_not_replicate_trades_master_edge`
- `operational_authority=false`

## Evidência consolidada

Na janela Paper 19D, o paper trading registrou 239 trades, PnL líquido de
`-21.35477552`, Profit Factor de `0.8033314207` e win rate de `40.5858%`.

No `trades_master` para a mesma janela, foram 243 trades, PnL líquido de
`143.166332`, Profit Factor de `2.0725730333` e win rate de `70.7819%`.

A divergência canônica é:

- PnL Paper menos Master: `-164.52110752`
- Win rate Paper menos Master: `-30.1961` pontos percentuais
- Conclusão: `paper_freqtrade_does_not_replicate_master_edge`

## Root cause: stop-loss

A principal degradação vem de excesso de stop-loss, especialmente em ETH long e
trades com duração inferior a 30 minutos.

Evidência registrada:

- Trades ROI: `97`
- PnL ROI: `87.22777285`
- Trades stop-loss: `142`
- PnL stop-loss: `-108.58254837`
- Simulação removendo stop-loss abaixo de 30m: `13.56136734`
- Delta simulado: `34.91614286`

Essa evidência não autoriza alteração operacional de stop-loss. Ela apenas abre
linha de pesquisa controlada.

## Temporal alignment mismatch

A divergência não é explicada apenas por stop-loss. Há diferença material de
timing, side e saída:

- Matches em 15m: `13`
- Matches em 30m: `31`
- Matches em 60m: `42`
- Lado oposto em 30m: `18`
- Lado oposto em 60m: `28`
- Paper stop após master win em 30m: `26`
- Paper stop após master win em 60m: `40`

## Coverage parcial

A cobertura de candle de entrada permite apenas análise parcial/local:

- Total Paper: `239`
- Trades cobertos no candle de entrada: `192`
- Cobertura: `80.33%`
- Trades sem cobertura: `47`
- Sem cobertura: `19.67%`
- Materialização completa de features: `false`
- Materialização parcial/local: `true`

## Candidate shadow rules

A melhor regra candidata registrada é:

- `lb_10m_ret_close <= -0.0038501215827868`
- `lb_30m_ret_close <= -0.0060685748963285`

Resultado de pesquisa:

- Flagged count: `32`
- Target flagged: `21`
- Baseline flagged: `11`
- Precision: `65.625%`
- Recall: `41.176%`
- Delta simulado de PnL removido: `8.9745`

Essa regra pode ser revisada em research, mas não pode ser promovida para
Freqtrade, RiskManager, IA Shadow runtime ou qualquer executor.

## Decisão final

`BLOQUEADO_PARA_OPERACAO_MANTER_EM_RESEARCH`

Permanece proibido:

- alterar Freqtrade strategy;
- alterar RiskManager;
- alterar stop-loss operacional;
- alterar stake ou leverage;
- promover regra candidata;
- promover modelo;
- habilitar live ou canary;
- enviar ordem real;
- usar exchange privada;
- escrever em `data/` ou runtime;
- rodar OCR, rebuild, training ou IA Shadow incremental nesta branch.

## Próximos passos permitidos

- versionar contratos do Daily Learning source map;
- criar loaders read-only;
- criar KPI pack diario;
- criar divergence/alignment diario;
- criar candle coverage/entry features diario;
- criar mistake/winner catalog;
- criar pattern mining research;
- criar candidate shadow rule registry;
- criar OOS validation.

## Execução

No-write por padrão:

```powershell
python .\scripts\build_paper_master_divergence_research_closeout_v1.py --project-root . --no-write --json
```

Escrita explícita somente para caminho fora de `data/`, `runtime/`, `reports/`,
`logs/` e `freqtrade/`:

```powershell
python .\scripts\build_paper_master_divergence_research_closeout_v1.py --project-root . --output "$env:TEMP\paper_master_divergence_closeout.json" --json
```

## Validação

```powershell
python -m compileall scripts smartcrypto tests
python -m pytest .\tests\test_paper_master_divergence_research_closeout_v1.py -q
python .\scripts\build_paper_master_divergence_research_closeout_v1.py --project-root . --no-write --json
python .\scripts\generate_project_manifest.py --check
python .\scripts\scan_versioned_secrets.py --project-root . --json
python -m pip_audit -r ".\requirements-dev.lock" --progress-spinner off
git diff --check
```
