import { useCallback, useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react'
import { AlertTriangle, ChevronLeft, ChevronRight, Crosshair, ImageOff } from 'lucide-react'
import { Button } from '@/components/Button'
import { apiUrl, post } from '@/lib/api'
import { BASE, type StagedDocument, type Stamp } from '@/lib/lcStamper'
import { cn } from '@/utils/cn'

const PAGE_ZOOM = 1.4          // the page image is drawn at 1.4 px per point
const SPRITE_ZOOM = 3          // the stamp image is drawn sharper, so it stays crisp when the page is shown larger
const EDGE_PT = 18             // the gap a preset position keeps from the page edge

interface Sprite {
  png: string
  margin: number
  width: number
  height: number
  box_w: number
  box_h: number
  text: string
}

interface Props {
  documents: StagedDocument[]
  listId: string | null
  stamp: Stamp
  showStamp: boolean
  onMove: (relX: number, relY: number) => void
  onOverflow?: (overflow: boolean) => void
}

const clamp = (value: number, low: number, high: number) => Math.min(high, Math.max(low, value))

/**
 * The page and the stamp, as they will be written: the page image and the stamp image both come from the PDF
 * library that writes the real stamp. Drag the stamp, nudge it with the arrow keys, or pick a preset position.
 */
export function StampCanvas({ documents, listId, stamp, showStamp, onMove, onOverflow }: Props) {
  const [docId, setDocId] = useState<string>('')
  const [page, setPage] = useState(1)
  const [natural, setNatural] = useState<{ w: number; h: number } | null>(null)
  const [imageFailed, setImageFailed] = useState(false)
  const [sprite, setSprite] = useState<Sprite | null>(null)
  const [width, setWidth] = useState(0)
  const frame = useRef<HTMLDivElement>(null)
  const drag = useRef<{ x: number; y: number; relX: number; relY: number } | null>(null)

  const doc = documents.find((d) => d.id === docId) ?? documents[0]
  useEffect(() => {
    if (documents.length && !documents.some((d) => d.id === docId)) {
      setDocId(documents[0].id)
      setPage(1)
    }
  }, [documents, docId])

  useLayoutEffect(() => {
    const node = frame.current
    if (!node) return
    const observer = new ResizeObserver(() => setWidth(node.clientWidth))
    observer.observe(node)
    setWidth(node.clientWidth)
    return () => observer.disconnect()
  }, [doc?.id])

  // the stamp image: redrawn a moment after the text, size, weight or colour changes (not when it is moved)
  const { text, font_size, bold, color } = stamp
  useEffect(() => {
    if (!showStamp || !text.trim()) {
      setSprite(null)
      return
    }
    let stale = false
    const timer = window.setTimeout(async () => {
      try {
        const reply = await post<Sprite>(`${BASE}/stamp-preview`, {
          stamp: { text, font_size, bold, color, rel_x: 0, rel_y: 0 },
          zoom: SPRITE_ZOOM,
          list_id: listId,
          document_name: doc?.name ?? '',
        })
        if (!stale) setSprite(reply)
      } catch {
        if (!stale) setSprite(null)
      }
    }, 220)
    return () => {
      stale = true
      window.clearTimeout(timer)
    }
  }, [showStamp, text, font_size, bold, color, listId, doc?.name])

  const pageW = natural ? natural.w / PAGE_ZOOM : 0            // in points
  const pageH = natural ? natural.h / PAGE_ZOOM : 0
  const scale = natural && width ? width / natural.w : 0         // css px per page-image px
  const spriteScale = pageW && width ? width / (pageW * SPRITE_ZOOM) : 0

  const overflow = Boolean(
    sprite && pageW && (stamp.rel_x * pageW < -0.5 || stamp.rel_y * pageH < -0.5 ||
      stamp.rel_x * pageW + sprite.box_w > pageW + 0.5 || stamp.rel_y * pageH + sprite.box_h > pageH + 0.5),
  )
  useEffect(() => onOverflow?.(overflow), [overflow, onOverflow])

  const moveTo = useCallback((relX: number, relY: number) => {
    onMove(Math.round(relX * 10000) / 10000, Math.round(relY * 10000) / 10000)
  }, [onMove])

  function onPointerDown(event: PointerEvent<HTMLDivElement>) {
    event.currentTarget.setPointerCapture(event.pointerId)
    drag.current = { x: event.clientX, y: event.clientY, relX: stamp.rel_x, relY: stamp.rel_y }
  }
  function onPointerMove(event: PointerEvent<HTMLDivElement>) {
    const start = drag.current
    if (!start || !width || !natural) return
    const heightCss = (width * natural.h) / natural.w
    moveTo(clamp(start.relX + (event.clientX - start.x) / width, -0.3, 1.3),
      clamp(start.relY + (event.clientY - start.y) / heightCss, -0.3, 1.3))
  }
  function onKey(event: KeyboardEvent<HTMLDivElement>) {
    const step = (event.shiftKey ? 5 : 0.5)
    const delta: Record<string, [number, number]> = {
      ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step],
    }
    const move = delta[event.key]
    if (!move || !pageW) return
    event.preventDefault()
    moveTo(stamp.rel_x + move[0] / pageW, stamp.rel_y + move[1] / pageH)
  }

  function place(horizontal: 'left' | 'center' | 'right', vertical: 'top' | 'middle' | 'bottom') {
    if (!sprite || !pageW) return
    const x = horizontal === 'left' ? EDGE_PT : horizontal === 'right' ? pageW - sprite.box_w - EDGE_PT
      : (pageW - sprite.box_w) / 2
    const y = vertical === 'top' ? EDGE_PT : vertical === 'bottom' ? pageH - sprite.box_h - EDGE_PT
      : (pageH - sprite.box_h) / 2
    moveTo(x / pageW, y / pageH)
  }

  function moveInside() {
    if (!sprite || !pageW) return
    moveTo(clamp(stamp.rel_x, 0, Math.max(0, (pageW - sprite.box_w) / pageW)),
      clamp(stamp.rel_y, 0, Math.max(0, (pageH - sprite.box_h) / pageH)))
  }

  if (!doc) {
    return (
      <div className="grid min-h-64 place-items-center rounded-xl border border-dashed border-border p-6 text-center text-sm text-ink-dim">
        <div className="flex flex-col items-center gap-2">
          <ImageOff className="h-6 w-6 text-ink-faint" />
          Upload a PDF to see its page here and place the stamp.
        </div>
      </div>
    )
  }

  const heightCss = natural && width ? (width * natural.h) / natural.w : undefined
  const rows: ['top' | 'middle' | 'bottom', string][] = [['top', 'Top'], ['middle', 'Middle'], ['bottom', 'Bottom']]
  const cols: ['left' | 'center' | 'right', string][] = [['left', 'Left'], ['center', 'Centre'], ['right', 'Right']]

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        {documents.length > 1 && (
          <select
            aria-label="Document to preview"
            value={doc.id}
            onChange={(e) => { setDocId(e.target.value); setPage(1); setNatural(null) }}
            className="min-h-10 min-w-0 max-w-full flex-1 rounded-lg border border-border bg-surface px-3 text-sm text-ink"
          >
            {documents.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
          </select>
        )}
        <div className="ml-auto flex items-center gap-1">
          <button type="button" aria-label="Previous page" disabled={page <= 1}
            onClick={() => { setPage((p) => Math.max(1, p - 1)); setNatural(null) }}
            className="grid h-10 w-10 place-items-center rounded-lg border border-border bg-surface text-ink disabled:opacity-40">
            <ChevronLeft className="h-4 w-4" />
          </button>
          <label className="flex items-center gap-1 text-sm text-ink-dim">
            Page
            <input
              type="number" min={1} max={doc.pages} value={page}
              onChange={(e) => { setPage(clamp(Number(e.target.value) || 1, 1, doc.pages)); setNatural(null) }}
              className="h-10 w-16 rounded-lg border border-border bg-surface px-2 text-center text-sm text-ink"
            />
            of {doc.pages}
          </label>
          <button type="button" aria-label="Next page" disabled={page >= doc.pages}
            onClick={() => { setPage((p) => Math.min(doc.pages, p + 1)); setNatural(null) }}
            className="grid h-10 w-10 place-items-center rounded-lg border border-border bg-surface text-ink disabled:opacity-40">
            <ChevronRight className="h-4 w-4" />
          </button>
        </div>
      </div>

      <div
        ref={frame}
        className="relative w-full select-none overflow-hidden rounded-xl border border-border bg-white shadow-sm"
        style={{ height: heightCss, minHeight: heightCss ? undefined : 240 }}
      >
        {imageFailed ? (
          <div className="grid h-60 place-items-center text-sm text-ink-dim">This page could not be shown.</div>
        ) : (
          <img
            key={`${doc.id}-${page}`}
            src={apiUrl(`${BASE}/documents/${doc.id}/pages/${page}?zoom=${PAGE_ZOOM}`)}
            alt={`Page ${page} of ${doc.name}`}
            draggable={false}
            onLoad={(e) => { setImageFailed(false); setNatural({ w: e.currentTarget.naturalWidth, h: e.currentTarget.naturalHeight }) }}
            onError={() => setImageFailed(true)}
            className="block w-full"
          />
        )}
        {showStamp && sprite && natural && scale > 0 && (
          <div
            role="slider"
            tabIndex={0}
            aria-label="Stamp position. Drag it, or use the arrow keys."
            aria-valuetext={`${Math.round(stamp.rel_x * 100)}% from the left, ${Math.round(stamp.rel_y * 100)}% from the top`}
            onPointerDown={onPointerDown}
            onPointerMove={onPointerMove}
            onPointerUp={() => { drag.current = null }}
            onKeyDown={onKey}
            className={cn(
              'absolute cursor-grab touch-none outline-offset-2 hover:outline hover:outline-1 hover:outline-dashed hover:outline-accent',
              'focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent active:cursor-grabbing',
              overflow && 'outline outline-2 outline-red-500',
            )}
            style={{
              left: stamp.rel_x * width - sprite.margin * spriteScale,
              top: stamp.rel_y * (heightCss ?? 0) - sprite.margin * spriteScale,
              width: sprite.width * spriteScale,
              height: sprite.height * spriteScale,
              backgroundImage: `url(data:image/png;base64,${sprite.png})`,
              backgroundSize: '100% 100%',
            }}
          />
        )}
      </div>

      {showStamp && (
        <>
          {overflow && (
            <div className="flex flex-wrap items-center gap-2 rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-600 dark:text-red-400">
              <AlertTriangle className="h-4 w-4 shrink-0" />
              <span className="min-w-0 flex-1">The stamp runs off the page.</span>
              <Button variant="secondary" className="!min-h-9 !px-3 !py-1.5" onClick={moveInside}>Move inside</Button>
            </div>
          )}
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            <div className="grid grid-cols-3 gap-1" role="group" aria-label="Preset positions">
              {rows.flatMap(([v, vl]) => cols.map(([h, hl]) => (
                <button
                  key={`${v}-${h}`} type="button" title={`${vl} ${hl}`} aria-label={`${vl} ${hl}`}
                  disabled={!sprite} onClick={() => place(h, v)}
                  className="grid h-8 w-8 place-items-center rounded-md border border-border bg-surface text-ink-faint transition hover:border-accent hover:text-accent disabled:opacity-40"
                >
                  <span className="h-1.5 w-1.5 rounded-full bg-current" />
                </button>
              )))}
            </div>
            <p className="flex min-w-0 flex-1 items-center gap-2 text-xs text-ink-dim">
              <Crosshair className="h-4 w-4 shrink-0 text-accent" />
              Drag the stamp on the page, use the arrow keys (Shift for bigger steps), or pick a corner.
              {sprite && <span className="text-ink-faint"> It is drawn exactly like this on every page.</span>}
            </p>
          </div>
        </>
      )}
    </div>
  )
}
