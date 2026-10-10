import { createContext } from 'react'
import type { Session } from '@supabase/supabase-js'

export type Principal = {
  authenticated: true
  user_id: string
  email?: string
  principal_type: 'admin' | 'organizer'
  username?: string
  name?: string
}

export type AuthStatus = 'initializing' | 'loading' | 'anonymous' | 'authenticated' | 'profile_error'
export type AuthContextValue = {
  status: AuthStatus
  session: Session | null
  principal: Principal | null
  profileError: string | null
  login: (email: string, password: string) => Promise<Principal>
  logout: () => Promise<void>
}

export const AuthContext = createContext<AuthContextValue | null>(null)
