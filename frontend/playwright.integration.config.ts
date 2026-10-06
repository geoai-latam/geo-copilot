import { defineConfig, devices } from '@playwright/test'

/**
 * E2E de INTEGRACIÓN — contra el stack REAL (backend + PostGIS + LLM + HITL).
 *
 * A diferencia de `playwright.config.ts` (headless, backend mockeado con
 * page.route, determinista, para CI), estos tests NO mockean nada: ejercitan el
 * sistema completo. Por eso:
 *  - Timeouts largos (el LLM + SQL + HITL tardan segundos).
 *  - Aserciones FLOJAS pero reales (toleran el no-determinismo del LLM: "apareció
 *    un número", "se renderizó una capa" — no "dice exactamente X").
 *  - Requieren el stack levantado (docker compose up) sirviendo en :3000 y
 *    proxeando /api al backend real.
 *
 * Correr a demanda: `npm run test:e2e:real` (con el stack arriba).
 */
export default defineConfig({
  testDir: './e2e-integration',
  timeout: 120_000, // LLM + HITL + SQL end-to-end
  expect: { timeout: 30_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list']],
  use: {
    // E2E_REAL_URL: el frontend de producción (nginx, :3000) NO publica el
    // oráculo __mapTestState (solo DEV o VITE_E2E=true, MapLibreMap.tsx).
    // Para estos tests: `npm run dev` (puerto 5173: es un origen permitido por
    // CORS y por el chequeo de Origin del WS) y E2E_REAL_URL=http://localhost:5173.
    baseURL: process.env.E2E_REAL_URL ?? 'http://localhost:3000',
    trace: 'on-first-retry',
    actionTimeout: 30_000,
  },
  projects: [
    // F6: con OIDC en el backend, Ana entra una vez (formulario real de Keycloak) y los specs
    // reutilizan su sesión; fase-6-identidad entra con cada usuario por su cuenta.
    { name: 'setup', testMatch: /auth\.setup\.ts/ },
    {
      name: 'chromium',
      dependencies: ['setup'],
      use: {
        ...devices['Desktop Chrome'],
        launchOptions: { args: ['--use-gl=angle', '--use-angle=swiftshader'] },
        storageState: 'e2e-integration/.auth/ana.json',
      },
    },
  ],
  // Reusa el stack YA corriendo (docker/dev). NO arranca un mock: si :3000 no
  // responde, la suite falla con un mensaje claro (hay que levantar el stack).
  webServer: {
    command: 'npm run dev',
    url: 'http://localhost:3000',
    reuseExistingServer: true,
    timeout: 60_000,
  },
})
