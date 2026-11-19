import { useCallback, useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Copy, Pencil, Plus, Trash2 } from 'lucide-react'
import { GlassCard } from '@/components/GlassCard'
import { Button } from '@/components/Button'
import { ErrorBanner } from '@/components/ErrorBanner'
import { Modal } from '@/components/Modal'
import { LoadingNotice } from '@/components/LoadingNotice'
import { AutoMailCard, describeSchedule, repeatOf, type ScheduleFields } from '@/components/AutoMailCard'
import {
  AsOnChoice, ReportsChoice, asOnShownDate, describeAsOn, shortAsOn, type AsOnMode, type AsOnSetting,
} from '@/components/ScheduleChoices'
import { ReportMailPanel, type MappingFix } from '@/components/ReportMailPanel'
import { ApiError, del, get, patch, post } from '@/lib/api'
import { formatIndianDate } from '@/lib/regional'
import { cn } from '@/utils/cn'

interface ScheduleSummary extends ScheduleFields {
  id: number
  name: string
  enabled: boolean
  next_run: string | null
  running: boolean
  last: { at: string | null; status: string | null; trigger: string | null }
  /** The Debtors Reports only: which of its reports the schedule sends. */
  reports: string[] | null
  /** Only for a report that can be as on a date. */
  as_on: AsOnSetting | null
  to_count: number
  cc_count: number
}

interface ScheduleList {
  schedules: ScheduleSummary[]
  max_schedules: number
  report_labels: Record<string, string> | null
  has_as_on: boolean
  /** Whether this person may create, change and delete schedules (otherwise they may only send by hand). */
  can_manage: boolean
}

function listOf(items: string[]): string {
  return items.length <= 1 ? (items[0] ?? '') : `${items.slice(0, -1).join(', ')} and ${items[items.length - 1]}`
}

/** What a schedule does, in plain words - the same sentence its settings add up to. */
function describeSchedulePlan(schedule: ScheduleSummary, list: ScheduleList, reportName: string): string {
  const labels = list.report_labels
  const what = schedule.reports && labels
    ? listOf(schedule.reports.map((key) => labels[key] ?? key))
    : reportName
  const asOn = schedule.as_on ? `, ${describeAsOn(schedule.as_on)}` : ''
  const people = schedule.to_count + schedule.cc_count
  const who = schedule.to_count === 0
    ? 'No one is set to receive it yet.'
    : `It goes to ${people} ${people === 1 ? 'person' : 'people'}.`
  const when = describeSchedule(schedule, repeatOf(schedule))
  return schedule.enabled
    ? `Mails ${what}${asOn}. ${when} ${who}`
    : `Mails ${what}${asOn}. Automatic mail is switched off, so nothing goes out on its own. When switched on: ${when.charAt(0).toLowerCase()}${when.slice(1)} ${who}`
}

function OnOff({ on }: { on: boolean }) {
  return (
    <span
      className={cn(
        'shrink-0 rounded-full px-2 py-0.5 text-[11px] font-semibold',
        on ? 'bg-emerald-500/15 text-emerald-700' : 'bg-ink-faint/15 text-ink-dim',
      )}
    >
      {on ? 'On' : 'Off'}
    </span>
  )
}

function ScheduleChip({
  schedule,
  list,
  selected,
  onSelect,
}: {
  schedule: ScheduleSummary
  list: ScheduleList
  selected: boolean
  onSelect: () => void
}) {
  const parts: string[] = []
  if (schedule.reports && list.report_labels) {
    parts.push(`${schedule.reports.length} of ${Object.keys(list.report_labels).length} reports`)
  }
  if (schedule.as_on) parts.push(shortAsOn(schedule.as_on))
  if (parts.length === 0) parts.push(schedule.enabled ? 'sends automatically' : 'sends by hand only')
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
        <span className="truncate text-sm font-semibold text-ink">{schedule.name}</span>
        <OnOff on={schedule.enabled} />
      </span>
      <span className="text-xs text-ink-dim">{parts.join(' · ')}</span>
    </button>
  )
}

function NewScheduleDialog({
  open,
  base,
  schedules,
  onClose,
  onCreated,
}: {
  open: boolean
  base: string
  schedules: ScheduleSummary[]
  onClose: () => void
  onCreated: (schedule: ScheduleSummary) => void
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
      onCreated(await post<ScheduleSummary>(`${base}/schedules`, { name, copy_of: copyOf ? Number(copyOf) : null }))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not create the schedule.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal open={open} onClose={onClose} title="New mail schedule">
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
          <span className="text-xs text-ink-faint">Anything that tells the schedules apart at a glance.</span>
        </label>
        {schedules.length > 0 && (
          <label className="flex flex-col gap-1.5 text-sm">
            <span className="font-medium text-ink-dim">Start from</span>
            <select className="field-control" value={copyOf} onChange={(event) => setCopyOf(event.target.value)}>
              <option value="">A blank schedule (you set it up next)</option>
              {schedules.map((schedule) => (
                <option key={schedule.id} value={schedule.id}>
                  A copy of &ldquo;{schedule.name}&rdquo;
                </option>
              ))}
            </select>
            <span className="text-xs text-ink-faint">
              A copy keeps the date, reports, timing, recipients and wording, and starts switched off.
            </span>
          </label>
        )}
        <p className="text-sm leading-6 text-ink-dim">
          The new schedule opens right after, with the date the report is as on, the days and times, the recipients
          and the wording all in one place to set.
        </p>
        {error && <ErrorBanner className="whitespace-pre-wrap">{error}</ErrorBanner>}
        <div className="flex justify-end gap-3">
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" loading={saving} disabled={!name.trim()}>
            Create schedule
          </Button>
        </div>
      </form>
    </Modal>
  )
}

function RenameDialog({
  schedule,
  base,
  onClose,
  onRenamed,
}: {
  schedule: ScheduleSummary | null
  base: string
  onClose: () => void
  onRenamed: () => void
}) {
  const [name, setName] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setName(schedule?.name ?? '')
    setError(null)
  }, [schedule])

  async function save() {
    if (!schedule) return
    setSaving(true)
    setError(null)
    try {
      await patch(`${base}/schedules/${schedule.id}`, { name })
      onRenamed()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not rename the schedule.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal open={schedule !== null} onClose={onClose} title="Rename schedule">
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
  schedule,
  base,
  onClose,
  onDeleted,
}: {
  schedule: ScheduleSummary | null
  base: string
  onClose: () => void
  onDeleted: () => void
}) {
  const [deleting, setDeleting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => setError(null), [schedule])

  async function remove() {
    if (!schedule) return
    setDeleting(true)
    setError(null)
    try {
      await del(`${base}/schedules/${schedule.id}`)
      onDeleted()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not delete the schedule.')
    } finally {
      setDeleting(false)
    }
  }

  return (
    <Modal open={schedule !== null} onClose={onClose} title="Delete this schedule?">
      <div className="flex flex-col gap-4">
        <p className="text-sm leading-6 text-ink-dim">
          &ldquo;{schedule?.name}&rdquo; will stop sending and disappear from this page. This cannot be undone. Other
          schedules are not affected.
        </p>
        {error && <ErrorBanner className="whitespace-pre-wrap">{error}</ErrorBanner>}
        <div className="flex justify-end gap-3">
          <Button variant="ghost" onClick={onClose}>
            Keep it
          </Button>
          <Button variant="danger" loading={deleting} onClick={() => void remove()}>
            Delete schedule
          </Button>
        </div>
      </div>
    </Modal>
  )
}

/**
 * Send this schedule's mail right now: build it, look at exactly what will go out, and send it only when you confirm.
 * It is the one way to send a schedule's mail by hand, so it starts from everything the schedule has saved. The date
 * the report is as on (and, for the Debtors Reports, which reports) can be changed for this one mail, and so can the
 * people, the subject and the text; the schedule keeps its own settings.
 */
function SendNowPanel({
  schedule,
  list,
  base,
  reportName,
  mappingFixes,
  unmappedHint,
  onSent,
}: {
  schedule: ScheduleSummary
  list: ScheduleList
  base: string
  reportName: string
  mappingFixes?: Record<string, MappingFix>
  unmappedHint?: string
  onSent: () => void
}) {
  const savedReports = schedule.reports
  const savedMode: AsOnMode = schedule.as_on?.mode ?? 'today'
  const savedDate = schedule.as_on?.as_on_date ?? ''
  const [reports, setReports] = useState<string[]>(savedReports ?? [])
  const [mode, setMode] = useState<AsOnMode>(savedMode)
  const [date, setDate] = useState(savedDate)

  // the schedule's saved choices changed (a Save above): start from them again
  const savedKey = JSON.stringify([savedReports, savedMode, savedDate])
  useEffect(() => {
    setReports(savedReports ?? [])
    setMode(savedMode)
    setDate(savedDate)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [savedKey])

  const changed =
    (savedReports !== null &&
      (reports.length !== savedReports.length || !reports.every((key) => savedReports.includes(key)))) ||
    (schedule.as_on !== null && (mode !== savedMode || (mode === 'date' && date !== savedDate)))

  const extraBody = useMemo(
    () => ({
      ...(savedReports !== null ? { reports } : {}),
      ...(schedule.as_on !== null ? { as_on_mode: mode, as_on_date: mode === 'date' ? date || null : null } : {}),
    }),
    [savedReports, schedule.as_on, reports, mode, date],
  )

  const labels = list.report_labels
  const reportOptions = labels ? Object.entries(labels).map(([key, label]) => ({ key, label })) : []
  const hasChoices = schedule.as_on !== null || savedReports !== null
  const incomplete = (savedReports !== null && reports.length === 0) || (schedule.as_on !== null && mode === 'date' && !date)

  const controls = hasChoices ? (
    <div className="flex flex-col gap-4 rounded-2xl border border-stroke/70 bg-surface/40 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-ink">What this mail contains</h3>
          <p className="text-xs leading-5 text-ink-dim">
            Starts from what this schedule has saved. Change anything here and only this mail is affected; the
            schedule keeps its own settings.
          </p>
        </div>
        {changed && (
          <Button
            variant="ghost"
            onClick={() => {
              setReports(savedReports ?? [])
              setMode(savedMode)
              setDate(savedDate)
            }}
          >
            Back to the schedule&rsquo;s settings
          </Button>
        )}
      </div>

      {savedReports !== null && (
        <div className="flex flex-col gap-2">
          <span className="text-xs font-medium text-ink-faint">Reports</span>
          <ReportsChoice options={reportOptions} selected={reports} onChange={setReports} />
        </div>
      )}

      {schedule.as_on && (
        <div className="flex flex-col gap-2">
          <span className="text-xs font-medium text-ink-faint">The date the report is as on</span>
          <AsOnChoice
            mode={mode}
            date={date}
            setting={schedule.as_on}
            forThisMail
            onChange={(nextMode, nextDate) => {
              setMode(nextMode)
              setDate(nextDate)
            }}
          />
        </div>
      )}
      {savedReports !== null && reports.length > 0 && schedule.as_on && !incomplete && (
        <p className="text-sm font-semibold text-ink">
          This mail: {listOf(reports.map((key) => labels?.[key] ?? key))}, as on{' '}
          {formatIndianDate(asOnShownDate(mode, date, schedule.as_on))}
          {mode === 'today' ? ' (the ERP as it is now)' : ''}.
        </p>
      )}
    </div>
  ) : undefined

  return (
    <ReportMailPanel
      baseUrl={`${base}/schedules/${schedule.id}`}
      reportName={reportName}
      eyebrow="Send now"
      title="Send this schedule's mail now"
      description="Builds the mail exactly as this schedule would, shows you what will go out, and sends it only when you click Confirm & send. Change the date, the people, the subject or the text for this one mail if you need to; the schedule itself is not affected. The mail is recorded in this schedule's send history."
      unmappedHint={unmappedHint}
      mappingFixes={mappingFixes}
      extraControls={controls}
      extraBody={extraBody}
      blocked={incomplete}
      onChanged={onSent}
      scheduleName={schedule.name}
    />
  )
}

/**
 * One schedule: everything below belongs to it - its name and what it does in a sentence, the settings that make it
 * up (date, reports, timing, recipients, wording, send history), and the way to send it now.
 */
function SchedulePanel({
  schedule,
  list,
  base,
  reportName,
  showUnmapped,
  mappingFixes,
  unmappedHint,
  onChanged,
  onRename,
  onDuplicate,
  onDelete,
  duplicating,
}: {
  schedule: ScheduleSummary
  list: ScheduleList
  base: string
  reportName: string
  showUnmapped: boolean
  mappingFixes?: Record<string, MappingFix>
  unmappedHint?: string
  onChanged: () => void
  onRename: () => void
  onDuplicate: () => void
  onDelete: () => void
  duplicating: boolean
}) {
  const scheduleBase = `${base}/schedules/${schedule.id}`
  // bumped when a mail was sent, so the settings card reloads its last run and send history
  const [refreshKey, setRefreshKey] = useState(0)

  return (
    <div className="flex flex-col gap-6 border-l-4 border-accent/50 pl-3 sm:pl-5">
      <GlassCard padding="lg" className="flex flex-col gap-3">
        <p className="text-xs font-semibold tracking-[0.08em] text-accent uppercase">The schedule you are working on</p>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="flex min-w-0 items-center gap-3">
            <h2 className="truncate font-display text-xl font-semibold tracking-[-0.025em] text-ink">
              {schedule.name}
            </h2>
            <OnOff on={schedule.enabled} />
          </div>
          {list.can_manage && (
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
          )}
        </div>
        <p className="text-sm leading-6 text-ink-dim">{describeSchedulePlan(schedule, list, reportName)}</p>
        {!list.can_manage && (
          <p className="text-xs text-ink-faint">
            Only an administrator changes a schedule. You can send its mail by hand below.
          </p>
        )}
      </GlassCard>

      {list.can_manage && (
        <AutoMailCard
          key={`${schedule.id}-${refreshKey}`}
          baseUrl={scheduleBase}
          reportName={reportName}
          showUnmapped={showUnmapped}
          description="Everything this schedule does is set here and saved with one Save: what the report is as on, when the mail goes out, who receives it and what it says. The report is built when the time arrives, so allow a few minutes before it lands. This schedule's send history is at the bottom of this card."
          onSaved={onChanged}
        />
      )}

      <SendNowPanel
        key={schedule.id}
        schedule={schedule}
        list={list}
        base={base}
        reportName={reportName}
        mappingFixes={mappingFixes}
        unmappedHint={unmappedHint}
        onSent={() => {
          setRefreshKey((key) => key + 1)
          onChanged()
        }}
      />
    </div>
  )
}

/**
 * An application's mail schedules: the list of them, and the one picked (its settings and how to send it now). Each
 * schedule is independent of the others; pick one to see and change it. The page that uses this shows its own
 * introduction above and any tables the report uses below.
 */
export function MailSchedules({
  base,
  reportName,
  showUnmapped = false,
  mappingFixes,
  unmappedHint,
}: {
  /** The application's API base, for example `/tools/unidentified-entries`. */
  base: string
  /** The report in words, for sentences ("the Unidentified Entries"). */
  reportName: string
  /** The "if a location has no mapping" choice, for a report that uses mappings. */
  showUnmapped?: boolean
  mappingFixes?: Record<string, MappingFix>
  unmappedHint?: string
}) {
  const [searchParams, setSearchParams] = useSearchParams()
  const [list, setList] = useState<ScheduleList | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [renaming, setRenaming] = useState<ScheduleSummary | null>(null)
  const [deleting, setDeleting] = useState<ScheduleSummary | null>(null)
  const [duplicating, setDuplicating] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setList(await get<ScheduleList>(`${base}/schedules`))
      setLoadError(null)
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.message : 'Could not load the mail schedules.')
    }
  }, [base])

  useEffect(() => {
    void load()
  }, [load])

  const schedules = list?.schedules ?? []
  const wanted = Number(searchParams.get('schedule'))
  const selected = schedules.find((schedule) => schedule.id === wanted) ?? schedules[0] ?? null

  function select(id: number) {
    setSearchParams({ schedule: String(id) }, { replace: true })
  }

  async function duplicate(schedule: ScheduleSummary) {
    setDuplicating(true)
    setActionError(null)
    try {
      const copy = await post<ScheduleSummary>(`${base}/schedules`, { name: `${schedule.name} (copy)`, copy_of: schedule.id })
      await load()
      select(copy.id)
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : 'Could not duplicate the schedule.')
    } finally {
      setDuplicating(false)
    }
  }

  return (
    <>
      {loadError && <ErrorBanner className="whitespace-pre-wrap">{loadError}</ErrorBanner>}
      {!list && !loadError && <LoadingNotice />}

      {list && (
        <GlassCard padding="lg" className="flex flex-col gap-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="font-display text-lg font-semibold text-ink">Mail schedules</h2>
              <p className="text-sm text-ink-dim">
                {schedules.length === 0
                  ? 'No schedules yet.'
                  : 'Pick a schedule to see and change it. Each schedule is independent of the others.'}
              </p>
            </div>
            {list.can_manage && (
              <Button
                icon={<Plus className="h-4 w-4" />}
                disabled={schedules.length >= list.max_schedules}
                onClick={() => setCreating(true)}
              >
                New schedule
              </Button>
            )}
          </div>
          {schedules.length > 0 && (
            <div className="flex flex-wrap gap-3">
              {schedules.map((schedule) => (
                <ScheduleChip
                  key={schedule.id}
                  schedule={schedule}
                  list={list}
                  selected={schedule.id === selected?.id}
                  onSelect={() => select(schedule.id)}
                />
              ))}
            </div>
          )}
          {schedules.length >= list.max_schedules && (
            <p className="text-xs text-ink-faint">
              At most {list.max_schedules} schedules. Delete one you no longer use to add another.
            </p>
          )}
          {schedules.length === 0 && (
            <p className="text-sm leading-6 text-ink-dim">
              {list.can_manage
                ? 'A schedule is one mail with its own date, timing, recipients and wording. Create your first one.'
                : 'An administrator has to create a schedule before a mail can be sent from here.'}
            </p>
          )}
          {actionError && <ErrorBanner className="whitespace-pre-wrap">{actionError}</ErrorBanner>}
        </GlassCard>
      )}

      {list && selected && (
        <>
          <SchedulePanel
            key={selected.id}
            schedule={selected}
            list={list}
            base={base}
            reportName={reportName}
            showUnmapped={showUnmapped}
            mappingFixes={mappingFixes}
            unmappedHint={unmappedHint}
            onChanged={() => void load()}
            onRename={() => setRenaming(selected)}
            onDuplicate={() => void duplicate(selected)}
            onDelete={() => setDeleting(selected)}
            duplicating={duplicating}
          />
        </>
      )}

      <NewScheduleDialog
        open={creating}
        base={base}
        schedules={schedules}
        onClose={() => setCreating(false)}
        onCreated={(schedule) => {
          setCreating(false)
          void load().then(() => select(schedule.id))
        }}
      />
      <RenameDialog
        schedule={renaming}
        base={base}
        onClose={() => setRenaming(null)}
        onRenamed={() => {
          setRenaming(null)
          void load()
        }}
      />
      <DeleteDialog
        schedule={deleting}
        base={base}
        onClose={() => setDeleting(null)}
        onDeleted={() => {
          setDeleting(null)
          setSearchParams({}, { replace: true })
          void load()
        }}
      />
    </>
  )
}
