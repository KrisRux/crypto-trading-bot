import { useCallback, useMemo, useState } from 'react'
import { api, ProfilesResponse, TradingProfile } from '../api'
import { usePolling } from '../hooks/usePolling'
import { useLang } from '../hooks/useLang'
import { profileBadgeClass } from '../utils/colors'

function valueText(value: unknown): string {
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(1)
  if (typeof value === 'boolean') return value ? 'ON' : 'OFF'
  if (value === null || value === undefined) return '-'
  return String(value)
}

function riskSummary(profile: TradingProfile): [string, unknown][] {
  const risk = profile.risk || {}
  return [
    ['Position %', risk.max_position_pct],
    ['SL %', risk.default_sl_pct],
    ['TP %', risk.default_tp_pct],
  ]
}


export default function Profiles() {
  const { l } = useLang()
  const fetchProfiles = useCallback(() => api.getProfiles(), [])
  const [data, loading, loadError, refetch] = usePolling<ProfilesResponse>(fetchProfiles, 10000)
  const [busyProfile, setBusyProfile] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')

  const invalidPayload = Boolean(data && (!data.profiles || typeof data.active !== 'string'))
  const entries = useMemo(() => {
    if (!data?.profiles) return []
    return Object.entries(data.profiles).sort(([a], [b]) => a.localeCompare(b))
  }, [data])

  const applyProfile = async (name: string) => {
    setBusyProfile(name)
    setError('')
    setSuccess('')
    try {
      const result = await api.applyProfile(name)
      setSuccess(l(`Profilo ${result.active_profile} applicato`, `Profile ${result.active_profile} applied`))
      refetch()
      setTimeout(() => setSuccess(''), 4000)
    } catch (e) {
      setError(String(e))
    } finally {
      setBusyProfile(null)
    }
  }

  if (loading && !data) {
    return <div className="text-gray-500 text-sm">{l('Caricamento...', 'Loading...')}</div>
  }

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-2xl font-bold text-white">{l('Profili', 'Profiles')}</h1>
        <p className="text-sm text-gray-400 mt-1">
          {l('Gestione manuale del profilo operativo applicato al motore di trading.', 'Manual control of the operating profile applied to the trading engine.')}
        </p>
      </header>

      {error && (
        <div role="alert" className="bg-red-900/30 border border-red-800/50 text-red-300 px-4 py-2 rounded text-sm">
          {error}
        </div>
      )}
      {(loadError || invalidPayload) && (
        <div role="alert" className="bg-red-900/30 border border-red-800/50 text-red-300 px-4 py-2 rounded text-sm">
          {loadError || l('Risposta profili non valida dal backend.', 'Invalid profiles response from backend.')}
        </div>
      )}
      {success && (
        <div role="status" className="bg-emerald-900/30 border border-emerald-800/50 text-emerald-300 px-4 py-2 rounded text-sm">
          {success}
        </div>
      )}

      <section className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
          <p className="text-xs uppercase tracking-wider text-gray-500 mb-2">{l('Profilo attivo', 'Active profile')}</p>
          <span className={`inline-flex px-3 py-1 rounded-full text-xs font-bold uppercase tracking-wider ${profileBadgeClass(data?.active || '')}`}>
            {data?.active || '-'}
          </span>
        </div>
        {Object.entries(data?.switching_rules || {}).map(([key, value]) => (
          <div key={key} className="bg-gray-900 border border-gray-800 rounded-lg p-4">
            <p className="text-xs uppercase tracking-wider text-gray-500 mb-2">{key.replace(/_/g, ' ')}</p>
            <p className="text-xl font-semibold text-white">{valueText(value)}</p>
          </div>
        ))}
      </section>

      <section className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {entries.map(([name, profile]) => {
          const active = name === data?.active
          const busy = busyProfile === name
          return (
            <article key={name} className={`bg-gray-900 border rounded-lg p-4 ${active ? 'border-emerald-700/70' : 'border-gray-800'}`}>
              <div className="flex items-start justify-between gap-3 mb-3">
                <div>
                  <h2 className="text-lg font-semibold text-white">{name}</h2>
                  <p className="text-xs text-gray-500 mt-1 min-h-[32px]">{profile.description || '-'}</p>
                </div>
                <span className={`shrink-0 px-2 py-0.5 rounded-full text-[10px] font-bold uppercase ${profileBadgeClass(name)}`}>
                  {active ? l('Attivo', 'Active') : name}
                </span>
              </div>

              <div className="grid grid-cols-3 gap-2 mb-4">
                {riskSummary(profile).map(([label, value]) => (
                  <div key={label} className="bg-gray-950/60 border border-gray-800 rounded-md p-2">
                    <p className="text-[10px] uppercase text-gray-600">{label}</p>
                    <p className="text-sm font-semibold text-gray-200">{valueText(value)}</p>
                  </div>
                ))}
              </div>

              <div className="flex flex-wrap items-center gap-2 mb-4">
                <span className={`px-2 py-0.5 rounded text-[10px] font-semibold ${profile.auto_apply ? 'bg-blue-900/50 text-blue-300' : 'bg-gray-800 text-gray-400'}`}>
                  {profile.auto_apply ? 'AUTO' : 'MANUAL'}
                </span>
                {profile.requires_approval && (
                  <span className="px-2 py-0.5 rounded text-[10px] font-semibold bg-amber-900/50 text-amber-300">
                    {l('APPROVAZIONE', 'APPROVAL')}
                  </span>
                )}
              </div>

              <button
                onClick={() => applyProfile(name)}
                disabled={active || busyProfile !== null}
                className="w-full px-4 py-2 bg-emerald-700 hover:bg-emerald-600 disabled:bg-gray-800 disabled:text-gray-500 disabled:cursor-not-allowed text-white text-sm font-medium rounded-lg transition-colors"
              >
                {active ? l('Gia applicato', 'Already applied') : busy ? l('Applicazione...', 'Applying...') : l('Applica profilo', 'Apply profile')}
              </button>
            </article>
          )
        })}
      </section>
    </div>
  )
}
