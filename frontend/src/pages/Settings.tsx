import { useEffect, useState } from 'react'
import { CheckCircle2, UserRound, XCircle } from 'lucide-react'
import { AppShell } from '@/components/AppShell'
import { GlassCard } from '@/components/GlassCard'
import { Button } from '@/components/Button'
import { PasswordInput } from '@/components/PasswordInput'
import { ApiError, get, put, post } from '@/lib/api'
import { useAuth } from '@/lib/auth-context'

interface EmailSettings {
  sender_email: string
  configured: boolean
  signature: string
}

export default function Settings() {
  const { user, refresh } = useAuth()
  const [firstName, setFirstName] = useState('')
  const [lastName, setLastName] = useState('')
  const [profileSaving, setProfileSaving] = useState(false)
  const [profileMessage, setProfileMessage] = useState<{ ok: boolean; text: string } | null>(null)
  const [senderEmail, setSenderEmail] = useState('')
  const [appPassword, setAppPassword] = useState('')
  const [signature, setSignature] = useState('')
  const [configured, setConfigured] = useState(false)

  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [saveMessage, setSaveMessage] = useState<{ ok: boolean; text: string } | null>(null)
  const [testMessage, setTestMessage] = useState<{ ok: boolean; text: string } | null>(null)

  useEffect(() => {
    setFirstName(user?.first_name ?? '')
    setLastName(user?.last_name ?? '')
  }, [user?.first_name, user?.last_name])

  useEffect(() => {
    void (async () => {
      setLoading(true)
      try {
        const data = await get<EmailSettings>('/settings/email')
        setSenderEmail(data.sender_email ?? '')
        setSignature(data.signature ?? '')
        setConfigured(Boolean(data.configured))
      } catch {
        // No settings saved yet; leave the form blank.
      } finally {
        setLoading(false)
      }
    })()
  }, [])

  async function handleSave() {
    setSaving(true)
    setSaveMessage(null)
    try {
      const saved = await put<EmailSettings>('/settings/email', {
        sender_email: senderEmail,
        app_password: appPassword || undefined,
        signature,
      })
      setSaveMessage({ ok: true, text: 'Settings saved.' })
      setAppPassword('')
      setConfigured(Boolean(saved.configured))
    } catch (err) {
      setSaveMessage({
        ok: false,
        text: err instanceof ApiError ? err.message : 'Failed to save settings.',
      })
    } finally {
      setSaving(false)
    }
  }

  async function handleProfileSave() {
    setProfileSaving(true)
    setProfileMessage(null)
    try {
      await put('/settings/profile', {
        first_name: firstName,
        last_name: lastName,
      })
      await refresh()
      setProfileMessage({ ok: true, text: 'Profile saved.' })
    } catch (err) {
      setProfileMessage({
        ok: false,
        text: err instanceof ApiError ? err.message : 'Failed to save profile.',
      })
    } finally {
      setProfileSaving(false)
    }
  }

  async function handleTest() {
    setTesting(true)
    setTestMessage(null)
    try {
      const result = await post<{ ok: boolean; message: string }>('/settings/email/test', {
        sender_email: senderEmail,
        app_password: appPassword,
      })
      setTestMessage({ ok: result.ok, text: result.message })
    } catch (err) {
      setTestMessage({
        ok: false,
        text: err instanceof ApiError ? err.message : 'Connection test failed.',
      })
    } finally {
      setTesting(false)
    }
  }

  return (
    <AppShell title="Settings">
      <div className="flex w-full min-w-0 flex-col gap-6">
        <GlassCard padding="lg">
          <div className="flex items-start gap-3">
            <div className="icon-tile grid h-11 w-11 shrink-0 place-items-center rounded-2xl">
              <UserRound className="h-5 w-5" />
            </div>
            <div>
              <p className="text-xs font-bold tracking-[0.1em] text-accent uppercase">Personal details</p>
              <h2 className="mt-1.5 font-display text-xl font-semibold tracking-[-0.025em] text-ink">Your profile</h2>
              <p className="mt-1 text-sm text-ink-dim">
                Add your name so the dashboard and account menu feel personal. Both fields are optional.
              </p>
            </div>
          </div>

          <form
            className="mt-6 flex flex-col gap-4"
            onSubmit={(event) => {
              event.preventDefault()
              void handleProfileSave()
            }}
          >
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <label className="flex flex-col gap-1.5 text-sm">
                <span className="font-medium text-ink-dim">
                  First name <span className="font-normal text-ink-faint">(optional)</span>
                </span>
                <input
                  type="text"
                  maxLength={100}
                  autoComplete="given-name"
                  value={firstName}
                  onChange={(event) => setFirstName(event.target.value)}
                  className="field-control"
                  placeholder="First name"
                />
              </label>
              <label className="flex flex-col gap-1.5 text-sm">
                <span className="font-medium text-ink-dim">
                  Last name <span className="font-normal text-ink-faint">(optional)</span>
                </span>
                <input
                  type="text"
                  maxLength={100}
                  autoComplete="family-name"
                  value={lastName}
                  onChange={(event) => setLastName(event.target.value)}
                  className="field-control"
                  placeholder="Last name"
                />
              </label>
            </div>

            <label className="flex flex-col gap-1.5 text-sm">
              <span className="font-medium text-ink-dim">Sign-in email</span>
              <input
                type="email"
                value={user?.email ?? ''}
                readOnly
                className="field-control cursor-not-allowed bg-bg-soft text-ink-dim"
              />
              <span className="text-xs text-ink-faint">An administrator can change your sign-in email.</span>
            </label>

            {profileMessage && (
              <p
                className={
                  profileMessage.ok
                    ? 'flex items-center gap-2 rounded-xl border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-600'
                    : 'flex items-center gap-2 rounded-xl border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-500'
                }
              >
                {profileMessage.ok ? (
                  <CheckCircle2 className="h-4 w-4 shrink-0" />
                ) : (
                  <XCircle className="h-4 w-4 shrink-0" />
                )}
                {profileMessage.text}
              </p>
            )}

            <div className="flex justify-end">
              <Button type="submit" loading={profileSaving}>
                Save profile
              </Button>
            </div>
          </form>
        </GlassCard>

      <GlassCard padding="lg">
        <p className="text-xs font-bold tracking-[0.1em] text-accent uppercase">Report delivery</p>
        <h2 className="mt-1.5 font-display text-xl font-semibold tracking-[-0.025em] text-ink">Email sender identity</h2>
        <p className="mt-1 text-sm text-ink-dim">
          Used as the "from" address when you personally send a generated report by email. Who
          reports get sent to (To/Cc) is set application-wide by an admin, not here.
        </p>

        <form
          className="mt-6 flex flex-col gap-4"
          onSubmit={(e) => {
            e.preventDefault()
            void handleSave()
          }}
        >
          <label className="flex flex-col gap-1.5 text-sm">
            <span className="font-medium text-ink-dim">Sender email</span>
            <input
              type="email"
              value={senderEmail}
              onChange={(e) => setSenderEmail(e.target.value)}
              disabled={loading}
              className="field-control disabled:opacity-50"
              placeholder="reports@company.com"
            />
          </label>

          <label className="flex flex-col gap-1.5 text-sm">
            <span className="flex items-center gap-2 font-medium text-ink-dim">
              App password
              {configured && (
                <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-2 py-0.5 text-xs font-medium text-emerald-500">
                  <CheckCircle2 className="h-3 w-3" /> Configured
                </span>
              )}
            </span>
            <PasswordInput
              value={appPassword}
              onChange={(e) => setAppPassword(e.target.value)}
              disabled={loading}
              autoComplete="new-password"
              placeholder={configured ? '••••••••' : 'App password'}
            />
          </label>

          <label className="flex flex-col gap-1.5 text-sm">
            <span className="font-medium text-ink-dim">Signature</span>
            <textarea
              value={signature}
              onChange={(e) => setSignature(e.target.value)}
              disabled={loading}
              rows={4}
              className="field-control resize-none disabled:opacity-50"
              placeholder="Regards,&#10;Accounts Team"
            />
          </label>

          {saveMessage && (
            <p
              className={
                saveMessage.ok
                  ? 'flex items-center gap-2 rounded-xl border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-600'
                  : 'flex items-center gap-2 rounded-xl border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-500'
              }
            >
              {saveMessage.ok ? (
                <CheckCircle2 className="h-4 w-4 shrink-0" />
              ) : (
                <XCircle className="h-4 w-4 shrink-0" />
              )}
              {saveMessage.text}
            </p>
          )}

          {testMessage && (
            <p
              className={
                testMessage.ok
                  ? 'flex items-center gap-2 rounded-xl border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-600'
                  : 'flex items-center gap-2 rounded-xl border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-500'
              }
            >
              {testMessage.ok ? (
                <CheckCircle2 className="h-4 w-4 shrink-0" />
              ) : (
                <XCircle className="h-4 w-4 shrink-0" />
              )}
              {testMessage.text}
            </p>
          )}

          <div className="mt-2 flex flex-col-reverse gap-3 sm:flex-row sm:justify-end">
            <Button
              type="button"
              variant="secondary"
              loading={testing}
              disabled={!senderEmail || !appPassword}
              onClick={() => void handleTest()}
            >
              Test connection
            </Button>
            <Button type="submit" variant="primary" loading={saving}>
              Save
            </Button>
          </div>
        </form>
      </GlassCard>
      </div>
    </AppShell>
  )
}
