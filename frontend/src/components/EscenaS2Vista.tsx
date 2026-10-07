/**
 * La escena que está en el mapa: ajustar su contraste (con el histograma de cada canal, de
 * `imagery_band_histogram`), leer qué hay en un píxel (`imagery_pixel`, con un clic en el mapa)
 * y descargar sus bandas (los COG que declara `imagery_scene_view`).
 */
import { useEffect, useState } from 'react'
import { Crosshair, Download, Loader2, SlidersHorizontal } from 'lucide-react'

import { useMapStore } from '@/stores'
import {
  ajustable, fechaCorta, type HistogramaBanda, type PixelS2, type ProductoS2, productoS2, rangoPorDefecto,
} from '@/lib/exploradorS2'
import { correrImagery } from '@/lib/exploradorS2Mapa'
import type { EscenaVista } from '@/stores/exploradorS2Store'

const CANALES = ['R', 'G', 'B']

function Histograma({ h, rango }: { h: HistogramaBanda; rango: [number, number] }) {
  if (!h.conteos?.length || !h.bordes?.length) return null
  const max = Math.max(...h.conteos, 1)
  const [x0, x1] = [h.bordes[0], h.bordes[h.bordes.length - 1]]
  const x = (v: number) => ((v - x0) / (x1 - x0 || 1)) * 100
  return (
    <svg className="s2-hist" viewBox="0 0 100 24" preserveAspectRatio="none" aria-label={`Histograma de ${h.banda}`}>
      {h.conteos.map((c, i) => (
        <rect key={i} x={(i / h.conteos!.length) * 100} width={100 / h.conteos!.length} y={24 - (c / max) * 24}
          height={(c / max) * 24} />
      ))}
      <rect className="s2-hist-rango" x={x(rango[0])} width={Math.max(0.5, x(rango[1]) - x(rango[0]))} y={0} height={24} />
    </svg>
  )
}

function TablaPixel({ pixel }: { pixel: PixelS2 }) {
  return (
    <table className="s2-pixel" data-testid="s2-pixel">
      <tbody>
        {Object.values(pixel.indices).map((v) => (
          <tr key={v.nombre} title={v.lectura}><th>{v.nombre}</th><td>{v.valor.toFixed(3)}</td></tr>
        ))}
        {Object.entries(pixel.bandas).map(([b, v]) => (
          <tr key={b} title={v.nombre}><th>{v.codigo}</th>
            <td>{v.valor === null ? '—' : v.clase ?? `${v.valor}${v.unidad === '%' ? ' %' : ''}`}</td></tr>
        ))}
      </tbody>
    </table>
  )
}

function Descargas({ vista }: { vista: EscenaVista }) {
  return (
    <details className="s2-apartado">
      <summary><Download className="w-3 h-3" /> Descargar bandas ({vista.descargas.length})</summary>
      <ul className="s2-descargas" data-testid="s2-descargas">
        {vista.descargas.map((d) => (
          <li key={d.banda}>
            <a href={d.url} target="_blank" rel="noopener noreferrer" download title={d.nombre}>
              {d.codigo}</a> <span className="imgp-sub">{d.nombre}{d.resolucion_m ? ` · ${d.resolucion_m} m` : ''}</span>
          </li>
        ))}
      </ul>
    </details>
  )
}

type Rangos = [number, number][]

/** Un control por canal (tres en color, uno en un índice o una banda) con su histograma. */
function Canales({ p, rangos, hist, ocupado, onCambio, onAplicar, porDefecto }: {
  p: ProductoS2; rangos: Rangos; hist: HistogramaBanda[] | null; ocupado: boolean
  onCambio: (r: Rangos) => void; onAplicar: (r: Rangos | null) => Promise<void>; porDefecto: () => Rangos
}) {
  const fijar = (i: number, j: 0 | 1, v: number) =>
    onCambio(rangos.map((par, k) => (k === i ? (j === 0 ? [v, par[1]] : [par[0], v]) : par)) as Rangos)
  // Cada canal toma el p2–p98 de su banda; el que no tiene histograma conserva su rango.
  const auto = () => onCambio(rangos.map((r, i) => {
    const h = hist?.[i]
    return h && typeof h.p2 === 'number' && typeof h.p98 === 'number' && h.p2 < h.p98 ? [h.p2, h.p98] : r
  }) as Rangos)
  const etiqueta = (i: number) => (rangos.length === 3 ? `${CANALES[i]} · ${p.bandas[i]}` : (p.bandas[0] ?? p.id))
  return (
    <>
      {rangos.map((r, i) => (
        <div key={i} className="s2-canal" data-testid="s2-canal">
          <span className="imgp-sub">{etiqueta(i)}</span>
          {hist?.[i] && <Histograma h={hist[i]} rango={r} />}
          <div className="imgp-row">
            <input type="number" step="0.01" value={r[0]} aria-label={`Mínimo ${etiqueta(i)}`}
              onChange={(e) => fijar(i, 0, Number(e.target.value))} />
            <input type="number" step="0.01" value={r[1]} aria-label={`Máximo ${etiqueta(i)}`}
              onChange={(e) => fijar(i, 1, Number(e.target.value))} />
          </div>
        </div>
      ))}
      <div className="s2-acciones">
        {hist && p.grupo !== 'indice' && <button className="imgp-chip" onClick={auto} disabled={ocupado}>Auto (p2–p98)</button>}
        <button className="imgp-chip" data-testid="s2-aplicar" disabled={ocupado || rangos.some(([a, b]) => !(a < b))}
          onClick={() => void onAplicar(rangos)}>Aplicar</button>
        <button className="imgp-chip" disabled={ocupado} onClick={() => { onCambio(porDefecto()); void onAplicar(null) }}>Restablecer</button>
      </div>
    </>
  )
}

export function EscenaS2Vista({ vista, ocupado, onAplicar }: {
  vista: EscenaVista
  ocupado: boolean
  onAplicar: (rangos: [number, number][] | null) => Promise<void>
}) {
  const p = productoS2(vista.producto)
  const canales = p.grupo === 'color' ? 3 : 1
  const porDefecto = (): [number, number][] => Array.from({ length: canales }, () => rangoPorDefecto(p))
  const [rangos, setRangos] = useState<[number, number][]>(vista.rangos ?? porDefecto())
  const [hist, setHist] = useState<HistogramaBanda[] | null>(null)
  const [inspeccionar, setInspeccionar] = useState(false)
  const [pixel, setPixel] = useState<PixelS2 | null>(null)
  const [cargando, setCargando] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const punto = useMapStore((s) => s.puntoMarcado)

  // Otra escena u otro producto: rangos y lecturas de ESA.
  useEffect(() => {
    setRangos(vista.rangos ?? porDefecto())
    setHist(null)
    setPixel(null)
  }, [vista.id, vista.producto]) // eslint-disable-line react-hooks/exhaustive-deps

  const con = async (que: string, fn: () => Promise<void>) => {
    setError(null)
    setCargando(que)
    try { await fn() } catch (e) { setError(e instanceof Error ? e.message : String(e)) } finally { setCargando(null) }
  }

  const verHistograma = () => con('hist', async () => {
    const res = await correrImagery('imagery_band_histogram', { scene_id: vista.id, bands: p.bandas })
    setHist(((res.facts?.bandas ?? []) as HistogramaBanda[]))
  })

  // Con «inspeccionar» activo, cada clic en el mapa lee ese píxel de la escena.
  useEffect(() => {
    if (!inspeccionar || !punto) return
    void con('pixel', async () => {
      const res = await correrImagery('imagery_pixel', {
        scene_id: vista.id, point_geojson: { type: 'Point', coordinates: [punto.lon, punto.lat] },
      })
      setPixel(res.facts as unknown as PixelS2)
    })
  }, [punto, inspeccionar]) // eslint-disable-line react-hooks/exhaustive-deps

  const clases = hist?.find((h) => h.clases)?.clases

  return (
    <section className="imgp-card" data-testid="s2-vista">
      <header>{p.etiqueta}</header>
      <span className="imgp-sub">{fechaCorta(vista.fecha)} · escena completa</span>
      <code className="s2-id" title={vista.id}>{vista.id}</code>
      {error && <p className="imgp-sub" role="alert">{error}</p>}

      <details className="s2-apartado" onToggle={(e) => { if ((e.target as HTMLDetailsElement).open && !hist && p.bandas.length) void verHistograma() }}>
        <summary><SlidersHorizontal className="w-3 h-3" /> {ajustable(p) ? 'Contraste' : 'Composición de la escena'}</summary>
        {cargando === 'hist' && <p className="imgp-sub"><Loader2 className="w-3 h-3 animate-spin" /> Leyendo la escena…</p>}
        {clases && (
          <ul className="s2-clases" data-testid="s2-clases">
            {clases.filter((c) => c.pct > 0).sort((a, b) => b.pct - a.pct).map((c) => (
              <li key={c.valor}><span>{c.etiqueta}</span><b>{c.pct}%</b></li>
            ))}
          </ul>
        )}
        {ajustable(p) && (
          <Canales p={p} rangos={rangos} hist={hist} ocupado={ocupado} onCambio={setRangos}
            onAplicar={onAplicar} porDefecto={porDefecto} />
        )}
      </details>

      <details className="s2-apartado" onToggle={(e) => setInspeccionar((e.target as HTMLDetailsElement).open)}>
        <summary><Crosshair className="w-3 h-3" /> Inspeccionar píxel</summary>
        <p className="imgp-sub">Haz clic en el mapa: el valor de cada banda en ese punto.</p>
        {cargando === 'pixel' && <p className="imgp-sub"><Loader2 className="w-3 h-3 animate-spin" /> Leyendo el píxel…</p>}
        {pixel && <TablaPixel pixel={pixel} />}
      </details>
      <Descargas vista={vista} />
    </section>
  )
}
