export const INDIAN_LOCALE = 'en-IN'
export const INDIAN_TIME_ZONE = 'Asia/Kolkata'

type DateValue = Date | string | number

function parseDate(value: DateValue): Date {
  if (value instanceof Date || typeof value === 'number') return new Date(value)

  // A date without a time is an Indian business date, while a legacy
  // timezone-less API timestamp was stored as UTC. Both rules avoid a date
  // silently changing according to the browser's local timezone.
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    return new Date(`${value}T00:00:00+05:30`)
  }
  if (/^\d{4}-\d{2}-\d{2}T/.test(value) && !/(?:Z|[+-]\d{2}:?\d{2})$/i.test(value)) {
    return new Date(`${value}Z`)
  }
  return new Date(value)
}

export function formatIndianDate(
  value: DateValue,
  options?: Intl.DateTimeFormatOptions,
): string {
  const date = parseDate(value)
  if (Number.isNaN(date.getTime())) return 'Not available'

  const selectedOptions = options ?? {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
  }
  return new Intl.DateTimeFormat(INDIAN_LOCALE, {
    ...selectedOptions,
    timeZone: INDIAN_TIME_ZONE,
  }).format(date)
}

export function formatIndianDateTime(value: DateValue): string {
  return formatIndianDate(value, {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: true,
  })
}

export function formatIndianTime(value: DateValue = new Date()): string {
  return formatIndianDate(value, {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: true,
  })
}

export function formatIndianNumber(
  value: number,
  options?: Intl.NumberFormatOptions,
): string {
  return new Intl.NumberFormat(INDIAN_LOCALE, options).format(value)
}

export function formatIndianCurrency(
  value: number,
  options?: Omit<Intl.NumberFormatOptions, 'style' | 'currency'>,
): string {
  return new Intl.NumberFormat(INDIAN_LOCALE, {
    style: 'currency',
    currency: 'INR',
    ...options,
  }).format(value)
}

export function getIndianHour(value: Date = new Date()): number {
  const hour = new Intl.DateTimeFormat(INDIAN_LOCALE, {
    hour: '2-digit',
    hourCycle: 'h23',
    timeZone: INDIAN_TIME_ZONE,
  }).format(value)
  return Number(hour)
}

export function getIndianCalendarParts(value: Date = new Date()): { year: number; month: number } {
  const parts = new Intl.DateTimeFormat(INDIAN_LOCALE, {
    year: 'numeric',
    month: 'numeric',
    timeZone: INDIAN_TIME_ZONE,
  }).formatToParts(value)
  return {
    year: Number(parts.find((part) => part.type === 'year')?.value),
    month: Number(parts.find((part) => part.type === 'month')?.value),
  }
}

export function getIndianDateInputValue(value: Date = new Date()): string {
  const parts = new Intl.DateTimeFormat(INDIAN_LOCALE, {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    timeZone: INDIAN_TIME_ZONE,
  }).formatToParts(value)
  const year = parts.find((part) => part.type === 'year')?.value ?? ''
  const month = parts.find((part) => part.type === 'month')?.value.padStart(2, '0') ?? ''
  const day = parts.find((part) => part.type === 'day')?.value.padStart(2, '0') ?? ''
  return year && month && day ? `${year}-${month}-${day}` : ''
}

export function getIndianMonthInputValue(value: Date = new Date()): string {
  const { year, month } = getIndianCalendarParts(value)
  return year && month ? `${year}-${String(month).padStart(2, '0')}` : ''
}

export function formatReportMonth(value: string): string {
  const match = /^(\d{4})-(0[1-9]|1[0-2])$/.exec(value)
  if (!match) return value
  const month = Number(match[2])
  const shortMonth = [
    'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
    'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
  ][month - 1]
  return `${shortMonth}-${match[1].slice(-2)}`
}

export function formatFilenameDate(value: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value)
  if (!match) return value
  return `${match[3]}.${match[2]}.${match[1]}`
}

export function getIndianMonthLabel(value: Date = new Date()): string {
  const parts = new Intl.DateTimeFormat(INDIAN_LOCALE, {
    month: 'short',
    year: '2-digit',
    timeZone: INDIAN_TIME_ZONE,
  }).formatToParts(value)
  const month = parts.find((part) => part.type === 'month')?.value ?? 'Mon'
  const year = parts.find((part) => part.type === 'year')?.value ?? '00'
  return `${month}-${year}`
}
