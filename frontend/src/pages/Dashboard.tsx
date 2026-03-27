import { useCallback } from 'react'
import { api, Balance, Position, TradeItem, EngineStatus } from '../api'
import { usePolling } from '../hooks/usePolling'
import { useLang } from '../hooks/useLang'
import StatCard from '../components/StatCard'

interface Props {
  mode: string
}

export default function Dashboard({ mode }: Props) {
  const { t } = useLang()

  const fetchBalance = useCallback(() => api.getBalance(), [])
  const fetchPositions = useCallback(() => api.getPositions(), [])
  const fetchTrades = useCallback(() => api.getTrades(), [])
  const fetchEngine = useCallback(() => api.getEngineStatus(), [])

  const [balance] = usePolling<Balance>(fetchBalance, 5000)
  const [positions] = usePolling<Position[]>(fetchPositions, 5000)
  const [trades] = usePolling<TradeItem[]>(fetchTrades, 10000)
  const [engine] = usePolling<EngineStatus>(fetchEngine, 5000)

  const handleReset = async () => {
    if (mode !== 'paper') return
    if (!confirm(t('reset_confirm'))) return
    try {
      await api.resetPaperPortfolio()
      window.location.reload()
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

  return (
    <div className="space-y-6">
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
          {mode === 'paper' && (
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
        {/* Multi-symbol prices */}
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
        <h2 className="text-lg font-semibold text-white mb-3">{t('open_positions')}</h2>
        {positions && positions.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-gray-400 border-b border-gray-800">
                  <th className="text-left py-2 px-3">{t('symbol')}</th>
                  <th className="text-right py-2 px-3">{t('qty')}</th>
                  <th className="text-right py-2 px-3">{t('entry')}</th>
                  <th className="text-right py-2 px-3">{t('current')}</th>
                  <th className="text-right py-2 px-3">{t('pnl')}</th>
                  <th className="text-right py-2 px-3">SL</th>
                  <th className="text-right py-2 px-3">TP</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((p) => (
                  <tr key={p.id} className="border-b border-gray-800/50 hover:bg-gray-900/50">
                    <td className="py-2 px-3 font-medium text-white">{p.symbol}</td>
                    <td className="py-2 px-3 text-right">{p.quantity.toFixed(6)}</td>
                    <td className="py-2 px-3 text-right">{p.entry_price.toLocaleString()}</td>
                    <td className="py-2 px-3 text-right">{p.current_price?.toLocaleString() ?? '-'}</td>
                    <td className={`py-2 px-3 text-right font-medium ${
                      p.unrealized_pnl >= 0 ? 'text-emerald-400' : 'text-red-400'
                    }`}>
                      {p.unrealized_pnl.toFixed(2)}
                    </td>
                    <td className="py-2 px-3 text-right text-red-400">{p.stop_loss?.toLocaleString() ?? '-'}</td>
                    <td className="py-2 px-3 text-right text-emerald-400">{p.take_profit?.toLocaleString() ?? '-'}</td>
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
        <h2 className="text-lg font-semibold text-white mb-3">{t('recent_trades')}</h2>
        {trades && trades.length > 0 ? (
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
                {trades.map((tr) => (
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
          <p className="text-gray-500 text-sm">{t('no_trades')}</p>
        )}
      </section>
    </div>
  )
}
