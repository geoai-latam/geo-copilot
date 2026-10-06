import { useEffect, useRef } from 'react'
import { Sliders, X, RotateCcw } from 'lucide-react'
import {
  useTweaks,
  ACCENT_OPTIONS,
  type Theme,
  type Density,
} from '@/hooks/useTweaks'
import { useClickOutside } from '@/hooks'
import { useUIStore, useShowTweaks } from '@/stores'

/**
 * Floating tweaks button + panel. Sits bottom-right. Mounted once at
 * the app root so the theme/density/accent settings apply globally
 * via `<html>` data-attributes and CSS variables (see useTweaks).
 *
 * Apertura: por el botón flotante o por el rail (Settings) vía
 * `useUIStore.showTweaks`.
 */
export function TweaksPanel() {
  const { tweaks, setTweak, reset } = useTweaks()
  const open = useShowTweaks()
  const setOpen = useUIStore((s) => s.setShowTweaks)
  const panelRef = useRef<HTMLDivElement>(null)

  useClickOutside(panelRef, () => setOpen(false), open)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [setOpen])

  return (
    <div
      ref={panelRef}
      style={{
        position: 'fixed',
        right: 12,
        bottom: 40,
        zIndex: 90,
      }}
    >
      {/* El botón flotante "Tweaks" se retiró (parecía un dev-tool). El panel
          de apariencia se abre desde el gear "Ajustes de apariencia" del rail. */}
      {open && (
        <div
          className="panel"
          style={{
            width: 260,
            boxShadow: '0 12px 40px rgba(20, 32, 26, 0.18)',
          }}
        >
          <div className="panel-header">
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: 'var(--text)', fontWeight: 600, fontSize: 13 }}>
              <Sliders className="w-3.5 h-3.5" />
              Apariencia
            </div>
            <div style={{ display: 'flex', gap: 4 }}>
              <button className="icon-btn" title="Restablecer" onClick={reset}>
                <RotateCcw className="w-3.5 h-3.5" />
              </button>
              <button className="icon-btn" title="Cerrar" onClick={() => setOpen(false)}>
                <X className="w-3.5 h-3.5" />
              </button>
            </div>
          </div>

          <div className="panel-body" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            <Section label="apariencia" />
            <Segmented<Theme>
              label="tema"
              value={tweaks.theme}
              options={[
                { value: 'light', label: 'claro' },
                { value: 'dark',  label: 'oscuro' },
              ]}
              onChange={(v) => setTweak('theme', v)}
            />
            <Segmented<Density>
              label="densidad"
              value={tweaks.density}
              options={[
                { value: 'comfortable', label: 'cómoda' },
                { value: 'compact',     label: 'compacta' },
              ]}
              onChange={(v) => setTweak('density', v)}
            />

            <Section label="acento" />
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              {ACCENT_OPTIONS.map((o) => {
                const selected = tweaks.accent === o.value
                return (
                  <button
                    key={o.value}
                    onClick={() => setTweak('accent', o.value)}
                    title={o.label}
                    aria-label={`acento ${o.label}`}
                    style={{
                      width: 26,
                      height: 26,
                      borderRadius: '50%',
                      background: o.value,
                      border: `2px solid ${selected ? 'var(--text)' : 'var(--border)'}`,
                      cursor: 'pointer',
                      padding: 0,
                      boxShadow: selected ? '0 0 0 2px var(--bg) inset' : 'none',
                    }}
                  />
                )
              })}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function Section({ label }: { label: string }) {
  return (
    <div
      style={{
        fontSize: 10,
        fontWeight: 600,
        letterSpacing: '0.06em',
        textTransform: 'uppercase',
        color: 'var(--text-mute)',
      }}
    >
      {label}
    </div>
  )
}

function Segmented<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string
  value: T
  options: { value: T; label: string }[]
  onChange: (v: T) => void
}) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <div style={{ fontSize: 11.5, color: 'var(--text-dim)', fontWeight: 500 }}>{label}</div>
      <div
        style={{
          display: 'flex',
          padding: 2,
          borderRadius: 8,
          background: 'var(--bg-2)',
          border: '1px solid var(--border)',
        }}
      >
        {options.map((o) => {
          const active = value === o.value
          return (
            <button
              key={o.value}
              onClick={() => onChange(o.value)}
              style={{
                flex: 1,
                padding: '5px 8px',
                border: 'none',
                borderRadius: 6,
                background: active ? 'var(--surface)' : 'transparent',
                color: active ? 'var(--text)' : 'var(--text-mute)',
                fontWeight: 500,
                fontSize: 11.5,
                cursor: 'pointer',
                boxShadow: active ? '0 1px 2px rgba(0,0,0,0.08)' : 'none',
                transition: 'all 120ms cubic-bezier(.2,.6,.2,1)',
              }}
            >
              {o.label}
            </button>
          )
        })}
      </div>
    </div>
  )
}
