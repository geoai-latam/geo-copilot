import { execFileSync } from 'node:child_process'
import { test, expect, type Page } from '@playwright/test'

/**
 * F7 — V3/V5 contra el stack REAL con réplicas (estado fuera del proceso):
 *
 * E7.1  Una aprobación pendiente SOBREVIVE al reinicio de la app: se reinician las réplicas con el
 *       panel abierto, el usuario aprueba después y el turno se reanuda y entrega su resultado (sin
 *       volver a preguntar: se ejecuta lo que el humano vio).
 * E7.1b El usuario RECARGA la página con la aprobación pendiente: el panel vuelve, aprueba y el
 *       resultado llega al chat por WebSocket (la petición HTTP original ya no existe) — auditoría #14.
 *
 * Reiniciar contenedores exige saber cuáles: E2E_APP_CONTAINERS="docker-app-1,docker-app-2". Sin
 * esa variable, E7.1 se salta (E7.1b no la necesita).
 * Referencia: la manzana 004503009 tiene 4 lotes en catastro.lotes.
 */

type Win = any
const CONTENEDORES = (process.env.E2E_APP_CONTAINERS ?? '').split(',').map((s) => s.trim()).filter(Boolean)
const CONSULTA = 'trae los lotes de la manzana 004503009'

async function waitForMap(page: Page): Promise<void> {
  await page.waitForFunction(
    () => (window as Win).__mapTestState?.engine === 'maplibre' && !!(window as Win).__mlmap,
    undefined, { timeout: 30_000 })
}

async function sesionNueva(page: Page): Promise<void> {
  await page.goto('/')
  await page.evaluate(() => window.sessionStorage.clear())
  await page.reload()
  await waitForMap(page)
}

async function preguntar(page: Page, text: string): Promise<void> {
  const input = page.getByPlaceholder(/Pregunta en lenguaje natural/i)
  await expect(input).toBeEnabled({ timeout: 60_000 })
  await input.fill(text)
  await input.press('Enter')
}

const capaDeCuatro = (page: Page) => page.evaluate(
  () => ((window as Win).__mapTestState?.layers ?? []).some((l: { featureCount: number }) => l.featureCount === 4))

async function appSana(page: Page): Promise<void> {
  await expect.poll(async () => (await page.request.get('/health').catch(() => null))?.status() ?? 0,
    { timeout: 120_000, intervals: [2_000] }).toBe(200)
}

test.describe('F7 · producción (réplicas, estado en Redis)', () => {
  test.setTimeout(420_000)

  test('E7.1: la aprobación pendiente sobrevive al reinicio de las réplicas', async ({ page }) => {
    test.skip(CONTENEDORES.length === 0, 'define E2E_APP_CONTAINERS para reiniciar la app')
    await sesionNueva(page)
    await preguntar(page, CONSULTA)
    await expect(page.locator('.sql-preview')).toBeVisible({ timeout: 120_000 })

    // el proceso que esperaba la aprobación muere con el panel abierto
    execFileSync('docker', ['restart', ...CONTENEDORES], { stdio: 'ignore' })
    await appSana(page)

    // el panel sigue (o vuelve al reconectar el WS) y el usuario aprueba DESPUÉS del reinicio
    await expect(page.locator('.sql-preview')).toBeVisible({ timeout: 90_000 })
    await page.getByRole('button', { name: /Aprobar y ejecutar/ }).click()

    // el turno se reanuda en el proceso nuevo y entrega SU resultado: la capa de 4 lotes
    await expect.poll(() => capaDeCuatro(page), { timeout: 240_000, intervals: [2_000] }).toBe(true)
    // sin volver a preguntar: lo aprobado era exactamente lo que se ejecutó
    await expect(page.locator('.sql-preview')).toHaveCount(0)
    await expect(page.getByPlaceholder(/Pregunta en lenguaje natural/i)).toBeEnabled({ timeout: 60_000 })
  })

  test('E7.1b: recargar con la aprobación pendiente y aprobar entrega el resultado por WS', async ({ page }) => {
    await sesionNueva(page)
    await preguntar(page, CONSULTA)
    await expect(page.locator('.sql-preview')).toBeVisible({ timeout: 120_000 })

    await page.reload()          // la petición HTTP que esperaba el resultado se pierde con la página
    await waitForMap(page)
    await expect(page.locator('.sql-preview')).toBeVisible({ timeout: 60_000 })
    await page.getByRole('button', { name: /Aprobar y ejecutar/ }).click()

    await expect.poll(() => capaDeCuatro(page), { timeout: 240_000, intervals: [2_000] }).toBe(true)
    const ultima = page.locator('.msg.msg-assist').last()
    await expect(ultima).not.toContainText(/Procesando consulta|Consultando/i, { timeout: 60_000 })
    // nada se queda colgado: se puede seguir preguntando
    await expect(page.getByPlaceholder(/Pregunta en lenguaje natural/i)).toBeEnabled({ timeout: 60_000 })
  })
})
