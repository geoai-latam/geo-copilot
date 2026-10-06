import { test, expect } from '@playwright/test'
import { mockInit, mockQuery, queryResult, waitForMap, sendQuery } from './helpers'

/**
 * FUNCIONALIDAD: shell de la UI — nueva conversación (limpia el chat), panel de
 * apariencia (Tweaks) y las sugerencias del estado vacío.
 */
test.describe('Sesión y UI', () => {
  test('"Nueva conversación" limpia el chat y vuelve al estado vacío', async ({ page }) => {
    await mockInit(page)
    await mockQuery(page, queryResult({ message: 'Respuesta de prueba.' }))
    // la sesión sigue viva (si no, la recuperación del socket crearía otra y el test no distinguiría)
    await page.route('**/api/v1/session/e2e-session', (r) => r.fulfill({
      status: 200, contentType: 'application/json', body: JSON.stringify({ session_id: 'e2e-session' }) }))
    await page.goto('/')
    await waitForMap(page)

    await sendQuery(page, 'hola')
    // a partir de aquí el backend entrega OTRA sesión: la pestaña tiene que pasar a usarla
    await page.route('**/api/v1/session/', (r) => r.request().method() === 'POST'
      ? r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ session_id: 'e2e-sesion-nueva' }) })
      : r.fallback())
    await expect(page.getByText('Respuesta de prueba.')).toBeVisible()

    // "Nueva conversación" pide confirmación (window.confirm) — aceptarla.
    page.on('dialog', (dialog) => dialog.accept())
    await page.locator('[title="Nueva conversación"]').click()
    // Vuelve el estado vacío (título del copiloto) y desaparece el mensaje.
    await expect(page.locator('.empty-state-title')).toHaveText('Copiloto geoespacial')
    await expect(page.getByText('Respuesta de prueba.')).toHaveCount(0)
    // V5 FH: una conversación nueva es una SESIÓN nueva (historial y workspace vacíos)
    await expect.poll(() => page.evaluate(() => window.sessionStorage.getItem('geo.session'))).toBe('e2e-sesion-nueva')
  })

  test('el panel de apariencia (Tweaks) se abre desde el rail', async ({ page }) => {
    await mockInit(page)
    await page.goto('/')
    await waitForMap(page)

    await page.locator('[title="Ajustes de apariencia"]').click()
    // El panel muestra los controles de tema y densidad.
    await expect(page.getByText('apariencia', { exact: true })).toBeVisible()
    await expect(page.getByText('tema', { exact: true })).toBeVisible()
    await expect(page.getByText('densidad', { exact: true })).toBeVisible()
  })

  test('el estado vacío ofrece sugerencias que lanzan una consulta', async ({ page }) => {
    await mockInit(page)
    await mockQuery(page, queryResult({ message: 'Aquí están las entidades.' }))
    await page.goto('/')
    await waitForMap(page)

    // Hay chips de sugerencia; al hacer click se envía la consulta.
    const firstSuggestion = page.locator('.suggestion').first()
    await expect(firstSuggestion).toBeVisible()
    const text = (await firstSuggestion.textContent())?.trim() ?? ''
    await firstSuggestion.click()
    // El texto de la sugerencia aparece como mensaje del usuario (se envió).
    await expect(page.getByText(text.replace(/^\s+/, ''), { exact: false }).first()).toBeVisible()
  })
})
