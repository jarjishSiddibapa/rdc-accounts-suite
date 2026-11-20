import { CalendarClock } from 'lucide-react'
import { ErrorBanner } from '@/components/ErrorBanner'
import { DatePicker, MonthYearPicker } from '@/components/TemporalPicker'
import { formatIndianDate } from '@/lib/regional'
import { segmentClass } from '@/utils/segment'

export type AsOnMode = 'today' | 'last_month_end' | 'date'

/** The date a schedule's report is as on, as the server describes it. */
export interface AsOnSetting {
  mode: AsOnMode
  as_on_date: string | null
  resolved_date: string | null
  problem: string | null
  today: string
  last_month_end: string
}

export interface ReportOption {
  key: string
  label: string
  selected: boolean
}

const AS_ON_CHOICES: { mode: AsOnMode; label: string }[] = [
  { mode: 'today', label: 'Today' },
  { mode: 'last_month_end', label: 'Last day of last month' },
  { mode: 'date', label: 'A date I pick' },
]

/** The date choice in words, for a schedule's one-line summary. */
export function describeAsOn(setting: Pick<AsOnSetting, 'mode' | 'as_on_date'>): string {
  if (setting.mode === 'today') return 'as on the day it is sent'
  if (setting.mode === 'last_month_end') return 'as on the last day of the month before it is sent'
  return setting.as_on_date ? `as on ${formatIndianDate(setting.as_on_date)}` : 'as on a date (not picked yet)'
}

/** The same, shorter, for a schedule's chip. */
export function shortAsOn(setting: Pick<AsOnSetting, 'mode' | 'as_on_date'>): string {
  if (setting.mode === 'today') return 'as on the day sent'
  if (setting.mode === 'last_month_end') return 'as on last month end'
  return setting.as_on_date ? `as on ${formatIndianDate(setting.as_on_date)}` : 'as on a date'
}

/** The date for the ERP the choice amounts to when the mail goes out now. */
export function asOnShownDate(mode: AsOnMode, date: string, setting: Pick<AsOnSetting, 'today' | 'last_month_end'>): string {
  return mode === 'today' ? setting.today : mode === 'last_month_end' ? setting.last_month_end : date
}

/**
 * The three ways to say what date a report is as on - the day the mail is sent, the last day of the month before it
 * (worked out at every mail), or a calendar date - with the date it comes to written out underneath. Used by a
 * schedule's settings and by the mail sent by hand (for that one mail only).
 */
export function AsOnChoice({
  mode,
  date,
  setting,
  onChange,
  forThisMail = false,
}: {
  mode: AsOnMode
  date: string
  /** Today and the last day of last month, as the server counts them. */
  setting: Pick<AsOnSetting, 'today' | 'last_month_end'> & { problem?: string | null }
  onChange: (mode: AsOnMode, date: string) => void
  /** Worded for one mail sent by hand rather than for every mail of the schedule. */
  forThisMail?: boolean
}) {
  const shown = asOnShownDate(mode, date, setting)
  return (
    <div className="flex flex-col gap-2">
      <div className="segmented-control max-w-full self-start">
        {AS_ON_CHOICES.map((choice) => (
          <button
            key={choice.mode}
            type="button"
            onClick={() => onChange(choice.mode, date)}
            className={segmentClass(mode === choice.mode)}
            aria-pressed={mode === choice.mode}
          >
            {choice.label}
          </button>
        ))}
      </div>

      {mode === 'today' && (
        <p className="text-sm leading-6 text-ink-dim">
          The day the mail is sent. The report is built from the ERP as it is at that moment.
        </p>
      )}
      {mode === 'last_month_end' && (
        <p className="text-sm leading-6 text-ink-dim">
          The last day of the month before the one the mail goes out in, including that day.
          {forThisMail ? '' : ' It is worked out again for every mail, so a mail sent on 3 December is as on 30 November.'}
        </p>
      )}
      {mode === 'date' && (
        <label className="flex max-w-xs flex-col gap-1.5 text-sm">
          <span className="text-xs font-medium text-ink-faint">As on date</span>
          <DatePicker
            value={date}
            max={setting.today}
            onValueChange={(value) => onChange(mode, value)}
            aria-label={forThisMail ? 'As on date for this mail' : 'As on date'}
          />
          {!forThisMail && (
            <span className="text-xs text-ink-faint">
              Every mail of this schedule will be as on this same date until you change it. Use it for a one-off
              pack; for a mail that repeats, choose Today or Last day of last month.
            </span>
          )}
        </label>
      )}

      <p className="text-sm font-semibold text-ink">
        {mode === 'date'
          ? date
            ? `The report will be as on ${formatIndianDate(date)}.`
            : 'Pick the date.'
          : `If sent today, the report will be as on ${formatIndianDate(shown)}.`}
      </p>
      {setting.problem && <ErrorBanner>{setting.problem}</ErrorBanner>}
    </div>
  )
}

// ── the days a report covers (the Sales Register) ─────────────────────────────────────────────────

export type PeriodMode = 'auto' | 'last_month' | 'range'

/** The days a schedule's report covers, as the server describes them (and the limits it enforces). */
export interface PeriodSetting {
  mode: PeriodMode
  from: string | null
  to: string | null
  /** What a run today would cover: one entry per month, each a file of its own. */
  covers: { label: string; from: string; to: string }[]
  problem: string | null
  today: string
  yesterday: string
  last_month_from: string
  last_month_to: string
  /** A picked range may run this many days into the month after its first day's month. */
  extra_days: number
}

type PeriodLimits = Pick<PeriodSetting, 'yesterday' | 'last_month_from' | 'last_month_to' | 'extra_days'> & {
  problem?: string | null
  covers?: PeriodSetting['covers']
}

const PERIOD_CHOICES: { mode: PeriodMode; label: string }[] = [
  { mode: 'auto', label: 'Automatic' },
  { mode: 'last_month', label: 'Last month' },
  { mode: 'range', label: 'A month or days I pick' },
]

function isoDay(date: Date): string {
  return date.toISOString().slice(0, 10)
}

function addDays(iso: string, days: number): string {
  const [year, month, day] = iso.split('-').map(Number)
  return isoDay(new Date(Date.UTC(year, month - 1, day + days)))
}

function lastDayOfMonth(iso: string): string {
  const [year, month] = iso.split('-').map(Number)
  return isoDay(new Date(Date.UTC(year, month, 0)))
}

/** The last day a range starting on ``from`` may end on: the end of that month and ``extra_days`` beyond it, never past yesterday. */
export function periodLatestEnd(from: string, setting: Pick<PeriodLimits, 'yesterday' | 'extra_days'>): string {
  const limit = addDays(lastDayOfMonth(from), setting.extra_days)
  return limit < setting.yesterday ? limit : setting.yesterday
}

/** Why the picked days cannot be reported (the same rules the server enforces), or null when they can. */
export function periodProblem(mode: PeriodMode, from: string, to: string, setting: PeriodLimits): string | null {
  if (mode !== 'range') return null
  if (!from || !to) return 'Pick the first and the last day the report covers.'
  if (to < from) return 'The last day cannot be before the first day.'
  if (from > setting.yesterday || to > setting.yesterday) {
    return `Only completed days can be reported. The last day can be ${formatIndianDate(setting.yesterday)} at the latest.`
  }
  const latest = periodLatestEnd(from, setting)
  if (to > latest) {
    return `A report can cover at most one complete month plus the first ${setting.extra_days} days of the next. From ${formatIndianDate(from)} the last day can be ${formatIndianDate(latest)} at the latest.`
  }
  return null
}

/** The days the choice amounts to when the mail goes out now, written out (empty while a range is incomplete). */
function periodShownDays(mode: PeriodMode, from: string, to: string, setting: PeriodLimits): string {
  if (mode === 'last_month') return `${formatIndianDate(setting.last_month_from)} to ${formatIndianDate(setting.last_month_to)}`
  if (mode === 'range') return from && to ? `${formatIndianDate(from)} to ${formatIndianDate(to)}` : ''
  const covers = setting.covers ?? []
  return covers.length === 0 ? '' : `${formatIndianDate(covers[0].from)} to ${formatIndianDate(covers[covers.length - 1].to)}`
}

/** The period in words, for a schedule's one-line summary. */
export function describePeriod(setting: Pick<PeriodSetting, 'mode' | 'from' | 'to'>): string {
  if (setting.mode === 'last_month') return 'covering the whole of last month'
  if (setting.mode === 'range') {
    return setting.from && setting.to
      ? `covering ${formatIndianDate(setting.from)} to ${formatIndianDate(setting.to)}`
      : 'covering days (not picked yet)'
  }
  return 'covering the month so far'
}

/** The same, shorter, for a schedule's chip. */
export function shortPeriod(setting: Pick<PeriodSetting, 'mode' | 'from' | 'to'>): string {
  if (setting.mode === 'last_month') return 'last month'
  if (setting.mode === 'range') {
    return setting.from && setting.to ? `${formatIndianDate(setting.from)} to ${formatIndianDate(setting.to)}` : 'days not picked'
  }
  return 'month so far'
}

/**
 * The days a report covers: the automatic period (the month so far, plus the whole previous month through the first
 * days of a month), the whole of last month (worked out at every mail), or a month / days the person picks - never
 * more than one complete month plus the first few days of the next. Used by a schedule's settings and by the mail sent
 * by hand (for that one mail only).
 */
export function PeriodChoice({
  mode,
  from,
  to,
  setting,
  onChange,
  forThisMail = false,
}: {
  mode: PeriodMode
  from: string
  to: string
  setting: PeriodLimits
  onChange: (mode: PeriodMode, from: string, to: string) => void
  forThisMail?: boolean
}) {
  const problem = periodProblem(mode, from, to, setting)
  const shown = periodShownDays(mode, from, to, setting)
  const latest = from ? periodLatestEnd(from, setting) : setting.yesterday
  const wholeMonth = from && to && from.endsWith('-01') && from.slice(0, 7) === to.slice(0, 7) ? from.slice(0, 7) : ''
  return (
    <div className="flex flex-col gap-2">
      <div className="segmented-control max-w-full self-start">
        {PERIOD_CHOICES.map((choice) => (
          <button
            key={choice.mode}
            type="button"
            onClick={() =>
              choice.mode === 'range' && !from && !to
                ? onChange('range', setting.last_month_from, setting.last_month_to) // a start: last month
                : onChange(choice.mode, from, to)
            }
            className={segmentClass(mode === choice.mode)}
            aria-pressed={mode === choice.mode}
          >
            {choice.label}
          </button>
        ))}
      </div>

      {mode === 'auto' && (
        <p className="text-sm leading-6 text-ink-dim">
          The month so far, through yesterday. Through the first {setting.extra_days} days of a month the whole of the
          month before is included as well. Each month is a file of its own.
        </p>
      )}
      {mode === 'last_month' && (
        <p className="text-sm leading-6 text-ink-dim">
          The whole of the month before the one the mail goes out in.
          {forThisMail ? '' : ' It is worked out again for every mail, so a mail sent on 3 December covers all of November.'}
        </p>
      )}
      {mode === 'range' && (
        <div className="flex flex-col gap-3">
          <label className="flex max-w-xs flex-col gap-1.5 text-sm">
            <span className="text-xs font-medium text-ink-faint">A whole month</span>
            <MonthYearPicker
              value={wholeMonth}
              max={setting.yesterday.slice(0, 7)}
              onValueChange={(month) => {
                if (!month) return
                const first = `${month}-01`
                const last = lastDayOfMonth(first)
                onChange(mode, first, last < setting.yesterday ? last : setting.yesterday)
              }}
              aria-label={forThisMail ? 'Month for this mail' : 'Month'}
            />
          </label>
          <div className="flex flex-wrap gap-3">
            <label className="flex w-44 flex-col gap-1.5 text-sm">
              <span className="text-xs font-medium text-ink-faint">From</span>
              <DatePicker
                value={from}
                max={setting.yesterday}
                onValueChange={(value) => {
                  // keep the last day inside what the new first day allows
                  const cap = value ? periodLatestEnd(value, setting) : ''
                  onChange(mode, value, value && to && to > cap ? cap : to)
                }}
                aria-label="From"
              />
            </label>
            <label className="flex w-44 flex-col gap-1.5 text-sm">
              <span className="text-xs font-medium text-ink-faint">To</span>
              <DatePicker
                value={to}
                min={from || undefined}
                max={latest}
                onValueChange={(value) => onChange(mode, from, value)}
                aria-label="To"
              />
            </label>
          </div>
          <p className="text-xs text-ink-faint">
            At most one complete month plus the first {setting.extra_days} days of the next
            {from ? `: from ${formatIndianDate(from)} the last day can be ${formatIndianDate(latest)}` : ''}. Each month
            is a file of its own.
            {forThisMail ? '' : ' Every mail of this schedule will cover these same days until you change them; for a mail that repeats, choose Automatic or Last month.'}
          </p>
        </div>
      )}

      <p className="text-sm font-semibold text-ink">
        {mode === 'range'
          ? shown && !problem
            ? `The report will cover ${shown}.`
            : 'Pick the days.'
          : shown
            ? `If sent today, the report will cover ${shown}.`
            : 'There is no completed day to report on yet.'}
      </p>
      {problem && <ErrorBanner>{problem}</ErrorBanner>}
      {setting.problem && !problem && <ErrorBanner>{setting.problem}</ErrorBanner>}
    </div>
  )
}

// ── the month a report is of (the Ultrafine Cashflow) ───────────────────────────────────────────

export type MonthMode = 'current' | 'last' | 'month'

/** The month a schedule's cashflow is of, as the server describes it. Months are written YYYY-MM. */
export interface MonthSetting {
  mode: MonthMode
  /** The month that was picked (kept while another choice is on). */
  value: string | null
  /** The month a mail sent today would be of. */
  resolved: string | null
  problem: string | null
  current: string
  last: string
}

const MONTH_CHOICES: { mode: MonthMode; label: string }[] = [
  { mode: 'current', label: 'This month' },
  { mode: 'last', label: 'Last month' },
  { mode: 'month', label: 'A month I pick' },
]

/** "November 2026" for "2026-11". */
export function monthName(month: string): string {
  const [year, number] = month.split('-').map(Number)
  if (!year || !number) return month
  return new Date(Date.UTC(year, number - 1, 1)).toLocaleDateString('en-IN', {
    month: 'long', year: 'numeric', timeZone: 'UTC',
  })
}

/** The month a choice amounts to when the mail goes out now ('' while a picked month is missing). */
export function monthShown(mode: MonthMode, value: string, setting: Pick<MonthSetting, 'current' | 'last'>): string {
  return mode === 'current' ? setting.current : mode === 'last' ? setting.last : value
}

/** Why the month cannot be mailed (the same rules the server enforces), or null when it can. */
export function monthProblem(mode: MonthMode, value: string, setting: Pick<MonthSetting, 'current'>): string | null {
  if (mode !== 'month') return null
  if (!/^\d{4}-(0[1-9]|1[0-2])$/.test(value)) return 'Pick the month the cashflow is of.'
  if (value > setting.current) return 'The cashflow of a month that has not started yet cannot be mailed.'
  return null
}

/** The month choice in words, for a schedule's one-line summary. */
export function describeMonth(setting: Pick<MonthSetting, 'mode' | 'value'>): string {
  if (setting.mode === 'current') return 'of the month it is sent in'
  if (setting.mode === 'last') return 'of the month before the one it is sent in'
  return setting.value ? `of ${monthName(setting.value)}` : 'of a month (not picked yet)'
}

/** The same, shorter, for a schedule's chip. */
export function shortMonth(setting: Pick<MonthSetting, 'mode' | 'value'>): string {
  if (setting.mode === 'current') return 'this month'
  if (setting.mode === 'last') return 'last month'
  return setting.value ? monthName(setting.value) : 'month not picked'
}

/**
 * The month a cashflow is of: the month the mail goes out in, the month before it (both worked out again at every
 * mail), or a month the person picks. Used by a schedule's settings and by the mail sent by hand (for that one mail
 * only). The cashflow of the month is rebuilt from the stored bank lines each time, before it is mailed.
 */
export function MonthChoice({
  mode,
  value,
  setting,
  onChange,
  forThisMail = false,
}: {
  mode: MonthMode
  value: string
  setting: Pick<MonthSetting, 'current' | 'last' | 'problem'>
  onChange: (mode: MonthMode, value: string) => void
  forThisMail?: boolean
}) {
  const problem = monthProblem(mode, value, setting)
  const shown = monthShown(mode, value, setting)
  return (
    <div className="flex flex-col gap-2">
      <div className="segmented-control max-w-full self-start">
        {MONTH_CHOICES.map((choice) => (
          <button
            key={choice.mode}
            type="button"
            onClick={() => onChange(choice.mode, choice.mode === 'month' && !value ? setting.last : value)}
            className={segmentClass(mode === choice.mode)}
            aria-pressed={mode === choice.mode}
          >
            {choice.label}
          </button>
        ))}
      </div>

      {mode === 'current' && (
        <p className="text-sm leading-6 text-ink-dim">
          The month the mail is sent in, up to the last bank line stored.
          {forThisMail ? '' : ' It is worked out again for every mail, so a mail sent on 3 December is of December.'}
        </p>
      )}
      {mode === 'last' && (
        <p className="text-sm leading-6 text-ink-dim">
          The month before the one the mail goes out in.
          {forThisMail ? '' : ' It is worked out again for every mail, so a mail sent on 3 December is of November.'}
        </p>
      )}
      {mode === 'month' && (
        <label className="flex max-w-xs flex-col gap-1.5 text-sm">
          <span className="text-xs font-medium text-ink-faint">Month</span>
          <MonthYearPicker
            value={value}
            max={setting.current}
            onValueChange={(next) => onChange(mode, next)}
            aria-label={forThisMail ? 'Month for this mail' : 'Month'}
          />
          {!forThisMail && (
            <span className="text-xs text-ink-faint">
              Every mail of this schedule will be of this same month until you change it. Use it for a one-off pack;
              for a mail that repeats, choose This month or Last month.
            </span>
          )}
        </label>
      )}

      <p className="text-sm font-semibold text-ink">
        {mode === 'month' && !shown
          ? 'Pick the month.'
          : mode === 'month'
            ? `The cashflow will be of ${monthName(shown)}.`
            : `If sent today, the cashflow will be of ${monthName(shown)}.`}
      </p>
      {problem && <ErrorBanner>{problem}</ErrorBanner>}
      {setting.problem && !problem && <ErrorBanner>{setting.problem}</ErrorBanner>}
    </div>
  )
}

/** Tick which of a report's parts the mail carries (the Debtors Reports): at least one. */
export function ReportsChoice({
  options,
  selected,
  onChange,
}: {
  options: { key: string; label: string }[]
  selected: string[]
  onChange: (selected: string[]) => void
}) {
  return (
    <div className="flex flex-col gap-2">
      {options.map(({ key, label }) => (
        <label key={key} className="flex items-center gap-3 text-sm font-medium text-ink">
          <input
            type="checkbox"
            className="h-4 w-4 accent-accent"
            checked={selected.includes(key)}
            onChange={(event) =>
              onChange(
                event.target.checked
                  ? options.map((option) => option.key).filter((item) => item === key || selected.includes(item))
                  : selected.filter((item) => item !== key),
              )
            }
          />
          {label}
        </label>
      ))}
      {selected.length === 0 && <ErrorBanner>Pick at least one report to send.</ErrorBanner>}
    </div>
  )
}

/** Says which schedule a card belongs to, so it is never in doubt which one is being changed. */
export function ScheduleTag({ name }: { name: string }) {
  return (
    <span
      className="inline-flex max-w-full items-center gap-1.5 rounded-full border border-accent/30 bg-accent/10 px-3 py-1 text-xs font-semibold text-accent"
      title={`This belongs to the schedule “${name}”`}
    >
      <CalendarClock className="h-3.5 w-3.5 shrink-0" />
      <span className="shrink-0 font-medium text-ink-dim">Schedule:</span>
      <span className="truncate">{name}</span>
    </span>
  )
}
