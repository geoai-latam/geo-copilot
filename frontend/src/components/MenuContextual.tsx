/**
 * FH.8 — menú contextual: clic derecho sobre un elemento (queda seleccionado) o «Acciones»
 * de una capa → las capacidades del registro que admiten ese tipo de geometría
 * (GET /acciones, lo declara cada capacidad: núcleo o MCP). Elegir una abre su formulario
 * (el del `input_schema`, con lo señalado ya puesto) y ejecuta la MISMA capacidad que usa el
 * agente; el resultado va al mapa como capa del usuario y sus hechos se muestran aquí.
 */
import { useEffect, useMemo, useState } from 'react'
import { Loader2, Play, X } from 'lucide-react'

import { useMenuContextual } from '@/lib/menuContextual'
import { aplicarResultado } from '@/lib/resultadoHerramienta'
import { accionesApi, type Accion, type McpRunResult } from '@/services/api'
import { useLayers, useSessionStore } from '@/stores'
import { buildMapContext } from '@/utils/mapContext'
import { Campo, Resultado } from './McpToolsPanel'
import { buildArguments, formFields } from './mcpTools.helpers'
import { paraCapa, valorObjetivo } from './MenuContextual.helpers'

const cache = new Map<string, Promise<Accion[]>>()
function accionesPara(geometria: string | null): Promise<Accion[]> {
  const k = geometria ?? '*'
  if (!cache.has(k)) {
    cache.set(k, accionesApi.listar(geometria).then((r) => r.acciones).catch((e) => { cache.delete(k); throw e }))
  }
  return cache.get(k)!
}

export function MenuContextual() { // eslint-disable-line complexity -- deuda congelada (F1); partir, no subir
  const abierto = useMenuContextual((s) => s.abierto)
  const cerrar = useMenuContextual((s) => s.cerrar)
  const capas = useLayers()
  const sessionId = useSessionStore((s) => s.sessionId)
  const [acciones, setAcciones] = useState<Accion[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [elegida, setElegida] = useState<Accion | null>(null)
  const [valores, setValores] = useState<Record<string, string>>({})
  const [corriendo, setCorriendo] = useState(false)
  const [resultado, setResultado] = useState<McpRunResult | null>(null)

  const capa = abierto ? capas.find((l) => l.id === abierto.capaId) : undefined

  useEffect(() => {
    setAcciones(null); setError(null); setElegida(null); setValores({}); setResultado(null)
    if (!abierto) return
    let vivo = true
    accionesPara(abierto.geometria)
      .then((a) => { if (vivo) setAcciones(a) })
      .catch((e) => { if (vivo) setError(e instanceof Error ? e.message : String(e)) })
    return () => { vivo = false }
  }, [abierto])

  useEffect(() => {
    if (!abierto) return
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') cerrar() }
    window.addEventListener('keydown', esc)
    return () => window.removeEventListener('keydown', esc)
  }, [abierto, cerrar])

  // el formulario sin el argumento objetivo (ese lo pone lo señalado)
  const campos = useMemo(() => (elegida ? formFields(elegida).filter((f) => f.name !== elegida.objetivo) : []), [elegida])

  if (!abierto || !capa) return null
  const lista = acciones ? paraCapa(acciones, capa, abierto.alcance) : []
  const sobre = abierto.alcance === 'seleccion'
    ? `lo seleccionado en «${capa.name}» (${capa.seleccion?.count ?? 0})` : `la capa «${capa.name}»`

  const ejecutar = async () => {
    if (!elegida || !sessionId) return
    const objetivo = valorObjetivo(elegida, capa, abierto.alcance)
    if (!objetivo.ok) { setError(objetivo.motivo); return }
    const resto = buildArguments(campos, valores, capas.map((l) => ({ id: l.id, name: l.name, datasetId: l.datasetId, data: l.data })))
    if (!resto.ok) { setError(resto.error); return }
    setCorriendo(true); setError(null); setResultado(null)
    try {
      const res = await accionesApi.run(elegida.herramienta, {
        session_id: sessionId, arguments: { ...resto.args, [elegida.objetivo]: objetivo.valor }, map_context: buildMapContext(),
      })
      setResultado(res)
      if (!res.success) { setError(res.message ?? 'La acción no devolvió resultado.'); return }
      aplicarResultado(res, `${elegida.titulo} · ${capa.name}`)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setCorriendo(false)
    }
  }

  // dentro de la ventana: si abajo no cabe al menos media pantalla, se abre más arriba; y
  // nunca más alto que lo que queda (V5: el resultado quedaba fuera de la vista)
  const left = Math.max(8, Math.min(abierto.x, window.innerWidth - 340))
  const top = Math.max(8, Math.min(abierto.y, window.innerHeight / 2))
  return (
    <div className="menu-contextual" role="menu" aria-label="Acciones" data-testid="menu-contextual"
         style={{ left, top, maxHeight: window.innerHeight - top - 12 }}>
      <div className="menu-contextual-head">
        <span>Sobre {sobre}</span>
        <button type="button" className="icon-btn" aria-label="Cerrar menú" onClick={cerrar}><X className="w-3.5 h-3.5" /></button>
      </div>
      {!acciones && !error && <div className="menu-contextual-nota"><Loader2 className="w-3.5 h-3.5 animate-spin" /> Buscando acciones…</div>}
      {acciones && !elegida && (
        lista.length ? (
          <ul className="menu-contextual-lista">
            {lista.map(({ accion, disponible, motivo }) => (
              <li key={accion.herramienta}>
                <button type="button" role="menuitem" disabled={!disponible} title={motivo ?? accion.description}
                        onClick={() => { setElegida(accion); setValores({}); setError(null) }}>
                  <span>{accion.titulo}</span>
                  <span className="menu-contextual-origen">{accion.server === 'core' ? 'núcleo' : accion.server}</span>
                </button>
              </li>
            ))}
          </ul>
        ) : <div className="menu-contextual-nota">No hay acciones para este tipo de elemento.</div>
      )}
      {elegida && (
        <div className="menu-contextual-form">
          <div className="menu-contextual-titulo">{elegida.titulo}</div>
          {campos.map((f) => (
            <Campo key={f.name} field={f} value={valores[f.name] ?? ''} layers={capas}
                   onChange={(v) => setValores((s) => ({ ...s, [f.name]: v }))} />
          ))}
          <div className="menu-contextual-botones">
            <button type="button" className="btn" onClick={() => { setElegida(null); setResultado(null); setError(null) }}>Volver</button>
            <button type="button" className="btn btn-primary" onClick={ejecutar} disabled={corriendo} data-testid="menu-ejecutar">
              {corriendo ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Play className="w-3.5 h-3.5" />} Ejecutar
            </button>
          </div>
          {resultado?.success && <Resultado result={resultado} />}
        </div>
      )}
      {error && <div className="dibujo-error" role="alert">{error}</div>}
    </div>
  )
}
