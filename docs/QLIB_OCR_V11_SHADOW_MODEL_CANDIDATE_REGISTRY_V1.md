# Qlib OCR V1.1 Shadow Model Candidate Registry V1

## Objetivo

Esta branch registra o candidato supervisionado Qlib OCR V1.1 como evidência
isolada de pesquisa/shadow. O registro documenta identidade, hash do artefato,
métricas e blockers de promoção sem alterar o modelo, o Qlib runtime ou qualquer
componente operacional.

O estado esperado é:

```text
candidate_registry_status=registered_research_only
promotion_status=blocked
decision=MANTER_EM_RESEARCH
```

## Por que existe um registry separado

O projeto já possui `smartcrypto/ml/model_registry.py` e o registry runtime
`data/models/registry/model_registry.json` para governança AI Shadow. Este fluxo
não importa esse módulo e não lê ou escreve esse arquivo.

O candidato OCR V1.1 ainda não superou os gates financeiros. Misturá-lo ao
registry existente criaria ambiguidade entre evidência experimental e modelo
aprovado. Por isso, o arquivo desta branch usa escopo explícito:

`qlib_ocr_v11_research_shadow_only`

## Inputs

- `data/reports/qlib_ocr_v11_supervised_training_summary.json`
- `data/reports/training_reports/smart_futuros_training_executive_pack.json`
- `data/models/qlib_ocr_v11/research/qlib_ocr_v11_supervised_candidate.joblib`

Os dois JSONs fornecem status, decisões, métricas e safety flags das Branches 04
e 05. O `.joblib` é tratado exclusivamente como blob opaco.

## O artefato não é carregado

O registry não importa `joblib` ou `pickle`, não instancia modelo e não executa
predição. A identidade do artefato usa somente:

- caminho;
- tamanho em bytes;
- SHA256 calculado por leitura binária em blocos.

Essa restrição evita execução de conteúdo serializado não confiável e mantém o
registro independente das dependências do modelo.

## Outputs runtime

Somente `--write` materializa:

- `data/models/qlib_ocr_v11/research/qlib_ocr_v11_shadow_candidate_registry.json`
- `data/reports/qlib_ocr_v11_shadow_model_candidate_registry_report.json`

Ambos ficam sob `data/`, são ignorados pelo Git e usam escrita JSON atômica com
chaves ordenadas. O modo default `--no-write` constrói o preview em memória sem
criar ou alterar arquivos.

## Idempotência e histórico

O candidato é identificado por `model_id`, versão research e prefixo do hash do
artefato. Repetir o registro do mesmo candidato atualiza sua entrada em vez de
duplicá-la.

O registry preserva:

- `registration_events`;
- `rejected_promotions`;
- eventual `champion_model_id` e `champion_model_version` preexistentes.

Nenhum champion é criado nesta branch.

## Blockers de promoção

O gate bloqueia promoção quando encontra qualquer uma destas condições:

- Branch 04 com status diferente de `ok`;
- decisão da Branch 04 não explicitamente aprovada para research candidate;
- seletor não supera o baseline all-test;
- métricas perfeitas suspeitas;
- PnL selecionado menor ou igual ao PnL all-test;
- Branch 05 mantém o modelo em research ou possui status warning/blocked;
- artefato ausente;
- safety flag insegura em qualquer input;
- tentativa implícita de promoção dentro deste registry research-only.

No estado atual, os blockers incluem status warning das Branches 04 e 05,
decisão `MANTER_EM_RESEARCH`, resultado financeiro abaixo do baseline e a
proibição estrutural de promoção deste escopo.

## CLI

Preview seguro, sem escrita:

```powershell
python .\scripts\register_qlib_ocr_v11_shadow_candidate.py `
  --project-root "." `
  --no-write `
  --json
```

Registro research-only explícito:

```powershell
python .\scripts\register_qlib_ocr_v11_shadow_candidate.py `
  --project-root "." `
  --write `
  --json
```

Os inputs e outputs podem ser substituídos com `--training-summary`,
`--executive-pack`, `--model-path`, `--registry-output` e `--report-output`.
`--strict` mantém o registro research-only, mas classifica o report como
`blocked` quando o gate possui blockers.

Exit code 0 cobre resultados controlados `ok`, `warning` e `blocked`. Exit code
1 é reservado a erro estrutural inesperado, como JSON inválido.

## Safety flags

Registry e report preservam:

- `paper_only=true` e `shadow_only=true`;
- live, ordens, exchange privada e produção desabilitados;
- nenhuma alteração de risco, modelo, Freqtrade, Qlib runtime ou RiskManager;
- nenhum treino, IA Shadow incremental, limpeza SQLite ou auto-promoção;
- `registers_model=false`, pois não há registro no registry produtivo.

## Validação

```powershell
python -m compileall scripts smartcrypto tests
python -m pytest .\tests\test_qlib_ocr_v11_shadow_model_candidate_registry_v1.py -q
python -m pytest .\tests\test_training_executive_report_pack_v1.py -q
python -m pytest .\tests\test_qlib_ocr_v11_supervised_training_lab.py -q
python -m pytest .\tests\test_model_registry.py -q
python -m pytest .\tests\test_model_registry_promotion_gate.py -q
python .\scripts\generate_project_manifest.py --check
python .\scripts\scan_versioned_secrets.py --project-root "." --json
python -m pip_audit -r .\requirements-dev.lock --progress-spinner off
```

## Decisão institucional

O candidato pode ser registrado como evidência research/shadow para auditoria e
comparação futura. Ele não deve ser promovido, registrado em produção ou ligado
ao Qlib runtime enquanto os blockers financeiros e de governança permanecerem.
