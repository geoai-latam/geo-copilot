import { test, expect, type Page } from '@playwright/test'
import * as fs from 'node:fs'
import * as path from 'node:path'

/**
 * FASE 2 — V5: recorridos de usuario final contra el stack REAL (escenarios
 * E2.1, E2.3, E2.4, E2.5 del plan; E2.2 y E2.6 están en fase-2-workspace).
 *
 * Correr (stack arriba + `npm run dev` en 5173):
 *   E2E_REAL_URL=http://localhost:5173 REF_DIR=../docs/validacion/evidencia/fase-2/v5 \
 *     npx playwright test --config playwright.integration.config.ts fase-2-v5
 *
 * Capa grande pública (E2.1): puntos de códigos postales de EE. UU., 41.033
 * features (Esri Living Atlas, sin token).
 */

type Win = any

const REF_DIR = process.env.REF_DIR ?? ''
const CAPA_GRANDE =
  'https://services.arcgis.com/P3ePLMYs2RVChkJx/arcgis/rest/services/USA_ZIP_Code_Points_analysis/FeatureServer/0'

async function waitForMap(page: Page): Promise<void> {
  await page.waitForFunction(
    () => (window as Win).__mapTestState?.engine === 'maplibre' && !!(window as Win).__mlmap,
    undefined,
    { timeout: 30_000 },
  )
}

async function turno(page: Page, texto: string, timeout = 480_000): Promise<string> {
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

async function evidencia(page: Page, nombre: string, extra: Record<string, unknown> = {}): Promise<void> {
  if (!REF_DIR) return
  fs.mkdirSync(REF_DIR, { recursive: true })
  const estado = await page.evaluate(() => (window as Win).__mapTestState)
  fs.writeFileSync(path.join(REF_DIR, `${nombre}.json`), JSON.stringify({ ...extra, estado }, null, 2))
  await page.screenshot({ path: path.join(REF_DIR, `${nombre}.jpg`), type: 'jpeg', quality: 80 })
}

/** El texto del mensaje sin la cabecera ("Copilot completado 22:06:16") ni el pie ("N resultados"). */
const cuerpo = (t: string) => t.replace(/^.*?\d\d:\d\d:\d\d/, '').replace(/\d+\s+resultados\s*$/, '')

const capas = (page: Page) => page.evaluate(() => (window as Win).__mapTestState?.layers ?? [])

/** Tiempo de un frame del mapa: si el navegador "se traba", esto se dispara. */
async function msPorFrame(page: Page): Promise<number> {
  return page.evaluate(() => new Promise<number>((resolve) => {
    let n = 0
    const t0 = performance.now()
    const paso = () => {
      n += 1
      if (n < 30) requestAnimationFrame(paso)
      else resolve((performance.now() - t0) / n)
    }
    requestAnimationFrame(paso)
  }))
}

test.describe.configure({ mode: 'serial' })

test('E2.1 · una capa ArcGIS de 41.033 puntos se ve completa, hace zoom y se simboliza sin trabarse', async ({ page }) => {
  test.setTimeout(1_200_000)
  const teselas: string[] = []
  page.on('request', (r) => { if (r.url().includes('/api/v1/tiles/ws/')) teselas.push(r.url()) })
  let respuestaQuery: Record<string, any> | null = null
  page.on('response', async (r) => {
    if (r.url().includes('/api/v1/query/') && r.request().method() === 'POST') {
      respuestaQuery = await r.json().catch(() => null)
    }
  })
  await page.goto('/')
  await waitForMap(page)

  const r1 = await turno(page, `carga esta capa: ${CAPA_GRANDE}`)
  await expect.poll(async () => (await capas(page)).length, { timeout: 60_000 }).toBe(1)
  const [capa] = await capas(page)
  // F4: la capa llega como artefacto del contrato.
  const art = ((respuestaQuery as Record<string, any> | null)?.artifacts ?? []).find((x: any) => x.kind === 'layer') ?? {}
  await evidencia(page, 'e21-cargada', { respuesta: r1, featureCount: capa.featureCount, tiles: art.tiles ?? null })
  expect(capa.featureCount).toBe(41033)          // completa, no los 10.000 de antes
  expect(art.tiles, 'una capa grande va por teselas').toBeTruthy()
  expect(art.inline ?? null).toBeNull()           // el GeoJSON NO viaja al navegador
  await expect.poll(() => teselas.length, { timeout: 30_000 }).toBeGreaterThan(0)

  // zoom: el mapa responde (frames < 100 ms) y pide teselas nuevas
  const antes = teselas.length
  await page.evaluate(() => (window as Win).__mlmap.easeTo({ center: [-74.0, 40.7], zoom: 9, duration: 0 }))
  await expect.poll(() => teselas.length, { timeout: 30_000 }).toBeGreaterThan(antes)
  expect(await msPorFrame(page)).toBeLessThan(100)
  await evidencia(page, 'e21-zoom')

  const r2 = await turno(page, 'colorea esos puntos por estado')
  await page.getByRole('button', { name: /^Mapa$/ }).click().catch(() => {})
  await page.waitForTimeout(3_000)  // teselas re-estiladas
  await evidencia(page, 'e21-simbolizada', { respuesta: r2 })
  const [estilada] = await capas(page)
  expect(estilada.rendererKind).not.toBe('single_symbol')
  expect(estilada.featureCount).toBe(41033)
  expect(await msPorFrame(page)).toBeLessThan(100)
})

test('E2.3 · LISA sobre un campo numérico: capa de clusters + explicación con cifras', async ({ page }) => {
  test.setTimeout(900_000)
  await page.goto('/')
  await waitForMap(page)
  // La cadena del workspace (datasets cruzables, cada paso rápido e indexado):
  // lotes → buffer de 300 m → construcciones que caen en él → LISA.
  await turno(page, 'trae los lotes de la manzana 004503009')
  await turno(page, 'haz un buffer de 300 m a esos lotes, disuelto')
  const r0 = await turno(page, 'trae las construcciones del catastro que caen dentro de ese buffer')
  await evidencia(page, 'e23-construcciones', { respuesta: r0 })
  // cuántas trajo (3107–3132 según el buffer que eligió: 8 o 64 segmentos por cuadrante)
  const nConstrucciones = (await capas(page)).at(-1)?.featureCount ?? 0
  expect(nConstrucciones).toBeGreaterThan(3000)
  const r = await turno(page, '¿hay agrupamiento espacial del número de pisos? calcula LISA y muéstrame los clusters')
  await evidencia(page, 'e23-lisa', { respuesta: r })
  expect(r).toMatch(/Moran|LISA|cl[uú]ster|HH|alto-alto/i)
  // narra CIFRAS del análisis (Moran, conteos por clase) — sin contar la hora
  // ni el contador "N resultados" que el texto del mensaje trae pegados
  expect(cuerpo(r), 'sin cifras del análisis').toMatch(/\d/)
  const ls = await capas(page)
  // Lotes, buffer y construcciones siguen; los clusters llegan como capa NUEVA (camino del
  // bucle: ws_spatial_autocorrelation) o como las mismas construcciones ENRIQUECIDAS con su
  // clase LISA (camino directo del sandbox, V3 F5). Lo que el usuario debe ver: una capa de
  // clusters con TODAS las construcciones, CLASIFICADA (H18: no un color único con una
  // narración que describe colores que no están).
  expect(ls.length).toBeGreaterThanOrEqual(3)
  const clusters = ls.find((c) => /LISA|cl[uú]ster/i.test(c.name))
  expect(clusters, `capa de clusters entre ${ls.map((c) => c.name).join(' | ')}`).toBeTruthy()
  expect(clusters!.featureCount).toBe(nConstrucciones)
  expect(clusters!.rendererKind).not.toBe('single_symbol')
})

test('E2.4 + E2.5 · recargar conserva las capas; otra sesión no las ve', async ({ page, browser }) => {
  test.setTimeout(600_000)
  await page.goto('/')
  await waitForMap(page)
  await turno(page, 'trae los lotes de la manzana 004503009')
  await expect.poll(async () => (await capas(page)).length).toBe(1)
  const sid = await page.evaluate(() => window.sessionStorage.getItem('geo.session'))
  const dataset = await page.evaluate(() => JSON.parse(
    window.sessionStorage.getItem(`geo.layers.${window.sessionStorage.getItem('geo.session')}`) ?? '[]',
  )[0]?.datasetId)
  expect(sid && dataset).toBeTruthy()

  await page.reload()
  await waitForMap(page)
  await expect.poll(async () => (await capas(page)).map((l: Win) => l.featureCount), { timeout: 30_000 }).toEqual([4])
  expect(await page.evaluate(() => window.sessionStorage.getItem('geo.session'))).toBe(sid)
  await evidencia(page, 'e24-recargada')

  // E2.5: OTRA sesión (otro contexto de navegador) pide esa capa por id
  const ctx = await browser.newContext()
  const otra = await ctx.newPage()
  await otra.goto('/')
  await waitForMap(otra)
  const sidB = await otra.evaluate(() => window.sessionStorage.getItem('geo.session'))
  expect(sidB).not.toBe(sid)
  const estados = await otra.evaluate(async ({ s, d }) => {
    // F6: con el token del usuario (la sesión es suya)
    const k = Object.keys(localStorage).find((x) => x.startsWith('oidc.user:'))
    const t = k ? JSON.parse(localStorage.getItem(k) || '{}').access_token : null
    const headers: Record<string, string> = t ? { Authorization: `Bearer ${t}` } : {}
    const r1 = await fetch(`/api/v1/workspace/${s}/datasets/${d}/capa`, { headers })
    const r2 = await fetch(`/api/v1/tiles/ws/${s}/${d}/0/0/0.pbf`, { headers })
    return [r1.status, r2.status]
  }, { s: sidB, d: dataset })
  expect(estados).toEqual([404, 404])
  const r = await turno(otra, `muéstrame la capa ${dataset}`)
  await evidencia(otra, 'e25-otra-sesion', { respuesta: r, estados })
  expect((await capas(otra)).some((l: Win) => l.featureCount === 4)).toBe(false)
  await ctx.close()
})
