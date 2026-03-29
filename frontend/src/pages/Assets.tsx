import { useState, useMemo } from 'react'
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

type SortKey = 'asset' | 'value_usdt' | 'total' | 'price_usdt'
type SortDir = 'asc' | 'desc'

export default function Assets() {
  const { t } = useLang()
  const [assets, loading, error] = usePolling<AssetItem[]>(api.getAssets, 10_000)
  const [balance] = usePolling(api.getBalance, 10_000)

  const mode = balance?.mode ?? 'paper'
  const isLive = mode === 'live'

  // -- Filters state --
  const [search, setSearch] = useState('')
  const [hideDust, setHideDust] = useState(false)
  const [dustThreshold, setDustThreshold] = useState(1)
  const [onlyWithPrice, setOnlyWithPrice] = useState(false)
  const [sortKey, setSortKey] = useState<SortKey>('value_usdt')
  const [sortDir, setSortDir] = useState<SortDir>('desc')

  const handleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    } else {
      setSortKey(key)
      setSortDir('desc')
    }
  }

  const filtered = useMemo(() => {
    if (!assets) return []
    let list = [...assets]

    if (search.trim())
      list = list.filter(a => a.asset.toLowerCase().includes(search.trim().toLowerCase()))

    if (hideDust)
      list = list.filter(a => a.value_usdt >= dustThreshold)

    if (onlyWithPrice)
      list = list.filter(a => a.price_usdt > 0)

    list.sort((a, b) => {
      const av = a[sortKey] as number | string
      const bv = b[sortKey] as number | string
      if (typeof av === 'string') return sortDir === 'asc' ? av.localeCompare(bv as string) : (bv as string).localeCompare(av)
      return sortDir === 'asc' ? (av as number) - (bv as number) : (bv as number) - (av as number)
    })

    return list
  }, [assets, search, hideDust, dustThreshold, onlyWithPrice, sortKey, sortDir])

  const totalValue = filtered.reduce((s, a) => s + a.value_usdt, 0)

  const SortIcon = ({ k }: { k: SortKey }) => {
    if (sortKey !== k) return <span className="text-gray-700 ml-1">↕</span>
    return <span className="text-blue-400 ml-1">{sortDir === 'asc' ? '↑' : '↓'}</span>
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
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

      {/* Filters bar */}
      <div className="bg-gray-900 border border-gray-800 rounded-xl p-3 flex flex-wrap gap-3 items-center">
        {/* Search */}
        <input
          type="text"
          placeholder="Cerca asset..."
          value={search}
          onChange={e => setSearch(e.target.value)}
          className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-blue-500 w-36"
        />

        {/* Hide dust */}
        <label className="flex items-center gap-2 cursor-pointer select-none">
          <button
            onClick={() => setHideDust(v => !v)}
            className={`relative w-9 h-5 rounded-full transition-colors ${hideDust ? 'bg-blue-600' : 'bg-gray-700'}`}
          >
            <span className={`absolute top-0.5 left-0.5 w-4 h-4 bg-white rounded-full transition-transform ${hideDust ? 'translate-x-4' : ''}`} />
          </button>
          <span className="text-xs text-gray-400">Nascondi dust</span>
          {hideDust && (
            <div className="flex items-center gap-1">
              <span className="text-xs text-gray-500">&lt;</span>
              <input
                type="number"
                min={0}
                step={0.1}
                value={dustThreshold}
                onChange={e => setDustThreshold(parseFloat(e.target.value) || 0)}
                className="w-14 bg-gray-800 border border-gray-700 rounded px-2 py-0.5 text-xs text-white focus:outline-none focus:border-blue-500"
              />
              <span className="text-xs text-gray-500">USDT</span>
            </div>
          )}
        </label>

        {/* Only with price */}
        <label className="flex items-center gap-2 cursor-pointer select-none">
          <button
            onClick={() => setOnlyWithPrice(v => !v)}
            className={`relative w-9 h-5 rounded-full transition-colors ${onlyWithPrice ? 'bg-blue-600' : 'bg-gray-700'}`}
          >
            <span className={`absolute top-0.5 left-0.5 w-4 h-4 bg-white rounded-full transition-transform ${onlyWithPrice ? 'translate-x-4' : ''}`} />
          </button>
          <span className="text-xs text-gray-400">Solo con prezzo</span>
        </label>

        {/* Reset filters */}
        {(search || hideDust || onlyWithPrice) && (
          <button
            onClick={() => { setSearch(''); setHideDust(false); setOnlyWithPrice(false) }}
            className="ml-auto text-xs text-gray-500 hover:text-white transition-colors"
          >
            Reset filtri
          </button>
        )}

        {/* Results count */}
        <span className="ml-auto text-xs text-gray-600">
          {filtered.length} / {assets?.length ?? 0} asset
        </span>
      </div>

      {/* Error */}
      {error && (
        <div className="bg-red-900/30 border border-red-700 rounded-lg px-4 py-3 text-red-300 text-sm">
          {error}
        </div>
      )}

      {/* Table */}
      <div className="bg-gray-900 rounded-xl border border-gray-800 overflow-hidden">
        {loading && !assets ? (
          <div className="text-center py-12 text-gray-500 text-sm">Caricamento...</div>
        ) : !filtered || filtered.length === 0 ? (
          <div className="text-center py-12 text-gray-500 text-sm">
            {assets && assets.length > 0 ? 'Nessun asset corrisponde ai filtri' : t('assets_no_data')}
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-800 text-xs text-gray-400 uppercase tracking-wide">
                <th className="px-4 py-3 text-left cursor-pointer hover:text-white select-none"
                  onClick={() => handleSort('asset')}>
                  {t('asset')}<SortIcon k="asset" />
                </th>
                <th className="px-4 py-3 text-right">{t('free')}</th>
                <th className="px-4 py-3 text-right">{t('locked')}</th>
                <th className="px-4 py-3 text-right cursor-pointer hover:text-white select-none"
                  onClick={() => handleSort('total')}>
                  {t('total_qty')}<SortIcon k="total" />
                </th>
                <th className="px-4 py-3 text-right cursor-pointer hover:text-white select-none"
                  onClick={() => handleSort('price_usdt')}>
                  {t('price_usdt')}<SortIcon k="price_usdt" />
                </th>
                <th className="px-4 py-3 text-right cursor-pointer hover:text-white select-none"
                  onClick={() => handleSort('value_usdt')}>
                  {t('value_usdt')}<SortIcon k="value_usdt" />
                </th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((a) => {
                const totalFiltered = filtered.reduce((s, x) => s + x.value_usdt, 0)
                const pct = totalFiltered > 0 ? (a.value_usdt / totalFiltered) * 100 : 0
                return (
                  <tr key={a.asset} className="border-b border-gray-800/50 hover:bg-gray-800/30 transition-colors">
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        <span className="font-semibold text-white">{a.asset}</span>
                        {a.locked > 0 && (
                          <span className="text-[10px] px-1.5 py-0.5 rounded bg-yellow-900/40 text-yellow-400 border border-yellow-800/50">
                            locked
                          </span>
                        )}
                        {a.price_usdt === 0 && (
                          <span className="text-[10px] px-1.5 py-0.5 rounded bg-gray-800 text-gray-500 border border-gray-700">
                            no price
                          </span>
                        )}
                      </div>
                      <div className="mt-1 h-1 w-24 bg-gray-800 rounded-full overflow-hidden">
                        <div className="h-full bg-blue-500 rounded-full" style={{ width: `${Math.min(pct, 100)}%` }} />
                      </div>
                    </td>
                    <td className="px-4 py-3 text-right font-mono text-gray-200">{fmtQty(a.free)}</td>
                    <td className="px-4 py-3 text-right font-mono">
                      {a.locked > 0
                        ? <span className="text-yellow-400">{fmtQty(a.locked)}</span>
                        : <span className="text-gray-600">—</span>}
                    </td>
                    <td className="px-4 py-3 text-right font-mono text-gray-200">{fmtQty(a.total)}</td>
                    <td className="px-4 py-3 text-right font-mono text-gray-400">
                      {a.price_usdt > 0
                        ? (a.asset === 'USDT' ? '1.00' : fmt(a.price_usdt, a.price_usdt >= 1 ? 2 : 6))
                        : <span className="text-gray-600">—</span>}
                    </td>
                    <td className="px-4 py-3 text-right">
                      {a.value_usdt > 0
                        ? <><span className="font-semibold text-white">{fmt(a.value_usdt)}</span>
                            <span className="ml-1.5 text-xs text-gray-500">{pct.toFixed(1)}%</span></>
                        : <span className="text-gray-600">—</span>}
                    </td>
                  </tr>
                )
              })}
            </tbody>
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
