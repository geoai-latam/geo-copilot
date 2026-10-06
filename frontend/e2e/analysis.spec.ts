import { test, expect } from '@playwright/test'
import { mockInit, mockQuery, queryResult, waitForMap, sendQuery } from './helpers'

/**
 * FUNCIONALIDAD: resultados ANALÍTICOS. Una consulta de agregación produce un
 * gráfico (panel de Resultados) y expone el SQL (pestaña SQL). El camino cubre el
 * canal de visualización chart + el bloque de SQL.
 */
test.describe('Análisis: gráfico + SQL', () => {
  test('una agregación muestra un gráfico en Resumen y el SQL en su pestaña', async ({ page }) => {
    await mockInit(page)
    await mockQuery(page, queryResult({
      message: 'Conteo de lotes por uso.',
      sql: 'SELECT uso, COUNT(*) AS conteo FROM catastro.lotes GROUP BY uso',
      visualizations: [{
        type: 'chart',
        config: { chart_type: 'bar', x_key: 'uso', y_key: 'conteo' },
        data: [
          { uso: 'residencial', conteo: 120 },
          { uso: 'comercial', conteo: 45 },
          { uso: 'industrial', conteo: 18 },
        ],
      }],
    }))

    await page.goto('/')
    await waitForMap(page)
    await sendQuery(page, 'cuenta lotes por uso')
    await expect(page.getByText('Conteo de lotes por uso.')).toBeVisible({ timeout: 15_000 })

    // El panel de resultados se abre solo con el gráfico (recharts, SVG).
    await expect(page.getByTestId('artefacto-chart').locator('svg.recharts-surface').first()).toBeVisible({ timeout: 10_000 })

    // Pestaña SQL → el SQL generado se muestra.
    await page.getByRole('button', { name: /^SQL/ }).click()
    await expect(page.getByText(/GROUP BY uso/)).toBeVisible()
  })
})
