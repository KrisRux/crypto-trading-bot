import { useState, useMemo } from 'react'
import { TradeItem } from '../../api'
import { useLang } from '../../hooks/useLang'
import { pnlColor } from '../../utils/colors'
import { SkeletonRow } from '../Skeleton'

interface Props {
  trades: TradeItem[]
  loading?: boolean
}

const filterBtnClass = (active: boolean) =>
  `px-2.5 py-1 rounded text-xs font-medium transition-colors ${
    active ? 'bg-blue-600 text-white' : 'bg-gray-800 text-gray-400 hover:bg-gray-700'
  }`

export default function TradeHistoryTable({ trades, loading }: Props) {
  const { t, l } = useLang()
  const [filterStatus, setFilterStatus] = useState('ALL')
  const [filterSide, setFilterSide] = useState('ALL')
  const [filterSymbol, setFilterSymbol] = useState('ALL')

  const symbols = useMemo(() => [...new Set(trades.map(tr => tr.symbol))].sort(), [trades])

  const filtered = useMemo(() => trades.filter(tr => {
    if (filterStatus !== 'ALL' && tr.status !== filterStatus) return false
    if (filterSide !== 'ALL' && tr.side !== filterSide) return false
    if (filterSymbol !== 'ALL' && tr.symbol !== filterSymbol) return false
    return true
  }), [trades, filterStatus, filterSide, filterSymbol])

  return (
    <section>
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-3">
        <h2 className="text-lg font-semibold text-white">{t('recent_trades')}</h2>
        <div className="flex flex-wrap gap-2">
          <div className="flex gap-1">
            {[
              { key: 'ALL', label: l('Tutti', 'All') },
              { key: 'OPEN', label: l('Aperti', 'Open') },
              { key: 'CLOSED', label: l('Chiusi', 'Closed') },
            ].map(({ key, label }) => (
              <button key={key} className={filterBtnClass(filterStatus === key)} onClick={() => setFilterStatus(key)}>
                {label}
              </button>
            ))}
          </div>
          <div className="flex gap-1">
            {[
              { key: 'ALL', label: l('Tutti', 'All') },
              { key: 'BUY', label: 'BUY' },
              { key: 'SELL', label: 'SELL' },
            ].map(({ key, label }) => (
              <button key={key} className={filterBtnClass(filterSide === key)} onClick={() => setFilterSide(key)}>
                {label}
              </button>
            ))}
          </div>
          {symbols.length > 1 && (
            <select
              value={filterSymbol}
              onChange={e => setFilterSymbol(e.target.value)}
              className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs text-white"
            >
              <option value="ALL">{l('Tutte le crypto', 'All crypto')}</option>
              {symbols.map(s => (
                <option key={s} value={s}>{s.replace('USDT', '')}</option>
              ))}
            </select>
          )}
        </div>
      </div>

      {loading ? (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <tbody>{Array.from({ length: 5 }).map((_, i) => <SkeletonRow key={i} cols={9} />)}</tbody>
          </table>
        </div>
      ) : filtered.length > 0 ? (
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
              {filtered.map(tr => (
                <tr key={tr.id} className="border-b border-gray-800/50 hover:bg-gray-900/50">
                  <td className="py-2 px-3 text-gray-500">#{tr.id}</td>
                  <td className="py-2 px-3 font-medium text-white">{tr.symbol}</td>
                  <td className={`py-2 px-3 ${tr.side === 'BUY' ? 'text-emerald-400' : 'text-red-400'}`}>{tr.side}</td>
                  <td className="py-2 px-3 text-right">{tr.entry_price.toLocaleString()}</td>
                  <td className="py-2 px-3 text-right">{tr.exit_price?.toLocaleString() ?? '-'}</td>
                  <td className={`py-2 px-3 text-right font-medium ${pnlColor(tr.pnl ?? 0)}`}>
                    {tr.pnl?.toFixed(2) ?? '-'}
                  </td>
                  <td className={`py-2 px-3 text-right ${pnlColor(tr.pnl_pct ?? 0)}`}>
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
          {trades.length > 0
            ? l('Nessun trade corrisponde ai filtri', 'No trades match the filters')
            : t('no_trades')
          }
        </p>
      )}
    </section>
  )
}
