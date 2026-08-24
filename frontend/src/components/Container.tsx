import type { ReactNode } from 'react'
import { cn } from '@/utils/cn'

export function Container({
  children,
  className,
}: {
  children: ReactNode
  className?: string
}) {
  return (
    <div className={cn('w-full px-6 sm:px-8 lg:px-12', className)}>
      {children}
    </div>
  )
}
