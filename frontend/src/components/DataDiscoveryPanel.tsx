/* eslint-disable max-lines -- deuda congelada (F1 del plan de calidad): partir por responsabilidad, no crecer */
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Search,
  X,
  Loader2,
  AlertTriangle,
  ExternalLink,
  Layers as LayersIcon,
  Image as ImageIcon,
  Map as MapIcon,
  ShieldCheck,
  Database,
  ChevronRight,
} from 'lucide-react'
import { discoveryApi, ApiError } from '@/services/api'
import { useSessionId } from '@/stores'
import { cargarDescubierto } from '@/lib/cargaDescubierta'
import { useChatStore } from '@/stores'
import type {
  DiscoveryIntent,
  DiscoveryLayer,
  DiscoverySearchResponse,
  DiscoveryServiceType,
  HubItem,
} from '@/types/discovery'
import { logger } from '@/utils/logger'
import {
  ZONE_PRESETS,
  buildSearchRequest,
  hechosDeTarjeta,
  nombreGeometria,
} from './discoveryPanel.helpers'

const SERVICE_BADGES: Record<DiscoveryServiceType, { label: string; icon: typeof LayersIcon; color: string }> = {
  FeatureServer: { label: 'Feature', icon: LayersIcon, color: '#2d6a4f' },
  MapServer:     { label: 'Map',     icon: MapIcon,    color: '#3d6a9e' },
  ImageServer:   { label: 'Image',   icon: ImageIcon,  color: '#8a4c8a' },
  GeoJSON:       { label: 'GeoJSON', icon: LayersIcon, color: '#3f8a5f' },
  CSV:           { label: 'CSV',     icon: Database,   color: '#5a6359' },
  Postgis:       { label: 'PostGIS', icon: Database,   color: '#2d6a4f' },
  Other:         { label: 'Otro',    icon: LayersIcon, color: '#8b9087' },
}

const INTENT_LABELS: Record<DiscoveryIntent, string> = {
  entity_focused:  'por entidad',
  topic_focused:   'por tema',
  zone_focused:    'por zona',
  service_focused: 'por servicio',
  imagery_focused: 'imágenes',
  recent_focused:  'recientes',
  exploratory:     'exploratorio',
}

function formatModified(modified: string | number | null | undefined): string {
  if (modified == null) return ''
  try {
    const dt =
      typeof modified === 'number'
        ? new Date(modified)
        : new Date(modified)
    if (Number.isNaN(dt.getTime())) return ''
    return dt.toLocaleDateString('es-CO', { year: 'numeric', month: 'short', day: '2-digit' })
  } catch {
    return ''
  }
}

export function DataDiscoveryPanel() { // eslint-disable-line complexity -- deuda congelada (F1); partir, no subir
  const [query, setQuery] = useState('')
  const [activeQuery, setActiveQuery] = useState('')
  const [zone, setZone] = useState<string | null>(null)
  const [serviceFilter, setServiceFilter] = useState<DiscoveryServiceType | null>(null)
  const [officialOnly, setOfficialOnly] = useState(false)
  // Modo global: si está activo, se desactiva el sesgo regional y la
  // detección por catálogo. Útil para descubrir datos de otros países.
  const [globalMode, setGlobalMode] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [response, setResponse] = useState<DiscoverySearchResponse | null>(null)
  const [loadingItemId, setLoadingItemId] = useState<string | null>(null)
  const [detailItem, setDetailItem] = useState<HubItem | null>(null)
  // servicio con varias capas: cuáles ofrece (por id de item) para que el usuario elija
  const [capasDe, setCapasDe] = useState<Record<string, DiscoveryLayer[]>>({})

  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  // FE5: secuencia de petición. Sin esto, dos búsquedas rápidas podían
  // resolver fuera de orden y una respuesta vieja pisaba la fresca.
  const requestSeqRef = useRef(0)

  // MIN-DISCOVERY-HEADERS / #35: pasar la sesión activa al cargar desde una
  // card reanuda el plan pausado (cadena buscar→cargar→pintar). Sin esto el
  // backend nunca despacha las operaciones pendientes.
  const sessionId = useSessionId()

  const runSearch = useCallback(
    async (q: string) => {
      // Toda la lógica query+chips→hints vive en `buildSearchRequest`
      // (en discoveryPanel.helpers.ts) para que sea testeable sin DOM.
      const request = buildSearchRequest({
        query: q,
        zone,
        serviceFilter,
        officialOnly,
        globalMode,
        maxResults: 60,
      })
      if (!request) {
        setResponse(null)
        return
      }

      const seq = ++requestSeqRef.current
      setLoading(true)
      setError(null)
      try {
        const resp = await discoveryApi.search(request.effectiveQuery, request.hints)
        // Solo aplicar si sigue siendo la última búsqueda lanzada.
        if (seq === requestSeqRef.current) setResponse(resp)
      } catch (e) {
        if (seq === requestSeqRef.current) {
          logger.error('[Discovery] search failed', e)
          setError(e instanceof Error ? e.message : 'Error desconocido')
        }
      } finally {
        if (seq === requestSeqRef.current) setLoading(false)
      }
    },
    [officialOnly, zone, serviceFilter, globalMode]
  )

  // Único effect que coordina debounce sobre query + reactividad a filtros.
  // Cuando solo cambian los chips (sin tocar el input) re-ejecuta inmediato.
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current)
    const wait = query !== activeQuery ? 400 : 0
    debounceRef.current = setTimeout(() => {
      setActiveQuery(query)
      runSearch(query)
    }, wait)
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current)
    }
  }, [query, zone, serviceFilter, officialOnly, globalMode, runSearch, activeQuery])

  const applyRefinement = useCallback(
    (action: string) => {
      // Las acciones que devuelve el DiscoveryAgent cuando hay 0 resultados:
      // - "broaden_tags" / "broaden": aflojar tags y filtros de tipo
      // - "drop_bbox": quitar bbox/zona
      // - "free_text" / "type_query": dejar sólo el text_query
      switch (action) {
        case 'drop_bbox':
          setZone(null)
          break
        case 'broaden_tags':
        case 'broaden':
          setServiceFilter(null)
          setOfficialOnly(false)
          break
        case 'free_text':
        case 'type_query':
          setZone(null)
          setServiceFilter(null)
          setOfficialOnly(false)
          break
        default:
          break
      }
    },
    []
  )

  const handleLoadItem = useCallback(
    async (item: HubItem, capa?: DiscoveryLayer) => {
      setLoadingItemId(item.id)
      setError(null)
      try {
        const carga = await cargarDescubierto(item, { sessionId: sessionId ?? undefined, capa })
        if (carga.estado === 'elegir_capa') {
          // Rama arcgis-busqueda: antes se cargaba la capa 0 (en Cota eran PUNTOS). Con varias,
          // elige el usuario viendo nombre y geometría.
          setCapasDe((m) => ({ ...m, [item.id]: carga.capas }))
          return
        }
        setCapasDe((m) => {
          if (!(item.id in m)) return m
          const resto = { ...m }
          delete resto[item.id]
          return resto
        })
        useChatStore.getState().addMessage({
          id: `msg-${Date.now()}-load-panel`, role: 'assistant', timestamp: new Date(), status: 'sent',
          content: carga.mensaje,
        })
      } catch (e) {
        logger.error('[Discovery] load failed', e)
        // LIVE.3: distinguir el 5xx del servicio remoto (upstream) de un
        // error de red real ("Failed to fetch"), para no mostrar un
        // mensaje genérico confuso.
        let msg: string
        if (e instanceof ApiError) {
          msg = (e.status >= 500)
            ? `El servicio remoto no está disponible (HTTP ${e.status}). Prueba con otra capa.`
            : (e.detail || e.message)
        } else if (e instanceof TypeError) {
          msg = 'No se pudo conectar con el servicio. Revisa tu conexión e intenta de nuevo.'
        } else {
          msg = e instanceof Error ? e.message : 'No se pudo cargar la capa.'
        }
        setError(msg)
      } finally {
        setLoadingItemId(null)
      }
    },
    [sessionId]
  )

  const items = response?.items ?? []
  const topItem = items[0] ?? null
  const restItems = items.slice(1)

  return (
    <div className="disc-panel">
      <div className="disc-body">
          <div className="disc-search">
            <Search size={14} />
            <input
              type="text"
              value={query}
              placeholder="manzanas Bogotá, ortofoto IGAC, POT Medellín…"
              onChange={(e) => setQuery(e.target.value)}
              autoFocus
            />
            {query && (
              <button
                className="disc-icon-btn"
                onClick={() => setQuery('')}
                title="Limpiar"
              >
                <X size={12} />
              </button>
            )}
          </div>

          <div className="disc-chips">
            {(['FeatureServer', 'MapServer', 'ImageServer'] as DiscoveryServiceType[]).map((s) => {
              const meta = SERVICE_BADGES[s]
              const Icon = meta.icon
              const active = serviceFilter === s
              return (
                <button
                  key={s}
                  className={`disc-chip ${active ? 'active' : ''}`}
                  onClick={() => setServiceFilter(active ? null : s)}
                  style={active ? { borderColor: meta.color, color: meta.color } : undefined}
                >
                  <Icon size={11} />
                  <span>{meta.label}</span>
                </button>
              )
            })}
            <span className="disc-chip-sep" />
            {ZONE_PRESETS.map((z) => (
              <button
                key={z.id}
                className={`disc-chip ${zone === z.id ? 'active' : ''}`}
                onClick={() => setZone(zone === z.id ? null : z.id)}
              >
                {z.label}
              </button>
            ))}
            <span className="disc-chip-sep" />
            <button
              className={`disc-chip ${officialOnly ? 'active' : ''}`}
              onClick={() => setOfficialOnly((v) => !v)}
              title="Solo cuentas institucionales colombianas"
              disabled={globalMode}
            >
              <ShieldCheck size={11} />
              <span>Oficial CO</span>
            </button>
            <button
              className={`disc-chip ${globalMode ? 'active' : ''}`}
              onClick={() => setGlobalMode((v) => !v)}
              title="Buscar en todo ArcGIS Hub sin sesgo regional (descubre datos de cualquier país)"
            >
              <span>🌐 Global</span>
            </button>
          </div>

          {response && !loading && (
            <div className="disc-intent">
              <span>{items.length} resultado{items.length === 1 ? '' : 's'}</span>
              <span className="disc-intent-tag">{INTENT_LABELS[response.intent]}</span>
            </div>
          )}

          {response && !loading && (response.criterio || (response.otras_busquedas?.length ?? 0) > 0) && (
            <div className="disc-criterio" data-testid="disc-criterio">
              {response.criterio && <span>{response.criterio}</span>}
              {(response.otras_busquedas?.length ?? 0) > 0 && (
                <span className="disc-criterio-otras">
                  También busqué: {(response.otras_busquedas ?? []).map((q) => `«${q}»`).join(', ')}
                </span>
              )}
            </div>
          )}

          {response?.authority_warning && items.length > 0 && (
            <div className="disc-warning">
              <AlertTriangle size={12} />
              <span>
                El primer resultado no dice de quién es (ni créditos ni una cuenta institucional conocida) — verifica la fuente antes de usarlo.
              </span>
            </div>
          )}

          {error && (
            <div className="disc-error">
              <AlertTriangle size={12} />
              <span>{error}</span>
            </div>
          )}

          {loading && (
            <div className="disc-loading">
              <Loader2 size={14} className="spin" />
              <span>Buscando en ArcGIS…</span>
            </div>
          )}

          <div className="disc-results">
            {!loading && response && items.length === 0 && (
              <div className="disc-empty">
                <p>Sin resultados.</p>
                <p className="disc-empty-hint">Prueba con:</p>
                <div className="disc-suggestions">
                  {(response.suggested_refinements ?? []).map((r, i) => (
                    <button
                      key={i}
                      type="button"
                      className="disc-suggestion-btn"
                      onClick={() => applyRefinement(r.action)}
                    >
                      <ChevronRight size={11} />
                      <span>{r.label}</span>
                    </button>
                  ))}
                </div>
              </div>
            )}

            {topItem && (
              <ResultCard
                item={topItem}
                featured
                loading={loadingItemId === topItem.id}
                capas={capasDe[topItem.id]}
                onLoad={(capa) => handleLoadItem(topItem, capa)}
                onDetail={() => setDetailItem(topItem)}
              />
            )}

            <div className="disc-grid">
              {restItems.map((it) => (
                <ResultCard
                  key={it.id}
                  item={it}
                  loading={loadingItemId === it.id}
                  capas={capasDe[it.id]}
                  onLoad={(capa) => handleLoadItem(it, capa)}
                  onDetail={() => setDetailItem(it)}
                />
              ))}
            </div>
          </div>
      </div>

      {detailItem && (
        <DetailDrawer item={detailItem} onClose={() => setDetailItem(null)} />
      )}
    </div>
  )
}

// =============================================================================
// Subcomponents
// =============================================================================
interface ResultCardProps {
  item: HubItem
  featured?: boolean
  loading: boolean
  /** Capas del servicio cuando tiene varias: el usuario elige cuál cargar. */
  capas?: DiscoveryLayer[]
  onLoad: (capa?: DiscoveryLayer) => void
  onDetail: () => void
}

function ResultCard({ item, featured, loading, capas, onLoad, onDetail }: ResultCardProps) {
  const badge = SERVICE_BADGES[item.service_type] ?? SERVICE_BADGES.Other
  const BadgeIcon = badge.icon
  const mod = formatModified(item.modified)
  const { fuente, uso } = hechosDeTarjeta(item)
  const eligiendo = (capas?.length ?? 0) > 1

  return (
    <article className={`disc-card ${featured ? 'featured' : ''}`}>
      <div className="disc-card-head">
        <div
          className="disc-badge"
          style={{ background: badge.color + '22', color: badge.color, borderColor: badge.color + '55' }}
          title={item.type_raw}
        >
          <BadgeIcon size={11} />
          <span>{badge.label}</span>
        </div>
        {item.redirected_from === 'socrata' && (
          <span className="disc-pill">↪ datos.gov.co</span>
        )}
        {mod && <span className="disc-meta">{mod}</span>}
      </div>

      <h3 className="disc-card-title" title={item.title}>{item.title}</h3>
      <p className="disc-card-fuente" title={[fuente, item.owner && `Cuenta: ${item.owner}`].filter(Boolean).join(' · ')}>
        <span className="disc-card-fuente-quien">{fuente}</span>
        {uso && <span className="disc-card-uso">· {uso}</span>}
      </p>
      {item.description && (
        <p className="disc-card-desc">{item.description}</p>
      )}

      {eligiendo && capas && (
        <div className="disc-capas" role="group" aria-label="Capas del servicio">
          <span className="disc-capas-titulo">Tiene {capas.length} capas: ¿cuál cargar?</span>
          {capas.map((c) => (
            <button key={c.id} type="button" className="disc-capa" disabled={loading} onClick={() => onLoad(c)}>
              <span>{c.nombre}</span>
              <span className="disc-capa-geom">{nombreGeometria(c.tipo_geometria)}</span>
            </button>
          ))}
        </div>
      )}

      <div className="disc-card-actions">
        <button
          className="disc-btn primary"
          onClick={() => onLoad()}
          disabled={loading || eligiendo}
        >
          {loading ? <Loader2 size={12} className="spin" /> : <LayersIcon size={12} />}
          <span>{loading ? 'Cargando…' : 'Cargar al mapa'}</span>
        </button>
        <button className="disc-btn ghost" onClick={onDetail}>
          Detalles
        </button>
      </div>
    </article>
  )
}

function DetailDrawer({ item, onClose }: { item: HubItem; onClose: () => void }) {
  return (
    <div className="disc-drawer">
      <div className="disc-drawer-head">
        <span>Detalle</span>
        <button className="disc-icon-btn" onClick={onClose}><X size={14} /></button>
      </div>
      <div className="disc-drawer-body">
        <h3>{item.title}</h3>
        <p className="disc-card-org">{item.org}</p>

        <dl>
          <dt>Tipo</dt><dd>{item.type_raw || item.service_type}</dd>
          {item.credits && (<><dt>Créditos</dt><dd>{item.credits}</dd></>)}
          {item.owner && (<><dt>Owner</dt><dd>{item.owner}</dd></>)}
          {typeof item.views === 'number' && (<><dt>Vistas</dt><dd>{item.views.toLocaleString('es-CO')}</dd></>)}
          {typeof item.completeness === 'number' && (<><dt>Metadatos</dt><dd>{item.completeness}/100</dd></>)}
          {item.layer_id != null && (<><dt>Layer ID</dt><dd>{item.layer_id}</dd></>)}
          {item.modified && (<><dt>Modificado</dt><dd>{formatModified(item.modified)}</dd></>)}
          {item.extent && (
            <>
              <dt>Extent</dt>
              <dd className="disc-mono">
                [{item.extent[0].toFixed(3)}, {item.extent[1].toFixed(3)},
                {' '}{item.extent[2].toFixed(3)}, {item.extent[3].toFixed(3)}]
              </dd>
            </>
          )}
          {item.tags && item.tags.length > 0 && (
            <>
              <dt>Tags</dt>
              <dd>
                <div className="disc-tag-list">
                  {item.tags.slice(0, 8).map((t) => <span key={t} className="disc-tag">{t}</span>)}
                </div>
              </dd>
            </>
          )}
          <dt>URL</dt>
          <dd className="disc-mono disc-url">{item.service_url}</dd>
        </dl>

        {item.hub_url && (
          <a
            className="disc-btn ghost"
            href={item.hub_url}
            target="_blank"
            rel="noopener noreferrer"
            style={{ marginTop: 12 }}
          >
            <ExternalLink size={12} />
            <span>Ver en hub.arcgis.com</span>
          </a>
        )}
      </div>
    </div>
  )
}
