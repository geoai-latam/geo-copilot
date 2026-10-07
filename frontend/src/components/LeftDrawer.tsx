import {
  X,
  Cable,
  Layers as LayersIcon,
  Database,
  PlugZap,
  Compass,
  Satellite,
  History as HistoryIcon,
  Eye,
  EyeOff,
  Trash2,
  Locate,
  ChevronUp,
  ChevronDown,
  Clock,
  CheckCircle2,
  XCircle,
  PenLine,
  Table as TableIcon,
  Palette,
  ScrollText,
  Zap,
  Columns2,
  ShieldCheck,
} from 'lucide-react'
import { AuditoriaPanel } from './AuditoriaPanel'
import {
  useUIStore,
  useActiveDrawer,
  useLayers,
  useChatStore,
  useQueryHistory,
} from '@/stores'
import { useState } from 'react'
import type { MapCommand } from '@/contracts'
import { editarDibujo, esDibujo, modoDeEdicion, renombrarCapa } from '@/lib/dibujo'
import { quitarTodas, useOperaciones } from '@/lib/operaciones'
import { rendererDe } from '@/lib/renderers'
import { DataDiscoveryPanel } from './DataDiscoveryPanel'
import { FiltroCapa } from './FiltroCapa'
import { ComoSeHizo } from './ComoSeHizo'
import { useMenuContextual } from '@/lib/menuContextual'
import { useComparacion } from '@/lib/comparacion'
import { geometriaDeCapa } from './MenuContextual.helpers'
import { EditorEstilo } from './EditorEstilo'
import { useResultsStore } from '@/stores/resultsStore'
import { McpToolsPanel } from './McpToolsPanel'
import { ExploradorS2 } from './ExploradorS2'
import { ConnectionsPanel } from './ConnectionsPanel'
import { DatabaseSchemaPanel } from './DatabaseSchemaPanel'

/**
 * Cajón izquierdo que se abre al pulsar Capas / Datos / Historial en el
 * rail. Reutiliza estilos de `.panel` y se pinta dentro del workspace,
 * a la izquierda del ChatDock.
 */
export function LeftDrawer() { // eslint-disable-line complexity -- deuda congelada (F1); partir, no subir
  const drawer = useActiveDrawer()
  const setActiveDrawer = useUIStore((s) => s.setActiveDrawer)

  if (!drawer) return null

  const title =
    drawer === 'layers' ? 'Capas activas'
    : drawer === 'data' ? 'Descubrir datos · ArcGIS Hub'
    : drawer === 'sentinel2' ? 'Explorador Sentinel-2'
    : drawer === 'database' ? 'Mi base de datos'
    : drawer === 'tools' ? 'Herramientas · Servicios conectados'
    : drawer === 'connections' ? 'Conexiones · Servidores MCP'
    : drawer === 'auditoria' ? 'Auditoría · quién hizo qué'
    : 'Historial de consultas'

  // Iconos: el drawer 'data' (Discovery) usa Compass (brújula) para
  // sugerir exploración/descubrimiento — antes usaba Database, que era
  // confuso porque ahora hay un drawer 'database' dedicado al schema real.
  const Icon =
    drawer === 'layers' ? LayersIcon
    : drawer === 'data' ? Compass
    : drawer === 'sentinel2' ? Satellite
    : drawer === 'database' ? Database
    : drawer === 'tools' ? PlugZap
    : drawer === 'connections' ? Cable
    : drawer === 'auditoria' ? ShieldCheck
    : HistoryIcon

  return (
    <aside className="left-drawer">
      <div className="left-drawer-head">
        <Icon className="w-4 h-4" />
        <span className="left-drawer-title">{title}</span>
        <button
          className="icon-btn"
          onClick={() => setActiveDrawer(null)}
          title="Cerrar"
        >
          <X className="w-3.5 h-3.5" />
        </button>
      </div>
      <div className="left-drawer-body">
        {drawer === 'layers' && <LayersDrawer />}
        {drawer === 'data' && <DataDiscoveryPanel />}
        {drawer === 'sentinel2' && <ExploradorS2 />}
        {drawer === 'database' && <DatabaseSchemaPanel />}
        {drawer === 'tools' && <McpToolsPanel />}
        {drawer === 'connections' && <ConnectionsPanel />}
        {drawer === 'auditoria' && <AuditoriaPanel />}
        {drawer === 'history' && <HistoryDrawer />}
      </div>
    </aside>
  )
}

/** FH.3: el nombre de la capa; doble clic lo edita (Enter guarda, Esc cancela). */
function NombreCapa({ id, nombre }: { id: string; nombre: string }) {
  const [editando, setEditando] = useState(false)
  const [valor, setValor] = useState(nombre)
  const [error, setError] = useState<string | null>(null)
  const guardar = async () => {
    setEditando(false)
    setError(await renombrarCapa(id, valor))
  }
  if (!editando) {
    return (
      <div className="layer-name" title={error ?? `${nombre} · doble clic para renombrar`}
           onDoubleClick={() => { setValor(nombre); setEditando(true) }}>
        {nombre}
        {error && <span className="layer-name-error" role="alert"> · {error}</span>}
      </div>
    )
  }
  return (
    <input
      className="layer-name-input"
      value={valor}
      autoFocus
      maxLength={120}
      aria-label={`Nuevo nombre de ${nombre}`}
      onChange={(e) => setValor(e.target.value)}
      onBlur={() => void guardar()}
      onKeyDown={(e) => {
        if (e.key === 'Enter') void guardar()
        if (e.key === 'Escape') { e.stopPropagation(); setEditando(false) }
      }}
    />
  )
}

function LayersDrawer() {
  const layers = useLayers()
  const clearAll = () => quitarTodas('user')
  // FH.1: cada gesto es una orden al mismo reducer que usa el agente: queda en el
  // registro (autor «usuario»), se deshace con Ctrl+Z y el agente la ve.
  const ejecutar = useOperaciones((s) => s.ejecutar)
  const hacer = (cmd: Record<string, unknown>) => ejecutar({ reason: null, args: {}, ...cmd } as MapCommand, 'user')
  const toggleLayer = (id: string) =>
    hacer({ op: 'set_visibility', layer_id: id, args: { visible: !layers.find((l) => l.id === id)?.visible } })
  const setLayerOpacity = (id: string, opacity: number) => hacer({ op: 'set_opacity', layer_id: id, args: { opacity } })
  const setLayerLabelField = (id: string, field: string | null) => hacer({ op: 'set_label', layer_id: id, args: { field } })
  const removeLayer = (id: string) => hacer({ op: 'remove_layer', layer_id: id })
  const flyToLayer = (id: string) => hacer({ op: 'zoom_to', layer_id: id })
  /** Lleva la capa a la posición `destino` del orden de dibujo (encima/debajo de la que está ahí). */
  const moveLayer = (id: string, destino: number) => {
    const desde = layers.findIndex((l) => l.id === id)
    const ref = layers[destino]
    if (desde === -1 || !ref || ref.id === id) return
    hacer({ op: 'reorder', layer_id: id, args: { to: destino > desde ? 'above' : 'below', relative_to: ref.id } })
  }
  const [arrastrada, setArrastrada] = useState<string | null>(null)
  const [estiloAbierto, setEstiloAbierto] = useState<string | null>(null)
  const [comoAbierto, setComoAbierto] = useState<string | null>(null)
  const [comparando, setComparando] = useState<string | null>(null)

  if (layers.length === 0) {
    return (
      <div className="empty-state">
        <LayersIcon className="w-8 h-8" style={{ margin: '0 auto 10px', color: 'var(--text-mute)' }} />
        <div className="empty-state-title">Sin capas</div>
        <div className="empty-state-text">Ejecuta una consulta para añadir capas al mapa.</div>
      </div>
    )
  }

  // F4 (S4.2): una sola lista para todos los tipos. Arriba en la lista = arriba
  // en el mapa (el store guarda el orden de dibujo: índice 0 = abajo).
  const deArribaAAbajo = [...layers].reverse()
  const indiceEnStore = (id: string) => layers.findIndex((l) => l.id === id)

  return (
    <>
      <div className="drawer-actions">
        <span className="drawer-count">
          {layers.length} capa{layers.length === 1 ? '' : 's'}
        </span>
        <button className="gc-btn gc-btn-ghost" onClick={clearAll} title="Borrar todas">
          <Trash2 className="w-3.5 h-3.5" />
          Limpiar
        </button>
      </div>
      <div className="drawer-list" data-testid="layers-list">
        {deArribaAAbajo.map((l, pos) => { // eslint-disable-line complexity -- deuda congelada (F1); partir, no subir
          const renderer = rendererDe(l.kind)
          const fila = renderer.panelRow(l)
          const i = indiceEnStore(l.id)
          return (
            <div
              key={l.id}
              className={`layer-item${arrastrada === l.id ? ' is-dragging' : ''}`}
              data-testid="layer-row"
              data-layer-id={l.id}
              data-layer-kind={l.kind}
              draggable
              onDragStart={(e) => {
                setArrastrada(l.id)
                e.dataTransfer.effectAllowed = 'move'
                e.dataTransfer.setData('text/plain', l.id)
              }}
              onDragEnd={() => setArrastrada(null)}
              onDragOver={(e) => e.preventDefault()}
              onDrop={(e) => {
                e.preventDefault()
                const id = e.dataTransfer.getData('text/plain') || arrastrada
                if (id && id !== l.id) moveLayer(id, i)
                setArrastrada(null)
              }}
            >
              {/* el nombre a lo ancho, arriba (antes se truncaba a «Lot…» entre los iconos) */}
              <div className="layer-head">
                <span className="layer-color" style={{ background: fila.swatch }} aria-hidden data-testid="layer-swatch" />
                <NombreCapa id={l.id} nombre={l.name} />
              </div>
              <div className="layer-meta">
                <div className="layer-sub mono">
                  {fila.sub} · {Math.round((l.opacity ?? 1) * 100)}%
                </div>
                <input
                  type="range"
                  className="layer-opacity"
                  min={0}
                  max={1}
                  step={0.05}
                  value={l.opacity ?? 1}
                  onChange={(e) => setLayerOpacity(l.id, Number(e.target.value))}
                  title={`Opacidad ${Math.round((l.opacity ?? 1) * 100)}%`}
                  aria-label={`Opacidad de ${l.name}`}
                />
                {fila.labelFields.length > 0 && (
                  <select
                    className="layer-label-select"
                    value={l.labelField ?? ''}
                    onChange={(e) => setLayerLabelField(l.id, e.target.value || null)}
                    title="Etiquetar por campo"
                    aria-label={`Etiqueta de ${l.name}`}
                  >
                    <option value="">Sin etiquetas</option>
                    {fila.labelFields.map((f) => (
                      <option key={f} value={f}>
                        Aa {f}
                      </option>
                    ))}
                  </select>
                )}
              </div>
              <div className="layer-order">
                <button
                  className="icon-btn"
                  onClick={() => moveLayer(l.id, i + 1)}
                  disabled={pos === 0}
                  title="Subir (dibujar encima)"
                  aria-label={`Subir ${l.name}`}
                >
                  <ChevronUp className="w-3.5 h-3.5" />
                </button>
                <button
                  className="icon-btn"
                  onClick={() => moveLayer(l.id, i - 1)}
                  disabled={pos === deArribaAAbajo.length - 1}
                  title="Bajar (dibujar debajo)"
                  aria-label={`Bajar ${l.name}`}
                >
                  <ChevronDown className="w-3.5 h-3.5" />
                </button>
              </div>
              {renderer.bounds(l) && (
                <button className="icon-btn" onClick={() => flyToLayer(l.id)} title="Centrar en la capa">
                  <Locate className="w-3.5 h-3.5" />
                </button>
              )}
              <button
                className="icon-btn"
                onClick={() => toggleLayer(l.id)}
                title={l.visible ? 'Ocultar' : 'Mostrar'}
              >
                {l.visible ? <Eye className="w-3.5 h-3.5" /> : <EyeOff className="w-3.5 h-3.5" />}
              </button>
              {(l.kind === 'vector-geojson' || l.kind === 'vector-mvt') && (
                <button className={`icon-btn${estiloAbierto === l.id ? ' is-active' : ''}`}
                        onClick={() => setEstiloAbierto(estiloAbierto === l.id ? null : l.id)}
                        title="Estilo (editar a mano)" aria-label={`Estilo de ${l.name}`} aria-pressed={estiloAbierto === l.id}>
                  <Palette className="w-3.5 h-3.5" />
                </button>
              )}
              {(l.kind === 'vector-geojson' || l.kind === 'vector-mvt') && (
                <button className="icon-btn" onClick={() => useResultsStore.getState().verTablaCapa(l.id)}
                        title="Tabla de atributos (vinculada al mapa)" aria-label={`Tabla de ${l.name}`}>
                  <TableIcon className="w-3.5 h-3.5" />
                </button>
              )}
              {esDibujo(l) && l.data.features?.some((f) => f.geometry && modoDeEdicion(f.geometry.type)) && (
                <button className="icon-btn" onClick={() => editarDibujo(l.id)} title="Editar los vértices del dibujo"
                        aria-label={`Editar vértices de ${l.name}`}>
                  <PenLine className="w-3.5 h-3.5" />
                </button>
              )}
              {(l.kind === 'vector-geojson' || l.kind === 'vector-mvt') && (
                <button className="icon-btn" title="Acciones sobre la capa" aria-label={`Acciones de ${l.name}`}
                        onClick={(e) => {
                          const r = e.currentTarget.getBoundingClientRect()
                          useMenuContextual.getState().abrir({ capaId: l.id, alcance: 'capa', geometria: geometriaDeCapa(l) },
                                                             r.right + 6, r.top)
                        }}>
                  <Zap className="w-3.5 h-3.5" />
                </button>
              )}
              {layers.length > 1 && (
                <button className={`icon-btn${comparando === l.id ? ' is-active' : ''}`}
                        onClick={() => setComparando(comparando === l.id ? null : l.id)}
                        title="Comparar con otra capa (cortina)" aria-label={`Comparar ${l.name}`}>
                  <Columns2 className="w-3.5 h-3.5" />
                </button>
              )}
              <button className={`icon-btn${comoAbierto === l.id ? ' is-active' : ''}`}
                      onClick={() => setComoAbierto(comoAbierto === l.id ? null : l.id)}
                      title="Cómo se hizo (procedencia)" aria-label={`Cómo se hizo ${l.name}`} aria-pressed={comoAbierto === l.id}>
                <ScrollText className="w-3.5 h-3.5" />
              </button>
              <button className="icon-btn" onClick={() => removeLayer(l.id)} title="Eliminar">
                <Trash2 className="w-3.5 h-3.5" />
              </button>
              {/* filtro y editor de estilo: a lo ancho, debajo de la fila (V5 FH.6: dentro de la
                  columna del nombre quedaban aplastados entre los iconos) */}
              {(l.kind === 'vector-geojson' || l.kind === 'vector-mvt' || comoAbierto === l.id || comparando === l.id) && (
                <div className="layer-extra">
                  {comparando === l.id && (
                    <label className="comparar-con">Comparar con
                      <select className="input" aria-label={`Comparar ${l.name} con`} value=""
                              onChange={(e) => { if (e.target.value) { useComparacion.getState().abrir(l.id, e.target.value); setComparando(null) } }}>
                        <option value="">— elige la otra capa —</option>
                        {layers.filter((o) => o.id !== l.id).map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
                      </select>
                    </label>
                  )}
                  {(l.kind === 'vector-geojson' || l.kind === 'vector-mvt') && <FiltroCapa capa={l} />}
                  {estiloAbierto === l.id && <EditorEstilo capa={l} />}
                  {comoAbierto === l.id && <ComoSeHizo capa={l} />}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </>
  )
}

function HistoryDrawer() {
  const history = useQueryHistory()
  const clear = useChatStore((s) => s.clearMessages)

  if (history.length === 0) {
    return (
      <div className="empty-state">
        <HistoryIcon className="w-8 h-8" style={{ margin: '0 auto 10px', color: 'var(--text-mute)' }} />
        <div className="empty-state-title">Sin historial</div>
        <div className="empty-state-text">Las consultas que ejecutes aparecerán aquí.</div>
      </div>
    )
  }

  return (
    <>
      <div className="drawer-actions">
        <span className="drawer-count">{history.length} consulta{history.length === 1 ? '' : 's'}</span>
        <button className="gc-btn gc-btn-ghost" onClick={clear} title="Limpiar chat">
          <Trash2 className="w-3.5 h-3.5" />
          Limpiar chat
        </button>
      </div>
      <div className="drawer-list">
        {history.map((h, i) => (
          <div key={i} className="history-item">
            <div className="history-icon">
              {h.success ? (
                <CheckCircle2 className="w-3.5 h-3.5" style={{ color: 'var(--ok)' }} />
              ) : (
                <XCircle className="w-3.5 h-3.5" style={{ color: 'var(--danger)' }} />
              )}
            </div>
            <div className="history-body">
              <div className="history-query" title={h.query}>{h.query}</div>
              <div className="history-meta">
                <Clock className="w-3 h-3" />
                <span className="mono">{new Date(h.timestamp).toLocaleTimeString('es', { hour12: false })}</span>
              </div>
            </div>
          </div>
        ))}
      </div>
    </>
  )
}
