import { test, expect, type Page } from '@playwright/test'

/**
 * E2E de INTEGRACIÓN — flujo agéntico REAL (LLM + PostGIS + HITL), sin mocks.
 * Requiere el stack levantado (docker compose up). Aserciones FLOJAS pero
 * reales: toleran que el LLM genere SQL/narración distinta cada vez, pero
 * fallan si el pipeline real se rompe (el LLM no genera SQL, el HITL no
 * aparece, PostGIS no devuelve, la capa no se renderiza).
 */

 
type Win = any

async function waitForMap(page: Page): Promise<void> {
  await page.waitForFunction(
    () => (window as Win).__mapTestState?.engine === 'maplibre' && !!(window as Win).__mlmap,
    undefined,
    { timeout: 30_000 },
  )
}

async function sendQuery(page: Page, text: string): Promise<void> {
  const input = page.getByPlaceholder(/Pregunta en lenguaje natural/i)
  await expect(input).toBeEnabled({ timeout: 20_000 })
  await input.fill(text)
  await input.press('Enter')
}

/** Espera el panel HITL real y devuelve el SQL que generó el LLM. */
async function waitForHitlSql(page: Page): Promise<string> {
  const preview = page.locator('.sql-preview')
  await expect(preview).toBeVisible({ timeout: 60_000 })
  return (await preview.textContent()) ?? ''
}

async function featureCount(page: Page): Promise<number> {
  return page.evaluate(() => (window as Win).__mapTestState?.featureCount ?? 0)
}

test.describe('Integración REAL — flujo agéntico contra el backend', () => {
  test('conteo: el LLM genera COUNT, el HITL lo pide, PostGIS lo resuelve', async ({ page }) => {
    await page.goto('/')
    await waitForMap(page)

    await sendQuery(page, '¿cuántos lotes hay en total en la base de datos?')

    // El LLM generó SQL de conteo y el HITL lo presenta (pipeline router→gis→HITL).
    const sql = await waitForHitlSql(page)
    expect(sql.toUpperCase()).toContain('COUNT') // invariante flojo: es un conteo
    expect(sql.toLowerCase()).toContain('lotes') // sobre la tabla de lotes

    // Aprobar → PostGIS ejecuta bajo gis_readonly → aparece un número grande.
    await page.getByRole('button', { name: /Aprobar y ejecutar/ }).click()
    // Aserción floja pero real: un número de 4+ dígitos (el conteo ~933k) aparece.
    await expect(page.getByText(/\d[\d.,]{3,}/).first()).toBeVisible({ timeout: 60_000 })
  })

  test('capa: una consulta espacial real renderiza polígonos en el mapa', async ({ page }) => {
    await page.goto('/')
    await waitForMap(page)
    expect(await featureCount(page)).toBe(0)

    await sendQuery(page, 'tráeme 60 lotes de catastro y muéstralos en el mapa')

    const sql = await waitForHitlSql(page)
    // Invariante flojo: es una consulta espacial sobre lotes con geometría.
    expect(sql.toLowerCase()).toContain('lotes')
    expect(sql.toLowerCase()).toMatch(/st_asgeojson|geom|shape/)

    await page.getByRole('button', { name: /Aprobar y ejecutar/ }).click()

    // Se renderizó una capa REAL: 0 < features (tolerante al nº exacto que elija
    // el LLM). Falla si PostGIS o el render se rompen.
    await expect.poll(() => featureCount(page), { timeout: 60_000 }).toBeGreaterThan(0)
    const n = await featureCount(page)
    expect(n).toBeLessThanOrEqual(1000) // sanity: no trajo la BD entera
  })

  test('HITL real: rechazar NO ejecuta ni renderiza capa', async ({ page }) => {
    await page.goto('/')
    await waitForMap(page)

    await sendQuery(page, 'muéstrame 40 construcciones en el mapa')
    await waitForHitlSql(page)

    // Rechazar → el panel se cierra y NO se renderiza nada.
    await page.getByRole('button', { name: /^Rechazar/ }).click()
    await expect(page.locator('.sql-preview')).toHaveCount(0, { timeout: 20_000 })
    // Damos un margen y verificamos que no apareció una capa.
    await page.waitForTimeout(2_000)
    expect(await featureCount(page)).toBe(0)
  })
})
