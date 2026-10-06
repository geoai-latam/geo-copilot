/**
 * Hook to detect clicks outside a referenced element
 */
import { useEffect, RefObject } from 'react'

/**
 * Calls callback when user clicks outside the referenced element
 * @param ref - Reference to the element to monitor
 * @param callback - Function to call when clicking outside
 * @param enabled - Whether the listener is active (default: true)
 */
export function useClickOutside<T extends HTMLElement>(
  ref: RefObject<T>,
  callback: () => void,
  enabled: boolean = true
): void {
  useEffect(() => {
    if (!enabled) return

    function handleClickOutside(event: MouseEvent) {
      if (ref.current && !ref.current.contains(event.target as Node)) {
        callback()
      }
    }

    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [ref, callback, enabled])
}
