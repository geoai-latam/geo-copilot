/**
 * FH.5 — la tabla de atributos VINCULADA a una capa del mapa.
 *
 * - Clic en una fila selecciona ese elemento en el mapa (origen «tabla»);
 *   Ctrl/⇧+clic lo suma o lo quita. Lo seleccionado se ve marcado aquí y en el mapa.
 * - ⌖ encuadra el elemento. «Solo seleccionados» deja ver solo esos.
 * - Σ en una columna: estadística del campo (n, únicos, mín/máx/media, frecuentes).
 * - La tabla muestra lo que muestra la capa: si está filtrada, solo ese subconjunto.
 * - Capa pequeña: en memoria. Capa grande (teselas): paginada contra el workspace.
 */
import { useEffect, useMemo, useState } from 'react'
import { ArrowDown, ArrowUp, Crosshair, Sigma, X } from 'lucide-react'

import { useOperaciones } from '@/lib/operaciones'
import { workspaceApi } from '@/services/api'
import { useMapStore } from '@/stores/mapStore'
import { useSessionStore } from '@/stores/sessionStore'
import { useResultsStore } from '@/stores/resultsStore'
import {
  desdeWorkspace, estadisticaEnMemoria, estaSeleccionada, filasEnMemoria, type Estadistica, type Fila,
} from './TablaCapa.helpers'

const POR_PAGINA = 25
const fmt = (v: unknown) => (typeof v === 'number' ? v.toLocaleString('es-CO', { maximumFractionDigits: 3 }) : String(v ?? '—'))

export function TablaCapa({ layerId }: { layerId: string }) { // eslint-disable-line complexity -- deuda congelada (F1); partir, no subir
  const capa = useMapStore((s) => s.layers.find((l) => l.id === layerId))
  const sessionId = useSessionStore((s) => s.sessionId)
  const [pagina, setPagina] = useState(0)
  const [orden, setOrden] = useState<{ campo: string | null; desc: boolean }>({ campo: null, desc: false })
  const [soloSeleccion, setSoloSeleccion] = useState(false)
  const [remotas, setRemotas] = useState<{ total: number; filas: Fila[] } | null>(null)
  const [stats, setStats] = useState<{ campo: string; e: Estadistica | null } | null>(null)
  const [error, setError] = useState<string | null>(null)

  const remota = capa ? desdeWorkspace(capa) : false
  const filtroClave = JSON.stringify(capa?.filtro ?? null)
  const idsSel = soloSeleccion ? (capa?.seleccion?.ids ?? null) : null

  // memoria: todas las filas visibles, ordenadas
  const locales = useMemo(
    () => (capa && !remota ? filasEnMemoria(capa, { soloSeleccion, orden: orden.campo, desc: orden.desc }) : []),
    [capa, remota, soloSeleccion, orden],
  )
  // workspace: una página
  useEffect(() => {
    if (!capa || !remota || !sessionId || !capa.datasetId) return
    let vivo = true
    workspaceApi.filas(sessionId, capa.datasetId, {
      offset: pagina * POR_PAGINA, limit: POR_PAGINA, orden: orden.campo, desc: orden.desc,
      filtro: capa.filtro ?? null, ids: soloSeleccion ? (idsSel ?? []) : null,
    }).then((r) => {
      if (vivo) { setRemotas({ total: r.total, filas: r.filas.map((f) => ({ id: f.fid, properties: f.properties, bbox: f.bbox })) }); setError(null) }
    }).catch((e) => { if (vivo) setError(`No se pudieron leer las filas: ${e instanceof Error ? e.message : String(e)}`) })
    return () => { vivo = false }
  }, [capa?.datasetId, remota, sessionId, pagina, orden, filtroClave, soloSeleccion, JSON.stringify(idsSel)]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => setPagina(0), [filtroClave, soloSeleccion, orden])

  if (!capa) return null
  const total = remota ? (remotas?.total ?? 0) : locales.length
  const filas = remota ? (remotas?.filas ?? []) : locales.slice(pagina * POR_PAGINA, (pagina + 1) * POR_PAGINA)
  const campos = capa.tiles?.fields?.length ? capa.tiles.fields
    : Object.keys((capa.data?.features?.[0]?.properties ?? {}) as Record<string, unknown>)
  const paginas = Math.max(1, Math.ceil(total / POR_PAGINA))

  const seleccionar = (fila: Fila, sumar: boolean) =>
    useOperaciones.getState().ejecutar({ op: 'select', layer_id: capa.id, reason: null,
      args: { ids: [fila.id], where: null, mode: sumar ? 'toggle' : 'replace', origin: 'table', count: null } }, 'user')
  const encuadrar = (fila: Fila) => { if (fila.bbox) useMapStore.getState().pedirEncuadre(fila.bbox) }
  const ordenarPor = (campo: string) =>
    setOrden((o) => (o.campo === campo ? { campo, desc: !o.desc } : { campo, desc: false }))
  const verEstadistica = async (campo: string) => {
    if (stats?.campo === campo) return setStats(null)
    if (!remota) return setStats({ campo, e: estadisticaEnMemoria(filasEnMemoria(capa), campo) })
    setStats({ campo, e: null })
    try {
      const e = await workspaceApi.estadistica(sessionId!, capa.datasetId!, campo, capa.filtro ?? null)
      setStats({ campo, e })
    } catch (err) {
      setError(`No se pudo calcular la estadística: ${err instanceof Error ? err.message : String(err)}`)
    }
  }

  return (
    <div className="tabla-capa" data-testid="tabla-capa">
      <div className="tabla-capa-cabecera">
        <strong>{capa.name}</strong>
        <span className="tabla-capa-cuenta" data-testid="tabla-capa-total">
          {total.toLocaleString('es-CO')} fila{total === 1 ? '' : 's'}{capa.filtro?.length ? ' (filtrada)' : ''}
        </span>
        <label className="tabla-capa-solo">
          <input type="checkbox" checked={soloSeleccion} onChange={(e) => setSoloSeleccion(e.target.checked)} />
          solo seleccionados
        </label>
        <button type="button" className="icon-btn" aria-label="Cerrar la tabla"
                onClick={() => useResultsStore.getState().verTablaCapa(null)}>
          <X className="w-3.5 h-3.5" />
        </button>
      </div>
      {error && <div className="tabla-capa-error" role="alert">{error}</div>}
      {stats && (
        <div className="tabla-capa-stats" data-testid="tabla-capa-stats">
          <strong>Σ {stats.campo}</strong>{' '}
          {!stats.e ? 'calculando…' : (
            <>
              {stats.e.con_valor.toLocaleString('es-CO')} con valor · {stats.e.unicos.toLocaleString('es-CO')} distintos
              {stats.e.numerico && <> · mín {fmt(stats.e.min)} · máx {fmt(stats.e.max)} · media {fmt(stats.e.media)}</>}
              {stats.e.frecuentes.length > 0 && (
                <div className="tabla-capa-frecuentes">
                  {stats.e.frecuentes.slice(0, 5).map((f) => `${f.valor} (${f.n})`).join(' · ')}
                </div>
              )}
            </>
          )}
        </div>
      )}
      <div className="tabla-capa-scroll">
        <table>
          <thead>
            <tr>
              <th aria-label="Encuadrar" />
              {campos.map((c) => (
                <th key={c}>
                  <button type="button" className="tabla-capa-col" onClick={() => ordenarPor(c)} title={`Ordenar por ${c}`}>
                    {c}
                    {orden.campo === c && (orden.desc ? <ArrowDown className="w-3 h-3" /> : <ArrowUp className="w-3 h-3" />)}
                  </button>
                  <button type="button" className="icon-btn" aria-label={`Estadística de ${c}`} onClick={() => void verEstadistica(c)}>
                    <Sigma className="w-3 h-3" />
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {filas.map((f) => {
              const sel = estaSeleccionada(capa, f)
              return (
                <tr key={f.id} data-testid="tabla-capa-fila" data-fid={f.id} aria-selected={sel}
                    className={sel ? 'is-seleccionada' : ''}
                    onClick={(e) => seleccionar(f, e.ctrlKey || e.metaKey || e.shiftKey)}>
                  <td>
                    <button type="button" className="icon-btn" aria-label={`Encuadrar ${f.id}`} disabled={!f.bbox}
                            onClick={(e) => { e.stopPropagation(); encuadrar(f) }}>
                      <Crosshair className="w-3 h-3" />
                    </button>
                  </td>
                  {campos.map((c) => <td key={c}>{fmt(f.properties[c])}</td>)}
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <div className="tabla-capa-paginas">
        <button type="button" className="gc-btn gc-btn-ghost" disabled={pagina === 0} onClick={() => setPagina(pagina - 1)}>‹</button>
        <span data-testid="tabla-capa-pagina">{pagina + 1} / {paginas}</span>
        <button type="button" className="gc-btn gc-btn-ghost" disabled={pagina + 1 >= paginas} onClick={() => setPagina(pagina + 1)}>›</button>
      </div>
    </div>
  )
}
