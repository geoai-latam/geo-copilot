import { test, expect, type Page } from '@playwright/test'

/**
 * E2E de INTEGRACIÓN — FRT-04 (capa objetivo por NOMBRE) contra el stack REAL
 * (LLM + PostGIS + HITL), sin mocks. Nivel 3 del spec
 * docs/SPEC_FRT-04_seleccion_capa_por_nombre.md.
 *
 * ESCENARIO (el que destapó el bug en la validación en vivo):
 *   1. cargar 40 lotes  → capa A
 *   2. cargar 30 construcciones → capa B (queda ACTIVA / última)
 *   3. "colorea los lotes de rojo" → debe re-estilar A (la NOMBRADA), NO B (la activa)
 *
 * La query 3 es COMPUESTA (symbology + color) → `react_policy=hybrid` la enruta a
 * agent_loop (ReAct), NO al nodo router. Este test cubre justo ESE camino: antes
 * del fix, el SymbologyAgent corría sobre B (features=30); después, sobre A (=40).
 *
 * DISCRIMINADOR ROBUSTO: el featureCount de la capa recién pintada de rojo
 * (single_symbol). No depende de nombres generados por el LLM: 40 (lotes) vs
 * 30 (construcciones) es la señal que se invierte entre roto y arreglado.
 *
 * Requiere el stack levantado (docker compose up). `npm run test:e2e:real`.
 */

 
type Win = any

interface LayerProbe {
  id: string
  name: string
  featureCount: number
  color: string
  rendererKind: string | null
}

async function waitForMap(page: Page): Promise<void> {
  await page.waitForFunction(
    () => (window as Win).__mapTestState?.engine === 'maplibre' && !!(window as Win).__mlmap,
    undefined,
    { timeout: 30_000 },
  )
}

async function sendQuery(page: Page, text: string): Promise<void> {
  const input = page.getByPlaceholder(/Pregunta en lenguaje natural/i)
  await expect(input).toBeEnabled({ timeout: 20_000 })
  await input.fill(text)
  await input.press('Enter')
}

async function waitForHitlSql(page: Page): Promise<string> {
  const preview = page.locator('.sql-preview')
  await expect(preview).toBeVisible({ timeout: 60_000 })
  return (await preview.textContent()) ?? ''
}

async function approve(page: Page): Promise<void> {
  await page.getByRole('button', { name: /Aprobar y ejecutar/ }).click()
  await expect(page.locator('.sql-preview')).toHaveCount(0, { timeout: 60_000 })
}

async function getLayers(page: Page): Promise<LayerProbe[]> {
  return page.evaluate(() => (window as Win).__mapTestState?.layers ?? [])
}

/** ¿El color hex es un rojo dominante? Robusto a variantes (#FF0000, #ef4444…). */
function isRed(color: string): boolean {
  const m = /^#?([0-9a-f]{6})$/i.exec((color ?? '').trim())
  if (!m) return false
  const n = parseInt(m[1], 16)
  const r = (n >> 16) & 0xff
  const g = (n >> 8) & 0xff
  const b = n & 0xff
  return r > 180 && g < 100 && b < 100
}

const isRedSingle = (l: LayerProbe): boolean =>
  l.rendererKind === 'single_symbol' && isRed(l.color)

test.describe('Integración REAL — FRT-04: re-estilar la capa NOMBRADA, no la activa', () => {
  test('"colorea los lotes de rojo" pinta LOTES (40), no la activa construcciones (30)', async ({
    page,
  }) => {
    test.setTimeout(240_000) // 3 queries reales + LLM + HITL + ReAct

    await page.goto('/')
    await waitForMap(page)
    expect(await getLayers(page)).toHaveLength(0)

    // ── 1. cargar lotes ──────────────────────────────────────────────────
    await sendQuery(page, 'tráeme 40 lotes de catastro en el mapa')
    const sql1 = await waitForHitlSql(page)
    expect(sql1.toLowerCase()).toContain('lotes')
    await approve(page)
    await expect.poll(() => getLayers(page).then((ls) => ls.length), {
      timeout: 60_000,
    }).toBe(1)

    const lotes = (await getLayers(page))[0]
    const lotesCount = lotes.featureCount
    expect(lotesCount).toBeGreaterThan(0)

    // ── 2. cargar construcciones (queda ACTIVA / última) ─────────────────
    await sendQuery(page, 'ahora tráeme 30 construcciones de catastro en el mapa')
    const sql2 = await waitForHitlSql(page)
    expect(sql2.toLowerCase()).toContain('construcciones')
    await approve(page)
    await expect.poll(() => getLayers(page).then((ls) => ls.length), {
      timeout: 60_000,
    }).toBe(2)

    const construcciones = (await getLayers(page)).find((l) => l.id !== lotes.id)!
    const construccionesCount = construcciones.featureCount
    expect(construccionesCount).toBeGreaterThan(0)
    // Sin este contraste el test no discriminaría cuál capa se pintó.
    expect(construccionesCount).not.toBe(lotesCount)

    // ── 3. FRT-04: colorear LA NOMBRADA (lotes), NO la activa ────────────
    // Query compuesta → hybrid → agent_loop (ReAct). Sin HITL (re-estilo).
    await sendQuery(page, 'colorea los lotes de rojo')

    // Espera a que exista una capa roja single_symbol (el re-estilo se renderizó).
    // Si el bug reapareciera, la capa roja sería construcciones (30): la espera
    // igual pasa y la ASERCIÓN de abajo falla claro (30 ≠ 40), no por timeout.
    await expect.poll(() => getLayers(page).then((ls) => ls.some(isRedSingle)), {
      timeout: 90_000,
    }).toBe(true)

    const styled = (await getLayers(page)).find(isRedSingle)!
    // La capa pintada de rojo tiene el conteo de LOTES (la nombrada), no el de
    // construcciones (la activa). ESTA es la esencia de FRT-04 en el camino ReAct.
    expect(styled.featureCount).toBe(lotesCount)
    expect(styled.featureCount).not.toBe(construccionesCount)
  })
})
