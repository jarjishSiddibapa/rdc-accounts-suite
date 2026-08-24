import type { HTMLAttributes, ReactNode } from 'react'
import { cn } from '@/utils/cn'

interface GlassCardProps extends HTMLAttributes<HTMLDivElement> {
  children: ReactNode
  padding?: 'none' | 'sm' | 'md' | 'lg'
}

const paddings = {
  none: '',
  sm: 'p-4',
  md: 'p-4 sm:p-5 lg:p-6',
  lg: 'p-5 sm:p-6 lg:p-8',
}

export function GlassCard({ children, className, padding = 'md', ...rest }: GlassCardProps) {
  return (
    <div className={cn('glass premium-card motion-surface rounded-[1.25rem]', paddings[padding], className)} {...rest}>
      {children}
    </div>
  )
}
