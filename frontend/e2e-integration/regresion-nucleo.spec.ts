import { test, expect, type Page } from '@playwright/test'
import * as fs from 'node:fs'
import * as path from 'node:path'

/**
 * GUION DE REGRESIÓN DEL NÚCLEO — los 10 recorridos de la ficha F1 del plan de
 * plataforma (docs/PLAN_PLATAFORMA_GEO_MCP_2026-09-24.md), contra el stack REAL.
 *
 * Doble uso:
 *  - T0.12: con REF_DIR=…/referencia-nucleo, fija la REFERENCIA (captura +
 *    JSON de estado por recorrido) tomada sobre `main` después de F0.
 *  - V3 de cada fase: se corre de nuevo con otro REF_DIR y se compara. Las
 *    aserciones de aquí son las invariantes que NO pueden romperse; la
 *    comparación visual/JSON es la revisión humana del acta.
 *
 * Correr (stack arriba + `npm run dev` en 5173, ver fase-0-suelo-firme.spec.ts):
 *   E2E_REAL_URL=http://localhost:5173 REF_DIR=../docs/validacion/evidencia/referencia-nucleo \
 *     npx playwright test --config playwright.integration.config.ts regresion-nucleo
 *
 * Aserciones flojas pero reales: el LLM no es determinista.
 */

type Win = any

interface LayerProbe {
  id: string
  name: string
  featureCount: number
  color: string
  rendererKind: string | null
}

const REF_DIR = process.env.REF_DIR ?? ''

async function waitForMap(page: Page): Promise<void> {
  await page.waitForFunction(
    () => (window as Win).__mapTestState?.engine === 'maplibre' && !!(window as Win).__mlmap,
    undefined,
    { timeout: 30_000 },
  )
}

const layers = (page: Page): Promise<LayerProbe[]> =>
  page.evaluate(() => (window as Win).__mapTestState?.layers ?? [])

/**
 * Un turno completo como lo haría un usuario: escribe, y mientras la respuesta
 * no llegue, resuelve cada panel de aprobación que aparezca (aprobar o
 * rechazar). Devuelve el texto de la respuesta.
 */
async function turno(
  page: Page,
  texto: string,
  { aprobar = true, timeout = 240_000 }: { aprobar?: boolean; timeout?: number } = {},
): Promise<string> {
  const msgs = page.locator('.msg.msg-assist')
  const previas = await msgs.count()
  const input = page.getByPlaceholder(/Pregunta en lenguaje natural/i)
  await expect(input).toBeEnabled({ timeout: 60_000 })
  await input.fill(texto)
  await input.press('Enter')

  const boton = page.getByRole('button', { name: aprobar ? /Aprobar y ejecutar/ : /^Rechazar$/ })
  const limite = Date.now() + timeout
  while (Date.now() < limite) {
    if (await boton.isVisible().catch(() => false)) {
      await boton.click()
      await page.waitForTimeout(500)
      continue
    }
    const n = await msgs.count()
    if (n > previas) {
      const t = (await msgs.last().textContent()) ?? ''
      if (!t.includes('Procesando consulta')) return t
    }
    await page.waitForTimeout(1_000)
  }
  throw new Error(`sin respuesta a «${texto}» en ${timeout / 1000}s`)
}

/** Guarda la evidencia del recorrido (captura + estado) si REF_DIR está definido. */
async function evidencia(page: Page, nombre: string, respuesta: string): Promise<void> {
  if (!REF_DIR) return
  fs.mkdirSync(REF_DIR, { recursive: true })
  const estado = await page.evaluate(() => {
    const w = window as Win
    return {
      featureCount: w.__mapTestState?.featureCount ?? 0,
      rendererKind: w.__mapTestState?.rendererKind ?? null,
      layers: (w.__mapTestState?.layers ?? []).map((l: Win) => ({
        name: l.name, featureCount: l.featureCount, color: l.color, rendererKind: l.rendererKind,
      })),
      pestañaActiva: document.querySelector('[aria-selected="true"], .tab.active')?.textContent?.trim() ?? null,
      filasDeTabla: document.querySelectorAll('table tbody tr').length,
      graficos: document.querySelectorAll('.recharts-surface').length,
      tarjetasDeServicio: document.querySelectorAll('.fs-card').length,
    }
  })
  fs.writeFileSync(
    path.join(REF_DIR, `${nombre}.json`),
    JSON.stringify({ recorrido: nombre, respuesta: respuesta.trim(), ...estado }, null, 2),
  )
  await page.screenshot({ path: path.join(REF_DIR, `${nombre}.jpg`), type: 'jpeg', quality: 70 })
}

test.describe.configure({ mode: 'serial' })

test.describe('Regresión del núcleo (10 recorridos)', () => {
  test.beforeEach(async ({ page }) => {
    test.setTimeout(420_000)
    await page.goto('/')
    await waitForMap(page)
  })

  test('01 · consulta simple a la BD', async ({ page }) => {
    const r = await turno(page, '¿cuántos lotes hay en total en la base de datos?')
    expect(r).toMatch(/\d[\d.,]{3,}/)
    await evidencia(page, '01-consulta-simple', r)
  })

  test('02 · buffer métrico sobre una capa cargada', async ({ page }) => {
    await turno(page, 'tráeme 30 construcciones de catastro en el mapa')
    await expect.poll(() => layers(page).then((l) => l.length), { timeout: 60_000 }).toBeGreaterThan(0)
    const r = await turno(page, 'hazles un buffer de 50 metros')
    expect(r).not.toMatch(/error|no pude|no se pudo/i)
    await evidencia(page, '02-buffer', r)
  })

  test('03 · análisis estadístico con tabla y gráfico', async ({ page }) => {
    await turno(page, 'tráeme 60 lotes de catastro en el mapa')
    const r = await turno(page, 'dame estadísticas descriptivas del área de estos lotes con un histograma')
    expect(r).not.toMatch(/no pude|no se pudo/i)
    await evidencia(page, '03-analisis', r)
  })

  test('04 · simbología por categorías', async ({ page }) => {
    await turno(page, 'tráeme 50 lotes de catastro en el mapa')
    // lotdispers es «N» en el 98 % de catastro.lotes: en 50 lotes casi siempre hay UNA categoría y
    // lo correcto es un color único (V3 FH). Las categorías se prueban con un campo que las tiene.
    const r = await turno(page, 'colorea los lotes por su manzana (campo manzcodigo)')
    await expect.poll(
      () => layers(page).then((ls) => ls.some((l) => l.rendererKind && l.rendererKind !== 'single_symbol')),
      { timeout: 60_000 },
    ).toBe(true)
    await evidencia(page, '04-simbologia', r)
  })

  test('05 · discovery → elegir → cargar', async ({ page }) => {
    const r1 = await turno(page, 'busca datos abiertos de estaciones de bomberos en Colombia')
    await expect(page.locator('.fs-card').first()).toBeVisible({ timeout: 60_000 })
    await evidencia(page, '05a-discovery', r1)
    const r2 = await turno(page, 'carga el 1')
    await evidencia(page, '05b-cargar', r2)
  })

  test('06 · NDVI de una capa cargada', async ({ page }) => {
    await turno(page, 'tráeme 20 lotes de catastro en el mapa')
    const r = await turno(page, 'calcula el NDVI de estos lotes')
    // Depende del clima real: si no hay escenas sin nubes recientes, la salida
    // honesta es decirlo. Lo que NO vale es un error técnico o un NDVI de
    // imágenes de otra época (antes el LLM ampliaba el rango hacia 2023).
    expect(r).toMatch(/NDVI|no hay escenas/i)
    expect(r).not.toMatch(/202[0-4]-\d\d-\d\d|de 202[0-4]/)
    await evidencia(page, '06-ndvi', r)
  })

  test('07 · clarificación ante una petición sin referente', async ({ page }) => {
    const r = await turno(page, 'mejóralo')
    // Pedir aclaración puede ser pregunta («¿qué capa?») o petición («indícame a qué te
    // refieres»): los dos piden el referente. Lo que NO vale es actuar sobre algo inventado.
    expect(r).toMatch(/\?|ind[ií]ca(me)?|dime|a qu[eé] te refieres|especifica|aclara/i)
    await evidencia(page, '07-clarificacion', r)
  })

  test('08 · HITL: rechazar no ejecuta nada', async ({ page }) => {
    const r = await turno(page, 'muéstrame 40 construcciones en el mapa', { aprobar: false })
    await page.waitForTimeout(2_000)
    expect(await layers(page)).toHaveLength(0)
    await evidencia(page, '08-hitl-rechazo', r)
  })

  test('09 · consulta multi-paso (traer + estilizar)', async ({ page }) => {
    const r = await turno(page, 'tráeme 30 lotes de catastro y coloréalos de rojo')
    await expect.poll(() => layers(page).then((l) => l.length), { timeout: 60_000 }).toBeGreaterThan(0)
    await evidencia(page, '09-multipaso', r)
  })

  test('10 · con dos capas, se colorea la nombrada', async ({ page }) => {
    await turno(page, 'tráeme 40 lotes de catastro en el mapa')
    await turno(page, 'ahora tráeme 30 construcciones de catastro en el mapa')
    await expect.poll(() => layers(page).then((l) => l.length), { timeout: 60_000 }).toBe(2)
    const antes = (await layers(page)).find((l) => l.featureCount === 30)!.color
    const r = await turno(page, 'colorea los lotes de rojo')
    await expect.poll(
      () => layers(page).then((ls) => ls.find((l) => l.featureCount === 40)?.rendererKind === 'single_symbol'),
      { timeout: 90_000 },
    ).toBe(true)
    expect((await layers(page)).find((l) => l.featureCount === 30)!.color).toBe(antes)
    await evidencia(page, '10-dos-capas', r)
  })
})
