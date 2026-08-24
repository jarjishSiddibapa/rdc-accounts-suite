import { scorePasswordStrength, type PasswordStrengthLevel } from '@/lib/passwordStrength'
import { cn } from '@/utils/cn'

const LEVEL_ORDER: PasswordStrengthLevel[] = ['weak', 'fair', 'good', 'strong']

const BAR_COLORS: Record<PasswordStrengthLevel, string> = {
  weak: 'bg-red-500',
  fair: 'bg-amber-500',
  good: 'bg-yellow-400',
  strong: 'bg-emerald-500',
}

const LABEL_COLORS: Record<PasswordStrengthLevel, string> = {
  weak: 'text-red-500',
  fair: 'text-amber-500',
  good: 'text-yellow-600',
  strong: 'text-emerald-500',
}

export function PasswordStrengthMeter({ password }: { password: string }) {
  if (!password) return null

  const strength = scorePasswordStrength(password)
  const filledBars = LEVEL_ORDER.indexOf(strength.level) + 1

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex gap-1.5">
        {LEVEL_ORDER.map((level, index) => (
          <div
            key={level}
            className={cn(
              'h-1.5 flex-1 rounded-full transition-colors',
              index < filledBars ? BAR_COLORS[strength.level] : 'bg-bg-soft',
            )}
          />
        ))}
      </div>
      <p className={cn('text-xs font-medium', LABEL_COLORS[strength.level])}>
        {strength.label}
        {!strength.isAcceptable && (
          <span className="ml-1 font-normal text-ink-faint">
            — needs at least 10 characters with uppercase, lowercase, a number, and a symbol
          </span>
        )}
      </p>
    </div>
  )
}
