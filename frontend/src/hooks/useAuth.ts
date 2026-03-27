import { createContext, useContext } from 'react'

export interface AuthState {
  token: string | null
  role: string
  displayName: string
  login: (token: string, role: string, displayName: string) => void
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
