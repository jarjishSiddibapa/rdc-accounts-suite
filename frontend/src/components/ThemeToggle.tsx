import { Moon, Sun } from 'lucide-react'
import { motion, AnimatePresence, useReducedMotion } from 'motion/react'
import { useTheme } from '@/hooks/useTheme'
import { cn } from '@/utils/cn'

export function ThemeToggle({ className }: { className?: string }) {
  const { theme, toggleTheme } = useTheme()
  const reduceMotion = useReducedMotion()

  return (
    <button
      onClick={toggleTheme}
      aria-label={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
      className={cn(
        'relative grid h-11 w-11 place-items-center overflow-hidden rounded-xl border border-border bg-surface/55 text-ink-dim shadow-[inset_0_1px_0_var(--surface-highlight)] transition duration-200 hover:-translate-y-0.5 hover:border-accent/40 hover:bg-surface hover:text-accent',
        className,
      )}
    >
      <AnimatePresence mode="wait" initial={false}>
        <motion.span
          key={theme}
          initial={reduceMotion ? false : { rotate: -90, opacity: 0, scale: 0.5 }}
          animate={{ rotate: 0, opacity: 1, scale: 1 }}
          exit={{ rotate: 90, opacity: 0, scale: 0.5 }}
          transition={{ duration: 0.25, ease: 'easeInOut' }}
          className="grid place-items-center"
        >
          {theme === 'dark' ? <Moon className="h-4 w-4" /> : <Sun className="h-4 w-4" />}
        </motion.span>
      </AnimatePresence>
    </button>
  )
}
