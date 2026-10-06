/* eslint-disable max-lines -- deuda congelada (F1 del plan de calidad): partir por responsabilidad, no crecer */
import { useState, useRef, useEffect } from 'react'
import {
  Send,
  Loader2,
  AlertCircle,
  ChevronRight,
  RefreshCw,
  CheckCircle,
  XCircle,
  Paperclip,
  Mic,
  Layers,
  Square,
} from 'lucide-react'
import { wsService } from '@/services/websocket'
import { useSessionId } from '@/stores'
import { useMapStore } from '@/stores'
import {
  useUIStore,
} from '@/stores'
import {
  useChatStore,
  useMessages,
  useIsLoading,
  useChatStatus,
  useRetryInfo,
} from '@/stores'
import { cargarDescubierto } from '@/lib/cargaDescubierta'
import { nombreGeometria } from './discoveryPanel.helpers'
import { logger } from '@/utils/logger'
import { runQuery, abortActiveQuery } from '@/lib/runQuery'
import { candidatas, filtrar, insertar, menciónEnCurso, vigentes, type Candidata, type Mencion } from '@/lib/menciones'
import { ChipsDeAlcance, ListaMenciones } from '@/components/Alcance'
import type { Artifact } from '@/contracts'
import type { ChatMessage, FoundService } from '@/types'
import type { DiscoveryLayer, HubItem } from '@/types/discovery'
import { TextoRespuesta } from './TextoRespuesta'
import { useSesionAuth } from '@/hooks/useSesionAuth'

// Sugerencias genéricas de partida. No están atadas a entidades
// específicas — el usuario verá ejemplos reales de su base en el
// drawer de "Datos" (catálogo dinámico desde /metadata/entities).
const SUGERENCIAS_INICIALES = [
  { texto: '¿Qué entidades hay disponibles?' },
  { texto: 'Muéstrame las capas con geometría' },
  // F6: calcular es de analista; a un visor no se le ofrece (V5 F6)
  { texto: 'Calcula un buffer de 500 m', calcula: true },
  { texto: 'Cuenta features por categoría' },
]

/** «40 resultados · capa · gráfico»: qué dejó el turno, en una línea. */
function resumenDeArtefactos(artefactos: Artifact[]): string | null {
  const partes: string[] = []
  const tabla = artefactos.find((a) => a.kind === 'table')
  const capas = artefactos.filter((a) => a.kind === 'layer')
  if (tabla && tabla.kind === 'table') partes.push(`${(tabla.total_rows ?? tabla.preview.length).toLocaleString('es-CO')} resultados`)
  else {
    const n = capas.reduce((acc, a) => acc + (a.kind === 'layer' ? a.layer.feature_count ?? 0 : 0), 0)
    if (n) partes.push(`${n.toLocaleString('es-CO')} resultados`)
  }
  if (capas.length) partes.push(capas.length === 1 ? 'capa' : `${capas.length} capas`)
  if (artefactos.some((a) => a.kind === 'chart')) partes.push('gráfico')
  if (artefactos.some((a) => a.kind === 'stats')) partes.push('estadísticas')
  if (artefactos.some((a) => a.kind === 'report')) partes.push('informe')
  return partes.length ? partes.join(' · ') : null
}

export function ChatDock() { // eslint-disable-line complexity -- deuda congelada (F1); partir, no subir
  const esVisor = useSesionAuth().usuario?.rol === 'viewer'
  const SUGGESTIONS = SUGERENCIAS_INICIALES.filter((s) => !(esVisor && s.calcula)).map((s) => s.texto)
  const [input, setInput] = useState('')
  // FH.4: menciones `@` elegidas y si este mensaje va sin la selección del mapa.
  const [menciones, setMenciones] = useState<Mencion[]>([])
  const [sinSeleccion, setSinSeleccion] = useState(false)
  const [lista, setLista] = useState<{ inicio: number; opciones: Candidata[]; activa: number } | null>(null)
  const firmaSeleccion = useMapStore((s) => s.layers.map((l) => `${l.id}:${l.seleccion?.count ?? 0}`).join('|'))
  // otra selección = otra decisión: el chip vuelve a aparecer
  useEffect(() => setSinSeleccion(false), [firmaSeleccion])
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const sessionId = useSessionId()

  const messages = useMessages()
  const isLoading = useIsLoading()
  const chatStatus = useChatStatus()
  const retryInfo = useRetryInfo()
  const addMessage = useChatStore((s) => s.addMessage)

  // servicio de una tarjeta con varias capas: el usuario elige cuál (no se adivina la 0)
  const [eleccion, setEleccion] = useState<{ svc: FoundService; capas: DiscoveryLayer[] } | null>(null)

  const toggleDrawer = useUIStore((s) => s.toggleDrawer)

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  // E3 (audit 2026-06-13): cargar el servicio CLICADO directamente por su item
  // estable (no por ordinal contra found_services de sesión, que podía ser de
  // otra búsqueda → cargaba el servicio equivocado). Mismo path que el panel
  // "Datos" (discoveryApi.load), que resuelve por el item, no por índice.
  const handleLoadService = async (svc: FoundService, capa?: DiscoveryLayer) => {
    try {
      const item = {
        id: svc.url,
        source: 'arcgis',
        org: '',
        title: svc.name,
        description: svc.description ?? '',
        service_type: svc.type,
        service_url: svc.url,
      } as unknown as HubItem
      // #35: pasamos la sesión. Si el plan quedó pausado (cadena "busca X,
      // cárgalas y píntalas de rojo"), el backend despacha el pintado pendiente
      // sobre esta capa y devuelve la simbología ya aplicada. Misma carga que el
      // panel «Descubrir»: completa, por teselas si es grande, y sin adivinar la capa 0.
      const carga = await cargarDescubierto(item, { sessionId: sessionId ?? undefined, capa })
      if (carga.estado === 'elegir_capa') {
        setEleccion({ svc, capas: carga.capas })
        return
      }
      setEleccion(null)
      addMessage({
        id: `msg-${Date.now()}-load-svc`,
        role: 'assistant',
        content: carga.mensaje,
        timestamp: new Date(),
        status: 'sent',
      })
    } catch (e) {
      // #3 (audit 2026-06-14): feedback HONESTO al usuario (agentic, no fallbacks)
      // — un fallo de carga no se traga en el log: se comunica en el chat.
      logger.error('[chat] load service failed', e)
      addMessage({
        id: `msg-${Date.now()}-load-err`,
        role: 'assistant',
        content: `No pude cargar «${svc.name}»: ${e instanceof Error ? e.message : 'error desconocido'}`,
        timestamp: new Date(),
        status: 'error',
      })
    }
  }

  // Auditoría 2026-09-08 §5 (3): el cuerpo de `sendQuery` vivía aquí y
  // `CanvasTabs` tenía su propia copia degradada. Ahora los dos llaman a
  // `lib/runQuery.ts` — un solo camino, con map_context, abort y
  // target_layer_id.
  const handleSubmit = async (e?: React.FormEvent) => {
    e?.preventDefault()
    const text = input.trim()
    if (!text) return
    const alcance = { menciones: vigentes(text, menciones), sinSeleccion }
    setInput('')
    setMenciones([])
    setSinSeleccion(false)
    setLista(null)
    await runQuery(text, alcance)
  }

  const getStatusChipClass = () => {
    switch (chatStatus) {
      case 'searching':        return 'status-chip-searching'
      case 'waiting_approval': return 'status-chip-loading'
      case 'error':            return 'status-chip-error'
      default:                 return 'status-chip-ready'
    }
  }

  const getStatusLabel = () => {
    switch (chatStatus) {
      case 'searching':        return 'Consultando…'
      case 'waiting_approval': return 'Esperando tu aprobación'
      case 'error':            return 'Error'
      default:                 return 'Listo'
    }
  }

  const formatTs = (date: Date) => {
    const d = date instanceof Date ? date : new Date(date)
    return d.toTimeString().slice(0, 8)
  }

  /** Tras cada tecla: ¿se está escribiendo un `@…`? → la lista de lo que se puede mencionar. */
  const alEscribir = (valor: string, cursor: number) => {
    setInput(valor)
    setMenciones((ms) => vigentes(valor, ms))
    const enCurso = menciónEnCurso(valor, cursor)
    if (!enCurso) return setLista(null)
    const opciones = filtrar(candidatas(useMapStore.getState().layers), enCurso.parcial)
    setLista(opciones.length ? { inicio: enCurso.inicio, opciones, activa: 0 } : null)
  }

  const elegirMencion = (c: Candidata) => {
    const ta = textareaRef.current
    if (!lista || !ta) return
    const { texto, cursor } = insertar(input, lista.inicio, ta.selectionStart ?? input.length, c)
    setInput(texto)
    setMenciones((ms) => [...ms, { tipo: c.tipo, layer_id: c.layer_id, texto: c.texto, ...(c.campo ? { campo: c.campo } : {}) }])
    setLista(null)
    requestAnimationFrame(() => { ta.focus(); ta.setSelectionRange(cursor, cursor) })
  }

  const quitarMencion = (m: Mencion) => {
    setMenciones((ms) => ms.filter((x) => x !== m))
    setInput((t) => t.replace(`${m.texto} `, '').replace(m.texto, ''))
  }

  const handleSuggestion = (text: string) => {
    setInput(text)
    textareaRef.current?.focus()
  }

  const renderMessage = (message: ChatMessage) => {
    const isUser = message.role === 'user'
    const isError = message.status === 'error'
    const isSending = message.status === 'sending'

    if (isUser) {
      return (
        <div key={message.id} className="msg msg-user">
          <div className="msg-head">
            <span className="who user">Tú</span>
            <span className="ts mono tabular">{formatTs(message.timestamp)}</span>
          </div>
          <div className="bubble-user">{message.content}</div>
        </div>
      )
    }

    return (
      <div key={message.id} className="msg msg-assist">
        <div className="msg-head">
          <span className="who">Copilot</span>
          {!isSending && !isError && (
            <span className="chip ok">
              <span className="dot" />
              completado
            </span>
          )}
          {isError && (
            <span className="chip danger">
              <AlertCircle className="w-3 h-3" />
              error
            </span>
          )}
          <span className="ts mono tabular">{formatTs(message.timestamp)}</span>
        </div>

        {isSending ? (
          <div className="thinking">
            <span className="spin" />
            <span className="cursor">Procesando consulta…</span>
          </div>
        ) : (
          <>
            <div className={isError ? 'bubble-error' : 'bubble-assist'}>
              {message.response?.correction && (
                <div className="chip warn" style={{ marginBottom: 8 }}>
                  <AlertCircle className="w-3 h-3" />
                  Auto-corregido ({message.response.correction.corrections_applied})
                </div>
              )}
              <TextoRespuesta texto={message.content} delTurno={message.capas} />

              {(() => {
                // F4: las tarjetas de servicios son un artefacto `services` de la respuesta.
                const servicios = message.response?.artifacts.find((a) => a.kind === 'services')
                return servicios && servicios.kind === 'services' ? (
                  <>
                    <FoundServicesGrid
                      services={servicios.items}
                      onSelect={(svc) => handleLoadService(svc)}
                      loading={isLoading}
                    />
                    {eleccion && servicios.items.some((s) => s.url === eleccion.svc.url) && (
                      <div className="disc-capas" role="group" aria-label="Capas del servicio">
                        <span className="disc-capas-titulo">
                          «{eleccion.svc.name}» tiene {eleccion.capas.length} capas: ¿cuál cargar?
                        </span>
                        {eleccion.capas.map((c) => (
                          <button key={c.id} type="button" className="disc-capa" disabled={isLoading}
                            onClick={() => handleLoadService(eleccion.svc, c)}>
                            <span>{c.nombre}</span>
                            <span className="disc-capa-geom">{nombreGeometria(c.tipo_geometria)}</span>
                          </button>
                        ))}
                      </div>
                    )}
                  </>
                ) : null
              })()}

              {(() => {
                // Solo el conteo: los resultados ya se ven en el mapa y en el panel.
                const resumen = message.response ? resumenDeArtefactos(message.response.artifacts) : null
                return resumen ? (
                  <div
                    data-testid="resumen-turno"
                    style={{
                      marginTop: 8,
                      paddingTop: 8,
                      borderTop: '1px solid var(--border)',
                      fontSize: 12,
                      color: 'var(--text-mute)',
                    }}
                  >
                    {resumen}
                  </div>
                ) : null
              })()}

              {(() => {
                // FH.8: siguientes pasos que propuso el LLM; solo bajo la ÚLTIMA respuesta
                // (las de antes describen un mapa que ya cambió). Clic = enviarlo.
                const sugerencias = message.response?.suggestions ?? []
                const ultima = messages[messages.length - 1]?.id === message.id
                return ultima && sugerencias.length ? (
                  <div className="sugerencias" data-testid="sugerencias">
                    {sugerencias.map((s) => (
                      <button key={s} type="button" className="sugerencia" disabled={isLoading}
                              onClick={() => void runQuery(s)}>{s}</button>
                    ))}
                  </div>
                ) : null
              })()}
            </div>
          </>
        )}
      </div>
    )
  }

  return (
    <aside className="chat">
      <div className="chat-head">
        <h2>Consulta</h2>
        <span className={`status-chip ${getStatusChipClass()}`}>
          {getStatusLabel()}
        </span>
        {sessionId && (
          <span className="session-id">#{sessionId.slice(-6)}</span>
        )}
      </div>

      {/* Retry indicator */}
      {retryInfo?.show && (
        <div
          style={{
            margin: '8px 16px 0',
            padding: '8px 10px',
            borderRadius: 6,
            fontSize: 12,
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            background:
              retryInfo.status === 'success' ? 'rgba(63, 138, 95, 0.08)'
              : retryInfo.status === 'failed' ? 'rgba(181, 74, 54, 0.08)'
              : 'rgba(176, 122, 28, 0.08)',
            border: `1px solid ${
              retryInfo.status === 'success' ? 'rgba(63, 138, 95, 0.3)'
              : retryInfo.status === 'failed' ? 'rgba(181, 74, 54, 0.3)'
              : 'rgba(176, 122, 28, 0.3)'
            }`,
            color:
              retryInfo.status === 'success' ? 'var(--ok)'
              : retryInfo.status === 'failed' ? 'var(--danger)'
              : 'var(--warn)',
          }}
        >
          {retryInfo.status === 'retrying' && <RefreshCw className="w-3.5 h-3.5 animate-spin" />}
          {retryInfo.status === 'correcting' && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
          {retryInfo.status === 'success' && <CheckCircle className="w-3.5 h-3.5" />}
          {retryInfo.status === 'failed' && <XCircle className="w-3.5 h-3.5" />}
          <span>
            {retryInfo.status === 'retrying' && (
              <><strong>Reintento {retryInfo.attempt}/{retryInfo.maxAttempts}</strong>{retryInfo.action && <span style={{ opacity: 0.8 }}> — {retryInfo.action}</span>}</>
            )}
            {retryInfo.status === 'correcting' && 'Aplicando corrección…'}
            {retryInfo.status === 'success' && (retryInfo.message ?? `Corregido en ${retryInfo.attempt} intento(s)`)}
            {retryInfo.status === 'failed' && (retryInfo.message ?? `Falló después de ${retryInfo.attempt} intentos`)}
          </span>
        </div>
      )}

      <div className="messages">
        {messages.length === 0 ? (
          <div className="empty-state" style={{ paddingTop: 56 }}>
            <img
              src="/geoai-logo.png"
              alt="GeoAI LATAM"
              style={{ width: 46, height: 46, margin: '0 auto 12px', display: 'block', objectFit: 'contain' }}
            />
            <div className="empty-state-title">Copiloto geoespacial</div>
            <div className="empty-state-text">
              Pregunta en lenguaje natural sobre tus datos geográficos.
            </div>
            <div style={{ marginTop: 18, display: 'flex', flexDirection: 'column', gap: 6 }}>
              {SUGGESTIONS.slice(0, 3).map((s, i) => (
                <button
                  key={i}
                  onClick={() => handleSuggestion(s)}
                  className="suggestion"
                  style={{ justifyContent: 'flex-start' }}
                >
                  <ChevronRight />
                  {s}
                </button>
              ))}
            </div>
          </div>
        ) : (
          messages.map(renderMessage)
        )}
        <div ref={messagesEndRef} />
      </div>

      <form onSubmit={handleSubmit} className="composer">
        {messages.length > 0 && (
          <div className="composer-suggestions">
            {SUGGESTIONS.map((s, i) => (
              <button
                key={i}
                type="button"
                className="suggestion"
                onClick={() => handleSuggestion(s)}
              >
                <ChevronRight />
                <span>{s}</span>
              </button>
            ))}
          </div>
        )}

        <ChipsDeAlcance menciones={menciones} onQuitar={quitarMencion}
                        sinSeleccion={sinSeleccion} onSinSeleccion={setSinSeleccion} />
        <div className="composer-box">
          {lista && <ListaMenciones opciones={lista.opciones} activa={lista.activa} onElegir={elegirMencion} />}
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => alEscribir(e.target.value, e.target.selectionStart ?? e.target.value.length)}
            placeholder="Pregunta en lenguaje natural sobre tus datos geográficos… (@ para mencionar una capa)"
            disabled={isLoading || !sessionId}
            rows={1}
            aria-label="Consulta"
            onKeyDown={(e) => {
              if (lista) {
                const n = lista.opciones.length
                if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
                  e.preventDefault()
                  const paso = e.key === 'ArrowDown' ? 1 : n - 1
                  setLista({ ...lista, activa: (lista.activa + paso) % n })
                  return
                }
                if (e.key === 'Enter' || e.key === 'Tab') {
                  e.preventDefault()
                  elegirMencion(lista.opciones[lista.activa])
                  return
                }
                if (e.key === 'Escape') {
                  e.preventDefault()
                  setLista(null)
                  return
                }
              }
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                handleSubmit()
              }
            }}
          />
          <div className="composer-actions">
            <div className="left">
              <button
                type="button"
                className="gc-btn gc-btn-ghost"
                title="Adjuntar archivo (próximamente)"
                disabled
              >
                <Paperclip className="w-3.5 h-3.5" />
              </button>
              <button
                type="button"
                className="gc-btn gc-btn-ghost"
                title="Ver capas activas"
                onClick={() => toggleDrawer('layers')}
              >
                <Layers className="w-3.5 h-3.5" />
              </button>
              <button
                type="button"
                className="gc-btn gc-btn-ghost"
                title="Dictado por voz (próximamente)"
                disabled
              >
                <Mic className="w-3.5 h-3.5" />
              </button>
            </div>
            <div className="right">
              <span className="kbd">⏎</span>
              {isLoading ? (
                <button
                  type="button"
                  className="gc-btn gc-btn-danger"
                  onClick={() => {
                    // FE4: abortar la petición HTTP en curso (cliente) ADEMÁS
                    // de cancelar la task del grafo por WS. Antes solo se
                    // enviaba el WS cancel, que no detiene la llamada HTTP.
                    // El controlador vive en `lib/runQuery.ts` para que este
                    // botón también corte la consulta que arrancó el canvas.
                    abortActiveQuery()
                    if (sessionId) {
                      wsService.send({ type: 'cancel', session_id: sessionId, data: {} })
                    }
                  }}
                  title="Cancelar consulta en curso"
                >
                  <Square className="w-3.5 h-3.5" />
                  Detener
                </button>
              ) : (
                <button
                  type="submit"
                  disabled={!input.trim() || !sessionId}
                  className="gc-btn gc-btn-primary"
                >
                  <Send className="w-3.5 h-3.5" />
                  Consultar
                </button>
              )}
            </div>
          </div>
        </div>
      </form>
    </aside>
  )
}

// =============================================================================
// FoundServicesGrid — tarjetas seleccionables para selección de servicio
// =============================================================================
interface FoundServicesGridProps {
  services: FoundService[]
  onSelect: (svc: FoundService) => void
  loading: boolean
}

function getServiceKind(svc: FoundService): 'FeatureServer' | 'MapServer' | 'ImageServer' | 'Other' {
  // El backend ya clasifica el tipo en hub_search._service_kind() — usamos
  // su valor exacto. Antes había una segunda capa de keywords en el cliente
  // ("feature" in type || "featureserver" in url) que podía discordar del
  // backend para URLs anómalas y mostrar el badge equivocado.
  const t = (svc.type || '') as 'FeatureServer' | 'MapServer' | 'ImageServer' | 'Other'
  return (['FeatureServer', 'MapServer', 'ImageServer'] as const).includes(t as never)
    ? t
    : 'Other'
}

const KIND_META: Record<string, { label: string; color: string }> = {
  FeatureServer: { label: 'Feature',  color: '#2d6a4f' },
  MapServer:     { label: 'Map',      color: '#3d6a9e' },
  ImageServer:   { label: 'Image',    color: '#8a4c8a' },
  Other:         { label: 'Servicio', color: '#8b9087' },
}

function getOrgLabel(url: string): string {
  try {
    const host = new URL(url).hostname
    // Casos comunes legibles
    if (host.includes('catastrobogota')) return 'Catastro Bogotá'
    if (host.includes('igac')) return 'IGAC'
    if (host.includes('invias')) return 'INVIAS'
    if (host.includes('ideam')) return 'IDEAM'
    if (host.includes('anla')) return 'ANLA'
    if (host.includes('anh.gov')) return 'ANH'
    if (host.includes('anm.gov')) return 'ANM'
    if (host.includes('upra')) return 'UPRA'
    if (host.includes('parquesnacionales')) return 'Parques Nacionales'
    if (host.includes('dane')) return 'DANE'
    if (host.includes('sgc.gov')) return 'SGC'
    if (host.includes('cortolima')) return 'CorTolima'
    if (host.includes('cvc.gov')) return 'CVC'
    return host.replace('www.', '').split('.')[0]
  } catch {
    return ''
  }
}

function FoundServicesGrid({ services, onSelect, loading }: FoundServicesGridProps) {
  return (
    <div className="fs-grid">
      {services.map((svc, i) => {
        const kind = getServiceKind(svc)
        const meta = KIND_META[kind]
        // de quién es: los créditos/organización que trae la búsqueda; sin ellos, el dominio
        const org = svc.credits || getOrgLabel(svc.url)
        return (
          <button
            key={`${svc.url}-${i}`}
            type="button"
            className="fs-card"
            onClick={() => onSelect(svc)}
            disabled={loading}
            title="Cargar al mapa"
          >
            <div className="fs-card-head">
              <span
                className="fs-badge"
                style={{
                  background: meta.color + '22',
                  color: meta.color,
                  borderColor: meta.color + '55',
                }}
              >
                {meta.label}
              </span>
              {org && <span className="fs-org" title={org}>{org}</span>}
              {typeof svc.views === 'number' && svc.views > 0 && (
                <span className="fs-org">{svc.views.toLocaleString('es-CO')} vistas</span>
              )}
              <span className="fs-index">#{i + 1}</span>
            </div>
            <div className="fs-card-title" title={svc.name}>{svc.name}</div>
            {svc.description && (
              <div className="fs-card-desc">{svc.description}</div>
            )}
            <div className="fs-card-action">
              <Layers className="w-3 h-3" />
              <span>Cargar al mapa</span>
              <ChevronRight className="w-3 h-3" />
            </div>
          </button>
        )
      })}
    </div>
  )
}
