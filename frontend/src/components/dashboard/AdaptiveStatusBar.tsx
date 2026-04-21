import { AdaptiveStatus } from '../../api'
import { useLang } from '../../hooks/useLang'
import { pnlColor, profileBadgeClass, regimeBadgeClass } from '../../utils/colors'

interface Props {
  adaptive: AdaptiveStatus
}

export default function AdaptiveStatusBar({ adaptive }: Props) {
  const { l } = useLang()

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2">
          <span className="text-xs text-gray-500 uppercase tracking-wider">{l('Profilo', 'Profile')}</span>
          <span className={`px-2.5 py-1 rounded-full text-xs font-bold uppercase tracking-wider ${profileBadgeClass(adaptive.active_profile)}`}>
            {adaptive.active_profile}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs text-gray-500 uppercase tracking-wider">{l('Regime', 'Regime')}</span>
          <span className={`px-2.5 py-1 rounded-full text-xs font-bold uppercase tracking-wider ${regimeBadgeClass(adaptive.regime.global_regime)}`}>
            {adaptive.regime.global_regime}
          </span>
        </div>
        <div className="flex items-center gap-4 ml-auto text-xs">
          <span className={pnlColor(adaptive.performance.pnl_6h)}>
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
  )
}
