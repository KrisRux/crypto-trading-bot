import { useState, useMemo } from 'react'
import { Position } from '../../api'
import { useLang } from '../../hooks/useLang'
import { pnlColor } from '../../utils/colors'
import { SkeletonRow } from '../Skeleton'

interface Props {
  positions: Position[]
  closingId: number | null
  onClose: (p: Position) => void
  loading?: boolean
}

const filterBtnClass = (active: boolean) =>
  `px-2.5 py-1 rounded text-xs font-medium transition-colors ${
    active ? 'bg-blue-600 text-white' : 'bg-gray-800 text-gray-400 hover:bg-gray-700'
  }`

export default function PositionsTable({ positions, closingId, onClose, loading }: Props) {
  const { t, l } = useLang()
  const [filterSymbol, setFilterSymbol] = useState('ALL')

  const symbols = useMemo(() => [...new Set(positions.map(p => p.symbol))].sort(), [positions])

  const filtered = useMemo(
    () => filterSymbol === 'ALL' ? positions : positions.filter(p => p.symbol === filterSymbol),
    [positions, filterSymbol]
  )

  return (
    <section>
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-lg font-semibold text-white">{t('open_positions')}</h2>
        {symbols.length > 1 && (
          <div className="flex gap-1">
            <button className={filterBtnClass(filterSymbol === 'ALL')} onClick={() => setFilterSymbol('ALL')}>
              {l('Tutte', 'All')}
            </button>
            {symbols.map(s => (
              <button key={s} className={filterBtnClass(filterSymbol === s)} onClick={() => setFilterSymbol(s)}>
                {s.replace('USDT', '')}
              </button>
            ))}
          </div>
        )}
      </div>
      {loading ? (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <tbody>{Array.from({ length: 3 }).map((_, i) => <SkeletonRow key={i} cols={11} />)}</tbody>
          </table>
        </div>
      ) : filtered.length > 0 ? (
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
                <th className="py-2 px-3" />
              </tr>
            </thead>
            <tbody>
              {filtered.map(p => (
                <tr key={p.id} className="border-b border-gray-800/50 hover:bg-gray-900/50">
                  <td className="py-2 px-3 font-medium text-white">{p.symbol}</td>
                  <td className="py-2 px-3">
                    <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${
                      p.side === 'HOLD' ? 'bg-gray-700 text-gray-400' : 'bg-blue-900 text-blue-300'
                    }`}>
                      {p.side === 'HOLD' ? 'Wallet' : 'Bot'}
                    </span>
                  </td>
                  <td className="py-2 px-3 text-right">{p.quantity.toFixed(6)}</td>
                  <td className="py-2 px-3 text-right">{p.entry_price.toLocaleString()}</td>
                  <td className="py-2 px-3 text-right">{p.current_price?.toLocaleString() ?? '-'}</td>
                  <td className="py-2 px-3 text-right text-yellow-300 font-medium">{p.position_value_usdt.toFixed(2)}</td>
                  <td className={`py-2 px-3 text-right font-medium ${pnlColor(p.unrealized_pnl)}`}>
                    {p.unrealized_pnl.toFixed(2)}
                  </td>
                  <td className={`py-2 px-3 text-right font-medium ${pnlColor(p.unrealized_pnl_pct)}`}>
                    {p.unrealized_pnl_pct.toFixed(2)}%
                  </td>
                  <td className="py-2 px-3 text-right text-red-400">{p.stop_loss?.toLocaleString() ?? '-'}</td>
                  <td className="py-2 px-3 text-right text-emerald-400">{p.take_profit?.toLocaleString() ?? '-'}</td>
                  <td className="py-2 px-3">
                    {p.side !== 'HOLD' && (
                      <button
                        onClick={() => onClose(p)}
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
  )
}
