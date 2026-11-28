import { useState, type FormEvent } from 'react'
import { Button } from '@/components/Button'
import { ErrorBanner } from '@/components/ErrorBanner'
import { put } from '@/lib/api'
import { BASE, type Status } from '@/lib/hsbcTradepay'
import { message } from './parts'

/** The fixed value of cell E1 of the bank's template: an administrator sets it once. */
export function HsbcId({ status, isAdmin, onSaved }: { status: Status; isAdmin: boolean; onSaved: () => void }) {
  const [value, setValue] = useState('')
  const [editing, setEditing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function save(event: FormEvent) {
    event.preventDefault()
    if (!value.trim()) return
    setSaving(true)
    setError(null)
    try {
      await put(`${BASE}/settings`, { hsbc_id: value })
      setEditing(false)
      onSaved()
    } catch (err) {
      setError(message(err, 'The HSBC ID could not be saved.'))
    } finally {
      setSaving(false)
    }
  }

  if (!isAdmin) {
    return status.ready ? null : (
      <p className="status-banner border-amber-500/30 bg-amber-500/8 text-amber-600 dark:text-amber-400">
        An administrator has to set the HSBC ID (cell E1 of the template) here once before files can be made.
      </p>
    )
  }

  if (status.ready && !editing) {
    return (
      <p className="text-sm text-ink-dim">
        HSBC ID (cell E1): <span className="font-mono text-ink [overflow-wrap:anywhere]">{status.hsbc_id}</span>{' '}
        <button type="button" className="font-semibold text-accent hover:underline"
          onClick={() => { setValue(status.hsbc_id); setError(null); setEditing(true) }}>Change</button>
      </p>
    )
  }

  return (
    <form onSubmit={(e) => void save(e)} className="flex flex-col gap-2 rounded-xl border border-amber-500/30 bg-amber-500/5 p-4">
      <label htmlFor="hsbc-id" className="text-sm font-semibold text-ink">
        HSBC ID: the value in cell E1 of your HSBC template
      </label>
      <div className="flex flex-col gap-2 sm:flex-row">
        <input id="hsbc-id" className="field-control w-full sm:max-w-md" value={value} autoComplete="off" maxLength={30}
          onChange={(e) => setValue(e.target.value.replace(/\s/g, '').toUpperCase())} placeholder="Copy it from cell E1" />
        <Button type="submit" loading={saving} disabled={value.length < 6}>Save</Button>
        {status.ready && <Button type="button" variant="ghost" onClick={() => setEditing(false)}>Cancel</Button>}
      </div>
      <p className="text-xs text-ink-faint">
        6 to 30 letters and digits. Set once by an administrator; it goes into every file unchanged.
      </p>
      {error && <ErrorBanner>{error}</ErrorBanner>}
    </form>
  )
}
