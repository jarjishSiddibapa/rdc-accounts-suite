import { useRef } from 'react'
import { Bold, Italic, Underline, RemoveFormatting } from 'lucide-react'
import { cn } from '@/utils/cn'

interface RichTextEditorProps {
  value: string
  onChange: (html: string) => void
  placeholder?: string
  className?: string
  minHeight?: number
  disabled?: boolean
}

/**
 * Minimal shared rich-text (bold/italic/underline) editor for email body
 * fields, so sent mail can carry real formatting instead of plain text.
 * Built from scratch (contentEditable + document.execCommand) since no
 * rich-text library exists in this project - this is an internal LAN tool,
 * not a public web app, so execCommand's long-standing deprecation-without-
 * removal is an acceptable tradeoff for staying dependency-free.
 *
 * Deliberately uncontrolled after mount: `value` seeds the editor once (see
 * the ref callback below) and is never written back into the DOM on every
 * parent re-render, which would otherwise reset the caret position while
 * typing. Callers that need to force fresh content in (e.g. after
 * regenerating a preview) should remount by changing the component's `key`.
 */
export function RichTextEditor({ value, onChange, placeholder, className, minHeight = 160, disabled }: RichTextEditorProps) {
  const ref = useRef<HTMLDivElement | null>(null)
  const initialized = useRef(false)

  function seedOnce(node: HTMLDivElement | null) {
    ref.current = node
    if (node && !initialized.current) {
      node.innerHTML = value || ''
      initialized.current = true
    }
  }

  function exec(command: string) {
    if (disabled) return
    ref.current?.focus()
    document.execCommand(command)
    if (ref.current) onChange(ref.current.innerHTML)
  }

  const isEmpty = !value || value.replace(/<[^>]*>/g, '').trim() === ''

  return (
    <div className={cn('overflow-hidden rounded-xl border border-border bg-surface', className)}>
      <div className="flex items-center gap-1 border-b border-border bg-bg-soft px-2 py-1.5">
        <ToolbarButton icon={Bold} label="Bold" onClick={() => exec('bold')} disabled={disabled} />
        <ToolbarButton icon={Italic} label="Italic" onClick={() => exec('italic')} disabled={disabled} />
        <ToolbarButton icon={Underline} label="Underline" onClick={() => exec('underline')} disabled={disabled} />
        <div className="mx-1 h-4 w-px bg-border" />
        <ToolbarButton icon={RemoveFormatting} label="Clear formatting" onClick={() => exec('removeFormat')} disabled={disabled} />
      </div>
      <div className="relative">
        {isEmpty && placeholder && (
          <span className="pointer-events-none absolute top-3 left-3 text-sm text-ink-faint">{placeholder}</span>
        )}
        <div
          ref={seedOnce}
          contentEditable={!disabled}
          suppressContentEditableWarning
          onInput={(e) => onChange(e.currentTarget.innerHTML)}
          onBlur={(e) => onChange(e.currentTarget.innerHTML)}
          style={{ minHeight }}
          className={cn(
            'px-3 py-2.5 text-sm leading-relaxed text-ink outline-none',
            disabled && 'cursor-not-allowed opacity-50',
          )}
        />
      </div>
    </div>
  )
}

function ToolbarButton({
  icon: Icon,
  label,
  onClick,
  disabled,
}: {
  icon: typeof Bold
  label: string
  onClick: () => void
  disabled?: boolean
}) {
  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      disabled={disabled}
      onMouseDown={(e) => e.preventDefault()}
      onClick={onClick}
      className="rounded-lg p-1.5 text-ink-dim transition hover:bg-surface hover:text-ink disabled:cursor-not-allowed disabled:opacity-50"
    >
      <Icon className="h-4 w-4" />
    </button>
  )
}
