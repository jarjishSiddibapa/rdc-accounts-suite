import { cn } from './cn'

/**
 * Class string for a tab/segment button inside `.segmented-control` or a tab
 * row. One definition so every tool's tabs look and animate the same.
 */
export function segmentClass(active: boolean, size: 'md' | 'sm' = 'md') {
  return cn(
    'rounded-xl font-semibold transition duration-200',
    size === 'sm' ? 'px-3.5 py-2 text-xs' : 'px-4 py-2 text-sm',
    active
      ? 'bg-accent text-white shadow-[0_8px_18px_-12px_color-mix(in_oklab,var(--color-accent)_75%,transparent)] dark:bg-accent-2'
      : 'border border-transparent text-ink-dim hover:bg-surface/70 hover:text-ink',
  )
}
