import { useCallback, useEffect, useState } from 'react'
import { MailCheck } from 'lucide-react'
import { AppShell } from '@/components/AppShell'
import { GlassCard } from '@/components/GlassCard'
import { ErrorBanner } from '@/components/ErrorBanner'
import { MappingTable, type MappingColumn, type MappingRow } from '@/components/MappingTable'
import { LoadingNotice } from '@/components/LoadingNotice'
import { MailSchedules } from '@/components/MailSchedules'
import { type MappingFix } from '@/components/ReportMailPanel'
import { ApiError, del, get, post, put } from '@/lib/api'
import { segmentClass } from '@/utils/segment'

const BASE = '/tools/debtors-reports-auto-mailer'
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

function MappingSection({ config, base }: { config: MappingConfig; base: string }) {
  const [rows, setRows] = useState<MappingRow[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setRows(await get<MappingRow[]>(`${base}/mappings/${config.key}`))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not load mapping table.')
    } finally {
      setLoading(false)
    }
  }, [base, config.key])

  useEffect(() => {
    void load()
  }, [load])

  async function handleAdd(row: MappingRow) {
    await post(`${base}/mappings/${config.key}`, config.buildBody(row))
    await load()
  }

  async function handleEdit(index: number, row: MappingRow) {
    await put(`${base}/mappings/${config.key}/${config.buildKey(rows[index])}`, config.buildBody(row))
    await load()
  }

  async function handleDelete(index: number) {
    await del(`${base}/mappings/${config.key}/${config.buildKey(rows[index])}`)
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
  const [activeMappingTab, setActiveMappingTab] = useState(0)

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
                Mails the debtors reports: the AR Ageing (with the GST invoice numbers), the AR Unapplied
                Receipts report and the AR Untagged Invoices report (with Below 1k AR Invoices). Set up as many mail schedules as
                you need &mdash; for example a daily mail for the team and a month-end pack for the meeting. Each
                schedule has its own reports, as-on date, timing, recipients, wording and send history, and can
                also be sent by hand with a preview first. The ageing is fetched from the ERP once per mail, and
                each row&rsquo;s Location and Accounts Incharge follow its plant (the mapping tables at the bottom); the
                customer&rsquo;s ERP region is used only when the plant is not mapped or names no place.
              </p>
            </div>
          </div>
        </GlassCard>

        <MailSchedules
          base={BASE}
          reportName="the Debtors Reports"
          showUnmapped
          mappingFixes={MAIL_MAPPING_FIXES}
          unmappedHint='Map each value below and preview again, or preview anyway - they show as "-".'
        />

        <GlassCard padding="lg" className="flex flex-col gap-5">
          <div>
            <h2 className="font-display text-lg font-semibold text-ink">Mapping tables (shared by every schedule)</h2>
            <p className="text-sm text-ink-dim">
              Give each plant its Location, and each Location its Accounts Incharge. If a
              value is missing, a mail is held back (or sent with it shown as -, as set in that schedule&rsquo;s
              automatic mail settings).
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

          <MappingSection key={activeMappingTab} config={MAPPING_CONFIGS[activeMappingTab]} base={BASE} />
        </GlassCard>
      </div>
    </AppShell>
  )
}
