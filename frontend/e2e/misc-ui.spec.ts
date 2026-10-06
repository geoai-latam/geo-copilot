import { test, expect } from '@playwright/test'
import { mockInit, mockQuery, queryResult, fc, json, waitForMap, waitForStyleLoaded, sendQuery, waitForFeatureCount } from './helpers'

/**
 * FUNCIONALIDAD varia: panel del esquema de BD (introspección real) y exportar
 * el resultado como GeoJSON.
 */
test.describe('Esquema de BD y exportación', () => {
  test('el panel "Base de datos" muestra el esquema introspectado', async ({ page }) => {
    await mockInit(page)
    await page.route('**/api/v1/metadata/tables', (r) => json(r, {
      connected: true,
      schemas: [
        {
          name: 'catastro',
          tables: [
            {
              name: 'lotes',
              qualified_name: 'catastro.lotes',
              columns: [
                { name: 'geom', is_geometry: true, data_type: 'geometry' },
                { name: 'uso', is_geometry: false, data_type: 'text' },
              ],
            },
          ],
        },
      ],
      total_tables: 1,
      total_schemas: 1,
    }))

    await page.goto('/')
    await waitForMap(page)
    await page.locator('[title="Base de datos"]').click()
    // El esquema real aparece (nombre de esquema catastro).
    await expect(page.getByText('catastro', { exact: false }).first()).toBeVisible({ timeout: 10_000 })
  })

  test('exportar GeoJSON descarga el resultado de la consulta', async ({ page }) => {
    await mockInit(page)
    await mockQuery(page, queryResult({ message: 'Traje 3 lotes.', geojson: fc(3) }))
    await page.goto('/')
    await waitForMap(page)
    await waitForStyleLoaded(page)
    await sendQuery(page, 'trae 3 lotes')
    await waitForFeatureCount(page, 3)

    // Click en "Exportar GeoJSON" → inicia una descarga.
    const [download] = await Promise.all([
      page.waitForEvent('download'),
      page.locator('[title="Exportar GeoJSON"]').click(),
    ])
    expect(download.suggestedFilename()).toContain('geodata')
    expect(download.suggestedFilename()).toContain('.geojson')
  })
})
