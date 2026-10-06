import { test, expect } from '@playwright/test'
import { fc, json, mockInit, mockQuery, queryResult, sendQuery, waitForFeatureCount, waitForMap, waitForStyleLoaded, type Win } from './helpers'

/**
 * FH.7 (V2) — respuestas ancladas al mapa: los enlaces [[layer:…]] de la respuesta
 * resaltan al pasar el ratón y, con clic, seleccionan y encuadran el elemento; el agente
 * lo ve en el turno siguiente (origen «link»). Un enlace a algo que no existe es texto.
 * El panel «cómo se hizo» de la capa muestra su procedencia (SQL) y la de sus entradas.
 */

const DS = 'ds_00000000000000aa'
// fc(n): puntos en (-74.08 + (i % 5) * 0.01, 4.6 + floor(i / 5) * 0.01); el 7 = (-74.06, 4.61)
const MENSAJE = 'El de mayor valor es [[layer:activa?id=7|el punto 7]], de la capa [[layer:activa|Puntos]]. '
  + 'Ver también [[layer:activa?id=99|el punto 99]].'

test('DoD FH.7: el enlace de la respuesta resalta, selecciona y encuadra; el agente lo sabe', async ({ page }) => {
  const cuerpos: Array<Record<string, any>> = []
  await mockInit(page)
  await mockQuery(page, (body) => {
    cuerpos.push(body as Record<string, any>)
    return cuerpos.length === 1
      ? queryResult({ message: MENSAJE, geojson: fc(10), layerId: DS, layerName: 'Puntos' })
      : queryResult({ message: 'Entendido.' })
  })
  await page.goto('/')
  await waitForMap(page)
  await waitForStyleLoaded(page)
  await sendQuery(page, '¿cuál es el punto de mayor valor?')
  await waitForFeatureCount(page, 10)

  const enlaces = page.getByTestId('ref-mapa')
  await expect(enlaces).toHaveCount(2)
  await expect(enlaces.first()).toHaveText('el punto 7')
  await expect(page.getByTestId('ref-rota')).toHaveText('el punto 99') // no existe: solo texto

  // pasar el ratón: el resaltado lo incluye (sin tocar la selección)
  const capaId = await page.evaluate(() => (window as Win).__mapTestState.layers[0].id as string)
  await enlaces.first().hover()
  await expect.poll(() => page.evaluate((id) => JSON.stringify((window as Win).__mlmap.getFilter(`${id}-sel-circle`)), capaId))
    .toContain('[7]')
  await page.mouse.move(5, 5)
  await expect.poll(() => page.evaluate((id) => JSON.stringify((window as Win).__mlmap.getFilter(`${id}-sel-circle`)), capaId))
    .not.toContain('[7]')

  // clic: seleccionado y encuadrado
  await enlaces.first().click()
  await expect.poll(() => page.evaluate(() => (window as Win).__mapTestState.layers[0].seleccionados)).toBe(1)
  await page.waitForFunction(() => !(window as Win).__mlmap.isMoving())
  const centro = await page.evaluate(() => (window as Win).__mlmap.getCenter().toArray() as number[])
  expect(Math.abs(centro[1] - 4.61)).toBeLessThan(0.005)
  expect(await page.evaluate(() => (window as Win).__mlmap.getZoom())).toBeGreaterThan(15)

  // el turno siguiente lleva la selección, hecha desde un enlace
  await sendQuery(page, '¿y qué uso tiene?')
  await expect.poll(() => cuerpos.length).toBe(2)
  expect(cuerpos[1].map_context.layers[0].seleccion).toMatchObject({ ids: [7], origin: 'link' })
})

test('«cómo se hizo»: la procedencia de la capa y de sus entradas, con el SQL', async ({ page }) => {
  await mockInit(page)
  await page.route(`**/api/v1/workspace/e2e-session/datasets/${DS}/procedencia`, (r) => json(r, { pasos: [
    { dataset_id: DS, nombre: 'Puntos', disponible: true, provenance: {
      capability: 'core.buffer', arguments: { dataset: 'ds_00000000000000bb', meters: 100 }, produced_at: '2026-09-26T10:00:00Z',
      sql: null, code: null, source_version: null,
      edits: [{ capability: 'core.add_measure', arguments: { measure: 'area', campo: 'area_m2' }, produced_at: '2026-09-26T10:05:00Z' }] } },
    { dataset_id: 'ds_00000000000000bb', nombre: 'Lotes', disponible: true, provenance: {
      capability: 'core.query_data', arguments: { query: 'los lotes' }, produced_at: '2026-09-26T09:59:00Z',
      sql: "SELECT * FROM catastro.lotes WHERE manzcodigo = '008510017'", code: null, source_version: null, edits: [] } },
  ] }))
  await mockQuery(page, () => queryResult({ message: 'Listo.', geojson: fc(3), layerId: DS, layerName: 'Puntos' }))
  await page.goto('/')
  await waitForMap(page)
  await sendQuery(page, 'buffer de 100 m de los lotes')
  await waitForFeatureCount(page, 3)

  await page.locator('[title="Capas"]').click()
  await page.getByRole('button', { name: 'Cómo se hizo Puntos' }).click()
  const panel = page.getByTestId('como-se-hizo')
  await expect(panel).toContainText('Buffer')
  await expect(panel).toContainText('«Lotes»')        // la entrada, por su nombre
  await expect(panel).toContainText('Medida de cada elemento')
  await expect(panel).toContainText('Consulta a la base de datos')
  await expect(panel).toContainText("manzcodigo = '008510017'")
})
