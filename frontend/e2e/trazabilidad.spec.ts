import { test, expect, type WebSocketRoute } from '@playwright/test'
import { mockInit, respuesta, waitForMap, sendQuery } from './helpers'

/**
 * Trazabilidad del chat: mientras el agente trabaja, el usuario ve qué está haciendo (eventos
 * `trace` por WebSocket); al terminar, los pasos quedan con la respuesta («Cómo lo hice»).
 */
const paso = (data: Record<string, unknown>) => JSON.stringify({ type: 'trace', session_id: 'e2e-session', data })

test('el chat cuenta en vivo lo que hace el agente y lo guarda con la respuesta', async ({ page }) => {
  let ws: WebSocketRoute | null = null
  await page.routeWebSocket(/\/ws\//, (route) => { ws = route; route.onMessage(() => {}) })
  await mockInit(page)
  let soltar: () => void = () => {}
  const retenida = new Promise<void>((r) => { soltar = r })
  await page.route('**/api/v1/query/', async (r) => {
    ws?.send(paso({ id: 'i1', tipo: 'interpretar', estado: 'en_curso', titulo: 'Entendiendo tu consulta' }))
    ws?.send(paso({ id: 'i1', tipo: 'interpretar', estado: 'ok', titulo: 'Entendiendo tu consulta',
                    detalle: 'Pide ver una escena Sentinel-2 en NDWI', ms: 900 }))
    ws?.send(paso({ id: 't1', tipo: 'herramienta', estado: 'en_curso', titulo: 'imagery · imagery_scene_view',
                    herramienta: 'imagery__imagery_scene_view', argumentos: { scene_id: 'S2X', product: 'ndwi' } }))
    await retenida
    ws?.send(paso({ id: 't1', tipo: 'herramienta', estado: 'ok', titulo: 'imagery · imagery_scene_view',
                    herramienta: 'imagery__imagery_scene_view', argumentos: { scene_id: 'S2X', product: 'ndwi' },
                    detalle: 'scene.id: S2X · capa: ndwi 2026-08-02', ms: 2300 }))
    await new Promise((x) => setTimeout(x, 150))
    return r.fulfill({ status: 200, contentType: 'application/json',
      body: JSON.stringify(respuesta({ message: 'Listo: la escena en NDWI.' })) })
  })

  await page.goto('/')
  await waitForMap(page)
  // el id de la sesión ya no se muestra en la interfaz
  await expect(page.locator('body')).not.toContainText(/sesión #?[0-9a-f]{6}/)
  await expect(page.locator('.session-id')).toHaveCount(0)

  await sendQuery(page, 'muéstrame la escena S2X en NDWI')

  // En vivo: lo hecho y lo que está en curso, con la herramienta y sus argumentos.
  const viva = page.getByTestId('traza-viva')
  await expect(viva.getByTestId('traza-paso')).toHaveCount(2)
  await expect(viva).toContainText('Pide ver una escena Sentinel-2 en NDWI')
  await expect(viva).toContainText('imagery · imagery_scene_view')
  await expect(viva).toContainText('product: ndwi')
  await expect(page.locator('.thinking')).toContainText('imagery · imagery_scene_view…')
  await expect(viva.locator('.traza-en_curso')).toHaveCount(1)

  soltar()
  // Al terminar: la respuesta y, plegados, los pasos que la produjeron.
  await expect(page.getByText('Listo: la escena en NDWI.')).toBeVisible()
  const hecha = page.getByTestId('traza-respuesta')
  await expect(hecha).toContainText('Cómo lo hice · 2 pasos · 1 herramienta · 3.2 s')
  await hecha.locator('summary').click()
  await expect(hecha.locator('.traza-ok')).toHaveCount(2)
  await expect(hecha).toContainText('capa: ndwi 2026-08-02')
})
