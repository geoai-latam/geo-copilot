import { test, expect, type WebSocketRoute } from '@playwright/test'
import { mockInit, respuesta, waitForMap, sendQuery } from './helpers'

/**
 * FUNCIONALIDAD: HITL (human-in-the-loop). Una operación sensible (SQL/código)
 * dispara una solicitud de aprobación que llega por WebSocket y abre el panel;
 * el usuario aprueba o rechaza. Se mockea el WS con page.routeWebSocket y el
 * POST /approval, sin backend real.
 */
const APPROVAL = {
  type: 'approval_request',
  data: {
    approval_id: 'ap-e2e-1',
    content_type: 'sql',
    content: 'DELETE FROM catastro.lotes WHERE 1=1',
    warnings: ['Operación destructiva sobre 933473 filas'],
    title: 'Ejecutar consulta SQL',
  },
}

type Decision = { approvalId: string; action: string }

async function setupHitl(
  page: import('@playwright/test').Page,
): Promise<{ getWs: () => WebSocketRoute | null; decisiones: Decision[] }> {
  let ws: WebSocketRoute | null = null
  const decisiones: Decision[] = []
  await page.routeWebSocket(/\/ws\//, (route) => {
    ws = route
    route.onMessage(() => { /* consumir mensajes del cliente sin reenviar */ })
  })
  await mockInit(page)
  // La consulta dispara la solicitud de aprobación por el WS (HITL bloqueante).
  await page.route('**/api/v1/query/', async (r) => {
    ws?.send(JSON.stringify(APPROVAL))
    return r.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify(respuesta({ status: 'waiting_approval', message: '', requires_approval: true })),
    })
  })
  // POST /approval/{id} → éxito, y se ANOTA la decisión enviada. Antes este
  // mock solo devolvía 200: los dos tests terminaban en la misma aserción (que
  // el panel se cierra), así que aprobar y rechazar eran indistinguibles. Es el
  // guardarraíl de seguridad del producto y era el test más flojo de la suite.
  await page.route('**/api/v1/approval/**', (r) => {
    const req = r.request()
    const cuerpo = req.postDataJSON() as { action?: string } | null
    decisiones.push({
      approvalId: (req.url().split('/').pop() ?? '').split('?')[0],
      action: cuerpo?.action ?? '(sin action)',
    })
    return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ success: true }) })
  })
  return { getWs: () => ws, decisiones }
}

test.describe('HITL — aprobación de operaciones sensibles', () => {
  test('la solicitud aparece con el SQL y "Aprobar y ejecutar" la resuelve', async ({ page }) => {
    const { decisiones } = await setupHitl(page)
    await page.goto('/')
    await waitForMap(page)
    await sendQuery(page, 'borra todos los lotes')

    // El panel de aprobación muestra el SQL y el riesgo.
    await expect(page.locator('.sql-preview')).toContainText('DELETE FROM catastro.lotes', { timeout: 10_000 })
    await expect(page.getByText(/Operación destructiva/)).toBeVisible()

    // Aprobar → el panel se cierra (aprobación resuelta).
    await page.getByRole('button', { name: /Aprobar y ejecutar/ }).click()
    await expect(page.locator('.sql-preview')).toHaveCount(0, { timeout: 10_000 })

    // Lo que de verdad separa este test del de rechazo: la decisión ENVIADA.
    expect(decisiones).toEqual([{ approvalId: 'ap-e2e-1', action: 'approve' }])
  })

  test('"Rechazar" cierra la solicitud sin ejecutar', async ({ page }) => {
    const { decisiones } = await setupHitl(page)
    await page.goto('/')
    await waitForMap(page)
    await sendQuery(page, 'borra todos los lotes')

    await expect(page.locator('.sql-preview')).toContainText('DELETE FROM catastro.lotes', { timeout: 10_000 })
    await page.getByRole('button', { name: /^Rechazar/ }).click()
    await expect(page.locator('.sql-preview')).toHaveCount(0, { timeout: 10_000 })

    // Denegar tiene que llegar al servidor COMO denegación. Que el panel se
    // cierre no prueba nada: se cierra igual en los dos caminos.
    expect(decisiones).toEqual([{ approvalId: 'ap-e2e-1', action: 'reject' }])
    expect(decisiones.some((d) => d.action === 'approve')).toBe(false)
  })
})

test.describe('HITL — dos solicitudes seguidas (V5 F3 en Chrome)', () => {
  test('aprobar la primera NO aprueba la segunda: espera un clic propio', async ({ page }) => {
    const { getWs, decisiones } = await setupHitl(page)
    await page.goto('/')
    await waitForMap(page)
    await sendQuery(page, 'trae las construcciones del buffer')
    await expect(page.locator('.sql-preview')).toContainText('DELETE FROM catastro.lotes', { timeout: 10_000 })

    // El puntero queda SOBRE el botón (como en Chrome) y aprueba la primera.
    const aprobar = page.getByRole('button', { name: /Aprobar y ejecutar/ })
    await aprobar.hover()
    await aprobar.click()
    await expect.poll(() => decisiones.length).toBe(1)

    // Llega la segunda solicitud del mismo turno (el bucle pidió otra consulta).
    getWs()?.send(JSON.stringify({
      type: 'approval_request',
      data: { ...APPROVAL.data, approval_id: 'ap-e2e-2', content: 'SELECT COUNT(*) FROM catastro.construcciones' },
    }))
    await expect(page.locator('.sql-preview')).toContainText('SELECT COUNT(*)', { timeout: 10_000 })

    // Sin tocar nada durante 3 s, la segunda sigue PENDIENTE.
    await page.waitForTimeout(3_000)
    expect(decisiones).toEqual([{ approvalId: 'ap-e2e-1', action: 'approve' }])
    // …y el botón se LEE con el puntero encima (en Chrome quedaba en blanco).
    await aprobar.hover()
    await expect(aprobar).toHaveText(/Aprobar y ejecutar/)
    const color = await aprobar.evaluate((b) => {
      const s = getComputedStyle(b)
      return { fg: s.color, bg: s.backgroundColor }
    })
    expect(color.fg).not.toBe(color.bg)
  })
})
