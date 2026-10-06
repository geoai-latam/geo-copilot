import { useEffect, useRef } from 'react'
import {
  useSessionStore,
  useSessionId,
} from '@/stores'
import {
  useUIStore,
  useShowApprovalPanel,
} from '@/stores'
import {
  useChatStore,
  useMapStore,
} from '@/stores'
import {
  leerCamara, leerChat, recordarCamara, recordarCapas, recordarChat, recordarSesion, restaurarCapas, sesionDeLaPestana,
} from '@/lib/workspaceRestore'
import { sessionApi, healthApi, metadataApi } from '@/services/api'
import { wsService } from '@/services/websocket'
import { manejarAvisoDeTurno } from '@/lib/runQuery'
import { logger } from '@/utils/logger'
import type { ApprovalStatus } from '@/types'
import { MapLibreMap } from '@/components/MapLibreMap'
import { MapBasemapControl } from '@/components/MapBasemapControl'
import { MapLegend } from '@/components/MapLegend'
import { SeleccionBar } from '@/components/SeleccionBar'
import { DibujoBar } from '@/components/DibujoBar'
import { ErrorBoundary } from '@/components/ErrorBoundary'
import { FeaturePopup } from '@/components/FeaturePopup'
import { ApprovalPanel } from '@/components/ApprovalPanel'
import { TopBar } from '@/components/TopBar'
import { LeftPanel } from '@/components/LeftPanel'
import { LeftDrawer } from '@/components/LeftDrawer'
import { ChatDock } from '@/components/ChatDock'
import { StatusBar } from '@/components/StatusBar'
import { CanvasTabs } from '@/components/CanvasTabs'
import { AgentsPipe } from '@/components/AgentsPipe'
import { TweaksPanel } from '@/components/TweaksPanel'
import { MenuContextual } from '@/components/MenuContextual'
import { PedidoMapaBar } from '@/components/PedidoMapaBar'
import { VistasControl } from '@/components/VistasControl'
import { CompararCortina } from '@/components/CompararCortina'
import { TiempoControl } from '@/components/TiempoControl'

function App() {
  const sessionId = useSessionId()
  const showApprovalPanel = useShowApprovalPanel()

  const setSessionId = useSessionStore((s) => s.setSessionId)
  const addPendingApproval = useUIStore((s) => s.addPendingApproval)
  const setShowApprovalPanel = useUIStore((s) => s.setShowApprovalPanel)
  const setPortalConnected = useSessionStore((s) => s.setPortalConnected)
  const setBdConnected = useSessionStore((s) => s.setBdConnected)
  const setRetryInfo = useChatStore((s) => s.setRetryInfo)
  const clearRetryInfo = useChatStore((s) => s.clearRetryInfo)
  const setChatStatus = useChatStore((s) => s.setChatStatus)

  useEffect(() => {
    const initApp = async () => {
      try {
        // E2.4: tras recargar, la pestaña vuelve a SU sesión (si sigue viva) y
        // sus capas del workspace se restauran; si no, una sesión nueva.
        const { sessionId: sid, recuperada } = await sesionDeLaPestana()
        setSessionId(sid)
        if (recuperada) {
          // FH.11: la conversación y la cámara de la sesión (recargar o abrir un proyecto)
          const chat = leerChat(sid)
          if (chat.length && !useChatStore.getState().messages.length) useChatStore.setState({ messages: chat })
          const camara = leerCamara(sid)
          if (camara) useMapStore.getState().setMapView(camara.center, camara.zoom)
          const n = await restaurarCapas(sid)
          if (n) logger.log(`[workspace] ${n} capa(s) restaurada(s) de la sesión`)
        }
      } catch (error) {
        logger.error('Failed to create session:', error)
      }

      try {
        const health = await healthApi.check()
        const portalOk = health.components?.semantic_layer === 'healthy'
        setPortalConnected(portalOk)
      } catch (error) {
        logger.error('Health check failed:', error)
        setPortalConnected(false)
      }

      try {
        const entities = await metadataApi.listEntities()
        const bdOk = entities.total > 0
        setBdConnected(bdOk)
      } catch (error) {
        logger.error('Entities fetch failed:', error)
        setBdConnected(false)
      }
    }

    initApp()
  }, [setSessionId, setPortalConnected, setBdConnected])

  // S0.3: si el backend perdió la sesión (reinicio con sesiones en memoria),
  // el socket no puede volver a ella. Se crea una nueva y el effect de abajo
  // reconecta al cambiar `sessionId`.
  useEffect(() => {
    wsService.setSessionRecovery(async (id) => {
      if (await sessionApi.exists(id)) return id
      const session = await sessionApi.create()
      logger.warn(`[WS] La sesión ${id} ya no existe; se abre ${session.session_id}`)
      recordarSesion(session.session_id)
      setSessionId(session.session_id)
      return null
    })
    return () => wsService.setSessionRecovery(null)
  }, [setSessionId])

  // E2.4: recordar las capas del workspace de la sesión (id + estilo).
  useEffect(() => {
    if (!sessionId) return
    const a = useMapStore.subscribe((s, prev) => {
      if (s.layers !== prev.layers) recordarCapas(sessionId, s.layers)
      if (s.mapCenter !== prev.mapCenter || s.mapZoom !== prev.mapZoom) {
        recordarCamara(sessionId, { center: s.mapCenter, zoom: s.mapZoom })
      }
    })
    // FH.11: la conversación sobrevive a recargar (antes el chat quedaba vacío)
    const b = useChatStore.subscribe((s, prev) => {
      if (s.messages !== prev.messages) recordarChat(sessionId, s.messages)
    })
    return () => { a(); b() }
  }, [sessionId])

  const retryHideTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    if (sessionId) {
      // El estado del canal en tiempo real es un hecho que el usuario debe ver: sin él las
      // aprobaciones no llegan y la consulta espera en «Procesando…» sin explicación (auditoría
      // pre-producción: la barra decía «Conectado» con cada WebSocket rechazado por origen).
      const { setConnected } = useSessionStore.getState()
      const offConnect = wsService.onConnect(() => setConnected(true))
      const offDisconnect = wsService.onDisconnect(() => setConnected(false))
      wsService.connect(sessionId)

      const unsubscribe = wsService.onMessage((message) => {
        // F7 (E7.1 y auditoría): los turnos sin petición HTTP de esta pestaña — reanudados tras un
        // reinicio o aprobados tras recargar — avisan y entregan su resultado por aquí, ligados a
        // su turno; al reconectar se sabe si el que se esperaba sigue en curso.
        if (manejarAvisoDeTurno(message.type, message.data)) return

        if (message.type === 'approval_request') {
          const approvalData = message.data as ApprovalStatus
          addPendingApproval(approvalData)
          // HITL BLOQUEANTE: la respuesta HTTP no llega hasta resolver la
          // aprobación, así que el panel debe abrirse desde el WS — con el
          // flujo HTTP-only el usuario nunca veía la solicitud (timeout).
          setShowApprovalPanel(true)
          // Auditoría 2026-09-08 §5 (2): este es el instante en que la espera
          // deja de ser del servidor y pasa a ser del usuario. El chip lo dice.
          setChatStatus('waiting_approval')
        }

        else if (message.type === 'retry_started') {
          const data = message.data as {
            agent: string;
            attempt: number;
            max_attempts: number;
            error: string;
            action: string;
          }
          setRetryInfo({
            show: true,
            agent: data.agent,
            status: 'retrying',
            attempt: data.attempt,
            maxAttempts: data.max_attempts,
            error: data.error,
            action: data.action,
          })
        }

        else if (message.type === 'retry_correction') {
          // FE2: leer el valor VIVO del store, no el `retryInfo` cerrado en
          // el closure de este effect (que queda fijado al suscribir — null
          // en el primer connect — porque retryInfo no está en las deps).
          const current = useChatStore.getState().retryInfo
          if (current) {
            setRetryInfo({ ...current, status: 'correcting' })
          }
        }

        else if (message.type === 'retry_success') {
          const data = message.data as { agent: string; attempts: number; message: string }
          setRetryInfo({
            show: true,
            agent: data.agent,
            status: 'success',
            attempt: data.attempts,
            maxAttempts: data.attempts,
            message: data.message,
          })
          if (retryHideTimerRef.current) clearTimeout(retryHideTimerRef.current)
          retryHideTimerRef.current = setTimeout(() => {
            clearRetryInfo()
            retryHideTimerRef.current = null
          }, 3000)
        }

        else if (message.type === 'retry_failed') {
          const data = message.data as { agent: string; attempts: number; message: string }
          setRetryInfo({
            show: true,
            agent: data.agent,
            status: 'failed',
            attempt: data.attempts,
            maxAttempts: data.attempts,
            message: data.message,
          })
          if (retryHideTimerRef.current) clearTimeout(retryHideTimerRef.current)
          retryHideTimerRef.current = setTimeout(() => {
            clearRetryInfo()
            retryHideTimerRef.current = null
          }, 5000)
        }
      })

      return () => {
        if (retryHideTimerRef.current !== null) {
          clearTimeout(retryHideTimerRef.current)
          retryHideTimerRef.current = null
        }
        unsubscribe()
        offConnect()
        offDisconnect()
        wsService.disconnect()
        setConnected(false)
      }
    }
  }, [sessionId, addPendingApproval, setShowApprovalPanel, setRetryInfo, clearRetryInfo, setChatStatus])

  return (
    <div className="app-shell">
      <TopBar />

      <LeftPanel />

      <div className="workspace">
        <LeftDrawer />

        {/* UI-ERRORBOUNDARY: límites granulares por zona — un throw en el
            mapa o el chat NO debe blanquear toda la app (antes solo había uno
            en la raíz que sí lo hacía). */}
        <ErrorBoundary fallbackTitle="El chat tuvo un problema">
          <ChatDock />
        </ErrorBoundary>

        <CanvasTabs>
          <div className="map-container">
            {/* Motor de mapa único: MapLibre GL. Controles (zoom/atribución)
                nativos del propio mapa. */}
            <ErrorBoundary fallbackTitle="El mapa no pudo renderizarse">
              <MapLibreMap />
            </ErrorBoundary>
            <CompararCortina />
            <TiempoControl />

            <MapBasemapControl />
            <VistasControl />
            <SeleccionBar />
            <DibujoBar />
            <MapLegend />
            <MenuContextual />
            <PedidoMapaBar />

            <AgentsPipe />

            {/* FeaturePopup se renderiza FIJO en la pantalla (position:
                fixed con coords del click) — funciona sea cual sea el tab
                activo del CanvasTabs. */}
            <FeaturePopup />
          </div>
        </CanvasTabs>

        {/* Auditoría 2026-09-08 §5 (6): el panel HITL era la única zona sin
            boundary, y es la que recibe `message.data as ApprovalStatus` sin
            validar. Un campo degenerado del backend tumbaba la app entera —
            incluido el mapa y el chat, que no tienen nada que ver. */}
        {showApprovalPanel && (
          <ErrorBoundary fallbackTitle="El panel de aprobación no pudo renderizarse">
            <ApprovalPanel />
          </ErrorBoundary>
        )}
      </div>

      <StatusBar />

      <TweaksPanel />
    </div>
  )
}

export default App
