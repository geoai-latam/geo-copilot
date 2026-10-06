/**
 * MAP-PICKING-IDENTIFY — tests del identify REST sobre capas raster ArcGIS.
 *
 * Cubre buildIdentifyUrl (query params correctos por tipo de servicio, paridad
 * con el motor anterior) y parseIdentifyResponse (fixtures de respuesta real de
 * MapServer identify e ImageServer identify).
 */
import { describe, it, expect } from 'vitest'
import {
  detectServiceType,
  splitServiceRoot,
  buildIdentifyUrl,
  parseIdentifyResponse,
  type IdentifyExtent,
} from './arcgisIdentify'

const MAP_URL = 'https://services.igac.gov.co/arcgis/rest/services/Cartografia/MapServer'
const MAP_LAYER_URL = `${MAP_URL}/3`
const FEATURE_URL = 'https://services.igac.gov.co/arcgis/rest/services/Predios/FeatureServer/0'
const IMAGE_URL = 'https://tiledbasemaps.arcgis.com/arcgis/rest/services/DEM/ImageServer'

const EXTENT: IdentifyExtent = { xmin: -75, ymin: 4, xmax: -73, ymax: 6 }

/** Helper: parsea la query de una URL a un objeto plano decodificado. */
function query(url: string): Record<string, string> {
  const u = new URL(url)
  const out: Record<string, string> = {}
  u.searchParams.forEach((v, k) => {
    out[k] = v
  })
  return out
}

describe('detectServiceType', () => {
  it('reconoce ImageServer', () => {
    expect(detectServiceType(IMAGE_URL)).toBe('ImageServer')
    expect(detectServiceType(IMAGE_URL.toUpperCase())).toBe('ImageServer')
  })
  it('reconoce FeatureServer', () => {
    expect(detectServiceType(FEATURE_URL)).toBe('FeatureServer')
  })
  it('reconoce MapServer', () => {
    expect(detectServiceType(MAP_URL)).toBe('MapServer')
    expect(detectServiceType(MAP_LAYER_URL)).toBe('MapServer')
  })
  it('default MapServer para URLs desconocidas', () => {
    expect(detectServiceType('https://example.com/foo')).toBe('MapServer')
  })
})

describe('splitServiceRoot', () => {
  it('separa un layer id numérico final', () => {
    expect(splitServiceRoot(MAP_LAYER_URL)).toEqual({ base: MAP_URL, layerId: '3' })
  })
  it('sin layer id devuelve la URL como base', () => {
    expect(splitServiceRoot(MAP_URL)).toEqual({ base: MAP_URL, layerId: null })
  })
  it('ignora slash final', () => {
    expect(splitServiceRoot(`${MAP_LAYER_URL}/`)).toEqual({ base: MAP_URL, layerId: '3' })
  })
  it('no confunde el puerto ni el host con layer id', () => {
    expect(splitServiceRoot('https://host:8080/rest/MapServer')).toEqual({
      base: 'https://host:8080/rest/MapServer',
      layerId: null,
    })
  })
})

describe('buildIdentifyUrl — MapServer', () => {
  const url = buildIdentifyUrl(MAP_URL, { lon: -74.1, lat: 4.65, mapExtent: EXTENT })

  it('apunta a /identify en la raíz del servicio', () => {
    expect(new URL(url).pathname.endsWith('/MapServer/identify')).toBe(true)
  })

  it('geometry es "lon,lat" (paridad con el motor anterior)', () => {
    expect(query(url).geometry).toBe('-74.1,4.65')
  })

  it('geometryType=esriGeometryPoint, sr=4326, f=json', () => {
    const q = query(url)
    expect(q.geometryType).toBe('esriGeometryPoint')
    expect(q.sr).toBe('4326')
    expect(q.f).toBe('json')
  })

  it('tolerance por defecto = 2', () => {
    expect(query(url).tolerance).toBe('2')
  })

  it('imageDisplay por defecto = 256,256,96', () => {
    expect(query(url).imageDisplay).toBe('256,256,96')
  })

  it('mapExtent = xmin,ymin,xmax,ymax', () => {
    expect(query(url).mapExtent).toBe('-75,4,-73,6')
  })

  it('layers por defecto = visible', () => {
    expect(query(url).layers).toBe('visible')
  })

  it('returnGeometry=false por defecto', () => {
    expect(query(url).returnGeometry).toBe('false')
  })

  it('respeta overrides de tolerance/imageDisplay/layers/returnGeometry', () => {
    const q = query(
      buildIdentifyUrl(MAP_URL, {
        lon: -74,
        lat: 4,
        mapExtent: EXTENT,
        tolerance: 5,
        width: 800,
        height: 600,
        dpi: 120,
        layers: 'all:0,1',
        returnGeometry: true,
      }),
    )
    expect(q.tolerance).toBe('5')
    expect(q.imageDisplay).toBe('800,600,120')
    expect(q.layers).toBe('all:0,1')
    expect(q.returnGeometry).toBe('true')
  })

  it('con layer id en la URL usa layers=visible:<id> y /identify en la raíz', () => {
    const u = buildIdentifyUrl(MAP_LAYER_URL, { lon: -74, lat: 4, mapExtent: EXTENT })
    expect(new URL(u).pathname.endsWith('/MapServer/identify')).toBe(true)
    expect(query(u).layers).toBe('visible:3')
  })

  it('sr custom se propaga', () => {
    const u = buildIdentifyUrl(MAP_URL, { lon: 100, lat: 200, mapExtent: EXTENT, sr: 3857 })
    expect(query(u).sr).toBe('3857')
  })
})

describe('buildIdentifyUrl — FeatureServer', () => {
  it('construye /identify tratando FeatureServer como MapServer', () => {
    const u = buildIdentifyUrl(FEATURE_URL, { lon: -74, lat: 4, mapExtent: EXTENT })
    expect(new URL(u).pathname.endsWith('/FeatureServer/identify')).toBe(true)
    // el /0 es un layer id → layers=visible:0
    expect(query(u).layers).toBe('visible:0')
    expect(query(u).geometryType).toBe('esriGeometryPoint')
  })
})

describe('buildIdentifyUrl — ImageServer', () => {
  const url = buildIdentifyUrl(IMAGE_URL, { lon: -74.1, lat: 4.65 })

  it('apunta a /identify del ImageServer', () => {
    expect(new URL(url).pathname.endsWith('/ImageServer/identify')).toBe(true)
  })

  it('geometry es JSON de punto con spatialReference wkid', () => {
    const geom = JSON.parse(query(url).geometry) as {
      x: number
      y: number
      spatialReference: { wkid: number }
    }
    expect(geom).toEqual({ x: -74.1, y: 4.65, spatialReference: { wkid: 4326 } })
  })

  it('geometryType=esriGeometryPoint, f=json', () => {
    const q = query(url)
    expect(q.geometryType).toBe('esriGeometryPoint')
    expect(q.f).toBe('json')
  })

  it('returnCatalogItems=true y returnGeometry=false por defecto', () => {
    const q = query(url)
    expect(q.returnCatalogItems).toBe('true')
    expect(q.returnGeometry).toBe('false')
  })

  it('NO incluye mapExtent/imageDisplay/tolerance/layers (no aplican)', () => {
    const q = query(url)
    expect(q.mapExtent).toBeUndefined()
    expect(q.imageDisplay).toBeUndefined()
    expect(q.tolerance).toBeUndefined()
    expect(q.layers).toBeUndefined()
  })

  it('serviceType explícito fuerza el modo ImageServer aunque la URL no lo diga', () => {
    const u = buildIdentifyUrl('https://host/rest/services/Foo/MapServer', {
      lon: 1,
      lat: 2,
      serviceType: 'ImageServer',
    })
    expect(new URL(u).pathname.endsWith('/identify')).toBe(true)
    expect(query(u).returnCatalogItems).toBe('true')
    // no debe tratar el path como MapServer
    expect(query(u).imageDisplay).toBeUndefined()
  })

  it('returnCatalogItems se puede desactivar', () => {
    const u = buildIdentifyUrl(IMAGE_URL, { lon: 1, lat: 2, returnCatalogItems: false })
    expect(query(u).returnCatalogItems).toBe('false')
  })
})

// ---------------------------------------------------------------------------
// Fixtures de respuesta real
// ---------------------------------------------------------------------------

const MAP_IDENTIFY_JSON = {
  results: [
    {
      layerId: 0,
      layerName: 'Municipios',
      displayFieldName: 'NOMBRE',
      value: 'BOGOTA D.C.',
      attributes: {
        OBJECTID: '1',
        NOMBRE: 'BOGOTA D.C.',
        COD_DANE: '11001',
        AREA_KM2: '1587.0',
      },
      geometryType: 'esriGeometryPolygon',
      geometry: { rings: [[[-74.2, 4.5], [-74.0, 4.5], [-74.0, 4.8], [-74.2, 4.5]]] },
    },
    {
      layerId: 1,
      layerName: 'Departamentos',
      displayFieldName: 'DEPTO',
      value: 'CUNDINAMARCA',
      attributes: { OBJECTID: '25', DEPTO: 'CUNDINAMARCA', COD: '25' },
    },
  ],
}

const IMAGE_IDENTIFY_CATALOG_JSON = {
  objectId: 0,
  name: 'Landsat',
  value: '148',
  location: { x: -74.1, y: 4.65, spatialReference: { wkid: 4326 } },
  properties: null,
  catalogItems: {
    objectIdFieldName: 'OBJECTID',
    fields: [{ name: 'OBJECTID', type: 'esriFieldTypeOID' }],
    features: [
      {
        attributes: {
          OBJECTID: 12,
          Name: 'LC08_L1TP_008057',
          AcquisitionDate: 1600000000000,
          CloudCover: 0.12,
        },
      },
      {
        attributes: { OBJECTID: 34, Name: 'LC08_L1TP_008058', CloudCover: 0.4 },
      },
    ],
  },
  catalogItemVisibilities: [1, 0.5],
}

const IMAGE_IDENTIFY_PIXEL_JSON = {
  objectId: 0,
  name: 'Elevation',
  value: '2640.5',
  location: { x: -74.1, y: 4.65, spatialReference: { wkid: 4326 } },
  properties: { Elevation: '2640.5', Units: 'meters' },
}

describe('parseIdentifyResponse — MapServer', () => {
  it('normaliza cada result a { layerName, attributes, value }', () => {
    const rows = parseIdentifyResponse(MAP_IDENTIFY_JSON, 'MapServer')
    expect(rows).toHaveLength(2)
    expect(rows[0].layerName).toBe('Municipios')
    expect(rows[0].value).toBe('BOGOTA D.C.')
    expect(rows[0].attributes.COD_DANE).toBe('11001')
    expect(rows[1].layerName).toBe('Departamentos')
    expect(rows[1].attributes.DEPTO).toBe('CUNDINAMARCA')
  })

  it('no incluye la geometría en attributes', () => {
    const rows = parseIdentifyResponse(MAP_IDENTIFY_JSON, 'MapServer')
    expect(rows[0].attributes.geometry).toBeUndefined()
    expect('geometry' in rows[0]).toBe(false)
  })

  it('infiere MapServer cuando no se pasa serviceType', () => {
    expect(parseIdentifyResponse(MAP_IDENTIFY_JSON)).toHaveLength(2)
  })

  it('results vacío → []', () => {
    expect(parseIdentifyResponse({ results: [] })).toEqual([])
  })

  it('result sin attributes → attributes {}', () => {
    const rows = parseIdentifyResponse({ results: [{ layerName: 'X', value: 'v' }] }, 'MapServer')
    expect(rows[0].attributes).toEqual({})
    expect(rows[0].value).toBe('v')
  })

  it('result sin layerName → layerName ""', () => {
    const rows = parseIdentifyResponse({ results: [{ attributes: { a: 1 } }] }, 'MapServer')
    expect(rows[0].layerName).toBe('')
  })
})

describe('parseIdentifyResponse — ImageServer', () => {
  it('con catalogItems produce una fila por feature del mosaico', () => {
    const rows = parseIdentifyResponse(IMAGE_IDENTIFY_CATALOG_JSON, 'ImageServer')
    expect(rows).toHaveLength(2)
    expect(rows[0].layerName).toBe('Landsat')
    expect(rows[0].attributes.Name).toBe('LC08_L1TP_008057')
    expect(rows[0].value).toBe('148') // el valor del píxel se propaga
    expect(rows[1].attributes.OBJECTID).toBe(34)
  })

  it('sin catalogItems usa properties + value del píxel', () => {
    const rows = parseIdentifyResponse(IMAGE_IDENTIFY_PIXEL_JSON, 'ImageServer')
    expect(rows).toHaveLength(1)
    expect(rows[0].layerName).toBe('Elevation')
    expect(rows[0].value).toBe('2640.5')
    expect(rows[0].attributes.Units).toBe('meters')
  })

  it('infiere ImageServer por catalogItems/value cuando no hay serviceType', () => {
    expect(parseIdentifyResponse(IMAGE_IDENTIFY_CATALOG_JSON)).toHaveLength(2)
    expect(parseIdentifyResponse(IMAGE_IDENTIFY_PIXEL_JSON)).toHaveLength(1)
  })

  it('catalogItems.features vacío cae al modo píxel', () => {
    const rows = parseIdentifyResponse(
      { name: 'DEM', value: '10', properties: { a: 'b' }, catalogItems: { features: [] } },
      'ImageServer',
    )
    expect(rows).toHaveLength(1)
    expect(rows[0].value).toBe('10')
    expect(rows[0].attributes.a).toBe('b')
  })

  it('properties null → attributes {}', () => {
    const rows = parseIdentifyResponse({ name: 'DEM', value: '5', properties: null }, 'ImageServer')
    expect(rows[0].attributes).toEqual({})
    expect(rows[0].value).toBe('5')
  })

  it('sin name usa "Pixel"', () => {
    const rows = parseIdentifyResponse({ value: '7', properties: {} }, 'ImageServer')
    expect(rows[0].layerName).toBe('Pixel')
  })

  it('value numérico se conserva como number', () => {
    const rows = parseIdentifyResponse({ name: 'DEM', value: 2640, properties: {} }, 'ImageServer')
    expect(rows[0].value).toBe(2640)
  })
})

describe('parseIdentifyResponse — robustez', () => {
  it('respuesta de error ArcGIS → []', () => {
    expect(
      parseIdentifyResponse({ error: { code: 400, message: 'Invalid geometry' } }),
    ).toEqual([])
  })

  it('null/undefined/no-objeto → []', () => {
    expect(parseIdentifyResponse(null)).toEqual([])
    expect(parseIdentifyResponse(undefined)).toEqual([])
    expect(parseIdentifyResponse('nope')).toEqual([])
    expect(parseIdentifyResponse(42)).toEqual([])
    expect(parseIdentifyResponse([])).toEqual([])
  })

  it('objeto vacío → [] (results ausente)', () => {
    expect(parseIdentifyResponse({})).toEqual([])
  })
})
