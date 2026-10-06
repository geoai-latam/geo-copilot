/**
 * MAP-SYMBOLOGY — tests exhaustivos de la traducción del plan de simbología a
 * expresiones data-driven de MapLibre, con PARIDAD demostrada contra el motor
 * anterior (colorForFeature / sizeForFeature).
 *
 * Estrategia: además de assertar la ESTRUCTURA de cada expresión, se incluye un
 * mini-evaluador puro del subconjunto de operadores que emitimos (match, case,
 * to-number, to-string, get, all, >=, <, <=, ==) y una re-implementación de la
 * lógica del motor anterior; se cruzan ambos sobre features de muestra para
 * probar que la expresión evalúa EXACTAMENTE al mismo color/tamaño.
 */
import { describe, it, expect } from 'vitest'
import {
  extractClassification,
  fillColorExpression,
  lineColorExpression,
  circleRadiusExpression,
  textFieldExpression,
  dentroDeClase,
} from './maplibreSymbology'
import type { MaplibreValue } from './maplibreSymbology'
import { cuentasVisibles } from './leyenda'
import type { MapLayer } from '@/stores/mapStore'
import type { LayerSymbology, ClassBreak } from '@/types'

type Props = Record<string, unknown>

// ──────────────────────────────────────────────────────────────────────────
// Mini-evaluador del subconjunto de expresiones que produce el módulo.
// ──────────────────────────────────────────────────────────────────────────

function evalExpr(expr: MaplibreValue, props: Props): unknown {
  if (!Array.isArray(expr)) return expr
  const op = expr[0]
  switch (op) {
    case 'get':
      return props[expr[1] as string]
    case 'to-number': {
      const raw = evalExpr(expr[1], props)
      return Number(raw)
    }
    case 'to-string': {
      const raw = evalExpr(expr[1], props)
      return String(raw)
    }
    case 'all':
      return expr.slice(1).every((e) => Boolean(evalExpr(e, props)))
    case '>=':
      return (evalExpr(expr[1], props) as number) >= (evalExpr(expr[2], props) as number)
    case '<':
      return (evalExpr(expr[1], props) as number) < (evalExpr(expr[2], props) as number)
    case '<=':
      return (evalExpr(expr[1], props) as number) <= (evalExpr(expr[2], props) as number)
    case '==':
      return evalExpr(expr[1], props) === evalExpr(expr[2], props)
    case 'match': {
      const input = evalExpr(expr[1], props)
      // pares label/salida desde índice 2 hasta el penúltimo; último = fallback
      for (let i = 2; i < expr.length - 1; i += 2) {
        const label = expr[i] // literal (número o string)
        if (input === label) return evalExpr(expr[i + 1], props)
      }
      return evalExpr(expr[expr.length - 1], props)
    }
    case 'case': {
      for (let i = 1; i < expr.length - 1; i += 2) {
        if (evalExpr(expr[i], props)) return evalExpr(expr[i + 1], props)
      }
      return evalExpr(expr[expr.length - 1], props)
    }
    default:
      throw new Error(`evalExpr: operador no soportado ${String(op)}`)
  }
}

// ──────────────────────────────────────────────────────────────────────────
// Re-implementación de la lógica del motor anterior (el mapa anterior).
// ──────────────────────────────────────────────────────────────────────────

function prevColor(props: Props, symb: LayerSymbology): string | null {
  const field = symb.classification_field
  const breaks = symb.class_breaks
  if (!field || !breaks || breaks.length === 0) return null
  const raw = props[field]
  if (raw === undefined || raw === null) return null

  if (symb.symbology_type === 'unique_values') {
    const key = String(raw)
    const hit = breaks.find((b) => b.label === key)
    return hit ? hit.color : null
  }
  if (
    symb.symbology_type === 'graduated_colors' ||
    symb.symbology_type === 'graduated_symbols'
  ) {
    const v = typeof raw === 'number' ? raw : Number(raw)
    if (Number.isNaN(v)) return null
    for (let i = 0; i < breaks.length; i++) {
      const b = breaks[i]
      const lo = b.min_value
      const hi = b.max_value
      if (lo == null || hi == null) continue
      const isLast = i === breaks.length - 1
      if (isLast ? v >= lo && v <= hi : v >= lo && v < hi) return b.color
    }
  }
  return null
}

function prevSize(props: Props, symb: LayerSymbology): number | null {
  if (symb.symbology_type !== 'graduated_symbols') return null
  const field = symb.classification_field
  const breaks = symb.class_breaks
  if (!field || !breaks || breaks.length === 0) return null
  const raw = props[field]
  const v = typeof raw === 'number' ? raw : Number(raw)
  if (Number.isNaN(v)) return null
  const minSize = symb.symbol_size_min ?? 4
  const maxSize = symb.symbol_size_max ?? 32
  for (let i = 0; i < breaks.length; i++) {
    const b = breaks[i]
    const lo = b.min_value
    const hi = b.max_value
    if (lo == null || hi == null) continue
    const isLast = i === breaks.length - 1
    if (isLast ? v >= lo && v <= hi : v >= lo && v < hi) {
      const t = breaks.length > 1 ? i / (breaks.length - 1) : 0.5
      return minSize + t * (maxSize - minSize)
    }
  }
  return null
}

// ──────────────────────────────────────────────────────────────────────────
// Fixtures
// ──────────────────────────────────────────────────────────────────────────

const BASE = '#3388ff'

function graduatedBreaks(): ClassBreak[] {
  return [
    { min_value: 0, max_value: 10, label: '0-10', color: '#ff0000' },
    { min_value: 10, max_value: 20, label: '10-20', color: '#00ff00' },
    { min_value: 20, max_value: 30, label: '20-30', color: '#0000ff' },
  ]
}

// ──────────────────────────────────────────────────────────────────────────
// extractClassification
// ──────────────────────────────────────────────────────────────────────────

describe('extractClassification', () => {
  it('devuelve field + breaks cuando existen', () => {
    const symb: LayerSymbology = {
      symbology_type: 'unique_values',
      classification_field: 'tipo',
      class_breaks: [{ label: 'a', color: '#111' }],
    }
    expect(extractClassification(symb)).toEqual({
      field: 'tipo',
      breaks: [{ label: 'a', color: '#111' }],
    })
  })

  it('null cuando falta symbology', () => {
    expect(extractClassification(undefined)).toBeNull()
  })

  it('null cuando falta classification_field', () => {
    expect(
      extractClassification({ symbology_type: 'unique_values', class_breaks: [{ label: 'a', color: '#111' }] }),
    ).toBeNull()
  })

  it('null cuando no hay breaks', () => {
    expect(
      extractClassification({ symbology_type: 'unique_values', classification_field: 'x', class_breaks: [] }),
    ).toBeNull()
  })
})

// ──────────────────────────────────────────────────────────────────────────
// fillColorExpression — casos base
// ──────────────────────────────────────────────────────────────────────────

describe('fillColorExpression — base', () => {
  it('symbology undefined → baseColor', () => {
    expect(fillColorExpression(undefined, BASE)).toBe(BASE)
  })

  it('single_symbol → baseColor', () => {
    const symb: LayerSymbology = {
      symbology_type: 'single_symbol',
      classification_field: 'x',
      class_breaks: [{ label: 'a', color: '#111' }],
    }
    expect(fillColorExpression(symb, BASE)).toBe(BASE)
  })

  it('heatmap/cluster (no clasificación por feature) → baseColor', () => {
    const symb: LayerSymbology = {
      symbology_type: 'heatmap',
      classification_field: 'x',
      class_breaks: [{ label: 'a', color: '#111' }],
    }
    expect(fillColorExpression(symb, BASE)).toBe(BASE)
  })

  it('sin classification_field → baseColor', () => {
    expect(fillColorExpression({ symbology_type: 'unique_values', class_breaks: [{ label: 'a', color: '#1' }] }, BASE)).toBe(BASE)
  })
})

// ──────────────────────────────────────────────────────────────────────────
// unique_values — labels string
// ──────────────────────────────────────────────────────────────────────────

describe('fillColorExpression — unique_values (labels string)', () => {
  const symb: LayerSymbology = {
    symbology_type: 'unique_values',
    classification_field: 'categoria',
    class_breaks: [
      { label: 'bosque', color: '#0a0' },
      { label: 'agua', color: '#00a' },
      { label: 'urbano', color: '#a00' },
    ],
  }

  it('produce un match sobre to-string(get campo)', () => {
    const expr = fillColorExpression(symb, BASE)
    expect(expr).toEqual([
      'match',
      ['to-string', ['get', 'categoria']],
      'bosque', '#0a0',
      'agua', '#00a',
      'urbano', '#a00',
      BASE,
    ])
  })

  it('paridad: evaluar la expresión == motor anterior (con base como fallback)', () => {
    const expr = fillColorExpression(symb, BASE)
    for (const val of ['bosque', 'agua', 'urbano', 'paramo', 42]) {
      const props = { categoria: val }
      const expected = prevColor(props, symb) ?? BASE
      expect(evalExpr(expr as MaplibreValue, props)).toBe(expected)
    }
  })

  it('dedupe: labels repetidos conservan el primer color', () => {
    const dup: LayerSymbology = {
      symbology_type: 'unique_values',
      classification_field: 'c',
      class_breaks: [
        { label: 'x', color: '#111' },
        { label: 'x', color: '#222' },
        { label: 'y', color: '#333' },
      ],
    }
    expect(fillColorExpression(dup, BASE)).toEqual([
      'match',
      ['to-string', ['get', 'c']],
      'x', '#111',
      'y', '#333',
      BASE,
    ])
  })

  it('R4.5: la clase "Otros" NO entra al match — su color es el fallback', () => {
    const conOtros: LayerSymbology = {
      symbology_type: 'unique_values',
      classification_field: 'c',
      class_breaks: [
        { label: 'x', color: '#111' },
        { label: 'y', color: '#333' },
        { label: 'Otros', color: '#999999' },
      ],
    }
    const expr = fillColorExpression(conOtros, BASE)
    expect(expr).toEqual([
      'match',
      ['to-string', ['get', 'c']],
      'x', '#111',
      'y', '#333',
      '#999999', // gris de "Otros" como fallback, no BASE
    ])
    // Una categoría fuera del top-N pinta gris "Otros" — coherente con la leyenda.
    expect(evalExpr(expr as MaplibreValue, { c: 'categoria-rara' })).toBe('#999999')
  })
})

// ──────────────────────────────────────────────────────────────────────────
// unique_values — FLOAT categórico (fix del bug del motor anterior)
// ──────────────────────────────────────────────────────────────────────────

describe('fillColorExpression — unique_values float categórico (bug corregido)', () => {
  const symb: LayerSymbology = {
    symbology_type: 'unique_values',
    classification_field: 'estrato',
    class_breaks: [
      { label: '5.0', color: '#500' },
      { label: '6.0', color: '#600' },
    ],
  }

  it('todos los labels numéricos → match numérico sobre to-number', () => {
    expect(fillColorExpression(symb, BASE)).toEqual([
      'match',
      ['to-number', ['get', 'estrato']],
      5, '#500',
      6, '#600',
      BASE,
    ])
  })

  it('casa con valor numérico 5.0 (el motor anterior FALLABA: String(5.0)="5" ≠ "5.0")', () => {
    const expr = fillColorExpression(symb, BASE)
    // Fix: casa aunque el feature traiga number 5 o 5.0
    expect(evalExpr(expr as MaplibreValue, { estrato: 5 })).toBe('#500')
    expect(evalExpr(expr as MaplibreValue, { estrato: 5.0 })).toBe('#500')
    expect(evalExpr(expr as MaplibreValue, { estrato: 6 })).toBe('#600')
    // Y también casa con string "5" o "5.0"
    expect(evalExpr(expr as MaplibreValue, { estrato: '5' })).toBe('#500')
    expect(evalExpr(expr as MaplibreValue, { estrato: '5.0' })).toBe('#500')
    // Demostración de que el motor anterior fallaba con el número:
    expect(prevColor({ estrato: 5.0 }, symb)).toBeNull()
  })

  it('dedupe numérico: "5" y "5.0" colapsan a la misma clave', () => {
    const dup: LayerSymbology = {
      symbology_type: 'unique_values',
      classification_field: 'e',
      class_breaks: [
        { label: '5', color: '#aaa' },
        { label: '5.0', color: '#bbb' },
        { label: '7', color: '#ccc' },
      ],
    }
    expect(fillColorExpression(dup, BASE)).toEqual([
      'match',
      ['to-number', ['get', 'e']],
      5, '#aaa',
      7, '#ccc',
      BASE,
    ])
  })
})

// ──────────────────────────────────────────────────────────────────────────
// graduated_colors
// ──────────────────────────────────────────────────────────────────────────

describe('fillColorExpression — graduated_colors', () => {
  const symb: LayerSymbology = {
    symbology_type: 'graduated_colors',
    classification_field: 'poblacion',
    class_breaks: graduatedBreaks(),
  }

  it('produce un case de intervalos semiabiertos, último inclusive, fallback base', () => {
    const v = ['to-number', ['get', 'poblacion']]
    expect(fillColorExpression(symb, BASE)).toEqual([
      'case',
      ['all', ['>=', v, 0], ['<', v, 10]], '#ff0000',
      ['all', ['>=', v, 10], ['<', v, 20]], '#00ff00',
      ['all', ['>=', v, 20], ['<=', v, 30]], '#0000ff',
      BASE,
    ])
  })

  it('paridad exhaustiva contra el motor anterior', () => {
    const expr = fillColorExpression(symb, BASE)
    // fronteras, interior, fuera de rango
    for (const val of [-5, 0, 5, 9.999, 10, 15, 19.999, 20, 25, 30, 30.0001, 100]) {
      const props = { poblacion: val }
      const expected = prevColor(props, symb) ?? BASE
      expect(evalExpr(expr as MaplibreValue, props)).toBe(expected)
    }
  })

  it('salta breaks con min/max null', () => {
    const withNull: LayerSymbology = {
      symbology_type: 'graduated_colors',
      classification_field: 'p',
      class_breaks: [
        { min_value: null, max_value: null, label: 'sin datos', color: '#999' },
        { min_value: 0, max_value: 5, label: '0-5', color: '#111' },
        { min_value: 5, max_value: 10, label: '5-10', color: '#222' },
      ],
    }
    const v = ['to-number', ['get', 'p']]
    expect(fillColorExpression(withNull, BASE)).toEqual([
      'case',
      ['all', ['>=', v, 0], ['<', v, 5]], '#111',
      ['all', ['>=', v, 5], ['<=', v, 10]], '#222',
      BASE,
    ])
  })

  it('sin ningún break con bounds válidos → baseColor', () => {
    const noBounds: LayerSymbology = {
      symbology_type: 'graduated_colors',
      classification_field: 'p',
      class_breaks: [{ min_value: null, max_value: null, label: 'x', color: '#111' }],
    }
    expect(fillColorExpression(noBounds, BASE)).toBe(BASE)
  })
})

// ──────────────────────────────────────────────────────────────────────────
// lineColorExpression
// ──────────────────────────────────────────────────────────────────────────

describe('lineColorExpression', () => {
  it('replica la misma semántica que fillColorExpression (unique_values)', () => {
    const symb: LayerSymbology = {
      symbology_type: 'unique_values',
      classification_field: 'via',
      class_breaks: [{ label: 'primaria', color: '#f00' }],
    }
    expect(lineColorExpression(symb, BASE)).toEqual(fillColorExpression(symb, BASE))
  })

  it('single_symbol → baseColor', () => {
    expect(lineColorExpression({ symbology_type: 'single_symbol' }, BASE)).toBe(BASE)
  })
})

// ──────────────────────────────────────────────────────────────────────────
// circleRadiusExpression
// ──────────────────────────────────────────────────────────────────────────

describe('circleRadiusExpression', () => {
  it('no graduated_symbols → baseSize', () => {
    expect(circleRadiusExpression({ symbology_type: 'graduated_colors', classification_field: 'p', class_breaks: graduatedBreaks() }, 7)).toBe(7)
    expect(circleRadiusExpression(undefined, 7)).toBe(7)
    expect(circleRadiusExpression({ symbology_type: 'single_symbol' }, 7)).toBe(7)
  })

  it('graduated_symbols → case con tamaños discretos por índice (defaults 4..32)', () => {
    const symb: LayerSymbology = {
      symbology_type: 'graduated_symbols',
      classification_field: 'mag',
      class_breaks: graduatedBreaks(), // 3 breaks → t = 0, 0.5, 1 → 4, 18, 32
    }
    const v = ['to-number', ['get', 'mag']]
    expect(circleRadiusExpression(symb, 6)).toEqual([
      'case',
      ['all', ['>=', v, 0], ['<', v, 10]], 4,
      ['all', ['>=', v, 10], ['<', v, 20]], 18,
      ['all', ['>=', v, 20], ['<=', v, 30]], 32,
      6,
    ])
  })

  it('respeta symbol_size_min / symbol_size_max', () => {
    const symb: LayerSymbology = {
      symbology_type: 'graduated_symbols',
      classification_field: 'mag',
      class_breaks: graduatedBreaks(),
      symbol_size_min: 10,
      symbol_size_max: 30, // t=0,0.5,1 → 10, 20, 30
    }
    const v = ['to-number', ['get', 'mag']]
    expect(circleRadiusExpression(symb, 6)).toEqual([
      'case',
      ['all', ['>=', v, 0], ['<', v, 10]], 10,
      ['all', ['>=', v, 10], ['<', v, 20]], 20,
      ['all', ['>=', v, 20], ['<=', v, 30]], 30,
      6,
    ])
  })

  it('paridad exhaustiva de tamaño contra el motor anterior', () => {
    const symb: LayerSymbology = {
      symbology_type: 'graduated_symbols',
      classification_field: 'mag',
      class_breaks: graduatedBreaks(),
      symbol_size_min: 5,
      symbol_size_max: 25,
    }
    const expr = circleRadiusExpression(symb, 6)
    for (const val of [-1, 0, 9.999, 10, 20, 30, 31]) {
      const props = { mag: val }
      const expected = prevSize(props, symb) ?? 6
      expect(evalExpr(expr as MaplibreValue, props)).toBe(expected)
    }
  })

  it('un solo break → t = 0.5 (mismo default del motor anterior)', () => {
    const symb: LayerSymbology = {
      symbology_type: 'graduated_symbols',
      classification_field: 'mag',
      class_breaks: [{ min_value: 0, max_value: 100, label: 'todo', color: '#111' }],
      symbol_size_min: 4,
      symbol_size_max: 32,
    }
    const v = ['to-number', ['get', 'mag']]
    // t=0.5 → 4 + 0.5*28 = 18
    expect(circleRadiusExpression(symb, 6)).toEqual([
      'case',
      ['all', ['>=', v, 0], ['<=', v, 100]], 18,
      6,
    ])
  })
})

// ──────────────────────────────────────────────────────────────────────────
// textFieldExpression
// ──────────────────────────────────────────────────────────────────────────

describe('textFieldExpression', () => {
  it('devuelve to-string(get campo) cuando hay clasificación', () => {
    const symb: LayerSymbology = {
      symbology_type: 'unique_values',
      classification_field: 'nombre',
      class_breaks: [{ label: 'a', color: '#111' }],
    }
    expect(textFieldExpression(symb)).toEqual(['to-string', ['get', 'nombre']])
  })

  it('null sin clasificación', () => {
    expect(textFieldExpression(undefined)).toBeNull()
    expect(textFieldExpression({ symbology_type: 'single_symbol' })).toBeNull()
  })
})

// ──────────────────────────────────────────────────────────────────────────
// F7 (auditoría): un rango degenerado (min == max) es IGUALDAD, igual en mapa y leyenda
// ──────────────────────────────────────────────────────────────────────────

describe('rango degenerado = igualdad (mapa, leyenda y conteo del agente)', () => {
  // «rojo los lotes con 0 incidentes y verde los de 5 a 10»: el hueco 1-4 queda en gris
  const breaks: ClassBreak[] = [
    { min_value: 0, max_value: 0, label: '0 incidentes', color: '#e41a1c' },
    { min_value: 5, max_value: 10, label: '5 a 10', color: '#4daf4a' },
  ]
  const symb: LayerSymbology = { symbology_type: 'graduated_colors', classification_field: 'inc', class_breaks: breaks }

  it('[0, 0] pinta solo el 0; los valores del hueco quedan con el color base', () => {
    const v = ['to-number', ['get', 'inc']]
    const expr = fillColorExpression(symb, BASE)
    expect(expr).toEqual(['case', ['==', v, 0], '#e41a1c', ['all', ['>=', v, 5], ['<=', v, 10]], '#4daf4a', BASE])
    const pintado = [0, 2, 3, 7, 10].map((inc) => evalExpr(expr as MaplibreValue, { inc }))
    expect(pintado).toEqual(['#e41a1c', BASE, BASE, '#4daf4a', '#4daf4a'])
  })

  it('la leyenda cuenta con la misma regla que pinta el mapa (y que el agente: [1, 2])', () => {
    const feats = [0, 2, 3, 7, 10].map((inc, i) => ({ type: 'Feature', properties: { inc, k: i },
      geometry: { type: 'Point', coordinates: [i, 0] } }))
    const capa = { id: 'l', name: 'Lotes', kind: 'vector-geojson', visible: true, color: BASE, featureCount: 5,
                   addedAt: new Date(0), data: { type: 'FeatureCollection', features: feats }, symbology: symb,
                   filtro: [{ field: 'k', op: '>', value: -1 }] } as unknown as MapLayer
    expect(cuentasVisibles(capa)).toEqual([1, 2])
  })

  it('una clase manual vacía (sin min) no pinta ni cuenta nada, ni por su etiqueta', () => {
    const vacia: ClassBreak[] = [
      { min_value: null, max_value: 5, label: '5', color: '#377eb8' },
      { min_value: 5, max_value: 10, label: '5 a 10', color: '#4daf4a' },
    ]
    const s: LayerSymbology = { ...symb, class_breaks: vacia }
    const feats = [5, 7].map((inc, i) => ({ type: 'Feature', properties: { inc: String(inc), k: i },
      geometry: { type: 'Point', coordinates: [i, 0] } }))
    const capa = { id: 'l', name: 'Lotes', kind: 'vector-geojson', visible: true, color: BASE, featureCount: 2,
                   addedAt: new Date(0), data: { type: 'FeatureCollection', features: feats }, symbology: s,
                   filtro: [{ field: 'k', op: '>', value: -1 }] } as unknown as MapLayer
    expect(cuentasVisibles(capa)).toEqual([0, 2])
    expect(evalExpr(fillColorExpression(s, BASE) as MaplibreValue, { inc: 5 })).toBe('#4daf4a')
  })

  it('dentroDeClase: igualdad si lo === hi; [lo, hi) salvo la última', () => {
    expect(dentroDeClase(0, 0, 0, false)).toBe(true)
    expect(dentroDeClase(2, 0, 0, false)).toBe(false)
    expect(dentroDeClase(10, 5, 10, false)).toBe(false)
    expect(dentroDeClase(10, 5, 10, true)).toBe(true)
  })
})
