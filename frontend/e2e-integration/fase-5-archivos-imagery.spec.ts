import { execSync } from 'node:child_process'
import { test, expect, type Page } from '@playwright/test'

/**
 * FASE 5 (T5.5, T5.6) contra el stack REAL (V3): LLM real, PostGIS, archivos-mcp (DuckDB) e
 * imagery-mcp (Planetary Computer). Las referencias salen de la BD con psql, no del agente:
 *
 *   E5.5: dibujar un rectángulo sobre Chapinero → «carga los predios del GeoParquet que caen en
 *         @Área 1» → la capa trae EXACTAMENTE los lotes que PostGIS cuenta intersecando ese
 *         mismo dibujo (el GeoParquet es un recorte de catastro.lotes).
 *   E5.4: 30 lotes → «NDVI de cada lote con imágenes Landsat» → capa de 30 con NDVI y, en la
 *         procedencia del dataset, `collection: landsat-c2-l2` (no lo que diga el texto).
 *
 * Requiere: stack con perfil `connectors` (archivos-mcp con data/archivos de
 * `python scripts/demo_geoparquet.py`), Internet (imagery) y `npm run dev` en 5173.
 *   E2E_REAL_URL=http://localhost:5173 npx playwright test --config playwright.integration.config.ts fase-5-archivos-imagery
 */

type Win = any
const CHAPINERO_BBOX = '-74.075, 4.625, -74.045, 4.665' // el recorte que exporta scripts/demo_geoparquet.py

function psql(sql: string): string {
  const env = (n: string) => execSync(`docker exec geo_copilot_db printenv ${n}`).toString().trim()
  return execSync(`docker exec geo_copilot_db psql -U ${env('POSTGRES_USER')} -d ${env('POSTGRES_DB')} -tAc "${sql}"`)
    .toString().trim()
}

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

async function esperarTurno(page: Page): Promise<string> {
  const n = await page.locator('.msg-assist').count()
  const hitl = page.getByRole('button', { name: /Aprobar y ejecutar/ })
  const deadline = Date.now() + 240_000
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

async function capas(page: Page): Promise<{ name: string, featureCount: number }[]> {
  return page.evaluate(() => ((window as Win).__mapTestState?.layers ?? []).map((l: Win) => ({ name: l.name, featureCount: l.featureCount })))
}

test.describe('Integración REAL — F5 archivos (DuckDB) e imagery (Landsat)', () => {
  test.setTimeout(420_000)

  test('E5.5: los predios del GeoParquet que caen en @Área 1 = PostGIS sobre el mismo dibujo', async ({ page }) => {
    await page.goto('/')
    await page.evaluate(() => window.sessionStorage.clear())
    await page.reload()
    await waitForMap(page)
    await page.evaluate(() => (window as Win).__mlmap.jumpTo({ center: [-74.058, 4.645], zoom: 15 }))
    await page.waitForFunction(() => !(window as Win).__mlmap.isMoving())
    await page.getByRole('button', { name: /Dibujar un polígono/ }).click()
    const c = (await page.locator('.maplibregl-canvas').boundingBox())!
    const pts = [[0.45, 0.35], [0.62, 0.35], [0.62, 0.6], [0.45, 0.6]]
    for (const [x, y] of pts) await page.mouse.click(c.x + c.width * x, c.y + c.height * y)
    await page.mouse.click(c.x + c.width * pts[0][0], c.y + c.height * pts[0][1])
    await expect.poll(() => capas(page).then((ls) => ls.some((l) => /^Área/.test(l.name))), { timeout: 30_000 }).toBe(true)

    await sendQuery(page, 'carga los predios del GeoParquet que caen en el área que acabo de dibujar')
    const r = await esperarTurno(page)

    // referencia independiente: los lotes que PostGIS cuenta sobre EL MISMO dibujo guardado
    const dibujo = psql("SELECT schema_name||'.'||table_name FROM ws_meta.datasets WHERE name LIKE 'Área%' ORDER BY created_at DESC LIMIT 1")
    const ref = Number(psql(`SELECT count(*) FROM catastro.lotes l, ${dibujo} a WHERE l.shape && ST_MakeEnvelope(${CHAPINERO_BBOX}, 4326) AND ST_Intersects(l.shape, a.geom)`))
    expect(ref).toBeGreaterThan(100)
    await expect.poll(() => capas(page).then((ls) => ls.map((l) => l.featureCount)), { timeout: 60_000 }).toContain(ref)
    expect(r.replace(/[.,](?=\d{3}\b)/g, ''), r).toContain(String(ref))
  })

  test('E5.4: el NDVI por lote con Landsat usa Landsat (procedencia del dataset)', async ({ page }) => {
    await page.goto('/')
    await page.evaluate(() => window.sessionStorage.clear())
    await page.reload()
    await waitForMap(page)
    await sendQuery(page, 'tráeme 30 lotes del catastro en el mapa')
    await esperarTurno(page)
    await expect.poll(() => capas(page).then((ls) => ls.some((l) => l.featureCount === 30)), { timeout: 60_000 }).toBe(true)

    await sendQuery(page, 'calcula el NDVI de cada uno de estos lotes con imágenes Landsat de los últimos 4 meses')
    const r = await esperarTurno(page)
    expect(r).toMatch(/Landsat/i)
    // el dataset NDVI por lote más reciente: su procedencia dice qué colección se usó
    const prov = psql("SELECT layer_ref::text FROM ws_meta.datasets WHERE layer_ref::text ILIKE '%imagery_zonal_stats%' ORDER BY created_at DESC LIMIT 1")
    expect(prov).toContain('landsat-c2-l2')
    await expect.poll(() => capas(page).then((ls) => ls.filter((l) => l.featureCount === 30).length), { timeout: 30_000 }).toBeGreaterThanOrEqual(2)
  })
})
