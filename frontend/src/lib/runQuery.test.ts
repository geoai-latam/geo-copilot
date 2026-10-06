/**
 * Auditoría 2026-09-08 §5 (3) — un solo cliente de consultas.
 *
 * `CanvasTabs.refreshLastQuery` era una copia degradada de
 * `ChatDock.sendQuery`: sin `map_context`, sin `AbortSignal`, sin
 * `target_layer_id` y siempre con `addLayer`. En una consulta de simbología,
 * "Refrescar" DUPLICABA la capa en vez de re-estilarla — el bug que el chat
 * tenía arreglado desde el 2026-06-13.
 *
 * Estos tests fijan las cuatro propiedades que el camino degradado no tenía,
 * más las transiciones del chip de estado (§5 hallazgo 2), sobre el único
 * camino que queda.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

const process = vi.fn()
vi.mock('@/services/api', () => ({
  queryApi: { process: (...args: unknown[]) => process(...args) },
}))

const { runQuery, abortActiveQuery, resetActiveQuery, aplicarRespuestaReanudada } = await import('./runQuery')
const { useChatStore, useMapStore, useSessionStore, useUIStore } = await import('@/stores')
const { useResultsStore } = await import('@/stores/resultsStore')
const { respuesta, capa, estilo } = await import('@/contracts/fixtures')

const fc = (n: number) => ({
  type: 'FeatureCollection' as const,
  features: Array.from({ length: n }, () => ({
    type: 'Feature' as const,
    geometry: { type: 'Point' as const, coordinates: [-74, 4] },
    properties: {},
  })),
})

// F4: las respuestas simuladas son VÁLIDAS por contrato (runQuery las valida).
const ok = respuesta

beforeEach(() => {
  process.mockReset()
  resetActiveQuery()
  useChatStore.setState({
    messages: [],
    isLoading: false,
    chatStatus: 'ready',
    queryHistory: [],
  } as never)
  useMapStore.setState({ layers: [] } as never)
  useUIStore.setState({ showApprovalPanel: false } as never)
  useResultsStore.getState().limpiar()
  useSessionStore.setState({ sessionId: 's1' } as never)
})

describe('runQuery — el camino único', () => {
  it('adjunta map_context (el camino degradado no lo hacía)', async () => {
    process.mockResolvedValue(ok())
    await runQuery('cuántos lotes hay')
    const [payload] = process.mock.calls[0]
    expect(payload.map_context).toBeDefined()
    expect(payload.map_context.viewport.crs).toBe('EPSG:4326')
    expect(payload.query).toBe('cuántos lotes hay')
    expect(payload.session_id).toBe('s1')
  })

  it('pasa un AbortSignal y "Detener" lo dispara', async () => {
    let captured: AbortSignal | undefined
    process.mockImplementation((_payload: unknown, signal: AbortSignal) => {
      captured = signal
      return new Promise((_res, rej) => {
        signal.addEventListener('abort', () => {
          rej(Object.assign(new DOMException('aborted', 'AbortError')))
        })
      })
    })
    const pending = runQuery('consulta larga')
    await vi.waitFor(() => expect(captured).toBeDefined())
    expect(captured!.aborted).toBe(false)
    abortActiveQuery()
    await pending
    expect(captured!.aborted).toBe(true)
    const msgs = useChatStore.getState().messages
    expect(msgs.some((m) => m.content === 'Consulta cancelada.')).toBe(true)
    // Cancelar no es fallar: el chip vuelve a "Listo", no a "Error".
    expect(useChatStore.getState().chatStatus).toBe('ready')
  })

  it('una consulta de simbología RE-ESTILA la capa objetivo, no la duplica', async () => {
    useMapStore.getState().addLayer(fc(3), 'Lotes')
    const [target] = useMapStore.getState().layers
    process.mockResolvedValue(ok({
      intent: 'apply_symbology',
      artifacts: [capa({ inline: fc(3), style: { fill: { color: '#ff0000' } }, replaces: target.id })],
    }))

    await runQuery('píntalos de rojo')

    const layers = useMapStore.getState().layers
    // Esta es la aserción del hallazgo: UNA capa, no dos.
    expect(layers).toHaveLength(1)
    expect(layers[0].name).toBe('Lotes')
    expect(layers[0].color).toBe('#ff0000')
  })

  it('re-estila por target_layer_id, no por "la última añadida"', async () => {
    useMapStore.getState().addLayer(fc(2), 'Vías')
    useMapStore.getState().addLayer(fc(2), 'Lotes')
    const [vias] = useMapStore.getState().layers
    // Re-estilo sin datos nuevos: el backend manda una orden set_style.
    process.mockResolvedValue(ok({
      intent: 'apply_symbology',
      artifacts: [{
        kind: 'map_command',
        command: { op: 'set_style', layer_id: vias.id, args: { style: estilo({ fill: { color: '#00ff00' } }) }, reason: 'verde' },
      }],
    }))

    await runQuery('píntame las vías de verde')

    const layers = useMapStore.getState().layers
    expect(layers).toHaveLength(2)
    const reestilada = layers.find((l) => l.name === 'Vías')
    expect(reestilada?.color).toBe('#00ff00')
    expect(layers.find((l) => l.name === 'Lotes')?.color).not.toBe('#00ff00')
  })

  it('una consulta normal SÍ añade capa', async () => {
    process.mockResolvedValue(ok({ intent: 'query_data', artifacts: [capa({ inline: fc(5) })] }))
    await runQuery('dame los predios')
    expect(useMapStore.getState().layers).toHaveLength(1)
    expect(useMapStore.getState().layers[0].featureCount).toBe(5)
  })

  it('no arranca sin sesión, ni con otra consulta en vuelo', async () => {
    useSessionStore.setState({ sessionId: null } as never)
    await runQuery('hola')
    expect(process).not.toHaveBeenCalled()

    useSessionStore.setState({ sessionId: 's1' } as never)
    useChatStore.setState({ isLoading: true } as never)
    await runQuery('hola')
    expect(process).not.toHaveBeenCalled()
  })
})

describe('runQuery — el chip de estado deja de mentir', () => {
  it('pasa por "searching" durante la consulta y vuelve a "ready"', async () => {
    const visto: string[] = []
    process.mockImplementation(() => {
      visto.push(useChatStore.getState().chatStatus)
      return Promise.resolve(ok())
    })
    await runQuery('cuántos lotes hay')
    expect(visto).toEqual(['searching'])
    expect(useChatStore.getState().chatStatus).toBe('ready')
  })

  it('un fallo deja el chip en "error"', async () => {
    process.mockRejectedValue(new Error('500'))
    await runQuery('rompe')
    expect(useChatStore.getState().chatStatus).toBe('error')
    expect(useChatStore.getState().queryHistory[0].success).toBe(false)
  })

  it('si la respuesta pide aprobación, el chip lo dice', async () => {
    process.mockResolvedValue(ok({
      status: 'waiting_approval',
      requires_approval: true,
      pending_approval_id: 'ap-1',
    }))
    await runQuery('borra la tabla')
    expect(useChatStore.getState().chatStatus).toBe('waiting_approval')
    expect(useUIStore.getState().showApprovalPanel).toBe(true)
  })

  it('«acércate» ya no se decide en el cliente: va al agente (FH.1, orden zoom_to)', async () => {
    useMapStore.getState().addLayer(fc(4), 'Lotes')
    const [lotes] = useMapStore.getState().layers
    process.mockResolvedValue(ok({
      intent: 'map_control',
      artifacts: [{ kind: 'map_command', command: { op: 'zoom_to', layer_id: lotes.id, args: { bbox: null }, reason: null } }],
    }))
    await runQuery('acércate')
    expect(process).toHaveBeenCalledTimes(1)
    expect(useMapStore.getState().flyToLayerId).toBe(lotes.id)
  })
})

describe('runQuery — capa grande como teselas (S2.3)', () => {
  it('añade una capa teselada con su dataset y sin features en memoria', async () => {
    process.mockResolvedValue(ok({
      intent: 'query_data',
      artifacts: [capa({
        id: 'ds_0123456789abcdef', name: 'Lotes', featureCount: 60000, geometryType: 'Polygon',
        bbox: [-74.2, 4.5, -74.0, 4.7],
        tiles: {
          url_template: '/api/v1/tiles/ws/s1/ds_0123456789abcdef/{z}/{x}/{y}.pbf',
          source_layer: 'dataset', fields: ['lotcodigo'],
        },
      })],
    }))
    await runQuery('trae todos los lotes')
    const [enMapa] = useMapStore.getState().layers
    expect(enMapa.datasetId).toBe('ds_0123456789abcdef')
    expect(enMapa.tiles?.sourceLayer).toBe('dataset')
    expect(enMapa.featureCount).toBe(60000)
    expect(enMapa.data.features).toHaveLength(0)
  })
})

describe('runQuery — frontera del contrato y panel de resultados (S4.1/S4.3)', () => {
  it('una respuesta fuera de contrato no se pinta: error con el motivo', async () => {
    process.mockResolvedValue({ ...ok(), results: { geojson: fc(2) } })
    await runQuery('dame los predios')
    expect(useMapStore.getState().layers).toHaveLength(0)
    expect(useChatStore.getState().chatStatus).toBe('error')
    expect(useResultsStore.getState().turnos).toHaveLength(0)
  })

  it('el turno queda en el historial del panel con sus artefactos y su SQL', async () => {
    process.mockResolvedValue(ok({
      query_id: 'q-tabla',
      sql: 'SELECT 1',
      artifacts: [
        capa({ inline: fc(2) }),
        { kind: 'table', title: null, columns: ['a'], preview: [{ a: 1 }], rows_ref: null, total_rows: 1 },
      ],
    }))
    await runQuery('tabla de lotes')
    const { turnos, activo } = useResultsStore.getState()
    expect(activo).toBe('q-tabla')
    expect(turnos[0].sql).toBe('SELECT 1')
    expect(turnos[0].artefactos.map((a) => a.kind)).toEqual(['layer', 'table'])
    expect(useMapStore.getState().layers).toHaveLength(1)
  })
})

describe('turno reanudado tras un reinicio (F7, E7.1)', () => {
  it('su respuesta, que llega por WebSocket, se pinta como cualquier respuesta', () => {
    aplicarRespuestaReanudada(ok({ message: 'Traje los 4 lotes.' }), 'trae los lotes')
    const ultimo = useChatStore.getState().messages.slice(-1)[0]
    expect(ultimo?.content).toBe('Traje los 4 lotes.')
    expect(ultimo?.status).toBe('sent')
    expect(useChatStore.getState().chatStatus).toBe('ready')
    expect(useChatStore.getState().queryHistory.slice(-1)[0]?.query).toBe('trae los lotes')
  })

  it('una respuesta fuera de contrato no se pinta a medias: se dice', () => {
    aplicarRespuestaReanudada({ no: 'es una respuesta' }, 'x')
    const ultimo = useChatStore.getState().messages.slice(-1)[0]
    expect(ultimo?.status).toBe('error')
    expect(useChatStore.getState().chatStatus).toBe('error')
  })
})
