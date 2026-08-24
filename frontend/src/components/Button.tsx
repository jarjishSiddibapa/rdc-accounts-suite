import type { ButtonHTMLAttributes, ReactNode } from 'react'
import { Loader2 } from 'lucide-react'
import { cn } from '@/utils/cn'

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger'

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant
  loading?: boolean
  icon?: ReactNode
}

const base =
  'relative inline-flex min-h-11 w-full items-center justify-center gap-2 rounded-xl px-5 py-2.5 text-sm font-semibold tracking-[-0.01em] transition duration-200 active:scale-[0.985] disabled:cursor-not-allowed disabled:opacity-50 disabled:active:scale-100 focus-visible:outline-2 focus-visible:outline-accent sm:w-auto'

const variants: Record<Variant, string> = {
  primary:
    'border border-accent bg-accent text-white shadow-[0_12px_24px_-16px_color-mix(in_oklab,var(--color-accent)_74%,transparent)] hover:bg-accent-2 hover:border-accent-2 hover:shadow-[0_16px_30px_-18px_color-mix(in_oklab,var(--color-accent)_78%,transparent)] dark:border-accent-2 dark:bg-accent-2 dark:hover:border-[#274d91] dark:hover:bg-[#274d91]',
  secondary:
    'border border-border bg-surface text-ink shadow-[inset_0_1px_0_var(--surface-highlight),0_8px_20px_-18px_rgba(var(--shadow-rgb),0.45)] hover:border-accent/40 hover:text-accent',
  ghost: 'border border-transparent text-ink-dim hover:border-border hover:bg-bg-soft/80 hover:text-ink',
  danger: 'border border-red-600 bg-red-600 text-white shadow-[0_12px_24px_-16px_rgba(220,38,38,0.72)] hover:bg-red-700 hover:border-red-700',
}

export function Button({
  variant = 'primary',
  loading = false,
  icon,
  className,
  children,
  disabled,
  ...rest
}: ButtonProps) {
  return (
    <button
      className={cn(base, variants[variant], className)}
      disabled={disabled || loading}
      {...rest}
    >
      {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : icon}
      {children}
    </button>
  )
}
