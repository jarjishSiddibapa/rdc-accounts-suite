import { Component, type ErrorInfo, type ReactNode } from 'react'

interface Props {
  children: ReactNode
}

interface State {
  failed: boolean
}

export class AppErrorBoundary extends Component<Props, State> {
  state: State = { failed: false }

  static getDerivedStateFromError(): State {
    return { failed: true }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('Application render failed', error, info)
  }

  render() {
    if (this.state.failed) {
      return (
        <main id="main-content" className="grid min-h-[70vh] place-items-center p-6">
          <section className="glass max-w-lg rounded-2xl p-8 text-center" role="alert">
            <h1 className="font-display text-2xl font-semibold text-ink">This page could not load</h1>
            <p className="mt-2 text-sm text-ink-dim">
              Your work has not been submitted. Refresh the page to retry, or return to the dashboard.
            </p>
            <div className="mt-6 flex flex-wrap justify-center gap-3">
              <button
                type="button"
                onClick={() => window.location.reload()}
                className="rounded-xl border border-accent bg-accent px-5 py-2.5 text-sm font-semibold text-white transition hover:bg-accent-2"
              >
                Refresh
              </button>
              <a
                href="/"
                className="rounded-xl border border-border bg-surface px-5 py-2.5 text-sm font-semibold text-ink transition hover:border-accent/35 hover:text-accent"
              >
                Dashboard
              </a>
            </div>
          </section>
        </main>
      )
    }

    return this.props.children
  }
}
