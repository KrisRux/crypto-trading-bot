import { api, AssetItem } from '../api'
import { usePolling } from '../hooks/usePolling'
import { useLang } from '../hooks/useLang'

function fmt(n: number, decimals = 2): string {
  return n.toLocaleString(undefined, {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })
}

function fmtQty(n: number): string {
  if (n === 0) return '0'
  if (n >= 1) return fmt(n, 4)
  return n.toPrecision(4)
}

export default function Assets() {
  const { t } = useLang()
  const [assets, loading, error] = usePolling<AssetItem[]>(api.getAssets, 10_000)
  const [balance] = usePolling(api.getBalance, 10_000)

  const mode = balance?.mode ?? 'paper'
  const isLive = mode === 'live'

  const totalValue = assets?.reduce((s, a) => s + a.value_usdt, 0) ?? 0

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-bold text-white">{t('assets_title')}</h1>
          <span className={`px-2.5 py-0.5 rounded-full text-xs font-semibold ${
            isLive
              ? 'bg-green-900/60 text-green-300 border border-green-700'
              : 'bg-yellow-900/60 text-yellow-300 border border-yellow-700'
          }`}>
            {isLive ? t('assets_mode_live') : t('assets_mode_paper')}
          </span>
        </div>
        {assets && assets.length > 0 && (
          <div className="text-right">
            <p className="text-xs text-gray-500">{t('total_value')}</p>
            <p className="text-xl font-bold text-white">{fmt(totalValue)} USDT</p>
          </div>
        )}
      </div>

      {/* Error */}
      {error && (
        <div className="bg-red-900/30 border border-red-700 rounded-lg px-4 py-3 text-red-300 text-sm">
          {error.message}
        </div>
      )}

      {/* Table */}
      <div className="bg-gray-900 rounded-xl border border-gray-800 overflow-hidden">
        {loading && !assets ? (
          <div className="text-center py-12 text-gray-500 text-sm">Caricamento...</div>
        ) : !assets || assets.length === 0 ? (
          <div className="text-center py-12 text-gray-500 text-sm">{t('assets_no_data')}</div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-800 text-xs text-gray-400 uppercase tracking-wide">
                <th className="px-4 py-3 text-left">{t('asset')}</th>
                <th className="px-4 py-3 text-right">{t('free')}</th>
                <th className="px-4 py-3 text-right">{t('locked')}</th>
                <th className="px-4 py-3 text-right">{t('total_qty')}</th>
                <th className="px-4 py-3 text-right">{t('price_usdt')}</th>
                <th className="px-4 py-3 text-right">{t('value_usdt')}</th>
              </tr>
            </thead>
            <tbody>
              {assets.map((a) => {
                const pct = totalValue > 0 ? (a.value_usdt / totalValue) * 100 : 0
                return (
                  <tr key={a.asset} className="border-b border-gray-800/50 hover:bg-gray-800/30 transition-colors">
                    {/* Asset */}
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        <span className="font-semibold text-white">{a.asset}</span>
                        {a.locked > 0 && (
                          <span className="text-[10px] px-1.5 py-0.5 rounded bg-yellow-900/40 text-yellow-400 border border-yellow-800/50">
                            locked
                          </span>
                        )}
                      </div>
                      {/* Progress bar */}
                      <div className="mt-1 h-1 w-24 bg-gray-800 rounded-full overflow-hidden">
                        <div
                          className="h-full bg-blue-500 rounded-full"
                          style={{ width: `${Math.min(pct, 100)}%` }}
                        />
                      </div>
                    </td>
                    {/* Free */}
                    <td className="px-4 py-3 text-right font-mono text-gray-200">
                      {fmtQty(a.free)}
                    </td>
                    {/* Locked */}
                    <td className="px-4 py-3 text-right font-mono">
                      {a.locked > 0
                        ? <span className="text-yellow-400">{fmtQty(a.locked)}</span>
                        : <span className="text-gray-600">—</span>
                      }
                    </td>
                    {/* Total */}
                    <td className="px-4 py-3 text-right font-mono text-gray-200">
                      {fmtQty(a.total)}
                    </td>
                    {/* Price */}
                    <td className="px-4 py-3 text-right font-mono text-gray-400">
                      {a.asset === 'USDT' ? '1.00' : fmt(a.price_usdt, a.price_usdt >= 1 ? 2 : 6)}
                    </td>
                    {/* Value */}
                    <td className="px-4 py-3 text-right">
                      <span className="font-semibold text-white">{fmt(a.value_usdt)}</span>
                      <span className="ml-1.5 text-xs text-gray-500">{pct.toFixed(1)}%</span>
                    </td>
                  </tr>
                )
              })}
            </tbody>
            {/* Footer totale */}
            <tfoot>
              <tr className="border-t border-gray-700 bg-gray-800/40">
                <td colSpan={5} className="px-4 py-3 text-sm font-semibold text-gray-300">
                  {t('total_value')}
                </td>
                <td className="px-4 py-3 text-right font-bold text-white text-base">
                  {fmt(totalValue)} USDT
                </td>
              </tr>
            </tfoot>
          </table>
        )}
      </div>
    </div>
  )
}
