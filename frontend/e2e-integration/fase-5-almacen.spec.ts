import { test, expect, type Page } from '@playwright/test'

/**
 * FASE 5 (T5.4) — el adaptador `tabular_geo` contra el stack REAL (V3): las sedes educativas
 * llegan por `almacen-demo`, un MCP TABULAR a imagen del de Snowflake (filas en texto JSON, sin
 * contrato geo). El LLM declara qué columna es la geometría; el núcleo la valida y la materializa.
 * Mismas referencias que fase-5-postgis (psql): 41 sedes / 11 oficiales en Soacha y 3 lotes del
 * catastro a < 200 m. Requiere `almacen` activo y `sql` desactivado en mcp_servers.local.yaml.
 *
 * Correr: E2E_REAL_URL=http://localhost:5173 npx playwright test --config playwright.integration.config.ts fase-5-almacen
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

test.describe('Integración REAL — F5 MCP tabular (adaptador tabular_geo)', () => {
  test.setTimeout(420_000)

  test('E5.3: filas de un MCP tabular → capa, y cruce con el catastro', async ({ page }) => {
    await page.goto('/')
    await page.evaluate(() => window.sessionStorage.clear())
    await page.reload()
    await waitForMap(page)

    // E5.1a — solo está en la BD conectada: no se busca en portales ni se inventa con otra tabla
    await sendQuery(page, '¿cuántas sedes educativas hay en Soacha y cuántas son oficiales?')
    const a = await esperarTurno(page, 0)
    expect(a).toMatch(/\b41\b/)
    expect(a).toMatch(/\b11\b/)

    // E5.1b — la capa de la BD conectada en el mapa y el cruce con el catastro de la interna
    await sendQuery(page, 'muéstrame en el mapa las sedes educativas de Soacha')
    await esperarTurno(page, 1)
    await expect.poll(() => page.evaluate(() => ((window as Win).__mapTestState?.layers ?? [])
      .some((l: any) => l.featureCount === 41)), { timeout: 30_000 }).toBe(true)
    await sendQuery(page, '¿cuántos lotes del catastro de Bogotá hay a menos de 200 m de alguna de esas sedes?')
    const b = await esperarTurno(page, 2)
    expect(b, b).toMatch(/\b3\b/)
    expect(b).not.toMatch(/\b0 lotes\b/)
  })
})
