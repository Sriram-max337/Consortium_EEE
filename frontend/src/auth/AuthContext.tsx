import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import type { AuthError, Session } from '@supabase/supabase-js'
import { ApiError, apiGet } from '../lib/api'
import { supabase, supabaseConfigured } from '../lib/supabase'
import { AuthContext, type AuthStatus, type Principal } from './context'
import { AuthFlowError } from './errors'

const SESSION_RESTORE_TIMEOUT_MS = 12_000

function backendError(error: unknown): AuthFlowError {
  if (error instanceof AuthFlowError) return error
  if (error instanceof ApiError) {
    if (error.status === 401) {
      return new AuthFlowError('unauthorized', 'Your session was rejected. Sign in again and retry.')
    }
    if (error.status === 403) {
      return new AuthFlowError('inactive_profile', 'This account has no active Admin or Organizer profile. Contact an event administrator.')
    }
    if (error.kind === 'network') {
      return new AuthFlowError('network', error.message)
    }
    if (error.kind === 'malformed_response') {
      return new AuthFlowError('malformed_response', 'The backend returned an unreadable account profile. Please try again later.')
    }
    if (error.status >= 500) {
      return new AuthFlowError('server_error', 'The backend could not verify your account right now. Please try again later.')
    }
    return new AuthFlowError('server_error', `Account verification failed (HTTP ${error.status}). Please try again.`)
  }
  return new AuthFlowError('malformed_response', 'The backend returned an unrecognized account profile.')
}

function supabaseError(error: unknown): AuthFlowError {
  const authError = error as Partial<AuthError> & { code?: string; status?: number; name?: string; message?: string }
  const message = authError.message || ''
  const code = authError.code?.toLowerCase() || ''
  const name = authError.name?.toLowerCase() || ''

  if (code === 'email_not_confirmed' || /email not confirmed/i.test(message)) {
    return new AuthFlowError('email_not_confirmed', 'Confirm your email address before signing in.')
  }
  if (code === 'invalid_credentials' || /invalid login credentials/i.test(message)
    || authError.status === 400 || authError.status === 401) {
    return new AuthFlowError('invalid_credentials', 'The email or password is incorrect.')
  }
  if (name.includes('retryable') || /network|failed to fetch|fetch failed|timeout/i.test(message)
    || !navigator.onLine) {
    return new AuthFlowError('network', 'Could not reach Supabase Auth. Check your connection and try again.')
  }
  if (authError.status && authError.status >= 500) {
    return new AuthFlowError('server_error', 'Supabase Auth is temporarily unavailable. Please try again later.')
  }
  return new AuthFlowError('unknown', 'Sign-in could not be completed. Please try again.')
}

function isPrincipal(value: unknown): value is Principal {
  if (!value || typeof value !== 'object') return false
  const profile = value as Record<string, unknown>
  return profile.authenticated === true
    && typeof profile.user_id === 'string'
    && profile.user_id.length > 0
    && (profile.principal_type === 'admin' || profile.principal_type === 'organizer')
    && (profile.email === undefined || typeof profile.email === 'string')
    && (profile.username === undefined || typeof profile.username === 'string')
    && (profile.name === undefined || typeof profile.name === 'string')
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>('initializing')
  const [session, setSession] = useState<Session | null>(null)
  const [principal, setPrincipal] = useState<Principal | null>(null)
  const [profileError, setProfileError] = useState<string | null>(null)

  const generationRef = useRef(0)
  const activeTokenRef = useRef<string | null>(null)
  const statusRef = useRef<AuthStatus>('initializing')
  const sessionRef = useRef<Session | null>(null)
  const principalRef = useRef<Principal | null>(null)
  const successfulProfilesRef = useRef(new Map<string, Principal>())
  const profileRequestsRef = useRef(new Map<string, Promise<Principal>>())

  const updateAuthState = useCallback((
    nextStatus: AuthStatus,
    nextSession: Session | null,
    nextPrincipal: Principal | null,
    nextError: string | null,
  ) => {
    statusRef.current = nextStatus
    sessionRef.current = nextSession
    principalRef.current = nextPrincipal
    setStatus(nextStatus)
    setSession(nextSession)
    setPrincipal(nextPrincipal)
    setProfileError(nextError)
  }, [])

  const verifySession = useCallback((activeSession: Session): Promise<Principal> => {
    const token = activeSession.access_token
    const cached = successfulProfilesRef.current.get(token)
    if (cached) return Promise.resolve(cached)

    const pending = profileRequestsRef.current.get(token)
    if (pending) return pending

    const request = apiGet<unknown>('/auth/me', token).then((payload) => {
      if (!isPrincipal(payload)) {
        throw new AuthFlowError('malformed_response', 'The backend returned an invalid account profile.')
      }
      successfulProfilesRef.current.set(token, payload)
      return payload
    }).catch((error: unknown) => {
      throw backendError(error)
    }).finally(() => {
      if (profileRequestsRef.current.get(token) === request) {
        profileRequestsRef.current.delete(token)
      }
    })

    profileRequestsRef.current.set(token, request)
    return request
  }, [])

  const synchronizeSession = useCallback(async (nextSession: Session | null): Promise<Principal | null> => {
    if (!nextSession) {
      generationRef.current += 1
      activeTokenRef.current = null
      successfulProfilesRef.current.clear()
      profileRequestsRef.current.clear()
      updateAuthState('anonymous', null, null, null)
      return null
    }

    const token = nextSession.access_token
    if (activeTokenRef.current !== token) {
      generationRef.current += 1
      activeTokenRef.current = token
      updateAuthState('loading', nextSession, null, null)
    } else {
      sessionRef.current = nextSession
      setSession(nextSession)
      if (statusRef.current === 'authenticated' && principalRef.current?.user_id === nextSession.user.id) {
        return principalRef.current
      }
    }

    const generation = generationRef.current
    try {
      const verifiedPrincipal = await verifySession(nextSession)
      if (generation !== generationRef.current || activeTokenRef.current !== token) return null
      updateAuthState('authenticated', nextSession, verifiedPrincipal, null)
      return verifiedPrincipal
    } catch (error) {
      if (generation === generationRef.current && activeTokenRef.current === token) {
        const authError = backendError(error)
        updateAuthState('profile_error', nextSession, null, authError.message)
      }
      throw backendError(error)
    }
  }, [updateAuthState, verifySession])

  useEffect(() => {
    let alive = true
    const initialGeneration = generationRef.current
    const restoreTimeout = window.setTimeout(() => {
      if (!alive || generationRef.current !== initialGeneration || statusRef.current !== 'initializing') return
      generationRef.current += 1
      updateAuthState('profile_error', null, null, 'Session restoration timed out. Check your connection and sign in again.')
    }, SESSION_RESTORE_TIMEOUT_MS)

    const { data: subscription } = supabase.auth.onAuthStateChange((_event, nextSession) => {
      window.clearTimeout(restoreTimeout)
      void synchronizeSession(nextSession).catch((error: unknown) => {
        if (!alive || !nextSession || activeTokenRef.current !== nextSession.access_token) return
        const authError = backendError(error)
        if (statusRef.current !== 'profile_error') {
          updateAuthState('profile_error', sessionRef.current, null, authError.message)
        }
      })
    })

    void supabase.auth.getSession().then(({ data, error }) => {
      if (!alive || generationRef.current !== initialGeneration) return
      window.clearTimeout(restoreTimeout)
      if (error) {
        const authError = supabaseError(error)
        generationRef.current += 1
        updateAuthState('profile_error', null, null, authError.message)
        return
      }
      void synchronizeSession(data.session).catch((error: unknown) => {
        if (!alive || !data.session || activeTokenRef.current !== data.session.access_token
          || statusRef.current === 'profile_error') return
        const authError = backendError(error)
        updateAuthState('profile_error', sessionRef.current, null, authError.message)
      })
    }).catch((error: unknown) => {
      if (!alive || generationRef.current !== initialGeneration) return
      window.clearTimeout(restoreTimeout)
      generationRef.current += 1
      const authError = supabaseError(error)
      updateAuthState('profile_error', null, null, authError.message)
    })

    return () => {
      alive = false
      window.clearTimeout(restoreTimeout)
      subscription.subscription.unsubscribe()
    }
  }, [synchronizeSession, updateAuthState])

  const login = useCallback(async (email: string, password: string): Promise<Principal> => {
    setProfileError(null)
    setStatus('loading')
    statusRef.current = 'loading'
    setPrincipal(null)
    principalRef.current = null

    let attemptedToken: string | null = null
    try {
      if (!supabaseConfigured) {
        throw new AuthFlowError('configuration', 'Supabase Auth is not configured. Set VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY, then restart the frontend.')
      }

      let signInResult
      try {
        signInResult = await supabase.auth.signInWithPassword({ email, password })
      } catch (error) {
        throw supabaseError(error)
      }
      if (signInResult.error) throw supabaseError(signInResult.error)

      let currentSession
      try {
        currentSession = await supabase.auth.getSession()
      } catch (error) {
        throw supabaseError(error)
      }
      if (currentSession.error) throw supabaseError(currentSession.error)
      if (!currentSession.data.session) {
        throw new AuthFlowError('email_not_confirmed', 'Supabase did not return an active session. Confirm your email address and try again.')
      }

      const token = currentSession.data.session.access_token
      attemptedToken = token
      if (activeTokenRef.current !== token) {
        generationRef.current += 1
        activeTokenRef.current = token
      }
      const verifiedPrincipal = await synchronizeSession(currentSession.data.session)
      if (!verifiedPrincipal) {
        throw new AuthFlowError('session_changed', 'Your session changed while access was being checked. Please sign in again.')
      }
      return verifiedPrincipal
    } catch (error) {
      const authError = error instanceof AuthFlowError ? error : supabaseError(error)
      const stillCurrentLogin = attemptedToken === null || activeTokenRef.current === attemptedToken
      if (stillCurrentLogin && statusRef.current === 'loading') {
        const currentSession = sessionRef.current
        updateAuthState(currentSession ? 'profile_error' : 'anonymous', currentSession, null, authError.message)
      }
      throw authError
    }
  }, [synchronizeSession, updateAuthState])

  const logout = useCallback(async () => {
    const { error } = await supabase.auth.signOut()
    if (error) throw error
    if (activeTokenRef.current !== null) {
      generationRef.current += 1
      activeTokenRef.current = null
    }
    successfulProfilesRef.current.clear()
    profileRequestsRef.current.clear()
    updateAuthState('anonymous', null, null, null)
  }, [updateAuthState])

  const value = useMemo(() => ({ status, session, principal, profileError, login, logout }),
    [status, session, principal, profileError, login, logout])

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
