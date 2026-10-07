import { beforeEach, describe, expect, it } from 'vitest'

import { useTraza } from './trazaStore'

describe('trazaStore', () => {
  beforeEach(() => useTraza.getState().reiniciar())

  it('un paso entra en curso y el mismo id lo actualiza (no se duplica)', () => {
    const t = useTraza.getState()
    t.aplicar({ id: 'a', tipo: 'herramienta', estado: 'en_curso', titulo: 'x', argumentos: { k: 'v' } })
    t.aplicar({ id: 'b', tipo: 'pensar', estado: 'en_curso', titulo: 'y' })
    t.aplicar({ id: 'a', tipo: 'herramienta', estado: 'ok', titulo: 'x', detalle: 'hecho', ms: 1200 })
    const pasos = useTraza.getState().pasos
    expect(pasos.map((p) => [p.id, p.estado])).toEqual([['a', 'ok'], ['b', 'en_curso']])
    expect(pasos[0]).toMatchObject({ detalle: 'hecho', ms: 1200, argumentos: { k: 'v' } })
  })

  it('ignora lo que no es un paso y reinicia por turno', () => {
    useTraza.getState().aplicar({} as never)
    expect(useTraza.getState().pasos).toEqual([])
    useTraza.getState().aplicar({ id: 'a', tipo: 'pensar', estado: 'ok', titulo: 't' })
    useTraza.getState().reiniciar()
    expect(useTraza.getState().pasos).toEqual([])
  })
})
