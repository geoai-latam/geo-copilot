/**
 * F6 (E6.1) — la app solo se muestra con sesión iniciada cuando el backend usa OIDC.
 *
 * Arranque: lee la configuración de identidad del backend, completa el regreso del proveedor y
 * pide `GET /auth/yo` (nombre, organización y rol). Sin OIDC (desarrollo) deja pasar sin más.
 * Si el backend rechaza al usuario (sin organización o sin rol), lo dice y ofrece salir.
 */
import { useEffect, useState, type ReactNode } from 'react'
import { LogIn, LogOut } from 'lucide-react'
import { cabecerasAuth, cerrarSesion, fijarUsuario, iniciarAuth, iniciarSesion, type Usuario } from '@/lib/auth'
import { useSesionAuth } from '@/hooks/useSesionAuth'

type Fase = 'cargando' | 'lista' | 'rechazado' | 'sin_servidor'

async function pedirYo(): Promise<{ usuario: Usuario | null; motivo: string | null }> {
  try {
    const r = await fetch('/api/v1/auth/yo', { headers: cabecerasAuth() })
    if (r.ok) return { usuario: (await r.json()) as Usuario, motivo: null }
    if (r.status === 403) {
      const d = await r.json().catch(() => ({}))
      return { usuario: null, motivo: String(d.detail || 'Tu usuario no tiene acceso a GEO Copilot.') }
    }
  } catch {
    // backend caído: la app lo dirá con su indicador de conexión
  }
  return { usuario: null, motivo: null }
}

export function AuthGate({ children }: { children: ReactNode }) {
  const [fase, setFase] = useState<Fase>('cargando')
  const [motivo, setMotivo] = useState<string | null>(null)
  const sesion = useSesionAuth()

  const [intento, setIntento] = useState(0)
  useEffect(() => {
    let vivo = true
    void iniciarAuth().then((m) => { if (vivo) setFase(m === 'sin_servidor' ? 'sin_servidor' : 'lista') })
    return () => { vivo = false }
  }, [intento])

  // con sesión (o sin OIDC): quién es
  useEffect(() => {
    if (fase === 'cargando' || sesion.requiereLogin) return
    let vivo = true
    void pedirYo().then(({ usuario, motivo: m }) => {
      if (!vivo) return
      fijarUsuario(usuario)
      if (m) {
        setMotivo(m)
        setFase('rechazado')
      }
    })
    return () => { vivo = false }
  }, [fase, sesion.requiereLogin, sesion.modo])

  if (fase === 'cargando') {
    return <div className="auth-pantalla" aria-busy="true"><div className="auth-tarjeta">Cargando…</div></div>
  }
  if (fase === 'sin_servidor') {
    // V5: antes se mostraba la app «sin login» (y todo fallaba con 401) mientras el backend arrancaba
    return (
      <div className="auth-pantalla">
        <div className="auth-tarjeta" role="alert">
          <img src="/geoai-logo.png" alt="" width={40} height={40} />
          <h1>Sin conexión con el servidor</h1>
          <p>No se pudo contactar con el servidor de GEO Copilot. Puede estar reiniciándose.</p>
          <button type="button" className="btn btn-primary"
                  onClick={() => { setFase('cargando'); setIntento((n) => n + 1) }}>
            Reintentar
          </button>
        </div>
      </div>
    )
  }
  if (fase === 'rechazado') {
    return (
      <div className="auth-pantalla">
        <div className="auth-tarjeta" role="alert">
          <img src="/geoai-logo.png" alt="" width={40} height={40} />
          <h1>Sin acceso</h1>
          <p>{motivo}</p>
          <button type="button" className="btn btn-secondary" onClick={() => void cerrarSesion()}>
            <LogOut size={14} /> Salir
          </button>
        </div>
      </div>
    )
  }
  if (sesion.requiereLogin) {
    return (
      <div className="auth-pantalla">
        <div className="auth-tarjeta">
          <img src="/geoai-logo.png" alt="GeoAI LATAM" width={40} height={40} />
          <h1>Geo Copilot</h1>
          <p>Inicia sesión con la cuenta de tu organización para ver tus mapas y conversaciones.</p>
          <button type="button" className="btn btn-primary" onClick={() => void iniciarSesion()}>
            <LogIn size={14} /> Iniciar sesión
          </button>
        </div>
      </div>
    )
  }
  return <>{children}</>
}

const NOMBRE_ROL: Record<string, string> = { viewer: 'Lectura', analyst: 'Analista', admin: 'Administración' }

/** Quién está conectado (organización y rol) y salir. Solo con OIDC. */
export function MenuUsuario() {
  const { modo, usuario } = useSesionAuth()
  if (modo !== 'oidc' || !usuario) return null
  return (
    <div className="menu-usuario" title={`${usuario.nombre} · ${usuario.org_id} · ${usuario.rol ?? 'sin rol'}`}>
      <span className="menu-usuario-nombre">{usuario.nombre || usuario.sub}</span>
      <span className="menu-usuario-org mono">{usuario.org_id}</span>
      <span className="menu-usuario-rol">{NOMBRE_ROL[usuario.rol ?? ''] ?? usuario.rol}</span>
      <button type="button" className="menu-usuario-salir" aria-label="Cerrar sesión" onClick={() => void cerrarSesion()}>
        <LogOut size={14} />
      </button>
    </div>
  )
}
