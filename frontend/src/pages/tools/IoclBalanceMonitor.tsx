import { useEffect, useMemo, useState } from 'react'
import {
  Activity,
  AlertTriangle,
  CalendarClock,
  CheckCircle2,
  KeyRound,
  Mail,
  RefreshCw,
  Save,
  Send,
  ShieldCheck,
  Upload,
  WalletCards,
  XCircle,
} from 'lucide-react'
import { AppShell } from '@/components/AppShell'
import { Button } from '@/components/Button'
import { GlassCard } from '@/components/GlassCard'
import { PasswordInput } from '@/components/PasswordInput'
import { Pagination } from '@/components/Pagination'
import { ProgressPanel, type JobState } from '@/components/ProgressPanel'
import { LoadingNotice } from '@/components/LoadingNotice'
import { ApiError, get, post, postForm, put } from '@/lib/api'
import { useAuth } from '@/lib/auth-context'
import { PUBLIC_ISSUE_MESSAGE } from '@/lib/error-visibility'
import { formatIndianCurrency, formatIndianDateTime } from '@/lib/regional'

const BASE = '/tools/iocl-balance'
function visibleIssue(error: unknown, fallback: string, isAdmin: boolean): string {
  if (!isAdmin) return PUBLIC_ISSUE_MESSAGE
  return error instanceof ApiError ? error.message : fallback
}

interface MonitorStatus {
  enabled: boolean
  portal_configured: boolean
  mail_configured: boolean
  check_interval_minutes: number
  last_balance: number | null
  last_checked_at: string | null
  last_check_status: string | null
  last_error: string | null
  next_check_at: string | null
}

interface Settings extends MonitorStatus {
  version: number
  sender_email: string
  sender_app_password_configured: boolean
  login_url: string
  username: string
  password_configured: boolean
  session_configured: boolean
  login_timeout_seconds: number
  daily_email_enabled: boolean
  daily_email_time: string
  daily_to: string[]
  daily_cc: string[]
  daily_subject_template: string
  daily_body_template: string
  alerts_enabled: boolean
  alert_start_amount: number
  alert_repeat_minutes: number
  alert_to: string[]
  alert_cc: string[]
  alert_subject_template: string
  alert_body_template: string
  last_daily_sent_date: string | null
}

interface CheckResult {
  check_id?: number
  balance?: number
  checked_at?: string
  attempts?: number
  skipped?: boolean
  message?: string
}

interface CheckRow {
  id: number
  trigger: string
  status: string
  balance: number | null
  error_message: string | null
  checked_at: string
  duration_seconds: number | null
}

interface NotificationRow {
  id: number
  notification_type: string
  threshold_amount: number | null
  balance: number
  subject: string
  status: string
  error_message: string | null
  created_at: string
  sent_at: string | null
}

interface Page<T> { total: number; items: T[] }

function recipientText(values: string[]): string {
  return values.join('\n')
}

function parseRecipients(value: string): string[] {
  return Array.from(new Set(value.split(/[\n,;]+/).map((item) => item.trim()).filter(Boolean)))
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="flex min-w-0 flex-col gap-1.5 text-sm">
      <span className="font-medium text-ink-dim">{label}</span>
      {children}
      {hint && <span className="text-xs leading-5 text-ink-faint">{hint}</span>}
    </label>
  )
}

function StatusBadge({ status }: { status: string }) {
  const good = status === 'success' || status === 'sent'
  const active = status === 'pending' || status === 'sending'
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-lg px-2 py-1 text-xs font-semibold ${good ? 'bg-emerald-500/10 text-emerald-600' : active ? 'bg-amber-500/10 text-amber-600' : 'bg-red-500/10 text-red-500'}`}>
      {good ? <CheckCircle2 className="h-3.5 w-3.5" /> : active ? <RefreshCw className="h-3.5 w-3.5" /> : <XCircle className="h-3.5 w-3.5" />}
      {status}
    </span>
  )
}

export default function IoclBalanceMonitor() {
  const { user } = useAuth()
  const isAdmin = user?.role === 'admin'
  const [status, setStatus] = useState<MonitorStatus | null>(null)
  const [settings, setSettings] = useState<Settings | null>(null)
  const [password, setPassword] = useState('')
  const [senderAppPassword, setSenderAppPassword] = useState('')
  const [dailyTo, setDailyTo] = useState('')
  const [dailyCc, setDailyCc] = useState('')
  const [alertTo, setAlertTo] = useState('')
  const [alertCc, setAlertCc] = useState('')
  const [checks, setChecks] = useState<CheckRow[]>([])
  const [checkTotal, setCheckTotal] = useState(0)
  const [checkPage, setCheckPage] = useState(1)
  const [checkPageSize, setCheckPageSize] = useState(10)
  const [checkStatus, setCheckStatus] = useState('')
  const [checkTrigger, setCheckTrigger] = useState('')
  const [notifications, setNotifications] = useState<NotificationRow[]>([])
  const [notificationTotal, setNotificationTotal] = useState(0)
  const [notificationPage, setNotificationPage] = useState(1)
  const [notificationPageSize, setNotificationPageSize] = useState(10)
  const [notificationStatus, setNotificationStatus] = useState('')
  const [notificationType, setNotificationType] = useState('')
  const [loading, setLoading] = useState(true)
  const [savingPanel, setSavingPanel] = useState<string | null>(null)
  const [testingMail, setTestingMail] = useState<'daily' | 'alert' | null>(null)
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null)
  const [jobId, setJobId] = useState<string | null>(null)
  const [checking, setChecking] = useState(false)
  const [sessionUploading, setSessionUploading] = useState(false)

  async function loadChecks() {
    const params = new URLSearchParams({ limit: String(checkPageSize), offset: String((checkPage - 1) * checkPageSize) })
    if (checkStatus) params.set('status', checkStatus)
    if (checkTrigger) params.set('trigger', checkTrigger)
    const page = await get<Page<CheckRow>>(`${BASE}/checks?${params}`)
    setChecks(page.items)
    setCheckTotal(page.total)
  }

  async function loadNotifications() {
    const params = new URLSearchParams({ limit: String(notificationPageSize), offset: String((notificationPage - 1) * notificationPageSize) })
    if (notificationStatus) params.set('status', notificationStatus)
    if (notificationType) params.set('notification_type', notificationType)
    const page = await get<Page<NotificationRow>>(`${BASE}/notifications?${params}`)
    setNotifications(page.items)
    setNotificationTotal(page.total)
  }

  async function loadAll(initializeRecipients = true) {
    setLoading(true)
    try {
      const settingsRequest: Promise<Settings | null> = isAdmin
        ? get<Settings>(`${BASE}/settings`)
        : Promise.resolve(null)
      const [nextStatus, nextSettings, checkResultPage, notificationResultPage] = await Promise.all([
        get<MonitorStatus>(`${BASE}/status`),
        settingsRequest,
        get<Page<CheckRow>>(`${BASE}/checks?limit=${checkPageSize}&offset=${(checkPage - 1) * checkPageSize}`),
        get<Page<NotificationRow>>(`${BASE}/notifications?limit=${notificationPageSize}&offset=${(notificationPage - 1) * notificationPageSize}`),
      ])
      setStatus(nextStatus)
      setSettings(nextSettings)
      if (initializeRecipients && nextSettings) {
        setDailyTo(recipientText(nextSettings.daily_to))
        setDailyCc(recipientText(nextSettings.daily_cc))
        setAlertTo(recipientText(nextSettings.alert_to))
        setAlertCc(recipientText(nextSettings.alert_cc))
      }
      setChecks(checkResultPage.items)
      setCheckTotal(checkResultPage.total)
      setNotifications(notificationResultPage.items)
      setNotificationTotal(notificationResultPage.total)
    } catch (error) {
      setMessage({ ok: false, text: visibleIssue(error, 'Unable to load IOCL monitor settings.', isAdmin) })
    } finally {
      setLoading(false)
    }
  }

  async function refreshAfterCheck() {
    const nextStatus = await get<MonitorStatus>(`${BASE}/status`)
    setStatus(nextStatus)
    if (isAdmin) {
      const nextSettings = await get<Settings>(`${BASE}/settings`)
      setSettings(nextSettings)
    }
    await Promise.all([loadChecks(), loadNotifications()])
  }

  useEffect(() => { void loadAll() }, [])

  useEffect(() => {
    if (!loading) void loadChecks().catch((error) => setMessage({ ok: false, text: visibleIssue(error, 'Unable to load check history.', isAdmin) }))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [checkPage, checkPageSize, checkStatus, checkTrigger])

  useEffect(() => {
    if (!loading) void loadNotifications().catch((error) => setMessage({ ok: false, text: visibleIssue(error, 'Unable to load email history.', isAdmin) }))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [notificationPage, notificationPageSize, notificationStatus, notificationType])

  async function saveSettings(panel: string, successText: string) {
    if (!settings) return
    setSavingPanel(panel)
    setMessage(null)
    try {
      const updated = await put<Settings>(`${BASE}/settings`, {
        ...settings,
        password: password || null,
        sender_app_password: senderAppPassword || null,
        daily_to: parseRecipients(dailyTo),
        daily_cc: parseRecipients(dailyCc),
        alert_to: parseRecipients(alertTo),
        alert_cc: parseRecipients(alertCc),
      })
      setSettings(updated)
      setStatus(updated)
      setPassword('')
      setSenderAppPassword('')
      setMessage({ ok: true, text: successText })
    } catch (error) {
      setMessage({ ok: false, text: visibleIssue(error, 'Unable to save settings.', isAdmin) })
    } finally {
      setSavingPanel(null)
    }
  }

  async function testMail(mailType: 'daily' | 'alert') {
    if (!settings) return
    setTestingMail(mailType)
    setMessage(null)
    try {
      const result = await post<{ ok: boolean; sent_to: string }>(`${BASE}/test-mail`, {
        mail_type: mailType,
        subject_template: mailType === 'daily' ? settings.daily_subject_template : settings.alert_subject_template,
        body_template: mailType === 'daily' ? settings.daily_body_template : settings.alert_body_template,
      })
      setMessage({ ok: true, text: `Test mail sent to ${result.sent_to} — check your inbox.` })
    } catch (error) {
      setMessage({ ok: false, text: visibleIssue(error, 'Unable to send the test mail.', isAdmin) })
    } finally {
      setTestingMail(null)
    }
  }

  async function checkNow() {
    setChecking(true)
    setMessage(null)
    try {
      const result = await post<{ job_id: string }>(`${BASE}/check-now`)
      setJobId(result.job_id)
    } catch (error) {
      setChecking(false)
      setMessage({ ok: false, text: visibleIssue(error, 'Unable to start the balance check.', isAdmin) })
    }
  }

  async function uploadSession(file: File) {
    setSessionUploading(true)
    setMessage(null)
    try {
      const form = new FormData()
      form.append('file', file)
      const updated = await postForm<Settings>(`${BASE}/session`, form)
      setSettings(updated)
      setStatus(updated)
      setMessage({ ok: true, text: 'Browser session imported and encrypted.' })
    } catch (error) {
      setMessage({ ok: false, text: visibleIssue(error, 'Unable to import the browser session.', isAdmin) })
    } finally {
      setSessionUploading(false)
    }
  }

  const configured = Boolean(status?.portal_configured)
  const statusText = useMemo(() => {
    if (!status) return 'Loading'
    if (!status.enabled) return 'Paused'
    if (!configured) return 'Setup required'
    return status.last_check_status === 'error' ? 'Needs attention' : 'Monitoring'
  }, [status, configured])

  const anyActionRunning = savingPanel !== null || testingMail !== null

  if (loading || !status) {
    return <AppShell title="Ultrafine IOCL Balance Monitor"><LoadingNotice /></AppShell>
  }

  return (
    <AppShell title="Ultrafine IOCL Balance Monitor">
      <div className="flex flex-col gap-6">
        <GlassCard padding="lg" className="overflow-hidden">
          <div className="flex flex-col justify-between gap-6 lg:flex-row lg:items-center">
            <div className="flex items-start gap-4">
              <span className="icon-tile grid h-12 w-12 shrink-0 place-items-center rounded-xl"><WalletCards className="h-5 w-5" /></span>
              <div>
                <p className="text-sm font-semibold text-accent">Ultrafine treasury automation</p>
                <h2 className="mt-1.5 font-display text-2xl font-semibold tracking-[-0.03em] text-ink">IOCL balance, watched automatically</h2>
                <p className="mt-2 max-w-2xl text-sm leading-6 text-ink-dim">Checks the IOCL CCMS balance on a schedule, sends a morning balance mail, and repeats reminders while the balance remains below your threshold.</p>
              </div>
            </div>
            <Button icon={<RefreshCw className="h-4 w-4" />} loading={checking} onClick={() => void checkNow()}>Check balance now</Button>
          </div>

          <div className="mt-6 grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
            <div className="subpanel p-4"><span className="text-xs font-medium text-ink-faint">Current balance</span><p className="mt-1 font-display text-2xl font-semibold text-ink">{status.last_balance == null ? 'Not checked' : formatIndianCurrency(status.last_balance)}</p></div>
            <div className="subpanel p-4"><span className="text-xs font-medium text-ink-faint">Status</span><p className="mt-1 font-display text-xl font-semibold text-accent">{statusText}</p></div>
            <div className="subpanel p-4"><span className="text-xs font-medium text-ink-faint">Last checked</span><p className="mt-1 text-sm font-semibold text-ink">{status.last_checked_at ? formatIndianDateTime(status.last_checked_at) : 'Not yet'}</p></div>
            <div className="subpanel p-4"><span className="text-xs font-medium text-ink-faint">Next scheduled check</span><p className="mt-1 text-sm font-semibold text-ink">{status.next_check_at ? formatIndianDateTime(status.next_check_at) : 'Monitoring is paused'}</p></div>
            <div className="subpanel p-4">
              <span className="text-xs font-medium text-ink-faint">Mail automation</span>
              <p className={`mt-1 text-sm font-semibold ${status.mail_configured ? 'text-emerald-600' : 'text-amber-600'}`}>{status.mail_configured ? 'Sender ready' : 'Needs admin setup'}</p>
              <p className="mt-1 text-xs text-ink-faint">Managed centrally by an administrator</p>
            </div>
          </div>
        </GlassCard>

        {isAdmin && !status.mail_configured && (
          <div className="flex items-start gap-3 rounded-2xl border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm text-ink-dim">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
            Configure the dedicated IOCL sender below before scheduled messages can be delivered.
          </div>
        )}
        {!isAdmin && (
          <div className="flex items-start gap-3 rounded-2xl border border-accent/20 bg-accent/5 px-4 py-3 text-sm text-ink-dim">
            <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-accent" />
            Alert recipients, sender credentials, thresholds, and schedules are centrally controlled by an administrator. You can check the balance and review the complete history.
          </div>
        )}
        {message && <p className={`rounded-xl border px-4 py-3 text-sm ${message.ok ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-600' : 'border-red-500/30 bg-red-500/10 text-red-500'}`}>{message.text}</p>}
        {status.last_error && <p className="whitespace-pre-wrap break-words rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-500">{isAdmin ? 'Last check — technical details: ' : ''}{status.last_error}</p>}

        {jobId && (
          <ProgressPanel<CheckResult>
            jobId={jobId}
            cancelOnTabClose={false}
            poller={(id) => get<JobState<CheckResult>>(`${BASE}/jobs/${id}`)}
            onCancel={() => post(`${BASE}/jobs/${jobId}/cancel`)}
            onDone={(result) => {
              setChecking(false)
              setJobId(null)
              const attemptNote = (result?.attempts ?? 1) > 1 ? ` after ${result?.attempts} attempts` : ''
              setMessage({ ok: true, text: result?.skipped ? result.message ?? 'A check is already running.' : `Balance check completed${attemptNote}${result?.balance == null ? '.' : `: ${formatIndianCurrency(result.balance)}.`}` })
              void refreshAfterCheck()
            }}
            onError={(error) => { setChecking(false); setJobId(null); setMessage({ ok: false, text: isAdmin ? error : PUBLIC_ISSUE_MESSAGE }); void refreshAfterCheck() }}
          />
        )}

        {isAdmin && settings && <>
        <GlassCard padding="lg" className="flex flex-col gap-5">
          <div className="flex items-center gap-4"><span className="icon-tile grid h-11 w-11 place-items-center rounded-xl"><KeyRound className="h-5 w-5" /></span><div><h2 className="font-display text-xl font-semibold text-ink">Portal login</h2><p className="text-sm text-ink-dim">Encrypted server-side and never sent back to the browser.</p></div></div>
          <div className="grid gap-4 lg:grid-cols-2">
            <Field label="Login URL"><input className="field-control" value={settings.login_url} onChange={(event) => setSettings({ ...settings, login_url: event.target.value })} /></Field>
            <Field label="IOCL username"><input className="field-control" autoComplete="username" value={settings.username} onChange={(event) => setSettings({ ...settings, username: event.target.value })} /></Field>
            <Field label="IOCL password" hint={settings.password_configured ? 'Leave blank to keep the saved password.' : 'Required before monitoring can start.'}><PasswordInput autoComplete="new-password" value={password} onChange={(event) => setPassword(event.target.value)} placeholder={settings.password_configured ? 'Saved — enter only to replace it' : 'Enter IOCL password'} /></Field>
            <Field label="Login timeout (seconds)" hint="Give the portal more time if it loads slowly. 15–300 seconds."><input type="number" min={15} max={300} className="field-control" value={settings.login_timeout_seconds} onChange={(event) => setSettings({ ...settings, login_timeout_seconds: Number(event.target.value) })} /></Field>
          </div>
          <div className="subpanel flex flex-col justify-between gap-3 p-4 sm:flex-row sm:items-center">
            <div><p className="text-sm font-semibold text-ink">Saved browser session: {settings.session_configured ? 'Available' : 'Not imported yet'}</p><p className="mt-1 text-xs leading-5 text-ink-faint">Optional — helps skip a CAPTCHA. Import a working Playwright xtrapower_session.json to use it.</p></div>
            <label className="inline-flex min-h-11 cursor-pointer items-center justify-center gap-2 rounded-xl border border-border bg-surface px-4 text-sm font-semibold text-ink transition hover:border-accent/40 hover:text-accent"><Upload className="h-4 w-4" />{sessionUploading ? 'Importing…' : 'Import session'}<input type="file" accept=".json,application/json" className="sr-only" disabled={sessionUploading} onChange={(event) => { const file = event.target.files?.[0]; if (file) void uploadSession(file); event.currentTarget.value = '' }} /></label>
          </div>
          <div className="flex justify-end"><Button icon={<Save className="h-4 w-4" />} loading={savingPanel === 'portal'} disabled={anyActionRunning && savingPanel !== 'portal'} onClick={() => void saveSettings('portal', 'Portal login saved.')}>Save login details</Button></div>
        </GlassCard>

        <GlassCard padding="lg" className="flex flex-col gap-5">
          <div className="flex items-center gap-4"><span className="icon-tile grid h-11 w-11 place-items-center rounded-xl"><Mail className="h-5 w-5" /></span><div><h2 className="font-display text-xl font-semibold text-ink">Dedicated mail sender</h2><p className="text-sm text-ink-dim">The single account used by every scheduled morning mail and balance alert.</p></div></div>
          <div className="grid gap-4 lg:grid-cols-2">
            <Field label="Sender email" hint="Use an authorized Gmail or Google Workspace account."><input type="email" autoComplete="username" className="field-control" value={settings.sender_email} onChange={(event) => setSettings({ ...settings, sender_email: event.target.value })} /></Field>
            <Field label="Sender app password" hint={settings.sender_app_password_configured ? 'Leave blank to keep the saved app password.' : 'Required before email automation can start.'}><PasswordInput autoComplete="new-password" value={senderAppPassword} onChange={(event) => setSenderAppPassword(event.target.value)} placeholder={settings.sender_app_password_configured ? 'Saved — enter only to replace it' : 'Enter the 16-character app password'} /></Field>
          </div>
          <div className="subpanel flex flex-col justify-between gap-3 p-4 sm:flex-row sm:items-center">
            <div><p className="text-sm font-semibold text-ink">Sender status: {settings.mail_configured ? 'Ready' : 'Setup required'}</p><p className="mt-1 text-xs leading-5 text-ink-faint">Credentials are encrypted in MySQL and are never returned to the browser, job queue, history, or logs.</p></div>
            <Button icon={<Save className="h-4 w-4" />} loading={savingPanel === 'sender'} disabled={anyActionRunning && savingPanel !== 'sender'} onClick={() => void saveSettings('sender', 'Dedicated mail sender saved.')}>Save sender</Button>
          </div>
        </GlassCard>

        <GlassCard padding="lg" className="flex flex-col gap-5">
          <div className="flex items-center gap-4"><span className="icon-tile grid h-11 w-11 place-items-center rounded-xl"><Activity className="h-5 w-5" /></span><div><h2 className="font-display text-xl font-semibold text-ink">Automatic checking</h2><p className="text-sm text-ink-dim">How often the balance is checked on its own.</p></div></div>
          <label className="flex items-center gap-3 text-sm font-medium text-ink"><input type="checkbox" className="h-4 w-4 accent-accent" checked={settings.enabled} onChange={(event) => setSettings({ ...settings, enabled: event.target.checked })} />Automatically check the balance</label>
          <Field label="Check every (minutes)" hint="5–1440 minutes."><input type="number" min={5} max={1440} className="field-control max-w-xs" value={settings.check_interval_minutes} onChange={(event) => setSettings({ ...settings, check_interval_minutes: Number(event.target.value) })} /></Field>
          <div className="flex justify-end"><Button icon={<Save className="h-4 w-4" />} loading={savingPanel === 'rules'} disabled={anyActionRunning && savingPanel !== 'rules'} onClick={() => void saveSettings('rules', 'Checking schedule saved.')}>Save checking schedule</Button></div>
        </GlassCard>

        <div className="grid gap-6 xl:grid-cols-2">
          <GlassCard padding="lg" className="flex flex-col gap-5">
            <div className="flex items-center gap-3"><CalendarClock className="h-5 w-5 text-accent" /><div><h2 className="font-display text-lg font-semibold text-ink">Morning balance mail</h2><p className="text-sm text-ink-dim">One mail a day with the balance, sent at a fixed time.</p></div></div>
            <label className="flex items-center gap-3 text-sm font-medium text-ink"><input type="checkbox" className="h-4 w-4 accent-accent" checked={settings.daily_email_enabled} onChange={(event) => setSettings({ ...settings, daily_email_enabled: event.target.checked })} />Send the morning balance mail</label>
            <Field label="Send time (IST)"><input type="time" className="field-control" value={settings.daily_email_time} onChange={(event) => setSettings({ ...settings, daily_email_time: event.target.value })} /></Field>
            <Field label="To" hint="One address per line, or comma-separated."><textarea className="field-control min-h-24" value={dailyTo} onChange={(event) => setDailyTo(event.target.value)} /></Field>
            <Field label="Cc"><textarea className="field-control min-h-20" value={dailyCc} onChange={(event) => setDailyCc(event.target.value)} /></Field>
            <Field label="Subject" hint="Placeholders: {date}, {balance}, {balance_number}"><input className="field-control" value={settings.daily_subject_template} onChange={(event) => setSettings({ ...settings, daily_subject_template: event.target.value })} /></Field>
            <Field label="Body"><textarea className="field-control min-h-44 font-mono text-sm" value={settings.daily_body_template} onChange={(event) => setSettings({ ...settings, daily_body_template: event.target.value })} /></Field>
            <div className="flex flex-wrap justify-end gap-3">
              <Button variant="secondary" icon={<Send className="h-4 w-4" />} loading={testingMail === 'daily'} disabled={anyActionRunning && testingMail !== 'daily'} onClick={() => void testMail('daily')}>Send test mail to me</Button>
              <Button icon={<Save className="h-4 w-4" />} loading={savingPanel === 'daily'} disabled={anyActionRunning && savingPanel !== 'daily'} onClick={() => void saveSettings('daily', 'Morning balance mail saved.')}>Save morning mail</Button>
            </div>
          </GlassCard>

          <GlassCard padding="lg" className="flex flex-col gap-5">
            <div className="flex items-center gap-3"><Mail className="h-5 w-5 text-accent" /><div><h2 className="font-display text-lg font-semibold text-ink">Threshold reminders</h2><p className="text-sm text-ink-dim">Send an immediate alert below the threshold, then keep reminding until the balance recovers.</p></div></div>
            <label className="flex items-center gap-3 text-sm font-medium text-ink"><input type="checkbox" className="h-4 w-4 accent-accent" checked={settings.alerts_enabled} onChange={(event) => setSettings({ ...settings, alerts_enabled: event.target.checked })} />Send reminders while the balance is below the threshold</label>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Start alerting below (₹)"><input type="number" min={0} step={1000} className="field-control" value={settings.alert_start_amount} onChange={(event) => setSettings({ ...settings, alert_start_amount: Number(event.target.value) })} /></Field>
              <Field label="Repeat reminder every (minutes)" hint="1–43,200 minutes. Reminders are evaluated on each scheduled balance check."><input type="number" min={1} max={43200} step={1} className="field-control" value={settings.alert_repeat_minutes} onChange={(event) => setSettings({ ...settings, alert_repeat_minutes: Number(event.target.value) })} /></Field>
            </div>
            <Field label="To" hint="One address per line, or comma-separated."><textarea className="field-control min-h-24" value={alertTo} onChange={(event) => setAlertTo(event.target.value)} /></Field>
            <Field label="Cc"><textarea className="field-control min-h-20" value={alertCc} onChange={(event) => setAlertCc(event.target.value)} /></Field>
            <Field label="Subject" hint="Placeholders: {date}, {balance}, {balance_number}, {threshold}, {threshold_number}"><input className="field-control" value={settings.alert_subject_template} onChange={(event) => setSettings({ ...settings, alert_subject_template: event.target.value })} /></Field>
            <Field label="Body"><textarea className="field-control min-h-44 font-mono text-sm" value={settings.alert_body_template} onChange={(event) => setSettings({ ...settings, alert_body_template: event.target.value })} /></Field>
            <div className="flex flex-wrap justify-end gap-3">
              <Button variant="secondary" icon={<Send className="h-4 w-4" />} loading={testingMail === 'alert'} disabled={anyActionRunning && testingMail !== 'alert'} onClick={() => void testMail('alert')}>Send test mail to me</Button>
              <Button icon={<Save className="h-4 w-4" />} loading={savingPanel === 'alert'} disabled={anyActionRunning && savingPanel !== 'alert'} onClick={() => void saveSettings('alert', 'Threshold alerts saved.')}>Save threshold alerts</Button>
            </div>
          </GlassCard>
        </div>
        </>}

        <GlassCard padding="lg">
          <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-end"><div className="flex items-center gap-3"><ShieldCheck className="h-5 w-5 text-accent" /><div><h2 className="font-display text-lg font-semibold text-ink">Complete balance-check history</h2><p className="text-sm text-ink-dim">Every scheduled and manual check, including failures and skipped overlaps.</p></div></div><div className="flex flex-wrap gap-2"><select className="field-control min-w-36" value={checkTrigger} onChange={(event) => { setCheckTrigger(event.target.value); setCheckPage(1) }} aria-label="Filter checks by trigger"><option value="">All triggers</option><option value="scheduled">Scheduled</option><option value="manual">Manual</option></select><select className="field-control min-w-36" value={checkStatus} onChange={(event) => { setCheckStatus(event.target.value); setCheckPage(1) }} aria-label="Filter checks by status"><option value="">All statuses</option><option value="success">Success</option><option value="error">Error</option><option value="skipped">Skipped</option></select></div></div>
          <div className="mt-4 overflow-x-auto"><table className="w-full min-w-[56rem] text-left text-sm"><thead><tr className="border-b border-border text-xs text-ink-faint"><th className="px-2 py-3">Checked at (IST)</th><th className="px-2 py-3">Trigger</th><th className="px-2 py-3">Balance</th><th className="px-2 py-3">Duration</th><th className="px-2 py-3">Status</th><th className="px-2 py-3">{isAdmin ? 'Technical details' : 'Details'}</th></tr></thead><tbody>{checks.map((row) => <tr key={row.id} className="border-b border-border/60 align-top"><td className="whitespace-nowrap px-2 py-3 text-ink-dim">{formatIndianDateTime(row.checked_at)}</td><td className="px-2 py-3 capitalize text-ink-dim">{row.trigger}</td><td className="px-2 py-3 font-semibold text-ink">{row.balance == null ? '—' : formatIndianCurrency(row.balance)}</td><td className="px-2 py-3 text-ink-dim">{row.duration_seconds == null ? '—' : `${row.duration_seconds.toFixed(1)} s`}</td><td className="px-2 py-3"><StatusBadge status={row.status} /></td><td className="max-w-md whitespace-pre-wrap break-words px-2 py-3 text-xs leading-5 text-ink-dim">{row.error_message || 'Balance detected and notification rules evaluated.'}</td></tr>)}</tbody></table>{checks.length === 0 && <p className="py-8 text-center text-sm text-ink-faint">No balance checks match these filters.</p>}</div>
          <Pagination className="mt-4" page={checkPage} pageCount={Math.max(1, Math.ceil(checkTotal / checkPageSize))} pageSize={checkPageSize} totalItems={checkTotal} itemLabel="checks" pageSizeOptions={[10, 25, 50, 100]} onPageChange={setCheckPage} onPageSizeChange={(size) => { setCheckPageSize(size); setCheckPage(1) }} />
        </GlassCard>

        <GlassCard padding="lg">
          <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-end"><div className="flex items-center gap-3"><Mail className="h-5 w-5 text-accent" /><div><h2 className="font-display text-lg font-semibold text-ink">Complete notification history</h2><p className="text-sm text-ink-dim">Every morning balance mail and below-threshold reminder, with delivery result and any error.</p></div></div><div className="flex flex-wrap gap-2"><select className="field-control min-w-36" value={notificationType} onChange={(event) => { setNotificationType(event.target.value); setNotificationPage(1) }} aria-label="Filter emails by type"><option value="">All mail types</option><option value="daily">Morning balance</option><option value="threshold">Threshold reminder</option></select><select className="field-control min-w-36" value={notificationStatus} onChange={(event) => { setNotificationStatus(event.target.value); setNotificationPage(1) }} aria-label="Filter emails by status"><option value="">All statuses</option><option value="pending">Pending</option><option value="sending">Sending</option><option value="sent">Sent</option><option value="failed">Failed</option></select></div></div>
          <div className="mt-4 overflow-x-auto"><table className="w-full min-w-[62rem] text-left text-sm"><thead><tr className="border-b border-border text-xs text-ink-faint"><th className="px-2 py-3">Created at (IST)</th><th className="px-2 py-3">Mail</th><th className="px-2 py-3">Subject</th><th className="px-2 py-3">Balance</th><th className="px-2 py-3">Status</th><th className="px-2 py-3">{isAdmin ? 'Technical delivery details' : 'Delivery details'}</th></tr></thead><tbody>{notifications.map((row) => <tr key={row.id} className="border-b border-border/60 align-top"><td className="whitespace-nowrap px-2 py-3 text-ink-dim">{formatIndianDateTime(row.created_at)}</td><td className="px-2 py-3 text-ink">{row.notification_type === 'daily' ? 'Morning balance' : `Below ${formatIndianCurrency(row.threshold_amount ?? 0)}`}</td><td className="max-w-xs px-2 py-3 text-ink-dim">{row.subject}</td><td className="px-2 py-3 font-semibold text-ink">{formatIndianCurrency(row.balance)}</td><td className="px-2 py-3"><StatusBadge status={row.status} /></td><td className="max-w-sm whitespace-pre-wrap break-words px-2 py-3 text-xs leading-5 text-ink-dim">{row.error_message || (row.sent_at ? `Sent ${formatIndianDateTime(row.sent_at)}` : 'Awaiting delivery')}</td></tr>)}</tbody></table>{notifications.length === 0 && <p className="py-8 text-center text-sm text-ink-faint">No emails match these filters.</p>}</div>
          <Pagination className="mt-4" page={notificationPage} pageCount={Math.max(1, Math.ceil(notificationTotal / notificationPageSize))} pageSize={notificationPageSize} totalItems={notificationTotal} itemLabel="emails" pageSizeOptions={[10, 25, 50, 100]} onPageChange={setNotificationPage} onPageSizeChange={(size) => { setNotificationPageSize(size); setNotificationPage(1) }} />
        </GlassCard>
        <p className="text-center text-xs text-ink-faint">History is retained in MySQL under the suite-wide soft-delete policy. Scheduler actions are also mirrored to the central audit log.</p>
      </div>
    </AppShell>
  )
}
