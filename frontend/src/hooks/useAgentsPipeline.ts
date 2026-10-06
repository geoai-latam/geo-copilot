/**
 * Subscribe to WebSocket progress messages and expose a per-agent
 * pipeline state.
 *
 * The backend emits `plan_created` / `step_started` / `step_completed`
 * (Fase 6 #6 — orchestrator/planner). We translate those into 4 visual
 * agent stages: data → gis → sym → ins. Falls back to a static "idle"
 * pipeline when no query is active.
 */
import { useEffect, useState } from 'react'
import { wsService } from '@/services/websocket'

export type AgentId = 'data' | 'gis' | 'sym' | 'ins'
export type AgentState = 'idle' | 'active' | 'done' | 'failed'

export interface AgentNode {
  id: AgentId
  short: string
  label: string
  state: AgentState
}

const STAGES: Omit<AgentNode, 'state'>[] = [
  { id: 'data', short: 'data', label: 'Datos' },
  { id: 'gis',  short: 'gis',  label: 'Espacial' },
  { id: 'sym',  short: 'sym',  label: 'Simbología' },
  { id: 'ins',  short: 'ins',  label: 'Análisis' },
]

function classifyStep(agent: string | undefined, description: string | undefined): AgentId { // eslint-disable-line complexity -- deuda congelada (F1); partir, no subir
  const t = `${agent ?? ''} ${description ?? ''}`.toLowerCase()
  if (t.includes('symbol') || t.includes('simbo') || t.includes('palet') || t.includes('color')) return 'sym'
  if (t.includes('insight') || t.includes('análisis') || t.includes('analisis') || t.includes('respond') || t.includes('summary')) return 'ins'
  if (t.includes('gis') || t.includes('espac') || t.includes('postgis') || t.includes('geom') || t.includes('buffer') || t.includes('sql')) return 'gis'
  return 'data'
}

export function useAgentsPipeline(): { nodes: AgentNode[]; isRunning: boolean } {
  const [state, setState] = useState<Record<AgentId, AgentState>>({
    data: 'idle', gis: 'idle', sym: 'idle', ins: 'idle',
  })
  const [running, setRunning] = useState(false)

  useEffect(() => {
    const unsub = wsService.onMessage((message) => {
      const t = message.type
      const data = (message.data ?? {}) as Record<string, unknown>

      if (t === 'plan_created') {
        setRunning(true)
        setState({ data: 'active', gis: 'idle', sym: 'idle', ins: 'idle' })
      }
      else if (t === 'status') {
        const phase = (data.status as string | undefined)
        if (phase === 'processing') {
          // Nueva consulta — resetear todas las etapas. El primer
          // step_started que llegue (router, data_agent...) marcará
          // su etapa como active.
          setRunning(true)
          setState({ data: 'idle', gis: 'idle', sym: 'idle', ins: 'idle' })
        }
        else if (phase === 'error') {
          setRunning(false)
          setState((prev) => {
            const next = { ...prev }
            ;(['data', 'gis', 'sym', 'ins'] as AgentId[]).forEach((k) => {
              if (next[k] === 'active') next[k] = 'failed'
            })
            return next
          })
        }
      }
      else if (t === 'step_started') {
        const stage = classifyStep(data.agent as string | undefined, data.description as string | undefined)
        setRunning(true)
        // No marcar etapas anteriores como "done" automáticamente: que
        // el chip refleje qué corrió de verdad. Las etapas no tocadas
        // quedan en `idle` (grises) — eso comunica "se saltó".
        setState((prev) => ({ ...prev, [stage]: 'active' }))
      }
      else if (t === 'step_completed') {
        const stage = classifyStep(data.agent as string | undefined, data.description as string | undefined)
        setState((prev) => ({ ...prev, [stage]: 'done' }))
      }
      else if (t === 'result') {
        // Cierre del pipeline: bajar el flag de running. NO sobreescribir
        // las etapas: dejamos visibles las que realmente corrieron.
        setRunning(false)
        setState((prev) => {
          const next = { ...prev }
          ;(['data', 'gis', 'sym', 'ins'] as AgentId[]).forEach((k) => {
            if (next[k] === 'active') next[k] = 'done'
          })
          return next
        })
      }
      else if (t === 'error' || t === 'execution_cancelled') {
        setRunning(false)
        setState((prev) => {
          const next = { ...prev }
          ;(['data', 'gis', 'sym', 'ins'] as AgentId[]).forEach((k) => {
            if (next[k] === 'active') next[k] = 'failed'
          })
          return next
        })
      }
    })
    return () => { unsub() }
  }, [])

  const nodes: AgentNode[] = STAGES.map((s) => ({ ...s, state: state[s.id] }))
  return { nodes, isRunning: running }
}
