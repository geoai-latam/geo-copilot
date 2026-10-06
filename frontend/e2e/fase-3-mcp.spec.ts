import { test, expect } from '@playwright/test'
import { json, mockInit, waitForLayerKind, waitForMap, waitForStyleLoaded } from './helpers'

/**
 * F3 (V2) — panel de herramientas GENÉRICO de los servicios MCP conectados.
 *
 * El formulario sale del `input_schema` que publica cada servidor: aquí se
 * mockean dos servidores con esquemas distintos y se comprueba que el panel
 * los pinta sin conocerlos, que envía argumentos tipados, que el resultado va
 * al mapa (raster o capa vectorial) y que un servidor caído se dice.
 */

const PNG_1x1 = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M8AAAMBAQDJ/pLvAAAAAElFTkSuQmCC',
  'base64',
)

const TOOLS = [
  {
    server: 'imagery', tool: 'imagery_composite', herramienta: 'imagery__imagery_composite',
    description: 'VER la imagen satelital en color de una zona.', riesgo: 'read', estado: 'disponible',
    geo: { inputs: { aoi_geojson: { accepts: ['geometry', 'layer_ref'] } }, outputs: ['raster_tiles'] },
    input_schema: {
      type: 'object', required: ['aoi_geojson'],
      properties: {
        aoi_geojson: { type: 'object', title: 'Aoi Geojson' },
        combo: { type: 'string', enum: ['true_color', 'false_color', 'agriculture', 'swir'], default: 'true_color', title: 'Combo' },
        date_from: { anyOf: [{ type: 'string' }, { type: 'null' }], default: null, title: 'Date From' },
        max_cloud_pct: { anyOf: [{ type: 'number' }, { type: 'null' }], default: null, title: 'Max Cloud Pct' },
      },
    },
  },
  {
    server: 'hello', tool: 'hello_circle', herramienta: 'hello__hello_circle',
    description: 'Círculo métrico alrededor de un punto.', riesgo: 'read', estado: 'no_disponible',
    geo: { inputs: {}, outputs: ['feature_collection'] },
    input_schema: {
      type: 'object', required: ['lon', 'lat', 'meters'],
      properties: {
        lon: { type: 'number', title: 'Lon' }, lat: { type: 'number', title: 'Lat' },
        meters: { type: 'number', title: 'Meters', minimum: 1 },
      },
    },
  },
]

async function abrirPanel(page: import('@playwright/test').Page) {
  await page.goto('/')
  await waitForMap(page)
  await waitForStyleLoaded(page)
  await page.locator('[title="Herramientas"]').click()
  const panel = page.locator('[data-testid="mcp-tools-panel"]')
  await expect(panel).toBeVisible()
  return panel
}

test.describe('F3 · panel de herramientas MCP', () => {
  test.beforeEach(async ({ page }) => {
    await mockInit(page)
    await page.route('**/api/v1/connections/tools', (r) => json(r, { tools: TOOLS }))
    await page.route('**/api/v1/proxy/mcp/**', (r) =>
      r.fulfill({ status: 200, contentType: 'image/png', body: PNG_1x1 }))
  })

  test('el formulario sale del esquema y el resultado raster va al mapa con su leyenda', async ({ page }) => {
    let enviado: Record<string, unknown> | null = null
    await page.route('**/api/v1/connections/imagery/tools/imagery_composite/run', (r) => {
      enviado = JSON.parse(r.request().postData() ?? '{}')
      return json(r, {
        success: true, message: null,
        facts: { scene: { id: 'S2B_X', datetime: '2026-01-10T15:26:39Z', cloud_pct: 3.1 }, combo: 'false_color' },
        results: {
          visualization: { type: 'imagery' },
          external_imagery: {
            service_url: '/api/v1/proxy/mcp/imagery/tiles-rgb/S2B_X/false_color/{z}/{x}/{y}.png',
            name: 'false_color 2026-01-10', extent: { xmin: -74.2, ymin: 4.5, xmax: -74.0, ymax: 4.7 },
            legend: { type: 'ramp', field: 'NDVI', min: 0.05, max: 0.85 },
          },
        },
      })
    })
    const panel = await abrirPanel(page)

    // Campos generados: selector geo, enum como select, fecha, número.
    const form = panel.locator('[data-testid="mcp-tool-form"]')
    await expect(form.getByLabel('Aoi Geojson')).toHaveValue('viewport')
    await form.getByLabel('Combo').selectOption('false_color')
    await expect(form.getByLabel('Date From')).toHaveAttribute('type', 'date')
    await form.getByLabel('Date From').fill('2026-01-01')
    await form.getByLabel('Max Cloud Pct').fill('15')

    await panel.getByRole('button', { name: 'Ejecutar' }).click()
    const res = page.locator('[data-testid="mcp-tool-result"]')
    await expect(res).toContainText('false_color 2026-01-10')
    await expect(res.getByLabel('Leyenda NDVI')).toContainText('0.85')

    // Argumentos tipados: la zona visible viaja como `viewport` + map_context.
    expect(enviado).not.toBeNull()
    const body = enviado as unknown as { arguments: Record<string, unknown>; map_context: { viewport?: unknown } }
    expect(body.arguments).toEqual({ aoi_geojson: 'viewport', combo: 'false_color', date_from: '2026-01-01', max_cloud_pct: 15 })
    expect(body.map_context.viewport).toBeTruthy()

    await waitForLayerKind(page, 'raster-xyz')
  })

  test('otro servidor, otro formulario; un resultado vectorial entra como capa', async ({ page }) => {
    await page.route('**/api/v1/connections/hello/tools/hello_circle/run', (r) => json(r, {
      success: true, message: null, facts: { radio_m: 250, area_ha: 19.63 },
      results: {
        geojson: { type: 'FeatureCollection', features: [{ type: 'Feature', properties: { radio_m: 250 },
          geometry: { type: 'Polygon', coordinates: [[[-74.08, 4.6], [-74.07, 4.6], [-74.07, 4.61], [-74.08, 4.6]]] } }] },
        layer_name: 'Círculo de 250 m', layer_ref: { id: 'ds_aaaaaaaaaaaaaaaa', name: 'Círculo de 250 m', feature_count: 1 },
      },
    }))
    const panel = await abrirPanel(page)
    await panel.getByLabel('Herramienta').selectOption('hello/hello_circle')
    // el servidor está caído según el hub: se avisa, pero no se bloquea el intento
    await expect(panel.getByRole('status')).toContainText('no está disponible')

    const form = panel.locator('[data-testid="mcp-tool-form"]')
    await panel.getByRole('button', { name: 'Ejecutar' }).click()
    await expect(panel.getByRole('alert')).toContainText('Falta «Lon»')  // requerido, sin default

    await form.getByLabel('Lon').fill('-74.08')
    await form.getByLabel('Lat').fill('4.6')
    await form.getByLabel('Meters').fill('250')
    await panel.getByRole('button', { name: 'Ejecutar' }).click()
    await expect(page.locator('[data-testid="mcp-tool-result"]')).toContainText('1 elementos')
    await page.locator('[title="Capas"]').first().click()
    await expect(page.getByText('Círculo de 250 m').first()).toBeVisible()
  })

  test('el error del servidor se muestra tal cual, sin capa', async ({ page }) => {
    await page.route('**/api/v1/connections/imagery/tools/imagery_composite/run', (r) => json(r, {
      success: false, message: 'el servicio \'imagery\' no respondió (timeout)', facts: {}, results: {},
    }))
    const panel = await abrirPanel(page)
    await panel.getByRole('button', { name: 'Ejecutar' }).click()
    await expect(panel.getByRole('alert')).toContainText('no respondió')
    await expect(page.locator('[data-testid="mcp-tool-result"]')).toHaveCount(0)
  })

  test('sin servicios conectados el panel lo dice', async ({ page }) => {
    await page.unroute('**/api/v1/connections/tools')
    await page.route('**/api/v1/connections/tools', (r) => json(r, { tools: [] }))
    await page.goto('/')
    await waitForMap(page)
    await page.locator('[title="Herramientas"]').click()
    await expect(page.locator('[data-testid="mcp-tools-empty"]')).toContainText('No hay servicios conectados')
  })
})
