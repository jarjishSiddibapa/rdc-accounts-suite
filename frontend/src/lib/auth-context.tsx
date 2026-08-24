import {
  createContext,
  lazy,
  Suspense,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'
import { ApiError, AUTH_EXPIRED_EVENT, get, post } from './api'
import {
  announceLogout,
  clearIdleSessionState,
  prepareTabSession,
  startIdleSession,
  subscribeToTabLogout,
} from './tab-session'

// Lazy: pulls in Modal + framer-motion, which every page (including the
// unauthenticated login screen) would otherwise have to load upfront just
// for a warning that only ever shows after 29 idle minutes.
const IdleWarningModal = lazy(() =>
  import('@/components/IdleWarningModal').then((m) => ({ default: m.IdleWarningModal })),
)

export type Role = 'admin' | 'user'

export interface AuthUser {
  email: string
  first_name: string | null
  last_name: string | null
  role: Role
  // Regular users receive only explicit grants (an empty list means no app
  // access). Null is reserved for admins, who always have full access.
  allowed_apps: string[] | null
}

interface AuthContextValue {
  user: AuthUser | null
  loading: boolean
  login: (email: string, password: string) => Promise<void>
  logout: () => Promise<void>
  refresh: () => Promise<void>
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null)
  const [loading, setLoading] = useState(true)
  const [showIdleWarning, setShowIdleWarning] = useState(false)

  const refresh = useCallback(async () => {
    try {
      const me = await get<AuthUser>('/auth/me')
      setUser(me)
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setUser(null)
      } else {
        setUser(null)
      }
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      setLoading(true)
      try {
        const requireFreshLogin = await prepareTabSession()
        if (requireFreshLogin) {
          try {
            await post('/auth/logout')
          } catch {
            // The client still requires credentials even if the cleanup request cannot reach the server.
          }
          clearIdleSessionState()
          if (!cancelled) setUser(null)
          return
        }
        const me = await get<AuthUser>('/auth/me')
        if (!cancelled) setUser(me)
      } catch {
        if (!cancelled) setUser(null)
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    const handleExpired = () => {
      clearIdleSessionState()
      setUser(null)
      setShowIdleWarning(false)
      announceLogout()
    }
    window.addEventListener(AUTH_EXPIRED_EVENT, handleExpired)
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, handleExpired)
  }, [])

  useEffect(
    () =>
      subscribeToTabLogout(() => {
        clearIdleSessionState()
        setUser(null)
        setShowIdleWarning(false)
      }),
    [],
  )

  useEffect(() => {
    if (!user) return
    return startIdleSession(
      () => {
        clearIdleSessionState()
        setUser(null)
        setShowIdleWarning(false)
        announceLogout()
        void post('/auth/logout').catch(() => {
          // The local session is already closed; server-side idle validation
          // remains the fallback if the network is temporarily unavailable.
        })
      },
      () => post('/auth/activity'),
      () => setShowIdleWarning(true),
      () => setShowIdleWarning(false),
    )
  }, [user])

  const login = useCallback(async (email: string, password: string) => {
    const me = await post<AuthUser>('/auth/login', { email, password })
    setUser(me)
  }, [])

  const logout = useCallback(async () => {
    try {
      await post('/auth/logout')
    } finally {
      clearIdleSessionState()
      setUser(null)
      setShowIdleWarning(false)
      announceLogout()
    }
  }, [])

  const value = useMemo(
    () => ({ user, loading, login, logout, refresh }),
    [user, loading, login, logout, refresh],
  )

  return (
    <AuthContext.Provider value={value}>
      {children}
      {showIdleWarning && (
        <Suspense fallback={null}>
          <IdleWarningModal open onStayLoggedIn={() => setShowIdleWarning(false)} />
        </Suspense>
      )}
    </AuthContext.Provider>
  )
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) {
    throw new Error('useAuth must be used within an AuthProvider')
  }
  return ctx
}
