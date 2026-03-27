import { useCallback, useState } from 'react'
import { api, StrategyInfo, RiskParams } from '../api'
import { usePolling } from '../hooks/usePolling'
import { useLang } from '../hooks/useLang'

export default function Strategies() {
  const { t } = useLang()

  const fetchStrategies = useCallback(() => api.getStrategies(), [])
  const fetchRisk = useCallback(() => api.getRisk(), [])

  const [strategies, , , refetchStrats] = usePolling<StrategyInfo[]>(fetchStrategies, 10000)
  const [risk, , , refetchRisk] = usePolling<RiskParams>(fetchRisk, 10000)

  const [riskForm, setRiskForm] = useState<RiskParams | null>(null)

  const toggleStrategy = async (name: string, enabled: boolean) => {
    try {
      await api.updateStrategy({ name, enabled: !enabled })
      refetchStrats()
    } catch (e) {
      alert(`${t('update_failed')}: ${e}`)
    }
  }

  const updateParam = async (name: string, paramKey: string, value: string) => {
    const numVal = parseFloat(value)
    if (isNaN(numVal)) return
    try {
      await api.updateStrategy({ name, params: { [paramKey]: numVal } })
      refetchStrats()
    } catch (e) {
      alert(`${t('update_failed')}: ${e}`)
    }
  }

  const saveRisk = async () => {
    if (!riskForm) return
    try {
      await api.updateRisk(riskForm)
      setRiskForm(null)
      refetchRisk()
    } catch (e) {
      alert(`${t('update_failed')}: ${e}`)
    }
  }

  const currentRisk = riskForm || risk

  return (
    <div className="space-y-8">
      {/* Strategies section */}
      <section>
        <h2 className="text-lg font-semibold text-white mb-4">{t('trading_strategies')}</h2>
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {strategies?.map((s) => (
            <div key={s.name} className="bg-gray-900 border border-gray-800 rounded-lg p-4">
              <div className="flex items-center justify-between mb-3">
                <h3 className="font-medium text-white capitalize">
                  {s.name.replace(/_/g, ' ')}
                </h3>
                <button
                  onClick={() => toggleStrategy(s.name, s.enabled)}
                  className={`px-3 py-1 rounded-full text-xs font-bold transition-colors ${
                    s.enabled
                      ? 'bg-emerald-600 text-white hover:bg-emerald-700'
                      : 'bg-gray-700 text-gray-400 hover:bg-gray-600'
                  }`}
                >
                  {s.enabled ? 'ON' : 'OFF'}
                </button>
              </div>

              <div className="space-y-2">
                {Object.entries(s.params)
                  .filter(([k]) => k !== 'enabled')
                  .map(([key, val]) => (
                    <div key={key} className="flex items-center gap-2">
                      <label className="text-xs text-gray-400 w-28 truncate" title={key}>
                        {key}
                      </label>
                      <input
                        type="number"
                        defaultValue={String(val)}
                        onBlur={(e) => updateParam(s.name, key, e.target.value)}
                        className="flex-1 bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-white focus:outline-none focus:border-blue-500"
                      />
                    </div>
                  ))}
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* Risk Management section */}
      <section>
        <h2 className="text-lg font-semibold text-white mb-4">{t('risk_management')}</h2>
        {currentRisk && (
          <div className="bg-gray-900 border border-gray-800 rounded-lg p-4 max-w-md space-y-3">
            <div className="flex items-center gap-2">
              <label className="text-sm text-gray-400 w-52">{t('max_position_size')}</label>
              <input
                type="number"
                step="0.5"
                value={currentRisk.max_position_pct}
                onChange={(e) =>
                  setRiskForm({
                    ...currentRisk,
                    max_position_pct: parseFloat(e.target.value) || 0,
                  })
                }
                className="flex-1 bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-white focus:outline-none focus:border-blue-500"
              />
            </div>
            <div className="flex items-center gap-2">
              <label className="text-sm text-gray-400 w-52">{t('default_stop_loss')}</label>
              <input
                type="number"
                step="0.5"
                value={currentRisk.default_sl_pct}
                onChange={(e) =>
                  setRiskForm({
                    ...currentRisk,
                    default_sl_pct: parseFloat(e.target.value) || 0,
                  })
                }
                className="flex-1 bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-white focus:outline-none focus:border-blue-500"
              />
            </div>
            <div className="flex items-center gap-2">
              <label className="text-sm text-gray-400 w-52">{t('default_take_profit')}</label>
              <input
                type="number"
                step="0.5"
                value={currentRisk.default_tp_pct}
                onChange={(e) =>
                  setRiskForm({
                    ...currentRisk,
                    default_tp_pct: parseFloat(e.target.value) || 0,
                  })
                }
                className="flex-1 bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-white focus:outline-none focus:border-blue-500"
              />
            </div>
            {riskForm && (
              <button
                onClick={saveRisk}
                className="mt-2 px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white text-sm rounded transition-colors"
              >
                {t('save_risk')}
              </button>
            )}
          </div>
        )}
      </section>
    </div>
  )
}
