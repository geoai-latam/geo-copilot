import { test, expect } from '@playwright/test'
import {
  mockAuth,
  json, mockQuery, queryResult, fc, waitForMap, waitForStyleLoaded, sendQuery,
  waitForFeatureCount, type Win,
} from './helpers'

/**
 * F0 del plan de plataforma — lo visible para el usuario (V2, determinista).
 *
 * 1. FRT-04 por el camino cableado: con dos capas cargadas, "colorea los lotes"
 *    re-estila la capa NOMBRADA (la que el backend indica en target_layer_id),
 *    no la última añadida.
 * 2. S0.3: el WebSocket ya no recrea sesiones desconocidas. Si el backend perdió
 *    la sesión (reinicio con sesiones en memoria), el frontend abre una nueva
 *    y se reconecta con ella en vez de insistir con el id muerto.
 */

const GRADUATED = {
  symbology_type: 'graduated_colors',
  classification_field: 'valor',
  class_breaks: [
    { min_value: 0, max_value: 30, label: '0-30', color: '#fee5d9' },
    { min_value: 30, max_value: 1000, label: '30+', color: '#de2d26' },
  ],
}

async function mockInitBasico(page: import('@playwright/test').Page) {
  await page.route('**/api/v1/session/', (r) => json(r, { session_id: 'e2e-session' }))
  await page.route('**/health', (r) => json(r, { status: 'ok', components: { semantic_layer: 'healthy' } }))
  await page.route('**/api/v1/metadata/entities', (r) => json(r, { entities: [{ name: 'lotes' }], total: 1 }))
}

test.describe('F0 · suelo firme', () => {
  // F6: sin depender del backend real (que con OIDC pediría login)
  test.beforeEach(async ({ page }) => { await mockAuth(page) })

  test('FRT-04: con dos capas, se re-estila la nombrada y no la última', async ({ page }) => {
    await mockInitBasico(page)
    let objetivo: string | null = null
    await mockQuery(page, (body) => {
      const q = String((body as { query?: string } | null)?.query ?? '')
      if (/colorea/i.test(q)) {
        return queryResult({
          message: 'Coloreé los lotes por valor.',
          intent: 'apply_symbology',
          symbology: GRADUATED,
          target_layer_id: objetivo,
        })
      }
      if (/construcciones/i.test(q)) {
        return queryResult({ message: 'Traje 3 construcciones.', geojson: fc(3) })
      }
      return queryResult({ message: 'Traje 5 lotes.', geojson: fc(5) })
    })

    await page.goto('/')
    await waitForMap(page)
    await waitForStyleLoaded(page)

    await sendQuery(page, 'trae los lotes')
    await waitForFeatureCount(page, 5)
    await sendQuery(page, 'trae las construcciones')
    await waitForFeatureCount(page, 8)

    // Los lotes son la PRIMERA capa (5 features); la última son las construcciones.
    const capas = await page.evaluate(() => (window as Win).__mapTestState?.layers)
    const lotes = capas.find((l: { featureCount: number }) => l.featureCount === 5)
    const construcciones = capas.find((l: { featureCount: number }) => l.featureCount === 3)
    objetivo = lotes.id

    await sendQuery(page, 'colorea los lotes por valor')
    // Re-estilar quita la capa y la vuelve a añadir con OTRO id: se la reconoce
    // por su contenido (5 features), no por el id viejo.
    await page.waitForFunction(
      () => (window as Win).__mapTestState?.layers
        ?.find((l: { featureCount: number }) => l.featureCount === 5)?.rendererKind === 'graduated_colors',
      undefined,
      { timeout: 15_000 },
    )
    const despues = await page.evaluate(() => (window as Win).__mapTestState?.layers)
    expect(despues).toHaveLength(2)
    const construccionesDespues = despues.find((l: { featureCount: number }) => l.featureCount === 3)
    expect(construccionesDespues.id).toBe(construcciones.id) // la otra capa ni se tocó
    expect(construccionesDespues.rendererKind).not.toBe('graduated_colors')
  })

  test('S0.3: si el backend perdió la sesión, se abre otra y se reconecta', async ({ page }) => {
    // 1ª sesión: el backend "la pierde" (GET → 404). 2ª: existe.
    const creadas: string[] = []
    await page.route('**/api/v1/session/', (r) => {
      const id = creadas.length === 0 ? 'e2e-perdida' : 'e2e-nueva'
      creadas.push(id)
      return json(r, { session_id: id })
    })
    await page.route('**/api/v1/session/e2e-perdida', (r) => json(r, { detail: 'Session not found' }, 404))
    await page.route('**/api/v1/session/e2e-nueva', (r) => json(r, { session_id: 'e2e-nueva' }))
    await page.route('**/health', (r) => json(r, { status: 'ok', components: { semantic_layer: 'healthy' } }))
    await page.route('**/api/v1/metadata/entities', (r) => json(r, { entities: [], total: 0 }))

    // El socket de la sesión perdida NO se simula: sin backend (o con el real,
    // que la rechaza con 4404) el handshake falla como en producción. El de la
    // nueva sí responde.
    let conectado!: () => void
    const socketNuevo = new Promise<void>((resolve) => { conectado = resolve })
    await page.routeWebSocket(/\/ws\/e2e-nueva/, (ws) => {
      ws.send(JSON.stringify({ type: 'status', data: { status: 'connected', session_id: 'e2e-nueva' } }))
      ws.onMessage(() => { /* sin eco */ })
      conectado()
    })

    await page.goto('/')
    // Primer reintento a ~1 s del fallo; margen amplio.
    await Promise.race([
      socketNuevo,
      page.waitForTimeout(15_000).then(() => { throw new Error('no se reconectó con la sesión nueva') }),
    ])

    expect(creadas).toEqual(['e2e-perdida', 'e2e-nueva'])
  })
})
