/**
 * FH.6 — la leyenda VIVA de una capa, generada de lo que el mapa dibuja: las clases
 * del StyleSpec (categorías, graduados), el color único, la rampa de un mapa de
 * calor, la agrupación de un cluster y la rampa continua de un raster de índice.
 * Una capa que no tiene nada que explicar (imagen en color) no inventa una clase.
 */
import { paletteFrom } from '@/lib/maplibreCluster'
import { dentroDeClase } from '@/lib/maplibreSymbology'
import { cumpleTodas } from '@/lib/seleccion'
import type { MapLayer } from '@/stores/mapStore'

/** Rampa del índice que devuelven los rasters de imagery (NDVI…: rojo → verde). */
export const RAMPA_INDICE = ['#a50026', '#d78230', '#fee08b', '#a6d96a', '#1a9850', '#006837']

export interface Leyenda {
  titulo: string
  filas?: { label: string; color: string; count?: number | null }[]
  rampa?: { colores: string[]; min?: number; max?: number; campo?: string | null; nota?: string | null }
  nota?: string
}

const esRaster = (l: MapLayer) => l.kind === 'raster-xyz' || l.kind === 'arcgis-image' || l.kind === 'wms'

/** Raster: sus clases (la SCL de Sentinel-2) o su rampa, con los colores que declara el servidor
 * (NDWI azul, una banda en gris…) o la del índice de siempre. Una imagen en color no tiene. */
function leyendaRaster(l: MapLayer): Leyenda | null {
  const ley = l.legend
  if (ley?.clases?.length) {
    return { titulo: l.name, filas: ley.clases.map((c) => ({ label: c.etiqueta, color: c.color })), nota: ley.field }
  }
  if (ley && typeof ley.min === 'number' && typeof ley.max === 'number') {
    const colores = ley.colores?.length ? ley.colores : RAMPA_INDICE
    return { titulo: l.name, rampa: { colores, min: ley.min, max: ley.max, campo: ley.field ?? null, nota: ley.nota ?? null } }
  }
  return null
}

export function leyendaDe(l: MapLayer): Leyenda | null {
  if (!l.visible) return null
  if (esRaster(l)) return leyendaRaster(l)
  const s = l.symbology
  const titulo = l.name
  const tipo = s?.symbology_type
  if (tipo === 'heatmap') {
    return { titulo, rampa: { colores: paletteFrom(s), campo: s?.heatmap_intensity_field ?? null },
             nota: 'densidad de elementos (baja → alta)' }
  }
  if (tipo === 'cluster') {
    return { titulo, filas: [{ label: 'elementos agrupados por cercanía (el número es cuántos)', color: l.color }] }
  }
  const clases = s?.class_breaks ?? []
  if (clases.length) {
    const cuentas = cuentasVisibles(l)
    return {
      titulo,
      filas: clases.map((b, i) => ({ label: String(b.label), color: String(b.color),
                                     count: cuentas ? cuentas[i] : (l.filtro?.length ? null : b.count ?? null) })),
      ...(s?.classification_field ? { nota: `por ${s.classification_field}${s.color_scheme ? ` · rampa ${s.color_scheme}` : ''}` } : {}),
    }
  }
  return { titulo, filas: [{ label: 'Todos los elementos', color: l.color }] }
}

/**
 * Con la capa FILTRADA, cuántos elementos VISIBLES caen en cada clase (V5: la leyenda seguía
 * contando los 27 con solo 3 a la vista). En teselas no se puede contar aquí → null (sin conteo).
 */
export function cuentasVisibles(l: MapLayer): number[] | null {
  const s = l.symbology
  const clases = s?.class_breaks ?? []
  const campo = s?.classification_field
  if (!l.filtro?.length || !campo || !clases.length || l.kind !== 'vector-geojson') return null
  const cuentas = clases.map(() => 0)
  for (const f of l.data?.features ?? []) {
    const props = (f.properties ?? {}) as Record<string, unknown>
    if (!cumpleTodas(l.filtro, props)) continue
    const v = props[campo]
    const i = clases.findIndex((b, j) => {
      const min = (b as { min_value?: number | null }).min_value
      const max = (b as { max_value?: number | null }).max_value
      if (typeof min === 'number' && typeof max === 'number') {
        return dentroDeClase(Number(v), min, max, j === clases.length - 1) // la regla del mapa
      }
      // Un rango con un solo límite (clase manual vacía) no lo pinta el mapa: no cuenta nada.
      if (typeof min === 'number' || typeof max === 'number') return false
      return String(v) === String(b.label)
    })
    if (i >= 0) cuentas[i] += 1
  }
  return cuentas
}
