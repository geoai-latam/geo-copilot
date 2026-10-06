import { test, expect, type Page } from '@playwright/test'
import { json, mockInit, mockQuery, queryResult, sendQuery, waitForFeatureCount, waitForMap, waitForStyleLoaded, type Win } from './helpers'

/**
 * FH.8 (V2) — acciones contextuales y sugerencias.
 * Clic derecho sobre un polígono → queda seleccionado y el menú muestra las acciones que
 * admite (aquí, una tool de un MCP que el frontend no conoce); su formulario sale del
 * input_schema; ejecutar añade la capa. Bajo la última respuesta, las sugerencias del LLM se
 * envían con un clic.
 */

const poligono = (i: number) => {
  const x = -74.07 + i * 0.01
  return { type: 'Feature', properties: { lotcodigo: `L${i}` },
           geometry: { type: 'Polygon', coordinates: [[[x, 4.6], [x + 0.008, 4.6], [x + 0.008, 4.608], [x, 4.608], [x, 4.6]]] } }
}
const LOTES = { type: 'FeatureCollection', features: [0, 1, 2].map(poligono) }

const ACCION = {
  server: 'suelos', tool: 'textura', herramienta: 'suelos__textura', titulo: 'Textura del suelo por zona',
  description: 'Textura del suelo', objetivo: 'zona', costo: 'medium', riesgo: 'read', estado: 'habilitada',
  input_schema: { properties: { zona: { type: 'string' }, profundidad: { type: 'integer', title: 'Profundidad (cm)' } },
                  required: ['zona', 'profundidad'] },
  geo: { inputs: { zona: { accepts: ['geometry', 'layer_ref'], geometry_types: ['Polygon', 'MultiPolygon'] } } },
}

async function px(page: Page, lonlat: [number, number]) {
  return page.evaluate((c) => {
    const map = (window as Win).__mlmap
    const r = map.getCanvas().getBoundingClientRect()
    const p = map.project(c)
    return { x: r.left + p.x, y: r.top + p.y }
  }, lonlat)
}

test('DoD FH.8: clic derecho → la acción de un MCP que el frontend no conoce, con su formulario, añade su capa', async ({ page }) => {
  const pedidas: string[] = []
  const ejecuciones: Array<Record<string, any>> = []
  await mockInit(page)
  await page.route('**/api/v1/acciones?**', (r) => {
    pedidas.push(new URL(r.request().url()).searchParams.get('geometria') ?? '')
    return json(r, { acciones: [ACCION] })
  })
  await page.route('**/api/v1/acciones/suelos__textura/run', (r) => {
    ejecuciones.push(r.request().postDataJSON())
    return json(r, { success: true, message: null, facts: { textura: 'franco-arcillosa' }, results: {
      geojson: { type: 'FeatureCollection', features: [poligono(1)] }, layer_name: 'Textura del suelo', layer_ref: null } })
  })
  // una capa que solo vive en el navegador (sin ds_): el servicio recibe su geometría
  await mockQuery(page, () => queryResult({ message: 'Lotes.', geojson: LOTES, layerName: 'Lotes', layerId: 'mem-lotes' }))
  await page.goto('/')
  await waitForMap(page)
  await waitForStyleLoaded(page)
  await sendQuery(page, 'trae los lotes')
  await waitForFeatureCount(page, 3)
  await page.evaluate(() => (window as Win).__mlmap.jumpTo({ center: [-74.055, 4.604], zoom: 14 }))
  await page.waitForFunction(() => !(window as Win).__mlmap.isMoving())

  const p = await px(page, [-74.056, 4.604]) // dentro del lote L1
  await page.mouse.click(p.x, p.y, { button: 'right' })
  const menu = page.getByTestId('menu-contextual')
  await expect(menu).toBeVisible()
  expect(pedidas).toEqual(['Polygon'])
  await expect.poll(() => page.evaluate(() => (window as Win).__mapTestState.layers[0].seleccionados)).toBe(1)
  await expect(menu).toContainText('lo seleccionado en «Lotes» (1)')

  await menu.getByRole('menuitem', { name: /Textura del suelo por zona/ }).click()
  await menu.getByLabel('Profundidad (cm)').fill('30')
  await page.getByTestId('menu-ejecutar').click()
  await expect.poll(() => ejecuciones.length).toBe(1)
  expect(ejecuciones[0].arguments.profundidad).toBe(30)
  // capa en memoria: el servicio recibe la geometría de LO SELECCIONADO
  expect(ejecuciones[0].arguments.zona.features.map((f: any) => f.properties.lotcodigo)).toEqual(['L1'])
  await expect.poll(() => page.evaluate(() => (window as Win).__mapTestState.layers.length)).toBe(2)
  await expect(menu).toContainText('franco-arcillosa')
  await page.keyboard.press('Escape')
  await expect(menu).toHaveCount(0)
})

test('las sugerencias del LLM van bajo la última respuesta y un clic las envía', async ({ page }) => {
  const cuerpos: Array<Record<string, any>> = []
  await mockInit(page)
  await mockQuery(page, (body) => {
    cuerpos.push(body as Record<string, any>)
    return { ...queryResult({ message: cuerpos.length === 1 ? 'Añadí el área.' : 'Hecho.' }),
             suggestions: cuerpos.length === 1 ? ['Calcula el perímetro de cada lote', 'Resalta los lotes de más de 1000 m²'] : [] }
  })
  await page.goto('/')
  await waitForMap(page)
  await sendQuery(page, 'calcula el área de cada lote')
  const chips = page.getByTestId('sugerencias').getByRole('button')
  await expect(chips).toHaveCount(2)
  await chips.first().click()
  await expect.poll(() => cuerpos.length).toBe(2)
  expect(cuerpos[1].query).toBe('Calcula el perímetro de cada lote')
  await expect(page.getByTestId('sugerencias')).toHaveCount(0) // la nueva respuesta no trae: las viejas no se quedan
})
