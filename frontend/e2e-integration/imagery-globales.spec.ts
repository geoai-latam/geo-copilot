import * as fs from 'node:fs'
import * as path from 'node:path'
import { test, expect, type Page } from '@playwright/test'

/**
 * Capas globales de imagery-mcp (V5) — cobertura (WorldCover), población (WorldPop) y radar (Sentinel-1) por
 * el chat, contra el stack REAL. Cada cifra se recalcula después con el mismo límite (Nominatim) y las mismas
 * funciones independientes de tests/test_imagery_globales_vivo.py (V5_DIR/G*.json).
 *
 *   G1: «¿qué coberturas del suelo hay en el municipio de Chía? dame los porcentajes»
 *   G2: «¿cuánta población estima WorldPop para Chía en 2025?»
 *   G3: «¿cuánta agua ve el radar en el embalse de Tominé en el último mes?»
 */

const DIR = process.env.V5_DIR

async function sendQuery(page: Page, text: string): Promise<void> {
  const input = page.getByPlaceholder(/Pregunta en lenguaje natural/i)
  await expect(input).toBeEnabled({ timeout: 120_000 })
  await input.fill(text)
  await input.press('Enter')
}

async function esperarTurno(page: Page, previos: number): Promise<string> {
  const deadline = Date.now() + 400_000
  while (Date.now() < deadline) {
    const msgs = page.locator('.msg-assist')
    const texto = (await msgs.count()) > previos ? ((await msgs.last().textContent()) ?? '') : ''
    const cargando = await page.getByPlaceholder(/Pregunta en lenguaje natural/i).isDisabled()
    if (texto.trim() && !cargando && !/Procesando/.test(texto)) return texto
    await page.waitForTimeout(1_000)
  }
  throw new Error('el turno no terminó')
}

async function registrar(page: Page, nombre: string, datos: Record<string, unknown>): Promise<void> {
  if (!DIR) return
  fs.mkdirSync(DIR, { recursive: true })
  await page.screenshot({ path: path.join(DIR, `${nombre}.png`) })
  fs.writeFileSync(path.join(DIR, `${nombre}.json`), JSON.stringify(datos, null, 1), 'utf8')
}

/** Pregunta en una sesión nueva; la herramienta debe aparecer en la traza y la CIFRA en la respuesta (V5: una
 *  pasada «verde» solo había intentado la herramienta y luego se rindió). */
async function preguntar(page: Page, nombre: string, texto: string, herramienta: RegExp,
                         cifra: RegExp): Promise<void> {
  await page.goto('/')
  await page.evaluate(() => window.sessionStorage.clear())
  await page.reload()
  await sendQuery(page, texto)
  const r = await esperarTurno(page, 0)
  await registrar(page, nombre, { respuesta: r })
  expect(r, `debía usar ${herramienta}`).toMatch(herramienta)
  expect(r.split(/Cómo lo hice/)[0], r).toMatch(cifra)
}

test.describe('Integración REAL — capas globales de imagery-mcp', () => {
  test.setTimeout(900_000)

  test('G1: cobertura del suelo de un municipio', async ({ page }) => {
    await preguntar(page, 'G1-cobertura-chia', '¿qué coberturas del suelo hay en el municipio de Chía? dame los porcentajes',
      /imagery_land_cover/, /Construido[^%]{0,20}\d+([.,]\d+)?\s*%/)
  })

  test('G2: población estimada de un municipio', async ({ page }) => {
    await preguntar(page, 'G2-poblacion-chia', '¿cuánta población estima WorldPop para Chía en 2025?', /imagery_population/,
      /\b\d{2,3}[.\s ]?\d{3}\s*(personas|habitantes)/i)
  })

  test('G3: agua vista por el radar', async ({ page }) => {
    await preguntar(page, 'G3-radar-tomine', '¿cuánta agua ve el radar en el embalse de Tominé en el último mes?',
      /imagery_sar/, /\d+([.,]\d+)?\s*%/)
  })
})
