/**
 * Tests de la lógica del DataDiscoveryPanel.
 *
 * Antes (auditoría 2026-05-24): NO había tests para este componente —
 * uno de los gaps críticos del frontend. Como no usamos
 * @testing-library/react, extrajimos la lógica de transformación
 * query+chips→hints a `discoveryPanel.helpers.ts` y la testeamos aquí
 * sin necesidad de renderizar.
 */

import { describe, expect, it } from 'vitest'
import type { HubItem } from '@/types/discovery'
import {
  ZONE_PRESETS,
  buildSearchRequest,
  hechosDeTarjeta,
  necesitaElegirCapa,
  nombreGeometria,
} from './discoveryPanel.helpers'

describe('buildSearchRequest — caso degenerate', () => {
  it('devuelve null cuando no hay query ni filtro activo', () => {
    const result = buildSearchRequest({
      query: '',
      zone: null,
      serviceFilter: null,
      officialOnly: false,
      globalMode: false,
    })
    expect(result).toBeNull()
  })

  it('devuelve null cuando solo hay whitespace en query', () => {
    const result = buildSearchRequest({
      query: '   \t\n  ',
      zone: null,
      serviceFilter: null,
      officialOnly: false,
      globalMode: false,
    })
    expect(result).toBeNull()
  })
})

describe('buildSearchRequest — input de texto solo', () => {
  it('manda el texto trimmed sin hints adicionales', () => {
    const r = buildSearchRequest({
      query: '  catastro Bogotá  ',
      zone: null,
      serviceFilter: null,
      officialOnly: false,
      globalMode: false,
    })!
    expect(r.effectiveQuery).toBe('catastro Bogotá')
    expect(r.hints.tags_any).toBeUndefined()
    expect(r.hints.service_types).toBeUndefined()
    expect(r.hints.only_official_co).toBeFalsy()
    expect(r.hints.global_mode).toBeFalsy()
    expect(r.hints.max_results).toBe(60)
  })
})

describe('buildSearchRequest — chip de zona solo (regional default)', () => {
  it('chip Bogotá añade tags_any curados + extraQuery con Colombia', () => {
    const r = buildSearchRequest({
      query: '',
      zone: 'bogota',
      serviceFilter: null,
      officialOnly: false,
      globalMode: false,
    })!
    // El extraQuery del preset bogota es "Bogotá Colombia".
    expect(r.effectiveQuery).toBe('Bogotá Colombia')
    // Tags curados: bogota + variante con tilde + colombia
    expect(r.hints.tags_any).toEqual(['bogota', 'bogotá', 'colombia'])
    expect(r.hints.global_mode).toBeFalsy()
  })

  it('chip Cali añade "Cali Colombia" — previene match con California (USA)', () => {
    const r = buildSearchRequest({
      query: '',
      zone: 'cali',
      serviceFilter: null,
      officialOnly: false,
      globalMode: false,
    })!
    expect(r.effectiveQuery).toBe('Cali Colombia')
    expect(r.hints.tags_any).toContain('cali')
    expect(r.hints.tags_any).toContain('colombia')
    // Tags incluyen valle del cauca como signal extra.
    expect(r.hints.tags_any).toContain('valle del cauca')
  })

  it('chip Colombia (país) usa solo "Colombia" sin duplicar', () => {
    const r = buildSearchRequest({
      query: '',
      zone: 'colombia',
      serviceFilter: null,
      officialOnly: false,
      globalMode: false,
    })!
    // El extraQuery del preset colombia es "Colombia" (no "Colombia Colombia")
    expect(r.effectiveQuery).toBe('Colombia')
    expect(r.hints.tags_any).toEqual(['colombia'])
  })

  it('zone desconocido se ignora silenciosamente', () => {
    const r = buildSearchRequest({
      query: 'catastro',
      zone: 'narnia',  // no existe en ZONE_PRESETS
      serviceFilter: null,
      officialOnly: false,
      globalMode: false,
    })!
    expect(r.effectiveQuery).toBe('catastro')
    expect(r.hints.tags_any).toBeUndefined()
  })
})

describe('buildSearchRequest — chip de zona + texto', () => {
  it('combina query del usuario + extraQuery de zona', () => {
    const r = buildSearchRequest({
      query: 'manzanas',
      zone: 'bogota',
      serviceFilter: null,
      officialOnly: false,
      globalMode: false,
    })!
    expect(r.effectiveQuery).toBe('manzanas Bogotá Colombia')
    expect(r.hints.tags_any).toEqual(['bogota', 'bogotá', 'colombia'])
  })
})

describe('buildSearchRequest — service filter', () => {
  it('FeatureServer mapea a "Feature Service"', () => {
    const r = buildSearchRequest({
      query: 'ortofoto',
      zone: null,
      serviceFilter: 'FeatureServer',
      officialOnly: false,
      globalMode: false,
    })!
    expect(r.hints.service_types).toEqual(['Feature Service'])
  })

  it('MapServer mapea a "Map Service"', () => {
    const r = buildSearchRequest({
      query: '',
      zone: null,
      serviceFilter: 'MapServer',
      officialOnly: false,
      globalMode: false,
    })!
    expect(r.hints.service_types).toEqual(['Map Service'])
  })

  it('ImageServer mapea a "Image Service"', () => {
    const r = buildSearchRequest({
      query: '',
      zone: null,
      serviceFilter: 'ImageServer',
      officialOnly: false,
      globalMode: false,
    })!
    expect(r.hints.service_types).toEqual(['Image Service'])
  })

  it('tipos no-Hub (GeoJSON, CSV, Postgis, Other) NO se mandan a Hub', () => {
    for (const t of ['GeoJSON', 'CSV', 'Postgis', 'Other'] as const) {
      const r = buildSearchRequest({
        query: 'foo',
        zone: null,
        serviceFilter: t,
        officialOnly: false,
        globalMode: false,
      })!
      expect(r.hints.service_types).toBeUndefined()
    }
  })
})

describe('buildSearchRequest — chip "Oficial CO"', () => {
  it('setea only_official_co=true', () => {
    const r = buildSearchRequest({
      query: 'catastro',
      zone: null,
      serviceFilter: null,
      officialOnly: true,
      globalMode: false,
    })!
    expect(r.hints.only_official_co).toBe(true)
  })

  it('oficial CO solo (sin texto) genera request válido', () => {
    const r = buildSearchRequest({
      query: '',
      zone: null,
      serviceFilter: null,
      officialOnly: true,
      globalMode: false,
    })
    expect(r).not.toBeNull()
    expect(r!.hints.only_official_co).toBe(true)
    expect(r!.effectiveQuery).toBe('')
  })
})

describe('buildSearchRequest — chip "Global"', () => {
  it('setea global_mode=true', () => {
    const r = buildSearchRequest({
      query: 'ortofoto',
      zone: null,
      serviceFilter: null,
      officialOnly: false,
      globalMode: true,
    })!
    expect(r.hints.global_mode).toBe(true)
  })

  it('en modo global NO añade tags_any de zona ni "Colombia" al q', () => {
    const r = buildSearchRequest({
      query: 'red vial',
      zone: 'bogota',  // chip activo
      serviceFilter: null,
      officialOnly: false,
      globalMode: true,
    })!
    // En global solo usa el LABEL de la zona, no el extraQuery con "Colombia".
    expect(r.effectiveQuery).toBe('red vial Bogotá')
    // Y NO añade tags_any — el usuario quiere búsqueda mundial.
    expect(r.hints.tags_any).toBeUndefined()
    expect(r.hints.global_mode).toBe(true)
  })

  it('global mode + Cali sin sesgar a Colombia', () => {
    const r = buildSearchRequest({
      query: '',
      zone: 'cali',
      serviceFilter: null,
      officialOnly: false,
      globalMode: true,
    })!
    // Solo el label, no "Cali Colombia".
    expect(r.effectiveQuery).toBe('Cali')
    expect(r.hints.tags_any).toBeUndefined()
  })

  it('global mode solo (sin nada más) genera request válido', () => {
    const r = buildSearchRequest({
      query: '',
      zone: null,
      serviceFilter: null,
      officialOnly: false,
      globalMode: true,
    })
    expect(r).not.toBeNull()
    expect(r!.hints.global_mode).toBe(true)
  })
})

describe('buildSearchRequest — combinaciones reales del panel', () => {
  it('chip Bogotá + Feature + Oficial CO', () => {
    const r = buildSearchRequest({
      query: 'manzanas',
      zone: 'bogota',
      serviceFilter: 'FeatureServer',
      officialOnly: true,
      globalMode: false,
    })!
    expect(r.effectiveQuery).toBe('manzanas Bogotá Colombia')
    expect(r.hints.tags_any).toEqual(['bogota', 'bogotá', 'colombia'])
    expect(r.hints.service_types).toEqual(['Feature Service'])
    expect(r.hints.only_official_co).toBe(true)
  })

  it('texto solo en modo Global (búsqueda mundial sin sesgo)', () => {
    const r = buildSearchRequest({
      query: 'ortofoto',
      zone: null,
      serviceFilter: 'ImageServer',
      officialOnly: false,
      globalMode: true,
    })!
    expect(r.effectiveQuery).toBe('ortofoto')
    expect(r.hints.tags_any).toBeUndefined()
    expect(r.hints.service_types).toEqual(['Image Service'])
    expect(r.hints.global_mode).toBe(true)
    expect(r.hints.only_official_co).toBeFalsy()
  })

  it('max_results respeta override del caller', () => {
    const r = buildSearchRequest({
      query: 'catastro',
      zone: null,
      serviceFilter: null,
      officialOnly: false,
      globalMode: false,
      maxResults: 200,
    })!
    expect(r.hints.max_results).toBe(200)
  })
})

describe('ZONE_PRESETS — invariantes del catálogo', () => {
  it('cada preset tiene id, label, tags, extraQuery', () => {
    for (const preset of ZONE_PRESETS) {
      expect(preset.id).toBeTruthy()
      expect(preset.label).toBeTruthy()
      expect(preset.tags.length).toBeGreaterThan(0)
      expect(preset.extraQuery).toBeTruthy()
    }
  })

  it('todas las zonas excepto "colombia" incluyen "colombia" en sus tags', () => {
    // Garantiza que ciudades/regiones siempre sesgan a Colombia.
    for (const preset of ZONE_PRESETS) {
      if (preset.id === 'colombia') continue
      expect(preset.tags).toContain('colombia')
    }
  })

  it('IDs son únicos', () => {
    const ids = ZONE_PRESETS.map((p) => p.id)
    expect(new Set(ids).size).toBe(ids.length)
  })
})

describe('selector de capa y hechos de la tarjeta (rama arcgis-busqueda)', () => {
  const base: HubItem = {
    id: 'a', source: 'arcgis_online', org: 'x', title: 'Cota', description: '',
    service_type: 'FeatureServer', service_url: 'https://s/arcgis/rest/services/Cota/FeatureServer',
  }

  it('un FeatureServer sin capa pregunta cuál cargar (antes se cargaba la 0: puntos en Cota)', () => {
    expect(necesitaElegirCapa(base)).toBe(true)
    expect(necesitaElegirCapa({ ...base, service_url: base.service_url + '/' })).toBe(true)
  })

  it('no pregunta si la capa ya está, si se sabe que es de una sola o si no es FeatureServer', () => {
    expect(necesitaElegirCapa({ ...base, service_url: base.service_url + '/3' })).toBe(false)
    expect(necesitaElegirCapa({ ...base, layer_id: 2 })).toBe(false)
    expect(necesitaElegirCapa({ ...base, single_layer: true })).toBe(false)
    expect(necesitaElegirCapa({ ...base, service_type: 'MapServer' })).toBe(false)
  })

  it('la fuente son los créditos declarados; si no hay, la organización', () => {
    expect(hechosDeTarjeta({ ...base, credits: 'Secretaría Distrital de Movilidad', views: 60010 }))
      .toEqual({ fuente: 'Secretaría Distrital de Movilidad', uso: `${(60010).toLocaleString('es-CO')} vistas` })
    expect(hechosDeTarjeta({ ...base, credits: '  ', views: 0 })).toEqual({ fuente: 'x', uso: null })
  })

  it('nombra la geometría para quien elige', () => {
    expect(nombreGeometria('Polyline')).toBe('líneas')
    expect(nombreGeometria('Polygon')).toBe('polígonos')
    expect(nombreGeometria('Raro')).toBe('Raro')
  })
})
