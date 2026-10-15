import * as fs from 'node:fs'
import * as path from 'node:path'
import { test, expect, type Page } from '@playwright/test'

/**
 * dane-mcp (V5) — el censo 2018 del DANE por el chat, como lo pediría una persona, contra el stack REAL.
 * Verdades medidas con consultas DIRECTAS al DANE (tests/test_dane_mcp_vivo.py), no con el agente:
 *
 *   N1: «¿cuánta gente vive en Chía según el censo del DANE?» → 129 613 (CNPV 2018, municipio 25175).
 *   N2: «¿cuántas personas viven en la localidad de Chapinero (Bogotá) según el censo?» → la suma de sus
 *       manzanas; se contrasta después con la suma espacial del propio DANE sobre el mismo límite
 *       (V5_DIR/N2-*.json).
 */

const DIR = process.env.V5_DIR

async function sendQuery(page: Page, text: string): Promise<void> {
  const input = page.getByPlaceholder(/Pregunta en lenguaje natural/i)
  await expect(input).toBeEnabled({ timeout: 120_000 })
  await input.fill(text)
  await input.press('Enter')
}

async function esperarTurno(page: Page, previos: number): Promise<string> {
  const deadline = Date.now() + 300_000
  while (Date.now() < deadline) {
    const msgs = page.locator('.msg-assist')
    const texto = (await msgs.count()) > previos ? ((await msgs.last().textContent()) ?? '') : ''
    const cargando = await page.getByPlaceholder(/Pregunta en lenguaje natural/i).isDisabled()
    if (texto.trim() && !cargando && !/Procesando/.test(texto)) return texto
    await page.waitForTimeout(1_000)
  }
  throw new Error('el turno no terminó')
}

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

test.describe('Integración REAL — dane-mcp', () => {
  test.setTimeout(600_000)

  test('N1: población de un municipio (cifra oficial)', async ({ page }) => {
    await nuevaSesion(page)
    await sendQuery(page, '¿cuánta gente vive en Chía según el censo del DANE?')
    const n1 = await esperarTurno(page, 0)
    await registrar(page, 'N1-chia', { respuesta: n1 })
    expect(n1, 'la cifra tiene que salir del DANE').toMatch(/dane_municipios/)
    expect(respuesta(n1), n1).toMatch(/129[.\s ]?613/)
  })

  test('N2: personas en las manzanas de una localidad', async ({ page }) => {
    await nuevaSesion(page)
    await sendQuery(page, '¿cuántas personas viven en la localidad de Chapinero en Bogotá según el censo del DANE? '
      + 'muéstrame las manzanas')
    const n2 = await esperarTurno(page, 0)
    await page.waitForTimeout(3_000)
    await registrar(page, 'N2-chapinero', { respuesta: n2 })
    expect(n2, 'la cifra tiene que salir de las manzanas del DANE').toMatch(/dane_censo_manzanas/)
  })
})
