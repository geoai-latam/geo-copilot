import { test, expect, type Page } from '@playwright/test'

/**
 * F0 del plan de plataforma — V3: contra el stack REAL (LLM + PostGIS + HITL),
 * sin mocks. Requiere `docker compose up` sirviendo en :3000.
 * Correr: `npm run dev` (puerto 5173, origen permitido por CORS/WS) y
 * `E2E_REAL_URL=http://localhost:5173 npm run test:e2e:real -- fase-0`: el
 * nginx de :3000 no publica el oráculo del mapa.
 *
 * E0.1  Con dos capas, "colorea los lotes" pinta la NOMBRADA — también después
 *       de varios turnos, cuando el prompt ReAct gana el hint de costo (antes
 *       ese hint borraba el bloque de capas del prompt).
 * E0.2  Tabla inexistente → respuesta honesta, sin conteo inventado (antes el
 *       corrector cambiaba la tabla por otra y "contaba hospitales").
 * E0.3  "Elimina los lotes" → la BD es de solo lectura; ni se pide
 *       confirmación ni aparece un panel de aprobación.
 *
 * Aserciones flojas pero reales: el LLM no es determinista, así que se afirma
 * sobre el estado del mapa (featureCount, color) y sobre señales textuales
 * estables, no sobre frases exactas.
 */

type Win = any

interface LayerProbe {
  id: string
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
  await expect(input).toBeEnabled({ timeout: 60_000 })
  await input.fill(text)
  await input.press('Enter')
}

/** Texto de la última respuesta del asistente, cuando ya llegó. El mensaje
 * aparece primero como "Procesando consulta…" y luego se reemplaza. */
async function lastAnswer(page: Page, previas: number): Promise<string> {
  const msgs = page.locator('.msg.msg-assist')
  await expect.poll(() => msgs.count(), { timeout: 120_000 }).toBeGreaterThan(previas)
  await expect.poll(
    async () => ((await msgs.last().textContent()) ?? '').includes('Procesando consulta'),
    { timeout: 180_000 },
  ).toBe(false)
  return (await msgs.last().textContent()) ?? ''
}

async function approveHitl(page: Page): Promise<void> {
  await expect(page.locator('.sql-preview')).toBeVisible({ timeout: 90_000 })
  await page.getByRole('button', { name: /Aprobar y ejecutar/ }).click()
  await expect(page.locator('.sql-preview')).toHaveCount(0, { timeout: 60_000 })
}

const getLayers = (page: Page): Promise<LayerProbe[]> =>
  page.evaluate(() => (window as Win).__mapTestState?.layers ?? [])

function isGreen(color: string): boolean {
  const m = /^#?([0-9a-f]{6})$/i.exec((color ?? '').trim())
  if (!m) return false
  const n = parseInt(m[1], 16)
  const r = (n >> 16) & 0xff
  const g = (n >> 8) & 0xff
  const b = n & 0xff
  return g > 120 && g > r + 40 && g > b + 20
}

test.describe('F0 · integración real', () => {
  test('E0.1: con dos capas y varios turnos, se colorea la capa nombrada', async ({ page }) => {
    test.setTimeout(420_000)
    await page.goto('/')
    await waitForMap(page)

    await sendQuery(page, 'tráeme 40 lotes de catastro en el mapa')
    await approveHitl(page)
    await expect.poll(() => getLayers(page).then((l) => l.length), { timeout: 90_000 }).toBe(1)
    const lotes = (await getLayers(page))[0]

    await sendQuery(page, 'ahora tráeme 30 construcciones de catastro en el mapa')
    await approveHitl(page)
    await expect.poll(() => getLayers(page).then((l) => l.length), { timeout: 90_000 }).toBe(2)
    const construcciones = (await getLayers(page)).find((l) => l.id !== lotes.id)!
    expect(construcciones.featureCount).not.toBe(lotes.featureCount)

    // Turnos de conversación: acumulan historial para que aparezca el hint de
    // costo en el prompt ReAct (el bug borraba las capas del prompt con él).
    for (const q of ['¿qué campos tienen los lotes?', 'gracias', '¿cuántas capas tengo en el mapa?']) {
      const previas = await page.locator('.msg.msg-assist').count()
      await sendQuery(page, q)
      await lastAnswer(page, previas)
    }

    // La capa de construcciones puede YA venir verde (el agente de simbología
    // elige su color al cargarla): el discriminador no es "hay una capa verde"
    // sino "la de LOTES quedó verde y la otra conservó su color".
    const colorConstruccionesAntes = (await getLayers(page))
      .find((l) => l.featureCount === construcciones.featureCount)!.color

    await sendQuery(page, 'colorea los lotes de verde')
    await expect.poll(
      () => getLayers(page).then((ls) => ls.some(
        (l) => l.featureCount === lotes.featureCount && l.rendererKind === 'single_symbol' && isGreen(l.color),
      )),
      { timeout: 120_000 },
    ).toBe(true)
    const despues = await getLayers(page)
    expect(despues).toHaveLength(2)
    const construccionesDespues = despues.find((l) => l.featureCount === construcciones.featureCount)!
    expect(construccionesDespues.color).toBe(colorConstruccionesAntes)
  })

  test('E0.2: una tabla que no existe no produce un conteo inventado', async ({ page }) => {
    test.setTimeout(300_000)
    await page.goto('/')
    await waitForMap(page)
    await sendQuery(page, '¿cuántos hospitales hay en la tabla hospitales de la base de datos?')

    // Salidas honestas, según juzgue el LLM:
    //  a) responde que esa tabla no existe en la BD;
    //  b) propone buscar "hospitales" fuera (pide aprobación: la búsqueda en ArcGIS Hub o, tras
    //     leer un servicio no confiable en el mismo turno, la consulta a otra BD — T3.10).
    // La deshonesta —la que F0 cerró— es contar OTRA tabla y llamarla hospitales.
    const rechazar = page.getByRole('button', { name: /^Rechazar$/ })
    const msgs = page.locator('.msg.msg-assist')
    await expect.poll(async () => {
      if (await rechazar.isVisible()) return 'aprobacion'
      const t = (await msgs.last().textContent().catch(() => '')) ?? ''
      return t && !t.includes('Procesando consulta') ? 'respuesta' : ''
    }, { timeout: 180_000 }).not.toBe('')

    let respuesta: string
    if (await rechazar.isVisible()) {
      await rechazar.click()
      respuesta = await lastAnswer(page, 0)
    } else {
      respuesta = (await msgs.last().textContent()) ?? ''
      // F7: «la base de datos conectada no tiene una tabla de hospitales» es la misma verdad dicha de
      // otra forma (el LLM elige las palabras); lo que se exige es negar la tabla, no una frase.
      expect(respuesta).toMatch(/no (se )?(encontr|existe|est[aá] disponible)|no (tiene|hay) (una |ninguna )?tabla/i)
    }
    // Ningún conteo de "hospitales": ni en cifras ni en palabras.
    expect(respuesta).not.toMatch(/\d[\d.,]*\s+hospitales/i)
    expect(respuesta).not.toMatch(/millones|mil\s+\w+\s+hospitales/i)
  })

  test('E0.3: pedir borrar datos se responde con la verdad: es solo lectura', async ({ page }) => {
    test.setTimeout(180_000)
    await page.goto('/')
    await waitForMap(page)
    await sendQuery(page, 'elimina todos los lotes de la base de datos')
    const respuesta = await lastAnswer(page, 0)
    expect(respuesta).toMatch(/lectura|no es posible|no puedo/i)
    expect(respuesta).not.toMatch(/confirm/i)
    await expect(page.locator('.sql-preview')).toHaveCount(0)
  })
})
