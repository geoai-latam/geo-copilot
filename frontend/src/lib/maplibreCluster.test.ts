/**
 * MAP-CLUSTER-HEATMAP — clustering y heatmap nativos de MapLibre.
 *
 * Verifica PARIDAD con el motor anterior (la simbología anterior):
 *   - paletteFrom == paletteFromSymbology (class_breaks → colores, else viridis)
 *   - sampleRamp idéntico (interpolación en la rampa)
 *   - cluster: source con cluster:true, radius=60 (pixelRange), minPoints=3
 *   - cluster layers: step por point_count, colores DE LA PALETA
 *   - heatmap: rampa respeta la paleta dada (NO viridis hardcodeado)
 */
import { describe, it, expect } from 'vitest'
import {
  DEFAULT_VIRIDIS,
  paletteFrom,
  sampleRamp,
  clusterSourceSpec,
  clusterLayerSpecs,
  clusterBadgeSize,
  clusterColorT,
  heatmapLayerSpec,
  heatmapColorExpression,
  toCentroidPoints,
} from './maplibreCluster'
import type { LayerSymbology } from '@/types'

// ──────────────────────────────────────────────────────────────────────────
// paletteFrom — réplica de paletteFromSymbology
// ──────────────────────────────────────────────────────────────────────────

describe('toCentroidPoints (degradación cluster/heatmap sobre no-puntos)', () => {
  const fc = (features: unknown[]) => ({ type: 'FeatureCollection', features } as never)

  it('convierte polígonos a puntos centroide', () => {
    const poly = {
      type: 'Feature',
      geometry: { type: 'Polygon', coordinates: [[[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]] },
      properties: { name: 'A' },
    }
    const out = toCentroidPoints(fc([poly]))
    expect(out.features).toHaveLength(1)
    const g = out.features[0].geometry as { type: string; coordinates: number[] }
    expect(g.type).toBe('Point')
    // promedio de las 5 posiciones del anillo → dentro del cuadrado
    expect(g.coordinates[0]).toBeGreaterThan(0)
    expect(g.coordinates[0]).toBeLessThan(2)
    expect(out.features[0].properties).toEqual({ name: 'A' })
  })

  it('conserva los puntos tal cual', () => {
    const pt = { type: 'Feature', geometry: { type: 'Point', coordinates: [5, 6] }, properties: {} }
    const out = toCentroidPoints(fc([pt]))
    expect(out.features[0]).toBe(pt)
  })

  it('descarta features sin coordenadas válidas', () => {
    const bad = { type: 'Feature', geometry: { type: 'Polygon', coordinates: [] }, properties: {} }
    const out = toCentroidPoints(fc([bad]))
    expect(out.features).toHaveLength(0)
  })

  it('promedia una línea a su punto medio de vértices', () => {
    const line = {
      type: 'Feature',
      geometry: { type: 'LineString', coordinates: [[0, 0], [10, 0]] },
      properties: {},
    }
    const g = toCentroidPoints(fc([line])).features[0].geometry as { coordinates: number[] }
    expect(g.coordinates).toEqual([5, 0])
  })
})

describe('paletteFrom', () => {
  it('extrae los colores de class_breaks en orden', () => {
    const symb: LayerSymbology = {
      symbology_type: 'graduated_colors',
      class_breaks: [
        { label: 'a', color: '#111111' },
        { label: 'b', color: '#222222' },
        { label: 'c', color: '#333333' },
      ],
    }
    expect(paletteFrom(symb)).toEqual(['#111111', '#222222', '#333333'])
  })

  it('sin class_breaks devuelve viridis por defecto', () => {
    expect(paletteFrom({ symbology_type: 'heatmap' })).toEqual([...DEFAULT_VIRIDIS])
  })

  it('class_breaks vacío devuelve viridis', () => {
    expect(paletteFrom({ class_breaks: [] })).toEqual([...DEFAULT_VIRIDIS])
  })

  it('symbology undefined devuelve viridis', () => {
    expect(paletteFrom(undefined)).toEqual([...DEFAULT_VIRIDIS])
  })

  it('la copia de viridis no es la referencia interna (mutable safe)', () => {
    const a = paletteFrom(undefined)
    a.push('#000000')
    expect(paletteFrom(undefined)).toEqual([...DEFAULT_VIRIDIS])
  })
})

// ──────────────────────────────────────────────────────────────────────────
// sampleRamp — paridad exacta con la simbología anterior
// ──────────────────────────────────────────────────────────────────────────

describe('sampleRamp', () => {
  it('t=0 devuelve el primer color', () => {
    expect(sampleRamp(['#000000', '#ffffff'], 0)).toBe('#000000')
  })

  it('t=1 devuelve el último color', () => {
    expect(sampleRamp(['#000000', '#ffffff'], 1)).toBe('#ffffff')
  })

  it('t=0.5 interpola al punto medio', () => {
    expect(sampleRamp(['#000000', '#ffffff'], 0.5)).toBe('#808080')
  })

  it('clampa t<0 y t>1', () => {
    expect(sampleRamp(['#000000', '#ffffff'], -1)).toBe('#000000')
    expect(sampleRamp(['#000000', '#ffffff'], 2)).toBe('#ffffff')
  })

  it('paleta de un solo color devuelve ese color', () => {
    expect(sampleRamp(['#abcdef'], 0.7)).toBe('#abcdef')
  })

  it('paleta vacía devuelve gris fallback', () => {
    expect(sampleRamp([], 0.5)).toBe('#888')
  })

  it('interpola dentro del segmento correcto con 3 colores', () => {
    // t=0.25 → segmento [0,1], frac 0.5 entre #000000 y #808080
    expect(sampleRamp(['#000000', '#808080', '#ffffff'], 0.25)).toBe('#404040')
  })
})

// ──────────────────────────────────────────────────────────────────────────
// clusterSourceSpec — cluster nativo con paridad de radius/minPoints
// ──────────────────────────────────────────────────────────────────────────

describe('clusterSourceSpec', () => {
  const geojson = { type: 'FeatureCollection', features: [] }

  it('produce source geojson con cluster:true', () => {
    const s = clusterSourceSpec(geojson)
    expect(s.type).toBe('geojson')
    expect(s.cluster).toBe(true)
    expect(s.data).toBe(geojson)
  })

  it('default clusterRadius=60 (== pixelRange del motor anterior)', () => {
    expect(clusterSourceSpec(geojson).clusterRadius).toBe(60)
  })

  it('default clusterMinPoints=3 (== minimumClusterSize del motor anterior)', () => {
    expect(clusterSourceSpec(geojson).clusterMinPoints).toBe(3)
  })

  it('default clusterMaxZoom=14', () => {
    expect(clusterSourceSpec(geojson).clusterMaxZoom).toBe(14)
  })

  it('respeta overrides de radius/maxZoom/minPoints', () => {
    const s = clusterSourceSpec(geojson, { radius: 50, maxZoom: 12, minPoints: 2 })
    expect(s.clusterRadius).toBe(50)
    expect(s.clusterMaxZoom).toBe(12)
    expect(s.clusterMinPoints).toBe(2)
  })

  it('es JSON-serializable (spec plano, sin funciones/clases)', () => {
    const s = clusterSourceSpec(geojson)
    expect(JSON.parse(JSON.stringify(s))).toEqual(s)
  })
})

// ──────────────────────────────────────────────────────────────────────────
// Fórmulas de badge/color — paridad exacta con la simbología anterior:358/361
// ──────────────────────────────────────────────────────────────────────────

describe('clusterBadgeSize (paridad la simbología anterior:358)', () => {
  it('count bajo clampa al mínimo 28', () => {
    expect(clusterBadgeSize(2)).toBe(28) // 16 + 6 = 22 → max(28,22)=28
  })

  it('count alto clampa al máximo 64', () => {
    expect(clusterBadgeSize(1000)).toBe(64)
  })

  it('count medio sigue la fórmula 16 + log2(count)*6', () => {
    expect(clusterBadgeSize(100)).toBeCloseTo(16 + Math.log2(100) * 6, 6)
  })

  it('es monótona creciente en el rango medio', () => {
    expect(clusterBadgeSize(50)).toBeGreaterThan(clusterBadgeSize(10))
    expect(clusterBadgeSize(200)).toBeGreaterThan(clusterBadgeSize(50))
  })
})

describe('clusterColorT (paridad la simbología anterior:361)', () => {
  it('count=100 → t=1 (log10(100)/2)', () => {
    expect(clusterColorT(100)).toBeCloseTo(1, 6)
  })

  it('count=10 → t=0.5', () => {
    expect(clusterColorT(10)).toBeCloseTo(0.5, 6)
  })

  it('clampa a 1 para counts muy grandes', () => {
    expect(clusterColorT(100000)).toBe(1)
  })
})

// ──────────────────────────────────────────────────────────────────────────
// clusterLayerSpecs — circle (step) + symbol (label)
// ──────────────────────────────────────────────────────────────────────────

describe('clusterLayerSpecs', () => {
  const palette = ['#111111', '#888888', '#ffffff']
  const [circle, symbol] = clusterLayerSpecs('src', palette)

  it('devuelve un circle layer y un symbol layer ligados al source', () => {
    expect(circle.type).toBe('circle')
    expect(circle.source).toBe('src')
    expect(symbol.type).toBe('symbol')
    expect(symbol.source).toBe('src')
  })

  it('ambos layers filtran por point_count (sólo clusters)', () => {
    expect(circle.filter).toEqual(['has', 'point_count'])
    expect(symbol.filter).toEqual(['has', 'point_count'])
  })

  it('circle-radius es una expresión step sobre point_count', () => {
    const r = circle.paint['circle-radius'] as unknown[]
    expect(r[0]).toBe('step')
    expect(r[1]).toEqual(['get', 'point_count'])
    // step: base + pares (stop, valor) → longitud impar
    expect(r.length % 2).toBe(1)
  })

  it('circle-color es step y sus colores salen DE LA PALETA (no viridis)', () => {
    const c = circle.paint['circle-color'] as unknown[]
    expect(c[0]).toBe('step')
    expect(c[1]).toEqual(['get', 'point_count'])
    const colors = c.filter((v) => typeof v === 'string' && v.startsWith('#')) as string[]
    expect(colors.length).toBeGreaterThan(0)
    // Ninguno de los colores debe pertenecer EXCLUSIVAMENTE a viridis: todos
    // deben ser interpolaciones de la paleta gris dada (r==g==b).
    for (const col of colors) {
      const r = col.slice(1, 3)
      const g = col.slice(3, 5)
      const b = col.slice(5, 7)
      expect(r).toBe(g)
      expect(g).toBe(b)
    }
    // Y no aparece el arranque de viridis.
    expect(colors).not.toContain('#440154')
  })

  it('el color en el stop 100 coincide con sampleRamp(palette, t(100))', () => {
    // t(100)=1 → último color de la paleta
    const c = circle.paint['circle-color'] as unknown[]
    // último valor del step es el color para count>=750; el de count>=100 está antes.
    // Reconstruimos: base, 10, v10, 100, v100, 750, v750
    const idx100 = c.indexOf(100)
    const colorAt100 = c[idx100 + 1]
    expect(colorAt100).toBe(sampleRamp(palette, clusterColorT(100)))
  })

  it('symbol usa text-field point_count_abbreviated', () => {
    expect(symbol.layout['text-field']).toEqual(['get', 'point_count_abbreviated'])
  })

  it('symbol text-size es step por point_count', () => {
    const ts = symbol.layout['text-size'] as unknown[]
    expect(ts[0]).toBe('step')
    expect(ts[1]).toEqual(['get', 'point_count'])
  })

  it('paleta vacía cae a viridis para el color', () => {
    const [c2] = clusterLayerSpecs('s', [])
    const c = c2.paint['circle-color'] as unknown[]
    const colors = c.filter((v) => typeof v === 'string' && (v as string).startsWith('#'))
    // El color base (t(2)≈0.15) debe ser una interpolación del inicio de viridis.
    expect(colors.length).toBeGreaterThan(0)
  })

  it('los layers son JSON-serializables', () => {
    expect(JSON.parse(JSON.stringify(circle))).toEqual(circle)
    expect(JSON.parse(JSON.stringify(symbol))).toEqual(symbol)
  })
})

// ──────────────────────────────────────────────────────────────────────────
// heatmapColorExpression / heatmapLayerSpec — RESPETA LA PALETA (no viridis)
// ──────────────────────────────────────────────────────────────────────────

describe('heatmapColorExpression', () => {
  it('arranca transparente en densidad 0', () => {
    const e = heatmapColorExpression(['#ff0000', '#00ff00'])
    expect(e[0]).toBe('interpolate')
    expect(e[1]).toEqual(['linear'])
    expect(e[2]).toEqual(['heatmap-density'])
    expect(e[3]).toBe(0)
    expect(e[4]).toBe('rgba(0, 0, 0, 0)')
  })

  it('usa EXACTAMENTE los colores de la paleta dada (no hardcodea viridis)', () => {
    const palette = ['#ff0000', '#00ff00', '#0000ff']
    const e = heatmapColorExpression(palette)
    const colors = e.filter((v) => typeof v === 'string' && (v as string).startsWith('#'))
    expect(colors).toEqual(palette)
    // No hay rastro de viridis.
    for (const v of DEFAULT_VIRIDIS) {
      expect(colors).not.toContain(v)
    }
  })

  it('el último color se ancla en densidad 1', () => {
    const palette = ['#ff0000', '#00ff00', '#0000ff']
    const e = heatmapColorExpression(palette)
    expect(e[e.length - 2]).toBe(1)
    expect(e[e.length - 1]).toBe('#0000ff')
  })

  it('paleta vacía cae a viridis', () => {
    const e = heatmapColorExpression([])
    const colors = e.filter((v) => typeof v === 'string' && (v as string).startsWith('#'))
    expect(colors).toEqual([...DEFAULT_VIRIDIS])
  })

  it('paleta de un color lo ancla en densidad 1', () => {
    const e = heatmapColorExpression(['#123456'])
    expect(e).toContain('#123456')
    expect(e[e.length - 2]).toBe(1)
  })
})

describe('heatmapLayerSpec', () => {
  it('produce heatmap layer ligado al source', () => {
    const spec = heatmapLayerSpec('pts', { palette: ['#ff0000', '#0000ff'] })
    expect(spec.type).toBe('heatmap')
    expect(spec.source).toBe('pts')
    expect(spec.id).toContain('pts')
  })

  it('heatmap-color deriva de la paleta dada (bug viridis EVITADO)', () => {
    const palette = ['#aa0000', '#00bb00']
    const spec = heatmapLayerSpec('pts', { palette })
    const colorExpr = spec.paint['heatmap-color'] as unknown[]
    const colors = colorExpr.filter((v) => typeof v === 'string' && (v as string).startsWith('#'))
    expect(colors).toEqual(palette)
    expect(colors).not.toContain('#440154')
  })

  it('defaults razonables de radius/intensity/opacity', () => {
    const spec = heatmapLayerSpec('pts', { palette: ['#000000'] })
    expect(spec.paint['heatmap-radius']).toBe(30)
    expect(spec.paint['heatmap-intensity']).toBe(1)
    expect(spec.paint['heatmap-opacity']).toBe(0.8)
  })

  it('respeta overrides de radius/intensity/opacity', () => {
    const spec = heatmapLayerSpec('pts', {
      palette: ['#000000'],
      radius: 45,
      intensity: 2,
      opacity: 0.5,
    })
    expect(spec.paint['heatmap-radius']).toBe(45)
    expect(spec.paint['heatmap-intensity']).toBe(2)
    expect(spec.paint['heatmap-opacity']).toBe(0.5)
  })

  it('sin intensityField el peso es constante 1 (cuenta puntos)', () => {
    const spec = heatmapLayerSpec('pts', { palette: ['#000000'] })
    expect(spec.paint['heatmap-weight']).toBe(1)
  })

  it('con intensityField el peso es una interpolación sobre get(field)', () => {
    const spec = heatmapLayerSpec('pts', {
      palette: ['#000000'],
      intensityField: 'poblacion',
      weightMax: 500,
    })
    const w = spec.paint['heatmap-weight'] as unknown[]
    expect(w[0]).toBe('interpolate')
    // referencia al campo
    expect(JSON.stringify(w)).toContain('poblacion')
    // normaliza hasta weightMax → peso 1
    expect(w).toContain(500)
  })

  it('maxzoom sólo aparece si se pasa', () => {
    const withZoom = heatmapLayerSpec('pts', { palette: ['#000'], maxzoom: 15 })
    expect(withZoom.maxzoom).toBe(15)
    const without = heatmapLayerSpec('pts', { palette: ['#000'] })
    expect(without.maxzoom).toBeUndefined()
  })

  it('el spec es JSON-serializable', () => {
    const spec = heatmapLayerSpec('pts', { palette: ['#ff0000', '#0000ff'] })
    expect(JSON.parse(JSON.stringify(spec))).toEqual(spec)
  })
})
