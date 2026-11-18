import { useCallback, useEffect, useState } from 'react'
import { CheckCircle2, ListChecks } from 'lucide-react'
import { GlassCard } from '@/components/GlassCard'
import { Button } from '@/components/Button'
import { ErrorBanner } from '@/components/ErrorBanner'
import { LoadingNotice } from '@/components/LoadingNotice'
import { ApiError, get, put } from '@/lib/api'

interface ReportOption {
  key: string
  label: string
  selected: boolean
}

interface ReportsSetting {
  reports: ReportOption[]
  selected: string[]
  all_selected: boolean
}

/**
 * Which of the reports go out. Saved for the application; the automatic mail, "Send now" and
 * "Mail this report" all send exactly these (the mail's subject and text follow the choice).
 */
export function ReportsCard({ baseUrl }: { baseUrl: string }) {
  const [setting, setSetting] = useState<ReportsSetting | null>(null)
  const [picked, setPicked] = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const apply = useCallback((value: ReportsSetting) => {
    setSetting(value)
    setPicked(value.selected)
  }, [])

  useEffect(() => {
    let cancelled = false
    get<ReportsSetting>(`${baseUrl}/reports`)
      .then((value) => {
        if (!cancelled) apply(value)
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof ApiError ? err.message : 'Could not load the reports to send.')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [baseUrl, apply])

  function toggle(key: string, on: boolean) {
    setSaved(false)
    setPicked((current) => (on ? [...current, key] : current.filter((item) => item !== key)))
  }

  async function save() {
    setSaving(true)
    setError(null)
    setSaved(false)
    try {
      apply(await put<ReportsSetting>(`${baseUrl}/reports`, { reports: picked }))
      setSaved(true)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save the reports to send.')
    } finally {
      setSaving(false)
    }
  }

  const unchanged =
    setting !== null &&
    picked.length === setting.selected.length &&
    picked.every((key) => setting.selected.includes(key))

  return (
    <GlassCard padding="lg" className="flex flex-col gap-4">
      <div className="flex items-center gap-3">
        <span className="icon-tile grid h-10 w-10 place-items-center rounded-xl">
          <ListChecks className="h-5 w-5" />
        </span>
        <div>
          <h2 className="font-display text-lg font-semibold text-ink">Reports to send</h2>
          <p className="text-sm leading-6 text-ink-dim">
            Pick which reports go out. Only those are built and attached, and the mail&rsquo;s subject, list and
            tables follow your choice. It is used by the automatic mail, by Send now and by Mail this report.
          </p>
        </div>
      </div>

      {loading ? (
        <LoadingNotice />
      ) : (
        <>
          <div className="flex flex-col gap-2.5">
            {setting?.reports.map((report) => (
              <label key={report.key} className="flex items-center gap-3 text-sm font-medium text-ink">
                <input
                  type="checkbox"
                  className="h-4 w-4 accent-accent"
                  checked={picked.includes(report.key)}
                  onChange={(event) => toggle(report.key, event.target.checked)}
                />
                {report.label}
              </label>
            ))}
          </div>

          {picked.length === 0 && <ErrorBanner>Pick at least one report to send.</ErrorBanner>}
          {error && <ErrorBanner className="whitespace-pre-wrap">{error}</ErrorBanner>}

          <div className="flex items-center justify-end gap-3">
            {saved && (
              <span className="flex items-center gap-1.5 text-sm text-emerald-600">
                <CheckCircle2 className="h-4 w-4" />
                Saved
              </span>
            )}
            <Button loading={saving} disabled={unchanged || picked.length === 0} onClick={() => void save()}>
              Save
            </Button>
          </div>
        </>
      )}
    </GlassCard>
  )
}
