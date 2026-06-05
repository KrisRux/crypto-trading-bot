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

## Idee di ricerca per un edge reale (oltre il tuning)
- Conferma multi-timeframe come **filtro obbligatorio** (già implementata nel live engine).
- Mean-reversion solo in regime range *con bassa* volatilità (non il contrario).
- Trend-following solo con allineamento 15m + 1h + 4h.
- Validare con purged k-fold / meta-labeling prima di credere a qualsiasi backtest in-sample.
- Benchmark onesto: se non si batte il buy&hold netto, la strategia non va in produzione.
