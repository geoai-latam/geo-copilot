/**
 * Viewport del mapa (bbox visible) — engine-agnóstico.
 *
 * Lee los límites del mapa desde el puente de solo lectura window.__mapViewport
 * (expuesto siempre por MapLibreMap; ruta de datos de prod). Cae a window.__mlmap
 * (instancia completa, solo DEV/E2E) por compatibilidad. Usado por
 * utils/mapContext para el `map_context` que se adjunta a cada consulta.
 */
interface MapLike {
  getBounds: () => { toArray: () => number[][] }
}

export function getViewBounds(): [number, number, number, number] | null {
  const w = window as unknown as { __mapViewport?: MapLike; __mlmap?: MapLike }
  const m = w.__mapViewport ?? w.__mlmap
  if (!m) return null
  try {
    const b = m.getBounds().toArray() // [[west, south], [east, north]]
    return [b[0][0], b[0][1], b[1][0], b[1][1]]
  } catch {
    return null
  }
}
