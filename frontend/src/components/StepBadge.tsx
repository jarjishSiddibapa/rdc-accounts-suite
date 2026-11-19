/** The number of a step in a setup, in the place an icon would be. */
export function StepBadge({ step, small = false }: { step: number; small?: boolean }) {
  return (
    <span
      className={`icon-tile grid shrink-0 place-items-center rounded-xl font-display font-semibold ${
        small ? 'h-10 w-10 text-base' : 'h-12 w-12 text-lg'
      }`}
      aria-hidden="true"
    >
      {step}
    </span>
  )
}
