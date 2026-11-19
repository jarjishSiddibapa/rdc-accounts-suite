import { useCallback, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Copy, MailCheck, Pencil, Plus, Trash2 } from 'lucide-react'
import { AppShell } from '@/components/AppShell'
import { GlassCard } from '@/components/GlassCard'
import { Button } from '@/components/Button'
import { ErrorBanner } from '@/components/ErrorBanner'
import { Modal } from '@/components/Modal'
import { MappingTable, type MappingColumn, type MappingRow } from '@/components/MappingTable'
import { LoadingNotice } from '@/components/LoadingNotice'
import { AutoMailCard, describeSchedule, repeatOf, type ScheduleFields } from '@/components/AutoMailCard'
import { AsOnCard, describeAsOn, type AsOnSetting } from '@/components/AsOnCard'
import { SendNowCard } from '@/components/SendNowCard'
import { ReportsCard } from '@/components/ReportsCard'
import { ReportMailPanel, type MappingFix } from '@/components/ReportMailPanel'
import { ApiError, del, get, patch, post, put } from '@/lib/api'
import { formatIndianDate } from '@/lib/regional'
import { cn } from '@/utils/cn'
import { segmentClass } from '@/utils/segment'

const BASE = '/tools/debtors-reports-auto-mailer'
const REPORT_NAMES: Record<string, string> = {
  ageing: 'the ageing with GST invoice numbers',
  unapplied: 'the Unapplied Receipts report',
  untagged: 'the Untagged Invoices and Below 1k reports',
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

// ── mail plans ───────────────────────────────────────────────────────────

interface PlanSummary extends ScheduleFields {
  id: number
  name: string
  enabled: boolean
  next_run: string | null
  running: boolean
  last: { at: string | null; status: string | null; trigger: string | null }
  reports: string[]
  as_on: AsOnSetting
  to_count: number
  cc_count: number
}

interface PlanList {
  plans: PlanSummary[]
  max_plans: number
}

function listOf(items: string[]): string {
  return items.length <= 1 ? (items[0] ?? '') : `${items.slice(0, -1).join(', ')} and ${items[items.length - 1]}`
}

function shortAsOn(plan: PlanSummary): string {
  if (plan.as_on.mode === 'today') return 'as on the day sent'
  if (plan.as_on.mode === 'last_month_end') return 'as on last month end'
  return plan.as_on.as_on_date ? `as on ${formatIndianDate(plan.as_on.as_on_date)}` : 'as on a date'
}

/** What the plan does, in plain words - the same sentence the plan's own cards add up to. */
function describePlan(plan: PlanSummary): string {
  const what = `Mails ${listOf(plan.reports.map((key) => REPORT_NAMES[key] ?? key))}, ${describeAsOn(plan.as_on)}`
  const who = plan.to_count === 0 ? 'No one is set to receive it yet.' : `It goes to ${plan.to_count + plan.cc_count} ${plan.to_count + plan.cc_count === 1 ? 'person' : 'people'}.`
  const when = describeSchedule(plan, repeatOf(plan))
  return plan.enabled
    ? `${what}. ${when} ${who}`
    : `${what}. Automatic mail is switched off, so nothing goes out on its own. When switched on: ${when.charAt(0).toLowerCase()}${when.slice(1)} ${who}`
}

function PlanChip({ plan, selected, onSelect }: { plan: PlanSummary; selected: boolean; onSelect: () => void }) {
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      className={cn(
        'flex min-w-44 max-w-64 flex-col items-start gap-1 rounded-2xl border px-4 py-3 text-left transition duration-200',
        selected
          ? 'border-accent bg-accent/10 shadow-[0_10px_24px_-18px_rgba(var(--shadow-rgb),0.6)]'
          : 'border-stroke/70 hover:border-accent/40 hover:bg-surface/70',
      )}
    >
      <span className="flex w-full items-center justify-between gap-3">
        <span className="truncate text-sm font-semibold text-ink">{plan.name}</span>
        <span
          className={cn(
            'shrink-0 rounded-full px-2 py-0.5 text-[11px] font-semibold',
            plan.enabled ? 'bg-emerald-500/15 text-emerald-700' : 'bg-ink-faint/15 text-ink-dim',
          )}
        >
          {plan.enabled ? 'On' : 'Off'}
        </span>
      </span>
      <span className="text-xs text-ink-dim">
        {plan.reports.length} of 3 reports · {shortAsOn(plan)}
      </span>
    </button>
  )
}

function NewPlanDialog({
  open,
  plans,
  onClose,
  onCreated,
}: {
  open: boolean
  plans: PlanSummary[]
  onClose: () => void
  onCreated: (plan: PlanSummary) => void
}) {
  const [name, setName] = useState('')
  const [copyOf, setCopyOf] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (open) {
      setName('')
      setCopyOf('')
      setError(null)
    }
  }, [open])

  async function create() {
    setSaving(true)
    setError(null)
    try {
      onCreated(await post<PlanSummary>(`${BASE}/plans`, { name, copy_of: copyOf ? Number(copyOf) : null }))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not create the plan.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal open={open} onClose={onClose} title="New mail plan">
      <form
        className="flex flex-col gap-4"
        onSubmit={(event) => {
          event.preventDefault()
          void create()
        }}
      >
        <label className="flex flex-col gap-1.5 text-sm">
          <span className="font-medium text-ink-dim">Name</span>
          <input
            className="field-control"
            value={name}
            maxLength={60}
            placeholder="For example: Month-end meeting"
            onChange={(event) => setName(event.target.value)}
          />
          <span className="text-xs text-ink-faint">Anything that tells the plans apart at a glance.</span>
        </label>
        {plans.length > 0 && (
          <label className="flex flex-col gap-1.5 text-sm">
            <span className="font-medium text-ink-dim">Start from</span>
            <select className="field-control" value={copyOf} onChange={(event) => setCopyOf(event.target.value)}>
              <option value="">A blank plan (all three reports, as on the day it is sent)</option>
              {plans.map((plan) => (
                <option key={plan.id} value={plan.id}>
                  A copy of &ldquo;{plan.name}&rdquo;
                </option>
              ))}
            </select>
            <span className="text-xs text-ink-faint">
              A copy keeps the reports, date, schedule, recipients and wording, and starts switched off.
            </span>
          </label>
        )}
        {error && <ErrorBanner className="whitespace-pre-wrap">{error}</ErrorBanner>}
        <div className="flex justify-end gap-3">
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" loading={saving} disabled={!name.trim()}>
            Create plan
          </Button>
        </div>
      </form>
    </Modal>
  )
}

function RenameDialog({
  plan,
  onClose,
  onRenamed,
}: {
  plan: PlanSummary | null
  onClose: () => void
  onRenamed: () => void
}) {
  const [name, setName] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setName(plan?.name ?? '')
    setError(null)
  }, [plan])

  async function save() {
    if (!plan) return
    setSaving(true)
    setError(null)
    try {
      await patch(`${BASE}/plans/${plan.id}`, { name })
      onRenamed()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not rename the plan.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal open={plan !== null} onClose={onClose} title="Rename plan">
      <form
        className="flex flex-col gap-4"
        onSubmit={(event) => {
          event.preventDefault()
          void save()
        }}
      >
        <input className="field-control" value={name} maxLength={60} onChange={(event) => setName(event.target.value)} />
        {error && <ErrorBanner className="whitespace-pre-wrap">{error}</ErrorBanner>}
        <div className="flex justify-end gap-3">
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" loading={saving} disabled={!name.trim()}>
            Rename
          </Button>
        </div>
      </form>
    </Modal>
  )
}

function DeleteDialog({
  plan,
  onClose,
  onDeleted,
}: {
  plan: PlanSummary | null
  onClose: () => void
  onDeleted: () => void
}) {
  const [deleting, setDeleting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => setError(null), [plan])

  async function remove() {
    if (!plan) return
    setDeleting(true)
    setError(null)
    try {
      await del(`${BASE}/plans/${plan.id}`)
      onDeleted()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not delete the plan.')
    } finally {
      setDeleting(false)
    }
  }

  return (
    <Modal open={plan !== null} onClose={onClose} title="Delete this plan?">
      <div className="flex flex-col gap-4">
        <p className="text-sm leading-6 text-ink-dim">
          &ldquo;{plan?.name}&rdquo; will stop sending and disappear from this page. This cannot be undone. Other
          plans are not affected.
        </p>
        {error && <ErrorBanner className="whitespace-pre-wrap">{error}</ErrorBanner>}
        <div className="flex justify-end gap-3">
          <Button variant="ghost" onClick={onClose}>
            Keep it
          </Button>
          <Button variant="danger" loading={deleting} onClick={() => void remove()}>
            Delete plan
          </Button>
        </div>
      </div>
    </Modal>
  )
}

/** One plan: its name and what it does in a sentence, then the steps that set it up, then ways to try it. */
function PlanPanel({
  plan,
  onChanged,
  onRename,
  onDuplicate,
  onDelete,
  duplicating,
}: {
  plan: PlanSummary
  onChanged: () => void
  onRename: () => void
  onDuplicate: () => void
  onDelete: () => void
  duplicating: boolean
}) {
  const base = `${BASE}/plans/${plan.id}`
  // bumped when a run finishes, so the schedule card reloads its last run and send log
  const [refreshKey, setRefreshKey] = useState(0)

  return (
    <div className="flex flex-col gap-6">
      <GlassCard padding="lg" className="flex flex-col gap-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="flex items-center gap-3">
            <h2 className="font-display text-xl font-semibold tracking-[-0.025em] text-ink">{plan.name}</h2>
            <span
              className={cn(
                'rounded-full px-2.5 py-0.5 text-xs font-semibold',
                plan.enabled ? 'bg-emerald-500/15 text-emerald-700' : 'bg-ink-faint/15 text-ink-dim',
              )}
            >
              {plan.enabled ? 'On' : 'Off'}
            </span>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" icon={<Pencil className="h-4 w-4" />} onClick={onRename}>
              Rename
            </Button>
            <Button variant="secondary" icon={<Copy className="h-4 w-4" />} loading={duplicating} onClick={onDuplicate}>
              Duplicate
            </Button>
            <Button variant="ghost" icon={<Trash2 className="h-4 w-4" />} onClick={onDelete}>
              Delete
            </Button>
          </div>
        </div>
        <p className="text-sm leading-6 text-ink-dim">{describePlan(plan)}</p>
      </GlassCard>

      <ReportsCard baseUrl={base} step={1} onSaved={onChanged} />
      <AsOnCard baseUrl={base} step={2} onSaved={onChanged} />
      <AutoMailCard
        key={`${plan.id}-${refreshKey}`}
        baseUrl={base}
        reportName="Debtors Reports"
        step={3}
        description="Switch the automatic mail on, choose the days and times, say who receives it and edit the wording. The reports are built when the time arrives, so allow a few minutes before it lands. The send history of this plan is at the bottom of this card."
        onSaved={onChanged}
      />

      <ReportMailPanel
        baseUrl={base}
        reportName="Debtors Reports"
        description="Try this plan, or send it once outside its schedule: builds the reports above as on the date above, shows you the exact mail, and sends it only when you confirm. It always goes from the system email, and the schedule is not affected."
        unmappedHint='Map each value below and preview again, or preview anyway - they show as "-".'
        mappingFixes={MAIL_MAPPING_FIXES}
      />

      <SendNowCard
        baseUrl={base}
        description="Runs this plan's mail right away with everything saved above (reports, date, recipients, wording), without a preview. It is recorded in this plan's send history like a scheduled mail. To look at the mail first, use the preview above."
        onFinished={() => {
          setRefreshKey((key) => key + 1)
          onChanged()
        }}
      />
    </div>
  )
}

// ── page ─────────────────────────────────────────────────────────────────

export default function DebtorsReportsAutoMailer() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [list, setList] = useState<PlanList | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [renaming, setRenaming] = useState<PlanSummary | null>(null)
  const [deleting, setDeleting] = useState<PlanSummary | null>(null)
  const [duplicating, setDuplicating] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [activeMappingTab, setActiveMappingTab] = useState(0)

  const load = useCallback(async () => {
    try {
      setList(await get<PlanList>(`${BASE}/plans`))
      setLoadError(null)
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.message : 'Could not load the mail plans.')
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const plans = list?.plans ?? []
  const wanted = Number(searchParams.get('plan'))
  const selected = plans.find((plan) => plan.id === wanted) ?? plans[0] ?? null

  function select(id: number) {
    setSearchParams({ plan: String(id) }, { replace: true })
  }

  async function duplicate(plan: PlanSummary) {
    setDuplicating(true)
    setActionError(null)
    try {
      const copy = await post<PlanSummary>(`${BASE}/plans`, { name: `${plan.name} (copy)`, copy_of: plan.id })
      await load()
      select(copy.id)
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : 'Could not duplicate the plan.')
    } finally {
      setDuplicating(false)
    }
  }

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
                Mails the debtors reports: the receivable ageing with the GST invoice numbers, the Unapplied
                Receipts report and the Untagged Invoices report (with Below 1k). Set up as many mail plans as you
                need &mdash; for example a daily mail for the team and a month-end pack for the meeting. Each plan
                has its own reports, as-on date, schedule, recipients, wording and send history. The ageing is
                fetched from the ERP once per mail, and each customer&rsquo;s Location comes from its ERP region,
                with the mapping tables at the bottom as the fallback.
              </p>
            </div>
          </div>
        </GlassCard>

        {loadError && <ErrorBanner className="whitespace-pre-wrap">{loadError}</ErrorBanner>}
        {!list && !loadError && <LoadingNotice />}

        {list && (
          <GlassCard padding="lg" className="flex flex-col gap-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h2 className="font-display text-lg font-semibold text-ink">Mail plans</h2>
                <p className="text-sm text-ink-dim">
                  {plans.length === 0
                    ? 'No plans yet.'
                    : 'Pick a plan to see and change it. Each plan is independent of the others.'}
                </p>
              </div>
              <Button
                icon={<Plus className="h-4 w-4" />}
                disabled={plans.length >= list.max_plans}
                onClick={() => setCreating(true)}
              >
                New plan
              </Button>
            </div>
            {plans.length > 0 && (
              <div className="flex flex-wrap gap-3">
                {plans.map((plan) => (
                  <PlanChip key={plan.id} plan={plan} selected={plan.id === selected?.id} onSelect={() => select(plan.id)} />
                ))}
              </div>
            )}
            {plans.length >= list.max_plans && (
              <p className="text-xs text-ink-faint">At most {list.max_plans} plans. Delete one you no longer use to add another.</p>
            )}
            {plans.length === 0 && (
              <p className="text-sm leading-6 text-ink-dim">
                A plan is one scheduled mail. Create your first one, then work down its three steps: which reports,
                which date, and when and to whom.
              </p>
            )}
            {actionError && <ErrorBanner className="whitespace-pre-wrap">{actionError}</ErrorBanner>}
          </GlassCard>
        )}

        {selected && (
          <PlanPanel
            key={selected.id}
            plan={selected}
            onChanged={() => void load()}
            onRename={() => setRenaming(selected)}
            onDuplicate={() => void duplicate(selected)}
            onDelete={() => setDeleting(selected)}
            duplicating={duplicating}
          />
        )}

        <GlassCard padding="lg" className="flex flex-col gap-5">
          <div>
            <h2 className="font-display text-lg font-semibold text-ink">Mapping tables (shared by every plan)</h2>
            <p className="text-sm text-ink-dim">
              Used for a customer the ERP holds no region for, and for the Accounts Incharge of every Location. If a
              value is missing, a mail is held back (or sent with it shown as -, as set in that plan&rsquo;s
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

      <NewPlanDialog
        open={creating}
        plans={plans}
        onClose={() => setCreating(false)}
        onCreated={(plan) => {
          setCreating(false)
          void load().then(() => select(plan.id))
        }}
      />
      <RenameDialog
        plan={renaming}
        onClose={() => setRenaming(null)}
        onRenamed={() => {
          setRenaming(null)
          void load()
        }}
      />
      <DeleteDialog
        plan={deleting}
        onClose={() => setDeleting(null)}
        onDeleted={() => {
          setDeleting(null)
          setSearchParams({}, { replace: true })
          void load()
        }}
      />
    </AppShell>
  )
}
