import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { AlertTriangle, ArrowRight, CheckCircle2, Download, Eye, Mail } from 'lucide-react'
import { GlassCard } from '@/components/GlassCard'
import { Button } from '@/components/Button'
import { ErrorBanner } from '@/components/ErrorBanner'
import { ProgressPanel, type JobState, type JobStatus } from '@/components/ProgressPanel'
import { ApiError, apiUrl, get, post } from '@/lib/api'
import { formatIndianDate } from '@/lib/regional'

interface Defaults {
  to: string[]
  cc: string[]
  subject: string
  intro: string
  placeholders: string[]
  sender_configured: boolean
  sender_email: string
}

interface FileEntry {
  name: string
  bytes: number | null
}

interface PreviewMessage {
  subject: string
  html_body: string
  attachments: FileEntry[]
}

interface Warning {
  category: string
  items: string[]
}

interface PreviewResult {
  status: 'preview' | 'needs_mapping_fix' | 'email_not_configured'
  message?: string
  from_email?: string
  to?: string[]
  cc?: string[]
  messages?: PreviewMessage[]
  downloads?: (FileEntry & { label: string })[]
  notes?: string[]
  validation_warnings?: Warning[]
}

interface SentMessage {
  subject: string
  sent_at: string
}

interface JobResponse {
  status: JobStatus
  progress: number
  phase: string
  result: PreviewResult | null
  error: string | null
}

function splitEmails(value: string): string[] {
  return value.split(/[,;\n]/).map((item) => item.trim()).filter(Boolean)
}

function formatBytes(bytes: number | null): string {
  if (bytes === null) return ''
  return bytes >= 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`
}

function formatSentTime(value: string): string {
  return formatIndianDate(value, {
    day: '2-digit', month: 'short', year: 'numeric',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: true,
  })
}

/**
 * Mail a report by hand, the same way as the Unaccounted / MRN / PO mail:
 * build the report, show exactly how the email will look (with the report to
 * download from right there), and send only when "Confirm & send" is clicked.
 * It sends from the signed-in person's own email identity (Settings).
 */
export function ReportMailPanel({ baseUrl, reportName }: { baseUrl: string; reportName: string }) {
  const [defaults, setDefaults] = useState<Defaults | null>(null)
  const [toText, setToText] = useState('')
  const [ccText, setCcText] = useState('')
  const [subject, setSubject] = useState('')
  const [intro, setIntro] = useState('')
  const [loadError, setLoadError] = useState<string | null>(null)

  const [starting, setStarting] = useState(false)
  const [jobId, setJobId] = useState<string | null>(null)
  const [result, setResult] = useState<PreviewResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [confirming, setConfirming] = useState(false)
  const [sent, setSent] = useState<SentMessage[] | null>(null)

  const busy = starting || (jobId !== null && result === null && !error)

  const load = useCallback(async () => {
    try {
      const data = await get<Defaults>(`${baseUrl}/mail/defaults`)
      setDefaults(data)
      setToText(data.to.join(', '))
      setCcText(data.cc.join(', '))
      setSubject(data.subject)
      setIntro(data.intro)
      setLoadError(null)
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.message : 'Could not load the email settings.')
    }
  }, [baseUrl])

  useEffect(() => {
    void load()
  }, [load])

  function edited(setter: (value: string) => void) {
    return (value: string) => {
      setter(value)
      // what was previewed no longer matches the fields
      setResult(null)
      setJobId(null)
      setSent(null)
      setError(null)
    }
  }

  async function handlePreview(allowUnmapped = false) {
    setStarting(true)
    setError(null)
    setResult(null)
    setSent(null)
    setJobId(null)
    try {
      const res = await post<{ job_id: string }>(`${baseUrl}/mail/preview`, {
        to: splitEmails(toText),
        cc: splitEmails(ccText),
        subject,
        intro,
        allow_unmapped: allowUnmapped,
      })
      setJobId(res.job_id)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not start the preview.')
    } finally {
      setStarting(false)
    }
  }

  async function pollPreview(id: string): Promise<JobState<PreviewResult>> {
    const job = await get<JobResponse>(`${baseUrl}/jobs/${id}`)
    return {
      status: job.status,
      progress: (job.progress ?? 0) * 100,
      phase: job.phase,
      result: job.result ?? undefined,
      error: job.error ?? undefined,
    }
  }

  async function handleConfirm() {
    if (!jobId) return
    setConfirming(true)
    setError(null)
    try {
      const res = await post<{ messages: SentMessage[] }>(`${baseUrl}/mail/confirm-send`, { job_id: jobId })
      setSent(res.messages)
      setResult(null)
      setJobId(null)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not send the email.')
    } finally {
      setConfirming(false)
    }
  }

  function handleDownload(index: number, name: string) {
    if (!jobId) return
    const anchor = document.createElement('a')
    anchor.href = apiUrl(`${baseUrl}/mail/download/${jobId}/${index}`)
    anchor.download = name
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
  }

  if (loadError) return <ErrorBanner>{loadError}</ErrorBanner>
  if (!defaults) return null

  const preview = result?.status === 'preview' ? result : null
  const first = preview?.messages?.[0]

  return (
    <GlassCard padding="lg" className="flex flex-col gap-5">
      <div className="flex items-center gap-4">
        <span className="icon-tile grid h-12 w-12 place-items-center rounded-xl">
          <Mail className="h-5 w-5" />
        </span>
        <div>
          <p className="text-sm font-semibold text-accent">Email</p>
          <h2 className="mt-1.5 font-display text-xl font-semibold tracking-[-0.025em] text-ink">
            Mail this report
          </h2>
          <p className="mt-1 text-sm leading-6 text-ink-dim">
            Builds the {reportName} from the latest ERP data and shows you exactly how the email will
            look. Nothing is sent until you click Confirm &amp; send, and you can download the report
            from the preview.
          </p>
        </div>
      </div>

      {!defaults.sender_configured && (
        <div className="flex items-start gap-3 rounded-xl border border-amber-500/30 bg-amber-500/10 px-4 py-3">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
          <div className="flex flex-col gap-2 text-sm">
            <span className="text-ink-dim">You haven't set up your email sender yet, so nothing can be sent from your account.</span>
            <Link to="/settings" className="inline-flex w-fit items-center gap-1.5 text-sm font-medium text-accent transition hover:gap-2.5">
              Go to Settings <ArrowRight className="h-4 w-4" />
            </Link>
          </div>
        </div>
      )}

      <div className="grid gap-4 sm:grid-cols-2">
        <label className="flex flex-col gap-1.5 text-sm">
          <span className="font-medium text-ink-dim">To (comma-separated)</span>
          <input type="text" inputMode="email" className="field-control" value={toText} onChange={(e) => edited(setToText)(e.target.value)} />
        </label>
        <label className="flex flex-col gap-1.5 text-sm">
          <span className="font-medium text-ink-dim">Cc (comma-separated)</span>
          <input type="text" inputMode="email" className="field-control" value={ccText} onChange={(e) => edited(setCcText)(e.target.value)} />
        </label>
      </div>
      <label className="flex flex-col gap-1.5 text-sm">
        <span className="font-medium text-ink-dim">Subject</span>
        <input type="text" className="field-control" value={subject} onChange={(e) => edited(setSubject)(e.target.value)} />
      </label>
      <label className="flex flex-col gap-1.5 text-sm">
        <span className="font-medium text-ink-dim">Mail text</span>
        <textarea rows={4} className="field-control" value={intro} onChange={(e) => edited(setIntro)(e.target.value)} />
        <span className="text-xs text-ink-faint">
          The tables and the attachment follow this text. {defaults.placeholders.map((name) => `{${name}}`).join(', ')}{' '}
          are filled in when the email is built.
        </span>
      </label>

      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-end">
        {defaults.sender_configured && (
          <span className="text-xs text-ink-faint sm:mr-auto">Sends from {defaults.sender_email}</span>
        )}
        <Button
          icon={<Eye className="h-4 w-4" />}
          loading={busy}
          disabled={busy || confirming || !defaults.sender_configured}
          onClick={() => void handlePreview()}
        >
          Preview email
        </Button>
      </div>

      {error && <ErrorBanner className="whitespace-pre-wrap">{error}</ErrorBanner>}

      {jobId && result === null && (
        <ProgressPanel
          jobId={jobId}
          poller={pollPreview}
          onDone={(res) => setResult(res ?? null)}
          onError={(message) => { setError(message); setJobId(null) }}
          onCancel={() => post(`${baseUrl}/jobs/${jobId}/cancel`)}
        />
      )}

      {sent && (
        <div className="flex flex-col gap-1 rounded-xl border border-emerald-500/30 bg-emerald-500/10 px-4 py-3 text-sm">
          <span className="flex items-center gap-2 font-medium text-emerald-600">
            <CheckCircle2 className="h-4 w-4" />
            {sent.length === 1 ? 'The email has been sent.' : `${sent.length} emails have been sent.`}
          </span>
          {sent.map((message, index) => (
            <span key={index} className="text-ink-dim">{message.subject} - {formatSentTime(message.sent_at)}</span>
          ))}
        </div>
      )}

      {result?.status === 'email_not_configured' && (
        <div className="flex items-start gap-3 rounded-xl border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm text-ink-dim">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
          {result.message ?? "You haven't set up your email sender yet. Go to Settings."}
        </div>
      )}

      {result?.status === 'needs_mapping_fix' && (
        <div className="flex flex-col gap-3 rounded-xl border border-amber-500/30 bg-amber-500/10 p-4">
          <div className="flex items-center gap-2 text-amber-600">
            <AlertTriangle className="h-4 w-4" />
            <h4 className="font-display text-sm font-semibold">Some values have no mapping</h4>
          </div>
          {(result.validation_warnings ?? []).map((warning) => (
            <p key={warning.category} className="text-sm text-ink-dim">
              <span className="font-medium text-ink">{warning.category} ({warning.items.length}):</span>{' '}
              {warning.items.slice(0, 8).join(', ')}{warning.items.length > 8 ? ` and ${warning.items.length - 8} more` : ''}
            </p>
          ))}
          <p className="text-sm text-ink-dim">
            Add them in the mapping tables on this page and preview again, or preview anyway - they show as "-".
          </p>
          <div className="flex justify-end">
            <Button variant="secondary" loading={starting} onClick={() => void handlePreview(true)}>
              Preview with unmapped values
            </Button>
          </div>
        </div>
      )}

      {preview && first && (
        <div className="flex flex-col gap-4 rounded-xl border border-accent/30 bg-accent/5 p-4">
          <div className="flex items-center gap-2 text-accent">
            <Eye className="h-4 w-4" />
            <h4 className="font-display text-sm font-semibold">Review before sending. Nothing has been sent yet</h4>
          </div>

          <div className="grid grid-cols-1 gap-x-6 gap-y-2 rounded-xl border border-border bg-surface/60 p-4 text-sm sm:grid-cols-[auto_1fr]">
            <span className="text-ink-faint">From</span>
            <span className="text-ink">{preview.from_email}</span>
            <span className="text-ink-faint">To</span>
            <span className="text-ink">{(preview.to ?? []).join(', ')}</span>
            {(preview.cc ?? []).length > 0 && (
              <>
                <span className="text-ink-faint">Cc</span>
                <span className="text-ink">{(preview.cc ?? []).join(', ')}</span>
              </>
            )}
            <span className="text-ink-faint">Subject</span>
            <span className="text-ink">{first.subject}</span>
            <span className="text-ink-faint">Attachments</span>
            <span className="text-ink">
              {first.attachments.map((a) => `${a.name}${a.bytes !== null ? ` (${formatBytes(a.bytes)})` : ''}`).join(', ') || 'None'}
            </span>
          </div>

          {(preview.messages?.length ?? 0) > 1 && (
            <div className="rounded-xl border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm text-ink-dim">
              <p className="font-medium text-ink">
                This goes out as {preview.messages?.length} emails, because the report is too large for one:
              </p>
              <ul className="mt-1 list-disc pl-5">
                {preview.messages?.map((message, index) => (
                  <li key={index}>
                    {message.subject}
                    <span className="text-xs text-ink-faint">
                      {' '}({message.attachments.map((a) => `${a.name}, ${formatBytes(a.bytes)}`).join('; ')})
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {(preview.notes ?? []).map((note, index) => (
            <p key={index} className="text-sm text-amber-700">{note}</p>
          ))}

          {(preview.downloads ?? []).length > 0 && (
            <div className="flex flex-wrap items-center gap-2 rounded-xl border border-border bg-surface/60 p-3">
              <span className="mr-auto text-sm font-medium text-ink-dim">Download the report</span>
              {(preview.downloads ?? []).map((file, index) => (
                <Button
                  key={index}
                  variant="secondary"
                  icon={<Download className="h-4 w-4" />}
                  onClick={() => handleDownload(index, file.name)}
                >
                  {file.label} ({formatBytes(file.bytes)})
                </Button>
              ))}
            </div>
          )}

          <div className="flex flex-col gap-1.5">
            <span className="text-sm font-medium text-ink-dim">Email preview</span>
            <iframe
              title="Email body preview"
              srcDoc={first.html_body}
              sandbox=""
              className="h-96 w-full rounded-xl border border-border bg-white"
            />
          </div>

          <div className="flex flex-col-reverse gap-3 sm:flex-row sm:justify-end">
            <Button variant="secondary" onClick={() => { setResult(null); setJobId(null) }}>
              Cancel
            </Button>
            <Button icon={<Mail className="h-4 w-4" />} loading={confirming} onClick={() => void handleConfirm()}>
              Confirm &amp; send
            </Button>
          </div>
        </div>
      )}
    </GlassCard>
  )
}
