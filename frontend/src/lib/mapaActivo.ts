/** El mapa principal (MapLibre), para lo que necesita su cámara (la cortina de comparación). */
import type * as maplibregl from 'maplibre-gl'

let mapa: maplibregl.Map | null = null
const oyentes = new Set<(m: maplibregl.Map | null) => void>()

export function registrarMapaActivo(m: maplibregl.Map | null) {
  mapa = m
  oyentes.forEach((f) => f(m))
}

export function mapaActivo(): maplibregl.Map | null {
  return mapa
}

export function alCambiarMapa(f: (m: maplibregl.Map | null) => void): () => void {
  oyentes.add(f)
  return () => oyentes.delete(f)
}
