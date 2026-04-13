import { useCallback, useState, useMemo } from 'react'
import { api, Balance, Position, TradeItem, EngineStatus, AdaptiveStatus, NewsSentiment } from '../api'
import { usePolling } from '../hooks/usePolling'
import { useLang } from '../hooks/useLang'
import StatCard from '../components/StatCard'

export default function Dashboard() {
  const { lang, t } = useLang()
  const l = (it: string, en: string) => (lang === 'it' ? it : en)

  const fetchBalance = useCallback(() => api.getBalance(), [])
  const fetchPositions = useCallback(() => api.getPositions(), [])
  const fetchTrades = useCallback(() => api.getTrades(), [])
  const fetchEngine = useCallback(() => api.getEngineStatus(), [])
  const fetchAdaptive = useCallback(() => api.getAdaptiveStatus(), [])
  const fetchSentiment = useCallback(() => api.getNewsSentiment(), [])

  const [balance, , , refetchBalance] = usePolling<Balance>(fetchBalance, 5000)
  const [positions, , , refetchPositions] = usePolling<Position[]>(fetchPositions, 5000)
  const [trades, , , refetchTrades] = usePolling<TradeItem[]>(fetchTrades, 10000)
  const [engine] = usePolling<EngineStatus>(fetchEngine, 5000)
  const [adaptive] = usePolling<AdaptiveStatus>(fetchAdaptive, 15000)
  const [sentiment] = usePolling<NewsSentiment>(fetchSentiment, 60000)

  const dataMode = balance?.mode || 'paper'

  // -- Trade filters --
  const [filterStatus, setFilterStatus] = useState<string>('ALL')
  const [filterSide, setFilterSide] = useState<string>('ALL')
  const [filterSymbol, setFilterSymbol] = useState<string>('ALL')

  // Unique symbols from trades
  const tradeSymbols = useMemo(() => {
    if (!trades) return []
    return [...new Set(trades.map((t) => t.symbol))].sort()
  }, [trades])

  // Filtered trades
  const filteredTrades = useMemo(() => {
    if (!trades) return []
    return trades.filter((tr) => {
      if (filterStatus !== 'ALL' && tr.status !== filterStatus) return false
      if (filterSide !== 'ALL' && tr.side !== filterSide) return false
      if (filterSymbol !== 'ALL' && tr.symbol !== filterSymbol) return false
      return true
    })
  }, [trades, filterStatus, filterSide, filterSymbol])

  // -- Position filters --
  const [posFilterSymbol, setPosFilterSymbol] = useState<string>('ALL')

  const posSymbols = useMemo(() => {
    if (!positions) return []
    return [...new Set(positions.map((p) => p.symbol))].sort()
  }, [positions])

  const filteredPositions = useMemo(() => {
    if (!positions) return []
    if (posFilterSymbol === 'ALL') return positions
    return positions.filter((p) => p.symbol === posFilterSymbol)
  }, [positions, posFilterSymbol])

  const [closingId, setClosingId] = useState<number | null>(null)

  const handleClosePosition = async (p: Position) => {
    const confirmed = confirm(
      l(
        `Chiudere la posizione ${p.symbol} (${p.quantity.toFixed(6)}) al prezzo corrente di mercato?`,
        `Close position ${p.symbol} (${p.quantity.toFixed(6)}) at current market price?`
      )
    )
    if (!confirmed) return
    setClosingId(p.id)
    try {
      await api.closePosition(p.id)
      refetchPositions()
      refetchTrades()
      refetchBalance()
    } catch (e) {
      alert(`${l('Chiusura fallita', 'Close failed')}: ${e}`)
    } finally {
      setClosingId(null)
    }
  }

  const handleReset = async () => {
    if (dataMode !== 'paper') return
    if (!confirm(t('reset_confirm'))) return
    try {
      await api.resetPaperPortfolio()
      refetchBalance()
      refetchPositions()
      refetchTrades()
    } catch (e) {
      alert(`${t('reset_failed')}: ${e}`)
    }
  }

  const handleExport = async () => {
    try {
      const csv = await api.exportTrades()
      const blob = new Blob([csv], { type: 'text/csv' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = 'paper_trades.csv'
      a.click()
      URL.revokeObjectURL(url)
    } catch (e) {
      alert(`${t('export_failed')}: ${e}`)
    }
  }

  const filterBtnClass = (active: boolean) =>
    `px-2.5 py-1 rounded text-xs font-medium transition-colors ${
      active ? 'bg-blue-600 text-white' : 'bg-gray-800 text-gray-400 hover:bg-gray-700'
    }`

  return (
    <div className="space-y-6">
      {/* Data mode indicator */}
      <div className="flex items-center gap-2">
        <span className={`px-2.5 py-1 rounded-full text-xs font-bold uppercase tracking-wider ${
          dataMode === 'live'
            ? 'bg-red-900/50 text-red-300 border border-red-800/50'
            : 'bg-emerald-900/50 text-emerald-300 border border-emerald-800/50'
        }`}>
          {dataMode === 'live' ? 'Live' : 'Paper'}
        </span>
        <span className="text-xs text-gray-500">
          {dataMode === 'live'
            ? l('Dati reali dal tuo conto Binance', 'Real data from your Binance account')
            : l('Dati da Binance Testnet', 'Data from Binance Testnet')
          }
        </span>
      </div>

      {/* Adaptive Layer Status */}
      {adaptive && (
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
          <div className="flex flex-wrap items-center gap-3">
            {/* Profile */}
            <div className="flex items-center gap-2">
              <span className="text-xs text-gray-500 uppercase tracking-wider">{l('Profilo', 'Profile')}</span>
              <span className={`px-2.5 py-1 rounded-full text-xs font-bold uppercase tracking-wider ${
                adaptive.active_profile === 'defensive'
                  ? 'bg-yellow-900/50 text-yellow-300 border border-yellow-800/50'
                  : adaptive.active_profile === 'aggressive_trend'
                    ? 'bg-red-900/50 text-red-300 border border-red-800/50'
                    : 'bg-blue-900/50 text-blue-300 border border-blue-800/50'
              }`}>
                {adaptive.active_profile}
              </span>
            </div>
            {/* Regime */}
            <div className="flex items-center gap-2">
              <span className="text-xs text-gray-500 uppercase tracking-wider">{l('Regime', 'Regime')}</span>
              <span className={`px-2.5 py-1 rounded-full text-xs font-bold uppercase tracking-wider ${
                adaptive.regime.global_regime === 'trend'
                  ? 'bg-emerald-900/50 text-emerald-300 border border-emerald-800/50'
                  : adaptive.regime.global_regime === 'volatile' || adaptive.regime.global_regime === 'defensive'
                    ? 'bg-red-900/50 text-red-300 border border-red-800/50'
                    : 'bg-gray-800 text-gray-300 border border-gray-700'
              }`}>
                {adaptive.regime.global_regime}
              </span>
            </div>
            {/* Key metrics */}
            <div className="flex items-center gap-4 ml-auto text-xs">
              <span className={adaptive.performance.pnl_6h >= 0 ? 'text-emerald-400' : 'text-red-400'}>
                PnL 6h: {adaptive.performance.pnl_6h >= 0 ? '+' : ''}{adaptive.performance.pnl_6h?.toFixed(2)}
              </span>
              <span className="text-gray-400">
                WR: {adaptive.performance.win_rate_last_10?.toFixed(0)}%
              </span>
              <span className={adaptive.performance.drawdown_intraday > 1.5 ? 'text-red-400' : 'text-gray-400'}>
                DD: {adaptive.performance.drawdown_intraday?.toFixed(2)}%
              </span>
              {adaptive.performance.consecutive_losses >= 3 && (
                <span className="text-red-400 font-medium">
                  {adaptive.performance.consecutive_losses} {l('perdite consecutive', 'consec. losses')}
                </span>
              )}
            </div>
          </div>
          {/* Advisor suggestion */}
          {adaptive.advisor?.suggested_profile && adaptive.advisor.suggested_profile !== adaptive.active_profile && (
            <div className="mt-2 bg-blue-900/20 border border-blue-800/40 rounded px-3 py-2 text-xs text-blue-300">
              <span className="font-medium">{l('Advisor suggerisce', 'Advisor suggests')}:</span>{' '}
              {adaptive.active_profile} → {adaptive.advisor.suggested_profile}
              <span className="text-blue-400/70 ml-1">
                ({(adaptive.advisor.confidence * 100).toFixed(0)}% {l('confidenza', 'confidence')})
              </span>
            </div>
          )}
        </div>
      )}

      {/* Market Sentiment */}
      {sentiment?.available && (
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <h3 className="text-sm font-semibold text-gray-400">Market Sentiment</h3>
              <span className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase ${
                sentiment.score > 0.1 ? 'bg-emerald-900/60 text-emerald-300' :
                sentiment.score < -0.1 ? 'bg-red-900/60 text-red-300' :
                'bg-gray-800 text-gray-400'
              }`}>
                {sentiment.label}
              </span>
            </div>
            <span className="text-[10px] text-gray-600">{sentiment.headline_count} {l('notizie', 'headlines')}</span>
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-3">
            <div className="bg-gray-800/50 rounded px-3 py-2">
              <p className="text-[10px] text-gray-500 uppercase">{l('Punteggio', 'Score')}</p>
              <p className={`text-lg font-bold ${sentiment.score > 0.1 ? 'text-emerald-400' : sentiment.score < -0.1 ? 'text-red-400' : 'text-gray-300'}`}>
                {sentiment.score > 0 ? '+' : ''}{sentiment.score.toFixed(2)}
              </p>
            </div>
            {sentiment.fear_greed_label && (
              <div className="bg-gray-800/50 rounded px-3 py-2">
                <p className="text-[10px] text-gray-500 uppercase">Fear & Greed</p>
                <p className={`text-lg font-bold ${
                  sentiment.fear_greed_value >= 60 ? 'text-emerald-400' :
                  sentiment.fear_greed_value <= 40 ? 'text-red-400' : 'text-yellow-400'
                }`}>
                  {sentiment.fear_greed_value}
                </p>
                <p className="text-[10px] text-gray-500">{sentiment.fear_greed_label}</p>
              </div>
            )}
            <div className="bg-gray-800/50 rounded px-3 py-2">
              <p className="text-[10px] text-gray-500 uppercase">Bull / Bear</p>
              <p className="text-sm font-bold text-gray-300">
                <span className="text-emerald-400">{sentiment.bullish_count}</span>
                {' / '}
                <span className="text-red-400">{sentiment.bearish_count}</span>
              </p>
            </div>
            <div className="bg-gray-800/50 rounded px-3 py-2">
              <p className="text-[10px] text-gray-500 uppercase">{l('Notizie', 'News')}</p>
              <p className="text-sm font-bold text-gray-300">{sentiment.headline_count}</p>
              <p className="text-[10px] text-gray-500">{sentiment.neutral_count} neutral</p>
            </div>
          </div>
          {sentiment.top_headlines?.length > 0 && (
            <div className="space-y-0.5">
              {sentiment.top_headlines.slice(0, 3).map((h, i) => (
                <div key={i} className="flex items-center gap-2 text-xs py-0.5">
                  <span className={`w-10 text-right font-mono text-[11px] shrink-0 ${
                    h.sentiment > 0.1 ? 'text-emerald-400' : h.sentiment < -0.1 ? 'text-red-400' : 'text-gray-500'
                  }`}>
                    {h.sentiment > 0 ? '+' : ''}{h.sentiment.toFixed(2)}
                  </span>
                  <span className="text-gray-400 truncate">{h.title}</span>
                  <span className="text-[9px] text-gray-600 shrink-0">{h.source}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Stats row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label={t('cash_balance')} value={balance?.cash_balance ?? 0} sub="USDT" />
        <StatCard label={t('total_equity')} value={balance?.total_equity ?? 0} sub="USDT" />
        <StatCard
          label={t('total_pnl')}
          value={balance?.total_pnl ?? 0}
          color={(balance?.total_pnl ?? 0) >= 0 ? 'text-emerald-400' : 'text-red-400'}
          sub="USDT"
        />
        <StatCard
          label={t('win_rate')}
          value={
            balance && balance.total_trades > 0
              ? `${((balance.winning_trades / balance.total_trades) * 100).toFixed(1)}%`
              : 'N/A'
          }
          sub={`${balance?.winning_trades ?? 0}${t('win_short')} / ${balance?.losing_trades ?? 0}${t('loss_short')}`}
        />
      </div>

      {/* Engine status + prices */}
      <div className="space-y-2">
        <div className="flex flex-wrap gap-4 items-center text-sm text-gray-400">
          <span>{t('engine')}: {engine?.running ? t('running') : t('stopped')}</span>
          {dataMode === 'paper' && (
            <>
              <button onClick={handleReset}
                className="px-3 py-1 bg-yellow-700 hover:bg-yellow-600 text-white text-xs rounded">
                {t('reset_portfolio')}
              </button>
              <button onClick={handleExport}
                className="px-3 py-1 bg-gray-700 hover:bg-gray-600 text-white text-xs rounded">
                {t('export_csv')}
              </button>
            </>
          )}
        </div>
        {engine?.last_prices && (
          <div className="flex flex-wrap gap-3">
            {Object.entries(engine.last_prices).map(([sym, price]) => (
              <div key={sym} className="bg-gray-900 border border-gray-800 rounded-lg px-3 py-2 flex items-center gap-2">
                <span className="text-xs font-bold text-white">{sym.replace('USDT', '')}</span>
                <span className="text-sm text-gray-300">
                  {price > 0 ? price.toLocaleString(undefined, { maximumFractionDigits: 2 }) : '...'}
                </span>
                <span className="text-[10px] text-gray-500">USDT</span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Open positions */}
      <section>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-semibold text-white">{t('open_positions')}</h2>
          {posSymbols.length > 1 && (
            <div className="flex gap-1">
              <button className={filterBtnClass(posFilterSymbol === 'ALL')}
                onClick={() => setPosFilterSymbol('ALL')}>
                {l('Tutte', 'All')}
              </button>
              {posSymbols.map((s) => (
                <button key={s} className={filterBtnClass(posFilterSymbol === s)}
                  onClick={() => setPosFilterSymbol(s)}>
                  {s.replace('USDT', '')}
                </button>
              ))}
            </div>
          )}
        </div>
        {filteredPositions.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-gray-400 border-b border-gray-800">
                  <th className="text-left py-2 px-3">{t('symbol')}</th>
                  <th className="text-left py-2 px-3">{l('Fonte', 'Source')}</th>
                  <th className="text-right py-2 px-3">{t('qty')}</th>
                  <th className="text-right py-2 px-3">{t('entry')}</th>
                  <th className="text-right py-2 px-3">{t('current')}</th>
                  <th className="text-right py-2 px-3">{l('Valore', 'Value')} $</th>
                  <th className="text-right py-2 px-3">{t('pnl')}</th>
                  <th className="text-right py-2 px-3">{t('pnl')} %</th>
                  <th className="text-right py-2 px-3">SL</th>
                  <th className="text-right py-2 px-3">TP</th>
                  <th className="py-2 px-3"></th>
                </tr>
              </thead>
              <tbody>
                {filteredPositions.map((p) => (
                  <tr key={p.id} className="border-b border-gray-800/50 hover:bg-gray-900/50">
                    <td className="py-2 px-3 font-medium text-white">{p.symbol}</td>
                    <td className="py-2 px-3">
                      <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${
                        p.side === 'HOLD'
                          ? 'bg-gray-700 text-gray-400'
                          : 'bg-blue-900 text-blue-300'
                      }`}>
                        {p.side === 'HOLD' ? 'Wallet' : 'Bot'}
                      </span>
                    </td>
                    <td className="py-2 px-3 text-right">{p.quantity.toFixed(6)}</td>
                    <td className="py-2 px-3 text-right">{p.entry_price.toLocaleString()}</td>
                    <td className="py-2 px-3 text-right">{p.current_price?.toLocaleString() ?? '-'}</td>
                    <td className="py-2 px-3 text-right text-yellow-300 font-medium">{p.position_value_usdt.toFixed(2)}</td>
                    <td className={`py-2 px-3 text-right font-medium ${
                      p.unrealized_pnl >= 0 ? 'text-emerald-400' : 'text-red-400'
                    }`}>
                      {p.unrealized_pnl.toFixed(2)}
                    </td>
                    <td className={`py-2 px-3 text-right font-medium ${
                      p.unrealized_pnl_pct >= 0 ? 'text-emerald-400' : 'text-red-400'
                    }`}>
                      {p.unrealized_pnl_pct.toFixed(2)}%
                    </td>
                    <td className="py-2 px-3 text-right text-red-400">{p.stop_loss?.toLocaleString() ?? '-'}</td>
                    <td className="py-2 px-3 text-right text-emerald-400">{p.take_profit?.toLocaleString() ?? '-'}</td>
                    <td className="py-2 px-3">
                      {p.side !== 'HOLD' && (
                        <button
                          onClick={() => handleClosePosition(p)}
                          disabled={closingId === p.id}
                          className="px-2 py-1 bg-red-900/60 hover:bg-red-800/70 border border-red-800/50 text-red-300 text-xs rounded transition-colors disabled:opacity-40"
                        >
                          {closingId === p.id ? '...' : l('Chiudi', 'Close')}
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-gray-500 text-sm">{t('no_positions')}</p>
        )}
      </section>

      {/* Trade history */}
      <section>
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-3">
          <h2 className="text-lg font-semibold text-white">{t('recent_trades')}</h2>
          <div className="flex flex-wrap gap-2">
            {/* Status filter */}
            <div className="flex gap-1">
              {[
                { key: 'ALL', label: l('Tutti', 'All') },
                { key: 'OPEN', label: l('Aperti', 'Open') },
                { key: 'CLOSED', label: l('Chiusi', 'Closed') },
              ].map(({ key, label }) => (
                <button key={key} className={filterBtnClass(filterStatus === key)}
                  onClick={() => setFilterStatus(key)}>
                  {label}
                </button>
              ))}
            </div>
            {/* Side filter */}
            <div className="flex gap-1">
              {[
                { key: 'ALL', label: l('Tutti', 'All') },
                { key: 'BUY', label: 'BUY' },
                { key: 'SELL', label: 'SELL' },
              ].map(({ key, label }) => (
                <button key={key} className={filterBtnClass(filterSide === key)}
                  onClick={() => setFilterSide(key)}>
                  {label}
                </button>
              ))}
            </div>
            {/* Symbol filter */}
            {tradeSymbols.length > 1 && (
              <select
                value={filterSymbol}
                onChange={(e) => setFilterSymbol(e.target.value)}
                className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs text-white"
              >
                <option value="ALL">{l('Tutte le crypto', 'All crypto')}</option>
                {tradeSymbols.map((s) => (
                  <option key={s} value={s}>{s.replace('USDT', '')}</option>
                ))}
              </select>
            )}
          </div>
        </div>

        {filteredTrades.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-gray-400 border-b border-gray-800">
                  <th className="text-left py-2 px-3">ID</th>
                  <th className="text-left py-2 px-3">{t('symbol')}</th>
                  <th className="text-left py-2 px-3">{t('side')}</th>
                  <th className="text-right py-2 px-3">{t('entry')}</th>
                  <th className="text-right py-2 px-3">{t('exit')}</th>
                  <th className="text-right py-2 px-3">{t('pnl')}</th>
                  <th className="text-right py-2 px-3">{t('pnl')} %</th>
                  <th className="text-left py-2 px-3">{t('status')}</th>
                  <th className="text-left py-2 px-3">{t('strategy')}</th>
                </tr>
              </thead>
              <tbody>
                {filteredTrades.map((tr) => (
                  <tr key={tr.id} className="border-b border-gray-800/50 hover:bg-gray-900/50">
                    <td className="py-2 px-3 text-gray-500">#{tr.id}</td>
                    <td className="py-2 px-3 font-medium text-white">{tr.symbol}</td>
                    <td className={`py-2 px-3 ${tr.side === 'BUY' ? 'text-emerald-400' : 'text-red-400'}`}>
                      {tr.side}
                    </td>
                    <td className="py-2 px-3 text-right">{tr.entry_price.toLocaleString()}</td>
                    <td className="py-2 px-3 text-right">{tr.exit_price?.toLocaleString() ?? '-'}</td>
                    <td className={`py-2 px-3 text-right font-medium ${
                      (tr.pnl ?? 0) >= 0 ? 'text-emerald-400' : 'text-red-400'
                    }`}>
                      {tr.pnl?.toFixed(2) ?? '-'}
                    </td>
                    <td className={`py-2 px-3 text-right ${
                      (tr.pnl_pct ?? 0) >= 0 ? 'text-emerald-400' : 'text-red-400'
                    }`}>
                      {tr.pnl_pct != null ? `${tr.pnl_pct.toFixed(2)}%` : '-'}
                    </td>
                    <td className="py-2 px-3">
                      <span className={`px-2 py-0.5 rounded text-xs ${
                        tr.status === 'OPEN' ? 'bg-blue-900 text-blue-300' : 'bg-gray-800 text-gray-400'
                      }`}>
                        {tr.status}
                      </span>
                    </td>
                    <td className="py-2 px-3 text-gray-400 text-xs">{tr.strategy ?? '-'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-gray-500 text-sm">
            {trades && trades.length > 0
              ? l('Nessun trade corrisponde ai filtri', 'No trades match the filters')
              : t('no_trades')
            }
          </p>
        )}
      </section>
    </div>
  )
}
