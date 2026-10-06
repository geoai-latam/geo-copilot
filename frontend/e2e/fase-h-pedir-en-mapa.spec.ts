import { test, expect, type Page } from '@playwright/test'
import { fc, mockInit, mockQuery, queryResult, sendQuery, waitForFeatureCount, waitForMap, waitForStyleLoaded, type Win } from './helpers'

/**
 * FH.9 (V2) — el agente pide algo EN el mapa (`request_input`) y el turno sigue con la
 * respuesta: la consulta ORIGINAL vuelve con `map_context.respuesta_mapa`.
 */

const pedido = (mode: string, prompt: string) => ({
  kind: 'map_command', command: { op: 'request_input', layer_id: null, reason: null, args: { mode, prompt } },
}) as never

async function preparar(page: Page, primera: unknown) {
  const cuerpos: Array<Record<string, any>> = []
  await mockInit(page)
  await mockQuery(page, (body) => {
    cuerpos.push(body as Record<string, any>)
    return cuerpos.length === 1 ? primera : queryResult({ message: 'Cerca hay 4 construcciones.' })
  })
  await page.goto('/')
  await waitForMap(page)
  await waitForStyleLoaded(page)
  return cuerpos
}

test('DoD FH.9: «¿qué hay cerca?» → el agente pide un punto, el usuario hace clic y el turno sigue', async ({ page }) => {
  const cuerpos = await preparar(page, queryResult({
    message: 'Marca en el mapa el punto de referencia.', intent: 'clarify',
    artifacts: [pedido('pick_point', 'Marca en el mapa el punto de referencia.')],
  }))
  await sendQuery(page, '¿qué hay cerca?')
  const barra = page.getByTestId('pedido-mapa')
  await expect(barra).toContainText('Marca en el mapa el punto de referencia.')
  await expect(barra).toContainText('Haz clic en el mapa.')

  const canvas = page.locator('.maplibregl-canvas')
  const caja = (await canvas.boundingBox())!
  await page.mouse.click(caja.x + caja.width * 0.6, caja.y + caja.height * 0.5)

  await expect.poll(() => cuerpos.length).toBe(2)
  expect(cuerpos[1].query).toBe('¿qué hay cerca?') // la consulta ORIGINAL
  expect(cuerpos[1].map_context.respuesta_mapa).toMatchObject({ modo: 'pick_point', pedido: 'Marca en el mapa el punto de referencia.' })
  expect(cuerpos[1].map_context.clicked_point).toMatchObject({ lon: expect.any(Number), lat: expect.any(Number) })
  await expect(page.getByText('📍 Punto marcado en el mapa')).toBeVisible()
  await expect(barra).toHaveCount(0)
  await expect(page.getByText('Cerca hay 4 construcciones.')).toBeVisible()
})

test('elegir una capa; y cancelar se le dice al agente', async ({ page }) => {
  const cuerpos: Array<Record<string, any>> = []
  await mockInit(page)
  await mockQuery(page, (body) => {
    cuerpos.push(body as Record<string, any>)
    const n = cuerpos.length
    if (n === 1) return queryResult({ message: 'Puntos.', geojson: fc(5), layerName: 'Puntos', layerId: 'mem-puntos' })
    if (n === 2 || n === 4) return queryResult({ message: '¿Qué capa?', artifacts: [pedido('pick_layer', '¿Sobre qué capa?')] })
    return queryResult({ message: 'Hecho.' })
  })
  await page.goto('/')
  await waitForMap(page)
  await sendQuery(page, 'trae los puntos')
  await waitForFeatureCount(page, 5)

  await sendQuery(page, 'cuéntalos')
  const barra = page.getByTestId('pedido-mapa')
  await barra.getByLabel('Capa').selectOption({ label: 'Puntos' })
  await barra.getByRole('button', { name: /Usar/ }).click()
  await expect.poll(() => cuerpos.length).toBe(3)
  const capaId = await page.evaluate(() => (window as Win).__mapTestState.layers[0].id)
  expect(cuerpos[2].query).toBe('cuéntalos')
  expect(cuerpos[2].map_context.respuesta_mapa).toMatchObject({ modo: 'pick_layer', layer_id: capaId })
  await expect(page.getByText('🗂️ Capa elegida: Puntos')).toBeVisible()

  await sendQuery(page, 'cuéntalos otra vez')
  await barra.getByRole('button', { name: 'No señalar nada' }).click()
  await expect.poll(() => cuerpos.length).toBe(5)
  expect(cuerpos[4].map_context.respuesta_mapa).toMatchObject({ modo: 'pick_layer', cancelado: true })
})

test('escribir otra cosa mientras espera deja el pedido sin responder', async ({ page }) => {
  const cuerpos = await preparar(page, queryResult({
    message: 'Marca el punto.', artifacts: [pedido('pick_point', 'Marca el punto.')] }))
  await sendQuery(page, '¿qué hay cerca?')
  await expect(page.getByTestId('pedido-mapa')).toBeVisible()
  await sendQuery(page, 'olvídalo, trae los lotes')
  await expect.poll(() => cuerpos.length).toBe(2)
  expect(cuerpos[1].map_context.respuesta_mapa).toBeUndefined()
  await expect(page.getByTestId('pedido-mapa')).toHaveCount(0)
})

test('V5: marcar el punto pedido ENCIMA de un elemento no lo selecciona (solo responde al pedido)', async ({ page }) => {
  const zona = {
    type: 'FeatureCollection',
    features: [{ type: 'Feature', properties: { nombre: 'Zona' },
      geometry: { type: 'Polygon', coordinates: [[[-74.2, 4.5], [-73.9, 4.5], [-73.9, 4.8], [-74.2, 4.8], [-74.2, 4.5]]] } }],
  }
  const cuerpos = await preparar(page, queryResult({
    message: 'Marca el punto.', intent: 'clarify', geojson: zona, layerName: 'Zona',
    artifacts: [pedido('pick_point', 'Marca el punto.')],
  }))
  await sendQuery(page, '¿cuántos hay a menos de 5 km de aquí?')
  await waitForFeatureCount(page, 1)
  await expect(page.getByTestId('pedido-mapa')).toBeVisible()
  const caja = (await page.locator('.maplibregl-canvas').boundingBox())!
  await page.mouse.click(caja.x + caja.width * 0.5, caja.y + caja.height * 0.5)
  await expect.poll(() => cuerpos.length).toBe(2)
  const capas = cuerpos[1].map_context.layers as Array<Record<string, unknown>>
  expect(capas.every((l) => !l.seleccion)).toBe(true)
  expect(cuerpos[1].map_context.clicked_point).toMatchObject({ lon: expect.any(Number), lat: expect.any(Number) })
})
