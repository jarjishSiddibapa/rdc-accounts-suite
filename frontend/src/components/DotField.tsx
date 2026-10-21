import { useEffect, useRef } from 'react'
import { useReducedMotion } from 'motion/react'
import { cn } from '@/utils/cn'

type RGB = [number, number, number]

interface DotFieldProps {
  className?: string
  /** Resting dot colour. */
  base: RGB
  /** Colour dots blend toward near the pointer and on a click ripple. */
  glow: RGB
  /** Opacity of a resting dot (0-1). */
  baseAlpha?: number
  /** Distance between dots in CSS px. */
  spacing?: number
  /** Pointer influence radius in CSS px. */
  reach?: number
}

interface Ripple {
  x: number
  y: number
  born: number
}

const RIPPLE_MS = 1300
const RIPPLE_SPEED = 0.46 // px per ms
const RIPPLE_WIDTH = 46

/**
 * A grid of dots that drifts on its own, swells and brightens toward the
 * pointer, gets pushed aside by it, and sends a ring out when you click. One
 * canvas, ~a few hundred dots; it stops drawing when hidden or off screen and
 * paints a single still frame when the user prefers reduced motion.
 *
 * It listens on its parent element, so it can sit behind other content.
 */
export function DotField({ className, base, glow, baseAlpha = 0.2, spacing = 26, reach = 170 }: DotFieldProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const reduceMotion = useReducedMotion()

  useEffect(() => {
    const canvas = canvasRef.current
    const host = canvas?.parentElement
    const ctx = canvas?.getContext('2d')
    if (!canvas || !host || !ctx) return

    let width = 0
    let height = 0
    let dpr = 1
    let raf = 0
    let running = false
    let last = 0
    let visible = true
    const pointer = { x: -9999, y: -9999, tx: -9999, ty: -9999, strength: 0, target: 0 }
    const ripples: Ripple[] = []

    const resize = () => {
      const rect = host.getBoundingClientRect()
      width = Math.max(1, Math.round(rect.width))
      height = Math.max(1, Math.round(rect.height))
      dpr = Math.min(window.devicePixelRatio || 1, 2)
      canvas.width = Math.round(width * dpr)
      canvas.height = Math.round(height * dpr)
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      if (!running) draw(performance.now())
    }

    const draw = (now: number) => {
      ctx.clearRect(0, 0, width, height)
      // Easing is time-based so it feels the same at 60 Hz and 144 Hz.
      const dt = last ? Math.min(now - last, 250) : 16
      last = now
      const follow = 1 - Math.exp(-dt / 55)
      pointer.x += (pointer.tx - pointer.x) * follow
      pointer.y += (pointer.ty - pointer.y) * follow
      pointer.strength += (pointer.target - pointer.strength) * (1 - Math.exp(-dt / 110))

      while (ripples.length && now - ripples[0].born > RIPPLE_MS) ripples.shift()

      const cols = Math.ceil(width / spacing) + 1
      const rows = Math.ceil(height / spacing) + 1
      const offsetX = (width - (cols - 1) * spacing) / 2
      const offsetY = (height - (rows - 1) * spacing) / 2
      const t = reduceMotion ? 0 : now * 0.0006

      for (let row = 0; row < rows; row++) {
        for (let col = 0; col < cols; col++) {
          const homeX = offsetX + col * spacing
          const homeY = offsetY + row * spacing

          // Slow travelling swell so the field is alive even when untouched.
          const swell = Math.sin(t + col * 0.32 + row * 0.24) * 0.5 + 0.5
          let radius = 1 + swell * 0.55
          let alpha = baseAlpha * (0.65 + swell * 0.5)
          let mix = 0
          let x = homeX
          let y = homeY

          if (pointer.strength > 0.01) {
            const dx = homeX - pointer.x
            const dy = homeY - pointer.y
            const dist = Math.hypot(dx, dy)
            if (dist < reach) {
              const f = (1 - dist / reach) ** 2 * pointer.strength
              const push = f * 15
              x += (dx / (dist || 1)) * push
              y += (dy / (dist || 1)) * push
              radius += f * 2.8
              alpha += f * (0.95 - alpha)
              mix = Math.max(mix, f)
            }
          }

          for (const ripple of ripples) {
            const age = now - ripple.born
            const dist = Math.hypot(homeX - ripple.x, homeY - ripple.y)
            const ring = Math.exp(-((dist - age * RIPPLE_SPEED) ** 2) / (2 * RIPPLE_WIDTH * RIPPLE_WIDTH))
            const f = ring * (1 - age / RIPPLE_MS)
            if (f > 0.01) {
              radius += f * 2.4
              alpha += f * (0.95 - alpha)
              mix = Math.max(mix, f)
            }
          }

          const r = Math.round(base[0] + (glow[0] - base[0]) * mix)
          const g = Math.round(base[1] + (glow[1] - base[1]) * mix)
          const b = Math.round(base[2] + (glow[2] - base[2]) * mix)
          ctx.fillStyle = `rgba(${r},${g},${b},${Math.min(alpha, 1).toFixed(3)})`
          ctx.beginPath()
          ctx.arc(x, y, radius, 0, Math.PI * 2)
          ctx.fill()
        }
      }
    }

    const frame = (now: number) => {
      if (!running) return
      draw(now)
      raf = requestAnimationFrame(frame)
    }

    const start = () => {
      if (running || reduceMotion || !visible || document.hidden) return
      running = true
      last = 0
      raf = requestAnimationFrame(frame)
    }
    const stop = () => {
      running = false
      cancelAnimationFrame(raf)
    }

    const onMove = (event: PointerEvent) => {
      const rect = host.getBoundingClientRect()
      pointer.tx = event.clientX - rect.left
      pointer.ty = event.clientY - rect.top
      if (pointer.target === 0) {
        pointer.x = pointer.tx
        pointer.y = pointer.ty
      }
      pointer.target = 1
    }
    const onLeave = () => {
      pointer.target = 0
    }
    const onDown = (event: PointerEvent) => {
      const rect = host.getBoundingClientRect()
      ripples.push({ x: event.clientX - rect.left, y: event.clientY - rect.top, born: performance.now() })
      if (ripples.length > 6) ripples.shift()
    }
    const onVisibility = () => (document.hidden ? stop() : start())

    resize()
    const resizeObserver = new ResizeObserver(resize)
    resizeObserver.observe(host)
    const intersection = new IntersectionObserver(([entry]) => {
      visible = entry.isIntersecting
      if (visible) start()
      else stop()
    })
    intersection.observe(host)

    if (!reduceMotion) {
      host.addEventListener('pointermove', onMove)
      host.addEventListener('pointerleave', onLeave)
      host.addEventListener('pointerdown', onDown)
    }
    document.addEventListener('visibilitychange', onVisibility)
    start()

    return () => {
      stop()
      resizeObserver.disconnect()
      intersection.disconnect()
      host.removeEventListener('pointermove', onMove)
      host.removeEventListener('pointerleave', onLeave)
      host.removeEventListener('pointerdown', onDown)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [base, glow, baseAlpha, spacing, reach, reduceMotion])

  return <canvas ref={canvasRef} aria-hidden="true" className={cn('pointer-events-none absolute inset-0 h-full w-full', className)} />
}
