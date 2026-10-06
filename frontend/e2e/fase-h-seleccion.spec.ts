import { test, expect, type Page } from '@playwright/test'
import { fc, mockInit, mockQuery, queryResult, sendQuery, waitForFeatureCount, waitForMap, waitForStyleLoaded, type Win } from './helpers'

/**
 * FH.2 (V2) — selección compartida: clic, Shift+clic, caja, Esc y la selección
 * del agente; todo por el mismo reducer y visible para el agente en el turno siguiente.
 */

/** Píxel (en la página) de un [lon, lat]. */
async function px(page: Page, lonlat: [number, number]) {
  return page.evaluate((c) => {
    const map = (window as Win).__mlmap
    const r = map.getCanvas().getBoundingClientRect()
    const p = map.project(c)
    return { x: r.left + p.x, y: r.top + p.y }
  }, lonlat)
}
const seleccionados = (page: Page) =>
  page.evaluate(() => ((window as Win).__mapTestState?.layers ?? []).reduce((n: number, l: { seleccionados?: number }) => n + (l.seleccionados ?? 0), 0))

// fc(n): puntos en (-74.08 + (i % 5) * 0.01, 4.6 + floor(i / 5) * 0.01)
const COORD = (i: number): [number, number] => [-74.08 + (i % 5) * 0.01, 4.6 + Math.floor(i / 5) * 0.01]

async function conPuntos(page: Page, turnos: (body: Record<string, any>, n: number) => unknown) {
  const cuerpos: Array<Record<string, any>> = []
  await mockQuery(page, (body) => {
    cuerpos.push(body as Record<string, any>)
    return turnos(body as Record<string, any>, cuerpos.length)
  })
  await page.goto('/')
  await waitForMap(page)
  await waitForStyleLoaded(page)
  await sendQuery(page, 'trae los puntos')
  await waitForFeatureCount(page, 10)
  await page.waitForFunction(() => !(window as Win).__mlmap.isMoving())
  // los puntos, en el centro del lienzo (lejos de paneles y sugerencias del chat)
  await page.evaluate(() => (window as Win).__mlmap.jumpTo({ center: [-74.06, 4.605], zoom: 13 }))
  await page.waitForFunction(() => !(window as Win).__mlmap.isMoving())
  return cuerpos
}

test.describe('FH.2 · selección compartida', () => {
  test.beforeEach(async ({ page }) => mockInit(page))

  test('clic selecciona, Shift+clic añade, Esc quita; el agente recibe los ids y el origen', async ({ page }) => {
    const cuerpos = await conPuntos(page, (_b, n) =>
      n === 1 ? queryResult({ message: 'Puntos.', geojson: fc(10), layerName: 'Puntos' }) : queryResult({ message: 'Ok.' }))
    const a = await px(page, COORD(0))
    const b = await px(page, COORD(6))
    await page.mouse.click(a.x, a.y)
    await expect.poll(() => seleccionados(page)).toBe(1)
    await expect(page.getByTestId('seleccion-chip')).toContainText('1 seleccionado')
    await page.keyboard.down('Shift')
    await page.mouse.click(b.x, b.y)
    await page.keyboard.up('Shift')
    await expect.poll(() => seleccionados(page)).toBe(2)

    await sendQuery(page, '¿cuánto suman estos?')
    await expect.poll(() => cuerpos.length).toBe(2)
    const capa = cuerpos[1].map_context.layers[0]
    expect(capa.seleccion).toEqual({ ids: [0, 6], count: 2, origin: 'click' })
    expect(cuerpos[1].map_context.acciones.map((x: { op: string; author: string }) => `${x.op}:${x.author}`))
      .toEqual(['select:user', 'select:user'])

    await page.locator('.map-container').click({ position: { x: 5, y: 300 } }) // foco fuera del chat
    await page.keyboard.press('Escape')
    await expect.poll(() => seleccionados(page)).toBe(0)
    await expect(page.getByTestId('seleccion-chip')).toHaveCount(0)
  })

  test('la caja selecciona lo que toca; Ctrl+Z la deshace', async ({ page }) => {
    await conPuntos(page, () => queryResult({ message: 'Puntos.', geojson: fc(10), layerName: 'Puntos' }))
    // caja alrededor de los puntos 0, 1, 5 y 6 (dos columnas, dos filas)
    const esq1 = await px(page, [-74.085, 4.595])
    const esq2 = await px(page, [-74.065, 4.615])
    await page.getByLabel('Seleccionar con caja').click()
    await page.mouse.move(esq1.x, esq1.y)
    await page.mouse.down()
    await page.mouse.move((esq1.x + esq2.x) / 2, (esq1.y + esq2.y) / 2, { steps: 4 })
    await page.mouse.move(esq2.x, esq2.y, { steps: 4 })
    await page.mouse.up()
    await expect.poll(() => seleccionados(page)).toBe(4)
    // el modo es de un solo uso
    await expect(page.getByLabel('Seleccionar con caja')).toHaveAttribute('aria-pressed', 'false')
    await page.keyboard.press('Control+z')
    await expect.poll(() => seleccionados(page)).toBe(0)
  })

  test('el lazo selecciona lo que rodea, no lo que queda fuera de su caja envolvente', async ({ page }) => {
    await conPuntos(page, () => queryResult({ message: 'Puntos.', geojson: fc(10), layerName: 'Puntos' }))
    // triángulo que rodea 0, 1 y 5 pero deja fuera 6 (que SÍ está en su caja envolvente)
    const v = await Promise.all(([[-74.086, 4.594], [-74.062, 4.594], [-74.086, 4.618]] as [number, number][])
      .map((c) => px(page, c)))
    await page.getByLabel('Seleccionar con lazo').click()
    await page.mouse.move(v[0].x, v[0].y)
    await page.mouse.down()
    for (const p of [v[1], v[2], v[0]]) await page.mouse.move(p.x, p.y, { steps: 6 })
    await page.mouse.up()
    await expect.poll(() => seleccionados(page)).toBe(3)
    const ids = await page.evaluate(() => {
      const map = (window as Win).__mlmap
      const capa = (map.getStyle().layers ?? []).find((l: { id: string }) => l.id.endsWith('-sel-circle'))
      return capa ? (map.getFilter(capa.id) as unknown[])[2] : null
    })
    expect((ids as [string, number[]])[1].slice().sort()).toEqual([0, 1, 5])
  })

  test('el agente selecciona por condición y se resalta; el siguiente turno lo sabe', async ({ page }) => {
    const cuerpos = await conPuntos(page, (body, n) => {
      if (n === 1) return queryResult({ message: 'Puntos.', geojson: fc(10), layerName: 'Puntos' })
      if (n === 2) {
        const id = body.map_context.layers[0].id
        return queryResult({ message: 'Seleccioné los de valor mayor a 50.', intent: 'map_control', artifacts: [
          { kind: 'map_command', command: { op: 'select', layer_id: id, reason: null,
            args: { where: { field: 'valor', op: '>', value: 50 }, ids: null, mode: 'replace', origin: 'agent', count: 4 } } },
        ] as never })
      }
      return queryResult({ message: 'Ok.' })
    })
    await sendQuery(page, 'selecciona los de valor mayor a 50')
    // fc: valor = i * 10 → 60, 70, 80, 90
    await expect.poll(() => seleccionados(page)).toBe(4)
    await expect(page.getByTestId('seleccion-chip')).toContainText('4 seleccionados')
    // el resaltado filtra exactamente esos (ids 6..9)
    const filtro = await page.evaluate(() => {
      const map = (window as Win).__mlmap
      const capa = (map.getStyle().layers ?? []).find((l: { id: string }) => l.id.endsWith('-sel-circle'))
      return capa ? map.getFilter(capa.id) : null
    })
    expect(filtro).toEqual(['in', ['id'], ['literal', [6, 7, 8, 9]]])
    await sendQuery(page, 'y ahora ¿cuánto suman?')
    await expect.poll(() => cuerpos.length).toBe(3)
    expect(cuerpos[2].map_context.layers[0].seleccion).toMatchObject({ ids: [6, 7, 8, 9], count: 4, origin: 'agent' })
  })
})
