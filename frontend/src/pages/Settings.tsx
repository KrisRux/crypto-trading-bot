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
  telegram_chat_id: string
  telegram_enabled: boolean
}

export default function Settings() {
  const { lang } = useLang()
  useAuth() // ensure authenticated
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
    telegram_chat_id: '',
    telegram_enabled: false,
  })
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState('')
  const [clearingType, setClearingType] = useState<'live' | 'testnet' | null>(null)
  const [testingTelegram, setTestingTelegram] = useState(false)

  const loadKeys = useCallback(() => {
    api.getMe()  // ensures cookie is valid before fetching settings
      .then(() =>
        fetch('/api/settings/keys', { credentials: 'include' })
      )
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
          telegram_chat_id: data.telegram_chat_id || '',
          telegram_enabled: data.telegram_enabled || false,
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
        telegram_chat_id: form.telegram_chat_id,
        telegram_enabled: form.telegram_enabled,
      }
      if (form.binance_api_key) body.binance_api_key = form.binance_api_key
      if (form.binance_api_secret) body.binance_api_secret = form.binance_api_secret
      if (form.binance_testnet_api_key) body.binance_testnet_api_key = form.binance_testnet_api_key
      if (form.binance_testnet_api_secret) body.binance_testnet_api_secret = form.binance_testnet_api_secret

      const resp = await fetch('/api/settings/keys', {
        method: 'PUT',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
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

  const handleClearKeys = async (type: 'live' | 'testnet') => {
    const label = type === 'live'
      ? t('Live API', 'Live API')
      : t('Testnet API', 'Testnet API')
    const confirmed = confirm(
      t(
        `Sei sicuro di voler eliminare le chiavi ${label}? Questa azione è irreversibile.`,
        `Are you sure you want to delete the ${label} keys? This action cannot be undone.`
      )
    )
    if (!confirmed) return
    setClearingType(type)
    try {
      await api.clearApiKeys(type)
      loadKeys()
    } catch (e) {
      setError(String(e))
    } finally {
      setClearingType(null)
    }
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
        {form.trading_enabled && form.trading_mode !== 'dry_run' && (
          (form.trading_mode === 'paper' && !keys?.has_testnet_keys) ||
          (form.trading_mode === 'live' && !keys?.has_live_keys)
        ) && (
          <div className="bg-red-900/30 border border-red-800/50 rounded-lg p-3 text-xs text-red-300">
            {form.trading_mode === 'paper'
              ? t(
                  'Trading abilitato ma chiavi Testnet mancanti! Configura le chiavi Testnet qui sotto per operare in Paper.',
                  'Trading enabled but Testnet keys missing! Configure Testnet keys below to trade in Paper mode.'
                )
              : t(
                  'Trading abilitato ma chiavi Live mancanti! Configura le chiavi Live qui sotto per operare.',
                  'Trading enabled but Live keys missing! Configure Live keys below to trade.'
                )
            }
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
            onClick={() => setForm({ ...form, trading_mode: 'dry_run' })}
            className={`flex-1 py-2 rounded-lg text-sm font-medium transition-colors ${
              form.trading_mode === 'dry_run'
                ? 'bg-amber-600 text-white'
                : 'bg-gray-800 text-gray-400 hover:bg-gray-700'
            }`}
          >
            Dry Run
          </button>
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
        {form.trading_mode === 'dry_run' && (
          <div className="bg-amber-900/30 border border-amber-800/50 rounded-lg p-3 text-xs text-amber-300">
            {t(
              'Dry Run: il bot analizza il mercato e logga tutto quello che farebbe (segnali, size, SL/TP, rischio), ma non apre nessuna posizione e non chiama Binance. Nessuna chiave API necessaria.',
              'Dry Run: the bot analyses the market and logs everything it would do (signals, size, SL/TP, risk), but opens no positions and makes no Binance API calls. No API keys required.'
            )}
          </div>
        )}
        {form.trading_mode === 'live' && (
          <div className="bg-red-900/30 border border-red-800/50 rounded-lg p-3 text-xs text-red-300">
            {t(
              'Attenzione: in modalita Live gli ordini vengono inviati realmente a Binance con le tue chiavi API.',
              'Warning: in Live mode, real orders are sent to Binance using your API keys.'
            )}
          </div>
        )}
      </section>

      {/* Testnet Keys — for Paper mode */}
      <section className="bg-gray-900 border border-gray-800 rounded-lg p-4 space-y-3">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-sm font-semibold text-white">
              Binance Testnet API
              <span className="ml-2 text-[10px] font-normal text-gray-500">({t('per Paper', 'for Paper')})</span>
            </h3>
            {keys?.has_testnet_keys && (
              <span className="text-xs text-emerald-400 mt-0.5 block">{t('Configurate', 'Configured')}: {keys.binance_testnet_api_key}</span>
            )}
          </div>
          {keys?.has_testnet_keys && (
            <button
              onClick={() => handleClearKeys('testnet')}
              disabled={clearingType === 'testnet'}
              className="px-3 py-1.5 bg-red-900/50 hover:bg-red-800/60 border border-red-800/50 text-red-300 text-xs font-medium rounded-lg transition-colors disabled:opacity-50"
            >
              {clearingType === 'testnet'
                ? t('Eliminazione...', 'Deleting...')
                : t('Elimina chiavi', 'Delete keys')}
            </button>
          )}
        </div>
        <p className="text-xs text-gray-500">
          {t(
            'Per la modalita Paper. Ordini reali su Binance Testnet (soldi virtuali). Ottieni le chiavi su testnet.binance.vision',
            'For Paper mode. Real orders on Binance Testnet (virtual money). Get keys at testnet.binance.vision'
          )}
        </p>
        <input
          type="text"
          placeholder={keys?.has_testnet_keys ? t('Lascia vuoto per non modificare', 'Leave empty to keep current') : 'Testnet API Key'}
          value={form.binance_testnet_api_key}
          onChange={(e) => setForm({ ...form, binance_testnet_api_key: e.target.value })}
          className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white placeholder-gray-600 focus:outline-none focus:border-blue-500"
        />
        <input
          type="password"
          placeholder={keys?.has_testnet_keys ? t('Lascia vuoto per non modificare', 'Leave empty to keep current') : 'Testnet API Secret'}
          value={form.binance_testnet_api_secret}
          onChange={(e) => setForm({ ...form, binance_testnet_api_secret: e.target.value })}
          className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white placeholder-gray-600 focus:outline-none focus:border-blue-500"
        />
      </section>

      {/* Live Keys — for Live mode */}
      <section className="bg-gray-900 border border-gray-800 rounded-lg p-4 space-y-3">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-sm font-semibold text-white">
              Binance Live API
              <span className="ml-2 text-[10px] font-normal text-gray-500">({t('per Live', 'for Live')})</span>
            </h3>
            {keys?.has_live_keys && (
              <span className="text-xs text-emerald-400 mt-0.5 block">{t('Configurate', 'Configured')}: {keys.binance_api_key}</span>
            )}
          </div>
          {keys?.has_live_keys && (
            <button
              onClick={() => handleClearKeys('live')}
              disabled={clearingType === 'live'}
              className="px-3 py-1.5 bg-red-900/50 hover:bg-red-800/60 border border-red-800/50 text-red-300 text-xs font-medium rounded-lg transition-colors disabled:opacity-50"
            >
              {clearingType === 'live'
                ? t('Eliminazione...', 'Deleting...')
                : t('Elimina chiavi', 'Delete keys')}
            </button>
          )}
        </div>
        <div className="bg-red-900/30 border border-red-800/50 rounded-lg p-3 text-xs text-red-300">
          {t(
            'ATTENZIONE: ordini con denaro reale! Abilita SOLO "Enable Spot & Margin Trading". MAI "Enable Withdrawals".',
            'WARNING: real money orders! Enable ONLY "Enable Spot & Margin Trading". NEVER "Enable Withdrawals".'
          )}
        </div>
        <input
          type="text"
          placeholder={keys?.has_live_keys ? t('Lascia vuoto per non modificare', 'Leave empty to keep current') : 'API Key'}
          value={form.binance_api_key}
          onChange={(e) => setForm({ ...form, binance_api_key: e.target.value })}
          className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white placeholder-gray-600 focus:outline-none focus:border-blue-500"
        />
        <input
          type="password"
          placeholder={keys?.has_live_keys ? t('Lascia vuoto per non modificare', 'Leave empty to keep current') : 'API Secret'}
          value={form.binance_api_secret}
          onChange={(e) => setForm({ ...form, binance_api_secret: e.target.value })}
          className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white placeholder-gray-600 focus:outline-none focus:border-blue-500"
        />
      </section>

      {/* Telegram Notifications */}
      <section className="bg-gray-900 border border-gray-800 rounded-lg p-4 space-y-3">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-sm font-semibold text-white">
              {t('Notifiche Telegram', 'Telegram Notifications')}
            </h3>
            <p className="text-xs text-gray-500 mt-0.5">
              {t(
                'Ricevi notifiche su Telegram per cambi profilo, drawdown, perdite consecutive e report giornaliero.',
                'Receive Telegram notifications for profile switches, drawdown, consecutive losses, and daily reports.'
              )}
            </p>
          </div>
          <button
            onClick={() => setForm({ ...form, telegram_enabled: !form.telegram_enabled })}
            className={`relative w-12 h-6 rounded-full transition-colors ${
              form.telegram_enabled ? 'bg-blue-600' : 'bg-gray-700'
            }`}
          >
            <span className={`absolute top-0.5 left-0.5 w-5 h-5 bg-white rounded-full transition-transform ${
              form.telegram_enabled ? 'translate-x-6' : ''
            }`} />
          </button>
        </div>
        <input
          type="text"
          placeholder="Telegram Chat ID (es. 17519601)"
          value={form.telegram_chat_id}
          onChange={(e) => setForm({ ...form, telegram_chat_id: e.target.value })}
          className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white placeholder-gray-600 focus:outline-none focus:border-blue-500"
        />
        <div className="flex items-center gap-3">
          <p className="text-xs text-gray-500 flex-1">
            {t(
              'Invia /start al bot Telegram del progetto, poi usa getUpdates per trovare il tuo Chat ID.',
              'Send /start to the project Telegram bot, then use getUpdates to find your Chat ID.'
            )}
          </p>
          {form.telegram_chat_id && (
            <button
              onClick={async () => {
                setTestingTelegram(true)
                try {
                  const resp = await fetch('/api/adaptive/telegram/test', {
                    method: 'POST', credentials: 'include',
                  })
                  if (!resp.ok) {
                    const err = await resp.json().catch(() => ({ detail: 'Error' }))
                    alert(err.detail || 'Errore invio test')
                  } else {
                    alert(t('Messaggio di test inviato!', 'Test message sent!'))
                  }
                } catch {
                  alert(t('Errore di rete', 'Network error'))
                } finally {
                  setTestingTelegram(false)
                }
              }}
              disabled={testingTelegram}
              className="px-3 py-1.5 bg-blue-900/50 hover:bg-blue-800/60 border border-blue-800/50 text-blue-300 text-xs font-medium rounded-lg transition-colors disabled:opacity-50 whitespace-nowrap"
            >
              {testingTelegram ? '...' : t('Invia Test', 'Send Test')}
            </button>
          )}
        </div>
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
