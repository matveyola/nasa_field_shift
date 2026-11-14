const limit = 500

/** Only opaque signal identifiers are persisted, never messages or field measurements. */
export function parseReadNotifications(raw: string | null): string[] {
  try {
    const value: unknown = JSON.parse(raw ?? '[]')
    return Array.isArray(value) ? [...new Set(value.filter((id): id is string =>
      typeof id === 'string' && id.length > 0 && id.length <= 1000))].slice(-limit) : []
  } catch { return [] }
}

export function markNotificationsRead(previous: readonly string[], ids: readonly string[]): string[] {
  const added = new Set(ids)
  return [...previous.filter((id) => !added.has(id)), ...added].slice(-limit)
}
