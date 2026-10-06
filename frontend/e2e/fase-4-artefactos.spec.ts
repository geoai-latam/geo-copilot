import { test, expect, type Page } from '@playwright/test'
import {
  capaRaster, fc, layerOrder, mockInit, mockQuery, queryResult, sendQuery, waitForFeatureCount, waitForLayerKind,
  waitForMap, waitForStyleLoaded, type Win,
} from './helpers'

/**
 * F4 (V2) — frontend genérico: una sola lista de capas para todos los tipos.
 *
 * El orden y la opacidad son comunes a raster y vector: una capa NDVI puede ir
 * encima de una vectorial (antes el raster siempre quedaba debajo) y se ve así
 * en el MAPA, no solo en el panel.
 */

const PNG_1x1 = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M8AAAMBAQDJ/pLvAAAAAElFTkSuQmCC',
  'base64',
)

/** Índice, en el estilo REAL de MapLibre, de la primera capa de estilo de cada source. */
async function ordenEnElMapa(page: Page): Promise<Record<string, number>> {
  return page.evaluate(() => {
    const out: Record<string, number> = {}
    ;((window as Win).__mlmap.getStyle().layers ?? []).forEach((l: { source?: string }, i: number) => {
      if (l.source && l.source !== 'basemap' && !(l.source in out)) out[l.source] = i
    })
    return out
  })
}

async function cargarVectorYRaster(page: Page) {
  await page.route('**/api/v1/proxy/mcp/**', (r) => r.fulfill({ status: 200, contentType: 'image/png', body: PNG_1x1 }))
  await mockQuery(page, queryResult({
    message: 'Lotes y su NDVI.',
    geojson: fc(4),
    artifacts: [capaRaster({
      name: 'NDVI 2026-01-17', url: '/api/v1/proxy/mcp/imagery/tiles/S2/{z}/{x}/{y}.png',
      bbox: [-74.2, 4.5, -74.0, 4.7],
    })],
  }))
  await page.goto('/')
  await waitForMap(page)
  await waitForStyleLoaded(page)
  await sendQuery(page, 'lotes y su ndvi')
  await waitForFeatureCount(page, 4)
  const raster = await waitForLayerKind(page, 'raster-xyz')
  const vector = (await layerOrder(page)).find((l) => l.kind === 'vector-geojson')!.id
  await page.locator('[title="Capas"]').click()
  return { raster, vector }
}

test.describe('F4 · una lista de capas para todos los tipos', () => {
  test.beforeEach(async ({ page }) => mockInit(page))

  test('el raster entra debajo del vector y se puede subir encima (en el mapa, no solo en el panel)', async ({ page }) => {
    const { raster, vector } = await cargarVectorYRaster(page)
    expect((await layerOrder(page)).map((l) => l.id)).toEqual([raster, vector])
    let orden = await ordenEnElMapa(page)
    expect(orden[raster]).toBeLessThan(orden[vector])

    // Panel: arriba en la lista = arriba en el mapa.
    const filas = page.getByTestId('layer-row')
    await expect(filas.first()).toHaveAttribute('data-layer-kind', 'vector-geojson')
    await page.getByRole('button', { name: /Subir NDVI/ }).click()

    await expect.poll(async () => (await layerOrder(page)).map((l) => l.id)).toEqual([vector, raster])
    await expect(filas.first()).toHaveAttribute('data-layer-kind', 'raster-xyz')
    orden = await ordenEnElMapa(page)
    expect(orden[raster]).toBeGreaterThan(orden[vector])
  })

  test('arrastrar una fila cambia el orden entre tipos', async ({ page }) => {
    const { raster, vector } = await cargarVectorYRaster(page)
    await page.locator(`[data-layer-id="${raster}"]`).dragTo(page.locator(`[data-layer-id="${vector}"]`))
    await expect.poll(async () => (await layerOrder(page)).map((l) => l.id)).toEqual([vector, raster])
  })

  test('la opacidad es común: el slider del raster cambia su raster-opacity', async ({ page }) => {
    const { raster } = await cargarVectorYRaster(page)
    await page.getByLabel(/Opacidad de NDVI/).fill('0.35')
    await expect
      .poll(() => page.evaluate((id) => (window as Win).__mlmap.getPaintProperty(`${id}-raster`, 'raster-opacity'), raster))
      .toBeCloseTo(0.35)
    expect((await layerOrder(page)).find((l) => l.id === raster)?.opacity).toBeCloseTo(0.35)
  })
})

test.describe('F4 · panel de resultados (S4.3)', () => {
  test.beforeEach(async ({ page }) => mockInit(page))

  test('E4.1: una consulta analítica deja capa, tabla y gráfico a la vista a la vez', async ({ page }) => {
    await mockQuery(page, queryResult({
      message: 'Área media por uso.',
      geojson: fc(6),
      sql: 'SELECT uso, AVG(area) FROM lotes GROUP BY uso',
      visualizations: [
        { type: 'table', data: [{ uso: 'res', area_media: 120 }, { uso: 'com', area_media: 80 }] },
        { type: 'chart', config: { chart_type: 'bar', x_key: 'uso', y_key: 'area_media' },
          data: [{ uso: 'res', area_media: 120 }, { uso: 'com', area_media: 80 }] },
      ],
    }))
    await page.goto('/')
    await waitForMap(page)
    await waitForStyleLoaded(page)
    await sendQuery(page, 'área media por uso')
    await waitForFeatureCount(page, 6)

    // El panel se abre solo, acoplado: el mapa con la capa sigue visible.
    const panel = page.getByTestId('panel-resultados')
    await expect(panel).toBeVisible()
    await expect(panel.getByTestId('artefacto-table')).toContainText('Area Media')
    await expect(panel.getByTestId('artefacto-table')).toContainText('120')
    await expect(panel.getByTestId('artefacto-chart').locator('svg.recharts-surface').first()).toBeVisible()
    // Las barras se DIBUJAN con su valor (no solo ejes): 120 vs 80 → proporción 1,5.
    await expect.poll(async () => {
      const hs = await panel.getByTestId('artefacto-chart').locator('.recharts-bar-rectangle .recharts-rectangle')
        .evaluateAll((els) => els.map((e) => Number(e.getAttribute('height'))))
      return hs.length === 2 && hs[1] > 20 ? Math.round((hs[0] / hs[1]) * 10) / 10 : null
    }, { timeout: 5_000 }).toBe(1.5)
    await expect(page.locator('.map-container')).toBeVisible()
    const cajaPanel = await panel.boundingBox()
    const cajaMapa = await page.locator('.map-container').boundingBox()
    // Hay mapa a la vista a la izquierda del panel (no lo tapa entero).
    expect(cajaPanel!.x - cajaMapa!.x).toBeGreaterThan(200)
    // Y la capa quedó encuadrada en ESE hueco, no detrás del panel.
    await expect.poll(async () => {
      const xs: number[] = await page.evaluate(() => {
        const map = (window as Win).__mlmap
        const r = map.getCanvas().getBoundingClientRect()
        return [[-74.08, 4.6], [-74.04, 4.61]].map((c) => r.left + map.project(c).x)
      })
      return Math.max(...xs) < cajaPanel!.x
    }, { timeout: 5_000 }).toBe(true)
    // Y el chat resume el turno.
    await expect(page.getByTestId('resumen-turno').last()).toContainText('capa')
    await expect(page.getByTestId('resumen-turno').last()).toContainText('gráfico')
  })

  test('E4.3: el historial vuelve al resultado de hace tres turnos (tabla y SQL de ese turno)', async ({ page }) => {
    let turno = 0
    await mockQuery(page, () => {
      turno += 1
      return queryResult({
        message: `Turno ${turno}.`,
        sql: `SELECT ${turno}`,
        visualizations: [{ type: 'table', data: [{ turno: `valor-${turno}` }] }],
      })
    })
    await page.goto('/')
    await waitForMap(page)
    for (const q of ['primera', 'segunda', 'tercera', 'cuarta']) {
      await sendQuery(page, q)
      await expect(page.getByText(`Turno ${['primera', 'segunda', 'tercera', 'cuarta'].indexOf(q) + 1}.`)).toBeVisible()
    }
    const panel = page.getByTestId('panel-resultados')
    await expect(panel).toContainText('valor-4')

    const historial = panel.getByTestId('historial-resultados')
    await expect(historial.locator('option')).toHaveCount(4)
    const primera = await historial.locator('option', { hasText: 'primera' }).getAttribute('value')
    await historial.selectOption(primera!)
    await expect(panel).toContainText('valor-1')
    await expect(panel).not.toContainText('valor-4')

    // El SQL también es el de ese turno.
    await page.getByRole('button', { name: /^SQL/ }).click()
    await expect(page.getByText('SELECT 1')).toBeVisible()
  })

  test('un informe del agente se muestra como texto (nunca como HTML)', async ({ page }) => {
    await mockQuery(page, queryResult({
      message: 'Informe listo.',
      artifacts: [{ kind: 'report', markdown: '# Resumen\n<img src=x onerror="window.__xss=1">\nTodo bien.', cites: ['ds_1'] }],
    }))
    await page.goto('/')
    await waitForMap(page)
    await sendQuery(page, 'hazme un informe')
    const informe = page.getByTestId('artefacto-report')
    await expect(informe).toContainText('# Resumen')
    await expect(informe).toContainText('<img src=x')
    await expect(informe.locator('img')).toHaveCount(0)
    expect(await page.evaluate(() => (window as Win).__xss)).toBeUndefined()
  })
})

test.describe('F4 · el agente ve las capas por referencia (S4.4, E4.5)', () => {
  test('con el NDVI cargado y un click, la consulta lleva la capa raster y el «aquí»', async ({ page }) => {
    await mockInit(page)
    await page.route('**/api/v1/proxy/mcp/**', (r) => r.fulfill({ status: 200, contentType: 'image/png', body: PNG_1x1 }))
    const cuerpos: Array<Record<string, any>> = []
    await mockQuery(page, (body) => {
      cuerpos.push(body as Record<string, any>)
      return cuerpos.length === 1
        ? queryResult({
            message: 'NDVI listo.',
            artifacts: [capaRaster({
              name: 'NDVI 2026-01-17', url: '/api/v1/proxy/mcp/imagery/tiles/S2B_X/{z}/{x}/{y}.png',
              bbox: [-74.2, 4.5, -74.0, 4.7], arguments: { date_from: '2026-01-01' },
            })],
          })
        : queryResult({ message: 'El NDVI ahí es 0,61.' })
    })
    await page.goto('/')
    await waitForMap(page)
    await waitForStyleLoaded(page)
    await sendQuery(page, 'calcula el ndvi de esta zona')
    const raster = await waitForLayerKind(page, 'raster-xyz')

    // El usuario marca un punto (con el mapa quieto: un click durante el
    // encuadre animado solo lo detiene): se ve el marcador.
    await page.waitForFunction(() => !(window as Win).__mlmap.isMoving())
    const caja = (await page.locator('.map-container').boundingBox())!
    await page.mouse.click(caja.x + caja.width * 0.4, caja.y + caja.height * 0.5)
    await expect(page.getByTestId('punto-marcado')).toBeVisible()

    await sendQuery(page, '¿qué valor tiene el NDVI aquí?')
    await expect.poll(() => cuerpos.length).toBe(2)
    const mc = cuerpos[1].map_context
    const capa = mc.layers.find((l: { id: string }) => l.id === raster)
    expect(capa).toMatchObject({
      kind: 'raster-xyz', name: 'NDVI 2026-01-17',
      url: '/api/v1/proxy/mcp/imagery/tiles/S2B_X/{z}/{x}/{y}.png',
      origin: { capability: 'mcp.imagery.imagery_ndvi', arguments: { date_from: '2026-01-01' } },
      bbox: [-74.2, 4.5, -74.0, 4.7],
    })
    expect(capa.data).toBeUndefined()
    // el «aquí» es un punto real en el mapa
    expect(mc.clicked_point.lon).toBeGreaterThan(-180)
    expect(mc.clicked_point.lat).toBeLessThan(90)
    expect(JSON.stringify(mc).length).toBeLessThan(5 * 1024)
  })
})

test.describe('F4 · varios turnos sobre el mismo mapa (T4.7)', () => {
  test.beforeEach(async ({ page }) => mockInit(page))

  const vectoriales = async (page: Page) => (await layerOrder(page)).filter((l) => l.kind.startsWith('vector'))
  const rendererKind = (page: Page) => page.evaluate(() => (window as Win).__mapTestState?.rendererKind ?? null)

  test('un turno de solo cifras no borra ni duplica la capa del turno anterior', async ({ page }) => {
    // V5 F4: ReAct borraba la capa cuando un análisis devolvía solo un gráfico, y la
    // capa activa reinyectada como externa se volvía a añadir en cada turno.
    let turno = 0
    await mockQuery(page, () => {
      turno += 1
      return turno === 1
        ? queryResult({ message: 'Estos son los lotes.', geojson: fc(5), layerName: 'Lotes' })
        : queryResult({ message: 'Hay 5 lotes.', visualizations: [{ type: 'table', data: [{ n: 5 }] }] })
    })
    await page.goto('/')
    await waitForMap(page)
    await waitForStyleLoaded(page)
    await sendQuery(page, 'muéstrame los lotes')
    await waitForFeatureCount(page, 5)
    const antes = await vectoriales(page)
    expect(antes).toHaveLength(1)

    await sendQuery(page, '¿cuántos lotes hay?')
    await expect(page.getByText('Hay 5 lotes.')).toBeVisible()
    await expect(page.getByTestId('panel-resultados').getByTestId('artefacto-table')).toContainText('5')
    expect(await vectoriales(page)).toEqual(antes)
    await waitForFeatureCount(page, 5)
  })

  test('re-estilar la capa que el agente nombra la sustituye EN SU SITIO (nombre y orden entre tipos)', async ({ page }) => {
    await page.route('**/api/v1/proxy/mcp/**', (r) => r.fulfill({ status: 200, contentType: 'image/png', body: PNG_1x1 }))
    const cuerpos: Array<Record<string, any>> = []
    await mockQuery(page, (body) => {
      cuerpos.push(body as Record<string, any>)
      const n = cuerpos.length
      if (n === 1) return queryResult({ message: 'Lotes.', geojson: fc(6), layerName: 'Lotes' })
      if (n === 2) {
        return queryResult({
          message: 'NDVI.',
          artifacts: [capaRaster({ name: 'NDVI', url: '/api/v1/proxy/mcp/imagery/tiles/S2/{z}/{x}/{y}.png', bbox: [-74.2, 4.5, -74.0, 4.7] })],
        })
      }
      // El backend re-estila la capa que el mapa le reportó, por id (FRT-04), no «la última».
      const lotes = (body as any).map_context.layers.find((l: { name: string }) => l.name === 'Lotes')
      return queryResult({
        message: 'Coloreados por uso.', intent: 'apply_symbology', geojson: fc(6), layerName: 'otro nombre',
        target_layer_id: lotes.id,
        symbology: { symbology_type: 'unique_values', classification_field: 'uso', classification_method: 'unique_values',
          class_breaks: [{ label: 'res', color: '#e41a1c' }, { label: 'com', color: '#377eb8' }, { label: 'ind', color: '#4daf4a' }] },
      })
    })
    await page.goto('/')
    await waitForMap(page)
    await waitForStyleLoaded(page)
    await sendQuery(page, 'los lotes')
    await waitForFeatureCount(page, 6)
    await sendQuery(page, 'el ndvi')
    const raster = await waitForLayerKind(page, 'raster-xyz')
    // El usuario sube el NDVI encima de los lotes.
    await page.locator('[title="Capas"]').click()
    await page.getByRole('button', { name: /Subir NDVI/ }).click()
    const tipos = async () => (await layerOrder(page)).map((l) => l.kind)
    await expect.poll(tipos).toEqual(['vector-geojson', 'raster-xyz'])

    await sendQuery(page, 'colorea los lotes por uso')
    await expect.poll(() => rendererKind(page)).toBe('unique_values')
    // Una sola capa vectorial, con su nombre, y el NDVI sigue encima.
    await expect.poll(tipos).toEqual(['vector-geojson', 'raster-xyz'])
    expect((await layerOrder(page)).find((l) => l.kind === 'raster-xyz')!.id).toBe(raster)
    await expect(page.getByTestId('layer-row').filter({ hasText: 'Lotes' })).toHaveCount(1)
    await expect(page.getByTestId('layer-row').filter({ hasText: 'otro nombre' })).toHaveCount(0)
  })

  test('una orden set_style sin datos re-estila la capa existente sin añadir otra', async ({ page }) => {
    let turno = 0
    await mockQuery(page, () => {
      turno += 1
      return turno === 1
        ? queryResult({ message: 'Lotes.', geojson: fc(6), layerName: 'Lotes' })
        : queryResult({ message: 'Hecho.', intent: 'apply_symbology', symbology: {
            symbology_type: 'graduated_colors', classification_field: 'valor', classification_method: 'quantile',
            class_breaks: [{ min_value: 0, max_value: 20, color: '#ffeeee', label: '0-20' },
                           { min_value: 20, max_value: 50, color: '#cc0000', label: '20-50' }] } })
    })
    await page.goto('/')
    await waitForMap(page)
    await waitForStyleLoaded(page)
    await sendQuery(page, 'los lotes')
    await waitForFeatureCount(page, 6)
    const antes = await vectoriales(page)
    await sendQuery(page, 'gradúalos por valor')
    await expect.poll(() => rendererKind(page)).toBe('graduated_colors')
    expect((await vectoriales(page)).map((l) => l.id)).toEqual(antes.map((l) => l.id))
  })

  test('varios artefactos de un turno se ven juntos y en su orden: estadísticas, gráfico y tabla recortada', async ({ page }) => {
    const filas = [{ uso: 'res', n: 30 }, { uso: 'com', n: 12 }]
    await mockQuery(page, queryResult({
      message: 'Resumen de lotes.',
      artifacts: [
        { kind: 'stats', title: 'Resumen', items: [{ label: 'Lotes', value: 41033, unit: null }, { label: 'Área media', value: 212.5, unit: 'm²' }] },
        { kind: 'chart', data: filas, spec: { chart_type: 'bar', x_key: 'uso', y_key: 'n', title: 'Lotes por uso' } },
        { kind: 'table', title: 'Lotes por uso', columns: ['uso', 'n'], preview: filas, rows_ref: null, total_rows: 41033 },
      ] as never,
    }))
    await page.goto('/')
    await waitForMap(page)
    await sendQuery(page, 'resume los lotes')
    const panel = page.getByTestId('panel-resultados')
    await expect(panel.getByTestId('artefacto-stats')).toContainText('Área media')
    await expect(panel.getByTestId('artefacto-stats')).toContainText('m²')
    await expect(panel.getByTestId('artefacto-chart')).toContainText('Lotes por uso')
    // La tabla dice que es un recorte, no finge ser todo.
    await expect(panel.getByTestId('artefacto-table')).toContainText(/2 de 41\.033 filas/)
    const orden = await panel.locator('section[data-testid^="artefacto-"]')
      .evaluateAll((els) => els.map((e) => e.getAttribute('data-testid')))
    expect(orden).toEqual(['artefacto-stats', 'artefacto-chart', 'artefacto-table'])
  })

  test('una respuesta fuera de contrato se rechaza con el motivo y no toca el mapa', async ({ page }) => {
    let turno = 0
    await mockQuery(page, () => {
      turno += 1
      if (turno === 1) return queryResult({ message: 'Lotes.', geojson: fc(3) })
      const r = queryResult({ message: 'x' }) as any
      r.artifacts = [{ kind: 'layer', layer: { id: 'sin-storage' } }]
      return r
    })
    await page.goto('/')
    await waitForMap(page)
    await waitForStyleLoaded(page)
    await sendQuery(page, 'los lotes')
    await waitForFeatureCount(page, 3)
    const antes = await layerOrder(page)
    await sendQuery(page, 'otra cosa')
    await expect(page.getByText(/^Error:/).last()).toBeVisible()
    expect(await layerOrder(page)).toEqual(antes)
  })
})
