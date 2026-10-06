import { test, expect } from '@playwright/test'
import { mockInit, mockQuery, queryResult, fc, waitForMap, waitForStyleLoaded, sendQuery, waitForFeatureCount, type Win } from './helpers'

/**
 * FUNCIONALIDAD: simbología / re-estilo. Tras cargar una capa, el usuario pide
 * "coloréala por <campo>"; el backend responde intent=apply_symbology con la
 * clasificación y el frontend re-renderiza la capa. El oráculo
 * __mapTestState.rendererKind refleja el tipo de simbología aplicado.
 */
const GRADUATED = {
  symbology_type: 'graduated_colors',
  classification_field: 'valor',
  class_breaks: [
    { min_value: 0, max_value: 30, label: '0-30', color: '#fee5d9' },
    { min_value: 30, max_value: 60, label: '30-60', color: '#de2d26' },
  ],
}

test.describe('Simbología / re-estilo', () => {
  test('re-estilar por un campo aplica clasificación graduada (rendererKind)', async ({ page }) => {
    // Mock por texto: la 1ª query trae la capa; la de "colorea" la re-estila.
    await mockInit(page)
    await mockQuery(page, (body) => {
      const q = String((body as { query?: string } | null)?.query ?? '')
      if (/color|simbolog|estilo|graduad/i.test(q)) {
        return queryResult({
          message: 'Apliqué colores graduados por valor.',
          intent: 'apply_symbology',
          geojson: fc(6),
          symbology: GRADUATED,
        })
      }
      return queryResult({ message: 'Traje 6 lotes.', geojson: fc(6) })
    })

    await page.goto('/')
    await waitForMap(page)
    await waitForStyleLoaded(page)

    await sendQuery(page, 'trae 6 lotes')
    await waitForFeatureCount(page, 6)

    await sendQuery(page, 'coloréalos por valor')
    // El oráculo refleja la simbología graduada aplicada.
    await page.waitForFunction(
      () => (window as Win).__mapTestState?.rendererKind === 'graduated_colors',
      undefined,
      { timeout: 15_000 },
    )
    const kind = await page.evaluate(() => (window as Win).__mapTestState?.rendererKind)
    expect(kind).toBe('graduated_colors')

    // Confirmación honesta en el chat (no "no se encontraron resultados").
    await expect(page.getByText('Apliqué colores graduados por valor.')).toBeVisible()
    // Las features siguen renderizadas (no se perdió la capa al re-estilar).
    const fcount = await page.evaluate(() => (window as Win).__mapTestState?.featureCount)
    expect(fcount).toBe(6)
  })
})
