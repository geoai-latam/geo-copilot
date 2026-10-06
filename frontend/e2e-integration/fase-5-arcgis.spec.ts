import { test, expect, type Page } from '@playwright/test'

/**
 * FASE 5 (T5.2) — el discovery de ArcGIS a través del servidor MCP de ArcGIS, contra el stack REAL
 * (V3) y servicios ArcGIS públicos reales (IDE de Cundinamarca). Referencias tomadas del servicio
 * con un conteo independiente (shapely), no del agente:
 *
 *   E5.2a: «carga esta capa: <URL de Equipamiento Cundinamarca>» → capa de 561 puntos (todos).
 *   E5.2b: la capa de municipios (116, Adultos_Mayores_60) y «¿cuántos puntos caen en cada
 *          municipio? los 3 con más» → Guaduas 35 (luego San Juan de Río Seco y Caparrapí, 26).
 *
 * Correr (stack arriba con arcgis-mcp, `npm run dev` en 5173; necesita Internet):
 *   E2E_REAL_URL=http://localhost:5173 npx playwright test --config playwright.integration.config.ts fase-5-arcgis
 */

type Win = any

async function waitForMap(page: Page): Promise<void> {
  await page.waitForFunction(() => (window as Win).__mapTestState?.engine === 'maplibre' && !!(window as Win).__mlmap,
                             undefined, { timeout: 30_000 })
}

async function sendQuery(page: Page, text: string): Promise<void> {
  const input = page.getByPlaceholder(/Pregunta en lenguaje natural/i)
  await expect(input).toBeEnabled({ timeout: 60_000 })
  await input.fill(text)
  await input.press('Enter')
}

/** Espera el final del turno aprobando el SQL si el agente lo pide (HITL, como el usuario). */
async function esperarTurno(page: Page, previos: number): Promise<string> {
  const hitl = page.getByRole('button', { name: /Aprobar y ejecutar/ })
  const deadline = Date.now() + 240_000
  while (Date.now() < deadline) {
    if (await hitl.isVisible().catch(() => false)) await hitl.click()
    const msgs = page.locator('.msg-assist')
    const texto = (await msgs.count()) > previos ? ((await msgs.last().textContent()) ?? '') : ''
    const cargando = await page.getByPlaceholder(/Pregunta en lenguaje natural/i).isDisabled()
    if (texto.trim() && !cargando && !/Procesando/.test(texto)) return texto
    await page.waitForTimeout(1_000)
  }
  throw new Error('el turno no terminó')
}

const BASE = 'https://services7.arcgis.com/lsxbLWF2l19Rmhqj/arcgis/rest/services'

async function capaCon(page: Page, n: number): Promise<boolean> {
  return page.evaluate((k) => ((window as Win).__mapTestState?.layers ?? []).some((l: any) => l.featureCount === k), n)
}

test.describe('Integración REAL — F5 ArcGIS vía MCP', () => {
  test.setTimeout(420_000)

  test('E5.2: cargar capas ArcGIS por el servidor MCP y cruzarlas', async ({ page }) => {
    await page.goto('/')
    await page.evaluate(() => window.sessionStorage.clear())
    await page.reload()
    await waitForMap(page)

    await sendQuery(page, `carga esta capa: ${BASE}/Equipamiento_Cundinamarca/FeatureServer/0`)
    await esperarTurno(page, 0)
    await expect.poll(() => capaCon(page, 561), { timeout: 60_000 }).toBe(true)

    await sendQuery(page, `carga esta capa: ${BASE}/Adultos_Mayores_60/FeatureServer/0`)
    await esperarTurno(page, 1)
    await expect.poll(() => capaCon(page, 116), { timeout: 60_000 }).toBe(true)

    await sendQuery(page, '¿cuántos puntos de Equipamiento Cundinamarca caen dentro de cada municipio de la capa '
      + 'de municipios? dime los 3 municipios con más')
    const b = await esperarTurno(page, 2)
    expect(b, b).toMatch(/Guaduas/)
    expect(b, b).toMatch(/\b35\b/)
  })
})
