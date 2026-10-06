import { describe, expect, it } from 'vitest'
import { resumenDe } from './auditoria'
import type { EntradaAuditoria } from '@/services/api'

const base: EntradaAuditoria = {
  id: 1, ts: '2026-09-28T03:00:00Z', actor_sub: 'u', actor_nombre: 'Ana', actor_via: 'oidc',
  accion: 'consulta', recurso: 'agente', session_id: 's', resultado: 'recibida', detalle: {},
}

describe('resumenDe (E6.5: qué hizo cada quien, en una línea)', () => {
  it('una consulta muestra lo que se preguntó', () => {
    expect(resumenDe({ ...base, detalle: { consulta: 'trae los lotes' } })).toBe('«trae los lotes»')
  })
  it('una aprobación muestra qué se aprobó (el SQL en una línea)', () => {
    const e = { ...base, accion: 'hitl.aprobar', detalle: { titulo: 'Ejecutar SQL', vista_previa: 'SELECT *\n  FROM lotes' } }
    expect(resumenDe(e)).toBe('Ejecutar SQL: SELECT * FROM lotes')
  })
  it('una ejecución muestra sus argumentos; sin ellos, nada', () => {
    expect(resumenDe({ ...base, accion: 'capacidad.ejecutar', detalle: { argumentos: { meters: 500 } } })).toBe('{"meters":500}')
    expect(resumenDe({ ...base, accion: 'capacidad.ejecutar', detalle: { argumentos: {} } })).toBe('')
  })
})
