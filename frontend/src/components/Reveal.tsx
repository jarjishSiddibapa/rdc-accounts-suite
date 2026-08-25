import type { ReactNode } from 'react'
import { cn } from '@/utils/cn'

interface RevealProps {
  children: ReactNode
  className?: string
  delay?: number
  y?: number
  once?: boolean
}

export function Reveal({ children, className, delay = 0, y = 16, once = true }: RevealProps) {
  void delay
  void y
  void once
  return <div className={cn(className)}>{children}</div>
}

export function RevealGroup({
  children,
  className,
  stagger = 0.06,
}: {
  children: ReactNode
  className?: string
  stagger?: number
}) {
  void stagger
  return <div className={cn(className)}>{children}</div>
}
