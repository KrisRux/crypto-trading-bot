/**
 * Internationalization — Italian / English translations.
 */

export type Lang = 'it' | 'en'

const translations = {
  // -- Navbar --
  nav_dashboard: { it: 'Dashboard', en: 'Dashboard' },
  nav_strategies: { it: 'Strategie', en: 'Strategies' },
  nav_logs: { it: 'Log', en: 'Logs' },

  // -- Mode --
  mode_live: { it: 'LIVE', en: 'LIVE' },
  mode_paper: { it: 'SIMULATO', en: 'PAPER' },
  mode_banner_live: {
    it: 'TRADING LIVE — Gli ordini reali vengono inviati a Binance',
    en: 'LIVE TRADING — Real orders are being placed on Binance',
  },
  mode_banner_paper: {
    it: 'TRADING SIMULATO — Nessun denaro reale coinvolto, portafoglio virtuale',
    en: 'PAPER TRADING — No real money involved, using simulated portfolio',
  },
  mode_switch_failed: {
    it: 'Cambio modalità fallito',
    en: 'Failed to switch mode',
  },

  // -- Dashboard --
  cash_balance: { it: 'Saldo Disponibile', en: 'Cash Balance' },
  total_equity: { it: 'Patrimonio Totale', en: 'Total Equity' },
  total_pnl: { it: 'PnL Totale', en: 'Total PnL' },
  win_rate: { it: 'Tasso Vittoria', en: 'Win Rate' },
  win_short: { it: 'V', en: 'W' },
  loss_short: { it: 'P', en: 'L' },
  engine: { it: 'Motore', en: 'Engine' },
  running: { it: 'Attivo', en: 'Running' },
  stopped: { it: 'Fermo', en: 'Stopped' },
  symbol: { it: 'Simbolo', en: 'Symbol' },
  last_price: { it: 'Ultimo Prezzo', en: 'Last Price' },
  reset_portfolio: { it: 'Resetta Portafoglio', en: 'Reset Portfolio' },
  reset_confirm: {
    it: 'Resettare il portafoglio simulato al capitale iniziale?',
    en: 'Reset paper portfolio to initial capital?',
  },
  reset_failed: { it: 'Reset fallito', en: 'Reset failed' },
  export_csv: { it: 'Esporta CSV', en: 'Export CSV' },
  export_failed: { it: 'Esportazione fallita', en: 'Export failed' },

  // -- Positions --
  open_positions: { it: 'Posizioni Aperte', en: 'Open Positions' },
  no_positions: { it: 'Nessuna posizione aperta.', en: 'No open positions.' },
  qty: { it: 'Qtà', en: 'Qty' },
  entry: { it: 'Ingresso', en: 'Entry' },
  current: { it: 'Attuale', en: 'Current' },
  pnl: { it: 'PnL', en: 'PnL' },

  // -- Trades --
  recent_trades: { it: 'Trade Recenti', en: 'Recent Trades' },
  no_trades: { it: 'Nessun trade ancora.', en: 'No trades yet.' },
  side: { it: 'Lato', en: 'Side' },
  exit: { it: 'Uscita', en: 'Exit' },
  status: { it: 'Stato', en: 'Status' },
  strategy: { it: 'Strategia', en: 'Strategy' },

  // -- Strategies page --
  trading_strategies: { it: 'Strategie di Trading', en: 'Trading Strategies' },
  risk_management: { it: 'Gestione del Rischio', en: 'Risk Management' },
  max_position_size: { it: 'Dimensione Max Posizione (%)', en: 'Max Position Size (%)' },
  default_stop_loss: { it: 'Stop Loss Predefinito (%)', en: 'Default Stop Loss (%)' },
  default_take_profit: { it: 'Take Profit Predefinito (%)', en: 'Default Take Profit (%)' },
  save_risk: { it: 'Salva Parametri Rischio', en: 'Save Risk Parameters' },
  update_failed: { it: 'Aggiornamento fallito', en: 'Update failed' },

  // -- Logs page --
  recent_signals: { it: 'Segnali Recenti', en: 'Recent Signals' },
  no_signals: {
    it: 'Nessun segnale generato. Il motore analizza le strategie ogni 60 secondi.',
    en: 'No signals generated yet. The engine runs strategy checks every 60 seconds.',
  },
  order_history: { it: 'Storico Ordini', en: 'Order History' },
  no_orders: { it: 'Nessun ordine eseguito.', en: 'No orders executed yet.' },
  time: { it: 'Ora', en: 'Time' },
  type: { it: 'Tipo', en: 'Type' },
  price: { it: 'Prezzo', en: 'Price' },
  reason: { it: 'Motivo', en: 'Reason' },
  order_type: { it: 'Tipo Ordine', en: 'Order Type' },
  filled: { it: 'Eseguito', en: 'Filled' },
  error: { it: 'Errore', en: 'Error' },

  // -- Manual page --
  nav_manual: { it: 'Manuale', en: 'Manual' },

  // -- Skills page --
  nav_skills: { it: 'Skills', en: 'Skills' },
} as const

export type TranslationKey = keyof typeof translations

export function t(key: TranslationKey, lang: Lang): string {
  return translations[key]?.[lang] ?? key
}

export default translations
