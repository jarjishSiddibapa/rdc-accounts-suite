import { useEffect, useState } from 'react'
import { DatabaseBackup, Download, HardDriveDownload, PlayCircle } from 'lucide-react'
import { AppShell } from '@/components/AppShell'
import { GlassCard } from '@/components/GlassCard'
import { Button } from '@/components/Button'
import { ApiError, apiUrl, get, put, post } from '@/lib/api'
import { formatIndianDate, formatIndianNumber } from '@/lib/regional'

interface BackupSettings {
  enabled: boolean
  backup_time: string
  max_backups: number
  scratch_cleanup_minutes: number
}

interface BackupFile {
  filename: string
  size_bytes: number
  created_at: string
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  const kb = bytes / 1024
  if (kb < 1024) return `${formatIndianNumber(Math.round(kb))} KB`
  const mb = kb / 1024
  if (mb < 1024) return `${(Math.round(mb * 10) / 10).toLocaleString('en-IN')} MB`
  const gb = mb / 1024
  return `${(Math.round(gb * 100) / 100).toLocaleString('en-IN')} GB`
}

export default function SystemMaintenance() {
  const [settings, setSettings] = useState<BackupSettings | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [runningNow, setRunningNow] = useState(false)
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null)
  const [backups, setBackups] = useState<BackupFile[]>([])

  async function loadAll() {
    setLoading(true)
    try {
      const [s, b] = await Promise.all([
        get<BackupSettings>('/admin/backup-settings'),
        get<BackupFile[]>('/admin/backups'),
      ])
      setSettings(s)
      setBackups(b)
    } catch (err) {
      setMessage({ ok: false, text: err instanceof ApiError ? err.message : 'Failed to load backup settings.' })
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void loadAll()
  }, [])

  async function handleSave() {
    if (!settings) return
    setSaving(true)
    setMessage(null)
    try {
      const updated = await put<BackupSettings>('/admin/backup-settings', settings)
      setSettings(updated)
      setMessage({ ok: true, text: 'Settings saved.' })
    } catch (err) {
      setMessage({ ok: false, text: err instanceof ApiError ? err.message : 'Failed to save settings.' })
    } finally {
      setSaving(false)
    }
  }

  async function handleRunNow() {
    setRunningNow(true)
    setMessage(null)
    try {
      const res = await post<{ ok: boolean; message: string }>('/admin/backup-settings/run-now')
      setMessage({ ok: res.ok, text: res.ok ? 'Backup completed.' : res.message })
      await loadAll()
    } catch (err) {
      setMessage({ ok: false, text: err instanceof ApiError ? err.message : 'Backup failed.' })
    } finally {
      setRunningNow(false)
    }
  }

  function handleDownload(filename: string) {
    const a = document.createElement('a')
    a.href = apiUrl(`/admin/backups/${encodeURIComponent(filename)}/download`)
    a.download = filename
    document.body.appendChild(a)
    a.click()
    a.remove()
  }

  return (
    <AppShell title="System maintenance">
      <div className="flex flex-col gap-6">
        <GlassCard padding="lg" className="overflow-hidden">
          <div className="flex flex-col justify-between gap-5 md:flex-row md:items-center">
            <div className="max-w-2xl">
              <p className="text-sm font-semibold text-accent">Operations</p>
              <h2 className="mt-1.5 font-display text-2xl font-semibold tracking-[-0.035em] text-ink">
                Database backups &amp; scratch cleanup
              </h2>
              <p className="mt-2 text-sm leading-6 text-ink-dim">
                Every backup is a complete mysqldump with table structure, data, routines, triggers, and
                events in one .sql file. Restoring anywhere is a single import with no other setup needed.
              </p>
            </div>
            <div className="grid grid-cols-2 gap-2 sm:min-w-72">
              <div className="subpanel flex items-center gap-3 p-3">
                <span className="grid h-9 w-9 place-items-center rounded-xl bg-accent/10 text-accent">
                  <DatabaseBackup className="h-4 w-4" />
                </span>
                <span className="text-xs font-semibold text-ink">
                  {formatIndianNumber(backups.length)} backup{backups.length === 1 ? '' : 's'} stored
                </span>
              </div>
              <div className="subpanel flex items-center gap-3 p-3">
                <span className="grid h-9 w-9 place-items-center rounded-xl bg-emerald-500/10 text-emerald-600">
                  <HardDriveDownload className="h-4 w-4" />
                </span>
                <span className="text-xs font-semibold text-ink">Scratch auto-cleanup</span>
              </div>
            </div>
          </div>
        </GlassCard>

        <GlassCard padding="lg">
          <h3 className="font-display text-lg font-semibold text-ink">Backup schedule</h3>
          <p className="mt-1 text-sm text-ink-dim">
            Runs once a day at the time below (IST). Older backups beyond the limit are deleted
            automatically after each run.
          </p>

          {loading || !settings ? (
            <p className="mt-4 text-sm text-ink-faint">Loading…</p>
          ) : (
            <div className="mt-5 flex flex-col gap-4">
              <label className="flex items-center gap-3 text-sm">
                <input
                  type="checkbox"
                  checked={settings.enabled}
                  onChange={(e) => setSettings({ ...settings, enabled: e.target.checked })}
                  className="h-4 w-4 accent-accent"
                />
                <span className="font-medium text-ink-dim">Automatic daily backups enabled</span>
              </label>

              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <label className="flex flex-col gap-1.5 text-sm">
                  <span className="font-medium text-ink-dim">Backup time (24h, IST)</span>
                  <input
                    type="time"
                    value={settings.backup_time}
                    onChange={(e) => setSettings({ ...settings, backup_time: e.target.value })}
                    className="field-control"
                  />
                </label>
                <label className="flex flex-col gap-1.5 text-sm">
                  <span className="font-medium text-ink-dim">Backups to keep</span>
                  <input
                    type="number"
                    min={1}
                    max={365}
                    value={settings.max_backups}
                    onChange={(e) => setSettings({ ...settings, max_backups: Number(e.target.value) })}
                    className="field-control"
                  />
                  <span className="text-xs text-ink-faint">The oldest backups beyond this count are deleted after each run.</span>
                </label>
              </div>

              {message && (
                <p className={message.ok ? 'text-sm text-emerald-600' : 'text-sm text-red-500'}>
                  {message.text}
                </p>
              )}

              <div className="flex flex-col-reverse gap-3 sm:flex-row sm:justify-end">
                <Button
                  variant="secondary"
                  icon={<PlayCircle className="h-4 w-4" />}
                  loading={runningNow}
                  onClick={() => void handleRunNow()}
                >
                  Run backup now
                </Button>
                <Button loading={saving} onClick={() => void handleSave()}>
                  Save schedule
                </Button>
              </div>
            </div>
          )}
        </GlassCard>

        <GlassCard padding="lg">
          <h3 className="font-display text-lg font-semibold text-ink">Scratch file cleanup</h3>
          <p className="mt-1 text-sm text-ink-dim">
            Every tool saves uploaded files and generated reports to a scratch folder while a job runs.
            Anything older than this, across every tool, is deleted automatically as a safety net.
            even if a job errored or its output was never downloaded.
          </p>
          {!loading && settings && (
            <div className="mt-5 flex flex-col gap-4">
              <label className="flex max-w-xs flex-col gap-1.5 text-sm">
                <span className="font-medium text-ink-dim">Delete scratch files older than (minutes)</span>
                <input
                  type="number"
                  min={5}
                  max={43_200}
                  value={settings.scratch_cleanup_minutes}
                  onChange={(e) => setSettings({ ...settings, scratch_cleanup_minutes: Number(e.target.value) })}
                  className="field-control"
                />
                <span className="text-xs text-ink-faint">
                  5 minutes to 43,200 minutes (30 days). For example, use 30 minutes to match the app's own auto-logout
                  time. Checked every 5 minutes.
                </span>
              </label>
              <div className="flex justify-end">
                <Button loading={saving} onClick={() => void handleSave()}>
                  Save
                </Button>
              </div>
            </div>
          )}
        </GlassCard>

        <GlassCard padding="lg">
          <h3 className="font-display text-lg font-semibold text-ink">Stored backups</h3>
          {backups.length === 0 ? (
            <p className="mt-4 text-sm text-ink-faint">No backups yet. Run one now, or wait for the schedule.</p>
          ) : (
            <div className="mt-4 flex flex-col gap-2">
              {backups.map((b) => (
                <div
                  key={b.filename}
                  className="subpanel flex flex-col gap-2 px-3.5 py-3 text-sm sm:flex-row sm:items-center sm:justify-between"
                >
                  <div className="min-w-0">
                    <p className="truncate font-medium text-ink">{b.filename}</p>
                    <p className="mt-0.5 text-xs text-ink-faint">
                      {formatIndianDate(b.created_at)} | {formatBytes(b.size_bytes)}
                    </p>
                  </div>
                  <Button
                    variant="secondary"
                    icon={<Download className="h-4 w-4" />}
                    onClick={() => handleDownload(b.filename)}
                    className="sm:w-auto"
                  >
                    Download
                  </Button>
                </div>
              ))}
            </div>
          )}
        </GlassCard>
      </div>
    </AppShell>
  )
}
