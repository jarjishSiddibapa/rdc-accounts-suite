import { Reveal } from '@/components/Reveal'
import { cn } from '@/utils/cn'

export function SectionHeading({
  eyebrow,
  title,
  description,
  align = 'left',
  className,
}: {
  eyebrow: string
  title: string
  description?: string
  align?: 'left' | 'center'
  className?: string
}) {
  return (
    <div className={cn(align === 'center' && 'text-center', className)}>
      <Reveal>
        <div className="mb-3 inline-flex items-center text-sm font-semibold text-accent">
          {eyebrow}
        </div>
      </Reveal>
      <Reveal delay={0.06}>
        <h2 className="text-3xl font-semibold tracking-[-0.025em] text-balance sm:text-4xl">
          {title}
        </h2>
      </Reveal>
      {description && (
        <Reveal delay={0.1}>
          <p className={cn('mt-3 max-w-2xl text-base text-ink-dim', align === 'center' && 'mx-auto')}>
            {description}
          </p>
        </Reveal>
      )}
    </div>
  )
}
