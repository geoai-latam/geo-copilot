import { test, expect, type Page } from '@playwright/test'
import { cabecerasDe } from './identidad'

/**
 * FASE 4 — frontend genérico contra el stack REAL (V3): LLM real, PostGIS, imagery-mcp.
 *
 * Nada se mockea y nada se le dicta al agente: se le pide lo que pediría un
 * usuario y se comprueba el resultado contra una REFERENCIA INDEPENDIENTE —
 *   E4.1: el conteo de `catastro.lotes` de la manzana 004503001 en la BD (15);
 *   E4.5: el NDVI que mide el motor directamente (tool `imagery_zonal_stats` con
 *         la referencia `punto`) en el MISMO punto que marcó el usuario.
 * Las aserciones toleran la redacción del LLM, no el dato.
 *
 * Correr (stack arriba, `npm run dev` en 5173):
 *   E2E_REAL_URL=http://localhost:5173 npx playwright test --config playwright.integration.config.ts fase-4-frontend
 */

type Win = any

const LOTES_MANZANA_004503001 = 15 // SELECT count(*) FROM catastro.lotes WHERE manzcodigo='004503001'

async function waitForMap(page: Page): Promise<void> {
  await page.waitForFunction(
    () => (window as Win).__mapTestState?.engine === 'maplibre' && !!(window as Win).__mlmap,
    undefined,
    { timeout: 30_000 },
  )
}

async function sendQuery(page: Page, text: string): Promise<void> {
  const input = page.getByPlaceholder(/Pregunta en lenguaje natural/i)
  await expect(input).toBeEnabled({ timeout: 60_000 })
  await input.fill(text)
  await input.press('Enter')
}

/** Espera el final del turno aprobando el SQL si el agente lo pide (HITL). */
async function esperarTurno(page: Page): Promise<string> {
  const msg = page.locator('.msg-assist').last()
  const hitl = page.getByRole('button', { name: /Aprobar y ejecutar/ })
  const deadline = Date.now() + 170_000
  while (Date.now() < deadline) {
    if (await hitl.isVisible().catch(() => false)) await hitl.click()
    const texto = (await msg.textContent()) ?? ''
    const cargando = await page.getByPlaceholder(/Pregunta en lenguaje natural/i).isDisabled()
    if (texto.trim() && !cargando) return texto
    await page.waitForTimeout(1_000)
  }
  throw new Error('el turno no terminó')
}

test.describe('Integración REAL — F4 frontend genérico', () => {
  test.setTimeout(240_000)

  test('E4.1: consulta analítica → capa, tabla y gráfico a la vez, con los lotes reales', async ({ page }) => {
    await page.goto('/')
    await waitForMap(page)
    await sendQuery(page,
      'trae los lotes de la manzana 004503001 y hazme una tabla y un gráfico de barras con el área de cada lote')
    const respuesta = await esperarTurno(page)
    expect(respuesta).not.toMatch(/^Error:/)

    // Capa: exactamente los lotes de la manzana, dibujados.
    await expect.poll(() => page.evaluate(() => (window as Win).__mapTestState?.featureCount), { timeout: 20_000 })
      .toBe(LOTES_MANZANA_004503001)

    // Panel: tabla y gráfico visibles a la vez, con el mapa al lado.
    const panel = page.getByTestId('panel-resultados')
    await expect(panel).toBeVisible()
    const chart = panel.getByTestId('artefacto-chart')
    await expect(chart.locator('svg.recharts-surface').first()).toBeVisible()
    // Solo las barras: `.recharts-rectangle` también es el cursor del tooltip si el ratón queda encima.
    await expect.poll(() => chart.locator('.recharts-bar-rectangle').count(), { timeout: 10_000 })
      .toBe(LOTES_MANZANA_004503001)
    // La tabla lleva el área (la cifra detrás de las barras), no solo atributos.
    const tabla = panel.getByTestId('artefacto-table').first()
    await expect(tabla).toContainText(/[AÁ]rea/i)
    await expect(tabla).toContainText(`${LOTES_MANZANA_004503001} filas`)
    await expect(page.locator('.map-container')).toBeVisible()
    const cajaPanel = (await panel.boundingBox())!
    const cajaMapa = (await page.locator('.map-container').boundingBox())!
    expect(cajaPanel.x - cajaMapa.x).toBeGreaterThan(200)
  })

  test('E4.5: con el NDVI cargado, «¿qué valor tiene el NDVI aquí?» da el valor del punto marcado', async ({ page, request }) => {
    const mapContexts: Array<Record<string, any>> = []
    page.on('request', (r) => {
      if (r.url().includes('/api/v1/query/') && r.method() === 'POST') {
        try { mapContexts.push(JSON.parse(r.postData() ?? '{}').map_context) } catch { /* no es JSON */ }
      }
    })
    await page.goto('/')
    await waitForMap(page)
    // Usaquén: zona con escena despejada el 2026-08-10 (1,3 % de nubes).
    await page.evaluate(() => (window as Win).__mlmap.jumpTo({ center: [-74.045, 4.735], zoom: 14 }))
    await page.waitForFunction(() => !(window as Win).__mlmap.isMoving())

    // El NDVI lo carga el usuario desde el panel genérico (no el test por detrás).
    await page.locator('[title="Herramientas"]').click()
    const tools = page.locator('[data-testid="mcp-tools-panel"]')
    await tools.getByLabel('Herramienta').selectOption('imagery/imagery_ndvi')
    const form = tools.locator('[data-testid="mcp-tool-form"]')
    await form.getByLabel('Date From').fill('2026-08-01')
    await form.getByLabel('Date To').fill('2026-08-31')
    await tools.getByRole('button', { name: 'Ejecutar' }).click()
    await expect(tools.locator('[data-testid="mcp-tool-result"]')).toBeVisible({ timeout: 150_000 })
    await page.waitForFunction(() => (window as Win).__mapTestState?.layers?.some((l: { kind: string }) => l.kind === 'raster-xyz'),
      undefined, { timeout: 20_000 })
    await page.locator('[title="Herramientas"]').click()

    // El usuario marca un punto con el ratón: uno despejado en la escena del 2026-08-10
    // (el 24 % de la zona está bajo nube; ahí la respuesta correcta es otra — rama de abajo).
    await page.waitForFunction(() => !(window as Win).__mlmap.isMoving())
    const px = await page.evaluate(() => {
      const map = (window as Win).__mlmap
      const r = map.getCanvas().getBoundingClientRect()
      const p = map.project([-74.045, 4.735])
      return { x: r.left + p.x, y: r.top + p.y }
    })
    await page.mouse.click(px.x, px.y)
    await expect(page.getByTestId('punto-marcado')).toBeVisible()

    await sendQuery(page, '¿qué valor tiene el NDVI aquí?')
    const respuesta = await esperarTurno(page)

    // Referencia independiente: el motor, en el punto EXACTO que viajó en el map_context.
    const pt = mapContexts.at(-1)?.clicked_point
    expect(pt?.lon).toBeGreaterThan(-75)
    const sonda = await request.post('/api/v1/connections/imagery/tools/imagery_zonal_stats/run', {
      headers: await cabecerasDe('ana'),
      // sesión nueva en cada pasada: una fija quedaba de otro dueño (la del día que se creó) → 404
      data: { session_id: `e2e-f4-sonda-${Date.now()}`, arguments: { features_geojson: 'punto', date_from: '2026-08-01', date_to: '2026-08-31' },
              map_context: { clicked_point: pt } },
    })
    expect(sonda.ok()).toBe(true)
    const cuerpo = await sonda.json()
    const ref = (cuerpo.results.geojson?.features?.[0]?.properties?.ndvi_mean ?? null) as number | null
    const escena = String(cuerpo.facts.scene.datetime).slice(0, 10)
    console.log(`[E4.5] punto ${pt.lon.toFixed(5)},${pt.lat.toFixed(5)} · sonda ${ref} (${escena}) · agente: ${respuesta}`)

    if (ref === null) {
      // El motor lo dejó en `skipped`.
      // Píxel sin valor (nube/agua): lo correcto es decirlo, no inventar un número.
      expect(respuesta).toMatch(/nube|agua|sombra|sin (valor|p[ií]xel)|no hay/i)
      return
    }
    // El agente da el valor del PUNTO (no la media de la zona): un número a ±0,01 de la sonda.
    const numeros = [...respuesta.matchAll(/-?\d+[.,]\d+/g)].map((m) => parseFloat(m[0].replace(',', '.')))
    expect(numeros.some((n) => Math.abs(n - ref) <= 0.01), `respuesta «${respuesta}» vs sonda ${ref} (${escena})`).toBe(true)
  })
})
