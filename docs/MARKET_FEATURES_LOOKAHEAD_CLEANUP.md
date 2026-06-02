# Market Features Lookahead Cleanup

## Objetivo

`data/features/market_features_60d.parquet` e um artefato operacional usado por
Qlib runtime, Phase13, Fase 5, dashboard e builders incrementais. Ele nao pode
conter colunas `future_ret_*`, pois isso cria risco de lookahead/leakage.

Este cleanup remove somente colunas `future_ret_*` do arquivo operacional atual,
com dry-run por padrao e backup obrigatorio antes de qualquer escrita.

## Dry-Run

Por padrao, o script nao escreve:

```powershell
python .\scripts\sanitize_market_features_lookahead.py
```

O relatorio informa quais colunas seriam removidas:

```text
data/reports/sanitize_market_features_lookahead_report.json
```

## Aplicar Limpeza

Para sobrescrever o arquivo operacional, use `--apply`:

```powershell
python .\scripts\sanitize_market_features_lookahead.py --apply
```

Antes de escrever, o script cria backup em:

```text
data/backups/market_features_lookahead_cleanup/<timestamp>/market_features_60d.parquet
```

## Contrato

O cleanup:

- remove apenas colunas `future_ret_*`;
- preserva numero de linhas;
- preserva todas as demais colunas;
- calcula hash antes e depois;
- gera relatorio JSON controlado;
- nao altera `training_dataset.parquet`;
- nao altera `trades_master`;
- nao toca no DB operacional do Freqtrade;
- nao chama exchange;
- nao envia ordens.

## Origem Do Artefato

Os writers operacionais devem chamar o contrato central em
`smartcrypto.market.market_feature_schema`:

```text
sanitize_operational_market_features(...)
write_operational_market_features(...)
```

Se labels futuras forem necessarias para treino offline ou walk-forward, elas
devem ser escritas em artefato separado de labels/targets, nunca em
`market_features_60d.parquet`.

`write_operational_market_features(...)` e a API preferencial para publicar
artefatos runtime. Ela sanitiza `future_ret_*`, grava opcionalmente labels em
arquivo separado quando solicitado e substitui o parquet operacional de forma
atomica.

## Auditoria De Writers Runtime

Para rastrear quem referencia ou publica o artefato operacional, use:

```powershell
python .\scripts\audit_market_features_runtime_writers.py
```

O relatorio e gravado em:

```text
data/reports/market_features_runtime_writer_audit.json
```

A auditoria escaneia `scripts/` e `smartcrypto/`, classifica referencias como
writer runtime, reader runtime, writer offline/labels, teste ou desconhecido, e
bloqueia writers runtime que nao passam pelo guard/sanitizacao central.

## Segurança

Este fluxo e paper/shadow only. Ele nao habilita live trading, nao usa API
privada, nao altera `.env`, nao reinicia containers e nao muda strategy.
