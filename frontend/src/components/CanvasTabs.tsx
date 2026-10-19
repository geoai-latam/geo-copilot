/* eslint-disable max-lines -- deuda congelada (F1 del plan de calidad): partir por responsabilidad, no crecer */
import { useMapStore } from '@/stores/mapStore'
import { TablaCapa } from '@/components/TablaCapa'
import { useState, useCallback, useRef, useEffect } from 'react'
import { useUIStore } from '@/stores/uiStore'
import {
  Globe2,
  LayoutPanelLeft,
  Code,
  Download,
  Maximize2,
  Minimize2,
  RefreshCw,
  Share2,
  X,
} from 'lucide-react'
import { useQueryHistory, useIsLoading, useSessionId } from '@/stores'
import { useResultsStore, useTurnoActivo, useTurnos, type Turno } from '@/stores/resultsStore'
import { runQuery } from '@/lib/runQuery'
import { logger } from '@/utils/logger'
import type { Artifact } from '@/contracts'
import { GeoDataTable } from './GeoDataTable'
import { Chart } from './Chart'
import { DeshacerRehacer } from './DeshacerRehacer'
import { ErrorBoundary } from './ErrorBoundary'
import { ImprimirMapa } from './ImprimirMapa'
import {
  cuentaDePanel,
  etiquetaDeTurno,
  filasDeTabla,
  pestanaPara,
  tablaDeCapa,
  visualizacionDeGrafico,
  type CanvasTabId,
} from './CanvasTabs.helpers'

interface CanvasTabsProps {
  children: React.ReactNode  // el mapa + overlays
}

interface TabDef {
  id: CanvasTabId
  label: string
  icon: React.ComponentType<{ className?: string }>
  badge?: string
}

/**
 * Pestañas del canvas (F4, S4.3). El mapa está SIEMPRE montado y visible:
 * «Resultados» es un panel acoplado a la derecha que apila los artefactos del
 * turno (tabla, gráfico, estadísticas, informe) sin tapar el mapa, con un
 * selector para volver a turnos anteriores. «SQL» sí cubre el mapa.
 */
export function CanvasTabs({ children }: CanvasTabsProps) {
  const [tab, setTab] = useState<CanvasTabId>('map')
  const turno = useTurnoActivo()
  const turnos = useTurnos()
  const queryHistory = useQueryHistory()
  const isLoading = useIsLoading()
  const sessionId = useSessionId()

  const canvasRef = useRef<HTMLDivElement>(null)
  const [isFullscreen, setIsFullscreen] = useState(false)

  // Con cada turno NUEVO, saltar a donde está lo que dejó. Volver a un turno
  // viejo desde el historial no mueve la pestaña (ya estás en Resultados).
  const ultimo = turnos.length ? turnos[turnos.length - 1].id : null
  useEffect(() => {
    const destino = pestanaPara(useResultsStore.getState().turnos.find((t) => t.id === ultimo) ?? null)
    if (destino) setTab(destino)
  }, [ultimo])

  // FH.5: abrir la tabla de una capa (desde el panel de capas) muestra el panel
  const tablaCapa = useResultsStore((s) => s.tablaCapa)
  useEffect(() => { if (tablaCapa) setTab('results') }, [tablaCapa])

  const nPanel = cuentaDePanel(turno)
  const tabs: TabDef[] = [
    { id: 'map',     label: 'Mapa',       icon: Globe2 },
    { id: 'results', label: 'Resultados', icon: LayoutPanelLeft, badge: nPanel ? String(nPanel) : undefined },
    { id: 'sql',     label: 'SQL',        icon: Code },
  ]

  const capaInline = turno?.artefactos.find((a) => a.kind === 'layer' && a.inline)
  const downloadGeoJSON = useCallback(() => {
    if (!capaInline || capaInline.kind !== 'layer' || !capaInline.inline) return
    const blob = new Blob([JSON.stringify(capaInline.inline, null, 2)], { type: 'application/geo+json' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `geodata_${new Date().toISOString().slice(0, 10)}.geojson`
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    URL.revokeObjectURL(url)
  }, [capaInline])

  // Refrescar = volver a escribir la última consulta (mismo `runQuery` que el
  // chat: map_context, abort, re-estilo en sitio). Auditoría 2026-09-08 §5 (3).
  const refreshLastQuery = useCallback(async () => {
    const last = queryHistory[0]?.query
    if (!last) return
    await runQuery(last)
  }, [queryHistory])

  const copyShareLink = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(window.location.href)
      logger.log('[CanvasTabs] URL copiada')
    } catch (e) {
      logger.error('[CanvasTabs] clipboard failed', e)
    }
  }, [])

  const toggleFullscreen = useCallback(() => {
    const el = canvasRef.current
    if (!el) return
    if (!document.fullscreenElement) {
      el.requestFullscreen?.()
        .then(() => setIsFullscreen(true))
        .catch((e) => logger.error('[CanvasTabs] fullscreen failed', e))
    } else {
      document.exitFullscreen?.().then(() => setIsFullscreen(false))
    }
  }, [])

  return (
    <div className="canvas" ref={canvasRef}>
      <div className="canvas-head">
        <div className="tabs">
          {tabs.map((t) => {
            const Ic = t.icon
            const active = tab === t.id
            return (
              <button
                key={t.id}
                className={`tab ${active ? 'active' : ''}`}
                onClick={() => setTab(t.id)}
              >
                <Ic />
                {t.label}
                {t.badge && <span className="tab-badge">{t.badge}</span>}
              </button>
            )
          })}
        </div>
        <div className="right">
          <DeshacerRehacer />
          <button
            className="gc-btn gc-btn-ghost"
            title={
              queryHistory.length === 0
                ? 'Sin consulta previa'
                : !sessionId
                  ? 'Sin sesión activa'
                  : 'Refrescar última consulta'
            }
            onClick={refreshLastQuery}
            disabled={queryHistory.length === 0 || isLoading || !sessionId}
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isLoading ? 'animate-spin' : ''}`} />
          </button>
          <button
            className="gc-btn gc-btn-ghost"
            title="Exportar GeoJSON"
            onClick={downloadGeoJSON}
            disabled={!capaInline}
          >
            <Download className="w-3.5 h-3.5" />
          </button>
          <ImprimirMapa />
          <button
            className="gc-btn gc-btn-ghost"
            title="Copiar enlace"
            onClick={copyShareLink}
          >
            <Share2 className="w-3.5 h-3.5" />
          </button>
          <button
            className="gc-btn gc-btn-ghost"
            title={isFullscreen ? 'Salir de pantalla completa' : 'Pantalla completa'}
            onClick={toggleFullscreen}
          >
            {isFullscreen ? <Minimize2 className="w-3.5 h-3.5" /> : <Maximize2 className="w-3.5 h-3.5" />}
          </button>
        </div>
      </div>

      <div className="canvas-body">
        {/* El mapa siempre montado debajo */}
        {children}

        {tab === 'results' && (
          <PanelResultados turno={turno} turnos={turnos} onClose={() => setTab('map')} />
        )}

        {tab === 'sql' && (
          <CanvasOverlay title="SQL · PostGIS">
            {turno?.sql ? (
              <SqlBlock sql={turno.sql} />
            ) : (
              <EmptyView icon={<Code />} title="Sin SQL disponible" hint="Cuando un agente genere SQL, se mostrará aquí con resaltado." />
            )}
          </CanvasOverlay>
        )}
      </div>
    </div>
  )
}

/** Panel acoplado a la derecha: el mapa sigue visible a su izquierda. */
function PanelResultados({ turno, turnos, onClose }: { turno: Turno | null; turnos: Turno[]; onClose: () => void }) {
  const ver = useResultsStore((s) => s.ver)
  // Cuánto mapa tapa el panel: el mapa encuadra en el resto (S4.3).
  const ref = useRef<HTMLElement>(null)
  useEffect(() => {
    const el = ref.current
    const set = useUIStore.getState().setRellenoDerecho
    if (!el) return
    const medir = () => set(el.offsetWidth + 12)
    medir()
    const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(medir) : null
    ro?.observe(el)
    return () => { ro?.disconnect(); set(0) }
  }, [])
  const filasCapa = turno ? tablaDeCapa(turno) : null
  const artefactos = turno?.artefactos ?? []
  const tablaCapa = useResultsStore((s) => s.tablaCapa)
  const vacio = !tablaCapa && (!turno || (!filasCapa && cuentaDePanel(turno) === 0))
  return (
    <aside
      ref={ref}
      className="panel"
      data-testid="panel-resultados"
      style={{
        position: 'absolute',
        // Debajo del chip del pipeline (data → gis → sym → ins), que va arriba al centro.
        top: 56,
        right: 12,
        bottom: 12,
        width: 'min(520px, calc(100% - 24px))',
        zIndex: 12,
        background: 'var(--surface)',
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
      }}
    >
      <div className="panel-header" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)', flex: 'none' }}>Resultados</div>
        {turnos.length > 0 && (
          <select
            aria-label="Historial de resultados"
            data-testid="historial-resultados"
            value={turno?.id ?? ''}
            onChange={(e) => ver(e.target.value)}
            style={{ flex: 1, minWidth: 0, fontSize: 12 }}
          >
            {[...turnos].reverse().map((t) => (
              <option key={t.id} value={t.id}>{etiquetaDeTurno(t)}</option>
            ))}
          </select>
        )}
        <button className="gc-btn gc-btn-ghost" title="Cerrar panel" aria-label="Cerrar panel" onClick={onClose}>
          <X className="w-3.5 h-3.5" />
        </button>
      </div>
      <div style={{ flex: 1, minHeight: 0, overflow: 'auto', padding: 12, display: 'flex', flexDirection: 'column', gap: 14 }}>
        {vacio ? (
          <EmptyView icon={<LayoutPanelLeft />} title="Sin resultados aún" hint="Tablas, gráficos, estadísticas e informes de cada consulta aparecerán aquí." />
        ) : (
          <>
            {tablaCapa && <TablaCapa layerId={tablaCapa} />}
            {artefactos.map((a, i) => <VistaArtefacto key={i} artefacto={a} />)}
            {filasCapa && (
              <Seccion titulo={`Tabla de la consulta · ${filasCapa.length.toLocaleString('es-CO')} filas`} testid="artefacto-table">
                <AvisoCapaVinculada turno={turno} />
                <GeoDataTable features={filasCapa} pageSize={20} maxHeight="50vh" />
              </Seccion>
            )}
          </>
        )}
      </div>
    </aside>
  )
}

/**
 * La tabla de un turno es lo que DEVOLVIÓ esa consulta: no sigue al filtro ni a la selección
 * de la capa. Si la capa sigue en el mapa, se dice y se ofrece su tabla vinculada (V5 FH.5:
 * «Tabla · 27 filas» junto a la capa filtrada a 2 confundía).
 */
function AvisoCapaVinculada({ turno, datasetId }: { turno?: Turno | null; datasetId?: string | null }) {
  const ds = datasetId ?? turno?.artefactos.find((a) => a.kind === 'layer')?.layer.id ?? null
  const capa = useMapStore((s) => (ds ? s.layers.find((l) => l.datasetId === ds) : undefined))
  if (!capa) return null
  const filtrada = capa.filtro?.length
    ? ` En el mapa está filtrada${capa.filtroCount != null ? `: ${capa.filtroCount} de ${capa.featureCount}` : ''}.`
    : ''
  return (
    <div className="aviso-tabla" data-testid="aviso-tabla-consulta">
      Estas son las filas que devolvió la consulta.{filtrada}{' '}
      <button type="button" className="gc-btn gc-btn-ghost" onClick={() => useResultsStore.getState().verTablaCapa(capa.id)}>
        Ver la tabla de «{capa.name}» (vinculada al mapa)
      </button>
    </div>
  )
}

function VistaArtefacto({ artefacto: a }: { artefacto: Artifact }) {
  switch (a.kind) {
    case 'table': {
      const total = a.total_rows ?? a.preview.length
      const nombre = a.title || 'Tabla de la consulta'
      const titulo = total > a.preview.length
        ? `${nombre} · ${a.preview.length.toLocaleString('es-CO')} de ${total.toLocaleString('es-CO')} filas`
        : `${nombre} · ${total.toLocaleString('es-CO')} filas`
      return (
        <Seccion titulo={titulo} testid="artefacto-table">
          <AvisoCapaVinculada datasetId={a.rows_ref ?? null} />
          <GeoDataTable features={filasDeTabla(a)} pageSize={20} maxHeight="50vh" />
        </Seccion>
      )
    }
    case 'chart':
      return (
        <Seccion titulo={a.spec.title || 'Gráfico'} testid="artefacto-chart">
          {/* Un throw del gráfico no debe blanquear la app ni el mapa. */}
          <ErrorBoundary fallbackTitle="El gráfico no pudo renderizarse">
            <Chart visualization={visualizacionDeGrafico(a)} />
          </ErrorBoundary>
        </Seccion>
      )
    case 'stats':
      return (
        <Seccion titulo={a.title || 'Estadísticas'} testid="artefacto-stats">
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))', gap: 10 }}>
            {a.items.map((s, i) => (
              <StatCardSimple key={i} label={s.label} value={s.value} unit={s.unit} />
            ))}
          </div>
        </Seccion>
      )
    case 'report':
      return (
        <Seccion titulo="Informe" testid="artefacto-report">
          {/* Texto plano con saltos: el markdown del agente nunca entra como HTML. */}
          <div style={{ whiteSpace: 'pre-wrap', fontSize: 13, lineHeight: 1.55, color: 'var(--text)' }}>{a.markdown}</div>
          {a.cites.length > 0 && (
            <div style={{ marginTop: 8, fontSize: 11.5, color: 'var(--text-mute)' }}>Fuentes: {a.cites.join(' · ')}</div>
          )}
        </Seccion>
      )
    default:
      return null
  }
}

function Seccion({ titulo, testid, children }: { titulo: string; testid: string; children: React.ReactNode }) {
  return (
    <section data-testid={testid}>
      <div style={{ fontSize: 11.5, fontWeight: 600, color: 'var(--text-mute)', textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: 6 }}>
        {titulo}
      </div>
      {children}
    </section>
  )
}

function CanvasOverlay({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div
      className="panel"
      style={{
        position: 'absolute',
        inset: 16,
        zIndex: 12,
        background: 'var(--surface)',
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
      }}
    >
      <div className="panel-header">
        <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>{title}</div>
      </div>
      <div style={{ flex: 1, minHeight: 0, overflow: 'auto' }}>
        {children}
      </div>
    </div>
  )
}

function EmptyView({
  icon,
  title,
  hint,
}: {
  icon: React.ReactNode
  title: string
  hint: string
}) {
  return (
    <div className="empty-state" style={{ padding: '64px 24px' }}>
      <div
        style={{
          width: 36,
          height: 36,
          margin: '0 auto 12px',
          color: 'var(--text-mute)',
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        {icon}
      </div>
      <div className="empty-state-title">{title}</div>
      <div className="empty-state-text">{hint}</div>
    </div>
  )
}

function StatCardSimple({ label, value, unit }: { label: string; value: number | string | null; unit?: string | null }) {
  return (
    <div
      style={{
        border: '1px solid var(--border)',
        borderRadius: 'var(--radius)',
        background: 'var(--surface)',
        padding: '10px 12px',
      }}
    >
      <div style={{ fontSize: 11.5, color: 'var(--text-mute)', marginBottom: 4 }}>{label}</div>
      <div
        style={{
          fontSize: 20,
          fontWeight: 600,
          color: 'var(--text)',
          letterSpacing: '-0.02em',
          fontVariantNumeric: 'tabular-nums',
        }}
      >
        {typeof value === 'number' ? value.toLocaleString('es-CO') : (value ?? '—')}
        {unit && <span style={{ fontSize: 12, fontWeight: 400, color: 'var(--text-mute)', marginLeft: 4 }}>{unit}</span>}
      </div>
    </div>
  )
}

function SqlBlock({ sql }: { sql: string }) {
  const highlighted = highlightSql(sql)
  const lines = highlighted.split('\n')
  return (
    <pre
      style={{
        margin: 0,
        padding: '16px 14px 16px 0',
        fontFamily: 'var(--font-mono)',
        fontSize: 12,
        lineHeight: 1.6,
        color: 'var(--text)',
        background: 'var(--bg)',
        height: '100%',
        overflow: 'auto',
      }}
    >
      {lines.map((html, i) => (
        <span
          key={i}
          style={{
            display: 'block',
            paddingLeft: 50,
            position: 'relative',
          }}
        >
          <span
            style={{
              position: 'absolute',
              left: 0,
              width: 38,
              textAlign: 'right',
              color: 'var(--text-mute)',
              fontSize: 10.5,
              paddingRight: 10,
              borderRight: '1px solid var(--border)',
              marginRight: 8,
            }}
          >
            {i + 1}
          </span>
          <span dangerouslySetInnerHTML={{ __html: html || '&nbsp;' }} />
        </span>
      ))}
    </pre>
  )
}

const KEYWORDS = new Set([
  'select','from','where','and','or','as','join','left','right','inner','outer','full',
  'on','group','order','by','having','limit','offset','with','insert','into','values',
  'update','set','delete','union','all','distinct','case','when','then','else','end',
  'asc','desc','null','is','not','in','exists','between','like','ilike','create','table',
  'view','index','drop','alter','add','column','primary','key','foreign','references',
])

const FUNCTIONS = new Set([
  'st_buffer','st_intersects','st_intersection','st_area','st_contains','st_within',
  'st_distance','st_within','st_union','st_difference','st_centroid','st_makepoint',
  'st_geomfromtext','st_astext','st_asgeojson','st_transform','st_setsrid','st_x','st_y',
  'count','sum','avg','min','max','nullif','coalesce','round','floor','ceil',
])

function escape(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

function highlightSql(sql: string): string { // eslint-disable-line complexity -- deuda congelada (F1); partir, no subir
  let out = ''
  let i = 0
  while (i < sql.length) {
    const c = sql[i]

    // line comment
    if (c === '-' && sql[i + 1] === '-') {
      let j = i
      while (j < sql.length && sql[j] !== '\n') j++
      out += `<span style="color:var(--text-mute);font-style:italic">${escape(sql.slice(i, j))}</span>`
      i = j
      continue
    }

    // single-quoted string
    if (c === "'") {
      let j = i + 1
      while (j < sql.length && sql[j] !== "'") j++
      j = Math.min(j + 1, sql.length)
      out += `<span style="color:var(--agent-ins)">${escape(sql.slice(i, j))}</span>`
      i = j
      continue
    }

    // number
    if (/[0-9]/.test(c) && !/[a-z_]/i.test(sql[i - 1] ?? '')) {
      let j = i
      while (j < sql.length && /[0-9.]/.test(sql[j])) j++
      out += `<span style="color:var(--info)">${escape(sql.slice(i, j))}</span>`
      i = j
      continue
    }

    // identifier / keyword / function
    if (/[a-z_]/i.test(c)) {
      let j = i
      while (j < sql.length && /[a-z0-9_]/i.test(sql[j])) j++
      const word = sql.slice(i, j)
      const lower = word.toLowerCase()
      if (KEYWORDS.has(lower)) {
        out += `<span style="color:var(--accent)">${escape(word.toUpperCase())}</span>`
      } else if (FUNCTIONS.has(lower)) {
        out += `<span style="color:var(--agent-sym)">${escape(word)}</span>`
      } else {
        out += escape(word)
      }
      i = j
      continue
    }

    out += escape(c)
    i++
  }
  return out
}
