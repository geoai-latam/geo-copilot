/** FH.11 — guardar el proyecto (mapa + conversación) y abrir uno guardado. */
import { useState } from 'react'
import { FolderOpen, Save } from 'lucide-react'

import { abrirProyecto, guardarProyecto } from '@/lib/proyectos'
import { proyectosApi, type ProyectoResumen } from '@/services/api'

export function ProyectosControl() {
  const [panel, setPanel] = useState<'guardar' | 'abrir' | null>(null)
  const [nombre, setNombre] = useState('')
  const [lista, setLista] = useState<ProyectoResumen[] | null>(null)
  const [aviso, setAviso] = useState<string | null>(null)

  const abrirPanel = async (p: 'guardar' | 'abrir') => {
    setAviso(null)
    setPanel(panel === p ? null : p)
    if (p === 'abrir') {
      try { setLista((await proyectosApi.listar()).proyectos) } catch (e) { setAviso(String(e)) }
    }
  }
  const guardar = async () => {
    try {
      const r = await guardarProyecto(nombre)
      setAviso(`Guardado «${r.nombre}»`)
      setNombre('')
    } catch (e) {
      setAviso(`No se pudo guardar: ${e instanceof Error ? e.message : String(e)}`)
    }
  }

  return (
    <div className="proyectos" data-testid="proyectos">
      <button type="button" className="icon-btn" onClick={() => void abrirPanel('guardar')} aria-label="Guardar proyecto"
              title="Guardar el proyecto (mapa + conversación)"><Save className="w-3.5 h-3.5" /></button>
      <button type="button" className="icon-btn" onClick={() => void abrirPanel('abrir')} aria-label="Abrir proyecto"
              title="Abrir un proyecto guardado"><FolderOpen className="w-3.5 h-3.5" /></button>
      {panel && (
        <div className="proyectos-panel">
          {panel === 'guardar' ? (
            <form onSubmit={(e) => { e.preventDefault(); if (nombre.trim()) void guardar() }} className="proyectos-form">
              <input className="input" aria-label="Nombre del proyecto" placeholder="Nombre del proyecto"
                     value={nombre} onChange={(e) => setNombre(e.target.value)} />
              <button type="submit" className="btn btn-primary" disabled={!nombre.trim()}>Guardar</button>
            </form>
          ) : (
            <ul className="proyectos-lista">
              {lista?.length === 0 && <li className="vistas-vacio">Aún no hay proyectos.</li>}
              {lista?.map((p) => (
                <li key={p.id}>
                  <button type="button" onClick={() => void abrirProyecto(p.id)} title={`Sesión ${p.workspace_id}`}>
                    <strong>{p.nombre}</strong>
                    <span>{p.capas} capa(s) · {new Date(p.updated_at).toLocaleString()}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
          {aviso && <p className="proyectos-aviso" role="status">{aviso}</p>}
        </div>
      )}
    </div>
  )
}
