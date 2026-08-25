import { MailCheck, Send } from 'lucide-react'
import { AppShell } from '@/components/AppShell'
import { GlassCard } from '@/components/GlassCard'
import { ReportRecipientsSection, SystemEmailSection } from '@/components/admin/EmailSettingsSections'

export default function EmailAdministration() {
  return (
    <AppShell title="Email administration">
      <div className="flex flex-col gap-6">
        <GlassCard padding="lg" className="overflow-hidden">
          <div className="flex flex-col justify-between gap-5 md:flex-row md:items-center">
            <div className="max-w-2xl">
              <p className="text-sm font-semibold text-accent">
                Communication controls
              </p>
              <h2 className="mt-1.5 font-display text-2xl font-semibold tracking-[-0.035em] text-ink">
                Email defaults and platform delivery
              </h2>
              <p className="mt-2 text-sm leading-6 text-ink-dim">
                Manage business recipients and the application&apos;s sender identity here. These
                settings are intentionally separate from user accounts and personal report senders.
              </p>
            </div>
            <div className="grid grid-cols-2 gap-2 sm:min-w-72">
              <div className="subpanel flex items-center gap-3 p-3">
                <span className="grid h-9 w-9 place-items-center rounded-xl bg-accent/10 text-accent">
                  <Send className="h-4 w-4" />
                </span>
                <span className="text-xs font-semibold text-ink">Report delivery</span>
              </div>
              <div className="subpanel flex items-center gap-3 p-3">
                <span className="grid h-9 w-9 place-items-center rounded-xl bg-emerald-500/10 text-emerald-600">
                  <MailCheck className="h-4 w-4" />
                </span>
                <span className="text-xs font-semibold text-ink">System sender</span>
              </div>
            </div>
          </div>
        </GlassCard>

        <ReportRecipientsSection />
        <SystemEmailSection />
      </div>
    </AppShell>
  )
}
