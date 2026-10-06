import * as fs from 'node:fs'
import * as path from 'node:path'
import { expect, type Page } from '@playwright/test'

/**
 * F6 — identidad en los E2E contra el stack REAL (Keycloak de desarrollo, realm `geo`).
 *
 * Usuarios de prueba (docker/keycloak/geo-realm.json): ana (acme, analyst), beto (acme, viewer),
 * carla (acme, admin), diego (beta, analyst). Contraseña: E2E_KC_PASSWORD o, en desarrollo,
 * KC_DEV_PASSWORD del .env de la raíz. Sin OIDC en el backend, todo esto no hace nada.
 */

export type Usuario = 'ana' | 'beto' | 'carla' | 'diego'

const KC = process.env.E2E_KC_URL ?? 'http://localhost:8081/realms/geo'
const API = process.env.E2E_API_URL ?? 'http://localhost:8000'

function passwordDev(): string {
  if (process.env.E2E_KC_PASSWORD) return process.env.E2E_KC_PASSWORD
  const env = path.resolve(process.cwd(), '..', '.env')  // se corre desde frontend/
  const linea = fs.existsSync(env)
    ? fs.readFileSync(env, 'utf8').split(/\r?\n/).find((l) => l.startsWith('KC_DEV_PASSWORD='))
    : undefined
  if (!linea) throw new Error('falta la contraseña de los usuarios de prueba (E2E_KC_PASSWORD o KC_DEV_PASSWORD en .env)')
  return linea.slice('KC_DEV_PASSWORD='.length).trim()
}

/** Valor de una variable del .env de la raíz (secretos de desarrollo que los E2E necesitan). */
export function envDev(nombre: string): string {
  if (process.env[nombre]) return process.env[nombre] as string
  const env = path.resolve(process.cwd(), '..', '.env')  // se corre desde frontend/
  const linea = fs.readFileSync(env, 'utf8').split(/\r?\n/).find((l) => l.startsWith(`${nombre}=`))
  if (!linea) throw new Error(`falta ${nombre} en el .env`)
  return linea.slice(nombre.length + 1).trim()
}

export async function oidcActivo(): Promise<boolean> {
  try {
    const r = await fetch(`${API}/api/v1/auth/config`)
    return (await r.json()).modo === 'oidc'
  } catch {
    return false
  }
}

/** Un token de acceso real del usuario (cliente geo-copilot-e2e, solo desarrollo). */
export async function tokenDe(usuario: Usuario): Promise<string> {
  const r = await fetch(`${KC}/protocol/openid-connect/token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({ grant_type: 'password', client_id: 'geo-copilot-e2e', username: usuario,
                                password: passwordDev(), scope: 'openid' }),
  })
  if (!r.ok) throw new Error(`Keycloak no dio token a ${usuario}: ${r.status}`)
  return (await r.json()).access_token as string
}

/** Cabeceras para llamar a la API como ese usuario (vacías sin OIDC). */
export async function cabecerasDe(usuario: Usuario): Promise<Record<string, string>> {
  return (await oidcActivo()) ? { Authorization: `Bearer ${await tokenDe(usuario)}` } : {}
}

/** Como una persona: la pantalla de entrada → el formulario de Keycloak → de vuelta en la app. */
export async function entrar(page: Page, usuario: Usuario): Promise<void> {
  await page.goto('/')
  const boton = page.getByRole('button', { name: /Iniciar sesión/ })
  await boton.waitFor({ timeout: 60_000 })
  await boton.click()
  await page.locator('#username').fill(usuario)
  await page.locator('#password').fill(passwordDev())
  await page.locator('#kc-login').click()
  await expect(page.locator('.menu-usuario')).toBeVisible({ timeout: 60_000 })
}

/** El token que la app guardó en el navegador (para un fetch dentro de la página). */
export const TOKEN_EN_PAGINA = `(() => {
  const k = Object.keys(localStorage).find((x) => x.startsWith('oidc.user:'))
  const t = k ? JSON.parse(localStorage.getItem(k) || '{}').access_token : null
  return t ? { Authorization: 'Bearer ' + t } : {}
})()`
