# Runtime Safety Paper Config Contract V1

## Objetivo

`config/runtime_safety.paper.yml` é o contrato canônico, versionado e
fail-closed para validar o ambiente paper/shadow do SMART FUTUROS. Ele elimina a
dependência de um YAML temporário para demonstrar que as flags e os limites
mínimos atendem ao schema `runtime_safety_config_v1`.

## Relação com o validator

O arquivo é consumido em modo read-only por
`scripts/validate_runtime_safety_config.py`. O validator confere schema, versão,
modo de runtime, flags booleanas e limites positivos. Com `--strict`, qualquer
warning também se torna blocker.

Uma validação bem-sucedida retorna:

- `status=ok`;
- `reason=runtime_safety_config_ok`;
- listas vazias em `blocking_findings`, `missing_required_keys` e
  `unsafe_flags`;
- runtime `paper`, com dry-run, paper/shadow e kill switch habilitados;
- live, canary, ordens e acesso privado à exchange desabilitados.

## Campos obrigatórios

O contrato declara:

- `schema_version=runtime_safety_config_v1`;
- `config_version=runtime_safety_paper_v1`;
- `runtime_mode=paper`;
- `dry_run=true`;
- `paper_only=true`;
- `shadow_only=true`;
- `kill_switch_enabled=true`.

Os limites cobrem drawdown, perdas diária e semanal, perdas consecutivas,
spread, slippage, latência, idade de dados e idade máxima de predições. Todos são
positivos e permanecem dentro dos níveis recomendados pelo validator.

## Safety flags

As flags de live, canary, envio de ordens, envio real de ordens, acesso privado à
exchange e `sends_orders` permanecem `false`. Dashboard e IA não recebem
autoridade para mudar risco, habilitar live, promover modelo, alterar leverage ou
alterar stake.

O YAML não contém credenciais, chaves de exchange ou endpoints privados.

## Limites da branch

Esta branch não executa Freqtrade, Qlib, IA Shadow, OCR, rebuild de dataset,
treinamento, promoção de modelo, ordens, acesso privado à exchange ou atualização
de risco. Também não altera módulos operacionais nem materializa
`data/runtime/runtime_safety_audit_config.json`.

Esta branch não fecha runtime evidence/readiness/soak/freshness. Ela apenas
fornece o contrato canônico paper-only que permitirá materializar
`runtime_safety_audit_config.json` em uma etapa posterior.

## Validação

Use um diretório temporário para o relatório:

```powershell
$tmp = Join-Path $env:TEMP ("futuros_runtime_safety_" + [guid]::NewGuid())
New-Item -ItemType Directory -Force -Path $tmp | Out-Null

python .\scripts\validate_runtime_safety_config.py `
  --config .\config\runtime_safety.paper.yml `
  --environment paper `
  --report (Join-Path $tmp "runtime_safety_audit_config.json") `
  --strict

python -m pytest .\tests\test_runtime_safety_paper_config_contract_v1.py -q
```

## Readiness e próximos passos

`status=ok` comprova somente que o config atende ao contrato de segurança
paper/shadow. Ele não aprova readiness, não encerra soak, não comprova freshness e
não autoriza canary, live ou ordens.

A etapa posterior poderá materializar a evidência runtime a partir deste config,
mantendo validação estrita, outputs ignorados pelo Git e os gates institucionais
de readiness independentes.
