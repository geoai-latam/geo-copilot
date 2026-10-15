import * as fs from 'node:fs'
import * as path from 'node:path'
import { test, expect, type Page } from '@playwright/test'

/**
 * ogc-mcp (V5) — servicios OGC oficiales de Colombia por el chat, como los pediría una persona, contra el
 * stack REAL (LLM real, ogc-mcp, Internet). Las verdades se midieron con consultas DIRECTAS a cada servicio
 * (resultType=hits / GetCapabilities), no con el agente (tests/test_ogc_mcp_vivo.py):
 *
 *   O1: «¿cuántos departamentos tiene Colombia según el DANE? trae la capa oficial» → 33 (DIVIPOLA 2025).
 *   O2: «muéstrame el mapa de amenaza sísmica del Servicio Geológico Colombiano» → capa WMS en el mapa
 *       (teselas PNG servidas por ogc-mcp a través del proxy del núcleo).
 *   O3: «¿qué áreas protegidas del RUNAP hay en la zona de Bogotá?» → elementos de Parques Nacionales.
 *
 * Correr (stack arriba con ogc-mcp; necesita Internet):
 *   E2E_REAL_URL=https://localhost:3443 npx playwright test --config playwright.integration.config.ts ogc-mcp
 * `V5_DIR` (opcional): carpeta donde dejar capturas y el registro de cada turno.
 */

const DIR = process.env.V5_DIR

async function sendQuery(page: Page, text: string): Promise<void> {
  const input = page.getByPlaceholder(/Pregunta en lenguaje natural/i)
  await expect(input).toBeEnabled({ timeout: 60_000 })
  await input.fill(text)
  await input.press('Enter')
}

async function esperarTurno(page: Page, previos: number): Promise<string> {
  const hitl = page.getByRole('button', { name: /Aprobar y ejecutar/ })
  const deadline = Date.now() + 300_000
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

/** Solo la respuesta (sin la traza «Cómo lo hice», que también tiene números: V5 aceptó un «33» de ella). */
function respuesta(texto: string): string {
  return texto.split(/Cómo lo hice/)[0]
}

async function capas(page: Page): Promise<string[]> {
  return page.locator('[data-testid="layer-row"]').allTextContents()
}

async function registrar(page: Page, nombre: string, datos: Record<string, unknown>): Promise<void> {
  if (!DIR) return
  fs.mkdirSync(DIR, { recursive: true })
  await page.screenshot({ path: path.join(DIR, `${nombre}.png`) })
  fs.writeFileSync(path.join(DIR, `${nombre}.json`), JSON.stringify(datos, null, 1), 'utf8')
}

test.describe('Integración REAL — ogc-mcp (servicios OGC de Colombia)', () => {
  test.setTimeout(900_000)

  test('O1–O3: catálogo OGC, capa WMS en el mapa y elementos WFS por el chat', async ({ page }) => {
    const teselas: { status: number; tipo: string }[] = []
    page.on('response', (r) => {
      if (r.url().includes('/proxy/mcp/ogc/wms/')) teselas.push({ status: r.status(), tipo: r.headers()['content-type'] ?? '' })
    })
    await page.goto('/')
    await page.evaluate(() => window.sessionStorage.clear())
    await page.reload()
    await expect(page.getByPlaceholder(/Pregunta en lenguaje natural/i)).toBeVisible({ timeout: 60_000 })

    await sendQuery(page, '¿cuántos departamentos tiene Colombia según el DANE? trae la capa oficial al mapa')
    const o1 = await esperarTurno(page, 0)
    await page.waitForTimeout(3_000)
    await registrar(page, 'O1-departamentos', { respuesta: o1, capas: await capas(page) })
    // la cifra tiene que salir de los ELEMENTOS traídos (V5: «32… según el servicio» con solo la imagen WMS).
    // La capa del DANE tiene 33: 32 departamentos y Bogotá D.C.; las dos lecturas son correctas.
    expect(o1, 'no trajo los elementos: la cifra no sale del servicio').toMatch(/ogc_traer_elementos/)
    expect(respuesta(o1), o1).toMatch(/\b33\b|\b32\b[^]*Bogot/)

    await sendQuery(page, 'muéstrame el mapa de amenaza sísmica del Servicio Geológico Colombiano')
    const o2 = await esperarTurno(page, 1)
    await page.waitForTimeout(8_000)  // las teselas llegan después del turno
    await registrar(page, 'O2-amenaza-sismica', { respuesta: o2, capas: await capas(page), teselas })
    expect(teselas.some((t) => t.status === 200 && t.tipo.startsWith('image/')), JSON.stringify(teselas)).toBe(true)

    await sendQuery(page, '¿qué áreas protegidas del RUNAP hay en la zona de Bogotá? ponlas en el mapa')
    const o3 = await esperarTurno(page, 2)
    await page.waitForTimeout(3_000)
    await registrar(page, 'O3-runap-bogota', { respuesta: o3, capas: await capas(page) })
    expect(respuesta(o3), o3).toMatch(/\d/)
  })

  test('O4: nombrar MUCHAS áreas sin inventar ninguna (las filas no caben en la observación)', async ({ page }) => {
    // V5: con 8 áreas del RUNAP (6 423 caracteres, tope 3 000) el agente vio ~3 y nombró 8: cinco inventadas.
    // Cada nombre de la respuesta se contrasta después con el RUNAP (V5_DIR/O4-*.json).
    await page.goto('/')
    await page.evaluate(() => window.sessionStorage.clear())
    await page.reload()
    await sendQuery(page, 'trae las áreas protegidas del RUNAP que hay en el rectángulo entre longitud -74.23 y '
      + '-74.0 y latitud 4.46 y 4.84 y nómbralas todas')
    const o4 = await esperarTurno(page, 0)
    await page.waitForTimeout(2_000)
    await registrar(page, 'O4-runap-nombres', { respuesta: o4, capas: await capas(page) })
    expect(o4, o4).toMatch(/ogc_traer_elementos/)
  })
})
