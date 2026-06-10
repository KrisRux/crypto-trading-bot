# Crypto Trading Bot

Bot di trading spot su Binance, **long-only**, con una sola strategia validata
(trend-following su breakout 4h), guardrail di rischio deterministici, web
dashboard e loop di monitoraggio KPI con notifiche Telegram.

> **DISCLAIMER**: software a scopo educativo e sperimentale. Il trading di
> criptovalute comporta rischi significativi. Non costituisce consiglio
> finanziario. Testa sempre in modalità paper prima di qualsiasi uso reale.

---

## Overview

**Cosa fa**: ogni 15 minuti analizza i simboli configurati; quando la strategia
`regime_breakout` rileva un breakout confermato in regime rialzista su candele
4h apre una posizione long con stop ATR e sizing risk-based; chiude su rottura
del canale, flip di regime o stop. In mercato ribassista **resta flat per
design**: su un conto spot la protezione del capitale È la posizione bear.

**Obiettivo della strategia**: catturare i trend rialzisti sostenuti (dove
sta l'edge misurato del time-series momentum) pagando il meno possibile in
costi — poche operazioni (~1–2/mese per simbolo), movimenti attesi molto
maggiori dello 0,24% di costo round-trip.

**Tre modalità**: `dry_run` (solo log), `paper` (portafoglio virtuale o
testnet Binance), `live` (chiavi reali, mai con permessi di withdrawal).

---

## Architettura

```
                 ┌──────────────────────────────────────────────┐
 Binance REST/WS │  TradingEngine (ciclo 15m)                   │
 ───────────────►│  • fetch candele chiuse (15m + 4h via        │
                 │    TimeframeFeed, cache per barra)           │
                 │  • regime_breakout → segnali BUY/SELL        │
                 │  • macro filter (flat-in-bear, MTF 1h)       │
                 │  • Guardrails.can_open_new_trade()           │
                 │  • esecuzione per-utente (dry/paper/live)    │
                 │  • exit: SL-first, profit lock, stale exit   │
                 └───────────────┬──────────────────────────────┘
                                 │ dopo ogni ciclo
                 ┌───────────────▼──────────────────────────────┐
                 │  MetaController (layer adattivo, MAI ordini) │
                 │  regime → performance → profili (risk-only)  │
                 │  → KPI/allarmi/trigger → Telegram → advisor  │
                 └───────────────┬──────────────────────────────┘
                                 │
        FastAPI /api ◄── DB SQLite (trade, ordini, utenti, portafogli)
            │
        React dashboard (saldo, posizioni, KPI, config, approvazioni)
```

| Modulo | Responsabilità |
|---|---|
| `app/trading_engine/engine.py` | Orchestrazione ciclo, esecuzione ordini, exit |
| `app/trading_engine/data_feed.py` | Candele chiuse per timeframe custom (cache per barra, dedup) |
| `app/strategies/regime_breakout.py` | L'unica strategia (segnali puri, nessun side effect) |
| `app/adaptive/guardrails.py` | Kill switch, cooldown, trade gate, throttle, risk scaler, circuit breaker |
| `app/adaptive/profile_manager.py` | Profili di **solo rischio** (normal/defensive/aggressive) con anti-thrashing |
| `app/adaptive/kpi_monitor.py` | KPI 30gg, allarmi, trigger di revisione, report giornaliero |
| `app/pnl.py` | Unica fonte di verità per fee/slippage/PnL netto |
| `app/backtesting/` | Harness event-driven no-lookahead + walk-forward + compare CLI |
| `app/api/routes.py` | REST API (auth JWT, trading, config, adaptive, KPI) |
| `frontend/` | Dashboard React + TypeScript + Tailwind |

**Flusso dati**: candele chiuse → segnali → filtri → guardrail → ordine →
`Trade` in DB (PnL **netto** via `compute_pnl`) → KPI/report.

---

## Strategia: `regime_breakout`

Trend-following long-only su candele **4h**, quattro regole trasparenti:

1. **Regime gate (direction-aware)** — entry solo se `close > EMA200` **e**
   EMA200 in salita. Niente acquisti in downtrend, mai.
2. **Entry** — breakout Donchian: il close supera il massimo delle 55 barre
   precedenti.
3. **Exit (edge-triggered)** — close sotto il minimo delle 20 barre precedenti
   **oppure** flip di regime; nessun take-profit vincolante (TP a 12×ATR, i
   winner corrono), stop hard a 2×ATR(4h) sotto l'entry.
4. **Filtro costi** — ATR% dell'entry in [0,5%, 6%]: il movimento atteso deve
   dominare i costi, ma niente entry in panico di volatilità.

**Validazione** (vedi `docs/EXPERIMENTS.md`): su 730 giorni (bull 2024 +
bear 2025/26) netta-positiva su 4/6 simboli (PF 1,25–2,74, maxDD 4–16%);
nel walk-forward out-of-sample quasi tutto-bear perde al massimo il 4,6%
contro un mercato a −67%. ~25 trade per simbolo in 2 anni.

**Risk management**:
- Sizing risk-based: la quantità deriva dalla distanza dello stop (rischio %
  fisso del capitale per trade), scalata dai moltiplicatori dei guardrail.
- Stop ATR del timeframe del segnale (il segnale porta il suo `atr_pct`).
- Guardrail pre-trade (unico punto: `can_open_new_trade()`): kill switch
  globale, cooldown per simbolo, gate di regime, score minimo dinamico,
  throttle per candela/ora, riduzione size dopo perdite, circuit breaker
  per strategia, performance gate.
- Profili (`config/profiles.json`): modulano **solo** position size e stop —
  mai la logica del segnale. Switch deterministici con cooldown, persistenza
  della recovery, dampening e guard anti flip-flop.

---

## Setup

### Requisiti
Python 3.12+, Node 20+, chiavi Binance (testnet e/o live).

### Backend
```bash
python -m venv venv
venv/Scripts/pip install -r requirements.txt        # Windows
# crea .env (vedi sotto)
venv/Scripts/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### Frontend
```bash
cd frontend && npm install && npm run dev            # dev su :5173
npm run build                                        # produzione (dist/)
```

### .env (server-wide)
```
DATABASE_URL=sqlite:///./trading_bot.db
SYMBOLS=BTCUSDT,ETHUSDT,BNBUSDT,XRPUSDT,SOLUSDT
MAX_POSITION_SIZE_PCT=2.0
DEFAULT_STOP_LOSS_PCT=3.0
DEFAULT_TAKE_PROFIT_PCT=5.0
JWT_SECRET=<token casuale>
ENCRYPTION_KEY=<chiave Fernet>
TELEGRAM_BOT_TOKEN=<token da @BotFather>
LOG_LEVEL=INFO
```
Le chiavi API Binance sono **per-utente**, inserite dalla UI (Settings) e
cifrate con Fernet nel DB. Mai chiavi nel codice o nel repo.

### API Binance
- Testnet Spot: https://testnet.binance.vision/
- Spot ufficiale: https://binance-docs.github.io/apidocs/spot/en/
- REST per candele/ordini con firma e sync del server time; WebSocket per i
  tick prezzo usati dal controllo intrabar di SL/TP.

### Deploy (Oracle Cloud / Ubuntu)
`deploy/setup.sh` (prima installazione), `deploy/update.sh` (pull + restart).
Nginx serve `frontend/dist` e proxa `/api/` su uvicorn (127.0.0.1:8000).

---

## Runtime: il flusso operativo

1. Ogni 15 minuti l'engine fetcha le candele 15m chiuse (per regime/exit) e —
   tramite `TimeframeFeed` — le 4h chiuse per la strategia (refetch solo a
   barra nuova, invocazione max 1 volta per barra).
2. I segnali passano: macro filter (blocco BUY se direzione bear o HTF down)
   → guardrail → cooldown/risk per-utente → ordine (market) con SL/TP.
3. Gli exit girano a ogni ciclo anche fuori orario di trading: SL-first al
   livello, profit lock/trailing, chiusura posizioni stantie.
4. Dopo il ciclo il MetaController aggiorna regime e performance, valuta i
   profili di rischio, calcola i KPI e invia report/allarmi Telegram
   (bot token server-wide, chat_id per-utente).
5. L'LLM advisor è **solo consultivo**: spiega lo stato e suggerisce; ogni
   modifica ai guardrail richiede approvazione umana esplicita (UI/Telegram).

---

## Metriche: cosa guardare e come leggerle

`GET /api/performance/kpi` (e report Telegram giornaliero, ore 6 UTC):

| KPI | Soglia sana | Significato |
|---|---|---|
| Expectancy netta/trade | > 0 | Se negativa su ≥20 trade, il sistema brucia capitale |
| Profit factor | > 1,1 | < 0,8 su 30+ trade ⇒ trigger di revisione automatico |
| Cost ratio | < 30% | Quota dei profitti lordi mangiata da fee+slippage |
| Turnover | < 1 trade/giorno | Più alto = sta tradando rumore |
| Max drawdown 30gg | < 2% capitale | Oltre ⇒ allarme CRITICAL |
| Win rate × payoff | coerenti | WR ~30% è OK **solo** con payoff > 2,5 (trend following) |
| Giorni senza trade | qualsiasi in bear | ⇒ trigger solo se il mercato è in uptrend |

Soglie modificabili a caldo in `config/kpi.json`. Tutto il PnL esposto è
**netto** (gross − fee − slippage, colonne dedicate sul `Trade`).

---

## Limiti: cosa il bot NON fa

- **Non shorta e non copre**: spot long-only. In bear market il risultato
  atteso è ~0 (flat), non un profitto.
- **Non predice**: segue trend confermati. Nei mercati laterali prolungati
  (es. BNB nel periodo di validazione) perde poco ma perde — è il costo noto
  dei falsi breakout.
- **Non si auto-modifica**: nessun LLM può toccare ordini o parametri; i
  cambi automatici sono solo i profili di rischio deterministici.
- **Edge non garantito**: la validazione copre un ciclo bull+bear (2024–26).
  Il gate per il live resta: walk-forward netto positivo su più simboli +
  30 giorni di paper coerenti col backtest.
- **Rischi residui**: gap oltre lo stop (lo stop è sul prezzo, il fill può
  essere peggiore), dipendenza da un solo exchange, un solo timeframe, una
  sola famiglia di segnali.

---

## Sviluppo

```bash
venv/Scripts/python -m pytest tests/ -q                  # 281 test
venv/Scripts/python -m app.backtesting.run --symbol BTCUSDT --interval 4h \
    --days 365 --strategy regime_breakout                # backtest singolo
venv/Scripts/python -m app.backtesting.compare --interval 4h --days 730 \
    --position-size 20 --atr-tp 0 --walk-forward         # confronto/OOS
```

Regola d'oro: **nessuna modifica alla strategia va in produzione senza
backtest walk-forward positivo e senza che la suite sia verde.**
