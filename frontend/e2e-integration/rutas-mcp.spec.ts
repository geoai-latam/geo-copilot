import * as fs from 'node:fs'
import * as path from 'node:path'
import { test, expect, type Page } from '@playwright/test'

/**
 * rutas-mcp (V5) — rutas e isócronas por el chat, como las pediría una persona, contra el stack REAL (LLM real,
 * Valhalla). El agente nombra lugares; los ubica con lugares-mcp y rutea con rutas-mcp. La verdad de cada turno
 * se mide después con el motor directo y con OSRM (otro motor) desde las coordenadas que usó el agente
 * (V5_DIR/R*.json; tests/test_rutas_mcp_vivo.py fija la de Plaza de Bolívar → Chía: 33,09 km, OSRM 33,94).
 *
 *   R1: «¿cuánto se tarda en carro de la Plaza de Bolívar de Bogotá a Chía y cuántos km son?»
 *   R2: «¿qué zona alcanzo caminando 15 minutos desde el Parque de la 93 en Bogotá?»
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

test.describe('Integración REAL — rutas-mcp', () => {
  test.setTimeout(600_000)

  test('R1: ruta entre dos lugares nombrados', async ({ page }) => {
    await nuevaSesion(page)
    await sendQuery(page, '¿cuánto se tarda en carro de la Plaza de Bolívar de Bogotá a Chía y cuántos kilómetros son?')
    const r1 = await esperarTurno(page, 0)
    await page.waitForTimeout(3_000)
    await registrar(page, 'R1-plaza-chia', { respuesta: r1 })
    expect(r1, 'la cifra tiene que salir del motor de rutas').toMatch(/rutas_ruta/)
    expect(respuesta(r1), r1).toMatch(/\b(2[5-9]|3\d|4[0-2])([.,]\d+)?\s*(km|kil)/i)
  })

  test('R2: isócrona a pie desde un lugar nombrado', async ({ page }) => {
    await nuevaSesion(page)
    await sendQuery(page, '¿qué zona alcanzo caminando 15 minutos desde el Parque de la 93 en Bogotá? muéstramela')
    const r2 = await esperarTurno(page, 0)
    await page.waitForTimeout(3_000)
    await registrar(page, 'R2-isocrona-93', { respuesta: r2 })
    expect(r2, 'la zona tiene que salir del motor de rutas').toMatch(/rutas_isocronas/)
  })
})
