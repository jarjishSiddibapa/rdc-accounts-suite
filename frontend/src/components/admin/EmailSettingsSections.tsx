import { useEffect, useState } from 'react'
import { GlassCard } from '@/components/GlassCard'
import { Button } from '@/components/Button'
import { PasswordInput } from '@/components/PasswordInput'
import { ApiError, get, post, put } from '@/lib/api'

interface SystemEmail {
  sender_email: string | null
  configured: boolean
}

interface RecipientApplication {
  key: string
  label: string
  to: string[]
  cc: string[]
}

/**
 * Who generated reports get emailed to - an application-wide business rule
 * (the same for every user, regardless of who's logged in and clicks Send),
 * not a per-user preference. Seeded from the desktop app's original
 * DEFAULT_TO/DEFAULT_CC constants.
 */
export function ReportRecipientsSection() {
  const [applications, setApplications] = useState<RecipientApplication[]>([])
  const [selectedKey, setSelectedKey] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [saveMessage, setSaveMessage] = useState<string | null>(null)

  useEffect(() => {
    void (async () => {
      setLoading(true)
      try {
        const data = await get<{ applications: RecipientApplication[] }>('/admin/report-recipients')
        setApplications(data.applications)
        setSelectedKey((current) => current || data.applications[0]?.key || '')
      } catch {
        // non-fatal, section just shows blank
      } finally {
        setLoading(false)
      }
    })()
  }, [])

  const selected = applications.find((application) => application.key === selectedKey)

  function updateSelected(field: 'to' | 'cc', value: string) {
    const emails = value.split(',').map((item) => item.trim())
    setApplications((current) =>
      current.map((application) =>
        application.key === selectedKey ? { ...application, [field]: emails } : application,
      ),
    )
  }

  async function handleSave() {
    if (!selected) return
    setSaving(true)
    setSaveMessage(null)
    try {
      await put('/admin/report-recipients', {
        app_key: selected.key,
        default_to: selected.to.map((email) => email.trim()).filter(Boolean),
        default_cc: selected.cc.map((email) => email.trim()).filter(Boolean),
      })
      setSaveMessage(`${selected.label} recipients saved.`)
    } catch (err) {
      setSaveMessage(err instanceof ApiError ? err.message : 'Failed to save.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <GlassCard padding="lg">
      <p className="text-sm font-semibold text-accent">Delivery defaults</p>
      <h2 className="mt-1.5 font-display text-xl font-semibold tracking-[-0.025em] text-ink">Report recipients</h2>
      <p className="mt-1 text-sm text-ink-dim">
        Manage the default To and CC recipients independently for each application. Individual
        sends can still override these values. An address can appear only once per application.
      </p>

      <label className="mt-4 flex max-w-md flex-col gap-1.5 text-sm">
        <span className="font-medium text-ink-dim">Application</span>
        <select
          value={selectedKey}
          onChange={(event) => {
            setSelectedKey(event.target.value)
            setSaveMessage(null)
          }}
          disabled={loading || applications.length === 0}
          className="field-control"
        >
          {applications.map((application) => (
            <option key={application.key} value={application.key}>
              {application.label}
            </option>
          ))}
        </select>
      </label>

      <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
        <label className="flex flex-col gap-1.5 text-sm">
          <span className="font-medium text-ink-dim">Default To (comma-separated)</span>
          <input
            type="text"
            inputMode="email"
            value={selected?.to.join(', ') ?? ''}
            onChange={(e) => updateSelected('to', e.target.value)}
            disabled={loading || !selected}
            placeholder="accountsincharges@rdc.in, accountsgroup@rdc.in"
            className="field-control disabled:opacity-50"
          />
        </label>
        <label className="flex flex-col gap-1.5 text-sm">
          <span className="font-medium text-ink-dim">Default CC (comma-separated)</span>
          <input
            type="text"
            inputMode="email"
            value={selected?.cc.join(', ') ?? ''}
            onChange={(e) => updateSelected('cc', e.target.value)}
            disabled={loading || !selected}
            placeholder="manish.modani@rdc.in, umesh.gawade@rdc.in"
            className="field-control disabled:opacity-50"
          />
        </label>
      </div>

      {saveMessage && <p className="mt-3 text-sm text-ink-dim">{saveMessage}</p>}

      <div className="mt-4 flex justify-end">
        <Button loading={saving} disabled={!selected} onClick={() => void handleSave()}>
          Save
        </Button>
      </div>
    </GlassCard>
  )
}

/**
 * The application's own system sender identity (password-reset / other
 * notification emails) - distinct from each user's personal per-user
 * report-sending identity, which lives on the regular Settings page.
 */
export function SystemEmailSection() {
  const [info, setInfo] = useState<SystemEmail | null>(null)
  const [senderEmail, setSenderEmail] = useState('')
  const [appPassword, setAppPassword] = useState('')
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null)
  const [testing, setTesting] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveMessage, setSaveMessage] = useState<string | null>(null)

  async function load() {
    try {
      const data = await get<SystemEmail>('/admin/system-email')
      setInfo(data)
      setSenderEmail(data.sender_email ?? '')
    } catch {
      // non-fatal, section just shows blank
    }
  }

  useEffect(() => {
    void load()
  }, [])

  async function handleTest() {
    setTesting(true)
    setTestResult(null)
    try {
      const res = await post<{ ok: boolean; message: string }>('/admin/system-email/test', {
        sender_email: senderEmail,
        app_password: appPassword,
      })
      setTestResult(res)
    } catch (err) {
      setTestResult({ ok: false, message: err instanceof Error ? err.message : 'Test failed.' })
    } finally {
      setTesting(false)
    }
  }

  async function handleSave() {
    setSaving(true)
    setSaveMessage(null)
    try {
      await put('/admin/system-email', {
        sender_email: senderEmail,
        app_password: appPassword || undefined,
      })
      setSaveMessage('Saved.')
      setAppPassword('')
      await load()
    } catch (err) {
      setSaveMessage(err instanceof Error ? err.message : 'Failed to save.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <GlassCard padding="lg">
      <p className="text-sm font-semibold text-accent">Platform identity</p>
      <h2 className="mt-1.5 font-display text-xl font-semibold tracking-[-0.025em] text-ink">System email</h2>
      <p className="mt-1 text-sm text-ink-dim">
        The address the application itself uses to send password-reset links and other
        notifications. This is separate from the per-user sender identity people configure for emailing
        their own reports.
        {info && (
          <span className="ml-1">
            {info.configured ? (
              <span className="text-green-500">Currently configured{info.sender_email ? ` as ${info.sender_email}` : ''}.</span>
            ) : (
              <span className="text-red-500">Not configured yet.</span>
            )}
          </span>
        )}
      </p>

      <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
        <label className="flex flex-col gap-1.5 text-sm">
          <span className="font-medium text-ink-dim">Sender email</span>
          <input
            type="email"
            value={senderEmail}
            onChange={(e) => setSenderEmail(e.target.value)}
            placeholder="name@rdc.in"
            className="field-control"
          />
        </label>
        <label className="flex flex-col gap-1.5 text-sm">
          <span className="font-medium text-ink-dim">App password</span>
          <PasswordInput
            value={appPassword}
            onChange={(e) => setAppPassword(e.target.value)}
            autoComplete="new-password"
            placeholder={info?.configured ? 'Leave blank to keep current password' : 'Gmail app password'}
          />
        </label>
      </div>

      {testResult && (
        <p
          className={`mt-3 rounded-xl border px-3 py-2 text-sm ${
            testResult.ok
              ? 'border-green-500/30 bg-green-500/10 text-green-500'
              : 'border-red-500/30 bg-red-500/10 text-red-500'
          }`}
        >
          {testResult.message}
        </p>
      )}
      {saveMessage && <p className="mt-3 text-sm text-ink-dim">{saveMessage}</p>}

      <div className="mt-4 flex flex-col-reverse gap-3 sm:flex-row sm:justify-end">
        <Button variant="secondary" loading={testing} onClick={() => void handleTest()}>
          Test connection
        </Button>
        <Button loading={saving} onClick={() => void handleSave()}>
          Save
        </Button>
      </div>
    </GlassCard>
  )
}
