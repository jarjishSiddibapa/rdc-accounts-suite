import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Download,
  Mail,
  Paperclip,
  RefreshCw,
  Send,
  Table2,
  XCircle,
} from 'lucide-react'
import { AppShell } from '@/components/AppShell'
import { GlassCard } from '@/components/GlassCard'
import { Button } from '@/components/Button'
import { FileDropzone } from '@/components/FileDropzone'
import { ProgressPanel, type JobState, type JobStatus } from '@/components/ProgressPanel'
import { MappingTable, type MappingColumn, type MappingRow } from '@/components/MappingTable'
import { CreatableCombobox } from '@/components/CreatableCombobox'
import { Pagination } from '@/components/Pagination'
import { DatePicker } from '@/components/TemporalPicker'
import { usePagination } from '@/hooks/usePagination'
import { ApiError, apiUrl, del, get, post, postForm, put } from '@/lib/api'
import { formatIndianDate, formatIndianNumber, getIndianDateInputValue } from '@/lib/regional'
import { cn } from '@/utils/cn'

const BASE = '/tools/ultrafine-payment-reminder'

// ── shared types ─────────────────────────────────────────────────────────

interface PlanRow {
  group_name: string
  total_balance: number
  total_overdue: number
  to_emails: string[]
  cc_emails: string[]
  pdf_attachment_path: string | null
  pdf_filename: string | null
  pdf_attached: boolean
  skip_reason: string | null
  subject: string
  body: string
}

interface PreviewResult {
  status: 'preview'
  from_email: string
  customers: PlanRow[]
  total: number
  sendable_count: number
  skipped_count: number
  customer_pdfs_found: number
}

interface PreviewJobResponse {
  status: JobStatus
  progress: number
  phase: string
  result: PreviewResult | null
  error: string | null
}

async function pollPreviewJob(jobId: string): Promise<JobState<PreviewResult>> {
  const job = await get<PreviewJobResponse>(`${BASE}/jobs/${jobId}`)
  return {
    status: job.status,
    progress: (job.progress ?? 0) * 100,
    phase: job.phase,
    result: job.result ?? undefined,
    error: job.error ?? undefined,
  }
}

interface SendReportRow {
  group_name: string
  total_balance: number
  total_overdue: number
  to_emails: string[]
  cc_emails: string[]
  pdf_attached: boolean
  status: 'sent' | 'failed' | 'skipped'
  detail: string
}

interface SendResult {
  status: 'sent'
  report: SendReportRow[]
  sent: number
  failed: number
  skipped: number
}

interface SendJobResponse {
  status: JobStatus
  progress: number
  phase: string
  result: SendResult | null
  error: string | null
}

async function pollSendJob(jobId: string): Promise<JobState<SendResult>> {
  const job = await get<SendJobResponse>(`${BASE}/jobs/${jobId}`)
  return {
    status: job.status,
    progress: (job.progress ?? 0) * 100,
    phase: job.phase,
    result: job.result ?? undefined,
    error: job.error ?? undefined,
  }
}

function currency(value: number): string {
  if (!value) return '-'
  return formatIndianNumber(value, { maximumFractionDigits: 2 })
}

// ── missing-recipient fix-up (mirrors the Unaccounted Transactions /
//    RDC Payables "missing mapping" panels: fix inline, then regenerate) ───

function MissingRecipientsPanel({
  rows,
  toSuggestions,
  ccSuggestions,
  onRegenerate,
  regenerating,
}: {
  rows: PlanRow[]
  toSuggestions: string[]
  ccSuggestions: string[]
  onRegenerate: () => void | Promise<void>
  regenerating: boolean
}) {
  const missing = rows.filter((r) => r.to_emails.length === 0 && r.cc_emails.length === 0)
  const [forms, setForms] = useState<Record<string, { to: string; cc: string }>>({})
  const [fixed, setFixed] = useState<Record<string, boolean>>({})
  const [fixing, setFixing] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const pagination = usePagination(missing, 10)

  if (missing.length === 0) return null

  async function handleFix(groupName: string) {
    const form = forms[groupName]
    if (!form?.to?.trim()) return
    setFixing(groupName)
    setError(null)
    try {
      await post(`${BASE}/mappings`, {
        customer_name: groupName,
        to_emails: form.to.trim(),
        cc_emails: form.cc?.trim() ?? '',
      })
      setFixed((prev) => ({ ...prev, [groupName]: true }))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to save mapping fix.')
    } finally {
      setFixing(null)
    }
  }

  const allFixed = missing.every((r) => fixed[r.group_name])

  return (
    <div className="flex flex-col gap-3 rounded-xl border border-amber-500/30 bg-amber-500/10 p-4">
      <div className="flex items-center gap-2 text-amber-600">
        <AlertTriangle className="h-4 w-4" />
        <h4 className="font-display text-sm font-semibold">
          Missing recipients ({formatIndianNumber(missing.length)} customer{missing.length === 1 ? '' : 's'})
        </h4>
      </div>
      <p className="text-sm text-ink-dim">
        These customers had no To/Cc in the upload and no saved mapping, so they'll be skipped. Add
        an email below to save it as their mapping, then regenerate the preview.
      </p>
      {error && <p className="text-sm text-red-500">{error}</p>}
      <div className="flex flex-col gap-2">
        {pagination.pagedItems.map((row) => (
          <div
            key={row.group_name}
            className="subpanel flex flex-col items-stretch gap-2 px-3 py-3 sm:flex-row sm:flex-wrap sm:items-center sm:py-2"
          >
            <span className="min-w-0 flex-1 truncate text-sm font-medium text-ink sm:min-w-[10rem]">
              {row.group_name}
            </span>
            <CreatableCombobox
              placeholder="To email(s)"
              value={forms[row.group_name]?.to ?? ''}
              options={toSuggestions}
              disabled={fixed[row.group_name]}
              onChange={(value) =>
                setForms((prev) => ({
                  ...prev,
                  [row.group_name]: { ...prev[row.group_name], to: value },
                }))
              }
              ariaLabel={`To email recipients for ${row.group_name}`}
              suggestionLabel="Existing To recipient sets"
              className="w-full sm:w-64"
            />
            <CreatableCombobox
              placeholder="Cc email(s) (optional)"
              value={forms[row.group_name]?.cc ?? ''}
              options={ccSuggestions}
              disabled={fixed[row.group_name]}
              onChange={(value) =>
                setForms((prev) => ({
                  ...prev,
                  [row.group_name]: { ...prev[row.group_name], cc: value },
                }))
              }
              ariaLabel={`Cc email recipients for ${row.group_name}`}
              suggestionLabel="Existing Cc recipient sets"
              className="w-full sm:w-64"
            />
            {fixed[row.group_name] ? (
              <span className="inline-flex items-center gap-1 text-sm text-emerald-500">
                <CheckCircle2 className="h-4 w-4" /> Saved
              </span>
            ) : (
              <Button
                variant="secondary"
                loading={fixing === row.group_name}
                disabled={!forms[row.group_name]?.to?.trim()}
                onClick={() => void handleFix(row.group_name)}
              >
                Fix
              </Button>
            )}
          </div>
        ))}
      </div>
      <Pagination
        page={pagination.page}
        pageCount={pagination.pageCount}
        pageSize={pagination.pageSize}
        totalItems={pagination.totalItems}
        itemLabel="missing recipients"
        onPageChange={pagination.setPage}
        onPageSizeChange={pagination.setPageSize}
      />
      <div className="flex justify-stretch sm:justify-end">
        <Button
          variant="secondary"
          icon={<RefreshCw className="h-4 w-4" />}
          disabled={!allFixed}
          loading={regenerating}
          onClick={() => void onRegenerate()}
        >
          Regenerate preview after fixes
        </Button>
      </div>
    </div>
  )
}

// ── preview results table ────────────────────────────────────────────────

function PreviewTable({ rows }: { rows: PlanRow[] }) {
  const pagination = usePagination(rows, 10)
  return (
    <div className="flex flex-col gap-3">
      <div className="table-shell">
        <table className="w-full min-w-[860px] border-collapse text-sm">
          <thead>
            <tr className="border-b border-border bg-bg-soft/45 text-left">
              <th className="px-4 py-3 font-medium text-ink-dim">Customer</th>
              <th className="px-4 py-3 text-right font-medium text-ink-dim">Total Balance</th>
              <th className="px-4 py-3 text-right font-medium text-ink-dim">Total Overdue</th>
              <th className="px-4 py-3 font-medium text-ink-dim">To</th>
              <th className="px-4 py-3 font-medium text-ink-dim">Cc</th>
              <th className="px-4 py-3 text-center font-medium text-ink-dim">PDF</th>
              <th className="px-4 py-3 font-medium text-ink-dim">Status</th>
            </tr>
          </thead>
          <tbody>
            {pagination.pagedItems.map((row) => (
              <tr
                key={row.group_name}
                className={cn(
                  'border-b border-border/80 transition-colors last:border-b-0 hover:bg-bg-soft/55',
                  row.skip_reason && 'opacity-60',
                )}
              >
                <td className="px-4 py-3 font-medium text-ink">{row.group_name}</td>
                <td className="px-4 py-3 text-right text-ink">{currency(row.total_balance)}</td>
                <td className="px-4 py-3 text-right text-ink">{currency(row.total_overdue)}</td>
                <td className="max-w-[14rem] truncate px-4 py-3 text-ink-dim" title={row.to_emails.join(', ')}>
                  {row.to_emails.join(', ') || 'Not available'}
                </td>
                <td className="max-w-[12rem] truncate px-4 py-3 text-ink-dim" title={row.cc_emails.join(', ')}>
                  {row.cc_emails.join(', ') || 'Not available'}
                </td>
                <td className="px-4 py-3 text-center">
                  {row.pdf_attached ? (
                    <Paperclip className="mx-auto h-4 w-4 text-accent" />
                  ) : (
                    <span className="text-ink-faint">Not available</span>
                  )}
                </td>
                <td className="px-4 py-3">
                  {row.skip_reason ? (
                    <span className="inline-flex items-center gap-1.5 text-xs font-medium text-amber-500">
                      <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
                      {row.skip_reason}
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1.5 text-xs font-medium text-emerald-500">
                      <CheckCircle2 className="h-3.5 w-3.5 shrink-0" /> Ready to send
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Pagination
        page={pagination.page}
        pageCount={pagination.pageCount}
        pageSize={pagination.pageSize}
        totalItems={pagination.totalItems}
        itemLabel="customers"
        onPageChange={pagination.setPage}
        onPageSizeChange={pagination.setPageSize}
      />
    </div>
  )
}

function SendReportTable({ rows }: { rows: SendReportRow[] }) {
  const pagination = usePagination(rows, 10)
  return (
    <div className="flex flex-col gap-3">
      <div className="table-shell">
        <table className="w-full min-w-[720px] border-collapse text-sm">
          <thead>
            <tr className="border-b border-border bg-bg-soft/45 text-left">
              <th className="px-4 py-3 font-medium text-ink-dim">Customer</th>
              <th className="px-4 py-3 text-right font-medium text-ink-dim">Total Balance</th>
              <th className="px-4 py-3 font-medium text-ink-dim">Status</th>
              <th className="px-4 py-3 font-medium text-ink-dim">Detail</th>
            </tr>
          </thead>
          <tbody>
            {pagination.pagedItems.map((row) => (
              <tr
                key={row.group_name}
                className="border-b border-border/80 transition-colors last:border-b-0 hover:bg-bg-soft/55"
              >
                <td className="px-4 py-3 font-medium text-ink">{row.group_name}</td>
                <td className="px-4 py-3 text-right text-ink">{currency(row.total_balance)}</td>
                <td className="px-4 py-3">
                  {row.status === 'sent' && (
                    <span className="inline-flex items-center gap-1.5 text-xs font-medium text-emerald-500">
                      <CheckCircle2 className="h-3.5 w-3.5" /> Sent
                    </span>
                  )}
                  {row.status === 'failed' && (
                    <span className="inline-flex items-center gap-1.5 text-xs font-medium text-red-500">
                      <XCircle className="h-3.5 w-3.5" /> Failed
                    </span>
                  )}
                  {row.status === 'skipped' && (
                    <span className="inline-flex items-center gap-1.5 text-xs font-medium text-amber-500">
                      <AlertTriangle className="h-3.5 w-3.5" /> Skipped
                    </span>
                  )}
                </td>
                <td className="px-4 py-3 text-ink-dim">{row.detail}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Pagination
        page={pagination.page}
        pageCount={pagination.pageCount}
        pageSize={pagination.pageSize}
        totalItems={pagination.totalItems}
        itemLabel="results"
        onPageChange={pagination.setPage}
        onPageSizeChange={pagination.setPageSize}
      />
    </div>
  )
}

// ── Customer -> Email mapping section ────────────────────────────────────

const MAPPING_COLUMNS: MappingColumn[] = [
  { key: 'customer_name', label: 'Customer Name' },
  { key: 'to_emails', label: 'To' },
  { key: 'cc_emails', label: 'Cc' },
]

function MappingSection() {
  const [rows, setRows] = useState<MappingRow[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await get<MappingRow[]>(`${BASE}/mappings`)
      setRows(data)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to load mapping table.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  async function handleAdd(row: MappingRow) {
    await post(`${BASE}/mappings`, {
      customer_name: row.customer_name ?? '',
      to_emails: row.to_emails ?? '',
      cc_emails: row.cc_emails ?? '',
    })
    await load()
  }

  async function handleEdit(index: number, row: MappingRow) {
    const original = rows[index]
    await put(`${BASE}/mappings/${encodeURIComponent(original.customer_name ?? '')}`, {
      customer_name: row.customer_name ?? '',
      to_emails: row.to_emails ?? '',
      cc_emails: row.cc_emails ?? '',
    })
    await load()
  }

  async function handleDelete(index: number) {
    const original = rows[index]
    await del(`${BASE}/mappings/${encodeURIComponent(original.customer_name ?? '')}`)
    await load()
  }

  return (
    <div className="flex flex-col gap-3">
      {error && (
        <p className="rounded-xl border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-500">
          {error}
        </p>
      )}
      {loading ? (
        <p className="py-10 text-center text-sm text-ink-faint">Loading...</p>
      ) : (
        <MappingTable
          title="Customer → Email mapping"
          addLabel="Add customer"
          columns={MAPPING_COLUMNS}
          rows={rows}
          onAdd={handleAdd}
          onEdit={handleEdit}
          onDelete={handleDelete}
        />
      )}
    </div>
  )
}

// ── page ──────────────────────────────────────────────────────────────────

export default function UltrafinePaymentReminder() {
  const [dataFile, setDataFile] = useState<File | null>(null)
  const [emailsFile, setEmailsFile] = useState<File | null>(null)
  const [pdfFiles, setPdfFiles] = useState<File[]>([])
  const [asOnDate, setAsOnDate] = useState(getIndianDateInputValue)

  const [submitting, setSubmitting] = useState(false)
  const [previewJobId, setPreviewJobId] = useState<string | null>(null)
  const [previewResult, setPreviewResult] = useState<PreviewResult | null>(null)
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [notConfigured, setNotConfigured] = useState(false)

  const [confirmSending, setConfirmSending] = useState(false)
  const [sendJobId, setSendJobId] = useState<string | null>(null)
  const [sendResult, setSendResult] = useState<SendResult | null>(null)
  const [sendError, setSendError] = useState<string | null>(null)
  const [recipientSuggestions, setRecipientSuggestions] = useState({ to: [] as string[], cc: [] as string[] })

  useEffect(() => {
    void get<MappingRow[]>(`${BASE}/mappings`)
      .then((rows) => {
        setRecipientSuggestions({
          to: [...new Set(rows.map((row) => row.to_emails).filter(Boolean))],
          cc: [...new Set(rows.map((row) => row.cc_emails).filter(Boolean))],
        })
      })
      .catch(() => {
        // Non-critical: recipient inputs still accept new free-text values.
      })
  }, [])

  async function handlePreview() {
    if (!dataFile) return
    setSubmitting(true)
    setPreviewError(null)
    setNotConfigured(false)
    setPreviewResult(null)
    setPreviewJobId(null)
    setSendJobId(null)
    setSendResult(null)
    setSendError(null)
    try {
      const fd = new FormData()
      fd.append('data_file', dataFile)
      if (emailsFile) fd.append('emails_file', emailsFile)
      pdfFiles.forEach((f) => fd.append('pdf_files', f))
      fd.append('as_on_date', formatIndianDate(asOnDate))
      const res = await postForm<{ job_id: string }>(`${BASE}/preview`, fd)
      setPreviewJobId(res.job_id)
      // submitting stays true until the background job itself settles (see
      // ProgressPanel's onDone/onError below) - the request returning just
      // means the job was queued, not that it's finished.
    } catch (err) {
      if (err instanceof ApiError && err.status === 400 && /Settings/i.test(err.message)) {
        setNotConfigured(true)
      } else {
        setPreviewError(err instanceof ApiError ? err.message : 'Failed to build the preview.')
      }
      setSubmitting(false)
    }
  }

  async function handleConfirmSend() {
    if (!previewJobId) return
    setConfirmSending(true)
    setSendError(null)
    try {
      const res = await post<{ job_id: string }>(`${BASE}/confirm-send`, { job_id: previewJobId })
      setSendJobId(res.job_id)
      // confirmSending stays true until the send job itself settles (see
      // ProgressPanel's onDone/onError below).
    } catch (err) {
      if (err instanceof ApiError && err.status === 400 && /Settings/i.test(err.message)) {
        setNotConfigured(true)
      } else {
        setSendError(err instanceof ApiError ? err.message : 'Failed to start sending.')
      }
      setConfirmSending(false)
    }
  }

  function handleClearAll() {
    setDataFile(null)
    setEmailsFile(null)
    setPdfFiles([])
    setAsOnDate(getIndianDateInputValue())
    setPreviewJobId(null)
    setPreviewResult(null)
    setPreviewError(null)
    setNotConfigured(false)
    setSendJobId(null)
    setSendResult(null)
    setSendError(null)
  }

  const sendable = previewResult?.customers.filter((c) => !c.skip_reason) ?? []

  return (
    <AppShell title="Ultrafine Bulk Payment Reminder Sender">
      <div className="flex flex-col gap-6">
        {/* ── Build & send ──────────────────────────────────────────── */}
        <GlassCard padding="lg" className="flex flex-col gap-6">
          <div className="flex items-center gap-4">
            <span className="icon-tile grid h-12 w-12 place-items-center rounded-xl">
              <Mail className="h-5 w-5" />
            </span>
            <div>
              <p className="text-sm font-semibold text-accent">
                Dunning &amp; collections
              </p>
              <h2 className="mt-1.5 font-display text-xl font-semibold tracking-[-0.025em] text-ink">
                Bulk payment reminder sender
              </h2>
              <p className="mt-1 text-sm leading-6 text-ink-dim">
                Upload the balance/aging workbook, optionally an emails workbook and per-customer
                PDF statements, then review exactly what will be sent before anything goes out.
              </p>
            </div>
          </div>

          <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
            <div className="flex flex-col gap-2">
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm font-medium text-ink-dim">
                  Balance / aging data file <span className="text-red-500">*</span>
                </span>
                <a
                  href={apiUrl(`${BASE}/template`)}
                  className="inline-flex items-center gap-1.5 text-xs font-medium text-accent hover:underline"
                >
                  <Download className="h-3.5 w-3.5" /> Download template
                </a>
              </div>
              <FileDropzone
                accept=".xls,.xlsx"
                label="Drag & drop the data workbook here, or click to browse"
                hint="Customer Name, Overdue, Net O/s, and the aging bucket columns"
                files={dataFile ? [dataFile] : []}
                onFilesSelected={(f) => setDataFile(f[0] ?? null)}
                onRemove={() => setDataFile(null)}
              />
            </div>
            <div className="flex flex-col gap-2">
              <span className="text-sm font-medium text-ink-dim">Emails file (optional)</span>
              <FileDropzone
                accept=".xls,.xlsx"
                label="Drag & drop the emails workbook here, or click to browse"
                hint="Customers missing here fall back to the saved mapping below"
                files={emailsFile ? [emailsFile] : []}
                onFilesSelected={(f) => setEmailsFile(f[0] ?? null)}
                onRemove={() => setEmailsFile(null)}
              />
            </div>
          </div>

          <div className="flex flex-col gap-2">
            <span className="text-sm font-medium text-ink-dim">PDF attachments (optional)</span>
            <FileDropzone
              multiple
              accept=".pdf"
              label="Drag & drop customer PDF statements here, or click to browse"
              hint="Matched to a customer by exact filename: “{Customer Name}.pdf”"
              files={pdfFiles}
              onFilesSelected={(f) => setPdfFiles((prev) => [...prev, ...f])}
              onRemove={(i) => setPdfFiles((prev) => prev.filter((_, idx) => idx !== i))}
            />
          </div>

          <label className="flex max-w-xs flex-col gap-1.5 text-sm">
            <span className="font-medium text-ink-dim">As-on date (shown in the email)</span>
            <DatePicker value={asOnDate} onValueChange={setAsOnDate} aria-label="As-on date" />
          </label>

          <div className="flex flex-wrap justify-end gap-3">
            <Button type="button" variant="ghost" onClick={handleClearAll}>
              Clear all
            </Button>
            <Button onClick={() => void handlePreview()} loading={submitting} disabled={!dataFile}>
              Build preview
            </Button>
          </div>

          {notConfigured && (
            <div className="flex items-start gap-3 rounded-xl border border-amber-500/30 bg-amber-500/10 px-4 py-3">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
              <div className="flex flex-col gap-2 text-sm">
                <span className="text-ink-dim">You haven't set up your email sender yet. Go to Settings.</span>
                <Link
                  to="/settings"
                  className="inline-flex w-fit items-center gap-1.5 text-sm font-medium text-accent transition hover:gap-2.5"
                >
                  Go to Settings <ArrowRight className="h-4 w-4" />
                </Link>
              </div>
            </div>
          )}

          {previewError && (
            <p className="rounded-xl border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-500">
              {previewError}
            </p>
          )}

          {previewJobId && !previewResult && (
            <ProgressPanel
              jobId={previewJobId}
              poller={pollPreviewJob}
              onDone={(r) => {
                setPreviewResult(r ?? null)
                setSubmitting(false)
              }}
              onError={(e) => {
                setPreviewError(e)
                setSubmitting(false)
              }}
              onCancel={() => post(`${BASE}/jobs/${previewJobId}/cancel`)}
            />
          )}

          {previewResult && (
            <div className="flex flex-col gap-5">
              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                {[
                  ['Total customers', previewResult.total, 'text-ink'],
                  ['Ready to send', previewResult.sendable_count, 'text-emerald-500'],
                  ['Skipped', previewResult.skipped_count, 'text-amber-500'],
                  ['PDFs matched', previewResult.customer_pdfs_found, 'text-accent'],
                ].map(([label, value, color]) => (
                  <div key={String(label)} className="rounded-xl border border-stroke/70 bg-surface/55 px-4 py-3">
                    <span className="text-xs font-medium text-ink-faint">{label}</span>
                    <p className={`mt-1 font-display text-2xl font-semibold ${color}`}>
                      {formatIndianNumber(Number(value))}
                    </p>
                  </div>
                ))}
              </div>

              <MissingRecipientsPanel
                rows={previewResult.customers}
                toSuggestions={recipientSuggestions.to}
                ccSuggestions={recipientSuggestions.cc}
                onRegenerate={handlePreview}
                regenerating={submitting}
              />

              <PreviewTable rows={previewResult.customers} />

              <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border p-4">
                <p className="text-sm text-ink-dim">
                  Sending from <span className="font-medium text-ink">{previewResult.from_email}</span>.{' '}
                  {formatIndianNumber(sendable.length)} email{sendable.length === 1 ? '' : 's'} will actually
                  go out; skipped rows are never sent.
                </p>
                <Button
                  icon={<Send className="h-4 w-4" />}
                  onClick={() => void handleConfirmSend()}
                  loading={confirmSending}
                  disabled={sendable.length === 0 || Boolean(sendResult)}
                >
                  Confirm &amp; send
                </Button>
              </div>

              {sendError && (
                <p className="rounded-xl border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-500">
                  {sendError}
                </p>
              )}

              {sendJobId && !sendResult && (
                <ProgressPanel
                  jobId={sendJobId}
                  poller={pollSendJob}
                  cancelOnTabClose={false}
                  onDone={(r) => {
                    setSendResult(r ?? null)
                    setConfirmSending(false)
                  }}
                  onError={(e) => {
                    setSendError(e)
                    setConfirmSending(false)
                  }}
                />
              )}

              {sendResult && (
                <div className="flex flex-col gap-4">
                  <div className="grid gap-3 sm:grid-cols-3">
                    {[
                      ['Sent', sendResult.sent, 'text-emerald-500'],
                      ['Failed', sendResult.failed, 'text-red-500'],
                      ['Skipped', sendResult.skipped, 'text-amber-500'],
                    ].map(([label, value, color]) => (
                      <div key={String(label)} className="rounded-xl border border-stroke/70 bg-surface/55 px-4 py-3">
                        <span className="text-xs font-medium text-ink-faint">{label}</span>
                        <p className={`mt-1 font-display text-2xl font-semibold ${color}`}>
                          {formatIndianNumber(Number(value))}
                        </p>
                      </div>
                    ))}
                  </div>
                  <SendReportTable rows={sendResult.report} />
                </div>
              )}
            </div>
          )}
        </GlassCard>

        {/* ── Customer -> Email mapping ───────────────────────────────── */}
        <GlassCard padding="lg" className="flex flex-col gap-5">
          <div className="flex items-center gap-4">
            <span className="icon-tile grid h-12 w-12 place-items-center rounded-xl">
              <Table2 className="h-5 w-5" />
            </span>
            <div>
              <h2 className="font-display text-lg font-semibold text-ink">Customer → Email mapping</h2>
              <p className="text-sm text-ink-dim">
                Recipients saved here are used whenever a customer's To/Cc is blank in the
                uploaded emails file (or no emails file is uploaded at all).
              </p>
            </div>
          </div>
          <MappingSection />
        </GlassCard>
      </div>
    </AppShell>
  )
}
