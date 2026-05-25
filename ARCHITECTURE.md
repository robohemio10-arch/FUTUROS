# Arquitetura

## Visão

```text
Binance Futures Data
        ↓
Data Downloader
        ↓
Market Feature Builder
        ↓
Trade Enricher
        ↓
Qlib/ML Model
        ↓
Prediction Exporter
        ↓
RiskManager
        ↓
freqtrade_signals.json
        ↓
Freqtrade Strategy
        ↓
Binance Futures
        ↓
State Repository / Dashboard / Logs
```

## Responsabilidades

| Componente | Responsabilidade |
|---|---|
| Docker | Ambiente operacional e isolamento |
| SmartCrypto Bot | Orquestrar score, risco e exportação de sinais |
| Qlib/ML | Pesquisa, treino, inferência e ranking |
| RiskManager | Autoridade final antes do sinal |
| Freqtrade | Executor final de ordens |
| Dashboard | Centro de comando e observabilidade |
| SQLite | Memória operacional e auditoria |
| Logs/Evidence | Prova operacional para Go/No-Go |

## Regras de arquitetura

1. Apenas um executor final pode operar em live.
2. Qlib/ML nunca envia ordem.
3. RiskManager é obrigatório antes do Freqtrade.
4. Sinal antigo expira.
5. Paper é obrigatório antes de live.
6. O dashboard lê estado confirmado, não estado presumido.
7. Configuração live deve ser separada de paper.
8. API key real não entra em repositório.
