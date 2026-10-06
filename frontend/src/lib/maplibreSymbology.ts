/**
 * MAP-SYMBOLOGY — traduce un plan de simbología (LayerSymbology del backend) a
 * expresiones data-driven de MapLibre GL, replicando la semántica del motor
 * anterior (el mapa anterior: colorForFeature / sizeForFeature).
 *
 * Todas las funciones son PURAS: reciben el plan + un valor base y devuelven un
 * OBJETO PLANO (expresión MapLibre, que es JSON: `['match', …]`, `['case', …]`,
 * `['step', …]`, `['interpolate', …]`) o el valor base tal cual. No importan
 * 'maplibre-gl' en runtime (jsdom no tiene WebGL) — solo tipos del dominio.
 *
 * PARIDAD con el motor anterior:
 *  - unique_values: el motor anterior hace `label === String(valor)` y devuelve
 *    el color del break que coincide (el mapa anterior:69-73). Aquí se replica con
 *    `['match', ...]`. EXCEPCIÓN aprobada: el bug de floats categóricos
 *    (`String(5.0)` en JS es `"5"`, pero el backend emite el label `"5.0"`, así
 *    que nunca casaban) se CORRIGE: cuando todos los labels son numéricos se
 *    compara como número (`['to-number', ['get', field]]` contra claves
 *    numéricas), de modo que 5, 5.0, "5" y "5.0" casan con el mismo break.
 *  - graduated_colors / graduated_symbols: el motor anterior busca el break cuyo
 *    intervalo [min, max) contiene el valor — la última clase incluye el max
 *    (el mapa anterior:81-91). Se replica con `['case', ...]` de intervalos
 *    semiabiertos + fallback al color base para valores fuera de rango (el motor
 *    anterior devolvía null ⇒ color base). Un `['step', ...]` NO reproduce ni el
 *    fallback base fuera de rango ni los intervalos semiabiertos exactos, por eso
 *    se usa `case`.
 *  - tamaño graduated_symbols: el motor anterior mapea el ÍNDICE del break (no el
 *    valor continuo) a `min + (i/(n-1)) * (max-min)` (el mapa anterior:112-121). Es
 *    discreto por clase, así que se replica con `['case', ...]` de tamaños
 *    precomputados por break — un `['interpolate', ...]` sobre el valor daría una
 *    rampa continua distinta y rompería la paridad pixel-perfect.
 */
import type { LayerSymbology, ClassBreak } from '@/types'

// ──────────────────────────────────────────────────────────────────────────
// Tipos de expresión (JSON plano; nada de maplibre-gl en runtime)
// ──────────────────────────────────────────────────────────────────────────

/** Valor JSON que puede aparecer dentro de una expresión MapLibre. */
export type MaplibreValue =
  | string
  | number
  | boolean
  | null
  | MaplibreValue[]

/** Una expresión MapLibre es siempre un array `[operador, ...args]`. */
export type MaplibreExpression = MaplibreValue[]

/** El paint `*-color` acepta un color literal o una expresión data-driven. */
export type ColorSpec = string | MaplibreExpression

/** El paint `circle-radius` acepta un número literal o una expresión. */
export type RadiusSpec = number | MaplibreExpression

/** Campo + breaks extraídos del plan (o `null` si no hay clasificación). */
export interface ClassificationPlan {
  field: string
  breaks: ClassBreak[]
}

// ──────────────────────────────────────────────────────────────────────────
// Auxiliar: extraer campo de clasificación + class_breaks
// ──────────────────────────────────────────────────────────────────────────

/**
 * Extrae el `classification_field` y los `class_breaks` del plan. Devuelve
 * `null` si falta el campo o no hay breaks — el caller usa entonces el valor
 * base (mismo guard que el mapa anterior:65).
 */
export function extractClassification(
  symbology: LayerSymbology | undefined,
): ClassificationPlan | null {
  if (!symbology) return null
  const field = symbology.classification_field
  const breaks = symbology.class_breaks
  if (!field || !breaks || breaks.length === 0) return null
  return { field, breaks }
}

// ──────────────────────────────────────────────────────────────────────────
// Helpers internos
// ──────────────────────────────────────────────────────────────────────────

/** ¿El label representa un número finito? (para el fix de floats categóricos) */
function labelIsNumeric(label: string): boolean {
  const t = label.trim()
  if (t === '') return false
  return Number.isFinite(Number(t))
}

/** Expresión que lee el valor numérico del campo. */
function numericValue(field: string): MaplibreExpression {
  return ['to-number', ['get', field]]
}

/**
 * Construye el `['match', ...]` para unique_values.
 *
 * Motor anterior (el mapa anterior:69-73): `label === String(valor)`.
 * Fix aprobado: si TODOS los labels son numéricos se compara como número, así
 * `"5"`, `"5.0"`, `5` y `5.0` casan con el mismo break (el bug del motor
 * anterior era que `String(5.0) === '5' !== '5.0'`).
 */
function buildUniqueMatch(
  field: string,
  breaks: ClassBreak[],
  fallback: string,
): ColorSpec {
  // R4.5: el backend puede emitir una clase "Otros" (categorías fuera del top
  // perfilado). NO es un valor a matchear — es el color de FALLBACK del match,
  // así el mapa pinta igual que declara la leyenda.
  const others = breaks.find((b) => b.label === 'Otros')
  if (others) fallback = others.color
  breaks = breaks.filter((b) => b.label !== 'Otros')

  const pairs = breaks.map((b) => ({ label: b.label, color: b.color }))
  const allNumeric =
    pairs.length > 0 && pairs.every((p) => labelIsNumeric(p.label))

  const args: MaplibreValue[] = ['match']
  const seen = new Set<string | number>()

  if (allNumeric) {
    // Comparar como número (fix de floats categóricos).
    args.push(numericValue(field))
    for (const p of pairs) {
      const key = Number(p.label)
      if (seen.has(key)) continue // match no admite claves duplicadas
      seen.add(key)
      args.push(key, p.color)
    }
  } else {
    // Comparar como string, replicando `String(valor)` del motor anterior.
    args.push(['to-string', ['get', field]])
    for (const p of pairs) {
      if (seen.has(p.label)) continue
      seen.add(p.label)
      args.push(p.label, p.color)
    }
  }

  // match necesita al menos un par label→salida; si no, devolver base.
  if (args.length < 4) return fallback
  args.push(fallback)
  return args
}

/**
 * ¿`n` cae en la clase `[lo, hi)` (la última, `[lo, hi]`)? F7 (auditoría): un rango degenerado
 * (`lo === hi`, «Cluster 0» = [0, 0], «0 incidentes») es IGUALDAD. Es la misma regla que cuentan el
 * agente de simbología (`_clase_de`) y la leyenda (`cuentasVisibles`).
 */
export function dentroDeClase(n: number, lo: number, hi: number, esUltima: boolean): boolean {
  if (lo === hi) return n === lo
  return n >= lo && (n < hi || (esUltima && n <= hi))
}

/** `dentroDeClase` como expresión de MapLibre. */
function enClase(v: MaplibreExpression, lo: number, hi: number, esUltima: boolean): MaplibreExpression {
  if (lo === hi) return ['==', v, lo]
  return ['all', ['>=', v, lo], esUltima ? ['<=', v, hi] : ['<', v, hi]]
}

/**
 * Construye el `['case', ...]` de intervalos para graduated (color o tamaño).
 *
 * Motor anterior (el mapa anterior:81-91 y 112-121): itera los breaks; los que
 * tienen min/max null se saltan; el intervalo es `[lo, hi)` salvo el ÚLTIMO
 * break (por índice global) que es `[lo, hi]`. Fuera de rango ⇒ fallback.
 */
function buildGraduatedCase(
  field: string,
  breaks: ClassBreak[],
  outputs: MaplibreValue[],
  fallback: MaplibreValue,
): MaplibreValue {
  const v = numericValue(field)
  const args: MaplibreValue[] = ['case']

  for (let i = 0; i < breaks.length; i++) {
    const b = breaks[i]
    const lo = b.min_value
    const hi = b.max_value
    if (lo == null || hi == null) continue // mismo `continue` del motor anterior
    args.push(enClase(v, lo, hi, i === breaks.length - 1), outputs[i])
  }

  // Sin condiciones válidas ⇒ valor base (no envolvemos en case).
  if (args.length < 3) return fallback
  args.push(fallback)
  return args
}

// ──────────────────────────────────────────────────────────────────────────
// API pública
// ──────────────────────────────────────────────────────────────────────────

/**
 * Expresión de color de relleno (`fill-color` / `circle-color`) por feature.
 *
 * - unique_values     → `['match', ['to-number'|'to-string', ['get', f]], …]`
 * - graduated_colors  → `['case', …intervalos…, baseColor]`
 * - graduated_symbols → igual que graduated_colors (el motor anterior también
 *   colorea por break en este modo, el mapa anterior:685-690)
 * - single_symbol / desconocido / sin clasificación → `baseColor`
 */
export function fillColorExpression(
  symbology: LayerSymbology | undefined,
  baseColor: string,
): ColorSpec {
  const plan = extractClassification(symbology)
  if (!plan || !symbology) return baseColor
  const { field, breaks } = plan
  const type = symbology.symbology_type

  if (type === 'unique_values') {
    return buildUniqueMatch(field, breaks, baseColor)
  }
  if (type === 'graduated_colors' || type === 'graduated_symbols') {
    const colors: MaplibreValue[] = breaks.map((b) => b.color)
    const expr = buildGraduatedCase(field, breaks, colors, baseColor)
    return typeof expr === 'string' ? expr : (expr as MaplibreExpression)
  }
  return baseColor
}

/**
 * Expresión de color de línea (`line-color`). Misma semántica de clasificación
 * que el relleno — el motor anterior aplica el mismo `colorForFeature` al
 * material de la polyline (el mapa anterior:702-707).
 */
export function lineColorExpression(
  symbology: LayerSymbology | undefined,
  baseColor: string,
): ColorSpec {
  return fillColorExpression(symbology, baseColor)
}

/**
 * Expresión de radio de círculo (`circle-radius`) por feature.
 *
 * Solo graduated_symbols produce expresión; el resto usa `baseSize`. El tamaño
 * es DISCRETO por break: `min + (i/(n-1)) * (max-min)` con el índice global del
 * break (el mapa anterior:112-121). Defaults del motor anterior: min 4, max 32.
 */
export function circleRadiusExpression(
  symbology: LayerSymbology | undefined,
  baseSize: number,
): RadiusSpec {
  const plan = extractClassification(symbology)
  if (!plan || !symbology) return baseSize
  if (symbology.symbology_type !== 'graduated_symbols') return baseSize

  const { field, breaks } = plan
  const minSize = symbology.symbol_size_min ?? 4
  const maxSize = symbology.symbol_size_max ?? 32
  const n = breaks.length

  const sizes: MaplibreValue[] = breaks.map((_, i) => {
    const t = n > 1 ? i / (n - 1) : 0.5
    return minSize + t * (maxSize - minSize)
  })

  const expr = buildGraduatedCase(field, breaks, sizes, baseSize)
  return typeof expr === 'number' ? expr : (expr as MaplibreExpression)
}

/**
 * Expresión de etiqueta (`text-field`) — muestra el valor del campo de
 * clasificación si existe, si no `null` (sin etiquetas). El motor anterior no
 * rotula por defecto, así que solo hay label cuando hay campo temático.
 */
export function textFieldExpression(
  symbology: LayerSymbology | undefined,
): MaplibreExpression | null {
  const plan = extractClassification(symbology)
  if (!plan) return null
  return ['to-string', ['get', plan.field]]
}
