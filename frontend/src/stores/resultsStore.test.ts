import { beforeEach, describe, expect, it } from 'vitest'
import { MAX_TURNOS, esDePanel, useResultsStore, type Turno } from './resultsStore'

const TABLA = { kind: 'table', title: null, columns: [], preview: [], rows_ref: null, total_rows: 0 } as never
const t = (id: string, artefactos = [TABLA]): Turno => ({ id, consulta: id, en: new Date(), artefactos, sql: null, mensaje: '' })

describe('resultsStore (S4.3)', () => {
  beforeEach(() => useResultsStore.getState().limpiar())

  it('registrar deja el turno nuevo como activo', () => {
    const s = useResultsStore.getState()
    s.registrar(t('a'))
    s.registrar(t('b'))
    expect(useResultsStore.getState().activo).toBe('b')
    expect(useResultsStore.getState().turnos.map((x) => x.id)).toEqual(['a', 'b'])
  })

  it('ver vuelve a un turno anterior (E4.3) y no inventa ids', () => {
    const s = useResultsStore.getState()
    ;['a', 'b', 'c', 'd'].forEach((id) => s.registrar(t(id)))
    s.ver('a')
    expect(useResultsStore.getState().activo).toBe('a')
    s.ver('nope')
    expect(useResultsStore.getState().activo).toBe('a')
  })

  it('re-registrar el mismo id no lo duplica', () => {
    const s = useResultsStore.getState()
    s.registrar(t('a'))
    s.registrar(t('a'))
    expect(useResultsStore.getState().turnos).toHaveLength(1)
  })

  it(`guarda como mucho ${MAX_TURNOS} turnos, descartando los más viejos`, () => {
    const s = useResultsStore.getState()
    for (let i = 0; i < MAX_TURNOS + 5; i++) s.registrar(t(`q${i}`))
    const ids = useResultsStore.getState().turnos.map((x) => x.id)
    expect(ids).toHaveLength(MAX_TURNOS)
    expect(ids[0]).toBe('q5')
  })

  it('las capas, órdenes de mapa y servicios no van al panel', () => {
    expect(esDePanel({ kind: 'table' } as never)).toBe(true)
    expect(esDePanel({ kind: 'report' } as never)).toBe(true)
    expect(esDePanel({ kind: 'layer' } as never)).toBe(false)
    expect(esDePanel({ kind: 'map_command' } as never)).toBe(false)
    expect(esDePanel({ kind: 'services' } as never)).toBe(false)
  })

  it('una respuesta solo de texto no entra al historial ni tapa el resultado anterior', () => {
    const s = useResultsStore.getState()
    s.registrar(t('grafico'))
    s.registrar(t('texto', []))
    s.registrar(t('servicios', [{ kind: 'services', items: [] } as never]))
    expect(useResultsStore.getState().activo).toBe('grafico')
    expect(useResultsStore.getState().turnos.map((x) => x.id)).toEqual(['grafico'])
  })
})
