import { useCallback } from 'react'
import { api, SignalItem, OrderItem } from '../api'
import { usePolling } from '../hooks/usePolling'
import { useLang } from '../hooks/useLang'

export default function Logs() {
  const { t } = useLang()

  const fetchSignals = useCallback(() => api.getSignals(), [])
  const fetchOrders = useCallback(() => api.getOrders(), [])

  const [signals] = usePolling<SignalItem[]>(fetchSignals, 5000)
  const [orders] = usePolling<OrderItem[]>(fetchOrders, 5000)

  return (
    <div className="space-y-8">
      {/* Signals */}
      <section>
        <h2 className="text-lg font-semibold text-white mb-3">{t('recent_signals')}</h2>
        {signals && signals.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-gray-400 border-b border-gray-800">
                  <th className="text-left py-2 px-3">{t('time')}</th>
                  <th className="text-left py-2 px-3">{t('type')}</th>
                  <th className="text-left py-2 px-3">{t('symbol')}</th>
                  <th className="text-right py-2 px-3">{t('price')}</th>
                  <th className="text-left py-2 px-3">{t('strategy')}</th>
                  <th className="text-left py-2 px-3">{t('reason')}</th>
                </tr>
              </thead>
              <tbody>
                {[...signals].reverse().map((s, i) => (
                  <tr key={i} className="border-b border-gray-800/50 hover:bg-gray-900/50">
                    <td className="py-2 px-3 text-gray-400 text-xs whitespace-nowrap">
                      {new Date(s.time).toLocaleString()}
                    </td>
                    <td className="py-2 px-3">
                      <span className={`px-2 py-0.5 rounded text-xs font-bold ${
                        s.type === 'BUY' ? 'bg-emerald-900 text-emerald-300'
                        : s.type === 'SELL' ? 'bg-red-900 text-red-300'
                        : 'bg-gray-800 text-gray-400'
                      }`}>
                        {s.type}
                      </span>
                    </td>
                    <td className="py-2 px-3 text-white">{s.symbol}</td>
                    <td className="py-2 px-3 text-right">{s.price.toLocaleString()}</td>
                    <td className="py-2 px-3 text-gray-400">{s.strategy}</td>
                    <td className="py-2 px-3 text-gray-400 text-xs">{s.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-gray-500 text-sm">{t('no_signals')}</p>
        )}
      </section>

      {/* Orders */}
      <section>
        <h2 className="text-lg font-semibold text-white mb-3">
          {t('order_history')}
        </h2>
        {orders && orders.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-gray-400 border-b border-gray-800">
                  <th className="text-left py-2 px-3">ID</th>
                  <th className="text-left py-2 px-3">{t('symbol')}</th>
                  <th className="text-left py-2 px-3">{t('side')}</th>
                  <th className="text-left py-2 px-3">{t('order_type')}</th>
                  <th className="text-right py-2 px-3">{t('qty')}</th>
                  <th className="text-right py-2 px-3">{t('price')}</th>
                  <th className="text-right py-2 px-3">{t('filled')}</th>
                  <th className="text-left py-2 px-3">{t('status')}</th>
                  <th className="text-left py-2 px-3">{t('error')}</th>
                  <th className="text-left py-2 px-3">{t('time')}</th>
                </tr>
              </thead>
              <tbody>
                {orders.map((o) => (
                  <tr key={o.id} className="border-b border-gray-800/50 hover:bg-gray-900/50">
                    <td className="py-2 px-3 text-gray-500">#{o.id}</td>
                    <td className="py-2 px-3 text-white">{o.symbol}</td>
                    <td className={`py-2 px-3 ${o.side === 'BUY' ? 'text-emerald-400' : 'text-red-400'}`}>
                      {o.side}
                    </td>
                    <td className="py-2 px-3 text-gray-400">{o.order_type}</td>
                    <td className="py-2 px-3 text-right">{o.quantity.toFixed(6)}</td>
                    <td className="py-2 px-3 text-right">{o.price?.toLocaleString() ?? '-'}</td>
                    <td className="py-2 px-3 text-right">{o.filled_price?.toLocaleString() ?? '-'}</td>
                    <td className="py-2 px-3">
                      <span className={`px-2 py-0.5 rounded text-xs ${
                        o.status === 'FILLED' ? 'bg-emerald-900 text-emerald-300'
                        : o.status === 'FAILED' ? 'bg-red-900 text-red-300'
                        : 'bg-yellow-900 text-yellow-300'
                      }`}>
                        {o.status}
                      </span>
                    </td>
                    <td className="py-2 px-3 text-red-400 text-xs max-w-[200px] truncate">
                      {o.error_message ?? ''}
                    </td>
                    <td className="py-2 px-3 text-gray-400 text-xs whitespace-nowrap">
                      {o.created_at ? new Date(o.created_at).toLocaleString() : '-'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-gray-500 text-sm">{t('no_orders')}</p>
        )}
      </section>
    </div>
  )
}
