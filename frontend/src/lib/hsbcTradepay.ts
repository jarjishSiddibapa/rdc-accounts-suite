/** Types and small helpers of the RDC HSBC Tradepay CSV Generation page. */

export const BASE = '/tools/hsbc-tradepay'
export const PARTY_LIMIT = 35
export const DESCRIPTION_LIMIT = 70

export interface Status {
  today: string
  ready: boolean
  hsbc_id: string
  locations: string[]
  location: string
  next_series: number
  descriptions: string[]
  max_rows: number
}

export interface Entry {
  id: number
  vendor_code: string
  invoice_no: string
  amount: string
}

export type EntryField = 'vendor_code' | 'invoice_no' | 'amount'
export const FIELDS: readonly EntryField[] = ['vendor_code', 'invoice_no', 'amount']
export const FIELD_LABELS: Record<EntryField, string> = {
  vendor_code: 'Vendor code', invoice_no: 'Invoice number', amount: 'Amount (net of TDS)',
}

export interface BankAccount {
  key: string
  account: string
  ifsc: string
  bank: string
  branch: string
  locations: string[]
  beneficiary: string
  /** Why the bank would reject it ('' when it can be used). */
  problem: string
  /** What to check once this account is chosen ('' when nothing). */
  caution: string
}

export interface InvoiceInfo {
  date: string | null
  amount: string
  paid: string
  open: string
  status: string
}

export interface CheckedRow {
  line: number
  vendor_code: string
  invoice_no: string
  amount: string
  vendor_name: string
  party_name: string
  city: string
  description: string
  accounts: BankAccount[]
  account_key: string
  manual_account: boolean
  invoice: InvoiceInfo | null
  earlier_files: string[]
  errors: string[]
  warnings: string[]
  notes: string[]
}

export type TextField = 'party_name' | 'city' | 'description'

/** A checked row plus what the user may change on it. */
export interface ReviewRow extends CheckedRow {
  entry_id: number
  typed_account: string
  typed_ifsc: string
  /** The text fields the user has changed: kept when the details are fetched again. */
  edited: TextField[]
}

export interface MadeFile {
  id: number
  file_code: string
  file_date: string
  rows: number
  total: string
  made_by: string
  made_at: string
  mine: boolean
}

let nextId = 1

export function blankEntry(values: Partial<Entry> = {}): Entry {
  return { vendor_code: '', invoice_no: '', amount: '', ...values, id: nextId++ }
}

export function isBlank(entry: Entry): boolean {
  return !entry.vendor_code.trim() && !entry.invoice_no.trim() && !entry.amount.trim()
}

/** Rows copied from Excel: one line per row, cells separated by tabs. A heading line is left out. */
export function parsePasted(text: string): string[][] {
  const rows = text.replace(/\r/g, '').split('\n').filter((line) => line.trim())
    .map((line) => line.split('\t').map((cell) => cell.trim()))
  if (rows.length && /vendor|supplier|code/i.test(rows[0][0] ?? '') && !/\d/.test(rows[0].join(''))) rows.shift()
  return rows
}

export function amountValue(text: string): number {
  const value = Number(text.replace(/^(rs\.?|inr|₹)\s*/i, '').replace(/[,\s]/g, ''))
  return Number.isFinite(value) ? value : 0
}

export const cleanParty = (text: string) => text.replace(/[^A-Za-z0-9 ]/g, '').slice(0, PARTY_LIMIT)
export const cleanCity = (text: string) => text.replace(/[^A-Za-z ]/g, '').slice(0, PARTY_LIMIT)
export const cleanAccount = (text: string) => text.replace(/[^A-Za-z0-9]/g, '').slice(0, 34)
export const cleanIfsc = (text: string) => text.replace(/[^A-Za-z0-9]/g, '').toUpperCase().slice(0, 11)

export const IFSC_PATTERN = /^[A-Z]{4}0[A-Z0-9]{6}$/

export function chosenAccount(row: ReviewRow): BankAccount | undefined {
  return row.accounts.find((a) => a.key === row.account_key)
}

/** What still has to be done on a row before the file can be made (empty: ready). */
export function rowProblems(row: ReviewRow): string[] {
  if (row.errors.length) return row.errors
  const problems: string[] = []
  if (!row.party_name.trim()) problems.push('Type the party name.')
  if (!row.city.trim()) problems.push('Type the city.')
  if (!row.description.trim()) problems.push('Choose the description of payment.')
  if (row.manual_account) {
    if (row.typed_account.length < 5) problems.push('Type the account number.')
    if (!IFSC_PATTERN.test(row.typed_ifsc)) problems.push('Type the IFSC (11 characters, the fifth is a zero).')
  } else {
    const account = chosenAccount(row)
    if (!account) problems.push(`Choose one of the ${row.accounts.length} bank accounts.`)
    else if (account.problem) problems.push(account.problem)
  }
  return problems
}

/** The same vendor and invoice number, however it was typed. */
export function repeatKey(row: CheckedRow): string {
  return `${row.vendor_code}|${row.invoice_no.replace(/\s+/g, ' ').trim().toUpperCase()}`
}

export function repeatedKeys(rows: ReviewRow[]): Set<string> {
  const seen = new Set<string>()
  const repeated = new Set<string>()
  for (const row of rows) {
    const key = repeatKey(row)
    if (seen.has(key)) repeated.add(key)
    seen.add(key)
  }
  return repeated
}

/** Everything on a row that a person has to look at and tick before the file is made. */
export function rowWarnings(row: ReviewRow, repeated: Set<string>): string[] {
  if (row.errors.length) return row.warnings
  const warnings = [...row.warnings]
  const caution = chosenAccount(row)?.caution
  if (caution) warnings.push(caution)
  if (repeated.has(repeatKey(row))) warnings.push('This vendor and invoice number are listed more than once.')
  return warnings
}

/** A freshly fetched row, keeping what the user had already chosen or typed for the same vendor and invoice. */
export function reviewRow(fresh: CheckedRow, entryId: number, before?: ReviewRow): ReviewRow {
  const row: ReviewRow = { ...fresh, entry_id: entryId, typed_account: '', typed_ifsc: '', edited: [] }
  if (!before || fresh.errors.length || before.vendor_code !== fresh.vendor_code) return row
  if (fresh.manual_account) {
    row.typed_account = before.typed_account
    row.typed_ifsc = before.typed_ifsc
  } else if (before.account_key && fresh.accounts.some((a) => a.key === before.account_key)) {
    row.account_key = before.account_key
  }
  if (before.invoice_no === fresh.invoice_no) {
    for (const field of before.edited) row[field] = before[field]
    row.edited = before.edited
  }
  return row
}

export function fileCode(location: string, series: string): string {
  const loc = location.trim().toUpperCase()
  const number = Number(series)
  if (!/^[A-Z]{2,3}$/.test(loc) || !Number.isInteger(number) || number < 1 || number > 999) return ''
  return `RDCC${loc}${String(number).padStart(2, '0')}`
}

export function saveFile(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = name
  link.click()
  window.setTimeout(() => URL.revokeObjectURL(url), 1_000)
}
