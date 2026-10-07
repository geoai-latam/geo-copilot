import { describe, expect, it } from 'vitest'

import type { McpRunResult } from '@/services/api'
import {
  cajaParaVer, escenasDe, fechaCorta, simbologiaCuadricula, teselasDe, ventanaPorDefecto, vistaDeBbox,
} from './exploradorS2'

const poligono = (w: number, s: number, e: number, n: number) => ({
  type: 'Polygon', coordinates: [[[w, s], [e, s], [e, n], [w, n], [w, s]]],
})

function resultado(results: McpRunResult['results']): McpRunResult {
  return { success: true, message: null, facts: {}, results }
}

describe('exploradorS2', () => {
  it('teselasDe ordena de la más despejada a la más nublada y saca el bbox de la huella', () => {
    const res = resultado({
      geojson: {
        type: 'FeatureCollection',
        features: [
          { type: 'Feature', geometry: poligono(-74.5, 3.6, -73.5, 4.5), properties: { tile: '18NWK', escenas: 25, nubes_min: 5.44, nubes_mediana: 80, cobertura_max: 100, mejor_escena: 'K', mejor_fecha: '2026-08-10' } },
          { type: 'Feature', geometry: poligono(-74.5, 4.5, -73.5, 5.4), properties: { tile: '18NWL', escenas: 25, nubes_min: 1.31, nubes_mediana: 69, cobertura_max: 100, mejor_escena: 'L', mejor_fecha: '2026-08-10' } },
        ],
      } as never,
    })
    const ts = teselasDe(res)
    expect(ts.map((t) => t.tile)).toEqual(['18NWL', '18NWK'])
    expect(ts[0].bbox).toEqual([-74.5, 4.5, -73.5, 5.4])
  })

  it('escenasDe lee la tabla de data.results', () => {
    const fila = { id: 'S2B_T18NWL_20260810T152745_L2A', tile: '18NWL', fecha: '2026-08-10T15:31:43Z', nubes: 1.31, cobertura: 100, plataforma: 'sentinel-2b', miniatura: 'https://x/L2A_PVI.jpg' }
    expect(escenasDe(resultado({ data: { results: [fila] } }))).toEqual([fila])
    expect(escenasDe(resultado({}))).toEqual([])
  })

  it('simbologiaCuadricula: cortes fijos y verde = despejado; para escenas más es mejor', () => {
    const nubes = simbologiaCuadricula('nubes_min')
    expect(nubes.classification_field).toBe('nubes_min')
    expect(nubes.class_breaks?.[0]).toMatchObject({ min_value: 0, max_value: 5, color: '#1a9850' })
    const escenas = simbologiaCuadricula('escenas')
    expect(escenas.class_breaks?.[0].color).toBe('#d73027')        // pocas escenas = rojo
    const cortes = escenas.class_breaks ?? []
    expect(cortes[cortes.length - 1].label).toBe('40+')
  })

  it('cajaParaVer: centrada en la vista si cae en la tesela, si no en la tesela; recortada a ella', () => {
    const tesela: [number, number, number, number] = [-74.5, 4.5, -73.5, 5.4]
    const enVista = cajaParaVer(tesela, [-74.2, 4.6, -74.0, 4.7])
    expect(enVista.coordinates[0][0]).toEqual([-74.3, 4.5])   // 4,45 se recorta al sur de la tesela
    const fuera = cajaParaVer(tesela, [-60, 0, -59, 1])
    const xs = fuera.coordinates[0].map((c) => c[0])
    expect(Math.min(...xs)).toBeCloseTo(-74.2)
    expect(Math.max(...xs)).toBeCloseTo(-73.8)
    const borde = cajaParaVer(tesela, [-74.5, 5.3, -74.45, 5.4])   // esquina: recorta a la tesela
    const ys = borde.coordinates[0].map((c) => c[1])
    expect(Math.max(...ys)).toBeLessThanOrEqual(5.4)
    expect(Math.min(...borde.coordinates[0].map((c) => c[0]))).toBeGreaterThanOrEqual(-74.5)
  })

  it('vistaDeBbox encuadra con un zoom razonable', () => {
    const tesela = vistaDeBbox([-74.5, 4.5, -73.5, 5.4])
    expect(tesela.centro).toEqual([-74, 4.95])
    expect(tesela.zoom).toBeGreaterThan(7)
    expect(tesela.zoom).toBeLessThan(10)
    expect(vistaDeBbox([-74.2, 4.5, -73.8, 4.9]).zoom).toBeGreaterThan(tesela.zoom)
  })

  it('ventanaPorDefecto y fechaCorta', () => {
    expect(ventanaPorDefecto(new Date('2026-10-06T12:00:00Z'))).toEqual({ desde: '2026-07-08', hasta: '2026-10-06' })
    expect(fechaCorta('2026-08-10T15:31:43Z')).toMatch(/10.*ago.*2026/)
    expect(fechaCorta('no-es-fecha')).toBe('no-es-fecha')
  })
})
