import { useCallback, useEffect, useState } from 'react'
import { CheckCircle2, MailCheck, Send } from 'lucide-react'
import { AppShell } from '@/components/AppShell'
import { GlassCard } from '@/components/GlassCard'
import { Button } from '@/components/Button'
import { ErrorBanner } from '@/components/ErrorBanner'
import { ProgressPanel, type JobState, type JobStatus } from '@/components/ProgressPanel'
import { MappingTable, type MappingColumn, type MappingRow } from '@/components/MappingTable'
import { LoadingNotice } from '@/components/LoadingNotice'
import { AutoMailCard } from '@/components/AutoMailCard'
import { ReportMailPanel, type MappingFix } from '@/components/ReportMailPanel'
import { useAuth } from '@/lib/auth-context'
import { ApiError, del, get, post, put } from '@/lib/api'
import { segmentClass } from '@/utils/segment'

const BASE = '/tools/debtors-reports-auto-mailer'
const REPORT_NAME = 'Debtors Reports'

// ── "send the automatic mail now" ────────────────────────────────────────

interface SendNowResult {
  status: 'sent' | 'held'
  message?: string
  detail?: { recipients?: { to: string[]; cc: string[] }; mails_sent?: number }
}

interface SendNowJob {
  status: JobStatus
  progress: number
  phase: string
  result: SendNowResult | null
  error: string | null
}

async function pollSendNow(jobId: string): Promise<JobState<SendNowResult>> {
  const job = await get<SendNowJob>(`${BASE}/jobs/${jobId}`)
  return {
    status: job.status,
    progress: (job.progress ?? 0) * 100,
    phase: job.phase,
    result: job.result ?? undefined,
    error: job.error ?? undefined,
  }
}

function SendNowCard({ onFinished }: { onFinished: () => void }) {
  const [jobId, setJobId] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<SendNowResult | null>(null)

  async function start() {
    setStarting(true)
    setError(null)
    setResult(null)
    setJobId(null)
    try {
      const res = await post<{ job_id: string }>(`${BASE}/auto-mail/send-now`)
      setJobId(res.job_id)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not start the mail.')
      setStarting(false)
    }
  }

  const sentTo = result?.detail?.recipients?.to ?? []

  return (
    <GlassCard padding="lg" className="flex flex-col gap-4">
      <div className="flex flex-col gap-1">
        <h2 className="font-display text-lg font-semibold text-ink">Send the automatic mail now</h2>
        <p className="text-sm leading-6 text-ink-dim">
          Runs the scheduled mail right away: it builds the three reports and sends them to the saved
          recipients, from the system email, with the saved subject and text. It is recorded in the send
          log below like any scheduled mail. To review the mail first, or to send it from your own email, use
          Mail this report above.
        </p>
      </div>
      <div className="flex justify-stretch sm:justify-end">
        <Button icon={<Send className="h-4 w-4" />} loading={starting} onClick={() => void start()}>
          Send now
        </Button>
      </div>
      {error && <ErrorBanner className="whitespace-pre-wrap">{error}</ErrorBanner>}
      {jobId && (
        <ProgressPanel
          jobId={jobId}
          poller={pollSendNow}
          onDone={(res) => {
            setResult(res ?? null)
            setStarting(false)
            onFinished()
          }}
          onError={(message) => {
            setError(message)
            setStarting(false)
            onFinished()
          }}
          onCancel={() => post(`${BASE}/jobs/${jobId}/cancel`)}
        />
      )}
      {result?.status === 'sent' && (
        <p className="flex items-center gap-2 text-sm text-emerald-600">
          <CheckCircle2 className="h-4 w-4" />
          Mail sent{sentTo.length ? ` to ${sentTo.join(', ')}` : ''}.
        </p>
      )}
      {result?.status === 'held' && (
        <ErrorBanner className="whitespace-pre-wrap">{result.message ?? 'The mail was held back.'}</ErrorBanner>
      )}
    </GlassCard>
  )
}

// ── mapping tables ───────────────────────────────────────────────────────

interface MappingConfig {
  key: string
  title: string
  addLabel: string
  columns: MappingColumn[]
  buildKey: (row: MappingRow) => string
  buildBody: (row: MappingRow) => Record<string, unknown>
}

const MAPPING_CONFIGS: MappingConfig[] = [
  {
    key: 'locations',
    title: 'Location Name',
    addLabel: 'Add location name',
    columns: [
      { key: 'location_name', label: 'Location Name (raw)' },
      { key: 'location', label: 'Location' },
    ],
    buildKey: (row) => encodeURIComponent(row.location_name ?? ''),
    buildBody: (row) => ({ location_name: row.location_name ?? '', location: row.location ?? '' }),
  },
  {
    key: 'accounts-incharge',
    title: 'Accounts Incharge',
    addLabel: 'Add accounts incharge',
    columns: [
      { key: 'location', label: 'Location' },
      { key: 'accounts_incharge', label: 'Accounts Incharge' },
    ],
    buildKey: (row) => encodeURIComponent(row.location ?? ''),
    buildBody: (row) => ({ location: row.location ?? '', accounts_incharge: row.accounts_incharge ?? '' }),
  },
]

// an unmapped value found while building the mail is mapped on the spot, into the same tables
const MAIL_MAPPING_FIXES: Record<string, MappingFix> = {
  'Location Not Mapped': {
    fieldLabel: 'Location',
    mappingKey: 'locations',
    suggestionKey: 'location',
    buildBody: (item, value) => ({ location_name: item, location: value }),
  },
  'Accounts Incharge Not Mapped': {
    fieldLabel: 'Accounts Incharge',
    mappingKey: 'accounts-incharge',
    suggestionKey: 'accounts_incharge',
    buildBody: (item, value) => ({ location: item, accounts_incharge: value }),
  },
}

function MappingSection({ config }: { config: MappingConfig }) {
  const [rows, setRows] = useState<MappingRow[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setRows(await get<MappingRow[]>(`${BASE}/mappings/${config.key}`))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not load mapping table.')
    } finally {
      setLoading(false)
    }
  }, [config.key])

  useEffect(() => {
    void load()
  }, [load])

  async function handleAdd(row: MappingRow) {
    await post(`${BASE}/mappings/${config.key}`, config.buildBody(row))
    await load()
  }

  async function handleEdit(index: number, row: MappingRow) {
    await put(`${BASE}/mappings/${config.key}/${config.buildKey(rows[index])}`, config.buildBody(row))
    await load()
  }

  async function handleDelete(index: number) {
    await del(`${BASE}/mappings/${config.key}/${config.buildKey(rows[index])}`)
    await load()
  }

  return (
    <div className="flex flex-col gap-3">
      {error && <ErrorBanner className="whitespace-pre-wrap">{error}</ErrorBanner>}
      {loading ? (
        <LoadingNotice />
      ) : (
        <MappingTable
          title={config.title}
          addLabel={config.addLabel}
          columns={config.columns}
          rows={rows}
          onAdd={handleAdd}
          onEdit={handleEdit}
          onDelete={handleDelete}
        />
      )}
    </div>
  )
}

// ── page ─────────────────────────────────────────────────────────────────

export default function DebtorsReportsAutoMailer() {
  const { user } = useAuth()
  const isAdmin = user?.role === 'admin'
  const [activeMappingTab, setActiveMappingTab] = useState(0)
  // bumped when a send-now run finishes, so the settings card reloads its last run and send log
  const [refreshKey, setRefreshKey] = useState(0)

  return (
    <AppShell title="Debtors Reports Auto Mailer">
      <div className="flex flex-col gap-6">
        <GlassCard padding="lg" className="flex flex-col gap-4">
          <div className="flex items-center gap-4">
            <span className="icon-tile grid h-12 w-12 place-items-center rounded-xl">
              <MailCheck className="h-5 w-5" />
            </span>
            <div>
              <p className="text-sm font-semibold text-accent">Accounts · Debtors</p>
              <h2 className="mt-1.5 font-display text-xl font-semibold tracking-[-0.025em] text-ink">
                Debtors Reports Auto Mailer
              </h2>
              <p className="mt-1 text-sm leading-6 text-ink-dim">
                One mail with all three debtors reports. The ageing is fetched from the ERP once and shared:
                the receivable ageing with the GST invoice numbers added, the Unapplied Receipts report (its
                summary in the mail) and the Untagged Invoices report (its summaries in the mail), each as an
                attachment. Each customer&apos;s Location comes from its ERP region, with the mapping tables
                below as the fallback.
              </p>
            </div>
          </div>
        </GlassCard>

        <ReportMailPanel
          baseUrl={BASE}
          reportName={REPORT_NAME}
          description="Builds the three reports from the live ERP and shows you the exact mail before anything is sent. It goes out from your own email."
          unmappedHint='Map each value above and preview again, or preview anyway - they show as "-".'
          mappingFixes={MAIL_MAPPING_FIXES}
        />

        {isAdmin && <SendNowCard onFinished={() => setRefreshKey((key) => key + 1)} />}

        {isAdmin && <AutoMailCard key={refreshKey} baseUrl={BASE} reportName={REPORT_NAME} />}

        <GlassCard padding="lg" className="flex flex-col gap-5">
          <div>
            <h2 className="font-display text-lg font-semibold text-ink">Mapping tables</h2>
            <p className="text-sm text-ink-dim">
              Used for a customer the ERP holds no region for, and for the Accounts Incharge of every Location.
              If a value is missing, the mail is held back (or sent with it shown as -, as set in the automatic
              mail settings).
            </p>
          </div>

          <div className="segmented-control">
            {MAPPING_CONFIGS.map((cfg, i) => (
              <button
                key={cfg.key}
                onClick={() => setActiveMappingTab(i)}
                className={segmentClass(activeMappingTab === i)}
              >
                {cfg.title}
              </button>
            ))}
          </div>

          <MappingSection key={activeMappingTab} config={MAPPING_CONFIGS[activeMappingTab]} />
        </GlassCard>
      </div>
    </AppShell>
  )
}
