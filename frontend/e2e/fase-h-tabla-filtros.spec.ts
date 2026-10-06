import { test, expect, type Page } from '@playwright/test'
import { capa, fc, mockInit, mockQuery, queryResult, sendQuery, waitForFeatureCount, waitForMap, waitForStyleLoaded, type Win } from './helpers'

/**
 * FH.5 (V2) — filtros de capa y tabla vinculada.
 * fc(10): valor = i*10, uso = res|com|ind (i % 3).
 */

async function conPuntos(page: Page, turnos?: (body: Record<string, any>, n: number) => unknown) {
  const cuerpos: Array<Record<string, any>> = []
  await mockInit(page)
  await mockQuery(page, (body) => {
    cuerpos.push(body as Record<string, any>)
    if (cuerpos.length === 1) return queryResult({ message: 'Puntos.', geojson: fc(10), layerName: 'Puntos' })
    return turnos ? turnos(body as Record<string, any>, cuerpos.length) : queryResult({ message: 'Ok.' })
  })
  await page.goto('/')
  await waitForMap(page)
  await waitForStyleLoaded(page)
  await sendQuery(page, 'trae los puntos')
  await waitForFeatureCount(page, 10)
  return cuerpos
}

const filtroEnElMapa = (page: Page) => page.evaluate(() => {
  const m = (window as Win).__mlmap
  const capa = (m.getStyle().layers ?? []).find((l: { id: string }) => /-circle$/.test(l.id) && !l.id.includes('-sel-'))
  return capa ? JSON.stringify(m.getFilter(capa.id)) : null
})

test.describe('FH.5 · filtros de capa', () => {
  test('filtrar a mano desde el panel: el mapa, el conteo y el agente lo ven', async ({ page }) => {
    const cuerpos = await conPuntos(page)
    await page.locator('[title="Capas"]').click()
    await page.getByRole('button', { name: 'Filtrar Puntos' }).click()
    await page.getByLabel('Campo del filtro').selectOption('uso')
    await page.getByLabel('Valor del filtro').fill('res')
    await page.getByRole('button', { name: 'Aplicar filtro' }).click()
    await expect(page.getByTestId('filtro-capa')).toContainText('uso = res')
    await expect(page.getByTestId('filtro-cuenta')).toHaveText('4 de 10')
    await expect.poll(() => filtroEnElMapa(page)).toContain('"uso"')

    await sendQuery(page, '¿cuántos quedan?')
    await expect.poll(() => cuerpos.length).toBe(2)
    const capa = cuerpos[1].map_context.layers[0]
    expect(capa.filtro).toEqual([{ field: 'uso', op: '=', value: 'res' }])
    expect(capa.filtro_count).toBe(4)
    expect(cuerpos[1].map_context.acciones.at(-1)).toMatchObject({ op: 'set_filter', author: 'user' })
  })

  test('DoD: el agente filtra, el usuario retoca el filtro a mano y el siguiente turno usa el retocado', async ({ page }) => {
    const cuerpos = await conPuntos(page, (body, n) => {
      if (n === 2) {
        const id = body.map_context.layers[0].id
        return queryResult({ message: 'Filtré los de valor mayor a 50.', intent: 'map_control', artifacts: [
          { kind: 'map_command', command: { op: 'set_filter', layer_id: id, reason: 'solo valor > 50',
            args: { where: [{ field: 'valor', op: '>', value: 50 }], count: 4 } } },
        ] as never })
      }
      return queryResult({ message: 'Ok.' })
    })
    await sendQuery(page, 'deja solo los de valor mayor a 50')
    await page.locator('[title="Capas"]').click()
    await expect(page.getByTestId('filtro-capa')).toContainText('valor > 50')
    await expect(page.getByTestId('filtro-cuenta')).toHaveText('4 de 10')

    // retoque a mano: 50 → 70
    await page.getByRole('button', { name: 'Editar filtro valor > 50' }).click()
    await page.getByLabel('Valor del filtro').fill('70')
    await page.getByRole('button', { name: 'Aplicar filtro' }).click()
    await expect(page.getByTestId('filtro-cuenta')).toHaveText('2 de 10')

    await sendQuery(page, 'hazme un resumen')
    await expect.poll(() => cuerpos.length).toBe(3)
    expect(cuerpos[2].map_context.layers[0].filtro).toEqual([{ field: 'valor', op: '>', value: 70 }])
    expect(cuerpos[2].map_context.acciones.map((a: { op: string; author: string }) => `${a.op}:${a.author}`))
      .toContain('set_filter:user')
    // Ctrl+Z deshace el retoque (vuelve el filtro del agente)
    await page.locator('.map-container').click({ position: { x: 5, y: 300 } })
    await page.keyboard.press('Control+z')
    await expect(page.getByTestId('filtro-capa')).toContainText('valor > 50')
  })
})

test.describe('FH.5 · tabla vinculada', () => {
  test('fila ↔ mapa: clic selecciona, Ctrl+clic suma, solo seleccionados, Σ y encuadrar', async ({ page }) => {
    await conPuntos(page)
    await page.locator('[title="Capas"]').click()
    await page.getByRole('button', { name: 'Tabla de Puntos' }).click()
    const tabla = page.getByTestId('tabla-capa')
    await expect(tabla.getByTestId('tabla-capa-fila')).toHaveCount(10)

    await tabla.locator('[data-testid="tabla-capa-fila"][data-fid="3"]').click()
    await tabla.locator('[data-testid="tabla-capa-fila"][data-fid="5"]').click({ modifiers: ['Control'] })
    await expect.poll(() => page.evaluate(() => (window as Win).__mapTestState.layers[0].seleccionados)).toBe(2)
    await expect(tabla.locator('tr[aria-selected="true"]')).toHaveCount(2)
    // la selección de la tabla es una selección como cualquier otra (origen «tabla»)
    const origen = await page.evaluate(() => (window as any).__operaciones.getState().registro.at(-1).args.origin)
    expect(origen).toBe('table')

    await tabla.getByText('solo seleccionados').click()
    await expect(tabla.getByTestId('tabla-capa-fila')).toHaveCount(2)

    await tabla.getByRole('button', { name: 'Estadística de valor' }).click()
    await expect(tabla.getByTestId('tabla-capa-stats')).toContainText('mín 0 · máx 90 · media 45')

    const antes = await page.evaluate(() => (window as Win).__mlmap.getCenter().toArray())
    await tabla.getByRole('button', { name: 'Encuadrar 5' }).click()
    await expect.poll(() => page.evaluate(() => (window as Win).__mlmap.getCenter().toArray())).not.toEqual(antes)
  })

  test('una capa grande (teselas) se pagina contra el workspace, con su filtro', async ({ page }) => {
    const pedidas: string[] = []
    await mockInit(page)
    await mockQuery(page, queryResult({ message: 'Lotes.', artifacts: [capa({
      id: 'ds_0123456789abcdef', name: 'Lotes', featureCount: 60000, geometryType: 'Polygon',
      bbox: [-74.2, 4.55, -74.0, 4.75],
      tiles: { url_template: '/api/v1/tiles/ws/e2e-session/ds_0123456789abcdef/{z}/{x}/{y}.pbf', source_layer: 'dataset',
               fields: ['lotcodigo', 'estrato'] } })] }))
    await page.route('**/api/v1/tiles/**', (r) => r.fulfill({ status: 204, body: '' }))
    await page.route('**/api/v1/workspace/e2e-session/datasets/ds_0123456789abcdef/filas**', async (r) => {
      pedidas.push(r.request().url())
      const off = Number(new URL(r.request().url()).searchParams.get('offset') ?? 0)
      await r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
        total: 60000, offset: off,
        filas: Array.from({ length: 25 }, (_, i) => ({ fid: off + i, properties: { lotcodigo: `L${off + i}`, estrato: 3 },
                                                        bbox: [-74.1, 4.6, -74.09, 4.61] })) }) })
    })
    await page.goto('/')
    await waitForMap(page)
    await waitForStyleLoaded(page)
    await sendQuery(page, 'todos los lotes')
    await page.locator('[title="Capas"]').click()
    await page.getByRole('button', { name: 'Tabla de Lotes' }).click()
    const tabla = page.getByTestId('tabla-capa')
    await expect(tabla.getByTestId('tabla-capa-total')).toHaveText('60.000 filas')
    await expect(tabla.getByTestId('tabla-capa-pagina')).toHaveText('1 / 2400')
    await tabla.getByRole('button', { name: '›' }).click()
    await expect(tabla.locator('[data-fid="25"]')).toBeVisible()
    expect(new URL(pedidas.at(-1)!).searchParams.get('offset')).toBe('25')
  })
})
