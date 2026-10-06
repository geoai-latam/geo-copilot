import { test, expect, type Page, type Route } from '@playwright/test'
import { mockInit, mockQuery, queryResult, sendQuery, waitForMap, waitForStyleLoaded, type Win } from './helpers'

/**
 * FH.3 (V2) — dibujos: se dibuja con clics en el mapa, se guarda en el workspace
 * como «Área 1», entra como capa y el agente la ve (DIBUJADA por el usuario);
 * se renombra y se editan sus vértices guardando por su fid.
 */

const DS = 'ds_aaaaaaaaaaaaaaaa'

interface Llamadas { crear: any[]; editar: any[]; consultas: any[] }

async function preparar(page: Page): Promise<Llamadas> {
  const llamadas: Llamadas = { crear: [], editar: [], consultas: [] }
  await mockInit(page)
  await mockQuery(page, (body) => {
    llamadas.consultas.push(body)
    return queryResult({ message: 'Ok.' })
  })
  const responder = (r: Route, name: string, geometry: unknown) => r.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify({
      layer_ref: { id: DS, name, feature_count: 1 },
      geojson: { type: 'FeatureCollection', features: [{ type: 'Feature', id: 0, properties: {}, geometry }] },
      tiles: null,
    }),
  })
  await page.route('**/api/v1/workspace/e2e-session/sketches', async (r) => {
    const b = r.request().postDataJSON()
    llamadas.crear.push(b)
    await responder(r, b.name, b.geojson.features[0].geometry)
  })
  let nombre = 'Área 1'
  let geom: unknown = null
  await page.route(`**/api/v1/workspace/e2e-session/datasets/${DS}`, async (r) => {
    const b = r.request().postDataJSON()
    llamadas.editar.push(b)
    if (b.name) nombre = b.name
    if (b.geojson) geom = b.geojson.features[0].geometry
    await responder(r, nombre, geom ?? llamadas.crear[0].geojson.features[0].geometry)
  })
  await page.goto('/')
  await waitForMap(page)
  await waitForStyleLoaded(page)
  await page.evaluate(() => (window as Win).__mlmap.jumpTo({ center: [-74.08, 4.6], zoom: 14 }))
  await page.waitForFunction(() => !(window as Win).__mlmap.isMoving())
  return llamadas
}

/** Centro del lienzo del mapa en la página, desplazado (dx, dy) px. */
async function enMapa(page: Page, dx: number, dy: number) {
  const r = await page.evaluate(() => {
    const c = (window as Win).__mlmap.getCanvas().getBoundingClientRect()
    return { x: c.left + c.width / 2, y: c.top + c.height / 2 }
  })
  return { x: r.x + dx, y: r.y + dy }
}

async function dibujarCuadrado(page: Page) {
  await page.getByLabel(/Dibujar un polígono/).click()
  const v = [await enMapa(page, -60, -60), await enMapa(page, 60, -60), await enMapa(page, 60, 60), await enMapa(page, -60, 60)]
  for (const p of v) {
    await page.mouse.move(p.x, p.y, { steps: 3 })
    await page.mouse.click(p.x, p.y)
  }
  await page.mouse.move(v[0].x, v[0].y, { steps: 3 })
  await page.mouse.click(v[0].x, v[0].y) // clic en el primer vértice: cierra
}

test.describe('FH.3 · dibujos', () => {
  test('un polígono dibujado se guarda como «Área 1», entra al mapa y el agente lo ve', async ({ page }) => {
    const llamadas = await preparar(page)
    const vista = () => page.evaluate(() => {
      const m = (window as Win).__mlmap
      return { z: m.getZoom().toFixed(3), c: m.getCenter().toArray().map((x: number) => x.toFixed(5)) }
    })
    const antes = await vista()
    await dibujarCuadrado(page)

    await expect.poll(() => llamadas.crear.length).toBe(1)
    await page.waitForTimeout(1500) // lo que tardaría un encuadre
    expect(await vista()).toEqual(antes) // el dibujo no mueve el mapa: el usuario ya lo está mirando
    // terra-draw no deja nada pintado (el dibujo ya es una capa normal)
    const restos = await page.evaluate(() => {
      const m = (window as Win).__mlmap
      return Object.keys(m.getStyle().sources).filter((id) => id.startsWith('td-'))
        .reduce((n, id) => n + ((m.getSource(id) as any)._data?.features?.length ?? 0), 0)
    })
    expect(restos).toBe(0)
    const enviado = llamadas.crear[0]
    expect(enviado.name).toBe('Área 1')
    expect(enviado.geojson.features[0].geometry.type).toBe('Polygon')
    expect(enviado.geojson.features[0].geometry.coordinates[0].length).toBe(5) // 4 vértices + cierre
    await expect.poll(() => page.evaluate(() => ((window as Win).__mapTestState?.layers ?? []).map((l: { name: string }) => l.name)))
      .toEqual(['Área 1'])
    // el modo es de un solo uso
    await expect(page.getByLabel(/Dibujar un polígono/)).toHaveAttribute('aria-pressed', 'false')

    await sendQuery(page, 'NDVI de lo que dibujé entre marzo y junio')
    await expect.poll(() => llamadas.consultas.length).toBe(1)
    const mc = llamadas.consultas[0].map_context
    expect(mc.layers[0]).toMatchObject({ name: 'Área 1', dataset_id: DS, origin: { capability: 'user.sketch' } })
    expect(mc.layers[0].data).toBeUndefined() // viaja por referencia
    // los clics del dibujo no marcan un «aquí» (V5 FH.3: el clic de cierre lo hacía)
    expect(mc.clicked_point ?? null).toBeNull()
    expect(mc.acciones).toEqual([expect.objectContaining({ op: 'add_layer', author: 'user', args: { dibujo: 'Polygon' } })])
  })

  for (const [etiqueta, nombre, tipo] of [
    [/Dibujar un punto/, 'Punto 1', 'Point'],
    [/Dibujar una línea/, 'Línea 1', 'LineString'],
    [/Dibujar un rectángulo/, 'Área 1', 'Polygon'],
    [/Dibujar un círculo/, 'Área 1', 'Polygon'],
  ] as const) {
    test(`${nombre} (${etiqueta.source}) se guarda con su geometría`, async ({ page }) => {
      const llamadas = await preparar(page)
      await page.getByLabel(etiqueta).click()
      const a = await enMapa(page, -50, -30)
      const b = await enMapa(page, 50, 40)
      await page.mouse.move(a.x, a.y, { steps: 3 })
      await page.mouse.click(a.x, a.y)
      if (tipo !== 'Point') {
        await page.mouse.move(b.x, b.y, { steps: 6 })
        await page.mouse.click(b.x, b.y)
        if (tipo === 'LineString') await page.mouse.click(b.x, b.y) // clic en el último vértice: termina
      }
      await expect.poll(() => llamadas.crear.length).toBe(1)
      expect(llamadas.crear[0].name).toBe(nombre)
      expect(llamadas.crear[0].geojson.features[0].geometry.type).toBe(tipo)
    })
  }

  test('Esc cancela el dibujo sin guardar nada', async ({ page }) => {
    const llamadas = await preparar(page)
    await page.getByLabel(/Dibujar un polígono/).click()
    const p = await enMapa(page, 0, 0)
    await page.mouse.click(p.x, p.y)
    await page.keyboard.press('Escape')
    await expect(page.getByLabel(/Dibujar un polígono/)).toHaveAttribute('aria-pressed', 'false')
    await page.waitForTimeout(300)
    expect(llamadas.crear).toHaveLength(0)
  })

  test('se renombra (doble clic) y se editan sus vértices guardando por su fid', async ({ page }) => {
    const llamadas = await preparar(page)
    await dibujarCuadrado(page)
    await expect.poll(() => llamadas.crear.length).toBe(1)

    await page.locator('[title="Capas"]').click()
    await page.getByTestId('layer-row').filter({ hasText: 'Área 1' }).locator('.layer-name').dblclick()
    const input = page.getByLabel('Nuevo nombre de Área 1')
    await input.fill('Finca La Esperanza')
    await input.press('Enter')
    await expect.poll(() => llamadas.editar.length).toBe(1)
    expect(llamadas.editar[0]).toEqual({ name: 'Finca La Esperanza' })
    await expect(page.getByTestId('layer-row').filter({ hasText: 'Finca La Esperanza' })).toHaveCount(1)

    // editar vértices: arrastrar una esquina y guardar con Enter
    await page.getByLabel('Editar vértices de Finca La Esperanza').click()
    await expect(page.getByTestId('dibujo-bar')).toContainText('Editando «Finca La Esperanza»')
    // los vértices en edición (terra-draw, td-*) se pintan ENCIMA de las capas
    const orden = await page.evaluate(() => (window as Win).__mlmap.getStyle().layers.map((l: { id: string }) => l.id))
    const primeraTd = orden.findIndex((id: string) => id.startsWith('td-'))
    expect(primeraTd).toBeGreaterThan(-1)
    expect(orden.slice(primeraTd).every((id: string) => id.startsWith('td-'))).toBe(true)
    const esquina = await enMapa(page, 60, 60)
    await page.mouse.move(esquina.x, esquina.y, { steps: 3 })
    await page.mouse.down()
    await page.mouse.move(esquina.x + 50, esquina.y + 40, { steps: 8 })
    await page.mouse.up()
    await page.keyboard.press('Enter')
    await expect.poll(() => llamadas.editar.length).toBe(2)
    const f = llamadas.editar[1].geojson.features[0]
    expect(f.id).toBe(0) // por su fid: el dataset conserva su id
    const antes = llamadas.crear[0].geojson.features[0].geometry.coordinates[0]
    expect(f.geometry.coordinates[0]).not.toEqual(antes)
    await expect(page.getByTestId('dibujo-bar')).not.toContainText('Editando')

    await sendQuery(page, '¿qué área tiene?')
    await expect.poll(() => llamadas.consultas.length).toBe(1)
    expect(llamadas.consultas[0].map_context.acciones.map((a: { op: string }) => a.op))
      .toEqual(['add_layer', 'rename_layer', 'edit_geometry'])
  })
})
