import type { ReactNode } from 'react'
import { motion, useReducedMotion } from 'motion/react'
import { useShellTitle } from '@/components/ShellLayout'

/**
 * One page's content area. The frame around it (sidebar, top bar, footer) is
 * the persistent ShellLayout; this names the page in the top bar and plays the
 * page-enter motion each time the route changes.
 */
export function AppShell({ title, children }: { title: string; children: ReactNode }) {
  const reduceMotion = useReducedMotion()
  useShellTitle(title)

  return (
    <motion.main
      id="main-content"
      className="min-w-0 w-full flex-1 pb-4"
      initial={reduceMotion ? false : { opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.36, ease: [0.16, 1, 0.3, 1] }}
    >
      {children}
    </motion.main>
  )
}
