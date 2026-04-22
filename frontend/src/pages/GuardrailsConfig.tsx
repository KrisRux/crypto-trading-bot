import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, AdaptiveStatus, TuningSuggestionItem } from '../api'
import { useLang } from '../hooks/useLang'
import { usePolling } from '../hooks/usePolling'
import StatCard from '../components/StatCard'

/* ═══════════════════════════════════════════════════════
   Types
   ═══════════════════════════════════════════════════════ */

type Cfg = Record<string, unknown>

interface FieldDef {
  key: string
  label: string
  hint: string
  min?: number
  max?: number
  step?: number
  type?: 'number' | 'boolean' | 'integer'
}

interface SectionDef {
  id: string
  title: string
  icon: string
  path: string[]          // path into the config object
  fields: FieldDef[]
}

/* ═══════════════════════════════════════════════════════
   Field metadata — describes every editable field
   ═══════════════════════════════════════════════════════ */

const SECTIONS: SectionDef[] = [
  {
    id: 'trade_gate_defensive', title: 'Trade Gate — Defensive', icon: 'D',
    path: ['trade_gate', 'defensive'],
    fields: [
      { key: 'require_symbol_trend', label: 'Require symbol trend', hint: 'Symbol must be in trend regime', type: 'boolean' },
      { key: 'min_adx', label: 'Min ADX', hint: 'ADX threshold to allow entry', min: 5, max: 60, step: 1 },
      { key: 'min_volume_ratio', label: 'Min Volume Ratio', hint: 'Volume vs 20-period avg', min: 0.1, max: 5, step: 0.1 },
      { key: 'min_bb_width_pct', label: 'Min BB Width %', hint: 'Bollinger Band width % of mid', min: 0, max: 10, step: 0.1 },
    ],
  },
  {
    id: 'trade_gate_range', title: 'Trade Gate — Range', icon: 'R',
    path: ['trade_gate', 'range'],
    fields: [
      { key: 'require_symbol_trend', label: 'Require symbol trend', hint: 'Symbol must be in trend regime', type: 'boolean' },
      { key: 'min_adx', label: 'Min ADX', hint: 'ADX threshold in range market', min: 5, max: 60, step: 1 },
      { key: 'min_volume_ratio', label: 'Min Volume Ratio', hint: 'Volume vs 20-period avg', min: 0.1, max: 5, step: 0.1 },
      { key: 'min_bb_width_pct', label: 'Min BB Width %', hint: 'Bollinger Band width % of mid', min: 0, max: 10, step: 0.1 },
    ],
  },
  {
    id: 'trade_gate_trend', title: 'Trade Gate — Trend', icon: 'T',
    path: ['trade_gate', 'trend'],
    fields: [
      { key: 'require_symbol_trend', label: 'Require symbol trend', hint: 'Symbol must be in trend regime', type: 'boolean' },
      { key: 'min_adx', label: 'Min ADX', hint: 'ADX threshold in trending market', min: 5, max: 60, step: 1 },
      { key: 'min_volume_ratio', label: 'Min Volume Ratio', hint: 'Volume vs 20-period avg', min: 0.1, max: 5, step: 0.1 },
      { key: 'min_bb_width_pct', label: 'Min BB Width %', hint: 'Bollinger Band width % of mid', min: 0, max: 10, step: 0.1 },
    ],
  },
  {
    id: 'trade_gate_volatile', title: 'Trade Gate — Volatile', icon: 'V',
    path: ['trade_gate', 'volatile'],
    fields: [
      { key: 'require_symbol_trend', label: 'Require symbol trend', hint: 'Symbol must be in trend regime', type: 'boolean' },
      { key: 'min_adx', label: 'Min ADX', hint: 'ADX threshold in volatile market', min: 5, max: 60, step: 1 },
      { key: 'min_volume_ratio', label: 'Min Volume Ratio', hint: 'Volume vs 20-period avg', min: 0.1, max: 5, step: 0.1 },
      { key: 'min_bb_width_pct', label: 'Min BB Width %', hint: 'Bollinger Band width % of mid', min: 0, max: 10, step: 0.1 },
    ],
  },
  {
    id: 'dynamic_score', title: 'Dynamic Score Filter', icon: 'S',
    path: ['dynamic_score'],
    fields: [
      { key: 'base_min_score', label: 'Base Min Score', hint: 'Minimum signal score to open trade', min: 0, max: 100, step: 1, type: 'integer' },
      { key: 'min_score_after_3_losses', label: 'Min Score (3+ losses)', hint: 'Score raised after 3 consecutive losses', min: 0, max: 100, step: 1, type: 'integer' },
      { key: 'min_score_after_5_losses', label: 'Min Score (5+ losses)', hint: 'Score raised after 5 consecutive losses', min: 0, max: 100, step: 1, type: 'integer' },
      { key: 'extra_score_in_bad_regime', label: 'Extra in bad regime', hint: 'Added to min score in range/defensive', min: 0, max: 20, step: 1, type: 'integer' },
      { key: 'max_score_cap', label: 'Max Score Cap', hint: 'Absolute maximum threshold (never above this)', min: 50, max: 100, step: 1, type: 'integer' },
    ],
  },
  {
    id: 'kill_switch', title: 'Kill Switch', icon: 'K',
    path: ['kill_switch'],
    fields: [
      { key: 'consecutive_losses_threshold', label: 'Consec. Losses Threshold', hint: 'Pause trading after N losses', min: 2, max: 20, step: 1, type: 'integer' },
      { key: 'low_win_rate_threshold', label: 'Low Win Rate %', hint: 'Pause if win rate drops below', min: 0, max: 50, step: 1, type: 'integer' },
      { key: 'intraday_drawdown_threshold', label: 'Drawdown Threshold %', hint: 'Pause if drawdown exceeds', min: 0.5, max: 10, step: 0.5 },
      { key: 'pnl_24h_threshold', label: 'PnL 24h Threshold', hint: 'Pause if 24h PnL below (USDT)', min: -50, max: 0, step: 1 },
      { key: 'pause_minutes_losses', label: 'Pause (losses)', hint: 'Minutes to pause on loss trigger', min: 10, max: 480, step: 10, type: 'integer' },
      { key: 'pause_minutes_drawdown', label: 'Pause (drawdown)', hint: 'Minutes to pause on drawdown trigger', min: 10, max: 480, step: 10, type: 'integer' },
    ],
  },
  {
    id: 'symbol_cooldown', title: 'Symbol Cooldown', icon: 'C',
    path: ['symbol_cooldown'],
    fields: [
      { key: 'consecutive_losses_threshold', label: 'Consec. Losses', hint: 'Cooldown symbol after N losses', min: 1, max: 10, step: 1, type: 'integer' },
      { key: 'cooldown_minutes_losses', label: 'Cooldown (losses) min', hint: 'Minutes to cool down after losses', min: 5, max: 240, step: 5, type: 'integer' },
      { key: 'stoploss_cluster_count', label: 'SL Cluster Count', hint: 'N stop-losses in window triggers cooldown', min: 1, max: 10, step: 1, type: 'integer' },
      { key: 'stoploss_cluster_window_minutes', label: 'SL Cluster Window min', hint: 'Time window for SL cluster detection', min: 10, max: 360, step: 10, type: 'integer' },
      { key: 'cooldown_minutes_cluster', label: 'Cooldown (cluster) min', hint: 'Minutes to cool down after SL cluster', min: 5, max: 240, step: 5, type: 'integer' },
    ],
  },
  {
    id: 'entry_throttle', title: 'Entry Throttle', icon: 'E',
    path: ['entry_throttle'],
    fields: [
      { key: 'max_entries_per_symbol_per_candle', label: 'Max per symbol/candle', hint: 'Entries per symbol per 15m candle', min: 1, max: 5, step: 1, type: 'integer' },
      { key: 'default_max_entries_per_hour', label: 'Default max/hour', hint: 'Hourly entry limit (fallback)', min: 1, max: 20, step: 1, type: 'integer' },
    ],
  },
  {
    id: 'risk_scaling', title: 'Risk Scaling', icon: 'R',
    path: ['risk_scaling'],
    fields: [
      { key: 'consecutive_losses_3_multiplier', label: 'Multiplier (3+ losses)', hint: 'Position size multiplier', min: 0.1, max: 1.0, step: 0.05 },
      { key: 'consecutive_losses_5_multiplier', label: 'Multiplier (5+ losses)', hint: 'Position size multiplier', min: 0.1, max: 1.0, step: 0.05 },
      { key: 'drawdown_threshold', label: 'Drawdown Threshold %', hint: 'Reduce size above this drawdown', min: 0.5, max: 5, step: 0.5 },
      { key: 'drawdown_min_multiplier', label: 'Drawdown Multiplier', hint: 'Min multiplier when DD exceeded', min: 0.1, max: 1.0, step: 0.05 },
    ],
  },
  {
    id: 'strategy_circuit_breaker', title: 'Strategy Circuit Breaker', icon: 'B',
    path: ['strategy_circuit_breaker'],
    fields: [
      { key: 'consecutive_losses_threshold', label: 'Consec. Losses', hint: 'Pause strategy after N losses', min: 2, max: 15, step: 1, type: 'integer' },
      { key: 'pause_minutes', label: 'Pause Minutes', hint: 'How long to pause the strategy', min: 10, max: 480, step: 10, type: 'integer' },
    ],
  },
]

/* presets */
const PRESETS: Record<string, { label: string; desc: string; overrides: Record<string, Record<string, unknown>> }> = {
  conservative: {
    label: 'Conservative', desc: 'Strict filtering, fewer trades, max protection',
    overrides: {
      'trade_gate.defensive': { min_adx: 30, min_volume_ratio: 1.6 },
      'trade_gate.range': { min_adx: 32, min_volume_ratio: 1.8 },
      'trade_gate.trend': { min_adx: 25, min_volume_ratio: 1.0 },
      'dynamic_score': { base_min_score: 82 },
    },
  },
  balanced: {
    label: 'Balanced', desc: 'Moderate thresholds, good balance risk/opportunity',
    overrides: {
      'trade_gate.defensive': { min_adx: 27, min_volume_ratio: 1.4 },
      'trade_gate.range': { min_adx: 28, min_volume_ratio: 1.0 },
      'trade_gate.trend': { min_adx: 24, min_volume_ratio: 0.9 },
      'dynamic_score': { base_min_score: 80 },
    },
  },
  aggressive: {
    label: 'Aggressive', desc: 'Lower thresholds, more trades, higher exposure',
    overrides: {
      'trade_gate.defensive': { min_adx: 24, min_volume_ratio: 1.2 },
      'trade_gate.range': { min_adx: 25, min_volume_ratio: 0.8 },
      'trade_gate.trend': { min_adx: 22, min_volume_ratio: 0.7 },
      'dynamic_score': { base_min_score: 75 },
    },
  },
}

/* ═══════════════════════════════════════════════════════
   Helpers
   ═══════════════════════════════════════════════════════ */

function getNestedValue(obj: Cfg, path: string[]): unknown {
  let cur: unknown = obj
  for (const k of path) {
    if (cur && typeof cur === 'object' && k in (cur as Cfg)) cur = (cur as Cfg)[k]
    else return undefined
  }
  return cur
}

function setNestedValue(obj: Cfg, path: string[], value: unknown): Cfg {
  const copy = JSON.parse(JSON.stringify(obj))
  let cur = copy
  for (let i = 0; i < path.length - 1; i++) {
    if (!(path[i] in cur)) cur[path[i]] = {}
    cur = cur[path[i]] as Cfg
  }
  cur[path[path.length - 1]] = value
  return copy
}

function flatDiff(a: Cfg, b: Cfg, prefix = ''): { path: string; from: unknown; to: unknown }[] {
  const diffs: { path: string; from: unknown; to: unknown }[] = []
  const keys = new Set([...Object.keys(a || {}), ...Object.keys(b || {})])
  for (const k of keys) {
    const pa = prefix ? `${prefix}.${k}` : k
    const va = (a || {})[k]
    const vb = (b || {})[k]
    if (typeof va === 'object' && va !== null && typeof vb === 'object' && vb !== null && !Array.isArray(va)) {
      diffs.push(...flatDiff(va as Cfg, vb as Cfg, pa))
    } else if (JSON.stringify(va) !== JSON.stringify(vb)) {
      diffs.push({ path: pa, from: va, to: vb })
    }
  }
  return diffs
}

/* ═══════════════════════════════════════════════════════
   COMPONENT
   ═══════════════════════════════════════════════════════ */

export default function GuardrailsConfig() {
  const { lang, l } = useLang()

  /* state */
  const [saved, setSaved] = useState<Cfg | null>(null)     // last loaded from server
  const [draft, setDraft] = useState<Cfg | null>(null)     // current edits
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')
  const [showConfirm, setShowConfirm] = useState(false)
  const [expandedSections, setExpandedSections] = useState<Set<string>>(new Set(SECTIONS.map(s => s.id)))

  /* adaptive status for KPI */
  const fetchAdaptive = useCallback(() => api.getAdaptiveStatus(), [])
  const [adaptive] = usePolling<AdaptiveStatus>(fetchAdaptive, 15000)

  /* load config */
  const loadConfig = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const cfg = await api.getGuardrailsConfig()
      setSaved(cfg)
      setDraft(JSON.parse(JSON.stringify(cfg)))
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { loadConfig() }, [loadConfig])

  /* dirty check */
  const isDirty = useMemo(() => {
    if (!saved || !draft) return false
    return JSON.stringify(saved) !== JSON.stringify(draft)
  }, [saved, draft])

  const diffs = useMemo(() => {
    if (!saved || !draft) return []
    return flatDiff(saved, draft)
  }, [saved, draft])

  /* validation */
  const validate = (): string[] => {
    const errs: string[] = []
    if (!draft) return ['No config loaded']
    for (const section of SECTIONS) {
      const obj = getNestedValue(draft, section.path) as Cfg | undefined
      if (!obj) continue
      for (const f of section.fields) {
        const val = obj[f.key]
        if (f.type === 'boolean') continue
        const num = Number(val)
        if (isNaN(num)) { errs.push(`${section.title} > ${f.label}: not a number`); continue }
        if (f.min !== undefined && num < f.min) errs.push(`${section.title} > ${f.label}: ${num} < min ${f.min}`)
        if (f.max !== undefined && num > f.max) errs.push(`${section.title} > ${f.label}: ${num} > max ${f.max}`)
      }
    }
    // Cross-field: range.min_adx should be >= trend.min_adx (soft warning)
    const trendAdx = Number((getNestedValue(draft, ['trade_gate', 'trend']) as Cfg)?.min_adx ?? 0)
    const rangeAdx = Number((getNestedValue(draft, ['trade_gate', 'range']) as Cfg)?.min_adx ?? 0)
    if (rangeAdx < trendAdx) errs.push(`Warning: range min_adx (${rangeAdx}) < trend min_adx (${trendAdx})`)
    return errs
  }

  /* save */
  const handleSave = async () => {
    setShowConfirm(false)
    setSaving(true)
    setError('')
    setSuccess('')
    try {
      await api.updateGuardrailsConfig(draft as Cfg)
      setSaved(JSON.parse(JSON.stringify(draft)))
      setSuccess(l('Configurazione salvata e ricaricata', 'Config saved and reloaded'))
      setTimeout(() => setSuccess(''), 4000)
    } catch (e) {
      setError(String(e))
    } finally {
      setSaving(false)
    }
  }

  /* reset to defaults */
  const handleReset = async () => {
    if (!confirm(l('Ripristinare i valori predefiniti?', 'Restore default values?'))) return
    setSaving(true)
    setError('')
    try {
      const res = await api.resetGuardrailsConfig()
      setSaved(res.config as Cfg)
      setDraft(JSON.parse(JSON.stringify(res.config)))
      setSuccess(l('Valori predefiniti ripristinati', 'Defaults restored'))
      setTimeout(() => setSuccess(''), 4000)
    } catch (e) {
      setError(String(e))
    } finally {
      setSaving(false)
    }
  }

  /* apply preset (fills draft, does NOT save) */
  const applyPreset = (presetId: string) => {
    if (!draft) return
    const preset = PRESETS[presetId]
    if (!preset) return
    let next = JSON.parse(JSON.stringify(draft)) as Cfg
    for (const [dotPath, overrides] of Object.entries(preset.overrides)) {
      const pathParts = dotPath.split('.')
      const obj = getNestedValue(next, pathParts) as Cfg | undefined
      if (obj) {
        for (const [k, v] of Object.entries(overrides)) {
          obj[k] = v
        }
        next = setNestedValue(next, pathParts, obj)
      }
    }
    setDraft(next)
  }

  /* field change */
  const handleFieldChange = (sectionPath: string[], key: string, value: unknown) => {
    if (!draft) return
    const fullPath = [...sectionPath, key]
    setDraft(setNestedValue(draft, fullPath, value))
  }

  /* toggle section */
  const toggleSection = (id: string) => {
    setExpandedSections(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id); else next.add(id)
      return next
    })
  }

  /* validation errors */
  const validationErrors = useMemo(() => draft ? validate() : [], [draft])

  /* ─── Render ─── */

  if (loading) return <div className="text-gray-500 text-sm">{l('Caricamento...', 'Loading...')}</div>

  return (
    <div className="space-y-5">
      {/* Header */}
      <div>
        <h2 className="text-lg font-semibold text-white">Guardrails Config</h2>
        <p className="text-sm text-gray-500">{l('Configurazione operativa dei filtri di rischio e trade gate', 'Risk filters and trade gate operational configuration')}</p>
      </div>

      {/* KPI bar */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <StatCard label="Profile" value={adaptive?.active_profile || '-'} color="text-purple-400" />
        <StatCard label="Regime" value={adaptive?.regime?.global_regime || '-'}
          color={adaptive?.regime?.global_regime === 'trend' ? 'text-emerald-400' : 'text-yellow-400'} />
        <StatCard label="Config"
          value={isDirty ? l('Modificato', 'Modified') : l('Salvato', 'Saved')}
          color={isDirty ? 'text-yellow-400' : 'text-emerald-400'} />
        <StatCard label="Min Score" value={
          (getNestedValue(draft || {}, ['dynamic_score', 'base_min_score']) as number) ?? '-'
        } color="text-blue-400" />
      </div>

      {/* Messages */}
      {error && <div className="bg-red-900/30 border border-red-800/50 text-red-300 px-4 py-2 rounded text-sm">{error}</div>}
      {success && <div className="bg-emerald-900/30 border border-emerald-800/50 text-emerald-300 px-4 py-2 rounded text-sm">{success}</div>}
      {validationErrors.length > 0 && (
        <div className="bg-yellow-900/20 border border-yellow-800/50 text-yellow-300 px-4 py-2 rounded text-sm">
          {validationErrors.map((e, i) => <div key={i}>{e}</div>)}
        </div>
      )}

      {/* Presets */}
      <div className="flex flex-wrap gap-2">
        {Object.entries(PRESETS).map(([id, p]) => (
          <button key={id} onClick={() => applyPreset(id)} title={p.desc}
            className="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 border border-gray-700 text-gray-300 text-xs rounded transition-colors">
            {p.label}
          </button>
        ))}
        <div className="flex-1" />
        <button onClick={handleReset} disabled={saving}
          className="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 border border-gray-700 text-gray-400 text-xs rounded transition-colors disabled:opacity-40">
          {l('Ripristina default', 'Restore defaults')}
        </button>
        <button onClick={loadConfig} disabled={loading}
          className="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 border border-gray-700 text-gray-400 text-xs rounded transition-colors disabled:opacity-40">
          Reload
        </button>
      </div>

      {/* ── AI Tuning Advisor ── */}
      <TuningAdvisorSection onApplyChanges={(changes) => {
        if (!draft) return
        let next = JSON.parse(JSON.stringify(draft)) as Cfg
        for (const c of changes) {
          const parts = (c.path as string).split('.')
          let obj: Cfg = next
          for (let i = 0; i < parts.length - 1; i++) {
            if (!(parts[i] in obj)) obj[parts[i]] = {}
            obj = obj[parts[i]] as Cfg
          }
          obj[parts[parts.length - 1]] = c.to
        }
        setDraft(next)
      }} l={l} />

      {/* Sections */}
      {draft && SECTIONS.map(section => {
        const obj = getNestedValue(draft, section.path) as Cfg | undefined
        const savedObj = saved ? getNestedValue(saved, section.path) as Cfg | undefined : undefined
        if (!obj) return null
        const isExpanded = expandedSections.has(section.id)

        return (
          <div key={section.id} className="bg-gray-900 border border-gray-800 rounded-lg overflow-hidden">
            {/* Section header — clickable */}
            <button onClick={() => toggleSection(section.id)}
              className="w-full flex items-center justify-between px-4 py-3 text-left hover:bg-gray-800/50 transition-colors">
              <div className="flex items-center gap-2">
                <span className="w-6 h-6 rounded bg-gray-800 flex items-center justify-center text-[10px] font-bold text-gray-400">{section.icon}</span>
                <span className="text-sm font-semibold text-white">{section.title}</span>
                {/* dirty badge */}
                {savedObj && section.fields.some(f => JSON.stringify(obj[f.key]) !== JSON.stringify(savedObj[f.key])) && (
                  <span className="px-1.5 py-0.5 rounded text-[9px] font-bold bg-yellow-900/60 text-yellow-300 uppercase">modified</span>
                )}
              </div>
              <svg className={`w-4 h-4 text-gray-500 transition-transform ${isExpanded ? 'rotate-180' : ''}`} fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
              </svg>
            </button>

            {/* Fields */}
            {isExpanded && (
              <div className="px-4 pb-4 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                {section.fields.map(f => {
                  const val = obj[f.key]
                  const savedVal = savedObj?.[f.key]
                  const changed = savedVal !== undefined && JSON.stringify(val) !== JSON.stringify(savedVal)

                  if (f.type === 'boolean') {
                    return (
                      <div key={f.key} className="flex items-center justify-between bg-gray-800/50 rounded-lg px-3 py-2.5">
                        <div>
                          <div className="text-xs font-medium text-gray-300">{f.label}</div>
                          <div className="text-[10px] text-gray-600">{f.hint}</div>
                        </div>
                        <button
                          onClick={() => handleFieldChange(section.path, f.key, !val)}
                          className={`w-10 h-5 rounded-full transition-colors relative ${val ? 'bg-emerald-600' : 'bg-gray-700'}`}>
                          <span className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform ${val ? 'left-5' : 'left-0.5'}`} />
                        </button>
                      </div>
                    )
                  }

                  return (
                    <div key={f.key} className={`bg-gray-800/50 rounded-lg px-3 py-2.5 ${changed ? 'ring-1 ring-yellow-700/50' : ''}`}>
                      <div className="flex items-baseline justify-between mb-1">
                        <label className="text-xs font-medium text-gray-300">{f.label}</label>
                        {changed && (
                          <span className="text-[9px] text-yellow-500">
                            was {String(savedVal)}
                          </span>
                        )}
                      </div>
                      <input
                        type="number"
                        value={val as number ?? ''}
                        min={f.min}
                        max={f.max}
                        step={f.step}
                        onChange={e => {
                          const raw = e.target.value
                          if (raw === '') return
                          const num = f.type === 'integer' ? parseInt(raw) : parseFloat(raw)
                          if (!isNaN(num)) handleFieldChange(section.path, f.key, num)
                        }}
                        className={`w-full bg-gray-900 border rounded px-2 py-1.5 text-sm text-white font-mono focus:outline-none focus:border-blue-500 ${
                          changed ? 'border-yellow-700' : 'border-gray-700'
                        }`}
                      />
                      <div className="text-[10px] text-gray-600 mt-1">{f.hint}{f.min !== undefined ? ` (${f.min}–${f.max})` : ''}</div>
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        )
      })}

      {/* Action bar */}
      <div className="flex items-center gap-3 flex-wrap sticky bottom-0 bg-gray-950/95 backdrop-blur py-3 border-t border-gray-800 -mx-4 px-4 sm:-mx-6 sm:px-6 lg:-mx-8 lg:px-8">
        <button
          onClick={() => {
            const errs = validate()
            if (errs.length > 0 && errs.some(e => !e.startsWith('Warning'))) {
              setError(errs.join('; '))
              return
            }
            setShowConfirm(true)
          }}
          disabled={!isDirty || saving}
          className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium rounded-lg transition-colors disabled:opacity-40">
          {saving ? l('Salvataggio...', 'Saving...') : l('Salva modifiche', 'Save changes')}
        </button>
        <button
          onClick={() => { setDraft(JSON.parse(JSON.stringify(saved))); setError('') }}
          disabled={!isDirty}
          className="px-4 py-2 bg-gray-800 hover:bg-gray-700 text-gray-300 text-sm rounded-lg transition-colors disabled:opacity-40">
          {l('Annulla modifiche', 'Discard changes')}
        </button>
        {isDirty && (
          <span className="text-xs text-yellow-500">{diffs.length} {l('modifiche non salvate', 'unsaved changes')}</span>
        )}
      </div>

      {/* Confirm dialog */}
      {showConfirm && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4" onClick={() => setShowConfirm(false)}>
          <div className="bg-gray-900 border border-gray-700 rounded-xl max-w-lg w-full p-5 space-y-4" onClick={e => e.stopPropagation()}>
            <h3 className="text-white font-semibold">{l('Conferma salvataggio', 'Confirm save')}</h3>
            <p className="text-sm text-gray-400">{l('Le seguenti modifiche verranno applicate e il bot le caricherà immediatamente:', 'The following changes will be applied and the bot will reload immediately:')}</p>
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
            <div className="flex gap-2 justify-end">
              <button onClick={() => setShowConfirm(false)}
                className="px-3 py-2 bg-gray-800 hover:bg-gray-700 text-gray-300 text-sm rounded-lg transition-colors">
                {l('Annulla', 'Cancel')}
              </button>
              <button onClick={handleSave}
                className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium rounded-lg transition-colors">
                {l('Salva e ricarica', 'Save & reload')}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

/* ═══════════════════════════════════════════════════════
   AI Tuning Advisor sub-component
   ═══════════════════════════════════════════════════════ */

function TuningAdvisorSection({ onApplyChanges, l }: {
  onApplyChanges: (changes: { path: string; from: unknown; to: unknown }[]) => void
  l: (it: string, en: string) => string
}) {
  const [generating, setGenerating] = useState(false)
  const [generateCooldown, setGenerateCooldown] = useState(false)
  const [suggestion, setSuggestion] = useState<TuningSuggestionItem | null>(null)
  const [noSuggestion, setNoSuggestion] = useState('')
  const [history, setHistory] = useState<TuningSuggestionItem[]>([])
  const [showHistory, setShowHistory] = useState(false)
  const [applying, setApplying] = useState(false)
  const [msg, setMsg] = useState('')
  const [llmStatus, setLlmStatus] = useState<{
    available: boolean; configured_model: string
    deepseek?: { available: boolean; configured: boolean; model: string }
    ollama?: { available: boolean; configured_model: string }
  } | null>(null)
  const [sentiment, setSentiment] = useState<{ score: number; label: string; headline_count: number; top_headlines: { title: string; sentiment: number }[]; available: boolean } | null>(null)

  const loadHistory = useCallback(async () => {
    try {
      const h = await api.getTuningHistory()
      setHistory(h)
    } catch (e) {
      setMsg(`Error loading history: ${e}`)
    }
  }, [])

  useEffect(() => { loadHistory() }, [loadHistory])

  // Check Ollama + sentiment status on mount
  useEffect(() => {
    api.getOllamaStatus()
      .then(data => setLlmStatus(data))
      .catch(() => {})
    api.getNewsSentiment()
      .then(data => setSentiment(data))
      .catch(() => {})
  }, [])

  const handleGenerate = async () => {
    if (generateCooldown) return
    setGenerating(true)
    setGenerateCooldown(true)
    setSuggestion(null)
    setNoSuggestion('')
    setMsg('')
    try {
      const res = await api.generateTuningSuggestion()
      if (res.suggestion) {
        setSuggestion(res.suggestion)
      } else {
        setNoSuggestion(res.reasoning || l('Nessun suggerimento', 'No suggestions'))
      }
      loadHistory()
    } catch (e) {
      setMsg(`Error: ${e}`)
    } finally {
      setGenerating(false)
      // Re-enable generate button after 30 seconds
      setTimeout(() => setGenerateCooldown(false), 30000)
    }
  }

  const handleApply = async (s: TuningSuggestionItem) => {
    if (!confirm(l('Applicare questo suggerimento ai guardrails?', 'Apply this suggestion to guardrails?'))) return
    setApplying(true)
    setMsg('')
    try {
      await api.applyTuningSuggestion(s.id)
      setMsg(l('Suggerimento applicato', 'Suggestion applied'))
      setSuggestion(null)
      loadHistory()
      setTimeout(() => setMsg(''), 4000)
    } catch (e) {
      setMsg(`Error: ${e}`)
    } finally {
      setApplying(false)
    }
  }

  const handleReject = async (s: TuningSuggestionItem) => {
    try {
      await api.rejectTuningSuggestion(s.id)
      setSuggestion(null)
      loadHistory()
    } catch (e) {
      setMsg(`Error: ${e}`)
    }
  }

  const handlePreview = async (s: TuningSuggestionItem) => {
    onApplyChanges(s.changes)
    setMsg(l('Valori copiati nel form — salva per applicare', 'Values copied to form — save to apply'))
    setTimeout(() => setMsg(''), 5000)
    // Mark suggestion as rejected so it doesn't stay orphaned in "new" state
    try {
      await api.rejectTuningSuggestion(s.id)
      setSuggestion(null)
      loadHistory()
    } catch { /* best-effort */ }
  }

  const riskBadge = (risk: string) => {
    const cls = risk === 'high' ? 'bg-red-900/60 text-red-300' :
                risk === 'medium' ? 'bg-yellow-900/60 text-yellow-300' :
                'bg-emerald-900/60 text-emerald-300'
    return <span className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase ${cls}`}>{risk}</span>
  }

  const statusBadge = (status: string) => {
    const cls = status === 'applied' ? 'bg-emerald-900/60 text-emerald-300' :
                status === 'rejected' ? 'bg-red-900/60 text-red-300' :
                'bg-blue-900/60 text-blue-300'
    return <span className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase ${cls}`}>{status}</span>
  }

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg overflow-hidden">
      <div className="px-4 py-3 flex items-center justify-between border-b border-gray-800">
        <div className="flex items-center gap-2">
          <span className="w-7 h-7 rounded bg-purple-900/50 flex items-center justify-center text-xs font-bold text-purple-300">AI</span>
          <span className="text-sm font-semibold text-white">AI Tuning Advisor</span>
          {llmStatus && (
            llmStatus.deepseek?.available
              ? <span className="px-2 py-0.5 rounded text-[9px] font-bold bg-blue-900/60 text-blue-300">DeepSeek: {llmStatus.deepseek.model}</span>
              : llmStatus.ollama?.available
                ? <span className="px-2 py-0.5 rounded text-[9px] font-bold bg-emerald-900/60 text-emerald-300">Ollama: {llmStatus.ollama.configured_model}</span>
                : <span className="px-2 py-0.5 rounded text-[9px] font-bold bg-gray-800 text-gray-500">LLM offline — rules mode</span>
          )}
          {sentiment?.available && (
            <span className={`px-2 py-0.5 rounded text-[9px] font-bold ${
              sentiment.score > 0.1 ? 'bg-emerald-900/60 text-emerald-300' :
              sentiment.score < -0.1 ? 'bg-red-900/60 text-red-300' :
              'bg-gray-800 text-gray-400'
            }`}>
              News: {sentiment.label} ({sentiment.score > 0 ? '+' : ''}{sentiment.score.toFixed(2)}) — {sentiment.headline_count} headlines
            </span>
          )}
        </div>
        <div className="flex gap-2">
          <button onClick={() => setShowHistory(!showHistory)}
            className="px-2 py-1 bg-gray-800 hover:bg-gray-700 text-gray-400 text-xs rounded transition-colors">
            {l('Cronologia', 'History')} ({history.length})
          </button>
          <button onClick={handleGenerate} disabled={generating || generateCooldown}
            className="px-3 py-1 bg-purple-900/60 hover:bg-purple-800/70 border border-purple-800/50 text-purple-300 text-xs rounded transition-colors disabled:opacity-40">
            {generating ? l('Analisi...', 'Analyzing...') : generateCooldown ? l('Attendi...', 'Wait...') : l('Genera suggerimento', 'Generate suggestion')}
          </button>
        </div>
      </div>

      <div className="px-4 py-3 space-y-3">
        {msg && <div className={`text-xs px-3 py-1.5 rounded ${msg.startsWith('Error') ? 'bg-red-900/30 text-red-300' : 'bg-emerald-900/30 text-emerald-300'}`}>{msg}</div>}
        {noSuggestion && <div className="text-xs text-gray-500 bg-gray-800 rounded px-3 py-2">{noSuggestion}</div>}

        {/* News headlines */}
        {sentiment?.available && sentiment.top_headlines?.length > 0 && (
          <div className="bg-gray-800/30 rounded px-3 py-2">
            <p className="text-[10px] text-gray-600 uppercase font-semibold mb-1">Top News ({sentiment.label})</p>
            {sentiment.top_headlines.slice(0, 3).map((h, i) => (
              <div key={i} className="flex items-center gap-2 text-[11px] py-0.5">
                <span className={`w-8 text-right font-mono ${h.sentiment > 0.1 ? 'text-emerald-400' : h.sentiment < -0.1 ? 'text-red-400' : 'text-gray-500'}`}>
                  {h.sentiment > 0 ? '+' : ''}{h.sentiment.toFixed(2)}
                </span>
                <span className="text-gray-400 truncate">{h.title}</span>
              </div>
            ))}
          </div>
        )}

        {/* Current suggestion */}
        {suggestion && (
          <div className="bg-gray-800/50 rounded-lg p-3 space-y-2 border border-purple-900/30">
            <div className="flex items-center gap-2">
              <span className="text-xs font-semibold text-white">{l('Suggerimento', 'Suggestion')} #{suggestion.id}</span>
              {riskBadge(suggestion.risk_level)}
              <span className="text-[10px] text-gray-500">{l('Confidenza', 'Confidence')}: {(suggestion.confidence * 100).toFixed(0)}%</span>
            </div>
            <p className="text-xs text-gray-400">{suggestion.reasoning}</p>

            {/* Changes diff */}
            <div className="space-y-1">
              {suggestion.changes.map((c, i) => (
                <div key={i} className="flex items-center gap-2 text-xs font-mono bg-gray-900 rounded px-2 py-1">
                  <span className="text-gray-400 flex-1 truncate">{c.path}</span>
                  <span className="text-red-400">{String(c.from)}</span>
                  <span className="text-gray-600">&rarr;</span>
                  <span className="text-emerald-400">{String(c.to)}</span>
                </div>
              ))}
            </div>

            {/* Context snapshot */}
            <div className="flex flex-wrap gap-3 text-[10px] text-gray-600">
              <span>Regime: {suggestion.global_regime}</span>
              <span>WR: {suggestion.win_rate?.toFixed(0)}%</span>
              <span>DD: {suggestion.drawdown?.toFixed(2)}%</span>
              <span>CL: {suggestion.consecutive_losses}</span>
              <span>Blocked: {suggestion.total_blocked}</span>
              <span>Passed: {suggestion.total_passed}</span>
            </div>

            {/* Actions */}
            <div className="flex gap-2 pt-1">
              <button onClick={() => handleApply(suggestion)} disabled={applying}
                className="px-3 py-1.5 bg-emerald-600 hover:bg-emerald-700 text-white text-xs rounded transition-colors disabled:opacity-40">
                {applying ? '...' : l('Applica direttamente', 'Apply directly')}
              </button>
              <button onClick={() => handlePreview(suggestion)}
                className="px-3 py-1.5 bg-blue-900/60 hover:bg-blue-800/70 text-blue-300 text-xs rounded transition-colors">
                {l('Copia nel form', 'Copy to form')}
              </button>
              <button onClick={() => handleReject(suggestion)}
                className="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 text-gray-400 text-xs rounded transition-colors">
                {l('Scarta', 'Reject')}
              </button>
            </div>
          </div>
        )}

        {/* History */}
        {showHistory && history.length > 0 && (
          <div className="space-y-1">
            <p className="text-[10px] text-gray-600 uppercase font-semibold">{l('Cronologia suggerimenti', 'Suggestion history')}</p>
            {history.slice(0, 10).map(h => (
              <div key={h.id} className="flex items-center gap-2 text-xs bg-gray-800/30 rounded px-2 py-1">
                <span className="text-gray-600 text-[10px] w-28 shrink-0">{h.created_at?.replace('T', ' ').substring(0, 16)}</span>
                {statusBadge(h.status)}
                {riskBadge(h.risk_level)}
                <span className="text-gray-500 flex-1 truncate">{h.changes.map(c => c.path).join(', ')}</span>
                {h.resolved_by && <span className="text-gray-600 text-[10px]">{h.resolved_by}</span>}
              </div>
            ))}
          </div>
        )}

        {!suggestion && !noSuggestion && !generating && history.length === 0 && (
          <p className="text-xs text-gray-600">{l('Premi "Genera suggerimento" per analizzare lo stato attuale', 'Press "Generate suggestion" to analyze current state')}</p>
        )}
      </div>
    </div>
  )
}
