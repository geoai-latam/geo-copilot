/**
 * Explorador Sentinel-2 (panel transaccional) — la misma puerta que usa el agente.
 *
 * 1. «Buscar en la vista» → `imagery_catalog_grid`: las teselas MGRS de la vista coloreadas por
 *    la métrica elegida (capa del workspace: el agente también la ve y la puede usar).
 * 2. Elegir una tesela → `imagery_catalog_scenes`: sus escenas con miniatura, nubes y cobertura.
 * 3. Ver una escena → `imagery_composite` (color) o `imagery_ndvi` con su `scene_id`.
 */
import { useState } from 'react'
import { AlertTriangle, Cloud, Grid3x3, Leaf, Loader2, Palette, Satellite, Search } from 'lucide-react'

import { mcpApi, type McpRunResult } from '@/services/api'
import { type OrdenEscenas, useExploradorS2 } from '@/stores/exploradorS2Store'
import { useMapStore, useSessionStore } from '@/stores'
import { capaDelUsuario, useOperaciones } from '@/lib/operaciones'
import { aplicarResultado } from '@/lib/resultadoHerramienta'
import { getViewBounds } from '@/lib/mapViewport'
import { buildMapContext } from '@/utils/mapContext'
import {
  cajaParaVer, type EscenaS2, escenasDe, fechaCorta, METRICAS, type Metrica, SERVIDOR_IMAGERY,
  simbologiaCuadricula, type TeselaS2, teselasDe, vistaDeBbox,
} from '@/lib/exploradorS2'

type Producto = 'true_color' | 'false_color' | 'agriculture' | 'swir' | 'ndvi'

const PRODUCTOS: { id: Producto; etiqueta: string; titulo: string; icono: typeof Palette }[] = [
  { id: 'true_color', etiqueta: 'Color', titulo: 'Color real (B04, B03, B02)', icono: Palette },
  { id: 'false_color', etiqueta: 'Falso color', titulo: 'Infrarrojo (B08, B04, B03): vegetación en rojo', icono: Palette },
  { id: 'agriculture', etiqueta: 'Agricultura', titulo: 'Agricultura (B11, B08, B02): cultivos y suelo', icono: Palette },
  { id: 'swir', etiqueta: 'SWIR', titulo: 'Infrarrojo de onda corta (B12, B8A, B04): humedad, quemas', icono: Palette },
  { id: 'ndvi', etiqueta: 'NDVI', titulo: 'Índice de vegetación (B08/B04)', icono: Leaf },
]

const ORDENES: { id: OrdenEscenas; etiqueta: string }[] = [
  { id: 'menos_nubes', etiqueta: 'Menos nubes' },
  { id: 'mas_cobertura', etiqueta: 'Más cobertura' },
  { id: 'reciente', etiqueta: 'Más reciente' },
]

/** Resalta en la cuadrícula la tesela abierta (null la apaga). */
function resaltarTesela(capa: string | null, tile: string | null) {
  if (!capa || !useMapStore.getState().layers.some((l) => l.id === capa)) return
  useMapStore.getState().setResaltado(capa, tile ? { where: { field: 'tile', op: '=', value: tile }, count: 1, origin: 'query' } : null)
}

async function correr(tool: string, args: Record<string, unknown>): Promise<McpRunResult> {
  const sessionId = useSessionStore.getState().sessionId
  if (!sessionId) throw new Error('No hay sesión activa.')
  const res = await mcpApi.run(SERVIDOR_IMAGERY, tool, {
    session_id: sessionId, arguments: args, map_context: buildMapContext(),
  })
  if (!res.success) throw new Error(res.message ?? 'La herramienta no devolvió resultado.')
  return res
}

/** Quita una capa del mapa si sigue ahí (el usuario pudo borrarla a mano). */
function quitarSiExiste(id: string | null) {
  if (id && useMapStore.getState().layers.some((l) => l.id === id)) {
    useOperaciones.getState().ejecutar({ op: 'remove_layer', layer_id: id, args: {}, reason: null }, 'user')
  }
}

/** Relleno de la cuadrícula: se apaga mientras se ve una escena para no teñirla. */
function rellenoCuadricula(capa: string | null, metrica: Metrica, relleno: boolean) {
  if (capa && useMapStore.getState().layers.some((l) => l.id === capa)) {
    useOperaciones.getState().ejecutar({
      op: 'set_style', layer_id: capa, args: { style: simbologiaCuadricula(metrica, relleno) as never },
      reason: 'explorador Sentinel-2',
    }, 'user')
  }
}

export function ExploradorS2() {
  const {
    desde, hasta, maxNubes, minCobertura, metrica, orden, teselas, capaGrid, tesela, escenas, rasterPrevio, fijar,
  } = useExploradorS2()
  const [cargando, setCargando] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const filtros = {
    date_from: desde, date_to: hasta,
    ...(maxNubes < 100 ? { max_cloud_pct: maxNubes } : {}),
    ...(minCobertura > 0 ? { min_coverage_pct: minCobertura } : {}),
  }

  const conEstado = async (que: string, fn: () => Promise<void>) => {
    setError(null)
    setCargando(que)
    try { await fn() } catch (e) { setError(e instanceof Error ? e.message : String(e)) } finally { setCargando(null) }
  }

  const buscar = () => conEstado('grid', async () => {
    const res = await correr('imagery_catalog_grid', { aoi_geojson: 'viewport', ...filtros })
    quitarSiExiste(capaGrid)   // la búsqueda nueva sustituye a la anterior (se puede deshacer)
    const fc = res.results.geojson
    const capa = fc?.features?.length
      ? capaDelUsuario(useMapStore.getState().addLayer(fc, `Sentinel-2 ${desde} → ${hasta}`,
        simbologiaCuadricula(metrica), res.results.layer_ref?.id)) ?? null
      : null
    fijar({ teselas: teselasDe(res), tesela: null, escenas: null, capaGrid: capa })
  })

  const cambiarMetrica = (m: Metrica) => {
    fijar({ metrica: m })
    rellenoCuadricula(capaGrid, m, tesela === null)
  }

  const cargarEscenas = (t: TeselaS2, o: OrdenEscenas) => conEstado(`tesela:${t.tile}`, async () => {
    fijar({ escenas: null })
    const res = await correr('imagery_catalog_scenes', { tile: t.tile, ...filtros, order: o, limit: 60 })
    fijar({ escenas: escenasDe(res) })
  })

  const abrirTesela = (t: TeselaS2) => {
    fijar({ tesela: t })
    resaltarTesela(capaGrid, t.tile)
    if (t.bbox) {
      const v = vistaDeBbox(t.bbox)
      useMapStore.getState().setMapView(v.centro, v.zoom)
    }
    return cargarEscenas(t, orden)
  }

  const cambiarOrden = (o: OrdenEscenas) => {
    fijar({ orden: o })
    if (tesela) void cargarEscenas(tesela, o)
  }

  const volverATeselas = () => {
    fijar({ tesela: null, escenas: null })
    resaltarTesela(capaGrid, null)
    rellenoCuadricula(capaGrid, metrica, true)
  }

  const ver = (e: EscenaS2, p: Producto) => conEstado(`ver:${e.id}:${p}`, async () => {
    if (!tesela?.bbox) throw new Error('La tesela no trae su huella.')
    const caja = cajaParaVer(tesela.bbox, getViewBounds())
    const res = p === 'ndvi'
      ? await correr('imagery_ndvi', { aoi_geojson: caja, scene_id: e.id })
      : await correr('imagery_composite', { aoi_geojson: caja, scene_id: e.id, combo: p })
    const previo = rasterPrevio && useMapStore.getState().layers.some((l) => l.id === rasterPrevio) ? rasterPrevio : null
    fijar({ rasterPrevio: aplicarResultado(res, `${p} ${e.id}`, previo).raster ?? previo })
    rellenoCuadricula(capaGrid, metrica, false)
    const xs = caja.coordinates[0].map((c) => c[0]), ys = caja.coordinates[0].map((c) => c[1])
    const v = vistaDeBbox([Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)])
    useMapStore.getState().setMapView(v.centro, v.zoom)
  })

  return (
    <div className="imgp" data-testid="explorador-s2">
      <section className="imgp-card">
        <header><Satellite className="w-3.5 h-3.5" /> Sentinel-2 L2A · 10 m</header>
        <div className="imgp-row">
          <label className="imgp-sub">Desde<input type="date" value={desde} onChange={(e) => fijar({ desde: e.target.value })} /></label>
          <label className="imgp-sub">Hasta<input type="date" value={hasta} onChange={(e) => fijar({ hasta: e.target.value })} /></label>
        </div>
        <label className="imgp-sub">Nubes máximas <b className="imgp-cloud-val">{maxNubes}%</b>
          <input className="imgp-slider" type="range" min={0} max={100} value={maxNubes}
            onChange={(e) => fijar({ maxNubes: Number(e.target.value) })} aria-label="Nubes máximas" />
        </label>
        <label className="imgp-sub">Cobertura mínima de la tesela <b className="imgp-cloud-val">{minCobertura}%</b>
          <input className="imgp-slider" type="range" min={0} max={100} value={minCobertura}
            onChange={(e) => fijar({ minCobertura: Number(e.target.value) })} aria-label="Cobertura mínima" />
        </label>
        <label className="imgp-sub">Colorear teselas por
          <select value={metrica} onChange={(e) => cambiarMetrica(e.target.value as Metrica)} aria-label="Colorear por">
            {METRICAS.map((m) => <option key={m.id} value={m.id}>{m.etiqueta}</option>)}
          </select>
        </label>
        <button className="btn btn-primary imgp-run" onClick={() => void buscar()} disabled={cargando !== null}
          data-testid="s2-buscar">
          {cargando === 'grid' ? <Loader2 className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />}
          Buscar en la vista
        </button>
        <p className="imgp-hint">Busca en lo que ves en el mapa (hasta el tamaño de un país, hasta un año).
          La primera búsqueda tarda más: lee el catálogo de escenas.</p>
      </section>

      {error && <div className="imgp-error" role="alert"><AlertTriangle className="w-4 h-4" />{error}</div>}

      {teselas && !tesela && (
        <section className="imgp-card" data-testid="s2-teselas">
          <header><Grid3x3 className="w-3.5 h-3.5" /> {teselas.length} teselas</header>
          {teselas.length === 0 && <p className="imgp-sub">Ninguna escena pasa los filtros en esta vista.</p>}
          <ul className="imgp-scenes">
            {teselas.map((t) => (
              <li key={t.tile}>
                <button className="imgp-scene" onClick={() => void abrirTesela(t)} disabled={cargando !== null}>
                  <span>{t.tile}</span>
                  <em><Cloud className="w-3 h-3" />{t.nubes_min}% · {t.escenas} escenas</em>
                  <i className={t.nubes_min <= 10 ? 'ok' : 'part'}>{t.mejor_fecha}</i>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {tesela && (
        <section className="imgp-card" data-testid="s2-escenas">
          <header>
            <Grid3x3 className="w-3.5 h-3.5" /> Tesela {tesela.tile}
            <button className="imgp-link" onClick={volverATeselas}>← teselas</button>
          </header>
          <label className="imgp-sub">Ordenar
            <select value={orden} onChange={(e) => cambiarOrden(e.target.value as OrdenEscenas)}
              aria-label="Ordenar escenas" disabled={cargando !== null}>
              {ORDENES.map((o) => <option key={o.id} value={o.id}>{o.etiqueta}</option>)}
            </select>
          </label>
          {cargando === `tesela:${tesela.tile}` && <p className="imgp-sub"><Loader2 className="w-3 h-3 animate-spin" /> Buscando escenas…</p>}
          {escenas?.length === 0 && <p className="imgp-sub">Ninguna escena pasa los filtros.</p>}
          <ul className="s2-escenas">
            {(escenas ?? []).map((e) => (
              <li key={e.id} className="s2-escena">
                <img src={e.miniatura} alt={`Vista previa ${e.id}`} loading="lazy" width={64} height={64} />
                <div>
                  <b>{fechaCorta(e.fecha)}</b>
                  <span className="imgp-sub">{e.nubes}% nubes · {e.cobertura}% cobertura</span>
                  <code className="s2-id" title={e.id}>{e.id}</code>
                  <div className="s2-acciones">
                    {PRODUCTOS.map(({ id, etiqueta, titulo, icono: Icono }) => (
                      <button key={id} className="imgp-chip" disabled={cargando !== null} title={titulo}
                        onClick={() => void ver(e, id)} data-testid={`s2-ver-${id}`}>
                        {cargando === `ver:${e.id}:${id}` ? <Loader2 className="w-3 h-3 animate-spin" /> : <Icono className="w-3 h-3" />} {etiqueta}
                      </button>
                    ))}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}
