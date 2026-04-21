import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, StrategyInfo, RiskParams } from '../api'
import { usePolling } from '../hooks/usePolling'
import { useLang } from '../hooks/useLang'
import StatCard from '../components/StatCard'
import Modal from '../components/Modal'

/* ═══════════════════════════════════════════════════════
   Param hints — human-readable labels and ranges
   ═══════════════════════════════════════════════════════ */

const PARAM_META: Record<string, { label: string; hint: string; min?: number; max?: number; step?: number }> = {
  // SMA
  fast_period:     { label: 'Fast Period',     hint: 'Fast SMA window (bars)',       min: 2,  max: 200, step: 1 },
  slow_period:     { label: 'Slow Period',     hint: 'Slow SMA window (bars)',       min: 5,  max: 500, step: 1 },
  adx_period:      { label: 'ADX Period',      hint: 'ADX calculation window',       min: 5,  max: 50,  step: 1 },
  adx_threshold:   { label: 'ADX Threshold',   hint: 'Min ADX to generate signals',  min: 5,  max: 60,  step: 1 },
  // RSI
  period:          { label: 'Period',          hint: 'RSI / indicator period (bars)', min: 2,  max: 100, step: 1 },
  oversold:        { label: 'Oversold',        hint: 'RSI oversold threshold',        min: 5,  max: 45,  step: 1 },
  overbought:      { label: 'Overbought',      hint: 'RSI overbought threshold',      min: 55, max: 95,  step: 1 },
  // MACD
  fast:            { label: 'Fast EMA',        hint: 'MACD fast EMA period',         min: 2,  max: 50,  step: 1 },
  slow:            { label: 'Slow EMA',        hint: 'MACD slow EMA period',         min: 5,  max: 100, step: 1 },
  signal:          { label: 'Signal Line',     hint: 'MACD signal line period',      min: 2,  max: 50,  step: 1 },
  // Embient
  trend_buy_threshold:   { label: 'Trend BUY Threshold',   hint: 'Min score to BUY in trend',    min: 0, max: 100, step: 1 },
  trend_sell_threshold:  { label: 'Trend SELL Threshold',  hint: 'Min score to SELL in trend',   min: 0, max: 100, step: 1 },
  range_buy_threshold:   { label: 'Range BUY Threshold',   hint: 'Min score to BUY in range',    min: 0, max: 100, step: 1 },
  range_sell_threshold:  { label: 'Range SELL Threshold',  hint: 'Min score to SELL in range',   min: 0, max: 100, step: 1 },
  neutral_sell_threshold:{ label: 'Neutral SELL Threshold', hint: 'Min score to SELL in neutral', min: 0, max: 100, step: 1 },
}

const RISK_FIELDS: { key: keyof RiskParams; label: string; hint: string; min: number; max: number; step: number }[] = [
  { key: 'max_position_pct', label: 'Max Position Size %', hint: 'Max % of capital per position', min: 0.1, max: 10, step: 0.1 },
  { key: 'default_sl_pct',   label: 'Default Stop Loss %', hint: '% below entry price',           min: 0.5, max: 20, step: 0.5 },
  { key: 'default_tp_pct',   label: 'Default Take Profit %', hint: '% above entry price',         min: 0.5, max: 30, step: 0.5 },
]

const STRATEGY_ICONS: Record<string, string> = {
  embient_enhanced: 'E',
  sma_crossover: 'S',
  macd_crossover: 'M',
  rsi_reversal: 'R',
}

/* ═══════════════════════════════════════════════════════
   COMPONENT
   ═══════════════════════════════════════════════════════ */

export default function Strategies() {
  const { lang, t, l } = useLang()

  /* data */
  const fetchStrategies = useCallback(() => api.getStrategies(), [])
  const fetchRisk = useCallback(() => api.getRisk(), [])
  const [strategies, loadingStrats] = usePolling<StrategyInfo[]>(fetchStrategies, 10000)
  const [risk, loadingRisk] = usePolling<RiskParams>(fetchRisk, 10000)

  /* draft state */
  const [draftStrats, setDraftStrats] = useState<Record<string, { enabled: boolean; params: Record<string, unknown> }>>({})
  const [savedStrats, setSavedStrats] = useState<Record<string, { enabled: boolean; params: Record<string, unknown> }>>({})
  const [draftRisk, setDraftRisk] = useState<RiskParams | null>(null)
  const [savedRisk, setSavedRisk] = useState<RiskParams | null>(null)

  /* UI state */
  const [expandedSections, setExpandedSections] = useState<Set<string>>(new Set())
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')
  const [showConfirm, setShowConfirm] = useState(false)

  /* init drafts from fetched data */
  useEffect(() => {
    if (strategies && Object.keys(draftStrats).length === 0) {
      const map: typeof draftStrats = {}
      for (const s of strategies) {
        map[s.name] = { enabled: s.enabled, params: { ...s.params } }
      }
      setDraftStrats(map)
      setSavedStrats(structuredClone(map))
      setExpandedSections(new Set(strategies.map(s => s.name)))
    }
  }, [strategies])

  useEffect(() => {
    if (risk && !draftRisk) {
      setDraftRisk({ ...risk })
      setSavedRisk({ ...risk })
    }
  }, [risk])

  /* dirty check */
  const isDirty = useMemo(() => {
    if (JSON.stringify(draftStrats) !== JSON.stringify(savedStrats)) return true
    if (draftRisk && savedRisk && JSON.stringify(draftRisk) !== JSON.stringify(savedRisk)) return true
    return false
  }, [draftStrats, savedStrats, draftRisk, savedRisk])

  /* compute diffs */
  const diffs = useMemo(() => {
    const out: { path: string; from: unknown; to: unknown }[] = []
    for (const [name, draft] of Object.entries(draftStrats)) {
      const saved = savedStrats[name]
      if (!saved) continue
      if (draft.enabled !== saved.enabled) {
        out.push({ path: `${name}.enabled`, from: saved.enabled ? 'ON' : 'OFF', to: draft.enabled ? 'ON' : 'OFF' })
      }
      for (const [k, v] of Object.entries(draft.params)) {
        if (k === 'enabled') continue
        if (JSON.stringify(v) !== JSON.stringify(saved.params[k])) {
          out.push({ path: `${name}.${k}`, from: saved.params[k], to: v })
        }
      }
    }
    if (draftRisk && savedRisk) {
      for (const f of RISK_FIELDS) {
        if (draftRisk[f.key] !== savedRisk[f.key]) {
          out.push({ path: `risk.${f.key}`, from: savedRisk[f.key], to: draftRisk[f.key] })
        }
      }
    }
    return out
  }, [draftStrats, savedStrats, draftRisk, savedRisk])

  /* handlers */
  const toggleSection = (id: string) => {
    setExpandedSections(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id); else next.add(id)
      return next
    })
  }

  const setStratParam = (name: string, key: string, value: unknown) => {
    setDraftStrats(prev => ({
      ...prev,
      [name]: { ...prev[name], params: { ...prev[name].params, [key]: value } },
    }))
  }

  const toggleEnabled = (name: string) => {
    setDraftStrats(prev => ({
      ...prev,
      [name]: { ...prev[name], enabled: !prev[name].enabled },
    }))
  }

  const handleSave = async () => {
    setShowConfirm(false)
    setSaving(true)
    setError('')
    setSuccess('')
    try {
      // Save each strategy that changed
      for (const [name, draft] of Object.entries(draftStrats)) {
        const saved = savedStrats[name]
        if (!saved || JSON.stringify(draft) === JSON.stringify(saved)) continue
        await api.updateStrategy({
          name,
          enabled: draft.enabled,
          params: Object.fromEntries(
            Object.entries(draft.params).filter(([k]) => k !== 'enabled')
          ),
        })
      }
      // Save risk if changed
      if (draftRisk && savedRisk && JSON.stringify(draftRisk) !== JSON.stringify(savedRisk)) {
        await api.updateRisk(draftRisk)
      }
      // Update saved snapshots
      setSavedStrats(structuredClone(draftStrats))
      setSavedRisk(draftRisk ? { ...draftRisk } : null)
      setSuccess(l('Configurazione salvata', 'Configuration saved'))
      setTimeout(() => setSuccess(''), 4000)
    } catch (e) {
      setError(String(e))
    } finally {
      setSaving(false)
    }
  }

  const handleDiscard = () => {
    setDraftStrats(structuredClone(savedStrats))
    setDraftRisk(savedRisk ? { ...savedRisk } : null)
    setError('')
  }

  /* render */
  if ((loadingStrats || loadingRisk) && !strategies) {
    return <div className="text-gray-500 text-sm">{l('Caricamento...', 'Loading...')}</div>
  }

  const activeCount = Object.values(draftStrats).filter(s => s.enabled).length
  const totalCount = Object.keys(draftStrats).length

  return (
    <div className="space-y-5">
      {/* Header */}
      <div>
        <h2 className="text-lg font-semibold text-white">{t('trading_strategies')}</h2>
        <p className="text-sm text-gray-500">{l('Configurazione strategie di trading e parametri di rischio', 'Trading strategies and risk parameters configuration')}</p>
      </div>

      {/* KPI */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <StatCard label={l('Strategie Attive', 'Active Strategies')} value={`${activeCount}/${totalCount}`} color="text-emerald-400" />
        <StatCard label={l('Posizione Max', 'Max Position')} value={`${draftRisk?.max_position_pct ?? '-'}%`} color="text-blue-400" />
        <StatCard label="Stop Loss" value={`${draftRisk?.default_sl_pct ?? '-'}%`} color="text-red-400" />
        <StatCard label="Take Profit" value={`${draftRisk?.default_tp_pct ?? '-'}%`} color="text-emerald-400" />
      </div>

      {/* Messages */}
      {error && <div className="bg-red-900/30 border border-red-800/50 text-red-300 px-4 py-2 rounded text-sm">{error}</div>}
      {success && <div className="bg-emerald-900/30 border border-emerald-800/50 text-emerald-300 px-4 py-2 rounded text-sm">{success}</div>}

      {/* Strategy sections */}
      {Object.entries(draftStrats).map(([name, draft]) => {
        const saved = savedStrats[name]
        const isExpanded = expandedSections.has(name)
        const sectionDirty = saved && JSON.stringify(draft) !== JSON.stringify(saved)
        const icon = STRATEGY_ICONS[name] || name[0].toUpperCase()
        const paramEntries = Object.entries(draft.params).filter(([k]) => k !== 'enabled')

        return (
          <div key={name} className="bg-gray-900 border border-gray-800 rounded-lg overflow-hidden">
            {/* Section header */}
            <button onClick={() => toggleSection(name)}
              className="w-full flex items-center justify-between px-4 py-3 text-left hover:bg-gray-800/50 transition-colors">
              <div className="flex items-center gap-3">
                <span className="w-7 h-7 rounded bg-gray-800 flex items-center justify-center text-xs font-bold text-gray-400">{icon}</span>
                <span className="text-sm font-semibold text-white capitalize">{name.replace(/_/g, ' ')}</span>
                {sectionDirty && (
                  <span className="px-1.5 py-0.5 rounded text-[9px] font-bold bg-yellow-900/60 text-yellow-300 uppercase">modified</span>
                )}
              </div>
              <div className="flex items-center gap-3">
                {/* ON/OFF toggle */}
                <div
                  role="switch"
                  aria-checked={draft.enabled}
                  aria-label={l('Abilita strategia', 'Enable strategy')}
                  tabIndex={0}
                  onClick={e => { e.stopPropagation(); toggleEnabled(name) }}
                  onKeyDown={e => { if (e.key === ' ' || e.key === 'Enter') { e.preventDefault(); e.stopPropagation(); toggleEnabled(name) } }}
                  className={`w-10 h-5 rounded-full transition-colors relative cursor-pointer ${draft.enabled ? 'bg-emerald-600' : 'bg-gray-700'}`}
                >
                  <span className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform ${draft.enabled ? 'left-5' : 'left-0.5'}`} />
                </div>
                <svg className={`w-4 h-4 text-gray-500 transition-transform ${isExpanded ? 'rotate-180' : ''}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                </svg>
              </div>
            </button>

            {/* Params */}
            {isExpanded && (
              <div className="px-4 pb-4 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                {paramEntries.map(([key, val]) => {
                  const meta = PARAM_META[key] || { label: key, hint: '' }
                  const savedVal = saved?.params[key]
                  const changed = savedVal !== undefined && JSON.stringify(val) !== JSON.stringify(savedVal)

                  return (
                    <div key={key} className={`bg-gray-800/50 rounded-lg px-3 py-2.5 ${changed ? 'ring-1 ring-yellow-700/50' : ''}`}>
                      <div className="flex items-baseline justify-between mb-1">
                        <label className="text-xs font-medium text-gray-300">{meta.label}</label>
                        {changed && (
                          <span className="text-[9px] text-yellow-500">was {String(savedVal)}</span>
                        )}
                      </div>
                      <input
                        type="number"
                        value={val as number ?? ''}
                        min={meta.min}
                        max={meta.max}
                        step={meta.step}
                        onChange={e => {
                          const raw = e.target.value
                          if (raw === '') return
                          const num = parseFloat(raw)
                          if (!isNaN(num)) setStratParam(name, key, num)
                        }}
                        className={`w-full bg-gray-900 border rounded px-2 py-1.5 text-sm text-white font-mono focus:outline-none focus:border-blue-500 ${
                          changed ? 'border-yellow-700' : 'border-gray-700'
                        }`}
                      />
                      {meta.hint && (
                        <div className="text-[10px] text-gray-600 mt-1">{meta.hint}{meta.min !== undefined ? ` (${meta.min}–${meta.max})` : ''}</div>
                      )}
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        )
      })}

      {/* Risk Management section */}
      {draftRisk && (
        <div className="bg-gray-900 border border-gray-800 rounded-lg overflow-hidden">
          <button onClick={() => toggleSection('_risk')}
            className="w-full flex items-center justify-between px-4 py-3 text-left hover:bg-gray-800/50 transition-colors">
            <div className="flex items-center gap-3">
              <span className="w-7 h-7 rounded bg-gray-800 flex items-center justify-center text-xs font-bold text-red-400">$</span>
              <span className="text-sm font-semibold text-white">{t('risk_management')}</span>
              {savedRisk && JSON.stringify(draftRisk) !== JSON.stringify(savedRisk) && (
                <span className="px-1.5 py-0.5 rounded text-[9px] font-bold bg-yellow-900/60 text-yellow-300 uppercase">modified</span>
              )}
            </div>
            <svg className={`w-4 h-4 text-gray-500 transition-transform ${expandedSections.has('_risk') ? 'rotate-180' : ''}`} fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
            </svg>
          </button>

          {expandedSections.has('_risk') && (
            <div className="px-4 pb-4 grid grid-cols-1 sm:grid-cols-3 gap-3">
              {RISK_FIELDS.map(f => {
                const val = draftRisk[f.key]
                const savedVal = savedRisk?.[f.key]
                const changed = savedVal !== undefined && val !== savedVal

                return (
                  <div key={f.key} className={`bg-gray-800/50 rounded-lg px-3 py-2.5 ${changed ? 'ring-1 ring-yellow-700/50' : ''}`}>
                    <div className="flex items-baseline justify-between mb-1">
                      <label className="text-xs font-medium text-gray-300">{f.label}</label>
                      {changed && (
                        <span className="text-[9px] text-yellow-500">was {savedVal}</span>
                      )}
                    </div>
                    <input
                      type="number"
                      value={val}
                      min={f.min}
                      max={f.max}
                      step={f.step}
                      onChange={e => {
                        const num = parseFloat(e.target.value)
                        if (!isNaN(num)) setDraftRisk({ ...draftRisk, [f.key]: num })
                      }}
                      className={`w-full bg-gray-900 border rounded px-2 py-1.5 text-sm text-white font-mono focus:outline-none focus:border-blue-500 ${
                        changed ? 'border-yellow-700' : 'border-gray-700'
                      }`}
                    />
                    <div className="text-[10px] text-gray-600 mt-1">{f.hint} ({f.min}–{f.max})</div>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      )}

      {/* Action bar */}
      <div className="flex items-center gap-3 flex-wrap sticky bottom-0 bg-gray-950/95 backdrop-blur py-3 border-t border-gray-800 -mx-4 px-4 sm:-mx-6 sm:px-6 lg:-mx-8 lg:px-8">
        <button
          onClick={() => setShowConfirm(true)}
          disabled={!isDirty || saving}
          className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium rounded-lg transition-colors disabled:opacity-40">
          {saving ? l('Salvataggio...', 'Saving...') : l('Salva modifiche', 'Save changes')}
        </button>
        <button
          onClick={handleDiscard}
          disabled={!isDirty}
          className="px-4 py-2 bg-gray-800 hover:bg-gray-700 text-gray-300 text-sm rounded-lg transition-colors disabled:opacity-40">
          {l('Annulla modifiche', 'Discard changes')}
        </button>
        {isDirty && (
          <span className="text-xs text-yellow-500">{diffs.length} {l('modifiche non salvate', 'unsaved changes')}</span>
        )}
      </div>

      {/* Confirm dialog */}
      <Modal
        open={showConfirm}
        onClose={() => setShowConfirm(false)}
        title={l('Conferma salvataggio', 'Confirm save')}
        actions={
          <>
            <button onClick={() => setShowConfirm(false)}
              className="px-3 py-2 bg-gray-800 hover:bg-gray-700 text-gray-300 text-sm rounded-lg transition-colors">
              {l('Annulla', 'Cancel')}
            </button>
            <button onClick={handleSave}
              className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium rounded-lg transition-colors">
              {l('Salva', 'Save')}
            </button>
          </>
        }
      >
        <p className="text-sm text-gray-400 mb-3">{l('Le seguenti modifiche verranno salvate:', 'The following changes will be saved:')}</p>
        <div className="max-h-60 overflow-auto space-y-1">
          {diffs.map((d, i) => (
            <div key={i} className="flex items-center gap-2 text-xs font-mono bg-gray-800 rounded px-2 py-1">
              <span className="text-gray-400 flex-1 truncate">{d.path}</span>
              <span className="text-red-400">{String(d.from)}</span>
              <span className="text-gray-600">&rarr;</span>
              <span className="text-emerald-400">{String(d.to)}</span>
            </div>
          ))}
        </div>
      </Modal>
    </div>
  )
}
