import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { Check, ChevronDown, Plus, Search } from 'lucide-react'
import { cn } from '@/utils/cn'

interface CreatableComboboxProps {
  value: string
  options: string[]
  onChange: (value: string) => void
  placeholder?: string
  disabled?: boolean
  className?: string
}

interface MenuItem {
  value: string
  isNew: boolean
}

const MAX_VISIBLE_OPTIONS = 60

/**
 * An editable combobox: focusing it reveals existing values, typing filters
 * them, and unmatched text remains a valid new value.
 */
export function CreatableCombobox({
  value,
  options,
  onChange,
  placeholder = 'Choose an existing value or type a new one',
  disabled = false,
  className,
}: CreatableComboboxProps) {
  const rootRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const listboxId = useId()
  const [open, setOpen] = useState(false)
  const [highlightedIndex, setHighlightedIndex] = useState(0)

  const normalizedOptions = useMemo(() => {
    const seen = new Set<string>()
    return options
      .map((option) => option.trim())
      .filter((option) => {
        const key = option.toLocaleLowerCase()
        if (!option || seen.has(key)) return false
        seen.add(key)
        return true
      })
      .sort((a, b) => a.localeCompare(b, undefined, { sensitivity: 'base', numeric: true }))
  }, [options])

  const menuItems = useMemo<MenuItem[]>(() => {
    const query = value.trim().toLocaleLowerCase()
    const matching = normalizedOptions
      .filter((option) => !query || option.toLocaleLowerCase().includes(query))
      .sort((a, b) => {
        if (!query) return 0
        const aStarts = a.toLocaleLowerCase().startsWith(query)
        const bStarts = b.toLocaleLowerCase().startsWith(query)
        return aStarts === bStarts ? 0 : aStarts ? -1 : 1
      })
      .slice(0, MAX_VISIBLE_OPTIONS)
      .map((option) => ({ value: option, isNew: false }))

    const hasExactMatch = normalizedOptions.some(
      (option) => option.toLocaleLowerCase() === query,
    )
    if (value.trim() && !hasExactMatch) matching.push({ value: value.trim(), isNew: true })
    return matching
  }, [normalizedOptions, value])

  useEffect(() => {
    const closeWhenClickingOutside = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener('pointerdown', closeWhenClickingOutside)
    return () => document.removeEventListener('pointerdown', closeWhenClickingOutside)
  }, [])

  useEffect(() => {
    setHighlightedIndex(0)
  }, [value, open])

  function selectItem(item: MenuItem) {
    onChange(item.value)
    setOpen(false)
    window.requestAnimationFrame(() => inputRef.current?.focus())
  }

  return (
    <div ref={rootRef} className={cn('relative', className)}>
      <Search className="pointer-events-none absolute top-1/2 left-3.5 z-10 h-4 w-4 -translate-y-1/2 text-ink-faint" />
      <input
        ref={inputRef}
        role="combobox"
        aria-autocomplete="list"
        aria-expanded={open}
        aria-controls={listboxId}
        aria-activedescendant={open && menuItems[highlightedIndex] ? `${listboxId}-${highlightedIndex}` : undefined}
        autoComplete="off"
        value={value}
        disabled={disabled}
        placeholder={placeholder}
        onFocus={() => setOpen(true)}
        onClick={() => setOpen(true)}
        onChange={(event) => {
          onChange(event.target.value)
          setOpen(true)
        }}
        onKeyDown={(event) => {
          if (event.key === 'ArrowDown') {
            event.preventDefault()
            setOpen(true)
            setHighlightedIndex((current) => Math.min(current + 1, Math.max(0, menuItems.length - 1)))
          } else if (event.key === 'ArrowUp') {
            event.preventDefault()
            setOpen(true)
            setHighlightedIndex((current) => Math.max(0, current - 1))
          } else if (event.key === 'Enter' && open && menuItems[highlightedIndex]) {
            event.preventDefault()
            selectItem(menuItems[highlightedIndex])
          } else if (event.key === 'Escape') {
            event.preventDefault()
            setOpen(false)
          }
        }}
        className="field-control w-full pr-11 pl-10"
      />
      <button
        type="button"
        tabIndex={-1}
        disabled={disabled}
        aria-label={open ? 'Close suggestions' : 'Show existing values'}
        onClick={() => {
          setOpen((current) => !current)
          inputRef.current?.focus()
        }}
        className="absolute top-1/2 right-2 z-10 grid h-8 w-8 -translate-y-1/2 place-items-center rounded-lg text-ink-faint transition hover:bg-bg-soft hover:text-accent disabled:opacity-40"
      >
        <ChevronDown className={cn('h-4 w-4 transition-transform duration-200', open && 'rotate-180')} />
      </button>

      <AnimatePresence>
        {open && !disabled && (
          <motion.div
            id={listboxId}
            role="listbox"
            initial={{ opacity: 0, y: -6, scale: 0.985 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -4, scale: 0.99 }}
            transition={{ duration: 0.18, ease: [0.16, 1, 0.3, 1] }}
            className="glass absolute z-30 mt-2 max-h-64 w-full overflow-y-auto rounded-xl border-border p-1.5 shadow-[0_24px_55px_-22px_rgba(var(--shadow-rgb),0.65)]"
          >
            <div className="flex items-center justify-between px-2.5 py-2 text-[10px] font-bold tracking-[0.12em] text-ink-faint uppercase">
              <span>{value.trim() ? 'Matching values' : 'Existing values'}</span>
              <span>{Math.min(menuItems.filter((item) => !item.isNew).length, MAX_VISIBLE_OPTIONS)}</span>
            </div>

            {menuItems.map((item, index) => (
              <button
                id={`${listboxId}-${index}`}
                key={`${item.isNew ? 'new' : 'existing'}-${item.value}`}
                type="button"
                role="option"
                aria-selected={highlightedIndex === index}
                onPointerEnter={() => setHighlightedIndex(index)}
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => selectItem(item)}
                className={cn(
                  'flex w-full items-center gap-2.5 rounded-lg px-3 py-2.5 text-left text-sm transition duration-150',
                  highlightedIndex === index ? 'bg-accent/10 text-accent' : 'text-ink hover:bg-bg-soft',
                )}
              >
                {item.isNew ? (
                  <Plus className="h-4 w-4 shrink-0" />
                ) : (
                  <Check
                    className={cn(
                      'h-4 w-4 shrink-0',
                      item.value === value ? 'opacity-100' : 'opacity-0',
                    )}
                  />
                )}
                <span className="min-w-0 flex-1 truncate">
                  {item.isNew ? `Use new value “${item.value}”` : item.value}
                </span>
              </button>
            ))}

            {menuItems.length === 0 && (
              <p className="px-3 py-4 text-center text-xs leading-5 text-ink-faint">
                No existing values yet. Start typing to add the first one.
              </p>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
