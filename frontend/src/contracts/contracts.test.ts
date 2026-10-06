/**
 * Test de contrato (F4, S4.1): backend y frontend no pueden divergir sin que falle.
 *
 * - Los tipos y schemas del frontend son exactamente los que genera el backend
 *   (`contracts/schema/`, exportados de los modelos pydantic).
 * - Una respuesta REAL de /query, construida por el backend con el mismo código
 *   que la API (`contracts/examples/query_response.json`), pasa el validador.
 * - Lo que no cumple el contrato se rechaza con el motivo.
 */
import fs from 'node:fs'
import path from 'node:path'
import { describe, expect, it } from 'vitest'

// @ts-expect-error — módulo JS del script de generación (sin tipos)
import { generar } from '../../scripts/gen-contracts.mjs'
import { RespuestaFueraDeContrato, validarRespuesta } from './index'
import { capa, capaRaster, respuesta } from './fixtures'

const RAIZ = path.resolve(__dirname, '..', '..', '..')
const ejemplo = () =>
  JSON.parse(fs.readFileSync(path.join(RAIZ, 'contracts', 'examples', 'query_response.json'), 'utf8'))

describe('contrato backend → frontend', () => {
  it('los tipos y schemas del frontend están al día con los del backend', async () => {
    const archivos: Record<string, string> = await generar()
    for (const [rel, contenido] of Object.entries(archivos)) {
      const actual = fs.readFileSync(path.join(__dirname, rel), 'utf8').replace(/\r\n/g, '\n')
      expect(actual, `${rel} desactualizado → npm run gen:contracts`).toBe(contenido)
    }
  })

  it('una respuesta real del backend pasa el validador', () => {
    const r = validarRespuesta(ejemplo())
    expect(r.artifacts.map((a) => a.kind)).toEqual(['layer', 'layer', 'chart', 'table', 'services'])
    const capa = r.artifacts[0]
    if (capa.kind !== 'layer') throw new Error('se esperaba una capa')
    expect(capa.layer.storage.kind).toBe('workspace-table')
    expect(capa.layer.style?.symbology_type).toBe('unique_values')
  })

  it('la API ya no manda los campos legados: si vuelven, el contrato los rechaza', () => {
    expect(() => validarRespuesta({ ...ejemplo(), results: { sql: 'x' } })).toThrow(RespuestaFueraDeContrato)
  })

  it.each([
    ['un tipo de artefacto desconocido', (e: any) => e.artifacts.push({ kind: 'holograma' })],
    ['un campo que el contrato no conoce', (e: any) => (e.artifacts[2].color = 'rojo')],
    ['falta un campo obligatorio', (e: any) => delete e.query_id],
    ['un estado inventado', (e: any) => (e.status = 'casi')],
    ['un raster sin plantilla XYZ válida', (e: any) => (e.artifacts[1].layer.storage.kind = 'wms')],
  ])('rechaza %s con el motivo', (_desc, estropear) => {
    const e = ejemplo()
    estropear(e)
    expect(() => validarRespuesta(e)).toThrow(RespuestaFueraDeContrato)
  })
})

describe('fixtures de test', () => {
  const valida = (r: unknown) => {
    try {
      return validarRespuesta(r)
    } catch (e) {
      throw new Error((e as RespuestaFueraDeContrato).problemas?.join('\n') ?? String(e), { cause: e })
    }
  }
  it('las respuestas que usan los tests pasan la misma frontera que runQuery', () => {
    valida(respuesta())
    valida(respuesta({
      artifacts: [
        capa({ inline: { type: 'FeatureCollection', features: [] }, style: { fill: { color: '#f00' } }, replaces: 'x' }),
        capa({ tiles: { url_template: '/t/{z}/{x}/{y}.pbf', source_layer: 'dataset', fields: [] }, bbox: [0, 0, 1, 1] }),
        capaRaster(),
        capaRaster({ arcgis: 'https://x.example/rest/services/Orto/ImageServer' }),
      ],
    }))
  })
})

describe('lo que devuelve la frontera', () => {
  it('es lo validado: un campo con default que no vino llega completado', () => {
    const r = validarRespuesta(respuesta({
      artifacts: [{ kind: 'table', columns: ['a'], preview: [], rows_ref: null, total_rows: 0 } as never],
    }))
    const t = r.artifacts[0]
    expect(t.kind === 'table' && t.title).toBeNull()
  })
})
