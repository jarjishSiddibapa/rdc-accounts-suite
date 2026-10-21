import { useRef, useState, type FormEvent, type KeyboardEvent, type PointerEvent, type ReactNode } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router-dom'
import { motion, useAnimationControls, useMotionValue, useReducedMotion, useSpring } from 'motion/react'
import { Mail } from 'lucide-react'
import { LogoMark } from '@/components/Logo'
import { Button } from '@/components/Button'
import { DotField } from '@/components/DotField'
import { ErrorBanner } from '@/components/ErrorBanner'
import { ThemeToggle } from '@/components/ThemeToggle'
import { PasswordInput } from '@/components/PasswordInput'
import { useAuth } from '@/lib/auth-context'
import { ApiError, post } from '@/lib/api'

const DOT_BASE: [number, number, number] = [255, 255, 255]
const DOT_GLOW: [number, number, number] = [146, 137, 255]
const ease = [0.16, 1, 0.3, 1] as const

/** Nudges its child a little toward the pointer, then springs back. */
function Magnetic({ children }: { children: ReactNode }) {
  const reduceMotion = useReducedMotion()
  const ref = useRef<HTMLDivElement>(null)
  const x = useMotionValue(0)
  const y = useMotionValue(0)
  const sx = useSpring(x, { stiffness: 260, damping: 18, mass: 0.4 })
  const sy = useSpring(y, { stiffness: 260, damping: 18, mass: 0.4 })

  function onMove(event: PointerEvent<HTMLDivElement>) {
    if (reduceMotion || !ref.current) return
    const rect = ref.current.getBoundingClientRect()
    x.set((event.clientX - (rect.left + rect.width / 2)) * 0.12)
    y.set((event.clientY - (rect.top + rect.height / 2)) * 0.28)
  }
  function onLeave() {
    x.set(0)
    y.set(0)
  }

  return (
    <motion.div ref={ref} style={{ x: sx, y: sy }} onPointerMove={onMove} onPointerLeave={onLeave}>
      {children}
    </motion.div>
  )
}

function Rise({ delay = 0, children }: { delay?: number; children: ReactNode }) {
  const reduceMotion = useReducedMotion()
  return (
    <motion.div
      initial={reduceMotion ? false : { opacity: 0, y: 18 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.55, delay, ease }}
    >
      {children}
    </motion.div>
  )
}

export default function Login() {
  const reduceMotion = useReducedMotion()
  const { user, login } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const shake = useAnimationControls()

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [capsLock, setCapsLock] = useState(false)

  const [forgotOpen, setForgotOpen] = useState(false)
  const [forgotEmail, setForgotEmail] = useState('')
  const [forgotMessage, setForgotMessage] = useState<{ ok: boolean; text: string } | null>(null)
  const [forgotLoading, setForgotLoading] = useState(false)

  if (user) {
    const from = (location.state as { from?: Location } | null)?.from?.pathname ?? '/'
    return <Navigate to={from} replace />
  }

  function rejectWithShake() {
    if (!reduceMotion) void shake.start({ x: [0, -11, 9, -6, 3, 0], transition: { duration: 0.42 } })
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setLoading(true)
    try {
      await login(email, password)
      const destination = (location.state as { from?: { pathname?: string } } | null)?.from?.pathname
      navigate(destination || '/', { replace: true })
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setError('Invalid email or password.')
      } else {
        setError(err instanceof Error ? err.message : 'Could not sign in. Please try again.')
      }
      rejectWithShake()
    } finally {
      setLoading(false)
    }
  }

  async function handleForgotSubmit(e: FormEvent) {
    e.preventDefault()
    setForgotLoading(true)
    setForgotMessage(null)
    try {
      const res = await post<{ message: string }>('/auth/forgot-password', {
        email: forgotEmail,
      })
      setForgotMessage({ ok: true, text: res.message })
    } catch (err) {
      setForgotMessage({
        ok: false,
        text: err instanceof Error ? err.message : 'Could not send reset link. Please try again.',
      })
      rejectWithShake()
    } finally {
      setForgotLoading(false)
    }
  }

  const watchCapsLock = (event: KeyboardEvent<HTMLInputElement>) => {
    setCapsLock(event.getModifierState('CapsLock'))
  }

  return (
    <main id="main-content" className="auth-page grid min-h-dvh lg:grid-cols-[1.18fr_1fr]">
      <section className="auth-hero hidden lg:flex" aria-label="RDC Accounts Suite">
        <DotField base={DOT_BASE} glow={DOT_GLOW} baseAlpha={0.2} />

        <motion.div
          className="pointer-events-none relative flex items-center gap-3"
          initial={reduceMotion ? false : { opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5, ease }}
        >
          <LogoMark className="h-10 w-10" tileClassName="fill-accent-2" />
          <span className="font-display text-base font-semibold tracking-[-0.01em]">RDC Accounts Suite</span>
        </motion.div>

        <div className="pointer-events-none relative mt-auto [container-type:inline-size]">
          <h1 className="font-display [font-size:min(12.2cqi,7.25rem)] leading-[0.96] font-semibold tracking-[-0.045em]">
            {['Every workflow.', 'One workspace.'].map((line, index) => (
              <span key={line} className="block overflow-hidden pb-[0.08em]">
                <motion.span
                  className="block"
                  initial={reduceMotion ? false : { y: '105%' }}
                  animate={{ y: 0 }}
                  transition={{ duration: 0.85, delay: 0.1 + index * 0.1, ease }}
                >
                  {line}
                </motion.span>
              </span>
            ))}
          </h1>
          <motion.p
            className="mt-9 max-w-md text-lg leading-relaxed text-white/70"
            initial={reduceMotion ? false : { opacity: 0, y: 18 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6, delay: 0.5, ease }}
          >
            Reports, reminders and reconciliations for the RDC Accounts team.
          </motion.p>
        </div>
      </section>

      <section className="auth-form-zone relative flex flex-col items-center px-6 pt-20 pb-6 sm:px-12">
        <div className="absolute top-[max(1rem,env(safe-area-inset-top))] right-[max(1rem,env(safe-area-inset-right))] sm:top-6 sm:right-6">
          <ThemeToggle />
        </div>

        <motion.div className="auth-form my-auto w-full max-w-[24rem]" animate={shake}>
          <Rise>
            <div className="mb-9">
              <LogoMark className="mb-7 h-12 w-12 lg:hidden" />
              <h2 className="font-display text-[2.25rem] leading-none font-semibold tracking-[-0.04em] text-ink">
                {forgotOpen ? 'Reset your password' : 'Sign in'}
              </h2>
              <p className="mt-3 text-base leading-6 text-ink-dim">
                {forgotOpen
                  ? 'Enter your email and we will send you a reset link.'
                  : 'Use your RDC account.'}
              </p>
            </div>
          </Rise>

          {!forgotOpen ? (
            <form className="flex flex-col gap-4" onSubmit={handleSubmit}>
              <Rise delay={0.06}>
                <label className="group/field flex flex-col gap-1.5 text-sm">
                  <span className="font-medium text-ink-dim transition-colors duration-200 group-focus-within/field:text-accent">Email</span>
                  <div className="relative">
                    <Mail className="pointer-events-none absolute top-1/2 left-3 h-4 w-4 -translate-y-1/2 text-ink-faint transition-colors duration-200 group-focus-within/field:text-accent" />
                    <input
                      autoFocus
                      required
                      type="email"
                      autoComplete="username"
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                      className="field-control w-full pr-3 pl-9"
                      placeholder="name@rdc.in"
                    />
                  </div>
                </label>
              </Rise>

              <Rise delay={0.12}>
                <label className="group/field flex flex-col gap-1.5 text-sm">
                  <span className="font-medium text-ink-dim transition-colors duration-200 group-focus-within/field:text-accent">Password</span>
                  <PasswordInput
                    required
                    name="password"
                    autoComplete="current-password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    onKeyDown={watchCapsLock}
                    onKeyUp={watchCapsLock}
                    onBlur={() => setCapsLock(false)}
                  />
                  <span
                    className={`auth-hint text-xs font-medium text-amber-600 dark:text-amber-400 ${capsLock ? 'auth-hint--on' : ''}`}
                    aria-live="polite"
                  >
                    {capsLock ? 'Caps Lock is on' : ' '}
                  </span>
                </label>
              </Rise>

              {error && <ErrorBanner>{error}</ErrorBanner>}

              <Rise delay={0.18}>
                <Magnetic>
                  <Button type="submit" variant="primary" loading={loading} className="min-h-12 w-full shadow-none sm:w-full">
                    Sign in
                  </Button>
                </Magnetic>
              </Rise>

              <Rise delay={0.22}>
                <button
                  type="button"
                  onClick={() => {
                    setForgotOpen(true)
                    setForgotEmail(email)
                    setForgotMessage(null)
                  }}
                  className="mx-auto block text-center text-sm text-ink-dim underline-offset-2 hover:text-accent hover:underline"
                >
                  Forgot password?
                </button>
              </Rise>
            </form>
          ) : (
            <motion.form
              className="flex flex-col gap-4"
              onSubmit={handleForgotSubmit}
              initial={reduceMotion ? false : { opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.4, ease }}
            >
              <label className="group/field flex flex-col gap-1.5 text-sm">
                <span className="font-medium text-ink-dim transition-colors duration-200 group-focus-within/field:text-accent">Email</span>
                <div className="relative">
                  <Mail className="pointer-events-none absolute top-1/2 left-3 h-4 w-4 -translate-y-1/2 text-ink-faint transition-colors duration-200 group-focus-within/field:text-accent" />
                  <input
                    autoFocus
                    required
                    type="email"
                    autoComplete="username"
                    value={forgotEmail}
                    onChange={(e) => setForgotEmail(e.target.value)}
                    className="field-control w-full pr-3 pl-9"
                    placeholder="name@rdc.in"
                  />
                </div>
              </label>

              {forgotMessage && !forgotMessage.ok && <ErrorBanner>{forgotMessage.text}</ErrorBanner>}

              {forgotMessage && forgotMessage.ok && (
                <p className="status-banner border-border bg-surface/60 text-ink-dim">
                  {forgotMessage.text}
                </p>
              )}

              <Magnetic>
                <Button type="submit" variant="primary" loading={forgotLoading} className="min-h-12 w-full shadow-none sm:w-full">
                  Send reset link
                </Button>
              </Magnetic>

              <button
                type="button"
                onClick={() => setForgotOpen(false)}
                className="mx-auto block text-center text-sm text-ink-dim underline-offset-2 hover:text-accent hover:underline"
              >
                Back to sign in
              </button>
            </motion.form>
          )}
        </motion.div>

        <p className="pt-10 text-xs font-medium text-ink-faint">RDC Accounts Suite. Made by Jarjish.</p>
      </section>
    </main>
  )
}
