import { createContext, useContext, type ReactNode } from 'react'
import { motion, useReducedMotion } from 'motion/react'
import { cn } from '@/utils/cn'

interface RevealProps {
  children: ReactNode
  className?: string
  delay?: number
  y?: number
  once?: boolean
}

const RevealGroupContext = createContext(false)

const enterEase = [0.16, 1, 0.3, 1] as const

export function Reveal({ children, className, delay = 0, y = 16, once = true }: RevealProps) {
  const grouped = useContext(RevealGroupContext)
  const reduceMotion = useReducedMotion()
  const variants = {
    hidden: { opacity: 0, y },
    show: {
      opacity: 1,
      y: 0,
      transition: { duration: 0.58, delay, ease: enterEase },
    },
  }

  if (grouped) {
    return (
      <motion.div className={cn(className)} variants={reduceMotion ? undefined : variants}>
        {children}
      </motion.div>
    )
  }

  return (
    <motion.div
      className={cn(className)}
      initial={reduceMotion ? false : 'hidden'}
      whileInView="show"
      viewport={{ once, amount: 0.16 }}
      variants={variants}
    >
      {children}
    </motion.div>
  )
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
  const reduceMotion = useReducedMotion()
  return (
    <RevealGroupContext.Provider value>
      <motion.div
        className={cn(className)}
        initial={reduceMotion ? false : 'hidden'}
        whileInView="show"
        viewport={{ once: true, amount: 0.08 }}
        variants={{
          hidden: {},
          show: { transition: { staggerChildren: stagger, delayChildren: 0.04 } },
        }}
      >
        {children}
      </motion.div>
    </RevealGroupContext.Provider>
  )
}
