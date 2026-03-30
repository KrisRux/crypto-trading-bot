import { Routes, Route, NavLink, Navigate, useNavigate } from 'react-router-dom'
import { useState, useEffect, useCallback } from 'react'
import Assets from './pages/Assets'
import Dashboard from './pages/Dashboard'
import Strategies from './pages/Strategies'
import Logs from './pages/Logs'
import Manual from './pages/Manual'
import Skills from './pages/Skills'
import Users from './pages/Users'
import Settings from './pages/Settings'
import Login from './pages/Login'
import { api } from './api'
import { Lang } from './i18n'
import { LangContext, useLang } from './hooks/useLang'
import { AuthContext } from './hooks/useAuth'
import { useIdleTimeout } from './hooks/useIdleTimeout'

function UtcClock() {
  const [time, setTime] = useState('')
  useEffect(() => {
    const update = () => {
      const now = new Date()
      setTime(now.toISOString().slice(11, 19))
    }
    update()
    const id = setInterval(update, 1000)
    return () => clearInterval(id)
  }, [])
  return (
    <span className="hidden sm:inline text-[10px] font-mono text-gray-600" title="UTC Time">
      UTC {time}
    </span>
  )
}

function AppContent() {
  const [menuOpen, setMenuOpen] = useState(false)
  const { lang, setLang, t } = useLang()
  const navigate = useNavigate()

  // Auth state — token lives in httpOnly cookie (not in JS/localStorage).
  // role and displayName are stored in localStorage as UI hints only;
  // the real auth check is done server-side on every request via the cookie.
  const [isAuthenticated, setIsAuthenticated] = useState<boolean | null>(null) // null = checking
  const [role, setRole] = useState<string>(() => localStorage.getItem('auth_role') || '')
  const [displayName, setDisplayName] = useState<string>(() => localStorage.getItem('auth_name') || '')
  const [sessionTimeout, setSessionTimeout] = useState<number>(() =>
    parseInt(localStorage.getItem('session_timeout') || '30', 10)
  )
  const isAdmin = role === 'admin'

  // On mount, verify auth by calling /api/me — the httpOnly cookie is sent automatically.
  useEffect(() => {
    api.getMe()
      .then((me) => {
        setRole(me.role)
        setDisplayName(me.display_name)
        localStorage.setItem('auth_role', me.role)
        localStorage.setItem('auth_name', me.display_name)
        setIsAuthenticated(true)
      })
      .catch(() => {
        setIsAuthenticated(false)
      })
  }, [])

  // Listen for session-expired events dispatched by the API client (any 401).
  // This avoids window.location.href reloads that caused infinite loops on mobile.
  useEffect(() => {
    const handler = () => {
      localStorage.removeItem('auth_role')
      localStorage.removeItem('auth_name')
      localStorage.removeItem('session_timeout')
      setRole('')
      setDisplayName('')
      setIsAuthenticated(false)
    }
    window.addEventListener('auth:expired', handler)
    return () => window.removeEventListener('auth:expired', handler)
  }, [])

  const login = useCallback((newRole: string, newName: string, timeoutMinutes?: number) => {
    // Token is already set as httpOnly cookie by the server — we only store UI hints.
    localStorage.setItem('auth_role', newRole)
    localStorage.setItem('auth_name', newName)
    if (timeoutMinutes) {
      localStorage.setItem('session_timeout', String(timeoutMinutes))
      setSessionTimeout(timeoutMinutes)
    }
    setRole(newRole)
    setDisplayName(newName)
    setIsAuthenticated(true)
    navigate('/')
  }, [navigate])

  const logout = useCallback(async () => {
    try {
      await api.logout() // clears the httpOnly cookie server-side
    } catch {
      // proceed with client-side cleanup regardless
    }
    localStorage.removeItem('auth_role')
    localStorage.removeItem('auth_name')
    localStorage.removeItem('session_timeout')
    setRole('')
    setDisplayName('')
    setIsAuthenticated(false)
    navigate('/login')
  }, [navigate])

  // Auto-logout on inactivity
  useIdleTimeout(sessionTimeout, logout, isAuthenticated === true)

  const navLinkClass = ({ isActive }: { isActive: boolean }) =>
    `px-3 py-2 rounded-md text-sm font-medium transition-colors ${
      isActive
        ? 'bg-gray-700 text-white'
        : 'text-gray-300 hover:bg-gray-700 hover:text-white'
    }`

  // Still checking auth (first render)
  if (isAuthenticated === null) {
    return (
      <div className="min-h-screen bg-gray-950 flex items-center justify-center">
        <div className="text-gray-500 text-sm">Loading...</div>
      </div>
    )
  }

  // Auth context value — token is null since it lives in httpOnly cookie
  const authValue = {
    token: null,
    role,
    displayName,
    login,
    logout,
    isAuthenticated: isAuthenticated === true,
    isAdmin,
  }

  if (!isAuthenticated) {
    return (
      <AuthContext.Provider value={authValue}>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="*" element={<Navigate to="/login" replace />} />
        </Routes>
      </AuthContext.Provider>
    )
  }

  return (
    <AuthContext.Provider value={authValue}>
      <div className="min-h-screen bg-gray-950">
        {/* Navigation bar */}
        <nav className="bg-gray-900 border-b border-gray-800">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
            <div className="flex items-center justify-between h-16">
              <div className="flex items-center gap-4">
                <span className="text-xl font-bold text-white">CryptoBot</span>
                <div className="hidden md:flex items-center gap-1">
                  <NavLink to="/" className={navLinkClass}>{t('nav_dashboard')}</NavLink>
                  <NavLink to="/wallet" className={navLinkClass}>{t('nav_assets')}</NavLink>
                  <NavLink to="/strategies" className={navLinkClass}>{t('nav_strategies')}</NavLink>
                  <NavLink to="/skills" className={navLinkClass}>{t('nav_skills')}</NavLink>
                  <NavLink to="/manual" className={navLinkClass}>{t('nav_manual')}</NavLink>
                  {role !== 'guest' && <NavLink to="/settings" className={navLinkClass}>{t('nav_settings')}</NavLink>}
                  {isAdmin && <NavLink to="/users" className={navLinkClass}>Utenti</NavLink>}
                  <NavLink to="/logs" className={navLinkClass}>{t('nav_logs')}</NavLink>
                </div>
              </div>

              <div className="flex items-center gap-3">
                <UtcClock />
                <span className="hidden sm:inline text-xs text-gray-500">{displayName}</span>
                {/* Language selector */}
                <button
                  onClick={() => setLang(lang === 'it' ? 'en' : 'it')}
                  className="px-2 py-1 rounded text-xs font-bold bg-gray-700 text-gray-200 hover:bg-gray-600 transition-colors uppercase"
                  title={lang === 'it' ? 'Switch to English' : 'Passa a Italiano'}
                >
                  {lang === 'it' ? 'EN' : 'IT'}
                </button>

                {/* Logout */}
                <button
                  onClick={logout}
                  className="flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium text-gray-400 hover:text-white hover:bg-red-900/50 border border-transparent hover:border-red-800/50 transition-colors"
                >
                  <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                      d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1" />
                  </svg>
                  <span className="hidden sm:inline">Logout</span>
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
                <NavLink to="/wallet" className={navLinkClass} onClick={() => setMenuOpen(false)}>
                  {t('nav_assets')}
                </NavLink>
                <NavLink to="/strategies" className={navLinkClass} onClick={() => setMenuOpen(false)}>
                  {t('nav_strategies')}
                </NavLink>
                <NavLink to="/skills" className={navLinkClass} onClick={() => setMenuOpen(false)}>
                  {t('nav_skills')}
                </NavLink>
                <NavLink to="/manual" className={navLinkClass} onClick={() => setMenuOpen(false)}>
                  {t('nav_manual')}
                </NavLink>
                {role !== 'guest' && (
                  <NavLink to="/settings" className={navLinkClass} onClick={() => setMenuOpen(false)}>
                    {t('nav_settings')}
                  </NavLink>
                )}
                {isAdmin && (
                  <NavLink to="/users" className={navLinkClass} onClick={() => setMenuOpen(false)}>
                    Utenti
                  </NavLink>
                )}
                <NavLink to="/logs" className={navLinkClass} onClick={() => setMenuOpen(false)}>
                  {t('nav_logs')}
                </NavLink>
              </div>
            )}
          </div>
        </nav>

        {/* Page content */}
        <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/wallet" element={<Assets />} />
            <Route path="/strategies" element={<Strategies />} />
            <Route path="/logs" element={<Logs />} />
            <Route path="/skills" element={<Skills />} />
            <Route path="/manual" element={<Manual />} />
            {role !== 'guest' && <Route path="/settings" element={<Settings />} />}
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
