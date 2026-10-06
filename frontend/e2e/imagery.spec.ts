import { test, expect } from '@playwright/test'
import { capaRaster, mockInit, mockQuery, queryResult, waitForMap, waitForStyleLoaded, sendQuery, waitForLayerKind, type Win } from './helpers'

// PNG 1×1 transparente para las teselas del proxy.
const PNG_1x1 = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M8AAAMBAQDJ/pLvAAAAAElFTkSuQmCC',
  'base64',
)

/**
 * FUNCIONALIDAD: capas de imagery (raster ArcGIS/NDVI). Un resultado con
 * descriptor `external_imagery` añade una capa raster al mapa vía el proxy de
 * teselas. Se verifica por el oráculo (capa `arcgis-image`) que queda montado.
 */
test.describe('Capas de imagery (raster)', () => {
  test('un artefacto raster ArcGIS monta una capa raster en el mapa', async ({ page }) => {
    await mockInit(page)
    // Teselas del proxy → PNG (evita errores de red del source raster).
    await page.route('**/api/v1/proxy/imagery**', (r) =>
      r.fulfill({ status: 200, contentType: 'image/png', body: PNG_1x1 }))

    await mockQuery(page, queryResult({
      message: 'Cargué la ortofoto IGAC.',
      artifacts: [capaRaster({
        name: 'Ortofoto IGAC', arcgis: 'https://services.example.com/rest/services/Orto/ImageServer',
        bbox: [-74.2, 4.5, -74.0, 4.8],
      })],
    }))

    await page.goto('/')
    await waitForMap(page)
    await waitForStyleLoaded(page)

    await sendQuery(page, 'trae la ortofoto del IGAC')
    await expect(page.getByText('Cargué la ortofoto IGAC.')).toBeVisible({ timeout: 15_000 })

    // Queda una capa raster ArcGIS montada (oráculo + source en MapLibre).
    const id = await waitForLayerKind(page, 'arcgis-image')
    const tipo = await page.evaluate((i) => (window as Win).__mlmap.getSource(i).type, id)
    expect(tipo).toBe('raster')
  })
})
