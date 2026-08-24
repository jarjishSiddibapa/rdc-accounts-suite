import type { InputHTMLAttributes, SelectHTMLAttributes } from 'react'
import { cn } from '@/utils/cn'
import { INDIAN_LOCALE } from '@/lib/regional'

type NativeTemporalPickerProps = Omit<
  InputHTMLAttributes<HTMLInputElement>,
  'type' | 'value' | 'onChange'
> & {
  value: string
  onValueChange: (value: string) => void
}

function NativeTemporalPicker({
  value,
  onValueChange,
  className,
  ...props
}: NativeTemporalPickerProps & { type: 'date' | 'month' }) {
  return (
    <input
      {...props}
      // Chromium picks the native date/month picker's displayed format
      // (day/month/year order) from this element's own lang attribute -
      // without it, it falls back to the OS locale, which on a US-locale
      // Windows install renders MM/DD/YYYY instead of India's DD/MM/YYYY.
      // The underlying value stored/submitted is always ISO (YYYY-MM-DD)
      // either way; only the on-screen display order changes.
      lang={INDIAN_LOCALE}
      type={props.type}
      value={value}
      onChange={(event) => onValueChange(event.target.value)}
      className={cn('field-control temporal-picker-control w-full', className)}
    />
  )
}

export function DatePicker(props: NativeTemporalPickerProps) {
  return <NativeTemporalPicker {...props} type="date" />
}

export function MonthYearPicker(props: NativeTemporalPickerProps) {
  return <NativeTemporalPicker {...props} type="month" />
}

const MONTHS = [
  'January',
  'February',
  'March',
  'April',
  'May',
  'June',
  'July',
  'August',
  'September',
  'October',
  'November',
  'December',
] as const

type MonthPickerProps = Omit<SelectHTMLAttributes<HTMLSelectElement>, 'value' | 'onChange'> & {
  value: number | ''
  onValueChange: (value: number | '') => void
  emptyLabel?: string
}

export function MonthPicker({
  value,
  onValueChange,
  emptyLabel = 'Select month',
  className,
  ...props
}: MonthPickerProps) {
  return (
    <select
      {...props}
      value={value}
      onChange={(event) => onValueChange(event.target.value ? Number(event.target.value) : '')}
      className={cn('field-control temporal-picker-control w-full', className)}
    >
      <option value="">{emptyLabel}</option>
      {MONTHS.map((month, index) => (
        <option key={month} value={index + 1}>
          {month}
        </option>
      ))}
    </select>
  )
}

type YearPickerProps = Omit<SelectHTMLAttributes<HTMLSelectElement>, 'value' | 'onChange'> & {
  value: number | ''
  onValueChange: (value: number | '') => void
  minYear?: number
  maxYear?: number
  emptyLabel?: string
}

export function YearPicker({
  value,
  onValueChange,
  minYear = new Date().getFullYear() - 20,
  maxYear = new Date().getFullYear() + 10,
  emptyLabel = 'Select year',
  className,
  ...props
}: YearPickerProps) {
  const startYear = Math.min(minYear, maxYear)
  const endYear = Math.max(minYear, maxYear)
  const years = Array.from({ length: endYear - startYear + 1 }, (_, index) => endYear - index)

  return (
    <select
      {...props}
      value={value}
      onChange={(event) => onValueChange(event.target.value ? Number(event.target.value) : '')}
      className={cn('field-control temporal-picker-control w-full', className)}
    >
      <option value="">{emptyLabel}</option>
      {years.map((year) => (
        <option key={year} value={year}>
          {year}
        </option>
      ))}
    </select>
  )
}
