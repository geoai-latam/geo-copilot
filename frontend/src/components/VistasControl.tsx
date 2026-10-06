/** FH.10 — marcadores del mapa: guardar la vista actual con un nombre y volver a ella. */
import { useEffect, useState } from 'react'
import { Bookmark, Plus, X } from 'lucide-react'

import { borrarVista, guardarVista, irAVista, useVistas } from '@/lib/vistas'
import { useSessionStore } from '@/stores/sessionStore'

export function VistasControl() {
  const sessionId = useSessionStore((s) => s.sessionId)
  const vistas = useVistas((s) => s.vistas)
  const [abierto, setAbierto] = useState(false)
  const [nombre, setNombre] = useState('')

  useEffect(() => useVistas.getState().cargar(sessionId), [sessionId])

  return (
    <div className="vistas-control" data-testid="vistas-control">
      <button type="button" className="icon-btn" aria-expanded={abierto} aria-label="Vistas guardadas"
              title="Vistas guardadas" onClick={() => setAbierto((a) => !a)}>
        <Bookmark className="w-3.5 h-3.5" />
        {vistas.length > 0 && <span className="vistas-n">{vistas.length}</span>}
      </button>
      {abierto && (
        <div className="vistas-panel">
          <form className="vistas-nueva" onSubmit={(e) => { e.preventDefault(); if (guardarVista(nombre)) setNombre('') }}>
            <input className="input" aria-label="Nombre de la vista" placeholder="Nombre de esta vista"
                   value={nombre} onChange={(e) => setNombre(e.target.value)} />
            <button type="submit" className="icon-btn" aria-label="Guardar esta vista" disabled={!nombre.trim()}>
              <Plus className="w-3.5 h-3.5" />
            </button>
          </form>
          {vistas.length === 0 && <p className="vistas-vacio">Aún no hay vistas guardadas.</p>}
          <ul>
            {vistas.map((v) => (
              <li key={v.id}>
                <button type="button" className="vistas-ir" onClick={() => irAVista(v.id)}>{v.nombre}</button>
                <button type="button" className="icon-btn" aria-label={`Borrar la vista ${v.nombre}`}
                        onClick={() => borrarVista(v.id)}><X className="w-3 h-3" /></button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
