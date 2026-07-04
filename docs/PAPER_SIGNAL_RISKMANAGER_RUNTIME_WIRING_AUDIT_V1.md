# Paper Signal RiskManager Runtime Wiring Audit V1

Branch: `codex/paper-signal-riskmanager-runtime-wiring-audit-v1`

## Objetivo

O `paper_signal_riskmanager_runtime_wiring_audit_v1` confirma, com evidência de código e execução real (não apenas leitura do handover), que todo escritor de sinal que o Freqtrade paper efetivamente lê passa pelo `RiskManager` antes de qualquer sinal poder ser marcado `risk_approved=True`, e que a falha ou indisponibilidade do `RiskManager` sempre resulta em rejeição (fail-closed).

Ele é **paper/shadow/research-only, read-only por padrão**. O auditor não treina modelo, não altera registry, não ativa live/canary, não envia ordens, não acessa exchange privada, não altera limites de risco e não altera Freqtrade/Qlib/IA Shadow runtime.

## Contexto do achado corrigido

Uma auditoria externa do handover do SMART FUTUROS encontrou que o arquivo que o Freqtrade paper realmente prioriza ler (`data/runtime/active_freqtrade_signals.json`) era escrito por um caminho que nunca chamava `RiskManager.approve()` / `RiskManager.approve_many()`. Em vez disso, cada escritor carimbava todo sinal candidato com um valor fixo de `risk_approved`, tornando a decisão do RiskManager irrelevante para quais sinais chegavam ao Freqtrade.

A correção introduziu um gate único e compartilhado, `smartcrypto/execution/signal_risk_gate.py`, e o conectou aos três escritores relevantes e ao leitor (estratégia Freqtrade). Este módulo de auditoria existe para reverificar essa correção de forma objetiva e repetível, e para impedir sua regressão silenciosa.

## O que é verificado

### Escritores (evidência estática)

Para cada arquivo abaixo, o auditor lê o código-fonte (texto, sem executar) e confirma que ele importa e chama `apply_risk_manager_gate`, e que não contém um `"risk_approved": True` fixo (hardcode) próprio:

- `smartcrypto/execution/signal_producer.py`
- `smartcrypto/qlib_engine/signal_exporter.py`
- `smartcrypto/execution/signal_contract_guard.py`

### Leitor (evidência estática)

- `freqtrade/user_data/strategies/SmartCryptoSignalStrategy.py`

O auditor confirma que a checagem de sinal ativo usa comparação booleana estrita (`risk_approved is not True` / `is True` — nunca uma checagem "truthy" comum), e que o fallback entre arquivos de sinal (`data/runtime/active_freqtrade_signals.json` → `data/freqtrade_signals.json` → demais candidatos) continua percorrendo os próximos arquivos quando um arquivo existe mas não tem sinal ativo aprovado, em vez de parar no primeiro arquivo que meramente existe. O fallback em si nunca concede aprovação: cada candidato, em cada arquivo, ainda precisa de `risk_approved is True`.

A inspeção do arquivo de estratégia é somente leitura de texto (o módulo nunca importa `freqtrade.strategy.IStrategy`), pelo mesmo motivo já usado em outros pontos do projeto: o pacote `freqtrade` é uma dependência pesada, ausente em vários sandboxes de desenvolvimento/CI.

### Gate (evidência dinâmica)

O auditor chama `apply_risk_manager_gate` de verdade, três vezes, contra entradas controladas:

- `fail_closed_on_missing_config`: aponta para um `risk_limits.yml` inexistente e espera `status=blocked`, zero sinais aprovados;
- `fail_closed_on_risk_manager_exception`: injeta um `RiskManager` de teste cujo `approve_many` lança exceção, e espera `status=blocked`, zero sinais aprovados;
- `approved_and_rejected_signals_handled_correctly`: injeta um `RiskManager` de teste que aprova só sinais "long", e confirma que os sinais rejeitados nunca aparecem em `approved_signals` e são carimbados `risk_approved=False`.

### Limitação conhecida, fora de escopo (não escondida)

`smartcrypto/execution/market_signal_exporter.py` tem o mesmo padrão histórico de `"risk_approved": True` fixo e **não foi corrigido nesta branch**, porque não está conectado a nenhum serviço de execução contínua do `docker-compose.paper.yml` — apenas um script standalone (`scripts/export_market_freqtrade_signals.py`), executado manualmente, o utiliza. O auditor detecta e relata esse fato em `known_limitations` e `evidence_gaps` em vez de omiti-lo. Corrigi-lo exige uma branch própria.

## Decisão

- qualquer escritor em escopo não conectado ao gate: `status=blocked`, `reason=writer_not_wired_to_risk_manager_gate`;
- leitor sem checagem estrita ou sem fallback completo: `status=blocked`, `reason=reader_does_not_enforce_strict_risk_approval`;
- qualquer probe dinâmico do gate falhando: `status=blocked`, `reason=risk_manager_gate_probe_failed`;
- tudo conectado e todos os probes passando: `status=ok`, `reason=paper_signal_riskmanager_runtime_wiring_confirmed`.

O auditor nunca aprova live, canary, envio de ordens ou promoção de modelo. Ele apenas confirma (ou nega) que o gate de risco está de fato no caminho de execução do sinal paper.

## CLI

Modo padrão, sem escrita:

```bash
python3 scripts/audit_paper_signal_riskmanager_runtime_wiring_v1.py --project-root . --json
```

Escrita explícita de relatório (JSON + Markdown):

```bash
python3 scripts/audit_paper_signal_riskmanager_runtime_wiring_v1.py --project-root . --write-report --json
```

Com `--write-report`, somente estes artefatos são materializados, e apenas sob `data/reports/`:

- `data/reports/paper_signal_riskmanager_runtime_wiring_audit_v1.json`
- `data/reports/paper_signal_riskmanager_runtime_wiring_audit_v1.md`

O código de saída do processo é `0` quando `status=ok` e `2` quando `status=blocked`.

## Safety Flags

O output mantém:

```text
paper_only=true
shadow_only=true
research_only=true
live_behavior_changed=false
canary_behavior_changed=false
sends_orders=false
exchange_private_access=false
order_submission_enabled=false
real_order_submission_enabled=false
changes_risk=false
changes_model=false
updates_risk_manager=false
registry_write_performed=false
writes_runtime=false
writes_sqlite=false
writes_parquet=false
```

## Limitações

- A verificação dos escritores e do leitor é estática (leitura de texto/regex), não uma análise semântica completa de AST; uma reescrita do código que preserve as strings esperadas mas mude o comportamento real não seria detectada por este módulo sozinho — por isso os probes dinâmicos do gate existem como segunda camada de evidência.
- `market_signal_exporter.py` permanece com o padrão antigo e é reportado, não corrigido, por estar fora do escopo desta branch.
- Este relatório é evidência para revisão de PR, não autorização operacional. Ele não substitui `RiskManager` em runtime; ele apenas confirma que `RiskManager` está de fato no caminho.

## Por Que Não Há Promoção Automática

Confirmar que o gate de risco está conectado não é o mesmo que autorizar operação real. `status=ok` aqui significa apenas que nenhum escritor em escopo pode mais gerar um sinal ativo sem aprovação explícita do RiskManager, e que o RiskManager falha fechado. Live, canary, ordens reais, alteração de limites de risco e alteração de modelo continuam exigindo branches, gates e aprovações manuais próprios e separados.
