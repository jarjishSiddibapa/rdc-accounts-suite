import { useCallback, useId, useRef, useState, type DragEvent } from 'react'
import { UploadCloud, FileText, X } from 'lucide-react'
import { cn } from '@/utils/cn'
import { formatIndianNumber } from '@/lib/regional'
import { Pagination } from '@/components/Pagination'
import { usePagination } from '@/hooks/usePagination'

interface FileDropzoneProps {
  onFilesSelected: (files: File[]) => void
  multiple?: boolean
  accept?: string
  label?: string
  hint?: string
  className?: string
  files?: File[]
  onRemove?: (index: number) => void
}

export function FileDropzone({
  onFilesSelected,
  multiple = false,
  accept,
  label = 'Drag & drop a file here, or click to browse',
  hint,
  className,
  files,
  onRemove,
}: FileDropzoneProps) {
  const inputId = useId()
  const inputRef = useRef<HTMLInputElement>(null)
  const [isDragging, setIsDragging] = useState(false)
  const filePagination = usePagination(
    (files ?? []).map((file, index) => ({ file, originalIndex: index })),
    5,
  )

  const handleFiles = useCallback(
    (fileList: FileList | null) => {
      if (!fileList || fileList.length === 0) return
      const arr = Array.from(fileList)
      onFilesSelected(multiple ? arr : [arr[0]])
    },
    [multiple, onFilesSelected],
  )

  const onDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault()
    setIsDragging(false)
    handleFiles(e.dataTransfer.files)
  }

  if (!multiple && files && files.length === 1) {
    const file = files[0]
    return (
      <div className={cn('flex flex-col gap-3', className)}>
        <div className="subpanel flex items-center gap-3 px-3.5 py-2.5 text-sm">
          <FileText className="h-4 w-4 shrink-0 text-accent" />
          <span className="min-w-0 flex-1 truncate text-ink">{file.name}</span>
          <span className="hidden shrink-0 text-xs text-ink-faint sm:inline">
            {formatIndianNumber(Math.round(file.size / 1024))} KB
          </span>
          <button
            type="button"
            onClick={() => inputRef.current?.click()}
            className="shrink-0 rounded-full px-3 py-1.5 text-xs font-semibold text-accent transition hover:bg-accent/10"
          >
            Change file
          </button>
          {onRemove && (
            <button
              type="button"
              onClick={() => onRemove(0)}
              aria-label={`Remove ${file.name}`}
              className="grid h-11 w-11 shrink-0 place-items-center rounded-full text-ink-faint transition hover:bg-bg-soft hover:text-ink sm:h-9 sm:w-9"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          )}
        </div>
        <input
          ref={inputRef}
          type="file"
          accept={accept}
          onChange={(e) => handleFiles(e.target.files)}
          className="hidden"
        />
      </div>
    )
  }

  const hasFiles = !!files && files.length > 0

  return (
    <div className={cn('flex flex-col gap-3', className)}>
      {hasFiles ? (
        <div
          role="button"
          tabIndex={0}
          onClick={() => inputRef.current?.click()}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') inputRef.current?.click()
          }}
          onDragOver={(e) => {
            e.preventDefault()
            setIsDragging(true)
          }}
          onDragLeave={() => setIsDragging(false)}
          onDrop={onDrop}
          className={cn(
            'subpanel flex cursor-pointer items-center justify-center gap-2 border-dashed px-3.5 py-2.5 text-sm font-semibold text-accent transition',
            isDragging ? 'border-accent bg-accent/8' : 'hover:bg-accent/5',
          )}
        >
          <UploadCloud className="h-4 w-4" />
          Add another file
          <input
            id={inputId}
            ref={inputRef}
            type="file"
            accept={accept}
            multiple={multiple}
            onChange={(e) => handleFiles(e.target.files)}
            className="hidden"
          />
        </div>
      ) : (
        <div
          role="button"
          tabIndex={0}
          onClick={() => inputRef.current?.click()}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') inputRef.current?.click()
          }}
          onDragOver={(e) => {
            e.preventDefault()
            setIsDragging(true)
          }}
          onDragLeave={() => setIsDragging(false)}
          onDrop={onDrop}
          className={cn(
            'group relative flex min-h-40 cursor-pointer flex-col items-center justify-center gap-2 overflow-hidden rounded-2xl border border-dashed border-border px-4 py-7 text-center transition duration-300 before:pointer-events-none before:absolute before:inset-0 before:bg-[radial-gradient(circle_at_50%_0%,color-mix(in_oklab,var(--color-accent)_12%,transparent),transparent_58%)] before:opacity-0 before:transition before:duration-300 sm:min-h-0 sm:px-6 sm:py-11',
            isDragging
              ? 'scale-[1.01] border-accent bg-accent/8 shadow-[0_18px_45px_-24px_color-mix(in_oklab,var(--color-accent)_70%,transparent)] before:opacity-100'
              : 'bg-surface/35 hover:-translate-y-0.5 hover:border-accent/50 hover:bg-surface/60 hover:shadow-[0_18px_45px_-30px_rgba(var(--shadow-rgb),0.55)] hover:before:opacity-100',
          )}
        >
          <span className="icon-tile relative grid h-12 w-12 place-items-center rounded-2xl transition duration-300 group-hover:-translate-y-1 group-hover:scale-105">
            <UploadCloud className="h-6 w-6" />
          </span>
          <p className="relative mt-1 text-sm font-semibold text-ink">{label}</p>
          {hint && <p className="text-xs text-ink-faint">{hint}</p>}
          <input
            id={inputId}
            ref={inputRef}
            type="file"
            accept={accept}
            multiple={multiple}
            onChange={(e) => handleFiles(e.target.files)}
            className="hidden"
          />
        </div>
      )}

      {files && files.length > 0 && (
        <div className="flex flex-col gap-3">
          <ul className="flex flex-col gap-2">
            {filePagination.pagedItems.map(({ file, originalIndex }) => (
              <li
                key={`${file.name}-${originalIndex}`}
                className="subpanel flex items-center gap-3 px-3.5 py-2.5 text-sm"
              >
                <FileText className="h-4 w-4 shrink-0 text-accent" />
                <span className="min-w-0 flex-1 truncate text-ink">{file.name}</span>
                <span className="hidden shrink-0 text-xs text-ink-faint sm:inline">
                  {formatIndianNumber(Math.round(file.size / 1024))} KB
                </span>
                {onRemove && (
                  <button
                    type="button"
                    onClick={() => onRemove(originalIndex)}
                    aria-label={`Remove ${file.name}`}
                  className="grid h-11 w-11 shrink-0 place-items-center rounded-full text-ink-faint transition hover:bg-bg-soft hover:text-ink sm:h-9 sm:w-9"
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                )}
              </li>
            ))}
          </ul>
          <Pagination
            page={filePagination.page}
            pageCount={filePagination.pageCount}
            pageSize={filePagination.pageSize}
            totalItems={filePagination.totalItems}
            pageSizeOptions={[5, 10, 25]}
            itemLabel="files"
            onPageChange={filePagination.setPage}
            onPageSizeChange={filePagination.setPageSize}
          />
        </div>
      )}
    </div>
  )
}
