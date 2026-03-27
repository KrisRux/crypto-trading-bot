import { useCallback, useState, useEffect } from 'react'
import { api } from '../api'
import { useLang } from '../hooks/useLang'
import { useAuth } from '../hooks/useAuth'

interface KeysState {
  trading_enabled: boolean
  trading_mode: string
  paper_initial_capital: number
  trading_start_hour: number | null
  trading_end_hour: number | null
  has_live_keys: boolean
  has_testnet_keys: boolean
  binance_api_key: string
  binance_testnet_api_key: string
}

export default function Settings() {
  const { lang } = useLang()
  const { role } = useAuth()
  const t = (it: string, en: string) => (lang === 'it' ? it : en)

  const [keys, setKeys] = useState<KeysState | null>(null)
  const [form, setForm] = useState({
    trading_enabled: false,
    trading_mode: 'paper',
    paper_initial_capital: 10000,
    trading_start_hour: null as number | null,
    trading_end_hour: null as number | null,
    binance_api_key: '',
    binance_api_secret: '',
    binance_testnet_api_key: '',
    binance_testnet_api_secret: '',
  })
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState('')

  const loadKeys = useCallback(() => {
    fetch('/api/settings/keys', {
      headers: {
        Authorization: `Bearer ${localStorage.getItem('auth_token')}`,
        'Content-Type': 'application/json',
      },
    })
      .then((r) => r.json())
      .then((data) => {
        setKeys(data)
        setForm((f) => ({
          ...f,
          trading_enabled: data.trading_enabled || false,
          trading_mode: data.trading_mode || 'paper',
          paper_initial_capital: data.paper_initial_capital || 10000,
          trading_start_hour: data.trading_start_hour,
          trading_end_hour: data.trading_end_hour,
        }))
      })
      .catch(() => {})
  }, [])

  useEffect(() => { loadKeys() }, [loadKeys])

  const handleSave = async () => {
    setSaved(false)
    setError('')
    try {
      const body: Record<string, unknown> = {
        trading_enabled: form.trading_enabled,
        trading_mode: form.trading_mode,
        paper_initial_capital: form.paper_initial_capital,
        trading_start_hour: form.trading_start_hour,
        trading_end_hour: form.trading_end_hour,
      }
      if (form.binance_api_key) body.binance_api_key = form.binance_api_key
      if (form.binance_api_secret) body.binance_api_secret = form.binance_api_secret
      if (form.binance_testnet_api_key) body.binance_testnet_api_key = form.binance_testnet_api_key
      if (form.binance_testnet_api_secret) body.binance_testnet_api_secret = form.binance_testnet_api_secret

      const resp = await fetch('/api/settings/keys', {
        method: 'PUT',
        headers: {
          Authorization: `Bearer ${localStorage.getItem('auth_token')}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(body),
      })
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: 'Error' }))
        throw new Error(err.detail || `Error ${resp.status}`)
      }
      setSaved(true)
      setForm((f) => ({
        ...f,
        binance_api_key: '',
        binance_api_secret: '',
        binance_testnet_api_key: '',
        binance_testnet_api_secret: '',
      }))
      loadKeys()
      setTimeout(() => setSaved(false), 3000)
    } catch (e) {
      setError(String(e))
    }
  }

  if (role === 'guest') {
    return (
      <div className="text-gray-400 text-sm">
        {t('Gli ospiti non possono configurare le impostazioni.', 'Guests cannot configure settings.')}
      </div>
    )
  }

  return (
    <div className="max-w-2xl space-y-8">
      <div>
        <h2 className="text-lg font-semibold text-white mb-1">
          {t('Impostazioni Account', 'Account Settings')}
        </h2>
        <p className="text-sm text-gray-400">
          {t(
            'Configura le tue chiavi API Binance e la modalita di trading. Ogni utente ha il proprio portafoglio separato.',
            'Configure your Binance API keys and trading mode. Each user has their own separate portfolio.'
          )}
        </p>
      </div>

      {/* Enable Trading */}
      <section className="bg-gray-900 border border-gray-800 rounded-lg p-4 space-y-3">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-sm font-semibold text-white">
              {t('Abilita Trading', 'Enable Trading')}
            </h3>
            <p className="text-xs text-gray-500 mt-0.5">
              {t(
                'Il bot eseguira operazioni sul tuo portafoglio solo se il trading e abilitato.',
                'The bot will only execute trades on your portfolio when trading is enabled.'
              )}
            </p>
          </div>
          <button
            onClick={() => setForm({ ...form, trading_enabled: !form.trading_enabled })}
            className={`relative w-12 h-6 rounded-full transition-colors ${
              form.trading_enabled ? 'bg-emerald-600' : 'bg-gray-700'
            }`}
          >
            <span className={`absolute top-0.5 left-0.5 w-5 h-5 bg-white rounded-full transition-transform ${
              form.trading_enabled ? 'translate-x-6' : ''
            }`} />
          </button>
        </div>
        {!form.trading_enabled && (
          <div className="bg-yellow-900/30 border border-yellow-800/50 rounded-lg p-3 text-xs text-yellow-300">
            {t(
              'Trading disabilitato. Il bot analizza il mercato ma non apre posizioni per il tuo account.',
              'Trading disabled. The bot analyses the market but does not open positions for your account.'
            )}
          </div>
        )}
        {form.trading_enabled && (
          (form.trading_mode === 'paper' && !keys?.has_testnet_keys) ||
          (form.trading_mode === 'live' && !keys?.has_live_keys)
        ) && (
          <div className="bg-red-900/30 border border-red-800/50 rounded-lg p-3 text-xs text-red-300">
            {t(
              'Trading abilitato ma chiavi API mancanti! Configura le chiavi qui sotto per la modalita selezionata, altrimenti nessun ordine verra eseguito.',
              'Trading enabled but API keys missing! Configure the keys below for your selected mode, otherwise no orders will be executed.'
            )}
          </div>
        )}
      </section>

      {/* Trading Schedule */}
      <section className="bg-gray-900 border border-gray-800 rounded-lg p-4 space-y-3">
        <h3 className="text-sm font-semibold text-white">
          {t('Orario di Trading', 'Trading Schedule')}
        </h3>
        <p className="text-xs text-gray-500">
          {t(
            'Configura le ore in cui il bot puo aprire nuove posizioni (fuso orario UTC). Le posizioni aperte vengono sempre monitorate per SL/TP anche fuori orario. Lascia vuoto per operare 24/7.',
            'Set the hours during which the bot can open new positions (UTC timezone). Open positions are always monitored for SL/TP even outside hours. Leave empty to trade 24/7.'
          )}
        </p>
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2">
            <label className="text-xs text-gray-400">{t('Dalle (UTC)', 'From (UTC)')}</label>
            <select
              value={form.trading_start_hour ?? ''}
              onChange={(e) => setForm({
                ...form,
                trading_start_hour: e.target.value === '' ? null : parseInt(e.target.value),
              })}
              className="bg-gray-800 border border-gray-700 rounded-lg px-2 py-1.5 text-sm text-white focus:outline-none focus:border-blue-500"
            >
              <option value="">24/7</option>
              {Array.from({ length: 24 }, (_, i) => (
                <option key={i} value={i}>{String(i).padStart(2, '0')}:00</option>
              ))}
            </select>
          </div>
          <span className="text-gray-600">—</span>
          <div className="flex items-center gap-2">
            <label className="text-xs text-gray-400">{t('Alle (UTC)', 'To (UTC)')}</label>
            <select
              value={form.trading_end_hour ?? ''}
              onChange={(e) => setForm({
                ...form,
                trading_end_hour: e.target.value === '' ? null : parseInt(e.target.value),
              })}
              className="bg-gray-800 border border-gray-700 rounded-lg px-2 py-1.5 text-sm text-white focus:outline-none focus:border-blue-500"
            >
              <option value="">24/7</option>
              {Array.from({ length: 24 }, (_, i) => (
                <option key={i} value={i}>{String(i).padStart(2, '0')}:00</option>
              ))}
            </select>
          </div>
        </div>
        {form.trading_start_hour != null && form.trading_end_hour != null && (
          <p className="text-xs text-blue-400">
            {form.trading_start_hour <= form.trading_end_hour
              ? t(
                  `Il bot opera dalle ${String(form.trading_start_hour).padStart(2,'0')}:00 alle ${String(form.trading_end_hour).padStart(2,'0')}:00 UTC`,
                  `Bot trades from ${String(form.trading_start_hour).padStart(2,'0')}:00 to ${String(form.trading_end_hour).padStart(2,'0')}:00 UTC`
                )
              : t(
                  `Il bot opera dalle ${String(form.trading_start_hour).padStart(2,'0')}:00 alle ${String(form.trading_end_hour).padStart(2,'0')}:00 UTC (notturno)`,
                  `Bot trades from ${String(form.trading_start_hour).padStart(2,'0')}:00 to ${String(form.trading_end_hour).padStart(2,'0')}:00 UTC (overnight)`
                )
            }
          </p>
        )}
      </section>

      {/* Trading Mode */}
      <section className="bg-gray-900 border border-gray-800 rounded-lg p-4 space-y-3">
        <h3 className="text-sm font-semibold text-white">{t('Modalita Trading', 'Trading Mode')}</h3>
        <div className="flex gap-3">
          <button
            onClick={() => setForm({ ...form, trading_mode: 'paper' })}
            className={`flex-1 py-2 rounded-lg text-sm font-medium transition-colors ${
              form.trading_mode === 'paper'
                ? 'bg-emerald-600 text-white'
                : 'bg-gray-800 text-gray-400 hover:bg-gray-700'
            }`}
          >
            {t('Simulato (Paper)', 'Paper (Simulated)')}
          </button>
          <button
            onClick={() => setForm({ ...form, trading_mode: 'live' })}
            className={`flex-1 py-2 rounded-lg text-sm font-medium transition-colors ${
              form.trading_mode === 'live'
                ? 'bg-red-600 text-white'
                : 'bg-gray-800 text-gray-400 hover:bg-gray-700'
            }`}
          >
            Live
          </button>
        </div>
        {form.trading_mode === 'live' && (
          <div className="bg-red-900/30 border border-red-800/50 rounded-lg p-3 text-xs text-red-300">
            {t(
              'Attenzione: in modalita Live gli ordini vengono inviati realmente a Binance con le tue chiavi API.',
              'Warning: in Live mode, real orders are sent to Binance using your API keys.'
            )}
          </div>
        )}
      </section>

      {/* Paper Capital */}
      <section className="bg-gray-900 border border-gray-800 rounded-lg p-4 space-y-3">
        <h3 className="text-sm font-semibold text-white">{t('Capitale Iniziale Paper', 'Paper Initial Capital')}</h3>
        <div className="flex items-center gap-2">
          <input
            type="number"
            value={form.paper_initial_capital}
            onChange={(e) => setForm({ ...form, paper_initial_capital: parseFloat(e.target.value) || 0 })}
            className="w-40 bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500"
          />
          <span className="text-sm text-gray-400">USDT</span>
        </div>
      </section>

      {/* Live Keys — shown first as primary */}
      <section className="bg-gray-900 border border-gray-800 rounded-lg p-4 space-y-3">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-white">Binance Live API</h3>
          {keys?.has_live_keys && (
            <span className="text-xs text-emerald-400">{t('Configurate', 'Configured')}: {keys.binance_api_key}</span>
          )}
        </div>
        <div className="bg-yellow-900/30 border border-yellow-800/50 rounded-lg p-3 text-xs text-yellow-300">
          {t(
            'Abilita SOLO "Enable Spot & Margin Trading". MAI abilitare "Enable Withdrawals".',
            'Enable ONLY "Enable Spot & Margin Trading". NEVER enable "Enable Withdrawals".'
          )}
        </div>
        <input
          type="text"
          placeholder="API Key"
          value={form.binance_api_key}
          onChange={(e) => setForm({ ...form, binance_api_key: e.target.value })}
          className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white placeholder-gray-600 focus:outline-none focus:border-blue-500"
        />
        <input
          type="password"
          placeholder="API Secret"
          value={form.binance_api_secret}
          onChange={(e) => setForm({ ...form, binance_api_secret: e.target.value })}
          className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white placeholder-gray-600 focus:outline-none focus:border-blue-500"
        />
      </section>

      {/* Testnet Keys */}
      <section className="bg-gray-900 border border-gray-800 rounded-lg p-4 space-y-3">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-white">Binance Testnet API</h3>
          {keys?.has_testnet_keys && (
            <span className="text-xs text-emerald-400">{t('Configurate', 'Configured')}: {keys.binance_testnet_api_key}</span>
          )}
        </div>
        <p className="text-xs text-gray-500">
          {t('Per il paper trading. Ottieni le chiavi su testnet.binance.vision', 'For paper trading. Get keys at testnet.binance.vision')}
        </p>
        <input
          type="text"
          placeholder="API Key"
          value={form.binance_testnet_api_key}
          onChange={(e) => setForm({ ...form, binance_testnet_api_key: e.target.value })}
          className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white placeholder-gray-600 focus:outline-none focus:border-blue-500"
        />
        <input
          type="password"
          placeholder="API Secret"
          value={form.binance_testnet_api_secret}
          onChange={(e) => setForm({ ...form, binance_testnet_api_secret: e.target.value })}
          className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white placeholder-gray-600 focus:outline-none focus:border-blue-500"
        />
      </section>

      {/* Save */}
      {error && (
        <div className="bg-red-900/30 border border-red-800/50 rounded-lg p-3 text-sm text-red-300">{error}</div>
      )}
      {saved && (
        <div className="bg-emerald-900/30 border border-emerald-800/50 rounded-lg p-3 text-sm text-emerald-300">
          {t('Impostazioni salvate con successo!', 'Settings saved successfully!')}
        </div>
      )}
      <button
        onClick={handleSave}
        className="px-6 py-2.5 bg-blue-600 hover:bg-blue-700 text-white font-medium rounded-lg transition-colors"
      >
        {t('Salva Impostazioni', 'Save Settings')}
      </button>
    </div>
  )
}
