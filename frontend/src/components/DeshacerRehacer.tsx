/**
 * FH.1 — deshacer/rehacer del mapa compartido (botones + Ctrl+Z / Ctrl+Shift+Z / Ctrl+Y).
 *
 * Deshace por igual lo que hizo el usuario y lo que hizo el agente; el título del
 * botón dice QUÉ se va a deshacer y de quién es. Los atajos no actúan mientras se
 * escribe (el Ctrl+Z del chat es el del texto).
 */
import { useEffect } from 'react'
import { Redo2, Undo2 } from 'lucide-react'

import { useOperaciones, type Entrada } from '@/lib/operaciones'

const QUE: Record<string, string> = {
  add_layer: 'añadir', replace_layer: 'estilo de', set_style: 'estilo de', set_visibility: 'visibilidad de',
  set_opacity: 'opacidad de', reorder: 'orden de', set_label: 'etiquetas de', remove_layer: 'quitar',
}

function describir(e: Entrada | undefined): string {
  if (!e) return ''
  const quien = e.author === 'agent' ? 'agente' : 'tú'
  return `${QUE[e.op] ?? e.op} «${e.layer_name ?? e.layer_id ?? 'mapa'}» (${quien})`
}

function escribiendo(t: EventTarget | null): boolean {
  const el = t as HTMLElement | null
  return !!el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT' || el.isContentEditable)
}

export function DeshacerRehacer() {
  const registro = useOperaciones((s) => s.registro)
  const pilaRehacer = useOperaciones((s) => s.rehacer)
  const deshacer = useOperaciones((s) => s.deshacer)
  const rehacer = useOperaciones((s) => s.rehacerUltima)

  const siguiente = [...registro].reverse().find((e) => !e.deshecha && e.inversa)
  const aRehacer = registro.find((e) => e.id === pilaRehacer[pilaRehacer.length - 1])

  useEffect(() => {
    const onKey = (ev: KeyboardEvent) => {
      if (!(ev.ctrlKey || ev.metaKey) || escribiendo(ev.target)) return
      const k = ev.key.toLowerCase()
      if (k === 'z' && !ev.shiftKey) {
        ev.preventDefault()
        useOperaciones.getState().deshacer()
      } else if ((k === 'z' && ev.shiftKey) || k === 'y') {
        ev.preventDefault()
        useOperaciones.getState().rehacerUltima()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  return (
    <>
      <button
        className="gc-btn gc-btn-ghost"
        onClick={() => deshacer()}
        disabled={!siguiente}
        title={siguiente ? `Deshacer: ${describir(siguiente)} — Ctrl+Z` : 'Nada que deshacer'}
        aria-label="Deshacer"
        data-testid="deshacer"
      >
        <Undo2 className="w-3.5 h-3.5" />
      </button>
      <button
        className="gc-btn gc-btn-ghost"
        onClick={() => rehacer()}
        disabled={!aRehacer}
        title={aRehacer ? `Rehacer: ${describir(aRehacer)} — Ctrl+Shift+Z` : 'Nada que rehacer'}
        aria-label="Rehacer"
        data-testid="rehacer"
      >
        <Redo2 className="w-3.5 h-3.5" />
      </button>
    </>
  )
}
