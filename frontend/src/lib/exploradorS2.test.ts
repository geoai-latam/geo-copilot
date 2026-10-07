import { describe, expect, it } from 'vitest'

import type { McpRunResult } from '@/services/api'
import {
  ajustable, escenasDe, fechaCorta, productoS2, rangoPorDefecto, simbologiaCuadricula, teselasDe, teselasDelMundo,
  ventanaPorDefecto, vistaDeBbox,
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

  it('vistaDeBbox encuadra con un zoom razonable', () => {
    const tesela = vistaDeBbox([-74.5, 4.5, -73.5, 5.4])
    expect(tesela.centro).toEqual([-74, 4.95])
    expect(tesela.zoom).toBeGreaterThan(7)
    expect(tesela.zoom).toBeLessThan(10)
    expect(vistaDeBbox([-74.2, 4.5, -73.8, 4.9]).zoom).toBeGreaterThan(tesela.zoom)
  })

  it('teselasDelMundo lee las más despejadas de los hechos', () => {
    const res = { success: true, message: null, results: {},
      facts: { mas_despejadas: [{ tile: '22XEP', nubes_min: 0, escenas: 727 }] } } as never
    expect(teselasDelMundo(res)).toEqual([{ tile: '22XEP', nubes_min: 0, escenas: 727, nubes_mediana: NaN, cobertura_max: NaN, bbox: null }])
  })

  it('productos: rango por defecto y bandas de cada uno', () => {
    expect(rangoPorDefecto(productoS2('ndwi'))).toEqual([-1, 1])
    expect(rangoPorDefecto(productoS2('cloud'))).toEqual([0, 100])
    expect(rangoPorDefecto(productoS2('false_color'))).toEqual([0, 0.4])
    expect(productoS2('agriculture').bandas).toEqual(['swir16', 'nir', 'blue'])
    expect(ajustable(productoS2('scl'))).toBe(false)
    expect(productoS2('inventado').id).toBe('true_color')
  })

  it('ventanaPorDefecto y fechaCorta', () => {
    expect(ventanaPorDefecto(new Date('2026-10-06T12:00:00Z'))).toEqual({ desde: '2026-07-08', hasta: '2026-10-06' })
    expect(fechaCorta('2026-08-10T15:31:43Z')).toMatch(/10.*ago.*2026/)
    expect(fechaCorta('no-es-fecha')).toBe('no-es-fecha')
  })
})
