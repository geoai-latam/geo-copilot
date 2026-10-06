import { test, expect } from '@playwright/test'
import { fc, mockInit, mockQuery, queryResult, sendQuery, waitForFeatureCount, waitForMap, waitForStyleLoaded, type Win } from './helpers'

/**
 * FH.1 / EH.6 (V2) — deshacer y rehacer lo que hizo el agente: un filtro y un reemplazo en su
 * sitio (la capa con un campo nuevo) se deshacen con Ctrl+Z / el botón, se rehacen con
 * Ctrl+Shift+Z o Ctrl+Y, y el agente ve en el turno siguiente qué quedó (deshecho o no).
 */

const DS = 'ds_00000000000000dd'
const conArea = () => fc(5, (i) => ({ n: i, area_m2: (i + 1) * 100 }))

test('deshacer y rehacer el filtro y el campo nuevo del agente; el agente lo sabe', async ({ page }) => {
  const cuerpos: Array<Record<string, any>> = []
  await mockInit(page)
  await mockQuery(page, (body) => {
    cuerpos.push(body as Record<string, any>)
    const n = cuerpos.length
    const id = (body as any).map_context?.layers?.[0]?.id
    if (n === 1) return queryResult({ message: 'Lotes.', geojson: fc(5, (i) => ({ n: i })), layerId: DS, layerName: 'Lotes' })
    if (n === 2) return queryResult({ message: 'Filtré.', artifacts: [{ kind: 'map_command',
      command: { op: 'set_filter', layer_id: id, reason: 'solo n > 1', args: { where: [{ field: 'n', op: '>', value: 1 }], count: 3 } } } as never] })
    if (n === 3) return queryResult({ message: 'Añadí area_m2.', geojson: conArea(), layerId: DS, layerName: 'Lotes', target_layer_id: id })
    return queryResult({ message: 'Ok.' })
  })
  await page.goto('/')
  await waitForMap(page)
  await waitForStyleLoaded(page)
  await sendQuery(page, 'trae los lotes')
  await waitForFeatureCount(page, 5)
  await sendQuery(page, 'deja solo los de n > 1')
  await expect.poll(() => cuerpos.length).toBe(2)
  await sendQuery(page, 'agrégales el área')
  await expect.poll(() => cuerpos.length).toBe(3)

  // los campos de la capa: los que muestra su tabla vinculada (lo que ve el usuario)
  await page.locator('[title="Capas"]').click()
  const campos = async () => {
    await page.getByRole('button', { name: 'Tabla de Lotes' }).click()
    return (await page.locator('[data-testid="tabla-capa"] th').allTextContents()).map((t) => t.trim())
  }
  const filtro = () => page.evaluate(() => (window as Win).__operaciones.getState().registro
    .filter((e: any) => e.op === 'set_filter').map((e: any) => e.deshecha))
  await expect.poll(campos).toContain('area_m2')

  // deshacer el reemplazo (vuelve la capa sin area_m2, CON su filtro) y luego el filtro
  await page.locator('.map-container').click({ position: { x: 5, y: 300 } })
  await page.keyboard.press('Control+z')
  await expect.poll(campos).not.toContain('area_m2')
  await page.getByRole('button', { name: /Deshacer/ }).first().click()
  await expect.poll(filtro).toEqual([true])

  // rehacer los dos: Ctrl+Y y Ctrl+Shift+Z
  await page.keyboard.press('Control+y')
  await expect.poll(filtro).toEqual([false])
  await page.keyboard.press('Control+Shift+z')
  await expect.poll(campos).toContain('area_m2')

  // el agente ve el registro con lo que quedó
  await sendQuery(page, '¿qué hay en el mapa?')
  await expect.poll(() => cuerpos.length).toBe(4)
  const acciones = cuerpos[3].map_context.acciones as Array<{ op: string; undone: boolean; author: string }>
  expect(acciones.find((a) => a.op === 'set_filter')).toMatchObject({ undone: false, author: 'agent' })
  expect(cuerpos[3].map_context.layers[0].filtro).toEqual([{ field: 'n', op: '>', value: 1 }])
})
