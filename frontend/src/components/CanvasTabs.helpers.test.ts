/**
 * S4.3 — lógica del panel de resultados sobre artefactos del contrato.
 */
import { describe, it, expect } from 'vitest'
import type { Artifact } from '@/contracts'
import type { Turno } from '@/stores/resultsStore'
import {
  cuentaDePanel,
  etiquetaDeTurno,
  filasDeTabla,
  pestanaPara,
  tablaDeCapa,
  visualizacionDeGrafico,
} from './CanvasTabs.helpers'

const capa = (inline: unknown = null): Artifact => ({
  kind: 'layer',
  layer: {
    id: 'ds_1', kind: 'vector', name: 'Lotes', crs: 'EPSG:4326', bbox: null,
    feature_count: 2, geometry_type: 'Polygon', provenance: null, style: null,
    storage: { kind: 'postgis', schema: 'workspace', table: 'ds_1' },
  },
  inline, tiles: null, replaces: null,
} as unknown as Artifact)
const tabla: Artifact = { kind: 'table', title: null, columns: ['a'], preview: [{ a: 1 }, { a: 2 }], rows_ref: 'ds_1', total_rows: 50 }
const grafico: Artifact = { kind: 'chart', spec: { chart_type: 'bar', x_key: 'a', y_key: ['b', 'c'], title: null }, data: [{ a: 'x', b: 1 }] }
const stats: Artifact = { kind: 'stats', title: null, items: [{ label: 'Media', value: 3.2, unit: null }] }

const turno = (artefactos: Artifact[], consulta = 'q'): Turno =>
  ({ id: 't', consulta, en: new Date(2026, 8, 25, 14, 5), artefactos, sql: null, mensaje: '' })

describe('pestanaPara', () => {
  it('turno analítico (capa + tabla + gráfico) → resultados: el panel se abre sin tapar el mapa', () => {
    expect(pestanaPara(turno([capa(), tabla, grafico]))).toBe('results')
  })
  it('solo capas → mapa', () => {
    expect(pestanaPara(turno([capa()]))).toBe('map')
  })
  it('sin turno o sin nada que mostrar → no cambia', () => {
    expect(pestanaPara(null)).toBeNull()
    expect(pestanaPara(turno([{ kind: 'services', items: [] } as unknown as Artifact]))).toBeNull()
  })
})

describe('artefactos → vistas', () => {
  it('tabla del contrato → filas de GeoDataTable', () => {
    expect(filasDeTabla(tabla as Extract<Artifact, { kind: 'table' }>)).toEqual([
      { properties: { a: 1 } }, { properties: { a: 2 } },
    ])
  })
  it('gráfico: y_key múltiple usa la primera serie', () => {
    const v = visualizacionDeGrafico(grafico as Extract<Artifact, { kind: 'chart' }>)
    expect(v.config).toEqual({ chart_type: 'bar', x_key: 'a', y_key: 'b' })
    expect(v.data).toEqual([{ a: 'x', b: 1 }])
  })
  it('sin tabla, la capa inline es la tabla del turno; con tabla, no se duplica', () => {
    const fc = { type: 'FeatureCollection', features: [{ properties: { x: 1 } }] }
    expect(tablaDeCapa(turno([capa(fc)]))).toHaveLength(1)
    expect(tablaDeCapa(turno([capa(fc), tabla]))).toBeNull()
    expect(tablaDeCapa(turno([capa()]))).toBeNull()
  })
  it('badge = artefactos de panel (+ tabla derivada de capa)', () => {
    expect(cuentaDePanel(turno([capa(), tabla, grafico, stats]))).toBe(3)
    expect(cuentaDePanel(turno([capa({ features: [{ properties: {} }] })]))).toBe(1)
    expect(cuentaDePanel(null)).toBe(0)
  })
  it('etiqueta del historial: hora + consulta recortada', () => {
    const e = etiquetaDeTurno(turno([], 'x'.repeat(80)))
    expect(e).toMatch(/·/)
    expect(e.endsWith('…')).toBe(true)
  })
})
