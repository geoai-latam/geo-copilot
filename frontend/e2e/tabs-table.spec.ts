import { test, expect } from '@playwright/test'
import { mockInit, mockQuery, queryResult, fc, waitForMap, waitForStyleLoaded, sendQuery, waitForFeatureCount } from './helpers'

/**
 * FUNCIONALIDAD: pestañas del canvas (Mapa / Resultados / SQL) + la tabla
 * de datos con paginación y ordenamiento. Camino: una consulta con 25 features
 * → el panel de resultados las muestra paginadas SIN tapar el mapa (F4, S4.3).
 */
test.describe('Pestañas y tabla de datos', () => {
  test.beforeEach(async ({ page }) => {
    await mockInit(page)
    await mockQuery(page, queryResult({
      message: 'Traje 25 lotes.',
      geojson: fc(25),
    }))
  })

  test('la tabla de la capa pagina en el panel de resultados y el mapa sigue visible', async ({ page }) => {
    await page.goto('/')
    await waitForMap(page)
    await waitForStyleLoaded(page)
    await sendQuery(page, 'trae 25 lotes')
    await waitForFeatureCount(page, 25)

    // Badge = cuántas vistas tiene el turno (aquí, la tabla de la capa). La
    // cuenta de filas va en el título de la sección, del mismo array (LIVE-03).
    const resultadosTab = page.getByRole('button', { name: /Resultados/ })
    await expect(resultadosTab.locator('.tab-badge')).toHaveText('1')

    // Turno solo con capa → se queda en el mapa; abrir el panel a mano.
    await resultadosTab.click()
    await expect(page.getByTestId('artefacto-table')).toContainText('25 filas')
    await expect(page.locator('.geo-table-container')).toBeVisible()
    // E4.1: el panel está acoplado, el mapa sigue a la vista.
    await expect(page.locator('.map-container')).toBeVisible()
    await expect(page.locator('.geo-table-pagination-info')).toHaveText('1-20 de 25')
    await expect(page.locator('.geo-table-pagination-page')).toHaveText('1 / 2')

    // Página siguiente → 21-25 de 25.
    await page.locator('[title="Pagina siguiente"]').click()
    await expect(page.locator('.geo-table-pagination-info')).toHaveText('21-25 de 25')
    await expect(page.locator('.geo-table-pagination-page')).toHaveText('2 / 2')

    // Volver al Mapa: el contenedor del mapa está visible de nuevo.
    await page.getByRole('button', { name: /Mapa/ }).click()
    await expect(page.locator('.map-container')).toBeVisible()
  })

  test('la tabla muestra columnas de las properties y ordena al click en la cabecera', async ({ page }) => {
    await page.goto('/')
    await waitForMap(page)
    await waitForStyleLoaded(page)
    await sendQuery(page, 'trae 25 lotes')
    await waitForFeatureCount(page, 25)

    await page.getByRole('button', { name: /Resultados/ }).click()
    const table = page.locator('.geo-table-container')
    await expect(table).toBeVisible()
    // Las properties (uso, valor, id) aparecen como cabeceras de columna.
    await expect(table.locator('th', { hasText: 'uso' })).toBeVisible()
    await expect(table.locator('th', { hasText: 'valor' })).toBeVisible()

    // Verificar que ORDENA de verdad, no solo que no se rompe. valor = i*10
    // (0,10,…,240). Sin ordenar la 1ª fila es valor=0; ordenando por valor DESC
    // (2 clicks: asc→desc) la 1ª fila debe ser el MÁXIMO (240). Si el sort no
    // hiciera nada, la 1ª fila seguiría en 0 → rojo.
    const firstRow = table.locator('.geo-table-row').first()
    await expect(firstRow).not.toContainText('240') // antes de ordenar
    const valorHeader = table.locator('th', { hasText: 'valor' })
    await valorHeader.click() // asc
    await valorHeader.click() // desc
    await expect(firstRow).toContainText('240') // el máximo quedó primero
  })
})
