import { defineConfig, devices } from '@playwright/test'

// E2E_PORT: el stack de docker ocupa el 3000 (nginx + backend REAL detrás).
// Con `reuseExistingServer`, estos E2E "deterministas" corrían contra él sin
// avisar. Para correrlos con el stack arriba: E2E_PORT=3100 npx playwright test
const PORT = Number(process.env.E2E_PORT ?? 3000)

/**
 * Arnés E2E de paridad (FND-E2E-HARNESS). Valida la migración a MapLibre GL
 * usando los oráculos instrumentados en la app:
 *   - window.__mapTestState  → motor de mapa activo + featureCount + renderer.
 *   - window.__mlmap         → instancia del mapa (expuesta en DEV/E2E).
 *
 * Determinista: NO ejecuta consultas al LLM. Prueba el motor de mapa y los
 * controles de UID que repuso el retire de Cesium (selector de basemap,
 * re-centrar). Corre contra el dev server de Vite (DEV → __mlmap disponible).
 */
export default defineConfig({
  testDir: './e2e',
  // Calienta el dev server antes del primer test. Sin esto, con la caché de
  // Vite fría, los 28 specs caen por timeout de arranque y no por regresión
  // (medido el 8-sep-2026). Ver e2e/global-setup.ts.
  globalSetup: './e2e/global-setup.ts',
  timeout: 30_000,
  expect: { timeout: 10_000 },
  // MapLibre necesita WebGL (SwiftShader headless). Varios contextos WebGL en
  // paralelo contienden y hacen flaky el oráculo del render (featureCount). Un
  // solo worker mantiene los E2E DETERMINISTAS a cambio de correr en serie.
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: [['list']],
  use: {
    baseURL: `http://localhost:${PORT}`,
    trace: 'on-first-retry',
  },
  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        // WebGL headless vía SwiftShader (MapLibre necesita WebGL).
        launchOptions: { args: ['--use-gl=angle', '--use-angle=swiftshader'] },
      },
    },
  ],
  webServer: {
    command: `npm run dev -- --port ${PORT} --strictPort`,
    port: PORT,
    reuseExistingServer: true,
    timeout: 60_000,
  },
})
