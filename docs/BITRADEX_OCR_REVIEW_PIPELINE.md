# Bitradex OCR Review Pipeline

Este pipeline e somente offline/review. Ele nao envia ordens, nao chama exchange privada, nao altera `.env`, nao modifica Docker e nao atualiza automaticamente `trades_master`, `trade_enriched`, `training_dataset` ou qualquer dataset oficial.

## Objetivo

O script `scripts/ocr_bitradex_images_to_review.py` ajuda a transformar screenshots locais da Bitradex em uma tabela de revisao manual. A saida contem texto OCR, candidatos extraidos e status de revisao, mas nenhuma linha deve ser importada sem conferencia humana.

## Por que screenshots nao sao versionados

Screenshots podem conter dados sensiveis, timestamps, identificadores, balances ou detalhes de operacao. Por isso, a pasta local de imagens deve ficar fora do git. Use um diretorio local privado e aponte o script com `--input-dir`.

## Como rodar

```powershell
python scripts/ocr_bitradex_images_to_review.py `
  --input-dir E:\CAMINHO\LOCAL\BITRADEX `
  --output-dir data/runtime/bitradex_ocr_review `
  --report data/reports/bitradex_ocr_review_report.json `
  --lang eng
```

Para validar descoberta de arquivos sem OCR:

```powershell
python scripts/ocr_bitradex_images_to_review.py `
  --input-dir E:\CAMINHO\LOCAL\BITRADEX `
  --output-dir data/runtime/bitradex_ocr_review `
  --report data/reports/bitradex_ocr_review_report.json `
  --dry-run `
  --no-xlsx
```

## Revisao antes da Fase 5

O OCR gera arquivos de revisao como CSV/XLSX e textos brutos. Antes de qualquer importacao para a Fase 5:

- conferir simbolo;
- conferir side/direcao;
- conferir preco de entrada;
- conferir preco de saida;
- conferir leverage;
- conferir horario;
- conferir PnL;
- remover linhas ambíguas ou incompletas.

O script nao move arquivos para inbox, nao escreve `trades_master` e nao aciona rebuild.

## Riscos de OCR

OCR pode errar:

- separador decimal;
- escala de preco;
- leverage `20x` vs `2.0`;
- side long/short;
- horario e timezone;
- PnL positivo/negativo;
- caracteres parecidos, como `O` e `0`.

Esses riscos justificam os reparos financeiros ja implementados no projeto: price scale OCR repair, financial input repair, leverage/PnL/return consistency e final financial quality resolution. Mesmo assim, reparos nao substituem revisao humana.

## Dependencias OCR

O script tenta usar `opencv-python`, `pillow`, `pytesseract` e o binario local do Tesseract quando disponiveis. A ausencia do engine nao quebra testes: use `--dry-run`, ou o script registra `OCR_ENGINE_UNAVAILABLE` para revisao de setup.

## O que isso nao libera

Este pipeline nao libera live trading, nao habilita `ORDER_SUBMISSION_ENABLED`, nao habilita `REAL_ORDER_SUBMISSION_ENABLED`, nao altera risco e nao executa trade. Ele e apenas uma ferramenta de preparacao e revisao manual offline.
