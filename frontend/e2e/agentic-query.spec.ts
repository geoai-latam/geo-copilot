import { test, expect, type Page, type Route } from '@playwright/test'
import { mockAuth, queryResult } from './helpers'

/**
 * TST-01 — E2E del camino AGÉNTICO usuario → query → respuesta → capa en el mapa.
 *
 * El único E2E previo (map.spec.ts) validaba el motor de mapa "sin tocar el
 * LLM", así que el flujo real (escribir una consulta y ver la capa renderizada)
 * no tenía ningún E2E. Aquí NO se llama al LLM real: se interceptan las llamadas
 * del backend con `page.route` y se sirve una respuesta determinista con
 * geojson. Se afirma sobre el oráculo instrumentado
 * `window.__mapTestState.featureCount` (sin esperas fijas).
 */


type Win = any

const GEOJSON_3 = {
  type: 'FeatureCollection',
  features: [
    { type: 'Feature', geometry: { type: 'Point', coordinates: [-74.07, 4.71] }, properties: { id: 1, uso: 'residencial' } },
    { type: 'Feature', geometry: { type: 'Point', coordinates: [-74.08, 4.72] }, properties: { id: 2, uso: 'comercial' } },
    { type: 'Feature', geometry: { type: 'Point', coordinates: [-74.09, 4.70] }, properties: { id: 3, uso: 'industrial' } },
  ],
}

/** Mockea el init de la app + el endpoint de query con una respuesta con geojson. */
async function mockBackend(page: Page, queryResponse: unknown): Promise<void> {
  const json = (route: Route, body: unknown) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })

  await mockAuth(page)
  await page.route('**/api/v1/session/', (r) => json(r, { session_id: 'e2e-session' }))
  await page.route('**/health', (r) => json(r, { status: 'ok', components: { semantic_layer: 'healthy' } }))
  await page.route('**/api/v1/metadata/entities', (r) => json(r, { entities: [{ name: 'lotes' }], total: 1 }))
  await page.route('**/api/v1/query/', (r) => json(r, queryResponse))
}

async function waitForMap(page: Page): Promise<void> {
  await page.waitForFunction(
    () => (window as unknown as Win).__mapTestState?.engine === 'maplibre' && !!(window as unknown as Win).__mlmap,
    undefined,
    { timeout: 20_000 },
  )
}

/**
 * El estilo debe estar CARGADO antes de añadir capas: el nodo sincroniza vía
 * `if (map.isStyleLoaded()) run()` y si no lo está registra un `once('load')`
 * que — tras el load inicial — ya no dispara. Esperar aquí evita esa carrera.
 */
async function waitForStyleLoaded(page: Page): Promise<void> {
  await page.waitForFunction(
    () => (window as unknown as Win).__mlmap?.isStyleLoaded?.() === true,
    undefined,
    { timeout: 15_000 },
  )
}

test.describe('Camino agéntico — query → capa en el mapa (TST-01)', () => {
  test('una consulta con resultados geográficos renderiza la capa (featureCount)', async ({ page }) => {
    await mockBackend(page, queryResult({ message: 'Traje 3 lotes de Bogotá.', geojson: GEOJSON_3 }))

    await page.goto('/')
    await waitForMap(page)
    await waitForStyleLoaded(page)

    // El textarea se habilita cuando hay sessionId (sessionApi.create mockeado).
    const input = page.getByPlaceholder(/Pregunta en lenguaje natural/i)
    await expect(input).toBeEnabled({ timeout: 10_000 })
    await input.fill('trae 3 lotes de Bogotá')
    await input.press('Enter')

    // ORÁCULO: la capa se renderizó cuando featureCount refleja las 3 features.
    // Condición, no espera fija.
    await page.waitForFunction(
      () => (window as unknown as Win).__mapTestState?.featureCount === 3,
      undefined,
      { timeout: 15_000 },
    )

    const state = await page.evaluate(() => (window as unknown as Win).__mapTestState)
    expect(state.featureCount).toBe(3)
    expect(state.engine).toBe('maplibre')

    // La narración del asistente aparece en el chat (camino completo). Texto
    // EXACTO del párrafo (evita chocar con badges tipo "N resultados").
    await expect(page.getByText('Traje 3 lotes de Bogotá.')).toBeVisible()
  })

  test('una consulta sin geometría no rompe el mapa (featureCount se mantiene)', async ({ page }) => {
    // Conteo/agregación: sin geojson pero con el escalar en el canal de tabla.
    await mockBackend(page, queryResult({
      message: 'Hay 933473 lotes en total.',
      visualizations: [{ type: 'table', data: [{ total_lotes: 933473 }] }],
    }))

    await page.goto('/')
    await waitForMap(page)

    const input = page.getByPlaceholder(/Pregunta en lenguaje natural/i)
    await expect(input).toBeEnabled({ timeout: 10_000 })
    await input.fill('¿cuántos lotes hay?')
    await input.press('Enter')

    // (1) El conteo se narra en el chat (texto exacto del párrafo).
    await expect(page.getByText('Hay 933473 lotes en total.')).toBeVisible({ timeout: 15_000 })

    // (2) El VALOR fluye al artefacto tabla, no solo al chat: el panel de
    // resultados se abre SOLO (turno con tabla) y la muestra. Si ese cableado
    // se rompe → rojo.
    const panel = page.getByTestId('panel-resultados')
    await expect(panel).toBeVisible()
    // Separador de miles según locale (headless: coma) → regex tolerante.
    await expect(panel.locator('.geo-table-container')).toContainText(/933[.,\u00a0]?473/)

    // (3) Invariante real: una consulta SIN geometría NO crea una capa fantasma
    // en el mapa (featureCount sigue en 0), y el mapa sigue montado.
    const state = await page.evaluate(() => (window as unknown as Win).__mapTestState)
    expect(state.engine).toBe('maplibre')
    expect(state.featureCount).toBe(0)
  })
})
