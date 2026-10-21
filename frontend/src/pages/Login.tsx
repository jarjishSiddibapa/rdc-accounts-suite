import { useState, type FormEvent } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router-dom'
import { motion, useReducedMotion } from 'motion/react'
import { Mail } from 'lucide-react'
import { LogoMark } from '@/components/Logo'
import { Button } from '@/components/Button'
import { ErrorBanner } from '@/components/ErrorBanner'
import { Footer } from '@/components/Footer'
import { ThemeToggle } from '@/components/ThemeToggle'
import { PasswordInput } from '@/components/PasswordInput'
import { useAuth } from '@/lib/auth-context'
import { ApiError, post } from '@/lib/api'

export default function Login() {
  const reduceMotion = useReducedMotion()
  const { user, login } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const [forgotOpen, setForgotOpen] = useState(false)
  const [forgotEmail, setForgotEmail] = useState('')
  const [forgotMessage, setForgotMessage] = useState<{ ok: boolean; text: string } | null>(null)
  const [forgotLoading, setForgotLoading] = useState(false)

  if (user) {
    const from = (location.state as { from?: Location } | null)?.from?.pathname ?? '/'
    return <Navigate to={from} replace />
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
    } finally {
      setForgotLoading(false)
    }
  }

  return (
    <>
    <main
      id="main-content"
      className="auth-page grid min-h-[calc(100dvh-3.25rem)] gap-0 p-3 lg:grid-cols-[minmax(0,1.06fr)_minmax(29rem,0.94fr)] lg:gap-3"
    >
      <motion.section
        className="auth-brand-surface hidden px-10 py-8 text-white lg:flex lg:flex-col xl:px-14 xl:py-10"
        initial={reduceMotion ? false : { opacity: 0, x: -22 }}
        animate={{ opacity: 1, x: 0 }}
        transition={{ duration: 0.55, ease: [0.16, 1, 0.3, 1] }}
      >
        <div className="relative flex items-center gap-3">
          <LogoMark className="h-11 w-11" tileClassName="fill-white/15" />
          <p className="font-display text-base font-semibold tracking-[-0.01em]">RDC Accounts Suite</p>
        </div>

        <h1 className="auth-brand-title relative my-auto max-w-2xl font-display text-4xl leading-[1.08] font-semibold tracking-[-0.035em] xl:text-5xl">
          <span className="block">Every accounts workflow.</span>
          <span className="block">One reliable workspace.</span>
        </h1>
      </motion.section>

      <motion.section
        className="auth-form-zone relative flex items-center justify-center px-3 py-16 sm:p-8 lg:px-8 lg:py-6 xl:px-12"
        initial={reduceMotion ? false : { opacity: 0, x: 22 }}
        animate={{ opacity: 1, x: 0 }}
        transition={{ duration: 0.55, delay: 0.04, ease: [0.16, 1, 0.3, 1] }}
      >
        <div className="absolute top-[max(1rem,env(safe-area-inset-top))] right-[max(1rem,env(safe-area-inset-right))] sm:top-7 sm:right-7">
          <ThemeToggle />
        </div>

      <div className="auth-card max-w-[29rem]">
        <div className="mb-7">
          <div className="mb-8 flex items-center gap-3 lg:hidden">
            <LogoMark className="h-11 w-11" />
            <p className="font-display text-sm font-semibold tracking-tight text-ink">RDC Accounts Suite</p>
          </div>
          <h1 className="font-display text-3xl font-semibold tracking-[-0.025em] text-ink sm:text-[2rem]">
            {forgotOpen ? 'Reset your password' : 'Sign in'}
          </h1>
          <p className="mt-2 text-sm leading-6 text-ink-dim">
            {forgotOpen
              ? 'Enter your email and we will send you a reset link.'
              : 'Use your RDC account.'}
          </p>
        </div>

        {!forgotOpen ? (
          <>
            <motion.form
              className="flex flex-col gap-4"
              onSubmit={handleSubmit}
              initial={reduceMotion ? false : { opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
            >
              <label className="flex flex-col gap-1.5 text-sm">
                <span className="font-medium text-ink-dim">Email</span>
                <div className="relative">
                  <Mail className="pointer-events-none absolute top-1/2 left-3 h-4 w-4 -translate-y-1/2 text-ink-faint" />
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

              <label className="flex flex-col gap-1.5 text-sm">
                <span className="font-medium text-ink-dim">Password</span>
                <PasswordInput
                  required
                  name="password"
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
              </label>

              {error && <ErrorBanner>{error}</ErrorBanner>}

              <Button type="submit" variant="primary" loading={loading} className="mt-2 min-h-12 w-full">
                Sign in
              </Button>

              <button
                type="button"
                onClick={() => {
                  setForgotOpen(true)
                  setForgotEmail(email)
                  setForgotMessage(null)
                }}
                className="text-center text-sm text-ink-dim underline-offset-2 hover:text-accent hover:underline"
              >
                Forgot password?
              </button>
            </motion.form>
          </>
        ) : (
          <motion.form
            className="flex flex-col gap-4"
            onSubmit={handleForgotSubmit}
            initial={reduceMotion ? false : { opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
          >
            <label className="flex flex-col gap-1.5 text-sm">
              <span className="font-medium text-ink-dim">Email</span>
              <div className="relative">
                <Mail className="pointer-events-none absolute top-1/2 left-3 h-4 w-4 -translate-y-1/2 text-ink-faint" />
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

            <Button type="submit" variant="primary" loading={forgotLoading} className="mt-2 min-h-12 w-full">
              Send reset link
            </Button>

            <button
              type="button"
              onClick={() => setForgotOpen(false)}
              className="text-center text-sm text-ink-dim underline-offset-2 hover:text-accent hover:underline"
            >
              Back to sign in
            </button>
          </motion.form>
        )}
      </div>
      </motion.section>
    </main>
    <Footer />
    </>
  )
}
