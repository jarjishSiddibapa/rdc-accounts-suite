import { useEffect, useMemo, useState } from 'react'

export interface PaginationState<T> {
  page: number
  pageCount: number
  pageSize: number
  totalItems: number
  startIndex: number
  endIndex: number
  pagedItems: T[]
  setPage: (page: number) => void
  setPageSize: (pageSize: number) => void
}

/**
 * Shared client-side pagination for the application's in-memory lists.
 * Keeps the active page valid when filtering, archiving, or refreshing changes
 * the number of available rows.
 */
export function usePagination<T>(items: T[], initialPageSize = 10, resetKey?: unknown): PaginationState<T> {
  const [page, setPageState] = useState(1)
  const [pageSize, setPageSizeState] = useState(initialPageSize)
  const pageCount = Math.max(1, Math.ceil(items.length / pageSize))
  const safePage = Math.min(page, pageCount)
  const startIndex = items.length === 0 ? 0 : (safePage - 1) * pageSize
  const endIndex = Math.min(startIndex + pageSize, items.length)

  useEffect(() => {
    setPageState(1)
  }, [resetKey])

  useEffect(() => {
    if (page > pageCount) setPageState(pageCount)
  }, [page, pageCount])

  const pagedItems = useMemo(
    () => items.slice(startIndex, endIndex),
    [items, startIndex, endIndex],
  )

  function setPage(nextPage: number) {
    setPageState(Math.min(Math.max(1, nextPage), pageCount))
  }

  function setPageSize(nextPageSize: number) {
    setPageSizeState(nextPageSize)
    setPageState(1)
  }

  return {
    page: safePage,
    pageCount,
    pageSize,
    totalItems: items.length,
    startIndex,
    endIndex,
    pagedItems,
    setPage,
    setPageSize,
  }
}
