import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Download,
  Megaphone,
  RefreshCw,
  Send,
  Users,
  XCircle,
} from 'lucide-react'
import { AppShell } from '@/components/AppShell'
import { GlassCard } from '@/components/GlassCard'
import { Button } from '@/components/Button'
import { FileDropzone } from '@/components/FileDropzone'
import { ProgressPanel, type JobState, type JobStatus } from '@/components/ProgressPanel'
import { LoadingNotice } from '@/components/LoadingNotice'
import { MappingTable, type MappingColumn, type MappingRow } from '@/components/MappingTable'
import { RichTextEditor } from '@/components/RichTextEditor'
import { Pagination } from '@/components/Pagination'
import { DatePicker } from '@/components/TemporalPicker'
import { usePagination } from '@/hooks/usePagination'
import { ApiError, apiUrl, del, get, post, postForm, put } from '@/lib/api'
import { formatIndianDate, formatIndianNumber, getIndianDateInputValue } from '@/lib/regional'

const BASE = '/tools/ultrafine-fse-reminder'

// ── shared types ─────────────────────────────────────────────────────────

interface IndividualPlanRow {
  fse_key: string
  fse_name: string
  total_target: number
  total_received: number
  total_shortfall: number
  party_count: number
  to: string[]
  cc: string[]
  missing_email: boolean
  subject: string
  body_html: string
}

interface BroadcastPlan {
  to: string[]
  cc: string[]
  subject: string
  body_html: string
  total_target: number
  total_received: number
  total_shortfall: number
  fse_count: number
  missing_email_count: number
}

interface PreviewResult {
  status: 'preview'
  individual: IndividualPlanRow[]
  broadcast: BroadcastPlan
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
  fse_key: string
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

interface BroadcastSendResult {
  status: 'sent'
  to: string[]
  cc: string[]
}

interface BroadcastSendJobResponse {
  status: JobStatus
  progress: number
  phase: string
  result: BroadcastSendResult | null
  error: string | null
}

async function pollBroadcastSendJob(jobId: string): Promise<JobState<BroadcastSendResult>> {
  const job = await get<BroadcastSendJobResponse>(`${BASE}/jobs/${jobId}`)
  return {
    status: job.status,
    progress: (job.progress ?? 0) * 100,
    phase: job.phase,
    result: job.result ?? undefined,
    error: job.error ?? undefined,
  }
}

function lakh(value: number): string {
  return `${value.toFixed(2)} L`
}

function joinAddrs(list: string[]): string {
  return list.join(', ')
}

function splitAddrs(value: string): string[] {
  return value
    .split(',')
    .map((v) => v.trim())
    .filter(Boolean)
}

// ── missing-email fix-up (mirrors the sibling ultrafine tools' missing-
//    recipient panels: fix inline, save to the mapping table, regenerate) ──

function MissingEmailPanel({
  rows,
  onRegenerate,
  regenerating,
}: {
  rows: IndividualPlanRow[]
  onRegenerate: () => void | Promise<void>
  regenerating: boolean
}) {
  const missing = rows.filter((r) => r.missing_email)
  const [forms, setForms] = useState<Record<string, string>>({})
  const [fixed, setFixed] = useState<Record<string, boolean>>({})
  const [fixing, setFixing] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const pagination = usePagination(missing, 10)

  if (missing.length === 0) return null

  async function handleFix(fseName: string) {
    const email = forms[fseName]?.trim()
    if (!email) return
    setFixing(fseName)
    setError(null)
    try {
      await post(`${BASE}/mappings`, { fse_name: fseName, email })
      setFixed((prev) => ({ ...prev, [fseName]: true }))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to save mapping fix.')
    } finally {
      setFixing(null)
    }
  }

  const allFixed = missing.every((r) => fixed[r.fse_name])

  return (
    <div className="flex flex-col gap-3 rounded-xl border border-amber-500/30 bg-amber-500/10 p-4">
      <div className="flex items-center gap-2 text-amber-600">
        <AlertTriangle className="h-4 w-4" />
        <h4 className="font-display text-sm font-semibold">
          Missing FSE emails ({formatIndianNumber(missing.length)})
        </h4>
      </div>
      <p className="text-sm text-ink-dim">
        These FSEs have no saved email, so they'll be skipped from both their own reminder and the
        broadcast. Add an address below to save it, then regenerate the preview.
      </p>
      {error && <p className="text-sm text-red-500">{error}</p>}
      <div className="flex flex-col gap-2">
        {pagination.pagedItems.map((row) => (
          <div
            key={row.fse_key}
            className="subpanel flex flex-col items-stretch gap-2 px-3 py-3 sm:flex-row sm:items-center sm:py-2"
          >
            <span className="min-w-0 flex-1 truncate text-sm font-medium text-ink sm:min-w-[10rem]">
              {row.fse_name}
            </span>
            <input
              type="email"
              placeholder="FSE email address"
              value={forms[row.fse_name] ?? ''}
              disabled={fixed[row.fse_name]}
              onChange={(e) => setForms((prev) => ({ ...prev, [row.fse_name]: e.target.value }))}
              aria-label={`Email for ${row.fse_name}`}
              className="field-control w-full sm:w-72"
            />
            {fixed[row.fse_name] ? (
              <span className="inline-flex items-center gap-1 text-sm text-emerald-500">
                <CheckCircle2 className="h-4 w-4" /> Saved
              </span>
            ) : (
              <Button
                variant="secondary"
                loading={fixing === row.fse_name}
                disabled={!forms[row.fse_name]?.trim()}
                onClick={() => void handleFix(row.fse_name)}
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
        itemLabel="missing FSE emails"
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

// ── per-FSE editable card ────────────────────────────────────────────────

interface EditableRow {
  fse_key: string
  to: string
  cc: string
  subject: string
  body_html: string
}

function IndividualCard({
  row,
  edited,
  onChange,
  onSend,
  sending,
  reportStatus,
}: {
  row: IndividualPlanRow
  edited: EditableRow
  onChange: (patch: Partial<EditableRow>) => void
  onSend: () => void
  sending: boolean
  reportStatus?: SendReportRow
}) {
  return (
    <div className="flex flex-col gap-3 rounded-xl border border-border p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h4 className="font-display text-base font-semibold text-ink">{row.fse_name}</h4>
          <p className="text-xs text-ink-dim">
            {formatIndianNumber(row.party_count)} part{row.party_count === 1 ? 'y' : 'ies'} · Target{' '}
            {lakh(row.total_target)} · Received {lakh(row.total_received)} · Short fall{' '}
            {lakh(row.total_shortfall)}
          </p>
        </div>
        {row.missing_email && (
          <span className="inline-flex items-center gap-1.5 text-xs font-medium text-amber-500">
            <AlertTriangle className="h-3.5 w-3.5" /> No saved email
          </span>
        )}
        {reportStatus?.status === 'sent' && (
          <span className="inline-flex items-center gap-1.5 text-xs font-medium text-emerald-500">
            <CheckCircle2 className="h-3.5 w-3.5" /> Sent
          </span>
        )}
        {reportStatus?.status === 'failed' && (
          <span className="inline-flex items-center gap-1.5 text-xs font-medium text-red-500" title={reportStatus.detail}>
            <XCircle className="h-3.5 w-3.5" /> Failed
          </span>
        )}
        {reportStatus?.status === 'skipped' && (
          <span className="inline-flex items-center gap-1.5 text-xs font-medium text-amber-500" title={reportStatus.detail}>
            <AlertTriangle className="h-3.5 w-3.5" /> Skipped
          </span>
        )}
      </div>

      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        <label className="flex flex-col gap-1 text-sm">
          <span className="font-medium text-ink-dim">To</span>
          <input
            className="field-control"
            value={edited.to}
            onChange={(e) => onChange({ to: e.target.value })}
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="font-medium text-ink-dim">Cc</span>
          <input
            className="field-control"
            value={edited.cc}
            onChange={(e) => onChange({ cc: e.target.value })}
          />
        </label>
      </div>

      <label className="flex flex-col gap-1 text-sm">
        <span className="font-medium text-ink-dim">Subject</span>
        <input className="field-control" value={edited.subject} onChange={(e) => onChange({ subject: e.target.value })} />
      </label>

      <label className="flex flex-col gap-1 text-sm">
        <span className="font-medium text-ink-dim">Body</span>
        <RichTextEditor value={edited.body_html} onChange={(html) => onChange({ body_html: html })} minHeight={200} />
      </label>

      <div className="flex justify-end">
        <Button
          variant="secondary"
          icon={<Send className="h-4 w-4" />}
          onClick={onSend}
          loading={sending}
          disabled={!edited.to.trim()}
        >
          Send this reminder
        </Button>
      </div>
    </div>
  )
}

// ── FSE -> Email mapping section ────────────────────────────────────────

const MAPPING_COLUMNS: MappingColumn[] = [
  { key: 'fse_name', label: 'FSE Name' },
  { key: 'email', label: 'Email' },
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
    await post(`${BASE}/mappings`, { fse_name: row.fse_name ?? '', email: row.email ?? '' })
    await load()
  }

  async function handleEdit(index: number, row: MappingRow) {
    const original = rows[index]
    await put(`${BASE}/mappings/${encodeURIComponent(original.fse_name ?? '')}`, {
      fse_name: row.fse_name ?? '',
      email: row.email ?? '',
    })
    await load()
  }

  async function handleDelete(index: number) {
    const original = rows[index]
    await del(`${BASE}/mappings/${encodeURIComponent(original.fse_name ?? '')}`)
    await load()
  }

  return (
    <div className="flex flex-col gap-3">
      {error && (
        <p className="rounded-xl border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-500">{error}</p>
      )}
      {loading ? (
        <LoadingNotice />
      ) : (
        <MappingTable
          title="FSE → Email mapping"
          addLabel="Add FSE"
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

export default function UltrafineFseReminder() {
  const [file, setFile] = useState<File | null>(null)
  const [asOnDate, setAsOnDate] = useState(getIndianDateInputValue)
  const [submitting, setSubmitting] = useState(false)
  const [previewJobId, setPreviewJobId] = useState<string | null>(null)
  const [previewResult, setPreviewResult] = useState<PreviewResult | null>(null)
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [notConfigured, setNotConfigured] = useState(false)

  const [editableRows, setEditableRows] = useState<Record<string, EditableRow>>({})
  const [editableBroadcast, setEditableBroadcast] = useState<EditableRow | null>(null)

  const [sendJobId, setSendJobId] = useState<string | null>(null)
  const [sendResult, setSendResult] = useState<SendResult | null>(null)
  const [sendError, setSendError] = useState<string | null>(null)
  const [sendingAll, setSendingAll] = useState(false)
  const [sendingKey, setSendingKey] = useState<string | null>(null)

  const [broadcastJobId, setBroadcastJobId] = useState<string | null>(null)
  const [broadcastResult, setBroadcastResult] = useState<BroadcastSendResult | null>(null)
  const [broadcastError, setBroadcastError] = useState<string | null>(null)
  const [broadcastSending, setBroadcastSending] = useState(false)

  const [sampleKey, setSampleKey] = useState<string | null>(null)
  const sampleRef = useRef<HTMLDivElement | null>(null)

  const pagination = usePagination(previewResult?.individual ?? [], 20)

  async function handlePreview() {
    if (!file) return
    setSubmitting(true)
    setPreviewError(null)
    setNotConfigured(false)
    setPreviewResult(null)
    setPreviewJobId(null)
    setSendJobId(null)
    setSendResult(null)
    setSendError(null)
    setBroadcastJobId(null)
    setBroadcastResult(null)
    setBroadcastError(null)
    try {
      const fd = new FormData()
      fd.append('file', file)
      fd.append('as_on_date', formatIndianDate(asOnDate))
      const res = await postForm<{ job_id: string }>(`${BASE}/preview`, fd)
      setPreviewJobId(res.job_id)
    } catch (err) {
      if (err instanceof ApiError && err.status === 400 && /Settings/i.test(err.message)) {
        setNotConfigured(true)
      } else {
        setPreviewError(err instanceof ApiError ? err.message : 'Failed to build the preview.')
      }
      setSubmitting(false)
    }
  }

  function seedEditableState(result: PreviewResult) {
    const rows: Record<string, EditableRow> = {}
    for (const row of result.individual) {
      rows[row.fse_key] = {
        fse_key: row.fse_key,
        to: joinAddrs(row.to),
        cc: joinAddrs(row.cc),
        subject: row.subject,
        body_html: row.body_html,
      }
    }
    setEditableRows(rows)
    setEditableBroadcast({
      fse_key: '__broadcast__',
      to: joinAddrs(result.broadcast.to),
      cc: joinAddrs(result.broadcast.cc),
      subject: result.broadcast.subject,
      body_html: result.broadcast.body_html,
    })
    const firstReady = result.individual.find((r) => !r.missing_email) ?? result.individual[0]
    setSampleKey(firstReady ? firstReady.fse_key : null)
  }

  function handleClearAll() {
    setFile(null)
    setAsOnDate(getIndianDateInputValue())
    setPreviewJobId(null)
    setPreviewResult(null)
    setPreviewError(null)
    setNotConfigured(false)
    setEditableRows({})
    setEditableBroadcast(null)
    setSampleKey(null)
    setSendJobId(null)
    setSendResult(null)
    setSendError(null)
    setBroadcastJobId(null)
    setBroadcastResult(null)
    setBroadcastError(null)
  }

  async function sendRows(rows: EditableRow[]) {
    setSendError(null)
    try {
      const res = await post<{ job_id: string }>(`${BASE}/send`, {
        rows: rows.map((r) => ({
          fse_key: r.fse_key,
          to: splitAddrs(r.to),
          cc: splitAddrs(r.cc),
          subject: r.subject,
          body_html: r.body_html,
        })),
      })
      setSendJobId(res.job_id)
      setSendResult(null)
    } catch (err) {
      setSendError(err instanceof ApiError ? err.message : 'Failed to start sending.')
      setSendingAll(false)
      setSendingKey(null)
    }
  }

  async function handleSendOne(fseKey: string) {
    const row = editableRows[fseKey]
    if (!row) return
    setSendingKey(fseKey)
    await sendRows([row])
  }

  async function handleSendAllReady() {
    const rows = (previewResult?.individual ?? [])
      .filter((r) => !r.missing_email)
      .map((r) => editableRows[r.fse_key])
      .filter((r): r is EditableRow => Boolean(r?.to.trim()))
    if (rows.length === 0) return
    setSendingAll(true)
    await sendRows(rows)
  }

  async function handleSendBroadcast() {
    if (!editableBroadcast) return
    setBroadcastSending(true)
    setBroadcastError(null)
    try {
      const res = await post<{ job_id: string }>(`${BASE}/send-broadcast`, {
        to: splitAddrs(editableBroadcast.to),
        cc: splitAddrs(editableBroadcast.cc),
        subject: editableBroadcast.subject,
        body_html: editableBroadcast.body_html,
      })
      setBroadcastJobId(res.job_id)
      setBroadcastResult(null)
    } catch (err) {
      setBroadcastError(err instanceof ApiError ? err.message : 'Failed to start sending.')
      setBroadcastSending(false)
    }
  }

  const reportByKey = Object.fromEntries((sendResult?.report ?? []).map((r) => [r.fse_key, r]))
  const readyCount = (previewResult?.individual ?? []).filter((r) => !r.missing_email).length

  return (
    <AppShell title="Ultrafine FSE Bulk Reminder">
      <div className="flex flex-col gap-6">
        {/* ── Build & send ──────────────────────────────────────────── */}
        <GlassCard padding="lg" className="flex flex-col gap-6">
          <div className="flex items-center gap-4">
            <span className="icon-tile grid h-12 w-12 place-items-center rounded-xl">
              <Megaphone className="h-5 w-5" />
            </span>
            <div>
              <p className="text-sm font-semibold text-accent">Credit control</p>
              <h2 className="mt-1.5 font-display text-xl font-semibold tracking-[-0.025em] text-ink">
                FSE collection reminder sender
              </h2>
              <p className="mt-1 text-sm leading-6 text-ink-dim">
                Upload the "Coll vs Target" tracker workbook to build one reminder email per FSE, plus
                one combined broadcast for management. Review, edit, and send from below.
              </p>
            </div>
          </div>

          <div className="flex flex-col gap-2">
            <div className="flex items-center justify-between gap-2">
              <span className="text-sm font-medium text-ink-dim">
                Tracker workbook <span className="text-red-500">*</span>
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
              label="Drag & drop the tracker workbook here, or click to browse"
              hint="Must contain a 'Coll vs Target' sheet with FSE, Party's Name, Target, Received, and Short Fall columns"
              files={file ? [file] : []}
              onFilesSelected={(f) => setFile(f[0] ?? null)}
              onRemove={() => setFile(null)}
            />
          </div>

          <label className="flex max-w-xs flex-col gap-1.5 text-sm">
            <span className="font-medium text-ink-dim">As-on date (shown in the subject &amp; body)</span>
            <DatePicker value={asOnDate} onValueChange={setAsOnDate} aria-label="As-on date" />
          </label>

          <div className="flex flex-wrap justify-end gap-3">
            <Button type="button" variant="ghost" onClick={handleClearAll}>
              Clear all
            </Button>
            <Button onClick={() => void handlePreview()} loading={submitting} disabled={!file}>
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
                if (r) seedEditableState(r)
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
            <div className="flex flex-col gap-6">
              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                {[
                  ['FSEs found', previewResult.individual.length, 'text-ink'],
                  ['Ready to send', readyCount, 'text-emerald-500'],
                  ['Missing emails', previewResult.broadcast.missing_email_count, 'text-amber-500'],
                  ['Grand total target', `${lakh(previewResult.broadcast.total_target)}`, 'text-accent'],
                ].map(([label, value, color]) => (
                  <div key={String(label)} className="rounded-xl border border-stroke/70 bg-surface/55 px-4 py-3">
                    <span className="text-xs font-medium text-ink-faint">{label}</span>
                    <p className={`mt-1 font-display text-2xl font-semibold ${color}`}>{value}</p>
                  </div>
                ))}
              </div>

              <MissingEmailPanel
                rows={previewResult.individual}
                onRegenerate={handlePreview}
                regenerating={submitting}
              />

              {/* ── Broadcast card ─────────────────────────────────────── */}
              {editableBroadcast && (
                <div className="flex flex-col gap-3 rounded-xl border border-accent/40 bg-accent/5 p-4">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div>
                      <h4 className="font-display text-base font-semibold text-ink">
                        Broadcast — all {previewResult.broadcast.fse_count} FSEs
                      </h4>
                      <p className="text-xs text-ink-dim">
                        Target {lakh(previewResult.broadcast.total_target)} · Received{' '}
                        {lakh(previewResult.broadcast.total_received)} · Short fall{' '}
                        {lakh(previewResult.broadcast.total_shortfall)}
                      </p>
                    </div>
                    {broadcastResult && (
                      <span className="inline-flex items-center gap-1.5 text-xs font-medium text-emerald-500">
                        <CheckCircle2 className="h-3.5 w-3.5" /> Sent
                      </span>
                    )}
                  </div>
                  <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                    <label className="flex flex-col gap-1 text-sm">
                      <span className="font-medium text-ink-dim">To (dynamic — every mapped FSE)</span>
                      <input
                        className="field-control"
                        value={editableBroadcast.to}
                        onChange={(e) => setEditableBroadcast((prev) => (prev ? { ...prev, to: e.target.value } : prev))}
                      />
                    </label>
                    <label className="flex flex-col gap-1 text-sm">
                      <span className="font-medium text-ink-dim">Cc</span>
                      <input
                        className="field-control"
                        value={editableBroadcast.cc}
                        onChange={(e) => setEditableBroadcast((prev) => (prev ? { ...prev, cc: e.target.value } : prev))}
                      />
                    </label>
                  </div>
                  <label className="flex flex-col gap-1 text-sm">
                    <span className="font-medium text-ink-dim">Subject</span>
                    <input
                      className="field-control"
                      value={editableBroadcast.subject}
                      onChange={(e) => setEditableBroadcast((prev) => (prev ? { ...prev, subject: e.target.value } : prev))}
                    />
                  </label>
                  <label className="flex flex-col gap-1 text-sm">
                    <span className="font-medium text-ink-dim">Body</span>
                    <RichTextEditor
                      value={editableBroadcast.body_html}
                      onChange={(html) => setEditableBroadcast((prev) => (prev ? { ...prev, body_html: html } : prev))}
                      minHeight={220}
                    />
                  </label>
                  {broadcastError && <p className="text-sm text-red-500">{broadcastError}</p>}
                  {broadcastJobId && !broadcastResult && (
                    <ProgressPanel
                      jobId={broadcastJobId}
                      poller={pollBroadcastSendJob}
                      cancelOnTabClose={false}
                      onDone={(r) => {
                        setBroadcastResult(r ?? null)
                        setBroadcastSending(false)
                      }}
                      onError={(e) => {
                        setBroadcastError(e)
                        setBroadcastSending(false)
                      }}
                    />
                  )}
                  <div className="flex justify-end">
                    <Button
                      icon={<Send className="h-4 w-4" />}
                      onClick={() => void handleSendBroadcast()}
                      loading={broadcastSending}
                      disabled={!editableBroadcast.to.trim() || Boolean(broadcastResult)}
                    >
                      Send broadcast
                    </Button>
                  </div>
                </div>
              )}

              {/* ── Individual FSE cards ───────────────────────────────── */}
              <div className="flex flex-col gap-3">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <h4 className="font-display text-base font-semibold text-ink">Individual reminders</h4>
                  <Button
                    variant="secondary"
                    icon={<Send className="h-4 w-4" />}
                    onClick={() => void handleSendAllReady()}
                    loading={sendingAll}
                    disabled={readyCount === 0}
                  >
                    Send all ready ({formatIndianNumber(readyCount)})
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
                      setSendingAll(false)
                      setSendingKey(null)
                    }}
                    onError={(e) => {
                      setSendError(e)
                      setSendingAll(false)
                      setSendingKey(null)
                    }}
                  />
                )}

                {sendResult && (
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
                )}

                <div ref={sampleRef} className="flex flex-col gap-2">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <p className="max-w-xl text-sm text-ink-dim">
                      Every FSE's reminder follows this exact format — only the name, table rows, and
                      totals differ. Check it here once, then use "Send all ready" above to send every
                      FSE's own reminder without reviewing each one individually.
                    </p>
                    {previewResult.individual.length > 1 && (
                      <label className="flex items-center gap-2 text-sm">
                        <span className="font-medium text-ink-dim">Preview FSE</span>
                        <select
                          className="field-control w-auto"
                          value={sampleKey ?? ''}
                          onChange={(e) => setSampleKey(e.target.value || null)}
                        >
                          {previewResult.individual.map((row) => (
                            <option key={row.fse_key} value={row.fse_key}>
                              {row.fse_name}
                              {row.missing_email ? ' (missing email)' : ''}
                            </option>
                          ))}
                        </select>
                      </label>
                    )}
                  </div>
                  {sampleKey &&
                    editableRows[sampleKey] &&
                    (() => {
                      const row = previewResult.individual.find((r) => r.fse_key === sampleKey)
                      if (!row) return null
                      return (
                        <IndividualCard
                          row={row}
                          edited={editableRows[sampleKey]}
                          onChange={(patch) =>
                            setEditableRows((prev) => ({ ...prev, [sampleKey]: { ...prev[sampleKey], ...patch } }))
                          }
                          onSend={() => void handleSendOne(sampleKey)}
                          sending={sendingKey === sampleKey}
                          reportStatus={reportByKey[sampleKey]}
                        />
                      )
                    })()}
                </div>

                <div className="overflow-x-auto rounded-xl border border-border">
                  <table className="w-full text-sm">
                    <thead className="bg-bg-soft text-xs font-medium text-ink-dim">
                      <tr>
                        <th className="px-3 py-2 text-left">FSE</th>
                        <th className="px-3 py-2 text-right">Target</th>
                        <th className="px-3 py-2 text-right">Received</th>
                        <th className="px-3 py-2 text-right">Short fall</th>
                        <th className="px-3 py-2 text-left">Status</th>
                        <th className="px-3 py-2" />
                      </tr>
                    </thead>
                    <tbody>
                      {pagination.pagedItems.map((row) => {
                        const status = reportByKey[row.fse_key]
                        return (
                          <tr key={row.fse_key} className="border-t border-border">
                            <td className="px-3 py-2 text-ink">{row.fse_name}</td>
                            <td className="px-3 py-2 text-right text-ink-dim">{lakh(row.total_target)}</td>
                            <td className="px-3 py-2 text-right text-ink-dim">{lakh(row.total_received)}</td>
                            <td className="px-3 py-2 text-right text-ink-dim">{lakh(row.total_shortfall)}</td>
                            <td className="px-3 py-2">
                              {row.missing_email ? (
                                <span className="inline-flex items-center gap-1 text-amber-500">
                                  <AlertTriangle className="h-3.5 w-3.5" /> No email
                                </span>
                              ) : status?.status === 'sent' ? (
                                <span className="inline-flex items-center gap-1 text-emerald-500">
                                  <CheckCircle2 className="h-3.5 w-3.5" /> Sent
                                </span>
                              ) : status?.status === 'failed' ? (
                                <span className="inline-flex items-center gap-1 text-red-500" title={status.detail}>
                                  <XCircle className="h-3.5 w-3.5" /> Failed
                                </span>
                              ) : status?.status === 'skipped' ? (
                                <span className="inline-flex items-center gap-1 text-amber-500" title={status.detail}>
                                  <AlertTriangle className="h-3.5 w-3.5" /> Skipped
                                </span>
                              ) : (
                                <span className="text-ink-dim">Ready</span>
                              )}
                            </td>
                            <td className="px-3 py-2 text-right">
                              <button
                                type="button"
                                className="text-xs font-medium text-accent hover:underline"
                                onClick={() => {
                                  setSampleKey(row.fse_key)
                                  sampleRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
                                }}
                              >
                                View &amp; edit
                              </button>
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
                <Pagination
                  page={pagination.page}
                  pageCount={pagination.pageCount}
                  pageSize={pagination.pageSize}
                  totalItems={pagination.totalItems}
                  itemLabel="FSEs"
                  onPageChange={pagination.setPage}
                  onPageSizeChange={pagination.setPageSize}
                />
              </div>
            </div>
          )}
        </GlassCard>

        {/* ── FSE -> Email mapping ─────────────────────────────────────── */}
        <GlassCard padding="lg" className="flex flex-col gap-5">
          <div className="flex items-center gap-4">
            <span className="icon-tile grid h-12 w-12 place-items-center rounded-xl">
              <Users className="h-5 w-5" />
            </span>
            <div>
              <h2 className="font-display text-lg font-semibold text-ink">FSE → Email mapping</h2>
              <p className="text-sm text-ink-dim">
                Saved here once, reused for every future upload — no need to re-enter an FSE's email
                every month.
              </p>
            </div>
          </div>
          <MappingSection />
        </GlassCard>
      </div>
    </AppShell>
  )
}
