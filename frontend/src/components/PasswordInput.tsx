import { useState, type InputHTMLAttributes } from 'react'
import { Eye, EyeOff, LockKeyhole } from 'lucide-react'
import { cn } from '@/utils/cn'

type PasswordInputProps = Omit<InputHTMLAttributes<HTMLInputElement>, 'type'> & {
  /** Called when the eye is clicked to show the text (e.g. to fetch a saved value). */
  onReveal?: () => void | Promise<void>
}

export function PasswordInput({ className, disabled, onReveal, ...props }: PasswordInputProps) {
  const [visible, setVisible] = useState(false)

  return (
    <div className="relative">
      <LockKeyhole className="pointer-events-none absolute top-1/2 left-3 h-4 w-4 -translate-y-1/2 text-ink-faint transition-colors duration-200 group-focus-within/field:text-accent" />
      <input
        {...props}
        type={visible ? 'text' : 'password'}
        disabled={disabled}
        className={cn(
          'field-control w-full py-2.5 pr-12 pl-9 disabled:cursor-not-allowed disabled:opacity-50',
          className,
        )}
      />
      <button
        type="button"
        disabled={disabled}
        onClick={() => {
          if (!visible) void onReveal?.()
          setVisible((current) => !current)
        }}
        aria-label={visible ? 'Hide password' : 'Show password'}
        aria-pressed={visible}
        className="absolute top-1/2 right-0 grid h-11 w-11 -translate-y-1/2 place-items-center rounded-xl text-ink-faint transition hover:bg-bg-soft hover:text-ink disabled:pointer-events-none"
      >
        {visible ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
      </button>
    </div>
  )
}
