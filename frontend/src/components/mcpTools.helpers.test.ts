import { describe, expect, it } from 'vitest'

import type { McpToolInfo } from '@/services/api'
import { buildArguments, factRows, formFields, PUNTO, VIEWPORT } from './mcpTools.helpers'

const tool = (props: McpToolInfo['input_schema']['properties'], required: string[] = [],
  geo: Record<string, { accepts?: string[] }> = {}): McpToolInfo => ({
  server: 's', tool: 't', herramienta: 's__t', description: '', riesgo: 'read', estado: 'disponible',
  input_schema: { properties: props, required }, geo: { inputs: geo },
})

describe('formFields: el formulario sale del esquema, sin conocer la tool', () => {
  it('deduce el control de cada tipo (incluido el opcional anyOf [X, null])', () => {
    const f = formFields(tool({
      combo: { type: 'string', enum: ['a', 'b'], default: 'a' },
      date_from: { anyOf: [{ type: 'string' }, { type: 'null' }], default: null },
      max_cloud_pct: { anyOf: [{ type: 'number' }, { type: 'null' }] },
      limit: { type: 'integer' },
      ok: { type: 'boolean' },
      filtro: { type: 'object' },
      nombre: { type: 'string', title: 'Nombre' },
      aoi: { type: 'object' },
    }, ['aoi'], { aoi: { accepts: ['geometry'] } }))
    expect(f.map((x) => [x.name, x.kind])).toEqual([
      ['aoi', 'geo'], ['combo', 'enum'], ['date_from', 'date'], ['max_cloud_pct', 'number'],
      ['limit', 'integer'], ['ok', 'boolean'], ['filtro', 'json'], ['nombre', 'text'],
    ])
    expect(f[0].required).toBe(true)
    expect(f.find((x) => x.name === 'nombre')?.label).toBe('Nombre')
  })
})

describe('buildArguments', () => {
  const campos = formFields(tool({
    aoi: { type: 'object' }, zona: { type: 'array' },
    n: { type: 'integer' }, x: { type: 'number' }, meta: { type: 'object' }, texto: { type: 'string' },
  }, ['aoi', 'texto'], { aoi: { accepts: ['geometry'] }, zona: { accepts: ['bbox'] } }))
  const capa = { type: 'FeatureCollection' as const, features: [
    { type: 'Feature', geometry: { type: 'Point', coordinates: [-74.1, 4.6] } },
    { type: 'Feature', geometry: { type: 'Point', coordinates: [-74.0, 4.7] } },
  ] }
  const capas = [
    { id: 'l1', name: 'Lotes', datasetId: 'ds_0123456789abcdef', data: capa },
    { id: 'l2', name: 'Externa', data: capa },
    { id: 'l3', name: 'Teselada', data: { type: 'FeatureCollection' as const, features: [] } },
  ]

  it('geo: viewport tal cual; workspace por ds_; capa del navegador inline o como bbox', () => {
    const r = buildArguments(campos, { aoi: VIEWPORT, zona: 'l2', texto: 'hola', n: '3', x: '0.5', meta: '{"a":1}' }, capas)
    expect(r).toEqual({ ok: true, args: { aoi: 'viewport', zona: [-74.1, 4.6, -74.0, 4.7], texto: 'hola', n: 3, x: 0.5, meta: { a: 1 } } })
    const w = buildArguments(campos, { aoi: 'l1', texto: 'x' }, capas)
    expect(w.ok && w.args.aoi).toBe('ds_0123456789abcdef')
    const inline = buildArguments(campos, { aoi: 'l2', texto: 'x' }, capas)
    expect(inline.ok && inline.args.aoi).toBe(capa)
  })

  it('el punto marcado viaja como referencia `punto` (el núcleo pone la geometría)', () => {
    const r = buildArguments(campos, { aoi: PUNTO, texto: 'x' }, capas)
    expect(r.ok && r.args.aoi).toBe('punto')
  })

  it('un geo requerido sin elegir usa la zona visible; uno opcional se omite', () => {
    const r = buildArguments(campos, { texto: 'x' }, capas)
    expect(r.ok && r.args).toEqual({ aoi: 'viewport', texto: 'x' })
  })

  it('errores legibles antes de llamar al servidor', () => {
    expect(buildArguments(campos, { aoi: 'l3', texto: 'x' }, capas)).toMatchObject({ ok: false, error: expect.stringContaining('Teselada') })
    expect(buildArguments(campos, { aoi: 'l9', texto: 'x' }, capas)).toMatchObject({ ok: false })
    expect(buildArguments(campos, {}, capas)).toMatchObject({ ok: false, error: 'Falta «texto».' })
    expect(buildArguments(campos, { texto: 'x', n: '2.5' }, capas)).toMatchObject({ ok: false, error: expect.stringContaining('entero') })
    expect(buildArguments(campos, { texto: 'x', meta: '{' }, capas)).toMatchObject({ ok: false, error: expect.stringContaining('JSON') })
  })
})

describe('factRows', () => {
  it('aplana dos niveles y resume listas de objetos', () => {
    expect(factRows({ scene: { id: 'S2', cloud_pct: 3.14159 }, alternatives: [{ d: 1 }, { d: 2 }], combo: 'swir', nada: null }))
      .toEqual([['scene.id', 'S2'], ['scene.cloud_pct', '3.142'], ['alternatives', '2 elementos'], ['combo', 'swir']])
  })
})

describe('formFields: geo numérico', () => {
  it('lon/lat marcados como geo siguen siendo números', () => {
    const f = formFields(tool({ lon: { type: 'number' }, aoi: { type: 'object' } }, [],
      { lon: { accepts: ['geometry'] }, aoi: { accepts: ['geometry'] } }))
    expect(f.map((x) => [x.name, x.kind])).toEqual([['aoi', 'geo'], ['lon', 'number']])
  })

})
