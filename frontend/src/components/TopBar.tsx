import {
  usePortalConnected,
  useBdConnected,
  useSessionId,
  useIsConnected,
} from '@/stores'
import { ProyectosControl } from './ProyectosControl'
import { MenuUsuario } from './AuthGate'

export function TopBar() {
  const portalConnected = usePortalConnected()
  const bdConnected = useBdConnected()
  const sessionId = useSessionId()
  // Canal en tiempo real (WebSocket): por él llegan las aprobaciones y el progreso.
  const tiempoReal = useIsConnected()

  const allConnected = portalConnected && bdConnected && (tiempoReal || !sessionId)
  const pillClass = allConnected ? '' : 'off'
  const pillLabel = allConnected
    ? 'Conectado'
    : portalConnected || bdConnected
      ? 'Parcial'
      : 'Desconectado'

  return (
    <header className="topbar">
      <div className="brand">
        <img className="logo" src="/geoai-logo.png" alt="GeoAI LATAM" width={24} height={24} />
        <span className="topbar-title">Geo Copilot</span>
      </div>

      {sessionId && (
        <>
          <div className="div" />
          <ProyectosControl />
        </>
      )}

      <div className="right">
        <span className={`pill ${pillClass}`}>
          <span className="d" />
          {pillLabel}
        </span>
        <span
          className="k mono"
          title={sessionId && !tiempoReal
            ? 'Sin tiempo real: las aprobaciones y el progreso no llegan hasta que se reconecte'
            : undefined}
        >
          Portal {portalConnected ? '·' : '✕'} BD {bdConnected ? '·' : '✕'}
          {sessionId ? ` Tiempo real ${tiempoReal ? '·' : '✕'}` : ''}
        </span>
        <MenuUsuario />
      </div>
    </header>
  )
}
