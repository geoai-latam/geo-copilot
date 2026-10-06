/**
 * F6 (S6.2, E6.3) — alta de una conexión MCP de la organización (solo administración).
 *
 * La credencial se escribe una vez: viaja al backend, se guarda cifrada y nunca vuelve (el
 * panel solo muestra si la conexión tiene credencial). El backend valida la URL (https y
 * destino público, salvo los hosts internos que permita la plataforma).
 */
import { useState, type FormEvent } from 'react'
import { Loader2, Plus, X } from 'lucide-react'
import { mcpApi } from '@/services/api'

export function AltaConexionForm({ onCreada, onCancelar }: { onCreada: () => void; onCancelar: () => void }) {
  const [id, setId] = useState('')
  const [url, setUrl] = useState('')
  const [descripcion, setDescripcion] = useState('')
  const [credencial, setCredencial] = useState('')
  const [tabular, setTabular] = useState(false)
  const [enviando, setEnviando] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const enviar = async (ev: FormEvent) => {
    ev.preventDefault()
    setEnviando(true)
    setError(null)
    try {
      await mcpApi.alta({
        id: id.trim(), url: url.trim(),
        ...(descripcion.trim() ? { description: descripcion.trim() } : {}),
        ...(credencial ? { credencial } : {}),
        ...(tabular ? { adapter: 'tabular_geo' as const } : {}),
      })
      setCredencial('')  // no se queda en memoria del formulario
      onCreada()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setEnviando(false)
    }
  }

  return (
    <form className="panel alta-conexion" onSubmit={(e) => void enviar(e)} aria-label="Nueva conexión">
      <header>
        <strong>Nueva conexión de tu organización</strong>
        <button type="button" className="gc-btn gc-btn-ghost" onClick={onCancelar} aria-label="Cancelar">
          <X className="w-3.5 h-3.5" />
        </button>
      </header>
      <label>
        Identificador
        <input value={id} onChange={(e) => setId(e.target.value.toLowerCase())} required pattern="[a-z][a-z0-9_]{0,30}"
               placeholder="almacen" title="minúsculas, números y _ (empieza por letra)" />
      </label>
      <label>
        URL del servidor MCP
        <input value={url} onChange={(e) => setUrl(e.target.value)} required type="url" placeholder="https://…/mcp" />
      </label>
      <label>
        Qué contiene (lo lee el agente)
        <input value={descripcion} onChange={(e) => setDescripcion(e.target.value)} maxLength={300}
               placeholder="Sedes educativas de Cundinamarca" />
      </label>
      <label>
        Credencial (Bearer)
        <input value={credencial} onChange={(e) => setCredencial(e.target.value)} type="password"
               autoComplete="new-password" placeholder="Se guarda cifrada; no se vuelve a mostrar" />
      </label>
      <label className="alta-conexion-check">
        <input type="checkbox" checked={tabular} onChange={(e) => setTabular(e.target.checked)} />
        Devuelve filas (tipo Snowflake): el agente declara qué columna es la geometría
      </label>
      {error && <div className="chip danger" role="alert" style={{ whiteSpace: 'normal' }}>{error}</div>}
      <button type="submit" className="btn btn-primary" disabled={enviando}>
        {enviando ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Plus className="w-3.5 h-3.5" />}
        Conectar
      </button>
    </form>
  )
}
