import { type Page, type Route, expect } from '@playwright/test'
import type { Artifact, QueryResponse } from '../src/contracts'
import { capa, estilo, respuesta } from '../src/contracts/fixtures'
export { capa, capaRaster, estilo, respuesta } from '../src/contracts/fixtures'

/**
 * Utilidades compartidas de los E2E. Todos los specs son DETERMINISTAS: no
 * llaman al LLM ni al backend real — interceptan las llamadas con `page.route`
 * y sirven respuestas fijas. Las aserciones van sobre los oráculos
 * instrumentados de la app (window.__mapTestState / __mlmap) o sobre el DOM.
 */


export type Win = any

export const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })

/** FeatureCollection de `n` puntos alrededor de Bogotá con properties simples. */
export function fc(n: number, props: (i: number) => Record<string, unknown> = (i) => ({ id: i, uso: ['res', 'com', 'ind'][i % 3], valor: i * 10 })) {
  return {
    type: 'FeatureCollection',
    features: Array.from({ length: n }, (_, i) => ({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [-74.08 + (i % 5) * 0.01, 4.6 + Math.floor(i / 5) * 0.01] },
      properties: props(i),
    })),
  }
}

/** F6: identidad de un E2E determinista. Sin OIDC (como el backend de desarrollo sin login) y
 * con un usuario administrador: la UI completa, sin depender del backend real. */
export const USUARIO_E2E = { sub: 'e2e', org_id: 'e2e', rol: 'admin', nombre: 'E2E', via: 'dev' }

export async function mockAuth(page: Page, usuario: Record<string, unknown> = USUARIO_E2E,
                               config: Record<string, unknown> = { modo: 'ninguno' }): Promise<void> {
  await page.route('**/api/v1/auth/config', (r) => json(r, config))
  await page.route('**/api/v1/auth/yo', (r) => json(r, usuario))
  await page.route('**/api/v1/session/*/ws-ticket', (r) => json(r, { ticket: 'ticket-e2e', expira_en_s: 30 }))
}

/** Mockea el init de la app (identidad / session / health / metadata) para habilitar la UI. */
export async function mockInit(page: Page): Promise<void> {
  await mockAuth(page)
  await page.route('**/api/v1/session/', (r) => json(r, { session_id: 'e2e-session' }))
  await page.route('**/health', (r) => json(r, { status: 'ok', components: { semantic_layer: 'healthy' } }))
  await page.route('**/api/v1/metadata/entities', (r) => json(r, { entities: [{ name: 'lotes' }, { name: 'construcciones' }], total: 2 }))
}

/** Mockea POST /query/ con una respuesta fija (o una función por request). */
export async function mockQuery(
  page: Page,
  response: unknown | ((body: unknown) => unknown),
): Promise<void> {
  await page.route('**/api/v1/query/', async (r) => {
    const body = typeof response === 'function'
      ? (response as (b: unknown) => unknown)(safeJson(r.request().postData()))
      : response
    return json(r, body)
  })
}

function safeJson(s: string | null): unknown {
  try { return s ? JSON.parse(s) : null } catch { return null }
}

/**
 * Respuesta de `/query` VÁLIDA POR CONTRATO (F4): la app la pasa por la misma
 * validación que al backend real, así que un mock desalineado falla en rojo en
 * vez de pintar a medias. Se construye con los fixtures del contrato.
 *
 * - `geojson` → capa inline (con `symbology` como estilo; `target_layer_id` la
 *   sustituye en su sitio).
 * - `symbology` sin `geojson` → orden `set_style` sobre la capa objetivo.
 * - `visualizations` (tabla/gráfico, dialecto viejo de los specs) → artefactos.
 */
export function queryResult(opts: {
  message: string
  intent?: string
  geojson?: unknown
  symbology?: Record<string, unknown> | null
  target_layer_id?: string | null
  layerId?: string
  layerName?: string
  row_count?: number
  sql?: string | null
  visualizations?: { type: string; config?: Record<string, unknown>; data?: Record<string, unknown>[] }[]
  found_services?: Record<string, unknown>[]
  requires_approval?: boolean
  pending_approval_id?: string
  artifacts?: Artifact[]
}): QueryResponse {
  const artifacts: Artifact[] = []
  if (opts.geojson) {
    artifacts.push(capa({
      id: opts.layerId, name: opts.layerName,
      inline: opts.geojson as Record<string, unknown>,
      style: opts.symbology ?? null,
      replaces: opts.target_layer_id ?? null,
    }))
  } else if (opts.symbology) {
    artifacts.push({
      kind: 'map_command',
      // El backend manda el StyleSpec COMPLETO (FH.1 lo valida): se completa con los defaults.
      command: { op: 'set_style', layer_id: opts.target_layer_id ?? null,
                 args: { style: estilo(opts.symbology as never) }, reason: '' },
    })
  }
  for (const v of opts.visualizations ?? []) {
    const data = v.data ?? []
    if (v.type === 'chart') {
      const c = v.config ?? {}
      artifacts.push({
        kind: 'chart', data,
        spec: { chart_type: (c.chart_type ?? 'bar') as 'bar', x_key: String(c.x_key), y_key: String(c.y_key), title: null },
      })
    } else {
      const columns = Object.keys(data[0] ?? {})
      artifacts.push({ kind: 'table', title: null, columns, preview: data, rows_ref: null, total_rows: opts.row_count ?? data.length })
    }
  }
  if (opts.found_services) {
    artifacts.push({
      kind: 'services',
      items: opts.found_services.map((s) => ({
        name: String(s.name ?? ''), url: String(s.url ?? ''), type: String(s.type ?? 'desconocido'),
        description: String(s.description ?? ''), layer_count: (s.layer_count as number | undefined) ?? null,
        source: String(s.source ?? ''),
      })),
    } as Artifact)
  }
  artifacts.push(...(opts.artifacts ?? []))
  return respuesta({
    status: opts.requires_approval ? 'waiting_approval' : 'completed',
    message: opts.message,
    intent: opts.intent ?? 'query_data',
    requires_approval: opts.requires_approval ?? false,
    pending_approval_id: opts.pending_approval_id ?? null,
    sql: opts.sql ?? null,
    query_id: `q-${Math.random().toString(36).slice(2, 10)}`,
    artifacts,
  })
}

export async function waitForMap(page: Page): Promise<void> {
  await page.waitForFunction(
    () => (window as Win).__mapTestState?.engine === 'maplibre' && !!(window as Win).__mlmap,
    undefined,
    { timeout: 20_000 },
  )
}

/** El estilo debe estar cargado antes de añadir capas (evita la carrera de syncLayers). */
export async function waitForStyleLoaded(page: Page): Promise<void> {
  await page.waitForFunction(
    () => (window as Win).__mlmap?.isStyleLoaded?.() === true,
    undefined,
    { timeout: 15_000 },
  )
}

/** Escribe una consulta en el chat y la envía (Enter). Requiere sessionId listo. */
export async function sendQuery(page: Page, text: string): Promise<void> {
  const input = page.getByPlaceholder(/Pregunta en lenguaje natural/i)
  await expect(input).toBeEnabled({ timeout: 10_000 })
  await input.fill(text)
  await input.press('Enter')
}

/** Espera a que el oráculo del mapa refleje `n` features renderizadas. */
export async function waitForFeatureCount(page: Page, n: number): Promise<void> {
  await page.waitForFunction(
    (expected) => (window as Win).__mapTestState?.featureCount === expected,
    n,
    { timeout: 15_000 },
  )
}

/**
 * F4 (T4.3): espera a que el oráculo del mapa tenga una capa de ese tipo
 * (`vector-geojson`, `vector-mvt`, `raster-xyz`, `arcgis-image`, `wms`) y a que
 * su source esté montado en MapLibre. Sin depender de ids internos.
 */
export async function waitForLayerKind(page: Page, kind: string): Promise<string> {
  const handle = await page.waitForFunction(
    (k) => {
      const w = window as Win
      const capa = (w.__mapTestState?.layers ?? []).find((l: { kind?: string }) => l.kind === k)
      return capa && w.__mlmap?.getSource(capa.id) ? capa.id : null
    },
    kind,
    { timeout: 10_000 },
  )
  return (await handle.jsonValue()) as string
}

/** Orden de dibujo del oráculo (abajo → arriba): [id, kind][]. */
export async function layerOrder(page: Page): Promise<Array<{ id: string; kind: string; opacity: number }>> {
  return page.evaluate(() =>
    ((window as Win).__mapTestState?.layers ?? []).map((l: { id: string; kind: string; opacity: number }) => ({
      id: l.id, kind: l.kind, opacity: l.opacity,
    })),
  )
}
