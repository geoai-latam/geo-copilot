import { test, expect, type Page } from '@playwright/test'

/**
 * FASE H — interacción híbrida mapa↔chat contra el stack REAL (V3): LLM real, PostGIS, imagery-mcp.
 * Nada se mockea ni se le dicta al agente; los datos se comprueban contra REFERENCIAS
 * INDEPENDIENTES tomadas de la BD (psql), no contra lo que dice el agente.
 *
 *   EH.1: 12 lotes seleccionados → «¿cuántas hectáreas suman?» = 1,0092 ha (ST_Area geography).
 *   EH.2: un polígono dibujado → «NDVI de lo que dibujé entre marzo y junio» → NDVI del dibujo.
 *   EH.4: filtro del agente (lotupredia > 100) retocado a mano (> 130) → la suma usa el retoque:
 *         2 lotes, 3 545,47 m².
 *   EH.9: «¿qué hay cerca?» sin nada marcado → pide un punto; clic → el turno sigue.
 *
 * Correr (stack arriba, `npm run dev` en 5173):
 *   E2E_REAL_URL=http://localhost:5173 npx playwright test --config playwright.integration.config.ts fase-h-hibrida
 */

type Win = any

const DOCE = ['008510017002', '008510017003', '008510017004', '008510017007', '008510017008', '008510017009',
              '008510017010', '008510017011', '008510017013', '008510017014', '008510017015', '008510017016']
const HA_DOCE = 1.0092 // SELECT sum(ST_Area(ST_Transform(shape,4326)::geography))/1e4 FROM catastro.lotes WHERE lotcodigo IN (…)
const M2_DOS = 3545.47 // lotupredia > 130 en la manzana 008510017: 011 (2098,19) + 060 (1447,28)

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
async function esperarTurno(page: Page): Promise<string> {
  const n = await page.locator('.msg-assist').count()
  const hitl = page.getByRole('button', { name: /Aprobar y ejecutar/ })
  const deadline = Date.now() + 200_000
  while (Date.now() < deadline) {
    if (await hitl.isVisible().catch(() => false)) await hitl.click()
    const msgs = page.locator('.msg-assist')
    const texto = (await msgs.count()) > n - 1 ? ((await msgs.last().textContent()) ?? '') : ''
    const cargando = await page.getByPlaceholder(/Pregunta en lenguaje natural/i).isDisabled()
    if (texto.trim() && !cargando && !/Procesando/.test(texto)) return texto
    await page.waitForTimeout(1_000)
  }
  throw new Error('el turno no terminó')
}

/** El número (con coma o punto decimal, miles con punto) más cercano a `ref` en el texto. */
function cifraCercana(texto: string, ref: number): number | null {
  const nums = (texto.match(/\d[\d.,]*/g) ?? []).flatMap((t) => {
    const a = Number(t.replace(/\.(?=\d{3}\b)/g, '').replace(',', '.'))
    const b = Number(t.replace(/,/g, ''))
    return [a, b].filter((x) => Number.isFinite(x))
  })
  return nums.length ? nums.reduce((m, x) => (Math.abs(x - ref) < Math.abs(m - ref) ? x : m)) : null
}

async function traerLotes(page: Page) {
  await sendQuery(page, 'trae los lotes de la manzana 008510017')
  await esperarTurno(page)
  await page.waitForFunction(() => ((window as Win).__mapTestState?.layers ?? []).some((l: Win) => l.featureCount >= 27),
                             undefined, { timeout: 60_000 })
  return page.evaluate(() => (window as Win).__mapTestState.layers.find((l: Win) => l.featureCount >= 27).id as string)
}

test.describe('Integración REAL — FH interacción híbrida', () => {
  test.setTimeout(420_000)

  test('EH.1 + EH.4: la selección y el filtro retocado a mano son el alcance del agente', async ({ page }) => {
    await page.goto('/')
    await waitForMap(page)
    const capa = await traerLotes(page)

    // EH.1: los 12 lotes seleccionados (el gesto de lazo lo cubre V2: aquí interesa el alcance)
    await page.evaluate(({ id, codigos }) => (window as Win).__operaciones.getState().ejecutar({
      op: 'select', layer_id: id, reason: null,
      args: { where: { field: 'lotcodigo', op: 'in', value: codigos }, mode: 'replace', origin: 'lasso', ids: null, count: null },
    }, 'user'), { id: capa, codigos: DOCE })
    await expect(page.getByText(/12 seleccionados de/)).toBeVisible()  // el chip de alcance, antes de enviar
    await sendQuery(page, '¿cuántas hectáreas suman?')
    const r1 = await esperarTurno(page)
    const ha = cifraCercana(r1, HA_DOCE)
    expect(ha, r1).not.toBeNull()
    expect(Math.abs((ha as number) - HA_DOCE) / HA_DOCE, r1).toBeLessThan(0.01)

    // EH.4: el agente filtra, el usuario retoca el filtro a mano y el resumen usa el retoque
    await page.evaluate(() => (window as Win).__operaciones.getState().ejecutar({ op: 'clear_selection', args: {}, reason: null }, 'user'))
    await sendQuery(page, 'deja solo los lotes con lotupredia mayor a 100')
    await esperarTurno(page)
    await page.evaluate((id) => (window as Win).__operaciones.getState().ejecutar({
      op: 'set_filter', layer_id: id, reason: null, args: { where: [{ field: 'lotupredia', op: '>', value: 130 }], count: null },
    }, 'user'), capa)
    await sendQuery(page, '¿cuántos lotes quedan y cuánto suman en metros cuadrados?')
    const r4 = await esperarTurno(page)
    expect(r4).toMatch(/\b2\b|dos/i)
    const m2 = cifraCercana(r4, M2_DOS)
    expect(m2, r4).not.toBeNull()
    expect(Math.abs((m2 as number) - M2_DOS) / M2_DOS, r4).toBeLessThan(0.01)
  })

  test('EH.2: el NDVI de lo que dibujé entre marzo y junio', async ({ page }) => {
    await page.goto('/')
    await waitForMap(page)
    await page.evaluate(() => (window as Win).__mlmap.jumpTo({ center: [-74.0525, 4.7215], zoom: 16 }))
    await page.waitForFunction(() => !(window as Win).__mlmap.isMoving())
    // dibujar un polígono (clic en el primer vértice lo cierra)
    await page.getByRole('button', { name: /Dibujar un polígono/ }).click()
    const c = (await page.locator('.maplibregl-canvas').boundingBox())!
    const pts = [[0.45, 0.4], [0.6, 0.4], [0.6, 0.6], [0.45, 0.6]]
    for (const [x, y] of pts) await page.mouse.click(c.x + c.width * x, c.y + c.height * y)
    await page.mouse.click(c.x + c.width * pts[0][0], c.y + c.height * pts[0][1])
    await page.waitForFunction(() => ((window as Win).__mapTestState?.layers ?? []).some((l: Win) => /^Área/.test(l.name)),
                               undefined, { timeout: 30_000 })
    await sendQuery(page, 'dame el NDVI de lo que dibujé entre marzo y junio de 2026')
    const r = await esperarTurno(page)
    expect(r).toMatch(/NDVI/i)
    expect(r).toMatch(/0[.,]\d{2}/)  // un valor de NDVI
    // un raster NDVI (o el NDVI por elemento del dibujo) llegó al mapa
    await expect.poll(() => page.evaluate(() => (window as Win).__mapTestState.layers.map((l: Win) => l.name).join('|')),
                      { timeout: 30_000 }).toMatch(/NDVI/i)
  })

  test('EH.9: «¿qué hay cerca?» sin nada marcado → pide un punto; clic → el turno sigue', async ({ page }) => {
    await page.goto('/')
    await waitForMap(page)
    await page.evaluate(() => (window as Win).__mlmap.jumpTo({ center: [-74.0525, 4.7215], zoom: 16 }))
    await sendQuery(page, '¿qué hay cerca?')
    await esperarTurno(page)
    const barra = page.getByTestId('pedido-mapa')
    await expect(barra).toBeVisible()
    const c = (await page.locator('.maplibregl-canvas').boundingBox())!
    await page.mouse.click(c.x + c.width * 0.55, c.y + c.height * 0.55)
    await expect(page.getByText('📍 Punto marcado en el mapa')).toBeVisible()
    const r = await esperarTurno(page)
    expect(r).not.toMatch(/^Error/)
    expect(r).toMatch(/\d/)
    await expect(barra).toHaveCount(0)
  })
})
