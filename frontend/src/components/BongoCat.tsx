import { useEffect, useRef, useState } from 'react'
import { motion, useMotionValue, useReducedMotion } from 'motion/react'

type CatMode = 'resting' | 'typing' | 'mouse'

const spring = { type: 'spring' as const, stiffness: 540, damping: 24, mass: 0.55 }

function isLoginInput(target: EventTarget | null): target is HTMLInputElement {
  return target instanceof HTMLInputElement && target.dataset.bongoInput === 'true'
}

export function BongoCat() {
  const reduceMotion = useReducedMotion()
  const [mode, setMode] = useState<CatMode>('resting')
  const [typingPaw, setTypingPaw] = useState<'left' | 'right'>('left')
  const [mousePressed, setMousePressed] = useState(false)
  const modeTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const clickTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const pupilX = useMotionValue(0)
  const pupilY = useMotionValue(0)

  useEffect(() => {
    if (reduceMotion) return

    const settleAfter = (delay: number) => {
      if (modeTimer.current) clearTimeout(modeTimer.current)
      modeTimer.current = setTimeout(() => setMode('resting'), delay)
    }

    const handleKeyDown = (event: KeyboardEvent) => {
      if (!isLoginInput(event.target) || ['Alt', 'Control', 'Meta', 'Shift'].includes(event.key)) return
      setMode('typing')
      setTypingPaw((current) => (current === 'left' ? 'right' : 'left'))
      settleAfter(320)
    }

    const handlePointerMove = (event: PointerEvent) => {
      if (event.pointerType === 'touch') return
      pupilX.set(((event.clientX / Math.max(window.innerWidth, 1)) * 2 - 1) * 2.6)
      pupilY.set(((event.clientY / Math.max(window.innerHeight, 1)) * 2 - 1) * 2)
      setMode('mouse')
      settleAfter(760)
    }

    const handlePointerDown = (event: PointerEvent) => {
      if (event.pointerType === 'touch') return
      setMode('mouse')
      setMousePressed(true)
      if (clickTimer.current) clearTimeout(clickTimer.current)
      clickTimer.current = setTimeout(() => setMousePressed(false), 130)
      settleAfter(760)
    }

    window.addEventListener('keydown', handleKeyDown)
    window.addEventListener('pointermove', handlePointerMove, { passive: true })
    window.addEventListener('pointerdown', handlePointerDown, { passive: true })

    return () => {
      window.removeEventListener('keydown', handleKeyDown)
      window.removeEventListener('pointermove', handlePointerMove)
      window.removeEventListener('pointerdown', handlePointerDown)
      if (modeTimer.current) clearTimeout(modeTimer.current)
      if (clickTimer.current) clearTimeout(clickTimer.current)
    }
  }, [pupilX, pupilY, reduceMotion])

  const leftTyping = mode === 'typing' && typingPaw === 'left'
  const rightTyping = mode === 'typing' && typingPaw === 'right'
  const usingMouse = mode === 'mouse'

  return (
    <div className="bongo-cat" data-mode={mode}>
      <svg
        viewBox="0 0 460 238"
        role="img"
        aria-label="Bongo Cat typing along with you and following your pointer"
        className="bongo-cat__scene"
      >
        <ellipse className="bongo-cat__desk-shadow" cx="230" cy="218" rx="188" ry="10" />

        <motion.path
          className="bongo-cat__tail"
          d="M119 146 C72 132 60 167 88 181 C108 191 118 175 103 165"
          animate={reduceMotion ? undefined : { rotate: [0, 3, 0, -2, 0] }}
          transition={{ duration: 3.8, repeat: Infinity, ease: 'easeInOut' }}
          style={{ transformOrigin: '119px 146px' }}
        />

        <path className="bongo-cat__body" d="M137 145 C142 109 169 91 230 91 C291 91 320 112 324 153 L315 193 L145 193 Z" />
        <path className="bongo-cat__body-shade" d="M148 158 C177 177 282 180 316 154 L315 193 L145 193 Z" />

        <path className="bongo-cat__ear" d="M162 69 L170 19 L207 52 Z" />
        <path className="bongo-cat__ear" d="M253 50 L292 18 L300 72 Z" />
        <path className="bongo-cat__ear-inner" d="M174 54 L178 34 L194 51 Z" />
        <path className="bongo-cat__ear-inner" d="M269 49 L286 33 L290 57 Z" />
        <path className="bongo-cat__head" d="M159 70 C164 37 191 31 229 31 C269 31 296 40 301 73 C306 107 282 129 230 129 C180 129 154 107 159 70 Z" />

        <motion.g className="bongo-cat__eye-track" style={{ x: pupilX, y: pupilY }}>
          <g className="bongo-cat__eyes">
            <ellipse cx="202" cy="73" rx="5.5" ry="7" />
            <ellipse cx="260" cy="73" rx="5.5" ry="7" />
          </g>
        </motion.g>
        <path className="bongo-cat__muzzle" d="M224 87 Q230 92 236 87" />
        <path className="bongo-cat__nose" d="M226 82 Q230 78 234 82 Q230 87 226 82 Z" />
        <path className="bongo-cat__whisker" d="M190 89 L157 84 M191 96 L155 99 M270 89 L303 84 M269 96 L305 100" />

        <g className="bongo-cat__keyboard">
          <path className="bongo-cat__keyboard-base" d="M111 170 L340 170 L363 216 L91 216 Z" />
          <path className="bongo-cat__keyboard-top" d="M119 174 L334 174 L349 207 L103 207 Z" />
          <g className="bongo-cat__keys">
            <path d="M126 180 H151 L154 189 H127 Z M158 180 H183 L185 189 H160 Z M190 180 H215 L216 189 H191 Z M222 180 H247 L247 189 H222 Z M254 180 H279 L278 189 H253 Z M286 180 H326 L329 189 H285 Z" />
            <path d="M121 193 H150 L152 202 H119 Z M157 193 H187 L188 202 H155 Z M194 193 H261 L260 202 H192 Z M267 193 H299 L297 202 H265 Z M305 193 H334 L338 202 H303 Z" />
          </g>
        </g>

        <g className="bongo-cat__mouse">
          <ellipse className="bongo-cat__mouse-shadow" cx="397" cy="210" rx="31" ry="6" />
          <path className="bongo-cat__mouse-body" d="M376 185 C377 169 386 161 398 161 C411 161 420 170 421 186 L421 204 C411 213 387 213 376 204 Z" />
          <path className="bongo-cat__mouse-divider" d="M398 162 V180 M377 183 H420" />
          <rect className="bongo-cat__mouse-wheel" x="395" y="168" width="6" height="10" rx="3" />
        </g>

        <motion.g
          className="bongo-cat__mouse-reach"
          animate={{ opacity: usingMouse ? 1 : 0 }}
          transition={{ duration: reduceMotion ? 0 : 0.16 }}
        >
          <path className="bongo-cat__mouse-reach-outline" d="M298 138 C331 140 354 161 383 188" />
          <path className="bongo-cat__mouse-reach-fill" d="M298 138 C331 140 354 161 383 188" />
        </motion.g>

        <motion.g
          className="bongo-cat__paw bongo-cat__paw--left"
          animate={{ y: leftTyping ? -10 : 0, rotate: leftTyping ? -5 : 0 }}
          transition={spring}
          style={{ transformOrigin: '170px 169px' }}
        >
          <path d="M151 130 C139 145 142 169 157 185 L187 184 C192 162 190 142 180 128 Z" />
          <ellipse cx="171" cy="184" rx="25" ry="17" />
          <path className="bongo-cat__toe" d="M160 183 Q164 177 168 183 M174 182 Q178 176 182 182" />
        </motion.g>

        <motion.g
          className="bongo-cat__paw bongo-cat__paw--right"
          animate={{
            x: usingMouse ? 109 : 0,
            y: usingMouse ? (mousePressed ? 20 : 14) : rightTyping ? -10 : 0,
            rotate: usingMouse ? 9 : rightTyping ? 5 : 0,
          }}
          transition={spring}
          style={{ transformOrigin: '290px 169px' }}
        >
          <path d="M280 128 C269 142 268 163 273 184 L305 184 C320 163 319 144 307 130 Z" />
          <ellipse cx="291" cy="184" rx="25" ry="17" />
          <path className="bongo-cat__toe" d="M280 182 Q284 176 288 182 M294 183 Q298 177 302 183" />
        </motion.g>

        <motion.path
          className="bongo-cat__mouth"
          d="M220 97 Q230 108 240 97"
          animate={{ scaleY: mode === 'typing' ? 1.35 : 0.75 }}
          transition={spring}
          style={{ transformOrigin: '230px 99px' }}
        />
      </svg>
      <p className="sr-only">The cat alternates its paws as you type and reaches for the mouse when you move the pointer.</p>
    </div>
  )
}
