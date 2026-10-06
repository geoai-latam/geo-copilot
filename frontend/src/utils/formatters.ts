/**
 * Formatting utilities for dates, numbers, and values
 */

/**
 * Formats a date to a localized time string (HH:MM)
 */
export function formatTime(date: Date | string): string {
  return new Date(date).toLocaleTimeString('es-CO', {
    hour: '2-digit',
    minute: '2-digit',
  })
}

/**
 * Formats a value for display in tables/lists
 * Handles null, undefined, and objects
 */
export function formatValue(value: unknown): string {
  if (value === null || value === undefined) return '-'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

/**
 * Formats large numbers with K/M suffixes
 */
export function formatLargeNumber(val: string | number): string {
  if (typeof val === 'number') {
    if (val >= 1000000) return `${(val / 1000000).toFixed(1)}M`
    if (val >= 1000) return `${(val / 1000).toFixed(1)}K`
    return val.toLocaleString()
  }
  return String(val)
}
