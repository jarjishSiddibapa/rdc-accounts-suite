import { useCallback, useEffect, useState } from 'react'
import { CalendarCheck, CheckCircle2 } from 'lucide-react'
import { GlassCard } from '@/components/GlassCard'
import { Button } from '@/components/Button'
import { ErrorBanner } from '@/components/ErrorBanner'
import { LoadingNotice } from '@/components/LoadingNotice'
import { StepBadge } from '@/components/StepBadge'
import { DatePicker } from '@/components/TemporalPicker'
import { ApiError, get, put } from '@/lib/api'
import { formatIndianDate } from '@/lib/regional'
import { segmentClass } from '@/utils/segment'

export type AsOnMode = 'today' | 'last_month_end' | 'date'

export interface AsOnSetting {
  mode: AsOnMode
  as_on_date: string | null
  resolved_date: string | null
  problem: string | null
  today: string
  last_month_end: string
}

/** The date choice in words, for a plan's one-line summary. */
export function describeAsOn(setting: AsOnSetting): string {
  if (setting.mode === 'today') return 'as on the day it is sent'
  if (setting.mode === 'last_month_end') return 'as on the last day of the month before it is sent'
  return setting.as_on_date ? `as on ${formatIndianDate(setting.as_on_date)}` : 'as on a date (not picked yet)'
}

const CHOICES: { mode: AsOnMode; label: string }[] = [
  { mode: 'today', label: 'Today' },
  { mode: 'last_month_end', label: 'Last day of last month' },
  { mode: 'date', label: 'A date I pick' },
]

/**
 * The date a mail plan's reports are as on: the day the mail is sent, the last day of the month before it
 * (worked out at every mail), or a calendar date. Used by the plan's automatic mail, Send now and Mail this
 * report.
 */
export function AsOnCard({
  baseUrl,
  step,
  onSaved,
}: {
  baseUrl: string
  step?: number
  onSaved?: () => void
}) {
  const [setting, setSetting] = useState<AsOnSetting | null>(null)
  const [mode, setMode] = useState<AsOnMode>('today')
  const [date, setDate] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const apply = useCallback((value: AsOnSetting) => {
    setSetting(value)
    setMode(value.mode)
    setDate(value.as_on_date ?? '')
  }, [])

  useEffect(() => {
    let cancelled = false
    get<AsOnSetting>(`${baseUrl}/as-on`)
      .then((value) => {
        if (!cancelled) apply(value)
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof ApiError ? err.message : 'Could not load the as on date.')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [baseUrl, apply])

  async function save() {
    setSaving(true)
    setError(null)
    setSaved(false)
    try {
      apply(await put<AsOnSetting>(`${baseUrl}/as-on`, { mode, as_on_date: mode === 'date' ? date || null : null }))
      setSaved(true)
      onSaved?.()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save the as on date.')
    } finally {
      setSaving(false)
    }
  }

  const unchanged =
    setting !== null && mode === setting.mode && (mode !== 'date' || date === (setting.as_on_date ?? ''))
  const shownDate =
    mode === 'today' ? setting?.today : mode === 'last_month_end' ? setting?.last_month_end : date || null

  return (
    <GlassCard padding="lg" className="flex flex-col gap-4">
      <div className="flex items-center gap-3">
        {step !== undefined ? (
          <StepBadge step={step} small />
        ) : (
          <span className="icon-tile grid h-10 w-10 place-items-center rounded-xl">
            <CalendarCheck className="h-5 w-5" />
          </span>
        )}
        <div>
          <h2 className="font-display text-lg font-semibold text-ink">The date the reports are as on</h2>
          <p className="text-sm leading-6 text-ink-dim">
            Every report, and every figure in the mail, is as it stood on this date.
          </p>
        </div>
      </div>

      {loading ? (
        <LoadingNotice />
      ) : (
        <>
          <div className="segmented-control self-start">
            {CHOICES.map((choice) => (
              <button
                key={choice.mode}
                type="button"
                onClick={() => {
                  setMode(choice.mode)
                  setSaved(false)
                }}
                className={segmentClass(mode === choice.mode)}
                aria-pressed={mode === choice.mode}
              >
                {choice.label}
              </button>
            ))}
          </div>

          {mode === 'today' && (
            <p className="text-sm leading-6 text-ink-dim">
              The day the mail is sent. The reports are built from the ERP as it is at that moment.
            </p>
          )}
          {mode === 'last_month_end' && (
            <p className="text-sm leading-6 text-ink-dim">
              The last day of the month before the one the mail goes out in, including that day. It is worked out
              again for every mail, so a mail sent on 3 December is as on 30 November.
            </p>
          )}
          {mode === 'date' && (
            <label className="flex max-w-xs flex-col gap-1.5 text-sm">
              <span className="text-xs font-medium text-ink-faint">As on date</span>
              <DatePicker
                value={date}
                max={setting?.today}
                onValueChange={(value) => {
                  setDate(value)
                  setSaved(false)
                }}
                aria-label="As on date"
              />
              <span className="text-xs text-ink-faint">
                Every mail of this plan will be as on this same date until you change it. Use it for a one-off
                pack; for a mail that repeats, choose Today or Last day of last month.
              </span>
            </label>
          )}

          {shownDate && (
            <p className="text-sm font-semibold text-ink">
              {mode === 'date'
                ? `Reports will be as on ${formatIndianDate(shownDate)}.`
                : `If sent today, the reports will be as on ${formatIndianDate(shownDate)}.`}
            </p>
          )}
          {setting?.problem && <ErrorBanner>{setting.problem}</ErrorBanner>}
          {error && <ErrorBanner className="whitespace-pre-wrap">{error}</ErrorBanner>}

          <div className="flex items-center justify-end gap-3">
            {saved && (
              <span className="flex items-center gap-1.5 text-sm text-emerald-600">
                <CheckCircle2 className="h-4 w-4" />
                Saved
              </span>
            )}
            <Button loading={saving} disabled={unchanged || (mode === 'date' && !date)} onClick={() => void save()}>
              Save
            </Button>
          </div>
        </>
      )}
    </GlassCard>
  )
}
