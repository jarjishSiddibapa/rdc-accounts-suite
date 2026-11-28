/** Types and small helpers of the RDC HSBC Tradepay CSV Generation page. */

export const BASE = '/tools/hsbc-tradepay'
export const PARTY_LIMIT = 35

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

export interface BankAccount {
  key: string
  account: string
  ifsc: string
  bank: string
  branch: string
  locations: string[]
  beneficiary: string
  account_ok: boolean
  ifsc_ok: boolean
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
  errors: string[]
  warnings: string[]
}

/** A checked row plus what the user may change on it. */
export interface ReviewRow extends CheckedRow {
  entry_id: number
  typed_account: string
  typed_ifsc: string
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

/** What still has to be done on a row before the file can be made (empty: ready). */
export function rowProblems(row: ReviewRow): string[] {
  const problems = [...row.errors]
  if (row.errors.length) return problems
  if (!row.party_name.trim()) problems.push('Type the party name.')
  if (!row.city.trim()) problems.push('Type the city.')
  if (!row.description.trim()) problems.push('Choose the description of payment.')
  if (row.manual_account) {
    if (row.typed_account.length < 5) problems.push('Type the account number.')
    if (!IFSC_PATTERN.test(row.typed_ifsc)) problems.push('Type the IFSC (11 characters, the fifth is a zero).')
  } else if (!row.account_key) {
    problems.push('Choose the bank account.')
  }
  return problems
}

export function fileCode(location: string, series: string): string {
  const loc = location.trim().toUpperCase()
  const number = Number(series)
  if (!/^[A-Z]{2,3}$/.test(loc) || !Number.isInteger(number) || number < 1 || number > 999) return ''
  return `RDCC${loc}${String(number).padStart(2, '0')}`
}
