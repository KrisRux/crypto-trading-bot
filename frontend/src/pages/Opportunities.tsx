import { useCallback } from 'react'
import { api, OpportunitiesResponse, OpportunityItem } from '../api'
import { usePolling } from '../hooks/usePolling'
import { useLang } from '../hooks/useLang'

const postureClass: Record<string, string> = {
  ATTACK: 'bg-emerald-900/50 text-emerald-300 border-emerald-800/60',
  STALK: 'bg-blue-900/50 text-blue-300 border-blue-800/60',
  WAIT: 'bg-gray-800 text-gray-300 border-gray-700',
  PAUSED: 'bg-red-900/50 text-red-300 border-red-800/60',
}

const actionClass: Record<string, string> = {
  ATTACK: 'bg-emerald-900/60 text-emerald-300',
  SHORT_ATTACK: 'bg-red-900/60 text-red-300',
  SHORT_WATCH: 'bg-orange-900/60 text-orange-300',
  WATCH_BREAKOUT: 'bg-blue-900/60 text-blue-300',
  WATCH_REVERSAL: 'bg-yellow-900/60 text-yellow-300',
  HOLD_MANAGE: 'bg-purple-900/60 text-purple-300',
  AVOID: 'bg-gray-800 text-gray-400',
}

function scoreColor(score: number) {
  if (score >= 75) return 'text-emerald-300'
  if (score >= 62) return 'text-blue-300'
  if (score >= 48) return 'text-yellow-300'
  return 'text-gray-500'
}

function pnlColor(value: number) {
  if (value > 0) return 'text-emerald-300'
  if (value < 0) return 'text-red-300'
  return 'text-gray-400'
}

function SetupLabel({ item }: { item: OpportunityItem }) {
  const label = item.setup.replace(/_/g, ' ')
  return <span className="text-xs text-gray-400 capitalize">{label}</span>
}

function OpportunityRow({ item }: { item: OpportunityItem }) {
  return (
    <tr className="border-b border-gray-800/80 hover:bg-gray-900/60">
      <td className="py-3 px-3">
        <div className="flex items-center gap-2">
          <span className="font-mono text-sm font-semibold text-white">{item.symbol}</span>
          <span className={`text-[10px] px-1.5 py-0.5 rounded ${item.side === 'SHORT' ? 'bg-red-950 text-red-300' : 'bg-emerald-950 text-emerald-300'}`}>{item.side}</span>
          {!item.active && <span className="text-[10px] px-1.5 py-0.5 rounded bg-gray-800 text-gray-500">off</span>}
        </div>
        <SetupLabel item={item} />
      </td>
      <td className="py-3 px-3 text-right">
        <span className={`font-mono text-lg font-semibold ${scoreColor(item.score)}`}>{item.score.toFixed(0)}</span>
      </td>
      <td className="py-3 px-3">
        <span className={`text-[10px] font-bold px-2 py-1 rounded uppercase ${actionClass[item.action] || actionClass.AVOID}`}>
          {item.action.replace(/_/g, ' ')}
        </span>
      </td>
      <td className="py-3 px-3 text-xs text-gray-300">
        <div className="font-mono">{item.regime}</div>
        <div className="text-gray-600">ADX {item.adx.toFixed(1)}</div>
      </td>
      <td className="py-3 px-3 text-xs font-mono text-gray-300 text-right">
        <div>{item.volume_ratio.toFixed(2)}x</div>
        <div className="text-gray-600">ATR {item.atr_pct.toFixed(2)}%</div>
      </td>
      <td className="py-3 px-3 text-xs font-mono text-right">
        <div className={pnlColor(item.recent_net_pnl)}>{item.recent_net_pnl.toFixed(2)}</div>
        <div className="text-gray-600">WR {item.recent_win_rate.toFixed(0)}%</div>
      </td>
      <td className="py-3 px-3 text-xs text-gray-400">
        <div className="space-y-1">
          {item.reasons.slice(0, 2).map((r, i) => <div key={i}>{r}</div>)}
          {item.blockers.slice(0, 2).map((b, i) => <div key={`b-${i}`} className="text-red-300/80">{b}</div>)}
        </div>
      </td>
    </tr>
  )
}

export default function Opportunities() {
  const { l } = useLang()
  const fetchOpportunities = useCallback(() => api.getOpportunities(), [])
  const [data, loading, error] = usePolling<OpportunitiesResponse>(fetchOpportunities, 15000)

  if (loading && !data) {
    return <div className="text-gray-500 text-sm">{l('Caricamento...', 'Loading...')}</div>
  }

  if (error) {
    return <div className="text-red-300 text-sm">{String(error)}</div>
  }

  const opportunities = data?.opportunities || []
  const top = opportunities[0]

  return (
    <div className="space-y-5">
      <div className="flex flex-col sm:flex-row sm:items-end sm:justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-white">Opportunity Scanner</h2>
          <p className="text-sm text-gray-500">{l('Ranking operativo delle occasioni long e short paper', 'Operational ranking of long and paper-short opportunities')}</p>
        </div>
        {data && (
          <div className={`inline-flex items-center gap-2 border rounded-lg px-3 py-2 ${postureClass[data.posture] || postureClass.WAIT}`}>
            <span className="text-xs font-bold uppercase">{data.posture}</span>
            <span className="text-xs opacity-80">{data.global_regime}</span>
          </div>
        )}
      </div>

      {data && (
        <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
          <div className="bg-gray-900 border border-gray-800 rounded-lg p-4 md:col-span-2">
            <div className="text-xs text-gray-500 mb-1">{l('Sintesi', 'Summary')}</div>
            <div className="text-sm text-gray-200">{data.summary}</div>
          </div>
          <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
            <div className="text-xs text-gray-500 mb-1">Top Symbol</div>
            <div className="text-xl font-mono font-semibold text-white">{data.top_symbol || '-'}</div>
            {top && <div className="text-xs text-gray-500">{top.action.replace(/_/g, ' ')}</div>}
          </div>
          <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
            <div className="text-xs text-gray-500 mb-1">News</div>
            <div className={data.news?.score && data.news.score < -0.1 ? 'text-red-300' : 'text-gray-200'}>
              {data.news?.label || '-'} {data.news?.fear_greed_value ? `F&G ${data.news.fear_greed_value}` : ''}
            </div>
            <div className="text-xs text-gray-500">{data.news?.headline_count || 0} headlines</div>
          </div>
        </div>
      )}

      <div className="bg-gray-900 border border-gray-800 rounded-lg overflow-hidden">
        <div className="px-4 py-3 border-b border-gray-800 flex items-center justify-between">
          <h3 className="text-sm font-semibold text-white">{l('Opportunita', 'Opportunities')}</h3>
          <span className="text-xs text-gray-600">{opportunities.length} symbols</span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[900px]">
            <thead className="bg-gray-950/60 text-[10px] uppercase text-gray-500">
              <tr>
                <th className="text-left py-2 px-3">Symbol</th>
                <th className="text-right py-2 px-3">Score</th>
                <th className="text-left py-2 px-3">Action</th>
                <th className="text-left py-2 px-3">Regime</th>
                <th className="text-right py-2 px-3">Market</th>
                <th className="text-right py-2 px-3">7d Net</th>
                <th className="text-left py-2 px-3">Read</th>
              </tr>
            </thead>
            <tbody>
              {opportunities.map(item => <OpportunityRow key={item.symbol} item={item} />)}
            </tbody>
          </table>
        </div>
      </div>

      {data?.news?.top_headlines?.length ? (
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
          <h3 className="text-sm font-semibold text-white mb-3">News Tape</h3>
          <div className="space-y-2">
            {data.news.top_headlines.slice(0, 5).map((h, i) => (
              <div key={i} className="flex items-center gap-3 text-xs">
                <span className={`font-mono w-12 text-right ${h.sentiment < -0.1 ? 'text-red-300' : h.sentiment > 0.1 ? 'text-emerald-300' : 'text-gray-500'}`}>
                  {h.sentiment > 0 ? '+' : ''}{h.sentiment.toFixed(2)}
                </span>
                <span className="text-gray-300 flex-1">{h.title}</span>
                <span className="text-gray-600 hidden sm:inline">{h.source}</span>
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  )
}
