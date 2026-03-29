import { createContext, useContext } from 'react'

export interface AuthState {
  token: null  // always null — token lives in httpOnly cookie, not in JS
  role: string
  displayName: string
  login: (role: string, displayName: string, timeoutMinutes?: number) => void
  logout: () => void
  isAuthenticated: boolean
  isAdmin: boolean
}

export const AuthContext = createContext<AuthState>({
  token: null,
  role: '',
  displayName: '',
  login: () => {},
  logout: () => {},
  isAuthenticated: false,
  isAdmin: false,
})

export function useAuth() {
  return useContext(AuthContext)
}
