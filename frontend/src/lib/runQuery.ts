/**
 * Un solo cliente de consultas.
 *
 * Auditoría 2026-09-08 §5 (3): había DOS. El bueno vivía dentro de
 * `ChatDock.sendQuery`; el otro, `CanvasTabs.refreshLastQuery`, era una copia
 * degradada — sin `map_context`, sin `AbortSignal`, sin `target_layer_id` y
 * sin el atajo de zoom en cliente. En una consulta de simbología el botón
 * "Refrescar" llamaba a `addLayer` y DUPLICABA la capa en vez de re-estilarla:
 * exactamente el bug que `ChatDock` documenta como arreglado desde el
 * 2026-06-13. Dos caminos para lo mismo es una promesa de que uno se queda
 * atrás; este módulo elimina el segundo en vez de parchearlo.
 *
 * Vive fuera de React a propósito: lo llaman dos componentes hermanos
 * (`ChatDock` y `CanvasTabs`) y el `AbortController` tiene que ser el mismo
 * para los dos — si cada uno tuviera el suyo, el botón "Detener" del chat no
 * cancelaría la consulta que arrancó el canvas. Zustand permite leer y actuar
 * sobre los stores con `getState()` sin suscripción, que es justo lo que hace
 * falta aquí.
 */
import { esperarAjustesEnVuelo } from '@/lib/pendientes'
import { validarRespuesta } from '@/contracts'
import { aplicarArtefactos } from '@/lib/aplicarArtefactos'
import { useOperaciones } from '@/lib/operaciones'
import { queryApi } from '@/services/api'
import {
  useChatStore,
  useSessionStore,
  useUIStore,
} from '@/stores'
import { useResultsStore } from '@/stores/resultsStore'
import type { ChatStatus } from '@/stores/chatStore'
import { usePedidoMapa } from '@/lib/pedidoMapa'
import { buildMapContext, type Alcance } from '@/utils/mapContext'
import { logger } from '@/utils/logger'
import type { ChatMessage } from '@/types'

/**
 * La consulta HTTP en vuelo. Solo puede haber una: `isLoading` corta la
 * segunda antes de empezar.
 */
let activeController: AbortController | null = null
/**
 * F7 (auditoría): el mensaje del asistente de la consulta HTTP en vuelo y el turno del servidor que
 * la atiende (llega en el `processing` del WebSocket). Liga a ESE mensaje lo que después llegue de
 * ese turno por el WebSocket.
 */
let enVuelo: { mensaje: string; turno: string | null } | null = null
// F7 (V5): la página se está yendo (recarga o cierre). La consulta en vuelo muere con ella, pero su
// turno sigue en el servidor: no es un error y su resultado llegará por el WebSocket.
let descargando = false
if (typeof window !== 'undefined') {
  window.addEventListener('beforeunload', () => { descargando = true })
  window.addEventListener('pagehide', () => { descargando = true })
}

/**
 * Aborta la consulta HTTP en curso, si la hay. La cancelación de la task del
 * grafo va aparte, por WebSocket, desde el componente que tiene la sesión.
 *
 * F7 (auditoría): sin consulta HTTP pero siguiendo un turno remoto, «Detener» deja de esperarlo.
 * Su resultado puede perderse sin que el WebSocket se reconecte (la réplica que lo corría murió,
 * Redis no publicó el aviso) y la entrada quedaba bloqueada hasta recargar. No depende de la
 * respuesta del servidor: con varios procesos no puede confirmar que nadie tenía la tarea.
 */
export function abortActiveQuery(): void {
  if (activeController) {
    activeController.abort()
    return
  }
  const chat = useChatStore.getState()
  if (!chat.turnoRemoto) return
  chat.addMessage({
    id: idMensaje('aviso'), role: 'assistant', timestamp: new Date(), status: 'sent',
    content: 'Dejé de esperar el resultado de la consulta retomada; si sigue en el servidor y termina, '
      + 'su resultado aparecerá aquí.',
  })
  terminarTurnoRemoto('ready')
}

/** Solo para tests: deja el módulo sin petición en vuelo. */
export function resetActiveQuery(): void {
  activeController = null
  enVuelo = null
}

const now = () => Date.now()
let secuencia = 0
/** Ids únicos aunque dos mensajes se creen en el mismo milisegundo (F7, auditoría). */
export const idMensaje = (prefijo: string) => `${prefijo}-${now()}-${++secuencia}`

/**
 * Una respuesta del agente → chat, panel de resultados, mapa e historial. La usan la consulta
 * HTTP y el resultado de un turno REANUDADO tras un reinicio del servidor (F7, E7.1), que llega
 * por WebSocket porque la petición HTTP original murió con el proceso.
 */
function aplicarRespuesta(
  response: unknown, consulta: string, assistantMessageId: string, marcar = true, historial = true,
) {
  const { updateMessage, addQueryToHistory } = useChatStore.getState()
  const contrato = validarRespuesta(response)

  updateMessage(assistantMessageId, {
    content: contrato.message || 'Consulta procesada.',
    status: 'sent',
    response: contrato,
  })

  // S4.3: los artefactos del turno (tabla, gráfico, estadísticas…) al panel de
  // resultados, con historial; las capas y órdenes al mapa.
  useResultsStore.getState().registrar({
    id: contrato.query_id,
    consulta,
    en: new Date(),
    artefactos: contrato.artifacts,
    sql: contrato.sql ?? null,
    mensaje: contrato.message ?? '',
  })
  const capasDelTurno = aplicarArtefactos(contrato, consulta)
  if (capasDelTurno.length) updateMessage(assistantMessageId, { capas: capasDelTurno })
  // Lo que el agente acaba de hacer con esta respuesta ya lo sabe: el «desde tu
  // última respuesta» del próximo turno empieza aquí. (No si otra consulta está en vuelo: su
  // registro de operaciones empezó con ella y no se corta a la mitad.)
  if (marcar) useOperaciones.getState().marcarTurno()

  if (historial) addQueryToHistory(consulta, contrato.status === 'completed')
  return contrato
}

/**
 * F7 (auditoría): un turno corre en el servidor sin petición HTTP de esta pestaña (reanudado tras
 * un reinicio, o aprobado tras recargar) y su resultado llegará por WebSocket. Es una consulta en
 * vuelo: bloquea otra (`isLoading`), muestra «Detener» y el chip dice que se consulta — salvo que
 * ya haya una consulta HTTP en vuelo, que es la dueña del chip.
 */
export function seguirTurnoRemoto(turnoId: string | null): void {
  const chat = useChatStore.getState()
  const bloquea = activeController === null
  chat.setTurnoRemoto({ id: turnoId, bloquea })
  if (bloquea) {
    chat.setLoading(true)
    chat.setChatStatus('searching')
  }
}

/** El turno remoto terminó (o el servidor dijo que no continúa): suelta lo que puso. */
export function terminarTurnoRemoto(estado: ChatStatus): void {
  const chat = useChatStore.getState()
  chat.setTurnoRemoto(null)
  if (activeController !== null) return  // la consulta HTTP en vuelo manda en el chip
  chat.setLoading(false)
  chat.setChatStatus(estado)
}

/** Suelta el turno remoto que se seguía, salvo que el resultado sea de OTRO turno (ese sigue). */
function soltarTurnoSeguido(turnoId: string | null | undefined, estado: ChatStatus): void {
  const t = useChatStore.getState().turnoRemoto
  if (t && t.id && turnoId && t.id !== turnoId) return
  terminarTurnoRemoto(estado)
}

/**
 * F7 (E7.1 y auditoría): la respuesta de un turno cuya petición HTTP ya no existe (el servidor se
 * reinició y lo retomó, o la pestaña se recargó) llega por el WebSocket. Se pinta como cualquier
 * respuesta, sin pisar otra consulta que esté en vuelo. Con `mensajeId` (la consulta de esta
 * pestaña que se cortó), en SU mensaje y sin volver a apuntarla en el historial.
 */
export function aplicarRespuestaReanudada(
  respuesta: unknown, consulta: string, turnoId: string | null = null, mensajeId: string | null = null,
): void {
  const { addMessage, updateMessage } = useChatStore.getState()
  const id = mensajeId ?? idMensaje('reanudada')
  if (!mensajeId) addMessage({ id, role: 'assistant', content: '', timestamp: new Date(), status: 'sending' })
  try {
    aplicarRespuesta(respuesta, consulta, id, activeController === null, mensajeId === null)
    soltarTurnoSeguido(turnoId, 'ready')
  } catch (error) {
    logger.error('Respuesta reanudada fuera de contrato:', error)
    updateMessage(id, { content: 'No pude mostrar el resultado de la consulta retomada.', status: 'error' })
    soltarTurnoSeguido(turnoId, 'error')
  }
}

/** Lo que el servidor manda por WebSocket sobre un turno sin petición HTTP (F7). */
interface AvisoTurno {
  status?: string
  reanudada?: boolean
  message?: string
  turno_id?: string | null
  approval_id?: string
  consulta?: string
  turnos_en_curso?: string[]
}

interface ResultadoTurno {
  entrega?: string
  reanudada?: boolean
  turno_id?: string | null
  consulta?: string
  respuesta?: unknown
  error?: string
}

/**
 * F7 (auditoría): el mensaje del asistente del turno que se interrumpió, si quedó en error. Antes
 * se reescribía el último error del chat, fuera del turno que fuera. Se busca por el `turno_id`
 * (el texto no es único: tras un «Bad Gateway» el usuario reenvía la misma consulta, y su error
 * real no debe reescribirse). Sin ese vínculo (se recargó la pestaña), por el texto solo si hay
 * UN candidato; si hay duda o no se encuentra, no se toca nada.
 */
function mensajeDelTurno(
  messages: ChatMessage[], mensajeDeTurno: Record<string, string>, d: AvisoTurno,
): ChatMessage | undefined {
  const vinculado = d.turno_id ? mensajeDeTurno[d.turno_id] : undefined
  if (vinculado) {
    const m = messages.find((x) => x.id === vinculado)
    return m?.status === 'error' ? m : undefined
  }
  if (!d.consulta) return undefined
  const candidatos = messages.filter((m, i) =>
    i > 0 && m.role === 'assistant' && m.status === 'error'
    && messages[i - 1].role === 'user' && messages[i - 1].content === d.consulta)
  return candidatos.length === 1 ? candidatos[0] : undefined
}

function avisoDeReanudacion(d: AvisoTurno): void {
  const chat = useChatStore.getState()
  // La petición original murió con el proceso y el chat la mostró como error: se dice qué fue.
  const interrumpido = mensajeDelTurno(chat.messages, chat.mensajeDeTurno, d)
  if (interrumpido) {
    chat.updateMessage(interrumpido.id, {
      content: 'Se interrumpió: el servidor se reinició mientras esperaba tu aprobación.', status: 'sent',
    })
  }
  chat.addMessage({
    id: idMensaje('aviso'), role: 'assistant', content: d.message ?? '', timestamp: new Date(), status: 'sent',
  })
  if (d.reanudada) {
    seguirTurnoRemoto(d.turno_id ?? null)
    return
  }
  // No continúa (rechazada, o sin turno que retomar): nada queda «Consultando…».
  if (d.approval_id) chat.marcarAprobacionSinTurno(d.approval_id)
  const t = chat.turnoRemoto
  if (t && (!t.id || t.id === d.turno_id)) terminarTurnoRemoto('ready')
}

/**
 * Al (re)conectar: si el turno que se seguía ya no está en curso, su resultado no llegó.
 * F7 (auditoría, deuda aceptada): el servidor cierra el registro del turno justo ANTES de entregar
 * su resultado; una reconexión en ese instante dice «no llegó» y el resultado aparece después
 * igual (se aplica: es cosmético).
 */
function comprobarTurnoRemoto(enCurso: string[]): void {
  const t = useChatStore.getState().turnoRemoto
  if (!t) return
  const sigue = t.id ? enCurso.includes(t.id) : enCurso.length > 0
  if (sigue) return
  useChatStore.getState().addMessage({
    id: idMensaje('aviso'), role: 'assistant', timestamp: new Date(), status: 'error',
    content: 'La consulta terminó mientras no había conexión con el servidor y su resultado no llegó a esta '
      + 'pestaña. Vuelve a enviarla si lo necesitas.',
  })
  terminarTurnoRemoto('error')
}

/**
 * F7: los mensajes del WebSocket que tratan de un turno sin petición HTTP de esta pestaña
 * (reanudado tras un reinicio, aprobado tras recargar). Devuelve true si el mensaje era de esos.
 */
/**
 * El `processing` de un turno con `turno_id` mientras esta pestaña tiene una consulta HTTP en
 * vuelo: es el turno de ESA consulta (el primero que llega; el retomado que se sigue, no).
 */
function vincularTurnoEnVuelo(turnoId: string): void {
  if (!enVuelo || enVuelo.turno) return
  if (useChatStore.getState().turnoRemoto?.id === turnoId) return
  enVuelo.turno = turnoId
  useChatStore.getState().vincularMensajeATurno(turnoId, enVuelo.mensaje)
}

/**
 * El resultado por WebSocket de un turno que arrancó una consulta HTTP de esta pestaña que se cortó
 * («Detener», un 504 del proxy): el turno siguió en el servidor. Va a SU mensaje, que ya decía
 * «cancelada» o el error; antes aparecía como una segunda respuesta (y otra vez en el historial).
 */
function mensajeCortado(turnoId: string | null | undefined): string | null {
  if (!turnoId) return null
  const chat = useChatStore.getState()
  const id = chat.turnosCortados.includes(turnoId) ? chat.mensajeDeTurno[turnoId] : undefined
  if (id && chat.messages.some((m) => m.id === id)) return id
  // F7 (V5): tras recargar, el vínculo en memoria se perdió; queda el que se guardó con el mensaje
  return chat.messages.find((m) => m.turnoPendiente === turnoId)?.id ?? null
}

export function manejarAvisoDeTurno(tipo: string, data: unknown): boolean { // eslint-disable-line complexity -- deuda congelada (F1); partir, no subir
  if (tipo === 'status') {
    const d = (data ?? {}) as AvisoTurno
    if (d.status === 'processing' && d.turno_id) {
      vincularTurnoEnVuelo(d.turno_id)
      return false  // el pipeline de agentes también lo escucha
    }
    if (d.status === 'connected' && Array.isArray(d.turnos_en_curso)) {
      comprobarTurnoRemoto(d.turnos_en_curso)
      return true
    }
    if (d.status === 'reanudacion') {
      avisoDeReanudacion(d)
      return true
    }
    return false
  }
  if (tipo === 'result') {
    const d = (data ?? {}) as ResultadoTurno
    // el `result` que solo cierra el chip de pipeline de una consulta HTTP viva no es de estos
    if (d.entrega !== 'ws' && !d.reanudada) return false
    // F7 (V5): el servidor manda también por WS el resultado de un turno con aprobación; si la
    // consulta HTTP de ESTA pestaña sigue viva, ella lo aplica (no se pinta dos veces).
    if (d.turno_id && enVuelo?.turno === d.turno_id) return true
    const chat = useChatStore.getState()
    // terminado: una aprobación de este turno que se resuelva después no lo vuelve a seguir
    if (d.turno_id) chat.marcarTurnoTerminado(d.turno_id)
    const propio = mensajeCortado(d.turno_id)
    if (propio) chat.updateMessage(propio, { turnoPendiente: undefined })
    if (d.respuesta) {
      aplicarRespuestaReanudada(d.respuesta, d.consulta ?? '', d.turno_id ?? null, propio)
    } else {
      const content = d.error ?? 'No se pudo retomar la consulta.'
      if (propio) chat.updateMessage(propio, { content, status: 'error' })
      else chat.addMessage({ id: idMensaje('aviso'), role: 'assistant', content, timestamp: new Date(), status: 'error' })
      soltarTurnoSeguido(d.turno_id, 'error')
    }
    return true
  }
  return false
}

/**
 * Ejecuta una consulta de lenguaje natural de punta a punta: mensajes del
 * chat, estado del chip, capas del mapa, historial y apertura del panel HITL.
 *
 * No lanza: todo error termina como mensaje visible en el chat.
 */
export async function runQuery(text: string, alcance: Alcance = {}): Promise<void> { // eslint-disable-line complexity -- deuda congelada (F1); partir, no subir
  const trimmed = text.trim()
  const chat = useChatStore.getState()
  const sessionId = useSessionStore.getState().sessionId
  if (!trimmed || chat.isLoading || !sessionId) return
  // FH.9: escribir otra cosa mientras el agente esperaba algo en el mapa = seguir sin responderle
  if (!alcance.respuestaMapa) usePedidoMapa.getState().limpiar()

  const {
    addMessage,
    updateMessage,
    setLoading,
    setChatStatus,
    addQueryToHistory,
  } = chat
  const ui = useUIStore.getState()

  const userMessage: ChatMessage = {
    id: idMensaje('msg'),
    role: 'user',
    // FH.9: una respuesta en el mapa se ve como tal («📍 Punto marcado…»); viaja la consulta original
    content: alcance.etiqueta ?? trimmed,
    timestamp: new Date(),
    status: 'sent',
  }
  addMessage(userMessage)

  // FH.1: ya no hay atajo de zoom por palabras clave en el cliente: «acércate a los
  // lotes» lo decide el agente y lo ejecuta con una orden `zoom_to` (queda en el
  // registro y se deshace como cualquier otra operación).

  setLoading(true)
  // Auditoría 2026-09-08 §5 (2): el chip pasa a "Consultando…" aquí, que es
  // el instante exacto en que el cliente sabe que hay una petición en vuelo.
  setChatStatus('searching')

  const assistantMessageId = `${idMensaje('msg')}-assistant`
  addMessage({
    id: assistantMessageId,
    role: 'assistant',
    content: '',
    timestamp: new Date(),
    status: 'sending',
  })

  const abortController = new AbortController()
  activeController = abortController
  enVuelo = { mensaje: assistantMessageId, turno: null }

  // EH.5: un ajuste manual que aún viaja (p. ej. la rampa recién elegida) entra en ESTE turno
  await esperarAjustesEnVuelo()
  // FH.1: lo que pasó en el mapa hasta aquí viaja en ESTE turno; lo siguiente, en el próximo.
  const mapContext = buildMapContext(alcance)
  useOperaciones.getState().marcarTurno()

  try {
    const response = await queryApi.process({
      query: trimmed,
      session_id: sessionId,
      // Fase A: adjuntar el estado del mapa para que el agente entienda
      // "esta capa", "el predio seleccionado", "esta zona".
      map_context: mapContext,
    }, abortController.signal)

    // F4 (S4.1): frontera tipada. Una respuesta fuera de contrato se rechaza aquí
    // con el motivo (va al catch → mensaje de error), no se pinta a medias.
    const contrato = aplicarRespuesta(response, trimmed, assistantMessageId)

    if (contrato.requires_approval && contrato.pending_approval_id) {
      ui.setShowApprovalPanel(true)
      // El chip no puede decir "Listo" con un modal de aprobación abierto
      // esperando al humano. (Con HITL bloqueante este camino casi no se
      // recorre: la respuesta HTTP no vuelve hasta resolver, y quien abre el
      // panel es el `approval_request` del WebSocket en App.tsx.)
      setChatStatus('waiting_approval')
    } else {
      setChatStatus('ready')
    }
  } catch (error) {
    // FE4: si el usuario pulsó "Detener", la petición se abortó — no es
    // un error real.
    // F7 (auditoría): el turno puede seguir en el servidor; si su resultado llega por el
    // WebSocket, irá a ESTE mensaje (no como una respuesta aparte).
    if (enVuelo?.turno) useChatStore.getState().marcarTurnoCortado(enVuelo.turno)
    if (descargando) {
      updateMessage(assistantMessageId, {
        content: 'La consulta sigue en el servidor; su resultado aparecerá aquí.',
        status: 'sent',
        ...(enVuelo?.turno ? { turnoPendiente: enVuelo.turno } : {}),
      })
    } else if (error instanceof DOMException && error.name === 'AbortError') {
      updateMessage(assistantMessageId, {
        content: 'Consulta cancelada.',
        status: 'sent',
      })
      setChatStatus('ready')
    } else {
      logger.error('Query error:', error)
      updateMessage(assistantMessageId, {
        content: `Error: ${error instanceof Error ? error.message : 'Error procesando consulta'}`,
        status: 'error',
      })
      addQueryToHistory(trimmed, false)
      setChatStatus('error')
    }
  } finally {
    activeController = null
    enVuelo = null
    const t = useChatStore.getState().turnoRemoto
    if (t) {
      // F7 (auditoría): un turno remoto empezó mientras esta consulta estaba en vuelo y sigue: ahora
      // el chip y el «Detener» son suyos (si no, otra consulta podía solaparse con él).
      useChatStore.getState().setTurnoRemoto({ ...t, bloquea: true })
      if (useChatStore.getState().chatStatus !== 'waiting_approval') setChatStatus('searching')
    } else {
      setLoading(false)
    }
  }
}
