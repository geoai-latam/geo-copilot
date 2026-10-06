import { test, expect } from '@playwright/test'
import { capaRaster, fc, json, mockInit, mockQuery, queryResult, sendQuery, waitForFeatureCount, waitForMap, waitForStyleLoaded, type Win } from './helpers'

/**
 * FH.10 (V2) — herramientas SIG estándar: comparar con cortina, control de tiempo, medir,
 * vistas guardadas e identificar multi-capa. Las del agente llegan como órdenes al mapa.
 */

const ndvi = (fecha: string) => {
  const c = capaRaster({ id: `rast_${fecha}`, name: `NDVI ${fecha}`, url: `/api/v1/proxy/mcp/imagery/tiles/${fecha}/{z}/{x}/{y}.png`,
                         bbox: [-74.1, 4.6, -74.0, 4.7] })
  return { ...c, layer: { ...c.layer, time: { start: `${fecha}T00:00:00+00:00`, end: null, field: null } } }
}
const orden = (op: string, args: Record<string, unknown>) =>
  ({ kind: 'map_command', command: { op, layer_id: null, reason: null, args } }) as never

test('DoD FH.10: «compara el NDVI de marzo y junio» abre la cortina; el tiempo anima la serie', async ({ page }) => {
  const cuerpos: Array<Record<string, any>> = []
  await page.route('**/api/v1/proxy/mcp/imagery/tiles/**', (r) => r.fulfill({ status: 204 }))
  await mockInit(page)
  await mockQuery(page, (body) => {
    cuerpos.push(body as Record<string, any>)
    return queryResult({ message: 'Te dejo marzo a la izquierda y junio a la derecha.', artifacts: [
      ndvi('2026-03-14'), ndvi('2026-06-18'),
      orden('compare', { left: 'NDVI 2026-03-14', right: 'NDVI 2026-06-18' }),
    ] })
  })
  await page.goto('/')
  await waitForMap(page)
  await waitForStyleLoaded(page)
  await sendQuery(page, 'compara el NDVI de marzo y junio en esta zona')

  const cortina = page.getByTestId('cortina')
  await expect(cortina).toBeVisible()
  await expect(cortina).toContainText('NDVI 2026-03-14')
  await expect(cortina).toContainText('NDVI 2026-06-18')
  // el mapa de la cortina dibuja la capa de la derecha; el principal la oculta
  await expect.poll(() => page.evaluate(() => {
    const m = (window as Win).__mlmapCortina
    return !!m && Object.keys(m.getStyle().sources).some((s: string) => s.startsWith('raster-'))
  })).toBe(true)

  // arrastrar la cortina la mueve
  const linea = page.getByRole('separator', { name: 'Cortina de comparación' })
  const caja = (await page.locator('.map-container').boundingBox())!
  const l = (await linea.boundingBox())!
  await page.mouse.move(l.x + 2, l.y + l.height / 2)
  await page.mouse.down()
  await page.mouse.move(caja.x + caja.width * 0.3, l.y + l.height / 2, { steps: 5 })
  await page.mouse.up()
  await expect.poll(async () => Number(await linea.getAttribute('aria-valuenow'))).toBeLessThan(40)

  // el siguiente turno sabe que la comparación está abierta y la serie
  await page.getByRole('button', { name: 'Cerrar la comparación' }).click()
  await expect(cortina).toHaveCount(0)
  const tiempo = page.getByTestId('tiempo-control')
  await expect(tiempo).toBeVisible()
  await tiempo.getByRole('button', { name: '2026-03-14' }).click()
  await expect(page.getByTestId('tiempo-fecha')).toHaveText('2026-03-14')
  await tiempo.getByRole('button', { name: 'Animar la serie' }).click()
  await expect(page.getByTestId('tiempo-fecha')).toHaveText('2026-06-18', { timeout: 4000 })
  await sendQuery(page, '¿qué estoy viendo?')
  await expect.poll(() => cuerpos.length).toBe(2)
  expect(cuerpos[1].map_context.serie_tiempo.fechas).toEqual(['2026-03-14', '2026-06-18'])
  expect(cuerpos[1].map_context.layers.map((c: any) => c.fecha)).toEqual(['2026-03-14', '2026-06-18'])
})

test('medir (geodésico en el servidor), vistas guardadas e identificar varias capas', async ({ page }) => {
  const medidas: unknown[] = []
  const cuerpos: Array<Record<string, any>> = []
  await mockInit(page)
  await page.route('**/api/v1/workspace/e2e-session/medir', (r) => {
    medidas.push(r.request().postDataJSON())
    return json(r, { tipo: 'longitud', longitud_m: 1109.29, longitud_km: 1.1093 })
  })
  await mockQuery(page, (body) => {
    cuerpos.push(body as Record<string, any>)
    return cuerpos.length === 1
      ? queryResult({ message: 'Puntos.', geojson: fc(3, (i) => ({ n: i })), layerName: 'Puntos A', layerId: 'mem-a' })
      : cuerpos.length === 2
        ? queryResult({ message: 'Más.', geojson: fc(3, (i) => ({ m: i * 10 })), layerName: 'Puntos B', layerId: 'mem-b' })
        : queryResult({ message: 'Ok.' })
  })
  await page.goto('/')
  await waitForMap(page)
  await waitForStyleLoaded(page)
  await sendQuery(page, 'trae A')
  await waitForFeatureCount(page, 3)
  await sendQuery(page, 'trae B')
  await expect.poll(() => page.evaluate(() => (window as Win).__mapTestState.layers.length)).toBe(2)
  await page.evaluate(() => (window as Win).__mlmap.jumpTo({ center: [-74.07, 4.6], zoom: 13 }))
  await page.waitForFunction(() => !(window as Win).__mlmap.isMoving())

  // identificar: en el punto hay un elemento de A y uno de B (mismas coordenadas)
  const p = await page.evaluate(() => {
    const map = (window as Win).__mlmap
    const r = map.getCanvas().getBoundingClientRect()
    const q = map.project([-74.07, 4.6])
    return { x: r.left + q.x, y: r.top + q.y }
  })
  await page.mouse.click(p.x, p.y)
  await expect(page.getByTestId('identificar-seccion')).toHaveCount(2)

  // medir una distancia: el servidor mide (geodésico) y no queda ninguna capa nueva
  await page.getByRole('button', { name: 'Medir una distancia' }).click()
  const c = (await page.locator('.maplibregl-canvas').boundingBox())!
  await page.mouse.click(c.x + c.width * 0.45, c.y + c.height * 0.3)
  await page.mouse.dblclick(c.x + c.width * 0.7, c.y + c.height * 0.3)
  await expect(page.getByTestId('medicion')).toContainText('1.109,29 m')
  expect((medidas[0] as { geometry: { type: string } }).geometry.type).toBe('LineString')
  expect(await page.evaluate(() => (window as Win).__mapTestState.layers.length)).toBe(2)

  await page.getByRole('button', { name: 'Cerrar popup' }).click()
  // vistas: guardar la actual y que el agente la vea
  await page.getByRole('button', { name: 'Vistas guardadas' }).click()
  await page.getByLabel('Nombre de la vista').fill('Centro')
  await page.getByRole('button', { name: 'Guardar esta vista' }).click()
  await sendQuery(page, 've a Centro')
  await expect.poll(() => cuerpos.length).toBe(3)
  expect(cuerpos[2].map_context.vistas).toEqual([{ nombre: 'Centro', bbox: expect.any(Array) }])
})
