export const PUBLIC_ISSUE_MESSAGE = 'We have encountered an issue, please contact Jarjish 🥲'

let revealTechnicalErrors = false

/** Updated synchronously by AuthProvider whenever the signed-in role changes. */
export function setTechnicalErrorVisibility(reveal: boolean): void {
  revealTechnicalErrors = reveal
}

export function canViewTechnicalErrors(): boolean {
  return revealTechnicalErrors
}

export function visibleErrorMessage(message?: string | null): string {
  if (revealTechnicalErrors && message) return message
  return PUBLIC_ISSUE_MESSAGE
}
