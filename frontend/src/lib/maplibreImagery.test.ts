import { describe, it, expect } from 'vitest'
import {
  isImageServerUrl,
  isXyzTemplate,
  resolveIsImageServer,
  cleanServiceUrl,
  imageryTileUrl,
  proxiedTileUrl,
  imageryRasterSource,
  imageryLayerSpec,
  transformRequestApi,
  PROXY_BASE,
  type ImageryDescriptor,
} from './maplibreImagery'

describe('isXyzTemplate', () => {
  it('las teselas NDVI/cambio con {z} son XYZ nativas; ArcGIS no', () => {
    expect(isXyzTemplate(
      '/api/v1/proxy/mcp/imagery/tiles/S2A_X/{z}/{x}/{y}.png?rescale=0.12,0.62')).toBe(true)
    expect(isXyzTemplate(
      '/api/v1/proxy/ndvi-diff-tiles/A/B/{z}/{x}/{y}.png?rescale=-0.5,0.5')).toBe(true)
    expect(isXyzTemplate(
      'https://sampleserver6.arcgisonline.com/arcgis/rest/services/Toronto/ImageServer'))
      .toBe(false)
  })

  it('la plantilla XYZ conserva el ?rescale íntegro (MapLibre solo sustituye z/x/y)', () => {
    const url = '/api/v1/proxy/mcp/imagery/tiles/S2A_X/{z}/{x}/{y}.png?rescale=0.12,0.62'
    expect(isXyzTemplate(url)).toBe(true)
    expect(url).toContain('?rescale=0.12,0.62')
  })
})

// Bases representativas del catálogo Discovery (ArcGIS REST).
const MAPSERVER =
  'https://sampleserver6.arcgisonline.com/arcgis/rest/services/USA/MapServer'
const IMAGESERVER =
  'https://sampleserver6.arcgisonline.com/arcgis/rest/services/Toronto/ImageServer'

// Los params EXACTOS que produce el motor anterior (motor-anterior.tsx:798), con el
// bbox en el token nativo de MapLibre en vez del bbox proyectado del motor anterior.
const EXPECTED_QUERY =
  'bbox={bbox-epsg-3857}' +
  '&bboxSR=3857&imageSR=3857&size=256,256&format=png&transparent=true&f=image'

describe('isImageServerUrl (paridad motor-anterior.tsx:788 /imageserver/i)', () => {
  it('detecta ImageServer sin importar el caso', () => {
    expect(isImageServerUrl(IMAGESERVER)).toBe(true)
    expect(isImageServerUrl('https://x/rest/services/Y/imageserver')).toBe(true)
    expect(isImageServerUrl('https://x/rest/services/Y/IMAGESERVER')).toBe(true)
  })
  it('NO marca MapServer como ImageServer', () => {
    expect(isImageServerUrl(MAPSERVER)).toBe(false)
    expect(isImageServerUrl('https://x/rest/services/Y/FeatureServer')).toBe(false)
  })
})

describe('resolveIsImageServer', () => {
  it('respeta type explícito ImageServer aunque la URL no lo diga', () => {
    const d: ImageryDescriptor = { service_url: MAPSERVER, type: 'ImageServer' }
    expect(resolveIsImageServer(d)).toBe(true)
  })
  it('respeta type explícito MapServer aunque la URL diga imageserver', () => {
    const d: ImageryDescriptor = { service_url: IMAGESERVER, type: 'MapServer' }
    expect(resolveIsImageServer(d)).toBe(false)
  })
  it('acepta el alias service_type del catálogo Discovery', () => {
    expect(
      resolveIsImageServer({ service_url: MAPSERVER, service_type: 'ImageServer' })
    ).toBe(true)
    expect(
      resolveIsImageServer({ service_url: IMAGESERVER, service_type: 'MapServer' })
    ).toBe(false)
  })
  it('es case-insensitive en el type declarado', () => {
    expect(resolveIsImageServer({ service_url: MAPSERVER, type: 'imageserver' })).toBe(true)
    expect(resolveIsImageServer({ service_url: IMAGESERVER, type: 'mapserver' })).toBe(false)
  })
  it('infiere de la URL cuando no hay type (paridad motor anterior)', () => {
    expect(resolveIsImageServer({ service_url: IMAGESERVER })).toBe(true)
    expect(resolveIsImageServer({ service_url: MAPSERVER })).toBe(false)
  })
  it('trata type null/desconocido como ausente y cae a la URL', () => {
    expect(resolveIsImageServer({ service_url: IMAGESERVER, type: null })).toBe(true)
    expect(
      resolveIsImageServer({ service_url: MAPSERVER, type: 'FeatureServer' })
    ).toBe(false)
  })
})

describe('cleanServiceUrl (paridad motor-anterior.tsx:796 replace(/\\/$/, ""))', () => {
  it('quita UNA barra final', () => {
    expect(cleanServiceUrl(`${IMAGESERVER}/`)).toBe(IMAGESERVER)
  })
  it('deja intacta una URL sin barra final', () => {
    expect(cleanServiceUrl(IMAGESERVER)).toBe(IMAGESERVER)
  })
  it('solo quita la última barra (replace, no rstrip)', () => {
    expect(cleanServiceUrl(`${IMAGESERVER}//`)).toBe(`${IMAGESERVER}/`)
  })
})

describe('imageryTileUrl — ImageServer usa /exportImage', () => {
  it('produce el urlTemplate exacto contra /exportImage', () => {
    const url = imageryTileUrl({ service_url: IMAGESERVER })
    expect(url).toBe(`${IMAGESERVER}/exportImage?${EXPECTED_QUERY}`)
  })
  it('incluye el token {bbox-epsg-3857} de MapLibre', () => {
    expect(imageryTileUrl({ service_url: IMAGESERVER })).toContain('bbox={bbox-epsg-3857}')
  })
  it('normaliza la barra final antes de /exportImage', () => {
    const url = imageryTileUrl({ service_url: `${IMAGESERVER}/` })
    expect(url).toBe(`${IMAGESERVER}/exportImage?${EXPECTED_QUERY}`)
    expect(url).not.toContain('//exportImage')
  })
})

describe('imageryTileUrl — MapServer usa /export', () => {
  it('produce el urlTemplate exacto contra /export', () => {
    const url = imageryTileUrl({ service_url: MAPSERVER })
    expect(url).toBe(`${MAPSERVER}/export?${EXPECTED_QUERY}`)
  })
  it('NO usa /exportImage para MapServer', () => {
    expect(imageryTileUrl({ service_url: MAPSERVER })).not.toContain('/exportImage')
  })
  it('respeta el type explícito para elegir el endpoint', () => {
    // type fuerza ImageServer aunque la URL sea MapServer
    expect(imageryTileUrl({ service_url: MAPSERVER, type: 'ImageServer' })).toContain(
      '/exportImage?'
    )
    // type fuerza MapServer aunque la URL sea ImageServer
    const forced = imageryTileUrl({ service_url: IMAGESERVER, type: 'MapServer' })
    expect(forced).toContain('/export?')
    expect(forced).not.toContain('/exportImage?')
  })
})

describe('imageryTileUrl — parámetros de export (paridad motor-anterior.tsx:798)', () => {
  it('incluye TODOS los params con los valores exactos', () => {
    const url = imageryTileUrl({ service_url: IMAGESERVER })
    const qs = url.split('?')[1]
    const params = new URLSearchParams(qs)
    expect(params.get('bbox')).toBe('{bbox-epsg-3857}')
    expect(params.get('bboxSR')).toBe('3857')
    expect(params.get('imageSR')).toBe('3857')
    expect(params.get('size')).toBe('256,256')
    expect(params.get('format')).toBe('png')
    expect(params.get('transparent')).toBe('true')
    expect(params.get('f')).toBe('image')
  })
  it('el orden de los params es estable (bbox primero, f último)', () => {
    const qs = imageryTileUrl({ service_url: MAPSERVER }).split('?')[1]
    expect(qs.startsWith('bbox={bbox-epsg-3857}')).toBe(true)
    expect(qs.endsWith('f=image')).toBe(true)
  })
})

describe('imageryRasterSource', () => {
  it('devuelve un raster source plano con tileSize 256', () => {
    const src = imageryRasterSource({ service_url: IMAGESERVER })
    expect(src).toEqual({
      type: 'raster',
      tiles: [`${IMAGESERVER}/exportImage?${EXPECTED_QUERY}`],
      tileSize: 256,
    })
  })
  it('MapServer produce el source contra /export', () => {
    const src = imageryRasterSource({ service_url: MAPSERVER })
    expect(src.type).toBe('raster')
    expect(src.tileSize).toBe(256)
    expect(src.tiles).toHaveLength(1)
    expect(src.tiles[0]).toBe(`${MAPSERVER}/export?${EXPECTED_QUERY}`)
  })
  it('es serializable a JSON (spec plano, sin instancias del motor)', () => {
    const src = imageryRasterSource({ service_url: IMAGESERVER })
    expect(JSON.parse(JSON.stringify(src))).toEqual(src)
  })
})

describe('proxiedTileUrl — rutea por el proxy de teselas del backend', () => {
  const PROXY = '/api/v1/proxy/imagery'

  it('deja {bbox-epsg-3857} LITERAL (MapLibre debe sustituirlo por tesela)', () => {
    const url = proxiedTileUrl({ service_url: IMAGESERVER }, PROXY)
    expect(url).toContain('bbox={bbox-epsg-3857}')
    // El placeholder NO debe quedar codificado, o MapLibre no lo reemplaza.
    expect(url).not.toContain('%7Bbbox')
  })
  it('codifica solo la base del servicio en el param service', () => {
    const url = proxiedTileUrl({ service_url: IMAGESERVER }, PROXY)
    const qs = new URLSearchParams(url.split('?')[1])
    expect(qs.get('service')).toBe(IMAGESERVER)
    expect(url.startsWith(`${PROXY}?service=${encodeURIComponent(IMAGESERVER)}`)).toBe(true)
  })
  it('pasa endpoint=exportImage para ImageServer y export para MapServer', () => {
    expect(new URLSearchParams(proxiedTileUrl({ service_url: IMAGESERVER }, PROXY).split('?')[1]).get('endpoint')).toBe('exportImage')
    expect(new URLSearchParams(proxiedTileUrl({ service_url: MAPSERVER }, PROXY).split('?')[1]).get('endpoint')).toBe('export')
  })
  it('respeta el type explícito para elegir el endpoint', () => {
    const forced = proxiedTileUrl({ service_url: MAPSERVER, type: 'ImageServer' }, PROXY)
    expect(new URLSearchParams(forced.split('?')[1]).get('endpoint')).toBe('exportImage')
  })
  it('normaliza la barra final de la base', () => {
    const url = proxiedTileUrl({ service_url: `${IMAGESERVER}/` }, PROXY)
    expect(new URLSearchParams(url.split('?')[1]).get('service')).toBe(IMAGESERVER)
  })
})

describe('imageryRasterSource con proxyBase', () => {
  const PROXY = '/api/v1/proxy/imagery'
  it('rutea el tile por el proxy cuando se pasa proxyBase', () => {
    const src = imageryRasterSource({ service_url: IMAGESERVER }, PROXY)
    expect(src.tiles[0]).toBe(proxiedTileUrl({ service_url: IMAGESERVER }, PROXY))
    expect(src.tiles[0].startsWith(PROXY)).toBe(true)
    expect(src.tileSize).toBe(256)
  })
  it('sin proxyBase va directo al servicio (comportamiento por defecto)', () => {
    const src = imageryRasterSource({ service_url: IMAGESERVER })
    expect(src.tiles[0]).toBe(`${IMAGESERVER}/exportImage?${EXPECTED_QUERY}`)
  })
})

describe('imageryLayerSpec', () => {
  it('devuelve un raster layer que apunta al source dado', () => {
    const spec = imageryLayerSpec('imagery-src-1')
    expect(spec).toEqual({
      id: 'imagery-src-1-layer',
      type: 'raster',
      source: 'imagery-src-1',
      paint: {},
    })
  })
  it('el id deriva del sourceId (trazabilidad 1:1)', () => {
    expect(imageryLayerSpec('foo').source).toBe('foo')
    expect(imageryLayerSpec('foo').id).toBe('foo-layer')
  })
  it('es serializable a JSON', () => {
    const spec = imageryLayerSpec('s')
    expect(JSON.parse(JSON.stringify(spec))).toEqual(spec)
  })
})

describe('composición source + layer (uso end-to-end esperado)', () => {
  it('un ImageServer se monta como source+layer coherentes', () => {
    const desc: ImageryDescriptor = { service_url: IMAGESERVER, type: 'ImageServer' }
    const sourceId = 'imagery-42'
    const src = imageryRasterSource(desc)
    const layer = imageryLayerSpec(sourceId)
    expect(src.tiles[0]).toContain('/exportImage?')
    expect(layer.source).toBe(sourceId)
    expect(layer.type).toBe(src.type)
  })
})

describe('transformRequestApi (el token del usuario en toda la API del mismo origen)', () => {
  const AUTH = { Authorization: 'Bearer t' }
  const ORIGEN = 'http://localhost:5173'
  const NDVI_TILE =
    '/api/v1/proxy/mcp/imagery/tiles/S2A_MSIL2A_20260601/13/1234/2345.png?rescale=-0.2,0.8'
  const ARCGIS_TILE =
    '/api/v1/proxy/imagery?service=https%3A%2F%2Figac%2FImageServer&endpoint=exportImage&bbox=1,2,3,4'
  const WS_TILE = `${ORIGEN}/api/v1/tiles/ws/sesion-1/ds_0123456789abcdef/12/1204/1974.pbf`
  const BASEMAP = 'https://basemaps.cartocdn.com/light_all/13/1234/2345.png'

  it('adjunta el token a las teselas NDVI de imagery-mcp (regresión H3)', () => {
    // El bug: el predicado antiguo comparaba contra '/api/v1/proxy/imagery', que
    // NO es substring de '/api/v1/proxy/mcp/imagery/tiles/…' → 401 con auth.
    expect(transformRequestApi(NDVI_TILE, AUTH, ORIGEN).headers).toEqual(AUTH)
  })

  it('y a las teselas ArcGIS de Discovery y a las del workspace (F6: antes iban sin nada)', () => {
    expect(transformRequestApi(ARCGIS_TILE, AUTH, ORIGEN).headers).toEqual(AUTH)
    expect(transformRequestApi(WS_TILE, AUTH, ORIGEN).headers).toEqual(AUTH)
  })

  it('NO manda el token a otros orígenes (basemaps, ni a una API con el mismo camino)', () => {
    const out = transformRequestApi(BASEMAP, AUTH, ORIGEN)
    expect(out.headers).toBeUndefined()
    expect(out.url).toBe(BASEMAP)
    expect(transformRequestApi('https://evil.test/api/v1/tiles/x.pbf', AUTH, ORIGEN).headers).toBeUndefined()
  })

  it('sin sesión (dev sin OIDC) no adjunta cabecera a ninguna ruta', () => {
    expect(transformRequestApi(NDVI_TILE, {}, ORIGEN).headers).toBeUndefined()
    expect(transformRequestApi(WS_TILE, {}, ORIGEN).headers).toBeUndefined()
  })

  it('ambas rutas del proxy comparten el prefijo protegido', () => {
    expect(NDVI_TILE).toContain(PROXY_BASE)
    expect(ARCGIS_TILE).toContain(PROXY_BASE)
    expect(BASEMAP).not.toContain(PROXY_BASE)
  })
})
