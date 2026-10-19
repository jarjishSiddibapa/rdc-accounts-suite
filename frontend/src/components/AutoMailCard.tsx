import { useCallback, useEffect, useMemo, useState } from 'react'
import { AlertTriangle, CheckCircle2, Mail, Send } from 'lucide-react'
import { GlassCard } from '@/components/GlassCard'
import { Button } from '@/components/Button'
import { ErrorBanner } from '@/components/ErrorBanner'
import { ProgressPanel, type JobState, type JobStatus } from '@/components/ProgressPanel'
import { ApiError, get, post, put } from '@/lib/api'
import { formatIndianDate, formatIndianDateTime } from '@/lib/regional'
import { cn } from '@/utils/cn'

interface LastRun {
  at: string | null
  trigger: string | null
  status: string | null
  error: string | null
  detail: Record<string, unknown> | null
}

interface AutoMailSettings {
  enabled: boolean
  days: number[]
  time: string
  to: string[]
  cc: string[]
  subject_template: string
  intro_template: string
  signature: string
  send_when_unmapped: boolean
  placeholders: string[]
  default_subject: string
  default_intro: string
  sender_configured: boolean
  next_run: string | null
  running: boolean
  last: LastRun
}

interface LogMessage {
  subject: string
  sent_at: string | null
  attachments: { name: string; bytes: number | null }[]
}

interface LogEntry {
  id: number
  trigger: string
  requested_by: string | null
  status: string
  started_at: string | null
  finished_at: string | null
  to: string[]
  cc: string[]
  messages: LogMessage[]
  detail: Record<string, unknown>
  error: string | null
}

interface LogPage {
  total: number
  items: LogEntry[]
}

const LOG_PAGE_SIZE = 10

const STATUS_STYLE: Record<string, { label: string; className: string }> = {
  sent: { label: 'Sent', className: 'bg-emerald-500/15 text-emerald-600' },
  failed: { label: 'Failed', className: 'bg-red-500/15 text-red-500' },
  held: { label: 'Held back', className: 'bg-amber-500/15 text-amber-600' },
  skipped: { label: 'Skipped', className: 'bg-slate-500/15 text-ink-dim' },
  running: { label: 'Running', className: 'bg-sky-500/15 text-sky-600' },
  interrupted: { label: 'Interrupted', className: 'bg-red-500/15 text-red-500' },
}

/** Date and time to the second, in IST - the log is about exactly when. */
function formatLogTime(value: string | null): string {
  if (!value) return '-'
  return formatIndianDate(value, {
    day: '2-digit', month: 'short', year: 'numeric',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: true,
  })
}

function formatBytes(bytes: number | null): string {
  if (bytes === null) return ''
  return bytes >= 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`
}

interface SendResult {
  status?: string
  message?: string
}

interface JobResponse {
  status: JobStatus
  progress: number
  phase: string
  result: SendResult | null
  error: string | null
}

const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
const PRESETS: { label: string; days: number[] }[] = [
  { label: 'Every day', days: [0, 1, 2, 3, 4, 5, 6] },
  { label: 'Mon – Fri', days: [0, 1, 2, 3, 4] },
  { label: 'Mon – Sat', days: [0, 1, 2, 3, 4, 5] },
]

function splitEmails(value: string): string[] {
  return value.split(/[,;\n]/).map((item) => item.trim()).filter(Boolean)
}

/**
 * Administrator-only controls for an application's automatic report mail:
 * the weekdays and time it goes out, who receives it, the wording, and an
 * on-demand "Send now". The server does the scheduling; this only edits the
 * settings and shows what happened last.
 */
export function AutoMailCard({ baseUrl, reportName }: { baseUrl: string; reportName: string }) {
  const [saved, setSaved] = useState<AutoMailSettings | null>(null)
  const [form, setForm] = useState<AutoMailSettings | null>(null)
  const [toText, setToText] = useState('')
  const [ccText, setCcText] = useState('')
  const [loadError, setLoadError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null)
  const [confirmSend, setConfirmSend] = useState(false)
  const [sendJobId, setSendJobId] = useState<string | null>(null)
  const [sendStarting, setSendStarting] = useState(false)
  const [sendOutcome, setSendOutcome] = useState<{ ok: boolean; text: string } | null>(null)
  const [log, setLog] = useState<LogPage | null>(null)
  const [logPage, setLogPage] = useState(0)
  const [logError, setLogError] = useState<string | null>(null)

  const apply = useCallback((data: AutoMailSettings) => {
    setSaved(data)
    setForm(data)
    setToText(data.to.join(', '))
    setCcText(data.cc.join(', '))
  }, [])

  const load = useCallback(async () => {
    try {
      apply(await get<AutoMailSettings>(`${baseUrl}/auto-mail`))
      setLoadError(null)
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.message : 'Could not load the automatic email settings.')
    }
  }, [apply, baseUrl])

  const loadLog = useCallback(async (page: number) => {
    try {
      setLog(await get<LogPage>(`${baseUrl}/auto-mail/log?limit=${LOG_PAGE_SIZE}&offset=${page * LOG_PAGE_SIZE}`))
      setLogPage(page)
      setLogError(null)
    } catch (err) {
      setLogError(err instanceof ApiError ? err.message : 'Could not load the send history.')
    }
  }, [baseUrl])

  useEffect(() => {
    void load()
    void loadLog(0)
  }, [load, loadLog])

  const current = useMemo(
    () => (form ? { ...form, to: splitEmails(toText), cc: splitEmails(ccText) } : null),
    [form, toText, ccText],
  )
  const dirty = useMemo(() => {
    if (!saved || !current) return false
    const keys = ['enabled', 'days', 'time', 'to', 'cc', 'subject_template', 'intro_template', 'signature', 'send_when_unmapped'] as const
    return keys.some((key) => JSON.stringify(saved[key]) !== JSON.stringify(current[key]))
  }, [saved, current])

  if (loadError) return <ErrorBanner>{loadError}</ErrorBanner>
  if (!form || !saved || !current) return null

  function update(patch: Partial<AutoMailSettings>) {
    setForm((previous) => (previous ? { ...previous, ...patch } : previous))
    setMessage(null)
  }

  function toggleDay(day: number) {
    const days = current!.days.includes(day) ? current!.days.filter((d) => d !== day) : [...current!.days, day]
    update({ days: days.sort((a, b) => a - b) })
  }

  async function handleSave() {
    if (!current) return
    setSaving(true)
    setMessage(null)
    try {
      const next = await put<AutoMailSettings>(`${baseUrl}/auto-mail`, {
        enabled: current.enabled,
        days: current.days,
        time: current.time,
        to: current.to,
        cc: current.cc,
        subject_template: current.subject_template,
        intro_template: current.intro_template,
        signature: current.signature,
        send_when_unmapped: current.send_when_unmapped,
      })
      apply(next)
      setMessage({ ok: true, text: 'Saved.' })
    } catch (err) {
      setMessage({ ok: false, text: err instanceof ApiError ? err.message : 'Could not save the settings.' })
    } finally {
      setSaving(false)
    }
  }

  async function handleSendNow() {
    setConfirmSend(false)
    setSendStarting(true)
    setSendOutcome(null)
    setSendJobId(null)
    try {
      const res = await post<{ job_id: string }>(`${baseUrl}/auto-mail/send-now`)
      setSendJobId(res.job_id)
    } catch (err) {
      setSendOutcome({ ok: false, text: err instanceof ApiError ? err.message : 'Could not start the mail.' })
    } finally {
      setSendStarting(false)
    }
  }

  async function pollSend(jobId: string): Promise<JobState<SendResult>> {
    const job = await get<JobResponse>(`${baseUrl}/jobs/${jobId}`)
    return {
      status: job.status,
      progress: (job.progress ?? 0) * 100,
      phase: job.phase,
      result: job.result ?? undefined,
      error: job.error ?? undefined,
    }
  }

  const sending = sendStarting || (sendJobId !== null && sendOutcome === null)
  const recipientCount = saved.to.length + saved.cc.length
  const last = saved.last

  return (
    <GlassCard padding="lg" className="flex flex-col gap-5">
      <div className="flex items-center gap-4">
        <span className="icon-tile grid h-12 w-12 place-items-center rounded-xl">
          <Mail className="h-5 w-5" />
        </span>
        <div>
          <p className="text-sm font-semibold text-accent">Administrator</p>
          <h2 className="mt-1.5 font-display text-xl font-semibold tracking-[-0.025em] text-ink">
            Automatic email
          </h2>
          <p className="mt-1 text-sm leading-6 text-ink-dim">
            Mails the {reportName} to the recipients below on the days and time you choose, using the
            latest data from the ERP. The report is built when the time arrives, so allow a few
            minutes before it lands.
          </p>
        </div>
      </div>

      {!saved.sender_configured && (
        <div className="flex items-start gap-3 rounded-2xl border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm text-amber-700">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>
            The system email is not set up, so nothing can be sent yet. Configure it under Email
            administration first.
          </span>
        </div>
      )}

      <label className="flex items-center gap-3 text-sm font-medium text-ink">
        <input
          type="checkbox"
          className="h-4 w-4 accent-accent"
          checked={current.enabled}
          onChange={(event) => update({ enabled: event.target.checked })}
        />
        Send this email automatically
      </label>

      <div className="flex flex-col gap-2">
        <span className="text-sm font-medium text-ink-dim">Send on</span>
        <div className="flex flex-wrap gap-2">
          {WEEKDAYS.map((name, day) => {
            const on = current.days.includes(day)
            return (
              <button
                key={name}
                type="button"
                aria-pressed={on}
                onClick={() => toggleDay(day)}
                className={cn(
                  'rounded-xl border px-3.5 py-2 text-sm font-semibold transition duration-200',
                  on
                    ? 'border-transparent bg-accent text-white dark:bg-accent-2'
                    : 'border-stroke/70 text-ink-dim hover:bg-surface/70 hover:text-ink',
                )}
              >
                {name}
              </button>
            )
          })}
        </div>
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs">
          {PRESETS.map((preset) => (
            <button
              key={preset.label}
              type="button"
              className="font-medium text-accent hover:underline"
              onClick={() => update({ days: preset.days })}
            >
              {preset.label}
            </button>
          ))}
        </div>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <label className="flex flex-col gap-1.5 text-sm">
          <span className="font-medium text-ink-dim">Send time (IST)</span>
          <input
            type="time"
            className="field-control"
            value={current.time}
            onChange={(event) => update({ time: event.target.value })}
          />
        </label>
        <label className="flex flex-col gap-1.5 text-sm">
          <span className="font-medium text-ink-dim">If a location or incharge has no mapping</span>
          <select
            className="field-control"
            value={current.send_when_unmapped ? 'send' : 'hold'}
            onChange={(event) => update({ send_when_unmapped: event.target.value === 'send' })}
          >
            <option value="send">Send anyway (the missing values show as -)</option>
            <option value="hold">Hold the mail until they are mapped</option>
          </select>
        </label>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <label className="flex flex-col gap-1.5 text-sm">
          <span className="font-medium text-ink-dim">To (comma-separated)</span>
          <input
            type="text"
            inputMode="email"
            className="field-control"
            value={toText}
            placeholder="name@rdc.in, other@rdc.in"
            onChange={(event) => { setToText(event.target.value); setMessage(null) }}
          />
        </label>
        <label className="flex flex-col gap-1.5 text-sm">
          <span className="font-medium text-ink-dim">Cc (comma-separated)</span>
          <input
            type="text"
            inputMode="email"
            className="field-control"
            value={ccText}
            placeholder="name@rdc.in"
            onChange={(event) => { setCcText(event.target.value); setMessage(null) }}
          />
        </label>
      </div>

      <label className="flex flex-col gap-1.5 text-sm">
        <span className="font-medium text-ink-dim">Subject</span>
        <input
          type="text"
          className="field-control"
          value={current.subject_template}
          onChange={(event) => update({ subject_template: event.target.value })}
        />
      </label>
      <label className="flex flex-col gap-1.5 text-sm">
        <span className="font-medium text-ink-dim">Mail text</span>
        <textarea
          rows={5}
          className="field-control"
          value={current.intro_template}
          onChange={(event) => update({ intro_template: event.target.value })}
        />
        <span className="text-xs text-ink-faint">
          The tables and the attachment follow this text. You can use{' '}
          {current.placeholders.map((name) => `{${name}}`).join(', ')} in the subject and the text.
        </span>
      </label>
      <label className="flex flex-col gap-1.5 text-sm">
        <span className="font-medium text-ink-dim">Signature (optional)</span>
        <textarea
          rows={3}
          className="field-control"
          value={current.signature}
          onChange={(event) => update({ signature: event.target.value })}
        />
      </label>

      <div className="flex flex-col gap-1 text-sm text-ink-dim">
        <p>
          {saved.enabled && saved.next_run
            ? `Next automatic mail: ${formatIndianDateTime(saved.next_run)} IST.`
            : 'Automatic mail is switched off.'}
        </p>
        {last.status && last.at && (
          <p className={cn(last.status === 'sent' ? 'text-emerald-600' : 'text-amber-600')}>
            Last run ({last.trigger === 'manual' ? 'sent by hand' : 'scheduled'}), {formatIndianDateTime(last.at)}:{' '}
            {last.status === 'sent' ? 'mail sent.' : last.status === 'held' ? 'held back.' : 'failed.'}
            {last.error ? ` ${last.error}` : ''}
          </p>
        )}
      </div>

      {message && (
        message.ok
          ? <p className="flex items-center gap-1.5 text-sm text-emerald-600"><CheckCircle2 className="h-4 w-4" />{message.text}</p>
          : <ErrorBanner className="whitespace-pre-wrap">{message.text}</ErrorBanner>
      )}

      {sendJobId && (
        <ProgressPanel
          jobId={sendJobId}
          poller={pollSend}
          cancelOnTabClose={false}
          onDone={(res) => {
            setSendOutcome(
              res?.status === 'sent'
                ? { ok: true, text: 'The mail has been sent.' }
                : { ok: false, text: res?.message ?? 'The mail was not sent.' },
            )
            void load()
            void loadLog(0)
          }}
          onError={(error) => {
            setSendOutcome({ ok: false, text: error })
            void load()
            void loadLog(0)
          }}
        />
      )}
      {sendOutcome && (
        sendOutcome.ok
          ? <p className="flex items-center gap-1.5 text-sm text-emerald-600"><CheckCircle2 className="h-4 w-4" />{sendOutcome.text}</p>
          : <ErrorBanner className="whitespace-pre-wrap">{sendOutcome.text}</ErrorBanner>
      )}

      {confirmSend && (
        <div className="flex flex-col gap-3 rounded-2xl border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm text-ink sm:flex-row sm:items-center sm:justify-between">
          <span>
            This builds the report now and mails it to {recipientCount} recipient{recipientCount === 1 ? '' : 's'}.
            It takes a few minutes.
          </span>
          <span className="flex gap-2">
            <Button variant="secondary" onClick={() => setConfirmSend(false)}>Cancel</Button>
            <Button icon={<Send className="h-4 w-4" />} onClick={() => void handleSendNow()}>Send now</Button>
          </span>
        </div>
      )}

      <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:items-center sm:justify-end">
        {dirty && <span className="text-xs text-ink-faint sm:mr-auto">Save your changes before sending.</span>}
        <Button
          variant="secondary"
          icon={<Send className="h-4 w-4" />}
          loading={sending}
          disabled={dirty || sending || saved.running || recipientCount === 0 || !saved.sender_configured}
          onClick={() => { setSendOutcome(null); setConfirmSend(true) }}
        >
          Send now
        </Button>
        <Button loading={saving} disabled={!dirty} onClick={() => void handleSave()}>
          Save
        </Button>
      </div>

      <div className="flex flex-col gap-3 border-t border-stroke/60 pt-5">
        <div className="flex items-center justify-between gap-3">
          <h3 className="font-display text-lg font-semibold text-ink">Send history</h3>
          <Button variant="secondary" onClick={() => void loadLog(logPage)}>Refresh</Button>
        </div>
        <p className="text-xs text-ink-faint">
          Every run, newest first: when it started and finished (IST), whether it was scheduled or sent
          by hand, who it went to, and each message actually sent.
        </p>
        {logError && <ErrorBanner>{logError}</ErrorBanner>}
        {log && log.items.length === 0 && <p className="text-sm text-ink-dim">Nothing has been sent yet.</p>}
        <div className="flex flex-col gap-3">
          {log?.items.map((entry) => {
            const style = STATUS_STYLE[entry.status] ?? { label: entry.status, className: 'bg-slate-500/15 text-ink-dim' }
            const note = typeof entry.detail?.attachment === 'string' ? entry.detail.attachment : null
            return (
              <div key={entry.id} className="rounded-xl border border-stroke/70 bg-surface/55 px-4 py-3 text-sm">
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                  <span className={cn('rounded-full px-2.5 py-0.5 text-xs font-semibold', style.className)}>{style.label}</span>
                  <span className="font-medium text-ink">{formatLogTime(entry.started_at)}</span>
                  <span className="text-ink-dim">
                    {entry.trigger === 'manual' ? `Sent by hand${entry.requested_by ? ` by ${entry.requested_by}` : ''}` : 'Scheduled'}
                  </span>
                  {entry.finished_at && entry.status !== 'running' && (
                    <span className="text-xs text-ink-faint">finished {formatLogTime(entry.finished_at)}</span>
                  )}
                </div>
                {entry.messages.length > 0 && (
                  <ul className="mt-2 flex flex-col gap-1 text-ink-dim">
                    {entry.messages.map((message, index) => (
                      <li key={index}>
                        <span className="text-ink">{message.subject}</span>
                        {' - sent '}{formatLogTime(message.sent_at)}
                        {message.attachments.length > 0 && (
                          <span className="text-xs text-ink-faint">
                            {' '}({message.attachments.map((a) => `${a.name}${a.bytes !== null ? `, ${formatBytes(a.bytes)}` : ''}`).join('; ')})
                          </span>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
                {(entry.to.length > 0 || entry.cc.length > 0) && (
                  <p className="mt-1 text-xs text-ink-faint">
                    To: {entry.to.join(', ') || '-'}{entry.cc.length > 0 ? ` | Cc: ${entry.cc.join(', ')}` : ''}
                  </p>
                )}
                {note && <p className="mt-1 text-xs text-ink-faint">Attachment: {note}</p>}
                {entry.error && <p className="mt-1 whitespace-pre-wrap text-xs text-red-500">{entry.error}</p>}
              </div>
            )
          })}
        </div>
        {log && log.total > LOG_PAGE_SIZE && (
          <div className="flex items-center justify-between text-sm text-ink-dim">
            <Button variant="secondary" disabled={logPage === 0} onClick={() => void loadLog(logPage - 1)}>Newer</Button>
            <span>
              Page {logPage + 1} of {Math.ceil(log.total / LOG_PAGE_SIZE)} ({log.total} runs)
            </span>
            <Button
              variant="secondary"
              disabled={(logPage + 1) * LOG_PAGE_SIZE >= log.total}
              onClick={() => void loadLog(logPage + 1)}
            >
              Older
            </Button>
          </div>
        )}
      </div>
    </GlassCard>
  )
}
