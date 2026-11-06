import { useCallback, useEffect, useState } from 'react'
import { CheckCircle2, Mail } from 'lucide-react'
import { GlassCard } from '@/components/GlassCard'
import { Button } from '@/components/Button'
import { ErrorBanner } from '@/components/ErrorBanner'
import { ApiError, get, post } from '@/lib/api'
import { formatIndianDateTime } from '@/lib/regional'

interface MailDefaults {
  available: boolean
  message?: string
  sender_email: string
  sender_configured: boolean
  to: string[]
  cc: string[]
  checked_at?: string | null
  subject?: string
  html_body?: string
}

function splitEmails(value: string): string[] {
  return value.split(/[,;\n]/).map((item) => item.trim()).filter(Boolean)
}

/**
 * "Send this mail now": the application's own mail (the latest check, in the wording saved in its settings)
 * to the people typed here. The saved scheduled recipients are only the starting point, so the mail can also
 * go to someone else. Open to everyone with the application, like its "check now" button. `refreshKey` should
 * change whenever a new check finishes so the card shows the newest data.
 */
export function MailNowCard({
  baseUrl,
  title,
  description,
  refreshKey,
  onSent,
}: {
  baseUrl: string
  title: string
  description: string
  refreshKey?: unknown
  onSent?: () => void
}) {
  const [defaults, setDefaults] = useState<MailDefaults | null>(null)
  const [toText, setToText] = useState('')
  const [ccText, setCcText] = useState('')
  const [subject, setSubject] = useState('')
  const [loadError, setLoadError] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [sending, setSending] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [sentTo, setSentTo] = useState<string[] | null>(null)

  const load = useCallback(async () => {
    try {
      const data = await get<MailDefaults>(`${baseUrl}/mail/defaults`)
      setDefaults(data)
      setToText(data.to.join(', '))
      setCcText(data.cc.join(', '))
      setSubject(data.subject ?? '')
      setLoadError(null)
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.message : 'Could not load the mail.')
    }
  }, [baseUrl])

  // reloading replaces what was typed, so only do it when the data underneath really changed
  useEffect(() => { void load() }, [load, refreshKey])

  const to = splitEmails(toText)
  const cc = splitEmails(ccText)

  async function send() {
    setSending(true)
    setError(null)
    setSentTo(null)
    try {
      await post(`${baseUrl}/mail/send`, { to, cc, subject })
      setSentTo([...to, ...cc])
      setConfirming(false)
      onSent?.()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not send the mail.')
    } finally {
      setSending(false)
    }
  }

  return (
    <GlassCard padding="lg" className="flex flex-col gap-4">
      <div className="flex flex-col gap-1">
        <h2 className="font-display text-lg font-semibold text-ink">{title}</h2>
        <p className="text-sm leading-6 text-ink-dim">{description}</p>
      </div>

      {loadError && <ErrorBanner>{loadError}</ErrorBanner>}

      {defaults && !defaults.available && (
        <p className="text-sm text-ink-dim">{defaults.message ?? 'There is nothing to send yet.'}</p>
      )}

      {defaults?.available && (
        <>
          <p className="text-xs text-ink-faint">
            Based on the check made {defaults.checked_at ? formatIndianDateTime(defaults.checked_at) : 'most recently'}.
            {' '}Use the check button above first if you need today’s numbers.
          </p>
          {!defaults.sender_configured && (
            <ErrorBanner>The sender email is not set up yet, so nothing can be sent. Ask an administrator to set it up.</ErrorBanner>
          )}
          <label className="flex flex-col gap-1.5">
            <span className="text-sm font-medium text-ink-dim">To</span>
            <input
              type="text"
              inputMode="email"
              className="field-control"
              value={toText}
              onChange={(e) => { setToText(e.target.value); setConfirming(false) }}
              placeholder="name@example.com, another@example.com"
            />
          </label>
          <label className="flex flex-col gap-1.5">
            <span className="text-sm font-medium text-ink-dim">Cc (optional)</span>
            <input
              type="text"
              inputMode="email"
              className="field-control"
              value={ccText}
              onChange={(e) => { setCcText(e.target.value); setConfirming(false) }}
            />
          </label>
          <p className="-mt-2 text-xs text-ink-faint">
            Separate several addresses with commas. These start as the people the scheduled mail goes to; change them to send to someone else.
          </p>
          <label className="flex flex-col gap-1.5">
            <span className="text-sm font-medium text-ink-dim">Subject</span>
            <input
              type="text"
              className="field-control"
              value={subject}
              maxLength={300}
              onChange={(e) => { setSubject(e.target.value); setConfirming(false) }}
            />
          </label>
          <div className="flex flex-col gap-1.5">
            <span className="text-sm font-medium text-ink-dim">Email preview</span>
            <iframe
              title="Email body preview"
              srcDoc={defaults.html_body}
              sandbox=""
              className="h-80 w-full rounded-xl border border-border bg-white"
            />
          </div>

          {error && <ErrorBanner className="whitespace-pre-wrap">{error}</ErrorBanner>}
          {sentTo && (
            <p className="flex items-center gap-2 text-sm text-emerald-600">
              <CheckCircle2 className="h-4 w-4" />
              Mail sent to {sentTo.join(', ')}.
            </p>
          )}

          <div className="flex flex-col-reverse items-stretch gap-3 sm:flex-row sm:items-center sm:justify-end">
            <span className="text-xs text-ink-faint sm:mr-auto">
              {defaults.sender_email ? `Sends from ${defaults.sender_email}` : ''}
            </span>
            {confirming ? (
              <>
                <Button variant="secondary" disabled={sending} onClick={() => setConfirming(false)}>Cancel</Button>
                <Button icon={<Mail className="h-4 w-4" />} loading={sending} onClick={() => void send()}>
                  Confirm and send to {to.length + cc.length} {to.length + cc.length === 1 ? 'person' : 'people'}
                </Button>
              </>
            ) : (
              <Button
                icon={<Mail className="h-4 w-4" />}
                disabled={!defaults.sender_configured || to.length === 0 || !subject.trim()}
                onClick={() => { setSentTo(null); setError(null); setConfirming(true) }}
              >
                Send mail
              </Button>
            )}
          </div>
        </>
      )}
    </GlassCard>
  )
}
