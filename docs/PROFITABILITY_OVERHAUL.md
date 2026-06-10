# Profitability Overhaul — 2026-06

Revisione critica e rifacimento orientato alla redditività reale del bot.
Branch: `feat/profitability-overhaul`.

## TL;DR (diagnosi dai dati reali)

Analisi sui dati live (249 trade paper, ~65 giorni):

| Metrica | Mostrato (lordo) | Reale (netto fee+slippage) |
|---|---:|---:|
| PnL | +35,73 USDT (+0,36%) | **−31,66 USDT (−0,32%)** |
| Profit factor | 1,32 | 0,80 |
| Costi totali | — | 67,39 USDT = **1,9× il profitto lordo** |

- Il PnL/equity mostrati erano **al lordo delle commissioni**: il bot sembrava in pari ma perdeva.
- Movimento medio per trade **0,16% < 0,24% di costo round-trip** → matematicamente perdente.
- I soli **long** (unica cosa eseguibile su spot live) erano **−46,5 netto**; l'unico contributo
  positivo veniva da **short simulati impossibili in live**.

Backtest indipendente (nuovo harness) su `embient_enhanced`, BTCUSDT 15m, 90 giorni reali,
sizing 20%: **net −10,4%**, profit factor **0,40**, alpha vs buy&hold **negativo**.

> Conclusione onesta: l'overhaul rende il bot **corretto, meno costoso e validabile**, ma la
> strategia `embient_enhanced` **non ha edge** sul periodo testato. Ora è *misurabile* e
> iterabile con il backtester.

## Cosa è cambiato (per area)

### Contabilità — una sola fonte di verità (`app/pnl.py`)
- Nuovo modulo `app.pnl.compute_pnl(side, entry, exit, qty, fee_pct, slippage_pct)` →
  `gross_pnl, fee, slippage, cost, net_pnl, *_pct`. Usato da engine, paper portfolio,
  reporting API, backtester e migrazione DB. Elimina le **4 formule fee divergenti**.
- `Trade` ha nuove colonne `gross_pnl, fee, slippage, exit_reason`; **`pnl` è ora il NETTO**.
- Migrazione idempotente `_backfill_trade_accounting()` riscrive lo storico a netto
  (lordo preservato in `gross_pnl`, reversibile). Gira al riavvio/deploy.
- Reporting API (`/balance`, `/performance/*`) legge le colonne nette → **niente più doppio
  conteggio fee**; win/loss giudicati sul **netto**.

### Correttezza esecuzione / segnali (`engine.py`, `risk_manager.py`, `portfolio.py`)
- **No-lookahead**: la candela in formazione viene scartata; segnali e regime usano solo
  candele **chiuse** (stop al repainting). La candela in formazione + tick WS restano solo
  per il rilevamento intrabar di TP/SL (uscite).
- **Exit SL-first al livello**: se una candela tocca sia SL che TP si contabilizza la
  **perdita** (era bias ottimistico); PnL al livello SL/TP, non a un prezzo recuperato.
- **Stop ATR** (volatility-aware) + **sizing risk-based** (rischio %/trade sulla distanza SL,
  cap sul notional) — vedi `_entry_plan`.
- **Slippage guard** + **VWAP** sui fill (ordini market); **sync server-time** (recvWindow).
- `disable_paper_shorts=True`: niente short sintetici (coerenza con spot live).
- Fix: `add_symbol` non usa più `get_event_loop()` (niente crash in contesto sync);
  `reset()` del paper cancella anche gli Order (bug ordini orfani: 1040 ordini / 248 trade).

### Adattabilità al mercato (bear)
- **Regime direzionale** (`market_regime_service`): nuovo campo `direction` (up/down/flat) +
  `is_bearish/is_bullish` + `global_direction`.
- **Filtro macro multi-timeframe** + **flat-in-bear**: niente BUY contro il trend del
  timeframe superiore o quando il regime del simbolo è ribassista.
  Eccezione controllata: un setup locale `trend/up` molto forte può passare contro
  un HTF ancora laggante, ma con size ridotta.

### Guardrails più selettivi (`config/guardrails.json`)
Allineati ai default documentati (erano stati tarati troppo permissivi):
`dynamic_score` 80/88/92, `range.min_adx` 28, `bad_regimes` include `range`,
`block_entry_on_symbol_regime` include `range`. Meno trade, di qualità più alta.

### Esecuzione / API Binance (`binance_client/`, `order_manager.py`)
- REST hardening: `recvWindow`, sync server-time, cache `exchangeInfo`, retry idempotente su
  DELETE, lettura `X-MBX-USED-WEIGHT`, errori Binance più chiari.
- `order_manager`: VWAP sui fill, `place_maker_order` (LIMIT post-only GTX), `smart_entry`.
  > Nota: gli ordini maker non sono ancora cablati nel flusso auto-trade (richiedono gestione
  > dei limit non riempiti); `place_market_order` ora applica VWAP + slippage guard.

## Nuove impostazioni `.env` (tutte con default ragionevoli)
```
TAKER_FEE_PCT=0.1            MAKER_FEE_PCT=0.1
PREFER_MAKER_ORDERS=false    MAKER_LIMIT_OFFSET_PCT=0.05   MAKER_FILL_TIMEOUT_S=20
MAX_SLIPPAGE_PCT=0.3         BINANCE_RECV_WINDOW=5000
USE_ATR_STOPS=true           ATR_SL_MULT=2.0   ATR_TP_MULT=3.0
RISK_BASED_SIZING=true       RISK_PCT_PER_TRADE=0.5
MTF_FILTER_ENABLED=true      MTF_INTERVAL=1h   MTF_EMA_PERIOD=200
MTF_COUNTERTREND_OVERRIDE_ENABLED=true
MTF_COUNTERTREND_MIN_SCORE=90
MTF_COUNTERTREND_MIN_ADX=32
MTF_COUNTERTREND_MIN_VOLUME_RATIO=1.3
MTF_COUNTERTREND_RISK_MULTIPLIER=0.5
FLAT_IN_BEAR=true            DISABLE_PAPER_SHORTS=true
```

## Backtester (`app/backtesting/`)
Event-driven, no-lookahead, costi via `app.pnl`, SL-first, benchmark buy&hold, walk-forward.
```
# Backtest singolo (klines pubbliche Binance, niente API key):
venv/Scripts/python.exe -m app.backtesting.run --symbol BTCUSDT --interval 15m --days 90 \
    --strategy embient_enhanced --position-size 20
# Walk-forward out-of-sample:
... --walk-forward --train 2000 --test 500
# Offline da CSV: ... --csv dati.csv
```
Flag: `--allow-short --fee --slippage --sl --tp --[no-]atr-stops --capital --json --testnet`.

## Prossimi passi consigliati (la strategia, non l'infrastruttura)
Vedi [EXPERIMENTS.md](EXPERIMENTS.md). In sintesi: la priorità ora è **trovare un edge reale**
(la strategia attuale non ce l'ha sul periodo testato). Esperimenti da far girare nel
backtester: sensibilità ai costi, filtro multi-timeframe on/off, stop ATR vs fissi,
riduzione frequenza/timeframe superiore, confronto vs buy&hold su più simboli.
Non passare a `live` finché un backtest **walk-forward** non mostra alpha netto positivo.
