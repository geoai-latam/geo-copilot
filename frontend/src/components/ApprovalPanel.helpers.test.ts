/**
 * Auditoría 2026-09-08 §5 (1) — el grid de impacto solo pinta lo que el
 * backend mandó.
 *
 * La propiedad que importa: NINGUNA celda se inventa. Si `impact_estimate`
 * no llega (que es lo que pasa hoy: `security/hitl.py:340-354` no lo emite),
 * el resultado es un array vacío y el panel dice que no hay estimación, en
 * vez de tres guiones que se leen como un cálculo que salió en blanco.
 */
import { describe, it, expect } from 'vitest'
import { buildImpactCells } from './ApprovalPanel.helpers'

describe('buildImpactCells', () => {
  it('sin impact_estimate no produce ninguna celda', () => {
    expect(buildImpactCells(undefined, false)).toEqual([])
    expect(buildImpactCells(null, false)).toEqual([])
  })

  it('un objeto vacío tampoco produce celdas (nadie mandó nada)', () => {
    expect(buildImpactCells({}, false)).toEqual([])
  })

  it('pinta SOLO los campos presentes', () => {
    const cells = buildImpactCells({ rows: 1200 }, false)
    expect(cells).toHaveLength(1)
    expect(cells[0].key).toBe('rows')
    // El separador de miles depende del ICU del entorno (el jsdom de CI
    // corre con small-icu y no agrupa), así que se compara contra la misma
    // API en vez de contra un literal.
    expect(cells[0].value).toBe(`~ ${(1200).toLocaleString('es')}`)
  })

  it('con los tres campos pinta las tres celdas en orden de lectura', () => {
    const cells = buildImpactCells({ rows: 10, cost: 42.6, time_ms: 250 }, false)
    expect(cells.map((c) => c.key)).toEqual(['rows', 'cost', 'time'])
  })

  it('formatea el tiempo en ms bajo el segundo y en s por encima', () => {
    expect(buildImpactCells({ time_ms: 250 }, false)[0].value).toBe('~ 250 ms')
    expect(buildImpactCells({ time_ms: 4200 }, false)[0].value).toBe('~ 4.2 s')
  })

  it('redondea el costo del planner', () => {
    expect(buildImpactCells({ cost: 1234.7 }, false)[0].value).toBe((1235).toLocaleString('es'))
    expect(buildImpactCells({ cost: 42.4 }, false)[0].value).toBe('42')
  })

  it('cae al riesgo cualitativo solo si no hay costo numérico', () => {
    const soloRiesgo = buildImpactCells({ risk: 'high' }, true)
    expect(soloRiesgo).toHaveLength(1)
    expect(soloRiesgo[0].label).toBe('Riesgo estimado')
    expect(soloRiesgo[0].value).toBe('alto')

    const conCosto = buildImpactCells({ cost: 900, risk: 'high' }, true)
    expect(conCosto).toHaveLength(1)
    expect(conCosto[0].label).toBe('Costo (planner)')
  })

  it('marca en warn la celda de costo cuando la acción es de alto riesgo', () => {
    expect(buildImpactCells({ cost: 900 }, true)[0].warn).toBe(true)
    expect(buildImpactCells({ cost: 900 }, false)[0].warn).toBe(false)
    // Filas y tiempo nunca se resaltan: no son la señal de riesgo.
    expect(buildImpactCells({ rows: 5, time_ms: 5 }, true).every((c) => !c.warn)).toBe(true)
  })

  it('un cero es un dato, no una ausencia', () => {
    const cells = buildImpactCells({ rows: 0, cost: 0, time_ms: 0 }, false)
    expect(cells).toHaveLength(3)
    expect(cells[0].value).toBe('~ 0')
  })
})
