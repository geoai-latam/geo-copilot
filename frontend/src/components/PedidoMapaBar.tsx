/**
 * FH.9 — lo que el agente pidió en el mapa, visible sobre él: la pregunta y cómo responder
 * (clic, dibujar, elegir capa, seleccionar + «Listo»). Cancelar también es una respuesta:
 * el agente sabe que el usuario no quiso señalarlo.
 */
import { useEffect, useState } from 'react'
import { Check, MapPin, X } from 'lucide-react'

import { cancelarDibujo, empezarDibujo } from '@/lib/dibujo'
import { usePedidoMapa } from '@/lib/pedidoMapa'
import { responder } from '@/lib/responderPedido'
import { useLayers, useMapStore } from '@/stores/mapStore'

const COMO: Record<string, string> = {
  pick_point: 'Haz clic en el mapa.',
  draw_area: 'Dibuja el área en el mapa (doble clic para cerrarla).',
  pick_layer: 'Elige la capa:',
  pick_features: 'Selecciónalos (clic, Shift+clic o caja) y pulsa «Listo».',
}

export function PedidoMapaBar() {
  const pedido = usePedidoMapa((s) => s.pedido)
  const capas = useLayers()
  const [capaElegida, setCapaElegida] = useState('')

  // un área pedida: la herramienta de dibujo ya abierta
  useEffect(() => {
    if (pedido?.modo === 'draw_area') empezarDibujo('polygon')
    setCapaElegida('')
  }, [pedido])

  if (!pedido) return null
  const conSeleccion = capas.find((l) => (!pedido.capaId || l.id === pedido.capaId) && (l.seleccion?.count ?? 0) > 0)
  const cancelar = () => {
    if (useMapStore.getState().modoDibujo) cancelarDibujo()
    void responder({ cancelado: true })
  }

  return (
    <div className="pedido-mapa" role="dialog" aria-label="El copiloto te pide algo en el mapa" data-testid="pedido-mapa">
      <MapPin className="w-4 h-4" aria-hidden />
      <div className="pedido-mapa-texto">
        <strong>{pedido.pregunta}</strong>
        <span>{COMO[pedido.modo]}</span>
      </div>
      {pedido.modo === 'pick_layer' && (
        <>
          <select className="input" aria-label="Capa" value={capaElegida} onChange={(e) => setCapaElegida(e.target.value)}>
            <option value="">—</option>
            {[...capas].reverse().map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
          </select>
          <button type="button" className="btn btn-primary" disabled={!capaElegida}
                  onClick={() => void responder({ layerId: capaElegida, nombre: capas.find((l) => l.id === capaElegida)?.name })}>
            <Check className="w-3.5 h-3.5" /> Usar
          </button>
        </>
      )}
      {pedido.modo === 'pick_features' && (
        <button type="button" className="btn btn-primary" disabled={!conSeleccion}
                onClick={() => conSeleccion && void responder({ layerId: conSeleccion.id, nombre: conSeleccion.name })}>
          <Check className="w-3.5 h-3.5" /> Listo{conSeleccion ? ` (${conSeleccion.seleccion?.count})` : ''}
        </button>
      )}
      <button type="button" className="icon-btn" onClick={cancelar} aria-label="No señalar nada" title="No señalar nada">
        <X className="w-3.5 h-3.5" />
      </button>
    </div>
  )
}
