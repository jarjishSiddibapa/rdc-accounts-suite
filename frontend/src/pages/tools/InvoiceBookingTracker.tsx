import { Fragment, useCallback, useEffect, useState, type ReactNode } from 'react'
import {
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  ClipboardCheck,
  KeyRound,
  Mail,
  Pencil,
  Plus,
  RefreshCw,
  Save,
  Search,
  Send,
  ShieldCheck,
  Trash2,
  Upload,
  XCircle,
} from 'lucide-react'
import { AppShell } from '@/components/AppShell'
import { Button } from '@/components/Button'
import { GlassCard } from '@/components/GlassCard'
import { LoadingNotice } from '@/components/LoadingNotice'
import { Modal } from '@/components/Modal'
import { Pagination } from '@/components/Pagination'
import { PasswordInput } from '@/components/PasswordInput'
import { ProgressPanel, type JobState } from '@/components/ProgressPanel'
import { ApiError, del, get, post, postForm, put } from '@/lib/api'
import { useAuth } from '@/lib/auth-context'
import { PUBLIC_ISSUE_MESSAGE } from '@/lib/error-visibility'
import { formatIndianDateTime, formatIndianNumber } from '@/lib/regional'

const BASE = '/tools/invoice-booking-tracker'
const ACCOUNT_IN_USE_MESSAGE = 'The DMS account is already logged in. Please update the tracker after the current DMS user signs out.'

interface Status {
  enabled: boolean
  portal_configured: boolean
  mail_configured: boolean
  scheduled_email_enabled: boolean
  scheduled_email_time: string
  last_total_pending: number | null
  last_checked_at: string | null
  last_check_status: string | null
  last_error: string | null
  last_scheduled_sent_date: string | null
}

interface Settings extends Status {
  version: number
  login_url: string
  username: string
  password_configured: boolean
  session_configured: boolean
  login_timeout_seconds: number
  sender_email: string
  sender_app_password_configured: boolean
  mail_to: string[]
  mail_cc: string[]
  subject_template: string
  body_template: string
  signature: string
}

interface TrackerResultRow {
  id?: number
  location: string
  responsible_person: string
  queue_label?: string
  pending_for_approval?: number
  submitted_to_accounts?: number
  locked?: number
  pending: number
  records_scanned: number
  pages_scanned: number
}

interface CheckResult {
  check_id?: number | null
  total_pending?: number
  total_records_scanned?: number
  total_pages_scanned?: number
  rows?: TrackerResultRow[]
  attempts?: number
  skipped?: boolean
  message?: string
}

interface CheckRow extends CheckResult {
  id: number
  trigger: string
  status: string
  error_message: string | null
  checked_at: string
  duration_seconds: number | null
}

interface LatestTracker extends CheckResult {
  available: boolean
  check_id: number | null
  trigger: string | null
  checked_at: string | null
}

interface NotificationRow {
  id: number
  subject: string
  attachment_filename: string
  status: string
  error_message: string | null
  created_at: string
  sent_at: string | null
}

interface MappingRow {
  id: number
  location: string
  responsible_person: string
  queue_label: string
  queue_key: string
  sort_order: number
  is_active: boolean
  is_deleted: boolean
}

interface Page<T> { total: number; items: T[] }

const emptyMapping: Omit<MappingRow, 'id' | 'is_deleted'> = {
  location: '', responsible_person: '', queue_label: '', queue_key: '', sort_order: 0, is_active: true,
}

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return <label className="flex min-w-0 flex-col gap-1.5 text-sm"><span className="font-medium text-ink-dim">{label}</span>{children}{hint && <span className="text-xs leading-5 text-ink-faint">{hint}</span>}</label>
}

function recipientText(values: string[]): string { return values.join('\n') }
function recipients(value: string): string[] { return Array.from(new Set(value.split(/[\n,;]+/).map((item) => item.trim()).filter(Boolean))) }

type FlashMessage = { ok: boolean; text: string } | null

function Flash({ message }: { message: FlashMessage }) {
  if (!message) return null
  return <p role="alert" className={`rounded-xl border px-4 py-3 text-sm ${message.ok ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-600' : 'border-red-500/30 bg-red-500/10 text-red-500'}`}>{message.text}</p>
}

function Badge({ status }: { status: string }) {
  const good = status === 'success' || status === 'sent'
  const active = status === 'pending' || status === 'sending'
  return <span className={`inline-flex items-center gap-1.5 rounded-lg px-2 py-1 text-xs font-semibold capitalize ${good ? 'bg-emerald-500/10 text-emerald-600' : active ? 'bg-amber-500/10 text-amber-600' : 'bg-red-500/10 text-red-500'}`}>{good ? <CheckCircle2 className="h-3.5 w-3.5" /> : active ? <RefreshCw className="h-3.5 w-3.5" /> : <XCircle className="h-3.5 w-3.5" />}{status}</span>
}

export default function InvoiceBookingTracker() {
  const { user } = useAuth()
  const isAdmin = user?.role === 'admin'
  const [status, setStatus] = useState<Status | null>(null)
  const [latest, setLatest] = useState<LatestTracker | null>(null)
  const [settings, setSettings] = useState<Settings | null>(null)
  const [password, setPassword] = useState('')
  const [senderPassword, setSenderPassword] = useState('')
  const [mailTo, setMailTo] = useState('')
  const [mailCc, setMailCc] = useState('')
  const [checks, setChecks] = useState<CheckRow[]>([])
  const [checkTotal, setCheckTotal] = useState(0)
  const [checkPage, setCheckPage] = useState(1)
  const [notifications, setNotifications] = useState<NotificationRow[]>([])
  const [notificationTotal, setNotificationTotal] = useState(0)
  const [notificationPage, setNotificationPage] = useState(1)
  const [mappings, setMappings] = useState<MappingRow[]>([])
  const [mappingTotal, setMappingTotal] = useState(0)
  const [mappingPage, setMappingPage] = useState(1)
  const [mappingSearch, setMappingSearch] = useState('')
  const [showArchived, setShowArchived] = useState(false)
  const [mappingDraft, setMappingDraft] = useState(emptyMapping)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [mappingModalOpen, setMappingModalOpen] = useState(false)
  const [mappingModalMessage, setMappingModalMessage] = useState<FlashMessage>(null)
  const [archiveConfirm, setArchiveConfirm] = useState<MappingRow | null>(null)
  const [expandedCheck, setExpandedCheck] = useState<number | null>(null)
  const [jobId, setJobId] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState<string | null>(null)
  const [message, setMessage] = useState<FlashMessage>(null)
  const [credentialsMessage, setCredentialsMessage] = useState<FlashMessage>(null)
  const [mailMessage, setMailMessage] = useState<FlashMessage>(null)
  const [mappingMessage, setMappingMessage] = useState<FlashMessage>(null)

  const loadChecks = useCallback(async () => {
    const page = await get<Page<CheckRow>>(`${BASE}/checks?limit=10&offset=${(checkPage - 1) * 10}`)
    setChecks(page.items); setCheckTotal(page.total)
  }, [checkPage])

  const loadNotifications = useCallback(async () => {
    const page = await get<Page<NotificationRow>>(`${BASE}/notifications?limit=10&offset=${(notificationPage - 1) * 10}`)
    setNotifications(page.items); setNotificationTotal(page.total)
  }, [notificationPage])

  const loadMappings = useCallback(async () => {
    const params = new URLSearchParams({ limit: '15', offset: String((mappingPage - 1) * 15), archived: String(showArchived) })
    if (mappingSearch.trim()) params.set('search', mappingSearch.trim())
    const page = await get<Page<MappingRow>>(`${BASE}/mappings?${params}`)
    setMappings(page.items); setMappingTotal(page.total)
  }, [mappingPage, mappingSearch, showArchived])

  const loadSummary = useCallback(async (initialize = false) => {
    const [nextStatus, nextLatest, nextSettings] = await Promise.all([
      get<Status>(`${BASE}/status`),
      get<LatestTracker>(`${BASE}/latest`),
      isAdmin ? get<Settings>(`${BASE}/settings`) : Promise.resolve(null),
    ])
    setStatus(nextStatus)
    setLatest(nextLatest)
    if (nextSettings) {
      setSettings(nextSettings)
      if (initialize) { setMailTo(recipientText(nextSettings.mail_to)); setMailCc(recipientText(nextSettings.mail_cc)) }
    }
  }, [isAdmin])

  useEffect(() => {
    void (async () => {
      setLoading(true)
      try { await Promise.all([loadSummary(true), loadChecks(), loadNotifications(), loadMappings()]) }
      catch (error) { setMessage({ ok: false, text: isAdmin && error instanceof ApiError ? error.message : PUBLIC_ISSUE_MESSAGE }) }
      finally { setLoading(false) }
    })()
  }, [isAdmin, loadSummary]) // secondary lists refresh independently below

  useEffect(() => { if (!loading) void loadChecks() }, [checkPage])
  useEffect(() => { if (!loading) void loadNotifications() }, [notificationPage])
  useEffect(() => { if (!loading) void loadMappings() }, [mappingPage, mappingSearch, showArchived])

  async function saveSettings() {
    if (!settings) return
    setBusy('settings'); setMailMessage(null)
    try {
      const next = await put<Settings>(`${BASE}/settings`, { ...settings, password: password || null, sender_app_password: senderPassword || null, mail_to: recipients(mailTo), mail_cc: recipients(mailCc) })
      setSettings(next); setStatus(next); setPassword(''); setSenderPassword(''); setMailTo(recipientText(next.mail_to)); setMailCc(recipientText(next.mail_cc)); setMailMessage({ ok: true, text: 'Tracker automation settings saved.' })
    } catch (error) { setMailMessage({ ok: false, text: isAdmin && error instanceof ApiError ? error.message : PUBLIC_ISSUE_MESSAGE }) }
    finally { setBusy(null) }
  }

  async function uploadSession(file: File) {
    setBusy('session'); setCredentialsMessage(null)
    try { const form = new FormData(); form.append('file', file); const next = await postForm<Settings>(`${BASE}/session`, form); setSettings(next); setStatus(next); setCredentialsMessage({ ok: true, text: 'Secure DMS browser session imported.' }) }
    catch (error) { setCredentialsMessage({ ok: false, text: isAdmin && error instanceof ApiError ? error.message : PUBLIC_ISSUE_MESSAGE }) }
    finally { setBusy(null) }
  }

  async function testMail() {
    if (!settings) return
    setBusy('test'); setMailMessage(null)
    try { await post(`${BASE}/test-mail`, { subject_template: settings.subject_template, body_template: settings.body_template }); setMailMessage({ ok: true, text: `Test tracker mail sent to ${user?.email}.` }) }
    catch (error) { setMailMessage({ ok: false, text: isAdmin && error instanceof ApiError ? error.message : PUBLIC_ISSUE_MESSAGE }) }
    finally { setBusy(null) }
  }

  async function checkNow() {
    setBusy('check'); setMessage(null)
    try { const result = await post<{ job_id: string }>(`${BASE}/check-now`); setJobId(result.job_id) }
    catch (error) { setBusy(null); setMessage({ ok: false, text: isAdmin && error instanceof ApiError ? error.message : PUBLIC_ISSUE_MESSAGE }) }
  }

  function openAddMapping() {
    setMappingDraft(emptyMapping); setEditingId(null); setMappingModalMessage(null); setMappingModalOpen(true)
  }

  function openEditMapping(row: MappingRow) {
    setMappingDraft({ location: row.location, responsible_person: row.responsible_person, queue_label: row.queue_label, queue_key: row.queue_key, sort_order: row.sort_order, is_active: row.is_active })
    setEditingId(row.id); setMappingModalMessage(null); setMappingModalOpen(true)
  }

  function closeMappingModal() {
    setMappingModalOpen(false); setEditingId(null); setMappingDraft(emptyMapping); setMappingModalMessage(null)
  }

  async function saveMapping() {
    setBusy('mapping'); setMappingModalMessage(null)
    const wasEditing = editingId !== null
    try {
      if (editingId) await put(`${BASE}/mappings/${editingId}`, mappingDraft)
      else await post(`${BASE}/mappings`, mappingDraft)
      await loadMappings()
      setMappingModalOpen(false); setEditingId(null); setMappingDraft(emptyMapping)
      setMappingMessage({ ok: true, text: wasEditing ? 'Mapping updated.' : 'Mapping added.' })
    } catch (error) { setMappingModalMessage({ ok: false, text: isAdmin && error instanceof ApiError ? error.message : PUBLIC_ISSUE_MESSAGE }) }
    finally { setBusy(null) }
  }

  async function restoreMapping(row: MappingRow) {
    setBusy(`mapping-${row.id}`); setMappingMessage(null)
    try { await post(`${BASE}/mappings/${row.id}/restore`); await loadMappings(); setMappingMessage({ ok: true, text: `"${row.location}" restored.` }) }
    catch (error) { setMappingMessage({ ok: false, text: isAdmin && error instanceof ApiError ? error.message : PUBLIC_ISSUE_MESSAGE }) }
    finally { setBusy(null) }
  }

  async function confirmArchiveMapping() {
    if (!archiveConfirm) return
    const row = archiveConfirm
    setBusy(`mapping-${row.id}`); setMappingMessage(null)
    try { await del(`${BASE}/mappings/${row.id}`); await loadMappings(); setArchiveConfirm(null); setMappingMessage({ ok: true, text: `"${row.location}" archived.` }) }
    catch (error) { setMappingMessage({ ok: false, text: isAdmin && error instanceof ApiError ? error.message : PUBLIC_ISSUE_MESSAGE }) }
    finally { setBusy(null) }
  }

  if (loading || !status) return <AppShell title="Ultrafine Invoice Booking Tracker"><LoadingNotice className="glass rounded-2xl" /></AppShell>

  return (
    <AppShell title="Ultrafine Invoice Booking Tracker">
      <div className="flex flex-col gap-6">
        <GlassCard padding="lg" className="overflow-hidden">
          <div className="flex flex-col justify-between gap-5 lg:flex-row lg:items-center">
            <div className="flex items-start gap-4"><span className="icon-tile grid h-12 w-12 shrink-0 place-items-center rounded-xl"><ClipboardCheck className="h-5 w-5" /></span><div><p className="text-sm font-semibold text-accent">Ultrafine workflow assurance</p><h1 className="mt-1 font-display text-2xl font-semibold tracking-tight text-ink">Every DMS page checked, every day</h1><p className="mt-2 max-w-2xl text-sm leading-6 text-ink-dim">Counts only “Pending for approval” and “Submitted to accounts” invoices, regardless of letter case, across every page of every configured work queue. Invoices locked open by another DMS user are excluded from that count until released.</p></div></div>
            <Button icon={<RefreshCw className="h-4 w-4" />} loading={busy === 'check'} disabled={Boolean(busy || jobId)} onClick={() => void checkNow()}>Check tracker now</Button>
          </div>
          <div className="mt-6 grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
            {[['Pending invoices', status.last_total_pending == null ? '—' : formatIndianNumber(status.last_total_pending)], ['Last check', status.last_checked_at ? formatIndianDateTime(status.last_checked_at) : 'Not checked yet'], ['Check status', status.last_check_status ?? 'Waiting'], ['Scheduled mail', !status.enabled ? 'Automation off' : status.scheduled_email_enabled ? `${status.scheduled_email_time} IST` : 'Mail off'], ['Last scheduled mail', status.last_scheduled_sent_date ?? 'Not sent yet']].map(([label, value]) => <div key={label} className="subpanel p-4"><p className="text-xs font-medium text-ink-faint">{label}</p><p className="mt-2 break-words font-display text-lg font-semibold text-ink">{value}</p></div>)}
          </div>
        </GlassCard>

        {!isAdmin && <div className="flex items-start gap-3 rounded-2xl border border-accent/20 bg-accent/5 px-4 py-3 text-sm text-ink-dim"><ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-accent" />Portal credentials, sender, recipients, schedule, templates, and mappings are centrally controlled by an administrator. You can run checks and review the complete history.</div>}
        {isAdmin && !status.enabled && <div className="flex items-start gap-3 rounded-2xl border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm text-amber-700"><ShieldCheck className="mt-0.5 h-4 w-4 shrink-0" /><span><strong>Daily automation is off.</strong> Enable it in “Daily tracker mail” and save the settings before expecting the 08:00 scheduled check or email.</span></div>}
        <Flash message={message} />
        {status.last_error && <p className="whitespace-pre-wrap break-words rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-500">{isAdmin ? 'Last check — technical details: ' : ''}{status.last_error}</p>}
        {jobId && <ProgressPanel<CheckResult> jobId={jobId} cancelOnTabClose={false} poller={(id) => get<JobState<CheckResult>>(`${BASE}/jobs/${id}`)} onCancel={() => post(`${BASE}/jobs/${jobId}/cancel`)} onDone={(result) => { setJobId(null); setBusy(null); setMessage({ ok: true, text: result?.skipped ? result.message ?? 'A tracker check is already running. Please wait a few minutes for it to finish, then try again.' : `Checked ${formatIndianNumber(result?.total_pages_scanned ?? 0)} pages and found ${formatIndianNumber(result?.total_pending ?? 0)} pending invoices.` }); void Promise.all([loadSummary(), loadChecks(), loadNotifications()]) }} onError={(error) => { setJobId(null); setBusy(null); setMessage({ ok: false, text: isAdmin || error === ACCOUNT_IN_USE_MESSAGE ? error : PUBLIC_ISSUE_MESSAGE }); void Promise.all([loadSummary(), loadChecks(), loadNotifications()]) }} />}

        <GlassCard padding="lg">
          <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-end">
            <div className="flex items-center gap-3"><ClipboardCheck className="h-5 w-5 text-accent" /><div><h2 className="font-display text-xl font-semibold text-ink">Latest tracker</h2><p className="text-sm text-ink-dim">The latest complete per-location table. This same snapshot is used for the scheduled email.</p></div></div>
            {latest?.available && latest.checked_at && <p className="text-xs text-ink-faint">Updated {formatIndianDateTime(latest.checked_at)} · <span className="capitalize">{latest.trigger}</span> check</p>}
          </div>
          {latest?.available && latest.rows?.length ? <div className="mt-4 overflow-x-auto">
            <table className="w-full min-w-[70rem] text-left text-sm">
              <thead><tr className="border-b border-border text-xs text-ink-faint"><th className="px-3 py-3">Location</th><th className="px-3 py-3">Responsible person</th><th className="px-3 py-3 text-right">Pending for approval</th><th className="px-3 py-3 text-right">Submitted to accounts</th><th className="px-3 py-3 text-right">Total pending</th><th className="px-3 py-3 text-right">Locked (excluded)</th>{isAdmin && <><th className="px-3 py-3 text-right">Records scanned</th><th className="px-3 py-3 text-right">Pages checked</th></>}</tr></thead>
              <tbody>{latest.rows.map((row) => <tr key={`${latest.check_id}-${row.id ?? row.location}`} className="border-b border-border/60"><td className="px-3 py-3 font-semibold text-ink">{row.location}</td><td className="px-3 py-3 text-ink-dim">{row.responsible_person}</td><td className="px-3 py-3 text-right tabular-nums text-ink-dim">{row.pending_for_approval == null ? '—' : formatIndianNumber(row.pending_for_approval)}</td><td className="px-3 py-3 text-right tabular-nums text-ink-dim">{row.submitted_to_accounts == null ? '—' : formatIndianNumber(row.submitted_to_accounts)}</td><td className="px-3 py-3 text-right font-semibold tabular-nums text-ink">{formatIndianNumber(row.pending)}</td><td className="px-3 py-3 text-right tabular-nums text-ink-faint">{row.locked == null ? '—' : formatIndianNumber(row.locked)}</td>{isAdmin && <><td className="px-3 py-3 text-right tabular-nums text-ink-dim">{formatIndianNumber(row.records_scanned)}</td><td className="px-3 py-3 text-right tabular-nums text-ink-dim">{formatIndianNumber(row.pages_scanned)}</td></>}</tr>)}</tbody>
              <tfoot><tr className="bg-accent/5 font-semibold text-ink"><td colSpan={2} className="px-3 py-3">Grand total</td><td className="px-3 py-3 text-right tabular-nums">{formatIndianNumber(latest.rows.reduce((sum, row) => sum + (row.pending_for_approval ?? 0), 0))}</td><td className="px-3 py-3 text-right tabular-nums">{formatIndianNumber(latest.rows.reduce((sum, row) => sum + (row.submitted_to_accounts ?? 0), 0))}</td><td className="px-3 py-3 text-right tabular-nums text-accent">{formatIndianNumber(latest.total_pending ?? latest.rows.reduce((sum, row) => sum + row.pending, 0))}</td><td className="px-3 py-3 text-right tabular-nums text-ink-faint">{formatIndianNumber(latest.rows.reduce((sum, row) => sum + (row.locked ?? 0), 0))}</td>{isAdmin && <><td className="px-3 py-3 text-right tabular-nums">{formatIndianNumber(latest.total_records_scanned ?? latest.rows.reduce((sum, row) => sum + row.records_scanned, 0))}</td><td className="px-3 py-3 text-right tabular-nums">{formatIndianNumber(latest.total_pages_scanned ?? latest.rows.reduce((sum, row) => sum + row.pages_scanned, 0))}</td></>}</tr></tfoot>
            </table>
            <p className="mt-2 text-xs text-ink-faint">“Locked” invoices are currently open for editing by another DMS user and are excluded from Total pending until they’re released.</p>
          </div> : <div className="mt-4 rounded-xl border border-dashed border-border px-4 py-8 text-center"><p className="font-medium text-ink">No completed tracker is available yet.</p><p className="mt-1 text-sm text-ink-dim">The full table will remain here after the first successful manual or scheduled check.</p></div>}
        </GlassCard>

        {isAdmin && settings && <>
          <GlassCard padding="lg" className="flex flex-col gap-5">
            <div className="flex items-center gap-3"><KeyRound className="h-5 w-5 text-accent" /><div><h2 className="font-display text-xl font-semibold text-ink">DMS access and dedicated sender</h2><p className="text-sm text-ink-dim">Shared administrator-owned credentials, encrypted in MySQL and never returned by the API.</p></div></div>
            <div className="grid gap-4 lg:grid-cols-2">
              <Field label="DMS login URL"><input className="field-control" value={settings.login_url} onChange={(event) => setSettings({ ...settings, login_url: event.target.value })} /></Field>
              <Field label="DMS username"><input className="field-control" autoComplete="username" value={settings.username} onChange={(event) => setSettings({ ...settings, username: event.target.value })} /></Field>
              <Field label="DMS password" hint={settings.password_configured ? 'Leave blank to keep the encrypted password.' : 'Required before checks can run.'}><PasswordInput value={password} onChange={(event) => setPassword(event.target.value)} placeholder={settings.password_configured ? 'Saved — enter only to replace' : 'Enter DMS password'} /></Field>
              <Field label="Login timeout (seconds)" hint="15–300 seconds"><input type="number" min={15} max={300} className="field-control" value={settings.login_timeout_seconds} onChange={(event) => setSettings({ ...settings, login_timeout_seconds: Number(event.target.value) })} /></Field>
              <Field label="Sender email"><input type="email" className="field-control" value={settings.sender_email} onChange={(event) => setSettings({ ...settings, sender_email: event.target.value })} /></Field>
              <Field label="Sender app password" hint={settings.sender_app_password_configured ? 'Leave blank to keep the encrypted app password.' : 'Required for scheduled mail.'}><PasswordInput value={senderPassword} onChange={(event) => setSenderPassword(event.target.value)} placeholder={settings.sender_app_password_configured ? 'Saved — enter only to replace' : 'Enter app password'} /></Field>
            </div>
            <div className="subpanel flex flex-col justify-between gap-3 p-4 sm:flex-row sm:items-center"><div><p className="text-sm font-semibold text-ink">Saved browser session: {settings.session_configured ? 'Available' : 'Not imported'}</p><p className="mt-1 text-xs text-ink-faint">Optional. Import a Playwright storage-state JSON if the portal uses a session challenge.</p></div><label className="inline-flex min-h-11 cursor-pointer items-center justify-center gap-2 rounded-xl border border-border bg-surface px-4 text-sm font-semibold text-ink"><Upload className="h-4 w-4" />{busy === 'session' ? 'Importing…' : 'Import session'}<input type="file" accept=".json,application/json" className="sr-only" disabled={Boolean(busy)} onChange={(event) => { const file = event.target.files?.[0]; if (file) void uploadSession(file); event.currentTarget.value = '' }} /></label></div>
            <Flash message={credentialsMessage} />
          </GlassCard>

          <GlassCard padding="lg" className="flex flex-col gap-5">
            <div className="flex items-center gap-3"><Mail className="h-5 w-5 text-accent" /><div><h2 className="font-display text-xl font-semibold text-ink">Daily tracker mail</h2><p className="text-sm text-ink-dim">A successful scheduled scan sends the table by email exactly once per date.</p></div></div>
            <div className="grid gap-4 lg:grid-cols-2">
              <label className="flex items-center gap-3 text-sm font-medium text-ink"><input type="checkbox" className="h-4 w-4 accent-accent" checked={settings.enabled} onChange={(event) => setSettings({ ...settings, enabled: event.target.checked })} />Enable daily tracker automation</label>
              <label className="flex items-center gap-3 text-sm font-medium text-ink"><input type="checkbox" className="h-4 w-4 accent-accent" checked={settings.scheduled_email_enabled} onChange={(event) => setSettings({ ...settings, scheduled_email_enabled: event.target.checked })} />Send the scheduled tracker mail</label>
              <Field label="Send time (IST)"><input type="time" className="field-control" value={settings.scheduled_email_time} onChange={(event) => setSettings({ ...settings, scheduled_email_time: event.target.value })} /></Field>
              <div />
              <Field label="To" hint="One address per line, or comma-separated."><textarea className="field-control min-h-24" value={mailTo} onChange={(event) => setMailTo(event.target.value)} /></Field>
              <Field label="Cc"><textarea className="field-control min-h-24" value={mailCc} onChange={(event) => setMailCc(event.target.value)} /></Field>
            </div>
            <Field label="Subject" hint="Placeholders: {date}, {total_pending}, {location_count}"><input className="field-control" value={settings.subject_template} onChange={(event) => setSettings({ ...settings, subject_template: event.target.value })} /></Field>
            <Field label="Body" hint="Placeholders: {date}, {total_pending}, {location_count}, {tracker_table}"><textarea className="field-control min-h-52 font-mono text-sm" value={settings.body_template} onChange={(event) => setSettings({ ...settings, body_template: event.target.value })} /></Field>
            <Field label="Signature" hint="Appended below the table on every scheduled and test mail. Leave blank for none."><textarea className="field-control min-h-24" value={settings.signature} onChange={(event) => setSettings({ ...settings, signature: event.target.value })} /></Field>
            <Flash message={mailMessage} />
            <div className="flex flex-wrap justify-end gap-3"><Button variant="secondary" icon={<Send className="h-4 w-4" />} loading={busy === 'test'} disabled={Boolean(busy)} onClick={() => void testMail()}>Send test mail to me</Button><Button icon={<Save className="h-4 w-4" />} loading={busy === 'settings'} disabled={Boolean(busy)} onClick={() => void saveSettings()}>Save all automation settings</Button></div>
          </GlassCard>

          <GlassCard padding="lg" className="flex flex-col gap-4">
            <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-end">
              <div><h2 className="font-display text-xl font-semibold text-ink">DMS tracker mappings</h2><p className="mt-1 text-sm text-ink-dim">Search, reorder, pause, edit, archive, or restore centrally managed work queues.</p></div>
              <div className="flex flex-wrap items-center gap-3">
                <div className="relative"><Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-faint" /><input className="field-control pl-9" placeholder="Search mappings" value={mappingSearch} onChange={(event) => { setMappingSearch(event.target.value); setMappingPage(1) }} /></div>
                {!showArchived && <Button icon={<Plus className="h-4 w-4" />} onClick={openAddMapping}>Add mapping</Button>}
              </div>
            </div>
            <label className="flex items-center gap-2 text-sm text-ink-dim"><input type="checkbox" className="accent-accent" checked={showArchived} onChange={(event) => { setShowArchived(event.target.checked); setMappingPage(1) }} />Show archived mappings</label>
            <Flash message={mappingMessage} />
            <div className="overflow-x-auto"><table className="w-full min-w-[70rem] text-left text-sm"><thead><tr className="border-b border-border text-xs text-ink-faint"><th className="px-2 py-3">Order</th><th className="px-2 py-3">Location</th><th className="px-2 py-3">Responsible person</th><th className="px-2 py-3">DMS work queue</th><th className="px-2 py-3">Active</th><th className="px-2 py-3 text-right">Actions</th></tr></thead><tbody>{mappings.map((row) => <tr key={row.id} className="border-b border-border/60 align-top"><td className="px-2 py-3 tabular-nums text-ink-dim">{row.sort_order}</td><td className="px-2 py-3 font-semibold text-ink">{row.location}</td><td className="px-2 py-3 text-ink-dim">{row.responsible_person}</td><td className="max-w-lg px-2 py-3 text-xs leading-5 text-ink-dim"><span className="block">{row.queue_label}</span><span className="text-ink-faint">{row.queue_key}</span></td><td className="px-2 py-3">{row.is_active ? 'Yes' : 'Paused'}</td><td className="px-2 py-3 text-right">{row.is_deleted ? <button className="inline-flex min-h-9 items-center gap-1 rounded-lg px-2 text-xs font-semibold text-emerald-600 hover:bg-emerald-500/10" disabled={busy === `mapping-${row.id}`} onClick={() => void restoreMapping(row)}><Trash2 className="h-3.5 w-3.5" />Restore</button> : <><button className="mr-2 inline-flex min-h-9 items-center gap-1 rounded-lg px-2 text-xs font-semibold text-accent hover:bg-accent/10" onClick={() => openEditMapping(row)}><Pencil className="h-3.5 w-3.5" />Edit</button><button className="inline-flex min-h-9 items-center gap-1 rounded-lg px-2 text-xs font-semibold text-red-500 hover:bg-red-500/10" disabled={busy === `mapping-${row.id}`} onClick={() => setArchiveConfirm(row)}><Trash2 className="h-3.5 w-3.5" />Archive</button></>}</td></tr>)}</tbody></table>{mappings.length === 0 && <p className="py-8 text-center text-sm text-ink-faint">No mappings match this search.</p>}</div>
            <Pagination page={mappingPage} pageCount={Math.max(1, Math.ceil(mappingTotal / 15))} pageSize={15} totalItems={mappingTotal} itemLabel="mappings" onPageChange={setMappingPage} onPageSizeChange={() => undefined} />
          </GlassCard>

          <Modal open={mappingModalOpen} onClose={closeMappingModal} title={editingId ? 'Edit mapping' : 'Add mapping'} className="max-w-2xl">
            <div className="flex flex-col gap-4">
              <Flash message={mappingModalMessage} />
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Order"><input type="number" min={0} className="field-control" value={mappingDraft.sort_order} onChange={(event) => setMappingDraft({ ...mappingDraft, sort_order: Number(event.target.value) })} /></Field>
                <Field label="Location"><input className="field-control" value={mappingDraft.location} onChange={(event) => setMappingDraft({ ...mappingDraft, location: event.target.value })} /></Field>
                <Field label="Responsible person"><input className="field-control" value={mappingDraft.responsible_person} onChange={(event) => setMappingDraft({ ...mappingDraft, responsible_person: event.target.value })} /></Field>
                <Field label="DMS work-queue label"><input className="field-control" value={mappingDraft.queue_label} onChange={(event) => setMappingDraft({ ...mappingDraft, queue_label: event.target.value })} /></Field>
                <Field label="Queue key (fallback)" hint="Used if the label can't be matched live on the portal."><input className="field-control" value={mappingDraft.queue_key} onChange={(event) => setMappingDraft({ ...mappingDraft, queue_key: event.target.value })} /></Field>
                <label className="flex items-center gap-2 self-end pb-2 text-sm text-ink-dim"><input type="checkbox" checked={mappingDraft.is_active} onChange={(event) => setMappingDraft({ ...mappingDraft, is_active: event.target.checked })} />Active</label>
              </div>
              <div className="mt-2 flex flex-col-reverse gap-3 sm:flex-row sm:justify-end">
                <Button variant="secondary" onClick={closeMappingModal}>Cancel</Button>
                <Button icon={<Save className="h-4 w-4" />} loading={busy === 'mapping'} disabled={!mappingDraft.location.trim() || !mappingDraft.responsible_person.trim() || !mappingDraft.queue_label.trim()} onClick={() => void saveMapping()}>{editingId ? 'Save changes' : 'Add mapping'}</Button>
              </div>
            </div>
          </Modal>

          <Modal open={archiveConfirm !== null} onClose={() => setArchiveConfirm(null)} title="Archive mapping">
            <p className="mb-6 text-sm text-ink-dim">Archive “{archiveConfirm?.location}”? It will be excluded from every future check until restored from “Show archived mappings.”</p>
            <div className="flex flex-col-reverse gap-3 sm:flex-row sm:justify-end">
              <Button variant="secondary" onClick={() => setArchiveConfirm(null)}>Cancel</Button>
              <Button variant="danger" loading={busy === `mapping-${archiveConfirm?.id}`} onClick={() => void confirmArchiveMapping()}>Archive</Button>
            </div>
          </Modal>
        </>}

        <GlassCard padding="lg">
          <div className="flex items-center gap-3"><ClipboardCheck className="h-5 w-5 text-accent" /><div><h2 className="font-display text-xl font-semibold text-ink">Complete tracker-check history</h2><p className="text-sm text-ink-dim">Every manual and scheduled attempt, plus the exact per-location results from successful scans.</p></div></div>
          <div className="mt-4 overflow-x-auto"><table className="w-full min-w-[84rem] text-left text-sm"><thead><tr className="border-b border-border text-xs text-ink-faint"><th className="px-2 py-3">Checked at (IST)</th><th className="px-2 py-3">Trigger</th><th className="px-2 py-3">Pending for approval</th><th className="px-2 py-3">Submitted to accounts</th><th className="px-2 py-3">Total pending</th><th className="px-2 py-3">Locked (excluded)</th>{isAdmin && <><th className="px-2 py-3">Records</th><th className="px-2 py-3">Pages</th></>}<th className="px-2 py-3">Status</th><th className="px-2 py-3">Details</th></tr></thead><tbody>{checks.map((row) => {
            const breakdownKnown = row.rows?.length && row.rows.some((detail) => detail.pending_for_approval != null || detail.submitted_to_accounts != null)
            const pendingForApprovalTotal = row.rows?.reduce((sum, detail) => sum + (detail.pending_for_approval ?? 0), 0)
            const submittedToAccountsTotal = row.rows?.reduce((sum, detail) => sum + (detail.submitted_to_accounts ?? 0), 0)
            const lockedTotal = row.rows?.reduce((sum, detail) => sum + (detail.locked ?? 0), 0)
            return <Fragment key={row.id}><tr className="border-b border-border/60 align-top"><td className="whitespace-nowrap px-2 py-3 text-ink-dim">{formatIndianDateTime(row.checked_at)}</td><td className="px-2 py-3 capitalize text-ink-dim">{row.trigger}</td><td className="px-2 py-3 text-ink-dim">{breakdownKnown ? formatIndianNumber(pendingForApprovalTotal ?? 0) : '—'}</td><td className="px-2 py-3 text-ink-dim">{breakdownKnown ? formatIndianNumber(submittedToAccountsTotal ?? 0) : '—'}</td><td className="px-2 py-3 font-semibold text-ink">{row.total_pending == null ? '—' : formatIndianNumber(row.total_pending)}</td><td className="px-2 py-3 text-ink-faint">{breakdownKnown ? formatIndianNumber(lockedTotal ?? 0) : '—'}</td>{isAdmin && <><td className="px-2 py-3 text-ink-dim">{row.total_records_scanned == null ? '—' : formatIndianNumber(row.total_records_scanned)}</td><td className="px-2 py-3 text-ink-dim">{row.total_pages_scanned ?? '—'}</td></>}<td className="px-2 py-3"><Badge status={row.status} /></td><td className="px-2 py-3">{row.rows?.length ? <button className="inline-flex items-center gap-1 text-xs font-semibold text-accent" onClick={() => setExpandedCheck(expandedCheck === row.id ? null : row.id)}>{expandedCheck === row.id ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}Per-location results</button> : <span className="max-w-md whitespace-pre-wrap break-words text-xs text-ink-dim">{row.error_message || 'No detail available.'}</span>}</td></tr>{expandedCheck === row.id && row.rows?.length ? <tr className="border-b border-border"><td colSpan={isAdmin ? 10 : 8} className="bg-bg-soft/45 p-4"><div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">{row.rows.map((detail) => <div key={`${row.id}-${detail.location}`} className="rounded-xl border border-border bg-surface p-3"><div className="flex justify-between gap-3"><div><p className="font-semibold text-ink">{detail.location}</p><p className="mt-1 text-xs text-ink-faint">{detail.responsible_person}</p></div><span className="font-display text-xl font-semibold text-accent">{detail.pending}</span></div><p className="mt-2 text-xs text-ink-dim">{detail.pending_for_approval == null ? '—' : formatIndianNumber(detail.pending_for_approval)} pending for approval · {detail.submitted_to_accounts == null ? '—' : formatIndianNumber(detail.submitted_to_accounts)} submitted to accounts</p>{detail.locked != null && detail.locked > 0 && <p className="mt-1 text-xs text-ink-faint">{formatIndianNumber(detail.locked)} locked (excluded)</p>}{isAdmin && <p className="mt-1 text-xs text-ink-faint">{detail.records_scanned} records · {detail.pages_scanned} pages checked</p>}</div>)}</div></td></tr> : null}</Fragment>
          })}</tbody></table>{checks.length === 0 && <p className="py-8 text-center text-sm text-ink-faint">No tracker checks have run yet.</p>}</div>
          <Pagination className="mt-4" page={checkPage} pageCount={Math.max(1, Math.ceil(checkTotal / 10))} pageSize={10} totalItems={checkTotal} itemLabel="checks" onPageChange={setCheckPage} onPageSizeChange={() => undefined} />
        </GlassCard>

        <GlassCard padding="lg">
          <div className="flex items-center gap-3"><Mail className="h-5 w-5 text-accent" /><div><h2 className="font-display text-xl font-semibold text-ink">Complete scheduled-mail history</h2><p className="text-sm text-ink-dim">Every generated tracker mail and its delivery result.</p></div></div>
          <div className="mt-4 overflow-x-auto"><table className="w-full min-w-[50rem] text-left text-sm"><thead><tr className="border-b border-border text-xs text-ink-faint"><th className="px-2 py-3">Created at (IST)</th><th className="px-2 py-3">Subject</th><th className="px-2 py-3">Status</th><th className="px-2 py-3">Delivery details</th></tr></thead><tbody>{notifications.map((row) => <tr key={row.id} className="border-b border-border/60 align-top"><td className="whitespace-nowrap px-2 py-3 text-ink-dim">{formatIndianDateTime(row.created_at)}</td><td className="max-w-sm px-2 py-3 text-ink">{row.subject}</td><td className="px-2 py-3"><Badge status={row.status} /></td><td className="max-w-md whitespace-pre-wrap break-words px-2 py-3 text-xs text-ink-dim">{row.error_message || (row.sent_at ? `Sent ${formatIndianDateTime(row.sent_at)}` : 'Awaiting delivery')}</td></tr>)}</tbody></table>{notifications.length === 0 && <p className="py-8 text-center text-sm text-ink-faint">No scheduled tracker mails have been generated yet.</p>}</div>
          <Pagination className="mt-4" page={notificationPage} pageCount={Math.max(1, Math.ceil(notificationTotal / 10))} pageSize={10} totalItems={notificationTotal} itemLabel="emails" onPageChange={setNotificationPage} onPageSizeChange={() => undefined} />
        </GlassCard>
      </div>
    </AppShell>
  )
}
