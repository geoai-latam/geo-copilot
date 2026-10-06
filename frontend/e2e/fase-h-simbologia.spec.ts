import { test, expect, type Page } from '@playwright/test'
import { fc, mockInit, mockQuery, queryResult, sendQuery, waitForFeatureCount, waitForMap, waitForStyleLoaded } from './helpers'

/**
 * FH.6 (V2) — simbología en los dos sentidos: el agente propone un graduado, el usuario
 * cambia la rampa a mano (queda FIJADA), el agente reclasifica en 7 clases y la rampa
 * del usuario sigue; si el agente cambia la rampa (porque se lo pidieron), el fijado cae.
 */

const RAMPAS: Record<string, string[]> = {
  viridis: ['#440154', '#3e4989', '#26838f', '#6cce5a', '#fde725'],
  Reds: ['#fee5d9', '#fcae91', '#fb6a4a', '#de2d26', '#a50f15'],
  Blues: ['#eff3ff', '#bdd7e7', '#6baed6', '#3182bd', '#08519c'],
}
const clases = (n: number, rampa: string) => Array.from({ length: n }, (_, i) => ({
  label: `${i * 10} – ${(i + 1) * 10}`, color: RAMPAS[rampa][i % 5], min_value: i * 10, max_value: (i + 1) * 10, count: 1,
}))
const graduado = (n: number, rampa: string, extra: Record<string, unknown> = {}) => ({
  symbology_type: 'graduated_colors', classification_field: 'valor', classification_method: 'natural_breaks',
  num_classes: n, color_scheme: rampa, class_breaks: clases(n, rampa), ...extra,
})

async function preparar(page: Page, demoraEstiloMs = 0) {
  const cuerpos: Array<Record<string, any>> = []
  const estilos: Array<Record<string, any>> = []
  await mockInit(page)
  await page.route('**/api/v1/workspace/rampas', (r) => r.fulfill({ status: 200, contentType: 'application/json',
    body: JSON.stringify({ rampas: RAMPAS }) }))
  // el backend calcula con el código del agente; aquí se simula con la misma forma
  await page.route('**/api/v1/workspace/e2e-session/estilo', async (r) => {
    const b = r.request().postDataJSON()
    estilos.push(b)
    if (demoraEstiloMs) await new Promise((res) => setTimeout(res, demoraEstiloMs))
    const d = b.diseno
    await r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
      style: { ...graduado(d.num_classes ?? 5, d.color_scheme ?? 'viridis'), layer_title: b.titulo, pinned: b.pinned } }) })
  })
  await mockQuery(page, (body) => {
    cuerpos.push(body as Record<string, any>)
    const n = cuerpos.length
    const id = (body as any).map_context?.layers?.[0]?.id
    if (n === 1) return queryResult({ message: 'Puntos.', geojson: fc(10), layerName: 'Puntos', symbology: graduado(5, 'viridis') })
    if (n === 2) return queryResult({ message: '7 clases.', intent: 'apply_symbology', target_layer_id: id, symbology: graduado(7, 'Reds') })
    return queryResult({ message: 'En azules.', intent: 'apply_symbology', target_layer_id: id, symbology: graduado(7, 'Blues') })
  })
  await page.goto('/')
  await waitForMap(page)
  await waitForStyleLoaded(page)
  await sendQuery(page, 'trae los puntos y gradúalos por valor')
  await waitForFeatureCount(page, 10)
  return { cuerpos, estilos }
}

test('DoD FH.6: la rampa fijada a mano sobrevive a «7 clases» del agente y cae si el agente la cambia', async ({ page }) => {
  const { cuerpos, estilos } = await preparar(page)

  // 1) el usuario cambia la rampa en el editor
  await page.locator('[title="Capas"]').click()
  await page.getByRole('button', { name: 'Estilo de Puntos' }).click()
  const editor = page.getByTestId('editor-estilo')
  await expect(editor.getByLabel('Rampa de color')).toHaveValue('viridis')
  await editor.getByLabel('Rampa de color').selectOption('Reds')
  await expect.poll(() => estilos.length).toBe(1)
  expect(estilos[0]).toMatchObject({ diseno: { symbology_type: 'graduated_colors', classification_field: 'valor',
                                               color_scheme: 'Reds', num_classes: 5 }, pinned: ['color_scheme'] })
  await expect(page.getByTestId('estilo-fijados')).toContainText('rampa')
  await expect(page.getByTestId('leyenda-capa')).toContainText('rampa Reds')

  // 2) el agente ve el estilo y lo fijado; responde con 7 clases en Reds: el fijado sigue
  await sendQuery(page, 'ahora clasifícalo en 7 clases')
  await expect.poll(() => cuerpos.length).toBe(2)
  expect(cuerpos[1].map_context.layers[0].style).toMatchObject({ color_scheme: 'Reds', pinned: ['color_scheme'] })
  await expect(page.getByTestId('leyenda-capa').locator('.map-legend-row')).toHaveCount(7)
  await expect(page.getByTestId('estilo-fijados')).toContainText('rampa')

  // 3) el agente cambia la rampa (se la pidieron): el fijado ya no está en pie
  await sendQuery(page, 'ponlo en azules')
  await expect.poll(() => cuerpos.length).toBe(3)
  await expect(page.getByTestId('leyenda-capa')).toContainText('rampa Blues')
  await expect(page.getByTestId('estilo-fijados')).toHaveCount(0)

  // Ctrl+Z vuelve a los Reds del turno anterior, con su fijado
  await page.locator('.map-container').click({ position: { x: 5, y: 300 } })
  await page.keyboard.press('Control+z')
  await expect(page.getByTestId('leyenda-capa')).toContainText('rampa Reds')
})

test('el editor: tocar las clases las fija y soltar el fijado lo quita', async ({ page }) => {
  const { estilos } = await preparar(page)
  await page.locator('[title="Capas"]').click()
  await page.getByRole('button', { name: 'Estilo de Puntos' }).click()
  const editor = page.getByTestId('editor-estilo')
  await editor.getByLabel('Número de clases').fill('4')
  await expect.poll(() => estilos.length).toBe(1)
  expect(estilos[0].pinned).toEqual(['num_classes'])
  await page.getByRole('button', { name: 'Soltar clases' }).click()
  await expect.poll(() => estilos.length).toBe(2)
  expect(estilos[1].pinned).toEqual([])
})

test('EH.5: pedir al agente justo después de tocar la rampa (el ajuste aún viaja) lleva el fijado', async ({ page }) => {
  const { cuerpos } = await preparar(page, 1200)
  await page.locator('[title="Capas"]').click()
  await page.getByRole('button', { name: 'Estilo de Puntos' }).click()
  await page.getByTestId('editor-estilo').getByLabel('Rampa de color').selectOption('Reds')
  await sendQuery(page, 'ahora clasifícalo en 7 clases') // sin esperar a que vuelva el estilo
  await expect.poll(() => cuerpos.length).toBe(2)
  expect(cuerpos[1].map_context.layers[0].style).toMatchObject({ color_scheme: 'Reds', pinned: ['color_scheme'] })
})
