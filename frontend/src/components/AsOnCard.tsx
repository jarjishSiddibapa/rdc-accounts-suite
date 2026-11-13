import { useCallback, useEffect, useState } from 'react'
import { CalendarCheck, CheckCircle2 } from 'lucide-react'
import { GlassCard } from '@/components/GlassCard'
import { Button } from '@/components/Button'
import { ErrorBanner } from '@/components/ErrorBanner'
import { LoadingNotice } from '@/components/LoadingNotice'
import { DatePicker } from '@/components/TemporalPicker'
import { ApiError, get, put } from '@/lib/api'
import { formatIndianDate } from '@/lib/regional'
import { segmentClass } from '@/utils/segment'

type Mode = 'date' | 'last_month_end'

interface AsOnSetting {
  mode: Mode
  as_on_date: string | null
  resolved_date: string | null
  problem: string | null
  today: string
  last_month_end: string
}

/**
 * The date the reports are as on: a calendar date, or the last day of last month (worked out at every run).
 * Saved for the application; the automatic mail, "Send now" and the preview all use it.
 */
export function AsOnCard({ baseUrl }: { baseUrl: string }) {
  const [setting, setSetting] = useState<AsOnSetting | null>(null)
  const [mode, setMode] = useState<Mode>('last_month_end')
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
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save the as on date.')
    } finally {
      setSaving(false)
    }
  }

  const unchanged =
    setting !== null && mode === setting.mode && (mode !== 'date' || date === (setting.as_on_date ?? ''))
  const shownDate =
    mode === 'last_month_end' ? setting?.last_month_end : mode === 'date' && date ? date : null

  return (
    <GlassCard padding="lg" className="flex flex-col gap-4">
      <div className="flex items-center gap-3">
        <span className="icon-tile grid h-10 w-10 place-items-center rounded-xl">
          <CalendarCheck className="h-5 w-5" />
        </span>
        <div>
          <h2 className="font-display text-lg font-semibold text-ink">As on date</h2>
          <p className="text-sm leading-6 text-ink-dim">
            Every report, and every figure in the mail, is as it stood on this date. It is used by the automatic
            mail, by Send now and by Mail this report.
          </p>
        </div>
      </div>

      {loading ? (
        <LoadingNotice />
      ) : (
        <>
          <div className="segmented-control self-start">
            <button
              type="button"
              onClick={() => setMode('date')}
              className={segmentClass(mode === 'date')}
              aria-pressed={mode === 'date'}
            >
              A date I pick
            </button>
            <button
              type="button"
              onClick={() => setMode('last_month_end')}
              className={segmentClass(mode === 'last_month_end')}
              aria-pressed={mode === 'last_month_end'}
            >
              Till last month end
            </button>
          </div>

          {mode === 'date' ? (
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
            </label>
          ) : (
            <p className="text-sm leading-6 text-ink-dim">
              Always the last day of the month before the one the mail goes out in, including that day. A mail sent
              today is as on {setting ? formatIndianDate(setting.last_month_end) : ''}.
            </p>
          )}

          {shownDate && (
            <p className="text-sm font-semibold text-ink">Reports will be as on {formatIndianDate(shownDate)}.</p>
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
