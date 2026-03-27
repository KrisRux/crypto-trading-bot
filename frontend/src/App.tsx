import { Routes, Route, NavLink, Navigate, useNavigate } from 'react-router-dom'
import { useState, useEffect, useCallback } from 'react'
import Dashboard from './pages/Dashboard'
import Strategies from './pages/Strategies'
import Logs from './pages/Logs'
import Manual from './pages/Manual'
import Skills from './pages/Skills'
import Users from './pages/Users'
import Login from './pages/Login'
import { api } from './api'
import { Lang } from './i18n'
import { LangContext, useLang } from './hooks/useLang'
import { AuthContext } from './hooks/useAuth'

function AppContent() {
  const [mode, setMode] = useState<string>('paper')
  const [menuOpen, setMenuOpen] = useState(false)
  const { lang, setLang, t } = useLang()
  const navigate = useNavigate()

  // Auth state
  const [token, setToken] = useState<string | null>(() =>
    localStorage.getItem('auth_token')
  )
  const [role, setRole] = useState<string>(() =>
    localStorage.getItem('auth_role') || ''
  )
  const [displayName, setDisplayName] = useState<string>(() =>
    localStorage.getItem('auth_name') || ''
  )
  const isAuthenticated = !!token
  const isAdmin = role === 'admin'

  const login = useCallback((newToken: string, newRole: string, newName: string) => {
    localStorage.setItem('auth_token', newToken)
    localStorage.setItem('auth_role', newRole)
    localStorage.setItem('auth_name', newName)
    setToken(newToken)
    setRole(newRole)
    setDisplayName(newName)
    navigate('/')
  }, [navigate])

  const logout = useCallback(() => {
    localStorage.removeItem('auth_token')
    localStorage.removeItem('auth_role')
    localStorage.removeItem('auth_name')
    setToken(null)
    setRole('')
    setDisplayName('')
    navigate('/login')
  }, [navigate])

  useEffect(() => {
    if (isAuthenticated) {
      api.getMode().then((d) => setMode(d.mode)).catch(() => {})
    }
  }, [isAuthenticated])

  const toggleMode = async () => {
    const newMode = mode === 'paper' ? 'live' : 'paper'
    try {
      const res = await api.switchMode(newMode)
      setMode(res.mode)
    } catch (e) {
      alert(`${t('mode_switch_failed')}: ${e}`)
    }
  }

  const navLinkClass = ({ isActive }: { isActive: boolean }) =>
    `px-3 py-2 rounded-md text-sm font-medium transition-colors ${
      isActive
        ? 'bg-gray-700 text-white'
        : 'text-gray-300 hover:bg-gray-700 hover:text-white'
    }`

  // If not authenticated, show login
  if (!isAuthenticated) {
    return (
      <AuthContext.Provider value={{ token, role, displayName, login, logout, isAuthenticated, isAdmin }}>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="*" element={<Navigate to="/login" replace />} />
        </Routes>
      </AuthContext.Provider>
    )
  }

  return (
    <AuthContext.Provider value={{ token, role, displayName, login, logout, isAuthenticated, isAdmin }}>
      <div className="min-h-screen bg-gray-950">
        {/* Navigation bar */}
        <nav className="bg-gray-900 border-b border-gray-800">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
            <div className="flex items-center justify-between h-16">
              <div className="flex items-center gap-4">
                <span className="text-xl font-bold text-white">CryptoBot</span>
                <div className="hidden md:flex items-center gap-1">
                  <NavLink to="/" className={navLinkClass}>{t('nav_dashboard')}</NavLink>
                  <NavLink to="/strategies" className={navLinkClass}>{t('nav_strategies')}</NavLink>
                  <NavLink to="/logs" className={navLinkClass}>{t('nav_logs')}</NavLink>
                  <NavLink to="/skills" className={navLinkClass}>{t('nav_skills')}</NavLink>
                  <NavLink to="/manual" className={navLinkClass}>{t('nav_manual')}</NavLink>
                  {isAdmin && <NavLink to="/users" className={navLinkClass}>Utenti</NavLink>}
                </div>
              </div>

              <div className="flex items-center gap-3">
                <span className="hidden sm:inline text-xs text-gray-500">{displayName}</span>
                {/* Language selector */}
                <button
                  onClick={() => setLang(lang === 'it' ? 'en' : 'it')}
                  className="px-2 py-1 rounded text-xs font-bold bg-gray-700 text-gray-200 hover:bg-gray-600 transition-colors uppercase"
                  title={lang === 'it' ? 'Switch to English' : 'Passa a Italiano'}
                >
                  {lang === 'it' ? 'EN' : 'IT'}
                </button>

                {/* Mode badge */}
                <button
                  onClick={toggleMode}
                  className={`px-3 py-1 rounded-full text-xs font-bold uppercase tracking-wider transition-colors ${
                    mode === 'live'
                      ? 'bg-red-600 text-white hover:bg-red-700'
                      : 'bg-emerald-600 text-white hover:bg-emerald-700'
                  }`}
                >
                  {mode === 'live' ? t('mode_live') : t('mode_paper')}
                </button>

                {/* Logout */}
                <button
                  onClick={logout}
                  className="px-2 py-1 rounded text-xs text-gray-400 hover:text-white hover:bg-gray-700 transition-colors"
                  title="Logout"
                >
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                      d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1" />
                  </svg>
                </button>

                {/* Mobile hamburger */}
                <button
                  className="md:hidden text-gray-300 hover:text-white"
                  onClick={() => setMenuOpen(!menuOpen)}
                >
                  <svg className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                      d={menuOpen ? 'M6 18L18 6M6 6l12 12' : 'M4 6h16M4 12h16M4 18h16'} />
                  </svg>
                </button>
              </div>
            </div>

            {/* Mobile nav links */}
            {menuOpen && (
              <div className="md:hidden pb-3 space-y-1">
                <NavLink to="/" className={navLinkClass} onClick={() => setMenuOpen(false)}>
                  {t('nav_dashboard')}
                </NavLink>
                <NavLink to="/strategies" className={navLinkClass} onClick={() => setMenuOpen(false)}>
                  {t('nav_strategies')}
                </NavLink>
                <NavLink to="/logs" className={navLinkClass} onClick={() => setMenuOpen(false)}>
                  {t('nav_logs')}
                </NavLink>
                <NavLink to="/skills" className={navLinkClass} onClick={() => setMenuOpen(false)}>
                  {t('nav_skills')}
                </NavLink>
                <NavLink to="/manual" className={navLinkClass} onClick={() => setMenuOpen(false)}>
                  {t('nav_manual')}
                </NavLink>
                {isAdmin && (
                  <NavLink to="/users" className={navLinkClass} onClick={() => setMenuOpen(false)}>
                    Utenti
                  </NavLink>
                )}
              </div>
            )}
          </div>
        </nav>

        {/* Mode banner */}
        <div className={`text-center py-1 text-xs font-semibold ${
          mode === 'live' ? 'bg-red-900/50 text-red-300' : 'bg-emerald-900/50 text-emerald-300'
        }`}>
          {mode === 'live' ? t('mode_banner_live') : t('mode_banner_paper')}
        </div>

        {/* Page content */}
        <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
          <Routes>
            <Route path="/" element={<Dashboard mode={mode} />} />
            <Route path="/strategies" element={<Strategies />} />
            <Route path="/logs" element={<Logs mode={mode} />} />
            <Route path="/skills" element={<Skills />} />
            <Route path="/manual" element={<Manual />} />
            {isAdmin && <Route path="/users" element={<Users />} />}
            <Route path="/login" element={<Navigate to="/" replace />} />
          </Routes>
        </main>
      </div>
    </AuthContext.Provider>
  )
}

function App() {
  const [lang, setLang] = useState<Lang>(() => {
    const saved = localStorage.getItem('lang')
    return (saved === 'en' || saved === 'it') ? saved : 'it'
  })

  const changeLang = (l: Lang) => {
    setLang(l)
    localStorage.setItem('lang', l)
  }

  return (
    <LangContext.Provider value={{ lang, setLang: changeLang }}>
      <AppContent />
    </LangContext.Provider>
  )
}

export default App
