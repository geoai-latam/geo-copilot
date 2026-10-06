/**
 * Helpers PUROS del panel de herramientas MCP (E3.2).
 *
 * Nada aquí conoce una tool concreta: el formulario sale del `input_schema`
 * (JSON Schema) que publica cada servidor y de su `_meta.geo` (qué argumentos
 * son geometría). Separado del componente para testearlo sin DOM.
 */

import type { JsonSchemaProp, McpToolInfo } from '@/services/api'

export type FieldKind = 'geo' | 'enum' | 'date' | 'number' | 'integer' | 'boolean' | 'json' | 'text'

export interface FormField {
  name: string
  label: string
  kind: FieldKind
  required: boolean
  description?: string
  options?: string[]
  defaultValue?: unknown
  minimum?: number
  maximum?: number
  /** Solo geo: qué acepta el servidor (geometry / bbox / layer_ref). */
  accepts?: string[]
}

/** Tipo base de una propiedad; `anyOf: [{type: X}, {type: null}]` (opcional) → X. */
export function baseType(prop: JsonSchemaProp): string | undefined {
  if (typeof prop.type === 'string') return prop.type
  if (Array.isArray(prop.type)) return prop.type.find((t) => t !== 'null')
  const noNull = (prop.anyOf ?? []).filter((p) => p.type !== 'null')
  return noNull.length === 1 ? baseType(noNull[0]) : undefined
}

function enumOf(prop: JsonSchemaProp): string[] | undefined {
  const e = prop.enum ?? (prop.anyOf ?? []).find((p) => p.enum)?.enum
  return e?.filter((v): v is string | number => v !== null).map(String)
}

// Solo presentación: un selector de fecha en vez de texto libre. Si el campo no
// es una fecha el servidor lo rechaza igual con su propio mensaje.
const PARECE_FECHA = /(^|_)(date|fecha)(_|$)/i

/** Campos del formulario, en el orden del esquema; los geo primero. */
export function formFields(tool: McpToolInfo): FormField[] {
  const props = tool.input_schema.properties ?? {}
  const required = new Set(tool.input_schema.required ?? [])
  const geo = tool.geo?.inputs ?? {}
  const fields = Object.entries(props).map(([name, prop]): FormField => { // eslint-disable-line complexity -- deuda congelada (F1); partir, no subir
    const t = baseType(prop)
    const opciones = enumOf(prop)
    // un número (lon, lat) nunca es una capa aunque el servidor lo marque como geo
    const esGeo = name in geo && t !== 'number' && t !== 'integer'
    const kind: FieldKind =
      esGeo ? 'geo'
        : opciones ? 'enum'
        : prop.format === 'date' || (t === 'string' && PARECE_FECHA.test(name)) ? 'date'
        : t === 'number' ? 'number'
        : t === 'integer' ? 'integer'
        : t === 'boolean' ? 'boolean'
        : t === 'object' || t === 'array' ? 'json'
        : 'text'
    return {
      name,
      label: prop.title ?? name,
      kind,
      required: required.has(name),
      description: prop.description,
      options: opciones,
      defaultValue: prop.default,
      minimum: prop.minimum,
      maximum: prop.maximum,
      accepts: esGeo ? geo[name]?.accepts ?? [] : undefined,
    }
  })
  return [...fields.filter((f) => f.kind === 'geo'), ...fields.filter((f) => f.kind !== 'geo')]
}

/** Una capa del mapa tal como la necesita el formulario. */
export interface LayerOption {
  id: string
  name: string
  datasetId?: string
  data?: { type: 'FeatureCollection'; features: unknown[] } | null
}

/** Valor de un selector geo: la zona visible o el id de una capa del mapa. */
export const VIEWPORT = 'viewport'
/** El punto que el usuario marcó en el mapa (el núcleo lo resuelve del map_context). */
export const PUNTO = 'punto'

type Resultado = { ok: true; args: Record<string, unknown> } | { ok: false, error: string }

/**
 * Argumentos para `run` desde el estado del formulario.
 * - geo: `viewport` viaja tal cual (el núcleo pone la zona visible); una capa del
 *   workspace viaja por su `ds_…`; una capa que solo vive en el navegador viaja
 *   como GeoJSON (o su bbox, si el servidor solo acepta bbox).
 * - vacíos se omiten: el servidor aplica sus defaults declarados.
 */
export function buildArguments( // eslint-disable-line complexity -- deuda congelada (F1); partir, no subir
  fields: FormField[],
  values: Record<string, string>,
  layers: LayerOption[],
): Resultado {
  const args: Record<string, unknown> = {}
  for (const f of fields) {
    const v = values[f.name] ?? ''
    if (f.kind === 'geo') {
      if (v === '' || v === VIEWPORT) {
        if (v === VIEWPORT || f.required) args[f.name] = VIEWPORT
        continue
      }
      if (v === PUNTO) {
        args[f.name] = PUNTO
        continue
      }
      const capa = layers.find((l) => l.id === v)
      if (!capa) return { ok: false, error: `La capa elegida para «${f.label}» ya no está en el mapa.` }
      if (capa.datasetId) {
        args[f.name] = capa.datasetId
      } else if (capa.data?.features?.length) {
        const soloBbox = !f.accepts?.includes('geometry') && f.accepts?.includes('bbox')
        args[f.name] = soloBbox ? geojsonBbox(capa.data as never) : capa.data
      } else {
        return { ok: false, error: `La capa «${capa.name}» no tiene geometría disponible en el navegador.` }
      }
      continue
    }
    if (v === '') {
      if (f.required && f.defaultValue === undefined) {
        return { ok: false, error: `Falta «${f.label}».` }
      }
      continue
    }
    if (f.kind === 'number' || f.kind === 'integer') {
      const n = Number(v)
      if (!Number.isFinite(n) || (f.kind === 'integer' && !Number.isInteger(n))) {
        return { ok: false, error: `«${f.label}» debe ser un número${f.kind === 'integer' ? ' entero' : ''}.` }
      }
      args[f.name] = n
    } else if (f.kind === 'boolean') {
      args[f.name] = v === 'true'
    } else if (f.kind === 'json') {
      try {
        args[f.name] = JSON.parse(v)
      } catch {
        return { ok: false, error: `«${f.label}» no es JSON válido.` }
      }
    } else {
      args[f.name] = v
    }
  }
  return { ok: true, args }
}

/** Hechos del servidor como filas legibles: escalares hasta dos niveles; listas, su tamaño. */
export function factRows(facts: Record<string, unknown>, prefijo = '', nivel = 0): [string, string][] {
  const filas: [string, string][] = []
  for (const [k, v] of Object.entries(facts ?? {})) {
    const clave = prefijo ? `${prefijo}.${k}` : k
    if (v === null || v === undefined) continue
    if (Array.isArray(v)) {
      filas.push([clave, v.every((x) => typeof x !== 'object') ? v.join(', ') : `${v.length} elementos`])
    } else if (typeof v === 'object') {
      if (nivel < 1) filas.push(...factRows(v as Record<string, unknown>, clave, nivel + 1))
    } else {
      filas.push([clave, typeof v === 'number' && !Number.isInteger(v) ? v.toFixed(3) : String(v)])
    }
  }
  return filas
}

/** bbox de una FeatureCollection. */
export function geojsonBbox(
  fc: { features?: { geometry?: { coordinates?: unknown } }[] } | null,
): [number, number, number, number] | null {
  if (!fc?.features?.length) return null
  let minx = Infinity, miny = Infinity, maxx = -Infinity, maxy = -Infinity
  const walk = (c: unknown): void => {
    if (!Array.isArray(c)) return
    if (typeof c[0] === 'number' && typeof c[1] === 'number') {
      minx = Math.min(minx, c[0]); maxx = Math.max(maxx, c[0])
      miny = Math.min(miny, c[1]); maxy = Math.max(maxy, c[1])
      return
    }
    for (const child of c) walk(child)
  }
  for (const f of fc.features) walk(f.geometry?.coordinates)
  return Number.isFinite(minx) ? [minx, miny, maxx, maxy] : null
}
