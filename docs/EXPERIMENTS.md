# Esperimenti di validazione strategia

Ipotesi falsificabili da eseguire con il backtester (`app/backtesting/`) **prima** di toccare
il live. Regola d'oro: niente `live` finché un walk-forward non mostra **alpha netto positivo**
e drawdown accettabile, su più simboli.

## Risultati già ottenuti (baseline onesta)

`embient_enhanced`, BTCUSDT 15m, 90 giorni reali (mercato B&H ≈ −9%):

| Sizing | Gross | Net | Profit factor | Win rate | Alpha vs B&H |
|---|---:|---:|---:|---:|---:|
| 100% | −10,3% | −42,4% | 0,40 | 30,8% | −33,5% |
| 20% | −2,5% | −10,4% | 0,40 | 30,8% | negativo |

→ La strategia grezza è **negative-alpha**: perde anche al lordo e non batte il buy&hold.
Le fee amplificano, ma il problema è l'assenza di edge. (Il backtester usa i segnali della
strategia senza il layer guardrails, che filtrerebbe ulteriormente.)

## Esperimenti proposti

1. **Sensibilità ai costi** — `--fee {0, 0.075, 0.1}` `--slippage {0.02, 0.05}`. Quanto edge
   lordo serve per sopravvivere? Se gross ≤ 0, nessuna fee config salva la strategia.
2. **Frequenza / timeframe** — stessa strategia su `--interval 15m | 1h | 4h`. Ipotesi: meno
   trade su TF più alto → meno costi, segnali più puliti.
3. **Stop ATR vs fissi** — `--atr-stops` vs `--no-atr-stops --sl 3 --tp 5`. Su altcoin volatili
   gli stop fissi vengono presi dal rumore.
4. **Confronto multi-simbolo** — ripetere su ETH/BNB/SOL/XRP/LTC. La strategia ha edge su
   *qualche* simbolo o è negativa ovunque?
5. **Walk-forward** — `--walk-forward --train 2000 --test 500`. L'edge (se emerge) regge
   **out-of-sample** o è overfitting?
6. **Long-only realistico** — `allow_short=False` (default) conferma cosa fa davvero il live.
7. **Confronto strategie** — `sma_crossover`, `macd_crossover`, `rsi_reversal` con stessi costi:
   quale (se alcuna) ha profit factor netto > 1?

## Risultati 2026-06-10 — regime_breakout (Donchian 4h regime-gated)

Comando: `python -m app.backtesting.compare --strategies regime_breakout,embient_enhanced,macd_crossover
--interval 4h --days 730 --position-size 20 --atr-tp 0` (e variante `--walk-forward --train 1500 --test 480`).

**Single-pass 730 gg** (bull 2024-H2 + bear 2025/26), net% con size 20%, stop ATR 2x, NO take-profit:

| Simbolo | regime_breakout | embient_enhanced | macd_crossover | B&H |
|---|---:|---:|---:|---:|
| BTCUSDT | **+3,09 (PF 1,34, 29 tr)** | −5,72 | +1,87 | −12,5 |
| ETHUSDT | **+4,23 (PF 1,34, 24 tr)** | −4,67 | −14,16 | −55,9 |
| BNBUSDT | −7,25 | −5,85 | −8,65 | −9,9 |
| XRPUSDT | **+39,70 (PF 2,74, 28 tr)** | +34,39 | −3,91 | +122,0 |
| SOLUSDT | **+3,34 (PF 1,25, 27 tr)** | −18,40 | −9,00 | −60,5 |
| LTCUSDT | −3,14 | −13,09 | −13,64 | −47,2 |

**Walk-forward OOS** (finestra test ≈ quasi solo bear, B&H −37/−68%): regime_breakout
ETH **+7,77 (PF 2,03)**, peggior simbolo SOL −4,64; embient peggior caso −19,98. MaxDD
regime_breakout 4–9% vs 7–26% embient; fee dimezzate (≈20-30 trade/2 anni per simbolo).

**Lettura onesta**: long-only in un ciclo a 2 regimi, regime_breakout è netto-positivo su 4/6
simboli, in bear puro perde al massimo il 4,6% (vs −67% del mercato) — il contratto
"cattura il bull, preserva nel bear" è rispettato. Debolezza nota: simboli range-bound
(BNB). Non ancora pronta per live: serve la selezione relative-strength a livello
portafoglio e il supporto per-strategy timeframe nell'engine.

## Idee di ricerca per un edge reale (oltre il tuning)
- Conferma multi-timeframe come **filtro obbligatorio** (già implementata nel live engine).
- Mean-reversion solo in regime range *con bassa* volatilità (non il contrario).
- Trend-following solo con allineamento 15m + 1h + 4h.
- Validare con purged k-fold / meta-labeling prima di credere a qualsiasi backtest in-sample.
- Benchmark onesto: se non si batte il buy&hold netto, la strategia non va in produzione.
