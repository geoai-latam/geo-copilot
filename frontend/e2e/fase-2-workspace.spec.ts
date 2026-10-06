import { test, expect, type Page } from '@playwright/test'
import { capa, json, mockInit, mockQuery, queryResult, fc, waitForMap, waitForStyleLoaded, sendQuery, waitForFeatureCount, type Win } from './helpers'

/**
 * FASE 2 — workspace espacial (V2, determinista: backend y WS mockeados).
 *
 * Lo que el usuario nota:
 *  - una capa grande llega como TESELAS del workspace (no GeoJSON) y se ve,
 *    se simboliza y el mapa vuela a su extensión;
 *  - una capa que ya está en el workspace viaja en las consultas siguientes
 *    solo por su id (el backend la lee del workspace de la sesión).
 */

const DS = 'ds_0123456789abcdef'
const TILE_URL = `/api/v1/tiles/ws/e2e-session/${DS}/{z}/{x}/{y}.pbf`
const BBOX: [number, number, number, number] = [-74.2, 4.55, -74.0, 4.75]

function resultadoTeselado(style: Record<string, unknown> | null = null) {
  return queryResult({
    message: 'Traje 60.000 lotes.',
    artifacts: [capa({
      id: DS, name: 'Lotes', featureCount: 60000, geometryType: 'Polygon', bbox: BBOX, style,
      tiles: { url_template: TILE_URL, source_layer: 'dataset', fields: ['lotcodigo', 'area'] },
    })],
  })
}

const clase = (label: string, color: string) => ({ label, color, count: null, min_value: null, max_value: null })

async function capasDeDatos(page: Page) {
  return page.evaluate(() => {
    const map = (window as Win).__mlmap
    return (map.getStyle().layers || [])
      .filter((l: { source?: string; type?: string }) => l.source && l.source !== 'basemap' && l.type !== 'raster')
      .map((l: Record<string, unknown>) => ({
        id: l.id, type: l.type, sourceLayer: l['source-layer'],
        fill: l.type === 'fill' ? map.getPaintProperty(l.id, 'fill-color') : undefined,
        src: map.getSource(l.source as string)?.serialize?.(),
      }))
  })
}

test.describe('Fase 2 — workspace', () => {
  test.beforeEach(async ({ page }) => {
    await mockInit(page)
    // Las teselas del workspace: 204 (vacías) — aquí se verifica el cableado,
    // el contenido de la tesela lo cubre el V3 contra PostGIS real.
    await page.route('**/api/v1/tiles/ws/**', (r) => r.fulfill({ status: 204 }))
  })

  test('una capa grande se dibuja desde las teselas del workspace', async ({ page }) => {
    await mockQuery(page, resultadoTeselado())
    const pedidas: string[] = []
    page.on('request', (r) => { if (r.url().includes('/api/v1/tiles/ws/')) pedidas.push(r.url()) })

    await page.goto('/')
    await waitForMap(page)
    await waitForStyleLoaded(page)
    await sendQuery(page, 'trae todos los lotes')

    await waitForFeatureCount(page, 60000)
    const todas = await capasDeDatos(page)
    // FH.2: el resaltado de la selección va sobre la MISMA fuente de teselas (filtro por fid)
    const resaltado = todas.filter((c: { id: string }) => c.id.includes('-sel-'))
    expect(resaltado.map((c: { type: string }) => c.type).sort()).toEqual(['fill', 'line'])
    const capas = todas.filter((c: { id: string }) => !c.id.includes('-sel-'))
    expect(capas.map((c: { type: string }) => c.type).sort()).toEqual(['fill', 'line'])
    for (const c of resaltado) expect(c.sourceLayer).toBe('dataset')
    for (const c of capas) {
      expect(c.sourceLayer).toBe('dataset')
      expect(c.src.type).toBe('vector')
      // URL ABSOLUTA: el worker de MapLibre no resuelve rutas relativas.
      expect(c.src.tiles[0]).toMatch(new RegExp(`^https?://[^/]+${TILE_URL.replace(/[{}.]/g, '\\$&')}$`))
    }
    // MapLibre pide teselas reales del workspace de ESA sesión.
    await expect.poll(() => pedidas.length, { timeout: 15_000 }).toBeGreaterThan(0)
    expect(pedidas[0]).toMatch(/\/api\/v1\/tiles\/ws\/e2e-session\/ds_0123456789abcdef\/\d+\/\d+\/\d+\.pbf$/)
    // El mapa vuela a la extensión declarada (no hay features en memoria).
    await expect.poll(async () => page.evaluate(() => {
      const c = (window as Win).__mlmap.getCenter()
      return [c.lng, c.lat]
    }), { timeout: 10_000 }).toEqual([expect.closeTo(-74.1, 1), expect.closeTo(4.65, 1)])
  })

  test('la capa teselada se simboliza con la misma expresión que una GeoJSON', async ({ page }) => {
    await mockQuery(page, resultadoTeselado({
      symbology_type: 'unique_values',
      classification_field: 'uso',
      class_breaks: [clase('res', '#ff0000'), clase('com', '#00ff00')],
    }))
    await page.goto('/')
    await waitForMap(page)
    await waitForStyleLoaded(page)
    await sendQuery(page, 'trae todos los lotes coloreados por uso')
    await waitForFeatureCount(page, 60000)

    const fill = (await capasDeDatos(page)).find((c: { type: string }) => c.type === 'fill')
    expect(fill.sourceLayer).toBe('dataset')
    // Expresión data-driven sobre la propiedad (no un color fijo).
    expect(JSON.stringify(fill.fill)).toContain('uso')
    expect(JSON.stringify(fill.fill)).toContain('#ff0000')
  })

  test('el panel de capas muestra el conteo real de la capa teselada', async ({ page }) => {
    await mockQuery(page, resultadoTeselado())
    await page.goto('/')
    await waitForMap(page)
    await waitForStyleLoaded(page)
    await sendQuery(page, 'trae todos los lotes')
    await waitForFeatureCount(page, 60000)
    await page.locator('[title="Capas"]').click()
    // con separador de miles (es-CO): 60.000
    await expect(page.locator('.layer-item .layer-sub').first()).toContainText(/60[.\u00a0,]?000 features/)
  })

  test('la capa del workspace viaja en la consulta siguiente solo por su id', async ({ page }) => {
    const cuerpos: Array<Record<string, unknown>> = []
    let turno = 0
    await mockQuery(page, (body) => {
      cuerpos.push(body as Record<string, unknown>)
      turno += 1
      return turno === 1
        ? queryResult({ message: 'Traje 5 lotes.', geojson: fc(5), layerId: DS, layerName: 'Lotes' })
        : queryResult({ message: 'Listo.' })
    })
    await page.goto('/')
    await waitForMap(page)
    await waitForStyleLoaded(page)
    await sendQuery(page, 'trae 5 lotes')
    await waitForFeatureCount(page, 5)
    await sendQuery(page, '¿cuál es el área total?')
    await expect.poll(() => cuerpos.length).toBe(2)

    const capa = ((cuerpos[1].map_context as { layers: Array<Record<string, unknown>> }).layers)[0]
    expect(capa.dataset_id).toBe(DS)
    expect(capa.data).toBeUndefined()          // la geometría NO viaja
    expect(capa.fields).toEqual(['id', 'uso', 'valor'])  // los metadatos sí
  })
})

test.describe('Fase 2 — recargar la página (E2.4)', () => {
  test('la pestaña vuelve a su sesión y restaura sus capas del workspace con su estilo', async ({ page }) => {
    await mockInit(page)
    const sesionesCreadas: number[] = []
    page.on('request', (r) => {
      if (r.url().endsWith('/api/v1/session/') && r.method() === 'POST') sesionesCreadas.push(1)
    })
    // la sesión sigue viva en el backend
    await page.route('**/api/v1/session/e2e-session', (r) => json(r, { session_id: 'e2e-session' }))
    const pedidas: string[] = []
    await page.route('**/api/v1/workspace/**', (r) => {
      pedidas.push(new URL(r.request().url()).pathname)
      return json(r, { layer_ref: { id: DS, name: 'Lotes', feature_count: 5 }, geojson: fc(5), tiles: null })
    })
    await mockQuery(page, () => {
      return queryResult({
        message: 'Traje 5 lotes.', geojson: fc(5), symbology: { fill: { color: '#ff00ff' } },
        layerId: DS, layerName: 'trae 5 lotes',
      })
    })

    await page.goto('/')
    await waitForMap(page)
    await waitForStyleLoaded(page)
    await sendQuery(page, 'trae 5 lotes')
    await waitForFeatureCount(page, 5)
    expect(sesionesCreadas).toHaveLength(1)

    await page.reload()
    await waitForMap(page)
    await waitForFeatureCount(page, 5)
    // misma sesión (no se creó otra) y la capa volvió desde el workspace
    expect(sesionesCreadas).toHaveLength(1)
    expect(pedidas).toEqual([`/api/v1/workspace/e2e-session/datasets/${DS}/capa`])
    const capas = await page.evaluate(() => (window as Win).__mapTestState.layers)
    expect(capas).toHaveLength(1)
    expect(capas[0].name).toBe('trae 5 lotes')
    expect(capas[0].color).toBe('#ff00ff')  // conserva la simbología
  })
})
