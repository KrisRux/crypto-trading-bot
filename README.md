# Crypto Trading Bot

Bot/agente di trading automatico per criptovalute con integrazione Binance Spot API, web dashboard responsive e modalita paper/live.

> **DISCLAIMER**: Questo software e fornito a scopo educativo e sperimentale. Il trading di criptovalute comporta rischi significativi. Non costituisce consiglio finanziario. Utilizza a tuo rischio e pericolo. Testa sempre prima in modalita paper trading.

---

## Funzionalita

- **Due modalita**: Paper trading (simulato) e Live trading (ordini reali su Binance)
- **Dati in tempo reale**: Prezzi via Binance WebSocket
- **Strategie configurabili**: SMA Crossover, RSI Reversal, MACD Crossover
- **Risk management**: Stop-loss, take-profit e position sizing automatici
- **Dashboard web responsive**: Utilizzabile da PC e smartphone
- **Export CSV**: Esporta lo storico dei trade in paper mode
- **Logging strutturato**: Log su file ruotati e console

## Architettura

```
bot_inv/
├── app/
│   ├── main.py                  # Entrypoint FastAPI
│   ├── config.py                # Configurazione da .env
│   ├── database.py              # Setup SQLAlchemy
│   ├── logging_config.py        # Logging strutturato
│   ├── api/
│   │   ├── routes.py            # REST API endpoints
│   │   └── schemas.py           # Pydantic schemas
│   ├── binance_client/
│   │   ├── rest_client.py       # Binance REST API (firmato HMAC-SHA256)
│   │   └── ws_client.py         # Binance WebSocket (prezzi real-time)
│   ├── strategies/
│   │   ├── base.py              # Classe astratta Strategy
│   │   ├── indicators.py        # SMA, EMA, RSI, MACD, Bollinger
│   │   ├── sma_crossover.py     # Strategia incrocio medie mobili
│   │   ├── rsi_strategy.py      # Strategia RSI overbought/oversold
│   │   └── macd_strategy.py     # Strategia MACD crossover
│   ├── trading_engine/
│   │   ├── engine.py            # Motore di trading principale
│   │   ├── order_manager.py     # Gestione esecuzione ordini
│   │   └── risk_manager.py      # Calcolo position size, SL/TP
│   ├── paper_trading/
│   │   └── portfolio.py         # Portafoglio virtuale
│   └── models/
│       ├── trade.py             # Modelli Trade, Order
│       └── portfolio.py         # Modelli PaperPortfolio, PaperPosition
├── frontend/                    # React + TypeScript + Tailwind CSS
│   ├── src/
│   │   ├── App.tsx              # Layout e routing
│   │   ├── api.ts               # Client API
│   │   ├── pages/
│   │   │   ├── Dashboard.tsx    # Saldo, posizioni, storico
│   │   │   ├── Strategies.tsx   # Configurazione strategie e rischio
│   │   │   └── Logs.tsx         # Segnali e ordini
│   │   ├── components/
│   │   │   └── StatCard.tsx
│   │   └── hooks/
│   │       └── usePolling.ts
│   └── ...
├── tests/                       # Test pytest
├── docker-compose.yml           # Deploy con Docker
├── requirements.txt             # Dipendenze Python
└── .env.example                 # Template configurazione
```

## Setup Rapido

### Prerequisiti

- Python 3.11+
- Node.js 18+
- Un account Binance (o Binance Testnet per paper trading)

### 1. Clona e configura

```bash
cd bot_inv
cp .env.example .env
# Modifica .env con le tue chiavi API
```

### 2. Backend

```bash
python -m venv venv
source venv/bin/activate   # Linux/macOS
# oppure: venv\Scripts\activate   # Windows

pip install -r requirements.txt
```

Avvia il backend:

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 3. Frontend

```bash
cd frontend
npm install
npm run dev
```

Il frontend sara disponibile su `http://localhost:5173` con proxy automatico verso il backend.

### 4. Esegui i test

```bash
pytest tests/ -v
```

## Configurazione API Key Binance

### Paper Trading (consigliato per iniziare)

1. Vai su [Binance Testnet](https://testnet.binance.vision/)
2. Effettua il login con GitHub
3. Genera le API keys
4. Inserisci le chiavi in `.env`:
   ```
   BINANCE_TESTNET_API_KEY=your_testnet_key
   BINANCE_TESTNET_API_SECRET=your_testnet_secret
   TRADING_MODE=paper
   ```

### Live Trading

1. Vai su [Binance API Management](https://www.binance.com/en/my/settings/api-management)
2. Crea una nuova API key
3. **IMPORTANTE**: Abilita SOLO "Enable Spot & Margin Trading"
4. **MAI abilitare** "Enable Withdrawals"
5. (Opzionale) Limita gli IP consentiti per sicurezza
6. Inserisci le chiavi in `.env`:
   ```
   BINANCE_API_KEY=your_live_key
   BINANCE_API_SECRET=your_live_secret
   TRADING_MODE=live
   ```

## Deploy con Docker

```bash
cp .env.example .env
# Modifica .env con le tue chiavi

docker-compose up --build -d
```

- Frontend: `http://localhost` (porta 80)
- Backend API: `http://localhost:8000`

## Parametri Configurabili

| Variabile | Default | Descrizione |
|-----------|---------|-------------|
| `TRADING_MODE` | `paper` | Modalita: `paper` o `live` |
| `PAPER_INITIAL_CAPITAL` | `10000` | Capitale iniziale paper (USDT) |
| `MAX_POSITION_SIZE_PCT` | `2.0` | Max % capitale per posizione |
| `DEFAULT_STOP_LOSS_PCT` | `3.0` | Stop loss default (%) |
| `DEFAULT_TAKE_PROFIT_PCT` | `5.0` | Take profit default (%) |
| `DEFAULT_SYMBOL` | `BTCUSDT` | Coppia di trading predefinita |

## Come Estendere le Strategie

Per aggiungere una nuova strategia:

1. Crea un file in `app/strategies/` (es. `my_strategy.py`)
2. Estendi la classe `Strategy`:

```python
from app.strategies.base import Strategy, Signal, SignalType

class MyStrategy(Strategy):
    name = "my_strategy"
    enabled = True

    def generate_signals(self, df, symbol):
        # La tua logica qui
        # df contiene: open, high, low, close, volume
        return []  # Lista di Signal

    def get_params(self):
        return {"enabled": self.enabled}
```

3. Registra la strategia in `app/main.py`:

```python
from app.strategies.my_strategy import MyStrategy
engine.register_strategy(MyStrategy())
```

La strategia apparira automaticamente nella pagina "Strategies" della web app.

## Note Tecniche

- Il trading engine esegue un ciclo ogni 60 secondi
- I prezzi real-time sono aggiornati via WebSocket
- Il database SQLite viene creato automaticamente al primo avvio
- I log vengono salvati in `logs/trading_bot.log` (rotazione automatica a 5 MB)
- Le richieste API a Binance sono firmate con HMAC-SHA256
- La modalita paper usa prezzi reali ma ordini simulati
