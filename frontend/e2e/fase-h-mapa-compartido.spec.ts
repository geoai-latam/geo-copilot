import { test, expect, type Page } from '@playwright/test'
import {
  estilo, fc, layerOrder, mockInit, mockQuery, queryResult, sendQuery, waitForFeatureCount, waitForMap,
  waitForStyleLoaded, type Win,
} from './helpers'

/**
 * FH.1 (V2) — el mapa compartido: lo que hace el agente y lo que hace el usuario
 * pasan por el mismo reducer, quedan en un registro, se deshacen con Ctrl+Z, y
 * el agente ve en el turno siguiente qué pasó (y qué se deshizo).
 */

const GRADUADO = estilo({
  symbology_type: 'graduated_colors', classification_field: 'valor', classification_method: 'quantile',
  class_breaks: [{ label: '0-20', color: '#ffeeee', min_value: 0, max_value: 20, count: 2 },
                 { label: '20-50', color: '#cc0000', min_value: 20, max_value: 50, count: 3 }],
})
const rendererKind = (page: Page) => page.evaluate(() => (window as Win).__mapTestState?.rendererKind ?? null)

async function conLotes(page: Page, turnos: (body: Record<string, any>, n: number) => unknown) {
  const cuerpos: Array<Record<string, any>> = []
  await mockQuery(page, (body) => {
    cuerpos.push(body as Record<string, any>)
    return turnos(body as Record<string, any>, cuerpos.length)
  })
  await page.goto('/')
  await waitForMap(page)
  await waitForStyleLoaded(page)
  return cuerpos
}

test.describe('FH.1 · mapa compartido con registro y deshacer', () => {
  test.beforeEach(async ({ page }) => mockInit(page))

  test('DoD: la simbología del agente se deshace con Ctrl+Z y el agente lo sabe en el turno siguiente', async ({ page }) => {
    const cuerpos = await conLotes(page, (body, n) => {
      if (n === 1) return queryResult({ message: 'Lotes.', geojson: fc(5), layerName: 'Lotes' })
      if (n === 2) {
        const lotes = body.map_context.layers.find((l: { name: string }) => l.name === 'Lotes')
        return queryResult({ message: 'Graduados por valor.', intent: 'apply_symbology', symbology: GRADUADO,
                             target_layer_id: lotes.id })
      }
      return queryResult({ message: 'Entendido.' })
    })
    await sendQuery(page, 'trae los lotes')
    await waitForFeatureCount(page, 5)
    await sendQuery(page, 'gradúalos por valor')
    await expect.poll(() => rendererKind(page)).toBe('graduated_colors')

    // El usuario deshace con el teclado (fuera del cuadro de texto).
    await page.locator('.map-container').click({ position: { x: 20, y: 20 } })
    await page.keyboard.press('Control+z')
    await expect.poll(() => rendererKind(page)).toBeNull()
    await expect(page.getByTestId('rehacer')).toBeEnabled()
    // rehacer y volver a deshacer, con el teclado
    await page.keyboard.press('Control+Shift+z')
    await expect.poll(() => rendererKind(page)).toBe('graduated_colors')
    await page.keyboard.press('Control+z')
    await expect.poll(() => rendererKind(page)).toBeNull()

    await sendQuery(page, '¿por qué ya no se ven los colores?')
    await expect.poll(() => cuerpos.length).toBe(3)
    const acciones = cuerpos[2].map_context.acciones
    expect(acciones).toEqual([expect.objectContaining({
      op: 'set_style', layer_name: 'Lotes', author: 'agent', undone: true,
    })])
    // Y el estado que ve: la capa, sin estilo.
    const lotes = cuerpos[2].map_context.layers.find((l: { name: string }) => l.name === 'Lotes')
    expect(lotes.style).toBeUndefined()
  })

  test('las órdenes del agente al mapa se aplican y quedan deshacibles (opacidad, orden, ocultar, zoom)', async ({ page }) => {
    await conLotes(page, (body, n) => {
      if (n === 1) return queryResult({ message: 'Lotes.', geojson: fc(5), layerName: 'Lotes' })
      if (n === 2) return queryResult({ message: 'Vías.', geojson: fc(3, (i) => ({ id: i })), layerName: 'Vías' })
      const [lotes, vias] = body.map_context.layers
      return queryResult({ message: 'Hecho.', intent: 'map_control', artifacts: [
        { kind: 'map_command', command: { op: 'set_opacity', layer_id: vias.id, args: { opacity: 0.3 }, reason: null } },
        { kind: 'map_command', command: { op: 'reorder', layer_id: vias.id,
                                          args: { to: 'below', relative_to: lotes.id }, reason: null } },
        { kind: 'map_command', command: { op: 'zoom_to', layer_id: 'activa', args: { bbox: null }, reason: null } },
      ] as never })
    })
    await sendQuery(page, 'trae los lotes')
    await waitForFeatureCount(page, 5)
    await sendQuery(page, 'trae las vías')
    await waitForFeatureCount(page, 8)
    const nombres = async () => (await page.evaluate(() => (window as Win).__mapTestState.layers)).map((l: { name: string }) => l.name)
    expect(await nombres()).toEqual(['Lotes', 'Vías'])

    await sendQuery(page, 'pon las vías semitransparentes y debajo de los lotes')
    await expect.poll(nombres).toEqual(['Vías', 'Lotes'])
    expect((await layerOrder(page))[0].opacity).toBeCloseTo(0.3)

    // Deshacer dos veces: primero el orden, luego la opacidad (zoom no cambia capas).
    await page.getByTestId('deshacer').click()
    await expect.poll(nombres).toEqual(['Lotes', 'Vías'])
    await page.getByTestId('deshacer').click()
    await expect.poll(async () => (await layerOrder(page))[1].opacity).toBe(1)
  })

  test('los gestos del usuario llegan al agente con autor «user»', async ({ page }) => {
    const cuerpos = await conLotes(page, (_b, n) => (n === 1
      ? queryResult({ message: 'Lotes.', geojson: fc(5), layerName: 'Lotes' })
      : queryResult({ message: 'Ok.' })))
    await sendQuery(page, 'trae los lotes')
    await waitForFeatureCount(page, 5)
    await page.locator('[title="Capas"]').click()
    await page.getByLabel(/Opacidad de Lotes/).fill('0.5')
    await page.getByTitle('Ocultar').first().click()
    await sendQuery(page, '¿qué cambié?')
    await expect.poll(() => cuerpos.length).toBe(2)
    const acciones = cuerpos[1].map_context.acciones
    expect(acciones.map((a: { op: string; author: string }) => [a.op, a.author])).toEqual(
      [['set_opacity', 'user'], ['set_visibility', 'user']])
    const lotes = cuerpos[1].map_context.layers[0]
    expect(lotes).toMatchObject({ opacity: 0.5, visible: false })
    // La capa que añadió el agente en el turno 1 no se reenvía: ya lo sabe.
    expect(acciones.some((a: { op: string }) => a.op === 'add_layer')).toBe(false)
  })

  test('«acércate» ya no se resuelve en el cliente: va al agente, que responde con zoom_to', async ({ page }) => {
    const cuerpos = await conLotes(page, (body, n) => (n === 1
      ? queryResult({ message: 'Lotes.', geojson: fc(5), layerName: 'Lotes' })
      : queryResult({ message: 'Me acerco a los lotes.', intent: 'map_control', artifacts: [
          { kind: 'map_command', command: { op: 'zoom_to', layer_id: body.map_context.layers[0].id,
                                            args: { bbox: null }, reason: null } }] as never })))
    await sendQuery(page, 'trae los lotes')
    await waitForFeatureCount(page, 5)
    await page.evaluate(() => (window as Win).__mlmap.jumpTo({ center: [-60, 0], zoom: 3 }))
    await sendQuery(page, 'acércate')
    await expect.poll(() => cuerpos.length).toBe(2)
    await expect.poll(() => page.evaluate(() => (window as Win).__mlmap.getZoom()), { timeout: 8_000 }).toBeGreaterThan(8)
  })
})
