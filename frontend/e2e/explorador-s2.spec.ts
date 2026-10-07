import { test, expect } from '@playwright/test'
import { json, mockInit, waitForFeatureCount, waitForLayerKind, waitForMap, waitForStyleLoaded } from './helpers'

/**
 * Explorador Sentinel-2: el panel usa las MISMAS tools que el agente (imagery-mcp).
 * buscar en la vista → teselas MGRS coloreadas en el mapa → escenas de una tesela con
 * miniatura → ver una escena (color) con su scene_id.
 */

const PNG_1x1 = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M8AAAMBAQDJ/pLvAAAAAElFTkSuQmCC',
  'base64',
)

const poligono = (w: number, s: number, e: number, n: number) => ({
  type: 'Polygon', coordinates: [[[w, s], [e, s], [e, n], [w, n], [w, s]]],
})

const GRID = {
  success: true, message: null, facts: { teselas: 2, escenas: 50 },
  results: {
    geojson: {
      type: 'FeatureCollection',
      features: [
        { type: 'Feature', geometry: poligono(-74.5, 3.6, -73.5, 4.5), properties: { tile: '18NWK', escenas: 25, nubes_min: 5.44, nubes_mediana: 82.4, cobertura_max: 100, mejor_escena: 'S2B_T18NWK_20260810T152745_L2A', mejor_fecha: '2026-08-10' } },
        { type: 'Feature', geometry: poligono(-74.5, 4.5, -73.5, 5.4), properties: { tile: '18NWL', escenas: 25, nubes_min: 1.31, nubes_mediana: 69.4, cobertura_max: 100, mejor_escena: 'S2B_T18NWL_20260810T152745_L2A', mejor_fecha: '2026-08-10' } },
      ],
    },
    layer_name: 'Imágenes Sentinel-2', layer_ref: { id: 'ds_s2grid00000000aa', name: 'Imágenes Sentinel-2', feature_count: 2 },
  },
}

const ESCENAS = {
  success: true, message: null, facts: { escenas: 2 },
  results: {
    data: { results: [
      { id: 'S2B_T18NWL_20260810T152745_L2A', tile: '18NWL', fecha: '2026-08-10T15:31:43Z', nubes: 1.31, cobertura: 100, plataforma: 'sentinel-2b', miniatura: 'https://imagenes.test/a/L2A_PVI.jpg' },
      { id: 'S2C_T18NWL_20260914T152651_L2A', tile: '18NWL', fecha: '2026-09-14T15:31:43Z', nubes: 42.02, cobertura: 100, plataforma: 'sentinel-2c', miniatura: 'https://imagenes.test/b/L2A_PVI.jpg' },
    ] },
    visualization: { type: 'table' },
  },
}

test.describe('Explorador Sentinel-2', () => {
  test.beforeEach(async ({ page }) => {
    await mockInit(page)
    await page.route('https://imagenes.test/**', (r) => r.fulfill({ status: 200, contentType: 'image/png', body: PNG_1x1 }))
    await page.route('**/api/v1/proxy/mcp/**', (r) => r.fulfill({ status: 200, contentType: 'image/png', body: PNG_1x1 }))
  })

  test('buscar → teselas en el mapa → escenas con miniatura → ver en color con su scene_id', async ({ page }) => {
    const enviados: Record<string, { arguments: Record<string, unknown> }> = {}
    const tool = (nombre: string, cuerpo: unknown) =>
      page.route(`**/api/v1/connections/imagery/tools/${nombre}/run`, (r) => {
        enviados[nombre] = JSON.parse(r.request().postData() ?? '{}')
        return json(r, cuerpo)
      })
    await tool('imagery_catalog_grid', GRID)
    await tool('imagery_catalog_scenes', ESCENAS)
    await tool('imagery_composite', {
      success: true, message: null, facts: { scene: { id: 'S2B_T18NWL_20260810T152745_L2A' } },
      results: {
        visualization: { type: 'imagery' },
        external_imagery: {
          service_url: '/api/v1/proxy/mcp/imagery/tiles-rgb/S2B_T18NWL_20260810T152745_L2A/true_color/{z}/{x}/{y}.png',
          name: 'true_color 2026-08-10', extent: { xmin: -74.3, ymin: 4.5, xmax: -73.9, ymax: 4.9 },
        },
      },
    })

    await page.goto('/')
    await waitForMap(page)
    await waitForStyleLoaded(page)
    await page.locator('[title="Sentinel-2"]').click()
    const panel = page.locator('[data-testid="explorador-s2"]')
    await expect(panel).toBeVisible()

    await panel.getByLabel('Nubes máximas').fill('60')
    await panel.getByTestId('s2-buscar').click()

    // Teselas: la más despejada primero, y la cuadrícula entra al mapa como capa.
    const teselas = panel.getByTestId('s2-teselas')
    await expect(teselas).toContainText('2 teselas')
    await expect(teselas.locator('.imgp-scene').first()).toContainText('18NWL')
    await waitForFeatureCount(page, 2)
    expect(enviados.imagery_catalog_grid.arguments).toMatchObject({ aoi_geojson: 'viewport', max_cloud_pct: 60, min_coverage_pct: 10 })

    await teselas.locator('.imgp-scene').first().click()
    const escenas = panel.getByTestId('s2-escenas')
    await expect(escenas).toContainText('Tesela 18NWL')
    await expect(escenas.locator('.s2-escena')).toHaveCount(2)
    await expect(escenas.locator('img').first()).toHaveAttribute('src', 'https://imagenes.test/a/L2A_PVI.jpg')
    expect(enviados.imagery_catalog_scenes.arguments).toMatchObject({ tile: '18NWL', max_cloud_pct: 60, order: 'menos_nubes' })
    await expect(escenas.locator('.s2-id').first()).toHaveText('S2B_T18NWL_20260810T152745_L2A')

    // Cambiar el orden vuelve a pedir las escenas con ese orden.
    await escenas.getByLabel('Ordenar escenas').selectOption('reciente')
    await expect.poll(() => enviados.imagery_catalog_scenes.arguments.order).toBe('reciente')
    await expect(escenas.locator('.s2-escena')).toHaveCount(2)

    await escenas.getByTestId('s2-ver-true_color').first().click()
    await waitForLayerKind(page, 'raster-xyz')
    const ver = enviados.imagery_composite.arguments
    expect(ver).toMatchObject({ scene_id: 'S2B_T18NWL_20260810T152745_L2A', combo: 'true_color' })
    expect((ver.aoi_geojson as { type: string }).type).toBe('Polygon')
  })

  test('el cajón recuerda su estado: la búsqueda y la escena nuevas sustituyen, el relleno no tiñe la escena', async ({ page }) => {
    const raster = (combo: string) => ({
      success: true, message: null, facts: {},
      results: {
        visualization: { type: 'imagery' },
        external_imagery: {
          service_url: `/api/v1/proxy/mcp/imagery/tiles-rgb/S2B_T18NWL_20260810T152745_L2A/${combo}/{z}/{x}/{y}.png`,
          name: `${combo} 2026-08-10`, extent: { xmin: -74.3, ymin: 4.5, xmax: -73.9, ymax: 4.9 },
        },
      },
    })
    await page.route('**/api/v1/connections/imagery/tools/imagery_catalog_grid/run', (r) => json(r, GRID))
    await page.route('**/api/v1/connections/imagery/tools/imagery_catalog_scenes/run', (r) => json(r, ESCENAS))
    await page.route('**/api/v1/connections/imagery/tools/imagery_composite/run', (r) =>
      json(r, raster(JSON.parse(r.request().postData() ?? '{}').arguments.combo)))

    // Opacidad del relleno de la cuadrícula y cuántos rasters hay en el mapa.
    const estado = () => page.evaluate(() => {
      const w = window as unknown as { __mapTestState?: { layers?: { id: string; kind?: string }[] }; __mlmap?: { getLayer: (id: string) => unknown; getPaintProperty: (id: string, p: string) => unknown; getFilter: (id: string) => unknown } }
      const capas = w.__mapTestState?.layers ?? []
      const grid = capas.find((l) => w.__mlmap?.getLayer(`${l.id}-fill`))
      return {
        relleno: grid ? w.__mlmap?.getPaintProperty(`${grid.id}-fill`, 'fill-opacity') : null,
        // El filtro de la capa de resaltado: ['boolean', false] = nada resaltado.
        resaltado: grid ? JSON.stringify(w.__mlmap?.getFilter(`${grid.id}-sel-fill`)) !== '["boolean",false]' : null,
        rasters: capas.filter((l) => l.kind === 'raster-xyz').length,
        cuadriculas: capas.filter((l) => w.__mlmap?.getLayer(`${l.id}-fill`)).length,
      }
    })

    await page.goto('/')
    await waitForMap(page)
    await waitForStyleLoaded(page)
    await page.locator('[title="Sentinel-2"]').click()
    const panel = page.locator('[data-testid="explorador-s2"]')
    await panel.getByTestId('s2-buscar').click()
    await waitForFeatureCount(page, 2)
    await panel.getByTestId('s2-teselas').locator('.imgp-scene').first().click()
    const escenas = panel.getByTestId('s2-escenas')
    await expect.poll(async () => (await estado()).resaltado).toBe(true)     // la tesela abierta, resaltada
    await escenas.getByTestId('s2-ver-true_color').first().click()
    await waitForLayerKind(page, 'raster-xyz')
    await expect.poll(estado).toMatchObject({ relleno: 0, rasters: 1, resaltado: false })

    await escenas.getByTestId('s2-ver-false_color').first().click()
    await expect.poll(async () => (await estado()).rasters).toBe(1)      // sustituye, no apila

    await escenas.getByRole('button', { name: '← teselas' }).click()
    await expect.poll(async () => (await estado()).relleno).toBe(0.45)

    // Cambiar de cajón y volver: la búsqueda sigue ahí.
    await page.locator('[title="Capas"]').click()
    await page.locator('[title="Sentinel-2"]').click()
    await expect(panel.getByTestId('s2-teselas')).toContainText('2 teselas')

    // La búsqueda siguiente sustituye la cuadrícula (no quedan dos).
    await panel.getByTestId('s2-buscar').click()
    await expect.poll(async () => (await estado()).cuadriculas).toBe(1)
    await waitForFeatureCount(page, 2)

    // Recargar la pestaña: el cajón sigue sabiendo qué buscó (y qué capas son suyas).
    await page.reload()
    await waitForMap(page)
    await page.locator('[title="Sentinel-2"]').click()
    await expect(panel.getByTestId('s2-teselas')).toContainText('2 teselas')
  })

  test('el error del servicio se dice tal cual', async ({ page }) => {
    await page.route('**/api/v1/connections/imagery/tools/imagery_catalog_grid/run', (r) => json(r, {
      success: false, message: 'el área pasa del tamaño de un país; acota la zona', facts: {}, results: {},
    }))
    await page.goto('/')
    await waitForMap(page)
    await page.locator('[title="Sentinel-2"]').click()
    const panel = page.locator('[data-testid="explorador-s2"]')
    await panel.getByTestId('s2-buscar').click()
    await expect(panel.getByRole('alert')).toContainText('tamaño de un país')
    await expect(panel.getByTestId('s2-teselas')).toHaveCount(0)
  })
})
