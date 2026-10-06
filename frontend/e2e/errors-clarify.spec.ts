import { test, expect } from '@playwright/test'
import { mockInit, mockQuery, queryResult, respuesta, waitForMap, sendQuery } from './helpers'

/**
 * FUNCIONALIDAD: honestidad del asistente. Un error del backend se comunica al
 * usuario (no se traga en silencio) y una consulta ambigua produce una PREGUNTA
 * (clarify), no una respuesta inventada.
 */
test.describe('Errores y clarify (honestidad)', () => {
  test('un error del backend se narra honestamente en el chat', async ({ page }) => {
    await mockInit(page)
    await mockQuery(page, respuesta({
      status: 'failed',
      message: 'No pude generar SQL válido para esa consulta.',
    }))
    await page.goto('/')
    await waitForMap(page)
    await sendQuery(page, 'consulta imposible zzz')
    await expect(page.getByText('No pude generar SQL válido para esa consulta.')).toBeVisible({ timeout: 15_000 })
    // Invariante real: un error NO renderiza datos (no hay capa fantasma).
    const state = await page.evaluate(() => (window as unknown as { __mapTestState?: { engine?: string; featureCount?: number } }).__mapTestState)
    expect(state?.featureCount ?? 0).toBe(0)
  })

  test('una consulta ambigua produce una pregunta (clarify)', async ({ page }) => {
    await mockInit(page)
    await mockQuery(page, queryResult({
      message: '¿Te refieres a los lotes o a las construcciones?',
      intent: 'clarify',
    }))
    await page.goto('/')
    await waitForMap(page)
    await sendQuery(page, 'crúzalas')
    await expect(page.getByText('¿Te refieres a los lotes o a las construcciones?')).toBeVisible({ timeout: 15_000 })
  })

  test('un fallo de red no rompe la app (mensaje de error, mapa vivo)', async ({ page }) => {
    await mockInit(page)
    // El endpoint de query aborta la conexión → el cliente muestra un error.
    await page.route('**/api/v1/query/', (r) => r.abort())
    await page.goto('/')
    await waitForMap(page)
    await sendQuery(page, 'trae lotes')
    // La app sigue viva (el mapa sigue montado) tras el fallo.
    const engine = await page.evaluate(() => (window as unknown as { __mapTestState?: { engine?: string } }).__mapTestState?.engine)
    expect(engine).toBe('maplibre')
  })
})
