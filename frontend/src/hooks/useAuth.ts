import { createContext, useContext } from 'react'

export interface AuthState {
  token: string | null
  login: (token: string) => void
  logout: () => void
  isAuthenticated: boolean
}

export const AuthContext = createContext<AuthState>({
  token: null,
  login: () => {},
  logout: () => {},
  isAuthenticated: false,
})

export function useAuth() {
  return useContext(AuthContext)
}
