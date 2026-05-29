# Runtime Authority Matrix

Esta matriz define a autoridade operacional oficial do FUTUROS/SmartCrypto. Ela existe para evitar ambiguidade entre Qlib, Phase13, Freqtrade, IA Shadow, Fase 14, Fase 5, Dashboard e Redis.

## Regra Central

O projeto permanece paper/shadow only, com live trading bloqueado. Nenhum componente abaixo pode habilitar live trading, enviar ordem real, usar chave real ou acessar exchange privada.

## Matriz Oficial

| Componente | Autoridade | Pode Executar Ordem? | Pode Escrever Sinal Operacional? | Observação |
| --- | --- | --- | --- | --- |
| Qlib | Research, scoring e prediction | Não | Não diretamente | Qlib produz score/predição para pesquisa e scoring. Não executa ordem. |
| Phase13 | Único produtor autorizado de sinal operacional | Não | Sim | Phase13 transforma predições autorizadas em sinal operacional, respeitando freshness e guardrails. |
| `data/runtime/active_freqtrade_signals.json` | Fonte operacional pinned | Não | Arquivo de saída autorizado do Phase13 | É a fonte pinned lida pelo Freqtrade no paper/dry-run. |
| Freqtrade | Único executor paper/dry-run | Sim, somente dry-run/paper | Não | Freqtrade é o executor paper. Não é live trading. |
| IA Shadow | Observador, filtro e análise | Não | Não | IA Shadow não escreve `active_freqtrade_signals.json` e não substitui Phase13. |
| Fase 14 | Coletor oficial de feedback paper | Não | Não | Lê feedback paper por snapshot SQLite e produz relatórios/datasets de feedback. |
| Fase 5 | Única via oficial de importação/rebuild | Não | Não | Importa trades e reconstrói `trades_master`, `trade_enriched` e `training_dataset`. |
| Dashboard | Read-only e observabilidade | Não | Não | Dashboard não executa trade, não altera risco e não habilita live. |
| Redis | Cache/mensageria opcional | Não | Não | Redis não é executor nem fonte de autoridade operacional. |

## Fluxo Autorizado

1. Qlib gera prediction/scoring em modo research.
2. Phase13 valida freshness e guardrails.
3. Phase13 escreve o sinal operacional autorizado.
4. `active_freqtrade_signals.json` atua como pinned signal.
5. Freqtrade consome o pinned signal em dry-run/paper.
6. Fase 14 coleta feedback paper via snapshot SQLite.
7. Fase 5 é a única via de importação/rebuild de trades e datasets.
8. IA Shadow observa, filtra e analisa sem executar e sem escrever sinal operacional.
9. Dashboard exibe estado e métricas em modo read-only.

## Limites De Autoridade

- Qlib não executa ordem.
- IA Shadow não executa ordem.
- Dashboard não executa ordem.
- Redis não executa ordem.
- Fase 14 não importa trades oficiais.
- Fase 5 não executa sinais.
- Freqtrade é executor somente em dry-run/paper.
- Phase13 é o único produtor autorizado do sinal operacional pinned.
