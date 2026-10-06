import { chromium, type FullConfig } from '@playwright/test'
import { mockAuth } from './helpers'

/**
 * Calienta el servidor de desarrollo antes del primer test.
 *
 * Por qué existe: `webServer` de Playwright solo espera a que el PUERTO abra.
 * Vite abre el puerto en ~350 ms y después, en la primera petición, optimiza
 * dependencias (maplibre-gl, react, recharts…) — eso tarda bastante más que los
 * 30 s de `timeout` por test. Resultado medido el 8-sep-2026 con la caché de
 * Vite fría: **28 de 28 specs en rojo**, todos con el mismo síntoma
 * (`waitForFunction` esperando `__mapTestState`), y los mismos 28 en verde en
 * cuanto el servidor está caliente. Es decir: quien clonara el repositorio y
 * corriera `npx playwright test` veía la suite entera rota sin que nada
 * estuviera roto.
 *
 * Esto hace UNA carga con presupuesto propio y generoso, y después los tests
 * corren contra un servidor que ya sirve módulos compilados.
 */
export default async function globalSetup(config: FullConfig): Promise<void> {
  const base = config.projects[0]?.use?.baseURL ?? 'http://localhost:3000'
  const navegador = await chromium.launch()
  const pagina = await navegador.newPage()
  // F6: sin login (el backend real, con OIDC, dejaría la carga en la pantalla de entrada)
  await mockAuth(pagina)
  const t0 = Date.now()
  try {
    await pagina.goto(base, { waitUntil: 'domcontentloaded', timeout: 120_000 })
    // El oráculo del mapa es la última pieza en montarse: si está, el bundle
    // entero se sirvió ya compilado.
    await pagina.waitForFunction(
      () => !!(window as unknown as { __mapTestState?: unknown }).__mapTestState,
      undefined,
      { timeout: 120_000 },
    )
    console.log(`[global-setup] servidor caliente en ${((Date.now() - t0) / 1000).toFixed(1)} s`)
  } catch (err) {
    // No abortamos la suite: que fallen los tests con su propio mensaje es más
    // informativo que un fallo de setup sin contexto. Pero se avisa.
    console.warn(
      `[global-setup] no se pudo calentar ${base} en 120 s (${String(err).slice(0, 120)}). ` +
      'Los specs pueden fallar por timeout de arranque, no por regresión.',
    )
  } finally {
    await navegador.close()
  }
}
