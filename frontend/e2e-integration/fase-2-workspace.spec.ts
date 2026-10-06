import { test, expect, type Page } from '@playwright/test'
import * as fs from 'node:fs'
import * as path from 'node:path'

/**
 * FASE 2 — workspace espacial, contra el stack REAL (V3).
 *
 * E2.6 buffer métrico y área total, y E2.2 cruce del workspace con la BD de
 * dominio, como los haría un usuario en el chat. Las cifras se contrastan con
 * referencias INDEPENDIENTES calculadas directo en PostGIS (no por la app):
 *   - buffer 500 m disuelto de los 4 lotes de la manzana 004503009: 97,36 ha
 *     (ST_Buffer planar en EPSG:9377, MAGNA-SIRGAS origen nacional);
 *   - construcciones del catastro dentro de ese buffer: 8184 (ST_Within) u
 *     8442 (ST_Intersects) — "caen dentro" admite las dos lecturas;
 *   - con el buffer GEODÉSICO de 64 segmentos por cuadrante (el del workspace desde
 *     T5.1: ST_Buffer(geography, 500, 'quad_segs=64'), 97,94 ha): 8230 u 8482. La
 *     distancia exacta de 500 m (ST_DWithin en geography) da 8477: 8482 es correcto.
 *     El LLM puede elegir cualquiera de los buffers válidos: se acepta cada lectura en su rango.
 *
 * Correr (stack arriba + `npm run dev` en 5173):
 *   E2E_REAL_URL=http://localhost:5173 REF_DIR=../docs/validacion/evidencia/fase-2 \
 *     npx playwright test --config playwright.integration.config.ts fase-2-workspace
 */

type Win = any

const REF_DIR = process.env.REF_DIR ?? ''
const HA_REFERENCIA = 97.36

async function waitForMap(page: Page): Promise<void> {
  await page.waitForFunction(
    () => (window as Win).__mapTestState?.engine === 'maplibre' && !!(window as Win).__mlmap,
    undefined,
    { timeout: 30_000 },
  )
}

async function turno(page: Page, texto: string, timeout = 240_000): Promise<string> {
  const msgs = page.locator('.msg.msg-assist')
  const previas = await msgs.count()
  const input = page.getByPlaceholder(/Pregunta en lenguaje natural/i)
  await expect(input).toBeEnabled({ timeout: 60_000 })
  await input.fill(texto)
  await input.press('Enter')
  const aprobar = page.getByRole('button', { name: /Aprobar y ejecutar/ })
  const limite = Date.now() + timeout
  while (Date.now() < limite) {
    if (await aprobar.isVisible().catch(() => false)) {
      await aprobar.click()
      await page.waitForTimeout(500)
      continue
    }
    if ((await msgs.count()) > previas) {
      const t = (await msgs.last().textContent()) ?? ''
      if (!t.includes('Procesando consulta')) return t
    }
    await page.waitForTimeout(1_000)
  }
  throw new Error(`sin respuesta a «${texto}» en ${timeout / 1000}s`)
}

async function evidencia(page: Page, nombre: string, respuesta: string): Promise<void> {
  if (!REF_DIR) return
  fs.mkdirSync(REF_DIR, { recursive: true })
  const estado = await page.evaluate(() => (window as Win).__mapTestState)
  fs.writeFileSync(path.join(REF_DIR, `${nombre}.json`), JSON.stringify({ respuesta, estado }, null, 2))
  await page.screenshot({ path: path.join(REF_DIR, `${nombre}.jpg`), type: 'jpeg', quality: 80 })
}

/** Números de la respuesta ("97,42 ha", "8.184") como floats. */
function numeros(texto: string): number[] {
  return (texto.match(/\d[\d.,]*/g) ?? []).map((s) => {
    const limpio = /,\d{1,2}$/.test(s) ? s.replace(/\./g, '').replace(',', '.') : s.replace(/[.,](?=\d{3}\b)/g, '').replace(',', '.')
    return Number(limpio)
  }).filter((n) => Number.isFinite(n))
}

test.describe.configure({ mode: 'serial' })

test('E2.6 + E2.2: buffer métrico exacto y cruce del workspace con el catastro', async ({ page }) => {
  test.setTimeout(900_000)
  const errores: string[] = []
  page.on('pageerror', (e) => errores.push(`pageerror: ${e.message}`))
  page.on('console', (m) => { if (m.type() === 'error') errores.push(`console: ${m.text()}`) })
  page.on('response', async (r) => {
    if (r.url().includes('/api/v1/query/') && r.request().method() === 'POST') {
      const j = await r.json().catch(() => null)
      const capa = (j?.artifacts ?? []).find((x: { kind: string }) => x.kind === 'layer') ?? {}
      console.log(`[query] intent=${j?.intent} geojson=${capa.inline?.features?.length ?? 0} ` +
        `layer=${capa.layer?.id ?? '-'} tiles=${!!capa.tiles} replaces=${capa.replaces ?? '-'}`)
    }
  })
  await page.goto('/')
  await waitForMap(page)

  const r1 = await turno(page, 'trae los lotes de la manzana 004503009')
  await expect.poll(() => page.evaluate(() => (window as Win).__mapTestState?.layers?.length ?? 0)).toBe(1)
  expect(r1).toMatch(/4\s+lotes/)
  await evidencia(page, '01-lotes', r1)

  const r2 = await turno(page, 'haz un buffer de 500 m a esos lotes, disuelto, y dime el área total en hectáreas')
  await evidencia(page, '02-buffer-area', r2)
  console.log('[errores]', JSON.stringify(errores.slice(-10)))
  // Una capa nueva (el buffer disuelto: 1 elemento), sin perder la anterior.
  await expect.poll(() => page.evaluate(() => (window as Win).__mapTestState?.layers?.length ?? 0)).toBe(2)
  const capas = await page.evaluate(() => (window as Win).__mapTestState.layers)
  expect(capas[1].featureCount).toBe(1)
  // El área narrada coincide con la referencia independiente (±1 %).
  // El número que precede a "ha"/"hectáreas" (el texto del mensaje trae además la hora).
  const ha = [...r2.matchAll(/(\d[\d.,]*)\s*(?:ha\b|hect)/gi)].flatMap((m) => numeros(m[1]))
  expect(ha.length, `área en ha en «${r2}»`).toBeGreaterThan(0)
  expect(Math.abs(ha[0] - HA_REFERENCIA) / HA_REFERENCIA).toBeLessThan(0.01)

  const r3 = await turno(page, '¿cuántas construcciones del catastro caen dentro de ese buffer?')
  await evidencia(page, '03-cruce-catastro', r3)
  const n = numeros(r3)
  // H12: la cifra es el conteo real (no el LIMIT de 1000)…
  // Buffers válidos (planar 9377, geodésico, el del sandbox) difieren <1 % en su borde: se
  // acepta cada lectura (within / intersects) dentro de su rango de referencias ±0,5 %.
  const rangos: [number, number][] = [[8184, 8230], [8442, 8482]]
  const enRango = (x: number) => rangos.some(([lo, hi]) => x >= lo * 0.995 && x <= hi * 1.005)
  expect(n.some(enRango), `within 8184–8230 u intersects 8442–8482 en «${r3}»`).toBe(true)
  // …y es exacta: un COUNT completo no se narra como aproximado. Lo aproximado es EL CONTEO («al
  // menos 8.000 construcciones», «unas 8 mil»), no cualquier otra cifra de la respuesta: la
  // regresión acumulada (V3, 2026-09-28) dio «hay 8,230 construcciones… área aproximadamente 41,73 ha»
  // y la aserción anterior, sobre todo el texto, lo contaba como fallo.
  expect(r3, 'un conteo exacto narrado como aproximado').not.toMatch(
    /(al menos|aproximadamente|unas?|cerca de|alrededor de)\s+[\d.,]+\s*(mil\s+)?construcciones/i)
})
