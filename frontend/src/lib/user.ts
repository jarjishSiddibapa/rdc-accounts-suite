export interface NamedUser {
  email: string
  first_name: string | null
  last_name: string | null
}

export function getUserFullName(user: NamedUser): string | null {
  const name = [user.first_name, user.last_name].filter(Boolean).join(' ').trim()
  return name || null
}

export function getUserDisplayName(user: NamedUser): string {
  return getUserFullName(user) ?? user.email
}

export function getUserGreetingName(user: NamedUser): string {
  if (user.first_name) return user.first_name
  if (user.last_name) return user.last_name
  const localPart = user.email.split('@')[0]?.split(/[._-]+/)[0] || 'there'
  return localPart.charAt(0).toUpperCase() + localPart.slice(1)
}

export function getUserInitials(user: NamedUser): string {
  const parts = [user.first_name, user.last_name].filter(Boolean) as string[]
  if (parts.length > 0) {
    return parts.map((part) => part.charAt(0)).join('').slice(0, 2).toUpperCase()
  }
  return user.email.charAt(0).toUpperCase()
}
