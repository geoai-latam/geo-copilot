import * as fs from 'node:fs'
import * as path from 'node:path'
import { test, expect, type Page } from '@playwright/test'

/**
 * amenazas-mcp (V5) — sismos, incendios, caudales y clima por el chat, contra el stack REAL. La cifra se exige
 * en la RESPUESTA (no en la traza) y se contrasta después con la fuente (tests/test_amenazas_mcp_vivo.py):
 *
 *   A1: sismos M≥4 en Colombia en el último año → del catálogo de la USGS (191 en el rectángulo del país).
 *   A2: caudal del Magdalena en Honda → miles de m³/s (la celda del punto es un afluente de 1,5: trampa).
 *   A3: focos de calor en el Vichada esta semana → FIRMS.
 *   A4: lluvia en Yopal en 2023 mes a mes → ERA5 / NASA POWER, en mm.
 */

const DIR = process.env.V5_DIR

async function sendQuery(page: Page, text: string): Promise<void> {
  const input = page.getByPlaceholder(/Pregunta en lenguaje natural/i)
  await expect(input).toBeEnabled({ timeout: 120_000 })
  await input.fill(text)
  await input.press('Enter')
}

async function esperarTurno(page: Page): Promise<string> {
  const deadline = Date.now() + 300_000
  while (Date.now() < deadline) {
    const msgs = page.locator('.msg-assist')
    const texto = (await msgs.count()) > 0 ? ((await msgs.last().textContent()) ?? '') : ''
    const cargando = await page.getByPlaceholder(/Pregunta en lenguaje natural/i).isDisabled()
    if (texto.trim() && !cargando && !/Procesando/.test(texto)) return texto
    await page.waitForTimeout(1_000)
  }
  throw new Error('el turno no terminó')
}

async function preguntar(page: Page, nombre: string, texto: string, herramienta: RegExp, cifra: RegExp) {
  await page.goto('/')
  await page.evaluate(() => window.sessionStorage.clear())
  await page.reload()
  await sendQuery(page, texto)
  const r = await esperarTurno(page)
  if (DIR) {
    fs.mkdirSync(DIR, { recursive: true })
    await page.screenshot({ path: path.join(DIR, `${nombre}.png`) })
    fs.writeFileSync(path.join(DIR, `${nombre}.json`), JSON.stringify({ respuesta: r }, null, 1), 'utf8')
  }
  expect(r, `debía usar ${herramienta}`).toMatch(herramienta)
  expect(r.split(/Cómo lo hice/)[0], r).toMatch(cifra)
}

test.describe('Integración REAL — amenazas-mcp', () => {
  test.setTimeout(600_000)

  test('A1: sismos fuertes del último año', async ({ page }) => {
    await preguntar(page, 'A1-sismos', '¿qué sismos de magnitud 4 o más hubo en Colombia en el último año? dime los más fuertes',
      /amenazas_sismos/, /\b[4-9][.,]\d\b/)
  })

  test('A2: caudal de un río grande (trampa de la celda)', async ({ page }) => {
    await preguntar(page, 'A2-magdalena-honda', '¿cómo está el caudal del río Magdalena en Honda?', /amenazas_caudal/,
      /\b\d[.\s ]?\d{3}([.,]\d+)?\s*(m³\/s|m3\/s|metros cúbicos)/i)
  })

  test('A3: focos de calor de la semana', async ({ page }) => {
    await preguntar(page, 'A3-incendios-vichada', '¿hay focos de incendio activos en el Vichada esta semana?',
      // «135 focos» o «focos de calor…: se detectaron 135» (V5: la segunda forma era un falso rojo)
      /amenazas_incendios/, /\b\d+\s*(focos|puntos|detecciones)|focos[^.]{0,60}?\b\d+\b(?!\s*(d[ií]as|h\b|horas))/i)
  })

  test('A4: lluvia mensual de un año', async ({ page }) => {
    await preguntar(page, 'A4-lluvia-yopal', '¿cuánto llovió en Yopal en 2023, mes a mes?', /clima_serie/, /\d+([.,]\d+)?\s*mm/)
  })
})
