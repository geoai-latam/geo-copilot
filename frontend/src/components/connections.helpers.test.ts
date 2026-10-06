import { describe, expect, it } from 'vitest'
import type { McpServerStatus } from '@/services/api'
import { estadoDe, hostDe, nivelDe, resumenTools, riesgoDe } from './connections.helpers'

const servidor = (tools: { habilitada: boolean }[]): McpServerStatus => ({
  id: 'imagery', url: 'http://imagery-mcp:9100/mcp', conformance: 'G2', estado: 'disponible',
  ultimo_error: null, servidor: null,
  tools: tools.map((t, i) => ({ nombre: `t${i}`, herramienta: `imagery__t${i}`, motivo: null, riesgo: 'read', geo: false, ...t })),
})

describe('panel de Conexiones (S4.5)', () => {
  it('el nivel se explica en lo que la plataforma hace con el servidor', () => {
    expect(nivelDe('G2')).toMatch(/teselas/)
    expect(nivelDe('G0')).toMatch(/texto/)
    expect(nivelDe('G9')).toBe('Nivel no declarado')
  })

  it('estado y riesgo tienen tono', () => {
    expect(estadoDe('disponible')).toEqual({ texto: 'Disponible', tono: 'ok' })
    expect(estadoDe('no_disponible')).toEqual({ texto: 'No disponible', tono: 'danger' })
    expect(estadoDe('desconocido')).toEqual({ texto: 'Sin verificar', tono: 'warn' })
    expect(riesgoDe('read').tono).toBe('ok')
    expect(riesgoDe('write').tono).toBe('danger')
    expect(riesgoDe('compute').texto).toBe('Cómputo')
  })

  it('resume cuántas tools hay y cuántas esperan aprobación', () => {
    expect(resumenTools(servidor([{ habilitada: true }]))).toBe('1 herramienta')
    expect(resumenTools(servidor([{ habilitada: true }, { habilitada: false }, { habilitada: true }])))
      .toBe('3 herramientas · 1 deshabilitada')
  })

  it('muestra el host, nunca credenciales ni ruta', () => {
    expect(hostDe('http://user:secreto@imagery-mcp:9100/mcp')).toBe('imagery-mcp:9100')
    expect(hostDe('no es url')).toBe('no es url')
  })
})
