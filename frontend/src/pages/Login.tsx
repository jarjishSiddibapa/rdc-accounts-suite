import { useState, type FormEvent } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router-dom'
import { FileSpreadsheet, Layers, ListChecks, Mail, Receipt, ShieldCheck } from 'lucide-react'
import { Button } from '@/components/Button'
import { Footer } from '@/components/Footer'
import { ThemeToggle } from '@/components/ThemeToggle'
import { PasswordInput } from '@/components/PasswordInput'
import { useAuth } from '@/lib/auth-context'
import { ApiError, post } from '@/lib/api'

const workflows = [
  { label: 'ERP exports, trial balance & GSTR-2B reporting', icon: FileSpreadsheet },
  { label: 'Payables, receivables & unaccounted-transaction reconciliation', icon: Receipt },
  { label: 'Exception handling & mapping resolution', icon: ListChecks },
  { label: 'Ultrafine customer balance & payment communications', icon: Mail },
]

export default function Login() {
  const { user, login } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const [forgotOpen, setForgotOpen] = useState(false)
  const [forgotEmail, setForgotEmail] = useState('')
  const [forgotMessage, setForgotMessage] = useState<string | null>(null)
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
        setError(err instanceof Error ? err.message : 'Login failed. Please try again.')
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
      setForgotMessage(res.message)
    } catch (err) {
      setForgotMessage(err instanceof Error ? err.message : 'Something went wrong. Try again.')
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
      <section className="auth-brand-surface hidden px-10 py-8 text-white lg:flex lg:flex-col xl:px-14 xl:py-10">
        <div className="relative flex items-center gap-3">
          <span className="grid h-11 w-11 place-items-center rounded-xl border border-white/18 bg-white/10 shadow-[inset_0_1px_0_rgba(255,255,255,0.18)]">
            <Layers className="h-5 w-5" />
          </span>
          <div>
            <p className="font-display text-base font-bold tracking-[-0.02em]">RDC Accounts Suite</p>
            <p className="mt-0.5 text-xs font-medium text-white/55">Finance operations workspace</p>
          </div>
        </div>

        <div className="auth-brand-copy relative my-auto max-w-2xl py-8 xl:py-10">
          <p className="mb-5 text-sm font-semibold text-white/65">
            The central software suite for RDC's Accounts Department
          </p>
          <h1 className="auth-brand-title max-w-xl font-display text-5xl leading-[1.02] font-semibold tracking-[-0.055em] text-balance xl:text-6xl">
            Every accounts workflow. One reliable workspace.
          </h1>
          <p className="mt-6 max-w-xl text-base leading-7 text-white/68 xl:text-lg">
            ERP conversion, payables and receivables reporting, trial balance, GSTR-2B, exception
            handling, and Ultrafine customer communications. All in one place, for RDC and Ultrafine alike.
          </p>
        </div>

        <div className="relative">
          <div className="auth-workflow-list">
            {workflows.map((workflow) => (
              <div key={workflow.label} className="auth-workflow-item">
                <workflow.icon className="h-4 w-4 shrink-0 text-white/80" strokeWidth={1.8} />
                <span>{workflow.label}</span>
              </div>
            ))}
          </div>
          <p className="auth-security mt-5 flex items-center gap-2 text-xs font-medium text-white/50">
            <ShieldCheck className="h-4 w-4" strokeWidth={1.8} />
            Secure internal access for authorized users
          </p>
        </div>
      </section>

      <section className="auth-form-zone relative flex items-center justify-center px-3 py-16 sm:p-8 lg:px-8 lg:py-6 xl:px-12">
        <div className="absolute top-[max(1rem,env(safe-area-inset-top))] right-[max(1rem,env(safe-area-inset-right))] sm:top-7 sm:right-7">
          <ThemeToggle />
        </div>

      <div className="auth-card max-w-[29rem]">
        <div className="mb-7">
          <div className="mb-8 flex items-center gap-3 lg:hidden">
            <span className="icon-tile grid h-11 w-11 place-items-center rounded-xl">
              <Layers className="h-5 w-5" />
            </span>
            <div>
              <p className="font-display text-sm font-bold tracking-tight text-ink">RDC Accounts Suite</p>
              <p className="mt-0.5 text-xs text-ink-faint">Finance operations workspace</p>
            </div>
          </div>
          <p className="text-sm font-semibold text-accent">
            {forgotOpen ? 'Account recovery' : 'Welcome back'}
          </p>
          <h1 className="mt-2 font-display text-3xl font-semibold tracking-[-0.04em] text-ink sm:text-[2rem]">
            {forgotOpen ? 'Reset your password' : 'Sign in'}
          </h1>
          <p className="mt-2 text-sm leading-6 text-ink-dim">
            {forgotOpen
              ? 'Enter your account email and we will send a password reset link.'
              : 'Use your RDC account details to access the workspace.'}
          </p>
        </div>

        {!forgotOpen ? (
          <form className="flex flex-col gap-4" onSubmit={handleSubmit}>
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
                placeholder="••••••••"
              />
            </label>

            {error && (
              <p role="alert" className="status-banner border-red-500/25 bg-red-500/8 text-red-500">
                {error}
              </p>
            )}

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
          </form>
        ) : (
          <form className="flex flex-col gap-4" onSubmit={handleForgotSubmit}>
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

            {forgotMessage && (
              <p className="status-banner border-border bg-surface/60 text-ink-dim">
                {forgotMessage}
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
          </form>
        )}
      </div>
      </section>
    </main>
    <Footer />
    </>
  )
}
