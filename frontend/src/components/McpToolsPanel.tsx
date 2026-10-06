/**
 * Panel de herramientas de los servicios MCP conectados (E3.2).
 *
 * Genérico: lista lo que el hub publica en GET /connections/tools y genera el
 * formulario desde el `input_schema` de cada tool (ver mcpTools.helpers). Al
 * ejecutar llama a la MISMA capacidad que usa el agente, así que un servidor
 * nuevo enchufado por YAML aparece aquí sin tocar el frontend. El resultado
 * trae la forma de `results` de /query y se pinta igual: capa del workspace,
 * teselas raster o tabla.
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle, Loader2, Play, PlugZap } from 'lucide-react'

import { mcpApi, type McpRunResult, type McpToolInfo } from '@/services/api'
import { useLayers, useMapStore, useSessionStore, useUIStore } from '@/stores'
import { aplicarResultado } from '@/lib/resultadoHerramienta'
import { buildMapContext } from '@/utils/mapContext'
import {
  buildArguments,
  factRows,
  type FormField,
  formFields,
  PUNTO,
  VIEWPORT,
} from './mcpTools.helpers'

const clave = (t: McpToolInfo) => `${t.server}/${t.tool}`

export function McpToolsPanel() { // eslint-disable-line complexity -- deuda congelada (F1): ESLint 10 suma `?.` y defaults; partir, no subir
  const [tools, setTools] = useState<McpToolInfo[] | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [selected, setSelected] = useState<string>('')
  const [values, setValues] = useState<Record<string, string>>({})
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<McpRunResult | null>(null)

  const layers = useLayers()
  const sessionId = useSessionStore((s) => s.sessionId)
  // Capa raster añadida por ESTE panel: se reemplaza en cada ejecución (#27).
  const lastImageryIdRef = useRef<string | null>(null)

  // «Probar» desde Conexiones: la tool pedida se lee UNA vez al montar (en dev,
  // StrictMode corre el efecto dos veces y la segunda ya no la encontraba).
  const pedidaRef = useRef(useUIStore.getState().herramientaPedida)

  useEffect(() => {
    mcpApi
      .tools()
      .then((r) => {
        setTools(r.tools)
        const elegida = r.tools.find((t) => clave(t) === pedidaRef.current) ?? r.tools[0]
        if (elegida) setSelected(clave(elegida))
        useUIStore.getState().probarHerramienta(null)
      })
      .catch((e) => setLoadError(String(e?.message ?? e)))
  }, [])

  const tool = tools?.find((t) => clave(t) === selected) ?? null
  const fields = useMemo(() => (tool ? formFields(tool) : []), [tool])
  const porServidor = useMemo(() => {
    const g = new Map<string, McpToolInfo[]>()
    for (const t of tools ?? []) g.set(t.server, [...(g.get(t.server) ?? []), t])
    return [...g.entries()]
  }, [tools])

  const elegir = (k: string) => {
    setSelected(k)
    setValues({})
    setResult(null)
    setError(null)
  }

  const run = async () => {
    if (!tool || !sessionId) return
    setError(null)
    setResult(null)
    const built = buildArguments(fields, values, layers.map((l) => ({
      id: l.id, name: l.name, datasetId: l.datasetId, data: l.data,
    })))
    if (!built.ok) { setError(built.error); return }
    setRunning(true)
    try {
      const res = await mcpApi.run(tool.server, tool.tool, {
        session_id: sessionId, arguments: built.args, map_context: buildMapContext(),
      })
      setResult(res)
      if (!res.success) { setError(res.message ?? 'La herramienta no devolvió resultado.'); return }
      lastImageryIdRef.current = aplicarResultado(res, `${tool.server} · ${tool.tool}`, lastImageryIdRef.current).raster
        ?? lastImageryIdRef.current
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setRunning(false)
    }
  }

  if (loadError) {
    return (
      <div className="imgp-empty">
        <PlugZap className="w-5 h-5" />
        <p>No se pudieron cargar las herramientas</p>
        <span>{loadError}</span>
      </div>
    )
  }
  if (!tools) {
    return (
      <div className="imgp-empty">
        <Loader2 className="w-5 h-5 animate-spin" />
        <p>Cargando herramientas…</p>
      </div>
    )
  }
  if (tools.length === 0) {
    return (
      <div className="imgp-empty" data-testid="mcp-tools-empty">
        <PlugZap className="w-5 h-5" />
        <p>No hay servicios conectados</p>
        <span>Se enchufan en config/mcp_servers.yaml.</span>
      </div>
    )
  }

  return (
    <div className="imgp" data-testid="mcp-tools-panel">
      <section className="imgp-card">
        <header>Herramienta</header>
        <select className="input" aria-label="Herramienta" value={selected}
                onChange={(e) => elegir(e.target.value)}>
          {porServidor.map(([server, ts]) => (
            <optgroup key={server} label={server}>
              {ts.map((t) => <option key={clave(t)} value={clave(t)}>{t.tool}</option>)}
            </optgroup>
          ))}
        </select>
        {tool && <p className="imgp-hint" data-testid="mcp-tool-description">{tool.description}</p>}
        {tool && tool.estado !== 'disponible' && (
          <p className="imgp-warn" role="status">
            <AlertTriangle className="w-3.5 h-3.5" /> El servicio «{tool.server}» no está disponible ahora.
          </p>
        )}
      </section>

      {fields.length > 0 && (
        <section className="imgp-card" data-testid="mcp-tool-form">
          <header>Parámetros</header>
          {fields.map((f) => (
            <Campo key={f.name} field={f} value={values[f.name] ?? ''} layers={layers}
                   onChange={(v) => setValues((s) => ({ ...s, [f.name]: v }))} />
          ))}
        </section>
      )}

      <button className="btn-primary imgp-run" onClick={run} disabled={running || !tool || !sessionId}>
        {running ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
        {running ? 'Ejecutando…' : 'Ejecutar'}
      </button>

      {error && (
        <div className="imgp-error" role="alert">
          <AlertTriangle className="w-4 h-4" /> {error}
        </div>
      )}

      {result?.success && <Resultado result={result} />}
    </div>
  )
}

export function Campo({ field: f, value, layers, onChange }: { // eslint-disable-line complexity -- deuda congelada (F1); partir, no subir
  field: FormField
  value: string
  layers: { id: string; name: string }[]
  onChange: (v: string) => void
}) {
  const hayPunto = useMapStore((s) => !!s.puntoMarcado)
  const etiqueta = `${f.label}${f.required ? ' *' : ''}`
  const placeholder = f.defaultValue !== undefined && f.defaultValue !== null ? String(f.defaultValue) : ''
  let control
  if (f.kind === 'geo') {
    control = (
      <select className="input" aria-label={f.label} value={value || VIEWPORT}
              onChange={(e) => onChange(e.target.value)}>
        <option value={VIEWPORT}>Zona visible del mapa</option>
        {hayPunto && <option value={PUNTO}>Punto marcado en el mapa</option>}
        {[...layers].reverse().map((l) => <option key={l.id} value={l.id}>Capa: {l.name}</option>)}
      </select>
    )
  } else if (f.kind === 'enum') {
    control = (
      <select className="input" aria-label={f.label} value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">{placeholder ? `(por defecto: ${placeholder})` : '—'}</option>
        {f.options?.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    )
  } else if (f.kind === 'boolean') {
    control = (
      <select className="input" aria-label={f.label} value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">{placeholder ? `(por defecto: ${placeholder})` : '—'}</option>
        <option value="true">sí</option>
        <option value="false">no</option>
      </select>
    )
  } else if (f.kind === 'json') {
    control = (
      <textarea className="input" aria-label={f.label} rows={3} value={value}
                placeholder="JSON" onChange={(e) => onChange(e.target.value)} />
    )
  } else {
    control = (
      <input className="input" aria-label={f.label} value={value} placeholder={placeholder}
             type={f.kind === 'date' ? 'date' : f.kind === 'number' || f.kind === 'integer' ? 'number' : 'text'}
             min={f.minimum} max={f.maximum} step={f.kind === 'number' ? 'any' : undefined}
             onChange={(e) => onChange(e.target.value)} />
    )
  }
  return (
    <label className="imgp-field" title={f.description}>
      <span className="imgp-sub">{etiqueta}</span>
      {control}
    </label>
  )
}

/** Resultado genérico: leyenda de la capa raster, hechos del servidor y tabla. */
export function Resultado({ result }: { result: McpRunResult }) { // eslint-disable-line complexity -- deuda congelada (F1); partir, no subir
  const r = result.results
  const leyenda = r.external_imagery?.legend
  const filas = r.data?.results ?? []
  const columnas = filas.length ? Object.keys(filas[0]) : []
  const hechos = factRows(result.facts)
  return (
    <section className="imgp-card imgp-result" data-testid="mcp-tool-result">
      {(r.layer_ref || r.external_imagery) && (
        <p className="imgp-facts">
          Añadido al mapa: <b>{r.external_imagery?.name ?? r.layer_name ?? r.layer_ref?.name}</b>
          {r.layer_ref?.feature_count != null && ` · ${r.layer_ref.feature_count} elementos`}
        </p>
      )}
      {leyenda && leyenda.min != null && leyenda.max != null && (
        <div className="imgp-legend" aria-label={`Leyenda ${leyenda.field ?? ''}`}>
          <div className="imgp-legend-bar" />
          <div className="imgp-legend-ticks">
            <span>{leyenda.min.toFixed(2)}</span>
            <span>{((leyenda.min + leyenda.max) / 2).toFixed(2)}</span>
            <span>{leyenda.max.toFixed(2)}</span>
          </div>
          <div className="imgp-sub">{leyenda.field}{leyenda.nota ? ` — ${leyenda.nota}` : ''}</div>
        </div>
      )}
      {filas.length > 0 && (
        <table className="imgp-table">
          <thead><tr>{columnas.map((c) => <th key={c}>{c}</th>)}</tr></thead>
          <tbody>
            {filas.slice(0, 12).map((f, i) => (
              <tr key={i}>
                {columnas.map((c) => (
                  <td key={c}>{typeof f[c] === 'number' && !Number.isInteger(f[c])
                    ? (f[c] as number).toFixed(3) : String(f[c] ?? '')}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {hechos.length > 0 && (
        // sin capa ni tabla (p. ej. medir), los hechos SON el resultado: abiertos (V5 FH.8)
        <details className="imgp-details" open={!r.geojson && !r.tiles && !r.external_imagery && !filas.length}>
          <summary>Detalles del servicio</summary>
          <dl className="imgp-kv">
            {hechos.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}
          </dl>
        </details>
      )}
    </section>
  )
}
