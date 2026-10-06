import { test, expect, type Page } from '@playwright/test'

/**
 * E2E de INTEGRACIÓN del MCP de imagery — computa NDVI de Sentinel-2 de VERDAD
 * (panel genérico → hub MCP → imagery-mcp → STAC → COGs de Planetary Computer). Sin mocks. Requiere el
 * stack levantado (incluye el contenedor geo_copilot_imagery) y salida a
 * internet. Aserción floja pero real: el NDVI medio es un número en [-1, 1].
 */

type Win = any

async function waitForMap(page: Page): Promise<void> {
  await page.waitForFunction(
    () => (window as Win).__mapTestState?.engine === 'maplibre' && !!(window as Win).__mlmap,
    undefined,
    { timeout: 30_000 },
  )
}

test.describe('Integración REAL — MCP de imagery (NDVI)', () => {
  // El fetch de COGs de Sentinel-2 puede tardar en frío.
  test.setTimeout(180_000)

  test('NDVI: el imagery-mcp computa el índice de Sentinel-2 sobre el viewport', async ({ page }) => {
    await page.goto('/')
    await waitForMap(page)

    // AOI pequeño (< 2500 km² del límite del servicio): acercar la cámara a una
    // zona de Bogotá con vegetación.
    await page.evaluate(() =>
      (window as Win).__mlmap.jumpTo({ center: [-74.05, 4.66], zoom: 13 }))
    await page.waitForFunction(() => !(window as Win).__mlmap.isMoving(), undefined, { timeout: 8_000 })

    // F3: el panel es GENÉRICO — la tool y su formulario salen del hub.
    await page.locator('[title="Herramientas"]').click()
    const panel = page.locator('[data-testid="mcp-tools-panel"]')
    await expect(panel).toBeVisible()
    await panel.getByLabel('Herramienta').selectOption('imagery/imagery_ndvi')

    // Período FIJO de temporada seca (enero 2026). Con el default (últimos 45
    // días) el test dependía del clima: en época de lluvias no hay escenas
    // despejadas y el servicio responde, con razón, "no hay escenas" (pasó en
    // la validación de F1). Aquí se prueba el pipeline STAC→COG→NDVI, no el clima.
    const form = panel.locator('[data-testid="mcp-tool-form"]')
    await form.getByLabel('Date From').fill('2026-01-01')
    await form.getByLabel('Date To').fill('2026-02-28')

    // AOI por defecto = zona visible del mapa.
    await expect(form.getByLabel('Aoi Geojson')).toHaveValue('viewport')
    await panel.getByRole('button', { name: 'Ejecutar' }).click()

    // imagery-mcp busca la mejor escena, descarga los COGs y computa NDVI; el
    // resultado llega por el hub → proxy genérico de teselas.
    const result = page.locator('[data-testid="mcp-tool-result"]')
    await expect(result).toBeVisible({ timeout: 150_000 })

    // El NDVI medio es un número físicamente válido [-1, 1] → el MCP computó de
    // verdad (no un placeholder). Falla si el pipeline STAC→COG→NDVI se rompe.
    const fila = result.locator('tr', { has: page.locator('td', { hasText: /^mean$/ }) })
    const mean = parseFloat(((await fila.locator('td').nth(1).textContent()) ?? '').replace(',', '.'))
    expect(Number.isFinite(mean)).toBe(true)
    expect(mean).toBeGreaterThanOrEqual(-1)
    expect(mean).toBeLessThanOrEqual(1)

    // La capa NDVI se dibuja con teselas del proxy genérico (/proxy/mcp/imagery/…).
    await page.waitForFunction(() => {
      const st = (window as Win).__mlmap.getStyle()
      return Object.values(st.sources).some((s: Win) =>
        (s.tiles ?? []).some((t: string) => t.includes('/api/v1/proxy/mcp/imagery/tiles/')))
    }, undefined, { timeout: 20_000 })
  })
})
