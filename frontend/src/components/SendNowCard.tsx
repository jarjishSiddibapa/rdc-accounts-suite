import { useState } from 'react'
import { CheckCircle2, Send } from 'lucide-react'
import { GlassCard } from '@/components/GlassCard'
import { Button } from '@/components/Button'
import { ErrorBanner } from '@/components/ErrorBanner'
import { ProgressPanel, type JobState, type JobStatus } from '@/components/ProgressPanel'
import { ApiError, get, post } from '@/lib/api'

interface SendNowResult {
  status: 'sent' | 'held'
  message?: string
  detail?: { recipients?: { to: string[]; cc: string[] }; mails_sent?: number }
}

interface SendNowJob {
  status: JobStatus
  progress: number
  phase: string
  result: SendNowResult | null
  error: string | null
}

async function pollSendNow(baseUrl: string, jobId: string): Promise<JobState<SendNowResult>> {
  const job = await get<SendNowJob>(`${baseUrl}/jobs/${jobId}`)
  return {
    status: job.status,
    progress: (job.progress ?? 0) * 100,
    phase: job.phase,
    result: job.result ?? undefined,
    error: job.error ?? undefined,
  }
}

/**
 * Administrator-only "send the automatic mail right now": the same job as a scheduled run (saved settings,
 * recipients and sender), recorded in the send log like one.
 */
export function SendNowCard({
  baseUrl,
  description,
  onFinished,
}: {
  baseUrl: string
  description: string
  onFinished: () => void
}) {
  const [jobId, setJobId] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<SendNowResult | null>(null)

  async function start() {
    setStarting(true)
    setError(null)
    setResult(null)
    setJobId(null)
    try {
      const res = await post<{ job_id: string }>(`${baseUrl}/auto-mail/send-now`)
      setJobId(res.job_id)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not start the mail.')
      setStarting(false)
    }
  }

  const sentTo = result?.detail?.recipients?.to ?? []

  return (
    <GlassCard padding="lg" className="flex flex-col gap-4">
      <div className="flex flex-col gap-1">
        <h2 className="font-display text-lg font-semibold text-ink">Send the automatic mail now</h2>
        <p className="text-sm leading-6 text-ink-dim">{description}</p>
      </div>
      <div className="flex justify-stretch sm:justify-end">
        <Button icon={<Send className="h-4 w-4" />} loading={starting} onClick={() => void start()}>
          Send now
        </Button>
      </div>
      {error && <ErrorBanner className="whitespace-pre-wrap">{error}</ErrorBanner>}
      {jobId && (
        <ProgressPanel
          jobId={jobId}
          poller={(id) => pollSendNow(baseUrl, id)}
          onDone={(res) => {
            setResult(res ?? null)
            setStarting(false)
            onFinished()
          }}
          onError={(message) => {
            setError(message)
            setStarting(false)
            onFinished()
          }}
          onCancel={() => post(`${baseUrl}/jobs/${jobId}/cancel`)}
        />
      )}
      {result?.status === 'sent' && (
        <p className="flex items-center gap-2 text-sm text-emerald-600">
          <CheckCircle2 className="h-4 w-4" />
          Mail sent{sentTo.length ? ` to ${sentTo.join(', ')}` : ''}.
        </p>
      )}
      {result?.status === 'held' && (
        <ErrorBanner className="whitespace-pre-wrap">{result.message ?? 'The mail was held back.'}</ErrorBanner>
      )}
    </GlassCard>
  )
}
