import { useState, useEffect } from 'react'
import {
  ShieldAlert,
  X,
  Check,
  XCircle,
  Edit3,
  AlertTriangle,
  Loader2,
  Play,
} from 'lucide-react'
import {
  useUIStore,
  usePendingApprovals,
  useSessionStore,
} from '@/stores'
import { useChatStore } from '@/stores'
import { approvalApi } from '@/services/api'
import { seguirTurnoRemoto } from '@/lib/runQuery'
import { logger } from '@/utils/logger'
import { buildImpactCells } from './ApprovalPanel.helpers'
import type { ApprovalStatus } from '@/types'

/**
 * HITL approval — rendered as a centered modal with an impact grid
 * (filas estimadas / costo / tiempo) and a SQL preview. Single-card
 * focus: shows the first pending approval; queues are surfaced by the
 * counter.
 */
export function ApprovalPanel() { // eslint-disable-line complexity -- deuda congelada (F1); partir, no subir
  const pendingApprovals = usePendingApprovals()
  const removePendingApproval = useUIStore((s) => s.removePendingApproval)
  const setShowApprovalPanel = useUIStore((s) => s.setShowApprovalPanel)
  const addMessage = useChatStore((s) => s.addMessage)
  const setChatStatus = useChatStore((s) => s.setChatStatus)
  // Fase 1 SEC-3: el backend ata cada aprobación a la sesión que la
  // originó. Si no hay sessionId no podemos submitear — se notifica al
  // usuario y se aborta.
  const sessionId = useSessionStore((s) => s.sessionId)

  const [processing, setProcessing] = useState<string | null>(null)
  const [editing, setEditing] = useState(false)
  const [modifiedContent, setModifiedContent] = useState('')

  const approval = pendingApprovals[0]

  // Reset edit state when the focused approval changes
  useEffect(() => {
    setEditing(false)
    setModifiedContent(approval?.content ?? '')
  }, [approval?.approval_id, approval?.content])

  const notifyMissingSession = () => {
    logger.error('[ApprovalPanel] No active sessionId — cannot submit approval')
    addMessage({
      id: `msg-${Date.now()}`,
      role: 'assistant',
      content: 'No hay sesión activa para aprobar. Recarga la página.',
      timestamp: new Date(),
      status: 'sent',
    })
    setChatStatus('error')
  }

  // Auditoría 2026-09-08 §5 (2): resuelta la aprobación, la espera vuelve a
  // ser del servidor — la petición HTTP del chat sigue viva hasta que el
  // grafo continúe. Si quedan más solicitudes en cola, el chip se queda
  // diciendo que espera al usuario, porque es verdad.
  const afterResolved = (a: ApprovalStatus) => {
    // V5 F3 (Chrome): leer la cola ACTUAL, no la del cierre. Si llegaba otra
    // solicitud mientras viajaba el POST de la primera, `pendingApprovals` del
    // cierre aún tenía 1 elemento → se cerraba el panel con la SEGUNDA
    // pendiente, invisible, y el turno quedaba colgado hasta el timeout HITL.
    if (useUIStore.getState().pendingApprovals.length !== 0) return
    setShowApprovalPanel(false)
    const chat = useChatStore.getState()
    // la consulta HTTP de esta pestaña (o un turno que ya se sigue) continúa
    if (chat.isLoading) {
      setChatStatus('searching')
      return
    }
    // F7 (auditoría): el servidor ya dijo que esta aprobación no retoma nada (rechazo de una
    // huérfana, turno perdido): nada queda «Consultando…».
    if (chat.aprobacionesSinTurno.includes(a.approval_id)) {
      setChatStatus('ready')
      return
    }
    const turnoId = a.turno_id ?? null
    // F7 (auditoría): su resultado ya llegó por el WebSocket ANTES que la respuesta de este POST
    // (p. ej. un rechazo, que termina el turno al instante): no hay nada que seguir.
    if (turnoId && chat.turnosTerminados.includes(turnoId)) {
      setChatStatus('ready')
      return
    }
    // Sin consulta en vuelo aquí (se recargó la pestaña, o el servidor se reinició): el turno
    // sigue en el servidor y su resultado llega por el WebSocket, ligado a su turno.
    seguirTurnoRemoto(turnoId)
  }

  // UI-HITL-CLOSE: los errores de submit (404 expirada, 403 sesión ajena, 500,
  // red) se tragaban con solo logger.error — el spinner se quitaba y no pasaba
  // nada visible, la app parecía colgada. Ahora se notifican en el chat.
  const notifyApprovalError = (verb: string, error: unknown) => {
    logger.error(`[ApprovalPanel] ${verb} error:`, error)
    addMessage({
      id: `msg-${Date.now()}`,
      role: 'assistant',
      content: `No se pudo ${verb} la solicitud. Revisa tu conexión e intenta de nuevo.`,
      timestamp: new Date(),
      status: 'sent',
    })
    setChatStatus('error')
  }

  const handleApprove = async (a: ApprovalStatus) => {
    if (!sessionId) {
      notifyMissingSession()
      return
    }
    setProcessing(a.approval_id)
    try {
      const result = await approvalApi.submit(a.approval_id, {
        action: 'approve',
        session_id: sessionId,
      })
      if (result.success) {
        removePendingApproval(a.approval_id)
        // NO añadir "Approved successfully" al chat — es ruido. La respuesta
        // real (con sus capas) llega como el turno de la consulta que esperaba.
        afterResolved(a)
      }
    } catch (error) {
      notifyApprovalError('aprobar', error)
    } finally {
      setProcessing(null)
    }
  }

  const handleReject = async (a: ApprovalStatus, reason?: string) => {
    if (!sessionId) {
      notifyMissingSession()
      return
    }
    setProcessing(a.approval_id)
    try {
      const result = await approvalApi.submit(a.approval_id, {
        action: 'reject',
        session_id: sessionId,
        reason: reason ?? 'Rechazado por el usuario',
      })
      if (result.success) {
        removePendingApproval(a.approval_id)
        // El reject NO debe agregar mensaje al chat — el responder del
        // grafo ya cerrará con un mensaje contextual ("Operación cancelada"
        // o el state actualizado). Evita el "Approved successfully" /
        // "Consulta rechazada" duplicado que veía el usuario.
        afterResolved(a)
      }
    } catch (error) {
      notifyApprovalError('rechazar', error)
    } finally {
      setProcessing(null)
    }
  }

  const handleModify = async (a: ApprovalStatus) => {
    if (!modifiedContent.trim()) return
    if (!sessionId) {
      notifyMissingSession()
      return
    }
    setProcessing(a.approval_id)
    try {
      const result = await approvalApi.submit(a.approval_id, {
        action: 'modify',
        session_id: sessionId,
        modified_content: modifiedContent,
      })
      if (result.success) {
        removePendingApproval(a.approval_id)
        setEditing(false)
        // Idéntico al handleApprove: NO posteamos mensaje "Modified
        // successfully" — la respuesta real del grafo (con el SQL
        // modificado ejecutado) llega como turno propio.
        afterResolved(a)
      }
    } catch (error) {
      notifyApprovalError('modificar', error)
    } finally {
      setProcessing(null)
    }
  }

  // Keyboard shortcuts: Esc cancel, Shift+Enter approve
  useEffect(() => {
    if (!approval) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        // #2 (audit 2026-06-13): Esc debe RECHAZAR (honesto), no solo ocultar
        // el panel dejando la solicitud "pendiente" en el backend y acumulando
        // la cola. Si se está editando, Esc cancela la edición (no rechaza todo).
        if (editing) {
          setEditing(false)
        } else {
          void handleReject(approval, 'Cerrado por el usuario (Esc)')
        }
      } else if (e.key === 'Enter' && e.shiftKey && !editing) {
        e.preventDefault()
        handleApprove(approval)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [approval, editing, sessionId])

  if (!approval) return null

  const isProcessing = processing === approval.approval_id

  // UI-HITL-CLOSE: cerrar el panel (backdrop / X / botón Cancelar) debe
  // RECHAZAR la solicitud —igual que Esc—, no solo ocultarla. Antes solo hacían
  // setShowApprovalPanel(false): el backend quedaba suspendido hasta el timeout
  // (~300s) y la cola de aprobaciones se acumulaba "fantasma".
  const handleClose = () => {
    if (isProcessing) return
    if (editing) { setEditing(false); return }
    void handleReject(approval, 'Cerrado por el usuario')
  }
  const hasWarnings = (approval.warnings?.length ?? 0) > 0
  const isHighRisk = approval.risk_level === 'high' || approval.impact_estimate?.risk === 'high'

  // Impact comes from the backend's `impact_estimate`. UI just renders.
  // See docs/design/frontend-backend-contract.md §3.
  //
  // Auditoría 2026-09-08 §5 (1): el grid se pintaba entero aunque el campo no
  // llegara nunca — tres "—" que se leían como cálculo vacío. Solo se pintan
  // las celdas con dato; si no hay ninguna, se DICE que no llegó estimación.
  const impactCells = buildImpactCells(approval.impact_estimate, isHighRisk)

  return (
    <div
      className="modal-backdrop"
      onClick={handleClose}
    >
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <ShieldAlert className="w-4 h-4" style={{ color: 'var(--warn)' }} />
          <h3>{approval.title || 'Confirmación requerida'}</h3>
          {(hasWarnings || isHighRisk) && (
            <span className="warn-badge">
              <AlertTriangle className="w-3 h-3" />
              {isHighRisk ? 'Alto riesgo' : 'Advertencias'}
            </span>
          )}
          {pendingApprovals.length > 1 && (
            <span className="chip" style={{ marginLeft: 4 }}>
              {pendingApprovals.length} pendientes
            </span>
          )}
          <button
            className="icon-btn close"
            onClick={handleClose}
            disabled={isProcessing}
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="modal-body">
          <p>
            {approval.description ?? (
              <>
                {/* Auditoría 2026-09-08 §5 (6): `content_type` viene tipado
                    como string obligatorio, pero websocket.py:559-572 tiene
                    una emisión degenerada que lo manda en null — y
                    `null.toUpperCase()` aquí tumbaba la app entera (el panel
                    no está dentro de ningún boundary). Se nombra la acción
                    solo si el servidor la declaró. */}
                El agente quiere ejecutar{' '}
                <strong>{approval.content_type ? approval.content_type.toUpperCase() : 'una acción'}</strong>.
                {' '}Revisa el contenido y aprueba para continuar.
              </>
            )}
          </p>

          {hasWarnings && (
            <ul
              style={{
                margin: '0 0 14px',
                padding: '10px 12px 10px 28px',
                borderRadius: 'var(--radius)',
                background: 'rgba(176, 122, 28, 0.08)',
                border: '1px solid rgba(176, 122, 28, 0.25)',
                color: 'var(--warn)',
                fontSize: 12.5,
              }}
            >
              {approval.warnings!.map((w, i) => (
                <li key={i} style={{ marginBottom: 2 }}>{w}</li>
              ))}
            </ul>
          )}

          {impactCells.length > 0 ? (
            <div className="impact-grid">
              {impactCells.map((cell) => (
                <div key={cell.key} className={`cell ${cell.warn ? 'warn' : ''}`}>
                  <div className="l">{cell.label}</div>
                  <div className="v">{cell.value}</div>
                </div>
              ))}
            </div>
          ) : (
            <p className="impact-missing">
              El servidor no envió estimación de impacto (filas, costo ni
              tiempo) para esta acción. Revisa el contenido antes de aprobar.
            </p>
          )}

          {editing ? (
            <textarea
              className="approval-code-editor"
              value={modifiedContent}
              onChange={(e) => setModifiedContent(e.target.value)}
              spellCheck={false}
              autoFocus
              style={{
                width: '100%',
                background: 'var(--bg-2)',
                border: '1px solid var(--border)',
                borderRadius: 'var(--radius)',
                marginBottom: 12,
              }}
            />
          ) : (
            <pre className="sql-preview">{approval.content}</pre>
          )}

          <p style={{ fontSize: 11.5, color: 'var(--text-mute)', margin: 0 }}>
            {/* R0.8 (auditoría 2026-07-26, AUD-15): contador explícito. El
                panel mostraba el SQL truncado a 500 caracteres sin elipsis ni
                indicación alguna, y ejecutaba el completo. Ahora `content`
                llega íntegro y se declara su tamaño, para que el revisor pueda
                notar si lo que lee no cuadra con lo que se le dice. */}
            <code className="mono">{(approval.content ?? '').length}</code> caracteres
            {' · '}
            {/* Auditoría 2026-09-08 §5 (1): decía `Sandbox gis_readonly` por
                defecto. El backend no manda `sandbox`, así que el panel
                afirmaba el confinamiento sin que nadie se lo hubiera dicho —
                en una UI de seguridad, un default disfrazado de observación.
                Ahora o se nombra el que llegó, o se declara que no llegó. */}
            {approval.sandbox
              ? <>Sandbox <code className="mono">{approval.sandbox}</code></>
              : <span className="unknown-field">sandbox no declarado por el servidor</span>}
            {approval.policy && (
              <>
                {' · '}política <code className="mono">{approval.policy}</code>
              </>
            )}
            {approval.action_type && (
              <>
                {' · '}acción <code className="mono">{approval.action_type}</code>
              </>
            )}
          </p>
        </div>

        <div className="modal-foot">
          <div className="left">
            {editing ? 'Edita y ejecuta · Esc para cancelar' : 'Shift + Enter para aprobar · Esc para cerrar'}
          </div>

          {editing ? (
            <>
              <button
                className="gc-btn"
                onClick={() => { setEditing(false); setModifiedContent(approval.content) }}
                disabled={isProcessing}
              >
                Cancelar edición
              </button>
              <button
                className="gc-btn gc-btn-primary"
                onClick={() => handleModify(approval)}
                disabled={isProcessing || !modifiedContent.trim()}
              >
                {isProcessing ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Play className="w-3.5 h-3.5" />}
                Ejecutar modificado
              </button>
            </>
          ) : (
            <>
              <button
                className="gc-btn"
                onClick={handleClose}
                disabled={isProcessing}
              >
                Cancelar
              </button>
              <button
                className="gc-btn gc-btn-danger"
                onClick={() => handleReject(approval)}
                disabled={isProcessing}
              >
                <XCircle className="w-3.5 h-3.5" />
                Rechazar
              </button>
              <button
                className="gc-btn"
                onClick={() => setEditing(true)}
                disabled={isProcessing}
              >
                <Edit3 className="w-3.5 h-3.5" />
                Editar
              </button>
              <button
                className="gc-btn gc-btn-primary"
                onClick={() => handleApprove(approval)}
                disabled={isProcessing}
              >
                {isProcessing ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Check className="w-3.5 h-3.5" />}
                Aprobar y ejecutar
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
