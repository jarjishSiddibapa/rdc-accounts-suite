import { useCallback, useEffect, useMemo, useState } from 'react'
import { Pencil, Plus, Archive } from 'lucide-react'
import { GlassCard } from '@/components/GlassCard'
import { Button } from '@/components/Button'
import { ErrorBanner } from '@/components/ErrorBanner'
import { LoadingNotice } from '@/components/LoadingNotice'
import { CreatableCombobox } from '@/components/CreatableCombobox'
import { MappingTable, type MappingColumn, type MappingRow } from '@/components/MappingTable'
import { Modal } from '@/components/Modal'
import { ApiError, del, get, post, put } from '@/lib/api'
import { MonthYearPicker } from '@/components/TemporalPicker'
import { cn } from '@/utils/cn'
import { BASE, monthLabel, type MappingOptions } from './shared'

type ApiRow = Record<string, unknown>

interface CrudConfig {
  path: string
  title: string
  addLabel: string
  columns: MappingColumn[]
  toRow?: (row: ApiRow) => MappingRow
  toBody: (row: MappingRow) => Record<string, unknown>
  suggestions?: (options: MappingOptions) => Record<string, string[]>
}

function plainRow(row: ApiRow): MappingRow {
  return Object.fromEntries(Object.entries(row).map(([key, value]) => [key, value == null ? '' : String(value)]))
}

const optionalNumber = (value: string | undefined) => (value && value.trim() !== '' ? Number(value) : null)

export const CRUD: Record<string, CrudConfig> = {
  aliases: {
    path: 'aliases',
    title: 'Bank description → name',
    addLabel: 'Add description',
    columns: [
      { key: 'desc_key', label: 'Bank description (numbers and codes removed)' },
      { key: 'party', label: 'Name' },
    ],
    toBody: (row) => ({ desc_key: row.desc_key ?? '', party: row.party ?? '' }),
    suggestions: (o) => ({ party: o.parties }),
  },
  parties: {
    path: 'parties',
    title: 'Names',
    addLabel: 'Add name',
    columns: [
      { key: 'name', label: 'Name' },
      { key: 'location', label: 'Location' },
      { key: 'cf_location', label: 'CF location' },
      { key: 'sharing_group', label: 'Sharing group (instead of a location)' },
    ],
    toBody: (row) => ({
      name: row.name ?? '',
      location: row.location || null,
      cf_location: row.cf_location || null,
      sharing_group: row.sharing_group || null,
    }),
    suggestions: (o) => ({ location: o.locations, cf_location: o.cf_locations, sharing_group: o.sharing_groups }),
  },
  'cf-locations': {
    path: 'cf-locations',
    title: 'CF locations and Summary settings',
    addLabel: 'Add CF location',
    columns: [
      { key: 'name', label: 'CF location' },
      { key: 'sort_order', label: 'Order' },
      { key: 'show_in_summary', label: 'Shown in Summary (Yes/No)' },
      { key: 'authorized_person', label: 'Authorized person' },
      { key: 'payable_pct', label: 'Payable % of collection' },
      { key: 'reserve_pct', label: 'Reserve % of collection' },
      { key: 'group', label: 'Subtotal group' },
    ],
    toRow: (row) => ({ ...plainRow(row), show_in_summary: row.show_in_summary ? 'Yes' : 'No' }),
    toBody: (row) => ({
      name: row.name ?? '',
      sort_order: Number(row.sort_order || 0),
      show_in_summary: (row.show_in_summary ?? 'Yes').trim().toLowerCase() !== 'no',
      authorized_person: row.authorized_person ?? '',
      payable_pct: Number(row.payable_pct || 100),
      reserve_pct: optionalNumber(row.reserve_pct),
      group: row.group ?? '',
    }),
    suggestions: (o) => ({ group: o.summary_groups, show_in_summary: ['Yes', 'No'] }),
  },
  'summary-groups': {
    path: 'summary-groups',
    title: 'Summary subtotal groups',
    addLabel: 'Add group',
    columns: [{ key: 'name', label: 'Group (prints a “Total …” row)' }],
    toBody: (row) => ({ name: row.name ?? '' }),
  },
}

export function CrudSection({
  config,
  options,
  onChanged,
}: {
  config: CrudConfig
  options: MappingOptions
  onChanged: () => void
}) {
  const [rows, setRows] = useState<MappingRow[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setError(null)
    try {
      const data = await get<ApiRow[]>(`${BASE}/mappings/${config.path}`)
      setRows(data.map((row) => (config.toRow ?? plainRow)(row)))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not load this table.')
    } finally {
      setLoading(false)
    }
  }, [config])

  useEffect(() => {
    setLoading(true)
    void load()
  }, [load])

  async function afterChange() {
    await load()
    onChanged()
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
          suggestions={config.suggestions?.(options)}
          onAdd={async (row) => {
            await post(`${BASE}/mappings/${config.path}`, config.toBody(row))
            await afterChange()
          }}
          onEdit={async (index, row) => {
            await put(`${BASE}/mappings/${config.path}/${rows[index].id}`, config.toBody(row))
            await afterChange()
          }}
          onDelete={async (index) => {
            await del(`${BASE}/mappings/${config.path}/${rows[index].id}`)
            await afterChange()
          }}
        />
      )}
    </div>
  )
}

// ── sharing ratio sets ────────────────────────────────────────────────────

interface RatioLine {
  location: string
  ratio: number
  cf_location: string
}

interface RatioSet {
  id: number
  group: string
  effective_from: string
  lines: RatioLine[]
  total: number
}

interface LineDraft {
  location: string
  percent: string
  cf_location: string
}

const percentText = (ratio: number) => String(Number((ratio * 100).toFixed(10)))

function RatioSetEditor({
  editing,
  sets,
  options,
  onClose,
  onSaved,
}: {
  editing: RatioSet | 'new' | null
  sets: RatioSet[]
  options: MappingOptions
  onClose: () => void
  onSaved: () => void
}) {
  const [group, setGroup] = useState('')
  const [effective, setEffective] = useState('')
  const [lines, setLines] = useState<LineDraft[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (editing === null) return
    setError(null)
    if (editing === 'new') {
      setGroup('')
      setEffective('')
      setLines([{ location: '', percent: '', cf_location: '' }])
    } else {
      setGroup(editing.group)
      setEffective(editing.effective_from.slice(0, 7))
      setLines(
        editing.lines.map((l) => ({ location: l.location, percent: percentText(l.ratio), cf_location: l.cf_location })),
      )
    }
  }, [editing])

  const total = lines.reduce((sum, l) => sum + (Number.parseFloat(l.percent) || 0), 0)
  const totalOk = Math.abs(total - 100) < 0.0001

  function copyLatest() {
    const latest = sets.filter((s) => s.group.toLowerCase() === group.trim().toLowerCase()).at(-1)
    if (latest) {
      setLines(
        latest.lines.map((l) => ({ location: l.location, percent: percentText(l.ratio), cf_location: l.cf_location })),
      )
    }
  }

  async function save() {
    if (editing === null) return
    setBusy(true)
    setError(null)
    try {
      const body = {
        group,
        effective_from: `${effective}-01T00:00`,
        lines: lines.map((l) => ({
          location: l.location,
          ratio: (Number.parseFloat(l.percent) || 0) / 100,
          cf_location: l.cf_location || null,
        })),
      }
      if (editing === 'new') await post(`${BASE}/mappings/ratio-sets`, body)
      else await put(`${BASE}/mappings/ratio-sets/${editing.id}`, body)
      onSaved()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save the ratio set.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      open={editing !== null}
      onClose={onClose}
      title={editing === 'new' ? 'Add sharing ratios' : 'Edit sharing ratios'}
      className="max-w-3xl"
    >
      <div className="flex flex-col gap-4">
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="flex flex-col gap-1.5 text-sm">
            <span className="font-medium text-ink-dim">Sharing group</span>
            <CreatableCombobox
              value={group}
              options={options.sharing_groups}
              onChange={setGroup}
              suggestionLabel="Sharing groups"
              placeholder="RDC, IOCL, ETC Pool …"
            />
          </label>
          <label className="flex flex-col gap-1.5 text-sm">
            <span className="font-medium text-ink-dim">Month</span>
            <MonthYearPicker value={effective} onValueChange={setEffective} aria-label="Month" />
          </label>
        </div>
        <p className="text-xs text-ink-faint">
          Like the Working sheet: a particular and its percentage. A particular that is itself a CF location
          (Main Operation) needs no CF location; "Main" or "Aggregate" does. These ratios are used for the
          month chosen and for every later month until you add a newer set, so earlier months keep theirs.
        </p>

        <div className="flex flex-col gap-2">
          {lines.map((line, index) => (
            <div key={index} className="flex flex-col gap-2 sm:flex-row sm:items-center">
              <input
                className="field-control w-full sm:flex-1"
                placeholder="Particular (e.g. Main Operation, or Main)"
                aria-label={`Particular of line ${index + 1}`}
                value={line.location}
                onChange={(e) => setLines((p) => p.map((l, i) => (i === index ? { ...l, location: e.target.value } : l)))}
              />
              <input
                className="field-control w-full text-right sm:w-32"
                inputMode="decimal"
                placeholder="%"
                aria-label={`Percentage of line ${index + 1}`}
                value={line.percent}
                onChange={(e) =>
                  setLines((p) => p.map((l, i) => (i === index ? { ...l, percent: e.target.value } : l)))
                }
              />
              <CreatableCombobox
                value={line.cf_location}
                options={options.cf_locations}
                placeholder="CF location (blank if the particular is one)"
                ariaLabel={`CF location of line ${index + 1}`}
                onChange={(cf_location) =>
                  setLines((p) => p.map((l, i) => (i === index ? { ...l, cf_location } : l)))
                }
                className="sm:flex-1"
              />
              <button
                type="button"
                aria-label={`Remove line ${index + 1}`}
                className="grid h-11 w-11 shrink-0 place-items-center rounded-full text-ink-dim hover:bg-bg-soft hover:text-red-500"
                onClick={() => setLines((p) => p.filter((_, i) => i !== index))}
              >
                <Archive className="h-4 w-4" />
              </button>
            </div>
          ))}
          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="ghost"
              className="min-h-9 px-3 py-1.5"
              icon={<Plus className="h-4 w-4" />}
              onClick={() => setLines((p) => [...p, { location: '', percent: '', cf_location: '' }])}
            >
              Add line
            </Button>
            {editing === 'new' && (
              <Button variant="ghost" className="min-h-9 px-3 py-1.5" onClick={copyLatest} disabled={!group.trim()}>
                Start from this group’s latest set
              </Button>
            )}
            <span className={cn('ml-auto text-sm', totalOk ? 'text-emerald-500' : 'text-amber-600')}>
              Total {Number(total.toFixed(8))}% {totalOk ? '' : '— must be 100%'}
            </span>
          </div>
        </div>

        {error && <ErrorBanner className="whitespace-pre-wrap">{error}</ErrorBanner>}
        <div className="flex flex-col-reverse gap-3 sm:flex-row sm:justify-end">
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button loading={busy} disabled={!totalOk || !group.trim() || !effective} onClick={() => void save()}>
            Save
          </Button>
        </div>
      </div>
    </Modal>
  )
}

function RatioSetsSection({ options, onChanged }: { options: MappingOptions; onChanged: () => void }) {
  const [sets, setSets] = useState<RatioSet[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [editing, setEditing] = useState<RatioSet | 'new' | null>(null)
  const [archiving, setArchiving] = useState<RatioSet | null>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    try {
      setSets(await get<RatioSet[]>(`${BASE}/mappings/ratio-sets`))
      setError(null)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not load the sharing ratios.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const byGroup = useMemo(() => {
    const groups = new Map<string, RatioSet[]>()
    sets.forEach((s) => groups.set(s.group, [...(groups.get(s.group) ?? []), s]))
    return [...groups.entries()]
  }, [sets])

  async function archive() {
    if (!archiving) return
    setBusy(true)
    try {
      await del(`${BASE}/mappings/ratio-sets/${archiving.id}`)
      setArchiving(null)
      await load()
      onChanged()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not archive the ratio set.')
      setArchiving(null)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h3 className="font-display text-base font-semibold text-ink">Sharing ratios</h3>
          <p className="text-sm text-ink-dim">
            Money from a name in a sharing group (RDC, IOCL, ETC Pool) is split by these percentages.
          </p>
        </div>
        <Button icon={<Plus className="h-4 w-4" />} onClick={() => setEditing('new')}>
          Add sharing ratios
        </Button>
      </div>
      {error && <ErrorBanner>{error}</ErrorBanner>}
      {loading ? (
        <LoadingNotice />
      ) : (
        byGroup.map(([group, groupSets]) => (
          <div key={group} className="flex flex-col gap-3">
            <h4 className="font-display text-sm font-semibold text-accent">{group}</h4>
            {[...groupSets].reverse().map((set) => (
              <div key={set.id} className="subpanel flex flex-col gap-2 p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-medium text-ink">
                    {monthLabel(set.effective_from.slice(0, 7))} onwards
                  </span>
                  <div className="ml-auto flex gap-1">
                    <button
                      type="button"
                      aria-label="Edit ratios"
                      onClick={() => setEditing(set)}
                      className="grid h-11 w-11 place-items-center rounded-full text-ink-dim hover:bg-bg-soft hover:text-accent sm:h-8 sm:w-8"
                    >
                      <Pencil className="h-4 w-4" />
                    </button>
                    <button
                      type="button"
                      aria-label="Archive ratios"
                      onClick={() => setArchiving(set)}
                      className="grid h-11 w-11 place-items-center rounded-full text-ink-dim hover:bg-bg-soft hover:text-red-500 sm:h-8 sm:w-8"
                    >
                      <Archive className="h-4 w-4" />
                    </button>
                  </div>
                </div>
                <div className="flex flex-wrap gap-x-6 gap-y-1 text-sm text-ink">
                  {set.lines.map((line) => (
                    <span key={line.location}>
                      {line.location}
                      {line.cf_location && line.cf_location !== line.location ? ` → ${line.cf_location}` : ''}:{' '}
                      <strong>{Number((line.ratio * 100).toFixed(4))}%</strong>
                    </span>
                  ))}
                </div>
              </div>
            ))}
          </div>
        ))
      )}
      {!loading && sets.length === 0 && <p className="text-sm text-ink-faint">No sharing ratios yet.</p>}

      <RatioSetEditor
        editing={editing}
        sets={sets}
        options={options}
        onClose={() => setEditing(null)}
        onSaved={() => {
          setEditing(null)
          void load()
          onChanged()
        }}
      />
      <Modal open={archiving !== null} onClose={() => setArchiving(null)} title="Archive sharing ratios">
        <p className="mb-6 text-sm text-ink-dim">
          Archive this ratio set? Transactions in its period will use the previous set (or be flagged if there
          is none). It stays in the database.
        </p>
        <div className="flex flex-col-reverse gap-3 sm:flex-row sm:justify-end">
          <Button variant="secondary" onClick={() => setArchiving(null)}>
            Cancel
          </Button>
          <Button variant="danger" loading={busy} onClick={() => void archive()}>
            Archive
          </Button>
        </div>
      </Modal>
    </div>
  )
}

// ── panel ────────────────────────────────────────────────────────────────

const TABS: { key: string; label: string }[] = [
  { key: 'aliases', label: 'Bank descriptions → name' },
  { key: 'parties', label: 'Name → location and CF location' },
  { key: 'sharing', label: 'Sharing ratios' },
]

/** CF locations as the Summary lists them (order, authorized person, payable %). */
export function SummarySettings({ options, onChanged }: { options: MappingOptions; onChanged: () => void }) {
  return (
    <div className="flex flex-col gap-6">
      <CrudSection config={CRUD['cf-locations']} options={options} onChanged={onChanged} />
      <CrudSection config={CRUD['summary-groups']} options={options} onChanged={onChanged} />
    </div>
  )
}

export function MappingsPanel({
  options,
  onChanged,
}: {
  options: MappingOptions
  onChanged: () => void
}) {
  const [tab, setTab] = useState('aliases')

  return (
    <GlassCard padding="lg" className="flex flex-col gap-5">
      <div>
        <h2 className="font-display text-lg font-semibold text-ink">Mappings</h2>
        <p className="text-sm text-ink-dim">
          A bank description gives the name; the name gives its location and CF location. Names in RDC, IOCL
          or ETC Pool are split by the sharing ratios instead. Change one row and every transaction follows.
        </p>
      </div>
      <div className="segmented-control">
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={cn(
              'rounded-xl px-4 py-2 text-sm font-semibold transition duration-200',
              tab === t.key
                ? 'bg-accent text-white shadow-[0_8px_18px_-12px_color-mix(in_oklab,var(--color-accent)_75%,transparent)] dark:bg-accent-2'
                : 'border border-transparent text-ink-dim hover:bg-surface/70 hover:text-ink',
            )}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === 'sharing' ? (
        <RatioSetsSection options={options} onChanged={onChanged} />
      ) : (
        <CrudSection key={tab} config={CRUD[tab]} options={options} onChanged={onChanged} />
      )}
    </GlassCard>
  )
}
