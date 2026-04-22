import { useCallback, useState } from 'react'
import { api, Balance, Position, TradeItem, EngineStatus, AdaptiveStatus, NewsSentiment, ApprovalRequestItem } from '../api'
import { usePolling } from '../hooks/usePolling'
import { useLang } from '../hooks/useLang'
import { useAuth } from '../hooks/useAuth'
import StatCard from '../components/StatCard'
import Badge from '../components/Badge'
import Modal from '../components/Modal'
import AdaptiveStatusBar from '../components/dashboard/AdaptiveStatusBar'
import MarketSentimentPanel from '../components/dashboard/MarketSentimentPanel'
import PositionsTable from '../components/dashboard/PositionsTable'
import TradeHistoryTable from '../components/dashboard/TradeHistoryTable'
import { pnlColor } from '../utils/colors'
import { SkeletonCard } from '../components/Skeleton'

export default function Dashboard() {
  const { t, l } = useLang()
  const { isAdmin } = useAuth()

  const fetchBalance = useCallback(() => api.getBalance(), [])
  const fetchPositions = useCallback(() => api.getPositions(), [])
  const fetchTrades = useCallback(() => api.getTrades(), [])
  const fetchEngine = useCallback(() => api.getEngineStatus(), [])
  const fetchAdaptive = useCallback(() => api.getAdaptiveStatus(), [])
  const fetchSentiment = useCallback(() => api.getNewsSentiment(), [])
  const fetchPendingApprovals = useCallback(
    () => (isAdmin ? api.getPendingApprovals() : Promise.resolve([] as ApprovalRequestItem[])),
    [isAdmin]
  )

  const [balance, , , refetchBalance] = usePolling<Balance>(fetchBalance, 5000)
  const [positions, loadingPositions, , refetchPositions] = usePolling<Position[]>(fetchPositions, 5000)
  const [trades, loadingTrades, , refetchTrades] = usePolling<TradeItem[]>(fetchTrades, 10000)
  const [engine] = usePolling<EngineStatus>(fetchEngine, 5000)
  const [adaptive] = usePolling<AdaptiveStatus>(fetchAdaptive, 15000)
  const [sentiment] = usePolling<NewsSentiment>(fetchSentiment, 60000)
  const [pendingApprovals] = usePolling<ApprovalRequestItem[]>(fetchPendingApprovals, 15000)

  const dataMode = balance?.mode || 'paper'

  // -- Close position flow --
  const [closingId, setClosingId] = useState<number | null>(null)
  const [pendingClose, setPendingClose] = useState<Position | null>(null)

  const confirmClosePosition = async () => {
    if (!pendingClose) return
    setClosingId(pendingClose.id)
    setPendingClose(null)
    setActionError('')
    try {
      await api.closePosition(pendingClose.id)
      refetchPositions()
      refetchTrades()
      refetchBalance()
    } catch (e) {
      setActionError(`${l('Chiusura fallita', 'Close failed')}: ${e}`)
    } finally {
      setClosingId(null)
    }
  }

  // -- Reset / export flow --
  const [showResetConfirm, setShowResetConfirm] = useState(false)
  const [actionError, setActionError] = useState('')

  const confirmReset = async () => {
    setShowResetConfirm(false)
    setActionError('')
    try {
      await api.resetPaperPortfolio()
      refetchBalance()
      refetchPositions()
      refetchTrades()
    } catch (e) {
      setActionError(`${t('reset_failed')}: ${e}`)
    }
  }

  const handleExport = async () => {
    setActionError('')
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
      setActionError(`${t('export_failed')}: ${e}`)
    }
  }

  return (
    <div className="space-y-6">
      {/* Action error banner */}
      {actionError && (
        <div role="alert" className="bg-red-900/30 border border-red-800/50 text-red-300 px-4 py-2 rounded text-sm">
          {actionError}
        </div>
      )}

      {/* Close position confirm modal */}
      <Modal
        open={!!pendingClose}
        onClose={() => setPendingClose(null)}
        title={l('Conferma chiusura posizione', 'Confirm position close')}
        actions={
          <>
            <button onClick={() => setPendingClose(null)}
              className="px-3 py-2 bg-gray-800 hover:bg-gray-700 text-gray-300 text-sm rounded-lg transition-colors">
              {l('Annulla', 'Cancel')}
            </button>
            <button onClick={confirmClosePosition}
              className="px-4 py-2 bg-red-700 hover:bg-red-600 text-white text-sm font-medium rounded-lg transition-colors">
              {l('Chiudi posizione', 'Close position')}
            </button>
          </>
        }
      >
        {pendingClose && (
          <p className="text-sm text-gray-400">
            {l(
              `Chiudere ${pendingClose.symbol} (${pendingClose.quantity.toFixed(6)}) al prezzo corrente di mercato?`,
              `Close ${pendingClose.symbol} (${pendingClose.quantity.toFixed(6)}) at current market price?`
            )}
          </p>
        )}
      </Modal>

      {/* Reset portfolio confirm modal */}
      <Modal
        open={showResetConfirm}
        onClose={() => setShowResetConfirm(false)}
        title={l('Conferma reset portfolio', 'Confirm portfolio reset')}
        actions={
          <>
            <button onClick={() => setShowResetConfirm(false)}
              className="px-3 py-2 bg-gray-800 hover:bg-gray-700 text-gray-300 text-sm rounded-lg transition-colors">
              {l('Annulla', 'Cancel')}
            </button>
            <button onClick={confirmReset}
              className="px-4 py-2 bg-yellow-700 hover:bg-yellow-600 text-white text-sm font-medium rounded-lg transition-colors">
              {l('Reset', 'Reset')}
            </button>
          </>
        }
      >
        <p className="text-sm text-gray-400">{t('reset_confirm')}</p>
      </Modal>

      {/* Data mode indicator */}
      <div className="flex items-center gap-2">
        <Badge variant={dataMode === 'live' ? 'danger' : 'success'}>
          {dataMode === 'live' ? 'Live' : 'Paper'}
        </Badge>
        <span className="text-xs text-gray-500">
          {dataMode === 'live'
            ? l('Dati reali dal tuo conto Binance', 'Real data from your Binance account')
            : l('Dati da Binance Testnet', 'Data from Binance Testnet')
          }
        </span>
      </div>

      {/* Adaptive Layer Status */}
      {adaptive && (
        <AdaptiveStatusBar
          adaptive={adaptive}
          pendingApprovalsCount={pendingApprovals?.length || 0}
          isAdmin={isAdmin}
        />
      )}

      {/* Market Sentiment */}
      {sentiment && <MarketSentimentPanel sentiment={sentiment} />}

      {/* KPI cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        {!balance ? (
          <>
            <SkeletonCard />
            <SkeletonCard />
            <SkeletonCard />
            <SkeletonCard />
          </>
        ) : (
          <>
            <StatCard label={t('cash_balance')} value={balance.cash_balance} sub="USDT" />
            <StatCard label={t('total_equity')} value={balance.total_equity} sub="USDT" />
            <StatCard
              label={t('total_pnl')}
              value={balance.total_pnl}
              color={pnlColor(balance.total_pnl)}
              sub="USDT"
            />
            <StatCard
              label={t('win_rate')}
              value={
                balance.total_trades > 0
                  ? `${((balance.winning_trades / balance.total_trades) * 100).toFixed(1)}%`
                  : 'N/A'
              }
              sub={`${balance.winning_trades}${t('win_short')} / ${balance.losing_trades}${t('loss_short')}`}
            />
          </>
        )}
      </div>

      {/* Engine status + prices */}
      <div className="space-y-2">
        <div className="flex flex-wrap gap-4 items-center text-sm text-gray-400">
          <span>{t('engine')}: {engine?.running ? t('running') : t('stopped')}</span>
          {dataMode === 'paper' && (
            <>
              <button onClick={() => setShowResetConfirm(true)}
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
      <PositionsTable
        positions={positions ?? []}
        closingId={closingId}
        onClose={setPendingClose}
        loading={loadingPositions && !positions}
      />

      {/* Trade history */}
      <TradeHistoryTable
        trades={trades ?? []}
        loading={loadingTrades && !trades}
      />
    </div>
  )
}
