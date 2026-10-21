import { cn } from '@/utils/cn'

interface LogoMarkProps {
  className?: string
  /** Fill of the rounded tile. The default follows the theme accent. */
  tileClassName?: string
  /** Colour of the "R" itself (white on the accent tile by default). */
  glyphColor?: string
}

/**
 * The suite's mark: a white "R" (a stem, a bowl and a leg, all one stroke
 * weight) on a rounded tile. Flat on purpose - one fill, no gradient or
 * shadow - so it stays crisp at 16px in the browser tab and at 48px on the
 * sign-in page. The same drawing is public/favicon.svg.
 */
export function LogoMark({ className, tileClassName = 'fill-accent dark:fill-accent-2', glyphColor = '#fff' }: LogoMarkProps) {
  return (
    <svg
      viewBox="0 0 64 64"
      role="img"
      aria-label="RDC Accounts Suite"
      className={cn('block shrink-0', className)}
    >
      <rect width="64" height="64" rx="16" className={tileClassName} />
      <g fill="none" stroke={glyphColor} strokeWidth="8">
        <path d="M23 15v34" />
        <path d="M23 19h12a8.5 8.5 0 0 1 0 17H23" strokeLinejoin="round" />
        <path d="m34 36 12 13" />
      </g>
    </svg>
  )
}
