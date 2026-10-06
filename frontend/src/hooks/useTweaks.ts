/**
 * useTweaks — persisted UI preferences (theme/density/accent).
 *
 * Values are mirrored to `<html data-theme="…" data-density="…">` and to
 * CSS variables so they affect both the design system tokens and the
 * Tailwind layer. Persisted to localStorage so they survive reloads.
 */
import { useCallback, useEffect, useState } from 'react'

export type Theme = 'light' | 'dark'
export type Density = 'comfortable' | 'compact'

export interface Tweaks {
  theme: Theme
  density: Density
  accent: string
}

export const TWEAK_DEFAULTS: Tweaks = {
  theme: 'light',
  density: 'comfortable',
  accent: '#2d6a4f',
}

export const ACCENT_OPTIONS: { value: string; label: string }[] = [
  { value: '#2d6a4f', label: 'bosque' },
  { value: '#3d6a9e', label: 'azul' },
  { value: '#8a4c8a', label: 'lavanda' },
  { value: '#b07a1c', label: 'ámbar' },
  { value: '#b54a36', label: 'arcilla' },
]

const STORAGE_KEY = 'geo-copilot.tweaks.v1'

function loadInitial(): Tweaks {
  if (typeof window === 'undefined') return TWEAK_DEFAULTS
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (!raw) return TWEAK_DEFAULTS
    const parsed = JSON.parse(raw) as Partial<Tweaks>
    return { ...TWEAK_DEFAULTS, ...parsed }
  } catch {
    return TWEAK_DEFAULTS
  }
}

export function useTweaks(): {
  tweaks: Tweaks
  setTweak: <K extends keyof Tweaks>(key: K, value: Tweaks[K]) => void
  reset: () => void
} {
  const [tweaks, setTweaks] = useState<Tweaks>(loadInitial)

  useEffect(() => {
    const root = document.documentElement
    root.dataset.theme = tweaks.theme
    root.dataset.density = tweaks.density
    root.style.setProperty('--accent', tweaks.accent)
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(tweaks))
    } catch {
      /* ignore quota errors */
    }
  }, [tweaks])

  const setTweak = useCallback(
    <K extends keyof Tweaks>(key: K, value: Tweaks[K]) => {
      setTweaks((prev) => ({ ...prev, [key]: value }))
    },
    [],
  )

  const reset = useCallback(() => setTweaks(TWEAK_DEFAULTS), [])

  return { tweaks, setTweak, reset }
}
