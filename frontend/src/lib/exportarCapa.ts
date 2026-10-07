/**
 * Exportar una capa a archivo: qué se pide al servidor (toda, lo filtrado o lo seleccionado) y
 * la descarga. Una capa que no vive en el workspace (un dibujo, un resultado pequeño) solo sale
 * como GeoJSON, armado aquí mismo.
 */
import { workspaceApi } from '@/services/api'
import { type MapLayer, useSessionStore } from '@/stores'

export const FORMATOS = [
  { id: 'gpkg', nombre: 'GeoPackage (.gpkg)' },
  { id: 'shp', nombre: 'Shapefile (.zip)' },
  { id: 'kml', nombre: 'KML — Google Earth' },
  { id: 'geojson', nombre: 'GeoJSON' },
  { id: 'csv', nombre: 'CSV (geometría en WKT)' },
  { id: 'dxf', nombre: 'DXF — CAD, solo dibujo' },
] as const

/** Sistemas de referencia habituales; «otro» admite cualquier EPSG. */
export const SISTEMAS = [
  { id: 'EPSG:4326', nombre: 'WGS84 (grados)' },
  { id: 'EPSG:9377', nombre: 'MAGNA-SIRGAS Origen Nacional (Colombia)' },
  { id: 'EPSG:3857', nombre: 'Web Mercator' },
] as const

export type Alcance = 'toda' | 'filtro' | 'seleccion'

/** Qué alcances tienen sentido para la capa: lo filtrado si tiene filtro, lo seleccionado si hay. */
export function alcancesDe(capa: MapLayer): Alcance[] {
  const out: Alcance[] = ['toda']
  if (capa.filtro?.length) out.push('filtro')
  if (capa.seleccion && (capa.seleccion.ids?.length || capa.seleccion.where)) out.push('seleccion')
  return out
}

/** Los parámetros de la petición según el alcance (la selección respeta también el filtro). */
export function pedidoDe(capa: MapLayer, alcance: Alcance): { filtro: unknown[] | null; ids: number[] | null } {
  const filtro = alcance === 'toda' ? [] : [...(capa.filtro ?? [])]
  let ids: number[] | null = null
  if (alcance === 'seleccion' && capa.seleccion) {
    if (capa.seleccion.ids?.length) ids = capa.seleccion.ids
    else if (capa.seleccion.where) filtro.push(capa.seleccion.where)
  }
  return { filtro: filtro.length ? filtro : null, ids }
}

function guardar(blob: Blob, archivo: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = archivo
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

/** Descarga un dataset del workspace (el enlace que deja el agente con `export_layer`). */
export async function descargarDataset(datasetId: string, formato: string, crs: string | null) {
  const sessionId = useSessionStore.getState().sessionId
  if (!sessionId) throw new Error('No hay sesión activa.')
  const r = await workspaceApi.exportar(sessionId, datasetId, { formato, crs })
  guardar(r.blob, r.archivo)
  return { archivo: r.archivo, elementos: r.elementos }
}

/** Descarga la capa; devuelve el nombre del archivo y cuántos elementos lleva. */
export async function exportarCapa(capa: MapLayer, formato: string, crs: string | null,
                                   alcance: Alcance): Promise<{ archivo: string; elementos: number }> {
  const sessionId = useSessionStore.getState().sessionId
  if (capa.datasetId && sessionId) {
    const r = await workspaceApi.exportar(sessionId, capa.datasetId, { formato, crs, ...pedidoDe(capa, alcance) })
    guardar(r.blob, r.archivo)
    return { archivo: r.archivo, elementos: r.elementos }
  }
  if (formato !== 'geojson') throw new Error('Esta capa no está en el servidor: solo se puede descargar como GeoJSON.')
  const datos = capa.data ?? { type: 'FeatureCollection', features: [] }
  const archivo = `${(capa.name || 'capa').normalize('NFKD').replace(/[^\w]+/g, '_').toLowerCase()}.geojson`
  guardar(new Blob([JSON.stringify(datos)], { type: 'application/geo+json' }), archivo)
  return { archivo, elementos: datos.features?.length ?? 0 }
}
