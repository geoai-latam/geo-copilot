/**
 * Explorador Sentinel-2 (panel transaccional) — la misma puerta que usa el agente.
 *
 * 1. Al abrir, el mundo entero: `imagery_catalog_world` pinta las ~29.000 teselas MGRS por la
 *    métrica elegida (agregados por meses). «Detallar la vista» usa `imagery_catalog_grid`
 *    (fechas exactas y la mejor escena de cada tesela). En globo o en plano.
 * 2. Una tesela (de la lista o con un clic en el mapa) → `imagery_catalog_scenes`.
 * 3. Una escena → `imagery_scene_view`: la escena ENTERA en el producto elegido (color, índice,
 *    banda suelta, SCL, nubes). Su tarjeta ajusta el contraste, lee píxeles y la descarga.
 */
import { useEffect, useState } from 'react'
import { AlertTriangle, Cloud, Eye, Globe2, Grid3x3, Loader2, Map as MapaIcono, Satellite, Search } from 'lucide-react'

import { useMapStore, useSelectedFeature, useSessionStore } from '@/stores'
import { aplicarResultado } from '@/lib/resultadoHerramienta'
import {
  type EscenaS2, escenasDe, fechaCorta, GRUPOS, METRICAS, type Metrica, PRODUCTOS_S2, productoS2,
  simbologiaCuadricula, type TeselaS2, teselasDe, teselasDelMundo, vistaDeBbox,
} from '@/lib/exploradorS2'
import {
  capaDeCuadricula, correrImagery, existeCapa, quitarSiExiste, rellenoCuadricula, resaltarTesela,
} from '@/lib/exploradorS2Mapa'
import { type OrdenEscenas, useExploradorS2 } from '@/stores/exploradorS2Store'
import { EscenaS2Vista } from './EscenaS2Vista'

const ORDENES: { id: OrdenEscenas; etiqueta: string }[] = [
  { id: 'menos_nubes', etiqueta: 'Menos nubes' },
  { id: 'mas_cobertura', etiqueta: 'Más cobertura' },
  { id: 'reciente', etiqueta: 'Más reciente' },
]

/** El mundo se pide UNA vez al abrir el explorador por primera vez (StrictMode monta dos). */
let mundoPedido = false

type Seccion = { layerId?: string; properties?: Record<string, unknown> }

function FiltrosS2() {
  const { desde, hasta, maxNubes, minCobertura, minEscenas, fijar } = useExploradorS2()
  return (
    <>
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
      <label className="imgp-sub">Escenas mínimas por tesela <b className="imgp-cloud-val">{minEscenas}</b>
        <input className="imgp-slider" type="range" min={0} max={60} value={minEscenas}
          onChange={(e) => fijar({ minEscenas: Number(e.target.value) })} aria-label="Escenas mínimas" />
      </label>
    </>
  )
}

function ListaTeselas({ teselas, mundo, ocupado, onAbrir }: {
  teselas: TeselaS2[]; mundo: boolean; ocupado: boolean; onAbrir: (t: TeselaS2) => void
}) {
  return (
    <section className="imgp-card" data-testid="s2-teselas">
      <header><Grid3x3 className="w-3.5 h-3.5" /> {mundo ? 'Las más despejadas' : `${teselas.length} teselas`}</header>
      {teselas.length === 0 && <p className="imgp-sub">Ninguna tesela pasa los filtros.</p>}
      <ul className="imgp-scenes">
        {teselas.slice(0, 50).map((t) => (
          <li key={t.tile}>
            <button className="imgp-scene" onClick={() => onAbrir(t)} disabled={ocupado}>
              <span>{t.tile}</span>
              <em><Cloud className="w-3 h-3" />{t.nubes_min}% · {t.escenas} escenas</em>
              {t.mejor_fecha && <i className={t.nubes_min <= 10 ? 'ok' : 'part'}>{t.mejor_fecha}</i>}
            </button>
          </li>
        ))}
      </ul>
    </section>
  )
}

function ListaEscenas({ escenas, vista, producto, cargando, onVer }: {
  escenas: EscenaS2[] | null; vista: string | null; producto: string; cargando: string | null
  onVer: (e: EscenaS2) => void
}) {
  if (escenas?.length === 0) return <p className="imgp-sub">Ninguna escena pasa los filtros.</p>
  return (
    <ul className="s2-escenas">
      {(escenas ?? []).map((e) => (
        <li key={e.id} className={`s2-escena${vista === e.id ? ' activa' : ''}`}>
          <img src={e.miniatura} alt={`Vista previa ${e.id}`} loading="lazy" width={64} height={64} />
          <div>
            <b>{fechaCorta(e.fecha)}</b>
            <span className="imgp-sub">{e.nubes}% nubes · {e.cobertura}% cobertura</span>
            <code className="s2-id" title={e.id}>{e.id}</code>
            <div className="s2-acciones">
              <button className="imgp-chip" disabled={cargando !== null} data-testid="s2-ver" onClick={() => onVer(e)}
                title={`Ver la escena entera: ${productoS2(producto).etiqueta}`}>
                {cargando === `ver:${e.id}` ? <Loader2 className="w-3 h-3 animate-spin" /> : <Eye className="w-3 h-3" />} Ver
              </button>
            </div>
          </div>
        </li>
      ))}
    </ul>
  )
}

export function ExploradorS2() {
  const {
    desde, hasta, maxNubes, minCobertura, minEscenas, metrica, orden, modo, producto, teselas, capaGrid,
    tesela, escenas, escenaVista, rasterPrevio, fijar,
  } = useExploradorS2()
  const proyeccion = useMapStore((s) => s.proyeccion)
  const seleccion = useSelectedFeature() as { secciones?: Seccion[] } | null
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

  const ponerCuadricula = (res: Parameters<typeof teselasDe>[0], nombre: string) => {
    quitarSiExiste(capaGrid)   // la cuadrícula nueva sustituye a la anterior (se puede deshacer)
    return capaDeCuadricula(res, nombre, simbologiaCuadricula(metrica))
  }

  const verMundo = () => conEstado('mundo', async () => {
    const res = await correrImagery('imagery_catalog_world', { ...filtros, ...(minEscenas > 0 ? { min_scenes: minEscenas } : {}) })
    const capa = ponerCuadricula(res, `Sentinel-2 en el mundo ${desde} → ${hasta}`)
    fijar({ modo: 'mundo', teselas: teselasDelMundo(res), tesela: null, escenas: null, capaGrid: capa })
  })

  const detallarVista = () => conEstado('vista', async () => {
    const res = await correrImagery('imagery_catalog_grid', { aoi_geojson: 'viewport', ...filtros })
    const capa = ponerCuadricula(res, `Sentinel-2 ${desde} → ${hasta}`)
    fijar({ modo: 'vista', teselas: teselasDe(res), tesela: null, escenas: null, capaGrid: capa })
  })

  useEffect(() => {
    // El estado guardado es de UNA sesión (sus capas viven en ella): con otra, se empieza de cero.
    const sid = useSessionStore.getState().sessionId
    const st = useExploradorS2.getState()
    if (st.sesion !== sid) {
      st.reiniciar()
      fijar({ sesion: sid })
    }
    if (!mundoPedido && useExploradorS2.getState().teselas === null) {
      mundoPedido = true
      void verMundo()
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps -- solo al abrir por primera vez

  const cargarEscenas = (t: TeselaS2, o: OrdenEscenas) => conEstado(`tesela:${t.tile}`, async () => {
    fijar({ escenas: null })
    const res = await correrImagery('imagery_catalog_scenes', { tile: t.tile, ...filtros, order: o, limit: 60 })
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

  // Un clic sobre la cuadrícula del mapa abre esa tesela (con el mundo no caben todas en la lista).
  useEffect(() => {
    const s = seleccion?.secciones?.find((x) => x.layerId === capaGrid)
    const p = s?.properties
    if (p && typeof p.tile === 'string' && p.tile !== tesela?.tile && cargando === null) {
      void abrirTesela({
        tile: p.tile, escenas: Number(p.escenas), nubes_min: Number(p.nubes_min),
        nubes_mediana: Number(p.nubes_mediana), cobertura_max: Number(p.cobertura_max), bbox: null,
      })
    }
  }, [seleccion]) // eslint-disable-line react-hooks/exhaustive-deps -- reacciona solo al clic

  const cambiarMetrica = (m: Metrica) => {
    fijar({ metrica: m })
    rellenoCuadricula(capaGrid, m, escenaVista === null)
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

  /** La escena ENTERA en el producto elegido; `rangos` = el contraste de su tarjeta. */
  /** Contraste de una escena pintada en el navegador: solo cambian sus rangos (sin servidor). */
  const contrasteLocal = (rangos: [number, number][] | null): boolean => {
    const capa = useMapStore.getState().layers.find((l) => l.id === rasterPrevio)
    const cog = capa?.cog
    if (!capa || !cog || !rangos || !['rgb', 'indice', 'banda'].includes(cog.tipo)) return false
    useMapStore.getState().setLayerCog(capa.id, { ...cog, rangos })
    if (escenaVista) fijar({ escenaVista: { ...escenaVista, rangos } })
    return true
  }

  const verEscena = (id: string, fecha: string, prod: string, rangos: [number, number][] | null) =>
    contrasteLocal(rangos) ? Promise.resolve() : conEstado(`ver:${id}`, async () => {
      const args: Record<string, unknown> = { scene_id: id, product: prod }
      if (rangos) {
        if (productoS2(prod).grupo === 'color') args.stretch = rangos
        else args.rescale = rangos[0]
      }
      const res = await correrImagery('imagery_scene_view', args)
      const previo = existeCapa(rasterPrevio) ? rasterPrevio : null
      const raster = aplicarResultado(res, `${prod} ${id}`, previo).raster ?? previo
      rellenoCuadricula(capaGrid, metrica, false)
      resaltarTesela(capaGrid, null)   // su relleno también teñiría la escena; la escena ya marca la tesela
      const escena = (res.facts?.scene ?? {}) as { bbox?: [number, number, number, number] }
      if (!rangos && escena.bbox) {
        const v = vistaDeBbox(escena.bbox)
        useMapStore.getState().setMapView(v.centro, v.zoom)
      }
      fijar({
        rasterPrevio: raster,
        escenaVista: { id, fecha, producto: prod, bbox: escena.bbox ?? null, rangos,
          descargas: (res.facts?.descargas ?? []) as never },
      })
    })

  return (
    <div className="imgp" data-testid="explorador-s2">
      <section className="imgp-card">
        <header>
          <Satellite className="w-3.5 h-3.5" /> Sentinel-2 L2A · 10 m
          <button className="imgp-link" onClick={() => useMapStore.getState().setProyeccion(proyeccion === 'globe' ? 'mercator' : 'globe')}
            data-testid="s2-globo" title="Ver el mapa como globo o en plano">
            {proyeccion === 'globe' ? <><MapaIcono className="w-3 h-3" /> Plano</> : <><Globe2 className="w-3 h-3" /> Globo</>}
          </button>
        </header>
        <FiltrosS2 />
        <label className="imgp-sub">Colorear teselas por
          <select value={metrica} onChange={(e) => cambiarMetrica(e.target.value as Metrica)} aria-label="Colorear por">
            {METRICAS.map((m) => <option key={m.id} value={m.id}>{m.etiqueta}</option>)}
          </select>
        </label>
        <div className="imgp-row">
          <button className="btn btn-primary imgp-run" onClick={() => void verMundo()} disabled={cargando !== null}
            data-testid="s2-mundo">
            {cargando === 'mundo' ? <Loader2 className="w-4 h-4 animate-spin" /> : <Globe2 className="w-4 h-4" />}
            El mundo
          </button>
          <button className="btn imgp-run" onClick={() => void detallarVista()} disabled={cargando !== null}
            data-testid="s2-buscar">
            {cargando === 'vista' ? <Loader2 className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />}
            Detallar la vista
          </button>
        </div>
        <p className="imgp-hint">{modo === 'mundo'
          ? 'Todas las teselas del mundo, por meses completos. Haz clic en una del mapa para ver sus escenas.'
          : 'Las teselas de la vista con fechas exactas y su escena más despejada (hasta el tamaño de un país).'}</p>
      </section>

      {error && <div className="imgp-error" role="alert"><AlertTriangle className="w-4 h-4" />{error}</div>}

      {escenaVista && (
        <EscenaS2Vista vista={escenaVista} ocupado={cargando !== null}
          onAplicar={(rangos) => verEscena(escenaVista.id, escenaVista.fecha, escenaVista.producto, rangos)} />
      )}

      {teselas && !tesela && (
        <ListaTeselas teselas={teselas} mundo={modo === 'mundo'} ocupado={cargando !== null} onAbrir={(t) => void abrirTesela(t)} />
      )}

      {tesela && (
        <section className="imgp-card" data-testid="s2-escenas">
          <header>
            <Grid3x3 className="w-3.5 h-3.5" /> Tesela {tesela.tile}
            <button className="imgp-link" onClick={volverATeselas}>← teselas</button>
          </header>
          <div className="imgp-row">
            <label className="imgp-sub">Ordenar
              <select value={orden} onChange={(e) => cambiarOrden(e.target.value as OrdenEscenas)}
                aria-label="Ordenar escenas" disabled={cargando !== null}>
                {ORDENES.map((o) => <option key={o.id} value={o.id}>{o.etiqueta}</option>)}
              </select>
            </label>
            <label className="imgp-sub">Ver como
              <select value={producto} onChange={(e) => fijar({ producto: e.target.value })} aria-label="Ver como">
                {GRUPOS.map((g) => (
                  <optgroup key={g.id} label={g.etiqueta}>
                    {PRODUCTOS_S2.filter((p) => p.grupo === g.id).map((p) => <option key={p.id} value={p.id}>{p.etiqueta}</option>)}
                  </optgroup>
                ))}
              </select>
            </label>
          </div>
          {cargando === `tesela:${tesela.tile}` && <p className="imgp-sub"><Loader2 className="w-3 h-3 animate-spin" /> Buscando escenas…</p>}
          <ListaEscenas escenas={escenas} vista={escenaVista?.id ?? null} producto={producto} cargando={cargando}
            onVer={(e) => void verEscena(e.id, e.fecha, producto, null)} />
        </section>
      )}
    </div>
  )
}
