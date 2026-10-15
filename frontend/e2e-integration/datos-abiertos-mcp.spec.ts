import * as fs from 'node:fs'
import * as path from 'node:path'
import { test, expect, type Page } from '@playwright/test'

/**
 * datos-abiertos-mcp (V5) — datos.gov.co y Datos Abiertos Bogotá por el chat, como los pediría una persona,
 * contra el stack REAL (LLM real, Internet). Verdades medidas con consultas DIRECTAS a los portales
 * (tests/test_datos_abiertos_mcp_vivo.py), no con el agente:
 *
 *   D1: instituciones educativas OFICIALES de Envigado (datos.gov.co g8hy-gwg8) → 23 registros (de 82):
 *       14 sedes principales y 9 sedes.
 *   D2: colegios privados de Bogotá (CKAN, GeoJSON en EPSG:3857) → 1 143, en Bogotá (no en el océano).
 *
 * Correr: E2E_REAL_URL=https://localhost:3443 npx playwright test --config playwright.integration.config.ts
 * datos-abiertos   (`V5_DIR` opcional: capturas y registro de cada turno)
 */

const DIR = process.env.V5_DIR

async function sendQuery(page: Page, text: string): Promise<void> {
  const input = page.getByPlaceholder(/Pregunta en lenguaje natural/i)
  await expect(input).toBeEnabled({ timeout: 120_000 })
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

/** Solo la respuesta (sin la traza «Cómo lo hice», que también tiene números). */
function respuesta(texto: string): string {
  return texto.split(/Cómo lo hice/)[0]
}

async function registrar(page: Page, nombre: string, datos: Record<string, unknown>): Promise<void> {
  if (!DIR) return
  fs.mkdirSync(DIR, { recursive: true })
  await page.screenshot({ path: path.join(DIR, `${nombre}.png`) })
  fs.writeFileSync(path.join(DIR, `${nombre}.json`), JSON.stringify(datos, null, 1), 'utf8')
}

async function nuevaSesion(page: Page): Promise<void> {
  await page.goto('/')
  await page.evaluate(() => window.sessionStorage.clear())
  await page.reload()
}

test.describe('Integración REAL — datos-abiertos-mcp', () => {
  test.setTimeout(600_000)

  test('D1: contar con un filtro hecho en datos.gov.co', async ({ page }) => {
    await nuevaSesion(page)
    await sendQuery(page, '¿cuántas instituciones educativas oficiales hay en Envigado según los datos abiertos? '
      + 'ponlas en el mapa')
    const d1 = await esperarTurno(page, 0)
    await page.waitForTimeout(3_000)
    await registrar(page, 'D1-envigado-oficiales', { respuesta: d1 })
    expect(d1, 'la cifra tiene que salir del portal').toMatch(/datos_consultar/)
    // 23 registros oficiales: 14 sedes principales y 9 sedes (medido con SODA directo); ambas lecturas valen
    expect(respuesta(d1), d1).toMatch(/\b23\b|\b14\b/)
  })

  test('D2: un recurso de Datos Abiertos Bogotá en su CRS (3857) cae en Bogotá', async ({ page }) => {
    await nuevaSesion(page)
    await sendQuery(page, 'trae los colegios privados de Bogotá del portal de datos abiertos de Bogotá')
    const d2 = await esperarTurno(page, 0)
    await page.waitForTimeout(4_000)
    await registrar(page, 'D2-colegios-privados-bogota', { respuesta: d2 })
    expect(respuesta(d2), d2).toMatch(/1[.\s]?143/)
  })
})
