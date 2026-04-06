# Crypto Trading Bot

Bot di trading automatico per criptovalute con integrazione Binance Spot API, web dashboard responsive, adaptive layer a 3 livelli con notifiche Telegram e human-in-the-loop.

> **DISCLAIMER**: Questo software e fornito a scopo educativo e sperimentale. Il trading di criptovalute comporta rischi significativi. Non costituisce consiglio finanziario. Utilizza a tuo rischio e pericolo. Testa sempre prima in modalita paper trading.

---

## Funzionalita

### Trading Engine
- **Tre modalita**: Dry Run (solo log), Paper (Binance Testnet), Live (ordini reali)
- **Dati in tempo reale**: Prezzi via Binance WebSocket con riconnessione automatica
- **Ciclo ogni 15 minuti**: Candele 15m per ridurre overtrading
- **Cooldown per simbolo**: 15 minuti tra un trade e l'altro per utente/simbolo
- **Multi-utente**: Ogni utente ha il proprio portafoglio, chiavi API e modalita

### Strategie
- **SMA Crossover**: Golden/death cross con filtro ADX >= 25
- **RSI Reversal**: Inversione da zone oversold/overbought
- **MACD Crossover**: Crossover MACD/signal con filtro ADX >= 25
- **Embient Enhanced**: Strategia principale multi-fattore con scoring 0-100, regime-aware (trend/range/neutral)
- **Parametri configurabili dalla UI**: Ogni strategia e modificabile senza deploy
- **Persistenza parametri**: Salvati in JSON, sopravvivono ai restart

### Regime Gate (ADX-based)
- **TREND (ADX >= 25)**: embient_enhanced ha priorita assoluta, rsi_reversal completamente disabilitata
- **RANGE (ADX < 25)**: rsi_reversal OK, embient solo se score >= 80
- **Signal arbitration**: Risoluzione conflitti BUY vs SELL con priorita per regime

### Adaptive Layer (3 livelli)
- **Market Regime Service**: Classifica il mercato per simbolo e globale usando ADX, ATR%, BB width, volume ratio. Regimi: trend, range, volatile, defensive
- **Performance Monitor**: Metriche rolling (PnL 1h/6h/24h, win rate, drawdown intraday, consecutive losses, trades/hour)
- **Profile Manager**: 3 profili (defensive, normal, aggressive_trend) con switching automatico deterministico, cooldown, hysteresis, limiti giornalieri
- **Notification Service**: Notifiche Telegram per-utente con dedup e rate limiting
- **Approval Service**: Human-in-the-loop per cambi profilo aggressivi (pending/approved/rejected/expired)
- **LLM Advisor**: Advisor read-only che spiega il comportamento del bot e suggerisce profili (mai execution)
- **Meta Controller**: Orchestratore che coordina tutti i servizi dopo ogni ciclo del motore

### Profili di Trading

| Profilo | max_position | SL | TP | embient trend_buy | Auto? |
|---|---|---|---|---|---|
| **defensive** | 1.0% | 3% | 5% | 85 | auto |
| **normal** | 1.5% | 3% | 5% | 80 | auto |
| **aggressive_trend** | 1.5% | 3% | 6% | 75 | richiede approvazione |

### Regole di Switching

| Da → A | Condizione |
|---|---|
| normal → defensive | pnl_6h <= -2 OR 3+ consecutive losses OR drawdown >= 1.5% |
| defensive → normal | win_rate >= 55% AND drawdown < 1% AND pochi API errors |
| normal → aggressive_trend | regime trend, win_rate >= 60%, pnl_6h > 0 (richiede approvazione) |
| aggressive → defensive | pnl_6h <= -2 OR 2+ losses OR drawdown >= 1.5% |
| qualsiasi → defensive | regime volatile o defensive |

### Notifiche Telegram
- **CRITICAL**: Drawdown breach, approval required, bot paused, API errors persistenti
- **WARNING**: Profile switch, consecutive losses
- **INFO**: Regime change, daily summary
- Notifica inviata una sola volta per episodio (si resetta quando la condizione rientra)
- Bot token server-wide, chat_id per-utente configurabile dalla UI

### Risk Management
- Stop-loss e take-profit automatici (configurabili per profilo)
- Position sizing basato su % del capitale
- Lot size arrotondato ai decimali Binance (step size)

### Web App
- **Dashboard**: Saldo, posizioni aperte con PnL%, banner profilo/regime/metriche, chiusura manuale posizioni
- **Strategies**: Configurazione parametri per ogni strategia con salvataggio live
- **Settings**: Modalita trading, chiavi API, orario trading, Telegram (chat_id + test)
- **Logs**: Segnali e ordini
- **Responsive**: Utilizzabile da PC e smartphone
- **Autenticazione JWT**: Cookie httpOnly, ruoli admin/user/guest

## Architettura

```
bot_inv/
├── app/
│   ├── main.py                    # Entrypoint FastAPI + startup adaptive layer
│   ├── config.py                  # Configurazione da .env
│   ├── database.py                # Setup SQLAlchemy + auto-migration colonne
│   ├── logging_config.py          # Logging strutturato
│   ├── strategy_store.py          # Persistenza parametri strategie/rischio (JSON)
│   │
│   ├── api/
│   │   ├── routes.py              # REST API (auth, trading, adaptive, approvals)
│   │   ├── auth.py                # JWT, password hashing, ruoli
│   │   └── schemas.py             # Pydantic schemas
│   │
│   ├── adaptive/                  # Layer adattivo a 3 livelli
│   │   ├── market_regime_service.py   # Regime detection (ADX, ATR, BB, volume)
│   │   ├── performance_monitor.py     # Metriche rolling da trade history
│   │   ├── profile_manager.py         # Profili + switching rules + hysteresis
│   │   ├── notification_service.py    # Telegram Bot API (per-utente)
│   │   ├── approval_service.py        # Human-in-the-loop approvals
│   │   ├── llm_advisor.py            # Advisor read-only (mai execution)
│   │   └── meta_controller.py        # Orchestratore post-cycle
│   │
│   ├── binance_client/
│   │   ├── rest_client.py         # Binance REST API (HMAC-SHA256)
│   │   └── ws_client.py           # Binance WebSocket (prezzi real-time)
│   │
│   ├── strategies/
│   │   ├── base.py                # Classe astratta Strategy + Signal
│   │   ├── indicators.py          # SMA, EMA, RSI, MACD, Bollinger, ADX
│   │   ├── sma_crossover.py       # Incrocio medie mobili + ADX gate
│   │   ├── rsi_strategy.py        # RSI oversold/overbought
│   │   ├── macd_strategy.py       # MACD crossover + ADX gate
│   │   └── embient_enhanced.py    # Multi-fattore regime-aware (principale)
│   │
│   ├── trading_engine/
│   │   ├── engine.py              # Motore principale (multi-user, multi-symbol)
│   │   ├── order_manager.py       # Esecuzione ordini Binance
│   │   └── risk_manager.py        # Position sizing, SL/TP
│   │
│   ├── paper_trading/
│   │   └── portfolio.py           # Portafoglio virtuale per-utente
│   │
│   ├── models/
│   │   ├── user.py                # User (auth, API keys, Telegram, schedule)
│   │   ├── trade.py               # Trade, Order, enums
│   │   ├── portfolio.py           # PaperPortfolio, PaperPosition
│   │   ├── symbol.py              # TradingSymbol
│   │   └── approval.py            # ApprovalRequest
│   │
│   └── embient_skills/            # Knowledge base trading (56 skills, 7 categorie)
│       ├── loader.py
│       └── data/
│
├── config/
│   └── profiles.json              # Profili trading (editable senza deploy)
│
├── frontend/                      # React + TypeScript + Tailwind CSS
│   └── src/
│       ├── App.tsx                # Layout e routing
│       ├── api.ts                 # Client API (typed)
│       ├── pages/
│       │   ├── Dashboard.tsx      # Saldo, posizioni, PnL%, banner adaptive
│       │   ├── Strategies.tsx     # Config strategie con auto-save
│       │   ├── Settings.tsx       # API keys, Telegram, modalita, orario
│       │   └── Logs.tsx           # Segnali e ordini
│       └── components/
│
├── tests/                         # Test pytest
├── deploy/
│   ├── setup.sh                   # Setup iniziale server
│   └── update.sh                  # Aggiornamento (git pull + build + restart)
├── docker-compose.yml
├── requirements.txt
└── .env.example
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
# Modifica .env con i tuoi valori
```

### 2. Backend

```bash
python -m venv venv
source venv/bin/activate   # Linux/macOS
# oppure: venv\Scripts\activate   # Windows

pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 3. Frontend

```bash
cd frontend
npm install
npm run dev
```

Il frontend sara disponibile su `http://localhost:5173`.

### 4. Test

```bash
pytest tests/ -v
```

## Configurazione .env

| Variabile | Default | Descrizione |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./trading_bot.db` | Database SQLAlchemy |
| `SYMBOLS` | `BTCUSDT,ETHUSDT` | Simboli monitorati (comma-separated) |
| `MAX_POSITION_SIZE_PCT` | `2.0` | Max % capitale per posizione |
| `DEFAULT_STOP_LOSS_PCT` | `3.0` | Stop loss default (%) |
| `DEFAULT_TAKE_PROFIT_PCT` | `5.0` | Take profit default (%) |
| `JWT_SECRET` | (da cambiare) | Chiave JWT per autenticazione |
| `ENCRYPTION_KEY` | (generare) | Chiave Fernet per cifrare API keys in DB |
| `TELEGRAM_BOT_TOKEN` | (vuoto) | Token bot Telegram da @BotFather |
| `LOG_LEVEL` | `INFO` | Livello logging |

**Nota**: Il `TELEGRAM_CHAT_ID` e per-utente, configurabile dalla pagina Settings della web app.

### Generazione chiavi

```bash
# JWT Secret
python -c "import secrets; print(secrets.token_hex(32))"

# Encryption Key (Fernet)
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## Configurazione Telegram

1. Crea un bot con [@BotFather](https://t.me/botfather) su Telegram
2. Copia il token e inseriscilo come `TELEGRAM_BOT_TOKEN` nel `.env` del server
3. Invia un messaggio al bot (es. `/start`)
4. Trova il tuo Chat ID: `https://api.telegram.org/bot<TOKEN>/getUpdates`
5. Nella web app, vai su **Settings** → sezione **Telegram Notifications**
6. Inserisci il Chat ID, abilita il toggle, premi **Salva**
7. Usa il pulsante **Invia Test** per verificare

## API Endpoints

### Autenticazione
| Metodo | Path | Descrizione |
|---|---|---|
| POST | `/api/login` | Login (restituisce cookie JWT) |
| POST | `/api/logout` | Logout |
| GET | `/api/me` | Info utente corrente |

### Trading
| Metodo | Path | Descrizione |
|---|---|---|
| GET | `/api/balance` | Saldo (paper o live) |
| GET | `/api/positions` | Posizioni aperte con PnL% |
| POST | `/api/positions/{id}/close` | Chiudi posizione manualmente |
| GET | `/api/orders` | Storico ordini |
| GET | `/api/trades` | Storico trade |
| GET | `/api/signals` | Ultimi segnali generati |

### Strategie e Rischio
| Metodo | Path | Descrizione |
|---|---|---|
| GET | `/api/strategies` | Lista strategie con parametri |
| PUT | `/api/strategies` | Modifica strategia (auto-save) |
| GET | `/api/risk` | Parametri rischio |
| PUT | `/api/risk` | Modifica rischio |

### Adaptive Layer
| Metodo | Path | Descrizione |
|---|---|---|
| GET | `/api/adaptive/status` | Stato completo (regime, profilo, performance, advisor) |
| GET | `/api/adaptive/profiles` | Lista profili + switching rules |
| PUT | `/api/adaptive/profiles/{name}` | Modifica profilo (admin) |
| POST | `/api/adaptive/profiles/{name}/apply` | Applica profilo manualmente (admin) |
| PUT | `/api/adaptive/switching-rules` | Modifica regole switching (admin) |
| POST | `/api/adaptive/telegram/test` | Invia test Telegram |

### Approvals
| Metodo | Path | Descrizione |
|---|---|---|
| GET | `/api/approvals` | Lista tutte le richieste |
| GET | `/api/approvals/pending` | Solo pending |
| POST | `/api/approvals/{id}/approve` | Approva (admin) |
| POST | `/api/approvals/{id}/reject` | Rifiuta (admin) |

### Configurazione
| Metodo | Path | Descrizione |
|---|---|---|
| GET | `/api/settings/keys` | Impostazioni utente (keys, mode, Telegram) |
| PUT | `/api/settings/keys` | Salva impostazioni |
| POST | `/api/symbols/add` | Aggiungi simbolo (admin) |
| POST | `/api/symbols/remove` | Rimuovi simbolo (admin) |

## Deploy su Server (systemd)

```bash
# Prima installazione
sudo ./deploy/setup.sh

# Aggiornamento
sudo ./deploy/update.sh

# Log
sudo tail -f /var/log/cryptobot.log
```

## Come aggiungere una nuova strategia

1. Crea un file in `app/strategies/` (es. `my_strategy.py`)
2. Estendi la classe `Strategy`:

```python
from app.strategies.base import Strategy, Signal, SignalType

class MyStrategy(Strategy):
    name = "my_strategy"
    enabled = True

    def generate_signals(self, df, symbol):
        # df contiene: open, high, low, close, volume (indice datetime)
        return []  # Lista di Signal

    def get_params(self):
        return {"my_param": self.my_param}

    def set_params(self, params):
        if "my_param" in params:
            self.my_param = params["my_param"]
```

3. Registra in `app/main.py`:

```python
engine.register_strategy(MyStrategy())
```

4. Aggiungi i parametri al profilo in `config/profiles.json`

La strategia apparira automaticamente nella dashboard.

## Note Tecniche

- Il trading engine esegue un ciclo ogni 15 minuti (candele 15m)
- Il meta-controller valuta regime/performance/profilo dopo ogni ciclo
- I prezzi real-time sono aggiornati via WebSocket (riconnessione automatica)
- Database SQLite con auto-migration per nuove colonne
- I log vengono salvati in `logs/trading_bot.log` (rotazione automatica)
- Le API keys sono cifrate con Fernet (AES-128-CBC) nel database
- L'autenticazione usa JWT in cookie httpOnly (protezione XSS)
- I profili e le switching rules sono in `config/profiles.json` (editable senza deploy)
- I parametri strategia sono persistiti in `strategy_params.json`
- Un LLM advisor analizza lo stato ma **non puo mai** modificare parametri o eseguire ordini
