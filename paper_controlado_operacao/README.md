# SmartCrypto — Operação Paper Contínua

Extraia este pacote em:

```text
C:\Smart Cripto
```

O comando principal é:

```powershell
cd "E:\FUTUROS"
.\paper_controlado_operacao\START_PAPER_24H.ps1
```

Para 7 dias:

```powershell
.\paper_controlado_operacao\START_PAPER_7D.ps1
```

Para monitorar:

```powershell
.\paper_controlado_operacao\MONITOR_PAPER_SESSION.ps1
```

Para coletar evidência:

```powershell
.\paper_controlado_operacao\COLLECT_PAPER_EVIDENCE.ps1
```

Para parar apenas o Freqtrade paper:

```powershell
.\paper_controlado_operacao\STOP_PAPER_SESSION.ps1
```

Para derrubar todos os containers:

```powershell
.\paper_controlado_operacao\STOP_PAPER_SESSION.ps1 -Down
```

## Segurança

Este pacote mantém o projeto em paper/dry-run. O Risk Manager bloqueia operação se detectar flags de live/real order ativas:

```text
LIVE_ENABLED=true
ORDER_SUBMISSION_ENABLED=true
REAL_ORDER_SUBMISSION_ENABLED=true
```

## Dashboard

Acesse:

```text
http://localhost:8502
```

Páginas incluídas:

1. Visão geral
2. Qlib / Predições
3. Sinais
4. Freqtrade
5. Trades paper
6. Performance
7. Feedback dataset
8. Logs
9. Risco / Kill switch
10. Evidências

