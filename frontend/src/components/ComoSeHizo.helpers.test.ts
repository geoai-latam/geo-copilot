import { describe, it, expect } from 'vitest'

import { argumentos, nombreCapacidad } from './ComoSeHizo.helpers'

describe('FH.7 — «cómo se hizo»', () => {
  it('nombra la capacidad: núcleo, servicio MCP y, si no la conoce, el id tal cual', () => {
    expect(nombreCapacidad('core.buffer')).toBe('Buffer')
    expect(nombreCapacidad('mcp.imagery.imagery_ndvi')).toBe('Servicio «imagery» · imagery_ndvi')
    expect(nombreCapacidad('core.algo_nuevo')).toBe('core.algo_nuevo')
  })

  it('los argumentos nombran las entradas por su dataset y omiten los vacíos', () => {
    const pasos = [{ dataset_id: 'ds_a', nombre: 'Buffer 100 m', disponible: true },
                   { dataset_id: 'ds_b', nombre: 'Lotes', disponible: true }]
    expect(argumentos({ dataset: 'ds_b', meters: 100, dissolve: null, extra: '' }, pasos))
      .toEqual([['dataset', '«Lotes»'], ['meters', '100']])
    expect(argumentos({ dataset: 'ds_zz' }, pasos)).toEqual([['dataset', 'ds_zz']])
  })
})
