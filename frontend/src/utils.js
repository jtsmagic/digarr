const DEFAULT_TIMEZONE = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';

/**
 * Format a UTC ISO timestamp for display in the user's configured timezone.
 * Handles timestamps stored without a Z suffix (appends it before parsing).
 */
export function formatDate(iso, timezone = DEFAULT_TIMEZONE) {
  if (!iso) return '—';
  const utc = (iso.endsWith('Z') || iso.includes('+')) ? iso : iso + 'Z';
  return new Date(utc).toLocaleString('en-US', {
    month: 'short', day: 'numeric', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
    timeZone: timezone,
  });
}
