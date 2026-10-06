import * as fs from 'node:fs'
import { test as setup } from '@playwright/test'
import { entrar, oidcActivo } from './identidad'

/**
 * F6: antes de la regresión, Ana (acme, analista) entra UNA vez por el formulario real de
 * Keycloak; el estado del navegador (tokens en localStorage, sesión del proveedor en cookies)
 * se reutiliza en todos los specs. Sin OIDC en el backend, un estado vacío.
 */
export const ESTADO_ANA = 'e2e-integration/.auth/ana.json'

setup('entrar como ana', async ({ page }) => {
  fs.mkdirSync('e2e-integration/.auth', { recursive: true })
  if (await oidcActivo()) {
    await entrar(page, 'ana')
  }
  await page.context().storageState({ path: ESTADO_ANA })
})
