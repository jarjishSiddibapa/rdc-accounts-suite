import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Download,
  Eye,
  FileCheck2,
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
import { Modal } from '@/components/Modal'
import { ProgressPanel, type JobState, type JobStatus } from '@/components/ProgressPanel'
import { MappingTable, type MappingRow } from '@/components/MappingTable'
import { Pagination } from '@/components/Pagination'
import { DatePicker } from '@/components/TemporalPicker'
import { usePagination } from '@/hooks/usePagination'
import { ApiError, apiUrl, get, post, postForm, put, del } from '@/lib/api'
import { formatIndianNumber, getIndianDateInputValue } from '@/lib/regional'
import { cn } from '@/utils/cn'

const BASE = '/tools/ultrafine-balance-confirmation'

// ── shapes (mirrors build_send_plan() in the backend processor) ──────────

type RecipientSource = 'upload' | 'saved_mapping' | 'none'

interface PreviewCustomer {
  customer_name: string
  balance: number
  to_emails: string[]
  cc_emails: string[]
  recipient_source: RecipientSource
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
  customers: PreviewCustomer[]
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

type SendRowStatus = 'sent' | 'failed' | 'skipped'

interface SendReportRow {
  customer_name: string
  balance: number
  to_emails: string[]
  cc_emails: string[]
  pdf_attached: boolean
  status: SendRowStatus
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

function recipientSourceLabel(source: RecipientSource): string {
  switch (source) {
    case 'upload':
      return 'From upload'
    case 'saved_mapping':
      return 'Saved mapping'
    case 'none':
      return 'None'
  }
}

function EmailList({ emails }: { emails: string[] }) {
  if (emails.length === 0) return <span className="text-ink-faint">—</span>
  return (
    <div className="flex flex-col gap-0.5">
      {emails.map((e) => (
        <span key={e} className="truncate">
          {e}
        </span>
      ))}
    </div>
  )
}

// ── per-customer detail modal (built subject/body preview) ───────────────

function CustomerDetailModal({
  customer,
  onClose,
}: {
  customer: PreviewCustomer | null
  onClose: () => void
}) {
  return (
    <Modal open={customer !== null} onClose={onClose} title={customer?.customer_name ?? 'Customer'}>
      {customer && (
        <div className="flex flex-col gap-4 text-sm">
          {customer.skip_reason && (
            <div className="flex items-start gap-2 rounded-xl border border-amber-500/30 bg-amber-500/10 px-3 py-2.5 text-amber-600">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              <span>{customer.skip_reason}</span>
            </div>
          )}
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div>
              <span className="text-xs font-medium text-ink-faint">Balance</span>
              <p className="text-ink">{formatIndianNumber(customer.balance)}</p>
            </div>
            <div>
              <span className="text-xs font-medium text-ink-faint">Recipient source</span>
              <p className="text-ink">{recipientSourceLabel(customer.recipient_source)}</p>
            </div>
            <div>
              <span className="text-xs font-medium text-ink-faint">To</span>
              <div className="text-ink"><EmailList emails={customer.to_emails} /></div>
            </div>
            <div>
              <span className="text-xs font-medium text-ink-faint">Cc</span>
              <div className="text-ink"><EmailList emails={customer.cc_emails} /></div>
            </div>
            <div className="sm:col-span-2">
              <span className="text-xs font-medium text-ink-faint">Attachment</span>
              <p className="text-ink">{customer.pdf_attached ? customer.pdf_filename : 'No PDF matched'}</p>
            </div>
          </div>
          <div className="flex flex-col gap-1.5">
            <span className="text-xs font-medium text-ink-faint">Subject</span>
            <p className="subpanel px-3 py-2 text-ink">{customer.subject}</p>
          </div>
          <div className="flex flex-col gap-1.5">
            <span className="text-xs font-medium text-ink-faint">Body</span>
            <div
              className="subpanel max-h-72 overflow-auto px-3 py-2 text-ink"
              // The body is HTML built server-side by this suite's own mail_builder
              // (never user-supplied), same trust boundary as the other mail-preview
              // tools in this suite (see UnaccountedTransactions.tsx's mail preview).
              dangerouslySetInnerHTML={{ __html: customer.body }}
            />
          </div>
        </div>
      )}
    </Modal>
  )
}

// ── missing-recipient fix-up (mirrors the Unaccounted Transactions /
//    RDC Payables "missing mapping" panels: fix inline, then regenerate) ───

function MissingRecipientsPanel({
  customers,
  onRegenerate,
  regenerating,
}: {
  customers: PreviewCustomer[]
  onRegenerate: () => void | Promise<void>
  regenerating: boolean
}) {
  const missing = customers.filter((c) => c.to_emails.length === 0 && c.cc_emails.length === 0)
  const [forms, setForms] = useState<Record<string, { to: string; cc: string }>>({})
  const [fixed, setFixed] = useState<Record<string, boolean>>({})
  const [fixing, setFixing] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const pagination = usePagination(missing, 10)

  if (missing.length === 0) return null

  async function handleFix(customerName: string) {
    const form = forms[customerName]
    if (!form?.to?.trim()) return
    setFixing(customerName)
    setError(null)
    try {
      await post(`${BASE}/mappings`, {
        customer_name: customerName,
        to_emails: form.to.trim(),
        cc_emails: form.cc?.trim() ?? '',
      })
      setFixed((prev) => ({ ...prev, [customerName]: true }))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to save mapping fix.')
    } finally {
      setFixing(null)
    }
  }

  const allFixed = missing.every((c) => fixed[c.customer_name])

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
        {pagination.pagedItems.map((customer) => (
          <div
            key={customer.customer_name}
            className="subpanel flex flex-col items-stretch gap-2 px-3 py-3 sm:flex-row sm:flex-wrap sm:items-center sm:py-2"
          >
            <span className="min-w-0 flex-1 truncate text-sm font-medium text-ink sm:min-w-[10rem]">
              {customer.customer_name}
            </span>
            <input
              placeholder="To email(s)"
              value={forms[customer.customer_name]?.to ?? ''}
              disabled={fixed[customer.customer_name]}
              onChange={(e) =>
                setForms((prev) => ({
                  ...prev,
                  [customer.customer_name]: { ...prev[customer.customer_name], to: e.target.value },
                }))
              }
              className="field-control w-full py-1.5 text-sm disabled:opacity-50 sm:min-h-9 sm:w-48"
            />
            <input
              placeholder="Cc email(s) (optional)"
              value={forms[customer.customer_name]?.cc ?? ''}
              disabled={fixed[customer.customer_name]}
              onChange={(e) =>
                setForms((prev) => ({
                  ...prev,
                  [customer.customer_name]: { ...prev[customer.customer_name], cc: e.target.value },
                }))
              }
              className="field-control w-full py-1.5 text-sm disabled:opacity-50 sm:min-h-9 sm:w-48"
            />
            {fixed[customer.customer_name] ? (
              <span className="inline-flex items-center gap-1 text-sm text-emerald-500">
                <CheckCircle2 className="h-4 w-4" /> Saved
              </span>
            ) : (
              <Button
                variant="secondary"
                loading={fixing === customer.customer_name}
                disabled={!forms[customer.customer_name]?.to?.trim()}
                onClick={() => void handleFix(customer.customer_name)}
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

// ── results table ─────────────────────────────────────────────────────────

function StatTile({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div className="rounded-xl border border-stroke/70 bg-surface/55 px-4 py-3">
      <span className="text-xs font-medium text-ink-faint">{label}</span>
      <p className={cn('mt-1 font-display text-2xl font-semibold', color)}>{formatIndianNumber(value)}</p>
    </div>
  )
}

function ResultsTable({ customers }: { customers: PreviewCustomer[] }) {
  const pagination = usePagination(customers, 10)
  const [viewing, setViewing] = useState<PreviewCustomer | null>(null)

  return (
    <div className="flex flex-col gap-3">
      <div className="table-shell">
        <table className="w-full min-w-[820px] border-collapse text-sm">
          <thead>
            <tr className="border-b border-border bg-bg-soft/45 text-left">
              <th className="px-4 py-3 font-medium text-ink-dim">Customer</th>
              <th className="px-4 py-3 font-medium text-ink-dim">Balance</th>
              <th className="px-4 py-3 font-medium text-ink-dim">To</th>
              <th className="px-4 py-3 font-medium text-ink-dim">Cc</th>
              <th className="px-4 py-3 font-medium text-ink-dim">PDF</th>
              <th className="px-4 py-3 font-medium text-ink-dim">Status</th>
              <th className="px-4 py-3 text-right font-medium text-ink-dim">View</th>
            </tr>
          </thead>
          <tbody>
            {pagination.pagedItems.map((customer) => (
              <tr
                key={customer.customer_name}
                className={cn(
                  'border-b border-border/80 transition-colors last:border-b-0 hover:bg-bg-soft/55',
                  customer.skip_reason && 'bg-amber-500/5',
                )}
              >
                <td className="px-4 py-3 text-ink">{customer.customer_name}</td>
                <td className="px-4 py-3 text-ink">{formatIndianNumber(customer.balance)}</td>
                <td className="px-4 py-3 text-ink-dim"><EmailList emails={customer.to_emails} /></td>
                <td className="px-4 py-3 text-ink-dim"><EmailList emails={customer.cc_emails} /></td>
                <td className="px-4 py-3">
                  {customer.pdf_attached ? (
                    <span className="inline-flex items-center gap-1.5 text-emerald-500">
                      <Paperclip className="h-3.5 w-3.5" /> Attached
                    </span>
                  ) : (
                    <span className="text-ink-faint">None</span>
                  )}
                </td>
                <td className="px-4 py-3">
                  {customer.skip_reason ? (
                    <span
                      className="inline-flex items-center gap-1.5 text-amber-600"
                      title={customer.skip_reason}
                    >
                      <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
                      <span className="max-w-[16rem] truncate">{customer.skip_reason}</span>
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1.5 text-emerald-500">
                      <CheckCircle2 className="h-3.5 w-3.5" /> Sendable
                    </span>
                  )}
                </td>
                <td className="px-4 py-3 text-right">
                  <button
                    type="button"
                    onClick={() => setViewing(customer)}
                    aria-label={`View built email for ${customer.customer_name}`}
                    className="grid h-9 w-9 place-items-center rounded-full text-ink-dim transition hover:bg-bg-soft hover:text-accent"
                  >
                    <Eye className="h-4 w-4" />
                  </button>
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
      <CustomerDetailModal customer={viewing} onClose={() => setViewing(null)} />
    </div>
  )
}

// ── send report (after confirm-send) ───────────────────────────────────

function sendStatusColor(status: SendRowStatus): string {
  switch (status) {
    case 'sent':
      return 'text-emerald-500'
    case 'failed':
      return 'text-red-500'
    case 'skipped':
      return 'text-amber-600'
  }
}

function SendReportTable({ report }: { report: SendReportRow[] }) {
  const pagination = usePagination(report, 10)
  return (
    <div className="flex flex-col gap-3">
      <div className="table-shell">
        <table className="w-full min-w-[640px] border-collapse text-sm">
          <thead>
            <tr className="border-b border-border bg-bg-soft/45 text-left">
              <th className="px-4 py-3 font-medium text-ink-dim">Customer</th>
              <th className="px-4 py-3 font-medium text-ink-dim">Balance</th>
              <th className="px-4 py-3 font-medium text-ink-dim">Status</th>
              <th className="px-4 py-3 font-medium text-ink-dim">Detail</th>
            </tr>
          </thead>
          <tbody>
            {pagination.pagedItems.map((row) => (
              <tr key={row.customer_name} className="border-b border-border/80 last:border-b-0">
                <td className="px-4 py-3 text-ink">{row.customer_name}</td>
                <td className="px-4 py-3 text-ink">{formatIndianNumber(row.balance)}</td>
                <td className={cn('px-4 py-3 font-medium capitalize', sendStatusColor(row.status))}>
                  <span className="inline-flex items-center gap-1.5">
                    {row.status === 'sent' && <CheckCircle2 className="h-3.5 w-3.5" />}
                    {row.status === 'failed' && <XCircle className="h-3.5 w-3.5" />}
                    {row.status === 'skipped' && <AlertTriangle className="h-3.5 w-3.5" />}
                    {row.status}
                  </span>
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
        itemLabel="report rows"
        onPageChange={pagination.setPage}
        onPageSizeChange={pagination.setPageSize}
      />
    </div>
  )
}

// ── Customer -> Email mapping section ────────────────────────────────────

const MAPPING_COLUMNS = [
  { key: 'customer_name', label: 'Customer Name' },
  { key: 'to_emails', label: 'To Emails' },
  { key: 'cc_emails', label: 'Cc Emails' },
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
      setError(err instanceof ApiError ? err.message : 'Failed to load the mapping table.')
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
          title="Customer -> Email mapping"
          addLabel="Add customer mapping"
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

export default function UltrafineBalanceConfirmation() {
  const [workbook, setWorkbook] = useState<File | null>(null)
  const [pdfFiles, setPdfFiles] = useState<File[]>([])
  const [asOnDate, setAsOnDate] = useState(getIndianDateInputValue)

  const [previewSubmitting, setPreviewSubmitting] = useState(false)
  const [previewJobId, setPreviewJobId] = useState<string | null>(null)
  const [previewResult, setPreviewResult] = useState<PreviewResult | null>(null)
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [notConfigured, setNotConfigured] = useState(false)

  const [sendSubmitting, setSendSubmitting] = useState(false)
  const [sendJobId, setSendJobId] = useState<string | null>(null)
  const [sendResult, setSendResult] = useState<SendResult | null>(null)
  const [sendError, setSendError] = useState<string | null>(null)

  const [mappingOpen, setMappingOpen] = useState(false)

  async function handlePreview() {
    if (!workbook) return
    setPreviewSubmitting(true)
    setPreviewError(null)
    setNotConfigured(false)
    setPreviewResult(null)
    setPreviewJobId(null)
    setSendJobId(null)
    setSendResult(null)
    setSendError(null)
    try {
      const fd = new FormData()
      fd.append('workbook', workbook)
      pdfFiles.forEach((f) => fd.append('pdf_files', f))
      fd.append('as_on_date', asOnDate)
      const res = await postForm<{ job_id: string }>(`${BASE}/preview`, fd)
      setPreviewJobId(res.job_id)
      // previewSubmitting stays true until the background job itself settles
      // (cleared in ProgressPanel's onDone/onError below) - the request
      // returning just means the job was queued.
    } catch (err) {
      if (err instanceof ApiError && err.status === 400 && /Settings/i.test(err.message)) {
        setNotConfigured(true)
      } else {
        setPreviewError(err instanceof ApiError ? err.message : 'Failed to start preview.')
      }
      setPreviewSubmitting(false)
    }
  }

  async function handleSend() {
    if (!previewJobId) return
    setSendSubmitting(true)
    setSendError(null)
    setNotConfigured(false)
    setSendResult(null)
    setSendJobId(null)
    try {
      const res = await post<{ job_id: string }>(`${BASE}/confirm-send`, { job_id: previewJobId })
      setSendJobId(res.job_id)
      // sendSubmitting stays true until the send job itself settles (see the
      // second ProgressPanel's onDone/onError below).
    } catch (err) {
      if (err instanceof ApiError && err.status === 400 && /Settings/i.test(err.message)) {
        setNotConfigured(true)
      } else {
        setSendError(err instanceof ApiError ? err.message : 'Failed to start sending.')
      }
      setSendSubmitting(false)
    }
  }

  const canSend = Boolean(previewResult) && previewResult!.sendable_count > 0 && !sendResult

  return (
    <AppShell title="Ultrafine Balance Confirmation Bulk Sender">
      <div className="flex flex-col gap-6">
        <GlassCard padding="lg" className="flex flex-col gap-6">
          <div className="flex items-center gap-4">
            <span className="icon-tile grid h-12 w-12 place-items-center rounded-xl">
              <Mail className="h-5 w-5" />
            </span>
            <div>
              <p className="text-xs font-bold tracking-[0.1em] text-accent uppercase">
                Balance confirmation
              </p>
              <h2 className="mt-1.5 font-display text-xl font-semibold tracking-[-0.025em] text-ink">
                Preview and send balance confirmation emails
              </h2>
              <p className="mt-1 text-sm leading-6 text-ink-dim">
                Upload the balance workbook (Customer Name, Net O/s, optionally To/CC Email IDs)
                and any per-customer PDF statements — each PDF is matched to a customer by
                filename. Recipients not given in the upload fall back to the saved mapping
                below. Every email is built and shown to you before anything is sent.
              </p>
            </div>
          </div>

          <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
            <div className="flex flex-col gap-2">
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm font-medium text-ink-dim">Balance workbook</span>
                <a
                  href={apiUrl(`${BASE}/template`)}
                  className="inline-flex items-center gap-1.5 text-xs font-medium text-accent hover:underline"
                >
                  <Download className="h-3.5 w-3.5" /> Download template
                </a>
              </div>
              <FileDropzone
                accept=".xls,.xlsx"
                label="Drag & drop the balance workbook here, or click to browse"
                hint="Customer Name and Net O/s are required; To/CC Email IDs are optional"
                files={workbook ? [workbook] : []}
                onFilesSelected={(f) => setWorkbook(f[0] ?? null)}
                onRemove={() => setWorkbook(null)}
              />
            </div>
            <div className="flex flex-col gap-2">
              <span className="text-sm font-medium text-ink-dim">Customer PDF attachments (optional)</span>
              <FileDropzone
                multiple
                accept=".pdf"
                label="Drag & drop customer PDF statements here, or click to browse"
                hint="Each PDF is matched to a customer by filename (e.g. Acme Ltd.pdf)"
                files={pdfFiles}
                onFilesSelected={(f) => setPdfFiles((prev) => [...prev, ...f])}
                onRemove={(i) => setPdfFiles((prev) => prev.filter((_, idx) => idx !== i))}
              />
            </div>
          </div>

          <label className="flex max-w-xs flex-col gap-1.5 text-sm">
            <span className="font-medium text-ink-dim">As-on date (optional)</span>
            <DatePicker value={asOnDate} onValueChange={setAsOnDate} aria-label="As-on date" />
          </label>

          <div className="flex justify-stretch sm:justify-end">
            <Button
              icon={<FileCheck2 className="h-4 w-4" />}
              onClick={() => void handlePreview()}
              loading={previewSubmitting && !previewResult}
              disabled={!workbook}
            >
              Preview
            </Button>
          </div>

          {previewError && (
            <p className="rounded-xl border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-500">
              {previewError}
            </p>
          )}

          {notConfigured && (
            <div className="flex items-start gap-3 rounded-xl border border-amber-500/30 bg-amber-500/10 px-4 py-3">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
              <div className="flex flex-col gap-2 text-sm">
                <span className="text-ink-dim">You haven't set up your email sender yet — go to Settings.</span>
                <Link
                  to="/settings"
                  className="inline-flex w-fit items-center gap-1.5 text-sm font-medium text-accent transition hover:gap-2.5"
                >
                  Go to Settings <ArrowRight className="h-4 w-4" />
                </Link>
              </div>
            </div>
          )}

          {previewJobId && !previewResult && (
            <ProgressPanel
              jobId={previewJobId}
              poller={pollPreviewJob}
              onDone={(res) => {
                setPreviewResult(res ?? null)
                setPreviewSubmitting(false)
              }}
              onError={(err) => {
                setPreviewError(err)
                setPreviewSubmitting(false)
              }}
              onCancel={() => post(`${BASE}/jobs/${previewJobId}/cancel`)}
            />
          )}

          {previewResult && (
            <div className="flex flex-col gap-4">
              <div className="flex items-center gap-2 text-sm font-semibold text-ink">
                <span className="text-ink-faint">From</span>
                <span>{previewResult.from_email}</span>
              </div>
              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                <StatTile label="Total customers" value={previewResult.total} color="text-ink" />
                <StatTile label="Sendable" value={previewResult.sendable_count} color="text-emerald-500" />
                <StatTile label="Skipped" value={previewResult.skipped_count} color="text-amber-500" />
                <StatTile label="PDFs matched" value={previewResult.customer_pdfs_found} color="text-accent" />
              </div>

              <MissingRecipientsPanel
                customers={previewResult.customers}
                onRegenerate={handlePreview}
                regenerating={previewSubmitting}
              />

              <ResultsTable customers={previewResult.customers} />

              <div className="flex justify-stretch sm:justify-end">
                <Button
                  icon={<Send className="h-4 w-4" />}
                  onClick={() => void handleSend()}
                  loading={sendSubmitting && !sendResult}
                  disabled={!canSend}
                >
                  {sendResult ? 'Sent' : `Send ${formatIndianNumber(previewResult.sendable_count)} email${previewResult.sendable_count === 1 ? '' : 's'}`}
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
                  onDone={(res) => {
                    setSendResult(res ?? null)
                    setSendSubmitting(false)
                  }}
                  onError={(err) => {
                    setSendError(err)
                    setSendSubmitting(false)
                  }}
                />
              )}

              {sendResult && (
                <div className="subpanel flex flex-col gap-4 p-4">
                  <div className="grid gap-3 sm:grid-cols-3">
                    <StatTile label="Sent" value={sendResult.sent} color="text-emerald-500" />
                    <StatTile label="Failed" value={sendResult.failed} color="text-red-500" />
                    <StatTile label="Skipped" value={sendResult.skipped} color="text-amber-500" />
                  </div>
                  <SendReportTable report={sendResult.report} />
                </div>
              )}
            </div>
          )}
        </GlassCard>

        {/* ── Customer -> Email mapping ──────────────────────────────── */}
        <GlassCard padding="lg" className="flex flex-col gap-5">
          <button
            type="button"
            onClick={() => setMappingOpen((prev) => !prev)}
            className="flex w-full items-center justify-between gap-3 text-left"
          >
            <div className="flex items-center gap-3">
              <span className="icon-tile grid h-10 w-10 place-items-center rounded-xl">
                <Table2 className="h-4 w-4" />
              </span>
              <div>
                <h2 className="font-display text-lg font-semibold text-ink">Customer -&gt; Email mapping</h2>
                <p className="text-sm text-ink-dim">
                  Saved recipients used whenever the upload doesn't include To/CC for a customer.
                </p>
              </div>
            </div>
            {mappingOpen ? (
              <ChevronUp className="h-5 w-5 shrink-0 text-ink-faint" />
            ) : (
              <ChevronDown className="h-5 w-5 shrink-0 text-ink-faint" />
            )}
          </button>
          {mappingOpen && <MappingSection />}
        </GlassCard>
      </div>
    </AppShell>
  )
}
