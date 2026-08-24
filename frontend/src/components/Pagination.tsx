import { ChevronFirst, ChevronLast, ChevronLeft, ChevronRight } from 'lucide-react'
import type { ReactNode } from 'react'
import { cn } from '@/utils/cn'
import { formatIndianNumber } from '@/lib/regional'

interface PaginationProps {
  page: number
  pageCount: number
  pageSize: number
  totalItems: number
  onPageChange: (page: number) => void
  onPageSizeChange: (pageSize: number) => void
  itemLabel?: string
  pageSizeOptions?: number[]
  className?: string
}

type PageItem = number | 'left-ellipsis' | 'right-ellipsis'

function getPageItems(page: number, pageCount: number): PageItem[] {
  if (pageCount <= 7) return Array.from({ length: pageCount }, (_, index) => index + 1)

  if (page <= 4) return [1, 2, 3, 4, 5, 'right-ellipsis', pageCount]
  if (page >= pageCount - 3) {
    return [1, 'left-ellipsis', pageCount - 4, pageCount - 3, pageCount - 2, pageCount - 1, pageCount]
  }
  return [1, 'left-ellipsis', page - 1, page, page + 1, 'right-ellipsis', pageCount]
}

export function Pagination({
  page,
  pageCount,
  pageSize,
  totalItems,
  onPageChange,
  onPageSizeChange,
  itemLabel = 'items',
  pageSizeOptions = [5, 10, 25, 50],
  className,
}: PaginationProps) {
  if (totalItems === 0) return null

  const start = (page - 1) * pageSize + 1
  const end = Math.min(page * pageSize, totalItems)
  const pageItems = getPageItems(page, pageCount)

  return (
    <nav
      aria-label={`${itemLabel} pagination`}
      className={cn(
        'flex flex-col gap-3 rounded-2xl border border-border bg-surface/40 px-3.5 py-3 sm:flex-row sm:items-center sm:justify-between',
        className,
      )}
    >
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-ink-faint">
        <span>
          Showing <strong className="font-semibold text-ink">{formatIndianNumber(start)}–{formatIndianNumber(end)}</strong> of{' '}
          <strong className="font-semibold text-ink">{formatIndianNumber(totalItems)}</strong> {itemLabel}
        </span>
        <label className="flex items-center gap-2">
          <span>Per page</span>
          <select
            value={pageSize}
            onChange={(event) => onPageSizeChange(Number(event.target.value))}
            aria-label={`${itemLabel} per page`}
            className="min-h-11 rounded-lg border border-border bg-surface px-2 text-xs font-semibold text-ink outline-none transition focus:border-accent sm:min-h-9"
          >
            {pageSizeOptions.map((option) => (
              <option key={option} value={option}>
                {formatIndianNumber(option)}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="flex w-full items-center justify-between gap-1 sm:w-auto sm:justify-start sm:self-auto">
        <PageButton label="First page" disabled={page === 1} onClick={() => onPageChange(1)}>
          <ChevronFirst className="h-3.5 w-3.5" />
        </PageButton>
        <PageButton label="Previous page" disabled={page === 1} onClick={() => onPageChange(page - 1)}>
          <ChevronLeft className="h-3.5 w-3.5" />
        </PageButton>

        <div className="hidden items-center gap-1 sm:flex">
          {pageItems.map((item) =>
            typeof item === 'number' ? (
              <button
                key={item}
                type="button"
                onClick={() => onPageChange(item)}
                aria-label={`Page ${item}`}
                aria-current={item === page ? 'page' : undefined}
                className={cn(
                  'grid h-9 min-w-9 place-items-center rounded-lg px-2 text-xs font-semibold transition',
                  item === page
                    ? 'bg-accent text-white shadow-[0_8px_18px_-12px_color-mix(in_oklab,var(--color-accent)_75%,transparent)] dark:bg-accent-2'
                    : 'text-ink-dim hover:bg-bg-soft hover:text-ink',
                )}
              >
                {formatIndianNumber(item)}
              </button>
            ) : (
              <span key={item} className="grid h-8 min-w-6 place-items-center text-xs text-ink-faint">
                …
              </span>
            ),
          )}
        </div>
        <span className="px-2 text-xs font-semibold text-ink-dim sm:hidden">
          {formatIndianNumber(page)} / {formatIndianNumber(pageCount)}
        </span>

        <PageButton label="Next page" disabled={page === pageCount} onClick={() => onPageChange(page + 1)}>
          <ChevronRight className="h-3.5 w-3.5" />
        </PageButton>
        <PageButton label="Last page" disabled={page === pageCount} onClick={() => onPageChange(pageCount)}>
          <ChevronLast className="h-3.5 w-3.5" />
        </PageButton>
      </div>
    </nav>
  )
}

function PageButton({
  label,
  disabled,
  onClick,
  children,
}: {
  label: string
  disabled: boolean
  onClick: () => void
  children: ReactNode
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      disabled={disabled}
      onClick={onClick}
      className="grid h-11 w-11 place-items-center rounded-lg border border-transparent text-ink-dim transition hover:border-border hover:bg-bg-soft hover:text-ink disabled:pointer-events-none disabled:opacity-30 sm:h-9 sm:w-9"
    >
      {children}
    </button>
  )
}
