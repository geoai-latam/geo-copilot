/** FH.7 — lógica del panel «cómo se hizo» (sin JSX: se prueba sin montar nada). */
import type { PasoProcedencia } from '@/services/api'

/** Nombre legible de la capacidad que produjo un paso (si no se conoce, su id tal cual). */
const NOMBRES: Record<string, string> = {
  'core.query_data': 'Consulta a la base de datos',
  'core.query_database': 'Consulta a la base de datos',
  'core.consulta': 'Consulta a la base de datos',
  'core.spatial_operation': 'Operación espacial (script de Python)',
  'core.analyze': 'Análisis (script de Python)',
  'core.buffer': 'Buffer',
  'core.overlay': 'Superposición',
  'core.spatial_join': 'Unión espacial',
  'core.aggregate': 'Agregación por zonas',
  'core.autocorrelation': 'Autocorrelación espacial (LISA / Gi*)',
  'core.analyze_layer': 'Análisis (script de Python)',
  'core.load_external': 'Carga de un servicio externo',
  'core.add_measure': 'Medida de cada elemento',
  'core.seleccion': 'Selección del usuario',
  'core.filtro': 'Filtro de la capa',
  'user.sketch': 'Dibujado por el usuario',
}

export function nombreCapacidad(cap: string): string {
  if (NOMBRES[cap]) return NOMBRES[cap]
  const mcp = /^mcp\.([^.]+)\.(.+)$/.exec(cap)
  if (mcp) return `Servicio «${mcp[1]}» · ${mcp[2]}`
  return cap
}

/** Los argumentos en líneas «clave: valor»; un `ds_…` se nombra con el paso al que corresponde. */
export function argumentos(args: Record<string, unknown> | undefined, pasos: PasoProcedencia[]): [string, string][] {
  const nombre = (v: unknown): string => {
    if (typeof v === 'string' && v.startsWith('ds_')) {
      const p = pasos.find((x) => x.dataset_id === v)
      return p?.nombre ? `«${p.nombre}»` : v
    }
    if (Array.isArray(v)) return v.map(nombre).join(', ')
    if (v && typeof v === 'object') return JSON.stringify(v)
    return String(v)
  }
  return Object.entries(args ?? {}).filter(([, v]) => v !== null && v !== undefined && v !== '')
    .map(([k, v]) => [k, nombre(v)])
}
