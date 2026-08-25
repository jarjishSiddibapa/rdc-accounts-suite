import { Search } from 'lucide-react'
import { cn } from '@/utils/cn'

interface SearchBoxProps {
  value: string
  onChange: (value: string) => void
  placeholder?: string
  'aria-label'?: string
  className?: string
}

/** Shared search input (icon + text field) used consistently across the app. */
export function SearchBox({
  value,
  onChange,
  placeholder = 'Search…',
  className,
  ...rest
}: SearchBoxProps) {
  return (
    <label className={cn('relative block', className)}>
      <Search className="pointer-events-none absolute top-1/2 left-3.5 h-4 w-4 -translate-y-1/2 text-ink-faint" />
      <input
        type="search"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        aria-label={rest['aria-label'] ?? placeholder}
        className="field-control w-full pl-10"
      />
    </label>
  )
}
