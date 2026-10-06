import { describe, it, expect } from 'vitest'
import {
  vectorSourceLayerName,
  vectorTileSourceSpec,
  vectorTileLayerSpecs,
} from './maplibreVectorTiles'

describe('vectorTileSourceSpec', () => {
  it('apunta al endpoint MVT con el template {z}/{x}/{y}', () => {
    const src = vectorTileSourceSpec('catastro', 'lotes')
    expect(src.type).toBe('vector')
    expect(src.tiles[0]).toBe('/api/v1/tiles/catastro/lotes/{z}/{x}/{y}.pbf')
  })
  it('respeta un tileBase custom', () => {
    const src = vectorTileSourceSpec('s', 't', 'https://x/tiles')
    expect(src.tiles[0]).toBe('https://x/tiles/s/t/{z}/{x}/{y}.pbf')
  })
})

describe('vectorSourceLayerName', () => {
  it('es schema.table (debe coincidir con ST_AsMVT del backend)', () => {
    expect(vectorSourceLayerName('catastro', 'lotes')).toBe('catastro.lotes')
  })
})

describe('vectorTileLayerSpecs', () => {
  it('polygon → fill + line, ambos con source-layer schema.table', () => {
    const specs = vectorTileLayerSpecs('vt-1', 'catastro', 'lotes', 'polygon', '#f00')
    expect(specs.map((s) => s.type)).toEqual(['fill', 'line'])
    expect(specs.every((s) => s['source-layer'] === 'catastro.lotes')).toBe(true)
    expect(specs.every((s) => s.source === 'vt-1')).toBe(true)
  })
  it('line → una sola capa line', () => {
    const specs = vectorTileLayerSpecs('vt-1', 's', 't', 'line', '#0f0')
    expect(specs).toHaveLength(1)
    expect(specs[0].type).toBe('line')
  })
  it('point → una sola capa circle', () => {
    const specs = vectorTileLayerSpecs('vt-1', 's', 't', 'point', '#00f')
    expect(specs).toHaveLength(1)
    expect(specs[0].type).toBe('circle')
    expect(specs[0].paint['circle-color']).toBe('#00f')
  })
  it('los specs son serializables a JSON', () => {
    const specs = vectorTileLayerSpecs('vt-1', 's', 't', 'polygon', '#abc')
    expect(JSON.parse(JSON.stringify(specs))).toEqual(specs)
  })
})
