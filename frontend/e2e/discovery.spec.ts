import { test, expect } from '@playwright/test'
import { mockInit, json, fc, waitForMap, waitForStyleLoaded, waitForFeatureCount } from './helpers'

/**
 * FUNCIONALIDAD: Discovery (descubrir datos externos). El usuario abre el panel
 * "Descubrir", busca un dataset (ArcGIS Hub) y lo carga al mapa. Se mockean
 * /discovery/regions, /discovery/search y /discovery/load.
 */
const ITEM = {
  id: 'hub-item-1',
  source: 'arcgis',
  org: 'IGAC',
  title: 'Ortofoto Bogotá 2024',
  description: 'Ortofotografía de Bogotá',
  service_type: 'FeatureServer',
  service_url: 'https://services.example.com/FeatureServer/0',
}

test.describe('Discovery — buscar y cargar datos externos', () => {
  test('buscar muestra resultados y "Cargar al mapa" añade la capa', async ({ page }) => {
    await mockInit(page)
    await page.route('**/api/v1/discovery/regions', (r) => json(r, { regions: [], default_region: 'colombia' }))
    await page.route('**/api/v1/discovery/search', (r) => json(r, { items: [ITEM], total: 1 }))
    await page.route('**/api/v1/discovery/load', (r) => json(r, {
      type: 'geojson',
      name: 'Ortofoto Bogotá 2024',
      geojson: fc(4),
      symbology: null,
      total_available: 5311,
    }))

    await page.goto('/')
    await waitForMap(page)
    await waitForStyleLoaded(page)

    // Abrir el panel Descubrir y buscar.
    await page.locator('[title="Descubrir"]').click()
    const search = page.getByPlaceholder(/manzanas Bogotá/i)
    await expect(search).toBeVisible()
    await search.fill('ortofoto igac')

    // Aparece la tarjeta de resultado.
    const card = page.locator('.disc-card').filter({ hasText: 'Ortofoto Bogotá 2024' })
    await expect(card).toBeVisible({ timeout: 10_000 })

    // Cargar al mapa → la capa se renderiza (featureCount refleja las 4 features).
    await card.getByRole('button', { name: /Cargar al mapa/ }).click()
    await waitForFeatureCount(page, 4)
    // Pendiente del acta FH: el servicio tiene más de lo cargado → se dice
    await expect(page.getByText(/El servicio tiene 5[.,]?311: es una muestra, no el total/)).toBeVisible()
  })
})
