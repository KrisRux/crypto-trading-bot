# Crypto Trading Bot

Bot di trading automatico per criptovalute con integrazione Binance Spot API, web dashboard responsive e modalita paper/live.

> **DISCLAIMER**: Questo software e fornito a scopo educativo e sperimentale. Il trading di criptovalute comporta rischi significativi. Non costituisce consiglio finanziario. Utilizza a tuo rischio e pericolo. Testa sempre prima in modalita paper trading.

---

## Funzionalita

- **Due modalita**: Paper trading (simulato) e Live trading (ordini reali su Binance)
- **Dati in tempo reale**: Prezzi via Binance WebSocket con riconnessione automatica
- **Strategie multiple**: SMA Crossover, RSI Reversal, MACD Crossover, Embient Enhanced (multi-fattore)
- **Conflict resolution**: Se due strategie danno segnali opposti sullo stesso simbolo nello stesso ciclo, il trade viene saltato
- **Risk management**: Stop-loss, take-profit e position sizing automatici (2% del capitale per trade)
- **Dashboard web responsive**: Utilizzabile da PC e smartphone
- **Export CSV**: Esporta lo storico dei trade in paper mode
- **Logging strutturato**: Log su file ruotati e console

## Architettura

```
bot_inv/
├── app/
│   ├── main.py                  # Entrypoint FastAPI + registrazione strategie
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
│   │   ├── macd_strategy.py     # Strategia MACD crossover
│   │   └── embient_enhanced.py  # Strategia multi-fattore con scoring (principale)
│   ├── trading_engine/
│   │   ├── engine.py            # Motore di trading principale
│   │   ├── order_manager.py     # Gestione esecuzione ordini
│   │   └── risk_manager.py      # Calcolo position size, SL/TP
│   ├── paper_trading/
│   │   └── portfolio.py         # Portafoglio virtuale per-utente
│   └── models/
│       ├── trade.py             # Modelli Trade, Order
│       ├── portfolio.py         # Modelli PaperPortfolio, PaperPosition
│       └── user.py              # Modello User (chiavi API, modalita trading)
├── frontend/                    # React + TypeScript + Tailwind CSS
│   ├── src/
│   │   ├── App.tsx              # Layout e routing
│   │   ├── api.ts               # Client API
│   │   ├── pages/
│   │   │   ├── Dashboard.tsx    # Saldo, posizioni, storico
│   │   │   ├── Strategies.tsx   # Configurazione strategie e rischio
│   │   │   └── Logs.tsx         # Segnali e ordini
│   │   └── components/
│   └── ...
├── tests/                       # Test pytest
├── deploy/
│   ├── setup.sh                 # Setup iniziale server
│   └── update.sh                # Aggiornamento (git pull + restart)
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
4. Inserisci le chiavi nelle impostazioni utente della dashboard

### Live Trading

1. Vai su [Binance API Management](https://www.binance.com/en/my/settings/api-management)
2. Crea una nuova API key
3. **IMPORTANTE**: Abilita SOLO "Enable Spot & Margin Trading"
4. **MAI abilitare** "Enable Withdrawals"
5. (Opzionale) Limita gli IP consentiti per sicurezza
6. Inserisci le chiavi nelle impostazioni utente della dashboard

## Deploy su Server (systemd)

```bash
# Prima installazione
sudo ./deploy/setup.sh

# Aggiornamento (pull + restart)
sudo ./deploy/update.sh

# Oppure manualmente
cd /opt/cryptobot
sudo -u cryptobot git pull
sudo systemctl restart cryptobot
sudo journalctl -u cryptobot -f
```

## Strategie

### Parametri attivi (configurati in `app/main.py`)

| Strategia | Parametri | Note |
|---|---|---|
| `sma_crossover` | fast=10, slow=30 | Golden/death cross su SMA |
| `rsi_reversal` | period=14, oversold=30, overbought=70 | Inversione da zone estreme |
| `macd_crossover` | fast=12, slow=26, signal=9 | Parametri standard |
| `embient_enhanced` | threshold=55, sma 10/30 | Multi-fattore con scoring 0-100 |

### Conflict Resolution

Se nello stesso ciclo (60s) due strategie generano segnali opposti (BUY + SELL) sullo stesso simbolo, il trade viene saltato. Il log mostrera:
```
INFO | User 1: conflicting signals on ETHUSDT — 1 BUY [macd_crossover] vs 1 SELL [embient_enhanced], skipping
```

### Strategia Embient Enhanced

Strategia principale multi-fattore con sistema di scoring (0-100). Genera un segnale solo se il punteggio supera la soglia (55). I fattori considerati:

| Fattore | Punti max | Trigger |
|---|---|---|
| SMA alignment | 25 | Fast > slow (bullish) + golden cross |
| RSI zone | 25 | Oversold < 30 o overbought > 70 |
| MACD momentum | 25 | MACD sopra signal + histogram crescente |
| Bollinger Band | 15 | Prezzo vicino banda inferiore/superiore |
| Volume | 10 | Volume > 1.5x media |

### Come aggiungere una nuova strategia

1. Crea un file in `app/strategies/` (es. `my_strategy.py`)
2. Estendi la classe `Strategy`:

```python
from app.strategies.base import Strategy, Signal, SignalType

class MyStrategy(Strategy):
    name = "my_strategy"
    enabled = True

    def generate_signals(self, df, symbol):
        # df contiene: open, high, low, close, volume
        return []  # Lista di Signal

    def get_params(self):
        return {"enabled": self.enabled}
```

3. Registra in `app/main.py`:

```python
from app.strategies.my_strategy import MyStrategy
engine.register_strategy(MyStrategy())
```

La strategia apparira automaticamente nella pagina "Strategies" della dashboard.

## Parametri Configurabili (.env)

| Variabile | Default | Descrizione |
|---|---|---|
| `SYMBOLS` | `BTCUSDT,ETHUSDT` | Simboli monitorati (comma-separated) |
| `MAX_POSITION_SIZE_PCT` | `2.0` | Max % capitale per posizione |
| `DEFAULT_STOP_LOSS_PCT` | `3.0` | Stop loss default (%) |
| `DEFAULT_TAKE_PROFIT_PCT` | `5.0` | Take profit default (%) |
| `LOG_LEVEL` | `INFO` | Livello logging |

## Note Tecniche

- Il trading engine esegue un ciclo ogni 60 secondi
- I prezzi real-time sono aggiornati via WebSocket (riconnessione automatica)
- Il database SQLite viene creato automaticamente al primo avvio
- I log vengono salvati in `/var/log/cryptobot.log` (rotazione automatica)
- Le richieste API a Binance sono firmate con HMAC-SHA256
- La modalita paper usa prezzi reali ma ordini simulati
- Ogni utente ha il proprio portafoglio paper isolato

## Reset Database

Per ripartire da zero sul server:

```bash
sudo systemctl stop cryptobot
sudo -u cryptobot mv /opt/cryptobot/trading_bot.db /opt/cryptobot/trading_bot.db.bak
sudo systemctl start cryptobot
# Il database viene ricreato automaticamente all'avvio
```

> Gli utenti e le chiavi API vengono persi — sara necessario riconfigurarli dalla dashboard.
