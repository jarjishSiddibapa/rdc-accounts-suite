export const PUBLIC_ISSUE_MESSAGE = 'We have encountered an issue, please contact Jarjish 🥲'

let revealTechnicalErrors = false

/** Updated synchronously by AuthProvider whenever the signed-in role changes. */
export function setTechnicalErrorVisibility(reveal: boolean): void {
  revealTechnicalErrors = reveal
}

export function canViewTechnicalErrors(): boolean {
  return revealTechnicalErrors
}

/**
 * API/job errors already arrive pre-filtered by the backend: a raised
 * ValueError/LookupError/domain exception (e.g. "Column 'Vendor Name' not
 * found", "Please upload a valid Excel file") carries its real message all
 * the way through, while a genuine crash is replaced with
 * PUBLIC_ISSUE_MESSAGE itself before it ever reaches here (see
 * app.jobs._run's JobUserError split and main.py's unhandled_exception_handler).
 * So every user - not just admins - should see whatever message comes back.
 * `revealTechnicalErrors` stays reserved for AppErrorBoundary's raw
 * client-side crash text, which has no such backend-side filtering.
 */
export function visibleErrorMessage(message?: string | null): string {
  return message || PUBLIC_ISSUE_MESSAGE
}
