import { useCallback, useEffect, useState } from 'react'
import { get } from '@/lib/api'
import { formatIndianNumber } from '@/lib/regional'
import type { JobState, JobStatus } from '@/components/ProgressPanel'

export const BASE = '/tools/ultrafine-cashflow'

export interface MonthRow {
  month: string
  transactions: number
  payments: number
  receipts: number
  issue_count: number
  file: { name: string; version: number; source: 'generated' | 'uploaded'; generated_at: string; available: boolean } | null
}

export interface MappingOptions {
  cf_locations: string[]
  locations: string[]
  parties: string[]
  sharing_groups: string[]
  summary_groups: string[]
}

export interface UnmappedKey {
  desc_key: string
  sample: string
  direction: string
  count: number
  total: number
}

export interface UnmappedOther {
  issue: string
  name: string
  sample: string
  count: number
  /** where the line was placed: the name's own location, a typed split, or a sharing-ratio line */
  source?: 'party' | 'split' | 'ratio'
}

export interface Unmapped {
  no_party: UnmappedKey[]
  other: UnmappedOther[]
}

export interface GenerateOutcome {
  month: string
  status: 'generated' | 'needs_mapping_fix' | 'no_data' | 'unchanged'
  version?: number
  file_name?: string
  issue_count?: number
  unmapped?: Unmapped
  new?: number
  duplicate?: number
  earlier_issue_count?: number
  unmapped_count?: number
}

export interface JobResponse<T> {
  status: JobStatus
  progress: number
  phase: string
  result: T | null
  error: string | null
}

export async function pollJob<T>(jobId: string): Promise<JobState<T>> {
  const job = await get<JobResponse<T>>(`${BASE}/jobs/${jobId}`)
  return {
    status: job.status,
    progress: (job.progress ?? 0) * 100,
    phase: job.phase,
    result: job.result ?? undefined,
    error: job.error ?? undefined,
  }
}

const MONTH_NAMES = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

export function monthLabel(month: string): string {
  const match = /^(\d{4})-(\d{2})$/.exec(month)
  return match ? `${MONTH_NAMES[Number(match[2]) - 1]} ${match[1]}` : month
}

export function money(value: number, digits = 2): string {
  return formatIndianNumber(value, { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

export const ISSUE_TEXT: Record<string, string> = {
  no_party: 'Description not mapped to a name',
  no_location: 'Name has no location',
  no_cf_location: 'No CF location chosen',
  no_ratio: 'No sharing ratio in force on this date',
  split_mismatch: 'Typed split does not add up to the bank amount',
}

/** Existing names offered by every combobox (parties, locations ...). */
export function useMappingOptions(): [MappingOptions, () => Promise<void>] {
  const [options, setOptions] = useState<MappingOptions>({
    cf_locations: [],
    locations: [],
    parties: [],
    sharing_groups: [],
    summary_groups: [],
  })
  const reload = useCallback(async () => {
    try {
      setOptions(await get<MappingOptions>(`${BASE}/mappings/options`))
    } catch {
      // Non-critical: inputs still accept typed values.
    }
  }, [])
  useEffect(() => {
    void reload()
  }, [reload])
  return [options, reload]
}

export function currentMonth(): string {
  const now = new Date()
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
}
