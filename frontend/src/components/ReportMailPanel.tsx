import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { AlertTriangle, ArrowRight, CheckCircle2, Download, Eye, Mail } from 'lucide-react'
import { GlassCard } from '@/components/GlassCard'
import { Button } from '@/components/Button'
import { ErrorBanner } from '@/components/ErrorBanner'
import { CreatableCombobox } from '@/components/CreatableCombobox'
import { Pagination } from '@/components/Pagination'
import { usePagination } from '@/hooks/usePagination'
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
  /** Only where the application lets the person choose whom the mail goes from. */
  sender_options?: SenderOption[]
  sender?: string
}

interface SenderOption {
  value: string
  label: string
  email: string | null
  configured: boolean
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

/** How one kind of unmapped value is fixed on the spot: which mapping table it goes into. */
export interface MappingFix {
  fieldLabel: string
  /** The table, under `${baseUrl}/mappings/`. */
  mappingKey: string
  /** The row to save for an unmapped `item` mapped to `value`. */
  buildBody: (item: string, value: string) => Record<string, unknown>
  /** The column of that table holding the values already in use, offered as suggestions. */
  suggestionKey: string
}

function MappingFixRows({
  baseUrl,
  category,
  items,
  fix,
  suggestions,
  fixed,
  onFixed,
}: {
  baseUrl: string
  category: string
  items: string[]
  fix: MappingFix
  suggestions: string[]
  fixed: Record<string, boolean>
  onFixed: (key: string) => void
}) {
  const [values, setValues] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const pagination = usePagination(items, 10)

  async function handleFix(item: string) {
    const value = values[item]?.trim()
    if (!value) return
    setSaving(item)
    setError(null)
    try {
      await post(`${baseUrl}/mappings/${fix.mappingKey}`, fix.buildBody(item, value))
      onFixed(`${category}::${item}`)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save the mapping.')
    } finally {
      setSaving(null)
    }
  }

  return (
    <div className="flex flex-col gap-2">
      <span className="text-xs font-bold tracking-[0.08em] text-amber-600 uppercase">
        {category} ({items.length})
      </span>
      {error && <ErrorBanner className="whitespace-pre-wrap">{error}</ErrorBanner>}
      {/* no scrolling box here: it would clip the drop-down list of the Location box */}
      <div className="flex flex-col gap-2">
        {pagination.pagedItems.map((item) => {
          const isFixed = Boolean(fixed[`${category}::${item}`])
          return (
            <div
              key={item}
              className="flex flex-col items-stretch gap-2 rounded-lg border border-amber-500/20 bg-bg-soft/40 px-3 py-2 sm:flex-row sm:flex-wrap sm:items-center"
            >
              <span className="min-w-0 flex-1 truncate text-sm font-medium text-ink sm:min-w-[10rem]">{item}</span>
              <CreatableCombobox
                placeholder={fix.fieldLabel}
                value={values[item] ?? ''}
                options={suggestions}
                disabled={isFixed}
                onChange={(value) => setValues((previous) => ({ ...previous, [item]: value }))}
                ariaLabel={`${fix.fieldLabel} for ${item}`}
                suggestionLabel={`Existing ${fix.fieldLabel.toLocaleLowerCase()} values`}
                className="w-full sm:w-64"
              />
              {isFixed ? (
                <span className="inline-flex items-center gap-1 text-sm text-emerald-500">
                  <CheckCircle2 className="h-4 w-4" /> Saved
                </span>
              ) : (
                <Button
                  variant="secondary"
                  loading={saving === item}
                  disabled={!values[item]?.trim()}
                  onClick={() => void handleFix(item)}
                >
                  Fix
                </Button>
              )}
            </div>
          )
        })}
      </div>
      <Pagination
        page={pagination.page}
        pageCount={pagination.pageCount}
        pageSize={pagination.pageSize}
        totalItems={pagination.totalItems}
        itemLabel={category.toLowerCase()}
        onPageChange={pagination.setPage}
        onPageSizeChange={pagination.setPageSize}
      />
    </div>
  )
}

/**
 * Mail a report by hand, the same way as the Unaccounted / MRN / PO mail:
 * build the report, show exactly how the email will look (with the report to
 * download from right there), and send only when "Confirm & send" is clicked.
 * It sends from the signed-in person's own email identity (Settings).
 */
export function ReportMailPanel({
  baseUrl,
  reportName,
  month,
  title = 'Mail this report',
  description,
  unmappedHint,
  mappingFixes,
  onClose,
  onChanged,
}: {
  baseUrl: string
  reportName: string
  /** For a report built per month (the cashflow): the month, sent with the preview request. */
  month?: string
  title?: string
  /** Replaces the sentence under the title. */
  description?: string
  /** Replaces the hint under the unmapped values. */
  unmappedHint?: string
  /** By warning category: lets each unmapped value be mapped right under the warning. */
  mappingFixes?: Record<string, MappingFix>
  /** Shows a Close button, for a panel that is opened on demand. */
  onClose?: () => void
  /** Called when a preview finished or a mail was sent (building the report can change stored data). */
  onChanged?: () => void
}) {
  const [defaults, setDefaults] = useState<Defaults | null>(null)
  const [sender, setSender] = useState('me')
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

  // values already used in the mapping tables, offered while mapping an unmapped one
  const [suggestions, setSuggestions] = useState<Record<string, string[]>>({})
  const [fixed, setFixed] = useState<Record<string, boolean>>({})
  const fixable = Boolean(mappingFixes)

  useEffect(() => {
    if (!mappingFixes) return
    void Promise.all(
      Object.values(mappingFixes).map(async (fix) => {
        const rows = await get<Record<string, string>[]>(`${baseUrl}/mappings/${fix.mappingKey}`)
        return [fix.mappingKey, [...new Set(rows.map((row) => row[fix.suggestionKey]).filter(Boolean))]] as [string, string[]]
      }),
    )
      .then((entries) => setSuggestions(Object.fromEntries(entries)))
      .catch(() => {
        // Non-critical: the inputs still take a new value typed in.
      })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [baseUrl, fixable, result])

  const load = useCallback(async () => {
    try {
      const data = await get<Defaults>(`${baseUrl}/mail/defaults`)
      setDefaults(data)
      setSender(data.sender ?? 'me')
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
    setFixed({})
    try {
      const res = await post<{ job_id: string }>(`${baseUrl}/mail/preview`, {
        to: splitEmails(toText),
        cc: splitEmails(ccText),
        subject,
        intro,
        allow_unmapped: allowUnmapped,
        ...(defaults?.sender_options ? { sender } : {}),
        ...(month ? { month } : {}),
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
      const res = await post<{ messages: SentMessage[] }>(
        `${baseUrl}/mail/confirm-send`,
        { job_id: jobId },
        10 * 60_000, // pushing a ~11 MB attachment through Gmail SMTP runs well past the default 30s
      )
      setSent(res.messages)
      setResult(null)
      setJobId(null)
      onChanged?.()
    } catch (err) {
      if (err instanceof ApiError && err.status === 408) {
        // The server keeps sending after the browser gives up, so the mail may still go out.
        setError(
          'This is taking longer than expected. The email may still be on its way: check the send history '
          + 'or the recipient\'s inbox before sending again.',
        )
      } else {
        setError(err instanceof ApiError ? err.message : 'Could not send the email.')
      }
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
            {title}
          </h2>
          <p className="mt-1 text-sm leading-6 text-ink-dim">
            {description ?? (
              <>
                Builds the {reportName} from the latest ERP data and shows you exactly how the email will
                look. Nothing is sent until you click Confirm &amp; send, and you can download the report
                from the preview.
              </>
            )}
          </p>
        </div>
        {onClose && (
          <Button variant="ghost" className="ml-auto" onClick={onClose}>
            Close
          </Button>
        )}
      </div>

      {!defaults.sender_configured && !defaults.sender_options && (
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

      {defaults.sender_options && (
        <label className="flex flex-col gap-1.5 text-sm sm:max-w-md">
          <span className="font-medium text-ink-dim">Send from</span>
          <select className="field-control" value={sender} onChange={(e) => edited(setSender)(e.target.value)}>
            {defaults.sender_options.map((option) => (
              <option key={option.value} value={option.value} disabled={!option.configured}>
                {option.label}{option.email ? ` (${option.email})` : ''}{option.configured ? '' : ' - not set up'}
              </option>
            ))}
          </select>
          <span className="text-xs text-ink-faint">
            The mail goes out through this Gmail account. Me and the people listed use the email they set up
            under Settings; the system email and the no-reply email are set up under Email administration.
          </span>
        </label>
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
        {defaults.sender_configured && !defaults.sender_options && (
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
          onDone={(res) => {
            setResult(res ?? null)
            onChanged?.()
          }}
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
          {(result.validation_warnings ?? []).map((warning) => {
            const fix = mappingFixes?.[warning.category]
            return fix ? (
              <MappingFixRows
                key={warning.category}
                baseUrl={baseUrl}
                category={warning.category}
                items={warning.items}
                fix={fix}
                suggestions={suggestions[fix.mappingKey] ?? []}
                fixed={fixed}
                onFixed={(key) => setFixed((previous) => ({ ...previous, [key]: true }))}
              />
            ) : (
              <p key={warning.category} className="text-sm text-ink-dim">
                <span className="font-medium text-ink">{warning.category} ({warning.items.length}):</span>{' '}
                {warning.items.slice(0, 8).join(', ')}{warning.items.length > 8 ? ` and ${warning.items.length - 8} more` : ''}
              </p>
            )
          })}
          <p className="text-sm text-ink-dim">
            {unmappedHint ?? 'Add them in the mapping tables on this page and preview again, or preview anyway - they show as "-".'}
          </p>
          <div className="flex flex-col-reverse gap-3 sm:flex-row sm:justify-end">
            <Button variant="secondary" loading={starting} onClick={() => void handlePreview(true)}>
              Preview with unmapped values
            </Button>
            {mappingFixes && (
              <Button
                loading={starting}
                disabled={
                  !(result.validation_warnings ?? []).every(
                    (warning) => warning.items.every((item) => fixed[`${warning.category}::${item}`]),
                  )
                }
                onClick={() => void handlePreview(false)}
              >
                Preview again
              </Button>
            )}
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
