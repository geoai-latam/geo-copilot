import { test, expect, type Page } from '@playwright/test'
import { mockAuth } from './helpers'

/**
 * E2E de paridad del motor de mapa MapLibre (FND-E2E-HARNESS).
 *
 * Cierra de forma DURABLE las validaciones que se hicieron a mano tras el
 * retire de Cesium: que MapLibre monta, que el selector de basemap repuesto
 * cambia los tiles, y que el botón "Vista mapa" re-centra. Usa los oráculos
 * window.__mapTestState / window.__mlmap (sin tocar el LLM).
 */


type Win = any

async function waitForMap(page: Page): Promise<void> {
  await page.waitForFunction(
    () => {
      const w = window as unknown as Win
      return w.__mapTestState?.engine === 'maplibre' && !!w.__mlmap
    },
    { timeout: 20_000 },
  )
}

test.describe('MapLibre — paridad post-retire de Cesium', () => {
  // F6: sin depender del backend real (que con OIDC pediría login)
  test.beforeEach(async ({ page }) => { await mockAuth(page) })

  test('monta MapLibre (oráculo engine=maplibre) y expone __mlmap', async ({ page }) => {
    await page.goto('/')
    await waitForMap(page)
    const engine = await page.evaluate(() => (window as unknown as Win).__mapTestState.engine)
    expect(engine).toBe('maplibre')
  })

  test('el selector de basemap cambia los tiles del source', async ({ page }) => {
    await page.goto('/')
    await waitForMap(page)
    const before = await page.evaluate(
      () => (window as unknown as Win).__mlmap.getStyle().sources.basemap.tiles[0],
    )
    await page.selectOption('.basemap-control select', 'arcgis-imagery')
    // TST-01: esperar la CONDICIÓN (tiles ya re-apuntados), no un tiempo fijo —
    // en CI el re-teselado puede tardar >600ms y el assert corría antes.
    await page.waitForFunction(
      (prev) => {
        const t = (window as unknown as Win).__mlmap.getStyle().sources.basemap.tiles[0]
        return t !== prev && String(t).includes('World_Imagery')
      },
      before,
      { timeout: 10_000 },
    )
    const after = await page.evaluate(
      () => (window as unknown as Win).__mlmap.getStyle().sources.basemap.tiles[0],
    )
    expect(after).not.toBe(before)
    expect(after).toContain('World_Imagery')
  })

  test('el botón "Vista mapa" re-centra el mapa en Bogotá', async ({ page }) => {
    await page.goto('/')
    await waitForMap(page)
    // Aleja la cámara a Cali, luego pide re-centrar.
    await page.evaluate(() =>
      (window as unknown as Win).__mlmap.jumpTo({ center: [-76.5, 3.42], zoom: 8 }),
    )
    // TST-01: jumpTo es síncrono → esperar que la cámara ESTÉ en Cali antes de
    // pedir el re-centro (sin timeout fijo).
    await page.waitForFunction(
      () => Math.abs((window as unknown as Win).__mlmap.getCenter().lng - -76.5) < 0.2,
      undefined,
      { timeout: 5_000 },
    )
    await page.click('[title="Vista mapa"]')
    // TST-01: esperar a que el flyTo TERMINE en Bogotá (isMoving=false), no un
    // tiempo fijo ni una tolerancia laxa — así el assert (precisión 0.05) no
    // corre a mitad de la animación.
    await page.waitForFunction(
      () => {
        const m = (window as unknown as Win).__mlmap
        const c = m.getCenter()
        return !m.isMoving() &&
          Math.abs(c.lng - -74.07) < 0.05 && Math.abs(c.lat - 4.71) < 0.05
      },
      undefined,
      { timeout: 12_000 },
    )
    const center = await page.evaluate(() => {
      const c = (window as unknown as Win).__mlmap.getCenter()
      return [c.lng, c.lat]
    })
    expect(center[0]).toBeCloseTo(-74.07, 1)
    expect(center[1]).toBeCloseTo(4.71, 1)
  })
})
