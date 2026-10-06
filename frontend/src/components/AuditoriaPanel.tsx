/**
 * F6 (E6.5) — quién ejecutó y aprobó qué en la organización (solo administración).
 *
 * Lee `GET /api/v1/auditoria` (lo último primero, de 50 en 50) y `…/verificar` (¿la cadena de
 * hashes está íntegra?). Cada entrada: cuándo, quién, qué hizo, sobre qué y con qué resultado.
 */
import { useCallback, useEffect, useState } from 'react'
import { ShieldAlert, ShieldCheck } from 'lucide-react'
import { auditoriaApi, type EntradaAuditoria } from '@/services/api'
import { resumenDe } from '@/lib/auditoria'

const VERBO: Record<string, string> = {
  consulta: 'preguntó',
  'capacidad.ejecutar': 'ejecutó',
  'hitl.solicitar': 'pidió aprobar',
  'hitl.aprobar': 'aprobó',
  'hitl.modificar': 'aprobó con cambios',
  'hitl.rechazar': 'rechazó',
  'hitl.expirar': 'dejó caducar',
  'tool.reaprobar': 're-aprobó la herramienta',
  'conexion.alta': 'dio de alta la conexión',
  'conexion.baja': 'dio de baja la conexión',
  'conexion.editar': 'cambió la conexión',
}

function recursoLegible(e: EntradaAuditoria): string {
  if (e.accion === 'consulta') return ''
  if (e.accion.startsWith('hitl.')) return e.recurso.split(':')[0].replace(/_/g, ' ')
  return e.recurso.replace(/^core\./, '').replace(/^mcp\./, 'MCP ')
}

export function AuditoriaPanel() {
  const [entradas, setEntradas] = useState<EntradaAuditoria[]>([])
  const [siguiente, setSiguiente] = useState<number | null>(null)
  const [integra, setIntegra] = useState<{ integra: boolean; encadenada: boolean } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [cargando, setCargando] = useState(false)

  const cargar = useCallback(async (antesDe?: number) => {
    setCargando(true)
    try {
      const r = await auditoriaApi.listar(antesDe)
      setEntradas((prev) => (antesDe ? [...prev, ...r.entradas] : r.entradas))
      setSiguiente(r.siguiente)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'No se pudo leer la auditoría')
    } finally {
      setCargando(false)
    }
  }, [])

  useEffect(() => {
    void cargar()
    void auditoriaApi.verificar().then(setIntegra).catch(() => setIntegra(null))
  }, [cargar])

  return (
    <div className="auditoria-panel">
      <div className="auditoria-cabecera">
        {integra && (integra.integra ? (
          <span className="auditoria-integra ok" title="Ninguna entrada se ha cambiado ni borrado">
            <ShieldCheck size={14} /> {integra.encadenada ? 'Registro íntegro' : 'Registro en memoria (sin cadena)'}
          </span>
        ) : (
          <span className="auditoria-integra rota" role="alert">
            <ShieldAlert size={14} /> El registro fue alterado fuera de la aplicación
          </span>
        ))}
        <button type="button" className="btn btn-secondary" onClick={() => void cargar()} disabled={cargando}>
          Actualizar
        </button>
      </div>
      {error && <p className="auditoria-error" role="alert">{error}</p>}
      {!error && !entradas.length && !cargando && <p className="auditoria-vacio">Todavía no hay actividad registrada.</p>}
      <ol className="auditoria-lista">
        {entradas.map((e) => (
          <li key={e.id} className={`auditoria-entrada res-${e.resultado}`}>
            <div className="auditoria-linea">
              <time dateTime={e.ts}>{new Date(e.ts).toLocaleString()}</time>
              <span className={`auditoria-resultado res-${e.resultado}`}>{e.resultado}</span>
            </div>
            <div className="auditoria-quien">
              <strong>{e.actor_nombre || e.actor_sub}</strong>{' '}
              {e.resultado === 'denegado' ? 'intentó (denegado)' : (VERBO[e.accion] ?? e.accion)}{' '}
              <span className="mono">{recursoLegible(e)}</span>
            </div>
            {resumenDe(e) && <div className="auditoria-detalle">{resumenDe(e)}</div>}
            {e.session_id && <div className="auditoria-sesion mono">sesión #{e.session_id.slice(-6)}</div>}
          </li>
        ))}
      </ol>
      {siguiente && (
        <button type="button" className="btn btn-secondary auditoria-mas" disabled={cargando}
          onClick={() => void cargar(siguiente)}>
          Anteriores
        </button>
      )}
    </div>
  )
}
