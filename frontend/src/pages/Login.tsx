import { useState, FormEvent } from 'react'
import { useAuth } from '../hooks/useAuth'
import { useLang } from '../hooks/useLang'
import { api } from '../api'

export default function Login() {
  const { login } = useAuth()
  const { lang } = useLang()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const t = (it: string, en: string) => (lang === 'it' ? it : en)

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      const res = await api.login(username, password)
      // Token is set as httpOnly cookie by the server — pass only UI hints
      login(res.role, res.display_name, res.session_timeout_minutes)
    } catch {
      setError(t('Credenziali non valide', 'Invalid credentials'))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-gray-950 flex items-center justify-center px-4">
      <div className="w-full max-w-sm">
        {/* Logo / title */}
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-gradient-to-br from-blue-500 to-emerald-500 mb-4">
            <svg className="w-8 h-8 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
                d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6" />
            </svg>
          </div>
          <h1 className="text-2xl font-bold text-white">CryptoBot</h1>
          <p className="text-sm text-gray-500 mt-1">
            {t('Trading automatico di criptovalute', 'Automated crypto trading')}
          </p>
        </div>

        {/* Login card */}
        <form onSubmit={handleSubmit} className="bg-gray-900 border border-gray-800 rounded-xl p-6 space-y-5 shadow-2xl">
          <div>
            <label className="block text-xs font-medium text-gray-400 mb-1.5">
              {t('Nome utente', 'Username')}
            </label>
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              autoFocus
              required
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2.5 text-sm text-white placeholder-gray-600 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500 transition-colors"
              placeholder={t('Inserisci nome utente', 'Enter username')}
            />
          </div>

          <div>
            <label className="block text-xs font-medium text-gray-400 mb-1.5">
              Password
            </label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              required
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2.5 text-sm text-white placeholder-gray-600 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500 transition-colors"
              placeholder={t('Inserisci password', 'Enter password')}
            />
          </div>

          {error && (
            <div className="bg-red-900/30 border border-red-800/50 rounded-lg px-3 py-2 text-sm text-red-300">
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full bg-blue-600 hover:bg-blue-700 disabled:bg-blue-800 disabled:cursor-not-allowed text-white font-medium py-2.5 rounded-lg transition-colors text-sm"
          >
            {loading
              ? t('Accesso in corso...', 'Signing in...')
              : t('Accedi', 'Sign in')}
          </button>
        </form>

        <p className="text-center text-[11px] text-gray-600 mt-6">
          {t(
            'Connessione protetta. Non condividere le tue credenziali.',
            'Secure connection. Do not share your credentials.'
          )}
        </p>
      </div>
    </div>
  )
}
