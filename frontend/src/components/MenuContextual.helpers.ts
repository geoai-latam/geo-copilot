/**
 * FH.8 — lógica del menú contextual (sin JSX): a qué se aplica, qué acciones admite lo
 * señalado y qué valor recibe el argumento objetivo de cada una.
 */
import type { Accion } from '@/services/api'
import type { MapLayer } from '@/stores/mapStore'
import { identidad } from '@/lib/seleccion'

/** Lo señalado: lo SELECCIONADO en una capa (clic derecho sobre un elemento) o la capa entera. */
export interface Objetivo {
  capaId: string
  alcance: 'seleccion' | 'capa'
  /** Tipo de geometría (el de sus elementos). */
  geometria: string | null
}

/** El tipo de geometría de una capa: el de sus teselas o el de su primer elemento. */
export function geometriaDeCapa(capa: MapLayer): string | null {
  const t = capa.tiles?.geometryType ?? capa.data?.features?.find((f) => f.geometry)?.geometry?.type
  return t ? String(t) : null
}

const acepta = (a: Accion) => a.geo?.inputs?.[a.objetivo]?.accepts ?? []

/**
 * El valor del argumento objetivo, o por qué esta acción no aplica aquí:
 * - una herramienta del workspace (`dataset`) necesita una capa del workspace: su `ds_…`, o
 *   `seleccion` (el núcleo materializa lo seleccionado);
 * - un servicio (`geometry`/`layer_ref`) recibe la referencia si la capa está en el workspace
 *   (`seleccion` o su `ds_…`) y, si solo vive en el navegador, su GeoJSON.
 */
export function valorObjetivo(accion: Accion, capa: MapLayer, alcance: Objetivo['alcance']):
  { ok: true; valor: unknown } | { ok: false; motivo: string } {
  const a = acepta(accion)
  if (capa.datasetId) return { ok: true, valor: alcance === 'seleccion' ? 'seleccion' : capa.datasetId }
  if (a.includes('dataset') && !a.includes('geometry')) {
    return { ok: false, motivo: 'solo para capas guardadas en el workspace' }
  }
  const feats = capa.data?.features ?? []
  const ids = new Set(capa.seleccion?.ids ?? [])
  const elegidos = alcance === 'seleccion' ? feats.filter((f, i) => ids.has(identidad(f, i))) : feats
  if (!elegidos.length) return { ok: false, motivo: 'no hay elementos con geometría' }
  return { ok: true, valor: { type: 'FeatureCollection', features: elegidos } }
}

/** Las acciones del listado que se pueden aplicar a esta capa (con su motivo si no). */
export function paraCapa(acciones: Accion[], capa: MapLayer, alcance: Objetivo['alcance']) {
  return acciones.map((accion) => {
    const v = valorObjetivo(accion, capa, alcance)
    return { accion, disponible: v.ok, motivo: v.ok ? null : v.motivo }
  })
}
