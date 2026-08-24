import { useState, type FormEvent } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { Layers, ShieldCheck } from 'lucide-react'
import { Button } from '@/components/Button'
import { Footer } from '@/components/Footer'
import { ThemeToggle } from '@/components/ThemeToggle'
import { PasswordInput } from '@/components/PasswordInput'
import { PasswordStrengthMeter } from '@/components/PasswordStrengthMeter'
import { post } from '@/lib/api'
import { generateStrongPassword, scorePasswordStrength } from '@/lib/passwordStrength'

export default function ResetPassword() {
  const [searchParams] = useSearchParams()
  const token = searchParams.get('token') ?? ''
  const navigate = useNavigate()

  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [done, setDone] = useState(false)

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)

    if (!token) {
      setError('This reset link is missing its token. Request a new one from the sign-in page.')
      return
    }
    if (newPassword !== confirmPassword) {
      setError("Passwords don't match.")
      return
    }
    if (!scorePasswordStrength(newPassword).isAcceptable) {
      setError('Choose a stronger password — at least 10 characters with uppercase, lowercase, a number, and a symbol.')
      return
    }

    setLoading(true)
    try {
      await post('/auth/reset-password', { token, new_password: newPassword })
      setDone(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'This reset link is invalid or has expired.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <>
    <main id="main-content" className="auth-page relative flex min-h-[calc(100dvh-3.25rem)] flex-col items-center justify-center overflow-hidden px-4 py-16 sm:p-6">
      <div className="absolute top-[max(1rem,env(safe-area-inset-top))] right-[max(1rem,env(safe-area-inset-right))] sm:top-6 sm:right-6">
        <ThemeToggle />
      </div>

      <div className="auth-card max-w-md">
        <div className="mb-6 flex flex-col items-center gap-3 text-center">
          <span className="icon-tile grid h-12 w-12 place-items-center rounded-xl">
            <Layers className="h-6 w-6" />
          </span>
          <div>
            <p className="text-xs font-bold tracking-[0.1em] text-accent uppercase">Account security</p>
            <h1 className="mt-2 font-display text-3xl font-semibold tracking-[-0.04em] text-ink">Reset your password</h1>
          </div>
        </div>

        {done ? (
          <div className="flex flex-col gap-4 text-center">
            <p className="text-sm text-ink-dim">
              Your password has been reset. You can now sign in with your new password.
            </p>
            <Button variant="primary" onClick={() => navigate('/login', { replace: true })}>
              Go to sign in
            </Button>
          </div>
        ) : (
          <form className="flex flex-col gap-4" onSubmit={handleSubmit}>
            <label className="flex flex-col gap-1.5 text-sm">
              <div className="flex items-center justify-between gap-2">
                <span className="font-medium text-ink-dim">New password</span>
                <button
                  type="button"
                  onClick={() => {
                    const generated = generateStrongPassword()
                    setNewPassword(generated)
                    setConfirmPassword(generated)
                  }}
                  className="text-xs font-medium text-accent hover:underline"
                >
                  Generate strong password
                </button>
              </div>
              <PasswordInput
                autoFocus
                required
                minLength={10}
                autoComplete="new-password"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                placeholder="At least 10 characters"
              />
              <PasswordStrengthMeter password={newPassword} />
            </label>

            <label className="flex flex-col gap-1.5 text-sm">
              <span className="font-medium text-ink-dim">Confirm new password</span>
              <PasswordInput
                required
                minLength={10}
                autoComplete="new-password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                placeholder="Repeat your new password"
              />
            </label>

            {error && (
              <p role="alert" className="rounded-xl border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-500">
                {error}
              </p>
            )}

            <Button
              type="submit"
              variant="primary"
              loading={loading}
              disabled={!scorePasswordStrength(newPassword).isAcceptable || !confirmPassword}
              className="mt-2 w-full"
            >
              Reset password
            </Button>

            <Link
              to="/login"
              className="text-center text-sm text-ink-dim underline-offset-2 hover:text-accent hover:underline"
            >
              Back to sign in
            </Link>
          </form>
        )}
        <p className="mt-7 flex items-center justify-center gap-2 border-t border-border pt-5 text-xs text-ink-faint">
          <ShieldCheck className="h-4 w-4 text-accent" strokeWidth={1.8} />
          Secure password recovery
        </p>
      </div>
    </main>
    <Footer />
    </>
  )
}
