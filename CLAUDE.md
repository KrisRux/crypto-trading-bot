# Claude Instructions

## Obiettivo del progetto
Crea e mantieni un bot/agente per trading di criptovalute su Binance, con web app responsive per desktop e mobile, modalita live/paper/dry-run, gestione autonoma di ordini e logiche di ingresso/uscita, adaptive layer a 3 livelli con notifiche Telegram e human-in-the-loop, e modulo per rilevare pattern di investimento.

## Riferimenti di progetto
- Repository principale (skills e agenti): https://github.com/SKE-Labs/agent-trading-skills
- Fai sempre riferimento a questo repository per skills, pattern e implementazioni condivise.

## Documentazione API da usare
Per tutte le integrazioni con Binance devi fare riferimento esclusivamente a questi link:

- Testnet Binance Spot: https://testnet.binance.vision/
- Documentazione Binance Spot API: https://binance-docs.github.io/apidocs/spot/en/

Quando implementi codice, assicurati di usare:
- endpoint testnet per la modalita di prova;
- endpoint spot ufficiali per la modalita live;
- WebSocket e REST coerenti con la documentazione ufficiale;
- gestione corretta di firma, timestamp, rate limit ed errori API.

## Regole importanti
- Non inserire mai chiavi API reali nel codice.
- Le chiavi devono essere lette da variabili d'ambiente o file `.env` o da db.
- Le chiavi API Binance sono cifrate con Fernet nel DB (per-utente).
- In modalita test/demo il sistema deve inviare ordini utilizzando il puntamento di test.
- In modalita live il sistema deve operare solo con permessi di trading, mai con permessi di withdrawal.
- Ogni funzione critica deve avere gestione errori e logging.
- Prima di eseguire azioni irreversibili, verifica sempre la modalita attiva.
- Nessun LLM puo modificare direttamente ordini o parametri live. L'LLM puo solo suggerire.
- I cambi automatici devono essere solo deterministici e confinati ai profili JSON.

## Architettura attuale

### Tre modalita di trading
- **dry_run**: il bot analizza e logga tutto, ma non apre posizioni e non chiama Binance
- **paper**: ordini reali su Binance Testnet (soldi virtuali) se chiavi testnet configurate, altrimenti simulazione locale
- **live**: ordini reali su Binance con le chiavi live dell'utente

### Trading Engine (`app/trading_engine/engine.py`)
- Ciclo ogni 15 minuti (candele 15m) per ridurre overtrading
- Cooldown per simbolo: 15 minuti tra un trade e l'altro
- Multi-utente: segnali condivisi, esecuzione per-utente
- Regime gate ADX-based prima dell'esecuzione:
  - TREND (ADX >= 25): embient priorita assoluta, rsi_reversal BLOCCATA
  - RANGE (ADX < 25): rsi_reversal OK, embient solo se score >= 80
- Signal arbitration per conflitti BUY vs SELL
- Dopo il ciclo, chiama `meta_controller.evaluate()` con i DataFrame

### Strategie (`app/strategies/`)
- **sma_crossover**: Golden/death cross con filtro ADX >= 25
- **rsi_reversal**: RSI oversold/overbought
- **macd_crossover**: MACD/signal crossover con filtro ADX >= 25
- **embient_enhanced**: Strategia principale, regime-aware (trend/range/neutral), scoring 0-100 con soglie per regime
- Indicatori disponibili in `indicators.py`: SMA, EMA, RSI, MACD, Bollinger Bands, ADX
- I parametri di tutte le strategie sono configurabili dalla UI e persistiti in `strategy_params.json`

### Adaptive Layer (`app/adaptive/`)
Layer esterno al motore, non tocca mai l'esecuzione degli ordini direttamente.

- **market_regime_service.py**: Classifica mercato per simbolo usando ADX, ATR%, BB width, volume ratio. Regimi: trend, range, volatile, defensive
- **performance_monitor.py**: Metriche rolling dal DB (pnl_1h/6h/24h, win_rate_last_10, drawdown_intraday, consecutive_losses, trades_per_hour)
- **profile_manager.py**: Carica profili da `config/profiles.json`, valuta regole di switching deterministiche con cooldown/hysteresis/limiti giornalieri
- **notification_service.py**: Telegram Bot API, bot_token server-wide, chat_id per-utente dal DB, dedup + rate limiting, alert una volta per episodio
- **approval_service.py**: Richieste approvazione in DB (pending/approved/rejected/expired), per cambi profilo aggressivi
- **llm_advisor.py**: Advisor READ-ONLY. Legge stato e produce spiegazioni + suggerimenti. MAI modifica parametri
- **meta_controller.py**: Orchestratore chiamato dopo ogni ciclo. Coordina: regime → performance → switch evaluation → notifications → advisor
- **guardrails.py**: Layer centralizzato pre-trade con kill switch, symbol cooldown, trade gate regime-aware, dynamic score, entry throttle, risk scaling, strategy circuit breaker. Punto unico: `can_open_new_trade()`. Config da `config/guardrails.json` (hot-reload via API).

### Guardrails (`config/guardrails.json`)
Layer di protezione centralizzato con 7 componenti:
- **KillSwitch**: pausa globale su consecutive_losses>=6 (90min), win_rate<=15% (90min), drawdown>=2% (120min), pnl_24h<=-6 (120min)
- **SymbolCooldown**: per-simbolo dopo 3 loss consecutive (60min) o 2 SL ravvicinati in 90min (90min)
- **TradeGate**: soglie regime-aware (ADX/volume/BB width) per global regime (defensive: ADX>=30, range: ADX>=32, trend: ADX>=25)
- **DynamicScoreFilter**: min score 80 (base), 88 (3+ loss), 92 (5+ loss), +5 in regime range/defensive, cap 95
- **EntryThrottle**: max 1 entry/simbolo/candle, max orarie per regime (defensive:2, range:3, trend:5)
- **RiskScaler**: multiplier size 0.75 (3+ loss), 0.50 (5+ loss o drawdown>=1.5%)
- **StrategyCircuitBreaker**: pausa strategia dopo 4 loss consecutive (2h), anche per coppia simbolo+strategia
API: `GET /adaptive/guardrails`, `POST /adaptive/guardrails/reload`

### Profili (`config/profiles.json`)
Tre profili con parametri rischio + soglie strategie + flag auto_apply/requires_approval:
- **defensive**: position 1%, soglie embient +5 vs normal, auto_apply
- **normal**: position 1.5%, soglie embient 80/75, auto_apply
- **aggressive_trend**: position 1.5%, TP 6%, soglie embient 75, requires_approval

Switching rules con cooldown (60 min), hysteresis (30 min), max 4 cambi/giorno.
Profili e switching rules editabili via API senza deploy.

### Modelli DB (`app/models/`)
- **User**: auth, chiavi API (Fernet-encrypted), trading_mode, paper_initial_capital, trading_hours, telegram_chat_id, telegram_enabled
- **Trade**: ciclo completo entry→exit, PnL, mode (paper/live), strategy
- **Order**: ordini singoli (BUY/SELL, MARKET/LIMIT)
- **PaperPortfolio** / **PaperPosition**: portafoglio virtuale per-utente
- **TradingSymbol**: simboli attivi (persistiti in DB)
- **ApprovalRequest**: richieste approvazione profilo (pending/approved/rejected/expired)

### Frontend (`frontend/`)
React + TypeScript + Tailwind CSS:
- **Dashboard.tsx**: Saldo, posizioni con PnL%, banner profilo/regime/metriche, chiusura manuale, advisor suggestion
- **Strategies.tsx**: Config parametri per strategia con auto-save (flash verde su salvataggio)
- **Settings.tsx**: Modalita trading (dry_run/paper/live), chiavi API, orario UTC, Telegram (chat_id + toggle + test)
- **Logs.tsx**: Segnali e ordini

### API (`app/api/routes.py`)
Endpoints principali:
- Auth: login, logout, me, CRUD utenti
- Trading: balance, positions, orders, trades, signals, close position
- Config: strategies CRUD, risk CRUD, symbols add/remove, settings/keys
- Adaptive: status, profiles CRUD, switching-rules, telegram test, guardrails status/reload
- Approvals: list, pending, approve, reject

## Configurazione

### .env (server-wide)
```
DATABASE_URL=sqlite:///./trading_bot.db
SYMBOLS=BTCUSDT,ETHUSDT,LTCUSDT,BNBUSDT,XRPUSDT,SOLUSDT
MAX_POSITION_SIZE_PCT=2.0
DEFAULT_STOP_LOSS_PCT=3.0
DEFAULT_TAKE_PROFIT_PCT=5.0
JWT_SECRET=<token casuale>
ENCRYPTION_KEY=<chiave Fernet>
TELEGRAM_BOT_TOKEN=<token da @BotFather>
LOG_LEVEL=INFO
```

### Per-utente (DB, configurabile da Settings UI)
- Chiavi API Binance (live + testnet)
- Trading mode (dry_run / paper / live)
- Orario trading (UTC)
- Telegram chat_id + enabled

### Profili (config/profiles.json, editabili via API)
- Parametri rischio per profilo
- Soglie strategie per profilo
- Switching rules (cooldown, hysteresis, max cambi/giorno)

## Funzionalita richieste (mantenute)

### Trading
- Il bot piazza ordini in modo autonomo
- Chiude posizioni quando raggiunge SL/TP configurabile
- Supporta: market order, limit order, stop loss, take profit
- Chiusura manuale posizioni dalla dashboard

### Pattern detection
- Indicatori tecnici: SMA, EMA, RSI, MACD, Bollinger Bands, ADX
- Segnali: BUY, SELL, HOLD
- Scoring 0-100 per embient_enhanced

### Web app
- Dashboard con saldo, posizioni, PnL%, banner profilo/regime
- Storico trade e ordini con filtri
- Configurazione strategie e rischio
- Selezione modalita dry_run / paper / live
- Notifiche Telegram configurabili per-utente
- Log tail da browser

### Modalita demo
- Simulazione ordini e posizioni su Binance Testnet o locale
- Saldo virtuale, storico trade simulati
- Reset portafoglio demo
- Export CSV

## Struttura progetto

```
app/
├── main.py
├── config.py
├── database.py
├── logging_config.py
├── strategy_store.py
├── api/
├── adaptive/
│   ├── market_regime_service.py
│   ├── performance_monitor.py
│   ├── profile_manager.py
│   ├── notification_service.py
│   ├── approval_service.py
│   ├── llm_advisor.py
│   ├── meta_controller.py
│   └── guardrails.py
├── binance_client/
├── strategies/
├── trading_engine/
├── paper_trading/
├── models/
└── embient_skills/
config/profiles.json
config/guardrails.json
frontend/
tests/
deploy/
```

## Git
- Dopo ogni modifica al codice, esegui sempre `git add`, `git commit` e `git push` per mantenere il repository aggiornato.
- Non lasciare modifiche locali senza push.

## Stile di lavoro
- Scrivi codice pulito, modulare e documentato.
- Se una richiesta e ambigua, fai una sola domanda chiarificatrice prima di procedere.
- Preferisci soluzioni semplici, robuste e facili da mantenere.
- Se proponi una strategia, separa sempre: acquisizione dati, calcolo indicatori, generazione segnali, esecuzione ordini.
- Se aggiungi colonne al DB, usa la funzione `_migrate_add_columns()` in `database.py` per retrocompatibilita con DB esistenti.
- I profili e le switching rules devono restare editabili senza deploy (via API o JSON).
- Il Telegram chat_id e per-utente (DB), il bot_token e server-wide (.env).

## Nota finale
Questo progetto deve essere pensato per uso sicuro e responsabile. La modalita testnet/paper trading deve essere sempre disponibile e ben evidenziata nell'interfaccia. Nessun LLM puo mai eseguire ordini o modificare parametri direttamente — solo suggerire.
