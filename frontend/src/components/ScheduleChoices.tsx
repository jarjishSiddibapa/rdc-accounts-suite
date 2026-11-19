import { CalendarClock } from 'lucide-react'
import { ErrorBanner } from '@/components/ErrorBanner'
import { DatePicker } from '@/components/TemporalPicker'
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
