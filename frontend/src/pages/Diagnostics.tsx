import { useCallback, useMemo, useState } from 'react'
import { api, DiagnosticsData, DiagEvent } from '../api'
import { usePolling } from '../hooks/usePolling'
import StatCard from '../components/StatCard'

/* ── helpers ── */
const badge = (text: string, cls: string) =>
  <span className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase ${cls}`}>{text}</span>

const regimeBadge = (r: string) => {
  const m: Record<string, string> = {
    trend: 'bg-emerald-900/60 text-emerald-300',
    range: 'bg-blue-900/60 text-blue-300',
    volatile: 'bg-yellow-900/60 text-yellow-300',
    defensive: 'bg-red-900/60 text-red-300',
  }
  return badge(r || '-', m[r] || 'bg-gray-800 text-gray-400')
}

const levelBadge = (l: string) => {
  const m: Record<string, string> = {
    blocked: 'bg-red-900/60 text-red-300',
    passed: 'bg-emerald-900/60 text-emerald-300',
    signal: 'bg-blue-900/60 text-blue-300',
    fill: 'bg-emerald-900/60 text-emerald-300',
    profile: 'bg-purple-900/60 text-purple-300',
    info: 'bg-gray-800 text-gray-400',
  }
  return badge(l, m[l] || 'bg-gray-800 text-gray-400')
}

const pnlColor = (v: number) => v >= 0 ? 'text-emerald-400' : 'text-red-400'
const pct = (n: number, d: number) => d > 0 ? (n / d * 100).toFixed(0) : '0'

/* ── count helper ── */
function countBy<T>(arr: T[], key: (item: T) => string): Record<string, number> {
  const out: Record<string, number> = {}
  for (const item of arr) { const k = key(item); out[k] = (out[k] || 0) + 1 }
  return out
}

function topEntries(obj: Record<string, number>, n = 10): [string, number][] {
  return Object.entries(obj).sort((a, b) => b[1] - a[1]).slice(0, n)
}

/* ── diagnosis engine ── */
interface DiagItem { level: 'ok' | 'warn' | 'crit'; title: string; desc: string }

function runDiagnosis(events: DiagEvent[], perf: DiagnosticsData['status']['performance'] | null, blockPct: number, filled: number, generated: number): DiagItem[] {
  const items: DiagItem[] = []
  if (blockPct > 85) items.push({ level: 'crit', title: 'Bot troppo prudente', desc: `${blockPct.toFixed(0)}% dei segnali viene bloccato dai guardrails.` })
  else if (blockPct > 60) items.push({ level: 'warn', title: 'Filtri mediamente severi', desc: `${blockPct.toFixed(0)}% dei segnali bloccato.` })
  if (filled === 0 && generated > 5) items.push({ level: 'crit', title: 'Zero trade eseguiti', desc: `${generated} segnali generati ma nessun fill.` })
  if (perf) {
    if (perf.consecutive_losses >= 6) items.push({ level: 'crit', title: `${perf.consecutive_losses} loss consecutive`, desc: 'Kill switch probabilmente attivo.' })
    else if (perf.consecutive_losses >= 3) items.push({ level: 'warn', title: `${perf.consecutive_losses} loss consecutive`, desc: 'Risk scaling attivo, soglie alzate.' })
    if (perf.drawdown_intraday >= 2) items.push({ level: 'crit', title: `Drawdown ${perf.drawdown_intraday.toFixed(2)}%`, desc: 'Trading probabilmente sospeso.' })
    else if (perf.drawdown_intraday >= 1.5) items.push({ level: 'warn', title: `Drawdown ${perf.drawdown_intraday.toFixed(2)}%`, desc: 'Risk multiplier ridotto a 0.50.' })
    if (perf.win_rate_last_10 <= 15 && perf.win_rate_last_10 > 0) items.push({ level: 'crit', title: `Win rate ${perf.win_rate_last_10.toFixed(0)}%`, desc: 'Win rate critico.' })
    if (perf.trades_per_hour < 0.1) items.push({ level: 'warn', title: 'Frequenza trade molto bassa', desc: `${perf.trades_per_hour.toFixed(2)} trades/h.` })
  }
  const ks = events.filter(e => e.type === 'kill_switch' && e.action === 'activated')
  if (ks.length > 0) items.push({ level: 'warn', title: 'Kill switch attivato nel periodo', desc: `${ks.length} attivazioni rilevate.` })
  if (items.length === 0) items.push({ level: 'ok', title: 'Nessun problema rilevato', desc: 'Parametri operativi nella norma.' })
  return items
}

/* ─────────────────────────────────── COMPONENT ─────────────────────────────────── */

export default function Diagnostics() {
  const fetcher = useCallback(() => api.getDiagnostics(), [])
  const [data, loading, error, refetch] = usePolling<DiagnosticsData>(fetcher, 30000)

  /* filters for event log */
  const [evSearch, setEvSearch] = useState('')
  const [evType, setEvType] = useState('')
  const [evSymbol, setEvSymbol] = useState('')

  /* derived data */
  const events = data?.events || []
  const status = data?.status
  const perf = status?.performance || null
  const regime = status?.regime
  const advisor = status?.advisor
  const guardrails = status?.guardrails

  const blocks = useMemo(() => events.filter(e => e.type === 'block'), [events])
  const passes = useMemo(() => events.filter(e => e.type === 'pass'), [events])
  const signals = useMemo(() => events.filter(e => e.type === 'signal'), [events])
  const fills = useMemo(() => events.filter(e => e.type === 'fill'), [events])
  const perfEntries = useMemo(() => {
    const seen = new Set<string>()
    return events.filter(e => {
      if (e.type !== 'perf' || !e.ts) return false
      // Truncate to minute to dedup (compute called twice per cycle, ~1s apart)
      const key = e.ts.substring(0, 16)
      if (seen.has(key)) return false
      seen.add(key)
      return true
    })
  }, [events])
  const profileChanges = useMemo(() => events.filter(e => e.type === 'profile'), [events])

  const totalBlocked = blocks.length
  const totalPassed = passes.length
  const total = totalBlocked + totalPassed || 1
  const blockPct = totalBlocked / total * 100

  /* block reason counts */
  const blocksByReason = useMemo(() => countBy(blocks, b => b.reason || 'unknown'), [blocks])
  const blocksBySymbol = useMemo(() => countBy(blocks.filter(b => b.symbol), b => b.symbol), [blocks])

  /* symbol diagnostics from regime events */
  const symbolData = useMemo(() => {
    const map: Record<string, { regime: string; adx: number; atr: number; bb: number; vol: number; signals: number; passed: number; blocked: number; filled: number; topBlock: string }> = {}
    for (const e of events.filter(ev => ev.type === 'regime')) {
      map[e.symbol] = { regime: e.regime!, adx: e.adx!, atr: e.atr!, bb: e.bb!, vol: e.vol!, signals: 0, passed: 0, blocked: 0, filled: 0, topBlock: '-' }
    }
    for (const e of signals) { if (map[e.symbol]) map[e.symbol].signals++ }
    for (const e of passes) { if (map[e.symbol]) map[e.symbol].passed++ }
    for (const e of blocks) { if (map[e.symbol]) map[e.symbol].blocked++ }
    for (const e of fills) { if (map[e.symbol]) map[e.symbol].filled++ }
    // Add symbols from JSON status that may not appear in log
    if (regime?.symbols) {
      for (const [sym, snap] of Object.entries(regime.symbols)) {
        if (!map[sym]) map[sym] = { regime: snap.regime, adx: snap.adx, atr: snap.atr_pct, bb: snap.bb_width_pct, vol: snap.volume_ratio, signals: 0, passed: 0, blocked: 0, filled: 0, topBlock: '-' }
      }
    }
    // top block per symbol
    for (const [sym] of Object.entries(map)) {
      const symBlocks = blocks.filter(b => b.symbol === sym)
      const reasons = countBy(symBlocks, b => b.reason || 'unknown')
      const top = topEntries(reasons, 1)[0]
      if (top) map[sym].topBlock = `${top[0]} (${top[1]})`
    }
    return map
  }, [events, signals, passes, blocks, fills, regime])

  /* funnel */
  const funnel = useMemo(() => ({
    generated: signals.length,
    gatePass: passes.filter(p => p.source === 'trade_gate').length,
    scorePass: passes.filter(p => p.source === 'dynamic_score').length,
    filled: fills.length,
    blocked: totalBlocked,
  }), [signals, passes, fills, totalBlocked])
  const funnelMax = Math.max(funnel.generated, 1)

  /* diagnosis */
  const diagnosis = useMemo(() => runDiagnosis(events, perf, blockPct, fills.length, signals.length), [events, perf, blockPct, fills, signals])

  /* event log filtering */
  const evTypes = useMemo(() => [...new Set(events.map(e => e.type))].sort(), [events])
  const evSymbols = useMemo(() => [...new Set(events.map(e => e.symbol).filter(Boolean))].sort(), [events])
  const filteredEvents = useMemo(() => {
    let out = [...events].reverse().slice(0, 500)
    if (evSearch) { const s = evSearch.toLowerCase(); out = out.filter(e => JSON.stringify(e).toLowerCase().includes(s)) }
    if (evType) out = out.filter(e => e.type === evType)
    if (evSymbol) out = out.filter(e => e.symbol === evSymbol)
    return out
  }, [events, evSearch, evType, evSymbol])

  const eventText = (e: DiagEvent): string => {
    if (e.type === 'block') return `${(e.source || '').toUpperCase()} blocked: ${e.reason || ''}`
    if (e.type === 'pass') return `${(e.source || '').toUpperCase()} passed`
    if (e.type === 'signal') return `Signal ${e.side} @ ${e.price} [${e.strategy}]`
    if (e.type === 'fill') return `FILL ${e.symbol}`
    if (e.type === 'perf') return `PnL24h=${e.pnl24h} WR=${e.wr}% DD=${e.dd}% CL=${e.consec}`
    if (e.type === 'regime') return `${e.regime?.toUpperCase()} ADX=${e.adx} Vol=${e.vol}x`
    if (e.type === 'profile') return `Profile: ${e.from} → ${e.to}`
    if (e.type === 'kill_switch') return `KILL_SWITCH ${e.action}`
    if (e.type === 'regime_change') return `Regime: ${e.from} → ${e.to}`
    if (e.type === 'risk') return `Risk multiplier=${e.multiplier}`
    return e.type
  }

  /* ─── funnel bar ─── */
  const FunnelBar = ({ label, value, color }: { label: string; value: number; color: string }) => (
    <div className="flex items-center gap-3 mb-1">
      <span className="w-32 text-right text-xs text-gray-500 shrink-0">{label}</span>
      <div className="flex-1 h-6 bg-gray-900 rounded overflow-hidden">
        <div className="h-full rounded flex items-center px-2" style={{ width: `${Math.max(value / funnelMax * 100, 3)}%`, background: color }}>
          <span className="text-[10px] font-bold text-white">{value}</span>
        </div>
      </div>
      <span className="w-10 text-right text-xs font-bold">{pct(value, funnelMax)}%</span>
    </div>
  )

  /* ─── render ─── */
  if (loading && !data) return <div className="text-gray-500">Loading diagnostics...</div>
  if (error && !data) return <div className="text-red-400">Error: {error}</div>
  if (!data) return <div className="text-gray-500">No data</div>

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-white">Diagnostics</h2>
        <button onClick={refetch} className="px-3 py-1.5 bg-blue-900/60 hover:bg-blue-800/70 border border-blue-800/50 text-blue-300 text-xs rounded transition-colors">
          Refresh
        </button>
      </div>

      {/* KPI row */}
      <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-3">
        <StatCard label="Profile" value={status?.active_profile || '-'} color="text-purple-400" />
        <StatCard label="Regime" value={regime?.global_regime || '-'} color={regime?.global_regime === 'trend' ? 'text-emerald-400' : regime?.global_regime === 'range' ? 'text-yellow-400' : 'text-red-400'} />
        <StatCard label="PnL 24h" value={perf?.pnl_24h?.toFixed(2) ?? '-'} color={pnlColor(perf?.pnl_24h || 0)} sub="USDT" />
        <StatCard label="Win Rate" value={`${perf?.win_rate_last_10?.toFixed(0) ?? '-'}%`} color={(perf?.win_rate_last_10 || 0) >= 50 ? 'text-emerald-400' : 'text-red-400'} sub="last 10" />
        <StatCard label="Drawdown" value={`${perf?.drawdown_intraday?.toFixed(2) ?? '-'}%`} color={(perf?.drawdown_intraday || 0) < 1 ? 'text-emerald-400' : 'text-red-400'} />
        <StatCard label="Consec Loss" value={perf?.consecutive_losses ?? '-'} color={(perf?.consecutive_losses || 0) < 3 ? 'text-emerald-400' : (perf?.consecutive_losses || 0) < 6 ? 'text-yellow-400' : 'text-red-400'} />
        <StatCard label="Trades/h" value={perf?.trades_per_hour?.toFixed(2) ?? '-'} color="text-blue-400" />
        <StatCard label="Blocked" value={totalBlocked} color="text-red-400" />
        <StatCard label="Passed" value={totalPassed} color="text-emerald-400" />
        <StatCard label="Block %" value={`${blockPct.toFixed(0)}%`} color={blockPct > 80 ? 'text-red-400' : blockPct > 50 ? 'text-yellow-400' : 'text-emerald-400'} />
        <StatCard label="Risk Mult" value={guardrails?.risk_multiplier?.toFixed(2) ?? '1.00'} color={(guardrails?.risk_multiplier ?? 1) < 1 ? 'text-yellow-400' : 'text-emerald-400'} />
        <StatCard label="Min Score" value={guardrails?.dynamic_score_min ?? 80} color="text-blue-400" />
      </div>

      {/* Performance timeline */}
      {perfEntries.length > 0 && (
        <section className="bg-gray-900 border border-gray-800 rounded-lg p-4">
          <h3 className="text-sm font-semibold text-gray-400 mb-3">Performance Timeline</h3>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead><tr className="text-gray-500 border-b border-gray-800">
                <th className="text-left py-1 px-2">Time</th><th className="text-right py-1 px-2">PnL 1h</th><th className="text-right py-1 px-2">PnL 6h</th>
                <th className="text-right py-1 px-2">PnL 24h</th><th className="text-right py-1 px-2">WR%</th><th className="text-right py-1 px-2">DD%</th>
                <th className="text-right py-1 px-2">CL</th><th className="text-right py-1 px-2">Trd/h</th>
              </tr></thead>
              <tbody>
                {[...perfEntries].reverse().slice(0, 50).map((e, i) => (
                  <tr key={i} className="border-b border-gray-800/40 hover:bg-gray-800/30">
                    <td className="py-1 px-2 text-gray-500 whitespace-nowrap">{e.ts}</td>
                    <td className={`py-1 px-2 text-right ${pnlColor(e.pnl1h!)}`}>{e.pnl1h?.toFixed(2)}</td>
                    <td className={`py-1 px-2 text-right ${pnlColor(e.pnl6h!)}`}>{e.pnl6h?.toFixed(2)}</td>
                    <td className={`py-1 px-2 text-right font-medium ${pnlColor(e.pnl24h!)}`}>{e.pnl24h?.toFixed(2)}</td>
                    <td className={`py-1 px-2 text-right ${(e.wr!) >= 50 ? 'text-emerald-400' : 'text-red-400'}`}>{e.wr?.toFixed(0)}%</td>
                    <td className={`py-1 px-2 text-right ${(e.dd!) < 1 ? 'text-emerald-400' : 'text-red-400'}`}>{e.dd?.toFixed(2)}%</td>
                    <td className={`py-1 px-2 text-right ${(e.consec!) >= 3 ? 'text-red-400' : 'text-gray-300'}`}>{e.consec}</td>
                    <td className="py-1 px-2 text-right text-gray-400">{e.tph?.toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* Guardrails: block reasons + block by symbol side by side */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Block reasons */}
        <section className="bg-gray-900 border border-gray-800 rounded-lg p-4">
          <h3 className="text-sm font-semibold text-gray-400 mb-3">Blocked by Reason</h3>
          {topEntries(blocksByReason, 12).length > 0 ? (
            <div className="space-y-1">
              {topEntries(blocksByReason, 12).map(([reason, count]) => (
                <div key={reason} className="flex items-center gap-2">
                  <div className="flex-1 h-5 bg-gray-800 rounded overflow-hidden">
                    <div className="h-full bg-red-900/70 rounded" style={{ width: `${count / Math.max(...Object.values(blocksByReason)) * 100}%` }} />
                  </div>
                  <span className="text-[11px] text-gray-400 w-48 truncate text-right" title={reason}>{reason}</span>
                  <span className="text-[11px] font-bold text-red-400 w-8 text-right">{count}</span>
                </div>
              ))}
            </div>
          ) : <p className="text-gray-600 text-xs">No blocks recorded</p>}
        </section>

        {/* Block by symbol */}
        <section className="bg-gray-900 border border-gray-800 rounded-lg p-4">
          <h3 className="text-sm font-semibold text-gray-400 mb-3">Blocked by Symbol</h3>
          {topEntries(blocksBySymbol).length > 0 ? (
            <div className="space-y-1">
              {topEntries(blocksBySymbol).map(([sym, count]) => (
                <div key={sym} className="flex items-center gap-2">
                  <span className="text-xs text-white w-20">{sym}</span>
                  <div className="flex-1 h-5 bg-gray-800 rounded overflow-hidden">
                    <div className="h-full bg-red-900/70 rounded" style={{ width: `${count / Math.max(...Object.values(blocksBySymbol)) * 100}%` }} />
                  </div>
                  <span className="text-[11px] font-bold text-red-400 w-8 text-right">{count}</span>
                </div>
              ))}
            </div>
          ) : <p className="text-gray-600 text-xs">No symbol blocks</p>}
        </section>
      </div>

      {/* Signal Funnel */}
      <section className="bg-gray-900 border border-gray-800 rounded-lg p-4">
        <h3 className="text-sm font-semibold text-gray-400 mb-3">Signal Funnel</h3>
        <FunnelBar label="Signals Generated" value={funnel.generated} color="#3b82f6" />
        <FunnelBar label="Trade Gate Pass" value={funnel.gatePass} color="#8b5cf6" />
        <FunnelBar label="Score Filter Pass" value={funnel.scorePass || funnel.gatePass} color="#d97706" />
        <FunnelBar label="Orders Filled" value={funnel.filled} color="#10b981" />
        <FunnelBar label="Blocked (total)" value={funnel.blocked} color="#ef4444" />
      </section>

      {/* Symbol Diagnostics */}
      <section className="bg-gray-900 border border-gray-800 rounded-lg p-4">
        <h3 className="text-sm font-semibold text-gray-400 mb-3">Symbol Diagnostics</h3>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead><tr className="text-gray-500 border-b border-gray-800">
              <th className="text-left py-1 px-2">Symbol</th><th className="text-left py-1 px-2">Regime</th>
              <th className="text-right py-1 px-2">ADX</th><th className="text-right py-1 px-2">ATR%</th>
              <th className="text-right py-1 px-2">BB%</th><th className="text-right py-1 px-2">Vol</th>
              <th className="text-right py-1 px-2">Signals</th><th className="text-right py-1 px-2">Passed</th>
              <th className="text-right py-1 px-2">Blocked</th><th className="text-right py-1 px-2">Filled</th>
              <th className="text-left py-1 px-2">Top Block</th>
            </tr></thead>
            <tbody>
              {Object.entries(symbolData).sort((a, b) => b[1].blocked - a[1].blocked).map(([sym, d]) => (
                <tr key={sym} className="border-b border-gray-800/40 hover:bg-gray-800/30">
                  <td className="py-1 px-2 font-medium text-white">{sym}</td>
                  <td className="py-1 px-2">{regimeBadge(d.regime)}</td>
                  <td className={`py-1 px-2 text-right ${d.adx >= 25 ? 'text-emerald-400' : d.adx >= 20 ? 'text-yellow-400' : 'text-red-400'}`}>{d.adx.toFixed(1)}</td>
                  <td className="py-1 px-2 text-right text-gray-400">{d.atr.toFixed(2)}</td>
                  <td className="py-1 px-2 text-right text-gray-400">{d.bb.toFixed(2)}</td>
                  <td className={`py-1 px-2 text-right ${d.vol >= 1.5 ? 'text-emerald-400' : 'text-gray-500'}`}>{d.vol.toFixed(1)}</td>
                  <td className="py-1 px-2 text-right">{d.signals}</td>
                  <td className="py-1 px-2 text-right text-emerald-400">{d.passed}</td>
                  <td className="py-1 px-2 text-right text-red-400">{d.blocked}</td>
                  <td className="py-1 px-2 text-right text-blue-400">{d.filled}</td>
                  <td className="py-1 px-2 text-gray-500 text-[10px]">{d.topBlock}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {/* Profile + Diagnosis side by side */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Profile & Advisor */}
        <section className="bg-gray-900 border border-gray-800 rounded-lg p-4">
          <h3 className="text-sm font-semibold text-gray-400 mb-3">Profile & Advisor</h3>
          <div className="grid grid-cols-3 gap-2 mb-3">
            <div className="bg-gray-800 rounded p-2"><p className="text-[10px] text-gray-500 uppercase">Active</p><p className="text-sm font-bold text-purple-400">{status?.active_profile || '-'}</p></div>
            <div className="bg-gray-800 rounded p-2"><p className="text-[10px] text-gray-500 uppercase">Suggested</p><p className="text-sm font-bold text-blue-400">{advisor?.suggested_profile || '-'}</p></div>
            <div className="bg-gray-800 rounded p-2"><p className="text-[10px] text-gray-500 uppercase">Confidence</p><p className="text-sm font-bold text-gray-300">{advisor?.confidence ?? '-'}%</p></div>
          </div>
          {advisor?.explanation && <p className="text-[11px] text-gray-500 bg-gray-800 rounded p-2 mb-3">{advisor.explanation}</p>}
          {profileChanges.length > 0 && (
            <div>
              <p className="text-[10px] text-gray-600 uppercase font-semibold mb-1">Profile Changes</p>
              {profileChanges.slice(-5).reverse().map((e, i) => (
                <div key={i} className="flex items-center gap-2 text-xs py-0.5">
                  <span className="text-gray-600 text-[10px]">{e.ts}</span>
                  {badge(e.from || '', 'bg-gray-800 text-gray-400')}
                  <span className="text-gray-600">→</span>
                  {badge(e.to || '', 'bg-purple-900/60 text-purple-300')}
                </div>
              ))}
            </div>
          )}
        </section>

        {/* Automated Diagnosis */}
        <section className="bg-gray-900 border border-gray-800 rounded-lg p-4">
          <h3 className="text-sm font-semibold text-gray-400 mb-3">Automated Diagnosis</h3>
          <div className="space-y-2">
            {diagnosis.map((d, i) => (
              <div key={i} className={`p-2 rounded border-l-2 ${
                d.level === 'crit' ? 'bg-red-900/10 border-red-500' :
                d.level === 'warn' ? 'bg-yellow-900/10 border-yellow-500' :
                'bg-emerald-900/10 border-emerald-500'
              }`}>
                <p className="text-xs font-semibold text-gray-200">{d.title}</p>
                <p className="text-[11px] text-gray-500">{d.desc}</p>
              </div>
            ))}
          </div>
        </section>
      </div>

      {/* Event Log */}
      <section className="bg-gray-900 border border-gray-800 rounded-lg p-4">
        <h3 className="text-sm font-semibold text-gray-400 mb-3">Event Log <span className="text-gray-600 font-normal">({events.length} events)</span></h3>
        <div className="flex gap-2 flex-wrap mb-3">
          <input type="text" value={evSearch} onChange={e => setEvSearch(e.target.value)} placeholder="Search..."
            className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs text-gray-300 w-48" />
          <select value={evType} onChange={e => setEvType(e.target.value)}
            className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs text-gray-300">
            <option value="">All types</option>
            {evTypes.map(t => <option key={t} value={t}>{t}</option>)}
          </select>
          <select value={evSymbol} onChange={e => setEvSymbol(e.target.value)}
            className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs text-gray-300">
            <option value="">All symbols</option>
            {evSymbols.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
        </div>
        <div className="overflow-x-auto max-h-96 overflow-y-auto">
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-gray-900"><tr className="text-gray-500 border-b border-gray-800">
              <th className="text-left py-1 px-2">Time</th><th className="text-left py-1 px-2">Type</th>
              <th className="text-left py-1 px-2">Symbol</th><th className="text-left py-1 px-2">Level</th>
              <th className="text-left py-1 px-2">Detail</th>
            </tr></thead>
            <tbody>
              {filteredEvents.map((e, i) => (
                <tr key={i} className="border-b border-gray-800/30 hover:bg-gray-800/20">
                  <td className="py-1 px-2 text-gray-600 whitespace-nowrap">{e.ts || '-'}</td>
                  <td className="py-1 px-2"><span className="px-1.5 py-0.5 rounded bg-gray-800 text-gray-500 text-[10px]">{e.type}</span></td>
                  <td className="py-1 px-2 text-white">{e.symbol || '-'}</td>
                  <td className="py-1 px-2">{levelBadge(e.level)}</td>
                  <td className="py-1 px-2 text-gray-400 text-[11px]">{eventText(e)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  )
}
