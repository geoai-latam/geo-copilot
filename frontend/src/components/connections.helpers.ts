/**
 * Panel de Conexiones (F4, S4.5): cómo se lee el estado de cada servidor MCP.
 * Lógica pura, sin React, para testearla. Los textos explican QUÉ significa cada
 * cosa para el usuario; los valores los decide el hub.
 */
import type { McpServerStatus } from '@/services/api'

export type Tono = 'ok' | 'warn' | 'danger'

/** Nivel de conformidad → qué hace la plataforma con ese servidor (docs/sistema/12 §3). */
export const NIVELES: Record<string, string> = {
  G0: 'MCP genérico: su respuesta llega al agente como texto',
  G1: 'Devuelve GeoResult: sus capas entran al workspace y al mapa',
  G2: 'GeoResult + teselas: sus capas raster se pintan en el mapa',
}

export function nivelDe(conformance: string): string {
  return NIVELES[conformance] ?? 'Nivel no declarado'
}

/** Estado del servidor (el del hub: disponible · no_disponible · desconocido) → chip. */
export function estadoDe(estado: string): { texto: string; tono: Tono } {
  if (estado === 'disponible') return { texto: 'Disponible', tono: 'ok' }
  if (estado === 'no_disponible') return { texto: 'No disponible', tono: 'danger' }
  if (estado === 'desconocido') return { texto: 'Sin verificar', tono: 'warn' }
  return { texto: estado || 'Sin verificar', tono: 'warn' }
}

/** Riesgo de una tool → etiqueta y tono (el HITL pide aprobación según el riesgo). */
export function riesgoDe(riesgo: string): { texto: string; tono: Tono } {
  if (riesgo === 'read') return { texto: 'Solo lectura', tono: 'ok' }
  if (riesgo === 'write') return { texto: 'Escribe', tono: 'danger' }
  return { texto: 'Cómputo', tono: 'warn' }
}

/** Resumen de una fila: «5 tools · 1 deshabilitada». */
export function resumenTools(s: McpServerStatus): string {
  const n = s.tools.length
  const off = s.tools.filter((t) => !t.habilitada).length
  const base = `${n} ${n === 1 ? 'herramienta' : 'herramientas'}`
  return off ? `${base} · ${off} ${off === 1 ? 'deshabilitada' : 'deshabilitadas'}` : base
}

/** Host de la URL del servidor (sin credenciales ni ruta). */
export function hostDe(url: string): string {
  try {
    return new URL(url).host
  } catch {
    return url
  }
}
