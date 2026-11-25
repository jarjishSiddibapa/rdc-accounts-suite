/** Shared types and small helpers of the RDC LC Stamper Application page. */

export const BASE = '/tools/lc-stamper'

export type Mode = 'stamp' | 'filter'
export type OcrMode = 'off' | 'auto' | 'always'
export type OutputKind = 'per_document' | 'combined'

export interface StagedDocument {
  id: string
  name: string
  pages: number
  size: number
  text_pages: number
  sampled: number
}

export interface ListSummary {
  id: string
  name: string
  rows: number
  unique: number
  duplicates: number
  has_dates: boolean
  has_amounts: boolean
  columns: string[]
  invoice_column: string
  warnings: string[]
  sample: { invoice: string; date: string | null; amount: number | null }[]
}

export interface Capabilities {
  online_ocr: boolean
  offline_ocr: boolean
  workers: number
  max_upload_mb: number
  retention_hours: number
}

export interface Rules {
  match_dates: boolean
  match_amounts: boolean
  amount_tolerance: number
  skip_blank: boolean
  ocr_mode: OcrMode
  online_ocr: boolean
  keep_duplicates: boolean
}

export interface Stamp {
  text: string
  font_size: number
  color: [number, number, number]
  rel_x: number
  rel_y: number
  bold: boolean
}

export interface Settings {
  mode: Mode
  output: OutputKind
  rules: Rules
  stamp: Stamp
}

export interface ResultRow {
  invoice: string
  status: string
  document: string
  page: number | null
  out_page: number | null
  excel_date: string | null
  pdf_date: string | null
  date_match: string
  excel_amount: number | null
  pdf_amount: number | null
  amount_match: string
  read_by: string
}

export interface RunResult {
  status: 'ok' | 'no_match'
  mode: Mode
  outputs: { name: string; pages: number; document: string }[]
  report_name: string | null
  counts: Record<string, number>
  warnings: string[]
  rows: ResultRow[]
  rows_total: number
  documents: { name: string; pages: number; kept: number }[]
  elapsed_s: number
}

export const DEFAULT_SETTINGS: Settings = {
  mode: 'stamp',
  output: 'per_document',
  rules: {
    match_dates: false,
    match_amounts: false,
    amount_tolerance: 1,
    skip_blank: true,
    ocr_mode: 'auto',
    online_ocr: true,
    keep_duplicates: false,
  },
  stamp: { text: 'APPROVED FOR PAYMENT', font_size: 12, color: [0, 0, 0], rel_x: 0.05, rel_y: 0.04, bold: true },
}

export const PLACEHOLDERS: { token: string; hint: string }[] = [
  { token: '{invoice}', hint: 'Invoice number' },
  { token: '{date}', hint: 'Invoice date from the list' },
  { token: '{amount}', hint: 'Invoice amount from the list' },
  { token: '{today}', hint: "Today's date" },
  { token: '{page}', hint: 'Page number in the PDF' },
  { token: '{document}', hint: 'PDF file name' },
]

export const COLOR_PRESETS: { name: string; rgb: [number, number, number] }[] = [
  { name: 'Black', rgb: [0, 0, 0] },
  { name: 'Red', rgb: [0.8, 0.05, 0.05] },
  { name: 'Blue', rgb: [0.05, 0.2, 0.75] },
  { name: 'Green', rgb: [0.05, 0.5, 0.2] },
]

const STORAGE_KEY = 'rdc-lc-stamper:settings:v1'

export function loadSettings(): Settings {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (!raw) return DEFAULT_SETTINGS
    const saved = JSON.parse(raw) as Partial<Settings>
    return {
      ...DEFAULT_SETTINGS,
      ...saved,
      rules: { ...DEFAULT_SETTINGS.rules, ...(saved.rules ?? {}) },
      stamp: { ...DEFAULT_SETTINGS.stamp, ...(saved.stamp ?? {}) },
    }
  } catch {
    return DEFAULT_SETTINGS
  }
}

export function saveSettings(settings: Settings): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(settings))
  } catch {
    /* private window or blocked storage: the settings just are not remembered */
  }
}

export function rgbToHex(rgb: number[]): string {
  return '#' + rgb.map((v) => Math.round(Math.min(1, Math.max(0, v)) * 255).toString(16).padStart(2, '0')).join('')
}

export function hexToRgb(hex: string): [number, number, number] {
  const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex)
  if (!m) return [0, 0, 0]
  return [parseInt(m[1], 16) / 255, parseInt(m[2], 16) / 255, parseInt(m[3], 16) / 255]
}

export function formatSize(bytes: number): string {
  if (bytes >= 1024 * 1024 * 1024) return `${(bytes / 1024 ** 3).toFixed(1)} GB`
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 ** 2).toFixed(1)} MB`
  return `${Math.max(1, Math.round(bytes / 1024))} KB`
}

export function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds.toFixed(1)} s`
  const minutes = Math.floor(seconds / 60)
  return `${minutes} min ${Math.round(seconds - minutes * 60)} s`
}

/** How a document will be read: a scan needs OCR, a system-made PDF does not. */
export function readability(doc: StagedDocument): { label: string; tone: 'good' | 'warn' | 'mixed' } {
  if (doc.sampled === 0) return { label: 'Unknown', tone: 'mixed' }
  if (doc.text_pages === doc.sampled) return { label: 'Text PDF · no OCR needed', tone: 'good' }
  if (doc.text_pages === 0) return { label: 'Scanned · OCR will be used', tone: 'warn' }
  return { label: 'Part scanned · OCR where needed', tone: 'mixed' }
}

export function statusTone(status: string): 'good' | 'warn' | 'bad' | 'dup' {
  if (status.startsWith('✓')) return 'good'
  if (status.startsWith('✗')) return 'bad'
  if (status.includes('Duplicate')) return 'dup'
  return 'warn'
}
