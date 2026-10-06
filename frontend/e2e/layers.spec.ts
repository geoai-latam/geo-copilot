import { test, expect, type Page } from '@playwright/test'
import { mockInit, mockQuery, queryResult, fc, waitForMap, waitForStyleLoaded, sendQuery, waitForFeatureCount, type Win } from './helpers'

// Lee el estilo REAL de la capa de datos en el mapa (no el basemap/imagery/
// etiquetas) — para verificar visibilidad y opacidad sobre el MAPA, no solo
// sobre el DOM del panel.
async function dataLayerVisibility(page: Page): Promise<string> {
  return page.evaluate(() => {
    const map = (window as Win).__mlmap
    const dl = (map.getStyle().layers || []).find(
      (l: { source?: string; type?: string }) =>
        l.source && l.source !== 'basemap' && !String(l.source).startsWith('img-') && l.type !== 'symbol',
    )
    return dl?.layout?.visibility ?? 'visible'
  })
}
async function dataLayerOpacity(page: Page): Promise<number> {
  return page.evaluate(() => {
    const map = (window as Win).__mlmap
    const dl = (map.getStyle().layers || []).find(
      (l: { source?: string; type?: string }) =>
        l.source && l.source !== 'basemap' && !String(l.source).startsWith('img-') && l.type !== 'symbol',
    )
    if (!dl) return 1
    const prop = dl.type === 'fill' ? 'fill-opacity' : dl.type === 'line' ? 'line-opacity' : 'circle-opacity'
    const v = map.getPaintProperty(dl.id, prop)
    return typeof v === 'number' ? v : 1
  })
}

/**
 * FUNCIONALIDAD: panel de Capas (drawer). Tras una consulta que añade una capa,
 * el drawer la lista con su conteo; se puede ocultar/mostrar, ajustar opacidad
 * y eliminar. Camino real usuario → gestión de capas.
 */
test.describe('Panel de Capas', () => {
  test.beforeEach(async ({ page }) => {
    await mockInit(page)
    // F4: el nombre de la capa lo pone el backend (layer.name del contrato).
    await mockQuery(page, queryResult({ message: 'Traje 5 lotes.', geojson: fc(5), layerName: 'trae 5 lotes' }))
    await page.goto('/')
    await waitForMap(page)
    await waitForStyleLoaded(page)
    await sendQuery(page, 'trae 5 lotes')
    await waitForFeatureCount(page, 5)
    await page.locator('[title="Capas"]').click()
  })

  test('la capa aparece en el drawer con su conteo de features', async ({ page }) => {
    const item = page.locator('.layer-item').first()
    await expect(item).toBeVisible()
    await expect(item.locator('.layer-name')).toHaveText('trae 5 lotes')
    await expect(item.locator('.layer-sub')).toContainText('5 features')
  })

  test('ocultar/mostrar cambia la visibilidad REAL de la capa en el mapa', async ({ page }) => {
    const item = page.locator('.layer-item').first()
    // No basta con el label del botón: verificamos el estilo REAL del mapa
    // (layout.visibility de la capa de datos). Si ocultar no tocara el mapa
    // (solo el label), este test seguiría rojo.
    expect(await dataLayerVisibility(page)).toBe('visible')

    await item.locator('[title="Ocultar"]').click()
    await expect(item.locator('[title="Mostrar"]')).toBeVisible()
    await expect.poll(() => dataLayerVisibility(page)).toBe('none')

    await item.locator('[title="Mostrar"]').click()
    await expect(item.locator('[title="Ocultar"]')).toBeVisible()
    await expect.poll(() => dataLayerVisibility(page)).toBe('visible')
  })

  test('eliminar quita la capa del drawer y del mapa (featureCount → 0)', async ({ page }) => {
    await page.locator('.layer-item').first().locator('[title="Eliminar"]').click()
    await expect(page.locator('.layer-item')).toHaveCount(0)
    // El oráculo del mapa refleja que ya no hay features renderizadas.
    await waitForFeatureCount(page, 0)
  })

  test('mover el slider de opacidad cambia la opacidad REAL en el mapa', async ({ page }) => {
    const item = page.locator('.layer-item').first()
    // Al 100% inicialmente (la etiqueta y el paint del mapa).
    await expect(item.locator('.layer-sub')).toContainText('100%')
    expect(await dataLayerOpacity(page)).toBeGreaterThan(0.9)

    // Bajar a 30% → la etiqueta Y la opacidad del paint del mapa cambian. Si el
    // slider no estuviera cableado (solo "existe"), esto seguiría rojo.
    const slider = item.locator('.layer-opacity')
    await slider.fill('0.3')
    await expect(item.locator('.layer-sub')).toContainText('30%')
    await expect.poll(() => dataLayerOpacity(page)).toBeLessThan(0.5)
  })
})
