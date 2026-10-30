import { useCallback, useEffect, useMemo, useState } from 'react'
import { AlertTriangle, CheckCircle2, Mail } from 'lucide-react'
import { GlassCard } from '@/components/GlassCard'
import { Button } from '@/components/Button'
import { ErrorBanner } from '@/components/ErrorBanner'
import { ApiError, get, put } from '@/lib/api'
import { formatIndianDate, formatIndianDateTime } from '@/lib/regional'
import { cn } from '@/utils/cn'
import { segmentClass } from '@/utils/segment'

interface LastRun {
  at: string | null
  trigger: string | null
  status: string | null
  error: string | null
  detail: Record<string, unknown> | null
}

interface AutoMailSettings {
  enabled: boolean
  mode: 'weekly' | 'monthly'
  days: number[]
  month_days: number[]
  time: string
  times: string[]
  to: string[]
  cc: string[]
  subject_template: string
  intro_template: string
  signature: string
  send_when_unmapped: boolean
  placeholders: string[]
  default_subject: string
  default_intro: string
  sender_user_id: number | null
  sender_options: { user_id: number; name: string; email: string }[]
  sender_email: string | null
  sender_configured: boolean
  sender_problem: string | null
  system_sender_email: string | null
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

const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
const PRESETS: { label: string; days: number[] }[] = [
  { label: 'Every day', days: [0, 1, 2, 3, 4, 5, 6] },
  { label: 'Mon – Fri', days: [0, 1, 2, 3, 4] },
  { label: 'Mon – Sat', days: [0, 1, 2, 3, 4, 5] },
]

function chipClass(on: boolean): string {
  return cn(
    'rounded-xl border px-3.5 py-2 text-sm font-semibold transition duration-200',
    on
      ? 'border-transparent bg-accent text-white dark:bg-accent-2'
      : 'border-stroke/70 text-ink-dim hover:bg-surface/70 hover:text-ink',
  )
}

const MONTH_DAYS = Array.from({ length: 31 }, (_, index) => index + 1)
const LAST_DAY = 0    // in month_days: the last day of the month, whatever its length
const MAX_TIMES = 6
const MONTH_PRESETS: { label: string; days: number[] }[] = [
  { label: '1st', days: [1] },
  { label: '1st and 15th', days: [1, 15] },
  { label: 'Last day', days: [LAST_DAY] },
]

function ordinal(day: number): string {
  const tens = day % 100
  if (tens >= 11 && tens <= 13) return `${day}th`
  return `${day}${({ 1: 'st', 2: 'nd', 3: 'rd' } as Record<number, string>)[day % 10] ?? 'th'}`
}

function splitEmails(value: string): string[] {
  return value.split(/[,;\n]/).map((item) => item.trim()).filter(Boolean)
}

/**
 * Administrator-only controls for an application's automatic report mail:
 * the weekdays and time it goes out, who receives it, the wording, and an
 * send history. The server does the scheduling; this only edits the settings
 * and shows what happened. Mailing a report by hand, with a preview first, is
 * ReportMailPanel.
 */
export function AutoMailCard({
  baseUrl,
  reportName,
  showUnmapped = true,
}: {
  baseUrl: string
  reportName: string
  /** The "if a location has no mapping" choice, for a report that uses mappings. */
  showUnmapped?: boolean
}) {
  const [saved, setSaved] = useState<AutoMailSettings | null>(null)
  const [form, setForm] = useState<AutoMailSettings | null>(null)
  const [toText, setToText] = useState('')
  const [ccText, setCcText] = useState('')
  const [loadError, setLoadError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null)
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
    const keys = ['enabled', 'mode', 'days', 'month_days', 'times', 'to', 'cc', 'subject_template', 'intro_template', 'signature', 'send_when_unmapped', 'sender_user_id'] as const
    return keys.some((key) => JSON.stringify(saved[key]) !== JSON.stringify(current[key]))
  }, [saved, current])

  if (loadError) return <ErrorBanner>{loadError}</ErrorBanner>
  if (!form || !saved || !current) return null

  function update(patch: Partial<AutoMailSettings>) {
    setForm((previous) => (previous ? { ...previous, ...patch } : previous))
    setMessage(null)
  }

  function toggleMonthDay(day: number) {
    const days = current!.month_days.includes(day)
      ? current!.month_days.filter((d) => d !== day)
      : [...current!.month_days, day]
    update({ month_days: days.sort((a, b) => a - b) })
  }

  // the earliest time stays in `time`, which older code and the server read as the (first) send time
  function setTimes(times: string[]) {
    const cleaned = times.map((value) => value)
    update({ times: cleaned, time: [...cleaned].filter(Boolean).sort()[0] ?? '' })
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
        mode: current.mode,
        days: current.days,
        month_days: current.month_days,
        time: [...current.times].sort()[0] ?? current.time,
        times: current.times,
        to: current.to,
        cc: current.cc,
        subject_template: current.subject_template,
        intro_template: current.intro_template,
        signature: current.signature,
        send_when_unmapped: current.send_when_unmapped,
        sender_user_id: current.sender_user_id,
      })
      apply(next)
      setMessage({ ok: true, text: 'Saved.' })
    } catch (err) {
      setMessage({ ok: false, text: err instanceof ApiError ? err.message : 'Could not save the settings.' })
    } finally {
      setSaving(false)
    }
  }

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
            {saved.sender_problem ?? 'Nothing can be sent yet: the sender is not set up.'}
          </span>
        </div>
      )}

      <label className="flex flex-col gap-1.5 text-sm">
        <span className="font-medium text-ink-dim">Send the automatic mail from</span>
        <select
          className="field-control"
          value={current.sender_user_id === null ? '' : String(current.sender_user_id)}
          onChange={(event) => update({ sender_user_id: event.target.value === '' ? null : Number(event.target.value) })}
        >
          <option value="">
            System email{saved.system_sender_email ? ` (${saved.system_sender_email})` : ' (not set up)'}
          </option>
          {current.sender_options.map((option) => (
            <option key={option.user_id} value={option.user_id}>
              {option.name} ({option.email})
            </option>
          ))}
          {current.sender_user_id !== null
            && !current.sender_options.some((option) => option.user_id === current.sender_user_id) && (
              <option value={current.sender_user_id}>Chosen person (no longer available)</option>
            )}
        </select>
        <span className="text-xs text-ink-faint">
          The mail goes out through this Gmail account. People listed here have set up their own email
          under Settings. The mail you send by hand always goes from your own email.
        </span>
      </label>

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
        <span className="text-sm font-medium text-ink-dim">Repeat</span>
        <div className="segmented-control self-start">
          {(['weekly', 'monthly'] as const).map((mode) => (
            <button
              key={mode}
              type="button"
              aria-pressed={current.mode === mode}
              onClick={() => update({ mode })}
              className={segmentClass(current.mode === mode)}
            >
              {mode === 'weekly' ? 'Weekly: on days of the week' : 'Monthly: on days of the month'}
            </button>
          ))}
        </div>
      </div>

      {current.mode === 'weekly' ? (
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
                  className={chipClass(on)}
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
      ) : (
        <div className="flex flex-col gap-2">
          <span className="text-sm font-medium text-ink-dim">Send on these days of the month</span>
          <div className="flex flex-wrap gap-2">
            {MONTH_DAYS.map((day) => {
              const on = current.month_days.includes(day)
              return (
                <button
                  key={day}
                  type="button"
                  aria-pressed={on}
                  aria-label={ordinal(day)}
                  onClick={() => toggleMonthDay(day)}
                  className={cn(chipClass(on), 'min-w-11 px-0 text-center')}
                >
                  {day}
                </button>
              )
            })}
            <button
              type="button"
              aria-pressed={current.month_days.includes(LAST_DAY)}
              onClick={() => toggleMonthDay(LAST_DAY)}
              className={chipClass(current.month_days.includes(LAST_DAY))}
            >
              Last day
            </button>
          </div>
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs">
            {MONTH_PRESETS.map((preset) => (
              <button
                key={preset.label}
                type="button"
                className="font-medium text-accent hover:underline"
                onClick={() => update({ month_days: preset.days })}
              >
                {preset.label}
              </button>
            ))}
          </div>
          <p className="text-xs text-ink-faint">
            A day a month does not have (the 31st in a 30-day month) is skipped; choose Last day to always
            send on the month&apos;s final day.
          </p>
        </div>
      )}

      <div className="flex flex-col gap-2">
        <span className="text-sm font-medium text-ink-dim">
          Send at (IST){current.times.length > 1 ? `: ${current.times.length} times a day` : ''}
        </span>
        <div className="flex flex-wrap items-center gap-2">
          {current.times.map((value, index) => (
            <span key={index} className="inline-flex items-center gap-1.5">
              <input
                type="time"
                className="field-control w-36"
                aria-label={`Send time ${index + 1}`}
                value={value}
                onChange={(event) => setTimes(current.times.map((t, i) => (i === index ? event.target.value : t)))}
              />
              {current.times.length > 1 && (
                <button
                  type="button"
                  className="rounded-lg px-2 py-1 text-xs font-medium text-ink-dim hover:bg-surface/70 hover:text-red-500"
                  aria-label={`Remove send time ${index + 1}`}
                  onClick={() => setTimes(current.times.filter((_, i) => i !== index))}
                >
                  Remove
                </button>
              )}
            </span>
          ))}
          {current.times.length < MAX_TIMES && (
            <button
              type="button"
              className="rounded-xl border border-dashed border-stroke/70 px-3 py-2 text-sm font-medium text-accent hover:bg-surface/70"
              onClick={() => setTimes([...current.times, '14:00'])}
            >
              + Add another time
            </button>
          )}
        </div>
      </div>

      {showUnmapped && (
        <label className="flex flex-col gap-1.5 text-sm sm:max-w-md">
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
      )}

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

      <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:items-center sm:justify-end">
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
