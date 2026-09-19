import type { ReactNode } from 'react'
import { cn } from '@/utils/cn'

interface ErrorBannerProps {
  children: ReactNode
  className?: string
}

/**
 * Standard inline error banner for API/job failures. Always announces itself
 * to screen readers (role="alert") - use this instead of an ad hoc <p> for
 * any user-facing error message.
 */
export function ErrorBanner({ children, className }: ErrorBannerProps) {
  return (
    <p role="alert" className={cn('status-banner border-red-500/25 bg-red-500/8 text-red-500', className)}>
      {children}
    </p>
  )
}
