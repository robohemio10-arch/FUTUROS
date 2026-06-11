# SMART FUTUROS Command Center V2 - Controls and Alerts Stubs

## Objetivo

Esta Branch 4 adiciona governança simulada ao **SMART FUTUROS Command Center**, nome
visual do **SMART FUTUROS Institutional Dashboard**. Ela sucede os contratos da Branch 1,
os snapshot builders da Branch 2 e as páginas read-only da Branch 3.

O resultado é uma camada de contratos futuros. Nenhum comando ou alerta é executado.

## Achados da Perícia

A perícia técnica manteve o desenho de oito abas e recuperou três blocos semânticos:

- **Readiness & Gates** fica na Aba 6 porque representa autorização, bloqueios e controles.
- **Financial Event Log / Decision Trace** fica na Aba 7 como rastreabilidade quantitativa.
- **Dataset / OCR / Training Pipeline Status** fica na Aba 7 como qualidade e proveniência.

Quando os campos ainda não existem nos snapshots, a interface mostra
`UNKNOWN/MISSING_OPTIONAL`. Ela não cria fonte substituta.

## Contratos de Comando

Os comandos são classificados em quatro níveis:

- N1: local e informativo, aceito apenas como operação de UI.
- N2: intenção dry-run sem execução.
- N3: intenção dry-run sensível com confirmação manual obrigatória.
- N4: `HARD_BLOCKED` sempre.

O `DashboardCommandStubAdapter` valida payloads e retorna um resultado serializável em
memória. `executed` é sempre falso. N4 nunca é aceito. O audit declara ausência de
CommandBus operacional, ordens, exchange privada, alteração de risco, configuração,
modelo, active signals, readiness, OCR, datasets ou runtime.

Não existe CommandBus real nesta camada. O módulo legado não é importado pelos stubs.

## Alertas e Mensageria

O roteamento é um contrato observacional:

- INFO: LOG.
- WARNING: TELEGRAM.
- CRITICAL: TELEGRAM e NTFY.
- PANIC: TELEGRAM, NTFY e OPERATOR_REQUIRED.

O `NotificationStubDispatcher` apenas devolve uma simulação. `sent` permanece falso,
nenhum token é lido e nenhuma biblioteca HTTP é usada. Não há fila ou dead letter runtime.

## Integração das Abas

### Aba 6

Mostra políticas N1-N4, resultados estáticos dry-run, painel N4 e a visão de Readiness &
Gates. Canary e live são forçados a falso; 7 dias não liberam canary e 30 dias não aprovam
automaticamente. O go/no-go manual permanece obrigatório.

### Aba 7

Mantém TCA e relatórios quantitativos e acrescenta Decision Trace e status do pipeline
Dataset/OCR/Training. Tudo vem do snapshot quantitativo existente. Não há leitura de DB
bruto, execução OCR, importação, rebuild ou escrita de dataset.

### Aba 8

Mantém `dashboard_alerts_messaging_snapshot.json` como fonte canônica e mostra políticas e
uma simulação de dispatch. Telegram e NTFY não são chamados.

## Segurança Operacional

Esta branch não executa comandos, não altera risco, não chama CommandBus real, não envia
Telegram/NTFY real, não chama exchange, não promove modelos, não executa OCR, não importa
trades, não reconstrói datasets, não altera readiness, não libera canary e não libera live.

O modo permanece paper/shadow only. RiskManager continua sendo a autoridade operacional.

## Validação

```powershell
python -m compileall scripts smartcrypto tests
python -m pytest tests/test_dashboard_controls_contracts_v2.py tests/test_dashboard_command_classifier_v2.py tests/test_dashboard_command_stub_adapter_v2.py tests/test_dashboard_controls_page_stubs_v2.py -q
python -m pytest tests/test_dashboard_alerts_contracts_v2.py tests/test_dashboard_notification_stub_dispatcher_v2.py tests/test_dashboard_alerts_page_stubs_v2.py tests/test_dashboard_controls_alerts_static_safety_v2.py -q
python -m pytest tests/test_dashboard_readiness_gates_snapshot_view_v2.py tests/test_dashboard_financial_event_log_decision_trace_v2.py tests/test_dashboard_dataset_ocr_pipeline_status_v2.py -q
python -m pytest -q
```

## Fora do Escopo

Não há execução operacional, mutação de kill switch, alteração de grid, dispatcher real,
HTTP, token, OCR, importação oficial, rebuild, limpeza SQLite, soak, Monte Carlo, evidence
pack, canary ou live.

A próxima branch aplica exclusivamente o tema visual institucional SMART FUTUROS.
