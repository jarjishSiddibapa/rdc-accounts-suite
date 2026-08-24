/**
 * Mirrors app/validation.py's validate_password exactly: length >= 10 and
 * all four character classes (lower/upper/digit/symbol) present is the
 * enforced minimum ("Strong"). Keep both in sync if this changes.
 */

export type PasswordStrengthLevel = 'weak' | 'fair' | 'good' | 'strong'

export interface PasswordStrength {
  level: PasswordStrengthLevel
  label: string
  classes: number
  isAcceptable: boolean
}

const MIN_LENGTH = 10

export function scorePasswordStrength(password: string): PasswordStrength {
  const classes = [/[a-z]/, /[A-Z]/, /[0-9]/, /[^A-Za-z0-9]/].filter((re) => re.test(password)).length

  let level: PasswordStrengthLevel
  if (password.length < MIN_LENGTH || classes <= 1) {
    level = 'weak'
  } else if (classes === 2) {
    level = 'fair'
  } else if (classes === 3) {
    level = 'good'
  } else {
    level = 'strong'
  }

  const labels: Record<PasswordStrengthLevel, string> = {
    weak: 'Weak',
    fair: 'Fair',
    good: 'Good',
    strong: 'Strong',
  }

  return { level, label: labels[level], classes, isAcceptable: level === 'strong' }
}

const LOWER = 'abcdefghijkmnpqrstuvwxyz'
const UPPER = 'ABCDEFGHJKLMNPQRSTUVWXYZ'
const DIGITS = '23456789'
const SYMBOLS = '!@#$%^&*-_+='
const ALL = LOWER + UPPER + DIGITS + SYMBOLS

function randomChar(pool: string): string {
  const bytes = new Uint32Array(1)
  crypto.getRandomValues(bytes)
  return pool[bytes[0] % pool.length]
}

/** Always includes all four classes, so it always scores "Strong". */
export function generateStrongPassword(length = 14): string {
  const required = [randomChar(LOWER), randomChar(UPPER), randomChar(DIGITS), randomChar(SYMBOLS)]
  const rest = Array.from({ length: Math.max(0, length - required.length) }, () => randomChar(ALL))
  const chars = [...required, ...rest]
  // Fisher-Yates shuffle so the required classes aren't always in the same position.
  for (let i = chars.length - 1; i > 0; i--) {
    const bytes = new Uint32Array(1)
    crypto.getRandomValues(bytes)
    const j = bytes[0] % (i + 1)
    ;[chars[i], chars[j]] = [chars[j], chars[i]]
  }
  return chars.join('')
}
