import { test, expect, type Page } from '@playwright/test'
import { fc, json, mockInit, mockQuery, queryResult, sendQuery, waitForFeatureCount, waitForMap, waitForStyleLoaded, type Win } from './helpers'

/**
 * FH.11 (V2) — proyectos: guardar el mapa y la conversación, cerrar el navegador y reabrir el
 * proyecto → capas (con filtro), vistas, cámara y chat como estaban. La conversación sobrevive
 * también a recargar la pestaña.
 */

const DS = 'ds_00000000000000cc'

async function workspaceMock(page: Page) {
  await page.route('**/api/v1/session/e2e-session', (r) => json(r, { session_id: 'e2e-session' }))
  await page.route('**/api/v1/workspace/**', (r) =>
    json(r, { layer_ref: { id: DS, name: 'Lotes', feature_count: 5 }, geojson: fc(5, (i) => ({ n: i })), tiles: null }))
}

test('la conversación sobrevive a recargar la pestaña', async ({ page }) => {
  await mockInit(page)
  await workspaceMock(page)
  await mockQuery(page, () => queryResult({ message: 'Traje 5 lotes.', geojson: fc(5), layerId: DS, layerName: 'Lotes' }))
  await page.goto('/')
  await waitForMap(page)
  await sendQuery(page, 'trae 5 lotes')
  await expect(page.getByText('Traje 5 lotes.')).toBeVisible()
  await page.reload()
  await waitForMap(page)
  await expect(page.getByText('Traje 5 lotes.')).toBeVisible()
  await expect(page.getByText('trae 5 lotes', { exact: true })).toBeVisible()
})

test('DoD FH.11: guardar, cerrar el navegador y reabrir el proyecto deja mapa y conversación como estaban', async ({ browser }) => {
  let guardado: Record<string, any> | null = null
  const uno = await browser.newPage()
  await mockInit(uno)
  await workspaceMock(uno)
  await uno.route('**/api/v1/proyectos', (r) => {
    guardado = r.request().postDataJSON()
    return json(r, { id: 'pr_00000000000000aa', nombre: 'Finca' }, 201)
  })
  await mockQuery(uno, () => queryResult({ message: 'Traje 5 lotes.', geojson: fc(5, (i) => ({ n: i })), layerId: DS, layerName: 'Lotes' }))
  await uno.goto('/')
  await waitForMap(uno)
  await waitForStyleLoaded(uno)
  await sendQuery(uno, 'trae 5 lotes')
  await waitForFeatureCount(uno, 5)
  // un filtro y una vista, y guardar el proyecto
  await uno.evaluate(() => {
    const id = (window as Win).__mapTestState.layers[0].id
    ;(window as Win).__operaciones.getState().ejecutar({ op: 'set_filter', layer_id: id, args: { where: [{ field: 'n', op: '>', value: 1 }] } }, 'user')
  })
  await uno.getByRole('button', { name: 'Vistas guardadas' }).click()
  await uno.getByLabel('Nombre de la vista').fill('Centro')
  await uno.getByRole('button', { name: 'Guardar esta vista' }).click()
  await uno.getByRole('button', { name: 'Guardar proyecto' }).click()
  await uno.getByLabel('Nombre del proyecto').fill('Finca')
  await uno.getByRole('button', { name: 'Guardar', exact: true }).click()
  await expect(uno.getByRole('status')).toContainText('Guardado «Finca»')
  expect(guardado).toMatchObject({ session_id: 'e2e-session', nombre: 'Finca' })
  const estado = guardado!.estado
  expect(estado.capas).toEqual([expect.objectContaining({ datasetId: DS, filtro: [{ field: 'n', op: '>', value: 1 }] })])
  expect(estado.vistas.map((v: any) => v.nombre)).toEqual(['Centro'])
  expect(estado.chat.map((m: any) => m.content)).toEqual(['trae 5 lotes', 'Traje 5 lotes.'])
  await uno.context().close()

  // otro navegador (nada en la pestaña): abrir el proyecto
  const ctx = await browser.newContext()
  const dos = await ctx.newPage()
  await mockInit(dos)
  await workspaceMock(dos)
  await dos.route('**/api/v1/session/', (r) => json(r, { session_id: 'otra-sesion' }))
  await dos.route('**/api/v1/proyectos', (r) => json(r, { proyectos: [
    { id: 'pr_00000000000000aa', nombre: 'Finca', workspace_id: 'e2e-session', updated_at: new Date().toISOString(), capas: 1 }] }))
  await dos.route('**/api/v1/proyectos/pr_00000000000000aa/abrir', (r) => json(r, {
    id: 'pr_00000000000000aa', nombre: 'Finca', session_id: 'e2e-session', estado }))
  await dos.goto('/')
  await waitForMap(dos)
  await expect(dos.getByText('Traje 5 lotes.')).toHaveCount(0)
  await dos.getByRole('button', { name: 'Abrir proyecto' }).click()
  await dos.getByRole('button', { name: /Finca/ }).click()
  await waitForMap(dos)
  await waitForFeatureCount(dos, 5)
  await expect(dos.getByText('Traje 5 lotes.')).toBeVisible()
  const capa = await dos.evaluate(() => (window as Win).__mapTestState.layers[0])
  expect(capa.name).toBe('Lotes')
  await dos.getByRole('button', { name: 'Vistas guardadas' }).click()
  await expect(dos.getByRole('button', { name: 'Centro', exact: true })).toBeVisible()
  // la sesión es la del proyecto: el siguiente turno va con ella y con el filtro
  const cuerpos: Array<Record<string, any>> = []
  await mockQuery(dos, (b) => { cuerpos.push(b as Record<string, any>); return queryResult({ message: 'Ok.' }) })
  await sendQuery(dos, '¿cuántos quedan?')
  await expect.poll(() => cuerpos.length).toBe(1)
  expect(cuerpos[0].session_id).toBe('e2e-session')
  expect(cuerpos[0].map_context.layers[0].filtro).toEqual([{ field: 'n', op: '>', value: 1 }])
  await ctx.close()
})
