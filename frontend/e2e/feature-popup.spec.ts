import { test, expect } from '@playwright/test'
import { mockInit, mockQuery, queryResult, fc, waitForMap, waitForStyleLoaded, sendQuery, waitForFeatureCount, type Win } from './helpers'

/**
 * FUNCIONALIDAD: picking vectorial. Al hacer click sobre una feature GeoJSON
 * renderizada aparece el popup con sus atributos. (El identify de capas RASTER
 * —FRT-03— está cubierto por su test unitario imageryIdentify.test.ts, ya que
 * requiere un servicio ArcGIS y no hay uno en el arnés determinista.)
 */
test.describe('Popup de feature (picking vectorial)', () => {
  test('click sobre una feature muestra el popup con sus atributos', async ({ page }) => {
    await mockInit(page)
    await mockQuery(page, queryResult({
      message: 'Traje lotes.',
      geojson: fc(5, (i) => ({ id: i, uso: 'residencial', estrato: 3 })),
    }))
    await page.goto('/')
    await waitForMap(page)
    await waitForStyleLoaded(page)

    await sendQuery(page, 'trae lotes')
    await waitForFeatureCount(page, 5)

    // Esperar a que el fitBounds a la capa termine (mapa quieto) para proyectar
    // la coordenada de una feature a píxeles de pantalla de forma estable.
    await page.waitForFunction(() => !(window as Win).__mlmap.isMoving(), undefined, { timeout: 8_000 })
    const pt = await page.evaluate(() => {
      const map = (window as Win).__mlmap
      const p = map.project([-74.08, 4.6]) // feature 0, relativo al canvas
      // project() es relativo al canvas del mapa; el click de Playwright usa
      // coords del viewport → sumar el offset del canvas.
      const rect = map.getCanvas().getBoundingClientRect()
      return { x: Math.round(rect.left + p.x), y: Math.round(rect.top + p.y) }
    })

    await page.mouse.click(pt.x, pt.y)

    // Aparece el popup con la capa y los atributos de la feature.
    const popup = page.locator('.feature-popup')
    await expect(popup).toBeVisible({ timeout: 8_000 })
    await expect(popup.locator('.feature-popup-list')).toContainText('residencial')
  })
})
