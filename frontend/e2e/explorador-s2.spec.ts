import { test, expect, type Page } from '@playwright/test'
import { json, mockInit, waitForFeatureCount, waitForLayerKind, waitForMap, waitForStyleLoaded } from './helpers'

/**
 * Explorador Sentinel-2: el panel usa las MISMAS tools que el agente (imagery-mcp).
 * Al abrir, el mundo (`imagery_catalog_world`) → una tesela → sus escenas con miniatura → ver
 * una escena ENTERA en el producto elegido (`imagery_scene_view`) → su contraste (histograma),
 * un píxel y sus descargas. «Detallar la vista» usa `imagery_catalog_grid`.
 */

const PNG_1x1 = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M8AAAMBAQDJ/pLvAAAAAElFTkSuQmCC',
  'base64',
)

const poligono = (w: number, s: number, e: number, n: number) => ({
  type: 'Polygon', coordinates: [[[w, s], [e, s], [e, n], [w, n], [w, s]]],
})

const TESELAS = [
  { type: 'Feature', geometry: poligono(-74.5, 3.6, -73.5, 4.5), properties: { tile: '18NWK', escenas: 25, nubes_min: 5, nubes_mediana: 82, cobertura_max: 100 } },
  { type: 'Feature', geometry: poligono(-74.5, 4.5, -73.5, 5.4), properties: { tile: '18NWL', escenas: 25, nubes_min: 1, nubes_mediana: 69, cobertura_max: 100 } },
]

const MUNDO = {
  success: true, message: null,
  facts: { teselas: 2, mas_despejadas: [{ tile: '18NWL', nubes_min: 1, escenas: 25 }, { tile: '18NWK', nubes_min: 5, escenas: 25 }] },
  results: { geojson: { type: 'FeatureCollection', features: TESELAS }, layer_ref: { id: 'ds_s2mundo0000000aa', name: 'Mundo', feature_count: 2 } },
}

const GRID = {
  success: true, message: null, facts: { teselas: 2 },
  results: {
    geojson: { type: 'FeatureCollection', features: TESELAS.map((f) => ({ ...f, properties: { ...f.properties, mejor_escena: `S2B_T${f.properties.tile}_20260810T152745_L2A`, mejor_fecha: '2026-08-10' } })) },
    layer_ref: { id: 'ds_s2grid00000000aa', name: 'Vista', feature_count: 2 },
  },
}

const ESCENAS = {
  success: true, message: null, facts: { escenas: 2 },
  results: {
    data: { results: [
      { id: 'S2B_T18NWL_20260810T152745_L2A', tile: '18NWL', fecha: '2026-08-10T15:31:43Z', nubes: 1.31, cobertura: 100, plataforma: 'sentinel-2b', miniatura: 'https://imagenes.test/a/L2A_PVI.jpg' },
      { id: 'S2C_T18NWL_20260914T152651_L2A', tile: '18NWL', fecha: '2026-09-14T15:31:43Z', nubes: 42.02, cobertura: 100, plataforma: 'sentinel-2c', miniatura: 'https://imagenes.test/b/L2A_PVI.jpg' },
    ] },
    visualization: { type: 'table' },
  },
}

const vista = (product: string, sid = 'S2B_T18NWL_20260810T152745_L2A') => ({
  success: true, message: null,
  facts: {
    scene: { id: sid, datetime: '2026-08-10T15:31:43Z', bbox: [-75.0, 4.43, -74.0, 5.43] },
    producto: { id: product },
    descargas: [
      { banda: 'red', codigo: 'B04', nombre: 'Rojo 665 nm', resolucion_m: 10, url: 'https://datos.test/B04.tif' },
      { banda: 'scl', codigo: 'SCL', nombre: 'Clasificación de la escena', resolucion_m: 20, url: 'https://datos.test/SCL.tif' },
    ],
  },
  results: {
    visualization: { type: 'imagery' },
    external_imagery: {
      service_url: `/api/v1/proxy/mcp/imagery/tiles-band/${sid}/${product}/{z}/{x}/{y}.png`,
      name: `${product} 2026-08-10`, extent: { xmin: -75.0, ymin: 4.43, xmax: -74.0, ymax: 5.43 },
      legend: product === 'ndwi' ? { type: 'ramp', field: 'NDWI', min: -1, max: 1, colores: ['#67001f', '#f7f7f7', '#053061'] } : null,
    },
  },
})

type Enviados = Record<string, { arguments: Record<string, unknown> }[]>

async function rutas(page: Page): Promise<Enviados> {
  const enviados: Enviados = {}
  const tool = (nombre: string, cuerpo: (args: Record<string, unknown>) => unknown) =>
    page.route(`**/api/v1/connections/imagery/tools/${nombre}/run`, (r) => {
      const body = JSON.parse(r.request().postData() ?? '{}')
      ;(enviados[nombre] ??= []).push(body)
      return json(r, cuerpo(body.arguments ?? {}))
    })
  await tool('imagery_catalog_world', () => MUNDO)
  await tool('imagery_catalog_grid', () => GRID)
  await tool('imagery_catalog_scenes', () => ESCENAS)
  await tool('imagery_scene_view', (a) => vista(String(a.product), String(a.scene_id)))
  await tool('imagery_band_histogram', () => ({
    success: true, message: null, results: {},
    facts: { bandas: [{ banda: 'nir', p2: 0.1, p98: 0.45, min: 0, max: 0.6, bordes: [0, 0.2, 0.4, 0.6], conteos: [5, 20, 3], unidad: 'reflectancia' }] },
  }))
  await tool('imagery_pixel', () => ({
    success: true, message: null, results: {},
    facts: {
      bandas: { red: { codigo: 'B04', nombre: 'Rojo', valor: 0.05, unidad: 'reflectancia' },
                scl: { codigo: 'SCL', nombre: 'Clasificación', valor: 4, clase: 'Vegetación' } },
      indices: { ndvi: { nombre: 'NDVI', valor: 0.71, lectura: 'vigor' } },
    },
  }))
  return enviados
}

const ultimo = (e: Enviados, tool: string) => e[tool]?.[e[tool].length - 1]?.arguments ?? {}

async function abrir(page: Page) {
  await page.goto('/')
  await waitForMap(page)
  await waitForStyleLoaded(page)
  await page.locator('[title="Sentinel-2"]').click()
  const panel = page.locator('[data-testid="explorador-s2"]')
  await expect(panel).toBeVisible()
  return panel
}

test.describe('Explorador Sentinel-2', () => {
  test.beforeEach(async ({ page }) => {
    await mockInit(page)
    await page.route('https://imagenes.test/**', (r) => r.fulfill({ status: 200, contentType: 'image/png', body: PNG_1x1 }))
    await page.route('**/api/v1/proxy/mcp/**', (r) => r.fulfill({ status: 200, contentType: 'image/png', body: PNG_1x1 }))
  })

  test('el mundo al abrir → una tesela → ver la escena entera en NDWI, con contraste, píxel y descargas', async ({ page }) => {
    const enviados = await rutas(page)
    const panel = await abrir(page)

    // El mundo se pide solo, una vez, con la ventana y los filtros del panel.
    const teselas = panel.getByTestId('s2-teselas')
    await expect(teselas).toContainText('Las más despejadas')
    await expect(teselas.locator('.imgp-scene').first()).toContainText('18NWL')
    await waitForFeatureCount(page, 2)
    expect(enviados.imagery_catalog_world).toHaveLength(1)
    expect(ultimo(enviados, 'imagery_catalog_world')).toMatchObject({ min_coverage_pct: 10 })

    await teselas.locator('.imgp-scene').first().click()
    const escenas = panel.getByTestId('s2-escenas')
    await expect(escenas.locator('.s2-escena')).toHaveCount(2)
    expect(ultimo(enviados, 'imagery_catalog_scenes')).toMatchObject({ tile: '18NWL', order: 'menos_nubes' })

    // Ver como NDWI: la escena ENTERA (sin AOI), con la leyenda que declara el servicio.
    await escenas.getByLabel('Ver como').selectOption('ndwi')
    await escenas.getByTestId('s2-ver').first().click()
    await waitForLayerKind(page, 'raster-xyz')
    expect(ultimo(enviados, 'imagery_scene_view')).toEqual({ scene_id: 'S2B_T18NWL_20260810T152745_L2A', product: 'ndwi' })
    const tarjeta = panel.getByTestId('s2-vista')
    await expect(tarjeta).toContainText('NDWI')
    await expect(page.locator('.map-legend')).toContainText('NDWI')

    // Contraste de un índice: un canal de -1 a 1; aplicar pide el mismo producto con `rescale`.
    await tarjeta.getByText('Contraste').click()
    await expect(tarjeta.getByTestId('s2-canal')).toHaveCount(1)
    await tarjeta.getByLabel(/Mínimo/).fill('-0.2')
    await tarjeta.getByTestId('s2-aplicar').click()
    await expect.poll(() => ultimo(enviados, 'imagery_scene_view').rescale).toEqual([-0.2, 1])

    // Píxel: con el apartado abierto, un clic en el mapa lee ese punto de la escena.
    await tarjeta.getByText('Inspeccionar píxel').click()
    const lienzo = (await page.locator('.maplibregl-canvas').boundingBox())!
    await page.mouse.click(lienzo.x + lienzo.width * 0.7, lienzo.y + lienzo.height * 0.3)
    await expect(tarjeta.getByTestId('s2-pixel')).toContainText('Vegetación')
    const punto = ultimo(enviados, 'imagery_pixel') as { scene_id: string; point_geojson: { type: string } }
    expect(punto.scene_id).toBe('S2B_T18NWL_20260810T152745_L2A')
    expect(punto.point_geojson.type).toBe('Point')

    // Descargas: un enlace por banda, al COG.
    await tarjeta.getByText(/Descargar bandas/).click()
    await expect(tarjeta.getByTestId('s2-descargas').locator('a').first()).toHaveAttribute('href', 'https://datos.test/B04.tif')
  })

  test('color con contraste por canal (histograma) y detallar la vista con fechas exactas', async ({ page }) => {
    const enviados = await rutas(page)
    const panel = await abrir(page)
    await panel.getByTestId('s2-teselas').locator('.imgp-scene').first().click()
    const escenas = panel.getByTestId('s2-escenas')
    await escenas.getByLabel('Ver como').selectOption('false_color')
    await escenas.getByTestId('s2-ver').first().click()
    await waitForLayerKind(page, 'raster-xyz')

    const tarjeta = panel.getByTestId('s2-vista')
    await tarjeta.getByText('Contraste').click()
    await expect(tarjeta.getByTestId('s2-canal')).toHaveCount(3)
    await expect.poll(() => ultimo(enviados, 'imagery_band_histogram').bands).toEqual(['nir', 'red', 'green'])
    await expect(tarjeta.locator('.s2-hist')).toHaveCount(1)              // el mock trae una banda
    await tarjeta.getByRole('button', { name: 'Auto (p2–p98)' }).click()
    await tarjeta.getByTestId('s2-aplicar').click()
    await expect.poll(() => ultimo(enviados, 'imagery_scene_view').stretch).toEqual([[0.1, 0.45], [0, 0.4], [0, 0.4]])

    await escenas.getByRole('button', { name: '← teselas' }).click()
    await panel.getByTestId('s2-buscar').click()
    await expect(panel.getByTestId('s2-teselas')).toContainText('2 teselas')
    await expect(panel.getByTestId('s2-teselas')).toContainText('2026-08-10')   // la mejor escena: solo con fechas exactas
    expect(ultimo(enviados, 'imagery_catalog_grid')).toMatchObject({ aoi_geojson: 'viewport' })
  })

  test('el cajón recuerda su estado: la cuadrícula y la escena nuevas sustituyen, el relleno no tiñe la escena', async ({ page }) => {
    await rutas(page)
    const estado = () => page.evaluate(() => {
      const w = window as unknown as { __mapTestState?: { layers?: { id: string; kind?: string }[] }; __mlmap?: { getLayer: (id: string) => unknown; getPaintProperty: (id: string, p: string) => unknown; getFilter: (id: string) => unknown } }
      const capas = w.__mapTestState?.layers ?? []
      const grid = capas.find((l) => w.__mlmap?.getLayer(`${l.id}-fill`))
      return {
        relleno: grid ? w.__mlmap?.getPaintProperty(`${grid.id}-fill`, 'fill-opacity') : null,
        // El filtro de la capa de resaltado: ['boolean', false] = nada resaltado.
        resaltado: grid ? JSON.stringify(w.__mlmap?.getFilter(`${grid.id}-sel-fill`)) !== '["boolean",false]' : null,
        rasters: capas.filter((l) => l.kind === 'raster-xyz').length,
        cuadriculas: capas.filter((l) => w.__mlmap?.getLayer(`${l.id}-fill`)).length,
      }
    })

    const panel = await abrir(page)
    await waitForFeatureCount(page, 2)
    await panel.getByTestId('s2-teselas').locator('.imgp-scene').first().click()
    await expect.poll(async () => (await estado()).resaltado).toBe(true)     // la tesela abierta, resaltada
    const escenas = panel.getByTestId('s2-escenas')
    await escenas.getByTestId('s2-ver').first().click()
    await waitForLayerKind(page, 'raster-xyz')
    await expect.poll(estado).toMatchObject({ relleno: 0, rasters: 1, resaltado: false })

    await escenas.getByTestId('s2-ver').nth(1).click()
    await expect(escenas.locator('.s2-escena.activa')).toContainText('14 de sept')
    await expect.poll(async () => (await estado()).rasters).toBe(1)      // sustituye, no apila

    await escenas.getByRole('button', { name: '← teselas' }).click()
    await expect.poll(async () => (await estado()).relleno).toBe(0.45)

    // Cambiar de cajón y volver: la búsqueda sigue ahí y el mundo no se vuelve a pedir.
    await page.locator('[title="Capas"]').click()
    await page.locator('[title="Sentinel-2"]').click()
    await expect(panel.getByTestId('s2-teselas')).toContainText('Las más despejadas')

    // Pedir el mundo otra vez sustituye la cuadrícula (no quedan dos).
    await panel.getByTestId('s2-mundo').click()
    await expect.poll(async () => (await estado()).cuadriculas).toBe(1)

    // Recargar la pestaña: el cajón sigue sabiendo qué buscó (y qué capas son suyas).
    await page.reload()
    await waitForMap(page)
    await page.locator('[title="Sentinel-2"]').click()
    await expect(panel.getByTestId('s2-teselas')).toContainText('Las más despejadas')
  })

  test('un clic sobre la cuadrícula del mapa abre esa tesela', async ({ page }) => {
    const enviados = await rutas(page)
    const panel = await abrir(page)
    await waitForFeatureCount(page, 2)
    // Clic en el centro de la tesela 18NWL (su píxel, con la cámara ya quieta: la capa nueva encuadra).
    await page.waitForFunction(() => !(window as unknown as { __mlmap: { isMoving: () => boolean } }).__mlmap.isMoving())
    await page.waitForTimeout(300)
    const lienzo = (await page.locator('.maplibregl-canvas').boundingBox())!
    const p = await page.evaluate(() => (window as unknown as { __mlmap: { project: (c: [number, number]) => { x: number; y: number } } })
      .__mlmap.project([-74.0, 4.95]))
    await page.mouse.click(lienzo.x + p.x, lienzo.y + p.y)
    await expect(panel.getByTestId('s2-escenas')).toContainText('Tesela 18NWL')
    expect(ultimo(enviados, 'imagery_catalog_scenes')).toMatchObject({ tile: '18NWL' })
  })

  test('globo o plano', async ({ page }) => {
    await rutas(page)
    const panel = await abrir(page)
    const proyeccion = () => page.evaluate(() =>
      (window as unknown as { __mlmap?: { getProjection: () => { type?: string } | undefined } }).__mlmap?.getProjection()?.type ?? 'mercator')
    await panel.getByTestId('s2-globo').click()
    await expect.poll(proyeccion).toBe('globe')
    await panel.getByTestId('s2-globo').click()
    await expect.poll(proyeccion).toBe('mercator')
  })

  test('el error del servicio se dice tal cual', async ({ page }) => {
    await page.route('**/api/v1/connections/imagery/tools/imagery_catalog_world/run', (r) => json(r, {
      success: false, message: 'no hay agregados publicados para la ventana', facts: {}, results: {},
    }))
    const panel = await abrir(page)
    await expect(panel.getByRole('alert')).toContainText('no hay agregados publicados')
    await expect(panel.getByTestId('s2-teselas')).toHaveCount(0)
  })
})
