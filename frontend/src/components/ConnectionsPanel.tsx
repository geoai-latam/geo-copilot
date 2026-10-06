/**
 * Panel de Conexiones (F4, S4.5; F6, S6.2): los servidores MCP de la plataforma y los de TU
 * organización, su estado, su nivel de conformidad y sus herramientas con su riesgo. Una tool
 * deshabilitada por el pinning (su descripción o esquema cambió) la re-aprueba un
 * administrador; «Probar» abre el formulario de esa herramienta. Un administrador da de alta y
 * de baja las conexiones de su organización (la credencial se guarda cifrada y no se muestra).
 */
import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle, Check, KeyRound, Loader2, Play, Plus, RefreshCw, Trash2 } from 'lucide-react'

import { mcpApi, type ConexionOrg, type McpServerStatus } from '@/services/api'
import { useUIStore } from '@/stores'
import { estadoDe, hostDe, nivelDe, resumenTools, riesgoDe } from './connections.helpers'
import { AltaConexionForm } from './AltaConexionForm'

export function ConnectionsPanel() {
  const [servers, setServers] = useState<McpServerStatus[] | null>(null)
  const [propias, setPropias] = useState<ConexionOrg[]>([])
  const [admin, setAdmin] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [aprobando, setAprobando] = useState<string | null>(null)
  const [nueva, setNueva] = useState(false)
  const [quitando, setQuitando] = useState<string | null>(null)
  const probar = useUIStore((s) => s.probarHerramienta)

  const cargar = useCallback(() => {
    setError(null)
    mcpApi
      .connections()
      .then((r) => {
        setServers(r.servers)
        setPropias(r.de_la_organizacion ?? [])
        setAdmin(!!r.puede_administrar)
      })
      .catch((e) => setError(String(e?.message ?? e)))
  }, [])

  const quitar = async (id: string) => {
    try {
      await mcpApi.baja(id)
      setQuitando(null)
      cargar()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  useEffect(cargar, [cargar])

  const aprobar = async (server: string, tool: string) => {
    setAprobando(`${server}/${tool}`)
    try {
      await mcpApi.approve(server, tool)
      cargar()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setAprobando(null)
    }
  }

  if (error && !servers) {
    return (
      <div className="imgp-empty" role="alert">
        <AlertTriangle className="w-5 h-5" />
        <p>No se pudo leer el estado de las conexiones</p>
        <span className="imgp-hint">{error}</span>
      </div>
    )
  }
  if (!servers) {
    return (
      <div className="imgp-empty">
        <Loader2 className="w-5 h-5 animate-spin" />
        <p>Consultando servidores…</p>
      </div>
    )
  }
  if (servers.length === 0 && !admin) {
    return (
      <div className="imgp-empty">
        <p>No hay servidores MCP configurados</p>
        <span className="imgp-hint">Se declaran en config/mcp_servers.yaml (ver docs/sistema/12).</span>
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 6 }}>
        <span className="imgp-hint">{servers.length} servidor(es)</span>
        <span style={{ display: 'flex', gap: 4 }}>
          {admin && !nueva && (
            <button className="gc-btn" onClick={() => setNueva(true)} aria-label="Añadir conexión">
              <Plus className="w-3.5 h-3.5" /> Añadir
            </button>
          )}
          <button className="gc-btn gc-btn-ghost" onClick={cargar} title="Actualizar estado" aria-label="Actualizar estado">
            <RefreshCw className="w-3.5 h-3.5" />
          </button>
        </span>
      </div>
      {nueva && <AltaConexionForm onCreada={() => { setNueva(false); cargar() }} onCancelar={() => setNueva(false)} />}
      {error && <div className="chip danger" role="alert">{error}</div>}
      {servers.map((s) => {
        const est = estadoDe(s.estado)
        const propia = propias.find((p) => p.id === s.id)
        return (
          <section key={s.id} className="panel" data-testid="conexion" data-server={s.id}
                   style={{ padding: 10, display: 'flex', flexDirection: 'column', gap: 8 }}>
            <header style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
              <strong style={{ fontSize: 13 }}>{s.servidor?.name || s.id}</strong>
              {s.servidor?.version && <span className="imgp-hint">v{s.servidor.version}</span>}
              {/* el id es el prefijo de sus herramientas (`<id>__…`); dos conexiones al mismo servidor
                  solo se distinguen por él (V5 F6) */}
              {s.servidor?.name && s.servidor.name !== s.id && (
                <code className="imgp-hint" data-testid="id-conexion">{s.id}</code>
              )}
              <span className={`chip ${est.tono}`} data-testid="estado-servidor">{est.texto}</span>
              <span className="chip" title={nivelDe(s.conformance)} data-testid="nivel-servidor">
                {s.conformance}
              </span>
              {s.de === 'organizacion' && <span className="chip ok">de tu organización</span>}
              {propia?.tiene_credencial && (
                <span className="chip" title="Credencial guardada cifrada; no se muestra"><KeyRound className="w-3 h-3" /> credencial</span>
              )}
              {admin && s.de === 'organizacion' && (
                quitando === s.id ? (
                  <span style={{ marginLeft: 'auto', display: 'flex', gap: 4, alignItems: 'center' }}>
                    <span className="imgp-hint">¿Quitarla?</span>
                    <button className="gc-btn" onClick={() => void quitar(s.id)} aria-label={`Confirmar quitar ${s.id}`}>Sí</button>
                    <button className="gc-btn gc-btn-ghost" onClick={() => setQuitando(null)}>No</button>
                  </span>
                ) : (
                  <button className="gc-btn gc-btn-ghost" style={{ marginLeft: 'auto' }} onClick={() => setQuitando(s.id)}
                          aria-label={`Quitar ${s.id}`} title="Dar de baja esta conexión">
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                )
              )}
            </header>
            <div className="imgp-hint">
              {nivelDe(s.conformance)} · {hostDe(s.url)} · {resumenTools(s)}
            </div>
            {s.ultimo_error && (
              <div className="chip danger" style={{ whiteSpace: 'normal' }}>Último error: {s.ultimo_error}</div>
            )}
            <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 6 }}>
              {s.tools.map((t) => {
                const r = riesgoDe(t.riesgo)
                const clave = `${s.id}/${t.nombre}`
                return (
                  <li key={t.nombre} data-testid="conexion-tool" data-tool={t.nombre}
                      style={{ display: 'flex', flexDirection: 'column', gap: 4, borderTop: '1px solid var(--border)', paddingTop: 6 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                      <code style={{ fontSize: 12 }}>{t.nombre}</code>
                      <span className={`chip ${r.tono}`}>{r.texto}</span>
                      {t.geo && <span className="chip" title="Acepta capas o zonas del mapa">geo</span>}
                      <span style={{ marginLeft: 'auto', display: 'flex', gap: 4 }}>
                        {t.habilitada ? (
                          <button className="gc-btn gc-btn-ghost" onClick={() => probar(clave)}
                                  aria-label={`Probar ${t.nombre}`} title="Abrir su formulario">
                            <Play className="w-3.5 h-3.5" /> Probar
                          </button>
                        ) : admin ? (
                          <button className="gc-btn" onClick={() => aprobar(s.id, t.nombre)}
                                  disabled={aprobando === clave} aria-label={`Aprobar ${t.nombre}`}>
                            {aprobando === clave ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Check className="w-3.5 h-3.5" />}
                            Aprobar
                          </button>
                        ) : null}
                      </span>
                    </div>
                    {!t.habilitada && (
                      <span className="imgp-hint" data-testid="motivo-deshabilitada">
                        Deshabilitada{t.motivo ? `: ${t.motivo}` : ''}.{' '}
                        {admin ? 'Revisa el cambio antes de aprobarla.' : 'Un administrador de tu organización debe re-aprobarla.'}
                      </span>
                    )}
                  </li>
                )
              })}
            </ul>
          </section>
        )
      })}
    </div>
  )
}
