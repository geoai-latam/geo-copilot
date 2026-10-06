import { test, expect, type Page } from '@playwright/test'
import { fc, mockInit, mockQuery, queryResult, sendQuery, waitForFeatureCount, waitForMap, waitForStyleLoaded, type Win } from './helpers'

/**
 * FH.4 (V2) — menciones `@` y alcance del mensaje: se elige la capa de una lista,
 * viaja como referencia (no como texto a interpretar) y el chip de la selección
 * se puede quitar para que el mensaje vaya sin ella.
 */

async function dosCapas(page: Page) {
  const cuerpos: Array<Record<string, any>> = []
  await mockInit(page)
  await mockQuery(page, (body) => {
    cuerpos.push(body as Record<string, any>)
    if (cuerpos.length === 1) return queryResult({ message: 'Vías.', geojson: fc(4), layerName: 'Vías' })
    if (cuerpos.length === 2) return queryResult({ message: 'Vías 2021.', geojson: fc(3), layerName: 'Vías 2021' })
    return queryResult({ message: 'Ok.' })
  })
  await page.goto('/')
  await waitForMap(page)
  await waitForStyleLoaded(page)
  await sendQuery(page, 'trae las vías')
  await waitForFeatureCount(page, 4)
  await sendQuery(page, 'trae las vías de 2021')
  await waitForFeatureCount(page, 7)
  return cuerpos
}

const idDe = (page: Page, nombre: string) =>
  page.evaluate((n) => ((window as Win).__mapTestState.layers as Array<{ id: string; name: string }>).find((l) => l.name === n)?.id, nombre)

test.describe('FH.4 · menciones y alcance', () => {
  test('@ lista las capas, se elige con el teclado y viaja como referencia', async ({ page }) => {
    const cuerpos = await dosCapas(page)
    const caja = page.getByLabel('Consulta')
    await caja.click()
    await caja.pressSequentially('buffer 100 m a @vía')
    const lista = page.getByRole('listbox', { name: 'Mencionar' })
    await expect(lista.getByRole('option')).toHaveText([/@Vías 2021/, /@Vías/])
    await caja.press('ArrowDown') // la segunda: «@Vías»
    await caja.press('Enter') // Enter con la lista abierta elige, no envía
    await expect(caja).toHaveValue('buffer 100 m a @Vías ')
    await expect(page.getByTestId('alcance-mencion')).toHaveText(['Vías'])
    expect(cuerpos).toHaveLength(2) // no se envió nada todavía

    await caja.press('Enter')
    await expect.poll(() => cuerpos.length).toBe(3)
    const vias = await idDe(page, 'Vías')
    expect(cuerpos[2].query).toBe('buffer 100 m a @Vías')
    expect(cuerpos[2].map_context.menciones).toEqual([{ tipo: 'capa', layer_id: vias, texto: '@Vías' }])
    // tras enviar, el alcance se limpia
    await expect(page.getByTestId('alcance-mencion')).toHaveCount(0)
  })

  test('borrar el @texto o quitar su chip saca la mención del mensaje', async ({ page }) => {
    const cuerpos = await dosCapas(page)
    const caja = page.getByLabel('Consulta')
    await caja.click()
    await caja.pressSequentially('@vías 2')
    await caja.press('Enter')
    await expect(page.getByTestId('alcance-mencion')).toHaveText(['Vías 2021'])
    await page.getByRole('button', { name: 'Quitar @Vías 2021' }).click()
    await expect(page.getByTestId('alcance-mencion')).toHaveCount(0)
    await expect(caja).toHaveValue('')
    await caja.pressSequentially('cuántas hay')
    await caja.press('Enter')
    await expect.poll(() => cuerpos.length).toBe(3)
    expect(cuerpos[2].map_context.menciones).toBeUndefined()
  })

  test('el chip de la selección está a la vista; quitarlo manda el mensaje sin ella', async ({ page }) => {
    const cuerpos = await dosCapas(page)
    const id = await idDe(page, 'Vías')
    await page.evaluate((layerId) => (window as any).__operaciones.getState().ejecutar({
      op: 'select', layer_id: layerId, reason: null,
      args: { ids: [0, 1], where: null, mode: 'replace', origin: 'click', count: null } }, 'user'), id)
    await expect(page.getByTestId('alcance-seleccion')).toContainText('2 seleccionados de Vías')

    // 1) con el chip: la selección viaja
    await sendQuery(page, '¿cuánto miden?')
    await expect.poll(() => cuerpos.length).toBe(3)
    expect(cuerpos[2].map_context.layers.find((l: { id: string }) => l.id === id).seleccion).toMatchObject({ ids: [0, 1] })

    // 2) quitando el chip: ESE mensaje va sin ella (el mapa la sigue mostrando)
    await page.getByRole('button', { name: 'No usar la selección en este mensaje' }).click()
    await expect(page.getByTestId('alcance-sin-seleccion')).toContainText('Vías entera')
    await sendQuery(page, '¿cuánto miden?')
    await expect.poll(() => cuerpos.length).toBe(4)
    expect(cuerpos[3].map_context.layers.find((l: { id: string }) => l.id === id).seleccion).toBeUndefined()
    expect(await page.evaluate(() => (window as Win).__mapTestState.layers.map((l: { seleccionados: number }) => l.seleccionados)))
      .toContain(2)
    // el siguiente mensaje vuelve a incluirla (el chip reaparece)
    await expect(page.getByTestId('alcance-seleccion')).toBeVisible()
  })
})
