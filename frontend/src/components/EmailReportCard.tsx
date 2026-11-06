import { useCallback, useEffect, useState } from 'react'
import { CheckCircle2, Mail } from 'lucide-react'
import { GlassCard } from '@/components/GlassCard'
import { Button } from '@/components/Button'
import { ErrorBanner } from '@/components/ErrorBanner'
import { ApiError, get, post } from '@/lib/api'
import { formatIndianDateTime } from '@/lib/regional'

interface EmailDefaults {
  to: string[]
  cc: string[]
  subject: string
  message: string
  sender_configured: boolean
  sender_email: string
}

interface LogItem {
  id: number
  status: string
  requested_by: string | null
  started_at: string | null
  to: string[]
  cc: string[]
  error: string | null
}

function splitEmails(value: string): string[] {
  return value.split(/[,;\n]/).map((item) => item.trim()).filter(Boolean)
}

function newRequestId(): string {
  return typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`
}

/**
 * "Email this report": sends the finished report of the job above as an attachment, from the signed-in
 * person's own email identity (Settings), to the people typed here. Shown next to the Download button of a
 * finished report. Open to everyone with the application; every send is kept in the history below.
 */
export function EmailReportCard({
  baseUrl,
  jobId,
  reportName,
}: {
  baseUrl: string
  jobId: string
  reportName: string
}) {
  const [open, setOpen] = useState(false)
  const [defaults, setDefaults] = useState<EmailDefaults | null>(null)
  const [toText, setToText] = useState('')
  const [ccText, setCcText] = useState('')
  const [subject, setSubject] = useState('')
  const [message, setMessage] = useState('')
  const [loadError, setLoadError] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [sending, setSending] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [sentTo, setSentTo] = useState<string[] | null>(null)
  const [log, setLog] = useState<LogItem[]>([])

  const loadLog = useCallback(async () => {
    try {
      const data = await get<{ items: LogItem[] }>(`${baseUrl}/email/log?limit=5`)
      setLog(data.items)
    } catch {
      setLog([])
    }
  }, [baseUrl])

  async function openPanel() {
    setOpen(true)
    setSentTo(null)
    setError(null)
    if (defaults) return
    try {
      const data = await get<EmailDefaults>(`${baseUrl}/email/defaults`)
      setDefaults(data)
      setToText(data.to.join(', '))
      setCcText(data.cc.join(', '))
      setSubject(data.subject)
      setMessage(data.message)
      setLoadError(null)
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.message : 'Could not load the email settings.')
    }
    void loadLog()
  }

  // a different report was generated: forget the previous confirmation and result
  useEffect(() => {
    setConfirming(false)
    setSentTo(null)
    setError(null)
  }, [jobId])

  const to = splitEmails(toText)
  const cc = splitEmails(ccText)

  async function send() {
    setSending(true)
    setError(null)
    try {
      await post(`${baseUrl}/email/send`, { job_id: jobId, request_id: newRequestId(), to, cc, subject, message })
      setSentTo([...to, ...cc])
      setConfirming(false)
      void loadLog()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not send the email.')
    } finally {
      setSending(false)
    }
  }

  if (!open) {
    return (
      <div className="flex justify-stretch sm:justify-end">
        <Button variant="secondary" icon={<Mail className="h-4 w-4" />} onClick={() => void openPanel()}>
          Email this report
        </Button>
      </div>
    )
  }

  return (
    <GlassCard padding="lg" className="flex flex-col gap-4">
      <div className="flex flex-col gap-1">
        <h2 className="font-display text-lg font-semibold text-ink">Email the {reportName}</h2>
        <p className="text-sm leading-6 text-ink-dim">
          The finished report is attached to the email. It goes out from your own email address (set up under Settings).
        </p>
      </div>

      {loadError && <ErrorBanner>{loadError}</ErrorBanner>}

      {defaults && (
        <>
          {!defaults.sender_configured && (
            <ErrorBanner>You haven’t set up your email sender yet, so nothing can be sent from your account. Go to Settings.</ErrorBanner>
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
          <p className="-mt-2 text-xs text-ink-faint">Separate several addresses with commas.</p>
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
          <label className="flex flex-col gap-1.5">
            <span className="text-sm font-medium text-ink-dim">Message</span>
            <textarea
              className="field-control min-h-28"
              value={message}
              maxLength={5000}
              onChange={(e) => { setMessage(e.target.value); setConfirming(false) }}
            />
          </label>

          {error && <ErrorBanner className="whitespace-pre-wrap">{error}</ErrorBanner>}
          {sentTo && (
            <p className="flex items-center gap-2 text-sm text-emerald-600">
              <CheckCircle2 className="h-4 w-4" />
              Email sent to {sentTo.join(', ')}.
            </p>
          )}

          <div className="flex flex-col-reverse items-stretch gap-3 sm:flex-row sm:items-center sm:justify-end">
            <span className="text-xs text-ink-faint sm:mr-auto">
              {defaults.sender_email ? `Sends from ${defaults.sender_email}` : ''}
            </span>
            <Button variant="secondary" disabled={sending} onClick={() => { setOpen(false); setConfirming(false) }}>
              Close
            </Button>
            {confirming ? (
              <Button icon={<Mail className="h-4 w-4" />} loading={sending} onClick={() => void send()}>
                Confirm and send to {to.length + cc.length} {to.length + cc.length === 1 ? 'person' : 'people'}
              </Button>
            ) : (
              <Button
                icon={<Mail className="h-4 w-4" />}
                disabled={!defaults.sender_configured || to.length === 0 || !subject.trim() || !message.trim()}
                onClick={() => { setSentTo(null); setError(null); setConfirming(true) }}
              >
                Send email
              </Button>
            )}
          </div>

          {log.length > 0 && (
            <div className="subpanel flex flex-col gap-2 p-4">
              <p className="text-sm font-semibold text-ink">Recently sent</p>
              <ul className="flex flex-col gap-1.5 text-xs text-ink-dim">
                {log.map((item) => (
                  <li key={item.id}>
                    {item.started_at ? formatIndianDateTime(item.started_at) : ''} by {item.requested_by ?? 'the system'} to{' '}
                    {[...item.to, ...item.cc].join(', ') || 'nobody'}:{' '}
                    <span className={item.status === 'sent' ? 'text-emerald-600' : 'text-red-500'}>
                      {item.status === 'sent' ? 'sent' : `not sent${item.error ? ` (${item.error})` : ''}`}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
    </GlassCard>
  )
}
