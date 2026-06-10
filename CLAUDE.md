# Claude Instructions

## Obiettivo del progetto
Bot di trading spot su Binance, long-only, con UNA strategia validata
(`regime_breakout`, trend-following su breakout 4h), guardrail deterministici,
web app responsive, modalità live/paper/dry-run, profili di rischio adattivi
con notifiche Telegram e human-in-the-loop, e loop permanente di monitoraggio
KPI. Filosofia: **semplicità misurabile** — ogni componente deve dimostrare
con i numeri che genera rendimento corretto per il rischio, o viene rimosso.

## Documentazione API da usare
Per tutte le integrazioni con Binance fare riferimento esclusivamente a:
- Testnet Binance Spot: https://testnet.binance.vision/
- Documentazione Binance Spot API: https://binance-docs.github.io/apidocs/spot/en/

Endpoint testnet in modalità prova, spot ufficiali in live; WebSocket e REST
coerenti con la documentazione; gestione corretta di firma, timestamp, rate
limit ed errori API.

## Regole importanti
- Non inserire mai chiavi API reali nel codice. Chiavi da .env o DB
  (per-utente, cifrate Fernet).
- In live solo permessi di trading, MAI withdrawal.
- Ogni funzione critica deve avere gestione errori e logging.
- Prima di azioni irreversibili verificare la modalità attiva.
- **Nessun LLM può modificare ordini o parametri live. Solo suggerire.**
  I cambi automatici sono solo i profili di rischio deterministici (JSON).
- **Nessuna modifica alla strategia va in produzione senza walk-forward netto
  positivo** (`app/backtesting/compare.py`) e suite test verde.
- Il sistema è SPOT LONG-ONLY ovunque: niente short, nemmeno simulati in
  paper (gli short sintetici falsificano il confronto paper vs live).

## Architettura attuale

### Tre modalità di trading
- **dry_run**: analizza e logga, nessun ordine
- **paper**: ordini su Binance Testnet (se chiavi testnet) o simulazione locale
- **live**: ordini reali con chiavi live per-utente

### Trading Engine (`app/trading_engine/`)
- `engine.py`: ciclo ogni 15 minuti su candele CHIUSE (no lookahead);
  esecuzione per-utente; cooldown 15 min per (utente, simbolo); exit SL-first
  al livello + profit lock/trailing + stale-position; macro filter che blocca
  i BUY quando la direzione del simbolo è bear (flat_in_bear) o l'HTF 1h è
  down; conflitti BUY/SELL risolti con la regola "un exit batte sempre un
  nuovo entry". Dopo il ciclo chiama `meta_controller.evaluate()`.
- `data_feed.py` (`TimeframeFeed`): frame di candele chiuse per strategie con
  `interval` custom (es. 4h) — cache invalidata solo alla chiusura di una
  nuova barra, strategia invocata max 1 volta per barra chiusa.
- `_entry_plan()`: stop ATR + sizing risk-based; il segnale può fornire
  `atr_pct` (ATR del SUO timeframe) e `tp_atr_mult` nei metadata.

### Strategia (`app/strategies/`)
UNA strategia registrata: **regime_breakout** (`regime_breakout.py`)
- `interval="4h"`, `min_history_bars` ~220
- Entry: breakout Donchian 55-bar in regime bull (close > EMA200 e EMA200 in
  salita) con ATR% in [0.5, 6] (filtro costi)
- Exit edge-triggered: rottura 20-bar low O flip di regime; sell_score=0
  (il SELL è solo chiusura, mai conviction short); TP 12×ATR non vincolante
- Validata su 730gg + walk-forward OOS: vedi docs/EXPERIMENTS.md (2026-06-10)
- Parametri configurabili da UI, persistiti via `strategy_store.py`
- `indicators.py`: SMA, EMA, RSI, MACD, Bollinger, ADX, ATR (libreria pura;
  il market_regime_service usa ADX/ATR/BB)
- Per aggiungere una strategia: implementare `Strategy` (base.py), dichiarare
  `interval`/`min_history_bars` se serve un TF custom, registrarla in main.py
  e nel registry del backtester, e validarla PRIMA con compare.py

### Adaptive Layer (`app/adaptive/`)
Layer esterno al motore, non tocca mai l'esecuzione degli ordini.
- **market_regime_service.py**: regime per simbolo (trend/range/volatile/
  defensive via ADX, ATR%, BB width, volume) + `direction` (up/down/flat) e
  `is_bearish()` usato dal macro filter
- **performance_monitor.py**: metriche rolling dal DB (pnl 1h/6h/24h, WR,
  drawdown intraday, consecutive losses)
- **profile_manager.py**: profili di SOLO rischio da `config/profiles.json`
  (normal 1.5%, defensive 0.75%, aggressive_trend 2% con approvazione).
  Anti-thrashing: recovery con campione minimo + persistenza 120 min,
  dampening 30 min sulla regola regime, flip-flop guard 240 min asimmetrico
  (il tightening non è mai ritardato). I profili NON toccano i parametri
  della strategia.
- **guardrails.py**: gate pre-trade unico `can_open_new_trade()` — kill
  switch, symbol cooldown, trade gate regime-aware, dynamic score, entry
  throttle, risk scaler, strategy circuit breaker, performance gate.
  Config `config/guardrails.json` (hot-reload via API).
- **kpi_monitor.py**: loop permanente — KPI 30gg (expectancy, PF, cost_ratio,
  turnover, DD, Sharpe/trade, attribution per-strategia), allarmi da
  `config/kpi.json`, trigger di revisione deterministici (pf_collapse,
  net_loss, strategy_negative, asleep_in_bull) che notificano senza MAI
  cambiare parametri. Report Telegram giornaliero; API `GET /performance/kpi`.
- **notification_service.py**: Telegram (bot token server-wide, chat_id
  per-utente, dedup + rate limiting)
- **approval_service.py**: richieste approvazione in DB (human-in-the-loop)
- **llm_advisor.py**: READ-ONLY, spiega e suggerisce; i suggerimenti di
  tuning guardrail si applicano SOLO con approvazione umana via API
- **meta_controller.py**: orchestratore post-ciclo (regime → performance →
  profili → KPI/report → advisor)

### Contabilità (`app/pnl.py`)
`Trade.pnl` è il NETTO (dopo fee+slippage); `gross_pnl/fee/slippage` colonne
dedicate. Tutto il PnL passa da `compute_pnl` — NON duplicare formule altrove.

### Backtesting (`app/backtesting/`)
Event-driven, no-lookahead (signal-on-close/fill-next-open), SL-first
intrabar, costi reali via app/pnl, TP disattivabile (`--atr-tp 0`),
walk-forward, orchestratore comparativo `compare.py` (strategie × simboli,
iniezione parametri live). SEMPRE validare qui prima di toccare il live.

### Modelli DB (`app/models/`)
User (auth, chiavi cifrate, trading_mode, telegram), Trade (entry→exit, PnL
netto, mode, strategy), Order, PaperPortfolio/PaperPosition (portfolio.py),
TradingSymbol, ApprovalRequest.
Nuove colonne DB: usare `_migrate_add_columns()` in `database.py`.

### Frontend (`frontend/`)
React + TypeScript + Tailwind: Dashboard (saldo, posizioni, banner profilo/
regime), Strategies (parametri con auto-save), Opportunities, Profiles,
Guardrails config, Approvals, Diagnostics, Settings (modalità, chiavi,
Telegram), Logs, Users, Assets, Manual.

### API (`app/api/routes.py`)
Auth JWT; trading (balance, positions, orders, trades, signals, close);
config (strategies, risk, symbols, settings/keys); adaptive (status, profiles,
switching-rules, guardrails status/reload/config, tuning con approvazione,
telegram test); performance (breakdown, mark-to-market, **kpi**); approvals;
diagnostics; logs/tail; paper reset/export.

## Configurazione
- `.env`: DATABASE_URL, SYMBOLS, MAX_POSITION_SIZE_PCT, DEFAULT_SL/TP,
  JWT_SECRET, ENCRYPTION_KEY, TELEGRAM_BOT_TOKEN, LOG_LEVEL (+ toggle in
  config.py: fee, ATR, risk sizing, MTF, flat_in_bear — default = raccomandato)
- Per-utente (DB, da Settings UI): chiavi Binance, trading mode, orario UTC,
  Telegram chat_id
- `config/profiles.json`: profili rischio + switching rules (editabili via API)
- `config/guardrails.json`: guardrail (hot-reload)
- `config/kpi.json`: soglie KPI/allarmi + ora report

## Git
- Dopo ogni modifica: `git add`, `git commit`, `git push`. Niente modifiche
  locali senza push.
- Il server di produzione (Oracle Cloud) si aggiorna con `deploy/update.sh`.

## Stile di lavoro
- Codice pulito, modulare, documentato; soluzioni semplici e robuste.
- Separare sempre: acquisizione dati, indicatori, segnali, esecuzione.
- Se una richiesta è ambigua, una sola domanda chiarificatrice.
- Componenti nuovi = giustificazione misurabile o non entrano. La complessità
  rimossa nel cleanup 2026-06 (4 strategie legacy, skills library, paper
  short, regime gate per-strategia) NON va reintrodotta senza validazione.

## Storia essenziale (per contesto, vedi git per i dettagli)
- 2026-06: profitability overhaul (docs/PROFITABILITY_OVERHAUL.md) — PnL
  netto centralizzato, no-lookahead, backtester; diagnosi: long-only senza
  edge in bear, costi > movimento medio catturato.
- 2026-06-10: cicli di miglioramento — anti-thrashing profili, strategia
  regime_breakout validata (docs/EXPERIMENTS.md), per-strategy timeframe,
  KPI loop, cleanup aggressivo (−7.400 righe). La strategia legacy
  embient_enhanced era negative-alpha misurata: non recuperarla.

## Nota finale
Uso sicuro e responsabile: testnet/paper sempre disponibile ed evidenziata.
Nessun LLM esegue ordini o modifica parametri direttamente — solo suggerimenti
con approvazione umana.
