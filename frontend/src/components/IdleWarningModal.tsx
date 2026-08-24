import { useEffect, useState } from 'react'
import { Clock } from 'lucide-react'
import { Modal } from './Modal'
import { Button } from './Button'

const WARNING_SECONDS = 60

export function IdleWarningModal({
  open,
  onStayLoggedIn,
}: {
  open: boolean
  onStayLoggedIn: () => void
}) {
  const [secondsLeft, setSecondsLeft] = useState(WARNING_SECONDS)

  useEffect(() => {
    if (!open) return
    setSecondsLeft(WARNING_SECONDS)
    const interval = window.setInterval(() => {
      setSecondsLeft((s) => Math.max(0, s - 1))
    }, 1000)
    return () => window.clearInterval(interval)
  }, [open])

  return (
    <Modal open={open} onClose={onStayLoggedIn} title="You're about to be signed out">
      <div className="flex flex-col gap-4">
        <p className="flex items-center gap-2 text-sm text-ink-dim">
          <Clock className="h-4 w-4 shrink-0 text-accent" />
          Due to inactivity, you&apos;ll be automatically logged out in{' '}
          <span className="font-semibold text-ink">{secondsLeft}s</span>.
        </p>
        <p className="text-sm text-ink-dim">
          Click below or interact with the page to stay signed in.
        </p>
        <Button onClick={onStayLoggedIn} className="self-end">
          Stay signed in
        </Button>
      </div>
    </Modal>
  )
}
