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
import { useMapStore, useSessionStore } from '@/stores'
import { capaDelUsuario, useOperaciones } from '@/lib/operaciones'
import { aplicarResultado } from '@/lib/resultadoHerramienta'
import { getViewBounds } from '@/lib/mapViewport'
import { buildMapContext } from '@/utils/mapContext'
import {
  cajaParaVer, type EscenaS2, escenasDe, fechaCorta, METRICAS, type Metrica, SERVIDOR_IMAGERY,
  simbologiaCuadricula, type TeselaS2, teselasDe, ventanaPorDefecto, vistaDeBbox,
} from '@/lib/exploradorS2'

type Producto = 'true_color' | 'false_color' | 'ndvi'

const PRODUCTOS: { id: Producto; etiqueta: string; icono: typeof Palette }[] = [
  { id: 'true_color', etiqueta: 'Color', icono: Palette },
  { id: 'false_color', etiqueta: 'Falso color', icono: Palette },
  { id: 'ndvi', etiqueta: 'NDVI', icono: Leaf },
]

async function correr(tool: string, args: Record<string, unknown>): Promise<McpRunResult> {
  const sessionId = useSessionStore.getState().sessionId
  if (!sessionId) throw new Error('No hay sesión activa.')
  const res = await mcpApi.run(SERVIDOR_IMAGERY, tool, {
    session_id: sessionId, arguments: args, map_context: buildMapContext(),
  })
  if (!res.success) throw new Error(res.message ?? 'La herramienta no devolvió resultado.')
  return res
}

export function ExploradorS2() {
  const inicial = ventanaPorDefecto(new Date())
  const [desde, setDesde] = useState(inicial.desde)
  const [hasta, setHasta] = useState(inicial.hasta)
  const [maxNubes, setMaxNubes] = useState(100)
  const [minCobertura, setMinCobertura] = useState(10)
  const [metrica, setMetrica] = useState<Metrica>('nubes_min')
  const [teselas, setTeselas] = useState<TeselaS2[] | null>(null)
  const [capaGrid, setCapaGrid] = useState<string | null>(null)
  const [tesela, setTesela] = useState<TeselaS2 | null>(null)
  const [escenas, setEscenas] = useState<EscenaS2[] | null>(null)
  const [cargando, setCargando] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [rasterPrevio, setRasterPrevio] = useState<string | null>(null)

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
    const lista = teselasDe(res)
    setTeselas(lista)
    setTesela(null)
    setEscenas(null)
    const map = useMapStore.getState()
    if (capaGrid) {   // la búsqueda nueva sustituye a la anterior (se puede deshacer)
      useOperaciones.getState().ejecutar({ op: 'remove_layer', layer_id: capaGrid, args: {}, reason: null }, 'user')
    }
    const fc = res.results.geojson
    setCapaGrid(fc?.features?.length
      ? capaDelUsuario(map.addLayer(fc, `Sentinel-2 ${desde} → ${hasta}`, simbologiaCuadricula(metrica),
        res.results.layer_ref?.id)) ?? null
      : null)
  })

  const cambiarMetrica = (m: Metrica) => {
    setMetrica(m)
    if (capaGrid) {
      useOperaciones.getState().ejecutar({
        op: 'set_style', layer_id: capaGrid, args: { style: simbologiaCuadricula(m) as never }, reason: 'explorador Sentinel-2',
      }, 'user')
    }
  }

  const abrirTesela = (t: TeselaS2) => conEstado(`tesela:${t.tile}`, async () => {
    setTesela(t)
    setEscenas(null)
    if (t.bbox) {
      const v = vistaDeBbox(t.bbox)
      useMapStore.getState().setMapView(v.centro, v.zoom)
    }
    const res = await correr('imagery_catalog_scenes', { tile: t.tile, ...filtros, limit: 60 })
    setEscenas(escenasDe(res))
  })

  const ver = (e: EscenaS2, p: Producto) => conEstado(`ver:${e.id}:${p}`, async () => {
    if (!tesela?.bbox) throw new Error('La tesela no trae su huella.')
    const caja = cajaParaVer(tesela.bbox, getViewBounds())
    const res = p === 'ndvi'
      ? await correr('imagery_ndvi', { aoi_geojson: caja, scene_id: e.id })
      : await correr('imagery_composite', { aoi_geojson: caja, scene_id: e.id, combo: p })
    setRasterPrevio(aplicarResultado(res, `${p} ${e.id}`, rasterPrevio).raster ?? rasterPrevio)
    const xs = caja.coordinates[0].map((c) => c[0]), ys = caja.coordinates[0].map((c) => c[1])
    const v = vistaDeBbox([Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)])
    useMapStore.getState().setMapView(v.centro, v.zoom)
  })

  return (
    <div className="imgp" data-testid="explorador-s2">
      <section className="imgp-card">
        <header><Satellite className="w-3.5 h-3.5" /> Sentinel-2 L2A · 10 m</header>
        <div className="imgp-row">
          <label className="imgp-sub">Desde<input type="date" value={desde} onChange={(e) => setDesde(e.target.value)} /></label>
          <label className="imgp-sub">Hasta<input type="date" value={hasta} onChange={(e) => setHasta(e.target.value)} /></label>
        </div>
        <label className="imgp-sub">Nubes máximas <b className="imgp-cloud-val">{maxNubes}%</b>
          <input className="imgp-slider" type="range" min={0} max={100} value={maxNubes}
            onChange={(e) => setMaxNubes(Number(e.target.value))} aria-label="Nubes máximas" />
        </label>
        <label className="imgp-sub">Cobertura mínima de la tesela <b className="imgp-cloud-val">{minCobertura}%</b>
          <input className="imgp-slider" type="range" min={0} max={100} value={minCobertura}
            onChange={(e) => setMinCobertura(Number(e.target.value))} aria-label="Cobertura mínima" />
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
            <button className="imgp-link" onClick={() => { setTesela(null); setEscenas(null) }}>← teselas</button>
          </header>
          {cargando === `tesela:${tesela.tile}` && <p className="imgp-sub"><Loader2 className="w-3 h-3 animate-spin" /> Buscando escenas…</p>}
          {escenas?.length === 0 && <p className="imgp-sub">Ninguna escena pasa los filtros.</p>}
          <ul className="s2-escenas">
            {(escenas ?? []).map((e) => (
              <li key={e.id} className="s2-escena">
                <img src={e.miniatura} alt={`Vista previa ${e.id}`} loading="lazy" width={64} height={64} />
                <div>
                  <b>{fechaCorta(e.fecha)}</b>
                  <span className="imgp-sub">{e.nubes}% nubes · {e.cobertura}% cobertura</span>
                  <div className="s2-acciones">
                    {PRODUCTOS.map(({ id, etiqueta, icono: Icono }) => (
                      <button key={id} className="imgp-chip" disabled={cargando !== null}
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
