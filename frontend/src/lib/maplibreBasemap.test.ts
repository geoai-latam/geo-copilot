/**
 * MAP-SETUP-BASEMAPS — normalización de basemaps a tiles raster de MapLibre.
 */
import { describe, it, expect } from 'vitest'
import { toTileTemplate, expandSubdomains, basemapRasterSpec } from './maplibreBasemap'
import { BASE_MAPS } from '@/stores/mapStore'
import type { BaseMapConfig } from '@/stores/mapStore'

describe('toTileTemplate', () => {
  it('base sin plantilla (OSM) → añade {z}/{x}/{y}.png', () => {
    expect(toTileTemplate('https://tile.openstreetmap.org/'))
      .toBe('https://tile.openstreetmap.org/{z}/{x}/{y}.png')
  })

  it('plantilla ArcGIS {z}/{y}/{x} → se conserva (MapLibre respeta el orden)', () => {
    const u = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'
    expect(toTileTemplate(u)).toBe(u)
  })

  it('plantilla Carto {z}/{x}/{y}.png → se conserva', () => {
    const u = 'https://basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png'
    expect(toTileTemplate(u)).toBe(u)
  })
})

describe('expandSubdomains', () => {
  it('expande {s} a a/b/c', () => {
    expect(expandSubdomains('https://tile-{s}.openstreetmap.fr/hot/{z}/{x}/{y}.png')).toEqual([
      'https://tile-a.openstreetmap.fr/hot/{z}/{x}/{y}.png',
      'https://tile-b.openstreetmap.fr/hot/{z}/{x}/{y}.png',
      'https://tile-c.openstreetmap.fr/hot/{z}/{x}/{y}.png',
    ])
  })

  it('sin {s} devuelve una sola URL', () => {
    expect(expandSubdomains('https://x/{z}/{x}/{y}.png')).toEqual(['https://x/{z}/{x}/{y}.png'])
  })
})

describe('basemapRasterSpec sobre TODOS los basemaps del store', () => {
  it('cada basemap produce ≥1 tile con {z},{x},{y} y sin {s} residual', () => {
    for (const cfg of BASE_MAPS) {
      const spec = basemapRasterSpec(cfg)
      expect(spec.tiles.length).toBeGreaterThanOrEqual(1)
      for (const t of spec.tiles) {
        expect(t).toContain('{z}')
        expect(t).toContain('{x}')
        expect(t).toContain('{y}')
        expect(t).not.toContain('{s}')
      }
    }
  })

  it('osm-hot expande a 3 subdominios', () => {
    const hot = BASE_MAPS.find((b) => b.id === 'osm-hot') as BaseMapConfig
    expect(basemapRasterSpec(hot).tiles).toHaveLength(3)
  })
})
